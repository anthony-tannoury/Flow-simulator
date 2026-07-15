from __future__ import annotations


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .piece import PickyPieceTaker, Piece
    from .interval import Interval
    from .outlet import Outlet


def merge_touching_sorted_intervals(intervals: list[Interval]) -> list[Interval]:
    from .interval import Interval

    merged: list[Interval] = []
    for interval in intervals:
        if merged and interval.start == merged[-1].end:
            merged[-1] = Interval(merged[-1].start, interval.end)
        else:
            merged.append(interval)
    return merged


def check_disjoint_sorted_intervals(intervals: list[Interval]) -> None:
    for i in range(1, len(intervals)):
        if not intervals[i].disjoint(intervals[i-1]):
            raise ValueError("Intervals must be pairwise disjoint")


def check_probabilities(probs: list[float]) -> None:
    if not all(0 <= p <= 1 for p in probs):
        raise ValueError("Probabilities must be in [0,1]")

    if abs(sum(probs) - 1) > 1e-6:
        raise ValueError("Probabilities must sum to 1")


def check_outlet_validity(giver: PickyPieceTaker, outlets: list[Outlet]) -> None:
    from .piece import PickyPieceTaker
    
    if not outlets:
        raise ValueError("Giver must have at least one outlet")

    for i in range(len(outlets)):
        for j in range(i + 1, len(outlets)):
            if not PickyPieceTaker.disjoint(outlets[i], outlets[j]):
                raise ValueError("Outlets must have disjoint valid models sets")

    valid_models_sets = [set(outlet.valid_models) for outlet in outlets]
    union = set.union(*valid_models_sets)

    if not giver.can_flush_into(PickyPieceTaker(list(union))):
        raise ValueError("Giver must be able to flush all models into outlets")
    

def place(pieces: list[Piece], outlets: list[Outlet]):
    for piece in pieces:
        placed = False
        for outlet in outlets:
            if outlet.can_take(piece):
                piece.enter(outlet.get())
                placed = True
                break
        
        assert placed
