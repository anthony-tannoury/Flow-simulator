import json
import salabim as sim

from datetime import date, time, datetime
from simulation.piece import Model, PieceGenerator
from simulation.sampler import Distribution
from simulation.function_generator import Linear, Exponential
from simulation.outlet import Outlet, Buffer, Router, BufferType
from simulation.judgement_day import ByTime, ByPiecesProduced
from simulation.interval import Interval
from simulation.shift_manager import ShiftManager
from simulation.resource import Resource, RestockableResource


def to_date(date_str: str) -> date:
    return date.strptime(date_str, '%d-%m-%Y')

def to_time(time_str: str) -> time:
    return time.strptime(time_str, '%H:%M')

def to_datetime(datetime_str: str) -> datetime:
    return datetime.strptime(datetime_str, '%d-%m-%Y %H:%M')


def join_shifts(shifts: list[list[Interval]]) -> list[Interval]:
        joined = []
        for shift in shifts:
            joined.extend(shift)
        return joined


def make_distribution(distribution: dict) -> Distribution:
        params = []
        for param in distribution['params']:
            match param['kind']:
                case 'constant':
                    param = param['value']
                case 'linear':
                    param = Linear.generate(param['x1'], param['y1'], param['x2'], param['y2'])
                case 'exponential':
                    param = Exponential.generate(param['x1'], param['y1'], param['x2'], param['y2'], param['limit'])
                case _:
                    raise NotImplementedError()
            params.append(param)
        
        if distribution['dist_type'] not in DISTR_TYPE_TO_CLASS:
            raise NotImplementedError()

        return Distribution(DISTR_TYPE_TO_CLASS[distribution['dist_type']], *params)


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


class Parser:
    def __init__(self, filename: str) -> None:
        with open(filename, 'r') as file:
            self.data = json.load(file)
        self.sim_start = to_datetime(self.data['start_date'])
        self.discriminate()
     
    def discriminate(self) -> None:
        self.per_kind: dict[str, list[dict]] = {}

        for node in self.data['nodes']:
            if node['kind'] not in self.per_kind:
                self.per_kind[node['kind']] = []
            self.per_kind[node['kind']] = node
        
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
            days_off = {to_date(d) for d in shift['days_off']}

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
                self.resources[resource['id']] = RestockableResource(**kwargs)
            else:
                self.resources[resource['id']] = Resource(**kwargs)

    def load_non_exit_buffers(self) -> None:
        self.buffers: dict[str, Outlet] = {}

        for buffer in self.per_kind['Buffer']:
            if buffer['buffer_type'] == 'EXIT':
                self.outlets[buffer['id']] = None
                continue
            valid_models = [self.models[model] for model in buffer['valid_models']]
            self.buffers[buffer['id']] = Buffer(
                capacity=float(buffer['capacity']),
                valid_models=valid_models,
                buffer_type=STR_TO_BUFFER_TYPE[buffer['buffer_type']]
            )

    def preload_routers(self) -> None:
        for router in self.per_kind['Router']:


    def load_piece_generator(self) -> None:
        assert len(self.per_kind['PieceGenerator']) == 1
        piece_generator_node = self.per_kind['PieceGenerator'][0]
        shifts = join_shifts(self.shifts[shift] for shift in piece_generator_node['shifts'])
        outlets = [self.buffers]