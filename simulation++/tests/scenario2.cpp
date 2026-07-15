// Scenario 2 (C++ side) — full factory. Companion of scenario2.py: same
// scenario, same seed; behaviour matches, individual draws do not.
#include "simulation.hpp"

#include <cstdio>

using namespace simulation;

int main() {
    auto& e = init(0, false);
    e.trace(false);

    // --- models: hierarchy ---------------------------------------------------
    auto* model_p = new Model("P");
    auto* model_p1 = new Model("P1");
    auto* model_p2 = new Model("P2");
    model_p1->set_parent(model_p);
    model_p2->set_parent(model_p);

    // --- generator -----------------------------------------------------------
    auto* b0 = new Buffer("B0", {model_p}, BufferType::PASSAGE);
    auto* gen = sim::make<PieceGenerator>({}, std::vector<std::pair<Model*, int>>{{model_p1, 40}, {model_p2, 30}},
                                          Intervals{interval(0, 1400)}, std::vector<Outlet*>{b0});

    // --- resources -----------------------------------------------------------
    auto* steel = new RestockableResource("steel", 300,
                                          distribution(DistType::Constant, {5}),
                                          distribution(DistType::Constant, {30}), 100);
    auto* lube = new Resource("lube", 200, -1, 500);
    auto* power = new RestockableResource("power", 50,
                                          distribution(DistType::Constant, {3}),
                                          distribution(DistType::Constant, {10}), 15);
    auto* raw_a = new Resource("raw_a", 400);
    auto* raw_b = new Resource("raw_b", 300);
    auto* mix = new Resource("mix", 120, 40);

    // --- operators -------------------------------------------------------------
    SamplerPtr prod1 = distribution(DistType::Uniform, {0.85, 1.15});
    SamplerPtr prod2 = distribution(DistType::Uniform, {0.9, 1.1});
    auto* og1 = new OperatorGroup("og1", 2, Intervals{interval(0, 2400)}, prod1);
    auto* og2 = new OperatorGroup("og2", 3, Intervals{interval(0, 2400)}, prod2);
    auto* og3 = new OperatorGroup("og3", 1, Intervals{interval(0, 2400)}, prod2);

    // --- buffers / router ------------------------------------------------------
    auto* b1 = new Buffer("B1", {model_p}, BufferType::PASSAGE);
    auto* exit_buffer = new Buffer("EXIT", {model_p}, BufferType::EXIT);
    auto* scrap = new Buffer("SCRAP", {model_p}, BufferType::SCRAP, gen);
    auto* router = new Router({{b1, Router::Prob(Param(Linear::generate(0, 0.9, 2000, 0.8)))},
                               {scrap, std::nullopt}});

    // --- T1: discriminating altruistic piece task ------------------------------
    Protocols t1_protocols{
        .pending_carriers_pre_flexible_shutdowns = std::make_shared<AbortOrWaitForCarriers>(0.5),
        .pending_carrier_pre_task_shift_end = std::make_shared<AbortPendingCarriers>(),
        .operator_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .task_shift_constraint = std::make_shared<PartiallyConstrainedByShift>(30),
        .operators_self_conscious = std::make_shared<Conscious>(),
    };
    auto t1_config = std::make_shared<PieceTaskConfig>();
    t1_config->task_shifts = {interval(0, 1900)};
    t1_config->startup_duration = distribution(DistType::Constant, {5});
    t1_config->loading_duration = distribution(DistType::Constant, {2});
    t1_config->startup_operators = Alternative({{{og1, 1}}});
    t1_config->loading_operators = Alternative({{{og2, 1}}});
    t1_config->operators = Alternative({{{og1, 1}}, {{og2, 2}}});
    t1_config->operator_scope = Scope::PER_BATCH;
    t1_config->resource_scope = Scope::PER_BATCH;
    t1_config->min_carriers = 2;
    t1_config->max_capacity = 6;
    t1_config->contiguous_carriers = false;
    t1_config->independent_carriers = false;
    t1_config->timeout = 300;
    t1_config->priority = 6;
    t1_config->protocols = t1_protocols;
    t1_config->models_configs = {
        {model_p, ModelConfig{distribution(DistType::Uniform, {6, 9}),
                              {{steel, 2.0}, {lube, 1.0}}, 2, 3}},
    };
    t1_config->piece_collector_type = PieceCollectorType::DISCRIMINATING_ALTRUISTIC;
    auto* t1 = sim::make<PieceTask>({}, t1_config, std::vector<Buffer*>{b0}, std::vector<Outlet*>{router});
    sim::make<NonFlexibleShutdowns>({}, t1, Intervals{interval(700, 760)});
    sim::make<FlexibleShutdowns>({}, t1, Intervals{interval(1200, 1260)});
    sim::make<Breakdown>({}, t1,
                         std::make_shared<FailureRate>(Bathtub::generate(1e-4, 2000, 2e-3, 2, 500), 30),
                         distribution(DistType::Uniform, {15, 25}),
                         std::vector<Outlet*>{b0});

    // --- RT: greedy resource task producing 'mix', PER_TASK operators ----------
    Protocols rt_protocols{
        .pending_carriers_pre_flexible_shutdowns = std::make_shared<AbortPendingCarriers>(),
        .pending_carrier_pre_task_shift_end = std::make_shared<AbortPendingCarriers>(),
        .operator_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .task_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .operators_self_conscious = std::make_shared<Unconscious>(),
    };
    auto rt_config = std::make_shared<ResourceTaskConfig>();
    rt_config->task_shifts = {interval(0, 2400)};
    rt_config->startup_duration = distribution(DistType::Constant, {2});
    rt_config->loading_duration = distribution(DistType::Constant, {1});
    rt_config->startup_operators = Alternative();
    rt_config->loading_operators = Alternative();
    rt_config->operators = Alternative({{{og3, 1}}});
    rt_config->operator_scope = Scope::PER_TASK;
    rt_config->resource_scope = Scope::PER_BATCH;
    rt_config->min_carriers = 1;
    rt_config->max_capacity = 10;
    rt_config->contiguous_carriers = false;
    rt_config->independent_carriers = false;
    rt_config->timeout = 250;
    rt_config->priority = 4;
    rt_config->protocols = rt_protocols;
    rt_config->non_transformed_resources = {{power, 1.0}};
    rt_config->transformed_resources_salvageable = {{raw_a, 0.6, true}, {raw_b, 0.4, false}};
    rt_config->resources_out_distr = {
        {mix, Bounded{distribution(DistType::Normal, {0.9, 0.05}), 0, 2}}};
    rt_config->duration = distribution(DistType::Uniform, {15, 20});
    rt_config->resource_collector_type = ResourceCollectorType::GREEDY;
    rt_config->min_carrier_capacity = 5.0;
    rt_config->max_carrier_capacity = 8.0;
    auto* rt = sim::make<ResourceTask>({}, rt_config);
    sim::make<Breakdown>({}, rt, distribution(DistType::Exponential, {400}),
                         distribution(DistType::Constant, {12}), std::vector<Outlet*>{});

    // --- T2: non-discriminating altruistic, consumes 'mix' PER_UNIT ------------
    Protocols t2_protocols{
        .pending_carriers_pre_flexible_shutdowns = std::make_shared<AbortPendingCarriers>(),
        .pending_carrier_pre_task_shift_end = std::make_shared<AbortPendingCarriers>(),
        .operator_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .task_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .operators_self_conscious = std::make_shared<Conscious>(),
    };
    SamplerPtr t2_shared_duration = distribution(DistType::Uniform, {4, 6});
    auto t2_config = std::make_shared<PieceTaskConfig>();
    t2_config->task_shifts = {interval(0, 2400)};
    t2_config->startup_duration = distribution(DistType::Constant, {1});
    t2_config->loading_duration = distribution(
        DistType::Constant, std::vector<Param>{Param(Linear::generate(0, 1.0, 2000, 2.0))});
    t2_config->startup_operators = Alternative();
    t2_config->loading_operators = Alternative();
    t2_config->operators = Alternative();
    t2_config->operator_scope = Scope::PER_BATCH;
    t2_config->resource_scope = Scope::PER_UNIT;
    t2_config->min_carriers = 1;
    t2_config->max_capacity = 8;
    t2_config->contiguous_carriers = true;
    t2_config->independent_carriers = true;
    t2_config->timeout = 150;
    t2_config->priority = 5;
    t2_config->protocols = t2_protocols;
    t2_config->models_configs = {
        {model_p, ModelConfig{t2_shared_duration, {{mix, 1.5}}, 1, 4}},
    };
    t2_config->piece_collector_type = PieceCollectorType::NON_DISCRIMINATING_ALTRUISTIC;
    sim::make<PieceTask>({}, t2_config, std::vector<Buffer*>{b1}, std::vector<Outlet*>{exit_buffer});

    auto* criterion = sim::make<ByPiecesProduced>({}, 55, exit_buffer, 3000.0);
    sim::make<SimulationStopper>({}, criterion);

    e.run(sim::RunOpts{.till = 100000});

    std::fprintf(stderr, "=== FINAL STATE ===\n");
    std::fprintf(stderr, "now=%.6f\n", e.now());
    std::fprintf(stderr, "generated=[%d, %d]\n", gen->generated[0], gen->generated[1]);
    struct NB { const char* n; Buffer* b; } bufs[] = {{"B0", b0}, {"B1", b1}, {"EXIT", exit_buffer}, {"SCRAP", scrap}};
    for (auto [n, b] : bufs) {
        std::fprintf(stderr, "%s len=%zu [", n, (size_t)b->size());
        bool first = true;
        for (auto* c : *static_cast<sim::Queue*>(b)) {
            auto* p = dynamic_cast<Piece*>(c);
            std::fprintf(stderr, "%s('%s', '%s')", first ? "" : ", ", p->id.c_str(), p->model->name.c_str());
            first = false;
        }
        std::fprintf(stderr, "]\n");
    }
    struct NR { const char* n; Resource* r; } ress[] = {{"steel", steel}, {"lube", lube}, {"power", power},
                                                       {"raw_a", raw_a}, {"raw_b", raw_b}, {"mix", mix}};
    for (auto [n, r] : ress)
        std::fprintf(stderr, "%s avail=%.9f claimed=%.9f\n", n, r->available_quantity(), r->claimed_quantity());
    return 0;
}
