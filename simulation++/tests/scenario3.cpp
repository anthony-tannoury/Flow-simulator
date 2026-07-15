// Twin scenario 3 (C++ side) — midnight-crossing weekly shifts + touching-
// interval merging. Must match scenario3.py.
#include "simulation.hpp"

#include <chrono>
#include <cstdio>

using namespace simulation;

int main() {
    auto& e = init(0, false);
    e.trace(true);

    auto* model_a = new Model("A");

    using days_t = ShiftManager::days_t;
    auto day = [](int y, unsigned m, unsigned d) {
        return days_t(std::chrono::year(y) / m / d);
    };
    ShiftManager::DateTime sim_start{day(2026, 1, 5), 0, 0};  // a Monday, 00:00

    std::vector<std::pair<double, double>> night{{0.0, 360.0}, {1320.0, 1440.0}};
    std::vector<std::vector<std::pair<double, double>>> shifts_per_day{
        night, night, night, night, night, {}, {}};
    std::set<long long> days_off{day(2026, 1, 7).time_since_epoch().count()};  // Wednesday off
    Intervals gen_shifts = ShiftManager::generate_weekly_shifts(
        sim_start, shifts_per_day, {true, true, true, true, true, false, false},
        days_off, day(2026, 1, 5), day(2026, 1, 11));
    std::fprintf(stderr, "gen_shifts:");
    for (const auto& s : gen_shifts) std::fprintf(stderr, " (%g, %g)", s->start, s->end);
    std::fprintf(stderr, "\n");

    auto* b0 = new Buffer("B0", {model_a}, BufferType::PASSAGE);
    auto* gen = sim::make<PieceGenerator>({}, std::vector<std::pair<Model*, int>>{{model_a, 40}},
                                          gen_shifts, std::vector<Outlet*>{b0});

    auto* og1 = new OperatorGroup("og1", 1, Intervals{interval(360, 840), interval(840, 1320)},
                                  distribution(DistType::Uniform, {0.9, 1.1}));

    auto* exit_buffer = new Buffer("EXIT", {model_a}, BufferType::EXIT);

    Protocols protocols{
        .pending_carriers_pre_flexible_shutdowns = std::make_shared<AbortPendingCarriers>(),
        .pending_carrier_pre_task_shift_end = std::make_shared<AbortPendingCarriers>(),
        .operator_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .task_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .operators_self_conscious = std::make_shared<Conscious>(),
    };
    auto t1_config = std::make_shared<PieceTaskConfig>();
    t1_config->task_shifts = {interval(0, 9000)};
    t1_config->startup_duration = distribution(DistType::Constant, {3});
    t1_config->loading_duration = distribution(DistType::Constant, {1});
    t1_config->startup_operators = Alternative();
    t1_config->loading_operators = Alternative();
    t1_config->operators = Alternative({{{og1, 1}}});
    t1_config->operator_scope = Scope::PER_BATCH;
    t1_config->resource_scope = Scope::PER_BATCH;
    t1_config->min_carriers = 1;
    t1_config->max_capacity = 4;
    t1_config->contiguous_carriers = false;
    t1_config->independent_carriers = false;
    t1_config->timeout = 400;
    t1_config->priority = 5;
    t1_config->protocols = protocols;
    t1_config->models_configs = {
        {model_a, ModelConfig{distribution(DistType::Uniform, {5, 8}), {}, 1, 2}},
    };
    t1_config->piece_collector_type = PieceCollectorType::NON_DISCRIMINATING_GREEDY;
    auto* t1 = sim::make<PieceTask>({}, t1_config, std::vector<Buffer*>{b0},
                                    std::vector<Outlet*>{exit_buffer});
    sim::make<NonFlexibleShutdowns>({}, t1, Intervals{interval(500, 560), interval(560, 620)});

    auto* criterion = sim::make<ByTime>({}, 9000.0);
    sim::make<SimulationStopper>({}, criterion);

    e.run(sim::RunOpts{.till = 100000});

    std::fprintf(stderr, "=== FINAL STATE ===\n");
    std::fprintf(stderr, "now=%.6f\n", e.now());
    std::fprintf(stderr, "gen_shifts_merged=[");
    for (size_t i = 0; i < gen->shifts.size(); ++i)
        std::fprintf(stderr, "%s(%g, %g)", i ? ", " : "", gen->shifts[i]->start, gen->shifts[i]->end);
    std::fprintf(stderr, "]\n");
    std::fprintf(stderr, "og1_shifts_merged=[");
    for (size_t i = 0; i < og1->shifts.size(); ++i)
        std::fprintf(stderr, "%s(%g, %g)", i ? ", " : "", og1->shifts[i]->start, og1->shifts[i]->end);
    std::fprintf(stderr, "]\n");
    std::fprintf(stderr, "shutdowns_merged=[");
    const auto& sdiv = t1->non_flexible_shutdowns->intervals;
    for (size_t i = 0; i < sdiv.size(); ++i)
        std::fprintf(stderr, "%s(%g, %g)", i ? ", " : "", sdiv[i]->start, sdiv[i]->end);
    std::fprintf(stderr, "]\n");
    std::fprintf(stderr, "generated=[%d]\n", gen->generated[0]);
    struct NB { const char* n; Buffer* b; } bufs[] = {{"B0", b0}, {"EXIT", exit_buffer}};
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
    double s1 = sim::random_stream().random(), s2 = sim::random_stream().random(),
           s3 = sim::random_stream().random();
    std::fprintf(stderr, "salabim_stream_next=[%.12f, %.12f, %.12f]\n", s1, s2, s3);
    double n1 = np_random.random_sample(), n2 = np_random.random_sample(),
           n3 = np_random.random_sample();
    std::fprintf(stderr, "np_stream_next=[%.12f, %.12f, %.12f]\n", n1, n2, n3);
    return 0;
}
