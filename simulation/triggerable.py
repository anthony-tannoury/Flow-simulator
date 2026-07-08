import salabim as sim


class Triggerable:
    def __init__(self) -> None:
        self.trigger = sim.State()
