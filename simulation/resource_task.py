from __future__ import annotations

import salabim as sim

from enum import Enum, auto
from .compat import override
from dataclasses import dataclass

from simulation import env
from .component import Component
from .task import TaskConfig, Task, Carrier, Scope
from .resource import Resource, RestockableResource
from .sampler import Distribution
from .helpers import check_probabilities
from .ables import Dispatchable, Donnable


class ResourceCollectorType(Enum):
    GREEDY = auto()
    ALTRUISTIC = auto()


class ResourceCollector(Component, Dispatchable, Donnable):
    def setup(self, task: ResourceTask) -> None:
        Dispatchable.__init__(self)
        Donnable.__init__(self)
        self.task = task
        self.requested_quantity = 0.0
        assert isinstance(task.config, ResourceTaskConfig)
        self.triggers = [r.trigger for r, _, _ in task.config.transformed_resources_salvageable]
        self.requested_quantities = [0.0 for _ in task.config.transformed_resources_salvageable]

    def balance_mix(self) -> None:
        assert isinstance(self.task.config, ResourceTaskConfig)
        limiting_factor = min(self.requested_quantities[i] / p for i, (_, p, _) in enumerate(self.task.config.transformed_resources_salvageable))

        excess_slots = sum(self.requested_quantities) - limiting_factor
        for i, (r, p, s) in enumerate(self.task.config.transformed_resources_salvageable):
            excess = self.requested_quantities[i] - limiting_factor * p
            if s and excess > 0:
                r.replenish(demander=self, quantity=excess)
            self.requested_quantities[i] = limiting_factor * p

        if excess_slots > 0:
            self.release((self.task.vacant_slots, excess_slots))
        self.requested_quantity = limiting_factor

    def top_up(self) -> None:
        assert isinstance(self.task.config, ResourceTaskConfig)
        available = min([r.available_quantity() / p for r, p, _ in self.task.config.transformed_resources_salvageable])

        additional_request = 0
        if (self.task.vacant_slots.available_quantity() > 0) and (available > 0):
            additional_request = min(self.task.vacant_slots.available_quantity(),
                                     self.task.config.max_carrier_capacity - self.task.config.min_carrier_capacity,
                                     available)
            self.request(*[(r, p * additional_request) for r, p, _ in self.task.config.transformed_resources_salvageable], fail_delay=0, request_priority=self.task.request_priority)
            assert not self.failed()
            for i, (_, p, _) in enumerate(self.task.config.transformed_resources_salvageable):
                self.requested_quantities[i] += p * additional_request
            self.requested_quantity += additional_request

        if self.task.config.contiguous_carriers:
            additional_slots_to_request = additional_request
        else:
            additional_slots_to_request = self.task.config.max_carrier_capacity - self.task.config.min_carrier_capacity
        self.request((self.task.vacant_slots, additional_slots_to_request), request_priority=self.task.request_priority, mode="wait_slot")


class GreedyResourceCollector(ResourceCollector):
    def process(self):
        assert isinstance(self.task.config, ResourceTaskConfig)
        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout
        timed_out = False

        while sum(self.requested_quantities) < self.task.config.min_carrier_capacity:
            for i, (r, p, _) in enumerate(self.task.config.transformed_resources_salvageable):
                requested_quantity_per_resource = min(p*self.task.config.min_carrier_capacity - self.requested_quantities[i], r.available_quantity())
                self.request((self.task.vacant_slots, requested_quantity_per_resource), request_priority=self.task.request_priority, mode="wait_slot")
                self.request((r, requested_quantity_per_resource), request_priority=self.task.request_priority)
                self.requested_quantities[i] += requested_quantity_per_resource

            if sum(self.requested_quantities) >= self.task.config.min_carrier_capacity:
                break

            self.wait(*self.triggers, fail_at=deadline, mode="wait_pieces")
            if self.failed():
                timed_out = True
                break

        if timed_out:
            self.balance_mix()
        else:
            self.requested_quantity = sum(self.requested_quantities)
            self.top_up()

        self.set_mode("")
        self.done.set(True)
        self.passivate()


class AltruisticResourceCollector(ResourceCollector):
    def process(self):
        assert isinstance(self.task.config, ResourceTaskConfig)
        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout

        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity), request_priority=self.task.request_priority, fail_at=deadline, mode="wait_slot")
        timed_out = self.failed()

        if not timed_out:
            self.request(*[(r, p*self.task.config.min_carrier_capacity) for r, p, _ in self.task.config.transformed_resources_salvageable], request_priority=self.task.request_priority, fail_at=deadline, mode="wait_pieces")
            if self.failed():
                timed_out = True
                self.release((self.task.vacant_slots, self.task.config.min_carrier_capacity))

        if not timed_out:
            for i, (_, p, _) in enumerate(self.task.config.transformed_resources_salvageable):
                self.requested_quantities[i] = p * self.task.config.min_carrier_capacity
            self.requested_quantity = self.task.config.min_carrier_capacity
            self.top_up()

        self.set_mode("")
        self.done.set(True)
        self.passivate()


@dataclass
class ResourceTaskConfig(TaskConfig):
    non_transformed_resources: list[tuple[Resource, float]]
    transformed_resources_salvageable: list[tuple[Resource, float, bool]]
    resources_out_distr: list[tuple[Resource, sim.Bounded]]
    duration: Distribution
    resource_collector_type: ResourceCollectorType
    min_carrier_capacity: float
    max_carrier_capacity: float


class ResourceCarrier(Carrier):
    def setup(self, task: ResourceTask) -> None:
        super().setup(task=task)
        assert isinstance(self.task.config, ResourceTaskConfig)
        match self.task.config.resource_collector_type:
            case ResourceCollectorType.GREEDY:
                self.resource_collector = GreedyResourceCollector(task=task)
            case ResourceCollectorType.ALTRUISTIC:
                self.resource_collector = AltruisticResourceCollector(task=task)

    @override
    def handle_restock(self) -> None:
        assert isinstance(self.task.config, ResourceTaskConfig)
        for resource, _ in self.task.config.non_transformed_resources:
            if isinstance(resource, RestockableResource):
                resource.restock(demander=self)

        for resource, _, _ in self.task.config.transformed_resources_salvageable:
            if isinstance(resource, RestockableResource):
                resource.restock(demander=self)

    @override
    def abort(self, *args) -> None:
        assert isinstance(self.task.config, ResourceTaskConfig)
        for i, (r, _, s) in enumerate(self.task.config.transformed_resources_salvageable):
            if s:
                r.replenish(demander=self, quantity=self.resource_collector.requested_quantities[i])

        self.resource_collector.set_mode("")
        self.resource_collector.done.set(True)
        self.resource_collector.cancel()

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
        if condition:
            self.task.is_frozen.set(True)
            self.abort()

    @override
    def wait_for_collector(self, fail_at: float) -> None:
        self.handle_restock()

        if env.now() >= fail_at:
            self.freeze_abort_if(True)
            return

        self.resource_collector.allow_dispatch.set(True)
        self.wait(self.resource_collector.done, fail_at=fail_at, cap_now=True, mode="collecting")

    @override
    def get_ideal_loading_duration(self) -> float:
        return self.task.config.loading_duration.sample_now()

    @override
    def get_ideal_duration(self) -> float:
        assert isinstance(self.task.config, ResourceTaskConfig)
        return self.task.config.duration.sample_now()

    @override
    def request_resources(self, fail_at: float) -> None:
        assert isinstance(self.task.config, ResourceTaskConfig)
        mult = 1 if self.task.config.resource_scope is Scope.PER_BATCH else self.resource_collector.requested_quantity
        resources = [(r, q*mult) for r, q in self.task.config.non_transformed_resources]
        self.request(*resources, fail_at=fail_at, cap_now=True, mode="wait_materials")
        self.freeze_abort_if(self.failed())

    @override
    def successfully_end_process(self):
        assert isinstance(self.task.config, ResourceTaskConfig)
        for resource_out, distr in self.task.config.resources_out_distr:
            resource_out.replenish(demander=self, quantity=distr.sample()*self.resource_collector.requested_quantity)

        self.task.batch_sizes.tally(self.resource_collector.requested_quantity)
        self.task.cycle_times.tally(env.now() - self.creation_time())

        self.resource_collector.set_mode("")
        self.resource_collector.cancel()
        self.set_mode("")
        self.done.set(True)
        self.task.pending_carriers.remove(self)
        self.task.active_carriers.remove(self)


class ResourceTask(Task):
    def setup(self, config: ResourceTaskConfig):
        check_probabilities([p for _, p, _ in config.transformed_resources_salvageable])

        if any(distr.lowerbound < 0 or distr.upperbound == float('inf') for _, distr in config.resources_out_distr):
            raise ValueError("Output resource distribution must be bounded in [0, +inf[")

        super().setup(config=config, carrier_type=ResourceCarrier)

    @override
    def abort(self, *args):
        for carrier in list(self.pending_carriers) + list(self.active_carriers):
            carrier.abort()
        self.release_task_operators()
        self.started_up = False
