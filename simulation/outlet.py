import salabim as sim
import numpy as np

from .piece import Model, PickyPieceTaker
from .ables import Triggerable
from .helpers import check_probabilities
from .distribution import Distribution

from abc import ABC, abstractmethod
from typing import override


class Outlet(PickyPieceTaker, ABC):
    def __init__(self, valid_models: list[Model]) -> None:
        super().__init__(valid_models)

    @abstractmethod
    def get(self) -> Buffer:
        pass


class Buffer(sim.Store, Outlet, Triggerable):
    def setup(self, valid_models: list[Model]) -> None:
        Outlet.__init__(self, valid_models)
        Triggerable.__init__(self)

    @override
    def get(self) -> Buffer:
        return self


class Router(Outlet):
    def __init__(self, outlets_probs: dict[Outlet, Distribution]) -> None:
        for distr in outlets_probs.values():
            if distr.distr_type is not sim.Constant:
                raise ValueError("Randomness is not allowed in outlet probability variations with time")

        valid_models_sets = [set(outlet.valid_models) for outlet in outlets_probs.keys()]
        intersection = set.intersection(*valid_models_sets)

        if not intersection:
            raise ValueError("Router outlets must have at least one valid model in common")

        Outlet.__init__(self, list(intersection))
        self.outlets = list(outlets_probs.keys())
        self.probs = list(outlets_probs.values())

    @override
    def get(self) -> Buffer:
        probs = [distr.sample_now() for distr in self.probs]
        check_probabilities(probs=probs)
        return self.outlets[np.random.choice(len(self.outlets), p=probs)].get()
