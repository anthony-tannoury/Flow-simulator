from __future__ import annotations

import salabim as sim
import numpy as np

from simulation import env
from .component import Component
from .helpers import check_outlet_validity, check_probabilities, place
from .shift_manager import ShiftManager, HasShifts
from .interval import Interval
from .ables import Triggerable
from abc import ABC, abstractmethod
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .outlet import Outlet


class Model:
    def __init__(self, name: str) -> None:
        self.name = name
        self.parent = None
        self.children: list[Model] = []

    def set_parent(self, parent: Model) -> None:
        self.parent = parent
        self.parent.children.append(self)


class Piece(sim.Component):
    ID = 0

    def setup(self, model: Model) -> None:
        from .kpis import WIP
        self.model = model
        self.id = str(Piece.ID).zfill(6)
        Piece.ID += 1
        self.journal: list[tuple[str, str, float]] = []
        WIP.tally(WIP() + 1)

    def enter(self, q, priority = None):
        from .outlet import Buffer, BufferType
        from .kpis import WIP
        assert isinstance(q, Buffer)
        q.trigger.trigger()
        if q.piece_generator is not None:
            idx = q.piece_generator.models.index(self.model)
            q.piece_generator.generated[idx] -= 1
            q.piece_generator.trigger.trigger()
        if q.buffer_type in (BufferType.EXIT, BufferType.SCRAP):
            WIP.tally(WIP() - 1)
        self.journal.append(('in', q.name(), env.now()))
        return super().enter(q, priority)

    def leave(self, q=None):
        from .outlet import Buffer
        if isinstance(q, Buffer):
            self.journal.append(('out', q.name(), env.now()))
        return super().leave(q)


class PickyPieceTaker:
    def __init__(self, valid_models: list[Model]) -> None:
        if not valid_models:
            raise ValueError("PickyPieceTaker must have at least one valid model")

        self.valid_models = valid_models

    def can_take(self, obj: Piece | Model) -> bool:
        model = obj.model if isinstance(obj, Piece) else obj
        can_take = False
        while model is not None and not can_take:
            can_take |= model in self.valid_models
            model = model.parent
        return can_take

    def can_flush_into(self, ppt: PickyPieceTaker) -> bool:
        return all(ppt.can_take(model) for model in self.valid_models)

    def disjoint(self, other: PickyPieceTaker) -> bool:
        return not (any(self.can_take(model) for model in other.valid_models)
                    or any(other.can_take(model) for model in self.valid_models))


class PieceGenerator(Component, PickyPieceTaker, HasShifts, Triggerable, ABC):
    COUNT = 0

    def setup(self, models: list[Model], shifts: list[Interval], outlets: list[Outlet]) -> None:
        if PieceGenerator.COUNT > 0:
            raise ValueError("Cannot have more than one piece generator")
        PieceGenerator.COUNT += 1

        self.models = list(models)
        PickyPieceTaker.__init__(self, self.models)
        HasShifts.__init__(self, shifts)
        Triggerable.__init__(self)
        check_outlet_validity(self, outlets)

        self.shift_manager = ShiftManager(entity=self)
        self.outlets = outlets
        self.generated = [0 for _ in range(len(self.models))]
        self.total_generated = [0 for _ in range(len(self.models))]

    def emit(self, idx: int) -> None:
        piece = Piece(model=self.models[idx])
        place([piece], self.outlets)
        self.generated[idx] += 1
        self.total_generated[idx] += 1

    def hold_within_shift(self, gap: float) -> bool:
        current_shift = self.current_or_last_shift()
        shift_time_left = current_shift.end - env.now() if current_shift is not None else float('inf')
        if gap > shift_time_left:
            self.hold(shift_time_left)
            return False
        self.hold(gap)
        return True

    @abstractmethod
    def process(self):
        pass


class GoalPieceGenerator(PieceGenerator):
    def setup(self, models_goals: dict[Model, int], shifts: list[Interval], outlets: list[Outlet],
              grace_period: float = 0.0, gap: float | None = None) -> None:
        super().setup(list(models_goals.keys()), shifts, outlets)
        self.goals = list(models_goals.values())
        self.probs = [0.0 for _ in range(len(self.models))]
        self.total_goal = sum(self.goals)
        if gap is not None:

            if grace_period:
                raise ValueError("Grace period only applies to the automatic gap")
            if gap <= 0:
                raise ValueError("Gap must be > 0")
            self.gap = gap
        else:


            working_time = sum(shift.length for shift in shifts)
            if grace_period < 0:
                raise ValueError("Grace period must be >= 0")
            if grace_period >= working_time:
                raise ValueError(f"Grace period ({grace_period}) must be smaller than the "
                                 f"generator's total shift time ({working_time})")
            self.gap = (working_time - grace_period) / self.total_goal

    def update_probs(self) -> None:
        total_generated = sum(self.generated)
        if self.total_goal == total_generated:
            self.probs = [0.0 for _ in range(len(self.models))]
        else:
            for i in range(len(self.models)):
                self.probs[i] = (self.goals[i] - self.generated[i]) / (self.total_goal - total_generated)

    def process(self):
        while True:
            self.wait((self.is_in_downtime, False))


            self.update_probs()
            if sum(self.probs) == 0:
                self.wait(self.trigger)
                continue

            current_shift = self.current_or_last_shift()
            shift_time_left = current_shift.end - env.now() if current_shift is not None else float('inf')
            if self.gap > shift_time_left:
                self.hold(shift_time_left)
                continue

            self.hold(self.gap)
            idx = np.random.choice(len(self.models), p=self.probs)
            self.emit(idx)


class RatePieceGenerator(PieceGenerator):
    def setup(self, models: list[Model], shifts: list[Interval], outlets: list[Outlet],
              gap: float | Callable[[float], float],
              model_probs: list[float | Callable[[float], float] | None]) -> None:
        if model_probs.count(None) > 1:
            raise ValueError("At most one model can be the freeloader in a rate generator")
        super().setup(models, shifts, outlets)
        self.gap = gap
        self.model_probs = model_probs
        self.freeloader_index = model_probs.index(None) if None in model_probs else -1

    def current_gap(self) -> float:
        gap = self.gap if isinstance(self.gap, (int, float)) else self.gap(env.now())
        if gap <= 0:


            raise ValueError(f"Rate generator gap must stay > 0; got {gap:.4f} at t={env.now():.1f}")
        return gap

    def current_probs(self) -> list[float]:
        probs = [0.0 if p is None else (p if isinstance(p, (int, float)) else p(env.now()))
                 for p in self.model_probs]
        if self.freeloader_index != -1:
            probs[self.freeloader_index] = 1 - sum(probs)
        check_probabilities(probs)
        return probs

    def process(self):
        while True:
            self.wait((self.is_in_downtime, False))
            if not self.hold_within_shift(self.current_gap()):
                continue
            idx = np.random.choice(len(self.models), p=self.current_probs())
            self.emit(idx)
