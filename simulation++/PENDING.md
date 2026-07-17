# simulation++ — pending changes to mirror from the Python simulation

The C++ port (`simulation.hpp`) tracks `simulation/*.py`. This file lists every
Python change made since the last sync, so the port can be updated in one pass.
Add an entry whenever the Python simulation changes; delete entries when they
land in `simulation.hpp`.

Last sync: the port matches the Python simulation as it was just before commit
`b48db69` (piece exit-order work); the shutdown generator, interval merging and
the RNG simplification are already in. Commits `b48db69..ecc1b5e` are covered
by the entries below.

## 1. Piece exit-order policy (`protocols.py`, `piece_task.py`)

* `protocols.py`: `ExitOrder` enum (`FIRST_IN_FIRST_OUT`, `FIRST_CREATED_FIRST_OUT`),
  `PieceExitOrder` protocol, `FirstInFirstOut` / `FirstCreatedFirstOut` classes.
* `piece_task.py`: `PieceProtocols(Protocols)` dataclass with field
  `piece_exit_order` (piece tasks now take `PieceProtocols`; resource tasks keep
  `Protocols`).
* `PieceCollector.pick_piece(**kwargs)` replaces the direct `from_store` calls in
  `collect_until`, `ensure_one` and `top_up`:
  - snapshot `(piece, buffer)` pairs from the inlet stores passing the caller's
    filter;
  - if any: pick `min` by policy key — FIFO: `piece.enter_time(buffer)`,
    FCFO: `piece.creation_time()` — take `[0]` (the piece!), and narrow the
    filter to `piece is target` (immediate honor, no scheduling point in
    between);
  - if none: plain `from_store` with the original filter so `fail_at`/
    `fail_delay` (timeout, instant top-up) keep working.
  Do NOT use salabim++'s `from_store` with a key argument across several
  stores — mirror this snapshot approach instead.
* `AltruisticMixin.collect_batch`: `valid_pieces` are `(piece, buffer)` pairs,
  sorted by the same policy key before truncation to `truncate`.

## 2. Focus-model policy for discriminating collectors (`protocols.py`, `piece_task.py`)

* `protocols.py`: `ModelChoice` enum (`MOST_PRESENT`, `FASTEST_TASK_DURATION`,
  `SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY`), `ModelChoiceCriteria` protocol,
  `MostPresent` / `FastestTaskDuration` / `SmallestGapToMinCarrierCapacity`.
* `PieceProtocols` gains field `batch_model_choice`.
* `PieceCollector.get_focus_model(present_models)`:
  - MOST_PRESENT: `Counter(present_models).most_common(1)[0][0]`;
  - FASTEST_TASK_DURATION: min over models by
    `get_model_config(model).duration.mean_now()` (deterministic mean, not a
    sample);
  - SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY: min over models by
    `min_carrier_capacity - count_present(model)` (negative gap = surplus wins).
  Used by `DiscriminatingGreedyPieceCollector` and
  `DiscriminatingAltruisticPieceCollector` in place of the inline
  `Counter(...).most_common` pick.

## 3. Distribution mean (`sampler.py`)

* `Distribution.mean(t)` = distribution constructed with params evaluated at
  `t`, `.mean()`; `Distribution.mean_now()` = `mean(env.now())`.
  (salabim++ distributions already expose `mean()`.)

## 4. KPI instrumentation (`kpis.py` + hooks across the simulation)

New module `simulation/kpis.py`: post-run collectors + CSV writer
(`write_report(directory, tasks, buffers, piece_generator, run_info)` →
run/postes/postes_modeles/buffers/flux/flux_modeles/temps_traversee/
series_temporelles CSVs, utf-8-sig). Mirror it once salabim++'s Monitor gains
whatever is missing of: `value_duration`, `xt`, `percentile`,
`number_of_entries` (level + non-level) — most already exist.

Hooks to mirror (behavior-neutral, verified identical results under the same
seed in Python):

* `task.py`
  - `Task.setup`: `all_carriers` list (finished carriers stay readable) +
    monitors `batch_sizes`, `cycle_times`, `startup_times`.
  - `Task.process`: append every new carrier to `all_carriers`.
  - `handle_startup`: tally elapsed startup time on success; `set_mode("")`
    after the PER_TASK operator request.
  - `Carrier.process`: `mode="wait_dispatch"` on the allow_dispatch wait.
  - `handle_batch_operators(..., work_mode)`: hold tagged `"loading"` /
    `"processing"` (parameter added; both call sites updated);
    `handle_task_operators` hold tagged `"processing"`.
* `piece_task.py`
  - `pick_piece`: default `mode="wait_pieces"` on the from_store.
  - every `vacant_slots` request (collect_until, ensure_one, top_up,
    block_remainder, altruistic paths): `mode="wait_slot"`.
  - altruistic trigger waits + discriminating present-models wait:
    `mode="wait_pieces"`.
  - collectors reset `set_mode("")` before `done.set(True)`; carriers and
    collectors reset mode in `abort` and `successfully_end_process`
    (a cancelled component's mode would otherwise accrue forever).
  - `PieceCarrier.wait_for_collector`: `mode="collecting"`;
    `request_resources`: `mode="wait_materials"`.
  - `PieceCarrier.successfully_end_process`: tally batch size + cycle time
    (now − carrier creation), count `task.deposited[model]` and — for pieces
    that landed in a SCRAP buffer via the immediate router —
    `task.scrapped[model]`.
  - `PieceTask.setup`: `deposited` / `scrapped` Counters.
* `resource_task.py`: same pattern (slot requests wait_slot, input gathering
  wait_pieces, non-transformed request wait_materials, collecting tag, mode
  resets, batch/cycle tallies with `requested_quantity`).
* `operator.py`: `Alternative.request` tags all demander requests/waits
  `mode="wait_operators"`.
* `resource.py`: `RestockableResource.restock` order hold tagged
  `mode="wait_materials"`.
* `piece.py`: global WIP level monitor (`kpis.WIP`): +1 in `Piece.setup`,
  −1 when entering an EXIT or SCRAP buffer.
* `parser` equivalent: object names passed to constructors; `report()` after
  the run (C++ side: same CSV format, same file names, same column names so
  the downstream tooling is shared).
* Report presentation layer (second pass): durations rendered as
  `Xj Xh Xm` (`3m 20s` under an hour), ratios as percentages, piece
  creation/fin instants as real calendar dates from `sim_start`; flux columns
  `flux_entrant_j`/`flux_sortant_j` on both `postes.csv` (fed by a
  `Task.pieces_in` counter incremented at every piece take in the four
  collector paths) and `buffers.csv`; débits are per day
  (`debit_pieces_j`, `debit_sorties_j`); `flux_modeles.csv` carries per-model
  traversée stats (moyenne/médiane/p90/max); `series_temporelles.csv` was
  removed (graph data will be handled separately).

## 5. Graphs support (data-side hooks only)

The plotting itself (`simulation/graphs.py`, matplotlib) stays in Python —
the C++ port only needs to produce the same *data*:

* `piece.py`: `Piece.journal` — `('in'|'out'|'task', name, t)` entries
  appended on every buffer enter/leave and at deposit (the `task` stamp is
  added in `PieceCarrier.successfully_end_process` before `place`);
  `Piece.leave(q)` override records the 'out'.
* `piece.py`: `PieceGenerator.total_generated` per model (physical births,
  never decremented — unlike `generated` which is scrap-aware).
* Monitors read for the plots (already in salabim++): resource
  `available_quantity`, store `length`, `vacant_slots.claimed_quantity`,
  operator-group `available_quantity`, plus the WIP monitor from entry 4.

## Not needed in C++

* Buffer monitor checkboxes were removed from the flow designer and the JSON
  format — the C++ port never had them; nothing to do.
