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

    def current_or_last_shift(self) -> Interval | None:
        for i, shift in enumerate(self.shifts):
            if shift.start > env.now():
                return self.shifts[i - 1] if i > 0 else None
            if shift.end >= env.now():
                return shift
        return self.shifts[-1] if self.shifts else None


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
    def shift_generator(shifts_per_week: list[list[Interval]], working_days_per_week: list[bool], days_off: list[int], horizon: Interval) -> list[Interval]:

        if len(shifts_per_week) != 7:
            raise ValueError("There must be 7 lists of shifts per week, one for each day")

        if len(working_days_per_week) != 7:
            raise ValueError("There must be 7 working days per week")

        if horizon.start != int(horizon.start) or horizon.end != int(horizon.end):
            raise ValueError("Horizon start and end must be integer day numbers")
        
        if not all(isinstance(d, int) for d in days_off):
            raise ValueError("Closing days must be integer day numbers")

        start_day = int(horizon.start)
        end_day = int(horizon.end)

        if not all(start_day <= d < end_day for d in days_off):
            raise ValueError("Closing days must be within horizon")

        days_off_set = set(days_off)

        for shifts_per_day in shifts_per_week:
            shifts_per_day.sort(key=lambda x: x.start)
            check_disjoint_sorted_intervals(shifts_per_day)

        all_shifts = []

        for day in range(start_day, end_day):
            if working_days_per_week[day % 7] and day not in days_off_set:
                for shift in shifts_per_week[day % 7]:
                    new_shift = shift.copy()
                    new_shift.translate(day * 1440)
                    all_shifts.append(new_shift)

        return all_shifts
