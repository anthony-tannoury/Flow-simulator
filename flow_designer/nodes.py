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
    from .ui_helpers import PIECE_POLICY_OPTIONS, POLICY_OPTIONS, PORT_COLORS, SHUTDOWN_TYPES, add_combo_input, as_float, as_int, connected_refs_from_port, default_policies, get_output_refs, get_property_json, new_uid, node_uid, sentence_case, to_canonical
except ImportError:
    from ui_helpers import PIECE_POLICY_OPTIONS, POLICY_OPTIONS, PORT_COLORS, SHUTDOWN_TYPES, add_combo_input, as_float, as_int, connected_refs_from_port, default_policies, get_output_refs, get_property_json, new_uid, node_uid, sentence_case, to_canonical


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
    NODE_NAME = "Shutdowns"
    kind = "Shutdowns"
    color = (125, 80, 130)

    def __init__(self):
        super().__init__()
        self.add_output("shutdowns", color=PORT_COLORS["shutdown"])

        add_combo_input(self, "shutdown_type", "Type",
                        [sentence_case(t) for t in SHUTDOWN_TYPES], sentence_case("NON_FLEXIBLE"))
        self.create_property("mode", "custom")
        self.create_property("intervals", "[]")
        self.create_property("generator", "{}")

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
        self.create_property("buffer_type", "PASSAGE")

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
        self.create_property("buffer_probs", "{}")

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
    NODE_NAME = "Piece generator"
    kind = "PieceGenerator"
    color = (145, 80, 80)

    def __init__(self):
        super().__init__()
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.create_property("shifts", "[]")

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
    NODE_NAME = "Piece task"
    kind = "Task"
    color = (150, 90, 60)

    def __init__(self):
        super().__init__()
        self.add_input("bufs_in", multi_input=True, color=PORT_COLORS["buffer"])
        self.add_input("shutdowns", multi_input=True, color=PORT_COLORS["shutdown"])
        self.add_input("breakdowns", multi_input=True, color=PORT_COLORS["breakdown"])
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])


        self.create_property("models_configs", "[]")
        self.create_property("startup_duration", "")
        self.create_property("loading_duration", "")
        self.create_property("operators", "[]")
        self.create_property("loading_operators", "[]")
        self.create_property("startup_operators", "[]")
        self.create_property("task_shifts", "[]")
        self.create_property("policies", json.dumps(default_policies(PIECE_POLICY_OPTIONS)))
        self.create_property("operator_scope", "PER_BATCH")
        self.create_property("resource_scope", "PER_BATCH")
        self.create_property("min_carriers", 1)
        self.create_property("max_capacity", 1.0)
        self.create_property("contiguous_carriers", False)
        self.create_property("independent_carriers", False)
        self.create_property("timeout", 1000000000.0)
        self.create_property("priority", 5)
        self.create_property("admin", False)
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
            "timeout": self.get_property("timeout"),
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
    NODE_NAME = "Resource task"
    kind = "ResourceTask"
    color = (150, 120, 60)

    def __init__(self):
        super().__init__()
        self.add_input("shutdowns", multi_input=True, color=PORT_COLORS["shutdown"])
        self.add_input("breakdowns", multi_input=True, color=PORT_COLORS["breakdown"])

        self.create_property("non_transformed_resources", "[]")
        self.create_property("transformed_resources", "[]")
        self.create_property("resources_out", "[]")
        self.create_property("duration", "")
        self.create_property("startup_duration", "")
        self.create_property("loading_duration", "")
        self.create_property("operators", "[]")
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
        self.create_property("admin", False)

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
            "timeout": self.get_property("timeout"),
            "priority": as_int(self.get_property("priority"), 5),
            "admin": bool(self.get_property("admin")),
            "shutdowns": connected_refs_from_port(self, "shutdowns", "input"),
            "breakdowns": connected_refs_from_port(self, "breakdowns", "input"),
            "position": [self.x_pos(), self.y_pos()],
        }


class BreakdownNode(SimNode):
    NODE_NAME = "Breakdown"
    kind = "Breakdown"
    color = (150, 65, 85)

    def __init__(self):
        super().__init__()
        self.add_output("breakdown", color=PORT_COLORS["breakdown"])
        self.add_output("bufs_out", multi_output=True, color=PORT_COLORS["task"])
        self.create_property("mtbf", "{}")
        self.create_property("mttr", "")

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
