from __future__ import annotations

import json
import sys
import uuid
from typing import Any, List, Tuple

from Qt import QtCore, QtWidgets
from NodeGraphQt import BaseNode, NodeGraph, PropertiesBinWidget

try:
    from NodeGraphQt import BackdropNode
except Exception:
    BackdropNode = None


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
    "LogNormal": [("mean", float, 0.0), ("sigma", float, 1.0)],
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

# Simulation stopping criteria (judgement_day.py). Friendly label -> canonical class name.
STOPPING_CRITERION_TYPES = [("Time", "ByTime"), ("Pieces produced", "ByPiecesProduced")]

PORT_COLORS = {
    "buffer": (80, 180, 120),
    "task": (230, 140, 70),
    "shutdown": (180, 100, 200),
    "breakdown": (220, 90, 110),
}

# Buffer statistics that can be monitored (toggled in the buffer's Monitor tab).
MONITOR_STATS = [
    ("avg_length", "avg length", True),
    ("max_length", "max length", True),
    ("length_std", "length std-dev", False),
    ("current_length", "final length", False),
    ("avg_stay", "avg stay time", True),
    ("max_stay", "max stay time", False),
    ("avg_time_before_arrival", "avg time before arrival", True),
    ("throughput", "throughput", True),
]


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
    NODE_NAME = "Simulation Node"

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
        add_combo_input(self, "shutdown_type", "type", SHUTDOWN_TYPES, "NON_FLEXIBLE")
        self.create_property("intervals", "[]")  # [{start, end}]

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "shutdown_type": self.get_property("shutdown_type") if self.has_property("shutdown_type") else "NON_FLEXIBLE",
            "intervals": get_property_json(self, "intervals", []),
            "position": [self.x_pos(), self.y_pos()],
        }


class BufferNode(SimNode):
    NODE_NAME = "Buffer"
    kind = "Buffer"
    color = (60, 125, 90)

    def __init__(self):
        super().__init__()
        self.add_input("from_task", multi_input=True, color=PORT_COLORS["task"])
        self.add_output("to_task", multi_output=True, color=PORT_COLORS["buffer"])
        self.create_property("valid_models", "[]")
        self.create_property("capacity", "inf")
        self.create_property("buffer_type", "PASSAGE")  # PASSAGE | SCRAP | EXIT
        self.create_property("monitor", "{}")  # {stat: bool}; monitored iff any stat is true

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "valid_models": get_property_json(self, "valid_models", []),
            "capacity": self.get_property("capacity"),
            "buffer_type": self.get_property("buffer_type") if self.has_property("buffer_type") else "PASSAGE",
            "monitor": {key: bool(get_property_json(self, "monitor", {}).get(key, False))
                        for key, _, _ in MONITOR_STATS},
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
    """PieceGenerator: per-model integer goals over chosen shifts -> outlets.
    Only childless (leaf) models can be generated."""
    NODE_NAME = "Piece Generator"
    kind = "PieceGenerator"
    color = (145, 80, 80)

    def __init__(self):
        super().__init__()
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.create_property("models_goals", "[]")  # [{model, goal}]
        self.create_property("shifts", "[]")         # [shift_name]

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "models_goals": get_property_json(self, "models_goals", []),
            "shifts": get_property_json(self, "shifts", []),
            "outlets": get_output_refs(self, "bufs_out"),
            "position": [self.x_pos(), self.y_pos()],
        }


class TaskNode(SimNode):
    """PieceTask. Everything except the piece-flow wiring lives in the card menu:
    per-model configs, task-level durations, operator alternatives, scopes, policies,
    task shifts, carrier settings."""
    NODE_NAME = "Piece Task"
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
        self.create_property("policies", "{}")
        self.create_property("operator_scope", "PER_BATCH")   # PER_BATCH | PER_TASK
        self.create_property("resource_scope", "PER_BATCH")   # PER_UNIT | PER_BATCH
        self.create_property("min_carriers", 1)
        self.create_property("max_capacity", 1.0)
        self.create_property("contiguous_carriers", False)
        self.create_property("independent_carriers", False)
        self.create_property("timeout", 1000000000.0)
        self.create_property("priority", 5)
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
            "timeout": as_float(self.get_property("timeout"), 1e9),
            "priority": as_int(self.get_property("priority"), 5),
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
    NODE_NAME = "Resource Task"
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
        self.create_property("policies", "{}")
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
            "timeout": as_float(self.get_property("timeout"), 1e9),
            "priority": as_int(self.get_property("priority"), 5),
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
        self.table.setHorizontalHeaderLabels(["model name", "parent model"])
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
        self.kind.addItems(list(FUNCTION_SPECS.keys()))
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
        for name, default in FUNCTION_SPECS[self.kind.currentText()]:
            self._play.addWidget(QtWidgets.QLabel(f"{name}:"))
            e = QtWidgets.QLineEdit(str(default))
            e.setMaximumWidth(64)
            self._play.addWidget(e)
            self._edits[name] = e

    def set_value(self, value):
        value = value or {}
        kind = value.get("kind", "constant")
        if kind not in FUNCTION_SPECS:
            kind = "constant"
        blocked = self.kind.blockSignals(True)
        self.kind.setCurrentText(kind)
        self.kind.blockSignals(blocked)
        self._rebuild()
        for name, _ in FUNCTION_SPECS[kind]:
            if name in value and name in self._edits:
                self._edits[name].setText(str(value[name]))

    def get_value(self):
        kind = self.kind.currentText()
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
        top.addWidget(QtWidgets.QLabel("type:"))
        self.dist = QtWidgets.QComboBox()
        self.dist.addItems(list(DISTRIBUTION_SPECS.keys()))
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
        for pname, _ptype, pdefault in DISTRIBUTION_SPECS[self.dist.currentText()]:
            tf = TimeFunctionWidget(value={"kind": "constant", "value": pdefault})
            self._form.addRow(f"{pname}", tf)
            self._params[pname] = tf

    def set_value(self, value):
        value = value or {}
        dist_type = value.get("dist_type", "Constant")
        if dist_type not in DISTRIBUTION_SPECS:
            dist_type = "Constant"
        blocked = self.dist.blockSignals(True)
        self.dist.setCurrentText(dist_type)
        self.dist.blockSignals(blocked)
        self._rebuild()
        params = value.get("params", {})
        for pname, tf in self._params.items():
            if pname in params:
                tf.set_value(params[pname])

    def get_value(self):
        return {"dist_type": self.dist.currentText(),
                "params": {n: w.get_value() for n, w in self._params.items()}}


class InfFloatWidget(QtWidgets.QWidget):
    """A float that can also be infinite (checkbox). Value: number or the string "inf"."""

    def __init__(self, value="inf", parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.chk = QtWidgets.QCheckBox("infinite")
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


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x) == int(x) else str(x)


class HourMinuteWidget(QtWidgets.QWidget):
    """A point in time entered as hours + minutes; the stored value is raw minutes,
    matching the simulation's Time(h, m) = 60*h + m. With allow_inf, an 'infinite'
    checkbox makes get_value() return the string \"inf\" (like InfFloatWidget)."""

    def __init__(self, value=0.0, allow_inf=False, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.chk = None
        if allow_inf:
            self.chk = QtWidgets.QCheckBox("infinite")
            lay.addWidget(self.chk)
        self.h = QtWidgets.QLineEdit(); self.h.setMaximumWidth(48)
        self.m = QtWidgets.QLineEdit(); self.m.setMaximumWidth(48)
        lay.addWidget(self.h); lay.addWidget(QtWidgets.QLabel("h"))
        lay.addWidget(self.m); lay.addWidget(QtWidgets.QLabel("m"))
        lay.addStretch(1)
        if self.chk is not None:
            self.chk.toggled.connect(self.h.setDisabled)
            self.chk.toggled.connect(self.m.setDisabled)
        self.set_value(value)

    def set_value(self, value):
        infinite = (value in ("inf", "Infinity") or (isinstance(value, float) and value == float("inf")))
        if self.chk is not None:
            self.chk.setChecked(infinite)
        if infinite:
            self.h.setText(""); self.m.setText("")
            self.h.setDisabled(True); self.m.setDisabled(True)
            return
        minutes = as_float(value)
        hours = int(minutes // 60)
        self.h.setText(str(hours))
        self.m.setText(_fmt_num(minutes - 60 * hours))
        self.h.setDisabled(False); self.m.setDisabled(False)

    def get_value(self):
        if self.chk is not None and self.chk.isChecked():
            return "inf"
        return 60 * as_float(self.h.text()) + as_float(self.m.text())


class _IntervalRow(QtWidgets.QWidget):
    def __init__(self, start=480.0, end=1020.0, on_remove=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.start = HourMinuteWidget(start)
        self.end = HourMinuteWidget(end)
        lay.addWidget(QtWidgets.QLabel("start:")); lay.addWidget(self.start)
        lay.addWidget(QtWidgets.QLabel("end:")); lay.addWidget(self.end)
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
            self._add(iv.get("start", 480.0), iv.get("end", 1020.0))
        self.chk.setChecked(working)
        self._box.setEnabled(working)

    def _add(self, start=480.0, end=1020.0):
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


class ShiftEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, entry=None):
        super().__init__(parent)
        self.setWindowTitle("Shift definition")
        entry = entry or {}
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(entry.get("name", ""))
        form.addRow("name", self.name)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("Shifts per weekday (times of day as hours + minutes):"))
        days = entry.get("days", [])
        self.day_rows = []
        for i, label in enumerate(WEEKDAYS):
            d = days[i] if i < len(days) else {}
            row = _DayRow(label, d.get("working", False), d.get("intervals"))
            self.day_rows.append(row)
            lay.addWidget(row)
        form2 = QtWidgets.QFormLayout()
        self.days_off = QtWidgets.QLineEdit(",".join(str(d) for d in entry.get("days_off", [])))
        form2.addRow("days off (integer day numbers from t=0, comma-separated)", self.days_off)
        hz = entry.get("horizon", {"start": 0, "end": 7})
        hbox = QtWidgets.QHBoxLayout()
        self.h_start = QtWidgets.QLineEdit(str(hz.get("start", 0))); self.h_start.setMaximumWidth(70)
        self.h_end = QtWidgets.QLineEdit(str(hz.get("end", 7))); self.h_end.setMaximumWidth(70)
        hbox.addWidget(QtWidgets.QLabel("start day:")); hbox.addWidget(self.h_start)
        hbox.addWidget(QtWidgets.QLabel("end day:")); hbox.addWidget(self.h_end); hbox.addStretch(1)
        hw = QtWidgets.QWidget(); hw.setLayout(hbox)
        form2.addRow("horizon (in days)", hw)
        lay.addLayout(form2)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def data(self):
        days_off = [as_int(x) for x in self.days_off.text().split(",") if x.strip() != ""]
        return {
            "name": self.name.text().strip(),
            "days": [r.data() for r in self.day_rows],
            "days_off": days_off,
            "horizon": {"start": as_int(self.h_start.text()), "end": as_int(self.h_end.text())},
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
        form.addRow("name", self.name)
        form.addRow("capacity (number of operators)", self.capacity)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("productivity:"))
        self.prod = SamplerWidget(entry.get("productivity"))
        lay.addWidget(self.prod)
        lay.addWidget(QtWidgets.QLabel("shifts (their concatenation is the group's schedule):"))
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
        self.restockable = QtWidgets.QCheckBox("restockable")
        form.addRow("name", self.name)
        form.addRow("lifespan", self.lifespan)
        form.addRow("max storage capacity", self.max_cap)
        form.addRow("initial capacity (in [0, max])", self.init_cap)
        form.addRow("", self.restockable)
        lay.addLayout(form)
        self.restock_box = QtWidgets.QGroupBox("restocking")
        rlay = QtWidgets.QVBoxLayout(self.restock_box)
        rlay.addWidget(QtWidgets.QLabel("order duration:"))
        self.order = SamplerWidget(entry.get("order_duration"))
        rlay.addWidget(self.order)
        rlay.addWidget(QtWidgets.QLabel("delivery duration:"))
        self.delivery = SamplerWidget(entry.get("delivery_duration"))
        rlay.addWidget(self.delivery)
        tform = QtWidgets.QFormLayout()
        self.threshold = QtWidgets.QLineEdit(str(entry.get("threshold", 0.0)))
        tform.addRow("reorder threshold", self.threshold)
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

    def _make_editor(self, entry):
        return ShiftEditorDialog(self, entry)


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

# Protocol types that carry a numeric parameter: type -> (json key, field label, default).
POLICY_TYPE_PARAMS = {
    "AbortOrWaitForCarriers": ("tolerance_fraction", "tolerance fraction", 0.5),
    "PartiallyConstrainedByShift": ("tolerance", "tolerance (time)", 0.0),
}


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
        add = QtWidgets.QPushButton(f"+ {add_label}")
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
        edit = QtWidgets.QLineEdit(str(value)); edit.setMaximumWidth(70)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        h.addWidget(combo); h.addWidget(QtWidgets.QLabel(self._label + ":")); h.addWidget(edit); h.addWidget(rm); h.addStretch(1)
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
        add = QtWidgets.QPushButton("+ alternative (OR)")
        add.clicked.connect(lambda: self._add_alt())
        lay.addWidget(add)
        for alt in (value or []):
            self._add_alt(alt)

    def _add_alt(self, members=None):
        box = QtWidgets.QGroupBox(f"alternative {len(self._alts) + 1} (all needed together)")
        bl = QtWidgets.QVBoxLayout(box)
        picker = ResourcePickerWidget(self._names, value_label="count", add_label="operator group", integer=True,
                                      entries=[{"resource": m.get("operator"), "value": m.get("count", 1)} for m in (members or [])])
        bl.addWidget(picker)
        rm = QtWidgets.QPushButton("remove alternative")
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
    """The five task protocols with their defaults. Types listed in POLICY_TYPE_PARAMS
    expose their numeric parameter (AbortOrWaitForCarriers' tolerance_fraction,
    PartiallyConstrainedByShift's tolerance in time units past the shift end).
    Value: {protocol_name: {"type", ...param}}."""

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        value = value or {}
        form = QtWidgets.QFormLayout(self)
        self._combos = {}
        self._params = {}
        for name, (options, default) in POLICY_OPTIONS.items():
            row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            saved = value.get(name, {})
            combo = QtWidgets.QComboBox(); combo.addItems(options)
            combo.setCurrentText(saved.get("type", default))
            h.addWidget(combo)
            lbl = QtWidgets.QLabel("")
            edit = QtWidgets.QLineEdit(); edit.setMaximumWidth(60)
            saved_spec = POLICY_TYPE_PARAMS.get(saved.get("type", default))
            if saved_spec is not None and saved_spec[0] in saved:
                edit.setText(str(saved[saved_spec[0]]))
            h.addWidget(lbl); h.addWidget(edit); h.addStretch(1)
            self._combos[name] = combo
            self._params[name] = (lbl, edit)
            def _upd(_=None, n=name):
                spec = POLICY_TYPE_PARAMS.get(self._combos[n].currentText())
                p_lbl, p_edit = self._params[n]
                p_lbl.setVisible(spec is not None)
                p_edit.setVisible(spec is not None)
                if spec is not None:
                    p_lbl.setText(spec[1] + ":")
                    if not p_edit.text():
                        p_edit.setText(str(spec[2]))
            combo.currentTextChanged.connect(_upd)
            _upd()
            form.addRow(name, row)

    def get_value(self):
        out = {}
        for name, combo in self._combos.items():
            t = combo.currentText()
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


class IntervalListWidget(QtWidgets.QWidget):
    """A list of {start, end} intervals with '+ interval'."""

    def __init__(self, intervals=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ interval"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        self._rows = []
        for iv in (intervals or []):
            self._add(iv.get("start", 0.0), iv.get("end", 1.0))

    def _add(self, start=0.0, end=1.0):
        row = _IntervalRow(start, end, on_remove=self._remove)
        self._rows.append(row); self._vl.addWidget(row)

    def _remove(self, row):
        if row in self._rows:
            self._rows.remove(row); row.setParent(None); row.deleteLater()

    def value(self):
        return [r.data() for r in self._rows]


class NameValuePicker(QtWidgets.QWidget):
    """Rows of (name-combo, int/float). Generic; used for generator model goals."""

    def __init__(self, names, value_label="goal", integer=True, entries=None, key="model", parent=None):
        super().__init__(parent)
        self._names = list(names); self._label = value_label; self._int = integer; self._key = key
        self._rows = []
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e.get(key), e.get("value", e.get("goal", 1)))

    def _add(self, name=None, value=1):
        row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if name in self._names:
            combo.setCurrentText(name)
        edit = QtWidgets.QLineEdit(str(value)); edit.setMaximumWidth(70)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        h.addWidget(combo); h.addWidget(QtWidgets.QLabel(self._label + ":")); h.addWidget(edit); h.addWidget(rm); h.addStretch(1)
        entry = (row, combo, edit); rm.clicked.connect(lambda: self._remove(entry))
        self._rows.append(entry); self._vl.addWidget(row)

    def _remove(self, entry):
        if entry in self._rows:
            self._rows.remove(entry); entry[0].setParent(None); entry[0].deleteLater()

    def value(self):
        out = []
        for _, combo, edit in self._rows:
            if combo.currentText():
                v = as_int(edit.text()) if self._int else as_float(edit.text())
                out.append({self._key: combo.currentText(), "value": v})
        return out


class ShutdownsMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Shutdowns")
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.type = QtWidgets.QComboBox(); self.type.addItems(SHUTDOWN_TYPES)
        self.type.setCurrentText(node.get_property("shutdown_type") if node.has_property("shutdown_type") else "NON_FLEXIBLE")
        form.addRow("type", self.type)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("intervals (simulation times as hours + minutes):"))
        self.intervals = IntervalListWidget(get_property_json(node, "intervals", []))
        lay.addWidget(self.intervals)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        self.node.set_property("shutdown_type", self.type.currentText())
        set_property_json(self.node, "intervals", self.intervals.value())


class BufferMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node, model_registry):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Buffer")
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        lay.addWidget(tabs)

        # --- Buffer tab: valid models, type, capacity ---
        buf_tab = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(buf_tab)
        bl.addWidget(QtWidgets.QLabel("valid models (selecting a model selects its children):"))
        self.models = ModelTreeWidget(model_registry, checked=get_property_json(node, "valid_models", []))
        bl.addWidget(self.models)
        form = QtWidgets.QFormLayout()
        self.buffer_type = QtWidgets.QComboBox()
        for t in BUFFER_TYPES:
            self.buffer_type.addItem(t.capitalize(), t)
        cur_type = node.get_property("buffer_type") if node.has_property("buffer_type") else "PASSAGE"
        i = self.buffer_type.findData(cur_type)
        self.buffer_type.setCurrentIndex(i if i >= 0 else 0)
        form.addRow("type", self.buffer_type)
        self.capacity = InfFloatWidget(node.get_property("capacity") if node.has_property("capacity") else "inf")
        form.addRow("capacity", self.capacity)
        bl.addLayout(form)
        tabs.addTab(buf_tab, "Buffer")

        # --- Monitor tab: which statistics to track on this buffer ---
        mon_tab = QtWidgets.QWidget()
        ml = QtWidgets.QVBoxLayout(mon_tab)
        ml.addWidget(QtWidgets.QLabel("statistics to monitor (unchecked everywhere = buffer not monitored):"))
        current = get_property_json(node, "monitor", {})
        self._monitor = {}
        for key, label, _default in MONITOR_STATS:
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(bool(current.get(key, False)))
            ml.addWidget(cb)
            self._monitor[key] = cb
        ml.addStretch(1)
        tabs.addTab(mon_tab, "Monitor")

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "valid_models", self.models.checked_models())
        self.node.set_property("capacity", self.capacity.get_value())
        self.node.set_property("buffer_type", self.buffer_type.currentData())
        set_property_json(self.node, "monitor", {k: cb.isChecked() for k, cb in self._monitor.items()})


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
            ff.addRow("freeloader", self.freeloader)
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


class GeneratorMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node, model_registry, shift_names):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Piece generator")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("model goals (only leaf models can be generated):"))
        goals = [{"model": e.get("model"), "value": e.get("goal", e.get("value", 1))}
                 for e in get_property_json(node, "models_goals", [])]
        self.goals = NameValuePicker(_leaf_model_names(model_registry), "goal", integer=True, entries=goals, key="model")
        lay.addWidget(self.goals)
        lay.addWidget(QtWidgets.QLabel("shifts:"))
        self.shifts = ShiftPickerWidget(shift_names, get_property_json(node, "shifts", []))
        lay.addWidget(self.shifts)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "models_goals",
                          [{"model": e["model"], "goal": e["value"]} for e in self.goals.value()])
        set_property_json(self.node, "shifts", self.shifts.chosen())


class StoppingCriterionDialog(QtWidgets.QDialog):
    """Pick when the simulation ends. Parameter slots appear dynamically per type:
    Time -> one time slot; Pieces produced -> total + timeout (the exit buffer is
    deduced by the parser from the single EXIT buffer, so it is not selected here)."""

    def __init__(self, parent, criterion):
        super().__init__(parent)
        self.setWindowTitle("Stopping criterion")
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QFormLayout()
        self.type = QtWidgets.QComboBox()
        for label, canonical in STOPPING_CRITERION_TYPES:
            self.type.addItem(label, canonical)
        top.addRow("stop on", self.type)
        lay.addLayout(top)
        self._host = QtWidgets.QWidget()
        self._form = QtWidgets.QFormLayout(self._host)
        self._form.setContentsMargins(12, 4, 0, 0)
        lay.addWidget(self._host)
        self._widgets = {}
        self.type.currentIndexChanged.connect(self._rebuild)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

        criterion = criterion or {}
        ci = self.type.findData(criterion.get("type", "ByTime"))
        blocked = self.type.blockSignals(True)
        self.type.setCurrentIndex(ci if ci >= 0 else 0)
        self.type.blockSignals(blocked)
        self._rebuild()
        self._load(criterion)

    def _rebuild(self, *_):
        while self._form.rowCount():
            self._form.removeRow(0)
        self._widgets = {}
        canonical = self.type.currentData()
        if canonical == "ByTime":
            e = HourMinuteWidget(0.0)
            self._widgets["time"] = e
            self._form.addRow("time", e)
        elif canonical == "ByPiecesProduced":
            total = QtWidgets.QLineEdit("0")
            self._widgets["total"] = total
            self._form.addRow("total pieces", total)
            timeout = HourMinuteWidget("inf", allow_inf=True)
            self._widgets["timeout"] = timeout
            self._form.addRow("timeout", timeout)

    def _load(self, criterion):
        if criterion.get("type") != self.type.currentData():
            return
        if "time" in self._widgets:
            self._widgets["time"].set_value(criterion.get("time", 0))
        if "total" in self._widgets:
            self._widgets["total"].setText(str(criterion.get("total", 0)))
        if "timeout" in self._widgets:
            self._widgets["timeout"].set_value(criterion.get("timeout", "inf"))

    def value(self):
        canonical = self.type.currentData()
        if canonical == "ByTime":
            return {"type": "ByTime", "time": self._widgets["time"].get_value()}
        return {"type": "ByPiecesProduced",
                "total": as_int(self._widgets["total"].text()),
                "timeout": self._widgets["timeout"].get_value()}


class BreakdownMenuDialog(QtWidgets.QDialog):
    def __init__(self, parent, node):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle("Breakdown")
        lay = QtWidgets.QVBoxLayout(self)
        mtbf = get_property_json(node, "mtbf", {}) or {}
        lay.addWidget(QtWidgets.QLabel("mtbf (mean time between failures):"))
        self.mode = QtWidgets.QComboBox(); self.mode.addItems(["distribution", "bathtub"])
        self.mode.setCurrentText(mtbf.get("mode", "distribution"))
        lay.addWidget(self.mode)
        self.dist = SamplerWidget(mtbf.get("distribution"))
        lay.addWidget(self.dist)
        self.bathtub_box = QtWidgets.QGroupBox("bathtub failure-rate a·e^(t/tau)+c+(beta/eta)(t/eta)^(beta-1)")
        bl = QtWidgets.QFormLayout(self.bathtub_box)
        self.bt = {}
        for k, d in (("a", 0.001), ("tau", 500.0), ("c", 0.01), ("beta", 2.0), ("eta", 300.0),
                     ("tolerance", 60.0), ("max_iters", 10000)):
            e = QtWidgets.QLineEdit(str(mtbf.get(k, d))); self.bt[k] = e; bl.addRow(k, e)
        lay.addWidget(self.bathtub_box)
        self.mode.currentTextChanged.connect(self._upd)
        lay.addWidget(QtWidgets.QLabel("mttr (mean time to repair) distribution:"))
        self.mttr = SamplerWidget(get_property_json(node, "mttr", None))
        lay.addWidget(self.mttr)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)
        self._upd()

    def _upd(self, *_):
        bathtub = self.mode.currentText() == "bathtub"
        self.bathtub_box.setVisible(bathtub); self.dist.setVisible(not bathtub)

    def apply(self):
        if self.mode.currentText() == "distribution":
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
        add = QtWidgets.QPushButton("+ model config"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        box = QtWidgets.QGroupBox(); bl = QtWidgets.QFormLayout(box)
        combo = QtWidgets.QComboBox(); combo.addItems(self._models)
        if entry.get("model") in self._models:
            combo.setCurrentText(entry["model"])
        bl.addRow("model", combo)
        dur = SamplerWidget(entry.get("duration")); bl.addRow("duration", dur)
        res = ResourcePickerWidget(self._resources, "quantity",
                                   [{"resource": r.get("resource"), "value": r.get("value", r.get("quantity", 1.0))}
                                    for r in entry.get("resources", [])])
        bl.addRow("resources", res)
        mn = QtWidgets.QLineEdit(str(entry.get("min_carrier_capacity", 1))); mn.setMaximumWidth(60)
        mx = QtWidgets.QLineEdit(str(entry.get("max_carrier_capacity", 1))); mx.setMaximumWidth(60)
        bl.addRow("min carrier capacity", mn); bl.addRow("max carrier capacity", mx)
        rm = QtWidgets.QPushButton("remove model"); bl.addRow(rm)
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


def _carrier_common_tab(node, operator_names, shift_names, collector_types, extra=None):
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
    f.addWidget(QtWidgets.QLabel("startup duration:")); acc["startup_duration"] = SamplerWidget(get_property_json(node, "startup_duration", None)); f.addWidget(acc["startup_duration"])
    f.addWidget(QtWidgets.QLabel("loading duration:")); acc["loading_duration"] = SamplerWidget(get_property_json(node, "loading_duration", None)); f.addWidget(acc["loading_duration"])
    for label, wdg in extra.get("durations", []):
        f.addWidget(QtWidgets.QLabel(label)); f.addWidget(wdg)
    f.addStretch(1)
    tabs.append(("Durations", _scroll(t)))

    # operators
    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    f.addWidget(QtWidgets.QLabel("operators (alternatives):")); acc["operators"] = AlternativesWidget(operator_names, get_property_json(node, "operators", [])); f.addWidget(acc["operators"])
    f.addWidget(QtWidgets.QLabel("loading operators:")); acc["loading_operators"] = AlternativesWidget(operator_names, get_property_json(node, "loading_operators", [])); f.addWidget(acc["loading_operators"])
    f.addWidget(QtWidgets.QLabel("startup operators:")); acc["startup_operators"] = AlternativesWidget(operator_names, get_property_json(node, "startup_operators", [])); f.addWidget(acc["startup_operators"])
    tabs.append(("Operators", _scroll(t)))

    # carriers
    t = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(t)
    for key, default in (("min_carriers", 1), ("max_capacity", 1.0), ("timeout", 1e9), ("priority", 5)):
        acc[key] = QtWidgets.QLineEdit(str(node.get_property(key))); f.addRow(key, acc[key])
    acc["contiguous_carriers"] = QtWidgets.QCheckBox(); acc["contiguous_carriers"].setChecked(bool(node.get_property("contiguous_carriers"))); f.addRow("contiguous carriers", acc["contiguous_carriers"])
    acc["independent_carriers"] = QtWidgets.QCheckBox(); acc["independent_carriers"].setChecked(bool(node.get_property("independent_carriers"))); f.addRow("independent carriers", acc["independent_carriers"])
    for label, wdg in extra.get("carriers", []):
        f.addRow(label, wdg)
    tabs.append(("Carriers", _scroll(t)))

    # scopes
    t = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(t)
    acc["operator_scope"] = QtWidgets.QComboBox(); acc["operator_scope"].addItems(["PER_BATCH", "PER_TASK"]); acc["operator_scope"].setCurrentText(node.get_property("operator_scope"))
    acc["resource_scope"] = QtWidgets.QComboBox(); acc["resource_scope"].addItems(["PER_UNIT", "PER_BATCH"]); acc["resource_scope"].setCurrentText(node.get_property("resource_scope"))
    f.addRow("operator scope", acc["operator_scope"]); f.addRow("resource scope", acc["resource_scope"])
    if collector_types is not None:
        acc["collector_type"] = QtWidgets.QComboBox(); acc["collector_type"].addItems(collector_types); acc["collector_type"].setCurrentText(node.get_property("collector_type") if node.has_property("collector_type") else collector_types[0])
        f.addRow("collector type", acc["collector_type"])
    for label, wdg in extra.get("scopes", []):
        f.addRow(label, wdg)
    tabs.append(("Scopes", _scroll(t)))

    # protocols (stored under the "policies" property/JSON key)
    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    acc["policies"] = PoliciesWidget(get_property_json(node, "policies", {})); f.addWidget(acc["policies"]); f.addStretch(1)
    tabs.append(("Protocols", _scroll(t)))

    # shifts
    t = QtWidgets.QWidget(); f = QtWidgets.QVBoxLayout(t)
    f.addWidget(QtWidgets.QLabel("task shifts:")); acc["task_shifts"] = ShiftPickerWidget(shift_names, get_property_json(node, "task_shifts", [])); f.addWidget(acc["task_shifts"]); f.addStretch(1)
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
    node.set_property("operator_scope", acc["operator_scope"].currentText())
    node.set_property("resource_scope", acc["resource_scope"].currentText())
    if "collector_type" in acc:
        node.set_property("collector_type", acc["collector_type"].currentText())
    node.set_property("min_carriers", as_int(acc["min_carriers"].text(), 1))
    node.set_property("max_capacity", as_float(acc["max_capacity"].text(), 1.0))
    node.set_property("timeout", as_float(acc["timeout"].text(), 1e9))
    node.set_property("priority", as_int(acc["priority"].text(), 5))
    node.set_property("contiguous_carriers", acc["contiguous_carriers"].isChecked())
    node.set_property("independent_carriers", acc["independent_carriers"].isChecked())
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
        common, self.acc = _carrier_common_tab(node, _names(win.operator_registry), _names(win.shift_registry), COLLECTOR_TYPES)
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
        f0.addWidget(QtWidgets.QLabel("non-transformed inputs (quantity consumed):"))
        self.non_transformed = ResourcePickerWidget(rnames, "quantity",
            [{"resource": e.get("resource"), "value": e.get("value", e.get("quantity", 1.0))} for e in get_property_json(node, "non_transformed_resources", [])])
        f0.addWidget(self.non_transformed)
        f0.addWidget(QtWidgets.QLabel("transformed inputs (proportion + salvageable):"))
        self.transformed = _TransformedWidget(rnames, get_property_json(node, "transformed_resources", []))
        f0.addWidget(self.transformed)
        f0.addWidget(QtWidgets.QLabel("outputs produced (bounded distribution, ≥ 0):"))
        self.outputs = _OutputsWidget(rnames, get_property_json(node, "resources_out", []))
        f0.addWidget(self.outputs)
        tabs.addTab(_scroll(t0), "Resources")
        # resource-task-specific fields, injected into the shared tabs where they belong
        self.duration = SamplerWidget(get_property_json(node, "duration", None))
        self.min_cc = QtWidgets.QLineEdit(str(node.get_property("min_carrier_capacity")))
        self.max_cc = QtWidgets.QLineEdit(str(node.get_property("max_carrier_capacity")))
        self.rct = QtWidgets.QComboBox(); self.rct.addItems(RESOURCE_COLLECTOR_TYPES); self.rct.setCurrentText(node.get_property("resource_collector_type"))
        extra = {
            "durations": [("duration:", self.duration)],
            "carriers": [("min carrier capacity", self.min_cc), ("max carrier capacity", self.max_cc)],
            "scopes": [("resource collector type", self.rct)],
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
        self.node.set_property("resource_collector_type", self.rct.currentText())
        _apply_carrier_common(self.node, self.acc)


class _TransformedWidget(QtWidgets.QWidget):
    def __init__(self, resource_names, entries=None, parent=None):
        super().__init__(parent)
        self._names = list(resource_names); self._rows = []
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget(); self._vl = QtWidgets.QVBoxLayout(self._host); self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton("+ transformed resource"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if entry.get("resource") in self._names:
            combo.setCurrentText(entry["resource"])
        prop = QtWidgets.QLineEdit(str(entry.get("proportion", 1.0))); prop.setMaximumWidth(60)
        salv = QtWidgets.QCheckBox("salvageable"); salv.setChecked(bool(entry.get("salvageable", True)))
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        h.addWidget(combo); h.addWidget(QtWidgets.QLabel("proportion:")); h.addWidget(prop); h.addWidget(salv); h.addWidget(rm); h.addStretch(1)
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
        add = QtWidgets.QPushButton("+ output resource"); add.clicked.connect(lambda: self._add()); lay.addWidget(add)
        for e in (entries or []):
            self._add(e)

    def _add(self, entry=None):
        entry = entry or {}
        box = QtWidgets.QGroupBox(); bl = QtWidgets.QFormLayout(box)
        combo = QtWidgets.QComboBox(); combo.addItems(self._names)
        if entry.get("resource") in self._names:
            combo.setCurrentText(entry["resource"])
        bl.addRow("resource", combo)
        dist = SamplerWidget(entry.get("distribution")); bl.addRow("amount", dist)
        lb = QtWidgets.QLineEdit(str(entry.get("lowerbound", 0.0))); lb.setMaximumWidth(90)
        ub = QtWidgets.QLineEdit(str(entry.get("upperbound", 1.0))); ub.setMaximumWidth(90)
        bl.addRow("lowerbound (≥ 0)", lb)
        bl.addRow("upperbound (finite)", ub)
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


class FlowEditorWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 850)

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
        self.stopping_criterion = {}  # {} | {"type": "ByTime"|"ByPiecesProduced", ...}

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
        self._connect_signals()
        self.statusBar().showMessage("Ready. Use the Create menu to add nodes.")

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        act_new = file_menu.addAction("New")
        act_import = file_menu.addAction("Import clean JSON (add)...")
        act_export = file_menu.addAction("Export clean JSON...")
        act_new.triggered.connect(self.new_graph)
        act_import.triggered.connect(self.import_clean_json_dialog)
        act_export.triggered.connect(self.export_clean_json_dialog)

        registries_menu = self.menuBar().addMenu("Registries")
        registries_menu.addAction("Edit models...").triggered.connect(self.edit_models)
        registries_menu.addAction("Edit resources...").triggered.connect(self.edit_resources)
        registries_menu.addAction("Edit operators...").triggered.connect(self.edit_operators)
        registries_menu.addAction("Edit shifts...").triggered.connect(self.edit_shifts)

        simulation_menu = self.menuBar().addMenu("Simulation")
        simulation_menu.addAction("Stopping criterion...").triggered.connect(self.edit_stopping_criterion)

        edit_menu = self.menuBar().addMenu("Edit")
        delete_action = edit_menu.addAction("Delete selected")
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected_nodes)

        tools_menu = self.menuBar().addMenu("Tools")
        tools_menu.addAction("Validate graph").triggered.connect(self.validate_graph_dialog)
        tools_menu.addAction("Frame all").triggered.connect(self.frame_all)

        templates_menu = self.menuBar().addMenu("Templates")
        templates_menu.addAction("Add Backdrop Around Selection").triggered.connect(self.add_backdrop_around_selection)

        create_menu = self.menuBar().addMenu("Create")
        for label, cls_name in [
            ("Shutdowns", "simulation.flow.ShutdownsNode"),
            ("Buffer", "simulation.flow.BufferNode"),
            ("Router", "simulation.flow.RouterNode"),
            ("Piece Generator", "simulation.flow.PieceGeneratorNode"),
            ("Piece Task", "simulation.flow.TaskNode"),
            ("Resource Task", "simulation.flow.ResourceTaskNode"),
            ("Breakdown", "simulation.flow.BreakdownNode"),
        ]:
            action = create_menu.addAction(label)
            action.triggered.connect(lambda checked=False, t=cls_name: self.create_node(t))

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
        self.graph.clear_session()
        self.model_registry = []
        self.stopping_criterion = {}

    def edit_models(self):
        dlg = ModelRegistryDialog(self, self.model_registry)
        if dlg.exec():
            try:
                self.model_registry = dlg.models()
                self.statusBar().showMessage(f"{len(self.model_registry)} models defined.")
            except Exception as e:
                qmessage(self, "Invalid models", str(e), QtWidgets.QMessageBox.Warning)

    def edit_resources(self):
        dlg = ResourceRegistryDialog(self, self.resource_registry)
        if dlg.exec():
            self.resource_registry = dlg.entries()
            self.statusBar().showMessage(f"{len(self.resource_registry)} resources defined.")

    def edit_operators(self):
        shift_names = [s.get("name", "") for s in self.shift_registry if s.get("name")]
        dlg = OperatorRegistryDialog(self, self.operator_registry, shift_names=shift_names)
        if dlg.exec():
            self.operator_registry = dlg.entries()
            self.statusBar().showMessage(f"{len(self.operator_registry)} operator groups defined.")

    def edit_shifts(self):
        dlg = ShiftRegistryDialog(self, self.shift_registry)
        if dlg.exec():
            self.shift_registry = dlg.entries()
            self.statusBar().showMessage(f"{len(self.shift_registry)} shift definitions.")

    def edit_stopping_criterion(self):
        dlg = StoppingCriterionDialog(self, self.stopping_criterion)
        if dlg.exec():
            self.stopping_criterion = dlg.value()
            label = next((lbl for lbl, canon in STOPPING_CRITERION_TYPES
                          if canon == self.stopping_criterion.get("type")), "?")
            self.statusBar().showMessage(f"Stopping criterion: {label}.")

    def on_node_double_clicked(self, node):
        kind = node_kind(node)
        dlg = None
        if kind == "Shutdowns":
            dlg = ShutdownsMenuDialog(self, node)
        elif kind == "Buffer":
            dlg = BufferMenuDialog(self, node, self.model_registry)
        elif kind == "Router":
            dlg = RouterMenuDialog(self, node)
        elif kind == "PieceGenerator":
            dlg = GeneratorMenuDialog(self, node, self.model_registry, _names(self.shift_registry))
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
        return {
            "editor": {"name": APP_NAME, "version": EDITOR_VERSION, "format": "clean-json"},
            "models": self.model_registry,
            "resources": self.resource_registry,
            "operators": self.operator_registry,
            "shifts": self.shift_registry,
            "stopping_criterion": self.stopping_criterion,
            "nodes": nodes,
            "connections": self.connections_clean(),
            "backdrops": backdrops,
        }

    def export_clean_json_dialog(self):
        problems = self.validate_graph()
        if problems:
            answer = QtWidgets.QMessageBox.question(
                self, "Validation warnings",
                "The graph has validation warnings. Export anyway?\n\n" + "\n".join(problems[:12]))
            if answer != QtWidgets.QMessageBox.Yes:
                return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export clean JSON", "clean_export.json", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.export_clean_json(), f, indent=2, ensure_ascii=False)
        self.statusBar().showMessage(f"Exported clean JSON: {path}")

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
        for c in self.connections_clean():
            if not is_valid_connection(c["from_kind"], c["from_port"], c["to_kind"], c["to_port"]):
                problems.append(f"Invalid connection: {c['from_kind']}.{c['from_port']} -> {c['to_kind']}.{c['to_port']}")

        for node in self.all_nodes():
            kind = node_kind(node)
            name = node.name()
            if kind in ("Task", "ResourceTask"):
                # the simulation rejects this protocol combination at load time
                pol = get_property_json(node, "policies", {})
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
            if kind == "Task":
                if not connected_refs_from_port(node, "bufs_in", "input"):
                    problems.append(f"Piece Task '{name}' has no input buffers.")
                if not get_output_refs(node, "bufs_out"):
                    problems.append(f"Piece Task '{name}' has no output buffers.")
                mc = get_property_json(node, "models_configs", [])
                if not mc:
                    problems.append(f"Piece Task '{name}' has no model configs.")
                else:
                    for m in mc:
                        if not m.get("duration"):
                            problems.append(f"Piece Task '{name}' model '{m.get('model')}' has no duration.")
                    # non-discriminating collectors need uniform duration / carrier-capacity across models
                    ct = str(node.get_property("collector_type") if node.has_property("collector_type") else "")
                    if ct.startswith("NON_DISCRIMINATING"):
                        for f, lbl in [("duration", "duration"),
                                       ("min_carrier_capacity", "min_carrier_capacity"),
                                       ("max_carrier_capacity", "max_carrier_capacity")]:
                            if len({json.dumps(m.get(f), sort_keys=True) for m in mc}) > 1:
                                problems.append(f"Piece Task '{name}': a non-discriminating collector requires "
                                                f"all models to share the same {lbl}.")
                    self._check_flushability(node, [m.get("model") for m in mc if m.get("model")],
                                             "bufs_out", "Piece Task", problems)

            elif kind == "ResourceTask":
                if not get_property_json(node, "duration", None):
                    problems.append(f"Resource Task '{name}' has no duration.")
                outs = get_property_json(node, "resources_out", [])
                if not outs:
                    problems.append(f"Resource Task '{name}' has no output resources.")
                for out in outs:
                    if as_float(out.get("lowerbound", 0.0)) < 0:
                        problems.append(f"Resource Task '{name}': output '{out.get('resource')}' "
                                        f"lowerbound must be ≥ 0.")
                    ub = out.get("upperbound", 1.0)
                    if ub in ("inf", "Infinity") or as_float(ub, 1.0) == float("inf"):
                        problems.append(f"Resource Task '{name}': output '{out.get('resource')}' "
                                        f"upperbound must be finite.")
                # transformed-resource proportions are treated as probabilities: in [0,1] and sum to 1
                tr = get_property_json(node, "transformed_resources", [])
                props = [as_float(t.get("proportion", 0.0)) for t in tr]
                if not tr:
                    problems.append(f"Resource Task '{name}': needs transformed resources whose proportions "
                                    f"sum to 1 (the simulation rejects an empty set).")
                elif any(p < 0 or p > 1 for p in props):
                    problems.append(f"Resource Task '{name}': transformed-resource proportions must be in [0, 1].")
                elif abs(sum(props) - 1.0) > 1e-6:
                    problems.append(f"Resource Task '{name}': transformed-resource proportions must sum to 1 "
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
                goals = get_property_json(node, "models_goals", [])
                if not goals:
                    problems.append(f"Piece Generator '{name}' has no model goals.")
                if not get_output_refs(node, "bufs_out"):
                    problems.append(f"Piece Generator '{name}' has no outlets.")
                self._check_flushability(node, [g.get("model") for g in goals if g.get("model")],
                                         "bufs_out", "Piece Generator", problems)

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
                ivs = get_property_json(node, "intervals", [])
                if any(as_float(iv.get("end")) < as_float(iv.get("start")) for iv in ivs):
                    problems.append(f"Shutdowns '{name}': an interval has end before start.")
                sv = sorted(ivs, key=lambda x: as_float(x.get("start")))
                for a, b in zip(sv, sv[1:]):
                    if not (min(as_float(a.get("end")), as_float(b.get("end")))
                            < max(as_float(a.get("start")), as_float(b.get("start")))):
                        problems.append(f"Shutdowns '{name}': intervals overlap or touch "
                                        f"(they must be pairwise disjoint).")
                        break

        # Aggregate (whole-graph) checks.
        buffer_types = [node.get_property("buffer_type") if node.has_property("buffer_type") else "PASSAGE"
                        for node in self.all_nodes() if node_kind(node) == "Buffer"]
        exit_count = buffer_types.count("EXIT")
        if exit_count == 0:
            problems.append("No EXIT buffer: the parser expects exactly one to define the simulation's exit.")
        elif exit_count > 1:
            problems.append(f"{exit_count} EXIT buffers: the simulation allows at most one.")
        if "SCRAP" in buffer_types and not any(node_kind(n) == "PieceGenerator" for n in self.all_nodes()):
            problems.append("A SCRAP buffer needs a Piece Generator to return its scrapped pieces to.")

        crit = self.stopping_criterion or {}
        if not crit:
            problems.append("No stopping criterion set (Simulation > Stopping criterion...); "
                            "the simulation may never terminate.")
        elif crit.get("type") == "ByPiecesProduced" and exit_count != 1:
            problems.append("Stopping criterion 'Pieces produced' needs exactly one EXIT buffer to count.")

        return problems

    def delete_selected_nodes(self):
        selected_nodes = self.graph.selected_nodes()
        if not selected_nodes:
            return
        for node in selected_nodes:
            self.graph.delete_node(node)

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
                     "Select nodes, then use Templates > Add Backdrop Around Selection.",
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
        "Shutdowns": ["intervals"],
        "Buffer": ["valid_models", "monitor"],
        "PieceGenerator": ["models_goals", "shifts"],
        "Task": ["models_configs", "startup_duration", "loading_duration", "operators",
                 "loading_operators", "startup_operators", "task_shifts", "policies"],
        "ResourceTask": ["non_transformed_resources", "transformed_resources", "resources_out",
                         "duration", "startup_duration", "loading_duration", "operators",
                         "loading_operators", "startup_operators", "task_shifts", "policies"],
        "Breakdown": ["mtbf", "mttr"],
    }
    _IMPORT_SCALAR_PROPS = {
        "Shutdowns": ["shutdown_type"],
        "Buffer": ["capacity", "buffer_type"],
        "Task": ["operator_scope", "resource_scope", "min_carriers", "max_capacity",
                 "contiguous_carriers", "independent_carriers", "timeout", "priority", "collector_type"],
        "ResourceTask": ["resource_scope", "operator_scope", "resource_collector_type",
                         "min_carriers", "max_capacity", "min_carrier_capacity", "max_carrier_capacity",
                         "contiguous_carriers", "independent_carriers", "timeout", "priority"],
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
            entry = {"name": name, "parent": model.get("parent") or None}
            self.model_registry.append(entry)
            existing[name] = entry
        if conflicts:
            qmessage(self, "Model conflict",
                     "These imported models already exist with a different parent and were kept as-is:\n- "
                     + "\n- ".join(conflicts), QtWidgets.QMessageBox.Warning)

    def _merge_named_registry(self, attr: str, imported: list) -> None:
        """Merge imported registry entries (resources/operators/shifts) by name; existing
        entries win on a name clash (models are handled separately, with conflict warnings)."""
        reg = getattr(self, attr, None) or []
        existing = {e.get("name") for e in reg if e.get("name")}
        for entry in imported or []:
            name = entry.get("name")
            if name and name not in existing:
                reg.append(dict(entry))
                existing.add(name)
        setattr(self, attr, reg)

    def import_clean_json(self, data: dict):
        data = self._remap_ids(data)
        data = self._offset_imported_positions(data)
        self._merge_models(data.get("models", []))
        self._merge_named_registry("resource_registry", data.get("resources", []))
        self._merge_named_registry("operator_registry", data.get("operators", []))
        self._merge_named_registry("shift_registry", data.get("shifts", []))
        if not self.stopping_criterion and data.get("stopping_criterion"):
            self.stopping_criterion = data["stopping_criterion"]

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

        imported_backdrops = data.get("backdrops", [])
        if imported_backdrops:
            for group in imported_backdrops:
                group_node_ids = group.get("nodes", group.get("wrapped_node_ids", []))
                group_nodes = [id_to_node[nid] for nid in group_node_ids if nid in id_to_node]
                if not group_nodes:
                    continue
                backdrop = self.add_backdrop_for_nodes(group_nodes, group.get("title", "Imported group"),
                                                       width=group.get("width"), height=group.get("height"))
                position = group.get("position")
                if backdrop is not None and isinstance(position, list) and len(position) >= 2:
                    self.set_node_position_safe(backdrop, position[0], position[1])
        else:
            imported_nodes = list(id_to_node.values())
            if imported_nodes:
                self.add_backdrop_for_nodes(imported_nodes, "Imported simulation")

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
