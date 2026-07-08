import salabim as sim


class Triggerable:
    def __init__(self) -> None:
        self.trigger = sim.State()


class Dispatchable:
    def __init__(self):
        self.allow_dispatch = sim.State(value=False)


class Donnable:
    def __init__(self):
        self.done = sim.State(value=False)
