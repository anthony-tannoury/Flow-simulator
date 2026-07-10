# simulation++ — the factory simulation in C++

`simulation.hpp` is a single-header C++20 translation of the Python simulation
(`simulation/*.py`) onto [salabim++](../salabim++/salabim.hpp). It is not "a
C++ version of the same idea" — it is the same simulation: same classes, same
logic, same validation messages, same event ordering, same random numbers.
Run the same scenario with the same seed in both and you get the same trace,
the same pieces in the same buffers in the same order, and both RNG streams
at the same position afterwards.

## Speed

The scaled benchmark in `tests/` (20,000 pieces through generator → task →
router/scrap → task → exit, with shifts, operators and productivity draws):

| | time | result |
|---|---|---|
| Python 3.13 + salabim 26.0.8 | 68.0 s | 17,990 exits + 2,010 scrap |
| C++ (clang 18, `-O2`) | **0.66 s** | byte-identical final state |

**≈ 100× faster**, with the full 340 KB final-state dump (every piece id,
model and buffer position, plus the next draws of both RNG streams) identical.

## Building

```bash
clang++ -std=c++20 -ffp-contract=off -O2 -I../salabim++ -I. my_scenario.cpp -o my_scenario
```

* **Use clang.** GCC 13 has an internal compiler error on braced initializer
  lists inside `co_await` (`co_await wait({{state, false}})`), which this code
  uses everywhere. Clang 18 compiles it fine.
* **Keep `-ffp-contract=off`.** Without it fused multiply-adds change the
  low bits of distribution samples and the runs stop being bit-identical.

## How the Python maps to C++

| Python | C++ |
|---|---|
| `import simulation` (module init) | `simulation::init(seed)` — creates `env`, seeds both streams, resets class counters |
| `np.random.choice(n, p=probs)` | `weighted_choice(probs)` — bit-exact mirror of numpy's legacy `RandomState` (own MT19937 stream, numpy seeding, cdf + searchsorted) |
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
(incl. `minutes_between`, `generate_weekly_shifts`, `generate_custom_shifts`
on `std::chrono`), piece, outlet (Buffer/Router with freeloader), resource
(lifespan/ExpiryManager, RestockableResource/Delivery), operator
(OperatorGroup/Alternative), protocols, interrupters (Breakdown, Flexible/
NonFlexibleShutdowns), task, piece_task (all four collectors), resource_task
(greedy/altruistic), judgement_day (ByTime, ByPiecesProduced,
SimulationStopper).

*`Exponential` the function generator is `ExponentialFn` in C++ — the name
clashes with `sim::Exponential`.

## How equivalence was verified

`tests/` contains twin scenarios written twice — once against `simulation/`
(Python), once against `simulation.hpp` — run with `trace=True` and compared
event for event by `tests/tracediff.py` (only source line numbers and
create-line interleaving are normalized; every event, time, quantity, state
change and honor is compared in order):

* **scenario1** — generator (2 models, 2 shifts) → buffer → discriminating
  greedy task with operators/productivity → router (10% scrap) →
  non-discriminating greedy task → exit; ByTime stopper.
  **3,812 ordered events + 451 creates: identical.**
* **scenario2** — model hierarchy, altruistic collectors, greedy ResourceTask
  transforming raw materials into an intermediate consumed downstream,
  RestockableResources (order/delivery), resource lifespan expiry, a
  FailureRate/Bathtub breakdown and an Exponential breakdown, flexible and
  non-flexible shutdowns, operator alternatives, PER_TASK and PER_UNIT
  scopes, time-varying probabilities and durations, ByPiecesProduced stopper.
  **6,428 ordered events + 729 creates: identical.**
* **bench** — scenario1 scaled to 20,000 pieces, trace off: full final-state
  dump identical, ~100× speedup.

Deliberately mimicked Python quirks (do not "fix" these in one language only):

* An `ExpiryManager` whose own replenish request triggers a `shave()` that
  cancels it stays in `expiry_managers` forever (Python's self-cancel skips
  the `remove`); the C++ reproduces this via the non-returning `cancel()`.
* Scenarios can crash salabim (Python: `ValueError: scheduled time ... before
  now`, C++: `sim::SalabimError` with the same message) when a collector's
  timeout deadline ends up in the past — e.g. a piece generator downtime gap
  longer than a task timeout. Same configs crash the same way in both.
* Config-validation **error precedence** can differ on invalid setups (C++
  base classes validate before constructor bodies; Python setup validates
  first). Valid configs behave identically.

## Running the verification suite

```bash
cd simulation++/tests
# Python twins (need salabim + numpy):
PYTHONPATH=../.. python3 scenario1.py > py1.txt 2> py1_final.txt
PYTHONPATH=../.. python3 scenario2.py > py2.txt 2> py2_final.txt
# C++ twins:
clang++ -std=c++20 -ffp-contract=off -O1 -I../../salabim++ -I.. scenario1.cpp -o s1 && ./s1 > cpp1.txt 2> cpp1_final.txt
clang++ -std=c++20 -ffp-contract=off -O1 -I../../salabim++ -I.. scenario2.cpp -o s2 && ./s2 > cpp2.txt 2> cpp2_final.txt
# compare:
python3 tracediff.py py1.txt cpp1.txt   # -> TRACES EQUIVALENT
python3 tracediff.py py2.txt cpp2.txt   # -> TRACES EQUIVALENT
diff py1_final.txt cpp1_final.txt && diff py2_final.txt cpp2_final.txt
```

## What changed in salabim++ for this port

Documented in [salabim++/README.md](../salabim++/README.md#sub-processes-call):
sub-process support (`call`), vector overloads for `request`/`wait`/
`from_store`, `from_store`/`to_store` default `urgent=True`, the deferred
anonymous-resource re-scan, and non-returning self-`cancel()`. Each fix was
found and confirmed by the trace diff.

## Next step

The Python JSON parser (`json_parser/`) instantiates these same classes from
the flow-designer export; once it is stable it can be translated onto this
header the same way to bridge the designer directly to the C++ engine.
