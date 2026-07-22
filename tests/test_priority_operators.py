import collections
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONST = lambda v: {"kind": "constant", "value": v}
DIST = lambda v: {"dist_type": "Constant", "params": {"value": CONST(v)}}

POLICIES = {
    "pending_carriers_pre_flexible_shutdowns": {"type": "AbortPendingCarriers"},
    "pending_carrier_pre_task_shift_end": {"type": "AbortPendingCarriers"},
    "operator_shift_constraint": {"type": "NotConstrainedByShift"},
    "task_shift_constraint": {"type": "NotConstrainedByShift"},
    "operators_self_conscious": {"type": "Unconscious"},
    "piece_exit_order": {"type": "FirstInFirstOut"},
    "batch_model_choice": {"type": "MostPresent"},
}


def task(tid, name, prio, model, buf):
    return {
        "id": tid, "kind": "Task", "name": name, "enabled": True,
        "models_configs": [{"model": model, "duration": DIST(10.0), "resources": [],
                            "min_carrier_capacity": 1, "max_carrier_capacity": 1}],
        "startup_duration": DIST(0.0), "loading_duration": DIST(5.0),
        "operators": [[{"operator": "op1", "count": 1}]],
        "loading_operators": [[{"operator": "op1", "count": 1}]],
        "startup_operators": [],
        "task_shifts": ["sh1"], "policies": POLICIES,
        "operator_scope": "PER_BATCH", "resource_scope": "PER_BATCH",
        "min_carriers": 1, "max_capacity": 3,
        "contiguous_carriers": False, "independent_carriers": True,
        "timeout": "inf", "priority": prio, "admin": False,
        "collector_type": "NON_DISCRIMINATING_GREEDY",
        "bufs_in": [buf], "bufs_out": ["bout"],
        "shutdowns": [], "breakdowns": [],
        "position": [0, 0],
    }


def contention_flow(tmp_path, low_priority):
    flow = {
        "editor": {"name": "test", "version": "0", "format": "clean-json"},
        "models": [{"id": "m1", "name": "M1", "parent": None},
                   {"id": "m2", "name": "M2", "parent": None}],
        "resources": [],
        "operators": [{"id": "op1", "name": "POOL", "capacity": 1, "shifts": ["sh1"],
                       "productivity": DIST(1.0)}],
        "closing_days": [],
        "shifts": [{"id": "sh1", "name": "always", "mode": "custom",
                    "custom_intervals": [{"start": "01-01-2026 00:00",
                                          "end": "01-03-2026 00:00"}],
                    "days_off": []}],
        "stopping_criterion": {"type": "ByPiecesProduced", "timeout": 43200.0, "gap": 1.0,
                               "grace_period": 0.0,
                               "models_goals": [{"model": "m1", "goal": 5000},
                                                {"model": "m2", "goal": 5000}]},
        "start_date": "01-01-2026 00:00",
        "seed": 0,
        "nodes": [
            {"id": "gen", "kind": "PieceGenerator", "name": "Gen", "enabled": True,
             "shifts": ["sh1"], "outlets": ["binA", "binB"], "position": [0, 0]},
            {"id": "binA", "kind": "Buffer", "name": "InA", "enabled": True,
             "buffer_type": "PASSAGE", "valid_models": ["m1"], "position": [0, 0]},
            {"id": "binB", "kind": "Buffer", "name": "InB", "enabled": True,
             "buffer_type": "PASSAGE", "valid_models": ["m2"], "position": [0, 0]},
            task("thigh", "HIGH", 10, "m1", "binA"),
            task("tlow", "LOW", low_priority, "m2", "binB"),
            {"id": "bout", "kind": "Buffer", "name": "Out", "enabled": True,
             "buffer_type": "EXIT", "valid_models": ["m1", "m2"], "position": [0, 0]},
        ],
        "connections": [],
    }
    path = tmp_path / f"prio_{low_priority}.json"
    path.write_text(json.dumps(flow))
    return str(path)


def run_exits(fresh_parser, path):
    p, env = fresh_parser(path)
    p.load_all()
    crit = p.stopping_criterion
    while not crit.done():
        env.run(duration=1440.0)
        if env.peek() == float("inf") and not crit.done():
            break
    return collections.Counter(piece.model.name for piece in crit.exit_buffer)


def test_equal_priorities_share_the_pool(fresh_parser, tmp_path):
    exits = run_exits(fresh_parser, contention_flow(tmp_path, 10))
    assert exits["M1"] + exits["M2"] == 2879
    assert abs(exits["M1"] - exits["M2"]) <= 5


def test_higher_priority_takes_every_operator(fresh_parser, tmp_path):
    exits = run_exits(fresh_parser, contention_flow(tmp_path, 0))
    assert exits["M1"] == 2879
    assert exits["M2"] == 0
