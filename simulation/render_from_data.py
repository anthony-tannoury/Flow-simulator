from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

from . import graphs, kpis
from .outlet import BufferType


_BUFFER_TYPES = {'PASSAGE': BufferType.PASSAGE, 'EXIT': BufferType.EXIT, 'SCRAP': BufferType.SCRAP}


class _Monitor:

    def __init__(self, block: dict):
        self._v = list(block.get('v', []))
        self._t = list(block.get('t', []))

    def xt(self):
        return self._v, self._t


class _Named:
    def __init__(self, name: str):
        self._name = name

    def name(self) -> str:
        return self._name


class _Task(_Named):
    def __init__(self, row: dict):
        super().__init__(row['name'])
        cap = row.get('capacity')
        self.config = SimpleNamespace(max_capacity=float('inf') if cap is None else cap)
        self.vacant_slots = SimpleNamespace(claimed_quantity=_Monitor(row))


class _Buffer(_Named):
    def __init__(self, row: dict):
        super().__init__(row['name'])
        self.length = _Monitor(row)
        self.buffer_type = _BUFFER_TYPES[row['type']]
        self.pieces: list = []

    def __iter__(self):
        return iter(self.pieces)


class _Group(_Named):
    def __init__(self, row: dict):
        super().__init__(row['name'])
        self.available_quantity = _Monitor(row)
        self.n_operators = row['n_operators']


class _Resource(_Named):
    def __init__(self, row: dict):
        super().__init__(row['name'])
        self.available_quantity = _Monitor(row)


class _Model:

    __slots__ = ('name',)

    def __init__(self, name: str):
        self.name = name


class _Piece:
    def __init__(self, model, journal):
        self.model = model
        self.journal = [tuple(entry) for entry in journal]


def _png_if_exists(run_dir: str, category: str, stem: str) -> str | None:
    rel = os.path.join('graphes', 'png', category, f"{stem}.png")
    return rel if os.path.isfile(os.path.join(run_dir, rel)) else None


def render(run_dir: str) -> None:
    with open(os.path.join(run_dir, 'graph_data.json'), encoding='utf-8') as f:
        gd = json.load(f)

    sim_start = (datetime.strptime(gd['sim_start'], '%d-%m-%Y %H:%M')
                 if gd.get('sim_start') else None)

    tasks = [_Task(r) for r in gd.get('tasks', [])]
    buffers = [_Buffer(r) for r in gd.get('buffers', [])]
    operators = [_Group(r) for r in gd.get('operators', [])]
    resources = [_Resource(r) for r in gd.get('resources', [])]


    interned: dict[str, _Model] = {}

    def model(name: str) -> _Model:
        return interned.setdefault(name, _Model(name))

    buffer_by_id = {row['id']: buf for row, buf in zip(gd.get('buffers', []), buffers)}
    for fp in gd.get('finished_pieces', []):
        buffer_by_id[fp['buffer_id']].pieces.append(_Piece(model(fp['model']), fp['journal']))

    gen_block = gd.get('generator') or {}
    generator = None
    if gen_block.get('models'):
        generator = SimpleNamespace(
            models=[model(n) for n in gen_block['models']],
            total_generated=gen_block.get('total_generated', []),
            goals=gen_block.get('goals'),
        )

    kpis.WIP = _Monitor(gd.get('wip', {}))

    graphs.write_graphs(os.path.join(run_dir, 'graphes'), tasks=tasks, buffers=buffers,
                        resources=resources, operator_groups=operators,
                        piece_generator=generator, sim_start=sim_start)


    safe = graphs._safe

    def keyed(rows, category, stem_of):
        return {r['id']: p for r in rows
                if (p := _png_if_exists(run_dir, category, stem_of(r['name'])))}

    report_path = os.path.join(run_dir, 'report.json')
    with open(report_path, encoding='utf-8') as f:
        report = json.load(f)
    report['graphs'] = {
        'tasks': keyed(gd.get('tasks', []), 'postes', lambda n: f"occupation_{safe(n)}"),
        'buffers': keyed(gd.get('buffers', []), 'buffers', lambda n: f"longueur_{safe(n)}"),
        'operators': keyed(gd.get('operators', []), 'operateurs', lambda n: f"disponibles_{safe(n)}"),
        'resources': keyed(gd.get('resources', []), 'ressources', lambda n: f"stock_{safe(n)}"),
        'models': {m: p for m in (gen_block.get('models') or [])
                   if (p := _png_if_exists(run_dir, 'modeles', f"trajectoires_{safe(m)}"))},
        'production': _png_if_exists(run_dir, 'modeles', 'production'),
        'encours': _png_if_exists(run_dir, 'ligne', 'encours'),
        'attente': _png_if_exists(run_dir, 'ligne', 'pieces_en_attente'),
    }
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=1, ensure_ascii=False)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("usage: python -m simulation.render_from_data <run_dir>", file=sys.stderr)
        sys.exit(2)
    render(sys.argv[1])
