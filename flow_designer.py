"""
Simulation Flow Editor using NodeGraphQt.

Goal
----
This editor is intentionally NOT a simulation runner.
It creates a typed node graph and exports a clean JSON file that you can later
parse into your simulation.py classes.

Install
-------
pip install NodeGraphQt PySide2
# or, depending on your environment:
pip install NodeGraphQt PySide6 Qt.py

Run
---
python flow_editor.py

Notes
-----
- Model definitions are global, edited from the "Models > Edit models..." menu.
- Cards/nodes use typed ports and the editor validates connections.
- Only clean JSON import/export is supported; NodeGraphQt session save/load was removed.
- SoftBuffer probabilities are stored by connected output node id, not by order.
- FirstTask model probabilities are edited with a model picker, not comma-separated text.
- HardBuffer is the old "Buffer"; SoftBuffer is the old "Buffer Tree".
  A SoftBuffer may route to another SoftBuffer (nested probabilistic routing).
- Backdrops are exported as clean JSON groups with their wrapped node ids AND their
  size, so importing restores them at the exact size they were saved at.

Advanced additions (vs flow_designer.py)
----------------------------------------
- Monitor cards: drop a Monitor, name it, and connect a HardBuffer's "monitor" output
  to it. Per-statistic checkboxes on the card pick which figures graph_parser_advanced
  reports (average length, average stay time, average time before arrival, etc.).
- Backdrop import/export now round-trips the backdrop size (width/height), fixing the
  bug where re-imported backdrops came back at a different size.
"""

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

APP_NAME = "Simulation Flow Editor"
EDITOR_VERSION = "0.1.0"

DISTRIBUTION_SPECS = {
    "Constant": [("value", float, 0.0)],
    "Uniform": [("low", float, 0.0), ("high", float, 1.0)],
    "Normal": [("mean", float, 0.0), ("std", float, 1.0)],
    "Exponential": [("mean", float, 1.0)],
    "Triangular": [("low", float, 0.0), ("mode", float, 0.5), ("high", float, 1.0)],
    "LogNormal": [("mean", float, 0.0), ("sigma", float, 1.0)],
}

BATCH_COLLECTORS = ["GreedyBatchCollector", "AltruisticBatchCollector"]
SCOPES_FOR_OPERATORS = ["PER_BATCH", "PER_TASK"]
SCOPES_FOR_RESOURCES = ["PER_PIECE", "PER_BATCH"]

# Old nomenclature -> new nomenclature, used when importing older clean JSON.
LEGACY_KIND_ALIASES = {
    "Buffer": "HardBuffer",
    "BufferTree": "SoftBuffer",
}

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

BUFFER_ROLES = ["Normal", "Exit", "Scrap"]

# Statistics offered by a Monitor card: (property key, checkbox label, default enabled).
# Keys must match MONITOR_STAT_DEFAULTS in graph_parser_advanced.py.
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
    """
    NodeGraphQt compatibility helper.

    In some versions:
        node.input("name") crashes because input() expects an index.
        node.output("name") crashes because output() expects an index.

    So we use:
        node.inputs()["name"]
        node.outputs()["name"]
    """
    try:
        if direction == "input":
            ports = node.inputs()
        else:
            ports = node.outputs()

        if isinstance(ports, dict):
            return ports.get(port_name)

        # Fallback for versions returning lists.
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
    return [
        node_uid(n)
        for n in connected_nodes_from_port(node, port_name, direction)
    ]


def get_input_ref(node, port_name: str):
    refs = connected_refs_from_port(node, port_name, "input")
    return refs[0] if refs else None


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
            f"Invalid template connection: "
            f"{out_kind}.{from_port_name} -> {in_kind}.{to_port_name}"
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


def add_float_input(node: BaseNode, name: str, label: str, default) -> None:
    """Embed an editable numeric field in the node. Falls back to a bin-only property."""
    try:
        node.add_text_input(name, label, text=str(default))
    except Exception:
        if not node.has_property(name):
            node.create_property(name, str(default))


def add_bool_input(node: BaseNode, name: str, label: str, default: bool) -> None:
    try:
        node.add_checkbox(name, label="", text=label, state=bool(default))
    except Exception:
        if not node.has_property(name):
            node.create_property(name, bool(default))

def add_combo_input(node: BaseNode, name: str, label: str, items: list, default: str) -> None:
    """
    Embed a dropdown menu in the node when NodeGraphQt supports it.
    Falls back to a normal property if not.
    """
    try:
        node.add_combo_menu(name, label=label, items=items)
        node.set_property(name, default)
    except Exception:
        if not node.has_property(name):
            node.create_property(name, default)

def set_text_prop(node: BaseNode, name: str, value) -> None:
    """Set a property as a string so it stays compatible with embedded text widgets."""
    sval = "" if value is None else str(value)
    if node.has_property(name):
        try:
            node.set_property(name, sval)
        except Exception:
            pass
    else:
        node.create_property(name, sval)


def set_bool_prop(node: BaseNode, name: str, value) -> None:
    bval = bool(value)
    if node.has_property(name):
        try:
            node.set_property(name, bval)
        except Exception:
            pass
    else:
        node.create_property(name, bval)


def read_capacity(node: BaseNode, default: float) -> float:
    for key in ("desired_capacity", "capacity"):
        if node.has_property(key):
            return as_float(node.get_property(key), default)
    return default


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


class DistributionNode(SimNode):
    NODE_NAME = "Distribution"
    kind = "Distribution"
    color = (80, 100, 160)

    def __init__(self):
        super().__init__()
        self.add_output("distribution", color=PORT_COLORS["duration"])
        self.create_property("dist_type", "Constant")
        self.create_property("params", {"value": 0.0})

    def to_clean_json(self) -> dict:
        params = self.get_property("params")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "distribution": {
                "type": self.get_property("dist_type"),
                "params": params or {},
            },
            "position": [self.x_pos(), self.y_pos()],
        }


class IntervalNode(SimNode):
    NODE_NAME = "Interval"
    kind = "Interval"
    color = (110, 90, 160)

    def __init__(self):
        super().__init__()
        self.add_output("interval", color=PORT_COLORS["interval"])
        add_float_input(self, "start", "start", 0.0)
        add_float_input(self, "end", "end", 1.0)

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "start": as_float(self.get_property("start")),
            "end": as_float(self.get_property("end")),
            "position": [self.x_pos(), self.y_pos()],
        }


class ScheduledShutdownsNode(SimNode):
    NODE_NAME = "Scheduled Shutdowns"
    kind = "ScheduledShutdowns"
    color = (125, 80, 130)

    def __init__(self):
        super().__init__()
        self.add_input("intervals", multi_input=True, color=PORT_COLORS["interval"])
        self.add_output("scheduled_shutdowns", color=PORT_COLORS["shutdown"])

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "intervals": connected_refs_from_port(self, "intervals", "input"),
            "position": [self.x_pos(), self.y_pos()],
        }


class ResourceNode(SimNode):
    NODE_NAME = "Resource"
    kind = "Resource"
    color = (120, 100, 60)

    def __init__(self):
        super().__init__()
        self.add_output("resource", color=PORT_COLORS["resource"])
        add_float_input(self, "desired_capacity", "capacity", 1.0)
        add_bool_input(self, "anonymous", "anonymous", False)

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "capacity": read_capacity(self, 1.0),
            "anonymous": bool(self.get_property("anonymous")),
            "position": [self.x_pos(), self.y_pos()],
        }


class RestockableResourceNode(SimNode):
    NODE_NAME = "Restockable Resource"
    kind = "RestockableResource"
    color = (140, 105, 55)

    def __init__(self):
        super().__init__()
        self.add_input("order_duration", color=PORT_COLORS["duration"])
        self.add_input("delivery_duration", color=PORT_COLORS["duration"])
        self.add_output("resource", color=PORT_COLORS["resource"])
        add_float_input(self, "desired_capacity", "capacity", 100.0)
        add_float_input(self, "threshold", "threshold", 20.0)

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "capacity": read_capacity(self, 100.0),
            "threshold": as_float(self.get_property("threshold"), 20.0),
            "order_duration": get_input_ref(self, "order_duration"),
            "delivery_duration": get_input_ref(self, "delivery_duration"),
            "position": [self.x_pos(), self.y_pos()],
        }


class HardBufferNode(SimNode):
    NODE_NAME = "Hard Buffer"
    kind = "HardBuffer"
    color = (60, 125, 90)

    def __init__(self):
        super().__init__()
        self.add_input("from_task", multi_input=True, color=PORT_COLORS["task"])
        self.add_output("to_task", multi_output=True, color=PORT_COLORS["buffer"])
        self.add_output("monitor", multi_output=True, color=PORT_COLORS["monitor"])
        self.create_property("valid_models", "[]")
        self.create_property("capacity", "inf")
        add_combo_input(self, "buffer_role", "role", BUFFER_ROLES, "Normal")

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "valid_models": get_property_json(self, "valid_models", []),
            "capacity": self.get_property("capacity"),
            "buffer_role": self.get_property("buffer_role") if self.has_property("buffer_role") else "Normal",
            "inputs_from": connected_refs_from_port(self, "from_task", "input"),
            "outputs_to": connected_refs_from_port(self, "to_task", "output"),
            "position": [self.x_pos(), self.y_pos()],
        }


class SoftBufferNode(SimNode):
    NODE_NAME = "Soft Buffer"
    kind = "SoftBuffer"
    color = (60, 115, 125)

    def __init__(self):
        super().__init__()
        self.add_input("from_task", multi_input=True, color=PORT_COLORS["task"])
        self.add_output("to_buffers", multi_output=True, color=PORT_COLORS["buffer"])
        self.create_property("buffer_probs", "{}")
        add_combo_input(self, "buffer_role", "role", BUFFER_ROLES, "Normal")

    def to_clean_json(self) -> dict:
        connected_buffers = connected_refs_from_port(self, "to_buffers", "output")
        prob_map = get_property_json(self, "buffer_probs", {})

        buffer_probs = []
        for buffer_id in connected_buffers:
            prob = as_float(prob_map.get(buffer_id, 0.0), 0.0)
            buffer_probs.append({
                "buffer": buffer_id,
                "probability": prob,
            })

        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "inputs_from": connected_refs_from_port(self, "from_task", "input"),
            "buffer_role": self.get_property("buffer_role") if self.has_property("buffer_role") else "Normal",
            "buffer_probs": buffer_probs,
            "position": [self.x_pos(), self.y_pos()],
        }


class FirstTaskNode(SimNode):
    NODE_NAME = "First Task"
    kind = "FirstTask"
    color = (145, 80, 80)

    def __init__(self):
        super().__init__()
        self.add_input("task_duration", color=PORT_COLORS["duration"])
        self.add_input("resources", multi_input=True, color=PORT_COLORS["resource"])
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.create_property("models_probs", "[]")
        self.create_property("resource_quantities", "{}")

    def to_clean_json(self) -> dict:
        resources = connected_refs_from_port(self, "resources", "input")
        q_map = get_property_json(self, "resource_quantities", {})
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "models_probs": get_property_json(self, "models_probs", []),
            "resources": [
                {"resource": rid, "quantity": as_float(q_map.get(rid, 1.0), 1.0)}
                for rid in resources
            ],
            "task_duration": get_input_ref(self, "task_duration"),
            "bufs_out": get_output_refs(self, "bufs_out"),
            "position": [self.x_pos(), self.y_pos()],
        }


class TaskNode(SimNode):
    NODE_NAME = "Task"
    kind = "Task"
    color = (150, 90, 60)

    def __init__(self):
        super().__init__()
        self.add_input("bufs_in", multi_input=True, color=PORT_COLORS["buffer"])
        self.add_input("resources", multi_input=True, color=PORT_COLORS["resource"])
        self.add_input("operators", multi_input=True, color=PORT_COLORS["resource"])
        self.add_input("startup_operators", multi_input=True, color=PORT_COLORS["resource"])
        self.add_input("task_duration", color=PORT_COLORS["duration"])
        self.add_input("startup_duration", color=PORT_COLORS["duration"])
        self.add_input("scheduled_shutdowns", color=PORT_COLORS["shutdown"])

        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.add_output("task_ref", multi_output=True, color=PORT_COLORS["breakdown"])

        self.create_property("capability", "[]")
        self.create_property("resources_scope", "PER_PIECE")
        self.create_property("operators_scope", "PER_BATCH")
        self.create_property("resource_quantities", "{}")
        self.create_property("operator_quantities", "{}")
        self.create_property("startup_operator_quantities", "{}")
        self.create_property("min_capacity", 1)
        self.create_property("max_capacity", 1)
        self.create_property("batch_collector", "GreedyBatchCollector")
        self.create_property("independent_carriers", False)

    def to_clean_json(self) -> dict:
        resources = connected_refs_from_port(self, "resources", "input")
        operators = connected_refs_from_port(self, "operators", "input")
        startup_ops = connected_refs_from_port(self, "startup_operators", "input")

        rq = get_property_json(self, "resource_quantities", {})
        oq = get_property_json(self, "operator_quantities", {})
        soq = get_property_json(self, "startup_operator_quantities", {})

        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "capability": get_property_json(self, "capability", []),
            "bufs_in": connected_refs_from_port(self, "bufs_in", "input"),
            "bufs_out": get_output_refs(self, "bufs_out"),
            "resources": [
                {"resource": rid, "quantity": as_float(rq.get(rid, 1.0), 1.0)}
                for rid in resources
            ],
            "resources_scope": self.get_property("resources_scope"),
            "operators": [
                {"resource": rid, "quantity": as_int(oq.get(rid, 1), 1)}
                for rid in operators
            ],
            "operators_scope": self.get_property("operators_scope"),
            "startup_operators": [
                {"resource": rid, "quantity": as_int(soq.get(rid, 1), 1)}
                for rid in startup_ops
            ],
            "task_duration": get_input_ref(self, "task_duration"),
            "startup_duration": get_input_ref(self, "startup_duration"),
            "min_capacity": as_int(self.get_property("min_capacity"), 1),
            "max_capacity": as_int(self.get_property("max_capacity"), 1),
            "batch_collector": self.get_property("batch_collector"),
            "independent_carriers": bool(self.get_property("independent_carriers")),
            "scheduled_shutdowns": get_input_ref(self, "scheduled_shutdowns"),
            "position": [self.x_pos(), self.y_pos()],
        }


class BreakdownNode(SimNode):
    NODE_NAME = "Breakdown"
    kind = "Breakdown"
    color = (150, 65, 85)

    def __init__(self):
        super().__init__()
        self.add_input("task", color=PORT_COLORS["breakdown"])
        self.add_input("mtbf", color=PORT_COLORS["duration"])
        self.add_input("mttr", color=PORT_COLORS["duration"])
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "task": get_input_ref(self, "task"),
            "mtbf": get_input_ref(self, "mtbf"),
            "mttr": get_input_ref(self, "mttr"),
            "bufs_out": get_output_refs(self, "bufs_out"),
            "position": [self.x_pos(), self.y_pos()],
        }


class MonitorNode(SimNode):
    NODE_NAME = "Monitor"
    kind = "Monitor"
    color = (55, 110, 125)

    def __init__(self):
        super().__init__()
        self.add_input("buffer", color=PORT_COLORS["monitor"])
        # One checkbox per statistic; toggled state is exported in "stats".
        for key, label, default in MONITOR_STATS:
            add_bool_input(self, key, label, default)

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "buffer": get_input_ref(self, "buffer"),
            "stats": {
                key: bool(self.get_property(key)) if self.has_property(key) else default
                for key, _, default in MONITOR_STATS
            },
            "position": [self.x_pos(), self.y_pos()],
        }


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


class MultiModelPickerDialog(QtWidgets.QDialog):
    def __init__(self, parent, all_models: List[dict], selected: List[str]):
        super().__init__(parent)
        self.setWindowTitle("Pick models")
        self.resize(380, 420)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)

        selected_set = set(selected)
        for m in all_models:
            item = QtWidgets.QListWidgetItem(m["name"])
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if m["name"] in selected_set else QtCore.Qt.Unchecked)
            self.list_widget.addItem(item)

        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        layout.addLayout(buttons)

        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def selected_models(self):
        result = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == QtCore.Qt.Checked:
                result.append(item.text())
        return result


class WeightedModelsDialog(QtWidgets.QDialog):
    def __init__(self, parent, all_models: List[dict], current: List[dict]):
        super().__init__(parent)
        self.setWindowTitle("FirstTask model probabilities")
        self.resize(520, 380)

        self.all_model_names = [m["name"] for m in all_models]

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["model", "probability"])
        self.table.horizontalHeader().setStretchLastSection(True)

        btn_add = QtWidgets.QPushButton("Add model")
        btn_remove = QtWidgets.QPushButton("Remove selected")
        btn_normalize = QtWidgets.QPushButton("Normalize")
        btn_ok = QtWidgets.QPushButton("OK")
        btn_cancel = QtWidgets.QPushButton("Cancel")

        tools = QtWidgets.QHBoxLayout()
        tools.addWidget(btn_add)
        tools.addWidget(btn_remove)
        tools.addWidget(btn_normalize)
        tools.addStretch()

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(btn_ok)
        bottom.addWidget(btn_cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Pick models and probabilities. Sum must be 1."))
        layout.addWidget(self.table)
        layout.addLayout(tools)
        layout.addLayout(bottom)

        btn_add.clicked.connect(lambda: self.add_row("", 0.0))
        btn_remove.clicked.connect(self.remove_selected)
        btn_normalize.clicked.connect(self.normalize)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        for item in current:
            self.add_row(item.get("model", ""), item.get("probability", 0.0))

    def add_row(self, model="", probability=0.0):
        row = self.table.rowCount()
        self.table.insertRow(row)

        combo = QtWidgets.QComboBox()
        combo.addItems(self.all_model_names)
        if model in self.all_model_names:
            combo.setCurrentText(model)
        self.table.setCellWidget(row, 0, combo)
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(probability)))

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def values(self) -> List[dict]:
        result = []
        total = 0.0
        seen = set()

        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 0)
            item = self.table.item(r, 1)
            model = combo.currentText().strip() if combo else ""
            prob = as_float(item.text() if item else 0.0, 0.0)

            if not model:
                continue
            if model in seen:
                raise ValueError(f"Duplicate model in FirstTask probabilities: {model}")
            if prob < 0 or prob > 1:
                raise ValueError("Probabilities must be in [0, 1].")

            seen.add(model)
            total += prob
            result.append({"model": model, "probability": prob})

        if not result:
            raise ValueError("At least one model is required.")
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"Probabilities must sum to 1. Current sum is {total}.")

        return result

    def normalize(self):
        probs = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 1)
            probs.append(as_float(item.text() if item else 0.0, 0.0))
        total = sum(probs)
        if total <= 0:
            return
        for r, p in enumerate(probs):
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(p / total)))

    def accept(self):
        try:
            self.values()
        except Exception as e:
            qmessage(self, "Invalid probabilities", str(e), QtWidgets.QMessageBox.Warning)
            return
        super().accept()


class DistributionDialog(QtWidgets.QDialog):
    def __init__(self, parent, dist_type: str, params: dict):
        super().__init__(parent)
        self.setWindowTitle("Distribution")
        self.resize(380, 260)

        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(list(DISTRIBUTION_SPECS.keys()))
        if dist_type in DISTRIBUTION_SPECS:
            self.type_combo.setCurrentText(dist_type)

        self.form = QtWidgets.QFormLayout()
        self.param_widgets = {}

        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Choose a distribution type and fill required arguments."))
        layout.addWidget(self.type_combo)
        layout.addLayout(self.form)
        layout.addLayout(buttons)

        self.type_combo.currentTextChanged.connect(lambda _: self.rebuild_form(params))
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        self.rebuild_form(params)

    def rebuild_form(self, params=None):
        params = params or {}
        while self.form.rowCount():
            self.form.removeRow(0)
        self.param_widgets = {}

        dist_type = self.type_combo.currentText()
        for name, _typ, default in DISTRIBUTION_SPECS[dist_type]:
            edit = QtWidgets.QLineEdit(str(params.get(name, default)))
            self.param_widgets[name] = edit
            self.form.addRow(name, edit)

    def value(self):
        dist_type = self.type_combo.currentText()
        params = {}
        for name, typ, default in DISTRIBUTION_SPECS[dist_type]:
            text = self.param_widgets[name].text()
            params[name] = typ(text)
        return dist_type, params


class QuantityDialog(QtWidgets.QDialog):
    def __init__(self, parent, title: str, connected_nodes: List[BaseNode], current_map: dict, integer=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(540, 340)
        self.connected_nodes = connected_nodes
        self.integer = integer

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["node name", "node id", "quantity"])
        self.table.horizontalHeader().setStretchLastSection(True)

        for n in connected_nodes:
            uid = node_uid(n)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(n.name()))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(uid))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(current_map.get(uid, 1))))

        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Quantities are keyed by connected node id."))
        layout.addWidget(self.table)
        layout.addLayout(buttons)

        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def values(self) -> dict:
        result = {}
        for r in range(self.table.rowCount()):
            uid = self.table.item(r, 1).text()
            q = self.table.item(r, 2).text()
            result[uid] = as_int(q, 1) if self.integer else as_float(q, 1.0)
        return result


class SoftBufferProbabilityDialog(QtWidgets.QDialog):
    def __init__(self, parent, connected_buffers: List[BaseNode], current_map: dict):
        super().__init__(parent)
        self.setWindowTitle("SoftBuffer probabilities")
        self.resize(540, 340)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["target name", "target id", "probability"])
        self.table.horizontalHeader().setStretchLastSection(True)

        for n in connected_buffers:
            uid = node_uid(n)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(n.name()))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(uid))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(current_map.get(uid, 0.0))))

        btn_normalize = QtWidgets.QPushButton("Normalize")
        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")

        tools = QtWidgets.QHBoxLayout()
        tools.addWidget(btn_normalize)
        tools.addStretch()
        tools.addWidget(ok)
        tools.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Probabilities are stored by connected target node id (Hard or Soft buffer), so order does not matter."))
        layout.addWidget(self.table)
        layout.addLayout(tools)

        btn_normalize.clicked.connect(self.normalize)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def values(self) -> dict:
        result = {}
        total = 0.0
        for r in range(self.table.rowCount()):
            uid = self.table.item(r, 1).text()
            prob = as_float(self.table.item(r, 2).text(), 0.0)
            if prob < 0 or prob > 1:
                raise ValueError("Probabilities must be in [0, 1].")
            total += prob
            result[uid] = prob

        if self.table.rowCount() > 0 and abs(total - 1.0) > 1e-9:
            raise ValueError(f"Probabilities must sum to 1. Current sum is {total}.")

        return result

    def normalize(self):
        probs = []
        for r in range(self.table.rowCount()):
            probs.append(as_float(self.table.item(r, 2).text(), 0.0))
        total = sum(probs)
        if total <= 0:
            return
        for r, p in enumerate(probs):
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(str(p / total)))

    def accept(self):
        try:
            self.values()
        except Exception as e:
            qmessage(self, "Invalid probabilities", str(e), QtWidgets.QMessageBox.Warning)
            return
        super().accept()


class ResourceConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent, node: BaseNode):
        super().__init__(parent)
        self.node = node
        kind = node_kind(node)
        self.setWindowTitle(f"{kind} config")
        self.resize(340, 180)

        self.capacity_edit = QtWidgets.QLineEdit(str(read_capacity(node, 1.0)))
        form = QtWidgets.QFormLayout()
        form.addRow("capacity", self.capacity_edit)

        self.threshold_edit = None
        self.anonymous_check = None

        if kind == "RestockableResource":
            thr = node.get_property("threshold") if node.has_property("threshold") else 20.0
            self.threshold_edit = QtWidgets.QLineEdit(str(thr))
            form.addRow("threshold", self.threshold_edit)
        else:
            self.anonymous_check = QtWidgets.QCheckBox("anonymous")
            state = bool(node.get_property("anonymous")) if node.has_property("anonymous") else False
            self.anonymous_check.setChecked(state)
            form.addRow("", self.anonymous_check)

        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def accept(self):
        if as_float(self.capacity_edit.text(), None) is None:
            qmessage(self, "Invalid capacity", "Capacity must be a number.", QtWidgets.QMessageBox.Warning)
            return
        set_text_prop(self.node, "desired_capacity", self.capacity_edit.text().strip())

        if self.threshold_edit is not None:
            if as_float(self.threshold_edit.text(), None) is None:
                qmessage(self, "Invalid threshold", "Threshold must be a number.", QtWidgets.QMessageBox.Warning)
                return
            set_text_prop(self.node, "threshold", self.threshold_edit.text().strip())

        if self.anonymous_check is not None:
            set_bool_prop(self.node, "anonymous", self.anonymous_check.isChecked())

        super().accept()


class TaskConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent, task_node: TaskNode, model_registry: List[dict]):
        super().__init__(parent)
        self.setWindowTitle("Task config")
        self.resize(520, 460)
        self.task_node = task_node
        self.model_registry = model_registry

        self.capability_btn = QtWidgets.QPushButton("Edit capability models...")
        self.capability_label = QtWidgets.QLabel(", ".join(get_property_json(task_node, "capability", [])) or "<none>")

        self.resources_scope = QtWidgets.QComboBox()
        self.resources_scope.addItems(SCOPES_FOR_RESOURCES)
        self.resources_scope.setCurrentText(task_node.get_property("resources_scope"))

        self.operators_scope = QtWidgets.QComboBox()
        self.operators_scope.addItems(SCOPES_FOR_OPERATORS)
        self.operators_scope.setCurrentText(task_node.get_property("operators_scope"))

        self.min_capacity = QtWidgets.QSpinBox()
        self.min_capacity.setMinimum(1)
        self.min_capacity.setMaximum(999999)
        self.min_capacity.setValue(as_int(task_node.get_property("min_capacity"), 1))

        self.max_capacity = QtWidgets.QSpinBox()
        self.max_capacity.setMinimum(1)
        self.max_capacity.setMaximum(999999)
        self.max_capacity.setValue(as_int(task_node.get_property("max_capacity"), 1))

        self.batch_collector = QtWidgets.QComboBox()
        self.batch_collector.addItems(BATCH_COLLECTORS)
        self.batch_collector.setCurrentText(task_node.get_property("batch_collector"))

        self.independent_carriers = QtWidgets.QCheckBox("independent_carriers")
        self.independent_carriers.setChecked(bool(task_node.get_property("independent_carriers")))

        btn_resources = QtWidgets.QPushButton("Edit resource quantities...")
        btn_operators = QtWidgets.QPushButton("Edit operator quantities...")
        btn_startup_ops = QtWidgets.QPushButton("Edit startup operator quantities...")

        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")

        form = QtWidgets.QFormLayout()
        form.addRow("capability", self.capability_label)
        form.addRow("", self.capability_btn)
        form.addRow("resources_scope", self.resources_scope)
        form.addRow("operators_scope", self.operators_scope)
        form.addRow("min_capacity", self.min_capacity)
        form.addRow("max_capacity", self.max_capacity)
        form.addRow("batch_collector", self.batch_collector)
        form.addRow("", self.independent_carriers)
        form.addRow("resources", btn_resources)
        form.addRow("operators", btn_operators)
        form.addRow("startup operators", btn_startup_ops)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

        self.capability_btn.clicked.connect(self.edit_capability)
        btn_resources.clicked.connect(self.edit_resource_quantities)
        btn_operators.clicked.connect(self.edit_operator_quantities)
        btn_startup_ops.clicked.connect(self.edit_startup_operator_quantities)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def edit_capability(self):
        current = get_property_json(self.task_node, "capability", [])
        dlg = MultiModelPickerDialog(self, self.model_registry, current)
        if dlg.exec():
            values = dlg.selected_models()
            set_property_json(self.task_node, "capability", values)
            self.capability_label.setText(", ".join(values) or "<none>")

    def edit_resource_quantities(self):
        connected = connected_nodes_from_port(self.task_node, "resources", "input")
        current = get_property_json(self.task_node, "resource_quantities", {})
        dlg = QuantityDialog(self, "Resource quantities", connected, current, integer=False)
        if dlg.exec():
            set_property_json(self.task_node, "resource_quantities", dlg.values())

    def edit_operator_quantities(self):
        connected = connected_nodes_from_port(self.task_node, "operators", "input")
        current = get_property_json(self.task_node, "operator_quantities", {})
        dlg = QuantityDialog(self, "Operator quantities", connected, current, integer=True)
        if dlg.exec():
            set_property_json(self.task_node, "operator_quantities", dlg.values())

    def edit_startup_operator_quantities(self):
        connected = connected_nodes_from_port(self.task_node, "startup_operators", "input")
        current = get_property_json(self.task_node, "startup_operator_quantities", {})
        dlg = QuantityDialog(self, "Startup operator quantities", connected, current, integer=True)
        if dlg.exec():
            set_property_json(self.task_node, "startup_operator_quantities", dlg.values())

    def accept(self):
        if self.min_capacity.value() > self.max_capacity.value():
            qmessage(self, "Invalid capacity", "min_capacity cannot be greater than max_capacity.", QtWidgets.QMessageBox.Warning)
            return

        self.task_node.set_property("resources_scope", self.resources_scope.currentText())
        self.task_node.set_property("operators_scope", self.operators_scope.currentText())
        self.task_node.set_property("min_capacity", self.min_capacity.value())
        self.task_node.set_property("max_capacity", self.max_capacity.value())
        self.task_node.set_property("batch_collector", self.batch_collector.currentText())
        self.task_node.set_property("independent_carriers", self.independent_carriers.isChecked())

        super().accept()


# ============================================================
# Validation
# ============================================================

def port_signature(port) -> Tuple[str, str, str]:
    """
    Returns (node_kind, direction, port_name).
    Direction comes from NodeGraphQt port type.
    """
    n = port.node()
    ptype = str(port.type_()).lower()
    direction = "input" if "in" in ptype else "output"
    return node_kind(n), direction, port.name()


def is_valid_connection(out_kind: str, out_port: str, in_kind: str, in_port: str) -> bool:
    """
    Strict connection rules.

    This is the main place to control what can feed what.
    """
    # Distribution feeds duration slots.
    if out_kind == "Distribution":
        return (
            (in_kind == "Task" and in_port in {"task_duration", "startup_duration"})
            or (in_kind == "FirstTask" and in_port == "task_duration")
            or (in_kind == "Breakdown" and in_port in {"mtbf", "mttr"})
            or (in_kind == "RestockableResource" and in_port in {"order_duration", "delivery_duration"})
        )

    # Interval feeds scheduled shutdowns.
    if out_kind == "Interval":
        return in_kind == "ScheduledShutdowns" and in_port == "intervals"

    # Scheduled shutdowns feed tasks.
    if out_kind == "ScheduledShutdowns":
        return in_kind == "Task" and in_port == "scheduled_shutdowns"

    # Resources feed task/resource slots.
    if out_kind in {"Resource", "RestockableResource"}:
        return (
            (in_kind == "Task" and in_port in {"resources", "operators", "startup_operators"})
            or (in_kind == "FirstTask" and in_port == "resources")
        )

    # HardBuffer feeds task inputs.
    if out_kind == "HardBuffer" and out_port == "to_task":
        return in_kind == "Task" and in_port == "bufs_in"

    # HardBuffer taps a Monitor card (observation only, does not move pieces).
    if out_kind == "HardBuffer" and out_port == "monitor":
        return in_kind == "Monitor" and in_port == "buffer"

    # Tasks and FirstTasks feed buffers (hard or soft).
    if out_kind in {"Task", "FirstTask", "Breakdown"} and out_port == "bufs_out":
        return in_kind in {"HardBuffer", "SoftBuffer"} and in_port == "from_task"

    # SoftBuffer routes to hard or soft buffers with probabilities.
    # Soft -> Soft is now allowed (nested probabilistic routing).
    if out_kind == "SoftBuffer" and out_port == "to_buffers":
        return in_kind in {"HardBuffer", "SoftBuffer"} and in_port == "from_task"

    # Technical task reference for breakdowns.
    if out_kind == "Task" and out_port == "task_ref":
        return in_kind == "Breakdown" and in_port == "task"

    return False


# ============================================================
# Main window
# ============================================================

class FlowEditorWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 850)

        self.graph = NodeGraph()

        # Allow rework/repair loops:
        # Breakdown -> Buffer -> Task -> Breakdown
        try:
            self.graph.set_acyclic(False)
        except Exception:
            pass

        try:
            from NodeGraphQt.constants import PipeLayoutEnum
            self.graph.set_pipe_style(PipeLayoutEnum.CURVED.value)
        except Exception:
            pass

        self.model_registry = []

        self.graph.register_nodes([
            DistributionNode,
            IntervalNode,
            ScheduledShutdownsNode,
            ResourceNode,
            RestockableResourceNode,
            HardBufferNode,
            SoftBufferNode,
            FirstTaskNode,
            TaskNode,
            BreakdownNode,
            MonitorNode,
        ])

        self.setCentralWidget(self.graph.widget)

        self.properties_bin = PropertiesBinWidget(node_graph=self.graph)
        self.properties_dock = QtWidgets.QDockWidget("Properties", self)
        self.properties_dock.setWidget(self.properties_bin)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.properties_dock)

        self._build_menus()
        self._connect_signals()

        self.statusBar().showMessage("Ready. Press Tab in the graph to create nodes.")

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")

        act_new = file_menu.addAction("New")
        act_import = file_menu.addAction("Import clean JSON...")
        act_export = file_menu.addAction("Export clean JSON...")

        act_new.triggered.connect(self.new_graph)
        act_import.triggered.connect(self.import_clean_json_dialog)
        act_export.triggered.connect(self.export_clean_json_dialog)

        model_menu = self.menuBar().addMenu("Models")
        act_models = model_menu.addAction("Edit models...")
        act_models.triggered.connect(self.edit_models)

        edit_menu = self.menuBar().addMenu("Edit")
        delete_action = edit_menu.addAction("Delete selected")
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected_nodes)

        tools_menu = self.menuBar().addMenu("Tools")
        act_validate = tools_menu.addAction("Validate graph")
        act_validate.triggered.connect(self.validate_graph_dialog)

        act_autolayout = tools_menu.addAction("Auto-layout selected")
        act_autolayout.triggered.connect(self.auto_layout_selected)

        act_frame = tools_menu.addAction("Frame all")
        act_frame.triggered.connect(self.frame_all)

        templates_menu = self.menuBar().addMenu("Templates")

        add_task_action = templates_menu.addAction("Add Task Template")
        add_first_task_action = templates_menu.addAction("Add FirstTask Template")
        add_backdrop_action = templates_menu.addAction("Add Backdrop Around Selection")

        add_task_action.triggered.connect(self.add_task_template)
        add_first_task_action.triggered.connect(self.add_first_task_template)
        add_backdrop_action.triggered.connect(self.add_backdrop_around_selection)

        create_menu = self.menuBar().addMenu("Create")
        for label, cls_name in [
            ("Distribution", "simulation.flow.DistributionNode"),
            ("Interval", "simulation.flow.IntervalNode"),
            ("Scheduled Shutdowns", "simulation.flow.ScheduledShutdownsNode"),
            ("Resource", "simulation.flow.ResourceNode"),
            ("Restockable Resource", "simulation.flow.RestockableResourceNode"),
            ("Hard Buffer", "simulation.flow.HardBufferNode"),
            ("Soft Buffer", "simulation.flow.SoftBufferNode"),
            ("First Task", "simulation.flow.FirstTaskNode"),
            ("Task", "simulation.flow.TaskNode"),
            ("Breakdown", "simulation.flow.BreakdownNode"),
            ("Monitor", "simulation.flow.MonitorNode"),
        ]:
            action = create_menu.addAction(label)
            action.triggered.connect(lambda checked=False, t=cls_name: self.create_node(t))

    def auto_layout_selected(self):
        try:
            nodes = self.graph.selected_nodes()
        except Exception:
            nodes = []
        if not nodes:
            nodes = self.all_nodes()
        if not nodes:
            return
        try:
            self.graph.auto_layout_nodes(nodes=nodes)
            self.focus_on_nodes(nodes)
        except Exception as e:
            qmessage(self, "Auto-layout", f"Not available in this NodeGraphQt version.\n{e}",
                     QtWidgets.QMessageBox.Warning)

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
            # Some NodeGraphQt versions may differ; export validation still works.
            pass

    def all_nodes(self) -> List[BaseNode]:
        try:
            return list(self.graph.all_nodes())
        except Exception:
            return list(self.graph.nodes())

    def current_view_center(self):
        """
        Returns the center of the currently visible graph view.
        New cards and templates will appear around this point.
        """
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
        return (
            min(r[0] for r in rects),
            min(r[1] for r in rects),
            max(r[2] for r in rects),
            max(r[3] for r in rects),
        )

    def shift_nodes(self, nodes, dx, dy):
        if not dx and not dy:
            return
        for n in nodes:
            try:
                x, y = n.x_pos(), n.y_pos()
                self.set_node_position_safe(n, x + dx, y + dy)
            except Exception:
                pass

    def place_clear_of_existing(self, new_nodes, existing_bounds, padding=160.0, direction="right"):
        """Move new_nodes so they sit clear of existing_bounds (no overlap)."""
        if existing_bounds is None:
            return
        nb = self.content_bounds(new_nodes)
        if nb is None:
            return
        ex_left, ex_top, ex_right, ex_bottom = existing_bounds
        nb_left, nb_top, nb_right, nb_bottom = nb
        if direction == "below":
            dx = ex_left - nb_left
            dy = (ex_bottom + padding) - nb_top
        else:  # "right"
            dx = (ex_right + padding) - nb_left
            dy = ex_top - nb_top
        self.shift_nodes(new_nodes, dx, dy)

    def focus_on_nodes(self, nodes):
        nodes = [n for n in nodes if n is not None]
        if not nodes:
            return
        try:
            self.graph.clear_selection()
        except Exception:
            pass
        for n in nodes:
            try:
                n.set_selected(True)
            except Exception:
                pass
        for attempt in (lambda: self.graph.center_on(nodes),
                        lambda: self.graph.fit_to_selection()):
            try:
                attempt()
                return
            except Exception:
                continue

    def create_node(self, node_type: str):
        node = self.graph.create_node(node_type)

        x, y = self.current_view_center()
        self.set_node_position_safe(node, x, y)

        return node

    def new_graph(self):
        self.graph.clear_session()
        self.model_registry = []



    def edit_models(self):
        dlg = ModelRegistryDialog(self, self.model_registry)
        if dlg.exec():
            try:
                self.model_registry = dlg.models()
                self.statusBar().showMessage(f"{len(self.model_registry)} models defined.")
            except Exception as e:
                qmessage(self, "Invalid models", str(e), QtWidgets.QMessageBox.Warning)

    def on_node_double_clicked(self, node):
        kind = node_kind(node)

        if kind == "Distribution":
            params = node.get_property("params")
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:
                    params = {}
            dlg = DistributionDialog(self, node.get_property("dist_type"), params or {})
            if dlg.exec():
                dist_type, params = dlg.value()
                node.set_property("dist_type", dist_type)
                node.set_property("params", params)
                node.set_name(f"{dist_type} distribution")

        elif kind == "HardBuffer":
            current = get_property_json(node, "valid_models", [])
            dlg = MultiModelPickerDialog(self, self.model_registry, current)
            if dlg.exec():
                set_property_json(node, "valid_models", dlg.selected_models())

        elif kind == "FirstTask":
            current = get_property_json(node, "models_probs", [])
            dlg = WeightedModelsDialog(self, self.model_registry, current)
            if dlg.exec():
                set_property_json(node, "models_probs", dlg.values())

            connected_resources = connected_nodes_from_port(node, "resources", "input")
            if connected_resources:
                current_q = get_property_json(node, "resource_quantities", {})
                qdlg = QuantityDialog(self, "FirstTask resource quantities", connected_resources, current_q, integer=False)
                if qdlg.exec():
                    set_property_json(node, "resource_quantities", qdlg.values())

        elif kind == "SoftBuffer":
            connected_buffers = connected_nodes_from_port(node, "to_buffers", "output")
            current = get_property_json(node, "buffer_probs", {})
            dlg = SoftBufferProbabilityDialog(self, connected_buffers, current)
            if dlg.exec():
                set_property_json(node, "buffer_probs", dlg.values())

        elif kind == "Task":
            dlg = TaskConfigDialog(self, node, self.model_registry)
            dlg.exec()

        elif kind in {"Resource", "RestockableResource"}:
            dlg = ResourceConfigDialog(self, node)
            dlg.exec()

        elif kind == "Interval":
            # Edited inline on the card (start / end).
            pass

        elif kind == "Breakdown":
            qmessage(
                self,
                "Breakdown card",
                "Connect:\n"
                "- Task.task_ref -> Breakdown.task\n"
                "- Distribution -> Breakdown.mtbf\n"
                "- Distribution -> Breakdown.mttr\n"
                "- Breakdown.bufs_out -> HardBuffer or SoftBuffer"
            )

    def on_port_connected(self, *args):
        """
        Tries to reject invalid connections immediately.
        NodeGraphQt signal signatures vary a bit between versions, so this function is defensive.
        """
        ports = [a for a in args if hasattr(a, "node") and hasattr(a, "name")]
        if len(ports) < 2:
            return

        p1, p2 = ports[0], ports[1]

        k1, d1, name1 = port_signature(p1)
        k2, d2, name2 = port_signature(p2)

        if d1 == "output" and d2 == "input":
            out_p, in_p = p1, p2
            out_kind, out_name = k1, name1
            in_kind, in_name = k2, name2
        elif d2 == "output" and d1 == "input":
            out_p, in_p = p2, p1
            out_kind, out_name = k2, name2
            in_kind, in_name = k1, name1
        else:
            return

        if not is_valid_connection(out_kind, out_name, in_kind, in_name):
            try:
                out_p.disconnect_from(in_p)
            except Exception:
                pass
            qmessage(
                self,
                "Invalid connection",
                f"Cannot connect {out_kind}.{out_name} to {in_kind}.{in_name}.",
                QtWidgets.QMessageBox.Warning,
            )

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
        """
        Compact readable clean JSON.

        This is the only persistence format in this editor. It keeps:
        - models
        - simulation cards
        - typed connections
        - backdrop groups
        """
        nodes = []
        backdrops = []

        for node in self.all_nodes():
            kind = node_kind(node)
            is_backdrop = (
                node.__class__.__name__ == "BackdropNode"
                or kind in {"Backdrop", "BackdropNode"}
            )

            if is_backdrop:
                wrapped_node_ids = get_property_json(node, "wrapped_node_ids", [])
                if not wrapped_node_ids:
                    continue

                title = node.get_property("backdrop_title") if node.has_property("backdrop_title") else node.name()

                # Persist the backdrop size so import restores it exactly instead of
                # re-fitting to the wrapped nodes (which changed the size on reload).
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
            "editor": {
                "name": APP_NAME,
                "version": EDITOR_VERSION,
                "format": "clean-json",
            },
            "models": self.model_registry,
            "nodes": nodes,
            "connections": self.connections_clean(),
            "backdrops": backdrops,
        }

    def export_clean_json_dialog(self):
        problems = self.validate_graph()
        if problems:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Validation warnings",
                "The graph has validation warnings. Export anyway?\n\n" + "\n".join(problems[:12]),
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export clean JSON", "clean_export.json", "JSON (*.json)")
        if not path:
            return

        data = self.export_clean_json()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.statusBar().showMessage(f"Exported clean JSON: {path}")

    def validate_graph_dialog(self):
        problems = self.validate_graph()
        if not problems:
            qmessage(self, "Validation", "No validation problems found.")
        else:
            qmessage(self, "Validation problems", "\n".join(problems[:50]), QtWidgets.QMessageBox.Warning)

    def validate_graph(self) -> List[str]:
        problems = []

        for c in self.connections_clean():
            if not is_valid_connection(c["from_kind"], c["from_port"], c["to_kind"], c["to_port"]):
                problems.append(
                    f"Invalid connection: {c['from_kind']}.{c['from_port']} -> {c['to_kind']}.{c['to_port']}"
                )

        for node in self.all_nodes():
            kind = node_kind(node)

            if kind == "Task":
                if not connected_refs_from_port(node, "bufs_in", "input"):
                    problems.append(f"Task '{node.name()}' has no input buffers.")
                if not get_input_ref(node, "task_duration"):
                    problems.append(f"Task '{node.name()}' has no task_duration distribution.")
                if not get_input_ref(node, "startup_duration"):
                    problems.append(f"Task '{node.name()}' has no startup_duration distribution.")
                if not get_property_json(node, "capability", []):
                    problems.append(f"Task '{node.name()}' has no capability models.")

            elif kind == "FirstTask":
                models_probs = get_property_json(node, "models_probs", [])
                total = sum(as_float(x.get("probability", 0.0), 0.0) for x in models_probs)
                if not models_probs:
                    problems.append(f"FirstTask '{node.name()}' has no model probabilities.")
                elif abs(total - 1.0) > 1e-9:
                    problems.append(f"FirstTask '{node.name()}' probabilities sum to {total}, not 1.")
                if not get_input_ref(node, "task_duration"):
                    problems.append(f"FirstTask '{node.name()}' has no task_duration distribution.")

            elif kind == "HardBuffer":
                if not get_property_json(node, "valid_models", []):
                    problems.append(f"HardBuffer '{node.name()}' has no valid_models.")

            elif kind == "SoftBuffer":
                connected_buffers = connected_refs_from_port(node, "to_buffers", "output")
                prob_map = get_property_json(node, "buffer_probs", {})
                total = sum(as_float(prob_map.get(bid, 0.0), 0.0) for bid in connected_buffers)
                if not connected_buffers:
                    problems.append(f"SoftBuffer '{node.name()}' has no output buffers.")
                elif abs(total - 1.0) > 1e-9:
                    problems.append(f"SoftBuffer '{node.name()}' probabilities over connected buffers sum to {total}, not 1.")

            elif kind == "Distribution":
                params = node.get_property("params")
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except Exception:
                        problems.append(f"Distribution '{node.name()}' has invalid params JSON.")

            elif kind == "ScheduledShutdowns":
                # Zero intervals is allowed but maybe suspicious.
                pass

            elif kind == "RestockableResource":
                if not get_input_ref(node, "delivery_duration"):
                    problems.append(f"RestockableResource '{node.name()}' has no delivery_duration distribution.")
                if not get_input_ref(node, "order_duration"):
                    problems.append(f"RestockableResource '{node.name()}' has no order_duration distribution.")

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

    def make_node(self, node_type: str, name: str, x: float, y: float):
        """
        Creates a node around the current visible view center.
        x and y are offsets from the current view center.
        """
        base_x, base_y = self.current_view_center()

        node = self.graph.create_node(node_type)
        node.set_name(name)
        self.set_node_position_safe(node, base_x + x, base_y + y)

        return node

    def ensure_dummy_model(self):
        """
        Makes templates immediately valid even before the user edits models.
        """
        if not hasattr(self, "model_registry"):
            self.model_registry = []

        for model in self.model_registry:
            if model.get("name") == "DummyModel":
                return "DummyModel"

        self.model_registry.append({
            "name": "DummyModel",
            "parent": None,
        })

        return "DummyModel"

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

    def create_distribution_template(self, name: str, x: float, y: float, value: float):
        node = self.make_node("simulation.flow.DistributionNode", name, x, y)

        self.set_property_safe(node, "dist_type", "Constant")
        self.set_json_property_safe(node, "params", {"value": value})

        return node

    def create_resource_template(self, name: str, x: float, y: float, capacity: float):
        node = self.make_node("simulation.flow.ResourceNode", name, x, y)
        set_text_prop(node, "desired_capacity", capacity)
        set_bool_prop(node, "anonymous", False)
        return node

    def create_buffer_template(self, name: str, x: float, y: float, model_name: str):
        node = self.make_node("simulation.flow.HardBufferNode", name, x, y)

        self.set_json_property_safe(node, "valid_models", [model_name])
        self.set_property_safe(node, "capacity", "inf")

        return node

    def create_shutdowns_template(self, name: str, x: float, y: float):
        node = self.make_node("simulation.flow.ScheduledShutdownsNode", name, x, y)
        return node

    def create_restockable_resource_template(self, name: str, x: float, y: float):
        """
        Creates:
        - RestockableResourceNode
        - Order Duration distribution
        - Delivery Duration distribution

        Then connects:
        Order Duration -> RestockableResource.order_duration
        Delivery Duration -> RestockableResource.delivery_duration
        """
        resource = self.make_node(
            "simulation.flow.RestockableResourceNode",
            name,
            x,
            y,
        )

        set_text_prop(resource, "desired_capacity", 100.0)
        set_text_prop(resource, "threshold", 20.0)

        order_duration = self.create_distribution_template(
            "Order Duration",
            x - 260,
            y - 80,
            0.0,
        )

        delivery_duration = self.create_distribution_template(
            "Delivery Duration",
            x - 260,
            y + 80,
            10.0,
        )

        connect_ports_by_name(order_duration, "distribution", resource, "order_duration")
        connect_ports_by_name(delivery_duration, "distribution", resource, "delivery_duration")

        return resource, order_duration, delivery_duration

    def add_backdrop_for_nodes(self, nodes, title: str, width=None, height=None):
        """
        Creates one persistent clean-JSON backdrop around exactly these nodes.

        The grouped node ids are stored in the backdrop itself, so export/import can
        reconstruct the same group instead of guessing from rectangle overlap.

        If width/height are given (import of a saved backdrop), the backdrop is set to
        that exact size. Otherwise it auto-fits the wrapped nodes (live creation).
        """
        if not nodes:
            return None

        clean_nodes = []
        seen = set()

        for node in nodes:
            if node is None:
                continue

            if node.__class__.__name__ == "BackdropNode":
                continue

            if node_kind(node) in {"Backdrop", "BackdropNode"}:
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
            qmessage(
                self,
                "Backdrop error",
                "Could not create a BackdropNode in this NodeGraphQt version.",
                QtWidgets.QMessageBox.Warning,
            )
            return None

        try:
            backdrop.set_text(title)
        except Exception:
            try:
                backdrop.set_name(title)
            except Exception:
                pass

        if width is not None and height is not None:
            # Restore the saved size exactly (do not auto-fit, which changes the size).
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
            qmessage(
                self,
                "No selection",
                "Select the nodes you want to group, then use Templates > Add Backdrop Around Selection.",
                QtWidgets.QMessageBox.Information,
            )
            return

        self.add_backdrop_for_nodes(selected, "Task group")

    def add_task_template(self):
        """
        Creates a clean left-to-right Task template without buffer cards.

        Columns:
        1. restock configuration / task configuration inputs
        2. Task
        3. breakdown distributions
        4. Breakdown

        Real HardBuffer / SoftBuffer cards are intentionally added manually.
        """
        existing_bounds = self.content_bounds()
        model_name = self.ensure_dummy_model()

        # Column 1A: distributions feeding the restockable resource.
        order_duration = self.create_distribution_template("Order Duration", -1120, 60, 0.0)
        delivery_duration = self.create_distribution_template("Delivery Duration", -1120, 220, 10.0)

        # Column 1B: task inputs and resources.
        task_duration = self.create_distribution_template("Task Duration", -760, -180, 1.0)
        startup_duration = self.create_distribution_template("Startup Duration", -760, -40, 0.0)
        restockable_resource = self.make_node(
            "simulation.flow.RestockableResourceNode",
            "Restockable Resource",
            -760,
            140,
        )
        set_text_prop(restockable_resource, "desired_capacity", 100.0)
        set_text_prop(restockable_resource, "threshold", 20.0)

        operator_resource = self.create_resource_template("Operator", -760, 360, 1.0)
        startup_operator = self.create_resource_template("Startup Operator", -760, 500, 1.0)
        shutdowns = self.create_shutdowns_template("No Scheduled Shutdowns", -760, 640)

        # Column 2: task.
        task = self.make_node("simulation.flow.TaskNode", "Task Template", -300, 160)

        # Column 3 and 4: breakdown.
        mtbf = self.create_distribution_template("MTBF", 80, 70, 100.0)
        mttr = self.create_distribution_template("MTTR", 80, 260, 10.0)
        breakdown = self.make_node("simulation.flow.BreakdownNode", "Breakdown", 440, 160)

        # Defaults.
        self.set_json_property_safe(task, "capability", [model_name])
        self.set_property_safe(task, "resources_scope", "PER_PIECE")
        self.set_property_safe(task, "operators_scope", "PER_BATCH")
        self.set_property_safe(task, "min_capacity", 1)
        self.set_property_safe(task, "max_capacity", 1)
        self.set_property_safe(task, "batch_collector", "GreedyBatchCollector")
        self.set_property_safe(task, "independent_carriers", False)

        # Restockable resource wiring.
        connect_ports_by_name(order_duration, "distribution", restockable_resource, "order_duration")
        connect_ports_by_name(delivery_duration, "distribution", restockable_resource, "delivery_duration")

        # Task wiring.
        connect_ports_by_name(task_duration, "distribution", task, "task_duration")
        connect_ports_by_name(startup_duration, "distribution", task, "startup_duration")
        connect_ports_by_name(restockable_resource, "resource", task, "resources")
        connect_ports_by_name(operator_resource, "resource", task, "operators")
        connect_ports_by_name(startup_operator, "resource", task, "startup_operators")
        connect_ports_by_name(shutdowns, "scheduled_shutdowns", task, "scheduled_shutdowns")

        # Breakdown wiring.
        connect_ports_by_name(task, "task_ref", breakdown, "task")
        connect_ports_by_name(mtbf, "distribution", breakdown, "mtbf")
        connect_ports_by_name(mttr, "distribution", breakdown, "mttr")

        self.set_json_property_safe(task, "resource_quantities", {
            node_uid(restockable_resource): 1.0,
        })
        self.set_json_property_safe(task, "operator_quantities", {
            node_uid(operator_resource): 1,
        })
        self.set_json_property_safe(task, "startup_operator_quantities", {
            node_uid(startup_operator): 1,
        })

        group_nodes = [
            order_duration,
            delivery_duration,
            task_duration,
            startup_duration,
            restockable_resource,
            operator_resource,
            startup_operator,
            shutdowns,
            task,
            mtbf,
            mttr,
            breakdown,
        ]

        self.place_clear_of_existing(group_nodes, existing_bounds)
        backdrop = self.add_backdrop_for_nodes(group_nodes, "Task Template")
        self.focus_on_nodes(group_nodes + ([backdrop] if backdrop else []))
        self.statusBar().showMessage("Added clean left-to-right Task template.")

    def add_first_task_template(self):
        """
        Creates a clean left-to-right FirstTask template without output buffers.

        Columns:
        1. generation/restock durations
        2. restockable resource
        3. FirstTask

        Real HardBuffer / SoftBuffer outputs are intentionally added manually.
        """
        existing_bounds = self.content_bounds()
        model_name = self.ensure_dummy_model()

        # Column 1: duration sources.
        generation_duration = self.create_distribution_template("Generation Duration", -900, -80, 1.0)
        order_duration = self.create_distribution_template("Order Duration", -900, 120, 0.0)
        delivery_duration = self.create_distribution_template("Delivery Duration", -900, 280, 10.0)

        # Column 2: resource.
        restockable_resource = self.make_node(
            "simulation.flow.RestockableResourceNode",
            "Restockable Resource",
            -540,
            180,
        )
        set_text_prop(restockable_resource, "desired_capacity", 100.0)
        set_text_prop(restockable_resource, "threshold", 20.0)

        # Column 3: source task.
        first_task = self.make_node("simulation.flow.FirstTaskNode", "FirstTask Template", -120, 180)

        self.set_json_property_safe(first_task, "models_probs", [
            {
                "model": model_name,
                "probability": 1.0,
            }
        ])

        connect_ports_by_name(generation_duration, "distribution", first_task, "task_duration")
        connect_ports_by_name(order_duration, "distribution", restockable_resource, "order_duration")
        connect_ports_by_name(delivery_duration, "distribution", restockable_resource, "delivery_duration")
        connect_ports_by_name(restockable_resource, "resource", first_task, "resources")

        self.set_json_property_safe(first_task, "resource_quantities", {
            node_uid(restockable_resource): 1.0,
        })

        group_nodes = [
            generation_duration,
            order_duration,
            delivery_duration,
            restockable_resource,
            first_task,
        ]

        self.place_clear_of_existing(group_nodes, existing_bounds)
        backdrop = self.add_backdrop_for_nodes(group_nodes, "FirstTask Template")
        self.focus_on_nodes(group_nodes + ([backdrop] if backdrop else []))
        self.statusBar().showMessage("Added clean left-to-right FirstTask template.")

    def node_type_from_kind(self, kind: str) -> str:
        mapping = {
            "Distribution": "simulation.flow.DistributionNode",
            "Interval": "simulation.flow.IntervalNode",
            "ScheduledShutdowns": "simulation.flow.ScheduledShutdownsNode",
            "Resource": "simulation.flow.ResourceNode",
            "RestockableResource": "simulation.flow.RestockableResourceNode",
            "HardBuffer": "simulation.flow.HardBufferNode",
            "SoftBuffer": "simulation.flow.SoftBufferNode",
            "FirstTask": "simulation.flow.FirstTaskNode",
            "Task": "simulation.flow.TaskNode",
            "Breakdown": "simulation.flow.BreakdownNode",
            "Monitor": "simulation.flow.MonitorNode",
        }

        # Accept legacy nomenclature from older exports.
        kind = LEGACY_KIND_ALIASES.get(kind, kind)

        if kind not in mapping:
            raise ValueError(f"Unknown node kind in JSON: {kind}")

        return mapping[kind]

    def apply_clean_json_to_node(self, node, node_data: dict):
        kind = node_data.get("kind")
        kind = LEGACY_KIND_ALIASES.get(kind, kind)  # accept legacy Buffer / BufferTree

        if "id" in node_data:
            self.set_property_safe(node, "uid", node_data["id"])

        if "name" in node_data:
            node.set_name(node_data["name"])

        position = node_data.get("position", [0, 0])
        if isinstance(position, list) and len(position) >= 2:
            self.set_node_position_safe(node, position[0], position[1])

        if kind == "Distribution":
            distribution = node_data.get("distribution", {})
            self.set_property_safe(node, "dist_type", distribution.get("type", "Constant"))
            self.set_json_property_safe(node, "params", distribution.get("params", {"value": 0.0}))

        elif kind == "Interval":
            set_text_prop(node, "start", node_data.get("start", 0.0))
            set_text_prop(node, "end", node_data.get("end", 1.0))

        elif kind == "Resource":
            set_text_prop(node, "desired_capacity", node_data.get("capacity", 1.0))
            set_bool_prop(node, "anonymous", node_data.get("anonymous", False))

        elif kind == "RestockableResource":
            set_text_prop(node, "desired_capacity", node_data.get("capacity", 100.0))
            set_text_prop(node, "threshold", node_data.get("threshold", 20.0))

        elif kind == "HardBuffer":
            self.set_json_property_safe(node, "valid_models", node_data.get("valid_models", []))
            self.set_property_safe(node, "capacity", node_data.get("capacity", "inf"))
            self.set_property_safe(node, "buffer_role", node_data.get("buffer_role", "Normal"))

        elif kind == "SoftBuffer":
            prob_map = {}

            for item in node_data.get("buffer_probs", []):
                buffer_id = item.get("buffer")
                probability = item.get("probability", 0.0)

                if buffer_id:
                    prob_map[buffer_id] = probability

            self.set_json_property_safe(node, "buffer_probs", prob_map)
            self.set_property_safe(node, "buffer_role", node_data.get("buffer_role", "Normal"))

        elif kind == "FirstTask":
            self.set_json_property_safe(node, "models_probs", node_data.get("models_probs", []))

            resource_quantities = {}
            for item in node_data.get("resources", []):
                rid = item.get("resource")
                quantity = item.get("quantity", 1.0)

                if rid:
                    resource_quantities[rid] = quantity

            self.set_json_property_safe(node, "resource_quantities", resource_quantities)

        elif kind == "Monitor":
            stats = node_data.get("stats", {}) or {}
            for key, _label, default in MONITOR_STATS:
                set_bool_prop(node, key, bool(stats.get(key, default)))

        elif kind == "Task":
            self.set_json_property_safe(node, "capability", node_data.get("capability", []))
            self.set_property_safe(node, "resources_scope", node_data.get("resources_scope", "PER_PIECE"))
            self.set_property_safe(node, "operators_scope", node_data.get("operators_scope", "PER_BATCH"))
            self.set_property_safe(node, "min_capacity", node_data.get("min_capacity", 1))
            self.set_property_safe(node, "max_capacity", node_data.get("max_capacity", 1))
            self.set_property_safe(node, "batch_collector", node_data.get("batch_collector", "GreedyBatchCollector"))
            self.set_property_safe(node, "independent_carriers", node_data.get("independent_carriers", False))

            resource_quantities = {}
            for item in node_data.get("resources", []):
                rid = item.get("resource")
                quantity = item.get("quantity", 1.0)

                if rid:
                    resource_quantities[rid] = quantity

            operator_quantities = {}
            for item in node_data.get("operators", []):
                rid = item.get("resource")
                quantity = item.get("quantity", 1)

                if rid:
                    operator_quantities[rid] = quantity

            startup_operator_quantities = {}
            for item in node_data.get("startup_operators", []):
                rid = item.get("resource")
                quantity = item.get("quantity", 1)

                if rid:
                    startup_operator_quantities[rid] = quantity

            self.set_json_property_safe(node, "resource_quantities", resource_quantities)
            self.set_json_property_safe(node, "operator_quantities", operator_quantities)
            self.set_json_property_safe(node, "startup_operator_quantities", startup_operator_quantities)

    def import_clean_json_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import clean JSON",
            "",
            "JSON (*.json)",
        )

        if not path:
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.import_clean_json(data)
        self.statusBar().showMessage(f"Imported clean JSON: {path}")

    def import_clean_json(self, data: dict):
        """
        Imports clean JSON only.

        It reconstructs:
        - cards
        - typed connections
        - persistent backdrop groups
        """
        self.graph.clear_session()
        self.model_registry = data.get("models", [])

        id_to_node = {}

        # 1. Create cards.
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

            card_id = card.get("id")
            if card_id:
                id_to_node[card_id] = node

        # 2. Reconnect cards.
        for connection in data.get("connections", []):
            from_node = id_to_node.get(connection.get("from_node"))
            to_node = id_to_node.get(connection.get("to_node"))

            if from_node is None or to_node is None:
                continue

            try:
                connect_ports_by_name(
                    from_node,
                    connection.get("from_port"),
                    to_node,
                    connection.get("to_port"),
                )
            except Exception as error:
                print(
                    "[WARNING] Could not reconnect "
                    f"{connection.get('from_node')}.{connection.get('from_port')} -> "
                    f"{connection.get('to_node')}.{connection.get('to_port')}: {error}"
                )

        # 3. Restore backdrop groups.
        imported_backdrops = data.get("backdrops", [])

        if imported_backdrops:
            for group in imported_backdrops:
                # New compact format uses "nodes"; older format used "wrapped_node_ids".
                group_node_ids = group.get("nodes", group.get("wrapped_node_ids", []))
                group_nodes = [
                    id_to_node[node_id]
                    for node_id in group_node_ids
                    if node_id in id_to_node
                ]

                if not group_nodes:
                    continue

                title = group.get("title", "Imported group")
                backdrop = self.add_backdrop_for_nodes(
                    group_nodes,
                    title,
                    width=group.get("width"),
                    height=group.get("height"),
                )

                position = group.get("position")
                if backdrop is not None and isinstance(position, list) and len(position) >= 2:
                    self.set_node_position_safe(backdrop, position[0], position[1])
        else:
            # Old exports did not preserve backdrop membership, so exact fidelity is impossible.
            # We still create one fallback visual group around imported cards.
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
