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
from datetime import datetime, timedelta

import salabim as sim

import simulation
from simulation import env


def fmt_duree(minutes) -> str:
    """70 -> '1h 10m', 525600 -> '365j 0h 0m', 3.33 -> '3m 20s', 0.5 -> '30s'."""
    if minutes == '' or minutes is None:
        return ''
    m = float(minutes)
    if m < 1:
        return f"{round(m * 60)}s"
    if m < 60:
        whole, secondes = int(m), round((m - int(m)) * 60)
        if secondes == 60:
            whole, secondes = whole + 1, 0
        if whole < 60:
            return f"{whole}m {secondes}s" if secondes else f"{whole}m"
    total = round(m)
    heures, mins = divmod(total, 60)
    if heures < 24:
        return f"{heures}h {mins}m"
    jours, heures = divmod(heures, 24)
    return f"{jours}j {heures}h {mins}m"


def fmt_pct(x) -> str:
    """0.0812 -> '8.1%', 0.83 -> '83%'."""
    if x == '' or x is None:
        return ''
    return f"{x * 100:.1f}".rstrip('0').rstrip('.') + '%'


def fmt_instant(minutes, sim_start: datetime | None) -> str:
    """A point in simulated time, as a real date when the start date is known."""
    if minutes == '' or minutes is None:
        return ''
    if sim_start is None:
        return fmt_duree(minutes)
    return (sim_start + timedelta(minutes=float(minutes))).strftime('%d-%m-%Y %H:%M')


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


def overlap_duration(mon_a, val_a, mon_b, val_b) -> float:
    """Time where level monitor a holds val_a AND b holds val_b (event-merged)."""
    xa, ta = mon_a.xt(force_numeric=False)
    xb, tb = mon_b.xt(force_numeric=False)
    times = sorted(set(ta) | set(tb))
    total, ia, ib = 0.0, 0, 0
    for k in range(1, len(times)):
        t0, t1 = times[k - 1], times[k]
        while ia + 1 < len(ta) and ta[ia + 1] <= t0:
            ia += 1
        while ib + 1 < len(tb) and tb[ib + 1] <= t0:
            ib += 1
        if xa[ia] == val_a and xb[ib] == val_b:
            total += t1 - t0
    return total


def ratio(num: float, den: float) -> float | str:
    return round(num / den, 4) if den else ''


def _num(value) -> float | str:
    return round(value, 4) if value is not None else ''


def _product(*values):
    result = 1.0
    for value in values:
        if value is None:
            return None
        result *= value
    return result


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
    # frozen only counts during opening hours: a task that freezes near a shift
    # end stays frozen through the whole downtime until the next shift start, and
    # that off-shift stretch is not "gel", it is just closed.
    gel = overlap_duration(task.is_frozen.value, True, task.is_in_downtime.value, False)
    nc = task.active_carriers.num_carriers.value
    tf = tt - nc.value_duration(0)  # temps de fonctionnement: union, station has >=1 active carrier

    is_piece_task = isinstance(task, PieceTask)
    tc = ideal_cycle_times(task)
    if is_piece_task:
        produites = sum(task.deposited.values())
        rebutees = sum(task.scrapped.values())
        tn = sum(tc[_config_key(task, m)] * n for m, n in task.deposited.items())
    else:
        n = task.batch_sizes.number_of_entries()
        produites = round(task.batch_sizes.mean() * n, 3) if n else 0
        rebutees = 0
        tn = produites * tc[None]
    bonnes = produites - rebutees

    carriers = task.all_carriers
    collectors = [c.piece_collector if hasattr(c, 'piece_collector') else c.resource_collector
                  for c in carriers]
    lancements = task.batch_sizes.number_of_entries()

    # OEE / TRS as availability x performance x quality (each in [0, 1]).
    # Performance compares the ideal value-adding time (TN) with the actual
    # value-adding machine time (loading + processing summed over every carrier).
    # Summing over carriers is what makes it correct for stations that run several
    # carriers in parallel (independent_carriers): the "union" temps_fonctionnement
    # would undercount that work and push the rate above 100%.
    t_loading = mode_total(carriers, 'loading')
    t_processing = mode_total(carriers, 'processing')
    value_add = t_loading + t_processing
    do_val = tf / tr if tr else None
    tp_val = tn / value_add if value_add else None
    tq_val = bonnes / produites if produites else None
    trs_val = _product(do_val, tp_val, tq_val)
    trg_val = trs_val * (tr / to) if trs_val is not None and to else None
    tre_val = trs_val * (tr / tt) if trs_val is not None and tt else None

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
        'disponibilite': _num(do_val),
        'performance': _num(tp_val),
        'qualite': _num(tq_val),
        'trs': _num(trs_val),
        'trg': _num(trg_val),
        'tre': _num(tre_val),
        'pieces_produites': produites,
        'pieces_bonnes': bonnes,
        'pieces_rebutees': rebutees,
        'nb_lancements': lancements,
        'taille_lot_moyenne': round(task.batch_sizes.mean(), 3) if lancements else '',
        'cycle_moyen': round(task.cycle_times.mean(), 3) if lancements else '',
        'cycle_p90': round(task.cycle_times.percentile(90), 3) if lancements else '',
        'cycle_max': round(task.cycle_times.maximum(), 3) if lancements else '',
        'debit_pieces_j': round(produites / tr * 1440, 3) if tr else '',
        'flux_entrant_j': round(task.pieces_in / tt * 1440, 3) if is_piece_task and tt else '',
        'flux_sortant_j': round(produites / tt * 1440, 3) if tt else '',
        'attente_pieces': round(mode_total(collectors, 'wait_pieces'), 3),
        'attente_place': round(mode_total(collectors, 'wait_slot'), 3),
        'attente_operateurs': round(mode_total(carriers, 'wait_operators')
                                    + task.mode.value_duration('wait_operators'), 3),
        'attente_matiere': round(mode_total(carriers, 'wait_materials'), 3),
        'attente_vague': round(mode_total(carriers, 'wait_dispatch'), 3),
        'temps_collecte': round(mode_total(carriers, 'collecting'), 3),
        'temps_chargement': round(t_loading, 3),
        'temps_traitement': round(t_processing, 3),
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
        'flux_entrant_j': round(entrees / tt * 1440, 3) if tt else '',
        'flux_sortant_j': round(sorties / tt * 1440, 3) if tt else '',
        'temps_moyen_entre_arrivees': round(tt / entrees, 3) if entrees else '',
    }


def operator_kpis(group) -> dict:
    tt = env.now()
    posted = group.is_in_downtime.value.value_duration(False)
    claimed_mean = group.claimed_quantity.mean()
    return {
        'groupe': group.name(),
        'effectif': group.n_operators,
        'temps_poste': round(posted, 3),
        'occupation_moyenne': round(claimed_mean, 3),
        'occupation_max': group.claimed_quantity.maximum(),
        # mean claimed is averaged over the whole run; scale it back to the time
        # the group was actually posted, against its full headcount
        'taux_occupation': ratio(claimed_mean * tt, group.n_operators * posted),
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


def _lead_stats(leads: list[float]) -> dict:
    def pct(values, q):
        return round(values[min(int(len(values) * q / 100), len(values) - 1)], 3) if values else ''
    return {
        'traversee_moyenne': round(sum(leads) / len(leads), 3) if leads else '',
        'traversee_mediane': pct(leads, 50),
        'traversee_p90': pct(leads, 90),
        'traversee_max': round(leads[-1], 3) if leads else '',
    }


def flow_kpis(buffers, piece_generator=None) -> tuple[dict, list[dict]]:
    from .outlet import BufferType
    tt = env.now()
    exits = [p for b in buffers if b.buffer_type is BufferType.EXIT for p in b]
    scraps = [p for b in buffers if b.buffer_type is BufferType.SCRAP for p in b]

    def lead(piece):
        return piece.enter_time(next(iter(piece.queues()))) - piece.creation_time()

    total = len(exits) + len(scraps)
    flux = {
        'duree_simulee': round(tt, 3),
        'sorties': len(exits),
        'rebuts': len(scraps),
        'taux_rebut': ratio(len(scraps), total),
        'debit_sorties_j': round(len(exits) / tt * 1440, 3) if tt else '',
        **_lead_stats(sorted(lead(p) for p in exits)),
        'encours_moyen': round(WIP.mean(), 3),
        'encours_max': WIP.maximum(),
        'encours_final': WIP(),
    }

    par_modele = []
    if piece_generator is not None:
        exits_par_modele: dict = {}
        for p in exits:
            exits_par_modele.setdefault(p.model, []).append(lead(p))
        scraps_par_modele: dict = {}
        for p in scraps:
            scraps_par_modele[p.model] = scraps_par_modele.get(p.model, 0) + 1
        # Only the goal generator carries per-model objectives; the rate generator
        # has none, so its objectif/atteinte columns stay blank.
        goals = getattr(piece_generator, 'goals', None)
        for i, model in enumerate(piece_generator.models):
            leads = sorted(exits_par_modele.get(model, []))
            rebuts = scraps_par_modele.get(model, 0)
            objectif = goals[i] if goals is not None else ''
            par_modele.append({
                'modele': model.name,
                'objectif': objectif,
                'genere': piece_generator.total_generated[i],
                'sorties': len(leads),
                'rebuts': rebuts,
                'taux_rebut': ratio(rebuts, len(leads) + rebuts),
                'atteinte': ratio(len(leads), objectif) if goals is not None else '',
                **_lead_stats(leads),
            })
    return flux, par_modele


# Presentation: durations become 'Xj Xh Xm', ratios become percentages and
# instants become calendar dates at write time; the collectors above keep
# returning raw minutes/fractions so they stay directly usable in code.
DUREE_COLS = {
    'temps_total', 'temps_ouverture', 'arrets_programmes', 'temps_requis',
    'pannes', 'mtbf', 'mttr', 'gel', 'mise_en_route', 'temps_fonctionnement',
    'cycle_moyen', 'cycle_p90', 'cycle_max',
    'attente_pieces', 'attente_place', 'attente_operateurs', 'attente_matiere',
    'attente_vague', 'temps_collecte', 'temps_chargement', 'temps_traitement',
    'sejour_moyen', 'sejour_max', 'temps_moyen_entre_arrivees', 'temps_poste',
    'traversee_moyenne', 'traversee_mediane', 'traversee_p90', 'traversee_max',
    'temps_traversee', 'tc_ideal', 'duree_simulee',
}
PCT_COLS = {'taux_de_charge', 'disponibilite', 'performance', 'qualite',
            'trs', 'trg', 'tre', 'taux_rebut', 'atteinte', 'taux_occupation'}
INSTANT_COLS = {'creation', 'fin'}


def _format_row(row: dict, sim_start: datetime | None) -> dict:
    out = {}
    for key, value in row.items():
        if key in DUREE_COLS:
            out[key] = fmt_duree(value)
        elif key in PCT_COLS:
            out[key] = fmt_pct(value)
        elif key in INSTANT_COLS:
            out[key] = fmt_instant(value, sim_start)
        else:
            out[key] = value
    return out


def _write_csv(path: str, rows: list[dict], sim_start: datetime | None = None) -> None:
    if not rows:
        return
    rows = [_format_row(row, sim_start) for row in rows]
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(directory: str, tasks: list, buffers: list, piece_generator=None,
                 run_info: dict | None = None, sim_start: datetime | None = None,
                 operator_groups: list | None = None) -> str:
    os.makedirs(directory, exist_ok=True)

    run = {'genere_le': datetime.now().isoformat(timespec='seconds'),
           'graine': simulation.SEED,
           'duree_simulee': fmt_duree(env.now())}
    run.update(run_info or {})
    _write_csv(os.path.join(directory, 'run.csv'),
               [{'cle': k, 'valeur': v} for k, v in run.items()])

    _write_csv(os.path.join(directory, 'postes.csv'),
               [task_kpis(t) for t in tasks], sim_start)
    _write_csv(os.path.join(directory, 'postes_modeles.csv'),
               [row for t in tasks for row in task_model_rows(t)], sim_start)
    _write_csv(os.path.join(directory, 'buffers.csv'),
               [buffer_kpis(b) for b in buffers], sim_start)

    _write_csv(os.path.join(directory, 'operateurs.csv'),
               [operator_kpis(g) for g in (operator_groups or [])], sim_start)

    flux, par_modele = flow_kpis(buffers, piece_generator)
    _write_csv(os.path.join(directory, 'flux.csv'),
               [{'cle': k, 'valeur': v} for k, v in _format_row(flux, sim_start).items()])
    _write_csv(os.path.join(directory, 'flux_modeles.csv'), par_modele, sim_start)
    _write_csv(os.path.join(directory, 'temps_traversee.csv'), lead_time_rows(buffers), sim_start)
    return directory
