// Benchmark (C++ side) — scenario1 scaled to 20,000 pieces, trace off.
// Companion of bench.py: same scenario, same seed; time both to compare.
#include "simulation.hpp"

#include <cstdio>

using namespace simulation;

int main() {
    auto& e = init(0, false);
    e.trace(false);

    auto* model_a = new Model("A");
    auto* model_b = new Model("B");

    Intervals gen_shifts{interval(0, 480000), interval(600000, 1080000)};
    auto* b0 = new Buffer("B0", {model_a, model_b}, BufferType::PASSAGE);
    auto* gen = sim::make<GoalPieceGenerator>({}, std::vector<std::pair<Model*, int>>{{model_a, 12000}, {model_b, 8000}},
                                          gen_shifts, std::vector<Outlet*>{b0});

    auto* og1 = new OperatorGroup("og1", 2, Intervals{interval(0, 1500000)},
                                  distribution(DistType::Uniform, {0.8, 1.2}));

    Protocols protocols{
        .pending_carriers_pre_flexible_shutdowns = std::make_shared<AbortPendingCarriers>(),
        .pending_carrier_pre_task_shift_end = std::make_shared<AbortPendingCarriers>(),
        .operator_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .task_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .operators_self_conscious = std::make_shared<Conscious>(),
    };

    auto* b1 = new Buffer("B1", {model_a, model_b}, BufferType::PASSAGE);
    auto* exit_buffer = new Buffer("EXIT", {model_a, model_b}, BufferType::EXIT);
    auto* scrap = new Buffer("SCRAP", {model_a, model_b}, BufferType::SCRAP, gen);
    auto* router = new Router({{b1, Router::Prob(0.9)}, {scrap, std::nullopt}});

    auto t1_config = std::make_shared<PieceTaskConfig>();
    t1_config->task_shifts = {interval(0, 1500000)};
    t1_config->startup_duration = distribution(DistType::Constant, {5});
    t1_config->loading_duration = distribution(DistType::Constant, {2});
    t1_config->startup_operators = Alternative();
    t1_config->loading_operators = Alternative();
    t1_config->operators = Alternative({{{og1, 1}}});
    t1_config->operator_scope = Scope::PER_BATCH;
    t1_config->resource_scope = Scope::PER_BATCH;
    t1_config->min_carriers = 1;
    t1_config->max_capacity = 4;
    t1_config->contiguous_carriers = false;
    t1_config->independent_carriers = false;
    t1_config->timeout = 300000;
    t1_config->priority = 5;
    t1_config->protocols = protocols;
    t1_config->models_configs = {
        {model_a, ModelConfig{distribution(DistType::Uniform, {8, 12}), {}, 2, 4}},
        {model_b, ModelConfig{distribution(DistType::Uniform, {6, 9}), {}, 2, 4}},
    };
    t1_config->piece_collector_type = PieceCollectorType::DISCRIMINATING_GREEDY;
    sim::make<PieceTask>({}, t1_config, std::vector<Buffer*>{b0}, std::vector<Outlet*>{router});

    SamplerPtr t2_duration = distribution(DistType::Uniform, {3, 5});
    auto t2_config = std::make_shared<PieceTaskConfig>();
    t2_config->task_shifts = {interval(0, 1500000)};
    t2_config->startup_duration = distribution(DistType::Constant, {1});
    t2_config->loading_duration = distribution(DistType::Constant, {1});
    t2_config->startup_operators = Alternative();
    t2_config->loading_operators = Alternative();
    t2_config->operators = Alternative();
    t2_config->operator_scope = Scope::PER_BATCH;
    t2_config->resource_scope = Scope::PER_BATCH;
    t2_config->min_carriers = 1;
    t2_config->max_capacity = 6;
    t2_config->contiguous_carriers = false;
    t2_config->independent_carriers = true;
    t2_config->timeout = 200000;
    t2_config->priority = 5;
    t2_config->protocols = protocols;
    t2_config->models_configs = {
        {model_a, ModelConfig{t2_duration, {}, 1, 3}},
        {model_b, ModelConfig{t2_duration, {}, 1, 3}},
    };
    t2_config->piece_collector_type = PieceCollectorType::NON_DISCRIMINATING_GREEDY;
    sim::make<PieceTask>({}, t2_config, std::vector<Buffer*>{b1}, std::vector<Outlet*>{exit_buffer});

    auto* criterion = sim::make<ByTime>({}, 1500000.0);
    sim::make<SimulationStopper>({}, criterion);

    e.run(sim::RunOpts{.till = 100000000});

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
    return 0;
}
