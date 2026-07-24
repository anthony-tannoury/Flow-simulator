from __future__ import annotations

import csv
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from simulation import env
from . import kpis


WAIT_COLOR = '#4a7ba6'
TASK_COLOR = '#d9581e'
LINE_COLOR = '#2b5f8c'


def _safe(name: str) -> str:
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_') or 'sans_nom'


def _dates(times, sim_start: datetime | None):
    if sim_start is None:
        return [t / 1440 for t in times]
    return [sim_start + timedelta(minutes=t) for t in times]


def _out_path(base: str, kind: str, category: str, stem: str, ext: str) -> str:
    folder = os.path.join(base, kind, category)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{stem}.{ext}")


_SERIES_FIG = None


def _series_figure():
    global _SERIES_FIG
    if _SERIES_FIG is None:
        _SERIES_FIG = plt.figure(figsize=(11, 4))
    _SERIES_FIG.clf()
    return _SERIES_FIG


def _write_series(base: str, category: str, stem: str, times, values, sim_start,
                  ylabel: str, title: str, color: str = LINE_COLOR,
                  ymax: float | None = None) -> None:
    with open(_out_path(base, 'csv', category, stem, 'csv'), 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['t', ylabel])
        for t, v in zip(_dates(times, sim_start), values):
            writer.writerow([t.strftime('%d-%m-%Y %H:%M') if sim_start else round(t, 4),
                             round(v, 4) if isinstance(v, float) else v])

    fig = _series_figure()
    ax = fig.add_subplot(111)
    xs = _dates(times, sim_start)
    ax.step(xs, values, where='post', color=color, linewidth=1.1)
    ax.fill_between(xs, values, step='post', color=color, alpha=0.15)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_ylim(bottom=0)
    if ymax is not None and ymax not in (float('inf'), 0):
        ax.set_ylim(0, ymax * 1.05)
    if sim_start is None:
        ax.set_xlabel('jours simulés')
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(_out_path(base, 'png', category, stem, 'png'), dpi=130)


def _sum_of_steps(series: list[tuple[list, list]]) -> tuple[list, list]:
    events = []
    for idx, (values, times) in enumerate(series):
        events.extend((t, idx, v) for v, t in zip(values, times))
    events.sort(key=lambda e: e[0])
    current = [0.0] * len(series)
    out_t, out_v = [], []
    for t, idx, v in events:
        current[idx] = v
        total = sum(current)
        if out_t and out_t[-1] == t:
            out_v[-1] = total
        else:
            out_t.append(t)
            out_v.append(total)
    return out_v, out_t


def resource_graphs(base, resources, sim_start):
    for res in resources:
        values, times = res.available_quantity.xt()
        _write_series(base, 'ressources', f"stock_{_safe(res.name())}", times, values, sim_start,
                      'stock', f"Stock : {res.name()}")


def buffer_graphs(base, buffers, sim_start):
    for buffer in buffers:
        values, times = buffer.length.xt()
        _write_series(base, 'buffers', f"longueur_{_safe(buffer.name())}", times, values, sim_start,
                      'pieces', f"Longueur : {buffer.name()}")


def line_graphs(base, buffers, sim_start):
    from .outlet import BufferType
    passage = [b for b in buffers if b.buffer_type is BufferType.PASSAGE]
    values, times = _sum_of_steps([b.length.xt() for b in passage])
    _write_series(base, 'ligne', 'pieces_en_attente', times, values, sim_start,
                  'pieces', 'Pièces en attente (somme des buffers de passage)')
    values, times = kpis.WIP.xt()
    _write_series(base, 'ligne', 'encours', times, values, sim_start,
                  'pieces', 'Encours (pièces ni sorties ni rebutées)')


def task_graphs(base, tasks, sim_start):
    for task in tasks:

        values, times = task.vacant_slots.claimed_quantity.xt()
        capacity = task.config.max_capacity
        cap_txt = f"{capacity:g}" if capacity != float('inf') else "illimitée"

        _write_series(base, 'postes', f"occupation_{_safe(task.name())}", times, values, sim_start,
                      'places occupées', f"Occupation : {task.name()} (capacité max {cap_txt})",
                      color=TASK_COLOR)


def operator_graphs(base, operator_groups, sim_start):
    for group in operator_groups:
        values, times = group.available_quantity.xt()
        _write_series(base, 'operateurs', f"disponibles_{_safe(group.name())}", times, values, sim_start,
                      'operateurs libres',
                      f"Opérateurs disponibles : {group.name()} (max {group.n_operators:g})",
                      ymax=group.n_operators)


def production_histogram(base, buffers, piece_generator):
    from .outlet import BufferType
    if piece_generator is None:
        return
    exits = Counter(p.model for b in buffers if b.buffer_type is BufferType.EXIT for p in b)


    goals = getattr(piece_generator, 'goals', None)
    names = [m.name for m in piece_generator.models]
    generees = list(piece_generator.total_generated)
    produites = [exits.get(m, 0) for m in piece_generator.models]
    objectifs = list(goals) if goals is not None else None

    with open(_out_path(base, 'csv', 'modeles', 'production', 'csv'), 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if objectifs is not None:
            writer.writerow(['modele', 'objectif', 'generees', 'produites'])
            for row in zip(names, objectifs, generees, produites):
                writer.writerow(row)
        else:
            writer.writerow(['modele', 'generees', 'produites'])
            for row in zip(names, generees, produites):
                writer.writerow(row)

    x = range(len(names))
    fig, ax = plt.subplots(figsize=(max(7, 1.6 * len(names)), 4.5))
    if objectifs is not None:
        width = 0.27
        ax.bar([i - width for i in x], objectifs, width, label='objectif', color='#9aa0a5')
        ax.bar(x, generees, width, label='générées', color=WAIT_COLOR)
        ax.bar([i + width for i in x], produites, width, label='produites (sorties)', color=TASK_COLOR)
        prod_x = [i + width for i in x]
        title = 'Production par modèle : objectif / générées / produites'
    else:
        width = 0.38
        ax.bar([i - width / 2 for i in x], generees, width, label='générées', color=WAIT_COLOR)
        ax.bar([i + width / 2 for i in x], produites, width, label='produites (sorties)', color=TASK_COLOR)
        prod_x = [i + width / 2 for i in x]
        title = 'Production par modèle : générées / produites'
    for i in x:
        ax.text(prod_x[i], produites[i], f" {produites[i]}", ha='center', va='bottom', fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, rotation=20, ha='right')
    ax.set_ylabel('pieces')
    ax.set_title(title)
    ax.legend()
    ax.grid(axis='y', alpha=0.25)
    fig.tight_layout()
    fig.savefig(_out_path(base, 'png', 'modeles', 'production', 'png'), dpi=130)
    plt.close(fig)


def write_graphs(directory: str, tasks: list, buffers: list, resources: list,
                 operator_groups: list, piece_generator=None,
                 sim_start: datetime | None = None) -> str:
    resource_graphs(directory, resources, sim_start)
    buffer_graphs(directory, buffers, sim_start)
    line_graphs(directory, buffers, sim_start)
    task_graphs(directory, tasks, sim_start)
    operator_graphs(directory, operator_groups, sim_start)
    production_histogram(directory, buffers, piece_generator)
    return directory
