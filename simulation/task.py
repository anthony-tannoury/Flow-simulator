from __future__ import annotations

import salabim as sim

from simulation import env
from .component import Component
from .shift_manager import HasShifts, ShiftManager
from .interrupters import NonFlexibleShutdowns, FlexibleShutdowns
from .interval import Interval
from .operator import Alternative
from .sampler import Distribution
from .operator import OperatorGroup
from .protocols import *
from .ables import Dispatchable, Donnable

from abc import ABC, abstractmethod
from typing import override
from dataclasses import dataclass
from enum import Enum, auto


class Carrier(Component, Dispatchable, Donnable, ABC):
    def setup(self, task: Task):
        Dispatchable.__init__(self)
        Donnable.__init__(self)
        self.task = task
        self.loaded = sim.State(value=False)

    @abstractmethod
    def handle_restock(self) -> None:
        pass

    @abstractmethod
    def abort(self, *args) -> None:
        pass

    @abstractmethod
    def freeze_abort_if(self, condition: bool) -> None:
        pass

    def handle_operators(self, operators: list[tuple[OperatorGroup, int]], ideal_duration: float) -> float:
        if not operators:
            task_shift_constraint_decision = self.task.config.protocols.task_shift_constraint.decide(self.task.current_or_last_shift(), ideal_duration)
            self.freeze_abort_if(task_shift_constraint_decision is Action.ABORT)
            return ideal_duration
        
        productivity = operators[0][0].productivity

        match self.task.config.protocols.operators_self_conscious.decide():
            case ConsciousnessState.CONSCIOUS:
                duration = ideal_duration / productivity.sample_now()
            case ConsciousnessState.UNCONSCIOUS:
                duration = ideal_duration

        current_operator_shift = operators[0][0].current_or_last_shift()
        operator_shift_constraint_decision = self.task.config.protocols.operator_shift_constraint.decide(current_operator_shift, duration)
        task_shift_constraint_decision = self.task.config.protocols.task_shift_constraint.decide(self.task.current_or_last_shift(), duration)

        self.freeze_abort_if(operator_shift_constraint_decision is Action.ABORT or task_shift_constraint_decision is Action.ABORT)
        return duration

    def handle_batch_operators(self, operators: Alternative, earliest_deadline: float, ideal_duration: float, fail_before: float, handle_restock: bool, work_mode: str) -> None:
        recuperated = operators.request(demander=self, fail_at=earliest_deadline - fail_before, cap_now=True)
        self.freeze_abort_if(self.failed())
        assert recuperated is not None

        duration = self.handle_operators(recuperated, ideal_duration)

        if handle_restock:
            self.handle_restock()
            self.request_resources(fail_at=earliest_deadline - duration - (fail_before - ideal_duration))

        self.hold(duration, mode=work_mode)
        self.release(*recuperated)

    def handle_task_operators(self, earliest_deadline: float, ideal_duration: float) -> None:
        duration = self.handle_operators(self.task.task_operators, ideal_duration)
        self.handle_restock()
        self.request_resources(fail_at=earliest_deadline - duration)
        self.hold(duration, mode="processing")

    @abstractmethod
    def wait_for_collector(self, fail_at: float) -> None:
        pass

    @abstractmethod
    def get_ideal_loading_duration(self) -> float:
        pass

    @abstractmethod
    def get_ideal_duration(self) -> float:
        pass

    @abstractmethod
    def request_resources(self, fail_at: float) -> None:
        pass

    @abstractmethod
    def successfully_end_process(self) -> None:
        pass

    def process(self):
        start_time = env.now()
        non_flexible_shutdown_deadline = self.task.non_flexible_shutdowns.get_deadline()
        task_current_shift = self.task.current_or_last_shift()
        earliest_deadline = min(non_flexible_shutdown_deadline, self.task.config.protocols.task_shift_constraint.deadline(task_current_shift))
        self.freeze_abort_if(env.now() >= earliest_deadline)

        self.wait_for_collector(earliest_deadline)
        self.freeze_abort_if(self.failed())
        self.loaded.set(True)

        ideal_loading_duration = self.get_ideal_loading_duration()
        ideal_duration = self.get_ideal_duration()

        match self.task.config.protocols.pending_carrier_pre_task_shift_end.decide(self.task.config.min_carriers, len(self.task.pending_carriers)):
            case Action.WAIT:
                self.task.skip_downtime_check = True
            case Action.ABORT:
                self.task.skip_downtime_check = False

        self.freeze_abort_if(env.now() > earliest_deadline - (ideal_duration + ideal_loading_duration))
        self.wait(self.allow_dispatch, fail_at=earliest_deadline - (ideal_duration + ideal_loading_duration), cap_now=True, mode="wait_dispatch")
        self.freeze_abort_if(self.failed())

        delegate_restock_to_loading = not self.task.config.operators
        self.handle_batch_operators(self.task.config.loading_operators, earliest_deadline,
                                    ideal_loading_duration, ideal_duration + ideal_loading_duration,
                                    delegate_restock_to_loading, work_mode="loading")
        if self.task.config.operator_scope is Scope.PER_BATCH:
            self.handle_batch_operators(self.task.config.operators, earliest_deadline,
                                        ideal_duration, ideal_duration, not delegate_restock_to_loading,
                                        work_mode="processing")
        else:
            self.handle_task_operators(earliest_deadline, ideal_duration)

        if self.task.flexible_shutdowns.adapt(Interval(start_time, env.now())):
            self.task.is_frozen.set(True)

        if self.task.is_frozen() and not self.task.skip_frozen_check and not self.task.skip_downtime_check:
            self.task.release()

        self.successfully_end_process()


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
    
    def __getitem__(self, key):
        return self.carriers[key]


class Scope(Enum):
    PER_UNIT = auto()
    PER_BATCH = auto()
    PER_TASK = auto()


class TaskStarter(Component, Donnable):
    def setup(self, task: Task):
        Donnable.__init__(self)
        self.task = task
    
    def process(self):
        duration = self.task.config.startup_duration.sample_now()
        while (next_shutdown := self.task.get_earliest_shutdown()) is not None and env.now() + duration > next_shutdown.start:
            self.hold(till=next_shutdown.end)
        
        deadline = self.task.get_earliest_deadline()
        self.task.config.startup_operators.request(demander=self, fail_at=deadline - duration)
        if self.failed():
            self.task.is_frozen.set(True)
            self.done.set(True)
            return
        
        self.hold(duration)
        self.task.startup_times.tally(duration)  # the setup work itself, not the wait for the crew
        self.done.set(True)


class TaskShiftManager(ShiftManager):
    @override
    def on_enter(self, *args):
        assert isinstance(self.entity, Task)
        self.entity.is_frozen.set(False)
        super().on_enter(*args)


@dataclass
class Protocols:
    pending_carriers_pre_flexible_shutdowns: PendingCarriers
    pending_carrier_pre_task_shift_end: PendingCarriers
    operator_shift_constraint: ShiftConstraint
    task_shift_constraint: ShiftConstraint
    operators_self_conscious: SelfConsciousness


@dataclass
class TaskConfig:
    task_shifts: list[Interval]
    startup_duration: Distribution
    loading_duration: Distribution

    startup_operators: Alternative
    loading_operators: Alternative
    operators: Alternative
    operator_scope: Scope
    resource_scope: Scope

    min_carriers: int
    max_capacity: float
    contiguous_carriers: bool
    independent_carriers: bool
    timeout: float
    priority: int

    protocols: Protocols


class Task(Component, HasShifts, ABC):
    def setup(self, config: TaskConfig, carrier_type: type[Carrier]) -> None:
        if config.operator_scope is Scope.PER_UNIT:
            raise ValueError("Operator scope cannot be PER_UNIT")
        
        if config.resource_scope is Scope.PER_TASK:
            raise ValueError("Resource scope cannot be PER_TASK")

        if not 0 <= config.priority <= 10:
            raise ValueError("Task priority must be in [0,10]")
        
        if isinstance(config.protocols.task_shift_constraint, ConstrainedByShift) and isinstance(config.protocols.pending_carrier_pre_task_shift_end, WaitForCarriers):
            raise ValueError("Task cannot be constrained by shift and wait for carrier completion pre task shift end at the same time")

        HasShifts.__init__(self, config.task_shifts)

        self.shift_manager = TaskShiftManager(entity=self)

        self.request_priority = 10 - config.priority
        self.config = config

        self.non_flexible_shutdowns = NonFlexibleShutdowns(task=self, intervals=[])
        self.flexible_shutdowns = FlexibleShutdowns(task=self, intervals=[])
        self.is_in_breakdown = sim.State(value=False)
        self.is_in_shutdown = sim.State(value=False)
        self.is_frozen = sim.State(value=False)

        self.task_operators: list[tuple[OperatorGroup, int]] = []
        self.carrier_type = carrier_type
        self.vacant_slots = sim.Resource(capacity=config.max_capacity)
        self.started_up = False
        self.pending_carriers = CarrierTracker()
        self.active_carriers = CarrierTracker()

        # KPI instrumentation: finished carriers stay readable, tallies fill on deposit
        self.all_carriers: list[Carrier] = []
        self.batch_sizes = sim.Monitor("batch_sizes")
        self.cycle_times = sim.Monitor("cycle_times")
        self.startup_times = sim.Monitor("startup_times")
        self.pieces_in = 0  # pieces physically taken from the inlets (retries included)

        self.skip_frozen_check = False
        self.skip_downtime_check = False

    @abstractmethod
    def abort(self, *args):
        pass

    def get_earliest_shutdown(self) -> Interval | None:
        fs = self.flexible_shutdowns.get_next_shutdown()
        nfs = self.non_flexible_shutdowns.get_next_shutdown()

        if fs is not None and nfs is not None:
            return min(fs, nfs, key=lambda s: s.start)
        elif nfs is None:
            return fs
        return nfs
    
    def get_earliest_deadline(self) -> float:
        earliest_shutdown = self.get_earliest_shutdown()
        return earliest_shutdown.start if earliest_shutdown is not None else float('inf')

    def handle_startup(self) -> None:
        task_starter = TaskStarter(task=self)
        self.wait(task_starter.done)
        if self.is_frozen():
            return

        self.started_up = True

        if self.config.operator_scope is Scope.PER_TASK:
            deadline = min(self.non_flexible_shutdowns.get_deadline(), self.flexible_shutdowns.get_deadline())
            self.task_operators = self.config.operators.request(demander=self, fail_at=deadline)
            self.set_mode("")
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
    
            if (self.is_frozen() and not self.skip_frozen_check) or (not self.started_up):
                continue

            new_carrier = self.carrier_type(task=self)
            self.pending_carriers.add(new_carrier)
            self.all_carriers.append(new_carrier)
            self.wait(new_carrier.loaded)

            if len(self.pending_carriers) >= self.config.min_carriers:
                dispatched = []
                while self.pending_carriers:
                    carrier = self.pending_carriers.pop()
                    carrier.allow_dispatch.set(True)
                    dispatched.append(carrier)
                    self.active_carriers.add(carrier)

                self.skip_frozen_check = False
                self.skip_downtime_check = False

                if not self.config.independent_carriers:
                    self.wait(*[carrier.done for carrier in dispatched], all=True)

            elif self.is_frozen() and self.flexible_shutdowns.get_deadline() <= env.now():
                decision = self.config.protocols.pending_carriers_pre_flexible_shutdowns.decide(self.config.min_carriers, len(self.pending_carriers))
                match decision:
                    case Action.ABORT:
                        while self.pending_carriers:
                            self.pending_carriers[0].abort()
                    case Action.WAIT:
                        self.skip_frozen_check = True
