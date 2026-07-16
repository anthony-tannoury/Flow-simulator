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

## Not needed in C++

* Buffer monitor checkboxes were removed from the flow designer and the JSON
  format — the C++ port never had them; nothing to do.
