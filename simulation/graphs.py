"""Graphs for a finished run: one graphes/ folder, one sub-folder per family,
and for every figure both the PNG and the CSV of the plotted data.

    graphes/
        ressources/    stock_<ressource>.csv|png
        buffers/       longueur_<buffer>.csv|png
        ligne/         pieces_en_attente.csv|png, encours.csv|png
        postes/        occupation_<poste>.csv|png
        operateurs/    disponibles_<groupe>.csv|png
        modeles/       trajectoires_<modele>.csv|png, production.csv|png

Everything is read post-run from the monitors salabim already keeps, plus the
piece journals (buffer in/out + task stamps) filled during the run.
"""
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
ABORT_COLOR = '#9aa0a5'


def _safe(name: str) -> str:
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_') or 'sans_nom'


def _dates(times, sim_start: datetime | None):
    if sim_start is None:
        return [t / 1440 for t in times]  # days since start
    return [sim_start + timedelta(minutes=t) for t in times]


def _write_series(folder: str, stem: str, times, values, sim_start, ylabel: str,
                  title: str, color: str = LINE_COLOR, ratio: bool = False) -> None:
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, stem + '.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['t', ylabel])
        for t, v in zip(_dates(times, sim_start), values):
            writer.writerow([t.strftime('%d-%m-%Y %H:%M') if sim_start else round(t, 4),
                             round(v, 4) if isinstance(v, float) else v])

    fig, ax = plt.subplots(figsize=(11, 4))
    xs = _dates(times, sim_start)
    ax.step(xs, values, where='post', color=color, linewidth=1.1)
    ax.fill_between(xs, values, step='post', color=color, alpha=0.15)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    if ratio:
        ax.set_ylim(-0.02, 1.05)
        ax.yaxis.set_major_formatter(lambda v, _: f"{v * 100:.0f}%")
    if sim_start is None:
        ax.set_xlabel('jours simulés')
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(os.path.join(folder, stem + '.png'), dpi=130)
    plt.close(fig)


def _sum_of_steps(series: list[tuple[list, list]]) -> tuple[list, list]:
    """Sum step functions given as (values, times) pairs, event by event."""
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


# ---------------------------------------------------------------------------
# time series
# ---------------------------------------------------------------------------

def resource_graphs(folder, resources, sim_start):
    for res in resources:
        values, times = res.available_quantity.xt()
        _write_series(folder, f"stock_{_safe(res.name())}", times, values, sim_start,
                      'stock', f"Stock — {res.name()}")


def buffer_graphs(folder, buffers, sim_start):
    for buffer in buffers:
        values, times = buffer.length.xt()
        _write_series(folder, f"longueur_{_safe(buffer.name())}", times, values, sim_start,
                      'pieces', f"Longueur — {buffer.name()}")


def line_graphs(folder, buffers, sim_start):
    from .outlet import BufferType
    passage = [b for b in buffers if b.buffer_type is BufferType.PASSAGE]
    values, times = _sum_of_steps([b.length.xt() for b in passage])
    _write_series(folder, 'pieces_en_attente', times, values, sim_start,
                  'pieces', 'Pièces en attente (somme des buffers de passage)')
    values, times = kpis.WIP.xt()
    _write_series(folder, 'encours', times, values, sim_start,
                  'pieces', 'Encours (pièces ni sorties ni rebutées)')


def task_graphs(folder, tasks, sim_start):
    for task in tasks:
        values, times = task.vacant_slots.claimed_quantity.xt()
        capacity = task.config.max_capacity
        ratios = [v / capacity for v in values]
        _write_series(folder, f"occupation_{_safe(task.name())}", times, ratios, sim_start,
                      'occupation', f"Occupation — {task.name()} (capacité {capacity:g})",
                      color=TASK_COLOR, ratio=True)


def operator_graphs(folder, operator_groups, sim_start):
    for group in operator_groups:
        values, times = group.available_quantity.xt()
        _write_series(folder, f"disponibles_{_safe(group.name())}", times, values, sim_start,
                      'operateurs libres', f"Opérateurs disponibles — {group.name()}")


# ---------------------------------------------------------------------------
# trajectories per model
# ---------------------------------------------------------------------------

def _segments(piece) -> list[tuple[str, str, float]]:
    """journal -> [(step name, 'attente'|'poste', duration)]; the final stay in
    the exit/scrap buffer is open-ended and not a step."""
    segments = []
    in_buffer = None      # (name, t)
    out_time = None
    task_name = None
    for kind, name, t in piece.journal:
        if kind == 'in':
            if out_time is not None:
                segments.append((task_name or '(interrompu)', 'poste', t - out_time))
                out_time, task_name = None, None
            in_buffer = (name, t)
        elif kind == 'out':
            if in_buffer is not None:
                segments.append((in_buffer[0], 'attente', t - in_buffer[1]))
                in_buffer = None
            out_time = t
        elif kind == 'task':
            task_name = name
    return segments


def _duration_unit(max_minutes: float) -> tuple[float, str]:
    if max_minutes > 2 * 1440:
        return 1440.0, 'jours'
    if max_minutes > 120:
        return 60.0, 'heures'
    return 1.0, 'minutes'


def trajectory_graphs(folder, buffers, piece_generator, sim_start, max_branches: int = 8):
    from .outlet import BufferType
    finished = [p for b in buffers if b.buffer_type in (BufferType.EXIT, BufferType.SCRAP) for p in b]
    if piece_generator is None or not finished:
        return
    os.makedirs(folder, exist_ok=True)

    for model in piece_generator.models:
        pieces = [p for p in finished if p.model is model]
        if not pieces:
            continue

        branches: dict[tuple, list] = {}
        for p in pieces:
            segments = _segments(p)
            branches.setdefault(tuple(s[0] for s in segments), []).append(segments)

        ranked = sorted(branches.items(), key=lambda kv: len(kv[1]), reverse=True)
        rows = []
        for rank, (signature, journeys) in enumerate(ranked, start=1):
            n = len(journeys)
            for position in range(len(signature)):
                mean = sum(j[position][2] for j in journeys) / n
                rows.append({
                    'modele': model.name, 'trajectoire': rank, 'n_pieces': n,
                    'part': kpis.fmt_pct(n / len(pieces)),
                    'ordre': position + 1, 'etape': signature[position],
                    'type': journeys[0][position][1],
                    'duree_moyenne_min': round(mean, 2),
                    'duree_moyenne': kpis.fmt_duree(mean),
                })

        stem = f"trajectoires_{_safe(model.name)}"
        with open(os.path.join(folder, stem + '.csv'), 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        plotted = ranked[:max_branches]
        totals = []
        for signature, journeys in plotted:
            n = len(journeys)
            totals.append(sum(sum(j[k][2] for j in journeys) / n for k in range(len(signature))))
        div, unit = _duration_unit(max(totals))

        fig_height = 1.1 + 0.62 * len(plotted)
        fig, ax = plt.subplots(figsize=(12, fig_height))
        xmax = max(t / div for t in totals)
        for y, (signature, journeys) in enumerate(plotted):
            n = len(journeys)
            left = 0.0
            for position, step in enumerate(signature):
                mean = sum(j[position][2] for j in journeys) / n / div
                seg_type = journeys[0][position][1]
                color = {'attente': WAIT_COLOR, 'poste': TASK_COLOR}.get(seg_type, ABORT_COLOR)
                ax.barh(y, mean, left=left, height=0.5, color=color,
                        alpha=0.55 if seg_type == 'attente' else 0.9,
                        edgecolor='white', linewidth=0.6)
                # label only when the text actually fits inside the segment
                if mean > (len(step) + 2) * 0.008 * xmax:
                    ax.text(left + mean / 2, y, step, ha='center', va='center',
                            fontsize=6.5, rotation=0, clip_on=True)
                left += mean
            ax.text(left + 0.01 * xmax, y,
                    f"n={n} ({n / len(pieces) * 100:.0f}%)", va='center', fontsize=8)
        ax.set_yticks(range(len(plotted)))
        ax.set_yticklabels([f"traj. {i + 1}" for i in range(len(plotted))])
        ax.invert_yaxis()
        ax.set_xlabel(f"durée moyenne cumulée ({unit})")
        extra = f" — {len(ranked) - len(plotted)} trajectoires rares non tracées" if len(ranked) > len(plotted) else ""
        ax.set_title(f"Trajectoires — {model.name} ({len(pieces)} pièces finies){extra}")
        ax.grid(axis='x', alpha=0.25)
        handles = [plt.Rectangle((0, 0), 1, 1, color=WAIT_COLOR, alpha=0.55),
                   plt.Rectangle((0, 0), 1, 1, color=TASK_COLOR, alpha=0.9)]
        ax.legend(handles, ['attente (buffer)', 'poste'], loc='lower right', fontsize=8)
        fig.tight_layout()
        fig.savefig(os.path.join(folder, stem + '.png'), dpi=130)
        plt.close(fig)


# ---------------------------------------------------------------------------
# production histogram
# ---------------------------------------------------------------------------

def production_histogram(folder, buffers, piece_generator):
    from .outlet import BufferType
    if piece_generator is None:
        return
    os.makedirs(folder, exist_ok=True)
    exits = Counter(p.model for b in buffers if b.buffer_type is BufferType.EXIT for p in b)

    names = [m.name for m in piece_generator.models]
    objectifs = list(piece_generator.goals)
    generees = list(piece_generator.total_generated)
    produites = [exits.get(m, 0) for m in piece_generator.models]

    with open(os.path.join(folder, 'production.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['modele', 'objectif', 'generees', 'produites'])
        for row in zip(names, objectifs, generees, produites):
            writer.writerow(row)

    x = range(len(names))
    width = 0.27
    fig, ax = plt.subplots(figsize=(max(7, 1.6 * len(names)), 4.5))
    ax.bar([i - width for i in x], objectifs, width, label='objectif', color='#9aa0a5')
    ax.bar(x, generees, width, label='générées', color=WAIT_COLOR)
    ax.bar([i + width for i in x], produites, width, label='produites (sorties)', color=TASK_COLOR)
    for i in x:
        ax.text(i + width, produites[i], f" {produites[i]}", ha='center', va='bottom', fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, rotation=20, ha='right')
    ax.set_ylabel('pieces')
    ax.set_title('Production par modèle — objectif / générées / produites')
    ax.legend()
    ax.grid(axis='y', alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, 'production.png'), dpi=130)
    plt.close(fig)


def write_graphs(directory: str, tasks: list, buffers: list, resources: list,
                 operator_groups: list, piece_generator=None,
                 sim_start: datetime | None = None) -> str:
    resource_graphs(os.path.join(directory, 'ressources'), resources, sim_start)
    buffer_graphs(os.path.join(directory, 'buffers'), buffers, sim_start)
    line_graphs(os.path.join(directory, 'ligne'), buffers, sim_start)
    task_graphs(os.path.join(directory, 'postes'), tasks, sim_start)
    operator_graphs(os.path.join(directory, 'operateurs'), operator_groups, sim_start)
    trajectory_graphs(os.path.join(directory, 'modeles'), buffers, piece_generator, sim_start)
    production_histogram(os.path.join(directory, 'modeles'), buffers, piece_generator)
    return directory
