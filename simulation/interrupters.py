import salabim as sim

from simulation import env
from .component import Component
from .interval import Interval, IntervalWaiter
from .distribution import Distribution
from .task import Task
from abc import ABC, abstractmethod
from typing import Callable, override


class Interruptible(ABC):
    def __init__(self) -> None:
        self.non_flexible_shutdowns = NonFlexibleShutdowns()
        self.flexible_shutdowns = FlexibleShutdowns()
        self.is_in_breakdown = sim.State(value=False)
        self.is_in_shutdown = sim.State(value=False)
        self.is_frozen = sim.State(value=False)

    def get_earliest_shutdown(self) -> Interval | None:
        fs = self.flexible_shutdowns.get_next_shutdown()
        nfs = self.non_flexible_shutdowns.get_next_shutdown()

        if fs is not None and nfs is not None:
            return min(fs, nfs, key=lambda s: s.start)
        elif nfs is None:
            return fs
        return nfs
    
    def get_earliest_deadline(self) -> float:
        earliest_shutdown = self.get_earliest_shutdown()
        return earliest_shutdown.start if earliest_shutdown is not None else float('inf')

    @abstractmethod
    def abort(self) -> None:
        pass


class Breakdown(Component, ABC):
    def setup(self, entity: Interruptible, mtbf: Distribution, mttr: Distribution) -> None:
        self.entity = entity
        self.mtbf = mtbf
        self.mttr = mttr

    def process(self):
        while True:
            self.wait((self.entity.is_in_shutdown, False))
            self.hold(till=self.mtbf.sample_now())

            if self.entity.is_in_shutdown.get():
                continue

            self.entity.abort()

            self.entity.is_in_breakdown.set(True)
            self.hold(self.mttr.sample_now())
            self.entity.is_in_breakdown.set(False)


class Shutdowns(IntervalWaiter):
    def setup(self, entity: Interruptible, intervals: list[Interval]):
        super().setup(intervals=intervals)
        self.entity = entity

    def get_next_shutdown(self) -> Interval | None:
        for interval in self.intervals:
            if interval.end > env.now():
                return interval
        return None

    def get_deadline(self) -> float:
        next_shutdown = self.get_next_shutdown()
        return next_shutdown.start if next_shutdown is not None else float('inf')
    
    @override
    def on_enter(self, *args):
        self.entity.is_in_shutdown.set(True)

    @override
    def on_leave(self, *args):
        self.entity.is_in_shutdown.set(False)
        self.entity.is_frozen.set(False)


class FlexibleShutdowns(Shutdowns):
    def setup(self, task: Task, intervals: list[Interval]):
        super().setup(entity=task, intervals=intervals)
        self.entity.flexible_shutdowns = self

    def rearrange(self, idx: int) -> None:
        while idx + 1 < len(self.intervals) and self.intervals[idx + 1].end < self.intervals[idx].start:
            self.intervals.pop(idx + 1)

    def adapt(self, operation_interval: Interval) -> bool:
        for i, interval in enumerate(self.intervals):
            if not Interval.disjoint(operation_interval, interval):
                interval.translate(operation_interval.end - interval.start)
                self.rearrange(i)
                return True

        return False
    
    @override
    def process(self):
        assert isinstance(self.entity, Task)
        while True:
            self.wait((self.entity.active_carriers.num_carriers, 0))

            next_shutdown = self.get_next_shutdown()
            if next_shutdown is None:
                break

            self.hold(till=next_shutdown.start, cap_now=True)
            self.task.started_up = False
            self.task.is_in_shutdown.set(True)
            self.hold(till=next_shutdown.end, cap_now=True)
            self.task.is_in_shutdown.set(False)
            self.task.is_frozen.set(False)
        


class NonFlexibleShutdowns(Shutdowns):
    def setup(self, entity: Interruptible, intervals:list[Interval]):
        super().setup(entity=entity, intervals=intervals)
        self.entity.non_flexible_shutdowns = self

