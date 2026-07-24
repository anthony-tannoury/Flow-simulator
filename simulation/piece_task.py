from __future__ import annotations
import salabim as sim

from dataclasses import dataclass
from .compat import override
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
    

class AssociationType(Enum):
    ASSOCIATIVE = auto()
    DISSOCIATIVE = auto()
    PASSIVE = auto()


class PieceCollector(Component, Dispatchable, Donnable):
    def setup(self, task: PieceTask) -> None:
        Dispatchable.__init__(self)
        Donnable.__init__(self)
        self.task = task
        self.collected_pieces: list[Piece] = []

    @property
    def collected_weight(self) -> int:
        return sum(len(piece.family) for piece in self.collected_pieces)

    def check_piece_family_discrimination_compatibility(self, piece: Piece) -> None:
        assert isinstance(self.task.config, PieceTaskConfig)
        discriminating = PieceCollectorType.is_discriminating(self.task.config.piece_collector_type)
        different_models = any(sibling.model is not piece.model for sibling in piece.family)
        if discriminating and different_models:
            raise RuntimeError("Piece collector picked a cluster of different models for a discriminating task")

    def pick_piece(self, **kwargs) -> tuple[Piece, Buffer]:
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
        return self.from_store(**kwargs), self.from_store_store()

    def guard_carrier_capacity(self, piece: Piece, max_carrier_capacity: int) -> None:
        weight = len(piece.family)
        if weight > max_carrier_capacity:
            raise ValueError(f"cluster of {weight} pieces exceeds max_carrier_capacity "
                             f"{max_carrier_capacity} at task '{self.task.name()}'")

    def collect_until(self, deadline: float, target: int, piece_filter) -> bool:
        assert isinstance(self.task.config, PieceTaskConfig)
        while self.collected_weight < target:
            self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority, fail_at=deadline, mode="wait_slot")
            if self.failed():
                return True
            piece, buffer = self.pick_piece(store=self.task.inlets, filter=piece_filter, fail_at=deadline, request_priority=self.task.request_priority)
            if self.failed():
                self.release((self.task.vacant_slots, 1))
                return True
            self.check_piece_family_discrimination_compatibility(piece)
            weight = len(piece.family)
            max_carrier_capacity = self.task.config.get_model_config(piece.model).max_carrier_capacity
            self.guard_carrier_capacity(piece, max_carrier_capacity)
            if self.collected_weight + weight > max_carrier_capacity:
                self.release((self.task.vacant_slots, 1))
                piece.enter(buffer)
                return False

            self.request((self.task.vacant_slots, weight - 1), request_priority=self.task.request_priority, fail_at=deadline, mode="wait_slot")
            if self.failed():
                self.release((self.task.vacant_slots, 1))
                piece.enter(buffer)
                return True

            self.collected_pieces.append(piece)
            self.task.pieces_in += weight
        return False

    def ensure_one(self) -> None:
        assert isinstance(self.task.config, PieceTaskConfig)
        if self.collected_pieces:
            return
        self.request((self.task.vacant_slots, 1), request_priority=self.task.request_priority, mode="wait_slot")
        piece, buffer = self.pick_piece(store=self.task.inlets, filter=self.task.can_take, request_priority=self.task.request_priority)
        self.check_piece_family_discrimination_compatibility(piece)
        weight = len(piece.family)
        max_carrier_capacity = self.task.config.get_model_config(piece.model).max_carrier_capacity
        self.guard_carrier_capacity(piece, max_carrier_capacity)
        self.request((self.task.vacant_slots, weight - 1), request_priority=self.task.request_priority, mode="wait_slot")
        self.collected_pieces.append(piece)
        self.task.pieces_in += weight

    def top_up(self, limit: int, piece_filter) -> None:
        while self.collected_weight < limit and self.task.vacant_slots.available_quantity() > 0:
            piece, buffer = self.pick_piece(store=self.task.inlets, filter=piece_filter, fail_delay=0, request_priority=self.task.request_priority)
            if self.failed():
                break
            self.check_piece_family_discrimination_compatibility(piece)
            weight = len(piece.family)
            if self.collected_weight + weight > limit or weight > self.task.vacant_slots.available_quantity():
                piece.enter(buffer)
                break

            self.request((self.task.vacant_slots, weight), request_priority=self.task.request_priority, mode="wait_slot")
            self.collected_pieces.append(piece)
            self.task.pieces_in += weight

    def block_remainder(self, max_carrier_capacity: int) -> None:
        if not self.task.config.contiguous_carriers:
            remainder = max_carrier_capacity - self.collected_weight
            if remainder > 0:
                self.request((self.task.vacant_slots, remainder), request_priority=self.task.request_priority, mode="wait_slot")

    def present_counts(self) -> dict[Model, int]:
        cache = getattr(self.task, '_can_take_cache', None)
        if cache is None:
            cache = self.task._can_take_cache = {}
        counts: dict[Model, int] = {}
        for inlet in self.task.inlets:
            for model, n in inlet.model_counts.items():
                if n <= 0:
                    continue
                ok = cache.get(model)
                if ok is None:
                    ok = cache[model] = self.task.can_take(model)
                if ok:
                    counts[model] = counts.get(model, 0) + n
        return counts

    def choose_focus_model(self, counts: dict[Model, int]) -> Model:
        assert isinstance(self.task.config.protocols, PieceProtocols)
        assert isinstance(self.task.config, PieceTaskConfig)
        match self.task.config.protocols.batch_model_choice.decide():
            case ModelChoice.MOST_PRESENT:
                key = lambda model: -counts[model]
            case ModelChoice.FASTEST_TASK_DURATION:
                key = lambda model: self.task.config.get_model_config(model).duration.mean_now()
            case ModelChoice.SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY:
                key = lambda model: self.task.config.get_model_config(model).min_carrier_capacity - counts[model]
        best = min(key(model) for model in counts)
        tied = [model for model in counts if key(model) == best]
        if len(tied) == 1:
            return tied[0]
        tied_set = set(tied)
        for inlet in self.task.inlets:
            for piece in inlet:
                if piece.model in tied_set:
                    return piece.model
        return tied[0]

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

        counts = self.present_counts()
        if counts:
            focus_on = self.choose_focus_model(counts)
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

            available_extra = self.task.vacant_slots.available_quantity()
            selected: list[tuple[Piece, Buffer]] = []
            weight_sum = 0
            for piece, buffer in valid_pieces:
                self.check_piece_family_discrimination_compatibility(piece)
                weight = len(piece.family)
                if weight > max_carrier_capacity:
                    raise ValueError(f"cluster of {weight} pieces exceeds max_carrier_capacity "
                                     f"{max_carrier_capacity} at task '{self.task.name()}'")
                if weight_sum + weight > max_carrier_capacity:
                    break
                if max(0, weight_sum + weight - min_carrier_capacity) > available_extra:
                    break
                selected.append((piece, buffer))
                weight_sum += weight

            if weight_sum >= min_carrier_capacity:
                additional = weight_sum - min_carrier_capacity
                if additional > 0:
                    self.request((self.task.vacant_slots, additional), fail_delay=0, request_priority=self.task.request_priority, mode="wait_slot")
                    if self.failed():
                        additional = 0
                        trimmed, weight_sum = [], 0
                        for piece, buffer in selected:
                            if weight_sum + len(piece.family) > min_carrier_capacity:
                                break
                            trimmed.append((piece, buffer))
                            weight_sum += len(piece.family)
                        selected = trimmed

                selected = [pb for pb in selected if pb[0] in pb[1]]
                weight_sum = sum(len(pb[0].family) for pb in selected)
                if weight_sum < min_carrier_capacity:
                    if additional > 0:
                        self.release((self.task.vacant_slots, additional))
                    continue

                surplus = (min_carrier_capacity + additional) - weight_sum
                if surplus > 0:
                    self.release((self.task.vacant_slots, surplus))

                for piece, buffer in selected:
                    piece.leave(buffer)
                    self.collected_pieces.append(piece)
                    self.task.pieces_in += len(piece.family)

                if not self.task.config.contiguous_carriers:
                    self.request((self.task.vacant_slots, max_carrier_capacity - weight_sum), request_priority=self.task.request_priority, mode="wait_slot")
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

        while not (counts := self.present_counts()):
            self.wait(*[inlet.trigger for inlet in self.task.inlets], fail_at=deadline, mode="wait_pieces")
            if self.failed():
                timed_out = True
                break

        if not timed_out:
            focus_on = self.choose_focus_model(counts)
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
    association_type: AssociationType = AssociationType.PASSIVE

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

        if not self.task.active_carriers:
            self.task.release_task_operators()
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
        mult = 1 if self.task.config.resource_scope is Scope.PER_BATCH else self.piece_collector.collected_weight
        resources = [(r, q*mult) for r, q in self.task.config.get_model_config(model).resources]
        self.request(*resources, fail_at=fail_at, cap_now=True, mode="wait_materials")
        self.freeze_abort_if(self.failed())

    @override
    def successfully_end_process(self):
        assert isinstance(self.task, PieceTask)
        assert isinstance(self.task.config, PieceTaskConfig)

        self.piece_collector.set_mode("")
        self.piece_collector.cancel()

        pieces = self.piece_collector.collected_pieces
        self.task.batch_sizes.tally(self.piece_collector.collected_weight)
        self.task.cycle_times.tally(env.now() - self.creation_time())
        for piece in pieces:
            if len(piece.journal) < piece.JOURNAL_CAP:
                piece.journal.append(('task', self.task.name(), env.now()))

        match self.task.config.association_type:
            case AssociationType.ASSOCIATIVE:
                Piece.associate_all(pieces)
                tokens = [pieces[0]]
            case AssociationType.DISSOCIATIVE:
                tokens = []
                for piece in pieces:
                    family = piece.family
                    Piece.dissociate_all(family)
                    tokens.extend(family)
            case AssociationType.PASSIVE:
                tokens = pieces

        place(tokens, self.task.outlets)

        for token in tokens:
            scrapped = any(isinstance(q, Buffer) and q.buffer_type is BufferType.SCRAP for q in token.queues())
            for member in token.family:
                self.task.deposited[member.model] += 1
                if scrapped:
                    self.task.scrapped[member.model] += 1

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
        self.release_task_operators()
        self.started_up = False
