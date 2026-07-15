import json
import salabim as sim

from datetime import date, time, datetime
from simulation.piece_task import PieceTask, PieceTaskConfig, ModelConfig, PieceCollectorType
from simulation.resource_task import ResourceTask, ResourceTaskConfig
from simulation.piece import Model, PieceGenerator
from simulation.task import Scope, Protocols
from simulation.sampler import Distribution
from simulation.function_generator import Linear, Exponential
from simulation.outlet import Outlet, Buffer, Router, BufferType
from simulation.judgement_day import ByTime, ByPiecesProduced
from simulation.interval import Interval
from simulation.shift_manager import ShiftManager
from simulation.resource import Resource, RestockableResource
from simulation.operator import Alternative, OperatorGroup
from typing import Callable


def to_date(date_str: str) -> date:
    return datetime.strptime(date_str, '%d-%m-%Y').date()

def to_time(time_str: str) -> time:
    return datetime.strptime(time_str, '%H:%M').time()

def to_datetime(datetime_str: str) -> datetime:
    return datetime.strptime(datetime_str, '%d-%m-%Y %H:%M')


def join_shifts(shifts: list[list[Interval]]) -> list[Interval]:
        joined = []
        for shift in shifts:
            joined.extend(shift)
        return joined


def make_callable(c: dict) -> float | Callable[[float], float]:
    match c['kind']:
        case 'constant':
            return c['value']
        case 'linear':
            return Linear.generate(c['x1'], c['y1'], c['x2'], c['y2'])
        case 'exponential':
            return Exponential.generate(c['x1'], c['y1'], c['x2'], c['y2'], c['limit'])
        case _:
            raise NotImplementedError()


def make_distribution(distribution: dict) -> Distribution:
        params = []
        for param in distribution['params']:
            params.append(make_callable(param))
        
        if distribution['dist_type'] not in DISTR_TYPE_TO_CLASS:
            raise NotImplementedError()

        return Distribution(DISTR_TYPE_TO_CLASS[distribution['dist_type']], *params)


def make_protocols(protocols: dict) -> Protocols:
    pass


DISTR_TYPE_TO_CLASS = {
    'Constant': sim.Constant,
    'Normal': sim.Normal,
    'Exponential': sim.Exponential
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

STR_TO_SCOPE = {
    'PER_UNIT': Scope.PER_UNIT,
    'PER_BATCH': Scope.PER_BATCH,
    'PER_TASK': Scope.PER_TASK
}


class Parser:
    def __init__(self, filename: str) -> None:
        with open(filename, 'r') as file:
            self.data = json.load(file)
        self.sim_start = to_datetime(self.data['start_date'])
        self.discriminate()
        self.by_id = {n['id']: n for n in self.data['nodes']}

    def make_models_configs(self, json_models_configs: dict) -> dict[Model, ModelConfig]:
        models_configs: dict[Model, ModelConfig] = {}

        for model_config in json_models_configs:
            model = self.models[model_config['model']]
            duration = make_distribution(model_config['duration'])
            resources = [(r['resource'], r['value']) for r in model_config['resources']]
            models_configs[model] = ModelConfig(
                duration=duration,
                resources=resources,
                min_carrier_capacity=model_config['min_carrier_capacity'],
                max_carrier_capacity=model_config['max_carrier_capacity']
            )

        return models_configs
    
    def make_alternative(self, operators: dict) -> Alternative:
        Alternative(*[[(self.operator_groups[og['id']], og['count']) for og in alt] for alt in operators])

    def touches_scrap(self, router: dict) -> bool:
        return any(self.by_id[e['buffer']]['buffer_type'] == 'SCRAP'
               for e in router['buffer_probs'])
     
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

            match shift['mode']:
                case 'weekly':
                    working_days = [d['working'] for d in shift['days']]
                    shifts_per_day = [[(to_time(s['start']), to_time(s['end'])) for s in d['intervals']] for d in shift['days']]
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
                    intervals = [(to_datetime(i['start']), to_datetime(i['end'])) for i in shift['intervals']]
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

        for operator in self.data['operators']:
            self.operator_groups[operator['id']] = OperatorGroup(
                name=operator['name'],
                capacity=operator['capacity'],
                shifts=join_shifts([self.shifts[id_] for id_ in operator['shifts']]),
                productivity=make_distribution(operator['productivity'])
            )

    def load_non_scrap_buffers(self) -> None:
        # First step in loading the flow so we initialize self.outlets here
        self.outlets: dict[str, Outlet] = {}
        self.scrap_buffers_ids = []

        for buffer in self.per_kind['Buffer']:
            if buffer['buffer_type'] == 'SCRAP':
                self.scrap_buffers_ids.append(buffer['id'])
                continue

            self.outlets[buffer['id']] = Buffer(
                capacity=float(buffer['capacity']),
                valid_models=[self.models[m] for m in buffer['valid_models']],
                buffer_type=STR_TO_BUFFER_TYPE[buffer['buffer_type']],
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
        piece_generator_node = self.per_kind['PieceGenerator'][0]

        models_goals = {
            mg['model']: mg['goal']
            for mg in piece_generator_node['models_goals']
        }
        shifts = join_shifts([self.shifts[shift['id']] for shift in piece_generator_node['shifts']])
        outlets = [self.outlets[id_] for id_ in piece_generator_node['outlets']]
        self.piece_generator = PieceGenerator(
            name=piece_generator_node['name'],
            models_goals=models_goals,
            shifts=shifts,
            outlets=outlets
        )

    def load_scrap_buffers(self) -> None:
        for id_ in self.scrap_buffers_ids:
            buffer = self.by_id[id_]
            self.buffers[buffer['id']] = Buffer(
                capacity=float(buffer['capacity']),
                valid_models=[self.models[m] for m in buffer['valid_models']],
                buffer_type=BufferType.SCRAP,
                piece_generator=self.piece_generator,
            )

    def load_piece_tasks(self) -> None:
        self.piece_tasks: dict[str, PieceTask] = {}

        for pt in self.per_kind['Task']:
            piece_task_config = PieceTaskConfig(
                task_shifts=join_shifts([self.shifts[id_] for id_ in pt['task_shifts']]),
                startup_duration=make_distribution(pt['startup_duration']),
                loading_duration=make_distribution(pt['loading_duration']),
                startup_operators=self.make_alternative(pt['startup_operators']),
                loading_operators=self.make_alternative(pt['loading_operators']),
                operators=self.make_alternative(pt['operators']),
                operator_scope=STR_TO_SCOPE[pt['operator_scope']],
                resource_scope=STR_TO_SCOPE[pt['resource_scope']],
                min_carriers=pt['min_carriers'],
                max_capacity=pt['max_capacity'],
                timeout=float(pt['timeout']),
                priority=pt['priority'],
                contiguous_carriers=pt['contiguous_carriers'],
                independent_carriers=pt['independent_carriers'],
                protocols=make_protocols(pt['policies']),
                models_configs=self.make_models_configs(pt['models_configs']),
                piece_collector_type=STR_TO_PIECE_COLLECTOR_TYPE[pt['collector_type']]
            )