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

# Map old two-value collector names onto the new enum when importing old JSON.
_LEGACY_COLLECTOR = {
    "GREEDY": "NON_DISCRIMINATING_GREEDY",
    "ALTRUISTIC": "NON_DISCRIMINATING_ALTRUISTIC",
    "GreedyBatchCollector": "NON_DISCRIMINATING_GREEDY",
    "AltruisticBatchCollector": "NON_DISCRIMINATING_ALTRUISTIC",
}

SCOPES_FOR_OPERATORS = ["PER_BATCH", "PER_TASK"]
SCOPES_FOR_RESOURCES = ["PER_PIECE", "PER_BATCH"]

SHUTDOWN_TYPES = ["NON_FLEXIBLE", "FLEXIBLE"]

# Old nomenclature -> new nomenclature, used when importing older clean JSON.
LEGACY_KIND_ALIASES = {
    "Buffer": "HardBuffer",
    "BufferTree": "SoftBuffer",
    "ScheduledShutdowns": "Shutdowns",
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
    "group": (200, 170, 90),
}

BUFFER_ROLES = ["Normal", "Exit", "Scrap"]

# Statistics offered by a Monitor card.
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


def add_float_input(node: BaseNode, name: str, label: str, default) -> None:
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
    try:
        node.add_combo_menu(name, label=label, items=items)
        node.set_property(name, default)
    except Exception:
        if not node.has_property(name):
            node.create_property(name, default)


def set_text_prop(node: BaseNode, name: str, value) -> None:
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


# ---- OR-of-ANDs (alternatives) resolution --------------------------------
# Operators (and per-model resources) are `list[list[(resource, qty)]]`:
# an outer OR of inner AND groups. These helpers turn the AND / OR grouping
# cards on the canvas into that structure for export.

RESOURCE_KINDS = {"Resource", "RestockableResource"}


def _coerce_qty(value, integer: bool):
    return as_int(value, 1) if integer else as_float(value, 1.0)


def _and_group_members(and_node, integer: bool) -> list:
    """The single AND group carried by an AND card: [{resource, quantity}, ...]."""
    quantities = get_property_json(and_node, "member_quantities", {})
    group = []
    for member in connected_nodes_from_port(and_node, "members", "input"):
        if node_kind(member) not in RESOURCE_KINDS:
            continue
        rid = node_uid(member)
        group.append({"resource": rid, "quantity": _coerce_qty(quantities.get(rid, 1), integer)})
    return group


def _or_group_alternatives(or_node, integer: bool) -> list:
    """The OR alternatives carried by an OR card: [[...group...], ...]."""
    quantities = get_property_json(or_node, "member_quantities", {})
    alternatives = []
    for src in connected_nodes_from_port(or_node, "groups", "input"):
        kind = node_kind(src)
        if kind == "AndGroup":
            group = _and_group_members(src, integer)
            if group:
                alternatives.append(group)
        elif kind in RESOURCE_KINDS:
            rid = node_uid(src)
            alternatives.append([{"resource": rid, "quantity": _coerce_qty(quantities.get(rid, 1), integer)}])
    return alternatives


def resolve_operator_dnf(node, port_name: str, direct_quantities: dict, integer: bool = True) -> list:
    """Resolve everything wired into an operator/resource port into OR-of-ANDs.

    A port may receive, in any mix:
      - OR cards      -> expand to their alternatives,
      - AND cards     -> one alternative each,
      - bare Resource cards wired directly -> ALL of them together form a single
        alternative (an implicit AND group), placed first. A single direct
        resource is thus just a one-member alternative; two direct resources mean
        "both required". Use an OR card when you want "any one of" instead.
    """
    direct_group = []
    card_alternatives = []
    for src in connected_nodes_from_port(node, port_name, "input"):
        kind = node_kind(src)
        if kind == "OrGroup":
            card_alternatives.extend(_or_group_alternatives(src, integer))
        elif kind == "AndGroup":
            group = _and_group_members(src, integer)
            if group:
                card_alternatives.append(group)
        elif kind in RESOURCE_KINDS:
            rid = node_uid(src)
            direct_group.append({"resource": rid, "quantity": _coerce_qty(direct_quantities.get(rid, 1), integer)})
    return ([direct_group] if direct_group else []) + card_alternatives


def direct_resource_nodes(node, port_name: str) -> list:
    """Resource/RestockableResource nodes wired straight into a port (no group card)."""
    return [n for n in connected_nodes_from_port(node, port_name, "input") if node_kind(n) in RESOURCE_KINDS]


def model_config_groups(mc: dict) -> list:
    """Normalize a stored per-model config to a list of AND-group dicts `{rid: qty}`.

    New format stores `resource_groups` (a list of dicts, OR between them).
    Legacy format stored a single `resources` dict (one implicit group).
    """
    groups = mc.get("resource_groups")
    if isinstance(groups, list):
        return [dict(g) for g in groups if g]
    legacy = mc.get("resources") or {}
    return [dict(legacy)] if legacy else []


def model_config_resource_dnf(mc: dict) -> list:
    """OR-of-ANDs export for a model's consumed resources: [[{resource, quantity}], ...]."""
    dnf = []
    for group in model_config_groups(mc):
        dnf.append([{"resource": rid, "quantity": as_float(q, 1.0)} for rid, q in group.items()])
    return dnf


def import_resource_groups(resources) -> list:
    """Parse an exported model `resources` value into internal group dicts `{rid: qty}`.

    Accepts new OR-of-ANDs (`[[{resource, quantity}], ...]`) and the legacy flat
    form (`[{resource, quantity}, ...]`, one implicit group).
    """
    groups = []
    if isinstance(resources, list) and resources and isinstance(resources[0], list):
        for grp in resources:
            d = {it["resource"]: it.get("quantity", 1.0)
                 for it in (grp or []) if isinstance(it, dict) and it.get("resource")}
            if d:
                groups.append(d)
    elif isinstance(resources, list):  # legacy flat -> one group
        d = {it["resource"]: it.get("quantity", 1.0)
             for it in resources if isinstance(it, dict) and it.get("resource")}
        if d:
            groups.append(d)
    return groups


def operator_quantities_from_export(operators) -> dict:
    """Recover per-resource quantities for directly-wired operators from an export.

    In a new OR-of-ANDs export the single-member alternatives are exactly the
    resources wired straight to the port (their quantity lives on the task);
    multi-member groups carry their quantities on AND cards and are skipped.
    Legacy exports are a flat `[{resource, quantity}]` list.
    """
    result = {}
    if not isinstance(operators, list):
        return result
    for entry in operators:
        if isinstance(entry, dict) and entry.get("resource"):  # legacy flat
            result[entry["resource"]] = entry.get("quantity", 1)
        elif isinstance(entry, list) and len(entry) == 1:       # single-member alternative
            it = entry[0]
            if isinstance(it, dict) and it.get("resource"):
                result[it["resource"]] = it.get("quantity", 1)
    return result


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

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "valid_models": get_property_json(self, "valid_models", []),
            "capacity": self.get_property("capacity"),
            "inputs_from": connected_refs_from_port(self, "from_task", "input"),
            "outputs_to": connected_refs_from_port(self, "to_task", "output"),
            "position": [self.x_pos(), self.y_pos()],
        }


class SoftBufferNode(SimNode):
    NODE_NAME = "Soft Buffer (Router)"
    kind = "SoftBuffer"
    color = (60, 115, 125)

    def __init__(self):
        super().__init__()
        self.add_input("from_task", multi_input=True, color=PORT_COLORS["task"])
        self.add_output("to_buffers", multi_output=True, color=PORT_COLORS["buffer"])
        self.create_property("buffer_probs", "{}")  # {buffer_id: <time-function>}

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


class FirstTaskNode(SimNode):
    """PieceGenerator: per-model integer goals over chosen shifts -> outlets.
    Only childless (leaf) models can be generated."""
    NODE_NAME = "Source (PieceGenerator)"
    kind = "FirstTask"
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
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.add_output("task_ref", multi_output=True, color=PORT_COLORS["breakdown"])

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
            "position": [self.x_pos(), self.y_pos()],
        }


class ResourceTaskNode(SimNode):
    """ResourceTask. Consumes/transforms resources into output resources. No piece
    flow; connects to breakdowns via task_ref and to shutdown cards."""
    NODE_NAME = "Resource Task"
    kind = "ResourceTask"
    color = (150, 120, 60)

    def __init__(self):
        super().__init__()
        self.add_input("shutdowns", multi_input=True, color=PORT_COLORS["shutdown"])
        self.add_output("task_ref", multi_output=True, color=PORT_COLORS["breakdown"])

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
            "transformed_resources_salvageable": get_property_json(self, "transformed_resources", []),
            "resources_out_distr": get_property_json(self, "resources_out", []),
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
        self.add_input("task", color=PORT_COLORS["breakdown"])
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.create_property("mtbf", "{}")   # {"mode": "distribution"|"bathtub", ...}
        self.create_property("mttr", "")     # <sampler>

    def to_clean_json(self) -> dict:
        return {
            "id": node_uid(self),
            "kind": self.kind,
            "name": self.name(),
            "task": get_input_ref(self, "task"),
            "mtbf": get_property_json(self, "mtbf", {}),
            "mttr": get_property_json(self, "mttr", None),
            "outlets": get_output_refs(self, "bufs_out"),
            "position": [self.x_pos(), self.y_pos()],
        }


class MonitorNode(SimNode):
    NODE_NAME = "Monitor"
    kind = "Monitor"
    color = (55, 110, 125)

    def __init__(self):
        super().__init__()
        self.add_input("buffer", color=PORT_COLORS["monitor"])
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


def port_signature(port) -> Tuple[str, str, str]:
    n = port.node()
    ptype = str(port.type_()).lower()
    direction = "input" if "in" in ptype else "output"
    return node_kind(n), direction, port.name()


def is_valid_connection(out_kind: str, out_port: str, in_kind: str, in_port: str) -> bool:
    """Strict connection rules. Central place controlling what can feed what."""

    # Distribution feeds duration slots.
    if out_kind == "Distribution":
        return (
            (in_kind == "Task" and in_port in {"durations", "startup_duration"})
            or (in_kind == "ResourceTask" and in_port in {"task_duration", "startup_duration"})
            or (in_kind == "Breakdown" and in_port == "mttr")
            or (in_kind == "RestockableResource" and in_port in {"order_duration", "delivery_duration"})
        )

    # Interval feeds shutdowns.
    if out_kind == "Interval":
        return in_kind == "Shutdowns" and in_port == "intervals"

    # Shutdowns feed tasks (piece or resource).
    if out_kind == "Shutdowns":
        return in_kind in {"Task", "ResourceTask"} and in_port == "shutdowns"

    # Resources feed task/resource slots, or group cards (AND/OR).
    if out_kind in {"Resource", "RestockableResource"}:
        if in_kind == "Task":
            return in_port in {"resources", "operators", "startup_operators"}
        if in_kind == "ResourceTask":
            return in_port in {
                "non_transformed_resources", "transformed_resources", "resources_out",
                "operators", "startup_operators",
            }
        if in_kind == "AndGroup":
            return in_port == "members"
        if in_kind == "OrGroup":
            return in_port == "groups"
        return False

    # AND card carries one group into an OR card or straight to a task operator port.
    if out_kind == "AndGroup" and out_port == "group":
        if in_kind == "OrGroup":
            return in_port == "groups"
        return in_kind in {"Task", "ResourceTask"} and in_port in {"operators", "startup_operators"}

    # OR card carries the alternatives to a task operator port.
    if out_kind == "OrGroup" and out_port == "out":
        return in_kind in {"Task", "ResourceTask"} and in_port in {"operators", "startup_operators"}

    # HardBuffer feeds task inputs.
    if out_kind == "HardBuffer" and out_port == "to_task":
        return in_kind == "Task" and in_port == "bufs_in"

    # HardBuffer taps a Monitor card.
    if out_kind == "HardBuffer" and out_port == "monitor":
        return in_kind == "Monitor" and in_port == "buffer"

    # Tasks / FirstTasks / Breakdowns feed buffers.
    if out_kind in {"Task", "FirstTask", "Breakdown"} and out_port == "bufs_out":
        return in_kind in {"HardBuffer", "SoftBuffer"} and in_port == "from_task"

    # SoftBuffer routes to hard or soft buffers with probabilities.
    if out_kind == "SoftBuffer" and out_port == "to_buffers":
        return in_kind in {"HardBuffer", "SoftBuffer"} and in_port == "from_task"

    # Technical task reference for breakdowns.
    if out_kind in {"Task", "ResourceTask"} and out_port == "task_ref":
        return in_kind == "Breakdown" and in_port == "task"

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


class ModelGoalsDialog(QtWidgets.QDialog):
    """PieceGenerator model goals: {model: integer goal}."""
    def __init__(self, parent, all_models: List[dict], current: List[dict]):
        super().__init__(parent)
        self.setWindowTitle("Source model goals")
        self.resize(480, 360)
        self.all_model_names = [m["name"] for m in all_models]

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["model", "goal (integer count)"])
        self.table.horizontalHeader().setStretchLastSection(True)

        btn_add = QtWidgets.QPushButton("Add model")
        btn_remove = QtWidgets.QPushButton("Remove selected")
        btn_ok = QtWidgets.QPushButton("OK")
        btn_cancel = QtWidgets.QPushButton("Cancel")

        tools = QtWidgets.QHBoxLayout()
        tools.addWidget(btn_add)
        tools.addWidget(btn_remove)
        tools.addStretch()

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(btn_ok)
        bottom.addWidget(btn_cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("How many of each model to produce over the working horizon."))
        layout.addWidget(self.table)
        layout.addLayout(tools)
        layout.addLayout(bottom)

        btn_add.clicked.connect(lambda: self.add_row("", 1))
        btn_remove.clicked.connect(self.remove_selected)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        for item in current:
            self.add_row(item.get("model", ""), item.get("goal", 1))

    def add_row(self, model="", goal=1):
        row = self.table.rowCount()
        self.table.insertRow(row)
        combo = QtWidgets.QComboBox()
        combo.addItems(self.all_model_names)
        if model in self.all_model_names:
            combo.setCurrentText(model)
        self.table.setCellWidget(row, 0, combo)
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(goal)))

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def values(self) -> List[dict]:
        result, seen, total = [], set(), 0
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 0)
            model = combo.currentText().strip() if combo else ""
            goal = as_int(self.table.item(r, 1).text() if self.table.item(r, 1) else 1, 1)
            if not model:
                continue
            if model in seen:
                raise ValueError(f"Duplicate model: {model}")
            if goal < 1:
                raise ValueError("goal must be >= 1.")
            seen.add(model)
            total += goal
            result.append({"model": model, "goal": goal})
        if not result:
            raise ValueError("At least one model is required.")
        return result

    def accept(self):
        try:
            self.values()
        except Exception as e:
            qmessage(self, "Invalid goals", str(e), QtWidgets.QMessageBox.Warning)
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
            params[name] = typ(self.param_widgets[name].text())
        return dist_type, params


class QuantityDialog(QtWidgets.QDialog):
    def __init__(self, parent, title: str, connected_nodes: List[BaseNode], current_map: dict, integer=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(540, 340)
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
        layout.addWidget(QtWidgets.QLabel("Probabilities are stored by connected target id (Hard or Soft buffer)."))
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
        probs = [as_float(self.table.item(r, 2).text(), 0.0) for r in range(self.table.rowCount())]
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

        if kind == "RestockableResource":
            thr = node.get_property("threshold") if node.has_property("threshold") else 20.0
            self.threshold_edit = QtWidgets.QLineEdit(str(thr))
            form.addRow("threshold", self.threshold_edit)

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
        if node_kind(self.node) == "Resource":
            set_bool_prop(self.node, "anonymous", False)
        super().accept()


class ResourceGroupDialog(QtWidgets.QDialog):
    """Edit ONE AND group: tick a subset of resources and set their quantities."""
    def __init__(self, parent, resource_nodes: List[BaseNode], current: dict):
        super().__init__(parent)
        self.setWindowTitle("AND group (resources needed together)")
        self.resize(560, 360)
        self.resource_nodes = list(resource_nodes)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["use", "resource name", "resource id", "quantity"])
        self.table.horizontalHeader().setStretchLastSection(True)

        for n in self.resource_nodes:
            uid = node_uid(n)
            row = self.table.rowCount()
            self.table.insertRow(row)
            chk = QtWidgets.QCheckBox()
            chk.setChecked(uid in current)
            self.table.setCellWidget(row, 0, chk)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(n.name()))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(uid))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(current.get(uid, 1.0))))

        ok = QtWidgets.QPushButton("OK"); cancel = QtWidgets.QPushButton("Cancel")
        buttons = QtWidgets.QHBoxLayout(); buttons.addStretch(); buttons.addWidget(ok); buttons.addWidget(cancel)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Tick the resources this alternative needs together, and set quantities.\n"
            "Only ticked rows are part of the group."))
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)

    def values(self) -> dict:
        result = {}
        for r in range(self.table.rowCount()):
            chk = self.table.cellWidget(r, 0)
            if chk and chk.isChecked():
                uid = self.table.item(r, 2).text()
                result[uid] = as_float(self.table.item(r, 3).text(), 1.0)
        return result

    def accept(self):
        if not self.values():
            qmessage(self, "Empty group", "Tick at least one resource (or press Cancel).",
                     QtWidgets.QMessageBox.Warning)
            return
        super().accept()


class ResourceGroupsDialog(QtWidgets.QDialog):
    """Edit a model's consumed resources as OR-of-ANDs (a list of alternative groups)."""
    def __init__(self, parent, resource_nodes: List[BaseNode], groups: List[dict]):
        super().__init__(parent)
        self.setWindowTitle("Resource alternatives for this model")
        self.resize(560, 360)
        self.resource_nodes = list(resource_nodes)
        self.groups = [dict(g) for g in (groups or []) if g]

        self.list_widget = QtWidgets.QListWidget()

        btn_add = QtWidgets.QPushButton("Add alternative")
        btn_edit = QtWidgets.QPushButton("Edit selected")
        btn_remove = QtWidgets.QPushButton("Remove selected")
        ok = QtWidgets.QPushButton("OK"); cancel = QtWidgets.QPushButton("Cancel")

        tools = QtWidgets.QHBoxLayout()
        tools.addWidget(btn_add); tools.addWidget(btn_edit); tools.addWidget(btn_remove); tools.addStretch()
        bottom = QtWidgets.QHBoxLayout(); bottom.addStretch(); bottom.addWidget(ok); bottom.addWidget(cancel)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Each row is one alternative (an AND group). The task uses the first\n"
            "alternative whose resources are all available. Wire Resource cards into\n"
            "the task's 'resources' port to make them selectable here."))
        layout.addWidget(self.list_widget)
        layout.addLayout(tools)
        layout.addLayout(bottom)

        btn_add.clicked.connect(self.add_group)
        btn_edit.clicked.connect(self.edit_selected)
        btn_remove.clicked.connect(self.remove_selected)
        self.list_widget.itemDoubleClicked.connect(lambda _: self.edit_selected())
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        self._refresh()

    def _name_for(self, uid):
        for n in self.resource_nodes:
            if node_uid(n) == uid:
                return n.name()
        return uid

    def _summary(self, group: dict) -> str:
        if not group:
            return "<empty>"
        return " + ".join(f"{self._name_for(uid)}x{group[uid]}" for uid in group)

    def _refresh(self):
        self.list_widget.clear()
        for i, group in enumerate(self.groups):
            self.list_widget.addItem(f"[{i + 1}] {self._summary(group)}")

    def add_group(self):
        dlg = ResourceGroupDialog(self, self.resource_nodes, {})
        if dlg.exec():
            self.groups.append(dlg.values())
            self._refresh()

    def edit_selected(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.groups):
            dlg = ResourceGroupDialog(self, self.resource_nodes, self.groups[row])
            if dlg.exec():
                self.groups[row] = dlg.values()
                self._refresh()

    def remove_selected(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.groups):
            self.groups.pop(row)
            self._refresh()

    def values(self) -> List[dict]:
        return [dict(g) for g in self.groups if g]


class ModelConfigsDialog(QtWidgets.QDialog):
    """Per-model config for a Piece Task: model -> (duration card, resource alternatives).

    Each row's resource alternatives (an OR-of-ANDs) are stored on the row's
    button widget as `_groups`, so removing/adding rows never desynchronizes them.
    """
    def __init__(self, parent, model_registry, duration_nodes, resource_nodes, current):
        super().__init__(parent)
        self.setWindowTitle("Model configs (duration + resource alternatives per model)")
        self.resize(680, 420)
        self.model_names = [m["name"] for m in model_registry]
        self.duration_nodes = list(duration_nodes)
        self.resource_nodes = list(resource_nodes)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["model", "duration (connected)", "resource alternatives"])
        self.table.horizontalHeader().setStretchLastSection(True)

        btn_add = QtWidgets.QPushButton("Add model")
        btn_remove = QtWidgets.QPushButton("Remove selected")
        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")

        tools = QtWidgets.QHBoxLayout()
        tools.addWidget(btn_add)
        tools.addWidget(btn_remove)
        tools.addStretch()
        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(ok)
        bottom.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Connect Distribution cards to the task's 'durations' port and Resource cards to 'resources',\n"
            "then give each model one duration and one or more resource alternatives (OR-of-ANDs)."))
        layout.addWidget(self.table)
        layout.addLayout(tools)
        layout.addLayout(bottom)

        btn_add.clicked.connect(lambda: self.add_row("", None, []))
        btn_remove.clicked.connect(self.remove_selected)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        for mc in current:
            self.add_row(mc.get("model", ""), mc.get("duration"), model_config_groups(mc))

    def _duration_combo(self, selected_id):
        combo = QtWidgets.QComboBox()
        combo.addItem("<none>", None)
        for n in self.duration_nodes:
            combo.addItem(n.name(), node_uid(n))
        if selected_id is not None:
            idx = combo.findData(selected_id)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        return combo

    def _resource_button(self, groups):
        btn = QtWidgets.QPushButton()
        btn._groups = [dict(g) for g in (groups or []) if g]
        self._refresh_resource_button(btn)
        btn.clicked.connect(lambda _=False, b=btn: self.edit_resources(b))
        return btn

    def _refresh_resource_button(self, btn):
        n = len(btn._groups)
        label = "Edit resource alternatives..." if n == 0 else f"Edit resource alternatives ({n})..."
        btn.setText(label)

    def add_row(self, model="", duration_id=None, groups=None):
        row = self.table.rowCount()
        self.table.insertRow(row)

        model_combo = QtWidgets.QComboBox()
        model_combo.addItems(self.model_names)
        if model in self.model_names:
            model_combo.setCurrentText(model)
        self.table.setCellWidget(row, 0, model_combo)
        self.table.setCellWidget(row, 1, self._duration_combo(duration_id))
        self.table.setCellWidget(row, 2, self._resource_button(groups))

    def edit_resources(self, btn):
        dlg = ResourceGroupsDialog(self, self.resource_nodes, btn._groups)
        if dlg.exec():
            btn._groups = dlg.values()
            self._refresh_resource_button(btn)

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def values(self) -> List[dict]:
        result, seen = [], set()
        for r in range(self.table.rowCount()):
            model_combo = self.table.cellWidget(r, 0)
            dur_combo = self.table.cellWidget(r, 1)
            res_btn = self.table.cellWidget(r, 2)
            model = model_combo.currentText().strip() if model_combo else ""
            duration_id = dur_combo.currentData() if dur_combo else None
            groups = list(getattr(res_btn, "_groups", []))
            if not model:
                continue
            if model in seen:
                raise ValueError(f"Duplicate model: {model}")
            seen.add(model)
            result.append({"model": model, "duration": duration_id, "resource_groups": groups})
        if not result:
            raise ValueError("At least one model config is required.")
        return result

    def accept(self):
        try:
            self.values()
        except Exception as e:
            qmessage(self, "Invalid model configs", str(e), QtWidgets.QMessageBox.Warning)
            return
        super().accept()


def _spin(val, minimum=1):
    s = QtWidgets.QSpinBox()
    s.setMinimum(minimum)
    s.setMaximum(999999)
    s.setValue(as_int(val, minimum))
    return s


class TaskConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent, task_node: TaskNode, model_registry: List[dict]):
        super().__init__(parent)
        self.setWindowTitle("Piece Task config")
        self.resize(560, 560)
        self.task_node = task_node
        self.model_registry = model_registry

        self.models_btn = QtWidgets.QPushButton("Edit model configs (duration + resources)...")
        self._refresh_models_label()

        self.resources_scope = QtWidgets.QComboBox(); self.resources_scope.addItems(SCOPES_FOR_RESOURCES)
        self.resources_scope.setCurrentText(task_node.get_property("resources_scope"))
        self.operators_scope = QtWidgets.QComboBox(); self.operators_scope.addItems(SCOPES_FOR_OPERATORS)
        self.operators_scope.setCurrentText(task_node.get_property("operators_scope"))

        self.min_carriers = _spin(task_node.get_property("min_carriers"))
        self.max_capacity = _spin(task_node.get_property("max_capacity"))
        self.min_carrier_capacity = _spin(task_node.get_property("min_carrier_capacity"))
        self.max_carrier_capacity = _spin(task_node.get_property("max_carrier_capacity"))

        self.contiguous_carriers = QtWidgets.QCheckBox("contiguous_carriers")
        self.contiguous_carriers.setChecked(bool(task_node.get_property("contiguous_carriers")))
        self.collector_type = QtWidgets.QComboBox(); self.collector_type.addItems(COLLECTOR_TYPES)
        self.collector_type.setCurrentText(task_node.get_property("collector_type"))
        self.independent_carriers = QtWidgets.QCheckBox("independent_carriers")
        self.independent_carriers.setChecked(bool(task_node.get_property("independent_carriers")))

        btn_operators = QtWidgets.QPushButton("Edit operator quantities...")
        btn_startup_ops = QtWidgets.QPushButton("Edit startup operator quantities...")
        ok = QtWidgets.QPushButton("OK"); cancel = QtWidgets.QPushButton("Cancel")

        form = QtWidgets.QFormLayout()
        form.addRow("models", self.models_label)
        form.addRow("", self.models_btn)
        form.addRow("resources_scope", self.resources_scope)
        form.addRow("operators_scope", self.operators_scope)
        form.addRow("min_carrier_capacity", self.min_carrier_capacity)
        form.addRow("max_carrier_capacity", self.max_carrier_capacity)
        form.addRow("max_capacity (total slots)", self.max_capacity)
        form.addRow("min_carriers", self.min_carriers)
        form.addRow("", self.contiguous_carriers)
        form.addRow("collector_type", self.collector_type)
        form.addRow("", self.independent_carriers)
        form.addRow("operators", btn_operators)
        form.addRow("startup operators", btn_startup_ops)
        buttons = QtWidgets.QHBoxLayout(); buttons.addStretch(); buttons.addWidget(ok); buttons.addWidget(cancel)
        layout = QtWidgets.QVBoxLayout(self); layout.addLayout(form); layout.addLayout(buttons)

        self.models_btn.clicked.connect(self.edit_model_configs)
        btn_operators.clicked.connect(self.edit_operator_quantities)
        btn_startup_ops.clicked.connect(self.edit_startup_operator_quantities)
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)

    def _refresh_models_label(self):
        mc = get_property_json(self.task_node, "models_configs", [])
        names = [m.get("model") for m in mc if m.get("model")]
        text = ", ".join(names) if names else "<none>"
        if not hasattr(self, "models_label"):
            self.models_label = QtWidgets.QLabel(text)
        else:
            self.models_label.setText(text)

    def edit_model_configs(self):
        durations = connected_nodes_from_port(self.task_node, "durations", "input")
        resources = connected_nodes_from_port(self.task_node, "resources", "input")
        current = get_property_json(self.task_node, "models_configs", [])
        dlg = ModelConfigsDialog(self, self.model_registry, durations, resources, current)
        if dlg.exec():
            set_property_json(self.task_node, "models_configs", dlg.values())
            self._refresh_models_label()

    def edit_operator_quantities(self):
        connected = direct_resource_nodes(self.task_node, "operators")
        current = get_property_json(self.task_node, "operator_quantities", {})
        dlg = QuantityDialog(self, "Operator quantities (resources wired directly)", connected, current, integer=True)
        if dlg.exec():
            set_property_json(self.task_node, "operator_quantities", dlg.values())

    def edit_startup_operator_quantities(self):
        connected = direct_resource_nodes(self.task_node, "startup_operators")
        current = get_property_json(self.task_node, "startup_operator_quantities", {})
        dlg = QuantityDialog(self, "Startup operator quantities (resources wired directly)", connected, current, integer=True)
        if dlg.exec():
            set_property_json(self.task_node, "startup_operator_quantities", dlg.values())

    def accept(self):
        if self.min_carrier_capacity.value() > self.max_carrier_capacity.value():
            qmessage(self, "Invalid capacity", "min_carrier_capacity cannot exceed max_carrier_capacity.", QtWidgets.QMessageBox.Warning); return
        if self.max_carrier_capacity.value() > self.max_capacity.value():
            qmessage(self, "Invalid capacity", "max_carrier_capacity cannot exceed max_capacity.", QtWidgets.QMessageBox.Warning); return
        self.task_node.set_property("resources_scope", self.resources_scope.currentText())
        self.task_node.set_property("operators_scope", self.operators_scope.currentText())
        self.task_node.set_property("min_carriers", self.min_carriers.value())
        self.task_node.set_property("max_capacity", self.max_capacity.value())
        self.task_node.set_property("min_carrier_capacity", self.min_carrier_capacity.value())
        self.task_node.set_property("max_carrier_capacity", self.max_carrier_capacity.value())
        self.task_node.set_property("contiguous_carriers", self.contiguous_carriers.isChecked())
        self.task_node.set_property("collector_type", self.collector_type.currentText())
        self.task_node.set_property("independent_carriers", self.independent_carriers.isChecked())
        super().accept()


class TransformedSpecsDialog(QtWidgets.QDialog):
    """Per transformed resource: proportion (per output unit) + salvageable flag."""
    def __init__(self, parent, connected_nodes: List[BaseNode], current_map: dict):
        super().__init__(parent)
        self.setWindowTitle("Transformed resources (proportion + salvageable)")
        self.resize(600, 340)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["resource name", "resource id", "proportion", "salvageable"])
        self.table.horizontalHeader().setStretchLastSection(True)

        for n in connected_nodes:
            uid = node_uid(n)
            spec = current_map.get(uid, {})
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(n.name()))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(uid))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(spec.get("proportion", 1.0))))
            chk = QtWidgets.QCheckBox()
            chk.setChecked(bool(spec.get("salvageable", True)))
            self.table.setCellWidget(row, 3, chk)

        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("proportion = units consumed per unit produced; salvageable = returned on abort."))
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def values(self) -> dict:
        result = {}
        for r in range(self.table.rowCount()):
            uid = self.table.item(r, 1).text()
            chk = self.table.cellWidget(r, 3)
            result[uid] = {
                "proportion": as_float(self.table.item(r, 2).text(), 1.0),
                "salvageable": bool(chk.isChecked()) if chk else True,
            }
        return result


class OutSpecsDialog(QtWidgets.QDialog):
    """Per output resource: a bounded distribution (type + params JSON + low/high)."""
    def __init__(self, parent, connected_nodes: List[BaseNode], current_map: dict):
        super().__init__(parent)
        self.setWindowTitle("Output resources (bounded production distribution)")
        self.resize(720, 360)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["resource name", "resource id", "dist type", "params (JSON)", "low", "high"])
        self.table.horizontalHeader().setStretchLastSection(True)

        for n in connected_nodes:
            uid = node_uid(n)
            spec = current_map.get(uid, {})
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(n.name()))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(uid))
            combo = QtWidgets.QComboBox()
            combo.addItems(list(DISTRIBUTION_SPECS.keys()))
            combo.setCurrentText(spec.get("dist_type", "Normal"))
            self.table.setCellWidget(row, 2, combo)
            params = spec.get("params", {"mean": 1.0, "std": 0.0})
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(json.dumps(params)))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(str(spec.get("low", 0.0))))
            self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(str(spec.get("high", 1.0))))

        ok = QtWidgets.QPushButton("OK")
        cancel = QtWidgets.QPushButton("Cancel")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Bounds are required by sim.Bounded: low >= 0 and high must be finite."))
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def values(self) -> dict:
        result = {}
        for r in range(self.table.rowCount()):
            uid = self.table.item(r, 1).text()
            combo = self.table.cellWidget(r, 2)
            try:
                params = json.loads(self.table.item(r, 3).text())
            except Exception:
                params = {}
            result[uid] = {
                "dist_type": combo.currentText() if combo else "Normal",
                "params": params,
                "low": as_float(self.table.item(r, 4).text(), 0.0),
                "high": as_float(self.table.item(r, 5).text(), 1.0),
            }
        return result

    def accept(self):
        for r in range(self.table.rowCount()):
            low = as_float(self.table.item(r, 4).text(), 0.0)
            high = as_float(self.table.item(r, 5).text(), 1.0)
            if low < 0:
                qmessage(self, "Invalid bounds", "low must be >= 0.", QtWidgets.QMessageBox.Warning); return
            if high <= low or high == float("inf"):
                qmessage(self, "Invalid bounds", "high must be finite and greater than low.", QtWidgets.QMessageBox.Warning); return
            try:
                json.loads(self.table.item(r, 3).text())
            except Exception:
                qmessage(self, "Invalid params", "params must be valid JSON.", QtWidgets.QMessageBox.Warning); return
        super().accept()


class ResourceTaskConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent, task_node: ResourceTaskNode):
        super().__init__(parent)
        self.setWindowTitle("Resource Task config")
        self.resize(560, 520)
        self.task_node = task_node

        self.resources_scope = QtWidgets.QComboBox(); self.resources_scope.addItems(SCOPES_FOR_RESOURCES)
        self.resources_scope.setCurrentText(task_node.get_property("resources_scope"))
        self.operators_scope = QtWidgets.QComboBox(); self.operators_scope.addItems(SCOPES_FOR_OPERATORS)
        self.operators_scope.setCurrentText(task_node.get_property("operators_scope"))
        self.collector_type = QtWidgets.QComboBox(); self.collector_type.addItems(RESOURCE_COLLECTOR_TYPES)
        self.collector_type.setCurrentText(task_node.get_property("resource_collector_type"))

        self.min_carriers = _spin(task_node.get_property("min_carriers"))
        self.max_capacity = _spin(task_node.get_property("max_capacity"))
        self.min_carrier_capacity = _spin(task_node.get_property("min_carrier_capacity"))
        self.max_carrier_capacity = _spin(task_node.get_property("max_carrier_capacity"))
        self.contiguous_carriers = QtWidgets.QCheckBox("contiguous_carriers")
        self.contiguous_carriers.setChecked(bool(task_node.get_property("contiguous_carriers")))
        self.independent_carriers = QtWidgets.QCheckBox("independent_carriers")
        self.independent_carriers.setChecked(bool(task_node.get_property("independent_carriers")))

        btn_nt = QtWidgets.QPushButton("Edit non-transformed quantities...")
        btn_tr = QtWidgets.QPushButton("Edit transformed (proportion + salvageable)...")
        btn_out = QtWidgets.QPushButton("Edit output distributions...")
        btn_ops = QtWidgets.QPushButton("Edit operator quantities...")
        btn_sops = QtWidgets.QPushButton("Edit startup operator quantities...")
        ok = QtWidgets.QPushButton("OK"); cancel = QtWidgets.QPushButton("Cancel")

        form = QtWidgets.QFormLayout()
        form.addRow("resources_scope", self.resources_scope)
        form.addRow("operators_scope", self.operators_scope)
        form.addRow("resource_collector_type", self.collector_type)
        form.addRow("min_carrier_capacity", self.min_carrier_capacity)
        form.addRow("max_carrier_capacity", self.max_carrier_capacity)
        form.addRow("max_capacity (total slots)", self.max_capacity)
        form.addRow("min_carriers", self.min_carriers)
        form.addRow("", self.contiguous_carriers)
        form.addRow("", self.independent_carriers)
        form.addRow("non-transformed", btn_nt)
        form.addRow("transformed", btn_tr)
        form.addRow("outputs", btn_out)
        form.addRow("operators", btn_ops)
        form.addRow("startup operators", btn_sops)
        buttons = QtWidgets.QHBoxLayout(); buttons.addStretch(); buttons.addWidget(ok); buttons.addWidget(cancel)
        layout = QtWidgets.QVBoxLayout(self); layout.addLayout(form); layout.addLayout(buttons)

        btn_nt.clicked.connect(self.edit_non_transformed)
        btn_tr.clicked.connect(self.edit_transformed)
        btn_out.clicked.connect(self.edit_outputs)
        btn_ops.clicked.connect(self.edit_operators)
        btn_sops.clicked.connect(self.edit_startup_operators)
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)

    def edit_non_transformed(self):
        connected = connected_nodes_from_port(self.task_node, "non_transformed_resources", "input")
        current = get_property_json(self.task_node, "non_transformed_quantities", {})
        dlg = QuantityDialog(self, "Non-transformed quantities", connected, current, integer=False)
        if dlg.exec():
            set_property_json(self.task_node, "non_transformed_quantities", dlg.values())

    def edit_transformed(self):
        connected = connected_nodes_from_port(self.task_node, "transformed_resources", "input")
        current = get_property_json(self.task_node, "transformed_specs", {})
        dlg = TransformedSpecsDialog(self, connected, current)
        if dlg.exec():
            set_property_json(self.task_node, "transformed_specs", dlg.values())

    def edit_outputs(self):
        connected = connected_nodes_from_port(self.task_node, "resources_out", "input")
        current = get_property_json(self.task_node, "out_specs", {})
        dlg = OutSpecsDialog(self, connected, current)
        if dlg.exec():
            set_property_json(self.task_node, "out_specs", dlg.values())

    def edit_operators(self):
        connected = direct_resource_nodes(self.task_node, "operators")
        current = get_property_json(self.task_node, "operator_quantities", {})
        dlg = QuantityDialog(self, "Operator quantities (resources wired directly)", connected, current, integer=True)
        if dlg.exec():
            set_property_json(self.task_node, "operator_quantities", dlg.values())

    def edit_startup_operators(self):
        connected = direct_resource_nodes(self.task_node, "startup_operators")
        current = get_property_json(self.task_node, "startup_operator_quantities", {})
        dlg = QuantityDialog(self, "Startup operator quantities (resources wired directly)", connected, current, integer=True)
        if dlg.exec():
            set_property_json(self.task_node, "startup_operator_quantities", dlg.values())

    def accept(self):
        if self.min_carrier_capacity.value() > self.max_carrier_capacity.value():
            qmessage(self, "Invalid capacity", "min_carrier_capacity cannot exceed max_carrier_capacity.", QtWidgets.QMessageBox.Warning); return
        if self.max_carrier_capacity.value() > self.max_capacity.value():
            qmessage(self, "Invalid capacity", "max_carrier_capacity cannot exceed max_capacity.", QtWidgets.QMessageBox.Warning); return
        self.task_node.set_property("resources_scope", self.resources_scope.currentText())
        self.task_node.set_property("operators_scope", self.operators_scope.currentText())
        self.task_node.set_property("resource_collector_type", self.collector_type.currentText())
        self.task_node.set_property("min_carriers", self.min_carriers.value())
        self.task_node.set_property("max_capacity", self.max_capacity.value())
        self.task_node.set_property("min_carrier_capacity", self.min_carrier_capacity.value())
        self.task_node.set_property("max_carrier_capacity", self.max_carrier_capacity.value())
        self.task_node.set_property("contiguous_carriers", self.contiguous_carriers.isChecked())
        self.task_node.set_property("independent_carriers", self.independent_carriers.isChecked())
        super().accept()


# ============================================================
# Main window
# ============================================================

# ============================================================
# Stage 1: reusable distribution/function widget + registries
# (Resources / Operators / Shifts). Distributions, resources and
# operators are no longer cards; they live in menus and are picked
# by name inside the cards that use them.
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


class _IntervalRow(QtWidgets.QWidget):
    def __init__(self, start=480.0, end=1020.0, on_remove=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.start = QtWidgets.QLineEdit(str(start)); self.start.setMaximumWidth(70)
        self.end = QtWidgets.QLineEdit(str(end)); self.end.setMaximumWidth(70)
        lay.addWidget(QtWidgets.QLabel("start:")); lay.addWidget(self.start)
        lay.addWidget(QtWidgets.QLabel("end:")); lay.addWidget(self.end)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24)
        if on_remove:
            rm.clicked.connect(lambda: on_remove(self))
        lay.addWidget(rm); lay.addStretch(1)

    def data(self):
        return {"start": as_float(self.start.text()), "end": as_float(self.end.text())}


class _DayRow(QtWidgets.QWidget):
    """One weekday: a working toggle + a list of shift intervals (minutes 0..1440)."""

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
        lay.addWidget(QtWidgets.QLabel("Shifts per weekday (times in minutes from midnight, 0–1440):"))
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
# Stage 2: selection widgets that reference the registries
# ============================================================

POLICY_OPTIONS = {
    "pending_carriers_pre_flexible_shutdowns": (["AbortPendingCarriers", "WaitForCarriers", "AbortOrWaitForCarriers"], "AbortPendingCarriers"),
    "pending_carrier_pre_task_shift_end": (["AbortPendingCarriers", "WaitForCarriers", "AbortOrWaitForCarriers"], "AbortPendingCarriers"),
    "operator_shift_constraint": (["ConstrainedByShift", "NotConstrainedByShift"], "ConstrainedByShift"),
    "task_shift_constraint": (["ConstrainedByShift", "NotConstrainedByShift"], "ConstrainedByShift"),
    "operators_self_conscious": (["Conscious", "Unconscious"], "Conscious"),
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

    def __init__(self, resource_names, value_label="quantity", entries=None, parent=None):
        super().__init__(parent)
        self._names = list(resource_names)
        self._label = value_label
        self._rows = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._host = QtWidgets.QWidget()
        self._vl = QtWidgets.QVBoxLayout(self._host)
        self._vl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._host)
        add = QtWidgets.QPushButton(f"+ resource")
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
                out.append({"resource": combo.currentText(), "value": as_float(edit.text())})
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
        picker = ResourcePickerWidget(self._names, value_label="count",
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
    """The five task protocols with their defaults; AbortOrWaitForCarriers exposes
    a tolerance_fraction. Value: {policy_name: {"type", ...params}}."""

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        value = value or {}
        form = QtWidgets.QFormLayout(self)
        self._combos = {}
        self._tol = {}
        for name, (options, default) in POLICY_OPTIONS.items():
            row = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            combo = QtWidgets.QComboBox(); combo.addItems(options)
            combo.setCurrentText(value.get(name, {}).get("type", default))
            h.addWidget(combo)
            tol = QtWidgets.QLineEdit(str(value.get(name, {}).get("tolerance_fraction", 0.5)))
            tol.setMaximumWidth(60)
            tol_lbl = QtWidgets.QLabel("tolerance:")
            h.addWidget(tol_lbl); h.addWidget(tol); h.addStretch(1)
            self._combos[name] = combo
            self._tol[name] = (tol_lbl, tol)
            def _upd(_=None, n=name):
                on = self._combos[n].currentText() == "AbortOrWaitForCarriers"
                self._tol[n][0].setVisible(on); self._tol[n][1].setVisible(on)
            combo.currentTextChanged.connect(_upd)
            _upd()
            form.addRow(name, row)

    def get_value(self):
        out = {}
        for name, combo in self._combos.items():
            t = combo.currentText()
            entry = {"type": t}
            if t == "AbortOrWaitForCarriers":
                entry["tolerance_fraction"] = as_float(self._tol[name][1].text(), 0.5)
            out[name] = entry
        return out


# ============================================================
# Stage 2: card menus (dialogs). They read/write node properties and
# reference the window's registries (models/resources/operators/shifts).
# ============================================================

def _names(reg):
    return [e.get("name", "") for e in reg if e.get("name")]


def _leaf_model_names(model_registry):
    parents = {m.get("parent") for m in model_registry}
    return [m["name"] for m in model_registry if m["name"] not in parents]


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
        lay.addWidget(QtWidgets.QLabel("intervals:"))
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
        lay.addWidget(QtWidgets.QLabel("valid models (selecting a model selects its children):"))
        self.models = ModelTreeWidget(model_registry, checked=get_property_json(node, "valid_models", []))
        lay.addWidget(self.models)
        form = QtWidgets.QFormLayout()
        self.capacity = InfFloatWidget(node.get_property("capacity") if node.has_property("capacity") else "inf")
        form.addRow("capacity", self.capacity)
        lay.addLayout(form)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "valid_models", self.models.checked_models())
        self.node.set_property("capacity", self.capacity.get_value())


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
        form = QtWidgets.QFormLayout()
        for b in self._buffers:
            bid = node_uid(b)
            tf = TimeFunctionWidget(current.get(bid, {"kind": "constant", "value": 0.0}))
            self._widgets[bid] = tf
            form.addRow(b.name(), tf)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("(probabilities are checked to sum to 1 when sampled)"))
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def apply(self):
        set_property_json(self.node, "buffer_probs", {bid: w.get_value() for bid, w in self._widgets.items()})


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


def _carrier_common_tab(node, operator_names, shift_names, collector_types):
    """Build the shared 'operators / carriers / policies / shifts' tabs for a task node.
    Returns (list-of-(label, widget), accessor-dict)."""
    tabs = []
    acc = {}

    # operators & durations
    t1 = QtWidgets.QWidget(); f1 = QtWidgets.QVBoxLayout(t1)
    f1.addWidget(QtWidgets.QLabel("startup duration:")); acc["startup_duration"] = SamplerWidget(get_property_json(node, "startup_duration", None)); f1.addWidget(acc["startup_duration"])
    f1.addWidget(QtWidgets.QLabel("loading duration:")); acc["loading_duration"] = SamplerWidget(get_property_json(node, "loading_duration", None)); f1.addWidget(acc["loading_duration"])
    f1.addWidget(QtWidgets.QLabel("operators (alternatives):")); acc["operators"] = AlternativesWidget(operator_names, get_property_json(node, "operators", [])); f1.addWidget(acc["operators"])
    f1.addWidget(QtWidgets.QLabel("loading operators:")); acc["loading_operators"] = AlternativesWidget(operator_names, get_property_json(node, "loading_operators", [])); f1.addWidget(acc["loading_operators"])
    f1.addWidget(QtWidgets.QLabel("startup operators:")); acc["startup_operators"] = AlternativesWidget(operator_names, get_property_json(node, "startup_operators", [])); f1.addWidget(acc["startup_operators"])
    tabs.append(("Operators & durations", _scroll(t1)))

    # carriers & scopes
    t2 = QtWidgets.QWidget(); f2 = QtWidgets.QFormLayout(t2)
    acc["operator_scope"] = QtWidgets.QComboBox(); acc["operator_scope"].addItems(["PER_BATCH", "PER_TASK"]); acc["operator_scope"].setCurrentText(node.get_property("operator_scope"))
    acc["resource_scope"] = QtWidgets.QComboBox(); acc["resource_scope"].addItems(["PER_UNIT", "PER_BATCH"]); acc["resource_scope"].setCurrentText(node.get_property("resource_scope"))
    f2.addRow("operator scope", acc["operator_scope"]); f2.addRow("resource scope", acc["resource_scope"])
    if collector_types is not None:
        acc["collector_type"] = QtWidgets.QComboBox(); acc["collector_type"].addItems(collector_types); acc["collector_type"].setCurrentText(node.get_property("collector_type") if node.has_property("collector_type") else collector_types[0])
        f2.addRow("collector type", acc["collector_type"])
    for key, default in (("min_carriers", 1), ("max_capacity", 1.0), ("timeout", 1e9), ("priority", 5)):
        acc[key] = QtWidgets.QLineEdit(str(node.get_property(key))); f2.addRow(key, acc[key])
    acc["contiguous_carriers"] = QtWidgets.QCheckBox(); acc["contiguous_carriers"].setChecked(bool(node.get_property("contiguous_carriers"))); f2.addRow("contiguous carriers", acc["contiguous_carriers"])
    acc["independent_carriers"] = QtWidgets.QCheckBox(); acc["independent_carriers"].setChecked(bool(node.get_property("independent_carriers"))); f2.addRow("independent carriers", acc["independent_carriers"])
    tabs.append(("Carriers & scopes", t2))

    # policies & shifts
    t3 = QtWidgets.QWidget(); f3 = QtWidgets.QVBoxLayout(t3)
    acc["policies"] = PoliciesWidget(get_property_json(node, "policies", {})); f3.addWidget(acc["policies"])
    f3.addWidget(QtWidgets.QLabel("task shifts:")); acc["task_shifts"] = ShiftPickerWidget(shift_names, get_property_json(node, "task_shifts", [])); f3.addWidget(acc["task_shifts"])
    tabs.append(("Policies & shifts", _scroll(t3)))
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
        # resources tab
        t0 = QtWidgets.QWidget(); f0 = QtWidgets.QVBoxLayout(t0)
        f0.addWidget(QtWidgets.QLabel("duration:")); self.duration = SamplerWidget(get_property_json(node, "duration", None)); f0.addWidget(self.duration)
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
        f1 = QtWidgets.QFormLayout()
        self.min_cc = QtWidgets.QLineEdit(str(node.get_property("min_carrier_capacity")))
        self.max_cc = QtWidgets.QLineEdit(str(node.get_property("max_carrier_capacity")))
        self.rct = QtWidgets.QComboBox(); self.rct.addItems(RESOURCE_COLLECTOR_TYPES); self.rct.setCurrentText(node.get_property("resource_collector_type"))
        f1.addRow("min carrier capacity", self.min_cc); f1.addRow("max carrier capacity", self.max_cc); f1.addRow("collector type", self.rct)
        f0.addLayout(f1)
        tabs.addTab(_scroll(t0), "Resources")
        common, self.acc = _carrier_common_tab(node, _names(win.operator_registry), _names(win.shift_registry), None)
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
        dist = SamplerWidget(entry.get("distribution")); bl.addRow("amount (≥0)", dist)
        rm = QtWidgets.QPushButton("×"); rm.setMaximumWidth(24); bl.addRow(rm)
        rec = (box, combo, dist); rm.clicked.connect(lambda: self._remove(rec))
        self._rows.append(rec); self._vl.addWidget(box)

    def _remove(self, rec):
        if rec in self._rows:
            self._rows.remove(rec); rec[0].setParent(None); rec[0].deleteLater()

    def value(self):
        return [{"resource": c.currentText(), "distribution": d.get_value(), "lowerbound": 0.0, "upperbound": "inf"}
                for _, c, d in self._rows if c.currentText()]


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

        self.graph.register_nodes([
            ShutdownsNode,
            HardBufferNode,
            SoftBufferNode,
            FirstTaskNode,
            TaskNode,
            ResourceTaskNode,
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
        self.statusBar().showMessage("Ready. Use the Create menu to add nodes.")

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        act_new = file_menu.addAction("New")
        act_import = file_menu.addAction("Import clean JSON (add)...")
        act_export = file_menu.addAction("Export clean JSON...")
        act_new.triggered.connect(self.new_graph)
        act_import.triggered.connect(self.import_clean_json_dialog)
        act_export.triggered.connect(self.export_clean_json_dialog)

        model_menu = self.menuBar().addMenu("Models")
        model_menu.addAction("Edit models...").triggered.connect(self.edit_models)

        registries_menu = self.menuBar().addMenu("Registries")
        registries_menu.addAction("Edit resources...").triggered.connect(self.edit_resources)
        registries_menu.addAction("Edit operators...").triggered.connect(self.edit_operators)
        registries_menu.addAction("Edit shifts...").triggered.connect(self.edit_shifts)

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
            ("Buffer", "simulation.flow.HardBufferNode"),
            ("Router", "simulation.flow.SoftBufferNode"),
            ("Source (PieceGenerator)", "simulation.flow.FirstTaskNode"),
            ("Piece Task", "simulation.flow.TaskNode"),
            ("Resource Task", "simulation.flow.ResourceTaskNode"),
            ("Breakdown", "simulation.flow.BreakdownNode"),
            ("Monitor", "simulation.flow.MonitorNode"),
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

    def place_clear_of_existing(self, new_nodes, existing_bounds, padding=160.0, direction="right"):
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
        else:
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
        for attempt in (lambda: self.graph.center_on(nodes), lambda: self.graph.fit_to_selection()):
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

    def on_node_double_clicked(self, node):
        kind = node_kind(node)
        dlg = None
        if kind == "Shutdowns":
            dlg = ShutdownsMenuDialog(self, node)
        elif kind == "HardBuffer":
            dlg = BufferMenuDialog(self, node, self.model_registry)
        elif kind == "SoftBuffer":
            dlg = RouterMenuDialog(self, node)
        elif kind == "FirstTask":
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

    def validate_graph(self) -> List[str]:
        problems = []
        for c in self.connections_clean():
            if not is_valid_connection(c["from_kind"], c["from_port"], c["to_kind"], c["to_port"]):
                problems.append(f"Invalid connection: {c['from_kind']}.{c['from_port']} -> {c['to_kind']}.{c['to_port']}")

        for node in self.all_nodes():
            kind = node_kind(node)
            if kind == "Task":
                if not connected_refs_from_port(node, "bufs_in", "input"):
                    problems.append(f"Piece Task '{node.name()}' has no input buffers.")
                if not get_input_ref(node, "startup_duration"):
                    problems.append(f"Piece Task '{node.name()}' has no startup_duration distribution.")
                mc = get_property_json(node, "models_configs", [])
                if not mc:
                    problems.append(f"Piece Task '{node.name()}' has no model configs.")
                else:
                    for m in mc:
                        if not m.get("duration"):
                            problems.append(f"Piece Task '{node.name()}' model '{m.get('model')}' has no duration.")

            elif kind == "ResourceTask":
                if not get_input_ref(node, "task_duration"):
                    problems.append(f"Resource Task '{node.name()}' has no task_duration distribution.")
                if not get_input_ref(node, "startup_duration"):
                    problems.append(f"Resource Task '{node.name()}' has no startup_duration distribution.")
                if not connected_refs_from_port(node, "resources_out", "input"):
                    problems.append(f"Resource Task '{node.name()}' has no output resources.")

            elif kind == "Breakdown":
                if not get_input_ref(node, "task"):
                    problems.append(f"Breakdown '{node.name()}' is not attached to a Task.")
                if not get_input_ref(node, "mttr"):
                    problems.append(f"Breakdown '{node.name()}' has no mttr distribution.")

            elif kind == "FirstTask":
                mg = get_property_json(node, "models_goals", [])
                if not mg:
                    problems.append(f"Source '{node.name()}' has no model goals.")
                if as_float(node.get_property("working_hours"), 0.0) <= 0:
                    problems.append(f"Source '{node.name()}' has non-positive working_hours.")
                if not get_output_refs(node, "bufs_out"):
                    problems.append(f"Source '{node.name()}' has no outlets.")

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
                    problems.append(f"SoftBuffer '{node.name()}' probabilities sum to {total}, not 1.")

            elif kind == "RestockableResource":
                if not get_input_ref(node, "delivery_duration"):
                    problems.append(f"RestockableResource '{node.name()}' has no delivery_duration distribution.")
                if not get_input_ref(node, "order_duration"):
                    problems.append(f"RestockableResource '{node.name()}' has no order_duration distribution.")

            elif kind == "AndGroup":
                if not direct_resource_nodes(node, "members"):
                    problems.append(f"AND group '{node.name()}' has no resources wired into 'members'.")

            elif kind == "OrGroup":
                if not connected_nodes_from_port(node, "groups", "input"):
                    problems.append(f"OR group '{node.name()}' has no alternatives wired into 'groups'.")
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
        base_x, base_y = self.current_view_center()
        node = self.graph.create_node(node_type)
        node.set_name(name)
        self.set_node_position_safe(node, base_x + x, base_y + y)
        return node

    def existing_model_names(self) -> List[str]:
        if not hasattr(self, "model_registry"):
            self.model_registry = []
        return [m.get("name") for m in self.model_registry if m.get("name")]

    def ensure_template_model_names(self) -> List[str]:
        names = self.existing_model_names()
        if names:
            return names
        self.model_registry.append({"name": "DummyModel", "parent": None})
        return ["DummyModel"]

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

    def add_task_template(self):
        existing_bounds = self.content_bounds()
        default_model = self.ensure_template_model_names()[0]

        task_duration = self.create_distribution_template("Task Duration", -760, -180, 1.0)
        startup_duration = self.create_distribution_template("Startup Duration", -760, -40, 0.0)

        restockable = self.make_node("simulation.flow.RestockableResourceNode", "Restockable Resource", -760, 140)
        set_text_prop(restockable, "desired_capacity", 100.0)
        set_text_prop(restockable, "threshold", 20.0)
        order_duration = self.create_distribution_template("Order Duration", -1120, 60, 0.0)
        delivery_duration = self.create_distribution_template("Delivery Duration", -1120, 220, 10.0)

        operator_resource = self.create_resource_template("Operator", -760, 360, 1.0)
        startup_operator = self.create_resource_template("Startup Operator", -760, 500, 1.0)
        shutdowns = self.make_node("simulation.flow.ShutdownsNode", "Shutdowns", -760, 640)

        task = self.make_node("simulation.flow.TaskNode", "Piece Task", -300, 160)
        mttr = self.create_distribution_template("MTTR", 80, 260, 10.0)
        breakdown = self.make_node("simulation.flow.BreakdownNode", "Breakdown", 440, 160)

        # Wiring.
        connect_ports_by_name(order_duration, "distribution", restockable, "order_duration")
        connect_ports_by_name(delivery_duration, "distribution", restockable, "delivery_duration")
        connect_ports_by_name(task_duration, "distribution", task, "durations")
        connect_ports_by_name(startup_duration, "distribution", task, "startup_duration")
        connect_ports_by_name(restockable, "resource", task, "resources")
        connect_ports_by_name(operator_resource, "resource", task, "operators")
        connect_ports_by_name(startup_operator, "resource", task, "startup_operators")
        connect_ports_by_name(shutdowns, "shutdowns", task, "shutdowns")
        connect_ports_by_name(task, "task_ref", breakdown, "task")
        connect_ports_by_name(mttr, "distribution", breakdown, "mttr")

        # Defaults.
        self.set_property_safe(task, "resources_scope", "PER_BATCH")
        self.set_property_safe(task, "operators_scope", "PER_BATCH")
        self.set_property_safe(task, "min_carriers", 1)
        self.set_property_safe(task, "max_capacity", 1)
        self.set_property_safe(task, "min_carrier_capacity", 1)
        self.set_property_safe(task, "max_carrier_capacity", 1)
        self.set_property_safe(task, "contiguous_carriers", False)
        self.set_property_safe(task, "collector_type", "NON_DISCRIMINATING_GREEDY")
        self.set_property_safe(task, "independent_carriers", False)
        self.set_json_property_safe(task, "models_configs", [{
            "model": default_model,
            "duration": node_uid(task_duration),
            "resource_groups": [{node_uid(restockable): 1.0}],
        }])
        self.set_json_property_safe(task, "operator_quantities", {node_uid(operator_resource): 1})
        self.set_json_property_safe(task, "startup_operator_quantities", {node_uid(startup_operator): 1})

        group_nodes = [order_duration, delivery_duration, task_duration, startup_duration,
                       restockable, operator_resource, startup_operator, shutdowns, task, mttr, breakdown]
        self.place_clear_of_existing(group_nodes, existing_bounds)
        backdrop = self.add_backdrop_for_nodes(group_nodes, "Piece Task Template")
        self.focus_on_nodes(group_nodes + ([backdrop] if backdrop else []))
        self.statusBar().showMessage("Added Piece Task template.")

    def add_operator_alternatives_template(self):
        """Demonstrate the OR-of-ANDs wiring: (Operator A & Operator B) OR Operator C.

        The OR card's 'out' is left free for the user to wire into a task's
        'operators' (or 'startup_operators') port.
        """
        existing_bounds = self.content_bounds()
        op_a = self.create_resource_template("Operator A", -520, -80, 1.0)
        op_b = self.create_resource_template("Operator B", -520, 60, 1.0)
        op_c = self.create_resource_template("Operator C", -520, 220, 1.0)
        and_card = self.make_node("simulation.flow.AndGroupNode", "AND (A & B)", -200, -10)
        or_card = self.make_node("simulation.flow.OrGroupNode", "OR (any alternative)", 120, 80)

        connect_ports_by_name(op_a, "resource", and_card, "members")
        connect_ports_by_name(op_b, "resource", and_card, "members")
        connect_ports_by_name(and_card, "group", or_card, "groups")
        connect_ports_by_name(op_c, "resource", or_card, "groups")

        self.set_json_property_safe(and_card, "member_quantities", {node_uid(op_a): 1, node_uid(op_b): 1})
        self.set_json_property_safe(or_card, "member_quantities", {node_uid(op_c): 1})

        group_nodes = [op_a, op_b, op_c, and_card, or_card]
        self.place_clear_of_existing(group_nodes, existing_bounds)
        backdrop = self.add_backdrop_for_nodes(group_nodes, "Operator Alternatives:  (A & B) OR C")
        self.focus_on_nodes(group_nodes + ([backdrop] if backdrop else []))
        self.statusBar().showMessage(
            "Added operator alternatives. Wire the OR card's 'out' into a task's 'operators' port.")

    def add_first_task_template(self):
        existing_bounds = self.content_bounds()
        first_task = self.make_node("simulation.flow.FirstTaskNode", "Source (PieceGenerator)", -120, 80)
        set_text_prop(first_task, "working_hours", 480.0)
        self.set_json_property_safe(first_task, "models_goals",
                                    [{"model": m, "goal": 1} for m in self.ensure_template_model_names()])
        group_nodes = [first_task]
        self.place_clear_of_existing(group_nodes, existing_bounds)
        backdrop = self.add_backdrop_for_nodes(group_nodes, "Source Template")
        self.focus_on_nodes(group_nodes + ([backdrop] if backdrop else []))
        self.statusBar().showMessage("Added Source (PieceGenerator) template.")

    def node_type_from_kind(self, kind: str) -> str:
        mapping = {
            "Distribution": "simulation.flow.DistributionNode",
            "Interval": "simulation.flow.IntervalNode",
            "Shutdowns": "simulation.flow.ShutdownsNode",
            "Resource": "simulation.flow.ResourceNode",
            "RestockableResource": "simulation.flow.RestockableResourceNode",
            "HardBuffer": "simulation.flow.HardBufferNode",
            "SoftBuffer": "simulation.flow.SoftBufferNode",
            "FirstTask": "simulation.flow.FirstTaskNode",
            "Task": "simulation.flow.TaskNode",
            "ResourceTask": "simulation.flow.ResourceTaskNode",
            "Breakdown": "simulation.flow.BreakdownNode",
            "Monitor": "simulation.flow.MonitorNode",
            "AndGroup": "simulation.flow.AndGroupNode",
            "OrGroup": "simulation.flow.OrGroupNode",
        }
        kind = LEGACY_KIND_ALIASES.get(kind, kind)
        if kind not in mapping:
            raise ValueError(f"Unknown node kind in JSON: {kind}")
        return mapping[kind]

    def apply_clean_json_to_node(self, node, node_data: dict):
        kind = node_data.get("kind")
        kind = LEGACY_KIND_ALIASES.get(kind, kind)

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

        elif kind == "Shutdowns":
            self.set_property_safe(node, "shutdown_type", node_data.get("shutdown_type", "NON_FLEXIBLE"))

        elif kind == "Resource":
            set_text_prop(node, "desired_capacity", node_data.get("capacity", 1.0))
            set_bool_prop(node, "anonymous", False)

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
                bid = item.get("buffer")
                if bid:
                    prob_map[bid] = item.get("probability", 0.0)
            self.set_json_property_safe(node, "buffer_probs", prob_map)
            self.set_property_safe(node, "buffer_role", node_data.get("buffer_role", "Normal"))

        elif kind == "FirstTask":
            self.set_json_property_safe(node, "models_goals", node_data.get("models_goals", []))
            set_text_prop(node, "working_hours", node_data.get("working_hours", 480.0))

        elif kind == "Monitor":
            stats = node_data.get("stats", {}) or {}
            for key, _label, default in MONITOR_STATS:
                set_bool_prop(node, key, bool(stats.get(key, default)))

        elif kind == "Breakdown":
            fr = node_data.get("failure_rate", {}) or {}
            for k, d in (("A", 0.0), ("tau", 1.0), ("c", 0.01), ("beta", 1.0), ("eta", 1.0)):
                set_text_prop(node, k, fr.get(k, d))

        elif kind == "Task":
            models_configs = []
            for mc in node_data.get("models_configs", []):
                models_configs.append({
                    "model": mc.get("model"),
                    "duration": mc.get("duration"),
                    "resource_groups": import_resource_groups(mc.get("resources")),
                })
            self.set_json_property_safe(node, "models_configs", models_configs)
            self.set_property_safe(node, "resources_scope", node_data.get("resources_scope", "PER_BATCH"))
            self.set_property_safe(node, "operators_scope", node_data.get("operators_scope", "PER_BATCH"))
            self.set_property_safe(node, "min_carrier_capacity", node_data.get("min_carrier_capacity", 1))
            self.set_property_safe(node, "max_carrier_capacity", node_data.get("max_carrier_capacity", 1))
            self.set_property_safe(node, "max_capacity", node_data.get("max_capacity", 1))
            self.set_property_safe(node, "min_carriers", node_data.get("min_carriers", 1))
            self.set_property_safe(node, "contiguous_carriers", node_data.get("contiguous_carriers", False))
            ct = node_data.get("collector_type") or _LEGACY_COLLECTOR.get(node_data.get("batch_collector_type")) \
                or _LEGACY_COLLECTOR.get(node_data.get("batch_collector"), "NON_DISCRIMINATING_GREEDY")
            self.set_property_safe(node, "collector_type", ct)
            self.set_property_safe(node, "independent_carriers", node_data.get("independent_carriers", False))
            self.set_json_property_safe(node, "operator_quantities", operator_quantities_from_export(node_data.get("operators", [])))
            self.set_json_property_safe(node, "startup_operator_quantities", operator_quantities_from_export(node_data.get("startup_operators", [])))

        elif kind == "ResourceTask":
            self.set_property_safe(node, "resources_scope", node_data.get("resources_scope", "PER_BATCH"))
            self.set_property_safe(node, "operators_scope", node_data.get("operators_scope", "PER_BATCH"))
            self.set_property_safe(node, "resource_collector_type", node_data.get("resource_collector_type", "GREEDY"))
            self.set_property_safe(node, "min_carrier_capacity", node_data.get("min_carrier_capacity", 1))
            self.set_property_safe(node, "max_carrier_capacity", node_data.get("max_carrier_capacity", 1))
            self.set_property_safe(node, "max_capacity", node_data.get("max_capacity", 1))
            self.set_property_safe(node, "min_carriers", node_data.get("min_carriers", 1))
            self.set_property_safe(node, "contiguous_carriers", node_data.get("contiguous_carriers", False))
            self.set_property_safe(node, "independent_carriers", node_data.get("independent_carriers", False))
            ntq = {it["resource"]: it.get("quantity", 1.0) for it in node_data.get("non_transformed_resources", []) if it.get("resource")}
            self.set_json_property_safe(node, "non_transformed_quantities", ntq)
            tspecs = {}
            for it in node_data.get("transformed_resources_salvageable", []):
                if it.get("resource"):
                    tspecs[it["resource"]] = {"proportion": it.get("proportion", 1.0), "salvageable": it.get("salvageable", True)}
            self.set_json_property_safe(node, "transformed_specs", tspecs)
            ospecs = {}
            for it in node_data.get("resources_out_distr", []):
                if it.get("resource"):
                    dist = it.get("distribution", {}) or {}
                    ospecs[it["resource"]] = {
                        "dist_type": dist.get("type", "Normal"),
                        "params": dist.get("params", {"mean": 1.0, "std": 0.0}),
                        "low": it.get("low", 0.0),
                        "high": it.get("high", 1.0),
                    }
            self.set_json_property_safe(node, "out_specs", ospecs)
            self.set_json_property_safe(node, "operator_quantities", operator_quantities_from_export(node_data.get("operators", [])))
            self.set_json_property_safe(node, "startup_operator_quantities", operator_quantities_from_export(node_data.get("startup_operators", [])))

        elif kind == "AndGroup":
            quantities = {it["resource"]: it.get("quantity", 1)
                          for it in node_data.get("members", []) if isinstance(it, dict) and it.get("resource")}
            self.set_json_property_safe(node, "member_quantities", quantities)

        elif kind == "OrGroup":
            self.set_json_property_safe(node, "member_quantities", node_data.get("member_quantities", {}) or {})

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

    def import_clean_json(self, data: dict):
        data = self._remap_ids(data)
        data = self._offset_imported_positions(data)
        self._merge_models(data.get("models", []))

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
