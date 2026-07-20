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
`engine/third_party/json.hpp`), so the binaries are portable and dependency-free.

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
  run folder, and JSON read/write are done and proven on the sample flow. The
  simulation itself is still a placeholder built in code (every such site is
  tagged `TODO(parser++)` / `TODO(kpis++)`).
- **M2 — engine** ⬜ `parser++` (flow JSON → sim), `kpis++` (collectors + CSVs +
  the full `report.json`), and catching `simulation.hpp` up to the current Python
  (the `simulation++/PENDING.md` entries). Then: sample, then atelier parity.
- **M3 — distribution** ⬜ per-platform static builds via GitHub Actions, committed
  into `engines/`.
- **M4 — designer** ⬜ the Python/C++ engine picker + auto-select of the bundled
  binary + a "select executable" fallback.
