# Code improvements — audit only, nothing applied

Every item below is a suggestion; none of it has been implemented. Items marked
**[measured]** come from actual instrumented runs on this codebase, not from
reading alone. Paths refer to the new layout (`cpp/…`, split `flow_designer/…`).

---

## 1. Cross-cutting (both engines — highest impact)

### 1.1 Operator claims ignore task priority **[measured]**
`simulation/operator.py` `Alternative.request` (and its mirror in
`cpp/simulation++/simulation.hpp`) never passes `request_priority` into the
underlying salabim requests, and the multi-pool path is a poll-and-retry race:
every waiter wakes on any pool trigger, retries alternatives in fixed order,
and losers re-queue at the tail. Consequences:

- The designer's per-task priority knob has **zero effect on labor
  allocation** — only on slot/piece/resource requests (`task.py`:
  `request_priority = 10 - config.priority` is simply never forwarded here).
- Under labor saturation, operators are distributed proportionally to *claim
  volume*, so flooded upstream stations starve the bottleneck. Measured on the
  atelier: goal 50k → 41,763 exits, goal 100k → 30,718 exits — a genuine
  congestion collapse driven entirely by this mechanism (the bottleneck's
  per-piece operator wait went 6.0 → 10.9 min, exactly its −27 % output).

Suggested fix: pass `request_priority` through `Alternative.request`, and make
the multi-pool wait honor priority (or at least FIFO with a persistent queue
position instead of a memoryless re-race). This is the single most valuable
behavioral improvement available.

### 1.2 No finite buffer capacity — no backpressure
Buffers are unbounded, and placement is unconditional: `helpers.place()` calls
`piece.enter(...)` synchronously, so a piece can never wait to enter a buffer.
Overloaded flows therefore express themselves as unbounded WIP + labor
starvation (see 1.1) instead of graceful upstream blocking. Adding an optional
`capacity` to `Buffer` (designer field + both engines) would let users model
real line coupling. Note on naming: the existing `attente_place` KPI is
*not* about buffers — it is the collectors' `wait_slot` time against the
task's own `max_capacity` slot pool (nonzero where carriers contend, e.g.
Rework 2/3). A placement-block wait would be a new measurement and should get
a distinct name (e.g. `attente_place_aval`) to avoid overloading this one.

### 1.3 Three hand-maintained copies of every enum/policy table
`flow_designer/ui_helpers.py` (`COLLECTOR_TYPES`, `POLICY_OPTIONS`, …),
`parser/parser.py` (`STR_TO_*`, `DEFAULT_POLICIES`) and
`cpp/engine/parser.hpp` (`distr_types()`, `piece_collector_types()`, …) each
restate the same identifiers and defaults. Any addition (a new protocol, a new
distribution) must be made three times and silently drifts if forgotten once.
Generate all three from one small spec file (JSON or Python) at build time, or
at least add a test that diffs the three lists.

### 1.4 Unbounded per-piece journals **[measured]**
Every `Piece` keeps a full journal of enter/leave events for its whole life
(`simulation/piece.py`, mirrored in C++). In saturated runs this is the bulk of
RAM (the 100k-goal C++ run peaked at ~5.8 GB; the pathological
`sample_flow.json` reached 10 GB). Make journals opt-in (a runtime flag), or
cap them per piece, or record only the last N hops unless a debug flag is set.

### 1.5 Collector focus-model scan is O(WIP) per attempt **[measured]**
`MostPresent` (and the collector loops generally) re-scan the whole inlet
buffer to count models each time a collector wakes. With WIP piled to tens of
thousands, wall time explodes super-linearly — measured: 50k goal ≈ 5 min,
100k goal ≈ 37 min on the same box. Keep a per-model counter on `Buffer`
(increment/decrement on enter/leave) and read it in O(models) instead.

### 1.6 `sample_flow.json` livelocks — and the engines let it **[measured]**
On main, unmodified `flow_designer/sample_flow.json` never terminates: goal
200, `timeout: inf`, and the run reached sim time 6.3×10⁹ minutes (~12,000
years) with 14 pieces out, memory growing without bound; the bathtub MTBF even
overflows `exp()` (`RuntimeWarning` in `simulation/function_generator.py`).
Two distinct improvements:
- fix or replace the sample flow (it is the first thing a new user runs);
- add an engine guard: if no piece has moved for a long horizon (or all shifts
  are exhausted) while the criterion is `inf`-bounded, stop with a clear
  message instead of simulating forever.

### 1.7 graph_data held fully in RAM, sampled per event
Both engines accumulate every monitor sample in memory and dump at the end
(`graph_data.json`). Long/saturated runs pay GBs for series nobody plots at
that resolution. Stream to disk incrementally, or decimate (e.g. keep ≤ 50k
points per series with min/max preservation).

---

## 2. Python simulation (`simulation/`)

- **`scratchpad.py` is dead code** — a stale near-duplicate of
  `resource_task.py` carriers. Nothing imports it. Delete it; its presence
  invites editing the wrong file.
- **Global mutable `env` singleton** (`simulation/__init__.py`) forces callers
  (tests, multi-run scripts) to reload the whole package to reset state — my
  own test harnesses had to `del sys.modules` in a loop. Provide
  `simulation.reset()` (rebuild env + reseed) or pass the env explicitly.
- **`typing.override` requires Python ≥ 3.12** (`simulation/operator.py`,
  others). The stock `python3.11` on many machines fails at import. Either
  guard it (`try: from typing import override; except ImportError: identity`)
  or drop the decorator — it is cosmetic.
- **`Piece.enter` asserts `isinstance(q, Buffer)`** — user config errors
  surface as bare `AssertionError`. Raise a typed error naming the piece and
  outlet.
- **`RatePieceGenerator.current_gap`** re-evaluates the time function per
  emission; harmless, but the guard message is the only place that reports the
  offending value — consider clamping-with-warning as an option since a
  mid-run raise loses the whole simulation.
- **Protocol scans**: `FirstCreatedFirstOut` / exit-order protocols walk the
  queue linearly per pick. With deep buffers this stacks onto 1.5. A sorted
  container or an index by creation order would remove the scan.
- **`kpis.py` mixes measurement and French formatting** — collectors return
  formatted strings (`fmt_duree`) in the same dicts used for machine
  consumption; `report.json` then re-derives raw values separately. Split
  "collect numbers" from "render CSV" so both outputs share one source.
- **`graphs.py` renders every PNG serially** with a fresh matplotlib figure
  per chart; on the atelier that is ~60 figures and dominates report time on
  slow machines. Reuse one figure (`fig.clf()`), or gate PNG rendering behind
  a flag (the designer's results mode reads `graph_data.json` anyway).
- **Salabim monitors everywhere**: every buffer/resource/task keeps
  level-monitors on by default. For headless batch sweeps, exposing a
  "no-stats" mode would speed runs measurably.

---

## 3. C++ engine (`cpp/`)

- **Build time**: the engine is one translation unit that `#include`s
  `salabim.hpp`, `simulation.hpp`, `parser.hpp`, `kpis.hpp` and the 25k-line
  `third_party/json.hpp`; every source touch costs a full ~1–2 min rebuild.
  A precompiled header for `json.hpp` (or splitting parser/kpis into their own
  TUs) would cut iteration time several-fold.
- **Ownership**: `Parser` allocates every `Model*`, `Resource*`,
  `OperatorGroup*`, `Outlet*`, `Task*` with raw `new` and never frees —
  acceptable for a run-once process, but it blocks embedding the engine (e.g.
  running N seeds in one process) and hides leaks from sanitizers. Switch the
  registries to `std::unique_ptr` (mechanical change, parser-local).
- **Registry maps** are `std::map<std::string, …>` — ordered tree lookups on
  every id resolution during load. `unordered_map` is a drop-in win (load-time
  only; harmless either way).
- **`graph_data` duplication**: series are stored in full `std::vector`s and
  then serialized through nlohmann json, momentarily tripling memory at report
  time in saturated runs. Stream the JSON out with a `dump()`-per-series loop
  or write CSV.
- **Determinism pinning**: the run1.json result (9,924 pieces at seed 0) is a
  perfect cheap regression oracle. Assert it in CI (see §6) so cross-engine
  drift is caught the day it appears.
- **`.DS_Store` is committed** under `cpp/salabim++/` — add to `.gitignore`
  and remove.

---

## 4. Flow designer (`flow_designer/`)

- **`FlowEditorWindow` is still a 1,700-line god-class** (canvas, file/session
  lifecycle, validation, results mode, copy/paste, run orchestration). The
  next natural cuts, all mechanical: validation (`validate_graph`,
  `_check_flushability`, `_outlet_valid_models`) into a `validation.py` that
  takes `(nodes, registries, criterion)`; export/load (`export_clean_json`,
  `load_clean_json`, `_instantiate_cards`, `_apply_ref_map` users) into a
  `serialization.py`; results-mode plumbing next to `results_mode.py`.
- **`validate_graph` is one 400-line function** — a dispatch table
  `{kind: check_fn}` would let each rule be tested in isolation and would have
  made the disabled-cards change a one-liner.
- **`get_property_json` JSON-decodes on every read** — card properties are
  stored as JSON strings and parsed hundreds of times per validation/export
  pass. Cache decoded values on the node (invalidate on `property_changed`),
  or store dicts directly (NodeGraphQt tolerates arbitrary Python values).
- **~90 `except Exception: pass` blocks** across the designer swallow real
  bugs (misspelled property names fail silently as UI no-ops). Narrow the
  common ones (NodeGraphQt version differences) to the specific exceptions
  they guard, and log the rest.
- **Dual package/script import shims** (`try: from .x import …
  except ImportError:`) exist in every split module. Standardize on package
  execution (`python -m flow_designer`) plus a tiny `__main__.py`, and delete
  the fallbacks.
- **`_apply_ref_map` mutates deep dicts by convention** — it knows every
  reference-carrying key by hand (`operators`, `loading_operators`, shifts,
  criterion models…). Each new reference type must be added here or ids leak
  into the export unmapped. A declarative schema of "where references live"
  would collapse this and the parser scrubbers (my `enabled` change had to
  enumerate reference keys in three places for the same reason).
- **Heat-map color save/restore** round-trips through the node property
  system with class-attribute shadowing caveats — fragile across NodeGraphQt
  versions; consider painting an overlay instead of mutating node colors.
- **`RunSimulationDialog`** hand-parses `@@TAG {json}` lines — fine, but the
  tag contract lives in three places (dialog, `sim_runner.py`,
  `cpp/engine/main.cpp`); document it once or share a tiny spec constant.

---

## 5. Python parser (`parser/`)

- **One 800-line class does loading *and* reporting** (`Parser.report`,
  `write_machine_report` next to `load_*`). Reporting depends only on the
  built registries; move it to `parser/report.py` so the parser is importable
  without matplotlib.
- **`lookup()` recomputes `canon_name` over the whole table per call** during
  load — build canonicalized dicts once per table (micro, but free).
- **User config errors raise `NotImplementedError`** in several places
  (unknown protocol, distribution, criterion). For a tool with hand-editable
  JSON, raise `ValueError` with the node name and offending value; the C++
  side already does this better (`std::invalid_argument` with the value).
- **`drop_disabled_nodes` + `discriminate` + `by_id`** each walk `nodes` —
  trivially one pass, but more importantly the scrubbed reference keys are
  duplicated between the two engines (see 4/`_apply_ref_map` item).

---

## 6. Build, CI, testing

- **CI publish job can lose the race it just created**: `build-engines.yml`
  commits binaries back to the branch; a human push in the window forces the
  manual rebase dance (hit twice during development). Add
  `git pull --rebase origin <branch>` with a retry loop before the push.
- **No regression pin in CI**: the smoke test only greps `@@DONE`. Add one
  deterministic run per engine (`run1.json`, seed 0) asserting the exact piece
  count (9,924 for C++ at HEAD) so behavioral drift fails the build instead
  of surfacing weeks later as "the two engines disagree".
- **No committed test suite**: the repo's real invariants (five policies,
  PER_TASK hand-off, restock, shift repeats, disabled cards, Python-vs-C++
  parity on the atelier) all live in throwaway scripts. A `tests/` with the
  reload-env fixture pattern and 10–15 golden tests would lock in years of
  debugging.
- **`Test runs/` (10 JSONs + an xlsx) lives in the repo root** — it is user
  data, not code; move under `flows/` or `examples/` (with the 4 designer
  JSONs) so the root stays orientation-friendly.
- **Windows binary** can only be produced by CI or a machine with MSVC —
  document that `engines/flow_sim-windows-x86_64.exe` lags local Linux/macOS
  builds unless CI ran, since the designer will happily pick the stale one.

---

## 7. Suggested priority order

1. §1.1 priority-aware operator claims (changes saturated-flow behavior users
   are actively confused by).
2. §6 regression pin + minimal test suite (locks everything else in).
3. §1.5 buffer model-count cache + §1.4 journal cap (turns 37-minute saturated
   runs into minutes, GBs into MBs).
4. §1.3 single source of truth for enums/policies (stops silent drift).
5. §1.2 finite buffers (new modeling capability).
6. §4 designer decomposition + property-decode cache (maintainability).
