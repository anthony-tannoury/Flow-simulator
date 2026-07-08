import salabim as sim
from dataclasses import dataclass
from typing import override
from enum import Enum, auto
from collections import Counter

from simulation import env
from .resource import Resource, RestockableResource
from .distribution import Distribution
from .task import TaskConfig, Task, Carrier, Scope
from .piece import Model, Piece, PickyPieceTaker
from .helpers import check_outlet_validity, place
from .outlet import Outlet, Buffer
from .protocols import *
from .component import Component
from .ables import Dispatchable, Donnable


class PieceCollectorType(Enum):
    DISCRIMINATING_GREEDY = auto()
    NON_DISCRIMINATING_GREEDY = auto()
    DISCRIMINATING_ALTRUISTIC = auto()
    NON_DISCRIMINATING_ALTRUISTIC = auto()

    @staticmethod
    def is_discriminating(bct: PieceCollectorType) -> bool:
        return bct in (PieceCollectorType.DISCRIMINATING_GREEDY, PieceCollectorType.DISCRIMINATING_ALTRUISTIC)


class PieceCollector(Component, Dispatchable, Donnable):
    def setup(self, task: PieceTask) -> None:
        Dispatchable.__init__(self)
        Donnable.__init__(self)
        self.task = task
        self.collected_pieces: list[Piece] = []

    def collect_until(self, deadline: float, target: int, piece_filter) -> bool:
        while len(self.collected_pieces) < target:
            self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority, fail_at=deadline)
            if self.failed():
                return True
            piece = self.from_store(self.task.inlets, filter=piece_filter, fail_at=deadline, request_priority=self.task.request_priority)
            if self.failed():
                self.release((self.task.vacant_slots, 1))
                return True
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)
        return False

    def ensure_one(self) -> None:
        if not self.collected_pieces:
            self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority)
            piece = self.from_store(self.task.inlets, filter=self.task.can_take, request_priority=self.task.request_priority)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

    def top_up(self, limit: int, piece_filter) -> None:
        while self.task.vacant_slots.available_quantity() > 0 and len(self.collected_pieces) < limit:
            piece = self.from_store(self.task.inlets, filter=piece_filter, fail_delay=0, request_priority=self.task.request_priority)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

    def block_remainder(self, max_carrier_capacity: int) -> None:
        if not self.task.config.contiguous_carriers:
            remainder = max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder), request_priority=self.task.request_priority)


class NonDiscriminatingGreedyPieceCollector(PieceCollector):
    def process(self):
        model_config = next(iter(self.task.config.models_configs.values()))

        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout

        timed_out = self.collect_until(deadline, model_config.min_carrier_capacity, self.task.can_take)
        if timed_out:
            self.ensure_one()
        else:
            self.top_up(model_config.max_carrier_capacity, self.task.can_take)

        self.block_remainder(model_config.max_carrier_capacity)
        self.done.set(True)
        self.passivate()


class DiscriminatingGreedyPieceCollector(PieceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout

        present_models = [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]
        if present_models:
            focus_on = Counter(present_models).most_common(1)[0][0]
        else:
            if self.collect_until(deadline, 1, self.task.can_take):
                self.ensure_one()
            focus_on = self.collected_pieces[0].model

        model_config = self.task.config.models_configs[focus_on]
        focus_filter = lambda p: self.task.can_take(p) and p.model is focus_on

        timed_out = self.collect_until(deadline, model_config.min_carrier_capacity, focus_filter)
        if timed_out:
            self.ensure_one()
        else:
            self.top_up(model_config.max_carrier_capacity, focus_filter)

        self.block_remainder(model_config.max_carrier_capacity)
        self.done.set(True)
        self.passivate()


class AltruisticMixin:
    def collect_batch(self: PieceCollector, deadline: float, min_carrier_capacity: int, max_carrier_capacity: int, piece_filter) -> bool:
        self.request((self.task.vacant_slots, min_carrier_capacity), request_priority=self.task.request_priority, fail_at=deadline)
        if self.failed():
            return True

        while not self.collected_pieces:
            valid_pieces = [(piece, buffer) for buffer in self.task.inlets for piece in buffer if piece_filter(piece)]
            truncate = min(max_carrier_capacity, self.task.vacant_slots.available_quantity() + min_carrier_capacity)
            valid_pieces = valid_pieces[:truncate]

            if len(valid_pieces) >= min_carrier_capacity - len(self.collected_pieces):
                for piece, buffer in valid_pieces:
                    if piece not in buffer:
                        continue
                    piece.leave(buffer)
                    self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority)
                    self.collected_pieces.append(piece)

                if not self.task.config.contiguous_carriers:
                    self.request((self.task.vacant_slots, max_carrier_capacity - len(valid_pieces)), request_priority=self.task.request_priority)
            else:
                self.wait(*[inlet.trigger for inlet in self.task.inlets], fail_at=deadline)
                if self.failed():
                    self.release((self.task.vacant_slots, min_carrier_capacity))
                    return True

        return False


class NonDiscriminatingAltruisticPieceCollector(PieceCollector, AltruisticMixin):
    def process(self):
        model_config = next(iter(self.task.config.models_configs.values()))

        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout

        if self.collect_batch(deadline, model_config.min_carrier_capacity, model_config.max_carrier_capacity, self.task.can_take):
            self.ensure_one()

        self.done.set(True)
        self.passivate()


class DiscriminatingAltruisticPieceCollector(PieceCollector, AltruisticMixin):
    def process(self):
        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout
        timed_out = False

        while not (present_models := [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]):
            self.wait(*[inlet.trigger for inlet in self.task.inlets], fail_at=deadline)
            if self.failed():
                timed_out = True
                break

        if not timed_out:
            focus_on = Counter(present_models).most_common(1)[0][0]
            model_config = self.task.config.models_configs[focus_on]
            timed_out = self.collect_batch(deadline, model_config.min_carrier_capacity, model_config.max_carrier_capacity,
                                           lambda p: self.task.can_take(p) and p.model is focus_on)

        if timed_out:
            self.ensure_one()

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
    def handle_restock(self) -> None:
        assert isinstance(self.task.config, PieceTaskConfig)
        for config in self.task.config.models_configs.values():
            for resource, _ in config.resources:
                if isinstance(resource, RestockableResource):
                    resource.restock(demander=self)

    @override
    def abort(self, *args):
        outlets = args[0] if args else self.task.inlets
        self.piece_collector.done.set(True)
        self.piece_collector.cancel()
        place(self.piece_collector.collected_pieces, outlets)

        self.loaded.set(True)
        self.done.set(True)

        self.task.pending_carriers.remove(self)
        self.task.active_carriers.remove(self)
        self.cancel()

    @override
    def freeze_abort_if(self, condition: bool) -> None:
        assert isinstance(self.task, PieceTask)
        if condition:
            self.task.is_frozen.set(True)
            self.abort(self.task.inlets)

    @override
    def wait_for_collector(self, fail_at: float) -> None:
        self.piece_collector.allow_dispatch.set(True)
        self.wait(self.piece_collector.done, fail_at=fail_at)

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
        if not PieceCollectorType.is_discriminating(config.piece_collector_type):
            first_config = next(iter(config.models_configs.values()))
            if not all(cfg.duration is first_config.duration for cfg in config.models_configs.values()):
                raise ValueError("Piece task cannot have different durations for models and not discriminate")
            
            if not all(cfg.min_carrier_capacity != first_config.min_carrier_capacity for cfg in config.models_configs.values()):
                raise Value("Piece task cannot have different min_carrrier_capacity for models and not discriminate")
            
            if not all(cfg.max_carrier_capacity != first_config.max_carrier_capacity for cfg in config.models_configs.values()):
                raise Value("Piece task cannot have different max_carrrier_capacity for models and not discriminate")

        PickyPieceTaker.__init__(self, list(config.models_configs.keys()))
        check_outlet_validity(self, outlets)

        super().setup(config=config, carrier_type=PieceCarrier)
        self.inlets = inlets
        self.outlets = outlets

    @override
    def abort(self, *args):
        for carrier in list(self.pending_carriers) + list(self.active_carriers):
            carrier.abort(*args)
        self.release()
        self.started_up = False
