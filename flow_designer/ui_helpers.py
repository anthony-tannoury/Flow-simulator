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
    system = platform.system()
    if system == "Windows":
        return "flow_sim-windows-x86_64.exe"
    if system == "Darwin":
        return "flow_sim-macos-universal"
    return "flow_sim-linux-x86_64"


def bundled_cpp_engine() -> str | None:
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
    "LogNormal": [("mean", float, 1.0), ("sigma", float, 1.0)],
}


FUNCTION_SPECS = {
    "constant":    [("value", 0.0)],
    "linear":      [("x1", 0.0), ("y1", 0.0), ("x2", 1.0), ("y2", 1.0)],
    "exponential": [("x1", 0.0), ("y1", 1.0), ("x2", 1.0), ("y2", 2.0), ("limit", 0.0)],
    "step":        [("x1", 0.0), ("y1", 0.0), ("x2", 1.0), ("y2", 1.0), ("step_size", 1.0)],
}


COLLECTOR_TYPES = [
    "NON_DISCRIMINATING_GREEDY",
    "DISCRIMINATING_GREEDY",
    "NON_DISCRIMINATING_ALTRUISTIC",
    "DISCRIMINATING_ALTRUISTIC",
]

RESOURCE_COLLECTOR_TYPES = ["GREEDY", "ALTRUISTIC"]

ASSOCIATION_TYPES = [
    "PASSIVE",
    "ASSOCIATIVE",
    "DISSOCIATIVE",
]

SHUTDOWN_TYPES = ["NON_FLEXIBLE", "FLEXIBLE"]


BUFFER_TYPES = ["PASSAGE", "SCRAP", "EXIT"]


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
    text = str(name or "").replace("_", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    words = text.split()
    if not words:
        return ""
    joined = " ".join(w.lower() for w in words)
    return joined[0].upper() + joined[1:]


def to_canonical(value: str, canonical_items: list) -> str:
    for canonical in canonical_items:
        if value == canonical or value == sentence_case(canonical):
            return canonical
    return value


def fill_combo(combo, canonical_items: list, current: str | None = None) -> None:
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


    if out_kind == "Shutdowns" and out_port == "shutdowns":
        return in_kind in {"Task", "ResourceTask"} and in_port == "shutdowns"


    if out_kind == "Breakdown" and out_port == "breakdown":
        return in_kind in {"Task", "ResourceTask"} and in_port == "breakdowns"


    if out_kind == "Buffer" and out_port == "to_task":
        return in_kind == "Task" and in_port == "bufs_in"


    if out_kind in {"Task", "PieceGenerator", "Breakdown"} and out_port == "bufs_out":
        return in_kind in {"Buffer", "Router"} and in_port == "from_task"


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
    try:
        return datetime.strptime(str(text).strip(), PY_DATE_TIME_FORMAT)
    except Exception:
        return None


def parse_date(text):
    try:
        return datetime.strptime(str(text).strip(), PY_DATE_FORMAT)
    except Exception:
        return None


def closing_day_label(entry: dict) -> str:
    date = entry.get("date", "?")
    name = (entry.get("name") or "").strip()
    return f"{date} - {name}" if name else date


def _hhmm_to_min(text):
    hh, _, mm = str(text).partition(":")
    return as_int(hh) * 60 + as_int(mm)


def _min_to_hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _merge_day_intervals(intervals):
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def translate_shift_entry(entry, minutes, name=None):
    WEEK = 7 * 1440
    e = copy.deepcopy(entry)
    if name is not None:
        e["name"] = name


    days = e.get("days", [])
    per_day = [[] for _ in range(7)]
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


    delta = timedelta(minutes=minutes)
    if e.get("custom_intervals"):
        shifted = []
        for iv in e["custom_intervals"]:
            s, en = parse_date_time(iv.get("start")), parse_date_time(iv.get("end"))
            if s and en:
                shifted.append({"start": (s + delta).strftime(PY_DATE_TIME_FORMAT),
                                "end": (en + delta).strftime(PY_DATE_TIME_FORMAT)})
            else:
                shifted.append(dict(iv))
        e["custom_intervals"] = shifted

    return e


POLICY_OPTIONS = {
    "pending_carriers_pre_flexible_shutdowns": (["AbortPendingCarriers", "WaitForCarriers", "AbortOrWaitForCarriers"], "AbortPendingCarriers"),
    "pending_carrier_pre_task_shift_end": (["AbortPendingCarriers", "WaitForCarriers", "AbortOrWaitForCarriers"], "AbortPendingCarriers"),
    "operator_shift_constraint": (["ConstrainedByShift", "NotConstrainedByShift", "PartiallyConstrainedByShift"], "ConstrainedByShift"),
    "task_shift_constraint": (["ConstrainedByShift", "NotConstrainedByShift", "PartiallyConstrainedByShift"], "ConstrainedByShift"),
    "operators_self_conscious": (["Conscious", "Unconscious"], "Conscious"),
}


PIECE_POLICY_OPTIONS = {
    **POLICY_OPTIONS,
    "piece_exit_order": (["FirstInFirstOut", "FirstCreatedFirstOut"], "FirstInFirstOut"),
    "batch_model_choice": (["MostPresent", "FastestTaskDuration", "SmallestGapToMinCarrierCapacity"], "MostPresent"),
}


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
    seen = set()
    while model is not None and model not in seen:
        if model in valid_models:
            return True
        seen.add(model)
        model = parents.get(model)
    return False


def _takers_disjoint(a: set, b: set, parents: dict) -> bool:
    return not (any(_taker_can_take(a, m, parents) for m in b)
                or any(_taker_can_take(b, m, parents) for m in a))


def ensure_ids(entries: list, prefix: str, old_by_name: dict | None = None, key: str = "name") -> list:
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
    bb = None
    if lay.count():
        last = lay.itemAt(lay.count() - 1).widget()
        if isinstance(last, QtWidgets.QDialogButtonBox):
            bb = last
            lay.removeWidget(bb)
    content = QtWidgets.QWidget()
    content.setLayout(lay)
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
    _NATIVE = (QtWidgets.QMessageBox, QtWidgets.QFileDialog, QtWidgets.QInputDialog,
               QtWidgets.QColorDialog, QtWidgets.QFontDialog, QtWidgets.QProgressDialog)

    def eventFilter(self, obj, event):
        if (event.type() == QtCore.QEvent.Show and isinstance(obj, QtWidgets.QDialog)
                and not isinstance(obj, self._NATIVE)):
            _cap_dialog_height(obj)
        return False
