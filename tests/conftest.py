import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


@pytest.fixture
def fresh_parser():
    os.chdir(REPO)

    def load(path):
        for mod in [m for m in list(sys.modules)
                    if m == 'simulation' or m == 'parser'
                    or m.startswith(('simulation.', 'parser.'))]:
            del sys.modules[mod]
        from parser.parser import Parser
        import simulation
        return Parser(path), simulation.env

    return load
