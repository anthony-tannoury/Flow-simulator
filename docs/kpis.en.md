# The run outputs and KPIs, explained

Every run writes a folder of results: CSV files you can open straight in Excel, plus a folder of charts. This document explains what every number means, how it is measured, and the traps to watch for when you read it.

If you have not yet, read the [simulation guide](simulation.en.md) first. This document uses its vocabulary (piece, task, carrier, buffer, operator, scope, admin) without redefining it. The [Flow Designer guide](flow-designer.en.md) tells you how to produce these outputs; this one tells you how to read them.

Two things up front:

- **Nothing needs to be turned on.** Every metric, for every station and every buffer, is measured on every run.
- **The two engines agree.** Whether you run the Python or the C++ engine, you get the same files with the same columns and the same numbers. The choice of engine is only about speed.

---

## The formats

- **Durations** look like `1h 10m` or `3j 5h 20m` (and `3m 20s` below an hour). Here `j` is days (jours). Internally everything is in simulated minutes; only the display changes.
- **Rates** are percentages, like `8.1%` or `83%`.
- **Instants** (when a piece was created or finished) are real calendar dates, like `05-01-2026 14:05`, counted from the run's start date.
- **Flows** are pieces per day (columns ending in `_j`).

---

## The time cascade: the key to reading everything

Almost every per-station number comes from one way of slicing time, the one in the NF E60-182 standard. You start from the whole calendar and peel off losses, layer by layer:

```
total time (TT)              the whole simulated span
└─ opening time (TO)         the station's schedule (its shifts)
   └─ required time (TR)     TO minus planned stops (maintenance)
      └─ running time (TF)   at least one batch is on the machine
         └─ value-added time   real loading plus processing
            └─ net time (TN)   reconstructed: ideal cycle x pieces produced
```

Two things matter about this.

**Net time is not a clock reading.** It is *reconstructed*: "making what the station made, at nominal pace, should have taken TN minutes." You compare it to the real value-added time (loading plus processing) to get performance. The gap is the pace loss (slow cycles, half-empty batches).

**The ideal cycle time, step by step.** This is the time it *should* take the station to make **one** piece if everything went perfectly: full batch, nominal pace, zero waiting. Concrete example, a baking oven:

- configured bake time: 120 minutes per rack on average,
- loading: 10 minutes,
- maximum rack: 4 pieces.

A full rack costs 120 + 10 = 130 minutes and yields 4 pieces, so `ideal cycle = 130 / 4 = 32.5 minutes per piece`. The nominal pace is its inverse, about 1.85 pieces per hour.

Why "on average"? Because configured durations are random distributions (Normal, Uniform, and so on). To define a stable reference you take their mean (evaluated at time zero if the parameters drift). This is a **reference convention**, not a measurement, exactly like the nominal rate printed on a real machine's spec sheet.

What it is used for: only to build net time (`ideal cycle x produced`), and therefore performance and the OEE. If the oven runs racks of 2 instead of 4, the machine time is the same but net time halves: performance drops, and the OEE shows the loss. Each model has its own duration and batch size, so its own ideal cycle. That is the `tc_ideal` column in `postes_modeles.csv`.

---

## postes.csv, one station per row

This is the big report. Each row is a task.

The `admin` column (yes/no) says whether the station is **administrative** (inspection, waiting, holding, storage) rather than productive. This is just a label, set by the task's admin flag in the designer. It changes nothing in the simulation; it only feeds the `synthese_admin.csv` summary.

### The times (`temps_*`, `arrets_programmes`, `pannes`, `gel`, `mise_en_route`)

- `temps_total`: the whole simulated span.
- `temps_ouverture`: time inside the station's schedule. Measured on the station's internal "in schedule" state, so it is exact even with shifts that cross midnight or land on holidays.
- `arrets_programmes`: the planned stops actually taken. A flexible stop that slid to let a batch finish is counted where it really happened.
- `temps_requis`: opening minus planned stops. This is the OEE denominator.
- `pannes`, `nb_pannes`, `mtbf`, `mttr`: total breakdown time, number of breakdowns, mean time between two breakdown starts, mean repair time. **Trap:** breakdowns are measured across the whole horizon, so one can overlap an off-schedule period, meaning `pannes` can exceed what the cascade suggests. The MTBF only shows once at least two breakdowns have occurred.
- `gel`: time spent "frozen" **during opening hours**. The station froze because it could not finish a batch before a crew left or before a stop. It restarts as soon as the relevant crew returns (not only at the station's next shift), so freeze stays bounded even if the station's shift runs for days. Closure (nights, weekends) is not freeze and is not counted here.
- `mise_en_route`, `nb_mises_en_route`: total time and number of startups (warm-up, setup). The station restarts after each interruption (breakdown, stop) **and at each new shift**. This is the setup time itself (the configured duration), **not** the wait for the startup crew: that wait is an availability loss, counted inside running time below, not here.
- `temps_fonctionnement`: time with at least one active batch on the station. This is the running time (TF) of the cascade.

### The rates (`taux_de_charge` to `tre`)

- `taux_de_charge` = TR / TO. How much of the opening is actually engaged (the rest goes to planned stops).
- `disponibilite` (availability) = TF / TR. When the station was meant to run, did it? Losses here: breakdowns, startups, waiting for the startup crew, freeze, and above all **starvation** (no pieces to work on). A hungry bottleneck station will have low availability. That is real, not a glitch.
- `performance` = TN / (loading time + processing time). When it was running, did it run at nominal pace? Losses: cycles slower than the mean, crew productivity, and above all **incomplete batches** (a rack running with 2 pieces out of 4). The value-added time is summed over **all** batches, not divided by running time, because a station can process several batches **in parallel** (independent carriers, waiting and storage zones). Summing each batch's time keeps performance inside [0, 100%].
- `qualite` (quality) = good / produced. A station's good pieces are the ones its downstream router did not send to scrap. A station with no route to scrap has quality = 1.
- `trs` (OEE) = availability x performance x quality. The headline number, always inside [0, 100%].
- `trg` = OEE x taux_de_charge: like OEE but planned stops count as loss too.
- `tre` = OEE x (TR / TT): the whole calendar counts, even closed nights.

### The production (`pieces_*`, `nb_lancements`, `taille_lot_moyenne`, `cycle_*`, `debit_pieces_j`, `flux_*`)

- `pieces_produites`: pieces deposited at the output by completed batches (batches evacuated by a breakdown or stop do not count; they produced nothing). For a resource task, this is the quantity of material transformed.
- `pieces_bonnes`, `pieces_rebutees`: the split of those pieces by the immediate downstream router's verdict.
- `nb_lancements`: number of completed batches. `taille_lot_moyenne`: their average size. An average size far below the maximum means performance loss.
- `cycle_moyen`, `cycle_p90`, `cycle_max`: a batch's duration, from its creation (start of collection) to depositing its pieces. The p90 reads: "9 out of 10 batches finish in under X minutes."
- `debit_pieces_j`: pieces produced per **day of required time** (the station's real pace when it is supposed to run).
- `flux_entrant_j`: pieces physically taken from the input buffers, per calendar day (pieces re-taken after an evacuation count; this is the physical flow). `flux_sortant_j`: pieces deposited at the output, per calendar day. A station that takes in more than it puts out is accumulating or evacuating.

### The waits (`attente_*`, `temps_collecte`, `temps_chargement`, `temps_traitement`)

This is the bottleneck diagnosis: where does the missing availability go? Each batch labels what it is doing, and the labels are summed:

- `attente_pieces`: upstream starvation, the collector is waiting for pieces.
- `attente_place`: the station is full, no free slot (its max capacity).
- `attente_operateurs`: the requested crew is not available.
- `attente_matiere`: not enough material in stock (reordering delays included).
- `attente_vague`: the batch is ready but waiting for the other batches of its wave (minimum carriers).
- `temps_collecte`: batch assembly time, seen from the batch.
- `temps_chargement`, `temps_traitement`: the productive time, loading then processing.

**Trap:** these columns partly overlap (a batch's `temps_collecte` covers its collector's `attente_pieces` / `attente_place`), and batches can wait in parallel. Do not add them up to recover opening time. Compare them to each other and between stations.

### The hours (`heures_machine`, `heures_main_oeuvre`)

The two workshop-accounting columns. They do **not** follow the same addition rule, and that is on purpose:

- `heures_machine`: the clock time the machine actually works (loading or processing), as a **union** over all batches. A station is one physical machine, so three batches running in parallel for 40 minutes count 40 machine minutes, not 120. Without parallelism, union equals sum. This is **not** running time: running time counts "at least one batch engaged", including that batch's own waits (crew unavailable, material missing), while machine hours keep only the instants the machine loads or processes. So machine hours are always at most running time, and the gap between them is the engaged batches' waits. Startup is not in here; it lives in its own `mise_en_route` column.
- `heures_main_oeuvre`: the operator-minutes reserved for the station by **all** its crews, as a **sum** (operators x duration). Three people working one hour is three labor hours. It covers the loading crew and the per-batch processing crew during their real jobs, the startup crew during warm-up, and a per-task crew across its **whole** posting (from request to release, including the idle time between batches, because that is staff tied to the station). The ratio `heures_main_oeuvre / heures_machine` gives the station's average staffing (people present per running machine hour).

---

## postes_modeles.csv, production per model

For each piece task: the model's ideal cycle time, and the pieces produced, good, and scrapped for that model. This is the detail that feeds net time.

---

## buffers.csv, one buffer per row

- `longueur_moyenne`, `longueur_max`, `longueur_ecart_type`: the queue, weighted by time (a one-minute spike weighs one minute). **A buffer that swells points at the bottleneck just downstream of it.**
- `longueur_finale`: what was left at the end.
- `sejour_moyen`, `sejour_max`: time pieces spent in this buffer (empty for exit and scrap buffers; you never leave those).
- `entrees`, `sorties`: total traffic. A piece taken instantly by a station still counts.
- `flux_entrant_j`, `flux_sortant_j`: the same traffic in pieces per calendar day. Inflow durably above outflow means the buffer swells, means a bottleneck downstream.
- `temps_moyen_entre_arrivees`: simulated span / entries.

---

## operateurs.csv, one operator group per row

- `effectif`: the group size. `temps_poste`: its total posted time (the sum of its shifts over the run).
- `occupation_moyenne`: the average number of operators requisitioned, averaged over the whole run (in and out of shift together).
- `heures_en_poste` / `heures_hors_poste`: operator-minutes requisitioned during / outside the group's shifts (2 operators held 90 minutes = 180). Diagnostic columns: the simulation releases a per-task crew at the end of its shift (even if the station is still waiting for pieces) and on a batch abort, and re-checks the shift fit after a wait for material, so `heures_hors_poste` should stay near zero. With operators constrained by their shift, whatever remains is never real work: it is a restock order (placed at end of shift, it holds the crew to its term) or, without a shift constraint, a batch that legitimately finishes afterward.
- `taux_occupation`: the total requisitioned (`occupation_moyenne` x simulated span) / (`effectif` x `temps_poste`): the share of posted time actually spent requisitioned. Because crews are released at end of shift, it naturally stays under 100% (bar a batch spilling over).
- `occupation_max`: the peak of operators requisitioned at once.

---

## ressources.csv, one material per row

- `capacite`: the resource's capacity.
- `stock_moyen`, `stock_min`, `stock_max`, `stock_final`: the stock level over time (time-weighted mean), its low and high, and what was left at the end.
- `consommation_totale`, `entrees_totales`: total consumed and total replenished over the run.
- `consommation_j`: consumption per calendar day.
- `nb_ruptures`, `temps_rupture`: how many times the stock hit zero, and the total time spent at zero. A material that keeps running dry is starving the tasks that need it; this is where you see it.

---

## flux.csv and flux_modeles.csv, the whole line

- `sorties`, `rebuts`, `taux_rebut`: the global verdict. Thanks to the scrap-aware generator, a scrapped piece is remade, so goals speak in good pieces.
- `debit_sorties_j`: good pieces per day, over the whole run.
- `traversee_*`: the lead time of the pieces that came out, from a piece's creation to its arrival at the exit. Mean, median, p90, max, and the same columns **per model** in `flux_modeles.csv`.
- `encours_moyen`, `encours_max`, `encours_final`: the work in progress (WIP), pieces born but not yet out or scrapped, wherever they are (buffers **and** machines). That is why `encours_final` can exceed the sum of the buffers.
- `flux_modeles.csv`, per model: the generator's `objectif`, `genere` (pieces actually injected, remakes included), exits, scrap, `atteinte` = exits / goal, and the lead times (mean / median / p90 / max) of the model. `objectif` and `atteinte` are only filled in goal mode (the "pieces produced" criterion). In rate mode (the "time" criterion) the generator has no per-model goal and these two stay empty; only `genere` counts the injections.

---

## synthese_admin.csv, administrative versus productive

A table answering "how much of the process goes into administrative stations?". One row per metric, columns for the two groups (admin tasks yes versus no), their shares and their ratio:

- `administratives`, `productives`, `total`: the cumulated value of the metric for each group, and the whole.
- `part_admin`, `part_productif`: each group's share of the total (the two add to 100%). This is the percentage you are after: "administrative stations account for X% of running time."
- `ratio_admin_productif`: the admin / productive ratio of the same metric (0.25 = admin stations weigh a quarter of what productive ones weigh).

The five metrics (rows): number of stations, running time, total cycle time (summed over all batches), machine hours, labor hours.

A typical read: waiting and storage stations marked admin can carry most of the running time (pieces linger there for a long time) while consuming almost no labor hours (nobody watches them). The table quantifies exactly that imbalance.

---

## temps_traversee.csv, one row per piece

The raw detail: piece, model, outcome (`sortie` for exit or `rebut` for scrap), creation date and finish date (real calendar dates), and lead time. This is the file to pivot in Excel for histograms by model or by period.

---

## graphes/, the charts

Every figure exists twice: the PNG, and the CSV of the plotted data (same values, so you can redraw the chart your way). The tree splits by format first, then by type:

```
graphes/
    png/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
    csv/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
```

- `ressources/stock_*`: each resource's stock over time.
- `buffers/longueur_*`: each buffer's length over time.
- `ligne/pieces_en_attente`: the sum of the passage buffers' lengths. `ligne/encours`: pieces born but neither out nor scrapped.
- `postes/occupation_*`: the number of occupied slots (occupied = capacity minus vacant) over time; the station's max capacity is recalled in the title. Note: with fixed-footprint batches (contiguous off), the slots reserved by a started batch count.
- `operateurs/disponibles_*`: each team's free operators over time (max size in the title, 0 off-schedule by construction).
- `modeles/trajectoires_<model>`: the model's journey, one bar per distinct trajectory observed (pieces of one model can follow different paths: reworks, holds), sorted most frequent to rarest, with `n` and its share. Each bar stacks the steps in order; a segment's length is the **average** time spent at that step (blue = waiting in a buffer, orange = a station). You see at a glance where the model loses its time, branch by branch. Only pieces that reached an end (exit or scrap) count; the exact detail is in the CSV.
- `modeles/production`, per model: in goal mode, three bars (generator goal, pieces generated including remakes, pieces produced at the exit); in rate mode, two bars only (generated and produced), since the generator has no goal.

On large runs, very long time series are thinned when the chart data is written, keeping the shape (the peaks and dips are preserved) while dropping some of the exact intermediate points. This only affects the plotted `graph_data`, never the numbers in the CSV reports.

---

## run.csv, the run's identity card

The source file, the start and **end** dates of the simulated calendar, the simulated span, the random seed, the generation date, the **compute time** (the real machine time the run took), and the chosen **stopping criterion** with its parameters (`critere_arret` = ByTime or ByPiecesProduced, `critere_details` = its settings). Two runs with the same seed and the same file produce exactly the same CSVs.

---

## A reading order that works

When you open a results folder and something looks off, this order usually gets you to the cause fastest:

1. **run.csv:** did the run even finish the way you expected (goal reached, or stopped on time or timeout)?
2. **flux.csv:** the headline totals, pieces out and scrap rate and WIP.
3. **buffers.csv:** find the buffer that swelled. The bottleneck is the station right after it.
4. **postes.csv for that station:** read its waits. `attente_operateurs` points at staffing, `attente_pieces` at upstream, `attente_matiere` at a resource, `attente_place` at its own capacity.
5. **operateurs.csv or ressources.csv:** confirm the shortage the waits pointed to.

Almost every question about a run resolves along that chain.
