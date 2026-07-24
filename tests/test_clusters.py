import collections

import pytest


def _build_chain(assoc_collector_name, mid_collector_name):
    import salabim as sim
    from simulation import env
    from simulation.interval import Interval
    from simulation.piece import Model, GoalPieceGenerator
    from simulation.outlet import Buffer, BufferType
    from simulation.sampler import Distribution
    from simulation.operator import Alternative
    from simulation.protocols import (AbortPendingCarriers, NotConstrainedByShift,
                                       Conscious, FirstInFirstOut, MostPresent)
    from simulation.task import Scope
    from simulation.piece_task import (PieceTaskConfig, ModelConfig, PieceTask,
                                       PieceCollectorType, PieceProtocols, AssociationType)
    from simulation.judgement_day import ByTime, SimulationStopper

    env.trace(False)
    assoc_collector = getattr(PieceCollectorType, assoc_collector_name)
    mid_collector = getattr(PieceCollectorType, mid_collector_name)

    m1, m2 = Model("M1"), Model("M2")
    shifts = [Interval(0, 1_000_000)]

    b0 = Buffer("B0", valid_models=[m1, m2], buffer_type=BufferType.PASSAGE)
    b1 = Buffer("B1", valid_models=[m1, m2], buffer_type=BufferType.PASSAGE)
    b2 = Buffer("B2", valid_models=[m1, m2], buffer_type=BufferType.PASSAGE)
    exit_buffer = Buffer("EXIT", valid_models=[m1, m2], buffer_type=BufferType.EXIT)

    goals = {m1: 30, m2: 20}
    GoalPieceGenerator(models_goals=goals, shifts=shifts, outlets=[b0], gap=1.0)

    protocols = PieceProtocols(AbortPendingCarriers(), AbortPendingCarriers(),
                               NotConstrainedByShift(), NotConstrainedByShift(),
                               Conscious(), FirstInFirstOut(), MostPresent())
    duration = Distribution(sim.Constant, 5)

    def cfg(assoc, collector, min_cc, max_cc, max_cap):
        return PieceTaskConfig(
            task_shifts=shifts, startup_duration=Distribution(sim.Constant, 0),
            loading_duration=Distribution(sim.Constant, 1),
            startup_operators=Alternative(), loading_operators=Alternative(),
            operators=Alternative(), operator_scope=Scope.PER_BATCH,
            resource_scope=Scope.PER_BATCH, min_carriers=1, max_capacity=max_cap,
            contiguous_carriers=False, independent_carriers=True, timeout=50,
            priority=5, admin=False, protocols=protocols,
            models_configs={
                m1: ModelConfig(duration=duration, resources=[], min_carrier_capacity=min_cc, max_carrier_capacity=max_cc),
                m2: ModelConfig(duration=duration, resources=[], min_carrier_capacity=min_cc, max_carrier_capacity=max_cc),
            },
            piece_collector_type=collector, association_type=assoc)

    t_assoc = PieceTask(name="ASSOC", config=cfg(AssociationType.ASSOCIATIVE, assoc_collector, 2, 4, 8),
                        inlets=[b0], outlets=[b1])
    t_mid = PieceTask(name="MID", config=cfg(AssociationType.PASSIVE, mid_collector, 1, 4, 8),
                      inlets=[b1], outlets=[b2])
    t_dis = PieceTask(name="DIS", config=cfg(AssociationType.DISSOCIATIVE, mid_collector, 1, 4, 8),
                      inlets=[b2], outlets=[exit_buffer])

    SimulationStopper(criterion=ByTime(time=5000))
    env.run(till=10_000_000)

    from simulation import kpis
    return {
        "total": sum(goals.values()),
        "exits": collections.Counter(p.model.name for p in exit_buffer),
        "leftovers": {b.name(): len(b) for b in (b0, b1, b2) if len(b)},
        "related_at_exit": [p.id for p in exit_buffer if p.has_family],
        "wip": kpis.WIP(),
        "assoc_deposited": {k.name: v for k, v in t_assoc.deposited.items()},
        "dis_deposited_sum": sum(t_dis.deposited.values()),
        "assoc_batch_max": t_assoc.batch_sizes.maximum(),
    }


@pytest.mark.parametrize("assoc_collector,mid_collector", [
    ("NON_DISCRIMINATING_GREEDY", "NON_DISCRIMINATING_GREEDY"),
    ("DISCRIMINATING_GREEDY", "NON_DISCRIMINATING_GREEDY"),
    ("NON_DISCRIMINATING_ALTRUISTIC", "NON_DISCRIMINATING_ALTRUISTIC"),
    ("DISCRIMINATING_ALTRUISTIC", "NON_DISCRIMINATING_ALTRUISTIC"),
])
def test_associate_passthrough_dissociate(fresh_sim, assoc_collector, mid_collector):
    r = _build_chain(assoc_collector, mid_collector)

    # every generated pattern reaches the exit, none stuck mid-line
    assert r["exits"]["M1"] == 30
    assert r["exits"]["M2"] == 20
    assert not r["leftovers"]
    # dissociation restored every pattern to a standalone piece
    assert not r["related_at_exit"]
    # no work-in-progress leak once the line drains
    assert r["wip"] == 0
    # a cluster never exceeds the carrier capacity it travels through
    assert r["assoc_batch_max"] <= 4
    # each pattern is counted once, under its own model (no N*N over-count)
    assert r["assoc_deposited"] == {"M1": 30, "M2": 20}
    assert r["dis_deposited_sum"] == r["total"]


def _run_guard(mid_collector_name, mid_max_carrier_capacity, mid_station_capacity=8):
    import salabim as sim
    from simulation import env
    from simulation.interval import Interval
    from simulation.piece import Model, GoalPieceGenerator
    from simulation.outlet import Buffer, BufferType
    from simulation.sampler import Distribution
    from simulation.operator import Alternative
    from simulation.protocols import (AbortPendingCarriers, NotConstrainedByShift,
                                       Conscious, FirstInFirstOut, MostPresent)
    from simulation.task import Scope
    from simulation.piece_task import (PieceTaskConfig, ModelConfig, PieceTask,
                                       PieceCollectorType, PieceProtocols, AssociationType)
    from simulation.judgement_day import ByTime, SimulationStopper

    env.trace(False)
    m1, m2 = Model("M1"), Model("M2")
    shifts = [Interval(0, 1_000_000)]
    b0 = Buffer("B0", valid_models=[m1, m2], buffer_type=BufferType.PASSAGE)
    b1 = Buffer("B1", valid_models=[m1, m2], buffer_type=BufferType.PASSAGE)
    exit_buffer = Buffer("EXIT", valid_models=[m1, m2], buffer_type=BufferType.EXIT)
    GoalPieceGenerator(models_goals={m1: 20, m2: 20}, shifts=shifts, outlets=[b0], gap=1.0)
    protocols = PieceProtocols(AbortPendingCarriers(), AbortPendingCarriers(),
                               NotConstrainedByShift(), NotConstrainedByShift(),
                               Conscious(), FirstInFirstOut(), MostPresent())
    duration = Distribution(sim.Constant, 5)

    def cfg(assoc, collector, min_cc, max_cc, station_cap=8):
        return PieceTaskConfig(
            task_shifts=shifts, startup_duration=Distribution(sim.Constant, 0),
            loading_duration=Distribution(sim.Constant, 1),
            startup_operators=Alternative(), loading_operators=Alternative(),
            operators=Alternative(), operator_scope=Scope.PER_BATCH,
            resource_scope=Scope.PER_BATCH, min_carriers=1, max_capacity=station_cap,
            contiguous_carriers=False, independent_carriers=True, timeout=50,
            priority=5, admin=False, protocols=protocols,
            models_configs={
                m1: ModelConfig(duration=duration, resources=[], min_carrier_capacity=min_cc, max_carrier_capacity=max_cc),
                m2: ModelConfig(duration=duration, resources=[], min_carrier_capacity=min_cc, max_carrier_capacity=max_cc),
            },
            piece_collector_type=collector, association_type=assoc)

    # ASSOC (non-disc) forms mixed clusters of exactly 4 patterns
    PieceTask(name="ASSOC", config=cfg(AssociationType.ASSOCIATIVE, PieceCollectorType.NON_DISCRIMINATING_GREEDY, 4, 4),
              inlets=[b0], outlets=[b1])
    PieceTask(name="MID", config=cfg(AssociationType.PASSIVE, getattr(PieceCollectorType, mid_collector_name), 1,
                                     mid_max_carrier_capacity, mid_station_capacity),
              inlets=[b1], outlets=[exit_buffer])
    SimulationStopper(criterion=ByTime(time=2000))
    env.run(till=10_000_000)


def test_cluster_over_capacity_raises(fresh_sim):
    # a weight-4 cluster reaching a task whose carrier cap is 3 is unsatisfiable
    with pytest.raises(ValueError, match="incoherent task configs"):
        _run_guard("NON_DISCRIMINATING_GREEDY", 3)


def test_cluster_over_station_capacity_raises(fresh_sim):
    # carrier cap fits the w4 cluster but the station only has 3 slots in total:
    # the sibling reservation could never be satisfied, so it must raise instead
    # of wedging the collector forever
    with pytest.raises(ValueError, match="incoherent task configs"):
        _run_guard("NON_DISCRIMINATING_GREEDY", 4, mid_station_capacity=3)


def test_mixed_cluster_into_discriminating_raises(fresh_sim):
    # a mixed-model cluster cannot be focused by a discriminating downstream task
    with pytest.raises(RuntimeError, match="cluster of different models"):
        _run_guard("DISCRIMINATING_GREEDY", 4)


def test_mixed_cluster_into_discriminating_altruistic_raises(fresh_sim):
    # the altruistic collection path must apply the same mixed-model guard
    with pytest.raises(RuntimeError, match="cluster of different models"):
        _run_guard("DISCRIMINATING_ALTRUISTIC", 4)


def test_no_slot_leak_when_sibling_reservation_times_out(fresh_sim):
    # MID (capacity 2, contiguous) first takes a lone M2 single into a 60 min
    # batch; the next collector claims the last free slot, then a w2 token
    # arrives and its sibling-slot request times out against the busy batch.
    # The put-back must release the claimed slot, otherwise the collector
    # starves itself in ensure_one and every token is stuck forever.
    import salabim as sim
    from simulation import env
    from simulation.interval import Interval
    from simulation.piece import Model, GoalPieceGenerator
    from simulation.outlet import Buffer, BufferType
    from simulation.sampler import Distribution
    from simulation.operator import Alternative
    from simulation.protocols import (AbortPendingCarriers, NotConstrainedByShift,
                                       Conscious, FirstInFirstOut, MostPresent)
    from simulation.task import Scope
    from simulation.piece_task import (PieceTaskConfig, ModelConfig, PieceTask,
                                       PieceCollectorType, PieceProtocols, AssociationType)
    from simulation.judgement_day import ByTime, SimulationStopper

    env.trace(False)
    m1, m2 = Model("M1"), Model("M2")
    shifts = [Interval(0, 1_000_000)]
    bA = Buffer("BA", valid_models=[m1], buffer_type=BufferType.PASSAGE)
    bB = Buffer("BB", valid_models=[m2], buffer_type=BufferType.PASSAGE)
    b1 = Buffer("B1", valid_models=[m1, m2], buffer_type=BufferType.PASSAGE)
    exit_buffer = Buffer("EXIT", valid_models=[m1, m2], buffer_type=BufferType.EXIT)
    GoalPieceGenerator(models_goals={m1: 4, m2: 1}, shifts=shifts, outlets=[bA, bB], gap=1.0)
    protocols = PieceProtocols(AbortPendingCarriers(), AbortPendingCarriers(),
                               NotConstrainedByShift(), NotConstrainedByShift(),
                               Conscious(), FirstInFirstOut(), MostPresent())

    def cfg(models, assoc, min_cc, max_cc, max_cap, timeout, dur, load, contiguous=False):
        duration = Distribution(sim.Constant, dur)
        return PieceTaskConfig(
            task_shifts=shifts, startup_duration=Distribution(sim.Constant, 0),
            loading_duration=Distribution(sim.Constant, load),
            startup_operators=Alternative(), loading_operators=Alternative(),
            operators=Alternative(), operator_scope=Scope.PER_BATCH,
            resource_scope=Scope.PER_BATCH, min_carriers=1, max_capacity=max_cap,
            contiguous_carriers=contiguous, independent_carriers=True, timeout=timeout,
            priority=5, admin=False, protocols=protocols,
            models_configs={m: ModelConfig(duration=duration, resources=[],
                                           min_carrier_capacity=min_cc, max_carrier_capacity=max_cc)
                            for m in models},
            piece_collector_type=PieceCollectorType.NON_DISCRIMINATING_GREEDY,
            association_type=assoc)

    PieceTask(name="ASSOC", config=cfg([m1], AssociationType.ASSOCIATIVE, 2, 2, 4, 20, 10, 1),
              inlets=[bA], outlets=[b1])
    PieceTask(name="PASS", config=cfg([m2], AssociationType.PASSIVE, 1, 1, 2, 20, 0.1, 0.1),
              inlets=[bB], outlets=[b1])
    PieceTask(name="MID", config=cfg([m1, m2], AssociationType.DISSOCIATIVE, 1, 2, 2, 9, 60, 1,
                                     contiguous=True),
              inlets=[b1], outlets=[exit_buffer])
    SimulationStopper(criterion=ByTime(time=400))
    env.run(till=10_000_000)

    exits = collections.Counter(p.model.name for p in exit_buffer)
    assert exits == {"M1": 4, "M2": 1}
    assert len(b1) == 0
    from simulation import kpis
    assert kpis.WIP() == 0
