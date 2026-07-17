from __future__ import annotations

import salabim as sim
import numpy as np

from simulation import env
from .component import Component
from .helpers import check_outlet_validity, place
from .shift_manager import ShiftManager, HasShifts
from .interval import Interval
from typing import TYPE_CHECKING

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
        self.journal: list[tuple[str, str, float]] = []  # ('in'|'out'|'task', name, t)
        WIP.tally(WIP() + 1)

    def enter(self, q, priority = None):
        from .outlet import Buffer, BufferType
        from .kpis import WIP
        assert isinstance(q, Buffer)
        q.trigger.trigger()
        if q.piece_generator is not None:
            idx = q.piece_generator.models.index(self.model)
            q.piece_generator.generated[idx] -= 1
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


class PieceGenerator(Component, PickyPieceTaker, HasShifts):
    COUNT = 0

    def setup(self, models_goals: dict[Model, int], shifts: list[Interval], outlets: list[Outlet]) -> None:
        if PieceGenerator.COUNT > 0:
            raise ValueError("Cannot have more than one piece generator")
        PieceGenerator.COUNT += 1
        
        self.models = list(models_goals.keys())
        PickyPieceTaker.__init__(self, self.models)
        HasShifts.__init__(self, shifts)
        check_outlet_validity(self, outlets)

        self.shift_manager = ShiftManager(entity=self)

        self.outlets = outlets
        self.goals = list(models_goals.values())
        self.probs = [0.0 for _ in range(len(self.models))]
        self.generated = [0 for _ in range(len(self.models))]
        self.total_generated = [0 for _ in range(len(self.models))]  # physical births, scrap remakes included

        self.total_goal = sum(self.goals)
        self.gap = sum(shift.length for shift in shifts) / self.total_goal

    def update_probs(self) -> None:
        total_generated = sum(self.generated)
        for i in range(len(self.models)):
            self.probs[i] = (self.goals[i] - self.generated[i]) / (self.total_goal - total_generated)

    def process(self):
        while sum(self.generated) < self.total_goal:
            self.wait((self.is_in_downtime, False))

            current_shift = self.current_or_last_shift()
            shift_time_left = current_shift.end - env.now() if current_shift is not None else float('inf')
            if self.gap > shift_time_left:
                self.hold(shift_time_left)
                continue

            self.update_probs()
            self.hold(self.gap)
            idx = np.random.choice(len(self.models), p=self.probs)
            piece = Piece(model=self.models[idx])
            place([piece], self.outlets)
            self.generated[idx] += 1
            self.total_generated[idx] += 1
