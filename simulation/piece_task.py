from __future__ import annotations
import salabim as sim

from dataclasses import dataclass
from typing import override
from enum import Enum, auto
from collections import Counter

from simulation import env
from .resource import Resource, RestockableResource
from .sampler import Distribution
from .task import TaskConfig, Task, Carrier, Scope, Protocols
from .piece import Model, Piece, PickyPieceTaker
from .helpers import check_outlet_validity, place
from .outlet import Outlet, Buffer, BufferType
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

    def pick_piece(self, **kwargs) -> Piece:
        assert isinstance(self.task.config.protocols, PieceProtocols)

        stores = kwargs['store'] if isinstance(kwargs['store'], list) else [kwargs['store']]
        piece_filter = kwargs.get('filter', lambda _: True)

        pieces = [(piece, buffer) for buffer in stores for piece in buffer if piece_filter(piece)]
        if pieces:
            match self.task.config.protocols.piece_exit_order.decide():
                case ExitOrder.FIRST_IN_FIRST_OUT:
                    target = min(pieces, key=lambda pb: pb[0].enter_time(pb[1]))[0]
                case ExitOrder.FIRST_CREATED_FIRST_OUT:
                    target = min(pieces, key=lambda pb: pb[0].creation_time())[0]
            kwargs['filter'] = lambda piece: piece is target

        kwargs.setdefault('mode', 'wait_pieces')
        return self.from_store(**kwargs)

    def collect_until(self, deadline: float, target: int, piece_filter) -> bool:
        while len(self.collected_pieces) < target:
            self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority, fail_at=deadline, mode="wait_slot")
            if self.failed():
                return True
            piece = self.pick_piece(store=self.task.inlets, filter=piece_filter, fail_at=deadline, request_priority=self.task.request_priority)
            if self.failed():
                self.release((self.task.vacant_slots, 1))
                return True
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)
            self.task.pieces_in += 1
        return False

    def ensure_one(self) -> None:
        if not self.collected_pieces:
            self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority, mode="wait_slot")
            piece = self.pick_piece(store=self.task.inlets, filter=self.task.can_take, request_priority=self.task.request_priority)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)
            self.task.pieces_in += 1

    def top_up(self, limit: int, piece_filter) -> None:
        while self.task.vacant_slots.available_quantity() > 0 and len(self.collected_pieces) < limit:
            piece = self.pick_piece(store=self.task.inlets, filter=piece_filter, fail_delay=0, request_priority=self.task.request_priority)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority, mode="wait_slot")
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)
            self.task.pieces_in += 1

    def block_remainder(self, max_carrier_capacity: int) -> None:
        if not self.task.config.contiguous_carriers:
            remainder = max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder), request_priority=self.task.request_priority, mode="wait_slot")

    def get_focus_model(self, present_models: list[Model]) -> Model:
        assert isinstance(self.task.config.protocols, PieceProtocols)
        assert isinstance(self.task.config, PieceTaskConfig)
        match self.task.config.protocols.batch_model_choice.decide():
            case ModelChoice.MOST_PRESENT:
                return Counter(present_models).most_common(1)[0][0]
            case ModelChoice.FASTEST_TASK_DURATION:
                return min(present_models, key=lambda model: self.task.config.get_model_config(model).duration.mean_now())
            case ModelChoice.SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY:
                counter = Counter(present_models)
                return min(present_models, key=lambda model: self.task.config.get_model_config(model).min_carrier_capacity - counter[model])

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
        self.set_mode("")
        self.done.set(True)
        self.passivate()


class DiscriminatingGreedyPieceCollector(PieceCollector):
    def process(self):
        assert isinstance(self.task.config, PieceTaskConfig)
        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout

        present_models = [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]
        if present_models:
            focus_on = self.get_focus_model(present_models)
        else:
            if self.collect_until(deadline, 1, self.task.can_take):
                self.ensure_one()
            focus_on = self.collected_pieces[0].model

        model_config = self.task.config.get_model_config(focus_on)
        focus_filter = lambda p: self.task.can_take(p) and p.model is focus_on

        timed_out = self.collect_until(deadline, model_config.min_carrier_capacity, focus_filter)
        if timed_out:
            self.ensure_one()
        else:
            self.top_up(model_config.max_carrier_capacity, focus_filter)

        self.block_remainder(model_config.max_carrier_capacity)
        self.set_mode("")
        self.done.set(True)
        self.passivate()


class AltruisticMixin:
    def collect_batch(self, deadline: float, min_carrier_capacity: int, max_carrier_capacity: int, piece_filter) -> bool:
        assert isinstance(self, PieceCollector)
        assert isinstance(self.task.config.protocols, PieceProtocols)

        self.request((self.task.vacant_slots, min_carrier_capacity), request_priority=self.task.request_priority, fail_at=deadline, mode="wait_slot")
        if self.failed():
            return True

        while not self.collected_pieces:
            valid_pieces = [(piece, buffer) for buffer in self.task.inlets for piece in buffer if piece_filter(piece)]

            match self.task.config.protocols.piece_exit_order.decide():
                case ExitOrder.FIRST_IN_FIRST_OUT:
                    valid_pieces.sort(key=lambda pb: pb[0].enter_time(pb[1]))
                case ExitOrder.FIRST_CREATED_FIRST_OUT:
                    valid_pieces.sort(key=lambda pb: pb[0].creation_time())
            
            truncate = min(max_carrier_capacity, self.task.vacant_slots.available_quantity() + min_carrier_capacity)
            valid_pieces = valid_pieces[:truncate]

            if len(valid_pieces) >= min_carrier_capacity:
                additional = len(valid_pieces) - min_carrier_capacity
                if additional > 0:
                    self.request((self.task.vacant_slots, additional), fail_delay=0, request_priority=self.task.request_priority, mode="wait_slot")
                    if self.failed():
                        additional = 0
                        valid_pieces = valid_pieces[:min_carrier_capacity]

                valid_pieces = [pb for pb in valid_pieces if pb[0] in pb[1]]
                if len(valid_pieces) < min_carrier_capacity:
                    if additional > 0:
                        self.release((self.task.vacant_slots, additional))
                    continue

                surplus = additional - (len(valid_pieces) - min_carrier_capacity)
                if surplus > 0:
                    self.release((self.task.vacant_slots, surplus))

                for piece, buffer in valid_pieces:
                    piece.leave(buffer)
                    self.collected_pieces.append(piece)
                    self.task.pieces_in += 1

                if not self.task.config.contiguous_carriers:
                    self.request((self.task.vacant_slots, max_carrier_capacity - len(valid_pieces)), request_priority=self.task.request_priority, mode="wait_slot")
            else:
                self.wait(*[inlet.trigger for inlet in self.task.inlets], fail_at=deadline, mode="wait_pieces")
                if self.failed():
                    self.release((self.task.vacant_slots, min_carrier_capacity))
                    return True

        return False


class NonDiscriminatingAltruisticPieceCollector(PieceCollector, AltruisticMixin):
    def process(self):
        assert isinstance(self.task.config, PieceTaskConfig)
        model_config = next(iter(self.task.config.models_configs.values()))

        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout

        if self.collect_batch(deadline, model_config.min_carrier_capacity, model_config.max_carrier_capacity, self.task.can_take):
            self.ensure_one()

        self.set_mode("")
        self.done.set(True)
        self.passivate()


class DiscriminatingAltruisticPieceCollector(PieceCollector, AltruisticMixin):
    def process(self):
        assert isinstance(self.task.config, PieceTaskConfig)
        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout
        timed_out = False

        while not (present_models := [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]):
            self.wait(*[inlet.trigger for inlet in self.task.inlets], fail_at=deadline, mode="wait_pieces")
            if self.failed():
                timed_out = True
                break

        if not timed_out:
            focus_on = self.get_focus_model(present_models)
            model_config = self.task.config.get_model_config(focus_on)
            timed_out = self.collect_batch(deadline, model_config.min_carrier_capacity, model_config.max_carrier_capacity,
                                           lambda p: self.task.can_take(p) and p.model is focus_on)

        if timed_out:
            self.ensure_one()

        self.set_mode("")
        self.done.set(True)
        self.passivate()


@dataclass
class ModelConfig:
    duration: Distribution
    resources: list[tuple[Resource, float]]
    min_carrier_capacity: int
    max_carrier_capacity: int


@dataclass
class PieceProtocols(Protocols):
    piece_exit_order: PieceExitOrder
    batch_model_choice: ModelChoiceCriteria

@dataclass
class PieceTaskConfig(TaskConfig):
    models_configs: dict[Model, ModelConfig]
    piece_collector_type: PieceCollectorType

    def get_model_config(self, model: Model) -> ModelConfig:
        m = model
        while m is not None:
            if m in self.models_configs:
                return self.models_configs[m]
            m = m.parent
        raise KeyError(f"No model config for {model.name} or any of its ancestors")


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
        assert isinstance(self.task, PieceTask)
        outlets = args[0] if args else self.task.inlets
        place(self.piece_collector.collected_pieces, outlets)
        self.piece_collector.set_mode("")
        self.piece_collector.done.set(True)
        self.piece_collector.cancel()

        self.set_mode("")
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
        self.wait(self.piece_collector.done, fail_at=fail_at, mode="collecting")

    @override
    def get_ideal_loading_duration(self):
        return self.task.config.loading_duration.sample_now()
    
    @override
    def get_ideal_duration(self):
        assert isinstance(self.task.config, PieceTaskConfig)
        model = self.piece_collector.collected_pieces[0].model
        model_config = self.task.config.get_model_config(model)
        return model_config.duration.sample_now()
    
    @override
    def request_resources(self, fail_at):
        assert isinstance(self.task.config, PieceTaskConfig)
        model = self.piece_collector.collected_pieces[0].model
        mult = 1 if self.task.config.resource_scope is Scope.PER_BATCH else len(self.piece_collector.collected_pieces)
        resources = [(r, q*mult) for r, q in self.task.config.get_model_config(model).resources]
        self.request(*resources, fail_at=fail_at, cap_now=True, mode="wait_materials")
        self.freeze_abort_if(self.failed())

    @override
    def successfully_end_process(self):
        assert isinstance(self.task, PieceTask)
        self.piece_collector.set_mode("")
        self.piece_collector.cancel()

        pieces = self.piece_collector.collected_pieces
        self.task.batch_sizes.tally(len(pieces))
        self.task.cycle_times.tally(env.now() - self.creation_time())
        place(pieces, self.task.outlets)
        for piece in pieces:
            self.task.deposited[piece.model] += 1
            if any(isinstance(q, Buffer) and q.buffer_type is BufferType.SCRAP for q in piece.queues()):
                self.task.scrapped[piece.model] += 1

        self.set_mode("")
        self.done.set(True)

        self.task.pending_carriers.remove(self)
        self.task.active_carriers.remove(self)


class PieceTask(Task, PickyPieceTaker):
    def setup(self, config: PieceTaskConfig, inlets: list[Buffer], outlets: list[Outlet]) -> None:
        if not PieceCollectorType.is_discriminating(config.piece_collector_type):
            first_config = next(iter(config.models_configs.values()))
            if not all(cfg.duration is first_config.duration for cfg in config.models_configs.values()):
                raise ValueError("Piece task cannot have different durations for models and not discriminate")
            
            if not all(cfg.min_carrier_capacity == first_config.min_carrier_capacity for cfg in config.models_configs.values()):
                raise ValueError("Piece task cannot have different min_carrrier_capacity for models and not discriminate")
            
            if not all(cfg.max_carrier_capacity == first_config.max_carrier_capacity for cfg in config.models_configs.values()):
                raise ValueError("Piece task cannot have different max_carrrier_capacity for models and not discriminate")

        PickyPieceTaker.__init__(self, list(config.models_configs.keys()))
        for inlet in inlets:
            if not inlet.can_flush_into(self):
                raise ValueError("Inlets must be able to flush into piece task")
        check_outlet_validity(self, outlets)

        super().setup(config=config, carrier_type=PieceCarrier)
        self.inlets = inlets
        self.outlets = outlets
        self.deposited: Counter[Model] = Counter()
        self.scrapped: Counter[Model] = Counter()

    @override
    def abort(self, *args):
        for carrier in reversed(list(self.pending_carriers) + list(self.active_carriers)):
            carrier.abort(*args)
        self.release()
        self.started_up = False
