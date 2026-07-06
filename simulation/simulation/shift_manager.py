import salabim as sim
from simulation import env

from .helpers import check_disjoint_sorted_intervals
from .interval import Interval, IntervalWaiter

from typing import override


class HasShifts:
    def __init__(self, shifts: list[Interval]) -> None:
        self.shifts = sorted(shifts, key=lambda shift: shift.start)
        check_disjoint_sorted_intervals(self.shifts)
        self.is_in_downtime = sim.State(value=True)

    def current_shift(self) -> Interval | None:
        for shift in self.shifts:
            if shift.start > env.now():
                break
            if shift.end >= env.now():
                return shift
        return None


class ShiftManager(IntervalWaiter):
    def setup(self, entity: HasShifts) -> None:
        super().setup(intervals=entity.shifts)
        self.entity = entity

    @override
    def on_enter(self, *args):
        self.entity.is_in_downtime.set(False)

    @override
    def on_leave(self, *args):
        self.entity.is_in_downtime.set(True)
