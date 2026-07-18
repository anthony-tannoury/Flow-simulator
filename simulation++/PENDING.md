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

The plotting itself (`simulation/graphs.py`, matplotlib) stays in Python;
the C++ port only needs to produce the same *data*. Output layout is
`graphes/{png,csv}/<category>/<stem>.{png,csv}` (format first, then category);
task occupation plots raw claimed slots (max_capacity - vacant) with capacity
in the title, operator plots carry the group's max headcount in the title.
Data hooks needed:

* `piece.py`: `Piece.journal` — `('in'|'out'|'task', name, t)` entries
  appended on every buffer enter/leave and at deposit (the `task` stamp is
  added in `PieceCarrier.successfully_end_process` before `place`);
  `Piece.leave(q)` override records the 'out'.
* `piece.py`: `PieceGenerator.total_generated` per model (physical births,
  never decremented — unlike `generated` which is scrap-aware).
* Monitors read for the plots (already in salabim++): resource
  `available_quantity`, store `length`, `vacant_slots.claimed_quantity`,
  operator-group `available_quantity`, plus the WIP monitor from entry 4.

## 6. KPI correctness fixes

* `task.py`: startup time is tallied as the actual setup hold (the sampled
  `startup_duration`) inside `TaskStarter` after its `hold(duration)`, NOT the
  wall-clock span in `handle_startup` (which included waiting for the startup
  crew). `handle_startup` no longer captures `startup_begin` or tallies.
* `kpis.py`: `gel` is the overlap of `is_frozen == True` AND
  `is_in_downtime == False` (frozen only during opening hours; a freeze that
  spills into the night must not count the night). New helper
  `overlap_duration(mon_a, val_a, mon_b, val_b)`.
* `kpis.py`: OEE is `Do x Tp x Tq` with `Tp = TN / (loading + processing time
  summed over every carrier)`, not `TN / TF_union` — the union undercounts
  parallel carriers and pushed the rate over 100%. `TRS = Do*Tp*Tq`,
  `TRG = TRS * (TR/TO)`, `TRE = TRS * (TR/TT)`; the old `TU`-based identity is
  dropped. `Do = TF_union / TR` is unchanged.

## 7. Freeze/startup lifecycle fixes

* `operator.py`: `OperatorGroup` gets `dependent_tasks: list`; `Task.setup`
  registers itself on every group it uses (operators + loading_operators +
  startup_operators). `OperatorShiftManager.on_enter` clears `is_frozen` on
  those tasks when the group comes back on shift. Reason: a task frozen because
  operators left was only unfrozen at its own next shift start; with merged
  multi-day shifts that could be weeks away, so one operator-shift-end froze
  the task for days. Now it resumes when the operators return.
* `task.py`: `TaskShiftManager.on_leave` sets `entity.started_up = False`
  (the machine warms up again each shift; `nb_mises_en_route` becomes per-shift
  instead of once per run). Note: this reduces throughput for tasks with a
  startup crew (they re-warm-up and wait for the crew every shift).
* `task.py`: the PER_TASK crew acquisition is decoupled from `started_up` (which
  now resets every shift end) and given its own lifecycle, so one crew supervises
  every carrier but hands off at operator-shift boundaries instead of being locked
  to the run. New `Task.requested_per_task_operators` flag (init False). The
  operator request moved out of `handle_startup` (now warm-up only) into
  `request_task_operators()` (request + set flag; freeze on failure) and
  `release_task_operators()` (release the held crew + clear flag, idempotent). In
  `Task.process`, after the state wait: (1) if PER_TASK and a crew is held and no
  carrier is active and any held group `is_in_downtime()`, release it (hand-off);
  (2) warm up if needed; (3) if PER_TASK, started up, not frozen and no crew held,
  `request_task_operators()`. Every task-level release now goes through
  `release_task_operators()` (base `Carrier.process` freeze branch, `PieceTask`/
  `ResourceTask.abort`) so the flag stays consistent. Reason: without this a
  PER_TASK task claimed its pool once and never let go across operator-shift
  boundaries — locked to one crew for the whole run and, with `started_up`
  resetting each shift, re-claiming without releasing until the line deadlocked.
  Now the crew is released the moment it goes off shift (after the current carrier)
  and the next on-shift pool is picked up; at most one crew is held at a time.

## 8. Step time-function (`function_generator.py`)

* New `Step` class alongside `Linear`/`Exponential`/`Bathtub`:
  `Step.generate(x1, y1, x2, y2, step_size)` returns a staircase that follows
  the line through (x1,y1)-(x2,y2) but holds each value for `step_size` on the
  x axis: `anchor = x1 + floor((t - x1) / step_size) * step_size`, value
  `y1 + slope * (anchor - x1)` with `slope = (y2 - y1)/(x2 - x1)`. Raises on a
  vertical span (`x1 == x2`) or `step_size <= 0`. (`function_generator.py`
  gained `import math`.)

## 9. Piece-generator split: goal vs rate (`piece.py`)

The single `PieceGenerator` became an abstract base with two concrete flavours;
the stopping criterion now drives which is built.

* `PieceGenerator(Component, PickyPieceTaker, HasShifts, ABC)`: shared `setup`
  (the one-generator guard, models/shifts/outlets, `generated` and the new
  `total_generated` — physical births, never decremented), `emit(idx)` (build a
  Piece, `place` it, bump both counters), `hold_within_shift(gap) -> bool` (hold
  `gap`, or hold to the shift end and return False when it would spill past the
  current shift), and an abstract `process`.
* `GoalPieceGenerator(PieceGenerator)`: `setup(models_goals, shifts, outlets)`;
  keeps `goals`, `probs`, `total_goal`, and `gap = sum(shift.length)/total_goal`.
  `update_probs` weights the remaining goal per model; `process` is the old
  goal-paced loop (wait downtime, respect the shift, `update_probs`, hold the
  gap, sample, `emit`). This is the behaviour the pre-split generator had.
* `RatePieceGenerator(PieceGenerator)`: `setup(models, shifts, outlets, gap,
  model_probs)` where `gap` is a float or callable(t) and `model_probs` is a
  list of float | callable(t) | None (exactly one None allowed = the freeloader,
  whose probability is `1 - sum(others)`); raises if more than one None.
  `current_gap()` / `current_probs()` evaluate the callables at `env.now()`,
  fill the freeloader slot, and `check_probabilities`. `process`: wait downtime,
  `hold_within_shift(current_gap())` (continue on a shift spill), sample with
  `current_probs()`, `emit`. Runs until the ByTime stopper fires.

## 10. Parser: generation split between the generator node and the criterion (`parser.py`)

* `make_callable` gains a `'step'` case ->
  `Step.generate(x1, y1, x2, y2, step_size)`.
* `load_piece_generator`: the generator node carries `shifts` and `outlets`
  (`shifts = join_shifts([self.shifts[id] for id in node['shifts']])`); what it
  emits comes from `data['stopping_criterion']`. `ByPiecesProduced` ->
  `GoalPieceGenerator(models_goals=...)`, `ByTime` ->
  `RatePieceGenerator(models=..., gap=make_callable(criterion['gap']),
  model_probs=[make_callable(p) if p is not None else None ...])`. The generator
  node JSON is `id/kind/name/shifts/outlets/position`; `models_goals`/
  `models_probs` live under the criterion, but the generator's `shifts` stay on
  the node. `load_stopping_criterion` totals `ByPiecesProduced` from
  `criterion['models_goals']`.

## 11. KPI/graph handling for the rate generator (`kpis.py`, `graphs.py`)

* `flow_kpis`: per-model rows use `getattr(piece_generator, 'goals', None)`; add
  a `genere` column (`total_generated[i]`, both flavours). `objectif`/`atteinte`
  are blank when there are no goals (rate generator).
* `production_histogram`: with goals, the three-bar chart (objectif/générées/
  produites) and CSV with an `objectif` column; without goals, a two-bar chart
  (générées/produites) and a CSV without `objectif`.

## 12. Labor and machine hours (`task.py`, `kpis.py`)

* `Task` gains `labor_minutes` (operator-minutes booked on the task by every
  crew) filled at three points: `Carrier.handle_batch_operators` adds
  `sum(counts) * duration` after each hold (loading + PER_BATCH processing);
  `TaskStarter` adds the startup crew's `sum(counts) * duration` after the
  warm-up hold; the PER_TASK crew accrues over its whole claim window
  (`request_task_operators` stamps `_task_crew_since`, `release_task_operators`
  adds `sum(counts) * (now - since)`), independent of how many carriers ran
  under it. `Task.labor_minutes_total()` adds a still-held crew's open claim.
* `kpis.task_kpis` gains two DUREE columns: `heures_machine` (= the existing
  value-add, loading + processing summed over carriers) and
  `heures_main_oeuvre` (= `labor_minutes_total()`).

## 13. Goal generator: grace period + scrap-triggered remakes (`piece.py`, `outlet.py` path, `parser.py`)

* `PieceGenerator` is now `Triggerable` (gains a `trigger` state). `Piece.enter`
  into a scrap buffer, right after decrementing `generated[idx]`, pulses
  `piece_generator.trigger` so a sleeping goal generator wakes for the remake.
* `GoalPieceGenerator.setup` gains `grace_period: float = 0.0` and
  `gap: float | None = None`. With `gap=None` (automatic) it paces with
  `gap = (sum(shift.length) - grace_period) / total_goal` (raises when
  `grace_period < 0` or `>= working_time`); the whole goal is therefore born
  with `grace_period` of working time to spare, a reserve that absorbs the
  off-pace scrap remakes. A non-None `gap` fixes the pacing verbatim (raises
  when `gap <= 0` or combined with a nonzero grace period).
* `GoalPieceGenerator.process` reordered: after the downtime wait it runs
  `update_probs()` first and, when all probs are 0 (everything asked for is
  out), does `wait(trigger)` instead of polling every gap; on wake it loops
  (downtime -> probs -> shift check -> hold(gap) -> emit). The nonzero path is
  unchanged (same holds, same single gap between update_probs and the RNG
  draw), so runs are bit-identical until the goal is first exhausted.
* Parser: `load_piece_generator` passes `gap=float(criterion['gap'])` when the
  criterion carries `gap`, otherwise
  `grace_period=float(criterion.get('grace_period', 0.0))`. The criterion JSON
  therefore carries either an optional `grace_period` (automatic pacing,
  default 0) or a `gap` (manual pacing, minutes) — the designer's
  By-pieces-produced settings write one or the other behind an
  automatic-gap toggle, never both.

## 14. Machine-readable report (`kpis.py`, `parser.py`) — mirror-worthy

* `kpis.operator_kpis(group)`: name, headcount, posted time (downtime False),
  mean/max claimed operators, `taux_occupation = claimed_mean * TT /
  (headcount * posted)`. `write_report` gains `operator_groups` and writes
  `operateurs.csv`; `temps_poste` joined DUREE_COLS, `taux_occupation` PCT_COLS.
* `Parser.write_machine_report(directory, run_info)` (called at the end of
  `report()`) writes two extra artifacts next to the CSVs:
  - `report.json`: the same collector dicts (`task_kpis`, `task_model_rows`,
    `buffer_kpis`, `operator_kpis`, `flow_kpis`) but RAW (minutes/fractions,
    unformatted) and keyed by node/registry id, plus a `run` block (source
    file, seed, sim end, criterion, pieces_sorties / objectif_total /
    objectif_atteint) and a `graphs` map of node id -> relative PNG path
    (only entries whose file exists).
  - `flow.json`: a byte copy of the flow that ran, so any run folder can be
    reopened standalone.
  The designer's results mode consumes exactly these two files; mirroring them
  in the C++ port makes its runs browsable in the same viewer.

## 15. Parser: tolerant type-name matching (`parser.py`) — optional

* `canon_name(value)` strips non-alphanumerics and lowercases; every type-name
  lookup (`dist_type`, function `kind`, policy `type`, buffer/collector/scope/
  shutdown types, shift/shutdown/mtbf `mode`, the stopping-criterion `type`)
  now matches through it, so `"ByTime"`, `"By time"`, `"PER_BATCH"` and
  `"Per batch"` all parse the same. Helpers: `lookup(table, value, what)` and
  `same_name(a, b)`. Node `kind` stays strict (structural discriminator).
* Optional to mirror: the designer still exports canonical identifiers
  (`ByTime`, `PER_BATCH`, `AbortPendingCarriers`, ...), so the C++ parser keeps
  working without this; it only matters for hand-edited files using the
  designer's sentence-case display forms.

## Not needed in C++

* Buffer monitor checkboxes were removed from the flow designer and the JSON
  format — the C++ port never had them; nothing to do.
* Designer save flow (dirty tracking, Save / Save as, unsaved-changes prompts on
  New/Open/exit) and the Run simulation dialog are designer-only. The dialog
  spawns `flow_designer/sim_runner.py`, which parses the saved JSON, runs the
  sim in slices and prints `@@META/@@PROGRESS/@@DONE/@@ERROR {json}` lines
  (criterion type, total time or piece goal, sim clock, pieces in the exit
  buffer); the popup renders elapsed/simulated time, an 'n / total' caption and
  a progress bar from them. JSON format unchanged.
* The designer UI now displays sentence-case labels (By time, Per batch,
  Non discriminating greedy...) but keeps exporting canonical names — combos
  carry the canonical value as item data. Only the Shutdowns card's on-node
  combo stores the display label in its property; its `to_clean_json`
  canonicalizes on export, so the JSON stays canonical there too.
* Results mode (`flow_designer/results_mode.py` + window hooks) is
  designer-only: it locks the canvas on the run's flow.json snapshot, routes
  double-clicks to per-card KPI dialogs, shows the Run/Flux/Opérateurs/Ligne
  dock, tooltips and the color-by-metric heat map — all read from report.json
  (entry 13); no simulation-side behavior involved.
* The flow-designer refactor around generation is designer-only; the C++ port has
  no designer and reads the criterion-based JSON described in §10. For the record:
  the generation mix (goals or per-model rates) lives in Simulation Settings per
  stopping-criterion type; the generator's `shifts` are edited on the generator
  card itself; both criteria show one fixed box per leaf model (no add/remove/
  dropdown, every leaf model always exported) — ByPiecesProduced a goal box
  (goal >= 0, total > 0), ByTime a probability time-function box with at most
  one freeloader (exported as null; all-constant mixes are validated to sum to
  1, or <= 1 with a freeloader); and importing a JSON no longer wraps it in an
  auto-generated backdrop (only backdrops saved in the file are recreated).
