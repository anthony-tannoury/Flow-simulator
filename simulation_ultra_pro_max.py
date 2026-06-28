from __future__ import annotations

import salabim as sim
import numpy as np

from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import override, Callable
from collections import Counter
from enum import Enum, auto


#########
# SETUP #
#########

SEED = 42
sim.yieldless(True)
env = sim.Environment(random_seed=SEED)
np.random.seed(SEED)


###########
# HELPERS #
###########

class Interval:
    def __init__(self, start: float, end: float) -> None:
        if start > end:
            raise ValueError("Interval start must be less than or equal to end")
        self.start = start
        self.end = end


def check_inlet_validity(receiver: PickyPieceTaker, inlets: list[Buffer]) -> None:
    if not inlets:
        raise ValueError("Receiver must have at least one inlet")

    if not all(inlet.can_flush_into(receiver) for inlet in inlets):
        raise ValueError("Receiver must be able to receive all models from inlets")


def check_outlet_validity(giver: PickyPieceTaker, outlets: list[Outlet]) -> None:
    if not outlets:
        raise ValueError("Giver must have at least one outlet")

    for i in range(len(outlets)):
        for j in range(i + 1, len(outlets)):
            if not PickyPieceTaker.disjoint(outlets[i], outlets[j]):
                raise ValueError("Outlets must have disjoint valid models sets")

    valid_models_sets = [set(outlet.valid_models) for outlet in outlets]
    union = set.union(*valid_models_sets)

    if not giver.can_flush_into(PickyPieceTaker(list(union))):
        raise ValueError("Giver must be able to flush all models into outlets")


def place(pieces: list[Piece], outlets: list[Outlet]) -> None:
    for piece in pieces:
        placed = False

        for outlet in outlets:
            buffer = outlet.get()
            if buffer.can_take(piece):
                piece.enter(buffer)
                placed = True
                break

        assert placed, "Could not place piece in outlets"


#########
# MODEL #
#########

class Model:
    def __init__(self, name: str, parent: Model | None, children: list[Model]):
        self.name = name
        self.parent = parent
        self.children = children

    def __repr__(self):
        return self.name


########################
# RESTOCKABLE RESOURCE #
########################

class Delivery(sim.Component):
    def setup(self, stock: RestockableResource, delivery_duration: sim.Distribution) -> None:
        self.stock = stock
        self.delivery_duration = delivery_duration

    def process(self):
        self.hold(self.delivery_duration.sample())
        missing = self.stock.capacity.value - self.stock.available_quantity()
        self.request((self.stock, -missing))
        self.stock.active_order = False


class RestockableResource(sim.Resource):
    def __init__(self, *args, **kwargs) -> None:
        kwargs["anonymous"] = True
        super().__init__(*args, **kwargs)

    def setup(self, order_duration: sim.Distribution, delivery_duration: sim.Distribution, threshold: float) -> None:
        self.order_duration = order_duration
        self.delivery_duration = delivery_duration
        self.threshold = threshold
        self.active_order = False

    def restock(self, demander: sim.Component, deadline: float) -> bool:
        if not self.active_order and self.available_quantity() < self.threshold:
            # If we do not have the time to make an order, we simply do not
            order_duration = self.order_duration.sample()
            if env.now() + order_duration > deadline:
                return False

            self.active_order = True
            demander.hold(order_duration)
            Delivery(stock=self, delivery_duration=self.delivery_duration)
            return True

        return True


#########
# PIECE #
#########

class Piece(sim.Component):
    ID = 0

    def setup(self, model: Model) -> None:
        if model.children:
            raise ValueError("Piece model must be a leaf model")

        self.model = model
        self.id = str(Piece.ID).zfill(6)
        Piece.ID += 1


class PickyPieceTaker:
    def __init__(self, valid_models: list[Model]) -> None:
        if not valid_models:
            raise ValueError("PickyPieceTaker must have at least one valid model")

        self.valid_models = valid_models

    def can_take(self, obj: Piece | Model) -> bool:
        model = obj.model if isinstance(obj, Piece) else obj
        can_take = False
        while model is not None and not can_take:
            can_take |= model in self.valid_models
            model = model.parent
        return can_take

    def can_flush_into(self, ppt: PickyPieceTaker) -> bool:
        return all(ppt.can_take(model) for model in self.valid_models)

    @staticmethod
    def disjoint(ppt1: PickyPieceTaker, ppt2: PickyPieceTaker) -> bool:
        return not (any(ppt1.can_take(model) for model in ppt2.valid_models)
                    or any(ppt2.can_take(model) for model in ppt1.valid_models))


##########
# BUFFER #
##########

class Outlet(PickyPieceTaker, ABC):
    def __init__(self, valid_models: list[Model]) -> None:
        super().__init__(valid_models)

    @abstractmethod
    def get(self) -> Buffer:
        pass


class Buffer(sim.Store, Outlet):
    def setup(self, valid_models: list[Model]) -> None:
        Outlet.__init__(self, valid_models)

    @override
    def get(self) -> Buffer:
        return self


class Router(Outlet):
    def __init__(self, outlets_probs: dict[Outlet, float]) -> None:
        valid_models_sets = [set(outlet.valid_models) for outlet in outlets_probs.keys()]
        intersection = set.intersection(*valid_models_sets)

        if not intersection:
            raise ValueError("Router outlets must have at least one valid model in common")

        Outlet.__init__(self, list(intersection))
        self.outlets = list(outlets_probs.keys())
        self.probs = list(outlets_probs.values())

    @override
    def get(self) -> Buffer:
        return self.outlets[np.random.choice(len(self.outlets), p=self.probs)].get()


################
# INTERRUPTERS #
################

class Breakdown(sim.Component):
    MAX_ITERS = 60000

    def setup(self, task: Task, failure_rate: Callable[[float], float], mttr: sim.Distribution, outlets: list[Outlet]) -> None:
        check_outlet_validity(task, outlets)
        self.task = task
        self.failure_rate = failure_rate
        self.mttr = mttr
        self.outlets = outlets

    def get_next_breakdown_time(self) -> float:
        threshold = -np.log(env.random.random())
        integral = 0
        t = env.now()
        dt = 60
        for _ in range(Breakdown.MAX_ITERS):
            if integral < threshold:
                integral += self.failure_rate(t) * dt
                t += dt
            else:
                return t
        raise ValueError(f"Integral did not cross threshold after {Breakdown.MAX_ITERS} iterations")

    def process(self):
        while True:
            self.wait((self.task.is_in_shutdown, False))

            next_breakdown_time = self.get_next_breakdown_time()
            self.hold(till=next_breakdown_time)

            if self.task.is_in_shutdown.get():
                continue

            self.task.started_up = False
            for carrier in self.task.active_carriers:
                carrier.abort(self.outlets)

            self.task.is_in_breakdown.set(True)
            self.hold(self.mttr.sample())
            self.task.is_in_breakdown.set(False)


class ScheduledShutdown(sim.Component):
    def setup(self, task: Task, intervals: list[Interval] = []) -> None:
        self.task = task
        self.intervals = intervals
        self.task.scheduled_shutdowns = self

    def next_shutdown(self) -> Interval | None:
        for interval in self.intervals:
            if interval.end > env.now():
                return interval
        return None

    def can_resume_at(self, duration: float) -> float | None:
        next_shutdown = self.next_shutdown()
        if next_shutdown is not None and env.now() + duration > next_shutdown.start:
            return next_shutdown.end
        return None

    def get_deadline(self) -> float:
        next_shutdown = self.next_shutdown()
        return next_shutdown.start if next_shutdown is not None else float('inf')

    def process(self):
        while (next_shutdown := self.next_shutdown()) is not None:
            self.hold(till=next_shutdown.start)
            self.task.started_up = False
            self.task.is_in_shutdown.set(True)
            self.hold(till=next_shutdown.end)
            self.task.is_in_shutdown.set(False)
            self.task.is_frozen.set(False)


####################
# BATCH COLLECTORS #
####################

class BatchCollectorType(Enum):
    GREEDY = auto()
    ALTRUISTIC = auto()

    def __init__(self, discriminate: bool) -> None:
        self.discriminate = discriminate


class Collector(sim.Component):
    def setup(self, task: Task) -> None:
        self.task = task
        self.requested_slots = 0
        self.collected_pieces: list[Piece] = []
        self.allow_dispatch = sim.State(value=False)
        self.done = sim.State(value=False)


class NonDiscriminatingGreedyCollector(Collector):
    def process(self):
        self.wait(self.allow_dispatch)
        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity))
        self.requested_slots += self.task.config.min_carrier_capacity

        while len(self.collected_pieces) < self.task.config.min_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        while self.task.vacant_slots.available_quantity() > 0 and len(self.collected_pieces) < self.task.config.max_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take, fail_delay=0)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1))
            self.requested_slots += 1

            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        if not self.task.config.contiguous_carriers:
            remainder = self.task.config.max_carrier_capacity - len(self.collected_pieces)
            self.request((self.task.vacant_slots, remainder))
            self.requested_slots += remainder

        self.done.set(True)
        # Requested slots are released: to be reclaimed in the carrier


class DiscriminatingGreedyCollector(Collector):
    def process(self):
        self.wait(self.allow_dispatch)
        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity))
        self.requested_slots += self.task.config.min_carrier_capacity

        present_models = [piece.model for inlet in self.task.inlets for piece in inlet if self.task.can_take(piece)]

        # If no valid pieces are available, wait for the first valid model to arrive and focus on it.
        # Otherwise, focus on the most present valid model
        if not present_models:
            piece = self.from_store(self.task.inlets, filter=self.task.can_take)
            assert isinstance(piece, Piece)
            focus_on = piece.model
        else:
            focus_on = Counter(present_models).most_common(1)[0][0]

        while len(self.collected_pieces) < self.task.config.min_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=lambda p: self.task.can_take(p) and p.model is focus_on)
            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        while self.task.vacant_slots.available_quantity() > 0 and len(self.collected_pieces) < self.task.config.max_carrier_capacity:
            piece = self.from_store(self.task.inlets, filter=lambda p: self.task.can_take(p) and p.model is focus_on, fail_delay=0)
            if self.failed():
                break

            self.request((self.task.vacant_slots, 1))
            self.requested_slots += 1

            assert isinstance(piece, Piece)
            self.collected_pieces.append(piece)

        self.release()
        self.done.set(True)



########
# TASK #
########

class Scope(Enum):
    PER_PIECE = auto()
    PER_BATCH = auto()
    PER_TASK = auto()


class Carrier(sim.Component):
    def setup(self, task: Task) -> None:
        self.task = task
        self.allow_dispatch = sim.State(value=False)
        self.done = sim.State(value=False)

        bct = task.config.batch_collector_type
        if bct is BatchCollectorType.GREEDY:
            if bct.discriminate:
                self.batch_collector = DiscriminatingGreedyCollector(task=task)
            else:
                self.batch_collector = NonDiscriminatingGreedyCollector(task=task)

    def abort(self, lifeboats: list[Outlet]) -> None:
        place(self.batch_collector.collected_pieces, lifeboats)
        self.batch_collector.done.set(True)
        self.batch_collector.cancel()

        if self in self.task.active_carriers:
            self.task.active_carriers.remove(self)

        self.done.set(True)
        self.cancel()

    def process(self):
        deadline = self.task.scheduled_shutdowns.get_deadline()

        self.batch_collector.allow_dispatch.set(True)
        # We do not know the task duration yet, so we just fail at deadline (and not deadline - duration)
        self.wait(self.batch_collector.done, fail_at=deadline)
        if self.failed():
            self.task.is_frozen.set(True)
            self.abort(self.task.inlets)
            return

        self.request((self.task.vacant_slots, self.batch_collector.requested_slots), fail_delay=0)
        assert not self.failed(), "Failed to instantly request slots in carrier"

        model = self.batch_collector.collected_pieces[0].model
        duration = self.task.config.models_durations[model].sample()

        resources_to_request = []

        if self.task.config.operators_scope is Scope.PER_BATCH:
            resources_to_request.extend(self.task.config.operators)
            for resource, _ in self.task.config.resources:
                if isinstance(resource, RestockableResource):
                    resource.restock(demander=self, deadline=deadline - duration)

        if self.task.config.resources_scope is Scope.PER_BATCH:
            resources_to_request.extend(self.task.config.resources)
        elif self.task.config.resources_scope is Scope.PER_PIECE:
            resources_to_request.extend(
                [(resource, quantity * len(self.batch_collector.collected_pieces)) for resource, quantity in
                 self.task.config.resources])

        self.request(*resources_to_request, fail_at=deadline - duration)
        # Did we fail to have the resources in time?
        if self.failed():
            self.task.is_frozen.set(True)
            self.abort(self.task.inlets)
            return

        self.wait(self.allow_dispatch, fail_at=deadline - duration)
        # Did we not dispatch the carrier in time?
        if self.failed():
            self.task.is_frozen.set(True)
            self.abort(self.task.inlets)
            return

        assert env.now() + duration <= deadline, "Failed to dispatch carrier despite there being enough time"
        self.hold(duration)

        place(self.batch_collector.collected_pieces, self.task.outlets)
        if self in self.task.active_carriers:
            self.task.active_carriers.remove(self)

        self.done.set(True)
        # Operators and slots are automatically released


class TaskStarter(sim.Component):
    def setup(self, task: Task) -> None:
        self.task = task
        self.done = sim.State(value=False)

    def process(self):
        duration = self.task.config.startup_duration.sample()
        while (resume_at := self.task.scheduled_shutdowns.can_resume_at(duration)) is not None:
            self.hold(till=resume_at)

        deadline = self.task.scheduled_shutdowns.get_deadline()
        self.request(*self.task.config.startup_operators, fail_at=deadline - duration, cap_now=True)
        if self.failed():
            self.task.is_frozen.set(True)
            self.done.set(True)
            return

        self.hold(duration)
        self.task.started_up = True
        self.done.set(True)
        # Startup operators are automatically released


@dataclass
class TaskConfig:
    models_durations: dict[Model, sim.Distribution]  # capability
    resources: list[tuple[sim.Resource, float]]
    resources_scope: Scope
    operators: list[tuple[sim.Resource, int]]
    operators_scope: Scope
    startup_operators: list[tuple[sim.Resource, int]]
    startup_duration: sim.Distribution

    min_carriers: int
    min_carrier_capacity: int
    max_carrier_capacity: int
    max_capacity: int
    contiguous_carriers: bool
    independent_carriers: bool

    batch_collector_type: BatchCollectorType


class Task(sim.Component, PickyPieceTaker):
    def setup(self, config: TaskConfig, inlets: list[Buffer], outlets: list[Outlet]) -> None:
        if not config.batch_collector_type.discriminate:
            first_distr = list(config.models_durations.values())[0]
            if not all(distr is first_distr for distr in config.models_durations.values()):
                raise ValueError(
                    "Batch collector cannot have different distributions for models AND not discriminate batches")

        if config.operators_scope is Scope.PER_PIECE:
            raise ValueError("Operators scope cannot be PER_PIECE")
        if config.resources_scope is Scope.PER_TASK:
            raise ValueError("Resources scope cannot be PER_TASK")

        PickyPieceTaker.__init__(self, list(config.models_durations.keys()))
        check_inlet_validity(self, inlets)
        check_outlet_validity(self, outlets)

        self.config = config
        self.inlets = inlets
        self.outlets = outlets

        self.vacant_slots = sim.Resource(capacity=config.max_capacity)

        self.started_up = False
        self.is_in_breakdown = sim.State(value=False)
        self.is_in_shutdown = sim.State(value=False)
        # is_frozen refers to the pre-shutdown phase where an operation was aborted because we did not have enough time
        # to complete it, so we wait for the shutdown to be over to reattempt the operation (startup or dispatching
        # carriers).
        self.is_frozen = sim.State(value=False)
        self.scheduled_shutdowns = ScheduledShutdown(task=self)

        self.active_carriers: list[Carrier] = []

    def process(self):
        while True:
            self.wait((self.is_in_breakdown, False), (self.is_in_shutdown, False), (self.is_frozen, False), all=True)

            if not self.started_up:
                task_starter = TaskStarter(task=self)
                self.wait(task_starter.done)

                # Did the startup fail because we could not get operators in time?
                if not self.started_up:
                    # WITHOUT is_frozen, when re-entering the loop, we attempt to boot the task once more. The reboot
                    # could be successful if we manage to sample a startup_duration less than the previous one, and the
                    # startup operators request is honored in time. But we do not want this behavior since it does not
                    # reflect reality.
                    continue

                if self.config.operators_scope is Scope.PER_TASK:
                    deadline = self.scheduled_shutdowns.get_deadline()
                    self.request(*self.config.operators, fail_at=deadline)

                    # Did we not get enough task operators in time?
                    if self.failed():
                        # Setting is_frozen to True is redundant because passing the deadline and failing is caused by a
                        # scheduled shutdown, so we would have is_in_shutdown set to True, but we add the state for
                        # clarity.
                        self.is_frozen.set(True)
                        continue

            if self.config.operators_scope is Scope.PER_TASK:
                deadline = self.scheduled_shutdowns.get_deadline()
                for resource, _ in self.config.resources:
                    if isinstance(resource, RestockableResource):
                        resource.restock(demander=self, deadline=deadline)

            new_carrier = Carrier(task=self)
            self.active_carriers.append(new_carrier)
            self.wait(new_carrier.batch_collector.done)

            # If new_carrier aborted because of a scheduled shutdown, then all non-dispatched carrier have also aborted
            non_dispatched_carriers = [carrier for carrier in self.active_carriers if not carrier.allow_dispatch.get()]
            if len(non_dispatched_carriers) >= self.config.min_carriers:
                for carrier in non_dispatched_carriers:
                    carrier.allow_dispatch.set(True)

                if not self.config.independent_carriers:
                    self.wait(*[carrier.done for carrier in non_dispatched_carriers], all=True)
