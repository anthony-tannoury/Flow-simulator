// Flow-simulator C++ engine ("flow_sim").
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
// The flow JSON is now parsed into a real simulation (parser++); the run is
// sliced exactly like sim_runner.py so the stopper's plain-run semantics hold
// (the SimulationStopper activates main, the slice returns early). The full KPI
// report (postes/buffers/flux CSVs + the rich report.json) is kpis++, still
// pending; until then a minimal, well-formed report.json is written and every
// such site is tagged `TODO(kpis++)`.
//
// Build with Clang or MSVC — salabim.hpp uses C++20 coroutines that GCC<=13
// miscompiles (internal compiler error).

#include "parser.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

using json = nlohmann::json;
using namespace simulation;
namespace fs = std::filesystem;

namespace {

void emit(const char* tag, const json& payload) {
    std::cout << "@@" << tag << ' ' << payload.dump() << std::endl;  // endl flushes: read live
}

std::string timestamp() {
    std::time_t now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d_%H%M%S", std::localtime(&now));
    return buf;
}

double wall_seconds(std::chrono::steady_clock::time_point t0) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
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
        std::stringstream buffer;
        buffer << f.rdbuf();
        std::string flow_text = buffer.str();  // nlohmann decodes UTF-8 (no mojibake)

        auto& e = init(0, false);
        e.trace(false);

        parser::Parser p(flow_text);
        p.load_all();
        StoppingCriterion* criterion = p.stopping_criterion;
        Buffer* exit_buffer = p.exit_buffer();

        // --- @@META + slice stride (mirror sim_runner.py) ----------------------
        json meta = {{"engine", "cpp"},
                     {"file", json_path.string()},
                     {"sim_start", p.data.value("start_date", "")}};
        double stride = 30.0;
        if (auto* bt = dynamic_cast<ByTime*>(criterion)) {
            meta["criterion"] = "ByTime";
            meta["total_time"] = bt->time;
            stride = std::max(1.0, bt->time / 1000.0);  // ~1000 progress points
        } else if (auto* bp = dynamic_cast<ByPiecesProduced*>(criterion)) {
            meta["criterion"] = "ByPiecesProduced";
            meta["goal"] = bp->total;
            if (!std::isinf(bp->timeout)) meta["timeout"] = bp->timeout;
            stride = 30.0;  // sim minutes per slice; grows when slices turn out empty
        } else {
            emit("ERROR", {{"message", "unknown stopping criterion"}});
            return 1;
        }
        emit("META", meta);

        auto t0 = std::chrono::steady_clock::now();
        auto snapshot = [&]() {
            return json{{"sim_now", e.now()},
                        {"elapsed", wall_seconds(t0)},
                        {"pieces", static_cast<int>(exit_buffer ? exit_buffer->size() : 0)}};
        };

        // Slice so progress can be reported from outside the sim; the stopper
        // activates main, so a slice returns early when the criterion fires.
        double last_emit = -1.0;
        while (!criterion->done()) {
            auto slice_started = std::chrono::steady_clock::now();
            e.run(sim::RunOpts{.till = e.now() + stride});
            if (std::isinf(e.peek()) && !criterion->done()) break;  // nothing left to schedule
            double now = wall_seconds(t0);
            if (now - last_emit >= 0.1) {
                emit("PROGRESS", snapshot());
                last_emit = now;
            }
            if (wall_seconds(slice_started) < 0.005) stride = std::min(stride * 2, 1440.0);
        }
        emit("PROGRESS", snapshot());

        // --- run folder + report ----------------------------------------------
        fs::path out_dir =
            fs::current_path() / "runs" / (timestamp() + "_" + json_path.stem().string());
        fs::create_directories(out_dir);

        // TODO(kpis++): the full report — postes/buffers/flux/operateurs CSVs and
        // the rich report.json (raw KPI dicts, admin_summary, graphs map). For now
        // a minimal, well-formed report.json so results mode has a run block.
        int exit_pieces = static_cast<int>(exit_buffer ? exit_buffer->size() : 0);
        json report;
        report["format"] = "flow-simulator-report";
        report["version"] = 1;
        report["run"] = {{"engine", "cpp"},
                         {"source_file", json_path.string()},
                         {"flow_snapshot", "flow.json"},
                         {"sim_end_minutes", e.now()},
                         {"criterion", p.data.at("stopping_criterion")},
                         {"pieces_sorties", exit_pieces}};
        std::ofstream(out_dir / "report.json") << report.dump(1);
        std::ofstream(out_dir / "flow.json") << flow_text;  // byte copy of the flow that ran

        json done = snapshot();
        done["report_dir"] = out_dir.string();
        emit("DONE", done);
        return 0;
    } catch (const std::exception& ex) {
        emit("ERROR", {{"message", ex.what()}});
        return 1;
    }
}
