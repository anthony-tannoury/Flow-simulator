# flow_sim — the C++ simulation engine

A native alternative to `flow_designer/sim_runner.py`. It reads a flow JSON, runs
the discrete-event simulation, and writes the same run folder the Python engine
does, so the flow designer can spawn either one interchangeably.

## The contract (identical to `sim_runner.py`)

```
flow_sim <flow.json>
```

Prints tagged progress lines to stdout, one at a time:

| line | when | payload |
|------|------|---------|
| `@@META {...}` | after loading | criterion + totals |
| `@@PROGRESS {...}` | during the run | sim clock, wall time, pieces |
| `@@DONE {...}` | after the report | the run directory |
| `@@ERROR {...}` | on a fatal error | message (then exits nonzero) |

Writes `runs/<stamp>_<stem>/` with `report.json` and `flow.json`. Graphs stay in
Python: the designer runs `graphs.py` on the run folder afterwards. Results are
**statistically equivalent** to the Python engine, not byte-identical (the RNG
streams differ across languages).

## Building

Depends only on header-only libraries (`salabim++`, `simulation++`,
`cpp/third_party/json.hpp`), so the binaries are portable and dependency-free.

```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
./build/flow_sim path/to/flow.json
```

**Use Clang or MSVC, not GCC.** `salabim.hpp` uses C++20 coroutines for its
process functions; GCC ≤13 hits an internal compiler error on them. Clang 18 and
MSVC build cleanly. CI (`.github/workflows/`) builds the three shipped binaries
with Clang (Linux/macOS) and MSVC (Windows).

## Status (milestones)

- **M1 — harness** ✅ argument handling, the `@@` protocol, the slicing loop, the
  run folder, and JSON read/write.
- **M2 — engine** ✅ the C++ engine parses a real flow, runs the simulation, and
  writes the same run folder the Python engine does.
  - `simulation.hpp` caught up to the current Python on every behavior-significant
    entry (see `simulation++/PENDING.md`).
  - `parser++` (`engine/parser.hpp`) — flow JSON → a live simulation. `main.cpp`
    slices the run exactly like `sim_runner.py`.
  - `kpis++` (`engine/kpis.hpp`) — the collectors, the utf-8-sig CSVs and the
    rich `report.json`. salabim++ gained a mode-over-time timeline so the
    machine-hours / `attente_*` columns read `mode_log()` the way Python reads
    `mode.xt()`.
  - Verified statistically equivalent to the Python engine (seed 0) on
    `sample_flow`, `sample_flow_rate` and `atelier_injection`: `generated` matches
    exactly, exit/scrap and every KPI column land within RNG-stream tolerance.
  - Graphs: the engine writes a `graph_data.json` (raw monitor time-series +
    per-piece journals + production tallies) instead of drawing. The designer then
    runs `python -m simulation.render_from_data <run>` after a C++ run, which feeds
    that data to the unchanged `simulation/graphs.py` and fills report.json's
    `graphs` map — so results mode shows the same figures (occupation, buffers,
    WIP, waiting, operator/resource availability, model trajectories, production)
    a Python run would. The matplotlib/naming logic lives in one place (Python).
- **M3 — distribution** ✅ per-platform static builds via GitHub Actions
  (`.github/workflows/build-engines.yml`), committed into `engines/`.
  - CI builds all three on GitHub-hosted runners — Linux (Clang, static libstdc++),
    macOS (Apple Clang, universal arm64 + x86-64) and Windows (MSVC, `/MT`) — smoke-
    tests each, and commits them back into `engines/` with `[skip ci]`. Verified the
    committed artifacts are an ELF x86-64, a 2-arch universal Mach-O, and a PE32+ x64
    exe respectively.
  - `engines/build_local.sh` / `build_local.bat` reproduce any platform's binary by
    hand with the same commands, if you'd rather not use Actions. A missing platform
    binary is harmless: the designer falls back to Python (or "select executable").
- **M4 — designer** ✅ the Python/C++ engine picker (Simulation → Engine), stored in
  QSettings, auto-selects the bundled `engines/flow_sim-<platform>` binary, with a
  "Select C++ executable" fallback and a graceful Python fallback when no native
  engine is present for the host platform.

A ByPiecesProduced flow whose goal is never reachable (every shift ends with the
goal unmet and no `timeout`) runs until nothing is left to schedule — the same
non-termination `sim_runner.py` has; give such flows a criterion `timeout`.
