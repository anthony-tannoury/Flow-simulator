import salabim as sim

from simulation import env
from .component import Component
from .interval import Interval, IntervalWaiter
from .distribution import Distribution
from .task import Task
from .piece_task import PieceTask
from .resource_task import ResourceTask
from .outlet import Outlet
from .helpers import check_outlet_validity
from abc import ABC
from typing import override


class Breakdown(Component, ABC):
    def setup(self, task: Task, mtbf: Distribution, mttr: Distribution, outlets: list[Outlet] | None = None) -> None:
        if outlets is None:
            outlets = []

        if outlets and isinstance(task, ResourceTask):
            raise ValueError("Breakdown on resource task cannot have outlets")
    
        self.task = task
        self.mtbf = mtbf
        self.mttr = mttr
        self.outlets = outlets

    def process(self):
        while True:
            self.wait((self.task.is_in_shutdown, False))
            self.hold(self.mtbf.sample_now())

            if self.task.is_in_shutdown.get():
                continue

            self.task.abort(self.outlets)
            self.task.is_in_breakdown.set(True)
            self.hold(self.mttr.sample_now())
            self.task.is_in_breakdown.set(False)


class Shutdowns(IntervalWaiter):
    def setup(self, task: Task, intervals: list[Interval]):
        super().setup(intervals=intervals)
        self.task = task

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
        self.task.abort(self.task.inlets)
        self.task.is_in_shutdown.set(True)

    @override
    def on_leave(self, *args):
        self.task.is_in_shutdown.set(False)
        self.task.is_frozen.set(False)


class FlexibleShutdowns(Shutdowns):
    def setup(self, task: Task, intervals: list[Interval]):
        super().setup(task=task, intervals=intervals)
        self.task.flexible_shutdowns = self

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
        while True:
            self.wait((self.task.active_carriers.num_carriers, 0))

            next_shutdown = self.get_next_shutdown()
            if next_shutdown is None:
                break

            self.hold(till=next_shutdown.start, cap_now=True)
            self.task.abort(self.task.inlets)
            self.task.is_in_shutdown.set(True)
            self.hold(till=next_shutdown.end, cap_now=True)
            self.task.is_in_shutdown.set(False)
            self.task.is_frozen.set(False)


class NonFlexibleShutdowns(Shutdowns):
    def setup(self, task: Task, intervals:list[Interval]):
        super().setup(task=task, intervals=intervals)
        self.task.non_flexible_shutdowns = self
