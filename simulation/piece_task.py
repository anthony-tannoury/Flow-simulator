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
from .operator import Alternative
from .protocols import *
from .component import Component


class PieceCollectorType(Enum):
    DISCRIMINATING_GREEDY = auto()
    NON_DISCRIMINATING_GREEDY = auto()
    DISCRIMINATING_ALTRUISTIC = auto()
    NON_DISCRIMINATING_ALTRUISTIC = auto()

    @staticmethod
    def is_discriminating(bct: PieceCollectorType) -> bool:
        return bct in (PieceCollectorType.DISCRIMINATING_GREEDY, PieceCollectorType.DISCRIMINATING_ALTRUISTIC)


class PieceCollector(Component):
    def setup(self, task: PieceTask) -> None:
        self.task = task
        self.collected_pieces: list[Piece] = []        
        self.allow_dispatch = sim.State(value=False)
        self.done = sim.State(value=False) 
        self.timeout_manager = PieceTimeoutManager(piece_collector=self)


class PieceTimeoutManager(Component):
    def setup(self, piece_collector: PieceCollector):
        self.piece_collector = piece_collector
        self.allow_dispatch = sim.State(value=False)

    def process(self):
        self.wait(self.allow_dispatch)
        self.wait(self.piece_collector.done, fail_delay=self.piece_collector.task.config.timeout)
        if not self.failed():
            return
        self.piece_collector.interrupt()
        if not self.piece_collector.collected_pieces:
            piece = self.from_store(self.piece_collector.task.inlets, filter=self.piece_collector.task.can_take, request_priority=self.piece_collector.task.config.priority)
            self.request((self.piece_collector.task.vacant_slots, 1), request_priority=self.piece_collector.task.config.priority)
            self.piece_collector.collected_pieces.append(piece)
        self.piece_collector.done.set(True)
        self.passivate()


class NonDiscriminatingGreedyPieceCollector(PieceCollector):
    def process(self):
        min_carrier_capacity = next(iter(self.task.config.models_configs.values())).min_carrier_capacity
        max_carrier_capacity = next(iter(self.task.config.models_configs.values())).max_carrier_capacity

        self.wait(self.allow_dispatch)
        self.timeout_manager.allow_dispatch.set(True)

        while len(self.collected_pieces) < min_carrier_capacity:
            self.request((self.task.vacant_slots, 1), request_priority=self.task.config.priority)
            piece = self.from_store(self.task.inlets, filter=self.task.can_take, request_priority=self.task.config.priority)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        while self.task.vacant_slots.available_quantity() > 0 and len(self.collected_pieces) < max_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take, fail_delay=0, request_priority=self.task.config.priority)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1), request_priority=self.task.config.priority)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        if not self.task.config.contiguous_carriers:
            remainder = max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder), request_priority=self.task.config.priority)

        self.done.set(True)
        self.passivate()


class DiscriminatingGreedyPieceCollector(PieceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        self.timeout_manager.allow_dispatch.set(True)

        present_models = [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]
        if not present_models:
            self.request((self.task.vacant_slots, 1), request_priority=self.task.config.priority)
            piece = self.from_store(self.task.inlets, filter=self.task.can_take, request_priority=self.task.config.priority)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)
            focus_on = piece.model
        else:
            focus_on = Counter(present_models).most_common(1)[0][0]

        min_carrier_capacity = self.task.config.models_configs[focus_on].min_carrier_capacity
        max_carrier_capacity = self.task.config.models_configs[focus_on].max_carrier_capacity

        while len(self.collected_pieces) < min_carrier_capacity:
            self.request((self.task.vacant_slots, 1), request_priority=self.task.config.priority)
            piece = self.from_store(self.task.inlets, filter=lambda p: self.task.can_take(p) and p.model is focus_on, request_priority=self.task.config.priority)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        while self.task.vacant_slots.available_quantity() > 0 and len(
                self.collected_pieces) < max_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=lambda p: self.task.can_take(p) and p.model is focus_on, fail_delay=0, request_priority=self.task.config.priority)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1), request_priority=self.task.config.priority)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        if not self.task.config.contiguous_carriers:
            remainder = max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder), request_priority=self.task.config.priority)

        self.done.set(True)
        self.passivate()


class NonDiscriminatingAltruisticPieceCollector(PieceCollector):
    def process(self):
        min_carrier_capacity = next(iter(self.task.config.models_configs.values())).min_carrier_capacity
        max_carrier_capacity = next(iter(self.task.config.models_configs.values())).max_carrier_capacity

        self.wait(self.allow_dispatch)
        self.timeout_manager.allow_dispatch.set(True)
        self.request((self.task.vacant_slots, min_carrier_capacity), request_priority=self.task.config.priority)

        while not self.collected_pieces:
            valid_pieces = [(piece, buffer) for buffer in self.task.inlets for piece in buffer if self.task.can_take(piece)]
            truncate = min(max_carrier_capacity, self.task.vacant_slots.available_quantity() + min_carrier_capacity)
            valid_pieces = valid_pieces[:truncate]

            if len(valid_pieces) >= min_carrier_capacity:
                valid_pieces = list(filter(lambda pb: pb[0] in pb[1], valid_pieces))
                
                if len(valid_pieces) < min_carrier_capacity:
                    continue
            
                if self.task.config.contiguous_carriers:
                    additional_slots_to_request = len(valid_pieces) - min_carrier_capacity
                else:
                    additional_slots_to_request = max_carrier_capacity - min_carrier_capacity
                self.request((self.task.vacant_slots, additional_slots_to_request), request_priority=self.task.config.priority)

                for piece, buffer in valid_pieces:
                    piece.leave(buffer)
                    self.collected_pieces.append(piece)
            else:
                self.wait(*[inlet.trigger for inlet in self.task.inlets])

        self.done.set(True)
        self.passivate()


class DiscriminatingAltruisticPieceCollector(PieceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        self.timeout_manager.allow_dispatch.set(True)

        while not (present_models := [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]):
            self.wait(*[inlet.trigger for inlet in self.task.inlets])

        focus_on = Counter(present_models).most_common(1)[0][0]
        min_carrier_capacity = self.task.config.models_configs[focus_on].min_carrier_capacity
        max_carrier_capacity = self.task.config.models_configs[focus_on].max_carrier_capacity
        self.request((self.task.vacant_slots, min_carrier_capacity), request_priority=self.task.config.priority)


        while not self.collected_pieces:
            valid_pieces = [(piece, buffer) for buffer in self.task.inlets for piece in buffer if (self.task.can_take(piece) and piece.model is focus_on)]
            truncate = min(max_carrier_capacity, self.task.vacant_slots.available_quantity() + min_carrier_capacity)
            valid_pieces = valid_pieces[:truncate]

            if len(valid_pieces) >= min_carrier_capacity:
                valid_pieces = list(filter(lambda pb: pb[0] in pb[1], valid_pieces))
                
                if len(valid_pieces) < min_carrier_capacity:
                    continue
            
                if self.task.config.contiguous_carriers:
                    additional_slots_to_request = len(valid_pieces) - min_carrier_capacity
                else:
                    additional_slots_to_request = max_carrier_capacity - min_carrier_capacity
                self.request((self.task.vacant_slots, additional_slots_to_request), request_priority=self.task.config.priority)

                for piece, buffer in valid_pieces:
                    piece.leave(buffer)
                    self.collected_pieces.append(piece)
            else:
                self.wait(*[inlet.trigger for inlet in self.task.inlets])

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

        assert isinstance(task.config, PieceTaskConfig)
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
        self.piece_collector.timeout_manager.cancel()
        self.piece_collector.cancel()
        place(self.piece_collector.collected_pieces, lifeboats)

        self.loaded.set(True)
        self.done.set(True)

        self.task.pending_carriers.remove(self)
        self.task.active_carriers.remove(self)
        self.cancel()

    @override
    def get_ideal_loading_duration(self):
        return self.task.config.loading_duration.sample_now()
    
    @override
    def get_ideal_duration(self):
        model = self.piece_collector.collected_pieces[0].model
        model_config = self.task.config.models_configs[model]
        return model_config.duration.sample_now()
    
    @override
    def request_resources(self, fail_at):
        model = self.piece_collector.collected_pieces[0].model
        mult = 1 if self.task.config.resource_scope is Scope.PER_BATCH else len(self.piece_collector.collected_pieces)
        resources = [(r, q*mult) for r, q in self.task.config.models_configs[model].resources]
        self.request(*resources, fail_at=fail_at)
        self.freeze_abort_if(self.failed())

    @override
    def successfully_end_process(self):
        self.piece_collector.cancel()
        place(self.piece_collector.collected_pieces, self.task.outlets)
        self.done.set(True)

        self.task.pending_carriers.remove(self)
        self.task.active_carriers.remove(self)


class PieceTask(Task, PickyPieceTaker):
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
    def handle_restock(self, demander: Component) -> None:
        assert isinstance(self.config, PieceTaskConfig)
        for config in self.config.models_configs.values():
            for resource, _ in config.resources:
                if isinstance(resource, RestockableResource):
                    resource.restock(demander=demander)
