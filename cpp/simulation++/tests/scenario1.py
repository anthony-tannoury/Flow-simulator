# Scenario 1 (Python side) — companion of scenario1.cpp: same scenario, same seed.
# Behaviour matches the C++ run; the individual draws (and so the exact
# counts) do not.
# Generator (2 models, shifts) -> B0 -> PieceTask T1 (discriminating greedy,
# operators w/ productivity, uniform durations) -> Router(90% B1 / 10% scrap)
# -> PieceTask T2 (non-discriminating greedy, no operators) -> exit.
import sys

import salabim as sim
from simulation import env
from simulation.interval import Interval
from simulation.piece import Model, PieceGenerator
from simulation.outlet import Buffer, BufferType, Router
from simulation.sampler import Distribution
from simulation.operator import OperatorGroup, Alternative
from simulation.protocols import (AbortPendingCarriers, NotConstrainedByShift,
                                  Conscious)
from simulation.task import Protocols, Scope
from simulation.piece_task import (PieceTaskConfig, ModelConfig, PieceTask,
                                   PieceCollectorType)
from simulation.judgement_day import ByTime, SimulationStopper

env.trace(False)

model_a = Model("A")
model_b = Model("B")

gen_shifts = [Interval(0, 480), Interval(600, 1080)]
b0 = Buffer("B0", valid_models=[model_a, model_b], buffer_type=BufferType.PASSAGE)
gen = PieceGenerator(models_goals={model_a: 30, model_b: 20},
                     shifts=gen_shifts, outlets=[b0])

og1 = OperatorGroup("og1", capacity=2,
                    shifts=[Interval(0, 1400)],
                    productivity=Distribution(sim.Uniform, 0.8, 1.2))

protocols = Protocols(
    pending_carriers_pre_flexible_shutdowns=AbortPendingCarriers(),
    pending_carrier_pre_task_shift_end=AbortPendingCarriers(),
    operator_shift_constraint=NotConstrainedByShift(),
    task_shift_constraint=NotConstrainedByShift(),
    operators_self_conscious=Conscious(),
)

b1 = Buffer("B1", valid_models=[model_a, model_b], buffer_type=BufferType.PASSAGE)
exit_buffer = Buffer("EXIT", valid_models=[model_a, model_b], buffer_type=BufferType.EXIT)
scrap = Buffer("SCRAP", valid_models=[model_a, model_b], buffer_type=BufferType.SCRAP,
               piece_generator=gen)
router = Router({b1: 0.9, scrap: None})

t1_config = PieceTaskConfig(
    task_shifts=[Interval(0, 1400)],
    startup_duration=Distribution(sim.Constant, 5),
    loading_duration=Distribution(sim.Constant, 2),
    startup_operators=Alternative(),
    loading_operators=Alternative(),
    operators=Alternative([(og1, 1)]),
    operator_scope=Scope.PER_BATCH,
    resource_scope=Scope.PER_BATCH,
    min_carriers=1,
    max_capacity=4,
    contiguous_carriers=False,
    independent_carriers=False,
    timeout=200,
    priority=5,
    protocols=protocols,
    models_configs={
        model_a: ModelConfig(duration=Distribution(sim.Uniform, 8, 12),
                             resources=[], min_carrier_capacity=2, max_carrier_capacity=4),
        model_b: ModelConfig(duration=Distribution(sim.Uniform, 6, 9),
                             resources=[], min_carrier_capacity=2, max_carrier_capacity=4),
    },
    piece_collector_type=PieceCollectorType.DISCRIMINATING_GREEDY,
)
t1 = PieceTask(config=t1_config, inlets=[b0], outlets=[router])

t2_duration = Distribution(sim.Uniform, 3, 5)
t2_config = PieceTaskConfig(
    task_shifts=[Interval(0, 1400)],
    startup_duration=Distribution(sim.Constant, 1),
    loading_duration=Distribution(sim.Constant, 1),
    startup_operators=Alternative(),
    loading_operators=Alternative(),
    operators=Alternative(),
    operator_scope=Scope.PER_BATCH,
    resource_scope=Scope.PER_BATCH,
    min_carriers=1,
    max_capacity=6,
    contiguous_carriers=False,
    independent_carriers=True,
    timeout=100,
    priority=5,
    protocols=protocols,
    models_configs={
        model_a: ModelConfig(duration=t2_duration, resources=[],
                             min_carrier_capacity=1, max_carrier_capacity=3),
        model_b: ModelConfig(duration=t2_duration, resources=[],
                             min_carrier_capacity=1, max_carrier_capacity=3),
    },
    piece_collector_type=PieceCollectorType.NON_DISCRIMINATING_GREEDY,
)
t2 = PieceTask(config=t2_config, inlets=[b1], outlets=[exit_buffer])

criterion = ByTime(time=1500)
SimulationStopper(criterion=criterion)

env.run(till=100000)

print("=== FINAL STATE ===", file=sys.stderr)
print(f"now={env.now():.6f}", file=sys.stderr)
print(f"generated={gen.generated}", file=sys.stderr)
for name, buf in (("B0", b0), ("B1", b1), ("EXIT", exit_buffer), ("SCRAP", scrap)):
    contents = [(p.id, p.model.name) for p in buf]
    print(f"{name} len={len(buf)} {contents}", file=sys.stderr)
