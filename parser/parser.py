import json
import os
import shutil
import salabim as sim

from time import perf_counter
from simulation import env, kpis, graphs, reseed

from datetime import date, time, datetime, timedelta
from simulation.piece_task import PieceTask, PieceTaskConfig, ModelConfig, PieceCollectorType, PieceProtocols
from simulation.resource_task import ResourceTask, ResourceTaskConfig, ResourceCollectorType
from simulation.piece import Model, GoalPieceGenerator, RatePieceGenerator
from simulation.task import Task, Scope, Protocols
from simulation.sampler import Sampler, Distribution, FailureRate, LogNormal
from simulation.function_generator import Linear, Exponential, Step, Bathtub
from simulation.outlet import Outlet, Buffer, Router, BufferType
from simulation.judgement_day import ByTime, ByPiecesProduced, SimulationStopper
from simulation.interval import Interval
from simulation.shift_manager import ShiftManager
from simulation.resource import Resource, RestockableResource
from simulation.operator import Alternative, OperatorGroup
from simulation.interrupters import Breakdown, Shutdowns, FlexibleShutdowns, NonFlexibleShutdowns
from simulation.protocols import (AbortPendingCarriers, WaitForCarriers, AbortOrWaitForCarriers,
                                  ConstrainedByShift, NotConstrainedByShift, PartiallyConstrainedByShift,
                                  Conscious, Unconscious,
                                  FirstInFirstOut, FirstCreatedFirstOut,
                                  MostPresent, FastestTaskDuration, SmallestGapToMinCarrierCapacity)
from typing import Callable


def to_date(date_str: str) -> date:
    return datetime.strptime(date_str, '%d-%m-%Y').date()

def to_minutes(time_str: str) -> float:
    hour, minute = time_str.split(':')
    return 60 * int(hour) + int(minute)

def to_datetime(datetime_str: str) -> datetime:
    return datetime.strptime(datetime_str, '%d-%m-%Y %H:%M')


def join_shifts(shifts: list[list[Interval]]) -> list[Interval]:
        joined = []
        for shift in shifts:
            joined.extend(shift)
        return joined


def canon_name(value: str) -> str:
    """Normalization for type names: the canonical identifiers (ByTime, PER_BATCH,
    AbortPendingCarriers) and their sentence-case display forms (By time, Per batch,
    Abort pending carriers) collapse to one key, so files written with either
    naming parse the same."""
    return ''.join(ch for ch in str(value) if ch.isalnum()).lower()


def lookup(table: dict, value: str, what: str):
    """table[value], accepting any spelling canon_name folds together."""
    if value in table:
        return table[value]
    key = canon_name(value)
    for k, v in table.items():
        if canon_name(k) == key:
            return v
    raise NotImplementedError(f"unknown {what}: {value!r}")


def same_name(value: str, canonical: str) -> bool:
    return canon_name(value) == canon_name(canonical)


def make_callable(c: dict) -> float | Callable[[float], float]:
    match canon_name(c['kind']):
        case 'constant':
            return c['value']
        case 'linear':
            return Linear.generate(c['x1'], c['y1'], c['x2'], c['y2'])
        case 'exponential':
            return Exponential.generate(c['x1'], c['y1'], c['x2'], c['y2'], c['limit'])
        case 'step':
            return Step.generate(c['x1'], c['y1'], c['x2'], c['y2'], c['step_size'])
        case _:
            raise NotImplementedError()


def make_distribution(distribution: dict) -> Distribution:
        params = []
        for param in distribution['params'].values():
            params.append(make_callable(param))

        return Distribution(lookup(DISTR_TYPE_TO_CLASS, distribution['dist_type'], 'distribution type'), *params)


def make_salabim_distribution(distribution: dict) -> sim.Distribution:
    params = [make_callable(p) for p in distribution['params'].values()]

    if not all(isinstance(p, (int, float)) for p in params):
        raise NotImplementedError('output-resource distributions must have constant parameters')

    return lookup(DISTR_TYPE_TO_CLASS, distribution['dist_type'], 'distribution type')(*params)


def make_mtbf(mtbf: dict) -> Sampler:
    match canon_name(mtbf['mode']):
        case 'distribution':
            return make_distribution(mtbf['distribution'])
        case 'bathtub':
            return FailureRate(
                failure_rate=Bathtub.generate(mtbf['a'], mtbf['tau'], mtbf['c'], mtbf['beta'], mtbf['eta']),
                tolerance=mtbf['tolerance'],
                max_iters=mtbf['max_iters']
            )
        case _:
            raise NotImplementedError()


def make_protocol(policy: dict):
    match canon_name(policy['type']):
        case 'abortpendingcarriers':
            return AbortPendingCarriers()
        case 'waitforcarriers':
            return WaitForCarriers()
        case 'abortorwaitforcarriers':
            return AbortOrWaitForCarriers(tolerance_fraction=policy['tolerance_fraction'])
        case 'constrainedbyshift':
            return ConstrainedByShift()
        case 'notconstrainedbyshift':
            return NotConstrainedByShift()
        case 'partiallyconstrainedbyshift':
            return PartiallyConstrainedByShift(tolerance=policy['tolerance'])
        case 'conscious':
            return Conscious()
        case 'unconscious':
            return Unconscious()
        case 'firstinfirstout':
            return FirstInFirstOut()
        case 'firstcreatedfirstout':
            return FirstCreatedFirstOut()
        case 'mostpresent':
            return MostPresent()
        case 'fastesttaskduration':
            return FastestTaskDuration()
        case 'smallestgaptomincarriercapacity':
            return SmallestGapToMinCarrierCapacity()
        case _:
            raise NotImplementedError()


def make_protocols(policies: dict) -> Protocols:
    return Protocols(**{
        field: make_protocol(policies.get(field, default))
        for field, default in DEFAULT_POLICIES.items()
    })


def make_piece_protocols(policies: dict) -> PieceProtocols:
    return PieceProtocols(**{
        field: make_protocol(policies.get(field, default))
        for field, default in PIECE_DEFAULT_POLICIES.items()
    })


DISTR_TYPE_TO_CLASS = {
    'Constant': sim.Constant,
    'Uniform': sim.Uniform,
    'Normal': sim.Normal,
    'Exponential': sim.Exponential,
    'Triangular': sim.Triangular,
    'LogNormal': LogNormal,
}

STR_TO_BUFFER_TYPE = {
    'PASSAGE': BufferType.PASSAGE,
    'SCRAP': BufferType.SCRAP,
    'EXIT': BufferType.EXIT
}

STR_TO_PIECE_COLLECTOR_TYPE = {
    'DISCRIMINATING_GREEDY': PieceCollectorType.DISCRIMINATING_GREEDY,
    'NON_DISCRIMINATING_GREEDY': PieceCollectorType.NON_DISCRIMINATING_GREEDY,
    'DISCRIMINATING_ALTRUISTIC': PieceCollectorType.DISCRIMINATING_ALTRUISTIC,
    'NON_DISCRIMINATING_ALTRUISTIC': PieceCollectorType.NON_DISCRIMINATING_ALTRUISTIC,
}

STR_TO_RESOURCE_COLLECTOR_TYPE = {
    'GREEDY': ResourceCollectorType.GREEDY,
    'ALTRUISTIC': ResourceCollectorType.ALTRUISTIC
}

STR_TO_SCOPE = {
    'PER_UNIT': Scope.PER_UNIT,
    'PER_BATCH': Scope.PER_BATCH,
    'PER_TASK': Scope.PER_TASK
}

DEFAULT_POLICIES = {
    'pending_carriers_pre_flexible_shutdowns': {'type': 'AbortPendingCarriers'},
    'pending_carrier_pre_task_shift_end': {'type': 'AbortPendingCarriers'},
    'operator_shift_constraint': {'type': 'ConstrainedByShift'},
    'task_shift_constraint': {'type': 'ConstrainedByShift'},
    'operators_self_conscious': {'type': 'Conscious'}
}

PIECE_DEFAULT_POLICIES = {
    **DEFAULT_POLICIES,
    'piece_exit_order': {'type': 'FirstInFirstOut'},
    'batch_model_choice': {'type': 'MostPresent'}
}


class Parser:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        # encoding='utf-8' is not optional: without it Python uses the locale
        # code page (cp1252 on Western Windows), which reads UTF-8 accents as
        # mojibake ('Entrée' -> 'Entrée'). That corrupted string would then be
        # written faithfully to the report CSVs. The flow JSON is always UTF-8.
        with open(filename, 'r', encoding='utf-8') as file:
            self.data = json.load(file)
        # Re-seed the shared environment before anything is built or drawn, so the
        # run is reproducible for the flow's seed (0 by default) and differs between
        # seeds. Must happen here, ahead of load_all's object construction.
        self.seed = int(self.data.get('seed', 0))
        reseed(self.seed)
        self.sim_start = to_datetime(self.data['start_date'])
        self.discriminate()
        self.by_id = {n['id']: n for n in self.data['nodes']}

    @staticmethod
    def _describe_fn(fn) -> str:
        """Compact text for a time-function dict: constants show as the bare
        number, anything else as kind(param=value, ...)."""
        if not isinstance(fn, dict):
            return str(fn)
        if canon_name(fn.get('kind', 'constant')) == 'constant':
            return f"{fn.get('value', 0):g}"
        params = ', '.join(f"{k}={v:g}" for k, v in fn.items() if k != 'kind')
        return f"{fn['kind']}({params})"

    def describe_criterion(self) -> str:
        """The stopping criterion as readable text: model ids resolved to names,
        durations rendered like the rest of the report, functions compacted."""
        parts = []
        for key, value in self.data['stopping_criterion'].items():
            if key == 'type':
                continue
            if key == 'models_goals':
                parts.append('models_goals: ' + ', '.join(
                    f"{self.models[mg['model']].name} = {mg['goal']}" for mg in value))
            elif key == 'models_probs':
                parts.append('models_probs: ' + ', '.join(
                    f"{self.models[mp['model']].name} = "
                    + ('reste' if mp['probability'] is None else self._describe_fn(mp['probability']))
                    for mp in value))
            elif key == 'gap':
                parts.append(f"gap = {self._describe_fn(value)}")
            elif key in ('timeout', 'grace_period') and isinstance(value, (int, float)) and value != float('inf'):
                parts.append(f"{key} = {kpis.fmt_duree(value)}")
            else:
                parts.append(f"{key} = {value}")
        return '; '.join(parts)

    def report(self, directory: str | None = None) -> str:
        if directory is None:
            stem = os.path.splitext(os.path.basename(self.filename))[0]
            directory = os.path.join('runs', f"{datetime.now():%Y-%m-%d_%H%M%S}_{stem}")
        buffers = [o for o in self.outlets.values() if isinstance(o, Buffer)]

        criterion = self.data['stopping_criterion']
        run_info = {
            'fichier': self.filename,
            'debut': self.data['start_date'],
            'fin': (self.sim_start + timedelta(minutes=env.now())).strftime('%d-%m-%Y %H:%M'),
            'temps_calcul': kpis.fmt_duree((perf_counter() - self.loaded_at) / 60),
            'critere_arret': criterion['type'],
            'critere_details': self.describe_criterion(),
        }
        kpis.write_report(
            directory,
            tasks=list(self.tasks.values()),
            buffers=buffers,
            piece_generator=self.piece_generator,
            run_info=run_info,
            sim_start=self.sim_start,
            operator_groups=list(self.operator_groups.values()),
            resources=list(self.resources.values())
        )
        graphs.write_graphs(
            os.path.join(directory, 'graphes'),
            tasks=list(self.tasks.values()),
            buffers=buffers,
            resources=list(self.resources.values()),
            operator_groups=list(self.operator_groups.values()),
            piece_generator=self.piece_generator,
            sim_start=self.sim_start
        )
        self.write_machine_report(directory, run_info)
        return directory

    def write_machine_report(self, directory: str, run_info: dict) -> None:
        """report.json + flow.json next to the CSVs: the raw (unformatted) KPI
        dicts keyed by node id, the graph-file map, and a snapshot of the flow
        that ran — everything the designer's results mode needs to dress the
        graph back up with numbers."""
        from simulation.outlet import BufferType
        import simulation as simulation_pkg

        criterion = self.data['stopping_criterion']
        exit_pieces = sum(len(b) for b in self.outlets.values()
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
            [b for b in self.outlets.values() if isinstance(b, Buffer)], self.piece_generator)
        task_kpi_rows = {id_: kpis.task_kpis(t) for id_, t in self.tasks.items()}
        data = {
            'format': 'flow-simulator-report',
            'version': 1,
            'run': {
                **run_info,
                'source_file': os.path.abspath(self.filename),
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
            'tasks_models': {id_: rows for id_, t in self.tasks.items()
                             if (rows := kpis.task_model_rows(t))},
            'buffers': {id_: kpis.buffer_kpis(b) for id_, b in self.outlets.items()
                        if isinstance(b, Buffer)},
            'operators': {id_: kpis.operator_kpis(g) for id_, g in self.operator_groups.items()},
            'resources': {id_: kpis.resource_kpis(r) for id_, r in self.resources.items()},
            'flux': flux,
            'flux_modeles': flux_modeles,
            'graphs': {
                'tasks': {id_: p for id_, t in self.tasks.items()
                          if (p := png('postes', f"occupation_{safe(t.name())}"))},
                'buffers': {id_: p for id_, b in self.outlets.items()
                            if isinstance(b, Buffer)
                            and (p := png('buffers', f"longueur_{safe(b.name())}"))},
                'operators': {id_: p for id_, g in self.operator_groups.items()
                              if (p := png('operateurs', f"disponibles_{safe(g.name())}"))},
                'resources': {id_: p for id_, r in self.resources.items()
                              if (p := png('ressources', f"stock_{safe(r.name())}"))},
                'models': {m.name: p for m in self.piece_generator.models
                           if (p := png('modeles', f"trajectoires_{safe(m.name)}"))},
                'production': png('modeles', 'production'),
                'encours': png('ligne', 'encours'),
                'attente': png('ligne', 'pieces_en_attente'),
            },
        }
        with open(os.path.join(directory, 'report.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        shutil.copyfile(self.filename, os.path.join(directory, 'flow.json'))

    def load_all(self) -> None:
        self.load_models()
        self.load_closing_days()
        self.load_shifts()
        self.load_resources()
        self.load_operators()
        self.load_non_scrap_buffers()
        self.load_routers(with_scrap=False)
        self.load_piece_generator()
        self.load_scrap_buffers()
        self.load_routers(with_scrap=True)
        self.load_piece_tasks()
        self.load_resource_tasks()
        self.load_shutdowns()
        self.load_breakdowns()
        self.load_stopping_criterion()
        self.loaded_at = perf_counter()

    def to_interval(self, interval: dict) -> Interval:
        return Interval(
            ShiftManager.minutes_between(self.sim_start, to_datetime(interval['start'])),
            ShiftManager.minutes_between(self.sim_start, to_datetime(interval['end']))
        )

    def make_models_configs(self, json_models_configs: dict) -> dict[Model, ModelConfig]:
        models_configs: dict[Model, ModelConfig] = {}
        durations: dict[str, Distribution] = {}

        for model_config in json_models_configs:
            model = self.models[model_config['model']]
            key = json.dumps(model_config['duration'], sort_keys=True)
            if key not in durations:
                durations[key] = make_distribution(model_config['duration'])
            duration = durations[key]
            resources = [(self.resources[r['resource']], r['value']) for r in model_config['resources']]
            models_configs[model] = ModelConfig(
                duration=duration,
                resources=resources,
                min_carrier_capacity=model_config['min_carrier_capacity'],
                max_carrier_capacity=model_config['max_carrier_capacity']
            )

        return models_configs

    def make_alternative(self, alternatives: list) -> Alternative:
        return Alternative(*[[(self.operator_groups[m['operator']], m['count']) for m in alt]
                             for alt in alternatives])

    def touches_scrap(self, router: dict) -> bool:
        for entry in router['buffer_probs']:
            target = self.by_id[entry['buffer']]
            if target['kind'] == 'Router':
                raise NotImplementedError('router-to-router chains are not supported')
            if same_name(target['buffer_type'], 'SCRAP'):
                return True
        return False

    def discriminate(self) -> None:
        self.per_kind: dict[str, list[dict]] = {}

        for node in self.data['nodes']:
            if node['kind'] not in self.per_kind:
                self.per_kind[node['kind']] = []
            self.per_kind[node['kind']].append(node)

    def load_models(self) -> None:
        self.models: dict[str, Model] = {}

        for model in self.data['models']:
            self.models[model['id']] = Model(model['name'])

        for model in self.data['models']:
            if model['parent'] is not None:
                self.models[model['id']].set_parent(self.models[model['parent']])

    def load_closing_days(self) -> None:
        self.closing_days: dict[str, date] = {}

        for closing_day in self.data['closing_days']:
            self.closing_days[closing_day['id']] = to_date(closing_day['date'])

    @staticmethod
    def _shift_date(when, k: int, rep: dict):
        """`when` (a date or datetime) moved forward by k repeat-translations: k*years
        and k*months as calendar arithmetic (the day is clamped to the target month, so
        29 Feb in a non-leap year lands on 28 Feb), then k*(weeks*7 + days) fixed days.
        Returns the same type as `when`, so a yearly repeat keeps the calendar date."""
        import calendar as _calendar
        months = k * (int(rep.get('years', 0)) * 12 + int(rep.get('months', 0)))
        total = when.year * 12 + (when.month - 1) + months
        year, month0 = divmod(total, 12)
        month = month0 + 1
        day = min(when.day, _calendar.monthrange(year, month)[1])
        shifted = when.replace(year=year, month=month, day=day)
        fixed = k * (int(rep.get('weeks', 0)) * 7 + int(rep.get('days', 0)))
        return shifted + timedelta(days=fixed) if fixed else shifted

    def _generate_shift(self, shift: dict, days_off: set,
                        custom_intervals=None, horizon=None) -> list[Interval]:
        """Generate one shift's intervals. The source dates (weekly horizon or custom
        intervals) and the days off may be overridden to render a translated copy."""
        match canon_name(shift['mode']):
            case 'weekly':
                working_days = [d['working'] for d in shift['days']]
                shifts_per_day = [[(to_minutes(s['start']), to_minutes(s['end'])) for s in d['intervals']]
                                  for d in shift['days']]
                start, end = horizon if horizon is not None else (to_date(shift['horizon']['start']),
                                                                  to_date(shift['horizon']['end']))
                return ShiftManager.generate_weekly_shifts(
                    sim_start=self.sim_start, shifts_per_day=shifts_per_day,
                    working_days=working_days, days_off=days_off, start=start, end=end)
            case 'custom':
                intervals = (custom_intervals if custom_intervals is not None
                             else [(to_datetime(i['start']), to_datetime(i['end']))
                                   for i in shift['custom_intervals']])
                return ShiftManager.generate_custom_shifts(
                    sim_start=self.sim_start, shifts=intervals, days_off=days_off)
            case _:
                raise NotImplementedError()

    def load_shifts(self) -> None:
        self.shifts: dict[str, list[Interval]] = {}

        for shift in self.data['shifts']:
            base_days_off = {self.closing_days[d] for d in shift['days_off']}
            intervals = self._generate_shift(shift, base_days_off)

            # Repeat: append `count` translated copies of the whole shift. Each copy k
            # is regenerated from source dates moved forward by k translations (see
            # _shift_date), with its days off shifted the same way — so a December block
            # repeated yearly keeps its dates (leap years handled) and gets that year's
            # equivalent days off. Union-merge keeps the result sorted and disjoint.
            rep = shift.get('repeat') or {}
            count = int(rep.get('count', 0))
            has_translation = any(int(rep.get(u, 0)) for u in ('years', 'months', 'weeks', 'days'))
            if count > 0 and has_translation:
                weekly = canon_name(shift['mode']) == 'weekly'
                pieces = list(intervals)
                for k in range(1, count + 1):
                    days_off_k = {self._shift_date(d, k, rep) for d in base_days_off}
                    if weekly:
                        horizon = (self._shift_date(to_date(shift['horizon']['start']), k, rep),
                                   self._shift_date(to_date(shift['horizon']['end']), k, rep))
                        pieces += self._generate_shift(shift, days_off_k, horizon=horizon)
                    else:
                        ci = [(self._shift_date(to_datetime(i['start']), k, rep),
                               self._shift_date(to_datetime(i['end']), k, rep))
                              for i in shift['custom_intervals']]
                        pieces += self._generate_shift(shift, days_off_k, custom_intervals=ci)
                pieces.sort(key=lambda iv: iv.start)
                merged: list[Interval] = []
                for iv in pieces:
                    if merged and iv.start <= merged[-1].end:
                        if iv.end > merged[-1].end:
                            merged[-1] = Interval(merged[-1].start, iv.end)
                    else:
                        merged.append(iv)
                intervals = merged

            self.shifts[shift['id']] = intervals

    def load_resources(self) -> None:
        self.resources: dict[str, Resource] = {}

        for resource in self.data['resources']:
            kwargs = {
                'name': resource['name'],
                'lifespan': float(resource['lifespan']),
                'capacity': resource['max_capacity'],
                'initial_capacity': resource['initial_capacity']
            }

            if resource['restockable']:
                kwargs['order_duration'] = make_distribution(resource['order_duration'])
                kwargs['delivery_duration'] = make_distribution(resource['delivery_duration'])
                kwargs['threshold'] = resource['threshold']
                self.resources[resource['id']] = RestockableResource(**kwargs)
            else:
                self.resources[resource['id']] = Resource(**kwargs)

    def load_operators(self) -> None:
        self.operator_groups: dict[str, OperatorGroup] = {}
        productivities: dict[str, Distribution] = {}

        for operator in self.data['operators']:
            key = json.dumps(operator['productivity'], sort_keys=True)
            if key not in productivities:
                productivities[key] = make_distribution(operator['productivity'])

            self.operator_groups[operator['id']] = OperatorGroup(
                name=operator['name'],
                capacity=operator['capacity'],
                shifts=join_shifts([self.shifts[id_] for id_ in operator['shifts']]),
                productivity=productivities[key]
            )

    def load_non_scrap_buffers(self) -> None:
        self.outlets: dict[str, Outlet] = {}
        self.scrap_buffers_ids = []

        for buffer in self.per_kind['Buffer']:
            if same_name(buffer['buffer_type'], 'SCRAP'):
                self.scrap_buffers_ids.append(buffer['id'])
                continue

            self.outlets[buffer['id']] = Buffer(
                name=buffer['name'],
                valid_models=[self.models[m] for m in buffer['valid_models']],
                buffer_type=lookup(STR_TO_BUFFER_TYPE, buffer['buffer_type'], 'buffer type'),
            )

    def load_routers(self, with_scrap: bool) -> None:
        for router in self.per_kind.get('Router', []):
            if self.touches_scrap(router) != with_scrap:
                continue
            self.outlets[router['id']] = Router({
                self.outlets[e['buffer']]: make_callable(e['probability']) if e['probability'] is not None else None
                for e in router['buffer_probs']})

    def load_piece_generator(self) -> None:
        assert len(self.per_kind['PieceGenerator']) == 1
        node = self.per_kind['PieceGenerator'][0]
        criterion = self.data['stopping_criterion']

        for id_ in node['outlets']:
            if id_ not in self.outlets:
                raise ValueError(f"piece generator outlet {id_} is (or routes into) a scrap buffer")

        # the generator emits during its own shifts; what it emits is set by the
        # stopping criterion: a fixed set of goals (ByPiecesProduced) or a stream at
        # a given gap and mix (ByTime)
        shifts = join_shifts([self.shifts[id_] for id_ in node['shifts']])
        outlets = [self.outlets[id_] for id_ in node['outlets']]

        match canon_name(criterion['type']):
            case 'bypiecesproduced':
                models_goals = {self.models[mg['model']]: mg['goal'] for mg in criterion['models_goals']}
                # a criterion carrying 'gap' fixes the pacing by hand; otherwise it is
                # computed from the shifts and goals, minus the optional grace period
                if criterion.get('gap') is not None:
                    pacing = {'gap': float(criterion['gap'])}
                else:
                    pacing = {'grace_period': float(criterion.get('grace_period', 0.0))}
                self.piece_generator = GoalPieceGenerator(
                    name=node['name'], models_goals=models_goals, shifts=shifts, outlets=outlets,
                    **pacing)
            case 'bytime':
                models = [self.models[mp['model']] for mp in criterion['models_probs']]
                model_probs = [make_callable(mp['probability']) if mp['probability'] is not None else None
                               for mp in criterion['models_probs']]
                self.piece_generator = RatePieceGenerator(
                    name=node['name'], models=models, shifts=shifts, outlets=outlets,
                    gap=make_callable(criterion['gap']), model_probs=model_probs)
            case _:
                raise NotImplementedError()

    def load_scrap_buffers(self) -> None:
        for id_ in self.scrap_buffers_ids:
            buffer = self.by_id[id_]
            self.outlets[buffer['id']] = Buffer(
                name=buffer['name'],
                valid_models=[self.models[m] for m in buffer['valid_models']],
                buffer_type=BufferType.SCRAP,
                piece_generator=self.piece_generator,
            )

    def load_piece_tasks(self) -> None:
        self.tasks: dict[str, Task] = {}

        for pt in self.per_kind.get('Task', []):
            piece_task_config = PieceTaskConfig(
                task_shifts=join_shifts([self.shifts[id_] for id_ in pt['task_shifts']]),
                startup_duration=make_distribution(pt['startup_duration']),
                loading_duration=make_distribution(pt['loading_duration']),
                startup_operators=self.make_alternative(pt['startup_operators']),
                loading_operators=self.make_alternative(pt['loading_operators']),
                operators=self.make_alternative(pt['operators']),
                operator_scope=lookup(STR_TO_SCOPE, pt['operator_scope'], 'operator scope'),
                resource_scope=lookup(STR_TO_SCOPE, pt['resource_scope'], 'resource scope'),
                min_carriers=pt['min_carriers'],
                max_capacity=pt['max_capacity'],
                timeout=float(pt['timeout']),
                priority=pt['priority'],
                admin=bool(pt.get('admin', False)),
                contiguous_carriers=pt['contiguous_carriers'],
                independent_carriers=pt['independent_carriers'],
                protocols=make_piece_protocols(pt['policies']),
                models_configs=self.make_models_configs(pt['models_configs']),
                piece_collector_type=lookup(STR_TO_PIECE_COLLECTOR_TYPE, pt['collector_type'], 'collector type')
            )
            self.tasks[pt['id']] = PieceTask(
                name=pt['name'],
                config=piece_task_config,
                inlets=[self.outlets[id_] for id_ in pt['bufs_in']],
                outlets=[self.outlets[id_] for id_ in pt['bufs_out']]
            )

    def load_resource_tasks(self) -> None:
        for rt in self.per_kind.get('ResourceTask', []):
            resource_task_config = ResourceTaskConfig(
                task_shifts=join_shifts([self.shifts[id_] for id_ in rt['task_shifts']]),
                startup_duration=make_distribution(rt['startup_duration']),
                loading_duration=make_distribution(rt['loading_duration']),
                startup_operators=self.make_alternative(rt['startup_operators']),
                loading_operators=self.make_alternative(rt['loading_operators']),
                operators=self.make_alternative(rt['operators']),
                operator_scope=lookup(STR_TO_SCOPE, rt['operator_scope'], 'operator scope'),
                resource_scope=lookup(STR_TO_SCOPE, rt['resource_scope'], 'resource scope'),
                min_carriers=rt['min_carriers'],
                max_capacity=rt['max_capacity'],
                timeout=float(rt['timeout']),
                priority=rt['priority'],
                admin=bool(rt.get('admin', False)),
                contiguous_carriers=rt['contiguous_carriers'],
                independent_carriers=rt['independent_carriers'],
                protocols=make_protocols(rt['policies']),
                non_transformed_resources=[(self.resources[r['resource']], r['value'])
                                           for r in rt['non_transformed_resources']],
                transformed_resources_salvageable=[(self.resources[r['resource']], r['proportion'], r['salvageable'])
                                                   for r in rt['transformed_resources']],
                resources_out_distr=[(self.resources[r['resource']],
                                      sim.Bounded(make_salabim_distribution(r['distribution']),
                                                  r['lowerbound'], r['upperbound']))
                                     for r in rt['resources_out']],
                duration=make_distribution(rt['duration']),
                resource_collector_type=lookup(STR_TO_RESOURCE_COLLECTOR_TYPE, rt['resource_collector_type'], 'resource collector type'),
                min_carrier_capacity=rt['min_carrier_capacity'],
                max_carrier_capacity=rt['max_carrier_capacity']
            )
            self.tasks[rt['id']] = ResourceTask(
                name=rt['name'],
                config=resource_task_config
            )

    def load_shutdowns(self) -> None:
        for task_node in self.per_kind.get('Task', []) + self.per_kind.get('ResourceTask', []):
            task = self.tasks[task_node['id']]
            for id_ in task_node['shutdowns']:
                shutdowns_node = self.by_id[id_]

                match canon_name(shutdowns_node.get('mode', 'custom')):
                    case 'custom':
                        intervals = [self.to_interval(i) for i in shutdowns_node['intervals']]
                    case 'generator':
                        generator = shutdowns_node['generator']
                        intervals = Shutdowns.generate_periodic_shutdown(
                            task=task,
                            in_between=generator['in_between'],
                            shutdown_duration=generator['duration'],
                            sim_start=self.sim_start,
                            start=to_datetime(generator['start']),
                            end=to_datetime(generator['end'])
                        )
                    case _:
                        raise NotImplementedError()

                match canon_name(shutdowns_node['shutdown_type']):
                    case 'flexible':
                        FlexibleShutdowns(task=task, intervals=intervals)
                    case 'nonflexible':
                        NonFlexibleShutdowns(task=task, intervals=intervals)
                    case _:
                        raise NotImplementedError()

    def load_breakdowns(self) -> None:
        for breakdown in self.per_kind.get('Breakdown', []):
            Breakdown(
                name=breakdown['name'],
                task=self.tasks[breakdown['task']],
                mtbf=make_mtbf(breakdown['mtbf']),
                mttr=make_distribution(breakdown['mttr']),
                outlets=[self.outlets[id_] for id_ in breakdown['outlets']]
            )

    def load_stopping_criterion(self) -> None:
        criterion = self.data['stopping_criterion']

        match canon_name(criterion['type']):
            case 'bytime':
                minutes = ShiftManager.minutes_between(self.sim_start, to_datetime(criterion['time']))
                self.stopping_criterion = ByTime(time=minutes)
            case 'bypiecesproduced':
                total = sum(mg['goal'] for mg in criterion['models_goals'])
                exit_buffer = next(self.outlets[b['id']] for b in self.per_kind['Buffer']
                                   if same_name(b['buffer_type'], 'EXIT'))
                self.stopping_criterion = ByPiecesProduced(
                    total=total,
                    exit_buffer=exit_buffer,
                    timeout=float(criterion['timeout'])
                )
            case _:
                raise NotImplementedError()

        SimulationStopper(criterion=self.stopping_criterion)