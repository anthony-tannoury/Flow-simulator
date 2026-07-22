// kpis++ — post-run KPI collection and the report (mirror of simulation/kpis.py).
//
// Every value is read after the run from the monitors salabim++ already keeps
// (State value monitors, Resource claimed_quantity, Queue length / length_of_stay,
// the Component mode timeline) plus the light tallies the tasks fill
// (batch_sizes / cycle_times / startup_times, deposited / scrapped, WIP). Times
// are simulation minutes. CSVs are utf-8 with a BOM so Excel keeps the accents.
#pragma once

#include "simulation.hpp"

#include "json.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace kpis {

using ojson = nlohmann::ordered_json;  // insertion-ordered: preserves CSV column order
using namespace simulation;
namespace fs = std::filesystem;

// --- small numeric helpers --------------------------------------------------
inline double roundn(double x, int n) {
    double f = std::pow(10.0, n);
    return std::round(x * f) / f;
}
// round to 4 dp, or blank ("") when the denominator is 0 / value undefined.
inline ojson num(double x) { return roundn(x, 4); }
inline ojson blank() { return std::string(); }
inline ojson ratio(double numv, double denv) { return denv ? ojson(roundn(numv / denv, 4)) : blank(); }

// --- formatting (raw minutes/fractions -> display) --------------------------
inline std::string fmt_duree(double m) {
    if (m < 1) return std::to_string((long long)std::llround(m * 60)) + "s";
    if (m < 60) {
        int whole = (int)m, sec = (int)std::llround((m - whole) * 60);
        if (sec == 60) { whole += 1; sec = 0; }
        if (whole < 60)
            return sec ? std::to_string(whole) + "m " + std::to_string(sec) + "s"
                       : std::to_string(whole) + "m";
    }
    long long total = std::llround(m), heures = total / 60, mins = total % 60;
    if (heures < 24) return std::to_string(heures) + "h " + std::to_string(mins) + "m";
    long long jours = heures / 24;
    heures %= 24;
    return std::to_string(jours) + "j " + std::to_string(heures) + "h " + std::to_string(mins) + "m";
}
inline std::string fmt_pct(double x) {
    char buf[32];
    std::snprintf(buf, sizeof buf, "%.1f", x * 100.0);
    std::string s = buf;  // strip trailing 0 / '.'
    if (s.find('.') != std::string::npos) {
        while (s.back() == '0') s.pop_back();
        if (s.back() == '.') s.pop_back();
    }
    return s + "%";
}
// a point in simulated time as a real calendar date (dd-mm-yyyy HH:MM)
inline std::string fmt_instant(double minutes, const ShiftManager::DateTime& sim_start) {
    long long total = sim_start.hour * 60LL + sim_start.minute + (long long)std::llround(minutes);
    long long day_add = total / 1440;
    long long rem = total % 1440;
    if (rem < 0) { rem += 1440; day_add -= 1; }
    auto d = sim_start.date + std::chrono::days(day_add);
    std::chrono::year_month_day ymd{d};
    char buf[32];
    std::snprintf(buf, sizeof buf, "%02d-%02u-%04d %02lld:%02lld", (unsigned)ymd.day(),
                  (unsigned)ymd.month(), (int)ymd.year(), rem / 60, rem % 60);
    return buf;
}

// --- monitor-timeline helpers (read x_raw()/t_raw() like Python's xt) -------
inline int rising_edges(const sim::Monitor& mon, double value) {
    const auto& xs = mon.x_raw();
    int n = 0;
    for (std::size_t i = 1; i < xs.size(); ++i)
        if (xs[i] == value && xs[i - 1] != value) ++n;
    return n;
}
inline std::vector<double> edge_times(const sim::Monitor& mon, double value) {
    const auto& xs = mon.x_raw();
    const auto& ts = mon.t_raw();
    std::vector<double> out;
    for (std::size_t i = 1; i < xs.size(); ++i)
        if (xs[i] == value && xs[i - 1] != value) out.push_back(ts[i]);
    return out;
}
// total time a component spent in `tag` (mode timeline; last entry runs to now).
inline double mode_total_one(const Component* c, const std::string& tag, double now) {
    const auto& log = c->mode_log();
    double total = 0.0;
    for (std::size_t i = 0; i < log.size(); ++i)
        if (log[i].second == tag) {
            double end = (i + 1 < log.size()) ? log[i + 1].first : now;
            if (end > log[i].first) total += end - log[i].first;
        }
    return total;
}
inline double mode_total(const std::vector<Component*>& cs, const std::string& tag) {
    double now = env->now(), total = 0.0;
    for (const Component* c : cs) total += mode_total_one(c, tag, now);
    return total;
}
// wall-clock time where at least one component is in one of the modes (union).
inline double union_mode_duration(const std::vector<Component*>& cs, const std::set<std::string>& tags) {
    double now = env->now();
    std::vector<std::pair<double, int>> deltas;
    for (const Component* c : cs) {
        const auto& log = c->mode_log();
        for (std::size_t i = 0; i < log.size(); ++i)
            if (tags.count(log[i].second)) {
                double start = log[i].first;
                double end = (i + 1 < log.size()) ? log[i + 1].first : now;
                if (end > start) { deltas.push_back({start, 1}); deltas.push_back({end, -1}); }
            }
    }
    std::sort(deltas.begin(), deltas.end());
    double total = 0.0, prev = 0.0;
    int active = 0;
    bool have_prev = false;
    for (auto& [t, d] : deltas) {
        if (active > 0 && have_prev) total += t - prev;
        active += d;
        prev = t;
        have_prev = true;
    }
    return total;
}
// integral of a level monitor over the time a status monitor holds a value.
inline double level_during(const sim::Monitor& level, const sim::Monitor& status, double status_value) {
    const auto& xl = level.x_raw();
    const auto& tl = level.t_raw();
    const auto& xs = status.x_raw();
    const auto& ts = status.t_raw();
    std::set<double> times(tl.begin(), tl.end());
    times.insert(ts.begin(), ts.end());
    times.insert(env->now());
    std::vector<double> T(times.begin(), times.end());
    double total = 0.0;
    std::size_t il = 0, is = 0;
    for (std::size_t k = 1; k < T.size(); ++k) {
        double t0 = T[k - 1], t1 = T[k];
        while (il + 1 < tl.size() && tl[il + 1] <= t0) ++il;
        while (is + 1 < ts.size() && ts[is + 1] <= t0) ++is;
        if (!xs.empty() && xs[is] == status_value && !xl.empty()) total += xl[il] * (t1 - t0);
    }
    return total;
}
// time where level monitor a holds val_a AND b holds val_b (event-merged).
inline double overlap_duration(const sim::Monitor& a, double va, const sim::Monitor& b, double vb) {
    const auto& xa = a.x_raw();
    const auto& ta = a.t_raw();
    const auto& xb = b.x_raw();
    const auto& tb = b.t_raw();
    std::set<double> times(ta.begin(), ta.end());
    times.insert(tb.begin(), tb.end());
    std::vector<double> T(times.begin(), times.end());
    double total = 0.0;
    std::size_t ia = 0, ib = 0;
    for (std::size_t k = 1; k < T.size(); ++k) {
        double t0 = T[k - 1], t1 = T[k];
        while (ia + 1 < ta.size() && ta[ia + 1] <= t0) ++ia;
        while (ib + 1 < tb.size() && tb[ib + 1] <= t0) ++ib;
        if (!xa.empty() && !xb.empty() && xa[ia] == va && xb[ib] == vb) total += t1 - t0;
    }
    return total;
}

// --- ideal cycle times per model (from the task's own config) ---------------
inline const Model* config_key(const PieceTaskConfig& cfg, const Model* model) {
    const Model* m = model;
    while (m != nullptr) {
        for (const auto& [mm, mc] : cfg.models_configs)
            if (mm == m) return m;
        m = m->parent;
    }
    return model;
}
inline std::map<const Model*, double> ideal_cycle_times(Task* task) {
    std::map<const Model*, double> tc;
    double loading = task->config->loading_duration->mean(0.0);
    if (auto* pt = dynamic_cast<PieceTask*>(task)) {
        auto* cfg = pt->pconfig().get();
        for (const auto& [model, mc] : cfg->models_configs)
            tc[model] = (mc.duration->mean(0.0) + loading) / mc.max_carrier_capacity;
    } else {
        auto* cfg = static_cast<ResourceTaskConfig*>(task->config.get());
        tc[nullptr] = (cfg->duration->mean(0.0) + loading) / cfg->max_carrier_capacity;
    }
    return tc;
}

// collectors of a carrier list, as Components (for mode_total over collectors)
inline std::vector<Component*> collectors_of(const std::vector<Carrier*>& carriers) {
    std::vector<Component*> out;
    for (Carrier* c : carriers) {
        if (auto* pc = dynamic_cast<PieceCarrier*>(c)) out.push_back(pc->piece_collector);
        else if (auto* rc = dynamic_cast<ResourceCarrier*>(c)) out.push_back(rc->resource_collector);
    }
    return out;
}
inline std::vector<Component*> as_components(const std::vector<Carrier*>& carriers) {
    return std::vector<Component*>(carriers.begin(), carriers.end());
}

inline ojson num_opt(const std::optional<double>& v) { return v ? ojson(roundn(*v, 4)) : blank(); }

// --- collectors -------------------------------------------------------------
inline ojson task_kpis(Task* task) {
    double tt = env->now();
    double to = task->is_in_downtime.value_monitor().value_duration(0.0);
    double arrets = task->is_in_shutdown.value_monitor().value_duration(1.0);
    double tr = std::max(to - arrets, 0.0);
    double pannes = task->is_in_breakdown.value_monitor().value_duration(1.0);
    int nb_pannes = rising_edges(task->is_in_breakdown.value_monitor(), 1.0);
    auto debuts = edge_times(task->is_in_breakdown.value_monitor(), 1.0);
    ojson mtbf = blank();
    if (debuts.size() > 1) {
        double s = 0;
        for (std::size_t i = 1; i < debuts.size(); ++i) s += debuts[i] - debuts[i - 1];
        mtbf = roundn(s / (debuts.size() - 1), 3);
    }
    double gel = overlap_duration(task->is_frozen.value_monitor(), 1.0,
                                  task->is_in_downtime.value_monitor(), 0.0);
    double tf = tt - task->active_carriers.num_carriers.value_monitor().value_duration(0.0);

    bool is_piece = dynamic_cast<PieceTask*>(task) != nullptr;
    auto tc = ideal_cycle_times(task);
    double produites = 0, rebutees = 0, tn = 0;
    if (is_piece) {
        auto* pt = static_cast<PieceTask*>(task);
        auto* cfg = pt->pconfig().get();
        for (auto& [m, n] : pt->deposited) produites += n;
        for (auto& [m, n] : pt->scrapped) rebutees += n;
        for (auto& [m, n] : pt->deposited) tn += tc[config_key(*cfg, m)] * n;
    } else {
        long long n = task->batch_sizes.number_of_entries();
        produites = n ? roundn(task->batch_sizes.mean() * n, 3) : 0;
        tn = produites * tc[nullptr];
    }
    double bonnes = produites - rebutees;

    std::vector<Component*> carriers = as_components(task->all_carriers);
    std::vector<Component*> collectors = collectors_of(task->all_carriers);
    long long lancements = task->batch_sizes.number_of_entries();

    double t_loading = mode_total(carriers, "loading");
    double t_processing = mode_total(carriers, "processing");
    double value_add = t_loading + t_processing;
    std::optional<double> do_val = tr ? std::optional<double>(tf / tr) : std::nullopt;
    std::optional<double> tp_val = value_add ? std::optional<double>(tn / value_add) : std::nullopt;
    std::optional<double> tq_val = produites ? std::optional<double>(bonnes / produites) : std::nullopt;
    std::optional<double> trs_val, trg_val, tre_val;
    if (do_val && tp_val && tq_val) trs_val = *do_val * *tp_val * *tq_val;
    if (trs_val && to) trg_val = *trs_val * (tr / to);
    if (trs_val && tt) tre_val = *trs_val * (tr / tt);

    double now = env->now();
    ojson r;
    r["poste"] = task->name();
    r["type"] = is_piece ? "piece" : "resource";
    r["admin"] = (bool)task->config->admin;
    r["temps_total"] = roundn(tt, 3);
    r["temps_ouverture"] = roundn(to, 3);
    r["arrets_programmes"] = roundn(arrets, 3);
    r["temps_requis"] = roundn(tr, 3);
    r["pannes"] = roundn(pannes, 3);
    r["nb_pannes"] = nb_pannes;
    r["mtbf"] = mtbf;
    r["mttr"] = nb_pannes ? ojson(roundn(pannes / nb_pannes, 3)) : blank();
    r["gel"] = roundn(gel, 3);
    r["mise_en_route"] = task->startup_times.number_of_entries()
                             ? roundn(task->startup_times.mean() * task->startup_times.number_of_entries(), 3)
                             : 0.0;
    r["nb_mises_en_route"] = task->startup_times.number_of_entries();
    r["temps_fonctionnement"] = roundn(tf, 3);
    r["taux_de_charge"] = ratio(tr, to);
    r["disponibilite"] = num_opt(do_val);
    r["performance"] = num_opt(tp_val);
    r["qualite"] = num_opt(tq_val);
    r["trs"] = num_opt(trs_val);
    r["trg"] = num_opt(trg_val);
    r["tre"] = num_opt(tre_val);
    r["pieces_produites"] = produites;
    r["pieces_bonnes"] = bonnes;
    r["pieces_rebutees"] = rebutees;
    r["nb_lancements"] = lancements;
    r["taille_lot_moyenne"] = lancements ? ojson(roundn(task->batch_sizes.mean(), 3)) : blank();
    r["cycle_moyen"] = lancements ? ojson(roundn(task->cycle_times.mean(), 3)) : blank();
    r["cycle_p90"] = lancements ? ojson(roundn(task->cycle_times.percentile(90), 3)) : blank();
    r["cycle_max"] = lancements ? ojson(roundn(task->cycle_times.maximum(), 3)) : blank();
    r["debit_pieces_j"] = tr ? ojson(roundn(produites / tr * 1440, 3)) : blank();
    r["flux_entrant_j"] = (is_piece && tt) ? ojson(roundn(task->pieces_in / tt * 1440, 3)) : blank();
    r["flux_sortant_j"] = tt ? ojson(roundn(produites / tt * 1440, 3)) : blank();
    r["attente_pieces"] = roundn(mode_total(collectors, "wait_pieces"), 3);
    r["attente_place"] = roundn(mode_total(collectors, "wait_slot"), 3);
    r["attente_operateurs"] =
        roundn(mode_total(carriers, "wait_operators") + mode_total_one(task, "wait_operators", now), 3);
    r["attente_matiere"] = roundn(mode_total(carriers, "wait_materials"), 3);
    r["attente_vague"] = roundn(mode_total(carriers, "wait_dispatch"), 3);
    r["temps_collecte"] = roundn(mode_total(carriers, "collecting"), 3);
    r["temps_chargement"] = roundn(t_loading, 3);
    r["temps_traitement"] = roundn(t_processing, 3);
    r["heures_machine"] = roundn(union_mode_duration(carriers, {"loading", "processing"}), 3);
    r["heures_main_oeuvre"] = roundn(task->labor_minutes_total(), 3);
    return r;
}

inline std::vector<ojson> task_model_rows(Task* task) {
    std::vector<ojson> rows;
    auto* pt = dynamic_cast<PieceTask*>(task);
    if (!pt) return rows;
    auto* cfg = pt->pconfig().get();
    auto tc = ideal_cycle_times(task);
    std::vector<std::pair<Model*, int>> sorted(pt->deposited.begin(), pt->deposited.end());
    std::sort(sorted.begin(), sorted.end(),
              [](auto& a, auto& b) { return a.first->name < b.first->name; });
    for (auto& [model, n] : sorted) {
        int reb = pt->scrapped.count(model) ? pt->scrapped.at(model) : 0;
        ojson r;
        r["poste"] = task->name();
        r["modele"] = model->name;
        r["tc_ideal"] = roundn(tc[config_key(*cfg, model)], 3);
        r["produites"] = n;
        r["bonnes"] = n - reb;
        r["rebutees"] = reb;
        rows.push_back(r);
    }
    return rows;
}

inline ojson buffer_kpis(Buffer* b) {
    double tt = env->now();
    long long sorties = b->length_of_stay.number_of_entries();
    long long entrees = sorties + (long long)b->size();
    const char* type = b->buffer_type == BufferType::PASSAGE ? "PASSAGE"
                       : b->buffer_type == BufferType::SCRAP ? "SCRAP"
                                                             : "EXIT";
    ojson r;
    r["buffer"] = b->name();
    r["type"] = type;
    r["longueur_moyenne"] = roundn(b->length.mean(), 3);
    r["longueur_max"] = b->length.maximum();
    r["longueur_ecart_type"] = roundn(b->length.std(), 3);
    r["longueur_finale"] = (long long)b->size();
    r["sejour_moyen"] = sorties ? ojson(roundn(b->length_of_stay.mean(), 3)) : blank();
    r["sejour_max"] = sorties ? ojson(roundn(b->length_of_stay.maximum(), 3)) : blank();
    r["entrees"] = entrees;
    r["sorties"] = sorties;
    r["flux_entrant_j"] = tt ? ojson(roundn(entrees / tt * 1440, 3)) : blank();
    r["flux_sortant_j"] = tt ? ojson(roundn(sorties / tt * 1440, 3)) : blank();
    r["temps_moyen_entre_arrivees"] = entrees ? ojson(roundn(tt / entrees, 3)) : blank();
    return r;
}

inline ojson operator_kpis(OperatorGroup* g) {
    double tt = env->now();
    double posted = g->is_in_downtime.value_monitor().value_duration(0.0);
    double claimed_mean = g->claimed_quantity.mean();
    double en_poste = level_during(g->claimed_quantity, g->is_in_downtime.value_monitor(), 0.0);
    double hors_poste = level_during(g->claimed_quantity, g->is_in_downtime.value_monitor(), 1.0);
    ojson r;
    r["groupe"] = g->name();
    r["effectif"] = g->n_operators;
    r["temps_poste"] = roundn(posted, 3);
    r["occupation_moyenne"] = roundn(claimed_mean, 3);
    r["heures_en_poste"] = roundn(en_poste, 3);
    r["heures_hors_poste"] = roundn(hors_poste, 3);
    r["occupation_max"] = g->claimed_quantity.maximum();
    r["taux_occupation"] = ratio(claimed_mean * tt, g->n_operators * posted);
    return r;
}

// Per-resource stock metrics from the available_quantity ("stock") monitor:
// consommation = total downward movement, entrées = total upward movement
// (restocks for a restockable input, output for a resource a task produces),
// rupture = a fall to an empty stock. Mirrors kpis.resource_kpis.
inline ojson resource_kpis(sim::Resource* res) {
    double tt = env->now();
    sim::Monitor& stock = res->available_quantity;
    const std::vector<double>& v = stock.x_raw();
    double consommation = 0.0, entrees = 0.0;
    int ruptures = 0;
    for (size_t i = 1; i < v.size(); ++i) {
        double delta = v[i] - v[i - 1];
        if (delta < 0) consommation -= delta; else entrees += delta;
        if (v[i] == 0.0 && v[i - 1] > 0.0) ++ruptures;
    }
    ojson r;
    r["ressource"] = res->name();
    r["capacite"] = res->capacity();
    r["stock_moyen"] = roundn(stock.mean(), 3);
    r["stock_min"] = roundn(stock.minimum(), 3);
    r["stock_max"] = roundn(stock.maximum(), 3);
    r["stock_final"] = roundn(res->available_quantity(), 3);
    r["consommation_totale"] = roundn(consommation, 3);
    r["entrees_totales"] = roundn(entrees, 3);
    r["consommation_j"] = tt ? ojson(roundn(consommation / tt * 1440, 3)) : blank();
    r["nb_ruptures"] = ruptures;
    r["temps_rupture"] = roundn(stock.value_duration(0.0), 3);
    return r;
}

// --- flow / lead-time -------------------------------------------------------
inline ojson lead_stats(std::vector<double> leads) {
    std::sort(leads.begin(), leads.end());
    auto pct = [&](double q) -> ojson {
        if (leads.empty()) return blank();
        std::size_t idx = std::min((std::size_t)(leads.size() * q / 100.0), leads.size() - 1);
        return roundn(leads[idx], 3);
    };
    ojson r;
    if (leads.empty()) {
        r["traversee_moyenne"] = blank();
        r["traversee_mediane"] = blank();
        r["traversee_p90"] = blank();
        r["traversee_max"] = blank();
    } else {
        double s = 0;
        for (double l : leads) s += l;
        r["traversee_moyenne"] = roundn(s / leads.size(), 3);
        r["traversee_mediane"] = pct(50);
        r["traversee_p90"] = pct(90);
        r["traversee_max"] = roundn(leads.back(), 3);
    }
    return r;
}

inline std::vector<ojson> lead_time_rows(const std::vector<Buffer*>& buffers) {
    std::vector<ojson> rows;
    for (Buffer* b : buffers) {
        if (b->buffer_type == BufferType::PASSAGE) continue;
        const char* resultat = b->buffer_type == BufferType::EXIT ? "sortie" : "rebut";
        for (sim::Component* c : *b) {
            auto* piece = static_cast<Piece*>(c);
            double fin = piece->enter_time(*b);
            ojson r;
            r["piece"] = piece->id;
            r["modele"] = piece->model->name;
            r["resultat"] = resultat;
            r["creation"] = roundn(piece->creation_time(), 3);
            r["fin"] = roundn(fin, 3);
            r["temps_traversee"] = roundn(fin - piece->creation_time(), 3);
            rows.push_back(r);
        }
    }
    std::sort(rows.begin(), rows.end(),
              [](const ojson& a, const ojson& b) { return a.at("fin") < b.at("fin"); });
    return rows;
}

inline std::pair<ojson, std::vector<ojson>> flow_kpis(const std::vector<Buffer*>& buffers,
                                                      PieceGenerator* gen) {
    double tt = env->now();
    std::vector<Piece*> exits, scraps;
    for (Buffer* b : buffers) {
        if (b->buffer_type == BufferType::EXIT)
            for (sim::Component* c : *b) exits.push_back(static_cast<Piece*>(c));
        if (b->buffer_type == BufferType::SCRAP)
            for (sim::Component* c : *b) scraps.push_back(static_cast<Piece*>(c));
    }
    auto lead = [](Piece* p) {
        auto qs = p->queues();
        double enter = qs.empty() ? 0.0 : p->enter_time(*qs.front());
        return enter - p->creation_time();
    };
    long long total = (long long)exits.size() + (long long)scraps.size();
    std::vector<double> exit_leads;
    for (Piece* p : exits) exit_leads.push_back(lead(p));

    ojson flux;
    flux["duree_simulee"] = roundn(tt, 3);
    flux["sorties"] = (long long)exits.size();
    flux["rebuts"] = (long long)scraps.size();
    flux["taux_rebut"] = ratio((double)scraps.size(), (double)total);
    flux["debit_sorties_j"] = tt ? ojson(roundn(exits.size() / tt * 1440, 3)) : blank();
    ojson ls = lead_stats(exit_leads);
    for (auto& [k, v] : ls.items()) flux[k] = v;
    flux["encours_moyen"] = roundn(kpis_state::WIP->mean(), 3);
    flux["encours_max"] = kpis_state::WIP->maximum();
    flux["encours_final"] = kpis_state::wip_level;

    std::vector<ojson> par_modele;
    if (gen) {
        std::map<Model*, std::vector<double>> exits_by;
        for (Piece* p : exits) exits_by[p->model].push_back(lead(p));
        std::map<Model*, int> scraps_by;
        for (Piece* p : scraps) scraps_by[p->model] += 1;
        auto* goal_gen = dynamic_cast<GoalPieceGenerator*>(gen);
        for (std::size_t i = 0; i < gen->models.size(); ++i) {
            Model* model = gen->models[i];
            std::vector<double> leads = exits_by.count(model) ? exits_by[model] : std::vector<double>{};
            std::sort(leads.begin(), leads.end());
            int rebuts = scraps_by.count(model) ? scraps_by[model] : 0;
            ojson row;
            row["modele"] = model->name;
            row["objectif"] = goal_gen ? ojson(goal_gen->goals[i]) : blank();
            row["genere"] = gen->total_generated[i];
            row["sorties"] = (long long)leads.size();
            row["rebuts"] = rebuts;
            row["taux_rebut"] = ratio(rebuts, (double)leads.size() + rebuts);
            row["atteinte"] = goal_gen ? ratio((double)leads.size(), goal_gen->goals[i]) : blank();
            ojson ms = lead_stats(leads);
            for (auto& [k, v] : ms.items()) row[k] = v;
            par_modele.push_back(row);
        }
    }
    return {flux, par_modele};
}

// --- administrative vs productive roll-up -----------------------------------
inline double cell_num(const ojson& v) {  // number, or 0 for a blank/undefined cell
    return v.is_number() ? v.get<double>() : 0.0;
}

// (metric key, is-a-duration)
inline const std::vector<std::pair<std::string, bool>>& admin_indicateurs() {
    static const std::vector<std::pair<std::string, bool>> t = {
        {"nb_taches", false}, {"temps_fonctionnement", true}, {"cycle_total", true},
        {"heures_machine", true}, {"heures_main_oeuvre", true}};
    return t;
}
inline std::string admin_label(const std::string& k) {
    if (k == "nb_taches") return "Nombre de postes";
    if (k == "temps_fonctionnement") return "Temps de fonctionnement";
    if (k == "cycle_total") return "Temps de cycle total";
    if (k == "heures_machine") return "Heures machine";
    return "Heures main-d'\xC5\x93uvre";  // œ
}

inline ojson admin_summary(const std::vector<ojson>& task_rows) {
    std::map<std::string, double> admin, productif;
    for (auto& [k, _] : admin_indicateurs()) { admin[k] = 0; productif[k] = 0; }
    for (const ojson& row : task_rows) {
        auto& b = row.value("admin", false) ? admin : productif;
        double launches = cell_num(row.value("nb_lancements", ojson(0)));
        double cyclem = cell_num(row.value("cycle_moyen", blank()));
        b["nb_taches"] += 1;
        b["temps_fonctionnement"] += cell_num(row.value("temps_fonctionnement", ojson(0)));
        b["cycle_total"] += cyclem * launches;
        b["heures_machine"] += cell_num(row.value("heures_machine", ojson(0)));
        b["heures_main_oeuvre"] += cell_num(row.value("heures_main_oeuvre", ojson(0)));
    }
    ojson admin_j, prod_j, total_j, part_a, part_p, rap;
    ojson keys = ojson::array();
    for (auto& [k, _] : admin_indicateurs()) {
        keys.push_back(k);
        double a = admin[k], p = productif[k], t = a + p;
        admin_j[k] = a;
        prod_j[k] = p;
        total_j[k] = t;
        part_a[k] = t ? ojson(a / t) : blank();
        part_p[k] = t ? ojson(p / t) : blank();
        rap[k] = p ? ojson(a / p) : blank();
    }
    ojson r;
    r["indicateurs"] = keys;
    r["administratives"] = admin_j;
    r["productives"] = prod_j;
    r["total"] = total_j;
    r["part_administratives"] = part_a;
    r["part_productives"] = part_p;
    r["ratio_admin_sur_productif"] = rap;
    return r;
}

inline std::vector<ojson> admin_synthese_rows(const ojson& s) {
    auto fmt = [](double v, bool dur) -> ojson {
        return dur ? ojson(fmt_duree(v)) : (v == std::floor(v) ? ojson((long long)v) : ojson(roundn(v, 3)));
    };
    std::vector<ojson> rows;
    for (auto& [key, dur] : admin_indicateurs()) {
        const ojson& pa = s.at("part_administratives").at(key);
        const ojson& pp = s.at("part_productives").at(key);
        const ojson& rr = s.at("ratio_admin_sur_productif").at(key);
        ojson r;
        r["indicateur"] = admin_label(key);
        r["administratives"] = fmt(s.at("administratives").at(key).get<double>(), dur);
        r["productives"] = fmt(s.at("productives").at(key).get<double>(), dur);
        r["total"] = fmt(s.at("total").at(key).get<double>(), dur);
        r["part_admin"] = pa.is_number() ? ojson(fmt_pct(pa.get<double>())) : blank();
        r["part_productif"] = pp.is_number() ? ojson(fmt_pct(pp.get<double>())) : blank();
        r["ratio_admin_productif"] = rr.is_number() ? ojson(roundn(rr.get<double>(), 3)) : blank();
        rows.push_back(r);
    }
    return rows;
}

// --- CSV writer -------------------------------------------------------------
inline const std::set<std::string>& duree_cols() {
    static const std::set<std::string> s = {
        "temps_total", "temps_ouverture", "arrets_programmes", "temps_requis", "pannes", "mtbf",
        "mttr", "gel", "mise_en_route", "temps_fonctionnement", "cycle_moyen", "cycle_p90",
        "cycle_max", "attente_pieces", "attente_place", "attente_operateurs", "attente_matiere",
        "attente_vague", "temps_collecte", "temps_chargement", "temps_traitement", "heures_machine",
        "heures_main_oeuvre", "heures_en_poste", "heures_hors_poste", "sejour_moyen", "sejour_max",
        "temps_moyen_entre_arrivees", "temps_poste", "traversee_moyenne", "traversee_mediane",
        "traversee_p90", "traversee_max", "temps_traversee", "tc_ideal", "duree_simulee",
        "temps_rupture"};
    return s;
}
inline const std::set<std::string>& pct_cols() {
    static const std::set<std::string> s = {"taux_de_charge", "disponibilite", "performance",
                                            "qualite", "trs", "trg", "tre", "taux_rebut",
                                            "atteinte", "taux_occupation"};
    return s;
}

inline std::string csv_cell(const ojson& v) {
    if (v.is_string()) {
        std::string s = v.get<std::string>();
        if (s.find(',') != std::string::npos || s.find('"') != std::string::npos) {
            std::string q = "\"";
            for (char c : s) { if (c == '"') q += '"'; q += c; }
            return q + "\"";
        }
        return s;
    }
    if (v.is_boolean()) return v.get<bool>() ? "true" : "false";
    if (v.is_number_integer()) return std::to_string(v.get<long long>());
    if (v.is_number()) {
        std::string s = ojson(v).dump();  // no trailing-zero noise from printf
        return s;
    }
    return "";
}

inline ojson format_cell(const std::string& key, const ojson& v,
                         const ShiftManager::DateTime& sim_start) {
    if (!v.is_number()) {  // blanks / strings / bools pass through (bool handled below)
        if (key == "admin") return ojson(v.get<bool>() ? "oui" : "non");
        return v;
    }
    double d = v.get<double>();
    if (duree_cols().count(key)) return ojson(fmt_duree(d));
    if (pct_cols().count(key)) return ojson(fmt_pct(d));
    if (key == "creation" || key == "fin") return ojson(fmt_instant(d, sim_start));
    return v;
}

inline void write_csv(const fs::path& path, const std::vector<ojson>& rows,
                      const ShiftManager::DateTime& sim_start) {
    if (rows.empty()) return;
    std::ofstream f(path, std::ios::binary);
    f << "\xEF\xBB\xBF";  // utf-8 BOM so Excel keeps accents
    bool first = true;
    for (auto& [k, v] : rows[0].items()) { f << (first ? "" : ",") << k; first = false; }
    f << "\r\n";
    for (const ojson& row : rows) {
        first = true;
        for (auto& [k, v] : row.items()) {
            f << (first ? "" : ",") << csv_cell(format_cell(k, v, sim_start));
            first = false;
        }
        f << "\r\n";
    }
}

// key/value CSV (run.csv, flux.csv) from an object, formatting each value.
inline void write_kv_csv(const fs::path& path, const ojson& obj,
                         const ShiftManager::DateTime& sim_start) {
    std::vector<ojson> rows;
    for (auto& [k, v] : obj.items()) {
        ojson r;
        r["cle"] = k;
        r["valeur"] = format_cell(k, v, sim_start);
        rows.push_back(r);
    }
    write_csv(path, rows, sim_start);
}

// --- the full CSV report ----------------------------------------------------
inline void write_report(const fs::path& dir, const std::vector<Task*>& tasks,
                         const std::vector<Buffer*>& buffers, PieceGenerator* gen,
                         const std::vector<OperatorGroup*>& operator_groups, ojson run_info,
                         const ShiftManager::DateTime& sim_start,
                         const std::vector<sim::Resource*>& resources = {}) {
    fs::create_directories(dir);

    ojson run;
    run["duree_simulee"] = fmt_duree(env->now());
    run["graine"] = simulation::SEED;
    for (auto& [k, v] : run_info.items()) run[k] = v;  // fichier / criterion / ... from the caller
    write_kv_csv(dir / "run.csv", run, sim_start);

    std::vector<ojson> task_rows;
    for (Task* t : tasks) task_rows.push_back(task_kpis(t));
    write_csv(dir / "postes.csv", task_rows, sim_start);

    std::vector<ojson> model_rows;
    for (Task* t : tasks)
        for (ojson& r : task_model_rows(t)) model_rows.push_back(r);
    write_csv(dir / "postes_modeles.csv", model_rows, sim_start);

    if (!task_rows.empty())
        write_csv(dir / "synthese_admin.csv", admin_synthese_rows(admin_summary(task_rows)), sim_start);

    std::vector<ojson> buffer_rows;
    for (Buffer* b : buffers) buffer_rows.push_back(buffer_kpis(b));
    write_csv(dir / "buffers.csv", buffer_rows, sim_start);

    std::vector<ojson> op_rows;
    for (OperatorGroup* g : operator_groups) op_rows.push_back(operator_kpis(g));
    write_csv(dir / "operateurs.csv", op_rows, sim_start);

    std::vector<ojson> res_rows;
    for (sim::Resource* r : resources) res_rows.push_back(resource_kpis(r));
    write_csv(dir / "ressources.csv", res_rows, sim_start);

    auto [flux, flux_modeles] = flow_kpis(buffers, gen);
    write_kv_csv(dir / "flux.csv", flux, sim_start);
    write_csv(dir / "flux_modeles.csv", flux_modeles, sim_start);
    write_csv(dir / "temps_traversee.csv", lead_time_rows(buffers), sim_start);
}

}  // namespace kpis
