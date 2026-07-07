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

    @staticmethod
    def shift_generator(shifts_per_week: list[list[Interval]], working_days_per_week: list[bool], days_off: list[int], horizon: int) -> list[Interval]:
        if len(shifts_per_week) != 7:
            raise ValueError("There must be 7 lists of shifts per week, one for each day")
        
        if len(working_days_per_week) != 7:
            raise ValueError("There must be 7 working days per week")
        
        if not all(0 <= d < horizon for d in days_off):
            raise ValueError("Closing days must be within horizon")
        
        for shifts_per_day in shifts_per_week:
            shifts_per_day.sort(key=lambda x: x.start)
            check_disjoint_sorted_intervals(shifts_per_day)

        all_shifts = []
        for i in range(horizon):
            if working_days_per_week[i % 7] and i not in days_off:
                for shift in shifts_per_week[i % 7]:
                    new_shift = shift.copy()
                    new_shift.translate(i * 1440)
                    all_shifts.append(new_shift)

        return all_shifts
