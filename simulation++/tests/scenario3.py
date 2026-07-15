# Twin scenario 3 (Python side) — midnight-crossing weekly shifts + touching-
# interval merging. Must match scenario3.cpp. Requires the merge changes:
# merge_touching_sorted_intervals applied in HasShifts/IntervalWaiter and
# generate_weekly_shifts taking minutes-of-day pairs.
import sys
from datetime import date, datetime

import salabim as sim
from simulation import env
from simulation.interval import Interval
from simulation.shift_manager import ShiftManager
from simulation.piece import Model, PieceGenerator
from simulation.outlet import Buffer, BufferType
from simulation.sampler import Distribution
from simulation.operator import OperatorGroup, Alternative
from simulation.protocols import (AbortPendingCarriers, NotConstrainedByShift, Conscious)
from simulation.task import Protocols, Scope
from simulation.piece_task import (PieceTaskConfig, ModelConfig, PieceTask, PieceCollectorType)
from simulation.interrupters import NonFlexibleShutdowns
from simulation.judgement_day import ByTime, SimulationStopper

env.trace(True)

model_a = Model("A")

# Mon..Fri: early piece 00:00-06:00 + night piece 22:00-24:00 (in minutes).
# Consecutive working days merge at midnight into 22:00 -> 06:00 night shifts;
# Wednesday is a day off, so Tuesday's night piece stays a 22:00-24:00 stub.
night = [(0.0, 360.0), (1320.0, 1440.0)]
gen_shifts = ShiftManager.generate_weekly_shifts(
    sim_start=datetime(2026, 1, 5),                # a Monday, 00:00
    shifts_per_day=[night] * 5 + [[]] * 2,
    working_days=[True] * 5 + [False] * 2,
    days_off={date(2026, 1, 7)},                   # Wednesday off
    start=date(2026, 1, 5),
    end=date(2026, 1, 11),
)
print("gen_shifts:", " ".join(f"({s.start:g}, {s.end:g})" for s in gen_shifts), file=sys.stderr)

b0 = Buffer("B0", valid_models=[model_a], buffer_type=BufferType.PASSAGE)
gen = PieceGenerator(models_goals={model_a: 40}, shifts=gen_shifts, outlets=[b0])

# an operator whose schedule is two back-to-back shift lists: merged on arrival
og1 = OperatorGroup("og1", capacity=1,
                    shifts=[Interval(360, 840)] + [Interval(840, 1320)],
                    productivity=Distribution(sim.Uniform, 0.9, 1.1))

exit_buffer = Buffer("EXIT", valid_models=[model_a], buffer_type=BufferType.EXIT)

protocols = Protocols(
    pending_carriers_pre_flexible_shutdowns=AbortPendingCarriers(),
    pending_carrier_pre_task_shift_end=AbortPendingCarriers(),
    operator_shift_constraint=NotConstrainedByShift(),
    task_shift_constraint=NotConstrainedByShift(),
    operators_self_conscious=Conscious(),
)
t1_config = PieceTaskConfig(
    task_shifts=[Interval(0, 9000)],
    startup_duration=Distribution(sim.Constant, 3),
    loading_duration=Distribution(sim.Constant, 1),
    startup_operators=Alternative(),
    loading_operators=Alternative(),
    operators=Alternative([(og1, 1)]),
    operator_scope=Scope.PER_BATCH,
    resource_scope=Scope.PER_BATCH,
    min_carriers=1,
    max_capacity=4,
    contiguous_carriers=False,
    independent_carriers=False,
    timeout=400,
    priority=5,
    protocols=protocols,
    models_configs={
        model_a: ModelConfig(duration=Distribution(sim.Uniform, 5, 8),
                             resources=[], min_carrier_capacity=1, max_carrier_capacity=2),
    },
    piece_collector_type=PieceCollectorType.NON_DISCRIMINATING_GREEDY,
)
t1 = PieceTask(config=t1_config, inlets=[b0], outlets=[exit_buffer])
# two shutdown windows touching at 560: must behave as one 500-620 window
NonFlexibleShutdowns(task=t1, intervals=[Interval(500, 560), Interval(560, 620)])

criterion = ByTime(time=9000)
SimulationStopper(criterion=criterion)

env.run(till=100000)

print("=== FINAL STATE ===", file=sys.stderr)
print(f"now={env.now():.6f}", file=sys.stderr)
print("gen_shifts_merged=[" + ", ".join(f"({s.start:g}, {s.end:g})" for s in gen.shifts) + "]", file=sys.stderr)
print("og1_shifts_merged=[" + ", ".join(f"({s.start:g}, {s.end:g})" for s in og1.shifts) + "]", file=sys.stderr)
print("shutdowns_merged=[" + ", ".join(f"({i.start:g}, {i.end:g})" for i in t1.non_flexible_shutdowns.intervals) + "]", file=sys.stderr)
print(f"generated=[{gen.generated[0]}]", file=sys.stderr)
for name, buf in (("B0", b0), ("EXIT", exit_buffer)):
    contents = [(p.id, p.model.name) for p in buf]
    print(f"{name} len={len(buf)} {contents}", file=sys.stderr)
import random
import numpy as np
s = [random.random() for _ in range(3)]
print(f"salabim_stream_next=[{s[0]:.12f}, {s[1]:.12f}, {s[2]:.12f}]", file=sys.stderr)
n = [float(np.random.random_sample()) for _ in range(3)]
print(f"np_stream_next=[{n[0]:.12f}, {n[1]:.12f}, {n[2]:.12f}]", file=sys.stderr)
