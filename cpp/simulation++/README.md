# simulation++ — the factory simulation in C++

`simulation.hpp` is a single-header C++20 translation of the Python simulation
(`simulation/*.py`) onto [salabim++](../salabim++/salabim.hpp). Same classes,
same logic, same validation messages, same event mechanics — the two codebases
read side by side, module by module.

Runs are seeded and deterministic: the same seed gives the same C++ run every
time. They do **not** reproduce Python runs draw for draw — the two engines
draw their own random numbers, so a C++ run is another sample of the same
model, the way a different seed would be. Expect the final counts of the two
to land close together, not to be equal.

## Speed

The scaled benchmark in `tests/` (20,000 pieces through generator → task →
router/scrap → task → exit, with shifts, operators and productivity draws):

| | time | result |
|---|---|---|
| Python 3.13 + salabim 26.0.8 (`bench.py`) | 84 s | 17,990 exits + 2,010 scrap |
| C++ clang 18 `-O2` (`bench.cpp`) | **0.60 s** | 18,040 exits + 1,960 scrap |

**≈ 140× faster** — and the two result columns are exactly the "close but not
equal" promised above: same model, same seed, different draws.

## Building

```bash
clang++ -std=c++20 -O2 -I../salabim++ -I. my_scenario.cpp -o my_scenario
```

* **Use clang.** GCC 13 has an internal compiler error on braced initializer
  lists inside `co_await` (`co_await wait({{state, false}})`), which this code
  uses everywhere. Clang 18 compiles it fine.

## How the Python maps to C++

| Python | C++ |
|---|---|
| `import simulation` (module init) | `simulation::init(seed)` — creates `env`, seeds the stream, resets class counters |
| `np.random.choice(n, p=probs)` | `weighted_choice(probs)` — cumulative-probability pick on `sim::random_stream()` |
| `env.random` (module `random`) | `sim::random_stream()` (used by `FailureRate`) |
| `Distribution(sim.Uniform, 8, 12)` | `distribution(DistType::Uniform, {8, 12})` |
| callable distribution params | `Param(Linear::generate(...))` (`std::function<double(double)>`) |
| `Interval(a, b)` (shared, mutated) | `interval(a, b)` → `IntervalPtr` (`shared_ptr` = Python object identity) |
| `dict[Model, ...]` | `std::vector<std::pair<Model*, ...>>` (insertion-ordered, pointer identity) |
| dataclass configs | `TaskConfig` / `PieceTaskConfig` / `ResourceTaskConfig` structs, filled field by field |
| `PieceTask(config=..., inlets=..., outlets=...)` | `sim::make<PieceTask>({}, config, inlets, outlets)` |
| `Buffer(...)`, `OperatorGroup(...)`, `Router(...)` (non-components) | plain `new Buffer(...)`, `new OperatorGroup(...)`, `new Router(...)` |
| blocking helper method | `sim::Process` coroutine + `co_await call(helper())` (salabim++ sub-processes) |
| helper return value | out-parameter |
| `ValueError("...")` | `std::invalid_argument` with the same text |
| `self.cancel()` on self (kills the greenlet) | `cancel()` throws through the coroutine chain — code after it never runs, same as Python |

Class-for-class contents: ables, component (request/release hooks: shave +
trigger), interval, helpers, function_generator (`Linear`, `ExponentialFn`*,
`Bathtub`), sampler (`Distribution`, `FailureRate`, `Bounded`), shift_manager
(incl. `minutes_between`, `generate_weekly_shifts`, `generate_custom_shifts`,
`generate_periodic_shutdown` on `std::chrono`), piece, outlet (Buffer/Router
with freeloader), resource (lifespan/ExpiryManager, RestockableResource/
Delivery), operator (OperatorGroup/Alternative), protocols, interrupters
(Breakdown, Flexible/NonFlexibleShutdowns), task, piece_task (all four
collectors), resource_task (greedy/altruistic), judgement_day (ByTime,
ByPiecesProduced, SimulationStopper).

*`Exponential` the function generator is `ExponentialFn` in C++ — the name
clashes with `sim::Exponential`.

## Tests

`tests/` contains the same scenarios written twice — once against
`simulation/` (Python), once against `simulation.hpp` — with the same seed.
Each prints a `=== FINAL STATE ===` summary on stderr; run a pair and the
numbers should land close together (they will not be equal — see above).

* **scenario1** — generator (2 models, 2 shifts) → buffer → discriminating
  greedy task with operators/productivity → router (10% scrap) →
  non-discriminating greedy task → exit; ByTime stopper.
* **scenario2** — model hierarchy, altruistic collectors, greedy ResourceTask
  transforming raw materials into an intermediate consumed downstream,
  RestockableResources (order/delivery), resource lifespan expiry, a
  FailureRate/Bathtub breakdown and an Exponential breakdown, flexible and
  non-flexible shutdowns, operator alternatives, PER_TASK and PER_UNIT
  scopes, time-varying probabilities and durations, ByPiecesProduced stopper.
* **scenario3** — midnight-crossing weekly shifts built by
  `generate_weekly_shifts` from minutes-of-day pairs, plus touching-interval
  merging in `HasShifts`/`IntervalWaiter` and in the shutdown intervals.
* **bench** — scenario1 scaled to 20,000 pieces; time both to compare.

```bash
cd simulation++/tests
PYTHONPATH=../.. python3 scenario1.py          # Python (needs salabim + numpy)
clang++ -std=c++20 -O2 -I../../salabim++ -I.. scenario1.cpp -o s1 && ./s1
```

Deliberately mimicked Python quirks (do not "fix" these in one language only):

* An `ExpiryManager` whose own replenish request triggers a `shave()` that
  cancels it stays in `expiry_managers` forever (Python's self-cancel skips
  the `remove`); the C++ reproduces this via the non-returning `cancel()`.
* Both engines crash the same way (Python: `ValueError: scheduled time ...
  before now`, C++: `sim::SalabimError` with the same message) when a
  collector's timeout deadline ends up in the past — e.g. a piece generator
  downtime gap longer than a task timeout. Since the draws differ, a
  borderline config may crash in one language and not the other.
* Config-validation **error precedence** can differ on invalid setups (C++
  base classes validate before constructor bodies; Python setup validates
  first). Valid configs behave the same way.

## What changed in salabim++ for this port

Documented in [salabim++/README.md](../salabim++/README.md#sub-processes-call):
sub-process support (`call`), vector overloads for `request`/`wait`/
`from_store`, `from_store`/`to_store` default `urgent=True`, the deferred
anonymous-resource re-scan, and non-returning self-`cancel()`. Each fix was
found by diffing C++ traces against Python during development.

## Next step

The Python JSON parser (`parser/`) instantiates these same classes from the
flow-designer export; once it is stable it can be translated onto this header
the same way to bridge the designer directly to the C++ engine.
