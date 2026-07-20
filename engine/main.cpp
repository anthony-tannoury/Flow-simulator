// Flow-simulator C++ engine ("flow_sim") — M1 harness.
//
// Drop-in alternative to flow_designer/sim_runner.py. Same contract:
//   * invoked as   flow_sim <flow.json>
//   * prints machine-readable progress to stdout, one tagged line at a time:
//        @@META {...}      once, after loading: criterion + totals
//        @@PROGRESS {...}  during the run: sim clock, wall time, pieces
//        @@DONE {...}      once, after the report is written: the run directory
//        @@ERROR {...}     on a fatal error, before exiting nonzero
//   * writes runs/<stamp>_<stem>/ with report.json and flow.json
//
// M1 status: the harness below (argument handling, the @@ protocol, the slicing
// loop, the run folder, JSON read/write) is real and stays. Building the actual
// simulation FROM the flow JSON is parser++ (next step); until then a small
// placeholder sim stands in so the whole pipe is exercised end to end. Every
// placeholder site is tagged `TODO(parser++)` / `TODO(kpis++)`.
//
// Build with Clang or MSVC — salabim.hpp uses C++20 coroutines that GCC<=13
// miscompiles (internal compiler error).

#include "simulation.hpp"

#include "json.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

using json = nlohmann::json;
using namespace simulation;
namespace fs = std::filesystem;

namespace {

void emit(const char* tag, const json& payload) {
    // std::endl flushes: the designer reads these lines live.
    std::cout << "@@" << tag << ' ' << payload.dump() << std::endl;
}

// --- placeholder simulation ------------------------------------------------
// TODO(parser++): replace with `build_from_json(flow)` — a generator, tasks,
// buffers, operators, routers, criterion built from the JSON. For now: one
// model, one instant task, a 50-piece goal, so the harness has something real
// to run and count.
struct Built {
    Buffer* exit_buffer;
};

Built build_placeholder() {
    auto* m = new Model("M");
    Intervals shifts{interval(0, 100000)};
    auto* in_buffer = new Buffer("in", {m}, BufferType::PASSAGE);
    auto* exit_buffer = new Buffer("out", {m}, BufferType::EXIT);
    sim::make<PieceGenerator>({}, std::vector<std::pair<Model*, int>>{{m, 50}},
                              shifts, std::vector<Outlet*>{in_buffer});

    Protocols protocols{
        .pending_carriers_pre_flexible_shutdowns = std::make_shared<AbortPendingCarriers>(),
        .pending_carrier_pre_task_shift_end = std::make_shared<AbortPendingCarriers>(),
        .operator_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .task_shift_constraint = std::make_shared<NotConstrainedByShift>(),
        .operators_self_conscious = std::make_shared<Conscious>(),
    };
    auto cfg = std::make_shared<PieceTaskConfig>();
    cfg->task_shifts = {interval(0, 100000)};
    cfg->startup_duration = distribution(DistType::Constant, {0});
    cfg->loading_duration = distribution(DistType::Constant, {0});
    cfg->startup_operators = Alternative();
    cfg->loading_operators = Alternative();
    cfg->operators = Alternative();
    cfg->operator_scope = Scope::PER_BATCH;
    cfg->resource_scope = Scope::PER_BATCH;
    cfg->min_carriers = 1;
    cfg->max_capacity = 1;
    cfg->contiguous_carriers = false;
    cfg->independent_carriers = false;
    cfg->timeout = 100;
    cfg->priority = 5;
    cfg->protocols = protocols;
    cfg->models_configs = {{m, ModelConfig{distribution(DistType::Constant, {5}), {}, 1, 1}}};
    cfg->piece_collector_type = PieceCollectorType::NON_DISCRIMINATING_GREEDY;
    sim::make<PieceTask>({}, cfg, std::vector<Buffer*>{in_buffer}, std::vector<Outlet*>{exit_buffer});

    return {exit_buffer};
}

std::string timestamp() {
    std::time_t now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d_%H%M%S", std::localtime(&now));
    return buf;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2) {
            emit("ERROR", {{"message", "usage: flow_sim <flow.json>"}});
            return 2;
        }
        fs::path json_path = fs::absolute(argv[1]);
        std::ifstream f(json_path);
        if (!f) {
            emit("ERROR", {{"message", "cannot open " + json_path.string()}});
            return 2;
        }
        json flow = json::parse(f);  // reads UTF-8; the mojibake bug can't recur here

        // TODO(parser++): consume `flow` fully. For now just prove it parsed.
        std::string start_date = flow.value("start_date", "");
        std::size_t node_count = flow.contains("nodes") ? flow["nodes"].size() : 0;

        auto& e = init(0, false);
        e.trace(false);
        Built built = build_placeholder();

        // TODO(parser++): derive TILL and the criterion from the JSON.
        const double TILL = 2880.0;
        const double SLICE = 240.0;
        emit("META", {{"engine", "cpp"},
                      {"criterion", "placeholder"},
                      {"nodes", node_count},
                      {"start_date", start_date},
                      {"total_time", TILL}});

        auto t0 = std::chrono::steady_clock::now();
        for (double target = SLICE;; target += SLICE) {
            e.run(sim::RunOpts{.till = std::min(target, TILL)});
            double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
            emit("PROGRESS", {{"sim_now", e.now()},
                              {"elapsed", elapsed},
                              {"pieces", static_cast<int>(built.exit_buffer->size())}});
            if (e.now() >= TILL || std::isinf(e.peek())) break;
        }

        fs::path out_dir = fs::current_path() / "runs" / (timestamp() + "_" + json_path.stem().string());
        fs::create_directories(out_dir);

        // TODO(kpis++): the full report — postes/buffers/flux/operateurs CSVs and
        // the rich report.json. For now a minimal, well-formed report.json so the
        // designer's results mode has the run block to read.
        json report;
        report["format"] = "flow-simulator-report";
        report["version"] = 1;
        report["run"] = {{"engine", "cpp"},
                         {"sim_end_minutes", e.now()},
                         {"pieces_sorties", static_cast<int>(built.exit_buffer->size())},
                         {"source_file", json_path.string()},
                         {"flow_snapshot", "flow.json"}};
        std::ofstream(out_dir / "report.json") << report.dump(1);
        std::ofstream(out_dir / "flow.json") << flow.dump(1);

        double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        emit("DONE", {{"report_dir", out_dir.string()},
                      {"sim_now", e.now()},
                      {"elapsed", elapsed},
                      {"pieces", static_cast<int>(built.exit_buffer->size())}});
        return 0;
    } catch (const std::exception& ex) {
        emit("ERROR", {{"message", ex.what()}});
        return 1;
    }
}
