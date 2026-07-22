# salabim++ — salabim for C++

A single-header C++20 discrete event simulation library that mimics
[salabim](https://www.salabim.org) (Python, v26.0.8) — same world view, same
mechanics, same statistics, same trace format. Runs are seeded and
deterministic: the same model with the same seed gives the same run every
time. salabim++ has its own random streams (`sim::Random`, a
`std::mt19937_64`) — it does not reproduce a Python run's draws.

```cpp
#include "salabim.hpp"

struct Car : sim::Component {
    sim::Process process() override {
        while (true) {
            co_await hold(1);          // salabim: yield self.hold(1)
        }
    }
};

int main() {
    sim::Environment env({.trace = true});
    sim::make<Car>();                  // salabim: Car()
    env.run(sim::RunOpts{.till = 5});  // salabim: env.run(till=5)
}
```

Python generators become C++20 coroutines: `yield self.hold(10)` is
`co_await hold(10)`. Everything else keeps its salabim name and behaviour.

## What's inside

| salabim                       | salabim++                                            |
| ----------------------------- | ---------------------------------------------------- |
| `sim.Environment`             | `sim::Environment` (event chain, run/step, tracing, time units) |
| `sim.Component`               | `sim::Component` + `sim::make<T>()` (hold, passivate, activate, cancel, standby, interrupt/resume, request/release, wait, from_store/to_store, failed, modes, priorities, urgent) |
| `sim.Queue`                   | `sim::Queue` (priority-ordered, full length / length-of-stay statistics) |
| `sim.Resource`                | `sim::Resource` (capacity, anonymous, honor_only_first/highest, preemptive bumping) |
| `sim.State`                   | `sim::State<T>` (set/reset/trigger, predicate waits) |
| `sim.Store`                   | `sim::Store` (bounded, filters)                      |
| `sim.Monitor`                 | `sim::Monitor` (level + non-level, same `print_statistics()` / `print_histogram()` output format) |
| `sim.ComponentGenerator`      | `sim::ComponentGenerator<T>()` (iat / spread / equidistant) |
| distributions                 | `Uniform, Exponential, Normal, Triangular, IntUniform, Constant, Poisson, Weibull, Gamma, Erlang, Beta, Pdf/Pmf, Cdf` — drawing from shared or private `sim::Random` streams |
| `sim.Event`                   | `sim::Event` (scheduled callables)                   |
| trace (`trace=True`)          | same column layout, actions and wording              |

Not ported: animation/UI, video, string-eval wait conditions (use lambdas),
monitor slicing/merging/freezing, datetime mode. See the
[tutorial](TUTORIAL.md#differences) for the complete list.

## Sub-processes (`call`)

Python salabim in **yieldless** mode lets any helper method block
(`self.hold(...)` deep inside an ordinary method call). C++ coroutines cannot
block inside a plain function, so salabim++ adds `call()`: a helper is written
as a `sim::Process` coroutine and executed as a *sub-process* of the current
component — it may `co_await hold/request/wait/from_store` freely, and control
returns to the caller when it finishes:

```cpp
struct Worker : sim::Component {
    sim::Process fetch_and_stamp(double* out) {   // blocking helper
        co_await request(*press);
        co_await hold(3);
        release();
        *out = env->now();
    }
    sim::Process process() override {
        double t;
        co_await call(fetch_and_stamp(&t));       // like a plain call in Python
        co_await hold(1);
    }
};
```

Sub-processes nest arbitrarily, propagate exceptions to the caller, and are
destroyed as a chain when the component is cancelled mid-call. Return values
travel through out-parameters.

Three fidelity fixes that came out of trace-diffing full factory models against
Python salabim during development:

* `from_store`/`to_store` schedule their fail event with `urgent=True`,
  matching Python's default (affects same-time ordering).
* When a request self-honors at call time, the re-scan of anonymous resources
  is deferred until the component resumes — Python's `_push` switches greenlets
  whenever `self` is current, so the tail of `_tryrequest` runs at resumption.
* `cancel()` on the **current** component never returns (it unwinds the
  coroutine chain via an internal exception and the scheduler reaps the frames
  silently, with no `ended` trace) — exactly like Python's cancel, whose
  `_glet.switch()` abandons the greenlet mid-line. Code after a self-cancel
  does not run, including salabim-internal code such as a resource `shave()`
  that cancels the very component executing it.

## Verified against the real thing

The event mechanics were verified against Python salabim during development:
paired Python/C++ models — the salabim sample models (bank with 1 clerk;
3 clerks via resources, states, standby, stores, ComponentGenerator) plus a
mechanics torture test (interrupt/resume, urgent, priorities, oneof and
failing requests, anonymous resources, predicate waits) — were run with the
same seed and compared line by line, event for event. That comparison relied
on a Python-compatible RNG which has since been replaced by the plain
`std::mt19937_64` behind `sim::Random`, so salabim++ no longer reproduces
Python runs draw for draw; the verified event mechanics are unchanged.

As a bonus, salabim++ is about **40–45× faster** than Python salabim —
measured on *GigaFab*, a factory model spanning nearly every library feature,
at 4.2 million spawned components (Python: 12 m 39 s, C++: 19.7 s). Details
in [BENCHMARK.md](BENCHMARK.md).

## Requirements & building

* Any C++20 compiler with coroutines (tested with Apple clang 21 on macOS).
* No dependencies — copy `salabim.hpp` into your project and compile with
  `-std=c++20`.

```bash
clang++ -std=c++20 -O2 my_model.cpp -o my_model
```

or use the provided `CMakeLists.txt`.

## Documentation

* **[TUTORIAL.md](TUTORIAL.md)** — the full tutorial, written to mirror the
  salabim manual, with Python/C++ side-by-side snippets.
* **[examples/](examples/)** — the salabim bank tutorial models, an M/M/1
  queue checked against queueing theory, stores, ComponentGenerator, and a
  machine shop with breakdowns (interrupt/resume).

## License

MIT. Not affiliated with the salabim project — salabim itself is
© Ruud van der Ham and contributors, MIT licensed.
