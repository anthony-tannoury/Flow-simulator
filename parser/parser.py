import json
import os
import salabim as sim

from time import perf_counter
from simulation import env, kpis, graphs

from datetime import date, time, datetime, timedelta
from simulation.piece_task import PieceTask, PieceTaskConfig, ModelConfig, PieceCollectorType, PieceProtocols
from simulation.resource_task import ResourceTask, ResourceTaskConfig, ResourceCollectorType
from simulation.piece import Model, GoalPieceGenerator, RatePieceGenerator
from simulation.task import Task, Scope, Protocols
from simulation.sampler import Sampler, Distribution, FailureRate
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
    'Triangular': sim.Triangular
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
        with open(filename, 'r') as file:
            self.data = json.load(file)
        self.sim_start = to_datetime(self.data['start_date'])
        self.discriminate()
        self.by_id = {n['id']: n for n in self.data['nodes']}

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
            'critere_details': ', '.join(f"{k} = {v}" for k, v in criterion.items() if k != 'type'),
        }
        kpis.write_report(
            directory,
            tasks=list(self.tasks.values()),
            buffers=buffers,
            piece_generator=self.piece_generator,
            run_info=run_info,
            sim_start=self.sim_start
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
        return directory

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

    def load_shifts(self) -> None:
        self.shifts: dict[str, list[Interval]] = {}

        for shift in self.data['shifts']:
            days_off = {self.closing_days[d] for d in shift['days_off']}

            match canon_name(shift['mode']):
                case 'weekly':
                    working_days = [d['working'] for d in shift['days']]
                    shifts_per_day = [[(to_minutes(s['start']), to_minutes(s['end'])) for s in d['intervals']] for d in shift['days']]
                    start = to_date(shift['horizon']['start'])
                    end = to_date(shift['horizon']['end'])
                    self.shifts[shift['id']] = ShiftManager.generate_weekly_shifts(
                        sim_start=self.sim_start,
                        shifts_per_day=shifts_per_day,
                        working_days=working_days,
                        days_off=days_off,
                        start=start,
                        end=end
                    )
                case 'custom':
                    intervals = [(to_datetime(i['start']), to_datetime(i['end'])) for i in shift['custom_intervals']]
                    self.shifts[shift['id']] = ShiftManager.generate_custom_shifts(
                        sim_start=self.sim_start,
                        shifts=intervals,
                        days_off=days_off
                    )
                case _:
                    raise NotImplementedError()

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
                self.piece_generator = GoalPieceGenerator(
                    name=node['name'], models_goals=models_goals, shifts=shifts, outlets=outlets)
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