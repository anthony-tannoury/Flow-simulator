from simulation import env
from typing import Protocol
from enum import Enum, auto

from .interval import Interval


class Action(Enum):
    ABORT = auto()
    WAIT = auto()
    LAUNCH = auto()


class ConsciousnessState(Enum):
    CONSCIOUS = auto()
    UNCONSCIOUS = auto()


class PendingCarriers(Protocol):
    def decide(self, min_carriers: int, pending_carriers: int) -> Action: ...


class AbortPendingCarriers:
    def decide(self, min_carriers: int, pending_carriers: int) -> Action:
        return Action.ABORT
    

class WaitForCarriers:
    def decide(self, min_carriers: int, pending_carriers: int) -> Action:
        return Action.WAIT
    

class AbortOrWaitForCarriers:
    def __init__(self, tolerance_fraction: float):
        self.tolerance_fraction = tolerance_fraction
    
    def decide(self, min_carriers: int, pending_carriers: int) -> Action:
        return Action.ABORT if pending_carriers < min_carriers * self.tolerance_fraction else Action.WAIT


class ShiftConstraint(Protocol):
    def decide(self, current_shift: Interval | None, duration: float) -> Action: ...


class ConstrainedByShift:
    def decide(self, current_shift: Interval | None, duration: float) -> Action:
        if current_shift is None:
            return Action.ABORT
        return Action.ABORT if env.now() + duration > current_shift.end else Action.LAUNCH


class NotConstrainedByShift:
    def decide(self, current_shift: Interval | None, duration: float) -> Action:
        return Action.LAUNCH


class SelfConsciouness(Protocol):
    def decide(self) -> ConsciousnessState: ...


class Conscious:
    def decide(self) -> ConsciousnessState:
        return ConsciousnessState.CONSCIOUS


class Unconscious:
    def decide(self) -> ConsciousnessState:
        return ConsciousnessState.UNCONSCIOUS
