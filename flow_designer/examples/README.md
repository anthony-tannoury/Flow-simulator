# Flow-designer clean-JSON export

`sample_flow.json` is a complete, round-trip-verified export produced by the flow
designer. It is the contract a JSON→simulation loader consumes. Node `id`s here are
human-readable (`weld`, `router`, …); the live editor emits opaque uids instead, but
the shapes are identical.

The example models one line: `Bodies In` (generator) → `Raw Bodies` (buffer) →
`Weld & Assemble` (piece task) → `QC Router` → `Passed QC` (the `EXIT` buffer) /
`Scrap` (a `SCRAP` buffer, the router's freeloader), with a `Paint Line` resource
task, a flexible `Scheduled Maint.` shutdown, a bathtub `Weld-head Jam` breakdown, a
monitored raw buffer, and a `ByPiecesProduced` stopping criterion.

## Top level

```
{ "editor": {...},
  "models":    [ {name, parent} ],            # parent is null for roots
  "resources": [ <resource> ],                # registries: referenced BY NAME in nodes
  "operators": [ <operator> ],
  "shifts":    [ <shift> ],
  "stopping_criterion": <criterion>,          # when the run ends (may be {} if unset)
  "nodes":     [ <node> ],
  "connections": [ {from_node, from_kind, from_port, to_node, to_kind, to_port} ],
  "backdrops": [ {id, title, nodes, position, width, height} ] }
```

`connections` lists every wire explicitly. The same information is mirrored on the
nodes as id-lists (`bufs_in`, `outlets`, `task`, …) — use whichever is convenient.

## Distributions and functions of time

Every distribution is `{"dist_type": <name>, "params": { <param>: <time-fn> }}`.
Each parameter is itself a **function of time**, so any of them can vary:

```
constant:     {"kind": "constant",    "value": v}
linear:       {"kind": "linear",      "x1","y1","x2","y2"}          # line through 2 pts
exponential:  {"kind": "exponential", "x1","y1","x2","y2","limit"}
step:         {"kind": "step",        "x1","y1","x2","y2","step_size"}
```

`dist_type` is one of `Constant, Uniform, Normal, Exponential, Triangular, LogNormal`
(a card's dropdown). Example: a drifting mean —
`{"dist_type":"Normal","params":{"mean":{"kind":"linear","x1":0,"y1":6,"x2":20000,"y2":8},"std":{"kind":"constant","value":1}}}`.

## Registry entries

```
resource: {name, restockable, lifespan (number|"inf"), max_capacity, initial_capacity,
           # only when restockable:
           order_duration:<dist>, delivery_duration:<dist>, threshold}
operator: {name, capacity (int), productivity:<dist>, shifts:[shift_name]}
shift:    {name, days:[7 × {working:bool, intervals:[{start,end}]}]  # Mon..Sun, minutes from midnight
           days_off:[int day numbers], horizon:{start,end}}          # in days
```

All times in the JSON are raw minutes (the simulation's `Time(h, m) = 60*h + m` unit); the
designer edits them as hours + minutes (shift intervals, shutdown intervals, stopping times).

## Nodes (by `kind`)

Shared task fields (both `Task` and `ResourceTask`): `startup_duration`,
`loading_duration` (`<dist>`); `operators`, `loading_operators`, `startup_operators`
as **OR-of-ANDs** `[[{operator,count}], …]` (any one inner group satisfies it, and all
`(operator,count)` in a group are needed together — and all operators in a group must
share the same productivity); `task_shifts:[name]`; `policies`
(five entries below); `operator_scope` ∈ {PER_BATCH, PER_TASK}; `resource_scope` ∈
{PER_UNIT, PER_BATCH}; `min_carriers`, `max_capacity`, `contiguous_carriers`,
`independent_carriers`, `timeout`, `priority` (integer in `[0,10]`).

A **non-discriminating** piece-task `collector_type` requires every model in
`models_configs` to share the same `duration`, `min_carrier_capacity`, and
`max_carrier_capacity`. A generator's / piece-task's output buffers must accept
**disjoint** model sets and together cover every model it emits.

- **PieceGenerator**: `models_goals:[{model,goal}]`, `shifts:[name]`, `outlets:[bufferId]`.
- **Buffer**: `valid_models:[modelName]`, `capacity` (number|`"inf"`),
  `buffer_type` ∈ {`PASSAGE`, `SCRAP`, `EXIT`}, `monitor:{<stat>: bool}`,
  `inputs_from`, `outputs_to`.
  A `SCRAP` buffer returns its pieces to the generator (so their goals are re-made);
  there must be exactly one `EXIT` buffer (the loader deduces it, e.g. for the
  `ByPiecesProduced` criterion below). `monitor` toggles per-buffer statistics
  (`avg_length, max_length, length_std, current_length, avg_stay, max_stay,
  avg_time_before_arrival, throughput`) — attach a monitor iff any is `true`.
- **Router**: `inputs_from`, `buffer_probs:[{buffer:bufferId, probability:<time-fn>|null}]`
  (per connected buffer; probabilities are validated to sum to 1 at sample time).
  At most one buffer may have `probability: null` — the **freeloader**, whose
  probability is `1 − sum(others)`.
- **Task**: `models_configs:[{model, duration:<dist>, resources:[{resource,value}],
  min_carrier_capacity, max_carrier_capacity}]`, `collector_type`
  (`{NON_,}DISCRIMINATING_{GREEDY,ALTRUISTIC}`), plus the shared fields and
  `bufs_in`, `bufs_out`, `shutdowns`, `breakdowns`.
- **ResourceTask**: `non_transformed_resources:[{resource,value}]` (quantity consumed),
  `transformed_resources:[{resource,proportion,salvageable}]` (proportions are treated
  as probabilities — they must be in `[0,1]` and **sum to 1**),
  `resources_out:[{resource, distribution:<dist>, lowerbound, upperbound}]`
  (each output distribution must be bounded in `[0, +inf[` — `lowerbound ≥ 0` and a
  **finite** `upperbound`), `duration:<dist>`, `resource_collector_type` ∈ {GREEDY,
  ALTRUISTIC}, `min_carrier_capacity`, `max_carrier_capacity`, plus the shared fields,
  `shutdowns`, `breakdowns`.
- **Shutdowns**: `shutdown_type` ∈ {NON_FLEXIBLE, FLEXIBLE}, `intervals:[{start,end}]`
  (each `start ≤ end`; intervals must be pairwise disjoint and non-touching).
- **Breakdown**: `task:<taskId>`, `mttr:<dist>`, `outlets:[bufferId]`, and `mtbf` either
  `{"mode":"distribution","distribution":<dist>}` or
  `{"mode":"bathtub", a, tau, c, beta, eta, tolerance, max_iters}`.
  A breakdown on a **piece** task **must** have `outlets` (lifeboats for in-progress
  pieces); a breakdown on a **resource** task must have **none**.

Monitoring is no longer a separate node — it lives on each **Buffer** (`monitor`, above).

### Policies

`policies` has five keys, each `{"type": <ClassName>}`:

| key | choices |
|---|---|
| `pending_carriers_pre_flexible_shutdowns` | `AbortPendingCarriers`, `WaitForCarriers`, `AbortOrWaitForCarriers` |
| `pending_carrier_pre_task_shift_end` | same three |
| `operator_shift_constraint` | `ConstrainedByShift`, `NotConstrainedByShift`, `PartiallyConstrainedByShift` |
| `task_shift_constraint` | `ConstrainedByShift`, `NotConstrainedByShift`, `PartiallyConstrainedByShift` |
| `operators_self_conscious` | `Conscious`, `Unconscious` |

`AbortOrWaitForCarriers` carries an extra `tolerance_fraction` field.
`PartiallyConstrainedByShift` carries an extra `tolerance` field: the operation may
overrun the shift end by at most that many time units.

## Stopping criterion

Top-level `stopping_criterion` says when the run ends. `{}` means unset. One of:

```
{"type": "ByTime",           "time": t}                     # stop at simulation time t
{"type": "ByPiecesProduced", "total": n, "timeout": T}      # stop after n pieces reach the EXIT buffer,
                                                            # or at time T (number|"inf") — whichever first
```

`ByPiecesProduced` does not name the exit buffer: the loader uses the graph's single
`EXIT` buffer (see **Buffer** above).
