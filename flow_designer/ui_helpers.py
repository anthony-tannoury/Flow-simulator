from __future__ import annotations

import copy
import json
import os
import platform
import re
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, List, Tuple

from Qt import QtCore, QtGui, QtWidgets
from NodeGraphQt import BaseNode, NodeGraph, PropertiesBinWidget


def app_settings() -> QtCore.QSettings:
    return QtCore.QSettings("FlowSimulator", "FlowDesigner")


def cpp_engine_filename() -> str:
    """The bundled flow_sim binary name for this platform (see engines/)."""
    system = platform.system()
    if system == "Windows":
        return "flow_sim-windows-x86_64.exe"
    if system == "Darwin":
        return "flow_sim-macos-universal"
    return "flow_sim-linux-x86_64"


def bundled_cpp_engine() -> str | None:
    """Path to the bundled binary for this platform under engines/, if present."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo_root, "engines", cpp_engine_filename())
    return path if os.path.isfile(path) else None


APP_NAME = "Simulation Flow Designer"
EDITOR_VERSION = "0.3.0"

DISTRIBUTION_SPECS = {
    "Constant": [("value", float, 0.0)],
    "Uniform": [("low", float, 0.0), ("high", float, 1.0)],
    "Normal": [("mean", float, 0.0), ("std", float, 1.0)],
    "Exponential": [("mean", float, 1.0)],
    "Triangular": [("low", float, 0.0), ("mode", float, 0.5), ("high", float, 1.0)],
    "LogNormal": [("mean", float, 1.0), ("sigma", float, 1.0)],   # mean & std of the values (mean > 0)
}

# A distribution parameter (or productivity / router probability) is either constant or a
# function of time. Each form's dynamic float fields:
FUNCTION_SPECS = {
    "constant":    [("value", 0.0)],
    "linear":      [("x1", 0.0), ("y1", 0.0), ("x2", 1.0), ("y2", 1.0)],
    "exponential": [("x1", 0.0), ("y1", 1.0), ("x2", 1.0), ("y2", 2.0), ("limit", 0.0)],
    "step":        [("x1", 0.0), ("y1", 0.0), ("x2", 1.0), ("y2", 1.0), ("step_size", 1.0)],
}

# CollectorType (piece tasks): discriminating x greedy/altruistic.
COLLECTOR_TYPES = [
    "NON_DISCRIMINATING_GREEDY",
    "DISCRIMINATING_GREEDY",
    "NON_DISCRIMINATING_ALTRUISTIC",
    "DISCRIMINATING_ALTRUISTIC",
]
# ResourceCollectorType (resource tasks).
RESOURCE_COLLECTOR_TYPES = ["GREEDY", "ALTRUISTIC"]

SHUTDOWN_TYPES = ["NON_FLEXIBLE", "FLEXIBLE"]

# BufferType (outlet.py): where a piece lands.
BUFFER_TYPES = ["PASSAGE", "SCRAP", "EXIT"]

# Simulation stopping criteria (judgement_day.py), canonical class names.
STOPPING_CRITERION_TYPES = ["ByTime", "ByPiecesProduced"]

PORT_COLORS = {
    "buffer": (80, 180, 120),
    "task": (230, 140, 70),
    "shutdown": (180, 100, 200),
    "breakdown": (220, 90, 110),
}


def new_uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def sentence_case(name: str) -> str:
    """Display form of an identifier: words split on underscores and CamelCase
    humps, all lowercase except a single leading capital. ByTime -> 'By time',
    PER_BATCH -> 'Per batch', AbortPendingCarriers -> 'Abort pending carriers'."""
    text = str(name or "").replace("_", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    words = text.split()
    if not words:
        return ""
    joined = " ".join(w.lower() for w in words)
    return joined[0].upper() + joined[1:]


def to_canonical(value: str, canonical_items: list) -> str:
    """Inverse of sentence_case over a known vocabulary: accept either the
    canonical identifier or its display form; anything else passes through."""
    for canonical in canonical_items:
        if value == canonical or value == sentence_case(canonical):
            return canonical
    return value


def fill_combo(combo, canonical_items: list, current: str | None = None) -> None:
    """Populate a combo with sentence-case labels carrying the canonical values
    as item data (read back with currentData()), selecting `current` if given."""
    for canonical in canonical_items:
        combo.addItem(sentence_case(canonical), canonical)
    if current is not None:
        i = combo.findData(to_canonical(current, canonical_items))
        combo.setCurrentIndex(i if i >= 0 else 0)


def qmessage(parent, title: str, text: str, icon=QtWidgets.QMessageBox.Information):
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.exec()


def node_uid(node: BaseNode) -> str:
    if not node.has_property("uid"):
        node.create_property("uid", new_uid("node"))
    return node.get_property("uid")


def node_kind(node: BaseNode) -> str:
    return node.get_property("kind") if node.has_property("kind") else node.__class__.__name__


def get_connected_ports(port) -> list:
    try:
        return port.connected_ports()
    except Exception:
        return []


def get_port_by_name(node, port_name: str, direction: str):
    try:
        ports = node.inputs() if direction == "input" else node.outputs()
        if isinstance(ports, dict):
            return ports.get(port_name)
        for port in ports:
            if port.name() == port_name:
                return port
    except Exception:
        return None
    return None


def connected_ports_safe(port):
    if port is None:
        return []
    try:
        return port.connected_ports()
    except Exception:
        return []


def connected_nodes_from_port(node, port_name: str, direction: str):
    port = get_port_by_name(node, port_name, direction)
    result = []
    for other_port in connected_ports_safe(port):
        try:
            result.append(other_port.node())
        except Exception:
            pass
    return result


def connected_refs_from_port(node, port_name: str, direction: str):
    return [node_uid(n) for n in connected_nodes_from_port(node, port_name, direction)]


def get_output_refs(node, port_name: str):
    return connected_refs_from_port(node, port_name, "output")


def connect_ports_by_name(from_node, from_port_name: str, to_node, to_port_name: str):
    out_port = get_port_by_name(from_node, from_port_name, "output")
    in_port = get_port_by_name(to_node, to_port_name, "input")
    if out_port is None:
        raise ValueError(f"Output port not found: {from_node.name()}.{from_port_name}")
    if in_port is None:
        raise ValueError(f"Input port not found: {to_node.name()}.{to_port_name}")
    out_kind = node_kind(from_node)
    in_kind = node_kind(to_node)
    if not is_valid_connection(out_kind, from_port_name, in_kind, to_port_name):
        raise ValueError(
            f"Invalid template connection: {out_kind}.{from_port_name} -> {in_kind}.{to_port_name}"
        )
    out_port.connect_to(in_port)


def get_property_json(node: BaseNode, prop_name: str, default):
    if not node.has_property(prop_name):
        return default
    value = node.get_property(prop_name)
    if value in [None, ""]:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def set_property_json(node: BaseNode, prop_name: str, value):
    if not node.has_property(prop_name):
        node.create_property(prop_name, json.dumps(value, indent=2, ensure_ascii=False))
    else:
        node.set_property(prop_name, json.dumps(value, indent=2, ensure_ascii=False))


def add_combo_input(node: BaseNode, name: str, label: str, items: list, default: str) -> None:
    try:
        node.add_combo_menu(name, label=label, items=items)
        node.set_property(name, default)
    except Exception:
        if not node.has_property(name):
            node.create_property(name, default)


def port_signature(port) -> Tuple[str, str, str]:
    n = port.node()
    ptype = str(port.type_()).lower()
    direction = "input" if "in" in ptype else "output"
    return node_kind(n), direction, port.name()


def is_valid_connection(out_kind: str, out_port: str, in_kind: str, in_port: str) -> bool:
    """Strict connection rules -- the single place controlling what can feed what.
    The only wires are piece flow (buffers/router/tasks), shutdowns and breakdowns;
    everything else is configured inside the card menus."""

    # Shutdowns feed tasks (piece or resource).
    if out_kind == "Shutdowns" and out_port == "shutdowns":
        return in_kind in {"Task", "ResourceTask"} and in_port == "shutdowns"

    # Breakdowns attach directly to a task (piece or resource), like shutdowns.
    if out_kind == "Breakdown" and out_port == "breakdown":
        return in_kind in {"Task", "ResourceTask"} and in_port == "breakdowns"

    # Buffer feeds task inputs.
    if out_kind == "Buffer" and out_port == "to_task":
        return in_kind == "Task" and in_port == "bufs_in"

    # Tasks / piece generators / breakdowns feed buffers (breakdown outlets are lifeboats).
    if out_kind in {"Task", "PieceGenerator", "Breakdown"} and out_port == "bufs_out":
        return in_kind in {"Buffer", "Router"} and in_port == "from_task"

    # Router (router) routes to hard or soft buffers with probabilities.
    if out_kind == "Router" and out_port == "to_buffers":
        return in_kind in {"Buffer", "Router"} and in_port == "from_task"

    return False


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


DATE_TIME_FORMAT = "dd-MM-yyyy HH:mm"
PY_DATE_TIME_FORMAT = "%d-%m-%Y %H:%M"
DATE_FORMAT = "dd-MM-yyyy"
PY_DATE_FORMAT = "%d-%m-%Y"


def parse_date_time(text):
    """dd-mm-yyyy hh:mm -> datetime, or None if malformed."""
    try:
        return datetime.strptime(str(text).strip(), PY_DATE_TIME_FORMAT)
    except Exception:
        return None


def parse_date(text):
    """dd-mm-yyyy -> datetime (midnight), or None if malformed."""
    try:
        return datetime.strptime(str(text).strip(), PY_DATE_FORMAT)
    except Exception:
        return None


def closing_day_label(entry: dict) -> str:
    """Display text for a closing-day registry entry: the date, plus the optional label."""
    date = entry.get("date", "?")
    name = (entry.get("name") or "").strip()
    return f"{date} - {name}" if name else date


def _hhmm_to_min(text):
    hh, _, mm = str(text).partition(":")
    return as_int(hh) * 60 + as_int(mm)


def _min_to_hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"  # 1440 -> "24:00", the end-of-day convention


def _merge_day_intervals(intervals):
    """Merge overlapping or touching (start, end) minute intervals; sorted result."""
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def translate_shift_entry(entry, minutes, name=None):
    """A deep copy of a shift definition with every interval shifted by `minutes`
    (positive later, negative earlier). Weekly intervals move in a periodic week
    (mod 7 days): an interval that ends up crossing a day boundary is split, and the
    week end wraps back to its start — so e.g. a Mon-Fri 06:00-14:00 shift + 16 h
    becomes the matching night coverage (22:00-24:00 that day, 00:00-06:00 the next),
    exactly as such shifts are authored by hand. Custom intervals shift their
    absolute datetimes. The horizon and the days off are left unchanged."""
    WEEK = 7 * 1440
    e = copy.deepcopy(entry)
    if name is not None:
        e["name"] = name

    # Weekly: rebuild the seven weekday rows from the shifted, day-split pieces.
    days = e.get("days", [])
    per_day = [[] for _ in range(7)]  # (start_min, end_min) pieces landing on each weekday
    for i, d in enumerate(days[:7]):
        if not d.get("working"):
            continue
        for iv in d.get("intervals", []):
            start = i * 1440 + _hhmm_to_min(iv.get("start", "00:00"))
            end = i * 1440 + _hhmm_to_min(iv.get("end", "00:00"))
            length = end - start
            if length <= 0:
                continue
            cursor = (start + minutes) % WEEK
            while length > 0:
                day = cursor // 1440
                into_day = cursor % 1440
                take = min(1440 - into_day, length)
                per_day[day].append((into_day, into_day + take))
                length -= take
                cursor = (cursor + take) % WEEK
    if days:
        e["days"] = [
            {"working": bool(merged),
             "intervals": [{"start": _min_to_hhmm(s), "end": _min_to_hhmm(en)}
                           for s, en in merged]}
            for merged in (_merge_day_intervals(p) for p in per_day)
        ]

    # Custom: shift each absolute interval by the same duration.
    delta = timedelta(minutes=minutes)
    if e.get("custom_intervals"):
        shifted = []
        for iv in e["custom_intervals"]:
            s, en = parse_date_time(iv.get("start")), parse_date_time(iv.get("end"))
            if s and en:
                shifted.append({"start": (s + delta).strftime(PY_DATE_TIME_FORMAT),
                                "end": (en + delta).strftime(PY_DATE_TIME_FORMAT)})
            else:
                shifted.append(dict(iv))  # leave a malformed interval untouched
        e["custom_intervals"] = shifted

    return e


POLICY_OPTIONS = {
    "pending_carriers_pre_flexible_shutdowns": (["AbortPendingCarriers", "WaitForCarriers", "AbortOrWaitForCarriers"], "AbortPendingCarriers"),
    "pending_carrier_pre_task_shift_end": (["AbortPendingCarriers", "WaitForCarriers", "AbortOrWaitForCarriers"], "AbortPendingCarriers"),
    "operator_shift_constraint": (["ConstrainedByShift", "NotConstrainedByShift", "PartiallyConstrainedByShift"], "ConstrainedByShift"),
    "task_shift_constraint": (["ConstrainedByShift", "NotConstrainedByShift", "PartiallyConstrainedByShift"], "ConstrainedByShift"),
    "operators_self_conscious": (["Conscious", "Unconscious"], "Conscious"),
}

# Piece tasks add two collection policies on top of the shared five.
PIECE_POLICY_OPTIONS = {
    **POLICY_OPTIONS,
    "piece_exit_order": (["FirstInFirstOut", "FirstCreatedFirstOut"], "FirstInFirstOut"),
    "batch_model_choice": (["MostPresent", "FastestTaskDuration", "SmallestGapToMinCarrierCapacity"], "MostPresent"),
}

# Protocol types that carry a numeric parameter: type -> (json key, field label, default).
POLICY_TYPE_PARAMS = {
    "AbortOrWaitForCarriers": ("tolerance_fraction", "tolerance fraction", 0.5),
    "PartiallyConstrainedByShift": ("tolerance", "tolerance (time)", 0.0),
}


def default_policies(options) -> dict:
    return {name: {"type": default} for name, (_, default) in options.items()}


def _names(reg):
    return [e.get("name", "") for e in reg if e.get("name")]


def _leaf_model_names(model_registry):
    parents = {m.get("parent") for m in model_registry}
    return [m["name"] for m in model_registry if m["name"] not in parents]


def _model_parents(model_registry):
    return {m.get("name"): m.get("parent") for m in model_registry if m.get("name")}


def _taker_can_take(valid_models: set, model: str, parents: dict) -> bool:
    """Mirror PickyPieceTaker.can_take: a taker accepts a model if the model or any
    of its ancestors is in the taker's valid-model set."""
    seen = set()
    while model is not None and model not in seen:
        if model in valid_models:
            return True
        seen.add(model)
        model = parents.get(model)
    return False


def _takers_disjoint(a: set, b: set, parents: dict) -> bool:
    """Mirror PickyPieceTaker.disjoint (hierarchy-aware)."""
    return not (any(_taker_can_take(a, m, parents) for m in b)
                or any(_taker_can_take(b, m, parents) for m in a))


def ensure_ids(entries: list, prefix: str, old_by_name: dict | None = None, key: str = "name") -> list:
    """Give every registry entry a unique id. An entry without one reuses its prior
    id (matched by `key` against old_by_name) so ids survive edits, unless that id is
    already taken by a sibling — then it mints a fresh one. The id, never the key, is
    the identity, so two entries that happen to share a key still get distinct ids.
    `key` is "name" for models/resources/operators/shifts, "date" for closing days."""
    old_by_name = old_by_name or {}
    used = {e["id"] for e in entries if e.get("id")}
    for e in entries:
        if e.get("id"):
            continue
        candidate = old_by_name.get(e.get(key))
        e["id"] = candidate if (candidate and candidate not in used) else new_uid(prefix)
        used.add(e["id"])
    return entries


def _shift_export_shape(s: dict) -> dict:
    """Only the fields a shift's mode actually uses: weekly keeps days/horizon,
    custom keeps custom_intervals; both keep days_off and the optional repeat.
    mode is always present."""
    mode = s.get("mode", "weekly")
    out = {"id": s.get("id"), "name": s.get("name"), "mode": mode,
           "days_off": s.get("days_off", [])}
    if mode == "custom":
        out["custom_intervals"] = s.get("custom_intervals", [])
    else:
        out["days"] = s.get("days", [])
        out["horizon"] = s.get("horizon", {})
    if s.get("repeat", {}).get("count", 0):
        out["repeat"] = s["repeat"]
    return out


def _apply_ref_map(nodes, models, resources, operators, resolve, shifts=None, criterion=None) -> None:
    """Translate every registry reference (model/resource/operator/shift/closing day)
    in place, via resolve(kind, value) -> value. Node-to-node wires (node uids) are
    untouched. Shifts reference closing days: internally by date, exported by id.
    The generator node carries its shifts; the stopping criterion carries the
    per-model goals/probs."""
    for m in models:
        if m.get("parent"):
            m["parent"] = resolve("model", m["parent"])
    for o in operators:
        if o.get("shifts"):
            o["shifts"] = [resolve("shift", s) for s in o["shifts"]]
    for s in (shifts or []):
        if s.get("days_off"):
            s["days_off"] = [resolve("closing_day", d) for d in s["days_off"]]
    for n in nodes:
        k = n.get("kind")
        if k == "Buffer":
            n["valid_models"] = [resolve("model", x) for x in n.get("valid_models", [])]
        elif k == "PieceGenerator":
            n["shifts"] = [resolve("shift", s) for s in n.get("shifts", [])]
        elif k in ("Task", "ResourceTask"):
            if k == "Task":
                for mc in n.get("models_configs", []):
                    if "model" in mc:
                        mc["model"] = resolve("model", mc["model"])
                    for r in mc.get("resources", []):
                        if "resource" in r:
                            r["resource"] = resolve("resource", r["resource"])
            else:
                for fld in ("non_transformed_resources", "transformed_resources", "resources_out"):
                    for r in n.get(fld, []):
                        if "resource" in r:
                            r["resource"] = resolve("resource", r["resource"])
            for fld in ("operators", "loading_operators", "startup_operators"):
                for alt in n.get(fld, []):
                    for mem in alt:
                        if "operator" in mem:
                            mem["operator"] = resolve("operator", mem["operator"])
            n["task_shifts"] = [resolve("shift", s) for s in n.get("task_shifts", [])]

    if criterion is not None:
        for g in criterion.get("models_goals", []):
            if "model" in g:
                g["model"] = resolve("model", g["model"])
        for mp in criterion.get("models_probs", []):
            if "model" in mp:
                mp["model"] = resolve("model", mp["model"])


def _check_date_intervals(label, intervals, start_dt, problems):
    """Shared checks for a list of absolute {start, end} date intervals (shutdowns,
    custom shifts): dates parse, each ends after it starts, pairwise disjoint, and
    none begins before the simulation start date."""
    parsed = []
    for iv in intervals:
        d0 = parse_date_time(iv.get("start"))
        d1 = parse_date_time(iv.get("end"))
        if d0 is None or d1 is None:
            problems.append(f"{label}: dates must be 'dd-mm-yyyy hh:mm'.")
        elif d1 < d0:
            problems.append(f"{label}: an interval ends before it starts.")
        else:
            parsed.append((d0, d1))
    parsed.sort()
    for (a0, a1), (b0, b1) in zip(parsed, parsed[1:]):
        if not (min(a1, b1) < max(a0, b0)):
            problems.append(f"{label}: intervals overlap or touch (they must be pairwise disjoint).")
            break
    if start_dt is not None and parsed and parsed[0][0] < start_dt:
        problems.append(f"{label}: an interval begins before the simulation start date "
                        f"(would convert to negative minutes).")
    return parsed


def _cap_dialog_height(dialog, max_frac=0.9):
    """Keep a dialog reachable on small screens: if it is taller than the screen,
    move its content into a scroll area (with any trailing OK/Cancel box kept fixed
    below) and cap the height, so the buttons never fall off the bottom. Runs once."""
    if getattr(dialog, "_height_capped", False):
        return
    screen = QtWidgets.QApplication.primaryScreen()
    lay = dialog.layout()
    if screen is None or lay is None:
        return
    max_h = int(screen.availableGeometry().height() * max_frac)
    if dialog.sizeHint().height() <= max_h:
        return
    dialog._height_capped = True
    bb = None  # detach a trailing button box so it stays visible below the scroll area
    if lay.count():
        last = lay.itemAt(lay.count() - 1).widget()
        if isinstance(last, QtWidgets.QDialogButtonBox):
            bb = last
            lay.removeWidget(bb)
    content = QtWidgets.QWidget()
    content.setLayout(lay)  # reparents the whole layout into content; dialog loses its layout
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setWidget(content)
    outer = QtWidgets.QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(scroll)
    if bb is not None:
        bb.setParent(dialog)
        outer.addWidget(bb)
    dialog.setMaximumHeight(max_h)
    dialog.resize(dialog.width(), min(dialog.height() or max_h, max_h))


class _DialogHeightCapper(QtCore.QObject):
    """Application-wide: caps every custom config dialog to the screen height the
    first time it is shown (native message/file dialogs are left alone)."""
    _NATIVE = (QtWidgets.QMessageBox, QtWidgets.QFileDialog, QtWidgets.QInputDialog,
               QtWidgets.QColorDialog, QtWidgets.QFontDialog, QtWidgets.QProgressDialog)

    def eventFilter(self, obj, event):
        if (event.type() == QtCore.QEvent.Show and isinstance(obj, QtWidgets.QDialog)
                and not isinstance(obj, self._NATIVE)):
            _cap_dialog_height(obj)
        return False
