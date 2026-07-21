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

try:
    from . import results_mode
except ImportError:
    import results_mode

try:
    from NodeGraphQt import BackdropNode
except Exception:
    BackdropNode = None


# --- simulation engine selection (Python vs bundled C++) --------------------
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


# ============================================================
# Constants / type names
# ============================================================

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


# ============================================================
# Helpers
# ============================================================

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


# ============================================================
# Base simulation node
# ============================================================

class SimNode(BaseNode):
    __identifier__ = "simulation.flow"
    NODE_NAME = "Simulation node"

    kind = "SimNode"
    color = (70, 70, 70)

    def __init__(self):
        super().__init__()
        self.set_color(*self.color)
        self.create_property("uid", new_uid(self.kind.lower()))
        self.create_property("kind", self.kind)

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "position": [self.x_pos(), self.y_pos()],
            "properties": {},
        }


class ShutdownsNode(SimNode):
    """NonFlexibleShutdowns or FlexibleShutdowns (chosen via the type toggle).
    Intervals are edited in the card menu ('+ interval')."""
    NODE_NAME = "Shutdowns"
    kind = "Shutdowns"
    color = (125, 80, 130)

    def __init__(self):
        super().__init__()
        self.add_output("shutdowns", color=PORT_COLORS["shutdown"])
        # the on-card combo shows display labels; to_clean_json canonicalizes
        add_combo_input(self, "shutdown_type", "Type",
                        [sentence_case(t) for t in SHUTDOWN_TYPES], sentence_case("NON_FLEXIBLE"))
        self.create_property("mode", "custom")   # "custom" (explicit intervals) | "generator" (periodic)
        self.create_property("intervals", "[]")  # [{start, end}]
        self.create_property("generator", "{}")  # {in_between, duration, start, end}

    def to_clean_json(self) -> dict:
        mode = self.get_property("mode") if self.has_property("mode") else "custom"
        shutdown_type = self.get_property("shutdown_type") if self.has_property("shutdown_type") else "NON_FLEXIBLE"
        out = {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "shutdown_type": to_canonical(shutdown_type, SHUTDOWN_TYPES),
            "mode": mode,
            "position": [self.x_pos(), self.y_pos()],
        }
        # like shifts: only the fields the mode actually uses
        if mode == "generator":
            out["generator"] = get_property_json(self, "generator", {})
        else:
            out["intervals"] = get_property_json(self, "intervals", [])
        return out


class BufferNode(SimNode):
    NODE_NAME = "Buffer"
    kind = "Buffer"
    color = (60, 125, 90)

    def __init__(self):
        super().__init__()
        self.add_input("from_task", multi_input=True, color=PORT_COLORS["task"])
        self.add_output("to_task", multi_output=True, color=PORT_COLORS["buffer"])
        self.create_property("valid_models", "[]")
        self.create_property("buffer_type", "PASSAGE")  # PASSAGE | SCRAP | EXIT

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "valid_models": get_property_json(self, "valid_models", []),
            "buffer_type": self.get_property("buffer_type") if self.has_property("buffer_type") else "PASSAGE",
            "inputs_from": connected_refs_from_port(self, "from_task", "input"),
            "outputs_to": connected_refs_from_port(self, "to_task", "output"),
            "position": [self.x_pos(), self.y_pos()],
        }


class RouterNode(SimNode):
    NODE_NAME = "Router"
    kind = "Router"
    color = (60, 115, 125)

    def __init__(self):
        super().__init__()
        self.add_input("from_task", multi_input=True, color=PORT_COLORS["task"])
        self.add_output("to_buffers", multi_output=True, color=PORT_COLORS["buffer"])
        self.create_property("buffer_probs", "{}")  # {buffer_id: <time-function> | null}; null == freeloader (prob = 1 - others)

    def to_clean_json(self) -> dict:
        connected_buffers = connected_refs_from_port(self, "to_buffers", "output")
        prob_map = get_property_json(self, "buffer_probs", {})
        buffer_probs = [{"buffer": b, "probability": prob_map.get(b, {"kind": "constant", "value": 0.0})}
                        for b in connected_buffers]
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "inputs_from": connected_refs_from_port(self, "from_task", "input"),
            "buffer_probs": buffer_probs,
            "position": [self.x_pos(), self.y_pos()],
        }


class PieceGeneratorNode(SimNode):
    """PieceGenerator: the single source of pieces, wired to its outlets, emitting
    during the shifts chosen in its card menu. What it emits (models, and either
    goals or per-model rates) lives in the stopping criterion under Simulation
    Settings."""
    NODE_NAME = "Piece generator"
    kind = "PieceGenerator"
    color = (145, 80, 80)

    def __init__(self):
        super().__init__()
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.create_property("shifts", "[]")   # [shift_name]

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "shifts": get_property_json(self, "shifts", []),
            "outlets": get_output_refs(self, "bufs_out"),
            "position": [self.x_pos(), self.y_pos()],
        }


class TaskNode(SimNode):
    """PieceTask. Everything except the piece-flow wiring lives in the card menu:
    per-model configs, task-level durations, operator alternatives, scopes, policies,
    task shifts, carrier settings."""
    NODE_NAME = "Piece task"
    kind = "Task"
    color = (150, 90, 60)

    def __init__(self):
        super().__init__()
        self.add_input("bufs_in", multi_input=True, color=PORT_COLORS["buffer"])
        self.add_input("shutdowns", multi_input=True, color=PORT_COLORS["shutdown"])
        self.add_input("breakdowns", multi_input=True, color=PORT_COLORS["breakdown"])
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])

        # per-model: {model, duration:<sampler>, resources:[{resource,value}],
        #             min_carrier_capacity, max_carrier_capacity}
        self.create_property("models_configs", "[]")
        self.create_property("startup_duration", "")   # <sampler>
        self.create_property("loading_duration", "")   # <sampler>
        self.create_property("operators", "[]")        # <alternatives>
        self.create_property("loading_operators", "[]")
        self.create_property("startup_operators", "[]")
        self.create_property("task_shifts", "[]")      # [shift_name]
        self.create_property("policies", json.dumps(default_policies(PIECE_POLICY_OPTIONS)))
        self.create_property("operator_scope", "PER_BATCH")   # PER_BATCH | PER_TASK
        self.create_property("resource_scope", "PER_BATCH")   # PER_UNIT | PER_BATCH
        self.create_property("min_carriers", 1)
        self.create_property("max_capacity", 1.0)
        self.create_property("contiguous_carriers", False)
        self.create_property("independent_carriers", False)
        self.create_property("timeout", 1000000000.0)
        self.create_property("priority", 5)
        self.create_property("admin", False)   # administrative task: reporting classification only
        self.create_property("collector_type", "NON_DISCRIMINATING_GREEDY")

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "models_configs": get_property_json(self, "models_configs", []),
            "startup_duration": get_property_json(self, "startup_duration", None),
            "loading_duration": get_property_json(self, "loading_duration", None),
            "operators": get_property_json(self, "operators", []),
            "loading_operators": get_property_json(self, "loading_operators", []),
            "startup_operators": get_property_json(self, "startup_operators", []),
            "task_shifts": get_property_json(self, "task_shifts", []),
            "policies": get_property_json(self, "policies", {}),
            "operator_scope": self.get_property("operator_scope"),
            "resource_scope": self.get_property("resource_scope"),
            "min_carriers": as_int(self.get_property("min_carriers"), 1),
            "max_capacity": as_float(self.get_property("max_capacity"), 1.0),
            "contiguous_carriers": bool(self.get_property("contiguous_carriers")),
            "independent_carriers": bool(self.get_property("independent_carriers")),
            "timeout": self.get_property("timeout"),  # number of minutes | "inf"
            "priority": as_int(self.get_property("priority"), 5),
            "admin": bool(self.get_property("admin")),
            "collector_type": self.get_property("collector_type"),
            "bufs_in": connected_refs_from_port(self, "bufs_in", "input"),
            "bufs_out": get_output_refs(self, "bufs_out"),
            "shutdowns": connected_refs_from_port(self, "shutdowns", "input"),
            "breakdowns": connected_refs_from_port(self, "breakdowns", "input"),
            "position": [self.x_pos(), self.y_pos()],
        }


class ResourceTaskNode(SimNode):
    """ResourceTask. Consumes/transforms resources into output resources. No piece
    flow; breakdown and shutdown cards wire directly into it."""
    NODE_NAME = "Resource task"
    kind = "ResourceTask"
    color = (150, 120, 60)

    def __init__(self):
        super().__init__()
        self.add_input("shutdowns", multi_input=True, color=PORT_COLORS["shutdown"])
        self.add_input("breakdowns", multi_input=True, color=PORT_COLORS["breakdown"])

        self.create_property("non_transformed_resources", "[]")   # [{resource, value(quantity)}]
        self.create_property("transformed_resources", "[]")       # [{resource, proportion, salvageable}]
        self.create_property("resources_out", "[]")               # [{resource, distribution:<sampler>}]
        self.create_property("duration", "")                      # <sampler>
        self.create_property("startup_duration", "")              # <sampler>
        self.create_property("loading_duration", "")              # <sampler>
        self.create_property("operators", "[]")                   # <alternatives>
        self.create_property("loading_operators", "[]")
        self.create_property("startup_operators", "[]")
        self.create_property("task_shifts", "[]")
        self.create_property("policies", json.dumps(default_policies(POLICY_OPTIONS)))
        self.create_property("resource_scope", "PER_BATCH")
        self.create_property("operator_scope", "PER_BATCH")
        self.create_property("resource_collector_type", "GREEDY")
        self.create_property("min_carriers", 1)
        self.create_property("max_capacity", 1.0)
        self.create_property("min_carrier_capacity", 1.0)
        self.create_property("max_carrier_capacity", 1.0)
        self.create_property("contiguous_carriers", False)
        self.create_property("independent_carriers", False)
        self.create_property("timeout", 1000000000.0)
        self.create_property("priority", 5)
        self.create_property("admin", False)   # administrative task: reporting classification only

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "non_transformed_resources": get_property_json(self, "non_transformed_resources", []),
            "transformed_resources": get_property_json(self, "transformed_resources", []),
            "resources_out": get_property_json(self, "resources_out", []),
            "duration": get_property_json(self, "duration", None),
            "startup_duration": get_property_json(self, "startup_duration", None),
            "loading_duration": get_property_json(self, "loading_duration", None),
            "operators": get_property_json(self, "operators", []),
            "loading_operators": get_property_json(self, "loading_operators", []),
            "startup_operators": get_property_json(self, "startup_operators", []),
            "task_shifts": get_property_json(self, "task_shifts", []),
            "policies": get_property_json(self, "policies", {}),
            "resource_scope": self.get_property("resource_scope"),
            "operator_scope": self.get_property("operator_scope"),
            "resource_collector_type": self.get_property("resource_collector_type"),
            "min_carriers": as_int(self.get_property("min_carriers"), 1),
            "max_capacity": as_float(self.get_property("max_capacity"), 1.0),
            "min_carrier_capacity": as_float(self.get_property("min_carrier_capacity"), 1.0),
            "max_carrier_capacity": as_float(self.get_property("max_carrier_capacity"), 1.0),
            "contiguous_carriers": bool(self.get_property("contiguous_carriers")),
            "independent_carriers": bool(self.get_property("independent_carriers")),
            "timeout": self.get_property("timeout"),  # number of minutes | "inf"
            "priority": as_int(self.get_property("priority"), 5),
            "admin": bool(self.get_property("admin")),
            "shutdowns": connected_refs_from_port(self, "shutdowns", "input"),
            "breakdowns": connected_refs_from_port(self, "breakdowns", "input"),
            "position": [self.x_pos(), self.y_pos()],
        }


class BreakdownNode(SimNode):
    """Breakdown on a task. mtbf is a distribution or a bathtub failure-rate;
    mttr is a distribution. Piece-task breakdowns need lifeboat outlets."""
    NODE_NAME = "Breakdown"
    kind = "Breakdown"
    color = (150, 65, 85)

    def __init__(self):
        super().__init__()
        self.add_output("breakdown", color=PORT_COLORS["breakdown"])   # wires directly into the task
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.create_property("mtbf", "{}")   # {"mode": "distribution"|"bathtub", ...}
        self.create_property("mttr", "")     # <sampler>

    def to_clean_json(self) -> dict:
        tasks = get_output_refs(self, "breakdown")
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "task": tasks[0] if tasks else None,
            "mtbf": get_property_json(self, "mtbf", {}),
            "mttr": get_property_json(self, "mttr", None),
            "outlets": get_output_refs(self, "bufs_out"),
            "position": [self.x_pos(), self.y_pos()],
        }


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


# ============================================================
# Dialogs
# ============================================================

class ModelRegistryDialog(QtWidgets.QDialog):
    def __init__(self, parent, models: List[dict]):
        super().__init__(parent)
        self.setWindowTitle("Edit models")
        self.resize(520, 360)

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Model name", "Parent model"])
        self.table.horizontalHeader().setStretchLastSection(True)

        btn_add = QtWidgets.QPushButton("Add")
        btn_remove = QtWidgets.QPushButton("Remove selected")
        btn_ok = QtWidgets.QPushButton("OK")
        btn_cancel = QtWidgets.QPushButton("Cancel")

        top = QtWidgets.QHBoxLayout()
        top.addWidget(btn_add)
        top.addWidget(btn_remove)
        top.addStretch()

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(btn_ok)
        bottom.addWidget(btn_cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Define models once, then use pickers in cards."))
        layout.addWidget(self.table)
        layout.addLayout(top)
        layout.addLayout(bottom)

        btn_add.clicked.connect(self.add_row)
        btn_remove.clicked.connect(self.remove_selected)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        for m in models:
            self.add_row(m.get("name", ""), m.get("parent", ""))

    def add_row(self, name="", parent=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(parent or ""))

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def models(self) -> List[dict]:
        result = []
        seen = set()
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, 0)
            parent_item = self.table.item(r, 1)
            name = name_item.text().strip() if name_item else ""
            parent = parent_item.text().strip() if parent_item else ""
            if not name:
                continue
            if name in seen:
                raise ValueError(f"Duplicate model name: {name}")
            seen.add(name)
            result.append({"name": name, "parent": parent or None})

        valid = {m["name"] for m in result}
        for m in result:
            if m["parent"] and m["parent"] not in valid:
                raise ValueError(f"Parent model '{m['parent']}' is not defined.")
        return result

    def accept(self):
        try:
            self.models()
        except Exception as e:
            qmessage(self, "Invalid models", str(e), QtWidgets.QMessageBox.Warning)
            return
        super().accept()


# ============================================================
# Reusable distribution/function widget + the Resource / Operator /
# Shift registries. Distributions, resources, operators and shifts are
# configured in menus and picked by name inside the cards that use them.
# ============================================================

def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


class TimeFunctionWidget(QtWidgets.QWidget):
    """One numeric quantity that is either constant or a function of time.
    Value: {"kind": "constant"|"linear"|"exponential"|"step", ...float params...}."""

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.kind = QtWidgets.QComboBox()
        fill_combo(self.kind, list(FUNCTION_SPECS.keys()))
        lay.addWidget(self.kind)
        self._host = QtWidgets.QWidget()
        self._play = QtWidgets.QHBoxLayout(self._host)
        self._play.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        lay.addStretch(1)
        self._edits = {}
        self.kind.currentTextChanged.connect(self._rebuild)
        self.set_value(value or {"kind": "constant", "value": 0.0})

    def _rebuild(self, *_):
        _clear_layout(self._play)
        self._edits = {}
        for name, default in FUNCTION_SPECS[self.kind.currentData()]:
            self._play.addWidget(QtWidgets.QLabel(f"{sentence_case(name)}:"))
            e = QtWidgets.QLineEdit(str(default))
            e.setMaximumWidth(64)
            self._play.addWidget(e)
            self._edits[name] = e

    def set_value(self, value):
        value = value or {}
        kind = to_canonical(value.get("kind", "constant"), list(FUNCTION_SPECS.keys()))
        if kind not in FUNCTION_SPECS:
            kind = "constant"
        blocked = self.kind.blockSignals(True)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.kind.blockSignals(blocked)
        self._rebuild()
        for name, _ in FUNCTION_SPECS[kind]:
            if name in value and name in self._edits:
                self._edits[name].setText(str(value[name]))

    def get_value(self):
        kind = self.kind.currentData()
        out = {"kind": kind}
        for name, _ in FUNCTION_SPECS[kind]:
            out[name] = as_float(self._edits[name].text())
        return out


class SamplerWidget(QtWidgets.QWidget):
    """A distribution whose every parameter is a TimeFunctionWidget.
    Value: {"dist_type": <name>, "params": {<pname>: <time-function>}}."""

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Type:"))
        self.dist = QtWidgets.QComboBox()
        fill_combo(self.dist, list(DISTRIBUTION_SPECS.keys()))
        top.addWidget(self.dist)
        top.addStretch(1)
        lay.addLayout(top)
        self._host = QtWidgets.QWidget()
        self._form = QtWidgets.QFormLayout(self._host)
        self._form.setContentsMargins(12, 2, 0, 0)
        lay.addWidget(self._host)
        self._params = {}
        self.dist.currentTextChanged.connect(self._rebuild)
        self.set_value(value or {"dist_type": "Constant",
                                 "params": {"value": {"kind": "constant", "value": 0.0}}})

    def _rebuild(self, *_):
        while self._form.rowCount():
            self._form.removeRow(0)
        self._params = {}
        for pname, _ptype, pdefault in DISTRIBUTION_SPECS[self.dist.currentData()]:
            tf = TimeFunctionWidget(value={"kind": "constant", "value": pdefault})
            self._form.addRow(sentence_case(pname), tf)
            self._params[pname] = tf

    def set_value(self, value):
        value = value or {}
        dist_type = to_canonical(value.get("dist_type", "Constant"), list(DISTRIBUTION_SPECS.keys()))
        if dist_type not in DISTRIBUTION_SPECS:
            dist_type = "Constant"
        blocked = self.dist.blockSignals(True)
        self.dist.setCurrentIndex(max(0, self.dist.findData(dist_type)))
        self.dist.blockSignals(blocked)
        self._rebuild()
        params = value.get("params", {})
        for pname, tf in self._params.items():
            if pname in params:
                tf.set_value(params[pname])

    def get_value(self):
        return {"dist_type": self.dist.currentData(),
                "params": {n: w.get_value() for n, w in self._params.items()}}


class InfFloatWidget(QtWidgets.QWidget):
    """A float that can also be infinite (checkbox). Value: number or the string "inf"."""

    def __init__(self, value="inf", parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.chk = QtWidgets.QCheckBox("Infinite")
        self.edit = QtWidgets.QLineEdit()
        self.edit.setMaximumWidth(90)
        lay.addWidget(self.chk)
        lay.addWidget(self.edit)
        lay.addStretch(1)
        self.chk.toggled.connect(self.edit.setDisabled)
        self.set_value(value)

    def set_value(self, value):
        infinite = (value in ("inf", "Infinity") or (isinstance(value, float) and value == float("inf")))
        self.chk.setChecked(infinite)
        self.edit.setDisabled(infinite)
        self.edit.setText("" if infinite else str(value))

    def get_value(self):
        return "inf" if self.chk.isChecked() else as_float(self.edit.text())


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class HourMinuteWidget(QtWidgets.QWidget):
    """A time of day entered as hours + minutes; the stored/returned value is an
    "hh:mm" string (e.g. "08:30")."""

    def __init__(self, value="00:00", parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.h = QtWidgets.QLineEdit(); self.h.setMaximumWidth(48)
        self.m = QtWidgets.QLineEdit(); self.m.setMaximumWidth(48)
        lay.addWidget(self.h); lay.addWidget(QtWidgets.QLabel("h"))
        lay.addWidget(self.m); lay.addWidget(QtWidgets.QLabel("m"))
        lay.addStretch(1)
        self.set_value(value)

    def set_value(self, value):
        hh, _, mm = str(value).partition(":")
        self.h.setText(str(as_int(hh))); self.m.setText(str(as_int(mm)))

    def get_value(self):
        return f"{as_int(self.h.text()):02d}:{as_int(self.m.text()):02d}"


# Absolute calendar formats. Date+time is used by the simulation start date, custom
# shift intervals, shutdown intervals and the ByTime stopping date; date-only is used
# by days off and shift horizons. Qt syntax below; Python strptime equivalents follow.
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


class DateTimeWidget(QtWidgets.QDateTimeEdit):
    """Calendar-popup picker for an absolute 'dd-mm-yyyy hh:mm'. The value travels
    as that string; converting it to raw simulation minutes (relative to the
    simulation start date) is the loader's job."""

    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat(DATE_TIME_FORMAT)
        self.set_value(value)

    def set_value(self, value):
        dt = QtCore.QDateTime.fromString(str(value or ""), DATE_TIME_FORMAT)
        if not dt.isValid():
            dt = QtCore.QDateTime(QtCore.QDate(2026, 1, 1), QtCore.QTime(0, 0))
        self.setDateTime(dt)

    def get_value(self):
        return self.dateTime().toString(DATE_TIME_FORMAT)


class DateWidget(QtWidgets.QDateEdit):
    """Calendar-popup picker for a date-only 'dd-mm-yyyy' (days off, horizons)."""

    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat(DATE_FORMAT)
        self.set_value(value)

    def set_value(self, value):
        d = QtCore.QDate.fromString(str(value or ""), DATE_FORMAT)
        if not d.isValid():
            d = QtCore.QDate(2026, 1, 1)
        self.setDate(d)

    def get_value(self):
        return self.date().toString(DATE_FORMAT)


def closing_day_label(entry: dict) -> str:
    """Display text for a closing-day registry entry: the date, plus the optional label."""
    date = entry.get("date", "?")
    name = (entry.get("name") or "").strip()
    return f"{date} - {name}" if name else date


class ClosingDayPickerWidget(QtWidgets.QListWidget):
    """Multi-select over the closing-days registry; the value is the chosen dates."""

    def __init__(self, closing_days, chosen=None, parent=None):
        super().__init__(parent)
        chosen = set(chosen or [])
        known = set()
        for entry in (closing_days or []):
            it = QtWidgets.QListWidgetItem(closing_day_label(entry))
            it.setData(QtCore.Qt.UserRole, entry.get("date"))
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if entry.get("date") in chosen else QtCore.Qt.Unchecked)
            self.addItem(it)
            known.add(entry.get("date"))
        # a day the shift still references but the registry no longer holds: keep it
        # visible (checked) so unchecking it is a deliberate act, not a silent loss
        for date in sorted(chosen - known):
            it = QtWidgets.QListWidgetItem(f"{date} (not in registry)")
            it.setData(QtCore.Qt.UserRole, date)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked)
            self.addItem(it)

    def value(self):
        return [self.item(i).data(QtCore.Qt.UserRole) for i in range(self.count())
                if self.item(i).checkState() == QtCore.Qt.Checked]


class _IntervalRow(QtWidgets.QWidget):
    def __init__(self, start="08:00", end="17:00", on_remove=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.start = HourMinuteWidget(start)
        self.end = HourMinuteWidget(end)
        lay.addWidget(QtWidgets.QLabel("Start:")); lay.addWidget(self.start)
        lay.addWidget(QtWidgets.QLabel("End:")); lay.addWidget(self.end)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        if on_remove:
            rm.clicked.connect(lambda: on_remove(self))
        lay.addWidget(rm); lay.addStretch(1)

    def data(self):
        return {"start": self.start.get_value(), "end": self.end.get_value()}


class _DayRow(QtWidgets.QWidget):
    """One weekday: a working toggle + a list of shift intervals (edited as h/m of day)."""

    def __init__(self, label, working=False, intervals=None, parent=None):
        super().__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.chk = QtWidgets.QCheckBox(label)
        self.chk.setMinimumWidth(48)
        outer.addWidget(self.chk, 0, QtCore.Qt.AlignTop)
        self._box = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._box)
        self._vl.setContentsMargins(0, 0, 0, 0)
        self._rows = []
        add = QtWidgets.QPushButton("+ interval")
        add.clicked.connect(lambda: self._add())
        self._vl.addWidget(add)
        outer.addWidget(self._box, 1)
        self.chk.toggled.connect(self._box.setEnabled)
        for iv in (intervals or []):
            self._add(iv.get("start", "08:00"), iv.get("end", "17:00"))
        self.chk.setChecked(working)
        self._box.setEnabled(working)

    def _add(self, start="08:00", end="17:00"):
        row = _IntervalRow(start, end, on_remove=self._remove)
        self._rows.append(row)
        self._vl.insertWidget(self._vl.count() - 1, row)  # keep the add button last

    def _remove(self, row):
        if row in self._rows:
            self._rows.remove(row)
            row.setParent(None)
            row.deleteLater()

    def data(self):
        return {"working": self.chk.isChecked(),
                "intervals": [r.data() for r in self._rows]}


class _CustomIntervalRow(QtWidgets.QWidget):
    """One absolute interval: start date+time -> end date+time (dd-mm-yyyy hh:mm)."""

    def __init__(self, start="01-01-2026 08:00", end="01-01-2026 17:00", on_remove=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.start = DateTimeWidget(start)
        self.end = DateTimeWidget(end)
        lay.addWidget(QtWidgets.QLabel("From:")); lay.addWidget(self.start)
        lay.addWidget(QtWidgets.QLabel("To:")); lay.addWidget(self.end)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        if on_remove:
            rm.clicked.connect(lambda: on_remove(self))
        lay.addWidget(rm); lay.addStretch(1)

    def data(self):
        return {"start": self.start.get_value(), "end": self.end.get_value()}


class CustomIntervalListWidget(QtWidgets.QWidget):
    """A list of absolute {start, end} date intervals with '+ interval'."""

    def __init__(self, intervals=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ interval"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        self._rows = []
        for iv in (intervals or []):
            self._add(iv.get("start"), iv.get("end"))

    def _add(self, start=None, end=None):
        row = _CustomIntervalRow(start or "01-01-2026 08:00", end or "01-01-2026 17:00",
                                 on_remove=self._remove)
        self._rows.append(row); self._vl.addWidget(row)

    def _remove(self, row):
        if row in self._rows:
            self._rows.remove(row); row.setParent(None); row.deleteLater()

    def value(self):
        return [r.data() for r in self._rows]


class ShiftEditorDialog(QtWidgets.QDialog):
    """A shift definition is either 'weekly' (the recurring weekday creator) or
    'custom' (an explicit list of absolute date intervals). A type dropdown picks
    the mode and the matching parameters appear below it; both configurations are
    kept in the entry. Days off are shared: one list of whole days, either mode,
    picked from the closing-days registry (Registries > Edit closing days...)."""

    SHIFT_MODES = [("Weekly", "weekly"), ("Custom", "custom")]

    def __init__(self, parent=None, entry=None, closing_days=None):
        super().__init__(parent)
        self.setWindowTitle("Shift definition")
        entry = entry or {}
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(entry.get("name", ""))
        form.addRow("Name", self.name)
        self.mode = QtWidgets.QComboBox()
        for label, canonical in self.SHIFT_MODES:
            self.mode.addItem(label, canonical)
        form.addRow("Type", self.mode)
        lay.addLayout(form)
        self._stack = QtWidgets.QStackedWidget()
        lay.addWidget(self._stack)

        # --- Weekly page: the recurring weekday creator ---
        weekly = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(weekly)
        wl.addWidget(QtWidgets.QLabel("Shifts per weekday (times of day as hours + minutes):"))
        days = entry.get("days", [])
        self.day_rows = []
        for i, label in enumerate(WEEKDAYS):
            d = days[i] if i < len(days) else {}
            row = _DayRow(label, d.get("working", False), d.get("intervals"))
            self.day_rows.append(row)
            wl.addWidget(row)
        form2 = QtWidgets.QFormLayout()
        hz = entry.get("horizon", {})
        hbox = QtWidgets.QHBoxLayout()
        self.h_start = DateWidget(hz.get("start"))
        self.h_end = DateWidget(hz.get("end"))
        hbox.addWidget(QtWidgets.QLabel("From day:")); hbox.addWidget(self.h_start)
        hbox.addWidget(QtWidgets.QLabel("To day:")); hbox.addWidget(self.h_end); hbox.addStretch(1)
        hw = QtWidgets.QWidget(); hw.setLayout(hbox)
        form2.addRow("Horizon", hw)
        wl.addLayout(form2)
        self._stack.addWidget(weekly)

        # --- Custom page: absolute date intervals (the loader converts them to raw
        #     minutes relative to the simulation start date) ---
        custom = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(custom)
        cl.addWidget(QtWidgets.QLabel("Absolute intervals (dd-mm-yyyy hh:mm). They are converted to\n"
                                      "minutes relative to the simulation start date (Simulation > Settings...)."))
        self.custom = CustomIntervalListWidget(entry.get("custom_intervals", []))
        cl.addWidget(self.custom)
        cl.addStretch(1)
        self._stack.addWidget(custom)

        self.mode.currentIndexChanged.connect(self._stack.setCurrentIndex)
        mi = self.mode.findData(entry.get("mode", "weekly"))
        self.mode.setCurrentIndex(mi if mi >= 0 else 0)
        self._stack.setCurrentIndex(self.mode.currentIndex())

        # --- Days off: shared by both modes, chosen from the closing-days registry ---
        closing_days = closing_days or []
        if closing_days:
            lay.addWidget(QtWidgets.QLabel("Days off (check closing days; applies in either mode):"))
        else:
            lay.addWidget(QtWidgets.QLabel("Days off: the closing-days registry is empty\n"
                                           "(add days in Registries > Edit closing days...)."))
        self.days_off = ClosingDayPickerWidget(closing_days, chosen=entry.get("days_off", []))
        self.days_off.setMaximumHeight(140)
        lay.addWidget(self.days_off)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def data(self):
        return {
            "name": self.name.text().strip(),
            "mode": self.mode.currentData(),
            "days": [r.data() for r in self.day_rows],
            "days_off": self.days_off.value(),
            "horizon": {"start": self.h_start.get_value(), "end": self.h_end.get_value()},
            "custom_intervals": self.custom.value(),
        }


class OperatorEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, entry=None, shift_names=None):
        super().__init__(parent)
        self.setWindowTitle("Operator group")
        entry = entry or {}
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(entry.get("name", ""))
        self.capacity = QtWidgets.QLineEdit(str(entry.get("capacity", 1)))
        form.addRow("Name", self.name)
        form.addRow("Capacity (number of operators)", self.capacity)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("Productivity:"))
        self.prod = SamplerWidget(entry.get("productivity"))
        lay.addWidget(self.prod)
        lay.addWidget(QtWidgets.QLabel("Shifts (their concatenation is the group's schedule):"))
        self.shifts = QtWidgets.QListWidget()
        chosen = set(entry.get("shifts", []))
        for nm in (shift_names or []):
            it = QtWidgets.QListWidgetItem(nm)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if nm in chosen else QtCore.Qt.Unchecked)
            self.shifts.addItem(it)
        lay.addWidget(self.shifts)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def data(self):
        shifts = [self.shifts.item(i).text() for i in range(self.shifts.count())
                  if self.shifts.item(i).checkState() == QtCore.Qt.Checked]
        return {
            "name": self.name.text().strip(),
            "capacity": as_int(self.capacity.text(), 1),
            "productivity": self.prod.get_value(),
            "shifts": shifts,
        }


class ResourceEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, entry=None):
        super().__init__(parent)
        self.setWindowTitle("Resource")
        entry = entry or {}
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(entry.get("name", ""))
        self.lifespan = InfFloatWidget(entry.get("lifespan", "inf"))
        self.max_cap = QtWidgets.QLineEdit(str(entry.get("max_capacity", 1.0)))
        self.init_cap = QtWidgets.QLineEdit(str(entry.get("initial_capacity", entry.get("max_capacity", 1.0))))
        self.restockable = QtWidgets.QCheckBox("Restockable")
        form.addRow("Name", self.name)
        form.addRow("Lifespan", self.lifespan)
        form.addRow("Max storage capacity", self.max_cap)
        form.addRow("Initial capacity (in [0, max])", self.init_cap)
        form.addRow("", self.restockable)
        lay.addLayout(form)
        self.restock_box = QtWidgets.QGroupBox("Restocking")
        rlay = QtWidgets.QVBoxLayout(self.restock_box)
        rlay.addWidget(QtWidgets.QLabel("Order duration:"))
        self.order = SamplerWidget(entry.get("order_duration"))
        rlay.addWidget(self.order)
        rlay.addWidget(QtWidgets.QLabel("Delivery duration:"))
        self.delivery = SamplerWidget(entry.get("delivery_duration"))
        rlay.addWidget(self.delivery)
        tform = QtWidgets.QFormLayout()
        self.threshold = QtWidgets.QLineEdit(str(entry.get("threshold", 0.0)))
        tform.addRow("Reorder threshold", self.threshold)
        rlay.addLayout(tform)
        lay.addWidget(self.restock_box)
        self.restockable.toggled.connect(self.restock_box.setVisible)
        self.restockable.setChecked(bool(entry.get("restockable", False)))
        self.restock_box.setVisible(self.restockable.isChecked())
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def data(self):
        out = {
            "name": self.name.text().strip(),
            "restockable": self.restockable.isChecked(),
            "lifespan": self.lifespan.get_value(),
            "max_capacity": as_float(self.max_cap.text()),
            "initial_capacity": as_float(self.init_cap.text()),
        }
        if self.restockable.isChecked():
            out["order_duration"] = self.order.get_value()
            out["delivery_duration"] = self.delivery.get_value()
            out["threshold"] = as_float(self.threshold.text())
        return out


class _RegistryDialog(QtWidgets.QDialog):
    """List of named entries with Add / Edit / Remove; subclasses supply the item editor."""
    reg_title = "Registry"

    def __init__(self, parent=None, entries=None):
        super().__init__(parent)
        self.setWindowTitle(self.reg_title)
        self.resize(560, 420)
        self._entries = [dict(e) for e in (entries or [])]
        lay = QtWidgets.QVBoxLayout(self)
        self.listw = QtWidgets.QListWidget()
        self.listw.itemDoubleClicked.connect(lambda *_: self._edit())
        lay.addWidget(self.listw)
        btns = QtWidgets.QHBoxLayout()
        for label, cb in [("Add", self._add), ("Edit", self._edit), ("Remove", self._remove)]:
            b = QtWidgets.QPushButton(label); b.clicked.connect(cb); btns.addWidget(b)
        lay.addLayout(btns)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._refresh()

    def _refresh(self):
        self.listw.clear()
        for e in self._entries:
            self.listw.addItem(e.get("name", "(unnamed)"))

    def _make_editor(self, entry):
        raise NotImplementedError

    def _add(self):
        dlg = self._make_editor(None)
        if dlg.exec():
            self._entries.append(dlg.data()); self._refresh()

    def _edit(self):
        row = self.listw.currentRow()
        if row < 0:
            return
        dlg = self._make_editor(self._entries[row])
        if dlg.exec():
            self._entries[row] = dlg.data(); self._refresh()

    def _remove(self):
        row = self.listw.currentRow()
        if row >= 0:
            del self._entries[row]; self._refresh()

    def entries(self):
        return self._entries


class ResourceRegistryDialog(_RegistryDialog):
    reg_title = "Resources"

    def _make_editor(self, entry):
        return ResourceEditorDialog(self, entry)


class ShiftRegistryDialog(_RegistryDialog):
    reg_title = "Shifts"

    def __init__(self, parent=None, entries=None, closing_days=None):
        self._closing_days = closing_days or []
        super().__init__(parent, entries)

    def _make_editor(self, entry):
        return ShiftEditorDialog(self, entry, closing_days=self._closing_days)


class ClosingDaysRegistryDialog(QtWidgets.QDialog):
    """The closing-days registry: whole days the factory is closed, defined once and
    picked (multi-select) inside every shift definition. Edited in place — a row per
    day: date picker + optional label — so adding many days stays cheap."""

    def __init__(self, parent=None, entries=None):
        super().__init__(parent)
        self.setWindowTitle("Closing days")
        self.resize(480, 420)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Days the factory is closed. Shifts pick their days off from this list."))
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True)
        self._host = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._host)
        self._vl.setContentsMargins(0, 0, 0, 0)
        self._vl.addStretch(1)
        scroll.setWidget(self._host)
        lay.addWidget(scroll)
        add = QtWidgets.QPushButton("+ Closing day")
        add.clicked.connect(lambda: self._add())
        lay.addWidget(add)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._rows = []
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = dict(entry or {})
        row = QtWidgets.QWidget()
        rl = QtWidgets.QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
        picker = DateWidget(entry.get("date") or "01-01-2026")
        label = QtWidgets.QLineEdit(entry.get("name", ""))
        label.setPlaceholderText("Label (optional)")
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        rl.addWidget(picker); rl.addWidget(label, 1); rl.addWidget(rm)
        rec = (row, picker, label, entry)  # entry kept so an existing id survives edits
        rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec)
        self._vl.insertWidget(self._vl.count() - 1, row)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def entries(self):
        out = []
        for _, picker, label, entry in self._rows:
            e = dict(entry)
            e["date"] = picker.get_value()
            e["name"] = label.text().strip()
            out.append(e)
        out.sort(key=lambda e: (parse_date(e.get("date")) or datetime.max, e.get("date", "")))
        return out


class OperatorRegistryDialog(_RegistryDialog):
    reg_title = "Operators"

    def __init__(self, parent=None, entries=None, shift_names=None):
        self._shift_names = shift_names or []
        super().__init__(parent, entries)

    def _make_editor(self, entry):
        return OperatorEditorDialog(self, entry, self._shift_names)


# ============================================================
# Selection widgets that reference the registries
# ============================================================

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


class ModelTreeWidget(QtWidgets.QTreeWidget):
    """Checkable model hierarchy with cascade: checking a model checks all its
    descendants; unchecking a model unchecks its ancestors. If leaves_only, only
    childless models are selectable (used by the piece generator)."""

    def __init__(self, model_registry, checked=None, leaves_only=False, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self._leaves_only = leaves_only
        self._items = {}
        checked = set(checked or [])
        children_of = {}
        for m in model_registry:
            children_of.setdefault(m.get("parent"), []).append(m["name"])
        has_children = {m["name"]: bool(children_of.get(m["name"])) for m in model_registry}

        def add(name, parent_item):
            item = QtWidgets.QTreeWidgetItem(parent_item, [name])
            selectable = not (leaves_only and has_children.get(name))
            if selectable:
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(0, QtCore.Qt.Checked if name in checked else QtCore.Qt.Unchecked)
            else:
                item.setFlags(QtCore.Qt.ItemIsEnabled)
            self._items[name] = item
            for child in children_of.get(name, []):
                add(child, item)

        for root in children_of.get(None, []):
            add(root, self)
        self.expandAll()
        self._guard = False
        self.itemChanged.connect(self._on_changed)

    def _on_changed(self, item, col):
        if self._guard:
            return
        self._guard = True
        state = item.checkState(0)
        if state == QtCore.Qt.Checked:
            self._set_descendants(item, QtCore.Qt.Checked)
        else:
            # unchecking a node unchecks its ancestors (child deselect -> parent deselect)
            p = item.parent()
            while p is not None:
                if p.flags() & QtCore.Qt.ItemIsUserCheckable:
                    p.setCheckState(0, QtCore.Qt.Unchecked)
                p = p.parent()
        self._guard = False

    def _set_descendants(self, item, state):
        for i in range(item.childCount()):
            ch = item.child(i)
            if ch.flags() & QtCore.Qt.ItemIsUserCheckable:
                ch.setCheckState(0, state)
            self._set_descendants(ch, state)

    def checked_models(self):
        return [name for name, it in self._items.items()
                if (it.flags() & QtCore.Qt.ItemIsUserCheckable) and it.checkState(0) == QtCore.Qt.Checked]


class ShiftPickerWidget(QtWidgets.QListWidget):
    """Multi-select of shift-definition names (their concatenation is the schedule)."""

    def __init__(self, shift_names, chosen=None, parent=None):
        super().__init__(parent)
        chosen = set(chosen or [])
        for nm in shift_names:
            it = QtWidgets.QListWidgetItem(nm)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if nm in chosen else QtCore.Qt.Unchecked)
            self.addItem(it)

    def chosen(self):
        return [self.item(i).text() for i in range(self.count())
                if self.item(i).checkState() == QtCore.Qt.Checked]


class ResourcePickerWidget(QtWidgets.QWidget):
    """Rows of (resource-name, float). Used for per-model resources (quantity) and
    resource-task non-transformed inputs (quantity). Value: [{"resource","value"}]."""

    def __init__(self, resource_names, value_label="quantity", entries=None,
                 add_label="resource", integer=False, parent=None):
        super().__init__(parent)
        self._names = list(resource_names)
        self._label = value_label
        self._int = integer
        self._rows = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._host)
        self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton(f"+ {sentence_case(add_label)}")
        add.clicked.connect(lambda: self._add())
        lay.addWidget(add)
        for e in (entries or []):
            self._add(e.get("resource"), e.get("value", e.get("quantity", e.get("proportion", 1.0))))

    def _add(self, resource=None, value=1.0):
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if resource in self._names:
            combo.setCurrentText(resource)
        text = str(as_int(value)) if self._int else str(value)  # counts show as 1, not 1.0
        edit = QtWidgets.QLineEdit(text); edit.setMaximumWidth(70)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        h.addWidget(combo); h.addWidget(QtWidgets.QLabel(sentence_case(self._label) + ":")); h.addWidget(edit); h.addWidget(rm); h.addStretch(1)
        entry = (row, combo, edit)
        rm.clicked.connect(lambda: self._remove(entry))
        self._rows.append(entry)
        self._vl.addWidget(row)

    def _remove(self, entry):
        if entry in self._rows:
            self._rows.remove(entry)
            entry[0].setParent(None); entry[0].deleteLater()

    def entries(self):
        out = []
        for _, combo, edit in self._rows:
            if combo.currentText():
                value = as_int(edit.text()) if self._int else as_float(edit.text())
                out.append({"resource": combo.currentText(), "value": value})
        return out


class AlternativesWidget(QtWidgets.QWidget):
    """An operator Alternative = OR of ANDs. Each alternative is a set of
    (operator-group, count). Value: [[{"operator","count"}, ...], ...]."""

    def __init__(self, operator_names, value=None, parent=None):
        super().__init__(parent)
        self._names = list(operator_names)
        self._alts = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._host)
        self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ Alternative (OR)")
        add.clicked.connect(lambda: self._add_alt())
        lay.addWidget(add)
        for alt in (value or []):
            self._add_alt(alt)

    def _add_alt(self, members=None):
        box = QtWidgets.QGroupBox(f"Alternative {len(self._alts) + 1} (all needed together)")
        bl = QtWidgets.QVBoxLayout(box)
        picker = ResourcePickerWidget(self._names, value_label="count", add_label="operator group", integer=True,
                                      entries=[{"resource": m.get("operator"), "value": m.get("count", 1)} for m in (members or [])])
        bl.addWidget(picker)
        rm = QtWidgets.QPushButton("Remove alternative")
        bl.addWidget(rm)
        entry = (box, picker)
        rm.clicked.connect(lambda: self._remove_alt(entry))
        self._alts.append(entry)
        self._vl.addWidget(box)

    def _remove_alt(self, entry):
        if entry in self._alts:
            self._alts.remove(entry)
            entry[0].setParent(None); entry[0].deleteLater()

    def get_value(self):
        out = []
        for _, picker in self._alts:
            members = [{"operator": e["resource"], "count": int(e["value"])} for e in picker.entries()]
            if members:
                out.append(members)
        return out


class PoliciesWidget(QtWidgets.QWidget):
    """The task protocols with their defaults (POLICY_OPTIONS for resource tasks,
    PIECE_POLICY_OPTIONS for piece tasks). Types listed in POLICY_TYPE_PARAMS
    expose their numeric parameter (AbortOrWaitForCarriers' tolerance_fraction,
    PartiallyConstrainedByShift's tolerance in time units past the shift end).
    Value: {protocol_name: {"type", ...param}}."""

    def __init__(self, value=None, parent=None, policy_options=None):
        super().__init__(parent)
        value = value or {}
        form = QtWidgets.QFormLayout(self)
        self._combos = {}
        self._params = {}
        for name, (options, default) in (policy_options or POLICY_OPTIONS).items():
            row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            saved = value.get(name, {})
            saved_type = to_canonical(saved.get("type", default), options)
            combo = QtWidgets.QComboBox(); fill_combo(combo, options, saved_type)
            h.addWidget(combo)
            lbl = QtWidgets.QLabel("")
            edit = QtWidgets.QLineEdit(); edit.setMaximumWidth(60)
            saved_spec = POLICY_TYPE_PARAMS.get(saved_type)
            if saved_spec is not None and saved_spec[0] in saved:
                edit.setText(str(saved[saved_spec[0]]))
            h.addWidget(lbl); h.addWidget(edit); h.addStretch(1)
            self._combos[name] = combo
            self._params[name] = (lbl, edit)
            def _upd(_=None, n=name):
                spec = POLICY_TYPE_PARAMS.get(self._combos[n].currentData())
                p_lbl, p_edit = self._params[n]
                p_lbl.setVisible(spec is not None)
                p_edit.setVisible(spec is not None)
                if spec is not None:
                    p_lbl.setText(sentence_case(spec[1]) + ":")
                    if not p_edit.text():
                        p_edit.setText(str(spec[2]))
            combo.currentTextChanged.connect(_upd)
            _upd()
            form.addRow(sentence_case(name), row)

    def get_value(self):
        out = {}
        for name, combo in self._combos.items():
            t = combo.currentData()
            entry = {"type": t}
            spec = POLICY_TYPE_PARAMS.get(t)
            if spec is not None:
                entry[spec[0]] = as_float(self._params[name][1].text(), spec[2])
            out[name] = entry
        return out


# ============================================================
# Card menus (dialogs). They read/write node properties and reference
# the window's registries (models/resources/operators/shifts).
# ============================================================

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


# ------------------------------------------------------------------
# Registry ids. Internally the designer references models/resources/operators/
# shifts by name (unique per registry); the exported JSON references them by a
# stable id instead, so a rename or a duplicate name can never break a link.
# Names <-> ids are translated only at the export/import boundary.
# ------------------------------------------------------------------

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
    custom keeps custom_intervals; both keep days_off. mode is always present."""
    mode = s.get("mode", "weekly")
    out = {"id": s.get("id"), "name": s.get("name"), "mode": mode,
           "days_off": s.get("days_off", [])}
    if mode == "custom":
        out["custom_intervals"] = s.get("custom_intervals", [])
    else:
        out["days"] = s.get("days", [])
        out["horizon"] = s.get("horizon", {})
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


class ShutdownsMenuDialog(QtWidgets.QDialog):
    """Shutdown windows are either 'custom' (an explicit list of absolute date
    intervals) or 'generator' (periodic: every N minutes a shutdown of D minutes,
    placed inside the attached task's shifts by generate_periodic_shutdown). A
    mode dropdown picks which; both configurations are kept on the node."""

    MODES = [("Custom", "custom"), ("Generator", "generator")]

    def __init__(self, parent, node):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Shutdowns")
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.type = QtWidgets.QComboBox()
        fill_combo(self.type, SHUTDOWN_TYPES,
                   node.get_property("shutdown_type") if node.has_property("shutdown_type") else "NON_FLEXIBLE")
        form.addRow("Type", self.type)
        self.mode = QtWidgets.QComboBox()
        for label, canonical in self.MODES:
            self.mode.addItem(label, canonical)
        form.addRow("Intervals", self.mode)
        lay.addLayout(form)
        self._stack = QtWidgets.QStackedWidget()
        lay.addWidget(self._stack)

        # --- Custom page: the explicit interval list ---
        custom = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(custom); cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QtWidgets.QLabel("Intervals (absolute dates, dd-mm-yyyy hh:mm):"))
        self.intervals = CustomIntervalListWidget(get_property_json(node, "intervals", []))
        cl.addWidget(self.intervals)
        cl.addStretch(1)
        self._stack.addWidget(custom)

        # --- Generator page: periodic shutdowns placed inside the task's shifts ---
        gen = QtWidgets.QWidget()
        gl = QtWidgets.QVBoxLayout(gen); gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(QtWidgets.QLabel("Periodic: a shutdown every 'in between' minutes, placed inside\n"
                                      "the attached task's shifts (parser calls the shutdown generator)."))
        gform = QtWidgets.QFormLayout()
        g = get_property_json(node, "generator", {})
        default_start = g.get("start") or getattr(parent, "start_date", "")
        self.g_in_between = QtWidgets.QLineEdit(str(g.get("in_between", 480.0)))
        self.g_duration = QtWidgets.QLineEdit(str(g.get("duration", 30.0)))
        self.g_start = DateTimeWidget(default_start)
        self.g_end = DateTimeWidget(g.get("end", ""))
        gform.addRow("In between (minutes)", self.g_in_between)
        gform.addRow("Duration (minutes)", self.g_duration)
        gform.addRow("From", self.g_start)
        gform.addRow("To", self.g_end)
        gl.addLayout(gform)
        gl.addStretch(1)
        self._stack.addWidget(gen)

        self.mode.currentIndexChanged.connect(self._stack.setCurrentIndex)
        mi = self.mode.findData(node.get_property("mode") if node.has_property("mode") else "custom")
        self.mode.setCurrentIndex(mi if mi >= 0 else 0)
        self._stack.setCurrentIndex(self.mode.currentIndex())

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        # the on-card combo lists display labels, so store the label form
        self.node.set_property("shutdown_type", sentence_case(self.type.currentData()))
        self.node.set_property("mode", self.mode.currentData())
        set_property_json(self.node, "intervals", self.intervals.value())
        set_property_json(self.node, "generator", {
            "in_between": as_float(self.g_in_between.text()),
            "duration": as_float(self.g_duration.text()),
            "start": self.g_start.get_value(),
            "end": self.g_end.get_value(),
        })


class BufferMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node, model_registry):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Buffer")
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        lay.addWidget(tabs)

        # --- Buffer tab: valid models + type ---
        buf_tab = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(buf_tab)
        bl.addWidget(QtWidgets.QLabel("Valid models (selecting a model selects its children):"))
        self.models = ModelTreeWidget(model_registry, checked=get_property_json(node, "valid_models", []))
        bl.addWidget(self.models)
        form = QtWidgets.QFormLayout()
        self.buffer_type = QtWidgets.QComboBox()
        fill_combo(self.buffer_type, BUFFER_TYPES,
                   node.get_property("buffer_type") if node.has_property("buffer_type") else "PASSAGE")
        form.addRow("Type", self.buffer_type)
        bl.addLayout(form)
        tabs.addTab(buf_tab, "Buffer")

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "valid_models", self.models.checked_models())
        self.node.set_property("buffer_type", self.buffer_type.currentData())


class RouterMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Router probabilities")
        lay = QtWidgets.QVBoxLayout(self)
        self._buffers = connected_nodes_from_port(node, "to_buffers", "output")
        current = get_property_json(node, "buffer_probs", {})
        self._widgets = {}
        if not self._buffers:
            lay.addWidget(QtWidgets.QLabel("Wire this router's 'to_buffers' output into buffers first."))
        # At most one freeloader: its probability is 1 - sum(others), so its slot is greyed out.
        self.freeloader = QtWidgets.QComboBox()
        self.freeloader.addItem("(none)", None)
        for b in self._buffers:
            self.freeloader.addItem(b.name(), node_uid(b))
        free_bid = next((bid for bid, v in current.items() if v is None), None)
        fi = self.freeloader.findData(free_bid) if free_bid else 0
        self.freeloader.setCurrentIndex(fi if fi >= 0 else 0)
        if self._buffers:
            ff = QtWidgets.QFormLayout()
            ff.addRow("Freeloader", self.freeloader)
            lay.addLayout(ff)
        form = QtWidgets.QFormLayout()
        for b in self._buffers:
            bid = node_uid(b)
            tf = TimeFunctionWidget(current.get(bid) or {"kind": "constant", "value": 0.0})
            self._widgets[bid] = tf
            form.addRow(b.name(), tf)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("(probabilities are checked to sum to 1 when sampled)"))
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)
        self.freeloader.currentIndexChanged.connect(self._sync_freeloader)
        self._sync_freeloader()

    def _sync_freeloader(self, *_):
        free_bid = self.freeloader.currentData()
        for bid, w in self._widgets.items():
            w.setDisabled(bid == free_bid)

    def apply(self):
        free_bid = self.freeloader.currentData()
        set_property_json(self.node, "buffer_probs",
                          {bid: (None if bid == free_bid else w.get_value())
                           for bid, w in self._widgets.items()})


class FixedGoalsWidget(QtWidgets.QWidget):
    """One fixed goal box per leaf model: no add/remove, no model dropdown, so every
    leaf model is forced to carry an explicit production goal. A model absent from
    the prior entries starts at 0. Value: [{"model", "goal"}] over all leaf models."""

    def __init__(self, leaf_model_names, entries=None, parent=None):
        super().__init__(parent)
        prior = {e.get("model"): e.get("goal", e.get("value", 0)) for e in (entries or [])}
        self._rows = []
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        if not leaf_model_names:
            form.addRow(QtWidgets.QLabel("(define at least one leaf model first)"))
        for name in leaf_model_names:
            edit = QtWidgets.QLineEdit(str(prior.get(name, 0)))
            edit.setMaximumWidth(90)
            form.addRow(name, edit)
            self._rows.append((name, edit))

    def value(self):
        return [{"model": name, "goal": as_int(edit.text())} for name, edit in self._rows]


class GeneratorMenuDialog(QtWidgets.QDialog):
    """The piece generator's only card setting: the shifts during which it emits.
    What it emits (models + goals or per-model rates) lives in the stopping
    criterion under Simulation Settings."""

    def __init__(self, parent, node, shift_names):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Piece generator")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Shifts (when the generator emits pieces):"))
        self.shifts = ShiftPickerWidget(shift_names, get_property_json(node, "shifts", []))
        lay.addWidget(self.shifts)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "shifts", self.shifts.chosen())


class FixedModelProbsWidget(QtWidgets.QWidget):
    """One fixed probability row per leaf model, used by the rate generator: no
    add/remove, no model dropdown, so every leaf model is forced to carry an
    explicit emission probability (a constant or a function of time). At most one
    model may be marked the freeloader: its probability is 1 - sum(others), so
    its function slot is disabled and it is exported as None. A model absent from
    the prior entries starts at 0. Value: [{"model", "probability": <time-function>
    | None}] over all leaf models."""

    def __init__(self, leaf_model_names, entries=None, parent=None):
        super().__init__(parent)
        prior = {e.get("model"): e.get("probability") for e in (entries or [])}
        self._rows = []  # (name, tf, free_chk)
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        if not leaf_model_names:
            form.addRow(QtWidgets.QLabel("(define at least one leaf model first)"))
        for name in leaf_model_names:
            is_free = name in prior and prior[name] is None
            probability = prior.get(name) or {"kind": "constant", "value": 0.0}
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            tf = TimeFunctionWidget(probability)
            tf.setDisabled(is_free)
            free_chk = QtWidgets.QCheckBox("Freeloader"); free_chk.setChecked(is_free)
            h.addWidget(tf); h.addWidget(free_chk); h.addStretch(1)
            entry = (name, tf, free_chk)
            free_chk.toggled.connect(lambda checked, e=entry: self._on_free_toggled(e, checked))
            self._rows.append(entry)
            form.addRow(name, row)

    def _on_free_toggled(self, entry, checked):
        if checked:  # freeloader is exclusive: clear any other, re-enable its slot
            for e in self._rows:
                if e is not entry and e[2].isChecked():
                    blocked = e[2].blockSignals(True); e[2].setChecked(False); e[2].blockSignals(blocked)
                    e[1].setDisabled(False)
        entry[1].setDisabled(checked)

    def value(self):
        return [{"model": name, "probability": None if free_chk.isChecked() else tf.get_value()}
                for name, tf, free_chk in self._rows]


class SimulationSettingsDialog(QtWidgets.QDialog):
    """Simulation settings: the start date (the calendar anchor every absolute date
    is converted against, always set) and the stopping criterion, which carries the
    piece generator's mix (the generator's shifts live on its own card). Each
    criterion type has its own section:
      - Pieces produced: one integer goal per leaf model and a timeout in minutes.
        The run ends when every goal is met (or the timeout elapses first); pieces
        are paced to hit the goals over the generator's shifts.
      - Time: an explicit stop date, a gap (constant or a function of time) and a
        per-model probability mix (each constant or a function of time), with one
        model optionally left as the freeloader (probability 1 - sum(others))."""

    def __init__(self, parent, start_date, criterion, model_registry, seed=0):
        super().__init__(parent)
        self.setWindowTitle("Simulation settings")
        self._leaf_models = _leaf_model_names(model_registry)
        self._pending = criterion or {}
        lay = QtWidgets.QVBoxLayout(self)

        start_box = QtWidgets.QGroupBox("Start date (calendar anchor of t=0)")
        sl = QtWidgets.QHBoxLayout(start_box)
        self.start_date = DateTimeWidget(start_date or "01-01-2026 00:00")
        sl.addWidget(self.start_date); sl.addStretch(1)
        lay.addWidget(start_box)

        seed_box = QtWidgets.QGroupBox("Random seed (the same seed reproduces the same run)")
        sdl = QtWidgets.QHBoxLayout(seed_box)
        self.seed_edit = QtWidgets.QLineEdit(str(int(seed) if seed is not None else 0))
        self.seed_edit.setValidator(QtGui.QIntValidator(0, 2**31 - 1, self))
        self.seed_edit.setMaximumWidth(120)
        sdl.addWidget(self.seed_edit); sdl.addStretch(1)
        lay.addWidget(seed_box)

        crit_box = QtWidgets.QGroupBox("Stopping criterion and piece generation")
        cl = QtWidgets.QVBoxLayout(crit_box)
        top = QtWidgets.QFormLayout()
        self.type = QtWidgets.QComboBox()
        fill_combo(self.type, STOPPING_CRITERION_TYPES)
        top.addRow("Stop on", self.type)
        cl.addLayout(top)
        self._host = QtWidgets.QWidget()
        self._host_lay = QtWidgets.QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(12, 4, 0, 0)
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self._host)
        cl.addWidget(scroll)
        lay.addWidget(crit_box, 1)
        self._widgets = {}
        self.type.currentIndexChanged.connect(self._rebuild)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

        ci = self.type.findData(self._pending.get("type", "ByPiecesProduced"))
        blocked = self.type.blockSignals(True)
        self.type.setCurrentIndex(ci if ci >= 0 else 0)
        self.type.blockSignals(blocked)
        self._rebuild()

    def start_value(self):
        return self.start_date.get_value()

    def seed_value(self):
        text = self.seed_edit.text().strip()
        try:
            return int(text)
        except (ValueError, TypeError):
            return 0

    def _rebuild(self, *_):
        _clear_layout(self._host_lay)
        self._widgets = {}
        canonical = self.type.currentData()
        src = self._pending if self._pending.get("type") == canonical else {}
        if canonical == "ByPiecesProduced":
            self._build_pieces(src)
        elif canonical == "ByTime":
            self._build_time(src)
        self._host_lay.addStretch(1)

    def _add_row(self, label, widget):
        box = QtWidgets.QGroupBox(label)
        bl = QtWidgets.QVBoxLayout(box); bl.setContentsMargins(8, 4, 8, 8)
        bl.addWidget(widget)
        self._host_lay.addWidget(box)

    def _build_pieces(self, src):
        goals = FixedGoalsWidget(self._leaf_models, src.get("models_goals", []))
        self._widgets["goals"] = goals
        self._add_row("Model goals (one goal per leaf model)", goals)

        # Pacing: automatic (gap computed so the goals fill the shifts minus a grace
        # period) or a hand-set gap. A saved criterion carrying "gap" is manual.
        pacing = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(pacing)
        pl.setContentsMargins(0, 0, 0, 0)
        auto = QtWidgets.QCheckBox("Automatic (the goals are paced to fill the generator's shifts)")
        auto.setChecked(src.get("gap") is None)
        pl.addWidget(auto)
        grace = QtWidgets.QLineEdit(str(src.get("grace_period", 0.0)))
        grace.setMaximumWidth(90)
        grace_row = QtWidgets.QWidget()
        gl = QtWidgets.QHBoxLayout(grace_row); gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(QtWidgets.QLabel("Grace period (minutes; shift time kept free at the end for scrap remakes):"))
        gl.addWidget(grace); gl.addStretch(1)
        pl.addWidget(grace_row)
        gap = QtWidgets.QLineEdit(str(src.get("gap", 60.0)))
        gap.setMaximumWidth(90)
        gap_row = QtWidgets.QWidget()
        gpl = QtWidgets.QHBoxLayout(gap_row); gpl.setContentsMargins(0, 0, 0, 0)
        gpl.addWidget(QtWidgets.QLabel("Gap (minutes between two pieces):"))
        gpl.addWidget(gap); gpl.addStretch(1)
        pl.addWidget(gap_row)
        self._widgets["auto_gap"] = auto
        self._widgets["grace"] = grace
        self._widgets["gap"] = gap

        def _sync_pacing(*_):
            grace_row.setVisible(auto.isChecked())
            gap_row.setVisible(not auto.isChecked())
        auto.toggled.connect(_sync_pacing)
        _sync_pacing()
        self._add_row("Gap between pieces", pacing)

        timeout = InfFloatWidget(src.get("timeout", "inf"))
        self._widgets["timeout"] = timeout
        self._add_row("Timeout (minutes)", timeout)

    def _build_time(self, src):
        stop = DateTimeWidget(src.get("time", ""))  # an absolute stop date
        self._widgets["time"] = stop
        self._add_row("Stop at", stop)
        gap = TimeFunctionWidget(src.get("gap") or {"kind": "constant", "value": 1.0})
        self._widgets["gap"] = gap
        self._add_row("Gap between pieces (minutes; constant or function of time)", gap)
        probs = FixedModelProbsWidget(self._leaf_models, src.get("models_probs", []))
        self._widgets["probs"] = probs
        self._add_row("Model probabilities (one per leaf model; the freeloader gets 1 - sum of the others)", probs)

    def value(self):
        canonical = self.type.currentData()
        if canonical == "ByPiecesProduced":
            out = {"type": "ByPiecesProduced",
                   "timeout": self._widgets["timeout"].get_value()}  # minutes | "inf"
            if self._widgets["auto_gap"].isChecked():
                out["grace_period"] = as_float(self._widgets["grace"].text())
            else:
                out["gap"] = as_float(self._widgets["gap"].text())
            out["models_goals"] = self._widgets["goals"].value()
            return out
        return {"type": "ByTime",
                "time": self._widgets["time"].get_value(),  # "dd-mm-yyyy hh:mm"
                "gap": self._widgets["gap"].get_value(),
                "models_probs": self._widgets["probs"].value()}


class BreakdownMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Breakdown")
        lay = QtWidgets.QVBoxLayout(self)
        mtbf = get_property_json(node, "mtbf", {}) or {}
        lay.addWidget(QtWidgets.QLabel("MTBF (mean time between failures):"))
        self.mode = QtWidgets.QComboBox()
        fill_combo(self.mode, ["distribution", "bathtub"], mtbf.get("mode", "distribution"))
        lay.addWidget(self.mode)
        self.dist = SamplerWidget(mtbf.get("distribution"))
        lay.addWidget(self.dist)
        self.bathtub_box = QtWidgets.QGroupBox("Bathtub failure-rate a·e^(t/tau)+c+(beta/eta)(t/eta)^(beta-1)")
        bl = QtWidgets.QFormLayout(self.bathtub_box)
        self.bt = {}
        for k, d in (("a", 0.001), ("tau", 500.0), ("c", 0.01), ("beta", 2.0), ("eta", 300.0),
                     ("tolerance", 60.0), ("max_iters", 10000)):
            e = QtWidgets.QLineEdit(str(mtbf.get(k, d))); self.bt[k] = e
            # formula symbols (a, tau, ...) match the title; only word-y params get prettified
            bl.addRow(k if len(k) <= 4 else sentence_case(k), e)
        lay.addWidget(self.bathtub_box)
        self.mode.currentTextChanged.connect(self._upd)
        lay.addWidget(QtWidgets.QLabel("MTTR (mean time to repair) distribution:"))
        self.mttr = SamplerWidget(get_property_json(node, "mttr", None))
        lay.addWidget(self.mttr)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)
        self._upd()

    def _upd(self, *_):
        bathtub = self.mode.currentData() == "bathtub"
        self.bathtub_box.setVisible(bathtub); self.dist.setVisible(not bathtub)

    def apply(self):
        if self.mode.currentData() == "distribution":
            mtbf = {"mode": "distribution", "distribution": self.dist.get_value()}
        else:
            mtbf = {"mode": "bathtub"}
            for k, e in self.bt.items():
                mtbf[k] = as_int(e.text()) if k == "max_iters" else as_float(e.text())
        set_property_json(self.node, "mtbf", mtbf)
        set_property_json(self.node, "mttr", self.mttr.get_value())


class ModelConfigsWidget(QtWidgets.QWidget):
    """Per-model configs for a piece task: list of {model, duration, resources, min/max carrier capacity}."""

    def __init__(self, model_names, resource_names, entries=None, parent=None):
        super().__init__(parent)
        self._models = list(model_names); self._resources = list(resource_names)
        self._rows = []
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ Model config"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        box = QtWidgets.QGroupBox(); bl = QtWidgets.QFormLayout(box)
        combo = QtWidgets.QComboBox(); combo.addItems(self._models)
        if entry.get("model") in self._models:
            combo.setCurrentText(entry["model"])
        bl.addRow("Model", combo)
        dur = SamplerWidget(entry.get("duration")); bl.addRow("Duration", dur)
        res = ResourcePickerWidget(self._resources, "quantity",
                                   [{"resource": r.get("resource"), "value": r.get("value", r.get("quantity", 1.0))}
                                    for r in entry.get("resources", [])])
        bl.addRow("Resources", res)
        mn = QtWidgets.QLineEdit(str(entry.get("min_carrier_capacity", 1))); mn.setMaximumWidth(60)
        mx = QtWidgets.QLineEdit(str(entry.get("max_carrier_capacity", 1))); mx.setMaximumWidth(60)
        bl.addRow("Min carrier capacity", mn); bl.addRow("Max carrier capacity", mx)
        rm = QtWidgets.QPushButton("Remove model"); bl.addRow(rm)
        rec = (box, combo, dur, res, mn, mx)
        rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec); self._vl.addWidget(box)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def value(self):
        out = []
        for _, combo, dur, res, mn, mx in self._rows:
            if not combo.currentText():
                continue
            out.append({
                "model": combo.currentText(),
                "duration": dur.get_value(),
                "resources": res.entries(),
                "min_carrier_capacity": as_int(mn.text(), 1),
                "max_carrier_capacity": as_int(mx.text(), 1),
            })
        return out


def _carrier_common_tab(node, operator_names, shift_names, collector_types, extra=None, policy_options=None):
    """Build the shared task-config tabs, one concept per tab (durations, operators,
    carriers, scopes, protocols, shifts). `extra` injects caller-owned rows into a tab:
    {"durations": [(label, widget)], "carriers": [...], "scopes": [...]}; those widgets
    are read back by the caller, not by _apply_carrier_common.
    Returns (list-of-(label, widget), accessor-dict)."""
    extra = extra or {}
    tabs = []
    acc = {}

    # durations
    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    f.addWidget(QtWidgets.QLabel("Startup duration:")); acc["startup_duration"] = SamplerWidget(get_property_json(node, "startup_duration", None)); f.addWidget(acc["startup_duration"])
    f.addWidget(QtWidgets.QLabel("Loading duration:")); acc["loading_duration"] = SamplerWidget(get_property_json(node, "loading_duration", None)); f.addWidget(acc["loading_duration"])
    for label, wdg in extra.get("durations", []):
        f.addWidget(QtWidgets.QLabel(label)); f.addWidget(wdg)
    f.addStretch(1)
    tabs.append(("Durations", _scroll(t)))

    # operators
    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    f.addWidget(QtWidgets.QLabel("Operators (alternatives):")); acc["operators"] = AlternativesWidget(operator_names, get_property_json(node, "operators", [])); f.addWidget(acc["operators"])
    f.addWidget(QtWidgets.QLabel("Loading operators:")); acc["loading_operators"] = AlternativesWidget(operator_names, get_property_json(node, "loading_operators", [])); f.addWidget(acc["loading_operators"])
    f.addWidget(QtWidgets.QLabel("Startup operators:")); acc["startup_operators"] = AlternativesWidget(operator_names, get_property_json(node, "startup_operators", [])); f.addWidget(acc["startup_operators"])
    tabs.append(("Operators", _scroll(t)))

    # carriers (also the general task-settings tab)
    t = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(t)
    acc["admin"] = QtWidgets.QCheckBox(); acc["admin"].setChecked(bool(node.get_property("admin")))
    acc["admin"].setToolTip("Administrative task (inspection, waiting, storage, ...). Does not change "
                            "the simulation; the report aggregates admin vs productive tasks separately.")
    f.addRow("Admin task", acc["admin"])
    acc["min_carriers"] = QtWidgets.QLineEdit(str(node.get_property("min_carriers"))); f.addRow("Min carriers", acc["min_carriers"])
    acc["max_capacity"] = QtWidgets.QLineEdit(str(node.get_property("max_capacity"))); f.addRow("Max capacity", acc["max_capacity"])
    acc["timeout"] = InfFloatWidget(node.get_property("timeout") if node.has_property("timeout") else "inf")
    f.addRow("Timeout", acc["timeout"])
    acc["priority"] = QtWidgets.QLineEdit(str(node.get_property("priority"))); f.addRow("Priority", acc["priority"])
    acc["contiguous_carriers"] = QtWidgets.QCheckBox(); acc["contiguous_carriers"].setChecked(bool(node.get_property("contiguous_carriers"))); f.addRow("Contiguous carriers", acc["contiguous_carriers"])
    acc["independent_carriers"] = QtWidgets.QCheckBox(); acc["independent_carriers"].setChecked(bool(node.get_property("independent_carriers"))); f.addRow("Independent carriers", acc["independent_carriers"])
    for label, wdg in extra.get("carriers", []):
        f.addRow(label, wdg)
    tabs.append(("Carriers", _scroll(t)))

    # scopes
    t = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(t)
    acc["operator_scope"] = QtWidgets.QComboBox(); fill_combo(acc["operator_scope"], ["PER_BATCH", "PER_TASK"], node.get_property("operator_scope"))
    acc["resource_scope"] = QtWidgets.QComboBox(); fill_combo(acc["resource_scope"], ["PER_UNIT", "PER_BATCH"], node.get_property("resource_scope"))
    f.addRow("Operator scope", acc["operator_scope"]); f.addRow("Resource scope", acc["resource_scope"])
    if collector_types is not None:
        acc["collector_type"] = QtWidgets.QComboBox()
        fill_combo(acc["collector_type"], collector_types,
                   node.get_property("collector_type") if node.has_property("collector_type") else collector_types[0])
        f.addRow("Collector type", acc["collector_type"])
    for label, wdg in extra.get("scopes", []):
        f.addRow(label, wdg)
    tabs.append(("Scopes", _scroll(t)))

    # protocols (stored under the "policies" property/JSON key)
    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    acc["policies"] = PoliciesWidget(get_property_json(node, "policies", {}), policy_options=policy_options)
    f.addWidget(acc["policies"]); f.addStretch(1)
    tabs.append(("Protocols", _scroll(t)))

    # shifts
    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    f.addWidget(QtWidgets.QLabel("Task shifts:")); acc["task_shifts"] = ShiftPickerWidget(shift_names, get_property_json(node, "task_shifts", [])); f.addWidget(acc["task_shifts"]); f.addStretch(1)
    tabs.append(("Shifts", _scroll(t)))

    return tabs, acc


def _scroll(widget):
    sc = QtWidgets.QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(widget); return sc


def _apply_carrier_common(node, acc):
    set_property_json(node, "startup_duration", acc["startup_duration"].get_value())
    set_property_json(node, "loading_duration", acc["loading_duration"].get_value())
    set_property_json(node, "operators", acc["operators"].get_value())
    set_property_json(node, "loading_operators", acc["loading_operators"].get_value())
    set_property_json(node, "startup_operators", acc["startup_operators"].get_value())
    node.set_property("operator_scope", acc["operator_scope"].currentData())
    node.set_property("resource_scope", acc["resource_scope"].currentData())
    if "collector_type" in acc:
        node.set_property("collector_type", acc["collector_type"].currentData())
    node.set_property("min_carriers", as_int(acc["min_carriers"].text(), 1))
    node.set_property("max_capacity", as_float(acc["max_capacity"].text(), 1.0))
    node.set_property("timeout", acc["timeout"].get_value())  # number of minutes | "inf"
    node.set_property("priority", as_int(acc["priority"].text(), 5))
    node.set_property("contiguous_carriers", acc["contiguous_carriers"].isChecked())
    node.set_property("independent_carriers", acc["independent_carriers"].isChecked())
    node.set_property("admin", acc["admin"].isChecked())
    set_property_json(node, "policies", acc["policies"].get_value())
    set_property_json(node, "task_shifts", acc["task_shifts"].chosen())


class PieceTaskMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node, win):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Piece task"); self.resize(640, 640)
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget(); lay.addWidget(tabs)
        t0 = QtWidgets.QWidget(); f0 = QtWidgets.QVBoxLayout(t0)
        self.models = ModelConfigsWidget(_names(win.model_registry), _names(win.resource_registry),
                                         get_property_json(node, "models_configs", []))
        f0.addWidget(self.models)
        tabs.addTab(_scroll(t0), "Models")
        common, self.acc = _carrier_common_tab(node, _names(win.operator_registry), _names(win.shift_registry), COLLECTOR_TYPES,
                                               policy_options=PIECE_POLICY_OPTIONS)
        for label, wdg in common:
            tabs.addTab(wdg, label)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "models_configs", self.models.value())
        _apply_carrier_common(self.node, self.acc)


class ResourceTaskMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node, win):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Resource task"); self.resize(640, 640)
        rnames = _names(win.resource_registry)
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget(); lay.addWidget(tabs)
        # resources tab (resource I/O only)
        t0 = QtWidgets.QWidget(); f0 = QtWidgets.QVBoxLayout(t0)
        f0.addWidget(QtWidgets.QLabel("Non-transformed inputs (quantity consumed):"))
        self.non_transformed = ResourcePickerWidget(rnames, "quantity",
            [{"resource": e.get("resource"), "value": e.get("value", e.get("quantity", 1.0))} for e in get_property_json(node, "non_transformed_resources", [])])
        f0.addWidget(self.non_transformed)
        f0.addWidget(QtWidgets.QLabel("Transformed inputs (proportion + salvageable):"))
        self.transformed = _TransformedWidget(rnames, get_property_json(node, "transformed_resources", []))
        f0.addWidget(self.transformed)
        f0.addWidget(QtWidgets.QLabel("Outputs produced (bounded distribution, ≥ 0):"))
        self.outputs = _OutputsWidget(rnames, get_property_json(node, "resources_out", []))
        f0.addWidget(self.outputs)
        tabs.addTab(_scroll(t0), "Resources")
        # resource-task-specific fields, injected into the shared tabs where they belong
        self.duration = SamplerWidget(get_property_json(node, "duration", None))
        self.min_cc = QtWidgets.QLineEdit(str(node.get_property("min_carrier_capacity")))
        self.max_cc = QtWidgets.QLineEdit(str(node.get_property("max_carrier_capacity")))
        self.rct = QtWidgets.QComboBox(); fill_combo(self.rct, RESOURCE_COLLECTOR_TYPES, node.get_property("resource_collector_type"))
        extra = {
            "durations": [("Duration:", self.duration)],
            "carriers": [("Min carrier capacity", self.min_cc), ("Max carrier capacity", self.max_cc)],
            "scopes": [("Resource collector type", self.rct)],
        }
        common, self.acc = _carrier_common_tab(node, _names(win.operator_registry), _names(win.shift_registry), None, extra=extra)
        for label, wdg in common:
            tabs.addTab(wdg, label)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "duration", self.duration.get_value())
        set_property_json(self.node, "non_transformed_resources", self.non_transformed.entries())
        set_property_json(self.node, "transformed_resources", self.transformed.value())
        set_property_json(self.node, "resources_out", self.outputs.value())
        self.node.set_property("min_carrier_capacity", as_float(self.min_cc.text(), 1.0))
        self.node.set_property("max_carrier_capacity", as_float(self.max_cc.text(), 1.0))
        self.node.set_property("resource_collector_type", self.rct.currentData())
        _apply_carrier_common(self.node, self.acc)


class _TransformedWidget(QtWidgets.QWidget):
    def __init__(self, resource_names, entries=None, parent=None):
        super().__init__(parent)
        self._names = list(resource_names); self._rows = []
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ Transformed resource"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if entry.get("resource") in self._names:
            combo.setCurrentText(entry["resource"])
        prop = QtWidgets.QLineEdit(str(entry.get("proportion", 1.0))); prop.setMaximumWidth(60)
        salv = QtWidgets.QCheckBox("Salvageable"); salv.setChecked(bool(entry.get("salvageable", True)))
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        h.addWidget(combo); h.addWidget(QtWidgets.QLabel("Proportion:")); h.addWidget(prop); h.addWidget(salv); h.addWidget(rm); h.addStretch(1)
        rec = (row, combo, prop, salv); rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec); self._vl.addWidget(row)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def value(self):
        return [{"resource": c.currentText(), "proportion": as_float(p.text()), "salvageable": s.isChecked()}
                for _, c, p, s in self._rows if c.currentText()]


class _OutputsWidget(QtWidgets.QWidget):
    def __init__(self, resource_names, entries=None, parent=None):
        super().__init__(parent)
        self._names = list(resource_names); self._rows = []
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ Output resource"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        box = QtWidgets.QGroupBox(); bl = QtWidgets.QFormLayout(box)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if entry.get("resource") in self._names:
            combo.setCurrentText(entry["resource"])
        bl.addRow("Resource", combo)
        dist = SamplerWidget(entry.get("distribution")); bl.addRow("Amount", dist)
        lb = QtWidgets.QLineEdit(str(entry.get("lowerbound", 0.0))); lb.setMaximumWidth(90)
        ub = QtWidgets.QLineEdit(str(entry.get("upperbound", 1.0))); ub.setMaximumWidth(90)
        bl.addRow("Lowerbound (≥ 0)", lb)
        bl.addRow("Upperbound (finite)", ub)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24); bl.addRow(rm)
        rec = (box, combo, dist, lb, ub); rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec); self._vl.addWidget(box)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def value(self):
        return [{"resource": c.currentText(), "distribution": d.get_value(),
                 "lowerbound": as_float(lb.text()), "upperbound": as_float(ub.text(), 1.0)}
                for _, c, d, lb, ub in self._rows if c.currentText()]


# ============================================================
# Run simulation (subprocess + progress popup)
# ============================================================

class RunSimulationDialog(QtWidgets.QDialog):
    """Progress popup for a simulation run. The simulation runs in a subprocess
    (sim_runner.py) that prints machine-readable '@@TAG {json}' lines; this dialog
    shows elapsed wall time, the simulated date and, per stopping criterion,
    either time progress (By time) or pieces produced (By pieces produced), with
    an 'n / total' caption above the progress bar. When the run finishes, the
    report folder is one click away."""

    BAR_STEPS = 1000  # progress bar resolution (fractions map to 0..BAR_STEPS)

    def __init__(self, parent, json_path: str, cpp_exe: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Run simulation")
        self.setMinimumWidth(460)
        self._json_path = json_path
        self._cpp_exe = cpp_exe  # None -> Python sim_runner.py; else the native engine
        self._meta = None
        self._sim_start = None
        self._report_dir = None
        self.view_results_requested = False
        self._error_message = None
        self._stderr_tail = []
        self._stdout_buffer = ""
        self._finished = False

        lay = QtWidgets.QVBoxLayout(self)
        file_lbl = QtWidgets.QLabel(f"Running {os.path.basename(json_path)}")
        file_lbl.setStyleSheet("font-weight: bold;")
        lay.addWidget(file_lbl)

        form = QtWidgets.QFormLayout()
        self.elapsed_lbl = QtWidgets.QLabel("0:00:00")
        form.addRow("Elapsed time", self.elapsed_lbl)
        self.sim_time_lbl = QtWidgets.QLabel("-")
        form.addRow("Simulated time", self.sim_time_lbl)
        self.pieces_lbl = QtWidgets.QLabel("-")
        self._pieces_row = form.rowCount()
        form.addRow("Pieces in exit buffer", self.pieces_lbl)
        self.timeout_lbl = QtWidgets.QLabel("-")
        self._timeout_row = form.rowCount()
        form.addRow("Timeout", self.timeout_lbl)
        lay.addLayout(form)
        self._form = form
        self._last_progress = {}
        self._set_form_row_visible(self._pieces_row, self.pieces_lbl, False)
        self._set_form_row_visible(self._timeout_row, self.timeout_lbl, False)

        self.caption_lbl = QtWidgets.QLabel("")
        self.caption_lbl.setAlignment(QtCore.Qt.AlignHCenter)
        lay.addWidget(self.caption_lbl)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, self.BAR_STEPS)
        self.bar.setValue(0)
        lay.addWidget(self.bar)

        self.status_lbl = QtWidgets.QLabel("Starting...")
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)

        buttons = QtWidgets.QHBoxLayout()
        self.view_results_btn = QtWidgets.QPushButton("View results")
        self.view_results_btn.setVisible(False)
        self.view_results_btn.setDefault(True)
        self.view_results_btn.clicked.connect(self._on_view_results)
        self.open_report_btn = QtWidgets.QPushButton("Open report folder")
        self.open_report_btn.setVisible(False)
        self.open_report_btn.clicked.connect(self._open_report)
        buttons.addStretch(1)
        buttons.addWidget(self.view_results_btn)
        buttons.addWidget(self.open_report_btn)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(self.cancel_btn)
        lay.addLayout(buttons)

        # wall clock, ticking every half second
        self._wall = QtCore.QElapsedTimer()
        self._wall.start()
        self._tick = QtCore.QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_elapsed)
        self._tick.start()

        # the simulation subprocess
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim_runner.py")
        self._proc = QtCore.QProcess(self)
        self._proc.setWorkingDirectory(repo_root)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("MPLBACKEND", "Agg")  # report figures only; never open windows
        self._proc.setProcessEnvironment(env)
        self._proc.readyReadStandardOutput.connect(self._on_stdout)
        self._proc.readyReadStandardError.connect(self._on_stderr)
        self._proc.finished.connect(self._on_finished)
        # Both engines honour the same <exe> <flow.json> -> @@TAG contract, so the
        # native binary is a drop-in for the Python runner.
        if self._cpp_exe:
            file_lbl.setText(f"Running {os.path.basename(json_path)}  (C++ engine)")
            self._proc.start(self._cpp_exe, [json_path])
        else:
            self._proc.start(sys.executable, ["-u", runner, json_path])

    # --- subprocess plumbing ---

    def _on_stdout(self):
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._stdout_buffer += data
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            self._handle_line(line.strip())

    def _on_stderr(self):
        data = bytes(self._proc.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr_tail.extend(data.splitlines())
        self._stderr_tail = self._stderr_tail[-30:]

    def _handle_line(self, line: str):
        if not line.startswith("@@"):
            return
        tag, _, payload = line[2:].partition(" ")
        try:
            info = json.loads(payload or "{}")
        except Exception:
            return
        if tag == "META":
            self._meta = info
            self._sim_start = parse_date_time(info.get("sim_start"))
            is_pieces = info.get("criterion") == "ByPiecesProduced"
            self._set_form_row_visible(self._pieces_row, self.pieces_lbl, is_pieces)
            timeout = info.get("timeout")  # only present when finite
            if is_pieces and timeout:
                if self._sim_start is not None:
                    deadline = self._sim_start + timedelta(minutes=timeout)
                    self.timeout_lbl.setText(
                        f"{deadline.strftime(PY_DATE_TIME_FORMAT)}  (after {timeout / 1440.0:g} days)")
                else:
                    self.timeout_lbl.setText(f"{timeout:g} minutes")
                self._set_form_row_visible(self._timeout_row, self.timeout_lbl, True)
            self.status_lbl.setText("Simulation running...")
        elif tag == "PROGRESS":
            self._show_progress(info)
        elif tag == "DONE":
            self._report_dir = info.get("report_dir")
            self._show_progress(info)
        elif tag == "ERROR":
            self._error_message = info.get("message")

    def _show_progress(self, info: dict):
        sim_now = info.get("sim_now")
        if sim_now is None:
            return
        self._last_progress = info
        if self._sim_start is not None:
            date = self._sim_start + timedelta(minutes=sim_now)
            self.sim_time_lbl.setText(f"{date.strftime(PY_DATE_TIME_FORMAT)}  (day {int(sim_now // 1440) + 1})")
        else:
            self.sim_time_lbl.setText(f"{sim_now:.0f} minutes")
        meta = self._meta or {}
        if meta.get("criterion") == "ByPiecesProduced":
            pieces = info.get("pieces")
            goal = meta.get("goal")
            if pieces is not None:
                self.pieces_lbl.setText(str(pieces))
                if goal:
                    self.caption_lbl.setText(f"{pieces} / {goal} pieces")
                    self.bar.setValue(min(self.BAR_STEPS, int(self.BAR_STEPS * pieces / goal)))
        elif meta.get("criterion") == "ByTime":
            total = meta.get("total_time")
            if total:
                self.caption_lbl.setText(f"{sim_now / 1440.0:.1f} / {total / 1440.0:.1f} days simulated")
                self.bar.setValue(min(self.BAR_STEPS, int(self.BAR_STEPS * sim_now / total)))

    def _outcome_line(self) -> str:
        """How the run ended, from the criterion's point of view: the goal was
        met, the timeout cut in first, or the simulation simply ran out of work."""
        meta = self._meta or {}
        if meta.get("criterion") == "ByPiecesProduced":
            pieces = self._last_progress.get("pieces")
            goal = meta.get("goal")
            if pieces is not None and goal:
                if pieces >= goal:
                    return f"Goal reached: {pieces} / {goal} pieces."
                if meta.get("timeout"):
                    return f"Timeout reached: {pieces} / {goal} pieces."
                return (f"Goal not reached: {pieces} / {goal} pieces "
                        f"(the simulation ran out of work; check the shifts).")
        elif meta.get("criterion") == "ByTime":
            return "Stop date reached."
        return "Simulation finished."

    def _on_finished(self, exit_code, *args):
        self._finished = True
        self._tick.stop()
        self._update_elapsed()
        self.cancel_btn.setText("Close")
        if exit_code == 0 and self._report_dir:
            self._render_cpp_graphs_if_needed()
            self.status_lbl.setText(f"{self._outcome_line()}\nReport written to:\n{self._report_dir}")
            self.open_report_btn.setVisible(True)
            self.view_results_btn.setVisible(
                os.path.isfile(os.path.join(self._report_dir, "report.json")))
        elif self._error_message:
            self.status_lbl.setText(f"Simulation failed: {self._error_message}")
        elif exit_code != 0:
            tail = "\n".join(self._stderr_tail[-8:])
            self.status_lbl.setText(f"Simulation failed (exit code {exit_code}).\n{tail}")
        else:
            self.status_lbl.setText(self._outcome_line())

    def _render_cpp_graphs_if_needed(self):
        """The native engine writes graph_data.json instead of drawing anything;
        turn it into the graphes/ PNGs and fill report.json's graphs map with the
        shared Python renderer, so results mode shows the same graphs a Python run
        would. No-op for the Python engine (it draws its own) or if the data is
        absent. A render failure is non-fatal — the report and KPIs are unaffected."""
        if not self._cpp_exe or not self._report_dir:
            return
        if not os.path.isfile(os.path.join(self._report_dir, "graph_data.json")):
            return
        self.status_lbl.setText("Generating graphs...")
        QtWidgets.QApplication.processEvents()
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = QtCore.QProcess(self)
        proc.setWorkingDirectory(repo_root)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("MPLBACKEND", "Agg")
        proc.setProcessEnvironment(env)
        proc.start(sys.executable, ["-m", "simulation.render_from_data", self._report_dir])
        if not proc.waitForFinished(120000) or proc.exitCode() != 0:
            self._stderr_tail.extend(
                bytes(proc.readAllStandardError()).decode("utf-8", errors="replace").splitlines())
            self._stderr_tail = self._stderr_tail[-30:]

    # --- UI helpers ---

    def _set_form_row_visible(self, row: int, field_widget, visible: bool):
        try:
            self._form.setRowVisible(row, visible)
        except Exception:  # Qt < 6.4 fallback: hide the widgets themselves
            field_widget.setVisible(visible)
            lbl = self._form.labelForField(field_widget)
            if lbl is not None:
                lbl.setVisible(visible)

    def _update_elapsed(self):
        seconds = int(self._wall.elapsed() / 1000)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        self.elapsed_lbl.setText(f"{h}:{m:02d}:{s:02d}")

    def _open_report(self):
        if self._report_dir:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self._report_dir))

    def _on_view_results(self):
        self.view_results_requested = True
        self.accept()

    @property
    def report_dir(self):
        return self._report_dir

    def _confirm_abort(self) -> bool:
        answer = QtWidgets.QMessageBox.question(
            self, "Stop simulation", "The simulation is still running. Stop it?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes

    def _kill(self):
        try:
            self._proc.kill()
            self._proc.waitForFinished(2000)
        except Exception:
            pass

    def _on_cancel_clicked(self):
        if self._finished:
            self.accept()
        elif self._confirm_abort():
            self._kill()
            self.reject()

    def closeEvent(self, event):
        if self._finished or self._confirm_abort():
            self._kill()
            event.accept()
        else:
            event.ignore()

    def reject(self):  # Esc key lands here too
        if self._finished:
            super().reject()
        elif self._confirm_abort():
            self._kill()
            super().reject()


class FlowEditorWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1400, 850)

        # Word-style session state: the file backing the canvas and whether the
        # canvas has diverged from it. The title shows "name[*] - app"; Qt swaps
        # the [*] marker in and out with setWindowModified.
        self.current_path = None
        self._dirty = False
        self._suspend_dirty = False  # True while (re)loading, so restores stay clean

        # Results mode: a finished run's report shown on the (locked) graph.
        self.results = None            # results_mode.ResultsData | None
        self._last_run_dir = None
        self._results_toolbar = None
        self._results_dock = None
        self._saved_node_colors = {}   # uid -> (r, g, b), for heat-map restore
        self._update_title()

        self.graph = NodeGraph()

        # Allow rework/repair loops: Breakdown -> Buffer -> Task -> Breakdown.
        try:
            self.graph.set_acyclic(False)
        except Exception:
            pass

        # Curved pipes (NodeGraphQt default look).
        try:
            from NodeGraphQt.constants import PipeLayoutEnum
            self.graph.set_pipe_style(PipeLayoutEnum.CURVED.value)
        except Exception:
            try:
                self.graph.set_pipe_style(0)  # 0 == curved in most NodeGraphQt builds
            except Exception:
                pass

        self.model_registry = []
        self.resource_registry = []
        self.operator_registry = []
        self.shift_registry = []
        self.closing_day_registry = []  # [{"id", "date": "dd-mm-yyyy", "name": label}]
        self.stopping_criterion = {}  # {} | {"type": "ByTime"|"ByPiecesProduced", ...}
        self.start_date = "01-01-2026 00:00"  # always set; the calendar anchor of t=0
        self.seed = 0  # RNG seed for the run; same seed → same run

        self.graph.register_nodes([
            ShutdownsNode,
            BufferNode,
            RouterNode,
            PieceGeneratorNode,
            TaskNode,
            ResourceTaskNode,
            BreakdownNode,
        ])

        self.setCentralWidget(self.graph.widget)

        self.properties_bin = PropertiesBinWidget(node_graph=self.graph)
        self.properties_dock = QtWidgets.QDockWidget("Properties", self)
        self.properties_dock.setWidget(self.properties_bin)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.properties_dock)

        self._build_menus()
        self._install_context_menus()
        self._connect_signals()
        self.statusBar().showMessage("Ready. Use the Create menu to add nodes.")

    def _build_menus(self):
        # every action that edits the session lands in _edit_actions, so results
        # mode can disable them in one sweep and restore them on exit
        self._edit_actions = []

        def editing(action):
            self._edit_actions.append(action)
            return action

        file_menu = self.menuBar().addMenu("File")
        act_new = editing(file_menu.addAction("New"))
        act_new.setShortcut(QtGui.QKeySequence.New)
        act_open = editing(file_menu.addAction("Open..."))
        act_open.setShortcut(QtGui.QKeySequence.Open)
        act_import = editing(file_menu.addAction("Import clean JSON (add)..."))
        file_menu.addSeparator()
        act_save = editing(file_menu.addAction("Save"))
        act_save.setShortcut(QtGui.QKeySequence.Save)
        act_save_as = editing(file_menu.addAction("Save as..."))
        act_save_as.setShortcut(QtGui.QKeySequence.SaveAs)
        act_new.triggered.connect(lambda checked=False: self.new_graph())
        act_open.triggered.connect(lambda checked=False: self.open_file_dialog())
        act_import.triggered.connect(self.import_clean_json_dialog)
        act_save.triggered.connect(lambda checked=False: self.save_file())
        act_save_as.triggered.connect(lambda checked=False: self.save_file_as())

        registries_menu = self.menuBar().addMenu("Registries")
        editing(registries_menu.addAction("Edit models...")).triggered.connect(self.edit_models)
        editing(registries_menu.addAction("Edit resources...")).triggered.connect(self.edit_resources)
        editing(registries_menu.addAction("Edit operators...")).triggered.connect(self.edit_operators)
        editing(registries_menu.addAction("Edit closing days...")).triggered.connect(self.edit_closing_days)
        editing(registries_menu.addAction("Edit shifts...")).triggered.connect(self.edit_shifts)

        simulation_menu = self.menuBar().addMenu("Simulation")
        editing(simulation_menu.addAction("Settings...")).triggered.connect(self.edit_simulation_settings)
        act_run = editing(simulation_menu.addAction("Run simulation..."))
        act_run.setShortcut("F5")
        act_run.triggered.connect(lambda checked=False: self.run_simulation())

        # Engine picker: Python (sim_runner.py) or the bundled native binary.
        engine_menu = simulation_menu.addMenu("Engine")
        backend = app_settings().value("engine/backend", "python")
        self._act_engine_py = engine_menu.addAction("Python")
        self._act_engine_cpp = engine_menu.addAction("C++ (native)")
        for act, name in ((self._act_engine_py, "python"), (self._act_engine_cpp, "cpp")):
            act.setCheckable(True)
            act.setChecked(backend == name)
            act.triggered.connect(lambda checked=False, n=name: self._choose_engine(n))
        engine_menu.addSeparator()
        engine_menu.addAction("Select C++ executable...").triggered.connect(
            lambda checked=False: self._pick_cpp_executable())

        results_menu = self.menuBar().addMenu("Results")
        self.act_view_last_results = results_menu.addAction("View last run results")
        self.act_view_last_results.setEnabled(False)
        self.act_view_last_results.triggered.connect(
            lambda checked=False: self._last_run_dir and self.enter_results_mode(self._last_run_dir))
        results_menu.addAction("Open run results...").triggered.connect(
            lambda checked=False: self.open_results_dialog())
        results_menu.addSeparator()
        self.act_exit_results = results_menu.addAction("Exit results mode")
        self.act_exit_results.setEnabled(False)
        self.act_exit_results.triggered.connect(lambda checked=False: self.exit_results_mode())

        edit_menu = self.menuBar().addMenu("Edit")
        copy_action = editing(edit_menu.addAction("Copy cards"))
        copy_action.setShortcut(QtGui.QKeySequence.Copy)  # Ctrl+C / Cmd+C on macOS
        copy_action.triggered.connect(lambda: self.copy_selected_cards())
        cut_action = editing(edit_menu.addAction("Cut cards"))
        cut_action.setShortcut(QtGui.QKeySequence.Cut)  # Ctrl+X / Cmd+X on macOS
        cut_action.triggered.connect(lambda: self.cut_selected_cards())
        paste_action = editing(edit_menu.addAction("Paste cards"))
        paste_action.setShortcut(QtGui.QKeySequence.Paste)  # Ctrl+V / Cmd+V on macOS
        paste_action.triggered.connect(self.paste_cards)
        dup_action = editing(edit_menu.addAction("Duplicate cards"))
        dup_action.setShortcut("Ctrl+D")  # Qt maps Ctrl to Cmd on macOS
        dup_action.triggered.connect(lambda: self.duplicate_selected_cards())
        edit_menu.addSeparator()
        delete_action = editing(edit_menu.addAction("Delete selected"))
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected_nodes)

        tools_menu = self.menuBar().addMenu("Tools")
        tools_menu.addAction("Validate graph").triggered.connect(self.validate_graph_dialog)
        tools_menu.addAction("Frame all").triggered.connect(self.frame_all)

        templates_menu = self.menuBar().addMenu("Templates")
        editing(templates_menu.addAction("Add backdrop around selection")).triggered.connect(
            self.add_backdrop_around_selection)

        create_menu = self.menuBar().addMenu("Create")
        for label, cls_name in [
            ("Shutdowns", "simulation.flow.ShutdownsNode"),
            ("Buffer", "simulation.flow.BufferNode"),
            ("Router", "simulation.flow.RouterNode"),
            ("Piece generator", "simulation.flow.PieceGeneratorNode"),
            ("Piece task", "simulation.flow.TaskNode"),
            ("Resource task", "simulation.flow.ResourceTaskNode"),
            ("Breakdown", "simulation.flow.BreakdownNode"),
        ]:
            action = editing(create_menu.addAction(label))
            action.triggered.connect(lambda checked=False, t=cls_name: self.create_node(t))

    def _install_context_menus(self):
        """Right-click menus: copy/paste on the canvas, copy on every card type.
        Cosmetic next to the Edit-menu shortcuts, so failures are non-fatal."""
        try:
            graph_menu = self.graph.get_context_menu("graph")
            graph_menu.add_command("Copy cards", lambda graph: self.copy_selected_cards())
            graph_menu.add_command("Cut cards", lambda graph: self.cut_selected_cards())
            graph_menu.add_command("Paste cards", lambda graph: self.paste_cards())
            graph_menu.add_command("Duplicate cards", lambda graph: self.duplicate_selected_cards())
            nodes_menu = self.graph.get_context_menu("nodes")
            for cls in (ShutdownsNode, BufferNode, RouterNode, PieceGeneratorNode,
                        TaskNode, ResourceTaskNode, BreakdownNode):
                node_type = f"{cls.__identifier__}.{cls.__name__}"
                nodes_menu.add_command(
                    "Copy cards",
                    func=lambda graph, node: self.copy_selected_cards(context_node=node),
                    node_type=node_type)
                nodes_menu.add_command(
                    "Cut cards",
                    func=lambda graph, node: self.cut_selected_cards(context_node=node),
                    node_type=node_type)
                nodes_menu.add_command(
                    "Duplicate cards",
                    func=lambda graph, node: self.duplicate_selected_cards(context_node=node),
                    node_type=node_type)
        except Exception as error:
            print(f"[WARNING] Could not install context menus: {error}")

    def frame_all(self):
        nodes = self.all_nodes()
        if not nodes:
            return
        try:
            self.graph.clear_selection()
            for n in nodes:
                n.set_selected(True)
            self.graph.fit_to_selection()
            self.graph.clear_selection()
        except Exception:
            try:
                self.graph.center_on(nodes)
            except Exception:
                pass

    def _connect_signals(self):
        try:
            self.graph.node_double_clicked.connect(self.on_node_double_clicked)
        except Exception:
            pass
        try:
            self.graph.port_connected.connect(self.on_port_connected)
        except Exception:
            pass
        # anything that mutates the graph marks the session dirty (node lifecycle,
        # wiring, property/position edits); best effort across NodeGraphQt versions
        for signal_name in ("node_created", "nodes_deleted", "port_connected",
                            "port_disconnected", "property_changed"):
            try:
                getattr(self.graph, signal_name).connect(self.mark_dirty)
            except Exception:
                pass
        for signal_name in ("node_created", "nodes_deleted", "port_connected",
                            "port_disconnected"):
            try:
                getattr(self.graph, signal_name).connect(self._results_mutation_guard)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Dirty tracking + Word-style save flow. The canvas belongs to a file
    # (current_path); mutating it marks the session dirty, and New / Open /
    # closing the window offer to save first.
    # ------------------------------------------------------------------

    def mark_dirty(self, *args, **kwargs):
        if self._suspend_dirty or self._dirty or self.results is not None:
            return
        self._dirty = True
        self.setWindowModified(True)

    def set_clean(self):
        self._dirty = False
        self.setWindowModified(False)

    def is_dirty(self) -> bool:
        return self._dirty

    def _display_name(self) -> str:
        return os.path.basename(self.current_path) if self.current_path else "Untitled"

    def _update_title(self):
        suffix = "  [results]" if self.results is not None else ""
        self.setWindowTitle(f"{self._display_name()}{suffix}[*] - {APP_NAME}")
        self.setWindowModified(self._dirty)

    def maybe_save(self) -> bool:
        """Offer to save unsaved changes before discarding the session.
        True = go ahead (saved or explicitly discarded), False = cancel."""
        if not self._dirty:
            return True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText(f"Do you want to save the changes made to {self._display_name()}?")
        box.setInformativeText("Your changes will be lost if you don't save them.")
        box.setStandardButtons(QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
                               | QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.Save)
        answer = box.exec()
        if answer == QtWidgets.QMessageBox.Save:
            return self.save_file()
        return answer == QtWidgets.QMessageBox.Discard

    def save_file(self) -> bool:
        if not self.current_path:
            return self.save_file_as()
        return self._write_to(self.current_path)

    def save_file_as(self) -> bool:
        suggested = self.current_path or "flow.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save flow JSON", suggested, "JSON (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        return self._write_to(path)

    def _write_to(self, path: str) -> bool:
        try:
            data = self.export_clean_json()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as error:
            qmessage(self, "Save failed", f"Could not save {path}:\n{error}",
                     QtWidgets.QMessageBox.Warning)
            return False
        self.current_path = path
        self.set_clean()
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}")
        return True

    def open_file_dialog(self):
        if not self.maybe_save():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open flow JSON", "", "JSON (*.json)")
        if not path:
            return
        self.open_file(path)

    def open_file(self, path: str):
        """Replace the session with a file's content (unlike the import action,
        which adds to it). Ids are kept, so open then save round-trips cleanly."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as error:
            qmessage(self, "Open failed", f"Could not open {path}:\n{error}",
                     QtWidgets.QMessageBox.Warning)
            return
        self._suspend_dirty = True
        try:
            self.reset_session()
            self.import_clean_json(data, remap_ids=False)
        finally:
            self._suspend_dirty = False
        self.current_path = path
        self.set_clean()
        self._update_title()
        self.statusBar().showMessage(f"Opened {path}")

    def closeEvent(self, event):
        if self.maybe_save():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Results mode: show a finished run's KPIs on the graph. The canvas is
    # locked (no card edits, moves, wires or registry changes); double-click
    # opens a card's stats, the bottom dock carries the run-wide tables, and
    # the toolbar offers a heat-map metric plus the exit button.
    # ------------------------------------------------------------------

    def open_results_dialog(self):
        start_dir = "runs" if os.path.isdir("runs") else ""
        run_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Open run results", start_dir)
        if run_dir:
            self.enter_results_mode(run_dir)

    def enter_results_mode(self, run_dir: str):
        try:
            results = results_mode.ResultsData(run_dir)
        except Exception as error:
            qmessage(self, "Open results failed", str(error), QtWidgets.QMessageBox.Warning)
            return
        if self.results is not None:
            self.exit_results_mode()

        # The canvas must be the graph that ran. If it already is (same ids,
        # nothing unsaved), keep it — view, selection and file identity survive.
        # Otherwise load the run's flow.json snapshot; it is a copy, so the
        # session becomes Untitled and later edits go through Save as.
        canvas_ids = {node_uid(n) for n in self.all_nodes()}
        if self._dirty or not results.node_ids() <= canvas_ids:
            if not self.maybe_save():
                return
            snapshot = results.snapshot_path()
            try:
                with open(snapshot, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as error:
                qmessage(self, "Open results failed",
                         f"Could not load the run's flow snapshot:\n{error}",
                         QtWidgets.QMessageBox.Warning)
                return
            self._suspend_dirty = True
            try:
                self.reset_session()
                self.import_clean_json(data, remap_ids=False)
            finally:
                self._suspend_dirty = False
            self.current_path = None
            self.set_clean()

        self.results = results
        self._last_run_dir = run_dir
        self.act_view_last_results.setEnabled(True)
        self._lock_for_results(True)
        self._build_results_toolbar()
        self._results_dock = results_mode.ResultsDock(self, results)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._results_dock)
        self._apply_results_tooltips(True)
        self.properties_dock.setVisible(False)  # its editors would bypass the lock
        self._update_title()
        self.statusBar().showMessage(
            "Results mode: cards are locked; double-click one for its stats.")

    def exit_results_mode(self):
        if self.results is None:
            return
        self._apply_heatmap(-1)  # restore original card colors
        self._apply_results_tooltips(False)
        self._lock_for_results(False)
        if self._results_toolbar is not None:
            self.removeToolBar(self._results_toolbar)
            self._results_toolbar.deleteLater()
            self._results_toolbar = None
        if self._results_dock is not None:
            self.removeDockWidget(self._results_dock)
            self._results_dock.deleteLater()
            self._results_dock = None
        self.properties_dock.setVisible(True)
        self.results = None
        self._update_title()
        self.statusBar().showMessage("Left results mode.")

    def _lock_for_results(self, lock: bool):
        for action in self._edit_actions:
            action.setEnabled(not lock)
        self.act_exit_results.setEnabled(lock)
        for node in self.all_nodes():
            try:  # NodeGraphQt has no node lock; freezing the graphics item works
                flag = getattr(QtWidgets.QGraphicsItem, "GraphicsItemFlag",
                               QtWidgets.QGraphicsItem).ItemIsMovable
                node.view.setFlag(flag, not lock)
            except Exception:
                pass

    def _build_results_toolbar(self):
        bar = QtWidgets.QToolBar("Results")
        bar.setObjectName("results_toolbar")
        bar.setMovable(False)
        label = QtWidgets.QLabel("  " + self.results.run_label() + "  ")
        label.setStyleSheet("font-weight: bold;")
        bar.addWidget(label)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        bar.addWidget(spacer)
        bar.addWidget(QtWidgets.QLabel("Color by: "))
        combo = QtWidgets.QComboBox()
        combo.addItem("None", -1)
        for i, (metric_label, *_rest) in enumerate(results_mode.HEAT_METRICS):
            combo.addItem(metric_label, i)
        combo.currentIndexChanged.connect(lambda _i: self._apply_heatmap(combo.currentData()))
        bar.addWidget(combo)
        exit_btn = QtWidgets.QPushButton("Exit results mode")
        exit_btn.clicked.connect(self.exit_results_mode)
        bar.addWidget(exit_btn)
        self.addToolBar(QtCore.Qt.TopToolBarArea, bar)
        self._results_toolbar = bar

    def _apply_heatmap(self, metric_index: int):
        self._suspend_dirty = True
        try:
            # restore first, so switching metrics never stacks tints
            for node in self.all_nodes():
                uid = node_uid(node)
                if uid in self._saved_node_colors:
                    try:
                        node.set_color(*self._saved_node_colors[uid])
                    except Exception:
                        pass
            self._saved_node_colors = {}
            if metric_index is None or metric_index < 0 or self.results is None:
                return
            colors = results_mode.heat_values(metric_index, self.results)
            for node in self.all_nodes():
                if node_kind(node) in ("Backdrop", "BackdropNode"):
                    continue
                uid = node_uid(node)
                # heat color inside the metric's family, neutral grey everywhere
                # else so the colored cards stand out
                target = colors.get(uid, results_mode.DIMMED_COLOR)
                try:
                    # SimNode's class attribute shadows BaseNode.color(); the
                    # live value sits in the node property system
                    current = node.get_property("color")
                    self._saved_node_colors[uid] = tuple(current)[:3]
                    node.set_color(*target)
                except Exception:
                    pass
        finally:
            self._suspend_dirty = False

    def _apply_results_tooltips(self, on: bool):
        for node in self.all_nodes():
            try:
                tip = results_mode.card_tooltip(node_kind(node), node_uid(node), self.results) if on else None
                node.view.setToolTip(tip or "")
            except Exception:
                pass

    def _results_mutation_guard(self, *args, **kwargs):
        if self.results is not None and not self._suspend_dirty:
            self._on_results_mutation()

    def _on_results_mutation(self):
        """Structural change while locked (a stray wire or delete slipping past
        the disabled menus): the report no longer matches, so drop the overlay."""
        self.exit_results_mode()
        self.mark_dirty()
        self.statusBar().showMessage("Graph changed: left results mode (the report "
                                     "no longer matches the canvas).")

    def run_simulation(self):
        """Save (the run executes the file on disk), warn about validation
        problems, then run the parser + simulation in a subprocess behind a
        progress popup."""
        if self._dirty and self.current_path:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Run simulation")
            box.setIcon(QtWidgets.QMessageBox.Question)
            box.setText(f"{self._display_name()} has unsaved changes.")
            box.setInformativeText("The simulation runs the saved file, so the changes "
                                   "must be saved first. Save and run?")
            box.setStandardButtons(QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel)
            box.setDefaultButton(QtWidgets.QMessageBox.Save)
            if box.exec() != QtWidgets.QMessageBox.Save:
                return
        if self._dirty or not self.current_path:
            if not self.save_file():  # falls through to Save as... when never saved
                return
        problems = self.validate_graph()
        if problems:
            answer = QtWidgets.QMessageBox.question(
                self, "Validation warnings",
                "The graph has validation warnings; the run may fail.\nRun anyway?\n\n"
                + "\n".join(problems[:12]))
            if answer != QtWidgets.QMessageBox.Yes:
                return
        cpp_exe = None
        if app_settings().value("engine/backend", "python") == "cpp":
            cpp_exe = self._resolve_cpp_engine()
            if cpp_exe is None:
                return  # no engine chosen; the user was already told
        dlg = RunSimulationDialog(self, self.current_path, cpp_exe=cpp_exe)
        dlg.exec()
        if dlg.report_dir:
            self._last_run_dir = dlg.report_dir
            self.act_view_last_results.setEnabled(True)
            if dlg.view_results_requested:
                self.enter_results_mode(dlg.report_dir)

    # --- C++ engine selection (M4) ------------------------------------------
    def _resolve_cpp_engine(self) -> str | None:
        """The native engine to run: a user-selected executable if one is set and
        still exists, else the bundled binary for this platform. When neither is
        available, offer to pick one; returns None if the user declines."""
        settings = app_settings()
        custom = settings.value("engine/cpp_path", "")
        if custom and os.path.isfile(custom):
            return custom
        bundled = bundled_cpp_engine()
        if bundled:
            return bundled
        answer = QtWidgets.QMessageBox.question(
            self, "C++ engine not found",
            f"No bundled C++ engine for this platform (expected "
            f"engines/{cpp_engine_filename()}).\n\nSelect an executable to use?",
            QtWidgets.QMessageBox.Open | QtWidgets.QMessageBox.Cancel)
        if answer == QtWidgets.QMessageBox.Open:
            return self._pick_cpp_executable()
        return None

    def _pick_cpp_executable(self) -> str | None:
        """Point at a flow_sim binary by hand (persisted for next time)."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select the C++ engine executable")
        if path:
            app_settings().setValue("engine/cpp_path", path)
        return path or None

    def _choose_engine(self, backend: str) -> None:
        app_settings().setValue("engine/backend", backend)
        self._act_engine_py.setChecked(backend == "python")
        self._act_engine_cpp.setChecked(backend == "cpp")
        if backend == "cpp" and self._resolve_cpp_engine_quiet() is None:
            QtWidgets.QMessageBox.information(
                self, "C++ engine",
                f"No bundled engine found (engines/{cpp_engine_filename()}). Use "
                "“Engine → Select C++ executable...” to point at one, or you'll "
                "be asked when you run.")

    def _resolve_cpp_engine_quiet(self) -> str | None:
        settings = app_settings()
        custom = settings.value("engine/cpp_path", "")
        if custom and os.path.isfile(custom):
            return custom
        return bundled_cpp_engine()

    def all_nodes(self) -> List[BaseNode]:
        try:
            return list(self.graph.all_nodes())
        except Exception:
            return list(self.graph.nodes())

    def current_view_center(self):
        try:
            viewer = self.graph.viewer()
            center = viewer.mapToScene(viewer.viewport().rect().center())
            return center.x(), center.y()
        except Exception:
            return 0.0, 0.0

    def _node_rect(self, node):
        try:
            r = node.view.sceneBoundingRect()
            if r.width() > 1 and r.height() > 1:
                return (r.left(), r.top(), r.right(), r.bottom())
        except Exception:
            pass
        try:
            x, y = node.x_pos(), node.y_pos()
        except Exception:
            return None
        return (x, y, x + 240.0, y + 200.0)

    def content_bounds(self, nodes=None):
        if nodes is None:
            nodes = self.all_nodes()
        rects = [r for r in (self._node_rect(n) for n in nodes) if r is not None]
        if not rects:
            return None
        return (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects))

    def shift_nodes(self, nodes, dx, dy):
        if not dx and not dy:
            return
        for n in nodes:
            try:
                x, y = n.x_pos(), n.y_pos()
                self.set_node_position_safe(n, x + dx, y + dy)
            except Exception:
                pass

    def create_node(self, node_type: str):
        node = self.graph.create_node(node_type)
        x, y = self.current_view_center()
        self.set_node_position_safe(node, x, y)
        return node

    def new_graph(self):
        if not self.maybe_save():
            return
        self._suspend_dirty = True
        try:
            self.reset_session()
        finally:
            self._suspend_dirty = False
        self.current_path = None
        self.set_clean()
        self._update_title()

    def reset_session(self):
        self.graph.clear_session()
        self.model_registry = []
        self.resource_registry = []
        self.operator_registry = []
        self.shift_registry = []
        self.closing_day_registry = []
        self.stopping_criterion = {}
        self.start_date = "01-01-2026 00:00"
        self.seed = 0

    @staticmethod
    def _ids_by_name(entries):
        return {e.get("name"): e.get("id") for e in entries if e.get("id")}

    def _mark_dirty_if_changed(self, before, after):
        if before != after:
            self.mark_dirty()

    def edit_models(self):
        dlg = ModelRegistryDialog(self, self.model_registry)
        if dlg.exec():
            try:
                before = copy.deepcopy(self.model_registry)
                self.model_registry = ensure_ids(dlg.models(), "model", self._ids_by_name(self.model_registry))
                self._mark_dirty_if_changed(before, self.model_registry)
                self.statusBar().showMessage(f"{len(self.model_registry)} models defined.")
            except Exception as e:
                qmessage(self, "Invalid models", str(e), QtWidgets.QMessageBox.Warning)

    def edit_resources(self):
        dlg = ResourceRegistryDialog(self, self.resource_registry)
        if dlg.exec():
            before = copy.deepcopy(self.resource_registry)
            self.resource_registry = ensure_ids(dlg.entries(), "resource", self._ids_by_name(self.resource_registry))
            self._mark_dirty_if_changed(before, self.resource_registry)
            self.statusBar().showMessage(f"{len(self.resource_registry)} resources defined.")

    def edit_operators(self):
        shift_names = [s.get("name", "") for s in self.shift_registry if s.get("name")]
        dlg = OperatorRegistryDialog(self, self.operator_registry, shift_names=shift_names)
        if dlg.exec():
            before = copy.deepcopy(self.operator_registry)
            self.operator_registry = ensure_ids(dlg.entries(), "operator", self._ids_by_name(self.operator_registry))
            self._mark_dirty_if_changed(before, self.operator_registry)
            self.statusBar().showMessage(f"{len(self.operator_registry)} operator groups defined.")

    def edit_closing_days(self):
        dlg = ClosingDaysRegistryDialog(self, self.closing_day_registry)
        if dlg.exec():
            before = copy.deepcopy(self.closing_day_registry)
            old_by_date = {e.get("date"): e.get("id") for e in self.closing_day_registry if e.get("id")}
            self.closing_day_registry = ensure_ids(dlg.entries(), "closingday", old_by_date, key="date")
            self._mark_dirty_if_changed(before, self.closing_day_registry)
            self.statusBar().showMessage(f"{len(self.closing_day_registry)} closing days defined.")

    def edit_shifts(self):
        dlg = ShiftRegistryDialog(self, self.shift_registry, closing_days=self.closing_day_registry)
        if dlg.exec():
            before = copy.deepcopy(self.shift_registry)
            self.shift_registry = ensure_ids(dlg.entries(), "shift", self._ids_by_name(self.shift_registry))
            self._mark_dirty_if_changed(before, self.shift_registry)
            self.statusBar().showMessage(f"{len(self.shift_registry)} shift definitions.")

    def edit_simulation_settings(self):
        dlg = SimulationSettingsDialog(self, self.start_date, self.stopping_criterion,
                                       self.model_registry, self.seed)
        if dlg.exec():
            before = (self.start_date, self.seed, copy.deepcopy(self.stopping_criterion))
            self.start_date = dlg.start_value()
            self.seed = dlg.seed_value()
            self.stopping_criterion = dlg.value()
            self._mark_dirty_if_changed(before, (self.start_date, self.seed, self.stopping_criterion))
            label = sentence_case(self.stopping_criterion.get("type") or "?")
            start = self.start_date or "not set"
            self.statusBar().showMessage(f"Simulation: start {start}; seed {self.seed}; stops on {label}.")

    def on_node_double_clicked(self, node):
        kind = node_kind(node)
        if self.results is not None:
            dlg = results_mode.card_dialog(self, kind, node_uid(node), node.name(), self.results)
            if dlg is None:
                self.statusBar().showMessage(f"No run stats for {kind} cards.")
            else:
                dlg.exec()
            return
        dlg = None
        if kind == "Shutdowns":
            dlg = ShutdownsMenuDialog(self, node)
        elif kind == "Buffer":
            dlg = BufferMenuDialog(self, node, self.model_registry)
        elif kind == "Router":
            dlg = RouterMenuDialog(self, node)
        elif kind == "PieceGenerator":
            dlg = GeneratorMenuDialog(self, node, _names(self.shift_registry))
        elif kind == "Breakdown":
            dlg = BreakdownMenuDialog(self, node)
        elif kind == "Task":
            dlg = PieceTaskMenuDialog(self, node, self)
        elif kind == "ResourceTask":
            dlg = ResourceTaskMenuDialog(self, node, self)
        if dlg is not None and dlg.exec():
            dlg.apply()

    def on_port_connected(self, *args):
        ports = [a for a in args if hasattr(a, "node") and hasattr(a, "name")]
        if len(ports) < 2:
            return
        p1, p2 = ports[0], ports[1]
        k1, d1, name1 = port_signature(p1)
        k2, d2, name2 = port_signature(p2)
        if d1 == "output" and d2 == "input":
            out_p, in_p = p1, p2
            out_kind, out_name, in_kind, in_name = k1, name1, k2, name2
        elif d2 == "output" and d1 == "input":
            out_p, in_p = p2, p1
            out_kind, out_name, in_kind, in_name = k2, name2, k1, name1
        else:
            return
        if not is_valid_connection(out_kind, out_name, in_kind, in_name):
            try:
                out_p.disconnect_from(in_p)
            except Exception:
                pass
            qmessage(self, "Invalid connection",
                     f"Cannot connect {out_kind}.{out_name} to {in_kind}.{in_name}.",
                     QtWidgets.QMessageBox.Warning)

    def connections_clean(self) -> List[dict]:
        result = []
        for node in self.all_nodes():
            try:
                outputs = node.outputs()
            except Exception:
                continue
            if not outputs:
                continue
            if isinstance(outputs, dict):
                output_items = outputs.items()
            else:
                output_items = []
                for port in outputs:
                    try:
                        output_items.append((port.name(), port))
                    except Exception:
                        pass
            for out_name, out_port in output_items:
                for in_port in get_connected_ports(out_port):
                    try:
                        target_node = in_port.node()
                        result.append({
                            "from_node": node_uid(node),
                            "from_kind": node_kind(node),
                            "from_port": out_name,
                            "to_node": node_uid(target_node),
                            "to_kind": node_kind(target_node),
                            "to_port": in_port.name(),
                        })
                    except Exception:
                        pass
        return result

    def export_clean_json(self) -> dict:
        nodes = []
        backdrops = []
        for node in self.all_nodes():
            kind = node_kind(node)
            is_backdrop = (node.__class__.__name__ == "BackdropNode" or kind in {"Backdrop", "BackdropNode"})
            if is_backdrop:
                wrapped_node_ids = get_property_json(node, "wrapped_node_ids", [])
                if not wrapped_node_ids:
                    continue
                title = node.get_property("backdrop_title") if node.has_property("backdrop_title") else node.name()
                width, height = None, None
                try:
                    width, height = node.size()
                except Exception:
                    if node.has_property("width"):
                        width = node.get_property("width")
                    if node.has_property("height"):
                        height = node.get_property("height")
                backdrops.append({
                    "id": node_uid(node),
                    "title": title,
                    "nodes": wrapped_node_ids,
                    "position": [node.x_pos(), node.y_pos()],
                    "width": width,
                    "height": height,
                })
                continue
            if hasattr(node, "to_clean_json"):
                node_data = node.to_clean_json()
                if node_data and node_data.get("kind"):
                    nodes.append(node_data)

        # Registries carry a stable id; references in nodes/registries are emitted as
        # those ids, not names. Work on copies so the live (name-based) state is untouched.
        ensure_ids(self.model_registry, "model")
        ensure_ids(self.resource_registry, "resource")
        ensure_ids(self.operator_registry, "operator")
        ensure_ids(self.shift_registry, "shift")
        ensure_ids(self.closing_day_registry, "closingday", key="date")
        models = copy.deepcopy(self.model_registry)
        resources = copy.deepcopy(self.resource_registry)
        operators = copy.deepcopy(self.operator_registry)
        closing_days = copy.deepcopy(self.closing_day_registry)
        shifts = [_shift_export_shape(copy.deepcopy(s)) for s in self.shift_registry]
        name_to_id = {
            "model": {m["name"]: m["id"] for m in models if m.get("name") and m.get("id")},
            "resource": {r["name"]: r["id"] for r in resources if r.get("name") and r.get("id")},
            "operator": {o["name"]: o["id"] for o in operators if o.get("name") and o.get("id")},
            "shift": {s["name"]: s["id"] for s in shifts if s.get("name") and s.get("id")},
            "closing_day": {c["date"]: c["id"] for c in closing_days if c.get("date") and c.get("id")},
        }
        criterion = copy.deepcopy(self.stopping_criterion)
        _apply_ref_map(nodes, models, resources, operators,
                       lambda kind, v: name_to_id[kind].get(v, v), shifts=shifts, criterion=criterion)
        return {
            "editor": {"name": APP_NAME, "version": EDITOR_VERSION, "format": "clean-json"},
            "models": models,
            "resources": resources,
            "operators": operators,
            "closing_days": closing_days,
            "shifts": shifts,
            "stopping_criterion": criterion,
            "start_date": self.start_date,
            "seed": self.seed,
            "nodes": nodes,
            "connections": self.connections_clean(),
            "backdrops": backdrops,
        }

    def validate_graph_dialog(self):
        problems = self.validate_graph()
        if not problems:
            qmessage(self, "Validation", "No validation problems found.")
        else:
            qmessage(self, "Validation problems", "\n".join(problems[:50]), QtWidgets.QMessageBox.Warning)

    def _outlet_valid_models(self, node):
        """Effective valid-model set an outlet accepts, or None if it can't be resolved
        statically (unconfigured buffer, or a router that isn't fed only by buffers)."""
        kind = node_kind(node)
        if kind == "Buffer":
            vm = get_property_json(node, "valid_models", [])
            return set(vm) if vm else None
        if kind == "Router":
            sets = []
            for b in connected_nodes_from_port(node, "to_buffers", "output"):
                if node_kind(b) != "Buffer":
                    return None
                vm = get_property_json(b, "valid_models", [])
                if not vm:
                    return None
                sets.append(set(vm))
            return set.intersection(*sets) if sets else None
        return None

    def _check_flushability(self, node, giver_models, out_port, label, problems):
        """Mirror check_outlet_validity: a generator/piece-task's outlets must be pairwise
        disjoint and together cover every model the giver emits. Skips silently when the
        wiring is incomplete (other checks flag empties)."""
        outlets = connected_nodes_from_port(node, out_port, "output")
        if not outlets or not giver_models:
            return
        resolved = []
        for o in outlets:
            vm = self._outlet_valid_models(o)
            if vm is None:
                return
            resolved.append((o, vm))
        parents = _model_parents(self.model_registry)
        for i in range(len(resolved)):
            for j in range(i + 1, len(resolved)):
                if not _takers_disjoint(resolved[i][1], resolved[j][1], parents):
                    problems.append(f"{label} '{node.name()}': outlets '{resolved[i][0].name()}' and "
                                    f"'{resolved[j][0].name()}' accept overlapping models (outlets must be disjoint).")
        union = set().union(*[vm for _, vm in resolved])
        for m in giver_models:
            if not _taker_can_take(union, m, parents):
                problems.append(f"{label} '{node.name()}': model '{m}' has no outlet that can accept it.")

    def validate_graph(self) -> List[str]:
        problems = []
        start_dt = parse_date_time(self.start_date)
        for c in self.connections_clean():
            if not is_valid_connection(c["from_kind"], c["from_port"], c["to_kind"], c["to_port"]):
                problems.append(f"Invalid connection: {c['from_kind']}.{c['from_port']} -> {c['to_kind']}.{c['to_port']}")

        for node in self.all_nodes():
            kind = node_kind(node)
            name = node.name()
            if kind in ("Task", "ResourceTask"):
                # every protocol must be present with a type the simulation knows
                pol = get_property_json(node, "policies", {})
                expected = PIECE_POLICY_OPTIONS if kind == "Task" else POLICY_OPTIONS
                for pname, (options, _default) in expected.items():
                    ptype = pol.get(pname, {}).get("type")
                    if ptype is None:
                        problems.append(f"'{name}': missing protocol '{pname}'.")
                    elif ptype not in options:
                        problems.append(f"'{name}': protocol '{pname}' has unknown type '{ptype}'.")
                # the simulation rejects this protocol combination at load time
                if (pol.get("task_shift_constraint", {}).get("type") == "ConstrainedByShift"
                        and pol.get("pending_carrier_pre_task_shift_end", {}).get("type") == "WaitForCarriers"):
                    problems.append(f"'{name}': ConstrainedByShift cannot be combined with "
                                    f"WaitForCarriers on pending_carrier_pre_task_shift_end.")
                # priority must be in [0, 10]
                if node.has_property("priority") and str(node.get_property("priority")) != "":
                    if not 0 <= as_int(node.get_property("priority")) <= 10:
                        problems.append(f"'{name}': task priority must be in [0, 10].")
                # operators in one AND-alternative must share the same productivity
                prod = {o.get("name"): json.dumps(o.get("productivity"), sort_keys=True)
                        for o in self.operator_registry}
                for field in ("operators", "loading_operators", "startup_operators"):
                    for group in get_property_json(node, field, []):
                        seen = {prod.get(g.get("operator")) for g in group if g.get("operator") in prod}
                        if len(seen) > 1:
                            problems.append(f"'{name}': operators in one alternative of '{field}' "
                                            f"must share the same productivity.")
                            break
            if kind == "Buffer":
                # every model a passage buffer accepts must be takeable by at least one
                # of the tasks consuming from it — otherwise pieces of that model enter
                # and pile up forever (dead end the simulation cannot detect)
                buffer_type = node.get_property("buffer_type") if node.has_property("buffer_type") else "PASSAGE"
                if buffer_type == "PASSAGE":
                    consumers = [t for t in connected_nodes_from_port(node, "to_task", "output")
                                 if node_kind(t) == "Task"]
                    if not consumers:
                        problems.append(f"Buffer '{name}': no task consumes from this buffer (dead end).")
                    else:
                        parents = _model_parents(self.model_registry)
                        children = {}
                        for m in self.model_registry:
                            children.setdefault(m.get("parent"), []).append(m["name"])

                        def leaves_under(model_name):
                            subs = children.get(model_name)
                            if not subs:
                                return [model_name]
                            return [leaf for s in subs for leaf in leaves_under(s)]

                        takeable = set()
                        for t in consumers:
                            takeable.update(mc.get("model") for mc in get_property_json(t, "models_configs", []))
                        uncovered = []
                        for model_name in get_property_json(node, "valid_models", []):
                            for leaf in leaves_under(model_name):
                                if leaf not in uncovered and not _taker_can_take(takeable, leaf, parents):
                                    uncovered.append(leaf)
                        for leaf in uncovered:
                            problems.append(f"Buffer '{name}': model '{leaf}' can enter but no "
                                            f"connected task can take it.")
            if kind == "Router":
                # the simulation samples branch probabilities at run time; catch what is
                # statically checkable (all-constant branches) at design time
                if not connected_nodes_from_port(node, "to_buffers", "output"):
                    problems.append(f"Router '{name}' has no outlets.")
                branch_map = get_property_json(node, "buffer_probs", {})
                branches = list(branch_map.values())
                freeloaders = [p for p in branches if p is None]
                if len(freeloaders) > 1:
                    problems.append(f"Router '{name}': at most one freeloader branch is allowed.")
                # a constant-0 branch can never carry a piece: the wire exists but is dead
                targets = {node_uid(b): b.name()
                           for b in connected_nodes_from_port(node, "to_buffers", "output")}
                for bid, p in branch_map.items():
                    if isinstance(p, dict) and p.get("kind") == "constant" and as_float(p.get("value", 0.0)) == 0.0:
                        problems.append(f"Router '{name}': branch '{targets.get(bid, bid)}' has "
                                        f"probability 0 (dead branch; no piece can ever take it).")
                consts = [p.get("value", 0.0) for p in branches
                          if isinstance(p, dict) and p.get("kind") == "constant"]
                if any(not 0 <= v <= 1 for v in consts):
                    problems.append(f"Router '{name}': branch probabilities must be in [0, 1].")
                if branches and all(p is None or (isinstance(p, dict) and p.get("kind") == "constant")
                                    for p in branches):
                    s = sum(consts)
                    if not freeloaders and abs(s - 1.0) > 1e-6:
                        problems.append(f"Router '{name}': branch probabilities sum to {s:g} "
                                        f"(must sum to 1, or mark one branch as the freeloader).")
                    elif freeloaders and s > 1 + 1e-6:
                        problems.append(f"Router '{name}': branch probabilities sum to {s:g} "
                                        f"(must be <= 1 so the freeloader can take the rest).")
            if kind == "Task":
                if not connected_refs_from_port(node, "bufs_in", "input"):
                    problems.append(f"Piece task '{name}' has no input buffers.")
                if not get_output_refs(node, "bufs_out"):
                    problems.append(f"Piece task '{name}' has no output buffers.")
                mc = get_property_json(node, "models_configs", [])
                if not mc:
                    problems.append(f"Piece task '{name}' has no model configs.")
                else:
                    for m in mc:
                        if not m.get("duration"):
                            problems.append(f"Piece task '{name}' model '{m.get('model')}' has no duration.")
                    # The vacant-slot pool (max_capacity) must fit the carrier
                    # capacities, or collectors deadlock waiting for slots that
                    # can never exist (they hold what they have while asking
                    # for the remainder with no timeout).
                    cap = as_float(node.get_property("max_capacity") if node.has_property("max_capacity") else 1.0, 1.0)
                    contiguous = bool(node.get_property("contiguous_carriers")) if node.has_property("contiguous_carriers") else False
                    for m in mc:
                        mn = as_float(m.get("min_carrier_capacity", 1))
                        mx = as_float(m.get("max_carrier_capacity", 1))
                        if cap < mn:
                            problems.append(f"Piece task '{name}': max_capacity {cap:g} is smaller than "
                                            f"min_carrier_capacity {mn:g} (model '{m.get('model')}'); "
                                            f"carriers can never collect their minimum batch.")
                        elif not contiguous and cap < mx:
                            problems.append(f"Piece task '{name}': non-contiguous carriers reserve "
                                            f"max_carrier_capacity {mx:g} slots (model '{m.get('model')}') "
                                            f"but max_capacity is {cap:g}; the collector deadlocks "
                                            f"waiting for slots that cannot exist.")
                    # non-discriminating collectors need uniform duration / carrier-capacity across models
                    ct = str(node.get_property("collector_type") if node.has_property("collector_type") else "")
                    if ct.startswith("NON_DISCRIMINATING"):
                        for f, lbl in [("duration", "duration"),
                                       ("min_carrier_capacity", "min_carrier_capacity"),
                                       ("max_carrier_capacity", "max_carrier_capacity")]:
                            if len({json.dumps(m.get(f), sort_keys=True) for m in mc}) > 1:
                                problems.append(f"Piece task '{name}': a non-discriminating collector requires "
                                                f"all models to share the same {lbl}.")
                    self._check_flushability(node, [m.get("model") for m in mc if m.get("model")],
                                             "bufs_out", "Piece task", problems)

            elif kind == "ResourceTask":
                if not get_property_json(node, "duration", None):
                    problems.append(f"Resource task '{name}' has no duration.")
                # same slot-pool rule as piece tasks
                cap = as_float(node.get_property("max_capacity") if node.has_property("max_capacity") else 1.0, 1.0)
                contiguous = bool(node.get_property("contiguous_carriers")) if node.has_property("contiguous_carriers") else False
                mn = as_float(node.get_property("min_carrier_capacity") if node.has_property("min_carrier_capacity") else 1.0, 1.0)
                mx = as_float(node.get_property("max_carrier_capacity") if node.has_property("max_carrier_capacity") else 1.0, 1.0)
                if cap < mn:
                    problems.append(f"Resource task '{name}': max_capacity {cap:g} is smaller than "
                                    f"min_carrier_capacity {mn:g}; carriers can never collect "
                                    f"their minimum batch.")
                elif not contiguous and cap < mx:
                    problems.append(f"Resource task '{name}': non-contiguous carriers reserve "
                                    f"max_carrier_capacity {mx:g} slots but max_capacity is {cap:g}; "
                                    f"the collector deadlocks waiting for slots that cannot exist.")
                outs = get_property_json(node, "resources_out", [])
                if not outs:
                    problems.append(f"Resource task '{name}' has no output resources.")
                for out in outs:
                    if as_float(out.get("lowerbound", 0.0)) < 0:
                        problems.append(f"Resource task '{name}': output '{out.get('resource')}' "
                                        f"lowerbound must be ≥ 0.")
                    ub = out.get("upperbound", 1.0)
                    if ub in ("inf", "Infinity") or as_float(ub, 1.0) == float("inf"):
                        problems.append(f"Resource task '{name}': output '{out.get('resource')}' "
                                        f"upperbound must be finite.")
                # transformed-resource proportions are treated as probabilities: in [0,1] and sum to 1
                tr = get_property_json(node, "transformed_resources", [])
                props = [as_float(t.get("proportion", 0.0)) for t in tr]
                if not tr:
                    problems.append(f"Resource task '{name}': needs transformed resources whose proportions "
                                    f"sum to 1 (the simulation rejects an empty set).")
                elif any(p < 0 or p > 1 for p in props):
                    problems.append(f"Resource task '{name}': transformed-resource proportions must be in [0, 1].")
                elif abs(sum(props) - 1.0) > 1e-6:
                    problems.append(f"Resource task '{name}': transformed-resource proportions must sum to 1 "
                                    f"(currently {sum(props):g}).")

            elif kind == "Breakdown":
                tasks = connected_nodes_from_port(node, "breakdown", "output")
                if not tasks:
                    problems.append(f"Breakdown '{name}' is not attached to a task.")
                if not (get_property_json(node, "mtbf", {}) or {}):
                    problems.append(f"Breakdown '{name}' has no mtbf set.")
                if not get_property_json(node, "mttr", None):
                    problems.append(f"Breakdown '{name}' has no mttr distribution.")
                has_outlets = bool(get_output_refs(node, "bufs_out"))
                for t in tasks:
                    if node_kind(t) == "Task" and not has_outlets:
                        problems.append(f"Breakdown '{name}' on piece task '{t.name()}' must have "
                                        f"lifeboat outlets for in-progress pieces.")
                    elif node_kind(t) == "ResourceTask" and has_outlets:
                        problems.append(f"Breakdown '{name}' on resource task '{t.name()}' cannot have outlets.")

            elif kind == "PieceGenerator":
                # What it emits (models + goals/rates) lives in the stopping criterion
                # (Simulation Settings); the node carries its shifts and its wiring.
                # The criterion block below checks the models against these outlets.
                if not get_output_refs(node, "bufs_out"):
                    problems.append(f"Piece generator '{name}' has no outlets.")
                if not get_property_json(node, "shifts", []):
                    problems.append(f"Piece generator '{name}' has no shifts (double-click it to choose when it emits).")
                # A scrap buffer must never sit on the generator's outlet chain, not
                # even through routers: freshly generated pieces would be scrapped on
                # arrival, and the parser cannot build the object cycle
                # generator -> router -> scrap -> generator anyway.
                frontier = list(connected_nodes_from_port(node, "bufs_out", "output"))
                seen = set()
                while frontier:
                    outlet = frontier.pop()
                    if id(outlet) in seen:
                        continue
                    seen.add(id(outlet))
                    okind = node_kind(outlet)
                    if (okind == "Buffer" and outlet.has_property("buffer_type")
                            and outlet.get_property("buffer_type") == "SCRAP"):
                        problems.append(f"Piece generator '{name}': its outlet chain reaches SCRAP "
                                        f"buffer '{outlet.name()}' (generated pieces would be "
                                        f"scrapped on arrival).")
                    elif okind == "Router":
                        frontier.extend(connected_nodes_from_port(outlet, "to_buffers", "output"))

            elif kind == "Buffer":
                if not get_property_json(node, "valid_models", []):
                    problems.append(f"Buffer '{name}' has no valid models.")

            elif kind == "Router":
                out_bufs = connected_nodes_from_port(node, "to_buffers", "output")
                if not out_bufs:
                    problems.append(f"Router '{name}' has no output buffers.")
                else:
                    vmsets = [set(get_property_json(b, "valid_models", []))
                              for b in out_bufs if node_kind(b) == "Buffer"]
                    if vmsets and all(vmsets) and not set.intersection(*vmsets):
                        problems.append(f"Router '{name}': its buffers share no common valid model "
                                        f"(router outlets must overlap).")

            elif kind == "Shutdowns":
                mode = node.get_property("mode") if node.has_property("mode") else "custom"
                if mode == "generator":
                    g = get_property_json(node, "generator", {})
                    g_start = parse_date_time(g.get("start"))
                    g_end = parse_date_time(g.get("end"))
                    if g_start is None or g_end is None:
                        problems.append(f"Shutdowns '{name}': generator dates must be 'dd-mm-yyyy hh:mm'.")
                    else:
                        if start_dt is not None and g_start < start_dt:
                            problems.append(f"Shutdowns '{name}': generator starts before the "
                                            f"simulation start date.")
                        if g_end <= g_start:
                            problems.append(f"Shutdowns '{name}': generator start must be before its end.")
                    in_between = as_float(g.get("in_between", 0.0))
                    duration = as_float(g.get("duration", 0.0))
                    if in_between <= 0:
                        problems.append(f"Shutdowns '{name}': 'in between' must be > 0 minutes.")
                    if duration <= 0:
                        problems.append(f"Shutdowns '{name}': duration must be > 0 minutes.")
                    if in_between > 0 and duration > in_between:
                        problems.append(f"Shutdowns '{name}': duration exceeds 'in between'; "
                                        f"consecutive shutdowns would overlap (the simulation "
                                        f"rejects overlapping intervals).")
                else:
                    _check_date_intervals(f"Shutdowns '{name}'",
                                          get_property_json(node, "intervals", []),
                                          start_dt, problems)

        # Aggregate (whole-graph) checks.
        # Registry entries are picked by name in the card menus, so two entries that
        # share a name are indistinguishable there (even though each still exports a
        # unique id). Flag it so the ambiguity never reaches the export silently.
        for label, reg in (("model", self.model_registry), ("resource", self.resource_registry),
                           ("operator", self.operator_registry), ("shift", self.shift_registry)):
            names = [e.get("name") for e in reg if e.get("name")]
            dupes = sorted({n for n in names if names.count(n) > 1})
            for n in dupes:
                problems.append(f"Two or more {label} registry entries are named '{n}'; "
                                f"names must be unique so cards can reference them.")

        # Closing days: shifts pick them by date, so each date must parse and be unique.
        cd_dates = [e.get("date") for e in self.closing_day_registry]
        for d in cd_dates:
            if parse_date(d) is None:
                problems.append(f"Closing day '{d}': date must be 'dd-mm-yyyy'.")
        for d in sorted({d for d in cd_dates if d and cd_dates.count(d) > 1}):
            problems.append(f"Closing day '{d}' appears more than once in the registry.")

        buffer_types = [node.get_property("buffer_type") if node.has_property("buffer_type") else "PASSAGE"
                        for node in self.all_nodes() if node_kind(node) == "Buffer"]
        exit_count = buffer_types.count("EXIT")
        if exit_count == 0:
            problems.append("No EXIT buffer: the parser expects exactly one to define the simulation's exit.")
        elif exit_count > 1:
            problems.append(f"{exit_count} EXIT buffers: the simulation allows at most one.")

        # Mirror the simulation's guard: exactly one piece generator.
        gen_count = sum(1 for n in self.all_nodes() if node_kind(n) == "PieceGenerator")
        if gen_count == 0:
            problems.append("No piece generator: the simulation requires exactly one.")
        elif gen_count > 1:
            problems.append(f"{gen_count} piece generators: the simulation allows exactly one.")

        # The start date is mandatory: every absolute date converts against it.
        if start_dt is None:
            problems.append("Simulation start date missing or not 'dd-mm-yyyy hh:mm' "
                            "(Simulation > Settings...).")

        # The stopping criterion carries the piece generator's mix (its shifts live
        # on the generator node); validate the generation params here and flush its
        # models through the generator's outlets.
        crit = self.stopping_criterion or {}
        gen_node = next((n for n in self.all_nodes() if node_kind(n) == "PieceGenerator"), None)
        if not crit:
            problems.append("No stopping criterion set (Simulation > Settings...); "
                            "the simulation may never terminate.")
        elif crit.get("type") == "ByPiecesProduced":
            if exit_count != 1:
                problems.append("Stopping criterion 'By pieces produced' needs exactly one EXIT buffer to count.")
            goals = crit.get("models_goals", [])
            if not goals:
                problems.append("Stopping criterion 'By pieces produced' has no model goals (Simulation > Settings...).")
            if any(as_int(g.get("goal", 0)) < 0 for g in goals):
                problems.append("Stopping criterion 'By pieces produced': every model goal must be a non-negative integer.")
            if goals and sum(as_int(g.get("goal", 0)) for g in goals) <= 0:
                problems.append("Stopping criterion 'By pieces produced': the total goal must be positive "
                                "(give at least one model a goal above zero).")
            if as_float(crit.get("grace_period", 0.0)) < 0:
                problems.append("Stopping criterion 'By pieces produced': the grace period must be >= 0 "
                                "minutes (the loader also rejects one longer than the generator's shifts).")
            if crit.get("gap") is not None and as_float(crit.get("gap"), 0.0) <= 0:
                problems.append("Stopping criterion 'By pieces produced': the gap must be > 0 minutes "
                                "(or switch back to the automatic gap).")
            if gen_node is not None:
                self._check_flushability(gen_node, [g.get("model") for g in goals if g.get("model")],
                                         "bufs_out", "Piece generator", problems)
        elif crit.get("type") == "ByTime":
            stop_dt = parse_date_time(crit.get("time"))
            if stop_dt is None:
                problems.append("Stopping date must be 'dd-mm-yyyy hh:mm' (Simulation > Settings...).")
            elif start_dt is not None and stop_dt <= start_dt:
                problems.append("Stopping date must be after the simulation start date.")
            probs = crit.get("models_probs", [])
            if not probs:
                problems.append("Stopping criterion 'By time' has no model probabilities (Simulation > Settings...).")
            freeloaders = [mp for mp in probs if mp.get("probability") is None]
            if len(freeloaders) > 1:
                problems.append("Stopping criterion 'By time': at most one model can be the freeloader "
                                "(the one with no probability).")
            # every probability box holds a value; catch what is statically checkable
            # (all-constant mixes) at design time, like the router branches
            consts = [mp["probability"].get("value", 0.0) for mp in probs
                      if isinstance(mp.get("probability"), dict) and mp["probability"].get("kind") == "constant"]
            if any(not 0 <= v <= 1 for v in consts):
                problems.append("Stopping criterion 'By time': model probabilities must be in [0, 1].")
            if probs and all(mp.get("probability") is None
                             or (isinstance(mp.get("probability"), dict)
                                 and mp["probability"].get("kind") == "constant")
                             for mp in probs):
                s = sum(consts)
                if not freeloaders and abs(s - 1.0) > 1e-6:
                    problems.append(f"Stopping criterion 'By time': model probabilities sum to {s:g} "
                                    f"(must sum to 1, or mark one model as the freeloader).")
                elif freeloaders and s > 1 + 1e-6:
                    problems.append(f"Stopping criterion 'By time': model probabilities sum to {s:g} "
                                    f"(must be <= 1 so the freeloader can take the rest).")
            if not crit.get("gap"):
                problems.append("Stopping criterion 'By time' has no gap between pieces (Simulation > Settings...).")
            if gen_node is not None:
                self._check_flushability(gen_node, [mp.get("model") for mp in probs if mp.get("model")],
                                         "bufs_out", "Piece generator", problems)

        # Shifts: days off come from the closing-days registry; custom mode = absolute
        # date intervals; weekly mode = date horizon containing the days off.
        known_closing = {e.get("date") for e in self.closing_day_registry if e.get("date")}
        for s in self.shift_registry:
            sname = s.get("name", "?")
            offs = [parse_date(x) for x in s.get("days_off", [])]
            if any(o is None for o in offs):
                problems.append(f"Shift '{sname}': days off must be 'dd-mm-yyyy'.")
            for x in s.get("days_off", []):
                if x not in known_closing:
                    problems.append(f"Shift '{sname}': day off '{x}' is not in the closing-days "
                                    f"registry (Registries > Edit closing days...).")
            if s.get("mode") == "custom":
                ivs = s.get("custom_intervals", [])
                if not ivs:
                    problems.append(f"Custom shift '{sname}' has no intervals.")
                _check_date_intervals(f"Custom shift '{sname}'", ivs, start_dt, problems)
            else:
                hz = s.get("horizon", {})
                h0 = parse_date(hz.get("start"))
                h1 = parse_date(hz.get("end"))
                if h0 is None or h1 is None:
                    problems.append(f"Shift '{sname}': horizon dates must be 'dd-mm-yyyy'.")
                elif h1 < h0:
                    problems.append(f"Shift '{sname}': horizon ends before it starts.")
                elif start_dt is not None and h0.date() < start_dt.date():
                    problems.append(f"Shift '{sname}': horizon begins before the simulation start date.")
                if (h0 is not None and h1 is not None
                        and any(o is not None and not (h0 <= o <= h1) for o in offs)):
                    problems.append(f"Shift '{sname}': a day off lies outside the horizon.")

        return problems

    def delete_selected_nodes(self):
        selected_nodes = self.graph.selected_nodes()
        if not selected_nodes:
            return
        for node in selected_nodes:
            self.graph.delete_node(node)

    # ------------------------------------------------------------------
    # Copy / paste of cards. The clipboard carries the same clean-JSON shape
    # as the export (cards + the connections between them) as plain text, so
    # a paste is a mini-import: ids re-minted, positions offset, new nodes
    # selected. Works across two running designers too.
    # ------------------------------------------------------------------

    CARD_CLIPBOARD_FORMAT = "flow-designer-cards"

    @staticmethod
    def _text_widget_with_focus():
        """The focused text-editing widget, if any — Ctrl+C/Ctrl+V must keep
        their native meaning while typing in a field (properties bin etc.)."""
        w = QtWidgets.QApplication.focusWidget()
        if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
            return w
        if isinstance(w, QtWidgets.QAbstractSpinBox):
            return w.lineEdit()
        return None

    def _selected_cards(self, context_node=None):
        nodes = [n for n in self.graph.selected_nodes() if hasattr(n, "to_clean_json")]
        if context_node is not None and context_node not in nodes:
            nodes = [context_node]  # right-clicked an unselected card: act on just it
        return nodes

    def _cards_payload(self, nodes):
        """Clean-JSON clipboard payload for these cards: the cards plus the wires
        between them (wires leaving the set are dropped). None if nothing usable."""
        cards = [c for c in (n.to_clean_json() for n in nodes) if c and c.get("kind")]
        if not cards:
            return None
        uids = {c["id"] for c in cards if c.get("id")}
        conns = [c for c in self.connections_clean()
                 if c.get("from_node") in uids and c.get("to_node") in uids]
        return {"format": self.CARD_CLIPBOARD_FORMAT, "nodes": cards, "connections": conns}

    # rough visual footprint of a card, for centering a pasted group (positions
    # are top-left corners; exact sizes aren't in the payload)
    _CARD_FOOTPRINT = (240.0, 200.0)

    def _materialize_cards(self, payload, delta=0.0, center=None):
        """Instantiate a payload's cards with fresh uids and leave the new cards
        selected. If center is given, the group is translated so its bounding-box
        center lands there; delta then nudges it (+x, +y). Returns the created nodes."""
        data = self._remap_ids(payload)
        positions = [c["position"] for c in data.get("nodes", [])
                     if isinstance(c.get("position"), list) and len(c["position"]) >= 2]
        dx = dy = delta
        if center is not None and positions:
            fw, fh = self._CARD_FOOTPRINT
            cx = (min(p[0] for p in positions) + max(p[0] for p in positions) + fw) / 2.0
            cy = (min(p[1] for p in positions) + max(p[1] for p in positions) + fh) / 2.0
            dx += center[0] - cx
            dy += center[1] - cy
        for card in data.get("nodes", []):
            pos = card.get("position")
            if isinstance(pos, list) and len(pos) >= 2:
                card["position"] = [pos[0] + dx, pos[1] + dy]
        created = self._instantiate_cards(data)
        if created:
            try:
                self.graph.clear_selection()
            except Exception:
                pass
            for node in created.values():
                try:
                    node.set_selected(True)
                except Exception:
                    pass
        return created

    def copy_selected_cards(self, context_node=None):
        w = self._text_widget_with_focus()
        if w is not None:
            w.copy()
            return
        payload = self._cards_payload(self._selected_cards(context_node))
        if payload is None:
            self.statusBar().showMessage("Nothing selected to copy.")
            return
        QtWidgets.QApplication.clipboard().setText(json.dumps(payload, indent=2, ensure_ascii=False))
        self.statusBar().showMessage(f"Copied {len(payload['nodes'])} card(s).")

    def cut_selected_cards(self, context_node=None):
        w = self._text_widget_with_focus()
        if w is not None:
            w.cut()
            return
        nodes = self._selected_cards(context_node)
        payload = self._cards_payload(nodes)
        if payload is None:
            self.statusBar().showMessage("Nothing selected to cut.")
            return
        QtWidgets.QApplication.clipboard().setText(json.dumps(payload, indent=2, ensure_ascii=False))
        for node in nodes:
            self.graph.delete_node(node)
        self.statusBar().showMessage(f"Cut {len(payload['nodes'])} card(s).")

    def duplicate_selected_cards(self, context_node=None):
        """Copy + paste in one step, without touching the clipboard."""
        payload = self._cards_payload(self._selected_cards(context_node))
        if payload is None:
            self.statusBar().showMessage("Nothing selected to duplicate.")
            return
        created = self._materialize_cards(payload, 40.0)
        self.statusBar().showMessage(f"Duplicated {len(created)} card(s).")

    def paste_cards(self):
        w = self._text_widget_with_focus()
        if w is not None:
            w.paste()
            return
        text = QtWidgets.QApplication.clipboard().text()
        try:
            payload = json.loads(text or "")
        except Exception:
            payload = None
        if not isinstance(payload, dict) or payload.get("format") != self.CARD_CLIPBOARD_FORMAT:
            self.statusBar().showMessage("Clipboard holds no copied cards.")
            return
        # paste lands where the user is looking (view center); pasting the same
        # clipboard again without moving the view steps each copy further out
        if getattr(self, "_last_paste_text", None) == text:
            self._paste_serial += 1
        else:
            self._last_paste_text = text
            self._paste_serial = 0
        created = self._materialize_cards(payload, 40.0 * self._paste_serial,
                                          center=self.current_view_center())
        if not created:
            self.statusBar().showMessage("Clipboard holds no pasteable cards.")
            return
        self.statusBar().showMessage(f"Pasted {len(created)} card(s).")

    def set_node_position_safe(self, node, x: float, y: float):
        try:
            node.set_pos(x, y)
        except Exception:
            try:
                node.set_x_pos(x)
                node.set_y_pos(y)
            except Exception:
                pass

    def set_json_property_safe(self, node, prop_name: str, value):
        if node.has_property(prop_name):
            node.set_property(prop_name, json.dumps(value, ensure_ascii=False))
        else:
            node.create_property(prop_name, json.dumps(value, ensure_ascii=False))

    def set_property_safe(self, node, prop_name: str, value):
        if node.has_property(prop_name):
            node.set_property(prop_name, value)
        else:
            node.create_property(prop_name, value)

    def add_backdrop_for_nodes(self, nodes, title: str, width=None, height=None):
        if not nodes:
            return None
        clean_nodes, seen = [], set()
        for node in nodes:
            if node is None:
                continue
            if node.__class__.__name__ == "BackdropNode" or node_kind(node) in {"Backdrop", "BackdropNode"}:
                continue
            uid = node_uid(node)
            if uid in seen:
                continue
            seen.add(uid)
            clean_nodes.append(node)
        if not clean_nodes:
            return None

        backdrop = None
        try:
            backdrop = self.graph.create_node("nodeGraphQt.nodes.BackdropNode")
        except Exception:
            try:
                if BackdropNode is not None:
                    self.graph.register_node(BackdropNode, alias="Backdrop")
                    backdrop = self.graph.create_node("Backdrop")
            except Exception:
                backdrop = None
        if backdrop is None:
            qmessage(self, "Backdrop error", "Could not create a BackdropNode in this NodeGraphQt version.",
                     QtWidgets.QMessageBox.Warning)
            return None

        try:
            backdrop.set_text(title)
        except Exception:
            try:
                backdrop.set_name(title)
            except Exception:
                pass

        if width is not None and height is not None:
            try:
                backdrop.set_size(float(width), float(height))
            except Exception:
                try:
                    self.set_property_safe(backdrop, "width", float(width))
                    self.set_property_safe(backdrop, "height", float(height))
                except Exception:
                    pass
        else:
            try:
                backdrop.wrap_nodes(clean_nodes)
            except Exception:
                pass

        self.set_property_safe(backdrop, "backdrop_title", title)
        self.set_json_property_safe(backdrop, "wrapped_node_ids", [node_uid(n) for n in clean_nodes])
        return backdrop

    def add_backdrop_around_selection(self):
        try:
            selected = self.graph.selected_nodes()
        except Exception:
            selected = []
        if not selected:
            qmessage(self, "No selection",
                     "Select nodes, then use Templates > Add backdrop around selection.",
                     QtWidgets.QMessageBox.Information)
            return
        self.add_backdrop_for_nodes(selected, "Group")

    def node_type_from_kind(self, kind: str) -> str:
        mapping = {
            "Shutdowns": "simulation.flow.ShutdownsNode",
            "Buffer": "simulation.flow.BufferNode",
            "Router": "simulation.flow.RouterNode",
            "PieceGenerator": "simulation.flow.PieceGeneratorNode",
            "Task": "simulation.flow.TaskNode",
            "ResourceTask": "simulation.flow.ResourceTaskNode",
            "Breakdown": "simulation.flow.BreakdownNode",
        }
        if kind not in mapping:
            raise ValueError(f"Unknown node kind in JSON: {kind}")
        return mapping[kind]

    # Import is the inverse of each node's to_clean_json: restore stored properties
    # from the same-named keys. Structured (JSON) properties vs plain scalars:
    _IMPORT_JSON_PROPS = {
        "Shutdowns": ["intervals", "generator"],
        "Buffer": ["valid_models"],
        "PieceGenerator": ["shifts"],
        "Task": ["models_configs", "startup_duration", "loading_duration", "operators",
                 "loading_operators", "startup_operators", "task_shifts", "policies"],
        "ResourceTask": ["non_transformed_resources", "transformed_resources", "resources_out",
                         "duration", "startup_duration", "loading_duration", "operators",
                         "loading_operators", "startup_operators", "task_shifts", "policies"],
        "Breakdown": ["mtbf", "mttr"],
    }
    _IMPORT_SCALAR_PROPS = {
        "Shutdowns": ["shutdown_type", "mode"],
        "Buffer": ["buffer_type"],
        "Task": ["operator_scope", "resource_scope", "min_carriers", "max_capacity",
                 "contiguous_carriers", "independent_carriers", "timeout", "priority", "admin", "collector_type"],
        "ResourceTask": ["resource_scope", "operator_scope", "resource_collector_type",
                         "min_carriers", "max_capacity", "min_carrier_capacity", "max_carrier_capacity",
                         "contiguous_carriers", "independent_carriers", "timeout", "priority", "admin"],
    }

    def apply_clean_json_to_node(self, node, node_data: dict):
        kind = node_data.get("kind")

        if node_data.get("id"):
            self.set_property_safe(node, "uid", node_data["id"])
        if "name" in node_data:
            node.set_name(node_data["name"])
        position = node_data.get("position", [0, 0])
        if isinstance(position, list) and len(position) >= 2:
            self.set_node_position_safe(node, position[0], position[1])

        for key in self._IMPORT_JSON_PROPS.get(kind, []):
            if key in node_data:
                self.set_json_property_safe(node, key, node_data[key])
        for key in self._IMPORT_SCALAR_PROPS.get(kind, []):
            if key in node_data:
                self.set_property_safe(node, key, node_data[key])

        # Shapes that differ between the flat export and the stored property:
        if kind == "Shutdowns" and node_data.get("shutdown_type") is not None:
            # the on-card combo lists display labels while the JSON stays canonical
            self.set_property_safe(node, "shutdown_type",
                                   sentence_case(to_canonical(node_data["shutdown_type"], SHUTDOWN_TYPES)))
        if kind == "Router":
            prob_map = {}
            for item in node_data.get("buffer_probs", []):
                bid = item.get("buffer")
                if bid:
                    prob_map[bid] = item.get("probability", {"kind": "constant", "value": 0.0})
            self.set_json_property_safe(node, "buffer_probs", prob_map)

    def import_clean_json_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import clean JSON", "", "JSON (*.json)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.import_clean_json(data)
        self.statusBar().showMessage(f"Imported clean JSON: {path}")

    def _remap_ids(self, data: dict) -> dict:
        data = json.loads(json.dumps(data))
        mapping = {}
        for node in data.get("nodes", []):
            if isinstance(node, dict) and node.get("id"):
                mapping[node["id"]] = new_uid(str(node.get("kind", "node")).lower())

        def walk(obj):
            if isinstance(obj, dict):
                return {k: walk(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [walk(v) for v in obj]
            if isinstance(obj, str) and obj in mapping:
                return mapping[obj]
            return obj
        return walk(data)

    def _offset_imported_positions(self, data: dict, padding: float = 200.0) -> dict:
        existing = self.content_bounds()
        if existing is None:
            return data
        ex_left, ex_top, ex_right, ex_bottom = existing
        xs, ys = [], []
        for group in (data.get("nodes", []), data.get("backdrops", [])):
            for item in group:
                pos = item.get("position") if isinstance(item, dict) else None
                if isinstance(pos, list) and len(pos) >= 2:
                    xs.append(pos[0]); ys.append(pos[1])
        if not xs:
            return data
        dx = (ex_right + padding) - min(xs)
        dy = ex_top - min(ys)
        for group in (data.get("nodes", []), data.get("backdrops", [])):
            for item in group:
                pos = item.get("position") if isinstance(item, dict) else None
                if isinstance(pos, list) and len(pos) >= 2:
                    item["position"] = [pos[0] + dx, pos[1] + dy]
        return data

    def _resolve_ref_ids_to_names(self, data: dict) -> dict:
        """Inverse of the export's name->id mapping: rewrite id references in the
        imported nodes/registries back to names, so the internal (name-based) model
        keeps working. Old files that already reference by name are left untouched
        (their registries carry no ids, so the maps are empty)."""
        data = json.loads(json.dumps(data))  # never mutate the caller's dict
        regs = {"model": data.get("models", []), "resource": data.get("resources", []),
                "operator": data.get("operators", []), "shift": data.get("shifts", [])}
        id_to_name = {k: {e["id"]: e["name"] for e in v if e.get("id") and e.get("name")}
                      for k, v in regs.items()}
        # closing days are keyed by date, not name
        id_to_name["closing_day"] = {e["id"]: e["date"] for e in data.get("closing_days", [])
                                     if e.get("id") and e.get("date")}
        _apply_ref_map(data.get("nodes", []), regs["model"], regs["resource"], regs["operator"],
                       lambda kind, v: id_to_name[kind].get(v, v), shifts=regs["shift"],
                       criterion=data.get("stopping_criterion"))
        return data

    def _merge_models(self, imported_models: list) -> None:
        if not hasattr(self, "model_registry") or self.model_registry is None:
            self.model_registry = []
        existing = {m.get("name"): m for m in self.model_registry if m.get("name")}
        conflicts = []
        for model in imported_models or []:
            name = model.get("name")
            if not name:
                continue
            if name in existing:
                if (existing[name].get("parent") or None) != (model.get("parent") or None):
                    conflicts.append(name)
                continue
            entry = {"name": name, "parent": model.get("parent") or None, "id": model.get("id")}
            self.model_registry.append(entry)
            existing[name] = entry
        if conflicts:
            qmessage(self, "Model conflict",
                     "These imported models already exist with a different parent and were kept as-is:\n- "
                     + "\n- ".join(conflicts), QtWidgets.QMessageBox.Warning)

    def _merge_named_registry(self, attr: str, imported: list, key: str = "name") -> None:
        """Merge imported registry entries (resources/operators/shifts by name, closing
        days by date); existing entries win on a clash (models are handled separately,
        with conflict warnings)."""
        reg = getattr(self, attr, None) or []
        existing = {e.get(key) for e in reg if e.get(key)}
        for entry in imported or []:
            value = entry.get(key)
            if value and value not in existing:
                reg.append(dict(entry))
                existing.add(value)
        setattr(self, attr, reg)

    def _adopt_orphan_days_off(self) -> None:
        """Every day off referenced by a shift must exist in the closing-days registry.
        Old files carried raw dates on each shift; adopt any date the registry doesn't
        know yet so those shifts stay valid after import."""
        known = {e.get("date") for e in self.closing_day_registry if e.get("date")}
        for s in self.shift_registry:
            for d in s.get("days_off", []):
                if d not in known and parse_date(d) is not None:
                    self.closing_day_registry.append({"date": d, "name": ""})
                    known.add(d)
        self.closing_day_registry.sort(
            key=lambda e: (parse_date(e.get("date")) or datetime.max, e.get("date", "")))

    def _instantiate_cards(self, data: dict) -> dict:
        """Create nodes from clean-JSON cards and rewire the connections between
        them. Returns {card id: node}. Shared by import and paste."""
        id_to_node = {}
        for card in data.get("nodes", []):
            if not isinstance(card, dict):
                continue
            kind = card.get("kind")
            if not kind:
                continue
            try:
                node_type = self.node_type_from_kind(kind)
            except Exception as error:
                print(f"[WARNING] Skipping unknown node kind {kind}: {error}")
                continue
            node = self.graph.create_node(node_type)
            self.apply_clean_json_to_node(node, card)
            if card.get("id"):
                id_to_node[card["id"]] = node

        for connection in data.get("connections", []):
            from_node = id_to_node.get(connection.get("from_node"))
            to_node = id_to_node.get(connection.get("to_node"))
            if from_node is None or to_node is None:
                continue
            try:
                connect_ports_by_name(from_node, connection.get("from_port"), to_node, connection.get("to_port"))
            except Exception as error:
                print("[WARNING] Could not reconnect "
                      f"{connection.get('from_node')}.{connection.get('from_port')} -> "
                      f"{connection.get('to_node')}.{connection.get('to_port')}: {error}")
        return id_to_node

    def import_clean_json(self, data: dict, remap_ids: bool = True):
        data = self._resolve_ref_ids_to_names(data)
        if remap_ids:  # False when a file replaces the session (open): ids kept stable
            data = self._remap_ids(data)
        data = self._offset_imported_positions(data)
        self._merge_models(data.get("models", []))
        self._merge_named_registry("resource_registry", data.get("resources", []))
        self._merge_named_registry("operator_registry", data.get("operators", []))
        self._merge_named_registry("closing_day_registry", data.get("closing_days", []), key="date")
        self._merge_named_registry("shift_registry", data.get("shifts", []))
        self._adopt_orphan_days_off()
        ensure_ids(self.model_registry, "model")
        ensure_ids(self.resource_registry, "resource")
        ensure_ids(self.operator_registry, "operator")
        ensure_ids(self.shift_registry, "shift")
        ensure_ids(self.closing_day_registry, "closingday", key="date")
        if not self.stopping_criterion and data.get("stopping_criterion"):
            self.stopping_criterion = data["stopping_criterion"]
        if data.get("start_date"):
            # the imported file's dates were authored against its own anchor: adopt it
            self.start_date = data["start_date"]
        if data.get("seed") is not None:
            self.seed = int(data["seed"])

        id_to_node = self._instantiate_cards(data)

        # Only recreate backdrops that were actually saved in the file; never wrap
        # the import in a backdrop of our own.
        for group in data.get("backdrops", []):
            group_node_ids = group.get("nodes", group.get("wrapped_node_ids", []))
            group_nodes = [id_to_node[nid] for nid in group_node_ids if nid in id_to_node]
            if not group_nodes:
                continue
            backdrop = self.add_backdrop_for_nodes(group_nodes, group.get("title", "Imported group"),
                                                   width=group.get("width"), height=group.get("height"))
            position = group.get("position")
            if backdrop is not None and isinstance(position, list) and len(position) >= 2:
                self.set_node_position_safe(backdrop, position[0], position[1])

        self.frame_all()


# ============================================================
# Entrypoint
# ============================================================

def main():
    app = QtWidgets.QApplication(sys.argv)
    window = FlowEditorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())