from __future__ import annotations

import salabim as sim

from simulation import env
from .ables import Triggerable
from .interval import Interval
from .shift_manager import ShiftManager, HasShifts
from .sampler import Distribution

from typing import override


class OperatorShiftManager(ShiftManager):
    def setup(self, operator_group: OperatorGroup) -> None:
        super().setup(entity=operator_group)

    @override
    def on_enter(self, *args) -> None:
        assert isinstance(self.entity, OperatorGroup)
        self.entity.set_capacity(self.entity.n_operators)
        self.entity.trigger.trigger()
        # a task frozen because these operators had left resumes when they come back,
        # instead of staying frozen until its own (possibly weeks-away) shift start
        for task in self.entity.dependent_tasks:
            task.is_frozen.set(False)
        super().on_enter(*args)

    @override
    def on_leave(self, *args) -> None:
        assert isinstance(self.entity, OperatorGroup)
        self.entity.set_capacity(0)
        super().on_leave(*args)


class OperatorGroup(sim.Resource, HasShifts, Triggerable):
    def __init__(self, *args, **kwargs) -> None:
        kwargs['anonymous'] = False
        super().__init__(*args, **kwargs)

    def setup(self, shifts: list[Interval], productivity: Distribution) -> None:
        Triggerable.__init__(self)
        HasShifts.__init__(self, shifts)
        self.productivity = productivity
        self.n_operators = self.capacity()
        self.set_capacity(0)
        self.dependent_tasks: list = []  # tasks to unfreeze when this group comes back on shift
        self.manager = OperatorShiftManager(operator_group=self)
        

class Alternative:
    def __init__(self, *alternatives: list[tuple[OperatorGroup, int]]):
        self.alternatives = alternatives
        if not alternatives:
            return
        
        for alt in alternatives:
            productivity = alt[0][0].productivity
            if not all(o.productivity is productivity for o, _ in alt):
                raise ValueError("Operators do not have the same productivity")
            
        self.triggers = [r.trigger for alt in alternatives for r, _ in alt]

    def request(self, demander: sim.Component, **kwargs) -> list[tuple[OperatorGroup, int]] | None:
        if not self.alternatives:
            return []
        
        if 'fail_at' in kwargs:
            fail_at = kwargs['fail_at']
        elif 'fail_delay' in kwargs:
            fail_at = kwargs['fail_delay'] + env.now()
        else:
            fail_at = float('inf')

        cap_now = kwargs.get("cap_now", False)

        if len(self.alternatives) == 1:
            demander.request(*self.alternatives[0], fail_at=fail_at, cap_now=cap_now, mode="wait_operators")
            if not demander.failed():
                return self.alternatives[0]
            return None

        while True:
            for alt in self.alternatives:
                demander.request(*alt, fail_delay=0, mode="wait_operators")
                if not demander.failed():
                    return alt

            demander.wait(*self.triggers, fail_at=fail_at, cap_now=cap_now, mode="wait_operators")
            if demander.failed():
                return None
            
    def __bool__(self):
        return bool(self.alternatives)
