from datetime import datetime, date, time
import json
import salabim as sim

from simulation.judgement_day import *
from simulation.piece import Model, PieceGenerator
from simulation.resource import Resource, RestockableResource
from simulation.function_generator import Linear, Exponential
from simulation.sampler import Distribution
from simulation.interval import Interval
from simulation.interrupters import NonFlexibleShutdowns, FlexibleShutdowns, Breakdown
from simulation.piece_task import PieceTask
from simulation.resource_task import ResourceTask
from simulation.shift_manager import ShiftManager


class Parser:
    def __init__(self, filename: str):
        with open(filename, 'r') as file:
            self.data = json.load(file)
        
        self.sim_start = datetime.strptime(self.data['start_date'], '%d-%m-%Y %H:%M')

    @staticmethod
    def to_time(time_str: str) -> time:
        return time.strptime(time_str, '%H:%M')

    @staticmethod
    def to_date(date_str: str) -> date:
        return date.strptime(date_str, '%d-%m-%Y')
    
    @staticmethod
    def to_datetime(datetime_str: str) -> datetime:
        return datetime.strptime(datetime_str, '%d-%m-%Y %H:%M')
    
    @staticmethod
    def build_distribution(distribution) -> Distribution:
        params = []
        for p in distribution['params']:
            match p['kind']:
                case 'constant':
                    params.append(p['value'])
                case 'linear':
                    params.append(Linear.generate(p['x1'], p['y1'], p['x2'], p['y2']))
                case 'exponential':
                    params.append(Exponential.generate(p['x1'], p['y1'], p['x2'], p['y2'], p['limit']))
                case _:
                    raise NotImplementedError()

        match distribution['dist_type']:
            case 'Constant':
                return Distribution(sim.Constant, *params)
            case 'Normal':
                return Distribution(sim.Normal, *params)
            case 'Exponential':
                return Distribution(sim.Exponential, *params)
            case _:
                raise NotImplementedError()
            
    def discriminate(self) -> None:
        self.per_kind = {}

        for node in self.data['nodes']:
            if node['kind'] not in self.per_kind:
                self.per_kind[node['kind']] = []
            self.per_kind[node['kind']].append(node)
    
    def load_models(self) -> None:
        self.models: dict[str, Model] = {}

        for m in self.data['models']:
            self.models[m['id']] = Model(m['name'])

        for m in self.data['models']:
            if m['parent'] is not None:
                self.models[m['id']].set_parent(self.models[m['parent']])

    def load_resources(self) -> None:
        self.resources: dict[str, Resource] = {}

        for r in self.data['resources']:
            kwargs = {
                'name': r['name'],
                'initial_capacity': r['initial_capacity'],
                'capacity': r['max_capacity'],
                'lifespan': float(r['lifespan'])
            }

            if r['restockable']:
                kwargs['order_duration'] = Parser.build_distribution(r['order_duration'])
                kwargs['delivery_duration'] = Parser.build_distribution(r['delivery_duration'])
                kwargs['threshold'] = r['threshold']
                self.resources[r['id']] = RestockableResource(**kwargs)
            else:
                self.resources[r['id']] = Resource(**kwargs)

    def load_shifts(self) -> None:
        self.shifts: dict[str, list[Interval]] = {}

        for s in self.data['shifts']:
            days_off = {Parser.to_date(day_off) for day_off in s['days_off']}

            if s['mode'] == 'weekly':
                shifts_per_day = []
                working_days = []
                for day in s['days']:
                    working_days.append(day['working'])
                    day_shifts = []
                    for i in day['intervals']:
                        start = Parser.to_time(i['start'])
                        end = Parser.to_time(i['end'])
                        day_shifts.append((start, end))
                    shifts_per_day.append(day_shifts)

                start = Parser.to_date(s['horizon']['start'])
                end = Parser.to_date(s['horizon']['end'])

                self.shifts[s['id']] = ShiftManager.generate_weekly_shifts(
                    sim_start=self.sim_start,
                    shifts_per_day=shifts_per_day,
                    working_days=working_days,
                    days_off=days_off,
                    start=start,
                    end=end
                )
            elif s['mode'] == 'custom':
                shifts = [(Parser.to_datetime(i['start']), Parser.to_datetime(i['end'])) for i in s['custom_intervals']]
                self.shifts['id'] = ShiftManager.generate_custom_shifts(
                    sim_start=self.sim_start,
                    shifts=shifts,
                    days_off=days_off
                )
