from __future__ import annotations

import salabim as sim
import numpy as np

from simulation import env
from .piece import Model, PickyPieceTaker, PieceGenerator
from .ables import Triggerable
from .helpers import check_probabilities
from abc import ABC, abstractmethod
from typing import override, Callable
from enum import Enum, auto


class BufferType(Enum):
    PASSAGE = auto()
    SCRAP = auto()
    EXIT = auto()


class Outlet(PickyPieceTaker, ABC):
    def __init__(self, valid_models: list[Model]) -> None:
        super().__init__(valid_models)

    @abstractmethod
    def get(self) -> Buffer:
        pass


class Buffer(sim.Store, Outlet, Triggerable):
    EXIT_BUFFERS = 0

    def setup(self, valid_models: list[Model], buffer_type: BufferType, piece_generator: PieceGenerator | None = None) -> None:
        if buffer_type is BufferType.SCRAP and piece_generator is None:
            raise ValueError("Scrap buffer must be connected to piece generator")
        if buffer_type is not BufferType.SCRAP and piece_generator is not None:
            raise ValueError("Non-scrap buffer must not be connected to piece generator")
        if buffer_type is BufferType.EXIT:
            if Buffer.EXIT_BUFFERS == 1:
                raise ValueError("Simulation cannot have more than 1 exit buffer")
            Buffer.EXIT_BUFFERS += 1

        Outlet.__init__(self, valid_models)
        Triggerable.__init__(self)
        self.buffer_type = buffer_type
        self.piece_generator = piece_generator

    @override
    def get(self) -> Buffer:
        return self


class Router(Outlet):
    def __init__(self, outlets_probs: dict[Outlet, float | Callable[[float], float] | None]) -> None:
        outlets_probs_values = list(outlets_probs.values())
        if outlets_probs_values.count(None) > 1:
            raise ValueError("At most one freeloader are allowed in router")

        valid_models_sets = [set(outlet.valid_models) for outlet in outlets_probs.keys()]
        intersection = set.intersection(*valid_models_sets)

        if not intersection:
            raise ValueError("Router outlets must have at least one valid model in common")

        Outlet.__init__(self, list(intersection))
        self.outlets = list(outlets_probs.keys())
        self.probs = list(outlets_probs.values())
        self.freeloader_index = outlets_probs_values.index(None) if None in outlets_probs_values else -1

    @override
    def get(self) -> Buffer:
        probs = [p if isinstance(p, (int, float)) else p(env.now()) if p is not None else 0 for p in self.probs]
        if self.freeloader_index != -1:
            probs[self.freeloader_index] = 1 - sum(probs)

        check_probabilities(probs=probs)
        return self.outlets[np.random.choice(len(self.outlets), p=probs)].get()
