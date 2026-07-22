import copy
import json
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = json.load(open(os.path.join(REPO, "flow_designer", "sample_flow.json"), encoding="utf-8"))


def variant(tmp_path, disabled_names):
    data = copy.deepcopy(SRC)
    for node in data["nodes"]:
        node["enabled"] = node["name"] not in disabled_names
    path = tmp_path / "flow.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_all_enabled_keeps_every_node(fresh_parser, tmp_path):
    p, _ = fresh_parser(variant(tmp_path, set()))
    counts = {k: len(v) for k, v in p.per_kind.items()}
    assert counts["Breakdown"] == 2 and counts["Shutdowns"] == 2


def test_disabled_breakdown_and_shutdowns_vanish(fresh_parser, tmp_path):
    p, _ = fresh_parser(variant(tmp_path, {"Sprayer Fault", "Periodic Cleaning"}))
    counts = {k: len(v) for k, v in p.per_kind.items()}
    assert counts["Breakdown"] == 1 and counts["Shutdowns"] == 1
    for t in p.per_kind["Task"] + p.per_kind["ResourceTask"]:
        for sid in t.get("shutdowns", []):
            assert p.by_id[sid]["name"] != "Periodic Cleaning"
    p.load_all()


def test_disabled_task_takes_its_breakdown_with_it(fresh_parser, tmp_path):
    p, _ = fresh_parser(variant(tmp_path, {"Paint Line"}))
    counts = {k: len(v) for k, v in p.per_kind.items()}
    assert "ResourceTask" not in counts
    assert counts["Breakdown"] == 1
    p.load_all()


def test_disabled_generator_is_a_clear_error(fresh_parser, tmp_path):
    p, _ = fresh_parser(variant(tmp_path, {"Bodies In"}))
    with pytest.raises(ValueError, match="piece generator"):
        p.load_all()


def test_disabled_exit_buffer_is_a_clear_error(fresh_parser, tmp_path):
    exit_name = next(n["name"] for n in SRC["nodes"]
                     if n["kind"] == "Buffer" and n.get("buffer_type") == "EXIT")
    p, _ = fresh_parser(variant(tmp_path, {exit_name}))
    with pytest.raises(ValueError, match="EXIT"):
        p.load_all()
