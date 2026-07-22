# Code improvements — status after the implementation pass

Each item now carries a status. **APPLIED** items are implemented on this
branch and verified; **DEFERRED** items were deliberately not done, with the
reason. Verification gates used throughout: C++ `run1.json` seed 0 must
produce exactly **9,924** pieces and Python exactly **9,949** (both held at
every stage), plus the committed test suite in `tests/`.

---

## 1. Cross-cutting (both engines)

### 1.1 Operator claims ignore task priority — **APPLIED**
`Alternative.request` (Python `simulation/operator.py`, C++
`cpp/simulation++/simulation.hpp`) now takes a `request_priority` parameter and
forwards it into the underlying resource requests and the multi-pool trigger
wait; every call site passes the task's priority (designer priority 10 → queue
priority 0 = served first). Measured on a synthetic contended pool
(`tests/test_priority_operators.py`): equal priorities split 1,439/1,440;
priorities 10-vs-0 split **2,879/0**, identically in both engines. Equal-priority
flows reproduce the old behavior exactly (parity pins unchanged), because equal
priorities collapse to the previous FIFO. Note the semantics: priority
arbitrates requests *standing at the same honoring instant* — a serial task
with a single carrier still alternates with a competitor because its next
request arrives after the release (non-preemptive resources).

### 1.2 No finite buffer capacity — **DEFERRED**
A real modeling feature (designer field + both engines + new KPI), not a
performance item; kept out of this pass. Naming note stands: `attente_place`
is the collectors' `wait_slot` time against the task's own `max_capacity`
pool; a future placement-block wait needs its own KPI name.

### 1.3 Three copies of every enum/policy table — **APPLIED (guard, not codegen)**
`tests/test_enum_sync.py` diffs the designer constants, the Python parser
tables and the C++ `parser.hpp` tables (parsed from source); any drift now
fails the suite. Full codegen from one spec remains possible later.

### 1.4 Unbounded per-piece journals — **APPLIED**
Journals are capped at 512 entries per piece in both engines (`Piece.JOURNAL_CAP`).
Normal journeys use ~40; only pathological rework loops ever hit the cap.

### 1.5 Collector focus-model scan is O(WIP) — **APPLIED**
Buffers maintain per-model counts on piece enter/leave (both engines); the
collectors' present-model check and focus choice read counts (with a
per-task `can_take` cache), scanning pieces only to break exact ties in the
original first-in-queue order. Measured: saturated 50k-goal C++ run
**305 s → 125 s (2.4×)** with byte-identical output (41,763 pieces); run1
−22% even unsaturated. Python parity exact (9,949).

### 1.6 `sample_flow.json` livelock — **APPLIED (both halves)**
The sample now carries a finite 2-year timeout and completes in 0.07 s.
Engine guard: `ByPiecesProduced` with an infinite timeout stops with a clear
error after 400 simulated days without a single exit
(`no piece reached the exit … stopping a run that can no longer progress`),
identically worded in both engines. Finite-timeout runs are untouched.

### 1.7 graph_data held in RAM / per-event sampling — **APPLIED (C++ dump-time)**
C++ `graph_data.json` series beyond 50k points are decimated bucket-wise
keeping first/min/max/last (step-plot fidelity preserved). In-run streaming
(engine RAM) remains future work; the journal cap (1.4) already removed the
bigger RAM consumer.

---

## 2. Python simulation

- **`scratchpad.py` dead code — APPLIED**: deleted.
- **Global `env` singleton / `reset()` — DEFERRED**: a truthful `reset()` is
  impossible while every module binds `from simulation import env` at import
  time (stale references survive any rebinding); it needs the env-passing
  refactor. Tests use the reload fixture in `tests/conftest.py` instead.
- **`typing.override` needs 3.12 — APPLIED**: `simulation/compat.py` shims it
  (identity decorator on 3.11); all seven users import from there.
- **`Piece.enter` bare assert — APPLIED**: raises a `TypeError` naming the
  piece, its model and the offending target.
- **Protocol scans (FirstCreatedFirstOut etc.) — DEFERRED**: inspected; they
  operate on carrier-sized lists (≤ max_carrier_capacity), not WIP — not hot.
- **kpis measurement/formatting split — DEFERRED**: byte-stable CSV output is
  a compatibility contract with existing sheets; splitting risks silent format
  drift for zero speed. Revisit alongside a deliberate format change.
- **graphs.py figure churn — APPLIED**: the per-series renderer reuses one
  module-level figure (`_series_figure`); output pixels unchanged (same
  figsize/dpi).
- **Monitors no-stats mode — DEFERRED**: needs designer plumbing to be usable.

## 3. C++ engine

- **Build time (PCH / split TUs) — DEFERRED**: requires reworking three build
  scripts + CI per platform; iteration cost is real but bounded (~90 s).
- **Raw-pointer registries → unique_ptr — DEFERRED**: components stay
  referenced by the global env (queues, monitors) until process exit;
  parser-owned deletion would tear objects down under a live env for zero
  runtime benefit in the current run-once process. Do it when the engine is
  embedded/multi-run.
- **map → unordered_map — DEFERRED**: the registries are iterated when
  building reports; changing iteration order churns CSV/JSON row order for a
  negligible load-time gain.
- **graph_data duplication — APPLIED** via 1.7 decimation.
- **Determinism pin in CI — APPLIED**: the Linux CI job asserts run1.json →
  exactly 9,924 pieces.
- **`.DS_Store` committed — APPLIED**: removed and ignored.

## 4. Flow designer

- **FlowEditorWindow decomposition (validation/serialization modules) —
  DEFERRED**: pure maintainability; the earlier module split already took the
  file from 4,972 to ~1,780 lines. Next natural cut documented here.
- **validate_graph dispatch table — DEFERRED**: same reason.
- **get_property_json decode cache — DEFERRED**: callers rely on receiving a
  fresh object each call (several mutate the result); caching would alias
  state. The real fix is storing dicts directly on nodes, a behavior change
  beyond this pass.
- **except-Exception narrowing — DEFERRED**: ~90 sites needing case-by-case
  judgment about NodeGraphQt version differences.
- **Package entrypoint — APPLIED**: `python -m flow_designer` works via
  `flow_designer/__main__.py` (the script invocation still works too).
- **`_apply_ref_map` declarative reference schema — DEFERRED**: design change
  shared with both parsers' scrubbers; worth doing together, not piecemeal.
- **Heat-map overlay, @@TAG contract note — DEFERRED**: cosmetic.

## 5. Python parser

- **Loading/reporting split — APPLIED**: report writing (incl. report.json)
  lives in `parser/report.py`; `Parser.report()` delegates lazily, so
  importing the parser no longer imports matplotlib.
- **lookup() canonical precompute — APPLIED**: canonical tables built once per
  table and cached.
- **Config errors as NotImplementedError — APPLIED**: every user-reachable
  case now raises `ValueError` with the offending value (unknown protocol,
  distribution, shift mode, shutdown mode/type, criterion type,
  router-to-router, non-constant output distribution).
- **One-pass discriminate/by_id — APPLIED**.

## 6. Build, CI, testing

- **CI publish race — APPLIED**: the publish job retries with
  `git pull --rebase -X theirs` up to four times before giving up.
- **Regression pin — APPLIED** (see §3).
- **Committed test suite — APPLIED**: `tests/` with 13 tests — enum/policy
  sync across the three sources, disabled-node scrubbing incl. the breakdown
  cascade and clear generator/exit errors, and the operator-priority
  behavior (fair split at equal priorities, total win at higher priority).
  Run with `python -m pytest tests/` (~1 min; the priority tests simulate).
- **Test runs/ + flow JSONs relocation — DEFERRED**: `Test runs/` is the
  user's data set referenced by existing sheets; the designer JSONs are
  wired into build-script and CI smoke tests. Moving them buys tidiness at
  the cost of breaking those references.
- **Windows binary staleness — noted**; unchanged.

---

## Measured results of this pass

| check | before | after |
|---|---|---|
| C++ run1.json (seed 0) | 9,924 pieces / 5.2 s | **9,924 pieces / 4.0 s** |
| Python run1.json (seed 0) | 9,949 pieces | **9,949 pieces** |
| C++ saturated 50k-goal atelier | 41,763 pieces / 305 s | **41,763 pieces / 125 s** |
| sample_flow.json | livelock (10+ GB, never ends) | **ends in 0.07 s / guarded** |
| operator priority 10 vs 0, one pool | no effect | **2,879 vs 0 (both engines)** |
