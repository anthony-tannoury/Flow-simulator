import salabim as sim
import numpy as np

from .component import Component
from .piece import PickyPieceTaker, Model, Piece
from .outlet import Outlet
from .helpers import check_outlet_validity, place
from .shift_manager import ShiftManager, HasShifts
from .interval import Interval


class PieceGenerator(Component, PickyPieceTaker, HasShifts):
    def setup(self, models_goals: dict[Model, int], shifts: list[Interval], outlets: list[Outlet]) -> None:
        self.models = list(models_goals.keys())
        PickyPieceTaker.__init__(self, self.models)
        HasShifts.__init__(self, shifts)
        check_outlet_validity(self, outlets)

        self.shift_manager = ShiftManager(entity=self)

        self.outlets = outlets
        self.goals = list(models_goals.values())
        self.probs = [0.0 for _ in range(len(self.models))]
        self.generated = [0 for _ in range(len(self.models))]

        self.total_goal = sum(self.goals)
        self.gap = sum(shift.length for shift in shifts) / self.total_goal

    def update_probs(self) -> None:
        total_generated = sum(self.generated)
        for i in range(len(self.models)):
            self.probs[i] = (self.goals[i] - self.generated[i]) / (self.total_goal - total_generated)

    def process(self):
        while sum(self.generated) < self.total_goal:
            self.wait((self.is_in_downtime, False), (self.is_in_shutdown, False), (self.is_in_breakdown, False), all=True)
            self.update_probs()
            inter_arrival_time = self.gap
            if self.current_or_last_shift() is not None:
                inter_arrival_time = min(inter_arrival_time, self.current_or_last_shift.end)
            self.hold(inter_arrival_time)
            idx = np.random.choice(len(self.models), p=self.probs)
            piece = Piece(model=self.models[idx])
            place([piece], self.outlets)
            self.generated[idx] += 1
