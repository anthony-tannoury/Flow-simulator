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


class ExitOrder(Enum):
    FIRST_IN_FIRST_OUT = auto()
    FIRST_CREATED_FIRST_OUT = auto()


class ModelChoice(Enum):
    MOST_PRESENT = auto()
    FASTEST_TASK_DURATION = auto()
    SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY = auto()


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
    def deadline(self, current_shift: Interval | None) -> float: ...


class ConstrainedByShift:
    def decide(self, current_shift: Interval | None, duration: float) -> Action:
        if current_shift is None:
            return Action.ABORT
        return Action.ABORT if env.now() + duration > current_shift.end else Action.LAUNCH
    
    def deadline(self, current_shift: Interval | None) -> float:
        return current_shift.end if current_shift is not None else float('inf')


class NotConstrainedByShift:
    def decide(self, current_shift: Interval | None, duration: float) -> Action:
        return Action.LAUNCH
    
    def deadline(self, current_shift: Interval | None) -> float:
        return float('inf')
    

class PartiallyConstrainedByShift:
    def __init__(self, tolerance: float) -> None:
        self.tolerance = tolerance

    def decide(self, current_shift: Interval | None, duration: float) -> Action:
        if current_shift is None:
            return Action.ABORT
        return Action.ABORT if env.now() + duration > current_shift.end + self.tolerance else Action.LAUNCH
    
    def deadline(self, current_shift: Interval | None) -> float:
        return current_shift.end + self.tolerance if current_shift is not None else float('inf')


class SelfConsciousness(Protocol):
    def decide(self) -> ConsciousnessState: ...


class Conscious:
    def decide(self) -> ConsciousnessState:
        return ConsciousnessState.CONSCIOUS


class Unconscious:
    def decide(self) -> ConsciousnessState:
        return ConsciousnessState.UNCONSCIOUS
    

class PieceExitOrder(Protocol):
    def decide(self) -> ExitOrder: ...


class FirstInFirstOut:
    def decide(self) -> ExitOrder:
        return ExitOrder.FIRST_IN_FIRST_OUT
    

class FirstCreatedFirstOut:
    def decide(self) -> ExitOrder:
        return ExitOrder.FIRST_CREATED_FIRST_OUT
    

class ModelChoiceCriteria(Protocol):
    def decide(self) -> ModelChoice: ...


class MostPresent:
    def decide(self) -> ModelChoice:
        return ModelChoice.MOST_PRESENT
    
class FastestTaskDuration:
    def decide(self) -> ModelChoice:
        return ModelChoice.FASTEST_TASK_DURATION
    
class SmallestGapToMinCarrierCapacity:
    def decide(self) -> ModelChoice:
        return ModelChoice.SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY
    
