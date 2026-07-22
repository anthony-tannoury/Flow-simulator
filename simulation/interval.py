from __future__ import annotations

from .helpers import check_disjoint_sorted_intervals, merge_touching_sorted_intervals
from .component import Component

from abc import ABC, abstractmethod


class Time(float):
    def __new__(cls, h: float, m: float, s: float = 0):
        value = 60 * h + m + s / 60
        return super().__new__(cls, value)


class Interval:
    def __init__(self, start: float, end: float) -> None:
        if end < start:
            raise ValueError("Interval start must be before interval end")

        self.start = start
        self.end = end

    @property
    def length(self) -> float:
        return self.end - self.start

    def translate(self, t: float) -> None:
        self.start += t
        self.end += t

    def disjoint(self, other: Interval) -> bool:
        return min(self.end, other.end) < max(self.start, other.start)

    def copy(self) -> Interval:
        return Interval(self.start, self.end)

    def __repr__(self):
        return f"[{self.start}, {self.end}]"


class IntervalWaiter(Component, ABC):
    def setup(self, intervals: list[Interval]) -> None:
        self.intervals = merge_touching_sorted_intervals(sorted(intervals, key=lambda i: i.start))
        check_disjoint_sorted_intervals(self.intervals)

    @abstractmethod
    def on_enter(self, *args) -> None:
        pass

    @abstractmethod
    def on_leave(self, *args) -> None:
        pass

    def process(self):
        for interval in self.intervals:
            self.hold(till=interval.start, cap_now=True)
            self.on_enter()
            self.hold(till=interval.end, cap_now=True)
            self.on_leave()
