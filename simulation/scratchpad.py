class ResourceCollector(Component):
    def setup(self, task: ResourceTask) -> None:
        self.task = task
        self.allow_dispatch = sim.State(value=False)
        self.done = sim.State(value=False)
        self.requested_quantity = 0
        assert isinstance(task.config, ResourceTaskConfig)
        self.triggers = [r.trigger for r, _, _ in task.config.transformed_resources_salvageable]
        self.requested_quantities = [0 for _ in task.config.transformed_resources_salvageable]

    def balance_mix(self) -> None:
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
        self.request((self.task.vacant_slots, additional_slots_to_request), request_priority=self.task.request_priority)


class GreedyResourceCollector(ResourceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout
        timed_out = False

        while sum(self.requested_quantities) < self.task.config.min_carrier_capacity:
            for i, (r, p, _) in enumerate(self.task.config.transformed_resources_salvageable):
                requested_quantity_per_resource = min(p*self.task.config.min_carrier_capacity - self.requested_quantities[i], r.available_quantity())
                self.request((self.task.vacant_slots, requested_quantity_per_resource), request_priority=self.task.request_priority)
                self.request((r, requested_quantity_per_resource), request_priority=self.task.request_priority)
                self.requested_quantities[i] += requested_quantity_per_resource

            if sum(self.requested_quantities) >= self.task.config.min_carrier_capacity:
                break

            self.wait(*self.triggers, fail_at=deadline)
            if self.failed():
                timed_out = True
                break

        if timed_out:
            self.balance_mix()
        else:
            self.requested_quantity = sum(self.requested_quantities)
            self.top_up()

        self.done.set(True)
        self.passivate()


class AltruisticResourceCollector(ResourceCollector):
    def process(self):
        self.wait(self.allow_dispatch)
        deadline = env.now() + self.task.config.timeout

        self.request((self.task.vacant_slots, self.task.config.min_carrier_capacity), request_priority=self.task.request_priority, fail_at=deadline)
        timed_out = self.failed()

        if not timed_out:
            self.request(*[(r, p*self.task.config.min_carrier_capacity) for r, p, _ in self.task.config.transformed_resources_salvageable], request_priority=self.task.request_priority, fail_at=deadline)
            if self.failed():
                timed_out = True
                self.release((self.task.vacant_slots, self.task.config.min_carrier_capacity))

        if not timed_out:
            for i, (_, p, _) in enumerate(self.task.config.transformed_resources_salvageable):
                self.requested_quantities[i] = p * self.task.config.min_carrier_capacity
            self.requested_quantity = self.task.config.min_carrier_capacity
            self.top_up()

        self.done.set(True)
        self.passivate()
