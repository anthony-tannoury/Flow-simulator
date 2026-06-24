"""
Visual (animated) salabim view of a parsed flow.

This module takes a flow exported by ``flow_designer.py`` (the same JSON that
``graph_parser.py`` consumes), builds the live simulation through ``GraphParser``
and then draws it with salabim's built-in 2D animation engine.

What is drawn
-------------
Only the **essential material flow** is shown — first tasks, tasks and the buffers
they are wired to — plus the edges between them. Distributions, resources,
breakdowns, scheduled shutdowns, monitors and the like are *not* drawn (they still
drive the simulation, they just aren't part of the picture).

Buffers
~~~~~~~
Every hard buffer shows a per-model count and a total. In addition:

* **Normal** buffers also show the pieces themselves as **colour-coded squares**
  (one square per piece, colour = the piece's model).
* **Exit** and **Scrap** buffers show **counts only** (no squares).

Models / colours are handled automatically: add or rename models in the flow and
the legend, the per-model counts and the square colours follow. Children are
prioritised over their parents — a model that has children is represented by those
children; a model with no children represents itself.

Tasks light up while working, turn red on breakdown and purple on a scheduled stop.

Controls
--------
The whole scene is laid out at a readable scale and you *pan and zoom* around it
(salabim binds this out of the box):

    drag (left button) ... pan          u ......... reset the zoom
    mouse wheel .......... zoom          space ..... pause / resume
    s .... single step                  -/+ ....... slower / faster

Usage
-----
    python visual_simulation.py                       # atelier_injection.json
    python visual_simulation.py clean_export.json
    python visual_simulation.py atelier_injection.json --till 5000 --speed 8

    # build the model + layout without opening a window (CI / headless check):
    python visual_simulation.py --no-animate --till 2000
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from simulation import env, sim, Model, Piece, HardBuffer, SoftBuffer, Task, FirstTask
from graph_parser import GraphParser


# ============================================================
# Appearance
# ============================================================

# Per-kind card colours, copied from flow_designer.py so the animation reads like
# the editor the flow was designed in. Only flow kinds are kept.
KIND_COLORS = {
    "HardBuffer": (60, 125, 90),
    "SoftBuffer": (60, 115, 125),
    "FirstTask": (145, 80, 80),
    "Task": (150, 90, 60),
}
DEFAULT_KIND_COLOR = (70, 70, 70)

# Edge tint by the port the wire leaves from.
PORT_COLORS = {
    "buffer": (80, 180, 120),
    "task": (230, 140, 70),
}

# Box footprint (in flow-designer scene units) per kind. Positions in the JSON are
# top-left corners, so width/height only need to be "close enough" to read well.
KIND_SIZES = {
    "FirstTask": (260, 150),
    "Task": (260, 150),
    "HardBuffer": (290, 190),
    "SoftBuffer": (230, 95),
}
DEFAULT_SIZE = (240, 120)

# Deterministic, colour-blind-friendly-ish palette assigned to models in name order.
MODEL_PALETTE = [
    "#e6584d", "#4da3e6", "#54c27a", "#e6b34d", "#a06fe0",
    "#4dd0d6", "#e07fb0", "#9fd14d", "#e6864d", "#6d7fe0",
    "#d65db1", "#00b8a9", "#f6c85f", "#9b8df0", "#5fb0f6",
]

BACKGROUND = "#1e1e28"

# Only these kinds are drawn; everything else is simulated but not shown.
FLOW_KINDS = {"Task", "FirstTask", "HardBuffer", "SoftBuffer"}


def _hex(rgb, factor: float = 1.0) -> str:
    """(r, g, b) 0-255 tuple -> '#rrggbb', optionally scaled by *factor*."""
    r, g, b = (max(0, min(255, int(c * factor))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _port_color(from_port: str):
    p = (from_port or "").lower()
    if "buf" in p or "buffer" in p:
        return PORT_COLORS["buffer"]
    return PORT_COLORS["task"]


# ============================================================
# Visual simulation
# ============================================================

class VisualSimulation:
    """Lays out and animates the model built by a :class:`GraphParser`."""

    # World-width (designer units) shown across the window on start-up. Chosen so
    # individual cards stay readable; the rest of the graph is reached by panning.
    INITIAL_SPAN = 3000

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.parser = GraphParser(filename)

        with open(filename, "r") as f:
            self.data = json.load(f)

        self.nodes = {n["id"]: n for n in self.data["nodes"]}
        self.connections = self.data.get("connections", [])

        self._resolve_models()
        self._compute_layout()

    # ---- models & colours ---------------------------------------------

    def _resolve_models(self) -> None:
        """Decide which models are *represented* and give each one a stable colour.

        Rule: prioritise children. A model that has children is represented by those
        children, not itself; a model with no children represents itself. We also
        always include any model a FirstTask actually creates, so no piece is left
        without a colour even in unusual flows.
        """
        models = self.parser.models  # name -> Model
        has_child = {m.parent.name for m in models.values() if m.parent is not None}
        leaves = {name for name in models if name not in has_child}

        created = set()
        for node in self.nodes.values():
            if node["kind"] == "FirstTask":
                created.update(mp["model"] for mp in node.get("models_probs", []))

        # Represented vocabulary, in a deterministic order so colours are stable as
        # the flow grows.
        self.represented_models = sorted(leaves | created)
        self.model_colors = {
            name: MODEL_PALETTE[i % len(MODEL_PALETTE)]
            for i, name in enumerate(self.represented_models)
        }

        # Teach pieces to draw themselves in their model colour (used nowhere now that
        # squares are drawn manually, but kept harmless/consistent).
        color_of = self.model_colors

        def piece_animation_objects(piece, id, screen_coordinates=True):
            color = color_of.get(piece.model.name, "#cccccc")
            ao = sim.AnimateRectangle(
                spec=(-10, -10, 10, 10), fillcolor=color, linecolor="white",
                linewidth=0.5, screen_coordinates=screen_coordinates,
            )
            return (24, 24, ao)

        Piece.animation_objects = piece_animation_objects

    # ---- geometry ------------------------------------------------------

    def _size(self, kind: str):
        return KIND_SIZES.get(kind, DEFAULT_SIZE)

    def _rect(self, node):
        """(left, bottom, right, top) of a node in salabim world coords.

        Designer scene-y grows downward; salabim world-y grows upward -> flip y.
        """
        x, y = node["position"]
        w, h = self._size(node["kind"])
        return x, -(y + h), x + w, -y

    def _center(self, node):
        left, bottom, right, top = self._rect(node)
        return (left + right) / 2, (bottom + top) / 2

    def _flow_nodes(self):
        return [n for n in self.nodes.values() if n["kind"] in FLOW_KINDS]

    def _compute_layout(self) -> None:
        xs, ys = [], []
        for node in self._flow_nodes():
            left, bottom, right, top = self._rect(node)
            xs += [left, right]
            ys += [bottom, top]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def _anchor_point(self):
        flow = self._flow_nodes()
        first = next((n for n in flow if n["kind"] == "FirstTask"), None)
        if first is None:
            first = min(flow, key=lambda n: n["position"][0])
        return self._center(first)

    def _initial_view(self, width: int, height: int):
        """World window (x0, y0, x1) to open on.

        Big graphs are not squeezed into the window — we open zoomed in to a readable
        scale, anchored on the source, and pan from there. Small graphs are fitted.
        """
        minx, miny, maxx, maxy = self.bbox
        bw, bh = (maxx - minx), (maxy - miny)
        win_aspect = height / width

        if bw <= self.INITIAL_SPAN and bh <= self.INITIAL_SPAN * win_aspect:
            cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
            span = max(bw, bh / win_aspect) * 1.12
            return cx - span / 2, cy - (span * win_aspect) / 2, cx + span / 2

        ax, ay = self._anchor_point()
        span = self.INITIAL_SPAN
        x0 = ax - 0.20 * span
        return x0, ay - (span * win_aspect) / 2, x0 + span

    # ---- registry: node id -> live engine object ----------------------

    def _live_object(self, node_id: str):
        p = self.parser
        for table in (p.hard_buffers, p.soft_buffers, p.tasks, p.first_tasks):
            if node_id in table:
                return table[node_id]
        return None

    # ---- buffer content helpers (evaluated each frame) ----------------

    @staticmethod
    def _contents(buf) -> list:
        return list(buf)

    def _count(self, buf, model_name: str) -> int:
        return sum(1 for p in buf if p.model.name == model_name)

    def _models_in(self, buf) -> list[str]:
        """Represented models this buffer is allowed to hold (stable per buffer)."""
        out = []
        for name in self.represented_models:
            model = self.parser.models.get(name)
            if model is not None and buf.can_take(model):
                out.append(name)
        return out

    # ============================================================
    # Drawing
    # ============================================================

    def build_animation(self) -> None:
        self._draw_edges()
        for node in self._flow_nodes():
            self._draw_node(node)
        self._draw_overlay()

    def _draw_edges(self) -> None:
        for conn in self.connections:
            src = self.nodes.get(conn["from_node"])
            dst = self.nodes.get(conn["to_node"])
            if src is None or dst is None:
                continue
            if src["kind"] not in FLOW_KINDS or dst["kind"] not in FLOW_KINDS:
                continue

            _, sb, sr, st = self._rect(src)
            dl, db, _, dt = self._rect(dst)
            x1, y1 = sr, (sb + st) / 2          # leave the source's right edge
            x2, y2 = dl, (db + dt) / 2          # enter the target's left edge
            midx = (x1 + x2) / 2                 # squared-off elbow

            sim.AnimateLine(
                spec=(x1, y1, midx, y1, midx, y2, x2, y2),
                linecolor=_hex(_port_color(conn.get("from_port", ""))),
                linewidth=3.0,
                layer=50,
            )

    def _draw_node(self, node) -> None:
        kind = node["kind"]
        left, bottom, right, top = self._rect(node)
        cx = (left + right) / 2
        base = KIND_COLORS.get(kind, DEFAULT_KIND_COLOR)
        live = self._live_object(node["id"])

        # box fill (dynamic for tasks & hard buffers)
        if isinstance(live, Task):
            fill = (lambda t=live, b=base: self._task_fill(t, b))
            line = (lambda t=live: self._task_line(t))
        elif isinstance(live, HardBuffer):
            fill = (lambda buf=live, b=base: _hex(b, 1.2 if len(buf) else 0.8))
            line = _hex(base, 1.6)
        else:
            fill = _hex(base)
            line = _hex(base, 1.6)

        sim.AnimateRectangle(spec=(left, bottom, right, top), fillcolor=fill,
                             linecolor=line, linewidth=2, layer=30)

        # role / kind tag (top-left)
        role = node.get("buffer_role")
        tag = f"{kind} · {role}" if (isinstance(live, HardBuffer) and role) else kind
        sim.AnimateText(text=tag, x=left + 12, y=top - 13, text_anchor="nw",
                        textcolor=_hex(base, 1.9), fontsize=11, layer=20)

        # name (top, centred)
        sim.AnimateText(text=node.get("name", node["id"]), x=cx, y=top - 31,
                        text_anchor="n", textcolor="white", fontsize=15,
                        max_lines=2, layer=20)

        if isinstance(live, HardBuffer):
            self._draw_buffer(node, live, left, bottom, right, top)
        elif isinstance(live, Task):
            self._draw_task_status(live, cx, bottom)
        elif isinstance(live, FirstTask):
            sim.AnimateText(text="▶ source", x=cx, y=bottom + 16, text_anchor="s",
                            textcolor="#e0a0a0", fontsize=13, layer=20)

    # ---- buffers -------------------------------------------------------

    def _draw_buffer(self, node, buf, left, bottom, right, top) -> None:
        role = node.get("buffer_role", "Normal")
        is_normal = role == "Normal"
        models_here = self._models_in(buf)

        # total (top-right, big)
        sim.AnimateText(text=lambda b=buf: f"Σ {len(b)}", x=right - 12, y=top - 13,
                        text_anchor="ne", textcolor="white", fontsize=18, layer=20)

        if is_normal:
            # compact per-model counts stacked under the name (small, colour-coded)
            y = top - 52
            for name in models_here:
                sim.AnimateText(
                    text=lambda b=buf, m=name: f"{m}: {self._count(b, m)}",
                    x=left + 12, y=y, text_anchor="nw",
                    textcolor=self.model_colors.get(name, "#cccccc"),
                    fontsize=11, layer=20,
                )
                y -= 16
            header = (top - y) + 6
            self._draw_squares(buf, left, bottom, right, top - header)
        else:
            # exit / scrap: counts only, larger and centred (no squares)
            y = top - 60
            for name in models_here:
                sim.AnimateText(
                    text=lambda b=buf, m=name: f"{m}:  {self._count(b, m)}",
                    x=(left + right) / 2, y=y, text_anchor="n",
                    textcolor=self.model_colors.get(name, "#cccccc"),
                    fontsize=15, layer=20,
                )
                y -= 24

    def _draw_squares(self, buf, left, bottom, right, top_area) -> None:
        pad, sq, pitch = 12, 20, 24
        cols = max(1, int((right - left - 2 * pad) // pitch))
        rows = max(1, int((top_area - bottom - pad) // pitch))
        for i in range(cols * rows):
            col, row = i % cols, i // cols
            x = left + pad + col * pitch + sq / 2
            y = top_area - pad - row * pitch - sq / 2
            sim.AnimateRectangle(
                spec=(-sq / 2, -sq / 2, sq / 2, sq / 2), x=x, y=y,
                fillcolor=lambda b=buf, i=i: self._square_color(b, i),
                linecolor="white", linewidth=0.4,
                visible=lambda b=buf, i=i: i < len(b),
                layer=10,
            )

    def _square_color(self, buf, i: int) -> str:
        lst = self._contents(buf)
        if i < len(lst):
            return self.model_colors.get(lst[i].model.name, "#cccccc")
        return BACKGROUND  # hidden anyway

    # ---- tasks ---------------------------------------------------------

    def _task_fill(self, task: Task, base) -> str:
        if task.is_in_breakdown.get():
            return "#c0392b"
        if task.is_in_scheduled_shutdown.get():
            return "#8e44ad"
        if task.active_carriers:
            return _hex(base, 1.45)
        return _hex(base, 0.75)

    def _task_line(self, task: Task) -> str:
        if task.is_in_breakdown.get():
            return "#ff6b5b"
        if task.active_carriers:
            return "#ffe08a"
        return "#888888"

    def _task_status_text(self, task: Task) -> str:
        if task.is_in_breakdown.get():
            return "● BREAKDOWN"
        if task.is_in_scheduled_shutdown.get():
            return "● SHUTDOWN"
        n = len(task.active_carriers)
        return f"● WORKING ({n})" if n else "○ idle"

    def _draw_task_status(self, task: Task, cx, bottom) -> None:
        sim.AnimateText(text=lambda t=task: self._task_status_text(t), x=cx,
                        y=bottom + 16, text_anchor="s",
                        textcolor=lambda t=task: self._task_line(t),
                        fontsize=13, layer=20)

    # ---- fixed overlay (screen coordinates) ---------------------------

    def _draw_overlay(self) -> None:
        sim.AnimateText(text=f"Flow Simulator — {self.filename}", x=18, y=-18,
                        xy_anchor="nw", text_anchor="nw", textcolor="white",
                        fontsize=20, screen_coordinates=True, layer=0)
        sim.AnimateText(text=lambda: f"t = {env.now():,.1f}", x=18, y=-46,
                        xy_anchor="nw", text_anchor="nw", textcolor="#9fd14d",
                        fontsize=16, screen_coordinates=True, layer=0)
        sim.AnimateText(
            text=lambda: f"pieces created: {Piece.ID}    WIP (in buffers): {self._wip()}",
            x=18, y=-70, xy_anchor="nw", text_anchor="nw", textcolor="#cccccc",
            fontsize=13, screen_coordinates=True, layer=0)
        sim.AnimateText(
            text="drag = pan   ·   wheel = zoom   ·   u = reset   ·   space = pause   ·   -/+ = speed",
            x=18, y=18, xy_anchor="sw", text_anchor="sw", textcolor="#777788",
            fontsize=12, screen_coordinates=True, layer=0)

        # model legend (top-right)
        y = -18
        for name in self.represented_models:
            sim.AnimateRectangle(spec=(-10, -8, 10, 8), x=-150, y=y, xy_anchor="ne",
                                 fillcolor=self.model_colors[name], linecolor="white",
                                 linewidth=0.5, screen_coordinates=True, layer=0)
            sim.AnimateText(text=f"model {name}", x=-132, y=y, xy_anchor="ne",
                            text_anchor="w", textcolor="white", fontsize=12,
                            screen_coordinates=True, layer=0)
            y -= 22

    def _wip(self) -> int:
        return sum(len(buf) for buf in self.parser.hard_buffers.values())

    # ============================================================
    # Run
    # ============================================================

    def run(self, till: float | None = None, animate: bool = True,
            speed: float = 8.0, width: int = 1280, height: int = 800) -> None:
        if animate:
            x0, y0, x1 = self._initial_view(width, height)
            env.animation_parameters(
                animate=True, synced=True, speed=speed, width=width, height=height,
                title=f"Flow Simulator — {self.filename}",
                background_color=BACKGROUND, foreground_color="white",
                # our own header draws the time/WIP, controls live in the hint bar
                show_menu_buttons=False, show_fps=False, show_time=False,
                x0=x0, y0=y0, x1=x1,
            )
            self.build_animation()

        env.run(till=till)


def main() -> None:
    ap = argparse.ArgumentParser(description="Animated salabim view of a designed flow.")
    ap.add_argument("file", nargs="?", default="atelier_injection.json",
                    help="flow JSON exported by flow_designer.py (default: atelier_injection.json)")
    ap.add_argument("--till", type=float, default=None,
                    help="stop time (default: run until the window is closed)")
    ap.add_argument("--speed", type=float, default=8.0,
                    help="simulation time units played per real second (default: 8)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--no-animate", action="store_true",
                    help="build the model + layout but do not open a window (headless)")
    args = ap.parse_args()

    vis = VisualSimulation(args.file)
    vis.run(till=args.till, animate=not args.no_animate, speed=args.speed,
            width=args.width, height=args.height)

    if args.no_animate:
        flow = vis._flow_nodes()
        print(f"Drawn nodes: {len(flow)} (flow kinds only), "
              f"connections: {sum(1 for c in vis.connections if vis.nodes.get(c['from_node'],{}).get('kind') in FLOW_KINDS and vis.nodes.get(c['to_node'],{}).get('kind') in FLOW_KINDS)}")
        print(f"Represented models -> colors: {vis.model_colors}")
        vis.parser.print_statistics()


if __name__ == "__main__":
    main()
