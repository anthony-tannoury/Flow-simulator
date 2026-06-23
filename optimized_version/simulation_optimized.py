"""
Performance-optimized variant of simulation.py.

Same modelling semantics as simulation.py, but the CPU-heavy ``standby()`` polling
in the batch collectors has been replaced with event-driven waiting:

* GreedyBatchCollector blocks on ``from_store(...)``, which salabim wakes natively
  the instant a matching piece is added to an input buffer (no per-event polling).
* AltruisticBatchCollector blocks on lightweight per-buffer "arrival" signal states
  that are created lazily and only touched when an altruistic collector is present,
  so a greedy-only model pays nothing for them.

Other optimizations:
* PickyPieceTaker.can_take is memoized per resolved model (the capability walk over
  the model parent chain is computed once per distinct model).
* Pieces carry a creation timestamp so monitors can measure lead time to a buffer.
* HardBuffers expose hooks (arrival monitors) used by graph_parser_advanced.

Because event ordering changes, a given random seed will NOT reproduce the exact
same numbers as simulation.py, but the model behaves identically in distribution.
The public surface (class names, ``env``, ``sim``) is unchanged so it is a drop-in
replacement for ``from simulation import *``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
import salabim as sim

sim.yieldless(True)
env = sim.Environment(random_seed=42, trace=False)


#########
# UTILS #
#########

class Interval:
    def __init__(self, start: float, end: float) -> None:
        if start > end:
            raise ValueError("Interval start must be before its end")
        self.start = start
        self.end = end

    @property
    def length(self):
        return self.end - self.start

    @staticmethod
    def disjoint(int1: Interval, int2: Interval) -> bool:
        if int1.start > int2.start:
            int1, int2 = int2, int1
        return int1.end < int2.start



##########
# MODELS #
##########

class Model:
    def __init__(self, name: str, parent: Model | None = None) -> None:
        self.name = name
        self.parent = parent

    def __repr__(self) -> str:
        if self.parent is not None:
            return f"{ {self.name}, {self.parent.name} }"
        return f"{ {self.name} }"


#########
# PIECE #
#########

class Piece(sim.Component):
    ID = 0

    def setup(self, model: Model) -> None:
        self.model = model
        self.id = str(Piece.ID).zfill(6)
        Piece.ID += 1


class PickyPieceTaker:
    def __init__(self, valid_models: list[Model]) -> None:
        self.valid_models = valid_models
        # Memoize the capability walk: each distinct resolved model is classified once.
        self._take_cache: dict[Model, bool] = {}

    def can_take(self, obj: Piece | Model) -> bool:
        model = obj.model if isinstance(obj, Piece) else obj
        cached = self._take_cache.get(model)
        if cached is not None:
            return cached
        can_take_piece = False
        m = model
        while m is not None and not can_take_piece:
            can_take_piece |= m in self.valid_models
            m = m.parent
        self._take_cache[model] = can_take_piece
        return can_take_piece

    def can_flush_into(self, other: PickyPieceTaker):
        for model in self.valid_models:
            if not other.can_take(model):
                return False
        return True

    @staticmethod
    def disjoint(ppt1: PickyPieceTaker, ppt2: PickyPieceTaker) -> bool:
        for model in ppt1.valid_models:
            if ppt2.can_take(model):
                return False

        for model in ppt2.valid_models:
            if ppt1.can_take(model):
                return False

        return True

    @staticmethod
    def same_valid_models(ppt1: PickyPieceTaker, ppt2: PickyPieceTaker) -> bool:
        return ppt1.can_flush_into(ppt2) and ppt2.can_flush_into(ppt1)


def _note_buffer_arrival(buffer, piece: Piece) -> None:
    """Hook fired whenever a piece is stored into a buffer.

    Updates any attached arrival monitors (lead time since piece creation) and
    wakes altruistic collectors waiting on this buffer. Both branches are skipped
    for buffers without monitors / waiters, so greedy-only models pay nothing.
    """
    monitors = getattr(buffer, "arrival_monitors", None)
    if monitors:
        delay = env.now() - piece.creation_time()
        for mon in monitors:
            mon.tally(delay)

    signal = getattr(buffer, "arrival_signal", None)
    if signal is not None:
        signal.set(signal.value() + 1)


class PiecePlacer(sim.Component):
    def setup(self, pieces: list[Piece], bufs_out: list[Buffer]):
        self.pieces = pieces
        self.bufs_out = bufs_out
        self.done = sim.State(value=False)

    def process(self):
        for piece in self.pieces:
            for buf_out in self.bufs_out:
                if buf_out.can_take(piece):
                    target = buf_out.choose_buffer() if isinstance(buf_out, SoftBuffer) else buf_out
                    self.to_store(target, piece)
                    _note_buffer_arrival(target, piece)
                    break

        self.done.set(True)


###########
# BUFFERS #
###########

class Buffer(PickyPieceTaker):
    def __init__(self, valid_models: list[Model]) -> None:
        super().__init__(valid_models)


class HardBuffer(sim.Store, Buffer):
    def setup(self, valid_models: list[Model]) -> None:
        PickyPieceTaker.__init__(self, valid_models)
        # Monitor hooks (populated by graph_parser_advanced when a Monitor card is attached).
        self.arrival_monitors: list[sim.Monitor] = []
        # Lazily created arrival signal (only when an altruistic collector subscribes).
        self.arrival_signal = None


class SoftBuffer(Buffer):
    def __init__(self) -> None:
        self.bufs_out = None
        self.probs = None

    def choose_buffer(self) -> Buffer:
        rand = sim.Uniform(0, 1).sample()
        cursor = 0

        for i in range(len(self.bufs_out)):
            cursor += self.probs[i]
            if rand < cursor:
                return self.bufs_out[i]
        return self.bufs_out[-1]

    def init(self, bufs_out_probs: list[tuple[Buffer, float]]) -> None:
        if not all(PickyPieceTaker.same_valid_models(bufs_out_probs[0][0], buf_out) for buf_out, _ in bufs_out_probs):
            raise ValueError("All buffers in soft buffer must accept the same models")

        if not all(0 <= prob <= 1 for _, prob in bufs_out_probs):
            raise ValueError("Probabilities in soft buffer must be in [0, 1]")

        if not abs(sum(prob for _, prob in bufs_out_probs) - 1) < 1e-9:
            raise ValueError("Probabilities in soft buffer must sum to 1")

        super().__init__(bufs_out_probs[0][0].valid_models)

        self.bufs_out = [buf_out for buf_out, _ in bufs_out_probs]
        self.probs = [prob for _, prob in bufs_out_probs]


###################
# BATCH COLLECTOR #
###################

class BatchCollector(sim.Component):
    def setup(self, task: Task) -> None:
        self.task = task
        self.collected_pieces = []
        self.done = sim.State(value=False)

    def update_done(self):
        self.done.set(len(self.collected_pieces) >= self.task.config.min_capacity)


class GreedyBatchCollector(BatchCollector):
    """Event-driven greedy collector.

    Semantics preserved from the standby() version: grab matching pieces as long as
    there is WIP capacity (``vacant_slots``), stop being "done" once ``min_capacity``
    is reached, but keep absorbing whatever is already available up to capacity.

    Instead of polling via standby(), it reserves a WIP slot then blocks on
    ``from_store`` for a matching piece -- salabim wakes it natively when a piece is
    added to any input buffer or when a slot frees up, so there is no busy loop.
    """

    def process(self):
        task = self.task
        min_cap = task.config.min_capacity

        # Phase 1 -- reach min_capacity, blocking efficiently for slots and pieces.
        while len(self.collected_pieces) < min_cap:
            self.request((task.vacant_slots, 1))                       # blocks until WIP slot free
            piece = self.from_store(task.bufs_in, filter=task.can_take)  # blocks until matching piece
            self.collected_pieces.append(piece)

        # Phase 2 -- greedily absorb pieces already waiting, up to remaining WIP capacity.
        while task.vacant_slots.available_quantity() > 0:
            piece = self.from_store(task.bufs_in, filter=task.can_take, fail_delay=0)
            if piece is None:                                          # nothing available right now
                break
            self.request((task.vacant_slots, 1))
            self.collected_pieces.append(piece)

        self.update_done()


class AltruisticBatchCollector(BatchCollector):
    """Event-driven altruistic collector.

    Semantics preserved: only ever grab pieces when a full batch of at least
    ``min_capacity`` can be formed at once (so pieces are never held hostage while
    waiting). Instead of polling via standby(), it blocks on lightweight per-buffer
    arrival signals plus the task slot signal, which are bumped only when an
    altruistic collector is actually waiting.
    """

    def process(self):
        task = self.task
        min_cap = task.config.min_capacity

        # Make sure the signals this collector waits on exist (lazy, greedy-only models skip this).
        for buf_in in task.bufs_in:
            if buf_in.arrival_signal is None:
                buf_in.arrival_signal = sim.State("arrival." + buf_in.name(), value=0)
        if task.slot_signal is None:
            task.slot_signal = sim.State("slot." + task.name(), value=0)

        while True:
            capacity = int(task.vacant_slots.available_quantity())
            valid_pieces = []
            if capacity > 0:
                for buf_in in task.bufs_in:
                    for piece in buf_in:
                        if task.can_take(piece):
                            valid_pieces.append((piece, buf_in))
                            if len(valid_pieces) >= capacity:
                                break
                    if len(valid_pieces) >= capacity:
                        break

            if len(valid_pieces) >= min_cap:
                for piece, buf_in in valid_pieces:
                    self.from_store(buf_in, filter=lambda p, q=piece: p is q)
                    self.collected_pieces.append(piece)
                self.request((task.vacant_slots, len(valid_pieces)))
                self.update_done()
                return

            # Not enough to form a batch yet: block until inputs change (arrival or freed slot).
            snapshot = [(buf_in.arrival_signal, buf_in.arrival_signal.value()) for buf_in in task.bufs_in]
            snapshot.append((task.slot_signal, task.slot_signal.value()))
            self.wait(*[(state, (lambda v, c, s, base=base: v != base)) for state, base in snapshot])


########
# TASK #
########

class Operation(sim.Component):
    def setup(self, duration: float) -> None:
        self.duration = duration
        self.complete = sim.State(value=False)

    def process(self):
        self.hold(self.duration)
        self.complete.set(True)


class Carrier(sim.Component):
    def setup(self, task: Task, task_duration: float) -> None:
        self.task = task
        self.task_duration = task_duration
        self.loaded_pieces: list[Piece] = []
        self.batch_collector = None
        self.claimed_resources = []
        self.allow_loading = sim.State(value=False)
        self.allow_dispatch = sim.State(value=False)
        self.loaded = sim.State(value=False)
        self.done = sim.State(value=False)

    def _broken(self) -> bool:
        return self.task.is_in_breakdown.get()

    def _abort(self):
        if self.batch_collector is not None and not self.batch_collector.done.get():
            self.batch_collector.cancel()

        if self.batch_collector is not None:
            pieces = list(self.batch_collector.collected_pieces)
        else:
            pieces = list(self.loaded_pieces)

        if self.claimed_resources:
            self.release(*self.claimed_resources)
            self.claimed_resources = []
        if pieces:
            self.request((self.task.vacant_slots, -len(pieces)))
            self.task.notify_slot_freed()
        if pieces and self.task.breakdown_bufs_out:
            placer = PiecePlacer(pieces=pieces, bufs_out=self.task.breakdown_bufs_out)
            self.wait((placer.done, True))

        self.done.set(True)
        if self in self.task.active_carriers:
            self.task.active_carriers.remove(self)

    def process(self):
        self.wait((self.allow_loading, True), (self.task.is_in_breakdown, True))
        if self._broken():
            self._abort()
            return

        self.batch_collector = self.task.config.batch_collector(task=self.task)
        self.wait((self.batch_collector.done, True), (self.task.is_in_breakdown, True))
        if self._broken():
            self._abort()
            return

        self.loaded.set(True)
        self.loaded_pieces = self.batch_collector.collected_pieces

        self.wait((self.allow_dispatch, True), (self.task.is_in_breakdown, True))
        if self._broken():
            self._abort(); return

        resources_to_request = []
        for resource, _ in self.task.config.resources:
            if hasattr(resource, "restock"):
                resource.restock(self)

        if self.task.config.resources_scope is Scope.PER_BATCH:
            for resource, quantity in self.task.config.resources:
                resources_to_request.append((resource, quantity))
        elif self.task.config.resources_scope is Scope.PER_PIECE:
            for resource, quantity in self.task.config.resources:
                resources_to_request.append((resource, quantity * len(self.loaded_pieces)))
        if self.task.config.operators_scope is Scope.PER_BATCH:
            for operator, quantity in self.task.config.operators:
                resources_to_request.append((operator, quantity))

        self.request(*resources_to_request)
        self.claimed_resources = resources_to_request

        if self.task.has_breakdown:
            operation = Operation(duration=self.task_duration)
            self.wait((operation.complete, True), (self.task.is_in_breakdown, True))
            if not operation.complete.get() and self._broken():
                operation.cancel()
                self._abort()
                return
        else:
            self.hold(self.task_duration)

        self.release(*self.claimed_resources)
        self.claimed_resources = []
        self.request((self.task.vacant_slots, -len(self.loaded_pieces)))
        self.task.notify_slot_freed()
        piece_placer = PiecePlacer(pieces=self.loaded_pieces, bufs_out=self.task.bufs_out)
        self.wait((piece_placer.done, True))
        self.done.set(True)
        if self in self.task.active_carriers:
            self.task.active_carriers.remove(self)


class Scope(Enum):
    PER_PIECE = auto()
    PER_BATCH = auto()
    PER_TASK = auto()


@dataclass
class TaskConfig:
    capability: list[Model]
    operators: list[tuple[sim.Resource, int]]
    operators_scope: Scope
    resources: list[tuple[sim.Resource, float]]
    resources_scope: Scope
    task_duration: sim.Distribution
    startup_duration: sim.Distribution
    startup_operators: list[tuple[sim.Resource, int]]
    min_capacity: int
    max_capacity: int
    batch_collector: type[BatchCollector]
    independent_carriers: bool
    scheduled_shutdowns: ScheduledShutdowns | None


class Task(sim.Component, PickyPieceTaker):
    def setup(self, config: TaskConfig, bufs_in: list[HardBuffer], bufs_out: list[Buffer]):
        if config.operators_scope is Scope.PER_PIECE:
            raise ValueError("Operators scope must be PER_BATCH or PER_TASK")

        if config.resources_scope is Scope.PER_TASK:
            raise ValueError("Resources scope must be PER_PIECE or PER_BATCH")

        flushable_models: list[Model] = []
        for i in range(len(bufs_out)):
            flushable_models += bufs_out[i].valid_models
            for j in range(i + 1, len(bufs_out)):
                if not PickyPieceTaker.disjoint(bufs_out[i], bufs_out[j]):
                    raise ValueError("Out buffers must be a partition of task capability")

        PickyPieceTaker.__init__(self, config.capability)

        if not self.can_flush_into(PickyPieceTaker(flushable_models)):
            raise ValueError("Task must be able to flush out all models in its capability")

        self.config = config
        self.bufs_in = bufs_in
        self.bufs_out = bufs_out

        self.active_carriers: list[Carrier] = []
        self.vacant_slots = sim.Resource(capacity=config.max_capacity, anonymous=True)

        self.started_up = sim.State(value=False)
        self.is_in_breakdown = sim.State(value=False)
        self.is_in_scheduled_shutdown = sim.State(value=False)
        self.has_breakdown = False
        self.breakdown_bufs_out = None
        # Lazily created signal, bumped when a WIP slot frees (only altruistic collectors wait on it).
        self.slot_signal = None

    def notify_slot_freed(self) -> None:
        if self.slot_signal is not None:
            self.slot_signal.set(self.slot_signal.value() + 1)

    def process(self):
        while True:
            if self.is_in_breakdown.get():
                if self.config.operators_scope is Scope.PER_TASK:
                    self.release()
                self.wait((self.is_in_breakdown, False))

            if not self.started_up.get():
                self.request(*self.config.startup_operators)
                self.hold(self.config.startup_duration.sample())
                self.release(*self.config.startup_operators)
                self.started_up.set(True)
                if self.config.operators_scope is Scope.PER_TASK:
                    self.request(*self.config.operators)

            task_duration = self.config.task_duration.sample()
            carrier = Carrier(task=self, task_duration=task_duration)
            self.active_carriers.append(carrier)
            if self.config.scheduled_shutdowns is not None:
                while (next_shutdown := self.config.scheduled_shutdowns.next_shutdown()) is not None:
                    if env.now() + task_duration <= next_shutdown.start:
                        break
                    if self.config.operators_scope is Scope.PER_TASK:
                        self.release()
                    self.hold(till=next_shutdown.start, cap_now=True)
                    self.is_in_scheduled_shutdown.set(True)
                    self.hold(till=next_shutdown.end)
                    self.is_in_scheduled_shutdown.set(False)
                    self.started_up.set(False)

            carrier.allow_loading.set(True)
            self.wait((carrier.loaded, True), (self.is_in_breakdown, True))

            if self.is_in_breakdown.get():
                continue

            carrier.allow_dispatch.set(True)

            if not self.config.independent_carriers:
                self.wait((carrier.done, True), (self.is_in_breakdown, True))
                if self.is_in_breakdown.get():
                    continue


@dataclass
class FirstTaskConfig:
    models_probs: list[tuple[Model, float]]
    resources: list[tuple[sim.Resource, float]]
    task_duration: sim.Distribution


class FirstTask(sim.Component, PickyPieceTaker):
    def setup(self, config: FirstTaskConfig, bufs_out: list[Buffer]) -> None:
        if not all(0 <= prob <= 1 for _, prob in config.models_probs):
            raise ValueError("Probabilities in first task must be in [0, 1]")

        if not abs(sum(prob for _, prob in config.models_probs) - 1) < 1e-9:
            raise ValueError("Probabilities in first task must sum to 1")

        flushable_models: list[Model] = []

        for i in range(len(bufs_out)):
            flushable_models += bufs_out[i].valid_models

            for j in range(i + 1, len(bufs_out)):
                if not PickyPieceTaker.disjoint(bufs_out[i], bufs_out[j]):
                    raise ValueError("Out buffers must be a partition of first task models")

        self.models = [m for m, _ in config.models_probs]
        self.probs = [p for _, p in config.models_probs]
        self.bufs_out = bufs_out
        self.config = config

        PickyPieceTaker.__init__(self, self.models)

        if not PickyPieceTaker.same_valid_models(PickyPieceTaker(flushable_models), self):
            raise ValueError("First task must be able to flush out all models")

    def process(self):
        while True:
            task_duration = self.config.task_duration.sample()

            resources_to_request = []

            for resource, quantity in self.config.resources:
                if hasattr(resource, "restock"):
                    resource.restock(self)
                resources_to_request.append((resource, quantity))

            if resources_to_request:
                self.request(*resources_to_request)

            model = np.random.choice(self.models, p=self.probs)
            new_piece = Piece(model=model)
            self.hold(task_duration)
            piece_placer = PiecePlacer(pieces=[new_piece], bufs_out=self.bufs_out)

            self.wait((piece_placer.done, True))


####################################
# BREAKDOWNS & SCHEDULED SHUTDOWNS #
####################################

class Breakdown(sim.Component):
    def setup(self, task: Task, mtbf: sim.Distribution, mttr: sim.Distribution, bufs_out: list[Buffer]) -> None:
        self.task = task
        self.mtbf = mtbf
        self.mttr = mttr
        self.bufs_out = bufs_out
        task.has_breakdown = True
        task.breakdown_bufs_out = bufs_out

    def process(self):
        while True:
            self.wait((self.task.is_in_scheduled_shutdown, False))
            self.hold(self.mtbf.sample())

            if self.task.is_in_scheduled_shutdown.get():
                continue

            self.task.is_in_breakdown.set(True)
            self.hold(self.mttr.sample())
            self.task.is_in_breakdown.set(False)
            self.task.started_up.set(False)


class ScheduledShutdowns:
    def __init__(self, intervals: list[Interval]) -> None:
        for int1 in intervals:
            for int2 in intervals:
                if int1 is int2:
                    continue
                if not Interval.disjoint(int1, int2):
                    raise ValueError("Scheduled breakdown intervals must be disjoint")

        self.intervals = sorted(intervals, key=lambda x: x.start)

    def next_shutdown(self) -> Interval | None:
        for interval in self.intervals:
            if interval.end > env.now():
                return interval
        return None


########################
# RESTOCKABLE RESOURCE #
########################

class Delivery(sim.Component):
    def setup(self, stock: RestockableResource, delivery_duration):
        self.stock = stock
        self.delivery_duration = delivery_duration

    def process(self):
        self.hold(self.delivery_duration)
        missing = self.stock.capacity - self.stock.available_quantity()
        if missing > 0:
            self.request((self.stock, -missing))
        self.stock.active_order = False


class RestockableResource(sim.Resource):
    def setup(self, order_duration: sim.Distribution, delivery_duration: sim.Distribution, threshold: float) -> None:
        self.order_duration = order_duration
        self.delivery_duration = delivery_duration
        self.threshold = threshold
        self.active_order = False

    def restock(self, demander: sim.Component):
        if not self.active_order and self.available_quantity() < self.threshold:
            self.active_order = True
            demander.hold(self.delivery_duration)
            Delivery(stock=self, delivery_duration=self.delivery_duration)
