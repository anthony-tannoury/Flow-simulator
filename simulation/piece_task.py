import salabim as sim
from dataclasses import dataclass
from typing import override
from enum import Enum, auto
from collections import Counter

from .resource import Resource, RestockableResource
from .distribution import Distribution
from .task import TaskConfig, Task, Carrier, Scope
from .piece import Model, Piece, PickyPieceTaker
from .helpers import check_outlet_validity, place
from .outlet import Outlet, Buffer
from .protocols import *


class PieceCollectorType(Enum):
    DISCRIMINATING_GREEDY = auto()
    NON_DISCRIMINATING_GREEDY = auto()
    DISCRIMINATING_ALTRUISTIC = auto()
    NON_DISCRIMINATING_ALTRUISTIC = auto()

    @staticmethod
    def is_discriminating(bct: PieceCollectorType) -> bool:
        return bct in (PieceCollectorType.DISCRIMINATING_GREEDY, PieceCollectorType.DISCRIMINATING_ALTRUISTIC)


class PieceCollector(sim.Component):
    def setup(self, task: PieceTask) -> None:
        self.task = task
        self.collected_pieces: list[Piece] = []        
        self.allow_dispatch = sim.State(value=False)
        self.done = sim.State(value=False)             


class NonDiscriminatingGreedyPieceCollector(PieceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity))

        while len(self.collected_pieces) < self.task.config.min_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        while self.task.vacant_slots.available_quantity() > 0 and len(self.collected_pieces) < self.task.config.max_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take, fail_delay=0)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1))

            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        if not self.task.config.contiguous_carriers:
            remainder = self.task.config.max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder))

        self.done.set(True)
        self.passivate()


class DiscriminatingGreedyPieceCollector(PieceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity))

        present_models = [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]
        if not present_models:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)
            focus_on = piece.model
        else:
            focus_on = Counter(present_models).most_common(1)[0][0]

        while len(self.collected_pieces) < self.task.config.min_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=lambda p: self.task.can_take(p) and p.model is focus_on)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        while self.task.vacant_slots.available_quantity() > 0 and len(
                self.collected_pieces) < self.task.config.max_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=lambda p: self.task.can_take(p) and p.model is focus_on, fail_delay=0)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1))

            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        if not self.task.config.contiguous_carriers:
            remainder = self.task.config.max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder))

        self.done.set(True)
        self.passivate()


class NonDiscriminatingAltruisticPieceCollector(PieceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity))

        while not self.collected_pieces:
            valid_pieces = [(piece, buffer) for buffer in self.task.inlets for piece in buffer if
                            self.task.can_take(piece)]
            truncate = min(self.task.config.max_carrier_capacity,
                           self.task.vacant_slots.available_quantity() + self.task.config.min_carrier_capacity)
            valid_pieces = valid_pieces[:truncate]

            if len(valid_pieces) >= self.task.config.min_carrier_capacity:
                if self.task.config.contiguous_carriers:
                    additional_slots_to_request = len(valid_pieces) - self.task.config.min_carrier_capacity
                else:
                    additional_slots_to_request = self.task.config.max_carrier_capacity - self.task.config.min_carrier_capacity
                self.request((self.task.vacant_slots, additional_slots_to_request))
                for piece, buffer in valid_pieces:
                    piece.leave(buffer)
                    self.collected_pieces.append(piece)
            else:
                snapshot = [(buffer.arrival_signal, buffer.arrival_signal.get()) for buffer in self.task.inlets]
                self.wait(*[(state, lambda value, comp, state, old=old: value != old) for state, old in snapshot])

        self.done.set(True)
        self.passivate()


class DiscriminatingAltruisticPieceCollector(PieceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity))

        while not (present_models := [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]):
            snapshot = [(buffer.arrival_signal, buffer.arrival_signal.get()) for buffer in self.task.inlets]
            self.wait(*[(state, lambda value, comp, state, old=old: value != old) for state, old in snapshot])

        focus_on = Counter(present_models).most_common(1)[0][0]

        while not self.collected_pieces:
            valid_pieces = [(piece, buffer) for buffer in self.task.inlets for piece in buffer if
                            (self.task.can_take(piece) and piece.model is focus_on)]
            truncate = min(self.task.config.max_carrier_capacity,
                           self.task.vacant_slots.available_quantity() + self.task.config.min_carrier_capacity)
            valid_pieces = valid_pieces[:truncate]

            if len(valid_pieces) >= self.task.config.min_carrier_capacity:
                if self.task.config.contiguous_carriers:
                    additional_slots_to_request = len(valid_pieces) - self.task.config.min_carrier_capacity
                else:
                    additional_slots_to_request = self.task.config.max_carrier_capacity - self.task.config.min_carrier_capacity
                self.request((self.task.vacant_slots, additional_slots_to_request))
                for piece, buffer in valid_pieces:
                    piece.leave(buffer)
                    self.collected_pieces.append(piece)
            else:
                snapshot = [(buffer.arrival_signal, buffer.arrival_signal.get()) for buffer in self.task.inlets]
                self.wait(*[(state, lambda value, comp, state, old=old: value != old) for state, old in snapshot])

        self.done.set(True)
        self.passivate()


@dataclass
class ModelConfig:
    duration: Distribution
    resources: list[tuple[Resource, float]]
    min_carrier_capacity: int
    max_carrier_capacity: int


@dataclass
class PieceTaskConfig(TaskConfig):
    models_configs: dict[Model, ModelConfig]
    piece_collector_type: PieceCollectorType


class PieceCarrier(Carrier):
    def setup(self, task: PieceTask) -> None:
        super().setup(task=task)

        match task.config.piece_collector_type:
            case PieceCollectorType.DISCRIMINATING_GREEDY: 
                self.piece_collector = DiscriminatingGreedyPieceCollector(task=task)
            case PieceCollectorType.NON_DISCRIMINATING_GREEDY:
                self.piece_collector = NonDiscriminatingGreedyPieceCollector(task=task)
            case PieceCollectorType.DISCRIMINATING_ALTRUISTIC:
                self.piece_collector = DiscriminatingAltruisticPieceCollector(task=task)
            case PieceCollectorType.NON_DISCRIMINATING_ALTRUISTIC:
                self.piece_collector = NonDiscriminatingAltruisticPieceCollector(task=task)

    @override
    def abort(self, lifeboats: list[Outlet]):
        self.piece_collector.done.set(True)
        self.piece_collector.cancel()
        place(self.piece_collector.collected_pieces, lifeboats)

        self.loaded.set(True)
        self.done.set(True)

        self.task.pending_carriers.remove(self)
        self.task.active_carriers.remove(self)
        self.cancel()

    def freeze_abort_if(self, condition: bool) -> None:
        if condition:
            self.task.is_frozen.set(True)
            self.abort(self.task.inlets)

    def handle_loading(self, earliest_deadline: float, ideal_duration: float, ideal_loading_duration: float) -> None:
        loading_operators = self.task.config.loading_operators.request(demander=self, fail_at=earliest_deadline - (ideal_duration + ideal_loading_duration))
        self.freeze_abort_if(self.failed())
        assert loading_operators is not None
        productivity = loading_operators[0][0].productivity
        assert all(o.productivity == productivity for o, _ in loading_operators), "No because wait i was gonna say 40 at least"

        match self.task.config.protocols.operators_self_conscious.decide():
            case ConsciousnessState.CONSCIOUS:
                duration = ideal_loading_duration / productivity.sample_now()
            case ConsciousnessState.UNCONSCIOUS:
                duration = ideal_loading_duration

        loading_operators_current_shift = loading_operators[0][0].current_shift()
        operator_shift_constraint_decision = self.task.config.protocols.operator_shift_constraint.decide(loading_operators_current_shift, duration)
        task_shift_constraint_decision = self.task.config.protocols.task_shift_constraint.decide(loading_operators_current_shift, duration)

        self.freeze_abort_if(operator_shift_constraint_decision is Action.ABORT or task_shift_constraint_decision is Action.ABORT)
        self.hold(duration)
        self.release(*loading_operators)

    def process(self):
        non_flexible_shutdown_deadline = self.task.non_flexible_shutdowns.get_deadline()
        task_shift_deadline = self.task.current_shift().end
        earliest_deadline = min(non_flexible_shutdown_deadline, task_shift_deadline)

        self.piece_collector.allow_dispatch.set(True)
        self.wait(self.piece_collector.done, fail_at=earliest_deadline)
        self.freeze_abort_if(self.failed())

        model = self.piece_collector.collected_pieces[0].model
        model_config = self.task.config.models_configs[model]
        ideal_duration = model_config.duration.sample_now()
        ideal_loading_duration = self.task.config.loading_duration.sample_now()

        self.request(*model_config.resources, fail_at=earliest_deadline - (ideal_duration + ideal_loading_duration))
        self.freeze_abort_if(self.failed())

        if self.task.config.loading_operators:
            self.handle_loading(earliest_deadline, ideal_duration, ideal_loading_duration)

        

        





class PieceTask(Task, PickyPieceTaker):
    @override
    def setup(self, config: PieceTaskConfig, inlets: list[Buffer], outlets: list[Outlet]) -> None:
        if PieceCollectorType.is_discriminating(config.piece_collector_type):
            if not all(distr is config.models_configs.values()[0].duration for distr in config.models_configs.values()):
                raise ValueError("Piece task cannot have different durations for models and not discriminate")

        PickyPieceTaker.__init__(self, list(config.models_configs.keys()))
        check_outlet_validity(self, outlets)

        super().setup(config=config, carrier_type=PieceCarrier)
        self.inlets = inlets
        self.outlets = outlets

    @override
    def handle_restock(self) -> None:
        if self.config.operators_scope is Scope.PER_TASK:
            for config in self.config.models_configs.values():
                for group in config.resources:
                    for resource, _ in group:
                        if isinstance(resource, RestockableResource):
                            resource.restock(demander=self)


