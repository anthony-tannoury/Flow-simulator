# Scenario 2 (Python side) — full factory. Companion of scenario2.cpp: same
# scenario, same seed; behaviour matches, individual draws do not.
# Covers: model hierarchy, altruistic collectors, ResourceTask (greedy),
# RestockableResource, lifespan/ExpiryManager, breakdowns (FailureRate/Bathtub
# + Exponential), both shutdown kinds, operator alternatives, PER_TASK &
# PER_UNIT scopes, time-function params (Linear), Router with callable prob,
# ByPiecesProduced stopper.
import sys

import salabim as sim
from simulation import env
from simulation.interval import Interval
from simulation.piece import Model, PieceGenerator
from simulation.outlet import Buffer, BufferType, Router
from simulation.sampler import Distribution, FailureRate
from simulation.function_generator import Linear, Bathtub
from simulation.resource import Resource, RestockableResource
from simulation.operator import OperatorGroup, Alternative
from simulation.protocols import (AbortPendingCarriers, AbortOrWaitForCarriers,
                                  NotConstrainedByShift, PartiallyConstrainedByShift,
                                  Conscious, Unconscious)
from simulation.task import Protocols, Scope
from simulation.piece_task import (PieceTaskConfig, ModelConfig, PieceTask,
                                   PieceCollectorType)
from simulation.resource_task import (ResourceTaskConfig, ResourceTask,
                                      ResourceCollectorType)
from simulation.interrupters import Breakdown, NonFlexibleShutdowns, FlexibleShutdowns
from simulation.judgement_day import ByPiecesProduced, SimulationStopper

env.trace(False)

# --- models: hierarchy -------------------------------------------------------
model_p = Model("P")
model_p1 = Model("P1")
model_p2 = Model("P2")
model_p1.set_parent(model_p)
model_p2.set_parent(model_p)

# --- generator ---------------------------------------------------------------
b0 = Buffer("B0", valid_models=[model_p], buffer_type=BufferType.PASSAGE)
gen = PieceGenerator(models_goals={model_p1: 40, model_p2: 30},
                     shifts=[Interval(0, 1400)], outlets=[b0])

# --- resources ---------------------------------------------------------------
steel = RestockableResource("steel", capacity=300,
                            order_duration=Distribution(sim.Constant, 5),
                            delivery_duration=Distribution(sim.Constant, 30),
                            threshold=100)
lube = Resource("lube", capacity=200, lifespan=500)
power = RestockableResource("power", capacity=50,
                            order_duration=Distribution(sim.Constant, 3),
                            delivery_duration=Distribution(sim.Constant, 10),
                            threshold=15)
raw_a = Resource("raw_a", capacity=400)
raw_b = Resource("raw_b", capacity=300)
mix = Resource("mix", capacity=120, initial_capacity=40)

# --- operators ---------------------------------------------------------------
prod1 = Distribution(sim.Uniform, 0.85, 1.15)
prod2 = Distribution(sim.Uniform, 0.9, 1.1)
og1 = OperatorGroup("og1", capacity=2, shifts=[Interval(0, 2400)], productivity=prod1)
og2 = OperatorGroup("og2", capacity=3, shifts=[Interval(0, 2400)], productivity=prod2)
og3 = OperatorGroup("og3", capacity=1, shifts=[Interval(0, 2400)], productivity=prod2)

# --- buffers / router --------------------------------------------------------
b1 = Buffer("B1", valid_models=[model_p], buffer_type=BufferType.PASSAGE)
exit_buffer = Buffer("EXIT", valid_models=[model_p], buffer_type=BufferType.EXIT)
scrap = Buffer("SCRAP", valid_models=[model_p], buffer_type=BufferType.SCRAP,
               piece_generator=gen)
router = Router({b1: Linear.generate(0, 0.9, 2000, 0.8), scrap: None})

# --- T1: discriminating altruistic piece task with resources + operators -----
t1_protocols = Protocols(
    pending_carriers_pre_flexible_shutdowns=AbortOrWaitForCarriers(0.5),
    pending_carrier_pre_task_shift_end=AbortPendingCarriers(),
    operator_shift_constraint=NotConstrainedByShift(),
    task_shift_constraint=PartiallyConstrainedByShift(tolerance=30),
    operators_self_conscious=Conscious(),
)
t1_config = PieceTaskConfig(
    task_shifts=[Interval(0, 1900)],
    startup_duration=Distribution(sim.Constant, 5),
    loading_duration=Distribution(sim.Constant, 2),
    startup_operators=Alternative([(og1, 1)]),
    loading_operators=Alternative([(og2, 1)]),
    operators=Alternative([(og1, 1)], [(og2, 2)]),
    operator_scope=Scope.PER_BATCH,
    resource_scope=Scope.PER_BATCH,
    min_carriers=2,
    max_capacity=6,
    contiguous_carriers=False,
    independent_carriers=False,
    timeout=300,
    priority=6,
    protocols=t1_protocols,
    models_configs={
        model_p: ModelConfig(duration=Distribution(sim.Uniform, 6, 9),
                             resources=[(steel, 2.0), (lube, 1.0)],
                             min_carrier_capacity=2, max_carrier_capacity=3),
    },
    piece_collector_type=PieceCollectorType.DISCRIMINATING_ALTRUISTIC,
)
t1 = PieceTask(config=t1_config, inlets=[b0], outlets=[router])
NonFlexibleShutdowns(task=t1, intervals=[Interval(700, 760)])
FlexibleShutdowns(task=t1, intervals=[Interval(1200, 1260)])
Breakdown(task=t1,
          mtbf=FailureRate(Bathtub.generate(a=1e-4, tau=2000, c=2e-3, beta=2, eta=500),
                           tolerance=30),
          mttr=Distribution(sim.Uniform, 15, 25),
          outlets=[b0])

# --- RT: greedy resource task producing 'mix', PER_TASK operators ------------
rt_protocols = Protocols(
    pending_carriers_pre_flexible_shutdowns=AbortPendingCarriers(),
    pending_carrier_pre_task_shift_end=AbortPendingCarriers(),
    operator_shift_constraint=NotConstrainedByShift(),
    task_shift_constraint=NotConstrainedByShift(),
    operators_self_conscious=Unconscious(),
)
rt_config = ResourceTaskConfig(
    task_shifts=[Interval(0, 2400)],
    startup_duration=Distribution(sim.Constant, 2),
    loading_duration=Distribution(sim.Constant, 1),
    startup_operators=Alternative(),
    loading_operators=Alternative(),
    operators=Alternative([(og3, 1)]),
    operator_scope=Scope.PER_TASK,
    resource_scope=Scope.PER_BATCH,
    min_carriers=1,
    max_capacity=10,
    contiguous_carriers=False,
    independent_carriers=False,
    timeout=250,
    priority=4,
    protocols=rt_protocols,
    non_transformed_resources=[(power, 1.0)],
    transformed_resources_salvageable=[(raw_a, 0.6, True), (raw_b, 0.4, False)],
    resources_out_distr=[(mix, sim.Bounded(sim.Normal(0.9, 0.05), 0, 2))],
    duration=Distribution(sim.Uniform, 15, 20),
    resource_collector_type=ResourceCollectorType.GREEDY,
    min_carrier_capacity=5.0,
    max_carrier_capacity=8.0,
)
rt = ResourceTask(config=rt_config)
Breakdown(task=rt, mtbf=Distribution(sim.Exponential, 400),
          mttr=Distribution(sim.Constant, 12))

# --- T2: non-discriminating altruistic, consumes 'mix' PER_UNIT --------------
t2_protocols = Protocols(
    pending_carriers_pre_flexible_shutdowns=AbortPendingCarriers(),
    pending_carrier_pre_task_shift_end=AbortPendingCarriers(),
    operator_shift_constraint=NotConstrainedByShift(),
    task_shift_constraint=NotConstrainedByShift(),
    operators_self_conscious=Conscious(),
)
t2_shared_duration = Distribution(sim.Uniform, 4, 6)
t2_config = PieceTaskConfig(
    task_shifts=[Interval(0, 2400)],
    startup_duration=Distribution(sim.Constant, 1),
    loading_duration=Distribution(sim.Constant, Linear.generate(0, 1.0, 2000, 2.0)),
    startup_operators=Alternative(),
    loading_operators=Alternative(),
    operators=Alternative(),
    operator_scope=Scope.PER_BATCH,
    resource_scope=Scope.PER_UNIT,
    min_carriers=1,
    max_capacity=8,
    contiguous_carriers=True,
    independent_carriers=True,
    timeout=150,
    priority=5,
    protocols=t2_protocols,
    models_configs={
        model_p: ModelConfig(duration=t2_shared_duration,
                             resources=[(mix, 1.5)],
                             min_carrier_capacity=1, max_carrier_capacity=4),
    },
    piece_collector_type=PieceCollectorType.NON_DISCRIMINATING_ALTRUISTIC,
)
t2 = PieceTask(config=t2_config, inlets=[b1], outlets=[exit_buffer])

criterion = ByPiecesProduced(total=55, exit_buffer=exit_buffer, timeout=3000)
SimulationStopper(criterion=criterion)

env.run(till=100000)

print("=== FINAL STATE ===", file=sys.stderr)
print(f"now={env.now():.6f}", file=sys.stderr)
print(f"generated={gen.generated}", file=sys.stderr)
for name, buf in (("B0", b0), ("B1", b1), ("EXIT", exit_buffer), ("SCRAP", scrap)):
    contents = [(p.id, p.model.name) for p in buf]
    print(f"{name} len={len(buf)} {contents}", file=sys.stderr)
for name, res in (("steel", steel), ("lube", lube), ("power", power),
                  ("raw_a", raw_a), ("raw_b", raw_b), ("mix", mix)):
    print(f"{name} avail={res.available_quantity():.9f} claimed={res.claimed_quantity():.9f}",
          file=sys.stderr)
