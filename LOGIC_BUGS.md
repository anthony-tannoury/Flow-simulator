# Flow Simulator — Logic Bug Report

Logic / control-flow review of `simulation.py`. Findings were produced by a 6-lens
adversarial sweep, then the salabim-dependent claims were independently confirmed with
direct repros against **salabim 26.0.8**.

Repro-confirmed library behaviors this report relies on:

1. `Enum` with `auto()` values passes the **value** into `__init__`, so `discriminate` becomes `1`/`2` (both truthy).
2. Successive `request()` calls on one component **accumulate** the claim (`3 → 5`).
3. A `request`/`wait` with `fail_at` strictly in the past (no `cap_now`) raises `ValueError: scheduled time before now`.
4. salabim **auto-releases** a component's claimed resources when its `process()` returns.
5. A requester already **queued** on a resource beats a same-instant `fail_delay=0` request.

Line numbers refer to the version reviewed (588-line file).

---

## High severity — confirmed

### L1 — `BatchCollectorType` enum is miswired; `discriminate` is always truthy
**Where:** definition `simulation.py:289`, branch `simulation.py:392`, guard `simulation.py:511`

`GREEDY`/`ALTRUISTIC` use `auto()` (values `1`/`2`), but the class also defines
`__init__(self, discriminate)`. For an `Enum`, the member value is passed to `__init__`, so
`discriminate` is `1`/`2` — never `False`.

Consequences:
- `Carrier.setup` always takes the `if bct.discriminate:` branch → `DiscriminatingGreedyCollector`
  is the only collector ever built; `NonDiscriminatingGreedyCollector` is **dead code**.
- The `if not config.batch_collector_type.discriminate:` guard at line 511 can never run, so the
  "non-discriminating collector with differing model distributions" validation is silently disabled.
- `ALTRUISTIC` is unhandled in `Carrier.setup` → `self.batch_collector` is never set → `AttributeError`.

**Fix:** carry the boolean explicitly (e.g. `GREEDY = (False,)` / `ALTRUISTIC = (True,)`, or a
separate mapping), branch `Carrier.setup` on it, and handle or remove `ALTRUISTIC`.

> Fixing this is the prerequisite for the rest — it changes which code paths actually run and will
> surface L7/L8/L9 in practice.

### L2 — Breakdown aborts only every *other* active carrier
**Where:** loop `simulation.py:245`, removal `simulation.py:403` *(independently flagged by all 6 lenses)*

`Breakdown.process` does `for carrier in self.task.active_carriers:` while `Carrier.abort()`
calls `self.task.active_carriers.remove(self)`. Mutating a list during iteration skips elements,
so roughly half the carriers are never aborted. The skipped carriers keep processing **through**
the breakdown, place pieces into the normal outlets instead of the lifeboat outlets, never release
their slots/operators, and remain as zombies in `active_carriers`.

**Fix:** iterate a snapshot — `for carrier in list(self.task.active_carriers): carrier.abort(self.outlets)`.

### L3 — PER_TASK operators leak on every breakdown reboot
**Where:** request `simulation.py:561`, breakdown flip `simulation.py:244`

With `operators_scope == PER_TASK`, operators are requested inside `if not self.started_up:` and
**never released** (the `Task.process` loop never returns, so auto-release never fires). A breakdown
sets `started_up = False`; on loop re-entry the operators are requested again, and claims accumulate
(confirmed `3 → 5`). After K breakdowns the task holds (K+1)× the operators → starves the shared
pool → eventual deadlock.

**Fix:** release the PER_TASK operators on teardown (breakdown/shutdown), or skip the re-request
when they are already held.

### L4 — Carrier crashes when `deadline - duration` is in the past
**Where:** collector wait `simulation.py:414`, requests `simulation.py:441` / `simulation.py:448`, late assert `simulation.py:455`

The collector is bounded only by `fail_at=deadline` (duration is unknown at that point, by design).
Once `duration` is sampled, if collection finished in the window `(deadline - duration, deadline)`,
lines 441/448 receive a `fail_at` earlier than `now` → **uncaught `ValueError` aborts the whole
simulation**. The PER_BATCH `restock` hold at line 432 advances `now` further, widening the window.
The guarding `assert` at line 455 executes only *after* the crash point.

**Fix:** before requesting, check `if env.now() + duration > deadline:` and freeze/abort instead of
issuing the request (or use a `cap_now`-safe value and handle the failure).

### L5 — A breakdown overlapping startup is lost
**Where:** `simulation.py:244`, `simulation.py:484`, startup wait `simulation.py:549`

`started_up` is a plain bool and a breakdown never cancels an in-flight `TaskStarter`. If a breakdown
fires mid-startup, `TaskStarter` completes and sets `started_up = True`; the Task — which does not
re-check `is_in_breakdown` after `self.wait(task_starter.done)` — then dispatches a carrier while the
machine is supposed to be down.

**Fix:** re-check the breakdown/shutdown gate after the startup wait (`continue` if set), and/or have
`TaskStarter` verify `is_in_breakdown` is `False` before setting `started_up = True`.

### L6 — Aborting a carrier mid-order strands `active_order = True` forever
**Where:** restock `simulation.py:113`–`simulation.py:122`, carrier call `simulation.py:432`, abort cancel `simulation.py:407`

`restock` sets `active_order = True`, holds `order_duration` on the carrier, and only **after** the
hold creates the `Delivery`. If a breakdown cancels the carrier during that hold, the `Delivery` is
never created and `active_order` is never cleared → that `RestockableResource` **never reorders
again** (every future `restock` short-circuits on `if not self.active_order`).

**Fix:** create the `Delivery` before the wait, or run the order on an independent component (so the
demander's cancellation can't strand it), or reset `active_order` in `abort()`.

### L7 — `DiscriminatingGreedyCollector` ignores `contiguous_carriers`
**Where:** non-discriminating block `simulation.py:328`, discriminating path `simulation.py:337`–`simulation.py:371`

Only `NonDiscriminatingGreedyCollector` reserves the remainder slots up to `max_carrier_capacity`
when `contiguous_carriers` is `False`. The discriminating collector has no equivalent block, so it
under-reserves: non-contiguous carriers don't occupy a full footprint, letting more carriers pack
into `max_capacity` than physically fit. (Combined with L1, this means `contiguous_carriers=False`
is currently a no-op everywhere.)

**Fix:** mirror the remainder-claim block in the discriminating collector before the handoff.

---

## Medium — real but conditional

### L8 — Slot handoff race: `release` → reclaim with `fail_delay=0` + `assert`
**Where:** release `simulation.py:333` / `simulation.py:370`, reclaim + assert `simulation.py:420`

The collector frees its `vacant_slots` claim and the carrier instantly re-claims the same count with
`fail_delay=0`, guarded by `assert not self.failed()`. Confirmed mechanism (repro #5): a requester
already queued on `vacant_slots` wins the freed slots before the carrier's re-request, so the assert
fires and crashes the run. Latent in the strict one-carrier-at-a-time flow, but reachable under
contention (`min_carriers > 1` or tight capacity).

**Fix:** transfer the claim from collector to carrier without round-tripping through the shared pool
(single owner), or replace the `fail_delay=0` + assert with a blocking request and real handling.

### L9 — No validation of capacity invariants
**Where:** collector requests `simulation.py:309` / `simulation.py:340`, remainder `simulation.py:329`

`Task.setup` never checks the relationships between `min_carrier_capacity`, `max_carrier_capacity`,
`max_capacity`, and `min_carriers`. Misconfigurations fail silently or hard:
- `max_capacity < min_carriers * min_carrier_capacity` → accumulating collectors deadlock.
- `max_carrier_capacity < min_carrier_capacity` → `remainder` is negative → `request(negative)` → `ValueError`.
- `max_carrier_capacity > max_capacity` → a single carrier can never fill.

**Fix:** add guards in `Task.setup`: `min_carrier_capacity <= max_carrier_capacity <= max_capacity`
and `max_capacity >= min_carriers * min_carrier_capacity` (use `max_carrier_capacity` for the
non-contiguous case). Raise `ValueError` on violation.

### L10 — Discriminating collector can block forever on a starved focus model
**Where:** `simulation.py:354`–`simulation.py:357`

After choosing `focus_on`, the collector requires `min_carrier_capacity` pieces of **exactly** that
model via a `from_store` with no `fail_delay`. If fewer than `min_carrier_capacity` of that model ever
arrive, it stalls permanently — even while other valid models pile up in the inlets.

**Fix:** re-select `focus_on` on starvation, or give the fill `from_store` a deadline + fallback to
another present model. Decide block-vs-refocus explicitly.

---

## Low / design decisions

### L11 — `next_shutdown` assumes sorted, non-overlapping intervals
**Where:** `simulation.py:259`–`simulation.py:263`

It returns the first list element with `end > now()`, not the chronologically next interval. An
unsorted/overlapping `intervals` list silently yields wrong deadlines (which drive every `fail_at`).

**Fix:** `self.intervals = sorted(intervals, key=lambda i: i.start)` in setup, and/or validate
non-overlapping.

### L12 — Breakdown hazard accrues during planned shutdowns
**Where:** `simulation.py:221`–`simulation.py:242`

The non-homogeneous Poisson integral is started before a shutdown and is not paused while the task
is down, so planned downtime still consumes time toward the next failure. The `continue` path on
hitting a shutdown also discards the partially-accumulated hazard and resamples from scratch. Both
are modeling-semantics choices to decide deliberately.

### Minor
- **Mixed RNG streams** — `Router` uses `np.random`, `Breakdown` uses `env.random`. Both are seeded,
  so runs stay reproducible; it's only a hazard if you ever reseed/parallelize one stream. Not a
  correctness bug as-is.
- **`place()` has no fallback for a finite-capacity `Buffer`** — `can_take` checks only model
  validity, so `piece.enter(buffer)` could overflow a bounded store and crash. Latent: `Buffer`
  defaults to unbounded.
- **Greedy top-up takes a piece before securing its slot** (`simulation.py:317` / `simulation.py:359`)
  — between the `available_quantity() > 0` check and the slot `request`, another requester could take
  the last slot, leaving the collector blocked while holding a pulled piece. Latent under the current
  single-active-collector flow.

---

## Explicitly dismissed (not bugs)

- **`restock`'s deadline check ignores `delivery_duration`** — intended design: `order_duration` is
  the operator's "go place the order" time; the delivery arriving after a shutdown is acceptable, so
  the check correctly guards only order *placement*.
- **"restock never refills" (one finder's claim)** — incorrect. The PER_TASK restock block (line 571)
  is outside the `if not started_up:` block and runs every loop iteration, so stock does get a
  per-batch reorder opportunity. The only real concern there is the conceptual coupling of restock to
  `operators_scope` rather than `resources_scope`, not a depletion deadlock.
