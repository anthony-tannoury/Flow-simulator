import json
import os
import shutil

from datetime import datetime, timedelta
from time import perf_counter

from simulation import env, kpis, graphs
from simulation.outlet import Buffer, BufferType

from .parser import canon_name, same_name


def describe_fn(fn) -> str:
    if not isinstance(fn, dict):
        return str(fn)
    if canon_name(fn.get('kind', 'constant')) == 'constant':
        return f"{fn.get('value', 0):g}"
    params = ', '.join(f"{k}={v:g}" for k, v in fn.items() if k != 'kind')
    return f"{fn['kind']}({params})"


def describe_criterion(parser) -> str:
    parts = []
    for key, value in parser.data['stopping_criterion'].items():
        if key == 'type':
            continue
        if key == 'models_goals':
            parts.append('models_goals: ' + ', '.join(
                f"{parser.models[mg['model']].name} = {mg['goal']}" for mg in value))
        elif key == 'models_probs':
            parts.append('models_probs: ' + ', '.join(
                f"{parser.models[mp['model']].name} = "
                + ('reste' if mp['probability'] is None else describe_fn(mp['probability']))
                for mp in value))
        elif key == 'gap':
            parts.append(f"gap = {describe_fn(value)}")
        elif key in ('timeout', 'grace_period') and isinstance(value, (int, float)) and value != float('inf'):
            parts.append(f"{key} = {kpis.fmt_duree(value)}")
        else:
            parts.append(f"{key} = {value}")
    return '; '.join(parts)


def write_report(parser, directory: str | None = None) -> str:
    if directory is None:
        stem = os.path.splitext(os.path.basename(parser.filename))[0]
        directory = os.path.join('runs', f"{datetime.now():%Y-%m-%d_%H%M%S}_{stem}")
    buffers = [o for o in parser.outlets.values() if isinstance(o, Buffer)]

    criterion = parser.data['stopping_criterion']
    run_info = {
        'fichier': parser.filename,
        'debut': parser.data['start_date'],
        'fin': (parser.sim_start + timedelta(minutes=env.now())).strftime('%d-%m-%Y %H:%M'),
        'temps_calcul': kpis.fmt_duree((perf_counter() - parser.loaded_at) / 60),
        'critere_arret': criterion['type'],
        'critere_details': describe_criterion(parser),
    }
    kpis.write_report(
        directory,
        tasks=list(parser.tasks.values()),
        buffers=buffers,
        piece_generator=parser.piece_generator,
        run_info=run_info,
        sim_start=parser.sim_start,
        operator_groups=list(parser.operator_groups.values()),
        resources=list(parser.resources.values())
    )
    graphs.write_graphs(
        os.path.join(directory, 'graphes'),
        tasks=list(parser.tasks.values()),
        buffers=buffers,
        resources=list(parser.resources.values()),
        operator_groups=list(parser.operator_groups.values()),
        piece_generator=parser.piece_generator,
        sim_start=parser.sim_start
    )
    write_machine_report(parser, directory, run_info)
    return directory


def write_machine_report(parser, directory: str, run_info: dict) -> None:
    import simulation as simulation_pkg

    criterion = parser.data['stopping_criterion']
    exit_pieces = sum(len(b) for b in parser.outlets.values()
                      if isinstance(b, Buffer) and b.buffer_type is BufferType.EXIT)
    goal_total = None
    goal_reached = None
    if same_name(criterion['type'], 'ByPiecesProduced'):
        goal_total = sum(mg['goal'] for mg in criterion['models_goals'])
        goal_reached = exit_pieces >= goal_total

    def png(category: str, stem: str) -> str | None:
        rel = os.path.join('graphes', 'png', category, f"{stem}.png")
        return rel if os.path.isfile(os.path.join(directory, rel)) else None

    safe = graphs._safe
    flux, flux_modeles = kpis.flow_kpis(
        [b for b in parser.outlets.values() if isinstance(b, Buffer)], parser.piece_generator)
    task_kpi_rows = {id_: kpis.task_kpis(t) for id_, t in parser.tasks.items()}
    data = {
        'format': 'flow-simulator-report',
        'version': 1,
        'run': {
            'engine': 'python',
            **run_info,
            'source_file': os.path.abspath(parser.filename),
            'flow_snapshot': 'flow.json',
            'sim_end_minutes': round(env.now(), 3),
            'graine': simulation_pkg.SEED,
            'genere_le': datetime.now().isoformat(timespec='seconds'),
            'criterion': criterion,
            'pieces_sorties': exit_pieces,
            'objectif_total': goal_total,
            'objectif_atteint': goal_reached,
        },
        'tasks': task_kpi_rows,
        'admin_summary': kpis.admin_summary(list(task_kpi_rows.values())),
        'tasks_models': {id_: rows for id_, t in parser.tasks.items()
                         if (rows := kpis.task_model_rows(t))},
        'buffers': {id_: kpis.buffer_kpis(b) for id_, b in parser.outlets.items()
                    if isinstance(b, Buffer)},
        'operators': {id_: kpis.operator_kpis(g) for id_, g in parser.operator_groups.items()},
        'resources': {id_: kpis.resource_kpis(r) for id_, r in parser.resources.items()},
        'flux': flux,
        'flux_modeles': flux_modeles,
        'graphs': {
            'tasks': {id_: p for id_, t in parser.tasks.items()
                      if (p := png('postes', f"occupation_{safe(t.name())}"))},
            'buffers': {id_: p for id_, b in parser.outlets.items()
                        if isinstance(b, Buffer)
                        and (p := png('buffers', f"longueur_{safe(b.name())}"))},
            'operators': {id_: p for id_, g in parser.operator_groups.items()
                          if (p := png('operateurs', f"disponibles_{safe(g.name())}"))},
            'resources': {id_: p for id_, r in parser.resources.items()
                          if (p := png('ressources', f"stock_{safe(r.name())}"))},
            'models': {m.name: p for m in parser.piece_generator.models
                       if (p := png('modeles', f"trajectoires_{safe(m.name)}"))},
            'production': png('modeles', 'production'),
            'encours': png('ligne', 'encours'),
            'attente': png('ligne', 'pieces_en_attente'),
        },
    }
    with open(os.path.join(directory, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    shutil.copyfile(parser.filename, os.path.join(directory, 'flow.json'))
