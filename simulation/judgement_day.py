from __future__ import annotations

import salabim as sim

from simulation import env
from .component import Component
from .outlet import Buffer, BufferType
from .ables import Dispatchable, Donnable
from abc import ABC, abstractmethod


class StoppingCriterion(Component, Dispatchable, Donnable, ABC):
    def setup(self):
        Dispatchable.__init__(self)
        Donnable.__init__(self)


class ByTime(StoppingCriterion):
    def setup(self, time: float):
        super().setup()
        self.time = time

    def process(self) -> None:
        self.wait(self.allow_dispatch)
        self.hold(self.time)
        self.done.set(True)


class ByPiecesProduced(StoppingCriterion):
    def setup(self, total: int, exit_buffer: Buffer, timeout: float = float('inf')):
        if exit_buffer.buffer_type is not BufferType.EXIT:
            raise ValueError("Stopping criterion must take an EXIT buffer")

        super().setup()
        self.total = total
        self.exit_buffer = exit_buffer
        self.timeout = timeout

    def process(self) -> None:
        self.wait(self.allow_dispatch)
        deadline = self.timeout + env.now()
        while len(self.exit_buffer) < self.total:
            self.wait(self.exit_buffer.trigger, fail_at=deadline)
            if self.failed():
                self.done.set(True)
                break
        self.done.set(True)


class SimulationStopper(Component):
    def setup(self, criterion: StoppingCriterion) -> None:
        self.env = env
        self.criterion = criterion
    
    def process(self):
        self.criterion.allow_dispatch.set(True)
        self.wait(self.criterion.done)
        env.main().activate()
