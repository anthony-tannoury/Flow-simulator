"""
Visual (animated) salabim view of a parsed flow.

This module takes a flow exported by ``flow_designer.py`` (the same JSON that
``graph_parser.py`` consumes), builds the live simulation through ``GraphParser``
and then draws it with salabim's built-in 2D animation engine.

Goals
-----
* **Look like the designer.** Every card from the flow is rendered as a coloured
  box at (roughly) the same place it occupies in ``flow_designer.py``, using the
  same per-kind colours, and the wiring between cards is redrawn as edges.
* **Show the material flow live.** Hard buffers display the pieces they currently
  hold (one little coloured square per piece, coloured by its root model), tasks
  light up while they are working / break down in red / go grey while idle, and a
  header keeps a running count of the work-in-progress.
* **Be explorable, not cramped.** Instead of squeezing a large graph into one
  window, the whole scene is laid out at a readable scale and you *pan and zoom*
  to move around it (salabim binds this out of the box):

      - drag with the left mouse button .... pan
      - mouse wheel ........................ zoom in / out
      - ``u`` .............................. reset the zoom
      - ``space`` .......................... pause / resume
      - ``s`` .............................. single step
      - ``-`` / ``+`` ...................... slow down / speed up

Usage
-----
    python visual_simulation.py                       # animates atelier_injection.json
    python visual_simulation.py clean_export.json
    python visual_simulation.py atelier_injection.json --till 5000 --speed 8

    # build the model + layout without opening a window (CI / headless check):
    python visual_simulation.py --no-animate --till 2000

The optimized_version/ folder is intentionally ignored; this builds straight on
top of simulation.py / graph_parser.py.
"""

from __future__ import annotations

import argparse
import json

from simulation import env, sim, Model, Piece, HardBuffer, SoftBuffer, Task, FirstTask
from graph_parser import GraphParser


# ============================================================
# Appearance — mirrors flow_designer.py
# ============================================================

# Per-kind card colours, copied from flow_designer.py so the animation reads like
# the editor the flow was designed in.
KIND_COLORS = {
    "Distribution": (80, 100, 160),
    "Interval": (110, 90, 160),
    "ScheduledShutdowns": (125, 80, 130),
    "Resource": (120, 100, 60),
    "RestockableResource": (140, 105, 55),
    "HardBuffer": (60, 125, 90),
    "SoftBuffer": (60, 115, 125),
    "FirstTask": (145, 80, 80),
    "Task": (150, 90, 60),
    "Breakdown": (150, 65, 85),
    "Monitor": (55, 110, 125),
}
DEFAULT_KIND_COLOR = (70, 70, 70)

# Colours used to tint an edge by the port it leaves from (flow_designer PORT_COLORS).
PORT_COLORS = {
    "buffer": (80, 180, 120),
    "task": (230, 140, 70),
    "duration": (90, 130, 230),
    "resource": (230, 190, 80),
    "shutdown": (180, 100, 200),
    "interval": (160, 110, 220),
    "breakdown": (220, 90, 110),
    "monitor": (110, 180, 200),
}

# Box footprint (in flow-designer scene units) used per kind. Positions in the JSON
# are top-left corners, so width/height only need to be "close enough" to read well.
KIND_SIZES = {
    "FirstTask": (260, 150),
    "Task": (260, 150),
    "HardBuffer": (250, 130),
    "SoftBuffer": (230, 95),
    "RestockableResource": (230, 120),
    "Breakdown": (230, 110),
    "Monitor": (230, 110),
    "Distribution": (220, 90),
    "Resource": (220, 85),
    "ScheduledShutdowns": (230, 95),
    "Interval": (210, 85),
}
DEFAULT_SIZE = (220, 95)

# Palette for piece colours (by root model).
MODEL_PALETTE = [
    "#e6584d", "#4da3e6", "#54c27a", "#e6b34d", "#a06fe0",
    "#4dd0d6", "#e07fb0", "#9fd14d", "#e6864d", "#6d7fe0",
]

BACKGROUND = "#1e1e28"
EDGE_FLOW_KINDS = {"HardBuffer", "SoftBuffer", "Task", "FirstTask"}


def _hex(rgb, factor: float = 1.0) -> str:
    """(r, g, b) 0-255 tuple -> '#rrggbb', optionally scaled by *factor*."""
    r, g, b = (max(0, min(255, int(c * factor))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _port_color(from_port: str, from_kind: str):
    """Pick an edge colour from the originating port name (falls back to the kind)."""
    p = (from_port or "").lower()
    if "duration" in p or p == "distribution":
        return PORT_COLORS["duration"]
    if "interval" in p:
        return PORT_COLORS["interval"]
    if "shutdown" in p:
        return PORT_COLORS["shutdown"]
    if "operator" in p or "resource" in p:
        return PORT_COLORS["resource"]
    if "monitor" in p:
        return PORT_COLORS["monitor"]
    if "breakdown" in p or "task_ref" in p:
        return PORT_COLORS["breakdown"]
    if "buf" in p or "buffer" in p:
        return PORT_COLORS["buffer"]
    if "task" in p:
        return PORT_COLORS["task"]
    return KIND_COLORS.get(from_kind, DEFAULT_KIND_COLOR)


def _root_model(model: Model) -> Model:
    while model.parent is not None:
        model = model.parent
    return model


# ============================================================
# Visual simulation
# ============================================================

class VisualSimulation:
    """Lays out and animates the model built by a :class:`GraphParser`."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.parser = GraphParser(filename)

        with open(filename, "r") as f:
            self.data = json.load(f)

        self.nodes = {n["id"]: n for n in self.data["nodes"]}
        self.connections = self.data.get("connections", [])

        self._assign_model_colors()
        self._compute_layout()

    # ---- model colours -------------------------------------------------

    def _assign_model_colors(self) -> None:
        """Map every root model to a colour and teach Piece how to draw itself."""
        roots = []
        for model in self.parser.models.values():
            root = _root_model(model)
            if root.name not in [r.name for r in roots]:
                roots.append(root)

        self.model_colors = {
            root.name: MODEL_PALETTE[i % len(MODEL_PALETTE)]
            for i, root in enumerate(roots)
        }
        color_of = self.model_colors

        def piece_animation_objects(piece, id, screen_coordinates=True):
            color = color_of.get(_root_model(piece.model).name, "#cccccc")
            size = 22
            ao = sim.AnimateRectangle(
                spec=(-size / 2, -size / 2, size / 2, size / 2),
                fillcolor=color,
                linecolor="white",
                linewidth=0.5,
                text=piece.model.name,
                textcolor="white",
                fontsize=9,
                screen_coordinates=screen_coordinates,
            )
            return (26, 26, ao)

        # Monkeypatch the Piece class so AnimateQueue renders pieces in model colour.
        Piece.animation_objects = piece_animation_objects

    # ---- geometry ------------------------------------------------------

    def _size(self, kind: str):
        return KIND_SIZES.get(kind, DEFAULT_SIZE)

    def _rect(self, node):
        """Return (left, bottom, right, top) of a node in salabim world coords.

        Designer scene-y grows downward; salabim world-y grows upward, so we flip y.
        """
        x, y = node["position"]
        w, h = self._size(node["kind"])
        left, right = x, x + w
        top, bottom = -y, -(y + h)
        return left, bottom, right, top

    def _center(self, node):
        left, bottom, right, top = self._rect(node)
        return (left + right) / 2, (bottom + top) / 2

    def _compute_layout(self) -> None:
        xs, ys = [], []
        for node in self.nodes.values():
            left, bottom, right, top = self._rect(node)
            xs += [left, right]
            ys += [bottom, top]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    # World-width (in designer units) shown across the window on start-up. Chosen so
    # individual cards stay readable; the rest of the graph is reached by panning.
    INITIAL_SPAN = 3000

    def _initial_view(self, width: int, height: int):
        """World window (x0, y0, x1) to open on.

        Big graphs are *not* squeezed into the window — we open zoomed in to a
        readable scale, anchored on the flow's source (the first task), and you pan
        from there. Graphs smaller than ``INITIAL_SPAN`` are simply fitted.
        """
        minx, miny, maxx, maxy = self.bbox
        bw, bh = (maxx - minx), (maxy - miny)
        win_aspect = height / width

        # Small enough to show at once -> fit it with a margin.
        if bw <= self.INITIAL_SPAN and bh <= self.INITIAL_SPAN * win_aspect:
            cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
            span = max(bw, bh / win_aspect) * 1.12
            return cx - span / 2, cy - (span * win_aspect) / 2, cx + span / 2

        # Large graph -> open on the source, source sitting in the left third.
        ax, ay = self._anchor_point()
        span = self.INITIAL_SPAN
        x0 = ax - 0.20 * span
        y0 = ay - (span * win_aspect) / 2
        return x0, y0, x0 + span

    def _anchor_point(self):
        """Centre of the node to open the view on: the first task, else left-most node."""
        first = next((n for n in self.nodes.values() if n["kind"] == "FirstTask"), None)
        if first is None:
            first = min(self.nodes.values(), key=lambda n: n["position"][0])
        return self._center(first)

    # ---- registry: node id -> live engine object ----------------------

    def _live_object(self, node_id: str):
        p = self.parser
        for table in (p.hard_buffers, p.soft_buffers, p.tasks, p.first_tasks):
            if node_id in table:
                return table[node_id]
        return None

    # ============================================================
    # Drawing
    # ============================================================

    def build_animation(self) -> None:
        self._draw_edges()
        for node in self.nodes.values():
            self._draw_node(node)
        self._draw_overlay()

    def _draw_edges(self) -> None:
        for conn in self.connections:
            src = self.nodes.get(conn["from_node"])
            dst = self.nodes.get(conn["to_node"])
            if src is None or dst is None:
                continue

            sl, sb, sr, st = self._rect(src)
            dl, db, dr, dt = self._rect(dst)
            # leave the right edge of the source, enter the left edge of the target
            x1, y1 = sr, (sb + st) / 2
            x2, y2 = dl, (db + dt) / 2

            is_flow = src["kind"] in EDGE_FLOW_KINDS and dst["kind"] in EDGE_FLOW_KINDS
            color = _port_color(conn.get("from_port", ""), conn.get("from_kind", ""))

            # gentle S-shaped elbow so parallel wires don't overlap into one line
            midx = (x1 + x2) / 2
            sim.AnimateLine(
                spec=(x1, y1, midx, y1, midx, y2, x2, y2),
                linecolor=_hex(color, 1.0 if is_flow else 0.7),
                linewidth=3.0 if is_flow else 1.2,
                layer=50,
            )

    def _draw_node(self, node) -> None:
        kind = node["kind"]
        left, bottom, right, top = self._rect(node)
        cx, cy = (left + right) / 2, (bottom + top) / 2
        base = KIND_COLORS.get(kind, DEFAULT_KIND_COLOR)
        live = self._live_object(node["id"])

        # --- box fill (dynamic for tasks & buffers) ---
        if isinstance(live, Task):
            fill = (lambda t=live, b=base: self._task_fill(t, b))
            line = (lambda t=live: self._task_line(t))
        elif isinstance(live, HardBuffer):
            fill = (lambda buf=live, b=base: _hex(b, 1.25 if len(buf) else 0.85))
            line = _hex(base, 1.6)
        else:
            fill = _hex(base)
            line = _hex(base, 1.6)

        sim.AnimateRectangle(
            spec=(left, bottom, right, top),
            fillcolor=fill,
            linecolor=line,
            linewidth=2,
            layer=30,
        )

        # --- kind tag (small, top-left) ---
        sim.AnimateText(
            text=kind,
            x=left + 10,
            y=top - 12,
            text_anchor="nw",
            textcolor=_hex(base, 1.9),
            fontsize=11,
            layer=20,
        )

        # --- name (centered) ---
        sim.AnimateText(
            text=node.get("name", node["id"]),
            x=cx,
            y=top - 34,
            text_anchor="n",
            textcolor="white",
            fontsize=15,
            layer=20,
        )

        if isinstance(live, HardBuffer):
            self._draw_buffer_contents(node, live, left, bottom, right, top)
        elif isinstance(live, Task):
            self._draw_task_status(live, cx, bottom)

    # ---- per-kind live decorations ------------------------------------

    def _draw_buffer_contents(self, node, buf: HardBuffer, left, bottom, right, top) -> None:
        # live count, top-right
        sim.AnimateText(
            text=lambda b=buf: f"{len(b)}",
            x=right - 12,
            y=top - 12,
            text_anchor="ne",
            textcolor="white",
            fontsize=18,
            layer=20,
        )
        # the pieces themselves, marching right along the bottom of the box
        sim.AnimateQueue(
            queue=buf,
            x=left + 22,
            y=bottom + 22,
            direction="e",
            max_length=max(1, int((right - left - 30) // 26)),
            title="",                    # buffer name is already drawn above
            screen_coordinates=False,    # anchor is in world coords, not pixels
            layer=10,
        )

    def _task_fill(self, task: Task, base) -> str:
        if task.is_in_breakdown.get():
            return "#c0392b"           # broken -> red
        if task.is_in_scheduled_shutdown.get():
            return "#8e44ad"           # scheduled stop -> purple
        if task.active_carriers:
            return _hex(base, 1.45)    # working -> bright
        return _hex(base, 0.75)        # idle -> dim

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
        if n:
            return f"● WORKING ({n})"
        return "○ idle"

    def _draw_task_status(self, task: Task, cx, bottom) -> None:
        sim.AnimateText(
            text=lambda t=task: self._task_status_text(t),
            x=cx,
            y=bottom + 16,
            text_anchor="s",
            textcolor=lambda t=task: self._task_line(t),
            fontsize=13,
            layer=20,
        )

    # ---- fixed overlay (screen coordinates) ---------------------------

    def _draw_overlay(self) -> None:
        sim.AnimateText(
            text=f"Flow Simulator — {self.filename}",
            x=18, y=-18, xy_anchor="nw",
            text_anchor="nw", textcolor="white", fontsize=20,
            screen_coordinates=True, layer=0,
        )
        sim.AnimateText(
            text=lambda: f"t = {env.now():,.1f}",
            x=18, y=-46, xy_anchor="nw",
            text_anchor="nw", textcolor="#9fd14d", fontsize=16,
            screen_coordinates=True, layer=0,
        )
        sim.AnimateText(
            text=lambda: (
                f"pieces created: {Piece.ID}    "
                f"WIP (in buffers): {self._wip()}"
            ),
            x=18, y=-70, xy_anchor="nw",
            text_anchor="nw", textcolor="#cccccc", fontsize=13,
            screen_coordinates=True, layer=0,
        )
        sim.AnimateText(
            text="drag = pan   ·   wheel = zoom   ·   u = reset   ·   space = pause   ·   -/+ = speed",
            x=18, y=18, xy_anchor="sw",
            text_anchor="sw", textcolor="#777788", fontsize=12,
            screen_coordinates=True, layer=0,
        )

        # model legend (top-right)
        y = -18
        for name, color in self.model_colors.items():
            sim.AnimateRectangle(
                spec=(-10, -8, 10, 8),
                x=-150, y=y, xy_anchor="ne",
                fillcolor=color, linecolor="white", linewidth=0.5,
                screen_coordinates=True, layer=0,
            )
            sim.AnimateText(
                text=f"model {name}",
                x=-132, y=y, xy_anchor="ne",
                text_anchor="w", textcolor="white", fontsize=12,
                screen_coordinates=True, layer=0,
            )
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
                animate=True,
                synced=True,
                speed=speed,
                width=width,
                height=height,
                title=f"Flow Simulator — {self.filename}",
                background_color=BACKGROUND,
                foreground_color="white",
                # Our own header draws the time/WIP, and the controls live in the hint
                # bar, so the built-in widgets are turned off to avoid overlap.
                show_menu_buttons=False,
                show_fps=False,
                show_time=False,
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
    vis.run(
        till=args.till,
        animate=not args.no_animate,
        speed=args.speed,
        width=args.width,
        height=args.height,
    )

    if args.no_animate:
        print(f"Built {len(vis.nodes)} nodes, {len(vis.connections)} connections.")
        print(f"Model colors: {vis.model_colors}")
        vis.parser.print_statistics()


if __name__ == "__main__":
    main()
