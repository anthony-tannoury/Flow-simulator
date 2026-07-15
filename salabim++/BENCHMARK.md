# GigaFab: the salabim++ vs Python salabim benchmark

`benchmark/gigafab.{py,cpp}` is one model written twice — a deliberately
complicated factory that exercises nearly every mechanic of the library at
once, at scale:

* a generator streams **orders** (exponential inter-arrival times); every
  order spawns 1–4 **lots** (so components create components, in the
  millions)
* **station A** — a `Resource` with capacity 3, Gamma service times
* **station B** — five worker components in the bank-style
  passivate/activate pattern with a FIFO `Queue` … and a breakdown generator
  that randomly **interrupts** working workers, claims a repairman
  `Resource`, and **resumes** them (remaining service time preserved)
* **inspection** — a capacity-2 `Resource` requested with `fail_delay=6`:
  impatient lots renege on timeout (`failed()`)
* **quality control** — a `Pdf` distribution sends ~10% of lots back to
  station B for rework (one rework max, then scrap)
* finished lots put **themselves** into a bounded **Store** (`to_store`,
  blocking when full); **trucks** batch-collect 8 lots each
  (`from_store`), burn 40 fuel from an **anonymous Resource** depot and
  haul them away
* a **State**-triggered refinery refills the depot when a truck notices fuel
  running low (`wait` / `set` / `reset`)

Both versions run with the same default seed (1234567). Because
`sim::PythonRandom` reproduces CPython's `random.Random` bit for bit, the two
programs must — and do — produce **byte-identical output**: every counter and
every statistics block, verified with `diff` at every scale tested, up to
**4.2 million spawned components**.

## Results

macOS / Apple Silicon (arm64), single-threaded. Apple clang 21 `-O2`,
CPython 3.14.6, salabim 26.0.8 (generator mode). Times are wall clock for the
whole process (`/usr/bin/time -l`), best of 3 for C++; RSS is peak resident
memory. "Components" is the number of simulation objects spawned during the
run (orders + lots + the fixed machinery).

| horizon T | components spawned | Python salabim | salabim++ (C++) | **speedup** | Python peak RSS | C++ peak RSS | output |
| --------: | -----------------: | -------------: | --------------: | ----------: | --------------: | -----------: | :----- |
|    20,000 |             34,636 |          4.9 s |          0.10 s |   **~49×**  |          — | — | identical |
|   600,000 |          1,048,413 |        176.7 s |          3.96 s | **44.6×**   |        1.00 GB |      1.32 GB | identical |
| 2,400,000 |          4,203,749 |        758.7 s (12 m 39 s) | 19.7 s | **38.6×** |        3.03 GB |      3.91 GB | identical |
| 6,000,000 |         10,512,151 |   ~32 min (extrapolated) |  55.4 s | —           |              — |      6.39 GB | —      |

Highlights of the 2.4 M-time-unit run (identical in both languages):

```text
orders 1201006
lots 3002732
produced 2370709
scrapped 23626
reneged 6287
trips 296336
repairs 3262
components 4203749
```

…followed by the full `print_statistics()` blocks for the station-B queue,
station A, the outbox store and the fuel depot — byte-for-byte equal.

## Notes

* **Speedup is a stable ~40–45×** across scales. The mild decline at the
  biggest sizes is allocator pressure: salabim++ keeps every terminated
  component addressable (the environment owns them, pointers stay valid),
  which is also why its RSS is slightly above Python's (CPython
  garbage-collects finished components; its RSS is dominated by the
  monitors' tally arrays instead).
* Python runs in salabim's *generator* mode (`yieldless(False)`) — the mode
  salabim++'s coroutines mirror. Salabim's default greenlet
  (`yieldless`) mode benchmarks slower than generator mode, so this is the
  conservative comparison.
* Reproduce with:

  ```bash
  cd benchmark
  PYTHON=/path/to/python-with-salabim ./run_benchmark.sh 600000 2400000
  ```
