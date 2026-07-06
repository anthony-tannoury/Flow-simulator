import salabim as sim

from .resource import Triggerable, Resource


class Component(sim.Component):
    def request(self, *args, fail_at = None, fail_delay = None, mode = None, urgent = False, request_priority = 0, priority = 0, cap_now = None, oneof = False, called_from = "request"):
        super().request(*args, fail_at=fail_at, fail_delay=fail_delay, mode=mode, urgent=urgent, request_priority=request_priority, priority=priority, cap_now=cap_now, oneof=oneof, called_from=called_from)
        for r, q in args:
            if isinstance(r, Resource) and r.lifespan < float('inf'):
                r.shave(q)
            if q < 0 and isinstance(r, Triggerable):
                r.trigger.trigger()

    def release(self, *args):
        for r in self.requested_resources():
            if isinstance(r, Triggerable):
                r.trigger.trigger()
        super().release(*args)
