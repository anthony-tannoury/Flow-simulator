# The salabim++ tutorial

*Discrete event simulation in C++, the salabim way.*

salabim++ is a C++20 port of [salabim](https://www.salabim.org), the Python
discrete event simulation (DES) package. It follows salabim so closely that
this tutorial can be read side by side with the salabim manual — every
concept, name and behaviour carries over. If you know salabim, you already
know salabim++; if you learn salabim++ here, you also learn salabim.

Everything lives in one header:

```cpp
#include "salabim.hpp"   // namespace sim — like Python's "import salabim as sim"
```

Compile with any C++20 compiler:

```bash
clang++ -std=c++20 -O2 my_model.cpp -o my_model
```

---

## Table of contents

1. [The world view](#1-the-world-view)
2. [A first model](#2-a-first-model)
3. [Components](#3-components)
4. [The bank office, take 1: passivate and activate](#4-the-bank-office-take-1-passivate-and-activate)
5. [Queues](#5-queues)
6. [The bank office, take 2: resources](#6-the-bank-office-take-2-resources)
7. [The bank office, take 3: states](#7-the-bank-office-take-3-states)
8. [Stores](#8-stores)
9. [Distributions and reproducible randomness](#9-distributions-and-reproducible-randomness)
10. [Monitors and statistics](#10-monitors-and-statistics)
11. [Process interaction reference](#11-process-interaction-reference)
12. [Tracing](#12-tracing)
13. [Time units](#13-time-units)
14. [ComponentGenerator](#14-componentgenerator)
15. [Interrupts: a machine shop](#15-interrupts-a-machine-shop)
16. [C++ specifics and pitfalls](#16-c-specifics-and-pitfalls)
17. [Differences from salabim](#17-differences-from-salabim)
18. [API cross-reference](#18-api-cross-reference)

---

## 1. The world view

Like salabim (and Simula, must, Prosim and Tomas before it), salabim++ uses
the **process interaction** world view: a simulation is a set of *components*,
each describing its behaviour over simulated time as a *process* — an
ordinary-looking piece of sequential code that can *hold* (let simulated time
pass), *passivate* (sleep until someone wakes it), *request* capacity from a
resource, *wait* for a state, and so on.

A central **environment** owns the simulation clock and the *event chain*: a
list of components scheduled to become *current* at some future time. Running
the simulation means: pop the earliest event, advance the clock to it, and let
that component execute until it gives control back. Time only ever advances
between events — this is discrete event simulation.

In Python salabim a process is a generator; every process interaction is
`yield`ed:

```python
class Car(sim.Component):
    def process(self):
        while True:
            yield self.hold(1)
```

In C++ a process is a **coroutine** returning `sim::Process`, and every
process interaction is `co_await`ed:

```cpp
struct Car : sim::Component {
    sim::Process process() override {
        while (true) {
            co_await hold(1);
        }
    }
};
```

The mapping is mechanical:

| Python salabim                       | salabim++                              |
| ------------------------------------ | -------------------------------------- |
| `class Car(sim.Component):`          | `struct Car : sim::Component {`        |
| `def process(self):`                 | `sim::Process process() override {`    |
| `yield self.hold(10)`                | `co_await hold(10)`                    |
| `yield self.passivate()`             | `co_await passivate()`                 |
| `yield self.request(clerks)`         | `co_await request(clerks)`             |
| `Car()`                              | `sim::make<Car>()`                     |
| `env.run(till=50)`                   | `env.run(sim::RunOpts{.till = 50})`    |
| keyword arguments                    | designated-initializer option structs  |

That last row is the key idiom: wherever salabim takes keyword arguments,
salabim++ takes a small options struct that you fill with C++20 designated
initializers — `hold(10, {.priority = 1, .urgent = true})` reads almost like
`hold(10, priority=1, urgent=True)`.

## 2. A first model

```cpp
#include "salabim.hpp"

struct Car : sim::Component {
    sim::Process process() override {
        while (true) {
            co_await hold(1);
        }
    }
};

int main() {
    sim::Environment env({.trace = true});
    sim::make<Car>();
    env.run(sim::RunOpts{.till = 5});
}
```

This prints the same trace salabim prints:

```text
line#        time current component    action                               information
------ ---------- -------------------- -----------------------------------  ---------------------
                                       line numbers refers to               01_hello_car.cpp
   30                                  default environment initialize
   30                                  main create
   30       0.000 main                 current
   31                                  car.0 create
   31                                  car.0 activate                       scheduled for 0.000 @   31+ process=process
   32                                  main run +5.000                      scheduled for 5.000 @   32+
   31+      0.000 car.0                current
   24                                  car.0 hold +1.000                    scheduled for 1.000 @   24+
   24+      1.000 car.0                current
   ...
   32+      5.000 main                 current
```

Walk through what happened:

* `sim::Environment env(...)` creates the environment and makes it the
  *default environment* (like salabim, you rarely pass `env` around — new
  objects attach to the default). It also creates the special component
  `main`, which represents your `main()` function's point of view.
* `sim::make<Car>()` creates a component. Because `Car` overrides
  `process()`, the component is immediately *activated*: scheduled to start
  its process now (`at`/`delay` options change that). Its name is derived
  from the class name: `car.0`, then `car.1`, and so on.
* `env.run({.till = 5})` schedules `main` at time 5 and starts the event
  loop. The car becomes current at 0, holds; becomes current at 1, holds; …
  At time 5 `main` becomes current again and `run()` returns.

## 3. Components

### Creating components: `sim::make<T>()`

Components are created with the factory `sim::make<T>(options, ctor-args...)`
— the counterpart of just calling `Customer()` in Python. The environment
owns every component you make; the returned raw pointer stays valid for the
life of the environment.

```cpp
auto* c1 = sim::make<Customer>();                       // Customer()
auto* c2 = sim::make<Customer>({.at = 10});             // Customer(at=10)
auto* c3 = sim::make<Customer>({.delay = 5, .urgent = true});
auto* c4 = sim::make<Customer>({.name = "vip client"}); // explicit name
```

`ComponentOptions` mirrors salabim's `Component.__init__` keywords:

```cpp
struct ComponentOptions {
    std::string name;               // "" -> lowercased class name + "."
    DurationSpec at, delay;         // when to start the process
    std::optional<double> priority; // event scheduling priority (lower = earlier)
    std::optional<bool> urgent;     // in front of equal (time, priority) events
    bool data_component;            // true -> never start process() (salabim process="")
    std::string process_name;       // name shown in the activate trace
    std::string mode;               // initial mode string
    bool suppress_trace, skip_standby;
    Environment* env;               // nullptr -> default environment
};
```

### Names

Naming follows salabim exactly:

* name omitted → lowercased class name plus a period: `customer.` →
  `customer.0`, `customer.1`, …
* a name ending in `.` is serialized from 0, ending in `,` from 1
* any other name is used as is

`name()`, `base_name()` and `sequence_number()` query the pieces.

### Constructor arguments and `setup()`

Where salabim passes extra keyword arguments to `setup()`, salabim++ passes
extra `make` arguments to your constructor:

```python
# python                                   # C++
class Customer(sim.Component):             struct Customer : sim::Component {
    def setup(self, patience):                 double patience;
        self.patience = patience               explicit Customer(double p) : patience(p) {}
                                           };
Customer(patience=30)                      sim::make<Customer>({}, 30.0);
```

(The options struct comes first; pass `{}` when you only have constructor
arguments.) If you also override `void setup()`, it is called — like in
salabim — after the component got its name and its initial activation, so
`env`, `name()` etc. are all usable inside.

### Data components

A component whose class does not override `process()` is a **data
component**: it is not scheduled and just exists (to be put in queues and
stores, to carry attributes). `sim::make<Job>()` where `struct Job :
sim::Component {};` gives `job.0`, `job.1`, … exactly like salabim data
components. You can start a process later with `activate()` — for data
components that have none, that's an error, as in salabim.

### Statuses

At any moment a component is in one of the salabim statuses:

```
data  current  standby  passive  interrupted  scheduled  requesting  waiting
```

Query with `status()` (an enum with exactly these names: `c->status() ==
sim::passive`) or the salabim shortcuts `isdata()`, `iscurrent()`,
`isscheduled()`, `ispassive()`, `isstandby()`, `isinterrupted()`,
`isrequesting()`, `iswaiting()`. Status history is recorded in a level
monitor: `status_monitor()` (so `c->status_monitor().value_duration(sim::passive)`
tells you how long the component was passive).

## 4. The bank office, take 1: passivate and activate

The salabim manual's first real model: customers arrive at a bank with one
clerk. When there is nothing to do the clerk sleeps (`passivate`); an
arriving customer puts itself in the waiting line and wakes the clerk if
needed, then sleeps itself until served. This is the full model
([examples/02_bank_1_clerk.cpp](examples/02_bank_1_clerk.cpp)):

```cpp
#include "salabim.hpp"

sim::Queue* waitingline;
sim::Component* clerk;

struct Clerk : sim::Component {
    sim::Component* customer = nullptr;
    sim::Process process() override {
        while (true) {
            while (waitingline->size() == 0)
                co_await passivate();          // sleep until work arrives
            customer = waitingline->pop();
            co_await hold(30);                 // serve
            customer->activate();              // wake the customer
        }
    }
};

struct Customer : sim::Component {
    sim::Process process() override {
        enter(*waitingline);
        if (clerk->ispassive())
            clerk->activate();                 // wake the clerk
        co_await passivate();                  // wait to be served
    }
};

struct CustomerGenerator : sim::Component {
    sim::Process process() override {
        while (true) {
            sim::make<Customer>();
            co_await hold(sim::Uniform(5, 15).sample());
        }
    }
};

int main() {
    sim::Environment env;
    sim::make<CustomerGenerator>();
    clerk = sim::make<Clerk>();
    sim::Queue wl("waitingline");
    waitingline = &wl;

    env.run(sim::RunOpts{.till = 50000});
    wl.print_statistics();
}
```

Note the *rules of engagement*, identical to salabim:

* Process-interaction calls on **yourself** (the current component) are
  `co_await`ed: `co_await passivate();`
* Calls on **another** component are plain calls: `clerk->activate();`,
  `customer->activate();`. They take effect immediately (the other component
  is rescheduled); your own process continues.

`print_statistics()` at the end prints the same numbers salabim prints —
mean/std/percentiles of the queue length (time-weighted) and of the length of
stay:

```text
Statistics of waitingline at         50000
                                                           all    excl.zero         zero
-------------------------------------------- -------------- ------------ ------------ ------------
Length of waitingline                        duration          50000        31600.9      18399.1
                                             mean                  1.443        2.283
...
```

## 5. Queues

`sim::Queue` is salabim's `Queue`: an ordered container of components with
full statistics.

```cpp
sim::Queue q("waitingline");          // sim.Queue("waitingline")

c->enter(q);                          // c.enter(q)       — at the tail
c->enter_at_head(q);                  //                    at the head
c->enter_sorted(q, priority);         // sorted by priority (lower = closer to head)
c->enter_in_front_of(q, *other);
c->enter_behind(q, *other);
c->leave(q);                          // c.leave(q)
c->leave();                           // leave all (non-internal) queues

q.head();  q.tail();  q.pop();        // pop(): head or nullptr, removed
q.size();  q.empty();  q.contains(c);
q[3];  q[-1];                         // python-style indexing (nullptr if out of range)
q.index(c);                           // -1 if not in queue
q.add(*c); q.add_sorted(*c, prio);    // queue-side spellings of enter*
q.remove(*c); q.clear();

for (sim::Component* c : q) { ... }   // iteration (head to tail; removing the
                                      // current element while iterating is fine)
```

Components can be in any number of queues at once. Per component:
`count(&q)` (membership), `queues()`, `enter_time(q)`, `priority(q)` /
`set_priority(q, prio)`, `successor(q)`, `predecessor(q)`.

Every queue carries four monitors — `length` (level), `length_of_stay`
(tallied on leave), `capacity`, `available_quantity` — plus
`number_of_arrivals` / `number_of_departures` and `arrival_rate()` /
`departure_rate()`. A queue constructed with `{.capacity = 5}` throws
`sim::QueueFullError` when overfilled (salabim's `QueueFullError`).

`print_statistics()`, `print_histograms()`, `print_info()` produce salabim's
output formats verbatim.

## 6. The bank office, take 2: resources

Resources model capacity. Instead of hand-rolling the clerk logic, give the
bank three clerks as a `Resource` with capacity 3
([examples/03_bank_3_clerks_resources.cpp](examples/03_bank_3_clerks_resources.cpp)):

```cpp
sim::Resource clerks("clerks", 3);                  // sim.Resource("clerks", 3)

struct Customer : sim::Component {
    sim::Process process() override {
        co_await request(clerks);                   // yield self.request(clerks)
        co_await hold(30);
        release();                                  // release all claims
    }
};
```

`request` puts the component in the resource's *requesters* queue; when the
request can be honored the component claims the quantity, moves to the
*claimers* queue and is rescheduled. If the process ends while still
claiming, the claims are released automatically (salabim behaviour).

The full request vocabulary:

```cpp
co_await request(r);                        // 1 unit of r
co_await request(r, 2);                     // 2 units
co_await request({{r1, 2}, {r2}});          // 2 of r1 AND 1 of r2 (atomically)
co_await request({{r1, 1}, {r2, 1}}, {.oneof = true});   // r1 OR r2
co_await request({{r, 1, 100}});            // requesters-queue priority 100
co_await request(r, 1, {.fail_delay = 50}); // give up after 50 time units
co_await request(r, 1, {.fail_at = 500});   // or at an absolute time
if (failed()) { ... }                       // did the request time out?
```

Release with `release()` (everything), `release(r)`, `release(r, q)` or
`release({{r1, 1}, {r2}})`.

Honoring works exactly like salabim: requesters are scanned head to tail and
each is honored when *all* its requested quantities fit (`oneof`: any one) —
so a small request may overtake a big one that doesn't fit, unless you create
the resource with `{.honor_only_first = true}` (strict FIFO) or
`{.honor_only_highest_priority = true}`.

### Anonymous resources (levels)

A resource created with `{.anonymous = true}` has no claimers — claimed
quantity is just a level, useful for tanks, stock, energy:

```cpp
sim::Resource tank("tank", 1000, {.anonymous = true, .initial_claimed_quantity = 1000});

co_await request({{tank, 42}});   // take 42 out of the available quantity
tank.release(10);                 // put 10 back (Resource::release, not Component::release)
co_await request({{tank, -42}}); // negative request: put 42 in (like salabim put)
```

### Resource statistics

`capacity`, `claimed_quantity`, `available_quantity` and `occupancy` are
level monitors; calling them gives the current value (`clerks.occupancy()`
like salabim's `clerks.occupancy()`), and `clerks.occupancy.mean()` the
time-weighted mean. `requesters()` and `claimers()` are full `Queue`s with
their own statistics. `set_capacity(c)` changes capacity on the fly (and may
honor pending requests). `print_statistics()` prints the salabim block.

## 7. The bank office, take 3: states

A `State` holds a value; components can *wait* for it
([examples/04_bank_3_clerks_states.cpp](examples/04_bank_3_clerks_states.cpp)):

```cpp
sim::State<bool> worktodo("worktodo");         // sim.State("worktodo")

// clerk:
if (waitingline->size() == 0)
    co_await wait({{worktodo, true, 1.0}});    // yield self.wait((worktodo, True, 1))

// customer:
enter(*waitingline);
worktodo.trigger(true, std::nullopt, 1);       // worktodo.trigger(max=1)
```

`sim::State<T>` is typed — `State<bool>` (the default salabim `False` state),
`State<double>`, `State<std::string>`, … Its API is salabim's:

```cpp
s.set(v);  s.set();          // set value (default: true/1)
s.reset(v); s.reset();       // set value (default: false/0)
s.get();  s();               // current value
s.trigger(v, v_after, max);  // set v, honor up to max waiters, then v_after
s.waiters();                 // Queue of waiting components
s.value_monitor();           // level monitor of the value over time
```

Wait specifications mirror salabim's tuples:

```cpp
co_await wait(s);                              // wait for "truthiness" (== True)
co_await wait({{light, std::string("green")}});// wait for a specific value
co_await wait({{level, [](double v) { return v >= 10; }}});   // predicate
co_await wait({{s1}, {s2}}, {.all = true});    // all instead of any
co_await wait({{s, true, 1.0}});               // waiters-queue priority
co_await wait(s, {.fail_delay = 50}); if (failed()) ...        // timeout
```

(The one salabim feature that cannot cross the language boundary is the
string-eval form `'$ == 3'` — use a lambda.)

## 8. Stores

A `Store` is a queue of items (components) with blocking put/get — salabim's
Store ([examples/07_store_producer_consumer.cpp](examples/07_store_producer_consumer.cpp)):

```cpp
sim::Store buffer("buffer", {.capacity = 5});

// producer
co_await to_store(buffer, *item);        // yield self.to_store(buffer, item)
                                         // blocks while the store is full

// consumer — from_store returns the item through co_await:
sim::Component* item = co_await from_store(buffer);
                                         // item = yield self.from_store(buffer)

// filters:
sim::Component* heavy = co_await from_store(buffer, {.filter = [](sim::Component* c) {
    return static_cast<Item*>(c)->weight > 8.0;
}});

// several stores, timeouts:
co_await from_store({&s1, &s2}, {.fail_delay = 10}); if (failed()) ...
```

`from_store_item()`, `from_store_store()` and `to_store_store()` report the
latest transfer. Stores are queues, so all queue statistics apply.

> **Inherited quirk:** if a *bounded* store simultaneously has a blocked
> `to_store` putter and a *filtered* `from_store` waiter whose filter matches
> the incoming item, salabim's honor logic recurses forever (Python dies with
> `RecursionError`). salabim++ detects the pattern and throws
> `sim::SalabimError` with an explanation. Standard fix: give the filtered
> getter its own (unbounded) store, as in the example.

## 9. Distributions and reproducible randomness

All salabim distributions are available and callable:

```cpp
sim::Exponential iat(10);          // mean 10        (or Exponential(sim::ExpRate{0.1}))
sim::Uniform u(5, 15);
sim::Normal n(10, 2);              // Normal(10, std::nullopt, {.use_gauss = true}) for gauss
sim::Triangular t(1, 10, 3);       // low, high, mode
sim::IntUniform d6(1, 6);
sim::Constant c(42);
sim::Poisson p(4);
sim::Weibull w(2, 1.5);            // scale, shape
sim::Gamma g(2, 3);                // shape, scale
sim::Erlang e(3, 0.5);             // shape, rate
sim::Beta b(2, 5);
sim::Pdf pdf({10, 50, 20, 30, 30, 20});      // values/probabilities interleaved
sim::Cdf cdf({0, 0, 10, 50, 30, 90, 50, 100}); // piecewise-linear cumulative

double x = iat.sample();           // or just iat()
double m = iat.mean();
double y = n.bounded_sample(0, 20);  // resample until within bounds
```

Wherever a duration or time is expected you may pass a number, a
distribution (sampled at that moment) or any callable returning `double` —
salabim's rule:

```cpp
co_await hold(iat);                          // sample the distribution
co_await hold([] { return heavy_math(); });  // call the callable
sim::make<Customer>({.at = iat});            // also in options
```

### Seeded, reproducible randomness

The environment seeds a shared random stream at construction with
`random_seed = 1234567` — the same default as salabim. The stream is a
`sim::Random` (a `std::mt19937_64`): runs are reproducible for a given seed,
but salabim++ draws its own random numbers — a salabim++ model and its
Python twin produce statistically equivalent results, not equal ones.

```cpp
sim::Environment env({.random_seed = 42});     // sim.Environment(random_seed=42)
env.random_seed(1234567);                      // reseed later
sim::Environment env2({.random_seed = sim::seed_no_reseed}); // like seed=""
sim::Environment env3({.random_seed = sim::seed_random});    // like seed="*"

sim::Random my_stream(42);                     // private stream
sim::Exponential iat(10, "", &my_stream);      // distribution on its own stream
```

## 10. Monitors and statistics

`sim::Monitor` is salabim's Monitor, in both flavours:

* **non-level** — you `tally(value [, weight])` observations (e.g. processing
  times);
* **level** — the tallied value *persists over time* and statistics are
  time-weighted (queue length, resource occupancy, state values). Created
  with `{.level = true}`.

```cpp
sim::Monitor waiting_time("waiting time");                     // non-level
sim::Monitor stock("stock level", {.level = true, .initial_tally = 100});

waiting_time.tally(4.5);
stock.tally(97);                    // "the level is 97 from now on"

m.mean();  m.std();  m.minimum();  m.maximum();
m.percentile(90);  m.median();
m.number_of_entries();  m.weight();          // level: m.duration()
m.value_duration(3);                         // time the level was exactly 3
m.bin_number_of_entries(0, 10);
m.xweight();                                 // raw (values, weights/durations)

m.monitor(false);   // suspend recording (an "off" period, like salabim)
m.monitor(true);
m.reset();

m.print_statistics();
m.print_histogram();                                   // autoscaled bins
m.print_histogram({.number_of_bins = 30, .lowerbound = 0, .bin_width = 10});
m.print_histogram({.values = true});                   // per-value durations
auto s = m.print_statistics(true, true, false, /*as_str=*/true);  // to string
```

Every statistic takes an optional `ex0` argument to exclude zero values, and
the printed blocks include the `all / excl.zero / zero` columns exactly as
salabim prints them. Queues, resources and states expose their built-in
monitors (`q.length`, `q.length_of_stay`, `r.occupancy`, `s.value_monitor()`,
`c->status_monitor()`, …) and their own `print_statistics()` /
`print_histograms()` aggregates.

An M/M/1 queue checked against theory, from
[examples/05_mm1_queue.cpp](examples/05_mm1_queue.cpp):

```text
rho (occupancy)     : simulated    0.799   theory    0.800
Lq (queue length)   : simulated    3.158   theory    3.200
Wq (time in queue)  : simulated   31.596   theory   32.000
```

## 11. Process interaction reference

All methods live on `sim::Component`. On the current component use
`co_await`; on another component call them plainly (then they act at once).

### hold

```cpp
co_await hold(10);                                  // duration
co_await hold(10, {.priority = 1, .urgent = true, .mode = "driving"});
co_await hold(sim::HoldOpts{.till = 50});           // absolute time
co_await hold(iat);                                 // sample a distribution
co_await hold(sim::HoldOpts{});                     // hold(): reschedule now
```

### passivate / activate / cancel / standby

```cpp
co_await passivate();               // sleep; scheduled_time becomes inf
other->activate();                  // wake a passive component (continues where it was)
other->activate({.delay = 5});      // ... 5 time units from now
other->activate({.at = 100, .urgent = true});
data_comp->activate();              // data component: (re)start its process()
co_await cancel();                  // become a data component (releases claims)
co_await standby();                 // become current after *every* event
```

`activate` on a requesting/waiting component cancels the request/wait (sets
`failed()`) unless you pass `{.keep_request = true}` / `{.keep_wait = true}`.
A passive component that is activated resumes exactly where it passivated —
that's the fundamental sleep/wake pattern of the bank example. `hold` on a
passive component resumes it after the duration ("wake with a delay").

### interrupt / resume

```cpp
victim->interrupt();                // pause a *scheduled* component; remembers remaining time
victim->interrupt();                // interrupts stack (level 2, 3, ...)
victim->resume();                   // pops one level; at level 0 reschedules
victim->resume({.all = true});      // back to level 0 at once
victim->interrupt_level();  victim->interrupted_status();  victim->remaining_duration();
```

### request / release / wait / stores

Covered in sections [6](#6-the-bank-office-take-2-resources),
[7](#7-the-bank-office-take-3-states) and [8](#8-stores). Common options for
`request`/`wait`/`from_store`/`to_store`: `.fail_at`, `.fail_delay` (timeout →
`failed()` is set), `.mode`, `.urgent`, `.priority` (of the timeout event),
`.request_priority` (position in the requesters/waiters queue), and
`.oneof` / `.all` where applicable.

### Scheduling order

The event list is ordered by `(time, priority, sequence)` — exactly salabim:
lower `priority` first at the same time (default 0; `run()` schedules main at
priority `inf` so everything at the end time still happens); `urgent = true`
puts a component in front of all events with the same time *and* priority
(urgent events among themselves: last in, first out). `standby` components
become current after every event — a busy-wait that is occasionally
invaluable and always expensive, also in salabim.

### Everything else

```cpp
c->name(); c->base_name(); c->sequence_number();
c->status(); c->ispassive(); ... c->ismain();
c->mode();  c->set_mode("phase 2");  c->mode_time();
c->creation_time(); c->scheduled_time(); c->scheduled_priority();
c->failed();  c->remaining_duration();
c->claimed_quantity(&r); c->requested_quantity(&r);
c->claimed_resources(); c->requested_resources(); c->isclaiming(&r); c->isbumped(&r);
c->print_info();
env.now();  env.peek();  env.current_component();  env.main();  env.reset_now();
env.step();                       // advance one event by hand
sim::make<sim::Event>({.at = 30}, [&] { customer->activate(); });  // scheduled lambda
```

## 12. Tracing

`{.trace = true}` at environment construction (or `env.trace(true)` later)
prints every event in salabim's four-column format, including the line
numbers of your source (captured via `std::source_location`):

```text
line#        time current component    action                               information
------ ---------- -------------------- -----------------------------------  ------------------------
    5+      0.000 car.0                current
    7                                  car.0 hold +1.000                    scheduled for 1.000 @    7+
```

* `env.suppress_trace_linenumbers(true)` blanks the line references.
* `component->suppress_trace(true)` hides one component's lines.
* `{ auto s = env.suppress_trace(); ... }` suppresses in a scope (the
  `with env.suppress_trace():` context manager).
* `env.trace_to(&stream)` redirects the trace to any `std::ostream`.
* Standby components would flood the trace; like salabim, their repeat
  "current (standby)" lines are suppressed by default
  (`env.suppress_trace_standby(false)` shows them).

## 13. Time units

Like salabim, the environment can be given a time unit, after which
conversion helpers are available:

```cpp
sim::Environment env({.time_unit = "minutes"});
env.hours(2);          // -> 120 (minutes)
env.seconds(90);       // -> 1.5
env.to_hours(240);     // -> 4
sim::Exponential iat(2, "hours");   // sampled values are converted to env units
```

Units: `years, weeks, days, hours, minutes, seconds, milliseconds,
microseconds` (default `"n/a"` — dimensionless, conversions raise).

## 14. ComponentGenerator

`sim::ComponentGenerator<T>(opts)` creates components for you
([examples/06_component_generator.cpp](examples/06_component_generator.cpp)):

```cpp
sim::ComponentGenerator<Customer>({.iat = sim::Exponential(10)});          // forever
sim::ComponentGenerator<Customer>({.till = 1000, .iat = sim::Uniform(5, 15)});
sim::ComponentGenerator<Customer>({.iat = iat_dist, .number = 100});       // stop after 100
sim::ComponentGenerator<Customer>({.iat = iat_dist, .force_at = true});    // first one at t=0
sim::ComponentGenerator<Customer>({.at = 30, .till = 40, .number = 3});    // uniformly spread
sim::ComponentGenerator<Customer>({.at = 50, .till = 62, .number = 4,
                                   .equidistant = true});                  // 50, 54, 58, 62
sim::ComponentGenerator<Customer>({.iat = iat, .at_end = [] { report(); }});
sim::ComponentGenerator<Customer>({.iat = iat,
                                   .factory = [] { return sim::make<Customer>({}, 42.0); }});
```

The generator itself is a component (named `Customer.generator.0`, salabim
style) and shows up in the trace with `process=do_iat` / `process=do_spread`.

## 15. Interrupts: a machine shop

[examples/08_machine_shop.cpp](examples/08_machine_shop.cpp) shows the
interrupt pattern: a machine processes jobs; a breakdown generator interrupts
it (the remaining processing time is frozen), claims a repairman resource,
and resumes the machine after the repair — the machine finishes the job as if
nothing happened, only later:

```cpp
if (machine->isscheduled()) {      // only a working machine can be interrupted
    machine->interrupt();
    co_await request(*repairman);
    co_await hold(repair_time);
    release();
    machine->resume();             // continues with the remaining time
}
```

## 16. C++ specifics and pitfalls

The library goes far to be salabim, but C++ is not Python. The rules that
matter:

**1. Never forget `co_await` on your own process calls.** `hold(10);`
without `co_await` schedules the component but does *not* give up control —
exactly like forgetting `yield` in Python salabim, and just as wrong. (The
scheduling happens eagerly inside the call; the `co_await` is what returns
control to the scheduler.)

**2. Only `co_await` your own calls.** `co_await other->hold(10)` is
meaningless (like `yield other.hold(10)` in Python) — the plain call
`other->hold(10)` already did the work; awaiting it would suspend *you*
without being scheduled ever again.

**3. Ownership.** Components are owned by the environment and die with it;
keep raw pointers freely. `Environment`, `Queue`, `Resource`, `State`,
`Store`, `Monitor` are value types you create in `main()` (or anywhere that
outlives the run). Create the `Environment` *first* — everything attaches to
the default environment, and destroying the environment while user-owned
queues still exist is fine (both directions are guarded).

**4. Long runs grow memory.** Terminated components are kept (the
environment owns them; their coroutine frames are freed on termination, so
the residue is small). Python salabim relies on garbage collection here; a
million-customer run in salabim++ keeps a million small `Customer` shells
around. If that matters, reuse components (activate them again) instead of
making new ones.

**5. Distribution lifetimes.** `DurationSpec` (what `hold`, `.at`, `.iat`
etc. accept) samples immediately when resolved, and *copies* rvalue
distributions (`.iat = sim::Exponential(10)` is safe). If you pass an lvalue
distribution it is referenced — keep it alive.

**6. Exceptions in processes** propagate out of `env.run()`; the offending
component becomes a data component.

**7. Trace line numbers** point at your `co_await` sites (via
`std::source_location`) — near-salabim, but the numbers themselves obviously
differ from a Python twin's. Use `suppress_trace_linenumbers(true)` when
diffing against Python.

## 17. Differences from salabim

Not ported (mostly Python- or UI-specific):

* animation, video, the `App`/UI machinery (headless simulation only)
* string-conditions in `wait` (`'$ > 5'`) — use lambdas
* Monitor slicing / merging / freezing / `as_dataframe`, period monitors
* datetime mode (`datetime0`), `spec_to_time` on date strings
* preemptive resources are ported (`{.preemptive = true}`, bumping), but not
  combined with multi-resource requests (salabim also forbids that)
* `yieldless` mode: salabim++ *is* generator-style salabim with `co_await` as
  the `yield`
* dynamic process switching by name (`activate(process="other")`): a data
  component's `activate()` restarts `process()`; arbitrary re-pointing isn't
  supported
* `Environment` shortcuts like `env.Queue(...)` — construct directly

Deliberate small deviations:

* `sim::make<T>()` instead of bare construction (C++ needs a factory to know
  the dynamic type before activation) — the only place the API differs
  structurally from salabim.
* Options structs instead of kwargs; `RunOpts{.till = ...}` instead of
  `run(till=...)`.
* `Store` internal requester queues are named after the store's *serialized*
  name (salabim uses the raw constructor argument there).
* `q.size()` for `len(q)`; `q.length` remains the level monitor, as in
  salabim.

Everything else — event ordering, honor policies, statistics formulas, trace
wording, naming, seeds, sampling — matches, and `verification/` proves it
line by line against Python salabim 26.0.8.

## 18. API cross-reference

| salabim (Python)                          | salabim++ (C++)                                        |
| ----------------------------------------- | ------------------------------------------------------ |
| `import salabim as sim`                    | `#include "salabim.hpp"` (namespace `sim`)             |
| `env = sim.Environment(trace=True)`        | `sim::Environment env({.trace = true});`               |
| `sim.Environment(random_seed=42)`          | `sim::Environment env({.random_seed = 42});`           |
| `env.run(till=100)` / `run(duration)` / `run()` | `env.run(sim::RunOpts{.till = 100})` / `env.run(100)` / `env.run()` |
| `env.now()`, `env.peek()`, `env.main()`    | same                                                   |
| `env.current_component()`                  | same                                                   |
| `class X(sim.Component)` + `def process()` | `struct X : sim::Component` + `sim::Process process() override` |
| `X(at=5, delay=2, urgent=True)`            | `sim::make<X>({.at = 5, .delay = 2, .urgent = true})`  |
| `def setup(self, a)` / `X(a=1)`            | constructor: `sim::make<X>({}, 1)`                     |
| `yield self.hold(7, mode="m")`             | `co_await hold(7, {.mode = "m"})`                      |
| `yield self.hold(till=50)`                 | `co_await hold(sim::HoldOpts{.till = 50})`             |
| `yield self.passivate()`                   | `co_await passivate()`                                 |
| `c.activate(delay=3)`                      | `c->activate({.delay = 3})`                            |
| `yield self.standby()`                     | `co_await standby()`                                   |
| `yield self.cancel()`                      | `co_await cancel()`                                    |
| `c.interrupt()` / `c.resume()`             | `c->interrupt()` / `c->resume()`                       |
| `yield self.request((r, 2, prio), oneof=True)` | `co_await request({{r, 2, prio}}, {.oneof = true})` |
| `yield self.request(r, fail_delay=50)`     | `co_await request(r, {.fail_delay = 50})`              |
| `self.release()` / `release((r, 1))`       | `release()` / `release(r, 1)`                          |
| `self.failed()`                            | `failed()`                                             |
| `yield self.wait((s, True, 1))`            | `co_await wait({{s, true, 1.0}})`                      |
| `yield self.wait((s, lambda x, *_: x > 3))`| `co_await wait({{s, [](double x) { return x > 3; }}})` |
| `item = yield self.from_store(store)`      | `sim::Component* item = co_await from_store(store)`    |
| `yield self.to_store(store, item)`         | `co_await to_store(store, *item)`                      |
| `q = sim.Queue("q")`                       | `sim::Queue q("q");`                                   |
| `self.enter(q)` / `len(q)` / `q.pop()`     | `enter(q)` / `q.size()` / `q.pop()`                    |
| `r = sim.Resource("r", 3)`                 | `sim::Resource r("r", 3);`                             |
| `sim.Resource(anonymous=True)`             | `sim::Resource r("r", cap, {.anonymous = true});`      |
| `s = sim.State("s")`                       | `sim::State<bool> s("s");`                             |
| `s.set()` / `s.reset()` / `s.trigger(max=1)` | `s.set()` / `s.reset()` / `s.trigger(true, std::nullopt, 1)` |
| `st = sim.Store("st", capacity=5)`         | `sim::Store st("st", {.capacity = 5});`                |
| `sim.ComponentGenerator(X, iat=d)`         | `sim::ComponentGenerator<X>({.iat = d})`               |
| `sim.Exponential(10)` etc.                 | `sim::Exponential(10)` etc.                            |
| `sim.Monitor("m", level=True)`             | `sim::Monitor m("m", {.level = true});`                |
| `m.print_statistics()` / `print_histogram()` | same                                                 |
| `q.print_statistics()` / `r.print_statistics()` / `s.print_statistics()` | same          |
| `x.print_info()`                           | same                                                   |
| `env.trace(True/False)`                    | `env.trace(true/false)`                                |
| `sim.random_seed(42)`                      | `sim::random_seed(42)`                                 |
| statuses `sim.passive` etc.                | `sim::passive` etc. (`c->status() == sim::passive`)    |

Happy simulating!
