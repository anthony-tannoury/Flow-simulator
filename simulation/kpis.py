"""Always-on KPI collection and CSV report.

Everything here is a post-run reader: the numbers come from the monitors
salabim already records (states, resources, stores) plus the light tallies
filled by the tasks themselves (batch_sizes, cycle_times, startup_times,
deposited/scrapped, mode tags). Nothing is configurable per node: every task
and every buffer is measured, every run.

Times are simulation minutes. CSVs are written in utf-8 with BOM so Excel
opens them with accents intact.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime

import salabim as sim

import simulation
from simulation import env


# Work-in-progress level monitor: +1 when a piece is born (Piece.setup),
# -1 when it reaches an EXIT or SCRAP buffer (Piece.enter).
WIP = sim.Monitor("wip", level=True)


def rising_edges(level_monitor, value=True) -> int:
    xs, _ = level_monitor.xt(force_numeric=False)
    return sum(1 for i in range(1, len(xs)) if xs[i] == value and xs[i - 1] != value)


def edge_times(level_monitor, value=True) -> list[float]:
    xs, ts = level_monitor.xt(force_numeric=False)
    return [ts[i] for i in range(1, len(xs)) if xs[i] == value and xs[i - 1] != value]


def mode_total(components, tag: str) -> float:
    return sum(c.mode.value_duration(tag) for c in components)


def ratio(num: float, den: float) -> float | str:
    return round(num / den, 4) if den else ''


def ideal_cycle_times(task) -> dict:
    """tc idéal per model (minutes per piece at nominal pace), from the task's
    own config: mean duration + mean loading, spread over a full carrier."""
    from .piece_task import PieceTask
    loading = task.config.loading_duration.mean(0.0)
    if isinstance(task, PieceTask):
        return {model: (mc.duration.mean(0.0) + loading) / mc.max_carrier_capacity
                for model, mc in task.config.models_configs.items()}
    return {None: (task.config.duration.mean(0.0) + loading) / task.config.max_carrier_capacity}


def task_kpis(task) -> dict:
    from .piece_task import PieceTask

    tt = env.now()
    to = task.is_in_downtime.value.value_duration(False)
    arrets = task.is_in_shutdown.value.value_duration(True)
    tr = max(to - arrets, 0.0)
    pannes = task.is_in_breakdown.value.value_duration(True)
    nb_pannes = rising_edges(task.is_in_breakdown.value)
    debuts_pannes = edge_times(task.is_in_breakdown.value)
    mtbf = ''
    if len(debuts_pannes) > 1:
        mtbf = round(sum(b - a for a, b in zip(debuts_pannes, debuts_pannes[1:])) / (len(debuts_pannes) - 1), 3)
    gel = task.is_frozen.value.value_duration(True)
    nc = task.active_carriers.num_carriers.value
    tf = tt - nc.value_duration(0)

    is_piece_task = isinstance(task, PieceTask)
    tc = ideal_cycle_times(task)
    if is_piece_task:
        produites = sum(task.deposited.values())
        rebutees = sum(task.scrapped.values())
        tn = sum(tc[_config_key(task, m)] * n for m, n in task.deposited.items())
        tu = tn - sum(tc[_config_key(task, m)] * n for m, n in task.scrapped.items())
    else:
        n = task.batch_sizes.number_of_entries()
        produites = round(task.batch_sizes.mean() * n, 3) if n else 0
        rebutees = 0
        tn = tu = produites * tc[None]
    bonnes = produites - rebutees

    carriers = task.all_carriers
    collectors = [c.piece_collector if hasattr(c, 'piece_collector') else c.resource_collector
                  for c in carriers]
    lancements = task.batch_sizes.number_of_entries()

    return {
        'poste': task.name(),
        'type': 'piece' if is_piece_task else 'resource',
        'temps_total': round(tt, 3),
        'temps_ouverture': round(to, 3),
        'arrets_programmes': round(arrets, 3),
        'temps_requis': round(tr, 3),
        'pannes': round(pannes, 3),
        'nb_pannes': nb_pannes,
        'mtbf': mtbf,
        'mttr': round(pannes / nb_pannes, 3) if nb_pannes else '',
        'gel': round(gel, 3),
        'mise_en_route': round(task.startup_times.mean() * task.startup_times.number_of_entries(), 3)
                         if task.startup_times.number_of_entries() else 0.0,
        'nb_mises_en_route': task.startup_times.number_of_entries(),
        'temps_fonctionnement': round(tf, 3),
        'taux_de_charge': ratio(tr, to),
        'disponibilite': ratio(tf, tr),
        'performance': ratio(tn, tf),
        'qualite': ratio(bonnes, produites),
        'trs': ratio(tu, tr),
        'trg': ratio(tu, to),
        'tre': ratio(tu, tt),
        'pieces_produites': produites,
        'pieces_bonnes': bonnes,
        'pieces_rebutees': rebutees,
        'nb_lancements': lancements,
        'taille_lot_moyenne': round(task.batch_sizes.mean(), 3) if lancements else '',
        'cycle_moyen': round(task.cycle_times.mean(), 3) if lancements else '',
        'cycle_p90': round(task.cycle_times.percentile(90), 3) if lancements else '',
        'cycle_max': round(task.cycle_times.maximum(), 3) if lancements else '',
        'debit_pieces_h': round(produites / tr * 60, 3) if tr else '',
        'attente_pieces': round(mode_total(collectors, 'wait_pieces'), 3),
        'attente_place': round(mode_total(collectors, 'wait_slot'), 3),
        'attente_operateurs': round(mode_total(carriers, 'wait_operators')
                                    + task.mode.value_duration('wait_operators'), 3),
        'attente_matiere': round(mode_total(carriers, 'wait_materials'), 3),
        'attente_vague': round(mode_total(carriers, 'wait_dispatch'), 3),
        'temps_collecte': round(mode_total(carriers, 'collecting'), 3),
        'temps_chargement': round(mode_total(carriers, 'loading'), 3),
        'temps_traitement': round(mode_total(carriers, 'processing'), 3),
    }


def _config_key(task, model):
    m = model
    while m is not None:
        if m in task.config.models_configs:
            return m
        m = m.parent
    raise KeyError(f"No model config for {model.name}")


def task_model_rows(task) -> list[dict]:
    from .piece_task import PieceTask
    if not isinstance(task, PieceTask):
        return []
    tc = ideal_cycle_times(task)
    rows = []
    for model, n in sorted(task.deposited.items(), key=lambda mn: mn[0].name):
        rebutees = task.scrapped.get(model, 0)
        rows.append({
            'poste': task.name(),
            'modele': model.name,
            'tc_ideal': round(tc[_config_key(task, model)], 3),
            'produites': n,
            'bonnes': n - rebutees,
            'rebutees': rebutees,
        })
    return rows


def buffer_kpis(buffer) -> dict:
    tt = env.now()
    sorties = buffer.length_of_stay.number_of_entries()
    entrees = sorties + len(buffer)
    return {
        'buffer': buffer.name(),
        'type': buffer.buffer_type.name,
        'longueur_moyenne': round(buffer.length.mean(), 3),
        'longueur_max': buffer.length.maximum(),
        'longueur_ecart_type': round(buffer.length.std(), 3),
        'longueur_finale': len(buffer),
        'sejour_moyen': round(buffer.length_of_stay.mean(), 3) if sorties else '',
        'sejour_max': round(buffer.length_of_stay.maximum(), 3) if sorties else '',
        'entrees': entrees,
        'sorties': sorties,
        'temps_moyen_entre_arrivees': round(tt / entrees, 3) if entrees else '',
    }


def lead_time_rows(buffers) -> list[dict]:
    from .outlet import BufferType
    rows = []
    for buffer in buffers:
        if buffer.buffer_type is BufferType.PASSAGE:
            continue
        resultat = 'sortie' if buffer.buffer_type is BufferType.EXIT else 'rebut'
        for piece in buffer:
            fin = piece.enter_time(buffer)
            rows.append({
                'piece': piece.id,
                'modele': piece.model.name,
                'resultat': resultat,
                'creation': round(piece.creation_time(), 3),
                'fin': round(fin, 3),
                'temps_traversee': round(fin - piece.creation_time(), 3),
            })
    return sorted(rows, key=lambda r: r['fin'])


def flow_kpis(buffers, piece_generator=None) -> tuple[dict, list[dict]]:
    from .outlet import BufferType
    tt = env.now()
    exits = [p for b in buffers if b.buffer_type is BufferType.EXIT for p in b]
    scraps = [p for b in buffers if b.buffer_type is BufferType.SCRAP for p in b]
    leads = sorted(p.enter_time(next(iter(p.queues()))) - p.creation_time() for p in exits)

    def pct(values, q):
        return round(values[min(int(len(values) * q / 100), len(values) - 1)], 3) if values else ''

    total = len(exits) + len(scraps)
    flux = {
        'duree_simulee': round(tt, 3),
        'sorties': len(exits),
        'rebuts': len(scraps),
        'taux_rebut': ratio(len(scraps), total),
        'debit_sorties_h': round(len(exits) / tt * 60, 3) if tt else '',
        'traversee_moyenne': round(sum(leads) / len(leads), 3) if leads else '',
        'traversee_mediane': pct(leads, 50),
        'traversee_p90': pct(leads, 90),
        'traversee_max': round(leads[-1], 3) if leads else '',
        'encours_moyen': round(WIP.mean(), 3),
        'encours_max': WIP.maximum(),
        'encours_final': WIP(),
    }

    par_modele = []
    if piece_generator is not None:
        exits_par_modele = {}
        for p in exits:
            exits_par_modele[p.model] = exits_par_modele.get(p.model, 0) + 1
        scraps_par_modele = {}
        for p in scraps:
            scraps_par_modele[p.model] = scraps_par_modele.get(p.model, 0) + 1
        for model, objectif in zip(piece_generator.models, piece_generator.goals):
            sorties = exits_par_modele.get(model, 0)
            rebuts = scraps_par_modele.get(model, 0)
            par_modele.append({
                'modele': model.name,
                'objectif': objectif,
                'sorties': sorties,
                'rebuts': rebuts,
                'taux_rebut': ratio(rebuts, sorties + rebuts),
                'atteinte': ratio(sorties, objectif),
            })
    return flux, par_modele


def timeseries_rows(buffers) -> list[dict]:
    rows = []
    for buffer in buffers:
        xs, ts = buffer.length.xt()
        rows.extend({'serie': 'longueur_buffer', 'nom': buffer.name(),
                     't': round(t, 3), 'valeur': x} for x, t in zip(xs, ts))
    xs, ts = WIP.xt()
    rows.extend({'serie': 'encours', 'nom': 'ligne',
                 't': round(t, 3), 'valeur': x} for x, t in zip(xs, ts))
    return rows


def _write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(directory: str, tasks: list, buffers: list, piece_generator=None,
                 run_info: dict | None = None) -> str:
    os.makedirs(directory, exist_ok=True)

    run = {'genere_le': datetime.now().isoformat(timespec='seconds'),
           'graine': simulation.SEED,
           'duree_simulee': round(env.now(), 3)}
    run.update(run_info or {})
    _write_csv(os.path.join(directory, 'run.csv'),
               [{'cle': k, 'valeur': v} for k, v in run.items()])

    _write_csv(os.path.join(directory, 'postes.csv'), [task_kpis(t) for t in tasks])
    _write_csv(os.path.join(directory, 'postes_modeles.csv'),
               [row for t in tasks for row in task_model_rows(t)])
    _write_csv(os.path.join(directory, 'buffers.csv'), [buffer_kpis(b) for b in buffers])

    flux, par_modele = flow_kpis(buffers, piece_generator)
    _write_csv(os.path.join(directory, 'flux.csv'),
               [{'cle': k, 'valeur': v} for k, v in flux.items()])
    _write_csv(os.path.join(directory, 'flux_modeles.csv'), par_modele)
    _write_csv(os.path.join(directory, 'temps_traversee.csv'), lead_time_rows(buffers))
    _write_csv(os.path.join(directory, 'series_temporelles.csv'), timeseries_rows(buffers))
    return directory
