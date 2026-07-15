from __future__ import annotations
import salabim as sim

from simulation import env
from datetime import datetime, date, time, timedelta
from .component import Component
from .interval import Interval, IntervalWaiter
from .protocols import Action
from abc import ABC
from typing import override, TYPE_CHECKING

if TYPE_CHECKING:
    from .task import Task
    from .sampler import Sampler, Distribution
    from .outlet import Outlet


class Breakdown(Component, ABC):
    def setup(self, task: Task, mtbf: Sampler, mttr: Distribution, outlets: list[Outlet] | None = None) -> None:
        from .resource_task import ResourceTask
        from .piece_task import PieceTask

        if outlets is None:
            outlets = []

        if outlets and isinstance(task, ResourceTask):
            raise ValueError("Breakdown on resource task cannot have outlets")
        
        if not outlets and isinstance(task, PieceTask):
            raise ValueError("Breakdowns on piece tasks must have outlets")
    
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


class Shutdowns(IntervalWaiter, ABC):
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
        self.task.abort()
        self.task.is_in_shutdown.set(True)

    @override
    def on_leave(self, *args):
        self.task.is_in_shutdown.set(False)
        self.task.is_frozen.set(False)

    @staticmethod
    def generate_periodic_shutdown(task: Task, in_between: float, shutdown_duration: float, sim_start: datetime, start: datetime, end: datetime) -> list[Interval]:
        if start < sim_start:
            raise ValueError("Periodic shutdowns start must be after simulation start")
        if start >= end:
            raise ValueError("Periodic shutdowns start must be before end")

        cursor = (start - sim_start).total_seconds() // 60
        horizon_end = (end - sim_start).total_seconds() // 60
        intervals: list[Interval] = []

        while cursor < horizon_end:
            current_or_next_shift = task.next_or_current_shift_from(cursor)
            if current_or_next_shift is None:
                break

            if cursor > current_or_next_shift.start and cursor + shutdown_duration <= current_or_next_shift.end:
                intervals.append(Interval(cursor, cursor + shutdown_duration))
                cursor += in_between
            elif (wiggle_room := current_or_next_shift.length - shutdown_duration) >= 0:
                cursor += sim.Uniform(0, wiggle_room).sample()
                intervals.append(Interval(cursor, cursor + shutdown_duration))
                cursor += in_between
            else:
                cursor = current_or_next_shift.end
                next_shift = task.next_or_current_shift_from(cursor)
                if next_shift is None:
                    break
                cursor = next_shift.start

        return intervals


class FlexibleShutdowns(Shutdowns):
    def setup(self, task: Task, intervals: list[Interval]):
        super().setup(task=task, intervals=intervals)
        self.task.flexible_shutdowns = self

    def rearrange(self, idx: int) -> None:
        while idx + 1 < len(self.intervals) and self.intervals[idx + 1].end < self.intervals[idx].start:
            self.intervals.pop(idx + 1)

    def adapt(self, operation_interval: Interval) -> bool:
        for i, interval in enumerate(self.intervals):
            if not Interval.disjoint(operation_interval, interval) and interval.start <= operation_interval.end:
                interval.translate(operation_interval.end - interval.start)
                self.rearrange(i)
                return True

        return False
    
    @override
    def process(self):
        while True:
            next_shutdown = self.get_next_shutdown()
            if next_shutdown is None:
                break

            if env.now() < next_shutdown.start:
                self.hold(till=next_shutdown.start)
                continue

            if self.task.config.protocols.pending_carriers_pre_flexible_shutdowns.decide(self.task.config.min_carriers, len(self.task.pending_carriers)) is Action.WAIT:
                self.wait((self.task.active_carriers.num_carriers, 0), (self.task.pending_carriers.num_carriers, 0), all=True)
            else:
                self.wait((self.task.active_carriers.num_carriers, 0))

            current = self.get_next_shutdown()
            if current is None or env.now() < current.start:
                continue

            self.task.abort()
            self.task.is_in_shutdown.set(True)
            self.hold(till=current.end, cap_now=True)
            self.task.is_in_shutdown.set(False)
            self.task.is_frozen.set(False)
            if current in self.intervals:
                self.intervals.remove(current)


class NonFlexibleShutdowns(Shutdowns):
    def setup(self, task: Task, intervals:list[Interval]):
        super().setup(task=task, intervals=intervals)
        self.task.non_flexible_shutdowns = self
