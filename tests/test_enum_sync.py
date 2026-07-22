import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "flow_designer"))

CPP_PARSER = open(os.path.join(REPO, "cpp", "engine", "parser.hpp"), encoding="utf-8").read()


def cpp_table_keys(fn_name):
    body = re.search(fn_name + r"\(\)\s*\{(.*?)return t;", CPP_PARSER, re.S).group(1)
    return set(re.findall(r'\{"([A-Za-z_]+)",', body))


def test_collector_types_in_sync():
    import ui_helpers
    from parser import parser as pyparser
    designer = set(ui_helpers.COLLECTOR_TYPES)
    python = set(pyparser.STR_TO_PIECE_COLLECTOR_TYPE)
    cpp = cpp_table_keys("piece_collector_types")
    assert designer == python == cpp


def test_resource_collector_types_in_sync():
    import ui_helpers
    from parser import parser as pyparser
    assert (set(ui_helpers.RESOURCE_COLLECTOR_TYPES)
            == set(pyparser.STR_TO_RESOURCE_COLLECTOR_TYPE)
            == cpp_table_keys("resource_collector_types"))


def test_buffer_types_in_sync():
    import ui_helpers
    from parser import parser as pyparser
    assert (set(ui_helpers.BUFFER_TYPES)
            == set(pyparser.STR_TO_BUFFER_TYPE)
            == cpp_table_keys("buffer_types"))


def test_distribution_types_in_sync():
    import ui_helpers
    from parser import parser as pyparser
    assert (set(ui_helpers.DISTRIBUTION_SPECS)
            == set(pyparser.DISTR_TYPE_TO_CLASS)
            == cpp_table_keys("distr_types"))


def test_scopes_in_sync():
    from parser import parser as pyparser
    assert set(pyparser.STR_TO_SCOPE) == cpp_table_keys("scopes")


def test_policy_defaults_in_sync():
    import ui_helpers
    from parser import parser as pyparser
    assert set(ui_helpers.PIECE_POLICY_OPTIONS) == set(pyparser.PIECE_DEFAULT_POLICIES)
    assert set(ui_helpers.POLICY_OPTIONS) == set(pyparser.DEFAULT_POLICIES)
