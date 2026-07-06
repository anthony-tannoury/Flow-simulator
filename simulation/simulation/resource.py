import salabim as sim

from .triggerable import Triggerable
from .component import Component
from .distribution import Distribution


class Resource(sim.Resource, Triggerable):
    def __init__(self, *args, **kwargs) -> None:
        kwargs['anonymous'] = True
        super().__init__(*args, **kwargs)

    def setup(self, initial_capacity: float | None = None, lifespan: float = float('inf')) -> None:
        if initial_capacity is None:
            initial_capacity = self.capacity()
        self.expiry_managers = [ExpiryManager(resource=self, quantity=self.capacity() - initial_capacity)]
        self.lifespan = lifespan

    def shave(self, quantity: float) -> None:
        assert self.available_quantity() >= quantity
        shaved_quantity = 0.0
        while shaved_quantity < quantity:
            assert self.expiry_managers
            expiry_manager = self.expiry_managers[0]

            if expiry_manager.quantity > quantity - shaved_quantity:
                expiry_manager.quantity -= quantity - shaved_quantity
                break

            shaved_quantity += expiry_manager.quantity
            expiry_manager.cancel()
            self.expiry_managers.remove(expiry_manager)

    def replenish(self, demander: Component, quantity: float) -> None:
        if self.lifespan == float('inf'):
            demander.request((self, -quantity))
        else:
            self.expiry_managers.append(ExpiryManager(resource=self, quantity=quantity))


class ExpiryManager(Component):
    def setup(self, resource: Resource, quantity: float) -> None:
        self.resource = resource
        self.quantity = quantity

    def process(self):
        self.request((self.resource, -self.quantity))
        self.hold(self.resource.lifespan)
        self.request((self.resource, self.quantity))


class Delivery(Component):
    def setup(self, stock: RestockableResource, delivery_duration: Distribution) -> None:
        self.stock = stock
        self.delivery_duration = delivery_duration

    def process(self):
        missing = self.stock.capacity.value - self.stock.available_quantity()
        self.hold(self.delivery_duration.sample())
        self.stock.replenish(demander=self, quantity=missing)
        self.stock.active_order = False


class RestockableResource(Resource):
    def setup(self, order_duration: Distribution, delivery_duration: Distribution, threshold: float, initial_capcity: float | None = None, lifespan: float = float('inf')) -> None:
        super().setup(initial_capacity=initial_capcity, lifespan=lifespan)
        self.order_duration = order_duration
        self.delivery_duration = delivery_duration
        self.threshold = threshold
        self.active_order = False

    def restock(self, demander: Component):
        if not self.active_order and self.available_quantity() < self.threshold:
            demander.hold(self.order_duration.sample_now())
            self.active_order = True
            Delivery(stock=self, delivery_duration=self.delivery_duration)
