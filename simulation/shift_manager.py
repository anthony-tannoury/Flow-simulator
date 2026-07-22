import salabim as sim
from simulation import env
from datetime import datetime, date, time, timedelta

from .helpers import check_disjoint_sorted_intervals, merge_touching_sorted_intervals
from .interval import Interval, IntervalWaiter

from .compat import override


class HasShifts:
    def __init__(self, shifts: list[Interval]) -> None:
        self.shifts = merge_touching_sorted_intervals(sorted(shifts, key=lambda shift: shift.start))
        check_disjoint_sorted_intervals(self.shifts)

        self.is_in_downtime = sim.State(value=True)

    def current_or_last_shift(self) -> Interval | None:
        for i, shift in enumerate(self.shifts):
            if shift.start > env.now():
                return self.shifts[i - 1] if i > 0 else None
            if shift.end >= env.now():
                return shift
        return self.shifts[-1] if self.shifts else None

    def next_or_current_shift_from(self, cursor: float) -> Interval | None:
        for shift in self.shifts:
            if shift.end > cursor:
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
    def minutes_between(date1: datetime | str, date2: datetime | str) -> int:
        format_str = "%d-%m-%Y %H:%M"
        if isinstance(date1, str):
            date1 = datetime.strptime(date1, format_str)
        if isinstance(date2, str):
            date2 = datetime.strptime(date2, format_str)
        delta = date2 - date1
        return int(delta.total_seconds() // 60)

    @staticmethod
    def generate_weekly_shifts(sim_start: datetime, shifts_per_day: list[list[tuple[float, float]]], working_days: list[bool], days_off: set[date], start: date, end: date) -> list[Interval]:
        if len(shifts_per_day) != 7:
            raise ValueError("There must be 7 lists of shifts per week, one for each day")

        if len(working_days) != 7:
            raise ValueError("There must be 7 working days per week")

        week_offset = sim_start.weekday()
        time_offset = 60 * sim_start.hour + sim_start.minute
        days_off_rel = {(day_off - sim_start.date()).days for day_off in days_off}

        intervals_per_day = [[Interval(s, e) for s, e in shift] for shift in shifts_per_day]

        all_shifts = []
        for i in range((start - sim_start.date()).days, (end - sim_start.date()).days + 1):
            if working_days[(i + week_offset) % 7] and i not in days_off_rel:
                for shift in intervals_per_day[(i + week_offset) % 7]:
                    new_shift = shift.copy()
                    new_shift.translate(i * 1440 - time_offset)
                    all_shifts.append(new_shift)

        return all_shifts

    @staticmethod
    def generate_custom_shifts(sim_start: datetime, shifts: list[tuple[datetime, datetime]], days_off: set[date]) -> list[Interval]:
        datetime_ranges = []
        for start, end in shifts:
            pieces = [(start, end)]
            for day_off in days_off:
                d_start = datetime.combine(day_off, time.min)
                d_end = d_start + timedelta(days=1)
                new_pieces = []
                for s, e in pieces:
                    if s < d_start:
                        new_pieces.append((s, min(e, d_start)))
                    if d_end < e:
                        new_pieces.append((max(s, d_end), e))
                pieces = new_pieces
            datetime_ranges.extend(pieces)

        return [Interval(ShiftManager.minutes_between(sim_start, start), ShiftManager.minutes_between(sim_start, end)) for start, end in datetime_ranges]
