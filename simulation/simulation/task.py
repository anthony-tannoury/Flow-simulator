import salabim as sim

from simulation import env
from .component import Component
from .shift_manager import HasShifts, ShiftManager
from .interrupters import Interruptible, NonFlexibleShutdowns, FlexibleShutdowns
from .interval import Interval
from .helpers import sample_distr_or_func
from .operator import Alternative
from .distribution import Distribution
from .protocols import *

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import override, Callable
from enum import Enum, auto


class Carrier(Component, ABC):
    def setup(self, task: Task):
        self.task = task
        self.allow_dispatch = sim.State(value=False)
        self.loaded = sim.State(value=False)
        self.done = sim.State(value=False)

    @abstractmethod
    def abort(self, *args) -> None:
        pass


class CarrierTracker:
    def __init__(self):
        self.carriers: list[Carrier] = []
        self.num_carriers = sim.State(value=0)

    def add(self, carrier: Carrier) -> None:
        self.carriers.append(carrier)
        self.num_carriers.set(self.num_carriers() + 1)

    def remove(self, carrier: Carrier) -> None:
        if carrier in self.carriers:
            self.carriers.remove(carrier)
            self.num_carriers.set(self.num_carriers() - 1)

    def pop(self) -> Carrier:
        self.num_carriers.set(self.num_carriers() - 1)
        return self.carriers.pop()

    def __iter__(self):
        return iter(self.carriers)
    
    def __len__(self):
        return len(self.carriers)
    
    def __bool__(self):
        return bool(self.carriers)


class Scope(Enum):
    PER_UNIT = auto()
    PER_BATCH = auto()
    PER_TASK = auto()


class TaskStarter(Component):
    def setup(self, task: Task):
        self.task = task
        self.done = sim.State(value=False)
    
    def process(self):
        duration = self.task.config.startup_duration.sample_now()
        while (next_shutdown := self.task.get_earliest_shutdown()) is not None:
            if env.now() + duration > next_shutdown.start:
                self.hold(till=next_shutdown.end)
        
        deadline = self.task.get_earliest_deadline()
        self.task.config.startup_operators.request(demander=self, fail_at=deadline - duration)
        if self.failed():
            self.task.is_frozen.set(True)
            self.done.set(True)
            return
        
        self.hold(duration)
        self.done.set(True)


@dataclass
class Protocols:
    pending_carriers_pre_flexible_shutdowns: PendingCarriersPreFlexibleShutdownProtocol
    operator_shift_constraint: ShiftConstraint
    task_shift_constraint: ShiftConstraint
    operators_self_conscious: SelfConsciouness
    operation_relay: OperationRelay


@dataclass
class TaskConfig:
    task_shifts: list[Interval]
    startup_duration: Distribution
    loading_duration: Distribution

    startup_operators: Alternative
    loading_operators: Alternative
    operators: Alternative
    shutdown_operators: Alternative
    operator_scope: Scope
    resource_scope: Scope

    min_carriers: int
    max_capacity: float
    contiguous_carriers: bool
    independent_carriers: bool

    protocols: Protocols


class Task(Component, Interruptible, HasShifts, ABC):
    def setup(self, config: TaskConfig, carrier_type: type[Carrier]) -> None:
        if config.operator_scope is Scope.PER_UNIT:
            raise ValueError("Operator scope cannot be PER_UNIT")
        
        if config.resource_scope is Scope.PER_TASK:
            raise ValueError("Resource scope cannot be PER_TASK")

        Interruptible.__init__(self)
        HasShifts.__init__(self, config.task_shifts)

        self.shift_manager = ShiftManager(entity=self)

        self.config = config
        self.carrier_type = carrier_type
        self.vacant_slots = sim.Resource(capacity=config.max_capacity)
        self.started_up = False
        self.pending_carriers = CarrierTracker()
        self.active_carriers = CarrierTracker()

        self.skip_frozen_check = False
        self.skip_downtime_check = False

    @override
    def abort(self) -> None:
        pass

    @abstractmethod
    def handle_restock(self) -> None:
        pass

    def handle_startup(self) -> None:
        task_starter = TaskStarter(operators=self.config.startup_operators, duration=self.config.startup_duration)
        self.wait(task_starter.done)
        if self.is_frozen():
            return
        
        self.started_up = True
        
        if self.config.operator_scope is Scope.PER_TASK:
            deadline = min(self.non_flexible_shutdowns.get_deadline(), self.flexible_shutdowns.get_deadline())
            self.config.operators.request(demander=self, fail_at=deadline)
            if self.failed():
                self.is_frozen.set(True)

    def process(self):
        while True:
            states = [self.is_in_breakdown, self.is_in_shutdown]
            if not self.skip_frozen_check:
                states.append(self.is_frozen)
            if not self.skip_downtime_check:
                states.append(self.is_in_downtime)
            self.wait(*[(state, False) for state in states], all=True)

            if not self.started_up:
                self.handle_startup()

            if self.is_frozen() and not self.skip_frozen_check:
                continue

            if self.config.operators_scope is Scope.PER_TASK:
                self.handle_restock()

            new_carrier = self.carrier_type(task=self)
            self.pending_carriers.add(new_carrier)

            if len(self.pending_carriers) >= self.config.min_carriers:
                while self.pending_carriers:
                    carrier = self.pending_carriers.pop()
                    carrier.allow_dispatch.set(True)
                    self.active_carriers.add(carrier)

                self.skip_frozen_check = False

                if not self.config.independent_carriers:
                    self.wait(*[carrier.done for carrier in self.pending_carriers], all=True)

            elif self.is_frozen():
                decision = self.config.protocols.pending_carriers_pre_flexible_shutdowns.decide(self.config.min_carriers, len(self.pending_carriers))
                match decision:
                    case Action.ABORT:
                        for carrier in self.pending_carriers():
                            carrier.abort(self.inlets)
                    case Action.WAIT:
                        self.skip_frozen_check = True


                


                



        

'''
Paramètres de rampup :
- tps de cycle (manuel)
- taux de rebuts
- mtbf
- mttr
- Nouvelles machines qui deviennent dispo au cours de la simulation
'''