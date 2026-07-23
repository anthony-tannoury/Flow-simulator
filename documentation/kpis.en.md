# KPI Reference

Every run produces a results folder containing CSV reports (directly readable in Excel) and a set of charts. This document defines every file and every metric: what it measures, how it is computed, and the points requiring care in interpretation.

**Prerequisite:** the [simulation reference](simulation.en.md), whose vocabulary (piece, task, carrier, buffer, operator, scope, admin) is used without redefinition. The [Flow Designer guide](flow-designer.en.md) describes how runs are produced.

General properties of the reports:

- All metrics are collected on every run, for every component. Nothing requires activation.
- The Python and C++ engines produce files with identical structure. Numeric values may differ between engines because their random-number generators differ; at an equal seed the results are statistically comparable but not identical.

---

## The time cascade

```
total time (TT)              the full simulated span
└─ opening time (TO)         the station's shifts
   └─ required time (TR)     TO minus scheduled stops
      └─ running time (TF)   at least one batch active on the station
         └─ value-added time   actual loading plus processing
            └─ net time (TN)   reconstructed: ideal cycle x pieces produced
```

Two definitions require attention.

**Net time is reconstructed, not measured.** TN is the time that producing the station's actual output would have taken at nominal pace. Compared against actual value-added time, it yields the performance rate; the difference represents pace losses (slow cycles, partial batches).

**The ideal cycle time** is the theoretical time to produce one piece under perfect conditions: full batch, nominal pace, no waiting. Example, an oven:

- configured processing time: 120 minutes per batch (mean),
- loading: 10 minutes,
- maximum batch: 4 pieces.

A full batch requires 130 minutes and yields 4 pieces: ideal cycle = 130 / 4 = 32.5 minutes per piece; nominal rate = 60 / 32.5, approximately 1.85 pieces per hour.

Since configured durations are distributions, the reference uses their mean (evaluated at t = 0 when parameters vary over time). This is a reference convention, analogous to the nominal rate on a machine's specification sheet, not a measurement. The ideal cycle serves exclusively to construct TN, and thereby performance and OEE. Each model has its own ideal cycle (`tc_ideal` in `postes_modeles.csv`).

---

## postes.csv, one station per row

The `admin` column (yes/no) reflects the task's admin flag. It has no effect on the simulation; it determines the grouping in `synthese_admin.csv`.

### Time columns (`temps_*`, `arrets_programmes`, `pannes`, `gel`, `mise_en_route`)

- `temps_total`: the full simulated span.
- `temps_ouverture`: time within the station's shifts, measured on the station's internal schedule state (correct across midnight and holidays).
- `arrets_programmes`: scheduled stops as actually taken. A flexible stop that slid to allow batch completion is counted where it occurred.
- `temps_requis`: opening minus scheduled stops. The OEE denominator.
- `pannes`, `nb_pannes`, `mtbf`, `mttr`: total breakdown time, breakdown count, mean time between breakdown starts, mean repair duration.

  > **Note.** Breakdowns are measured over the full horizon and may overlap off-schedule periods; `pannes` can therefore exceed what the cascade suggests. MTBF is reported only from two observed breakdowns onward.

- `gel`: frozen time **within opening hours**. It is the time during which the station could theoretically run but refrains, because it anticipates an imminent stop it could not clear: the end of its own shift, the end of its operators' shift, or a scheduled stop. Unable to finish a batch before that stop, it does not start one. The station resumes when the relevant crew returns, not only at its own next shift, which bounds the frozen time. Closure (nights, weekends) is not counted as frozen time.
- `mise_en_route`, `nb_mises_en_route`: total startup time and startup count. The station restarts after every interruption and at every new shift. This is the configured setup duration itself; waiting for the startup crew is an availability loss within running time, not part of this column.
- `temps_fonctionnement`: the time during which at least one batch is engaged, that is, already collected and loaded, then dispatched into the loading-then-processing pipeline. An engaged batch counts even during its waits for loading operators or materials. The collection waits (`attente_pieces`, `attente_place`) and the wave wait, which precede engagement, are not part of it. This is the TF of the cascade.

### Rate columns (`taux_de_charge` through `tre`)

- `taux_de_charge` = TR / TO: the engaged share of opening time.
- `disponibilite` = TF / TR: availability. Losses: breakdowns, startups, waiting for the startup crew, frozen time, and starvation (no pieces available). Low availability at a starved station reflects reality, not a measurement error.
- `performance` = TN / (loading time + processing time): pace efficiency while running. Losses: slower-than-nominal cycles, crew productivity, and partial batches. Value-added time is summed over all batches rather than divided by TF because a station may process batches in parallel; the summation keeps performance within [0, 100%].
- `qualite` = good / produced. A station's good pieces are those its immediate downstream router did not send to scrap; without a scrap route, quality equals 1.
- `trs` = availability x performance x quality: the OEE, within [0, 100%].

  > **Note.** Classic OEE is also written useful time / required time, where useful time is the net time of the good pieces alone (ideal cycle x good pieces). Expanding the product: (TF / TR) x (TN / value-added time) x (good / produced). Since TN = ideal cycle x produced, the last two factors reduce to useful time / value-added time. The identity OEE = useful time / required time therefore holds exactly when value-added time coincides with running time (single-batch station); performance divides by value-added time so it stays correct when batches run in parallel.

- `trg` = OEE x taux_de_charge: scheduled stops counted as losses.

  > **Note.** Substituting OEE = useful time / required time and taux_de_charge = TR / TO gives TRG = useful time / opening time.

- `tre` = OEE x (TR / TT): the full calendar counted, including closed periods.

### Production columns (`pieces_*`, `nb_lancements`, `taille_lot_moyenne`, `cycle_*`, `debit_pieces_j`, `flux_*`)

- `pieces_produites`: pieces deposited by completed batches. Batches evacuated by an interruption produced nothing and are not counted. For resource tasks, the quantity of material transformed.
- `pieces_bonnes`, `pieces_rebutees`: split by the immediate downstream router's verdict.
- `nb_lancements`: completed batch count. `taille_lot_moyenne`: mean batch size; a mean well below the maximum indicates performance loss.
- `cycle_moyen`, `cycle_p90`, `cycle_max`: batch duration from creation (start of collection) to deposit. The p90 reads: 9 out of 10 batches complete within this time.
- `debit_pieces_j`: pieces produced per day of required time.
- `flux_entrant_j`, `flux_sortant_j`: pieces physically taken from inputs and deposited at outputs, per calendar day. Re-collections after an evacuation count in the inflow, as physical flow. Sustained inflow above outflow indicates accumulation.

### Wait columns (`attente_*`, `temps_collecte`, `temps_chargement`, `temps_traitement`)

Each batch labels its current activity; the labels are accumulated:

- `attente_pieces`: waiting for pieces (upstream starvation).
- `attente_place`: waiting for free slots (the station's own max capacity).
- `attente_operateurs`: waiting for a crew.
- `attente_matiere`: waiting for material (reordering delays included).
- `attente_vague`: waiting for the other carriers of a wave. Relevant only when the minimum carrier count is greater than 1; otherwise this column stays at zero.
- `temps_collecte`: batch assembly time.
- `temps_chargement`, `temps_traitement`: loading and processing.

> **Note.** These columns partially overlap (`temps_collecte` covers the collector's piece and slot waits) and parallel batches wait concurrently.

### Hour columns (`heures_machine`, `heures_main_oeuvre`)

Two accounting columns with deliberately different aggregation rules:

- `heures_machine`: clock time during which the machine loads or processes, aggregated as a **union** over batches. A station is one physical machine: three parallel batches during 40 minutes contribute 40 machine minutes. `heures_machine` differs from TF: TF includes an engaged batch's waits, machine hours do not; machine hours are therefore at most TF, and the gap equals the engaged batches' waits. Startup time is excluded and reported in `mise_en_route`.
- `heures_main_oeuvre`: operator minutes reserved for the station by all its crews, aggregated as a **sum** (operators x duration). The account covers loading and per-batch processing crews during their jobs, the startup crew during setup, and per-task crews over their full posting, idle intervals included. The ratio `heures_main_oeuvre / heures_machine` expresses average staffing per machine hour.

> **Note.** The totals of these two columns over all stations appear in `flux.csv` (`heures_machine_totales`, `heures_main_oeuvre_totales`).

---

## postes_modeles.csv, production per model

Per piece task and model: the ideal cycle time (`tc_ideal`) and the produced, good, and scrapped counts. This is the detail underlying TN.

> **Note.** `tc_ideal` is the mean of the configured durations (processing and loading taken at their mean), not a measured time; it is the same ideal-cycle convention as in the time cascade.

---

## buffers.csv, one buffer per row

- `longueur_moyenne`, `longueur_max`, `longueur_ecart_type`: queue length statistics, time-weighted. A swelling buffer indicates a bottleneck immediately downstream.
- `longueur_finale`: the remaining count at the end of the run.
- `sejour_moyen`, `sejour_max`: piece dwell time (empty for exit and scrap buffers, which are terminal).
- `entrees`, `sorties`: total traffic, including pieces collected immediately upon arrival.
- `flux_entrant_j`, `flux_sortant_j`: the same traffic per calendar day.
- `temps_moyen_entre_arrivees`: simulated span / entries.

---

## operateurs.csv, one operator group per row

- `effectif`: group size. `temps_poste`: total posted time (the sum of the group's shifts over the run).
- `occupation_moyenne`: mean requisitioned headcount over the full span.
- `heures_en_poste` / `heures_hors_poste`: operator minutes requisitioned inside and outside the group's shifts. Diagnostic columns: per-task crews are released at end of shift and on batch aborts, and shift fit is re-verified after material waits, so `heures_hors_poste` is expected to remain near zero. Residual values correspond to restock orders holding a crew past the shift boundary or, without a shift constraint, batches legitimately completing after it.
- `taux_occupation`: total requisitioned time / (size x posted time), the requisitioned share of posted time. Values remain below 100% by construction, since crews are released at shift end.

  > **Note.** In theory this rate can exceed 100%: without a shift constraint, a crew may be requisitioned beyond its posted time (for example to finish a batch or honor a restock order), and the requisitioned time then exceeds the posted time.

- `occupation_max`: peak simultaneous requisition.

---

## ressources.csv, one resource per row

- `capacite`: the resource capacity, that is, the maximum quantity that can be stored at once.
- `stock_moyen`, `stock_min`, `stock_max`, `stock_final`: stock level statistics (time-weighted mean) and the final level.
- `consommation_totale`, `entrees_totales`: total consumption and total replenishment.
- `consommation_j`: consumption per calendar day.
- `nb_ruptures`, `temps_rupture`: number of stock-outs (stock reaching zero) and total time at zero. Recurrent stock-outs identify the resource starving its consumer tasks.

---

## flux.csv and flux_modeles.csv, line-level metrics

- `sorties`, `rebuts`, `taux_rebut`: overall output, scrap count, and scrap rate. With the scrap-aware generator, scrapped pieces are remade; goals are expressed in good pieces.
- `debit_sorties_j`: good pieces per day over the full span.
- `traversee_*`: lead time of exited pieces, from creation to exit: mean, median, p90, max. The same statistics per model appear in `flux_modeles.csv`.
- `encours_moyen`, `encours_max`, `encours_final`: work in progress: pieces created but neither exited nor scrapped, whether in buffers or on stations. `encours_final` can therefore exceed the sum of buffer contents.
- `heures_machine_totales`, `heures_main_oeuvre_totales`: machine hours and labor hours cumulated over all stations (the sums of the same-named columns in `postes.csv`).
- `flux_modeles.csv` per model: `objectif` (the generator goal), `genere` (pieces injected, remakes included), exits, scrap, `atteinte` = exits / goal, and the lead time statistics. `objectif` and `atteinte` are populated in goal mode only; in rate mode the generator has no per-model goal and these columns remain empty.

---

## synthese_admin.csv, administrative versus productive

A summary comparing tasks flagged admin against the others. One row per metric; columns give each group's cumulated value, the total, each group's share (`part_admin`, `part_productif`, summing to 100%), and the ratio `ratio_admin_productif`.

The five metrics: station count, running time, total cycle time (summed over batches), machine hours, labor hours.

---

## temps_traversee.csv, one row per piece

The raw per-piece record: piece, model, outcome (`sortie` or `rebut`), creation and completion dates, lead time. Suitable for pivot-table analysis by model or period.

---

## graphes/, the charts

Every figure is provided in two forms: the rendered PNG and the plotted data as CSV. The tree separates by format, then by category:

```
graphes/
    png/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
    csv/   ressources/ buffers/ ligne/ postes/ operateurs/ modeles/
```

- `ressources/stock_*`: stock level over time.
- `buffers/longueur_*`: buffer length over time.
- `ligne/pieces_en_attente`: total passage-buffer length; `ligne/encours`: work in progress over time.
- `postes/occupation_*`: occupied slots over time (occupied = capacity minus vacant); the station's capacity is stated in the title. With contiguous carriers disabled, slots reserved by a started batch count as occupied.
- `operateurs/disponibles_*`: available operators per group over time (zero outside shifts by construction).
- `modeles/trajectoires_<model>`: the model's observed routes, one bar per distinct trajectory, ordered by frequency, annotated with counts and shares. Each bar stacks the steps in order; segment length is the mean time at that step (blue: buffer wait; orange: station). Only completed pieces (exit or scrap) are included.
- `modeles/production`: per model, in goal mode three bars (goal, generated including remakes, produced); in rate mode two bars (generated, produced).

> **Note.** On very large runs, extremely long time series are downsampled when the chart data is written, preserving envelope shape (peaks and dips) while omitting some intermediate points. This applies only to the plotted data; the CSV report values are unaffected.

---

## run.csv, run identity

Source file, start and end calendar dates, simulated span, random seed, generation timestamp, compute time (the real execution duration), and the stopping criterion with its parameters (`critere_arret`, `critere_details`). Identical seed and model file reproduce identical CSVs on the same engine.
