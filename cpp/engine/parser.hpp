#pragma once

#include "simulation.hpp"

#include "json.hpp"

#include <cctype>
#include <chrono>
#include <cstdio>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace parser {

using json = nlohmann::json;
using namespace simulation;


inline std::string canon_name(const std::string& value) {
    std::string out;
    for (char ch : value)
        if (std::isalnum(static_cast<unsigned char>(ch)))
            out += static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return out;
}

inline bool same_name(const std::string& a, const std::string& b) {
    return canon_name(a) == canon_name(b);
}


template <class V>
const V& lookup(const std::vector<std::pair<std::string, V>>& table, const std::string& value,
                const char* what) {
    for (const auto& [k, v] : table)
        if (k == value) return v;
    std::string key = canon_name(value);
    for (const auto& [k, v] : table)
        if (canon_name(k) == key) return v;
    throw std::invalid_argument(std::string("unknown ") + what + ": " + value);
}


inline const std::vector<std::pair<std::string, DistType>>& distr_types() {
    static const std::vector<std::pair<std::string, DistType>> t = {
        {"Constant", DistType::Constant}, {"Uniform", DistType::Uniform},
        {"Normal", DistType::Normal},     {"Exponential", DistType::Exponential},
        {"Triangular", DistType::Triangular}, {"LogNormal", DistType::Lognormal}};
    return t;
}

inline const std::vector<std::pair<std::string, BufferType>>& buffer_types() {
    static const std::vector<std::pair<std::string, BufferType>> t = {
        {"PASSAGE", BufferType::PASSAGE}, {"SCRAP", BufferType::SCRAP}, {"EXIT", BufferType::EXIT}};
    return t;
}

inline const std::vector<std::pair<std::string, PieceCollectorType>>& piece_collector_types() {
    static const std::vector<std::pair<std::string, PieceCollectorType>> t = {
        {"DISCRIMINATING_GREEDY", PieceCollectorType::DISCRIMINATING_GREEDY},
        {"NON_DISCRIMINATING_GREEDY", PieceCollectorType::NON_DISCRIMINATING_GREEDY},
        {"DISCRIMINATING_ALTRUISTIC", PieceCollectorType::DISCRIMINATING_ALTRUISTIC},
        {"NON_DISCRIMINATING_ALTRUISTIC", PieceCollectorType::NON_DISCRIMINATING_ALTRUISTIC}};
    return t;
}

inline const std::vector<std::pair<std::string, ResourceCollectorType>>& resource_collector_types() {
    static const std::vector<std::pair<std::string, ResourceCollectorType>> t = {
        {"GREEDY", ResourceCollectorType::GREEDY}, {"ALTRUISTIC", ResourceCollectorType::ALTRUISTIC}};
    return t;
}

inline const std::vector<std::pair<std::string, AssociationType>>& association_types() {
    static const std::vector<std::pair<std::string, AssociationType>> t = {
        {"PASSIVE", AssociationType::PASSIVE},
        {"ASSOCIATIVE", AssociationType::ASSOCIATIVE},
        {"DISSOCIATIVE", AssociationType::DISSOCIATIVE}};
    return t;
}

inline const std::vector<std::pair<std::string, Scope>>& scopes() {
    static const std::vector<std::pair<std::string, Scope>> t = {
        {"PER_UNIT", Scope::PER_UNIT}, {"PER_BATCH", Scope::PER_BATCH}, {"PER_TASK", Scope::PER_TASK}};
    return t;
}


inline Param make_callable(const json& c) {
    std::string kind = canon_name(c.at("kind").get<std::string>());
    if (kind == "constant") return Param(c.at("value").get<double>());
    if (kind == "linear")
        return Param(Linear::generate(c.at("x1"), c.at("y1"), c.at("x2"), c.at("y2")));
    if (kind == "exponential")
        return Param(ExponentialFn::generate(c.at("x1"), c.at("y1"), c.at("x2"), c.at("y2"),
                                             c.at("limit")));
    if (kind == "step")
        return Param(Step::generate(c.at("x1"), c.at("y1"), c.at("x2"), c.at("y2"),
                                    c.at("step_size")));
    throw std::invalid_argument("unknown time-function kind: " + c.at("kind").get<std::string>());
}

inline bool is_constant(const Param& p) { return std::holds_alternative<double>(p); }


inline SamplerPtr make_distribution(const json& d) {
    std::vector<Param> params;
    for (const auto& [name, param] : d.at("params").items()) params.push_back(make_callable(param));
    return distribution(lookup(distr_types(), d.at("dist_type").get<std::string>(), "distribution type"),
                        std::move(params));
}


inline SamplerPtr make_salabim_distribution(const json& d) {
    std::vector<Param> params;
    for (const auto& [name, param] : d.at("params").items()) {
        Param p = make_callable(param);
        if (!is_constant(p))
            throw std::invalid_argument("output-resource distributions must have constant parameters");
        params.push_back(p);
    }
    return distribution(lookup(distr_types(), d.at("dist_type").get<std::string>(), "distribution type"),
                        std::move(params));
}

inline SamplerPtr make_mtbf(const json& m) {
    std::string mode = canon_name(m.at("mode").get<std::string>());
    if (mode == "distribution") return make_distribution(m.at("distribution"));
    if (mode == "bathtub")
        return std::make_shared<FailureRate>(
            Bathtub::generate(m.at("a"), m.at("tau"), m.at("c"), m.at("beta"), m.at("eta")),
            m.at("tolerance").get<double>(), m.at("max_iters").get<int>());
    throw std::invalid_argument("unknown mtbf mode: " + m.at("mode").get<std::string>());
}


inline std::shared_ptr<PendingCarriers> make_pending_carriers(const json& p) {
    std::string t = canon_name(p.at("type").get<std::string>());
    if (t == "abortpendingcarriers") return std::make_shared<AbortPendingCarriers>();
    if (t == "waitforcarriers") return std::make_shared<WaitForCarriers>();
    if (t == "abortorwaitforcarriers")
        return std::make_shared<AbortOrWaitForCarriers>(p.at("tolerance_fraction").get<double>());
    throw std::invalid_argument("unknown pending-carriers policy: " + p.at("type").get<std::string>());
}

inline std::shared_ptr<ShiftConstraint> make_shift_constraint(const json& p) {
    std::string t = canon_name(p.at("type").get<std::string>());
    if (t == "constrainedbyshift") return std::make_shared<ConstrainedByShift>();
    if (t == "notconstrainedbyshift") return std::make_shared<NotConstrainedByShift>();
    if (t == "partiallyconstrainedbyshift")
        return std::make_shared<PartiallyConstrainedByShift>(p.at("tolerance").get<double>());
    throw std::invalid_argument("unknown shift-constraint policy: " + p.at("type").get<std::string>());
}

inline std::shared_ptr<SelfConsciousness> make_self_consciousness(const json& p) {
    std::string t = canon_name(p.at("type").get<std::string>());
    if (t == "conscious") return std::make_shared<Conscious>();
    if (t == "unconscious") return std::make_shared<Unconscious>();
    throw std::invalid_argument("unknown self-consciousness policy: " + p.at("type").get<std::string>());
}

inline std::shared_ptr<PieceExitOrder> make_piece_exit_order(const json& p) {
    std::string t = canon_name(p.at("type").get<std::string>());
    if (t == "firstinfirstout") return std::make_shared<FirstInFirstOut>();
    if (t == "firstcreatedfirstout") return std::make_shared<FirstCreatedFirstOut>();
    throw std::invalid_argument("unknown piece-exit-order policy: " + p.at("type").get<std::string>());
}

inline std::shared_ptr<ModelChoiceCriteria> make_model_choice(const json& p) {
    std::string t = canon_name(p.at("type").get<std::string>());
    if (t == "mostpresent") return std::make_shared<MostPresent>();
    if (t == "fastesttaskduration") return std::make_shared<FastestTaskDuration>();
    if (t == "smallestgaptomincarriercapacity")
        return std::make_shared<SmallestGapToMinCarrierCapacity>();
    throw std::invalid_argument("unknown batch-model-choice policy: " + p.at("type").get<std::string>());
}


inline json policy_or(const json& policies, const char* field, const char* default_type) {
    if (policies.contains(field)) return policies.at(field);
    return json{{"type", default_type}};
}

inline Protocols make_protocols(const json& policies) {
    return Protocols{
        .pending_carriers_pre_flexible_shutdowns = make_pending_carriers(
            policy_or(policies, "pending_carriers_pre_flexible_shutdowns", "AbortPendingCarriers")),
        .pending_carrier_pre_task_shift_end = make_pending_carriers(
            policy_or(policies, "pending_carrier_pre_task_shift_end", "AbortPendingCarriers")),
        .operator_shift_constraint = make_shift_constraint(
            policy_or(policies, "operator_shift_constraint", "ConstrainedByShift")),
        .task_shift_constraint =
            make_shift_constraint(policy_or(policies, "task_shift_constraint", "ConstrainedByShift")),
        .operators_self_conscious =
            make_self_consciousness(policy_or(policies, "operators_self_conscious", "Conscious")),
    };
}


struct PieceProtocolBundle {
    Protocols shared;
    std::shared_ptr<PieceExitOrder> piece_exit_order;
    std::shared_ptr<ModelChoiceCriteria> batch_model_choice;
};

inline PieceProtocolBundle make_piece_protocols(const json& policies) {
    return {make_protocols(policies),
            make_piece_exit_order(policy_or(policies, "piece_exit_order", "FirstInFirstOut")),
            make_model_choice(policy_or(policies, "batch_model_choice", "MostPresent"))};
}


inline ShiftManager::days_t parse_date(const std::string& s) {
    int d = 0, m = 0, y = 0;
    std::sscanf(s.c_str(), "%d-%d-%d", &d, &m, &y);
    return std::chrono::sys_days{std::chrono::year{y} / std::chrono::month{unsigned(m)} /
                                 std::chrono::day{unsigned(d)}};
}

inline ShiftManager::DateTime parse_datetime(const std::string& s) {
    int d = 0, m = 0, y = 0, hh = 0, mm = 0;
    std::sscanf(s.c_str(), "%d-%d-%d %d:%d", &d, &m, &y, &hh, &mm);
    return {std::chrono::sys_days{std::chrono::year{y} / std::chrono::month{unsigned(m)} /
                                  std::chrono::day{unsigned(d)}},
            hh, mm};
}


inline std::chrono::sys_days shift_sysdays(std::chrono::sys_days d, long long k, const json& rep) {
    using namespace std::chrono;
    year_month_day ymd{d};
    long long months = k * (rep.value("years", 0LL) * 12 + rep.value("months", 0LL));
    long long total = static_cast<long long>(static_cast<int>(ymd.year())) * 12
                      + (static_cast<unsigned>(ymd.month()) - 1) + months;
    int y = static_cast<int>(total / 12);
    unsigned mo = static_cast<unsigned>(total % 12) + 1;
    year_month_day target = year{y} / month{mo} / ymd.day();
    sys_days base = target.ok() ? sys_days{target} : sys_days{year{y} / month{mo} / std::chrono::last};
    long long fixed = k * (rep.value("weeks", 0LL) * 7 + rep.value("days", 0LL));
    return base + days{fixed};
}

inline ShiftManager::DateTime shift_datetime(const ShiftManager::DateTime& dt, long long k, const json& rep) {
    return {shift_sysdays(dt.date, k, rep), dt.hour, dt.minute};
}

inline double to_minutes(const std::string& s) {
    int hh = 0, mm = 0;
    std::sscanf(s.c_str(), "%d:%d", &hh, &mm);
    return 60.0 * hh + mm;
}


inline double parse_float(const json& v) {
    if (v.is_string()) {
        std::string s = v.get<std::string>();
        std::string c = canon_name(s);
        if (c == "inf" || c == "inf0" || c == "infinity") return sim::inf;
        if (!s.empty() && s[0] == '-' && (canon_name(s.substr(1)) == "inf")) return -sim::inf;
        return std::stod(s);
    }
    return v.get<double>();
}

inline Intervals join_shifts(const std::vector<Intervals>& parts) {
    Intervals joined;
    for (const auto& part : parts)
        for (const auto& iv : part) joined.push_back(iv);
    return joined;
}


class Parser {
  public:
    json data;
    ShiftManager::DateTime sim_start;

    std::map<std::string, const json*> by_id;
    std::map<std::string, std::vector<const json*>> per_kind;

    std::map<std::string, Model*> models;
    std::map<std::string, ShiftManager::days_t> closing_days;
    std::map<std::string, Intervals> shifts;
    std::map<std::string, Resource*> resources;
    std::map<std::string, OperatorGroup*> operator_groups;
    std::map<std::string, Outlet*> outlets;
    std::vector<std::string> scrap_buffers_ids;
    std::map<std::string, Task*> tasks;
    std::vector<std::string> task_order;
    PieceGenerator* piece_generator = nullptr;
    StoppingCriterion* stopping_criterion = nullptr;

    explicit Parser(const std::string& flow_json_text) {
        data = json::parse(flow_json_text);
        sim_start = parse_datetime(data.at("start_date").get<std::string>());
        drop_disabled_nodes();
        discriminate();
        for (const auto& n : data.at("nodes")) by_id[n.at("id").get<std::string>()] = &n;
    }

    void load_all() {
        load_models();
        load_closing_days();
        load_shifts();
        load_resources();
        load_operators();
        load_non_scrap_buffers();
        load_routers(false);
        load_piece_generator();
        load_scrap_buffers();
        load_routers(true);
        load_piece_tasks();
        load_resource_tasks();
        load_shutdowns();
        load_breakdowns();
        load_stopping_criterion();
    }

    std::vector<Buffer*> buffer_list() const {
        std::vector<Buffer*> out;
        for (const auto& [id, o] : outlets)
            if (auto* b = dynamic_cast<Buffer*>(o)) out.push_back(b);
        return out;
    }

    Buffer* exit_buffer() const {
        for (const auto& [id, o] : outlets)
            if (auto* b = dynamic_cast<Buffer*>(o); b && b->buffer_type == BufferType::EXIT) return b;
        return nullptr;
    }

  private:
    const std::vector<const json*>& nodes_of(const char* kind) {
        static const std::vector<const json*> empty;
        auto it = per_kind.find(kind);
        return it == per_kind.end() ? empty : it->second;
    }

    Intervals join_named_shifts(const json& ids) {
        std::vector<Intervals> parts;
        for (const auto& id : ids) parts.push_back(shifts.at(id.get<std::string>()));
        return join_shifts(parts);
    }

    IntervalPtr to_interval(const json& iv) {
        return interval(
            double(ShiftManager::minutes_between(sim_start, parse_datetime(iv.at("start").get<std::string>()))),
            double(ShiftManager::minutes_between(sim_start, parse_datetime(iv.at("end").get<std::string>()))));
    }

    Alternative make_alternative(const json& alternatives) {
        std::vector<Alternative::OpsList> alts;
        for (const auto& alt : alternatives) {
            Alternative::OpsList ops;
            for (const auto& m : alt)
                ops.push_back({operator_groups.at(m.at("operator").get<std::string>()),
                               m.at("count").get<int>()});
            alts.push_back(std::move(ops));
        }
        return Alternative(std::move(alts));
    }

    std::vector<std::pair<Model*, ModelConfig>> make_models_configs(const json& list) {
        std::vector<std::pair<Model*, ModelConfig>> out;
        std::map<std::string, SamplerPtr> durations;
        for (const auto& mc : list) {
            Model* model = models.at(mc.at("model").get<std::string>());
            std::string key = mc.at("duration").dump();
            auto it = durations.find(key);
            if (it == durations.end()) it = durations.emplace(key, make_distribution(mc.at("duration"))).first;
            std::vector<std::pair<Resource*, double>> res;
            for (const auto& r : mc.at("resources"))
                res.push_back({resources.at(r.at("resource").get<std::string>()), r.at("value").get<double>()});
            out.push_back({model, ModelConfig{it->second, std::move(res),
                                              mc.at("min_carrier_capacity").get<int>(),
                                              mc.at("max_carrier_capacity").get<int>()}});
        }
        return out;
    }

    bool touches_scrap(const json& router) {
        for (const auto& entry : router.at("buffer_probs")) {
            const json& target = *by_id.at(entry.at("buffer").get<std::string>());
            if (target.at("kind") == "Router")
                throw std::invalid_argument("router-to-router chains are not supported");
            if (same_name(target.at("buffer_type").get<std::string>(), "SCRAP")) return true;
        }
        return false;
    }


    void drop_disabled_nodes() {
        if (!data.contains("nodes")) return;
        std::set<std::string> dead;
        for (const auto& n : data.at("nodes"))
            if (n.contains("enabled") && n.at("enabled").is_boolean() && !n.at("enabled").get<bool>())
                dead.insert(n.at("id").get<std::string>());
        if (dead.empty()) return;

        for (const auto& n : data.at("nodes"))
            if (n.at("kind") == "Breakdown" && n.contains("task") && n.at("task").is_string()
                && dead.count(n.at("task").get<std::string>()))
                dead.insert(n.at("id").get<std::string>());
        json kept = json::array();
        for (auto& n : data.at("nodes")) {
            if (dead.count(n.at("id").get<std::string>())) continue;
            for (const char* key : {"bufs_in", "bufs_out", "outlets", "shutdowns",
                                    "breakdowns", "inputs_from"}) {
                if (!n.contains(key)) continue;
                json filtered = json::array();
                for (const auto& id : n.at(key))
                    if (!(id.is_string() && dead.count(id.get<std::string>()))) filtered.push_back(id);
                n[key] = std::move(filtered);
            }
            if (n.at("kind") == "Router" && n.contains("buffer_probs")) {
                json filtered = json::array();
                for (const auto& e : n.at("buffer_probs"))
                    if (!dead.count(e.at("buffer").get<std::string>())) filtered.push_back(e);
                n["buffer_probs"] = std::move(filtered);
            }
            kept.push_back(std::move(n));
        }
        data["nodes"] = std::move(kept);
        if (data.contains("connections")) {
            json conns = json::array();
            for (const auto& c : data.at("connections"))
                if (!dead.count(c.value("from_node", "")) && !dead.count(c.value("to_node", "")))
                    conns.push_back(c);
            data["connections"] = std::move(conns);
        }
    }

    void discriminate() {
        for (const auto& node : data.at("nodes"))
            per_kind[node.at("kind").get<std::string>()].push_back(&node);
    }

    void load_models() {
        for (const auto& m : data.at("models"))
            models[m.at("id").get<std::string>()] = new Model(m.at("name").get<std::string>());
        for (const auto& m : data.at("models"))
            if (!m.at("parent").is_null())
                models.at(m.at("id").get<std::string>())
                    ->set_parent(models.at(m.at("parent").get<std::string>()));
    }

    void load_closing_days() {
        for (const auto& cd : data.at("closing_days"))
            closing_days[cd.at("id").get<std::string>()] = parse_date(cd.at("date").get<std::string>());
    }

    void load_shifts() {
        for (const auto& shift : data.at("shifts")) {
            const std::string sid = shift.at("id").get<std::string>();
            const std::string mode = canon_name(shift.at("mode").get<std::string>());
            const json rep = shift.contains("repeat") ? shift.at("repeat") : json::object();

            std::set<long long> base_days_off;
            for (const auto& d : shift.at("days_off"))
                base_days_off.insert(closing_days.at(d.get<std::string>()).time_since_epoch().count());


            std::vector<bool> working_days;
            std::vector<std::vector<std::pair<double, double>>> shifts_per_day;
            if (mode == "weekly") {
                for (const auto& d : shift.at("days")) {
                    working_days.push_back(d.at("working").get<bool>());
                    std::vector<std::pair<double, double>> day;
                    for (const auto& s : d.at("intervals"))
                        day.push_back({to_minutes(s.at("start").get<std::string>()),
                                       to_minutes(s.at("end").get<std::string>())});
                    shifts_per_day.push_back(std::move(day));
                }
            } else if (mode != "custom") {
                throw std::invalid_argument("unknown shift mode: " + shift.at("mode").get<std::string>());
            }


            auto generate = [&](long long k) -> Intervals {
                std::set<long long> days_off;
                for (long long c : base_days_off)
                    days_off.insert(k == 0 ? c
                        : shift_sysdays(std::chrono::sys_days{std::chrono::days(c)}, k, rep)
                              .time_since_epoch().count());
                if (mode == "weekly") {
                    auto start = parse_date(shift.at("horizon").at("start").get<std::string>());
                    auto end = parse_date(shift.at("horizon").at("end").get<std::string>());
                    if (k > 0) { start = shift_sysdays(start, k, rep); end = shift_sysdays(end, k, rep); }
                    return ShiftManager::generate_weekly_shifts(sim_start, shifts_per_day, working_days,
                                                                days_off, start, end);
                }
                std::vector<std::pair<ShiftManager::DateTime, ShiftManager::DateTime>> intervals;
                for (const auto& i : shift.at("custom_intervals")) {
                    auto s = parse_datetime(i.at("start").get<std::string>());
                    auto e = parse_datetime(i.at("end").get<std::string>());
                    if (k > 0) { s = shift_datetime(s, k, rep); e = shift_datetime(e, k, rep); }
                    intervals.push_back({s, e});
                }
                return ShiftManager::generate_custom_shifts(sim_start, intervals, days_off);
            };

            Intervals result = generate(0);

            long long count = rep.value("count", 0LL);
            bool has_translation = rep.value("years", 0LL) || rep.value("months", 0LL)
                                   || rep.value("weeks", 0LL) || rep.value("days", 0LL);
            if (count > 0 && has_translation) {
                Intervals pieces = result;
                for (long long k = 1; k <= count; ++k) {
                    Intervals copy = generate(k);
                    pieces.insert(pieces.end(), copy.begin(), copy.end());
                }
                std::sort(pieces.begin(), pieces.end(),
                          [](const IntervalPtr& a, const IntervalPtr& b) { return a->start < b->start; });
                Intervals merged;
                for (const auto& iv : pieces) {
                    if (!merged.empty() && iv->start <= merged.back()->end) {
                        if (iv->end > merged.back()->end) merged.back()->end = iv->end;
                    } else {
                        merged.push_back(interval(iv->start, iv->end));
                    }
                }
                result = merged;
            }
            shifts[sid] = result;
        }
    }

    void load_resources() {
        for (const auto& r : data.at("resources")) {
            std::string name = r.at("name").get<std::string>();
            double lifespan = parse_float(r.at("lifespan"));
            double capacity = r.at("max_capacity").get<double>();
            double initial = r.at("initial_capacity").get<double>();
            std::string id = r.at("id").get<std::string>();
            if (r.at("restockable").get<bool>()) {
                resources[id] = new RestockableResource(
                    name, capacity, make_distribution(r.at("order_duration")),
                    make_distribution(r.at("delivery_duration")), r.at("threshold").get<double>(),
                    initial, lifespan);
            } else {
                resources[id] = new Resource(name, capacity, initial, lifespan);
            }
        }
    }

    void load_operators() {
        std::map<std::string, SamplerPtr> productivities;
        for (const auto& op : data.at("operators")) {
            std::string key = op.at("productivity").dump();
            auto it = productivities.find(key);
            if (it == productivities.end())
                it = productivities.emplace(key, make_distribution(op.at("productivity"))).first;
            operator_groups[op.at("id").get<std::string>()] =
                new OperatorGroup(op.at("name").get<std::string>(), op.at("capacity").get<double>(),
                                  join_named_shifts(op.at("shifts")), it->second);
        }
    }

    void load_non_scrap_buffers() {
        for (const json* bp : nodes_of("Buffer")) {
            const json& b = *bp;
            std::string id = b.at("id").get<std::string>();
            if (same_name(b.at("buffer_type").get<std::string>(), "SCRAP")) {
                scrap_buffers_ids.push_back(id);
                continue;
            }
            std::vector<Model*> vm;
            for (const auto& m : b.at("valid_models")) vm.push_back(models.at(m.get<std::string>()));
            outlets[id] = new Buffer(b.at("name").get<std::string>(), vm,
                                     lookup(buffer_types(), b.at("buffer_type").get<std::string>(),
                                            "buffer type"));
        }
    }

    void load_routers(bool with_scrap) {
        for (const json* rp : nodes_of("Router")) {
            const json& r = *rp;
            if (touches_scrap(r) != with_scrap) continue;
            std::vector<std::pair<Outlet*, Router::Prob>> op;
            for (const auto& e : r.at("buffer_probs")) {
                Outlet* target = outlets.at(e.at("buffer").get<std::string>());
                Router::Prob prob = e.at("probability").is_null()
                                        ? Router::Prob{}
                                        : Router::Prob{make_callable(e.at("probability"))};
                op.push_back({target, prob});
            }
            outlets[r.at("id").get<std::string>()] = new Router(std::move(op));
        }
    }

    void load_piece_generator() {
        const auto& generators = nodes_of("PieceGenerator");
        if (generators.size() != 1)
            throw std::invalid_argument("the flow needs exactly one enabled piece generator, found " +
                                        std::to_string(generators.size()));
        const json& node = *generators.at(0);
        const json& criterion = data.at("stopping_criterion");
        for (const auto& id : node.at("outlets"))
            if (!outlets.count(id.get<std::string>()))
                throw std::invalid_argument("piece generator outlet routes into a scrap buffer");

        Intervals shifts_ = join_named_shifts(node.at("shifts"));
        std::vector<Outlet*> outs;
        for (const auto& id : node.at("outlets")) outs.push_back(outlets.at(id.get<std::string>()));
        std::string name = node.at("name").get<std::string>();

        std::string type = canon_name(criterion.at("type").get<std::string>());
        if (type == "bypiecesproduced") {
            std::vector<std::pair<Model*, int>> goals;
            for (const auto& mg : criterion.at("models_goals"))
                goals.push_back({models.at(mg.at("model").get<std::string>()), mg.at("goal").get<int>()});
            double grace = 0.0;
            std::optional<double> gap;
            if (criterion.contains("gap") && !criterion.at("gap").is_null())
                gap = parse_float(criterion.at("gap"));
            else if (criterion.contains("grace_period"))
                grace = parse_float(criterion.at("grace_period"));
            piece_generator = sim::make<GoalPieceGenerator>({.name = name}, goals, shifts_, outs, grace, gap);
        } else if (type == "bytime") {
            std::vector<Model*> ms;
            std::vector<std::optional<std::variant<double, TimeFn>>> probs;
            for (const auto& mp : criterion.at("models_probs")) {
                ms.push_back(models.at(mp.at("model").get<std::string>()));
                if (mp.at("probability").is_null()) probs.push_back(std::nullopt);
                else probs.push_back(make_callable(mp.at("probability")));
            }
            std::variant<double, TimeFn> gap = make_callable(criterion.at("gap"));
            piece_generator =
                sim::make<RatePieceGenerator>({.name = name}, ms, shifts_, outs, gap, probs);
        } else {
            throw std::invalid_argument("unknown stopping criterion type: " +
                                        criterion.at("type").get<std::string>());
        }
    }

    void load_scrap_buffers() {
        for (const std::string& id : scrap_buffers_ids) {
            const json& b = *by_id.at(id);
            std::vector<Model*> vm;
            for (const auto& m : b.at("valid_models")) vm.push_back(models.at(m.get<std::string>()));
            outlets[id] = new Buffer(b.at("name").get<std::string>(), vm, BufferType::SCRAP,
                                     piece_generator);
        }
    }

    std::vector<Outlet*> resolve_outlets(const json& ids) {
        std::vector<Outlet*> out;
        for (const auto& id : ids) out.push_back(outlets.at(id.get<std::string>()));
        return out;
    }

    std::vector<Buffer*> resolve_buffers(const json& ids) {
        std::vector<Buffer*> out;
        for (const auto& id : ids)
            out.push_back(dynamic_cast<Buffer*>(outlets.at(id.get<std::string>())));
        return out;
    }

    void fill_common_config(TaskConfig* cfg, const json& t, const Protocols& protocols) {
        cfg->task_shifts = join_named_shifts(t.at("task_shifts"));
        cfg->startup_duration = make_distribution(t.at("startup_duration"));
        cfg->loading_duration = make_distribution(t.at("loading_duration"));
        cfg->startup_operators = make_alternative(t.at("startup_operators"));
        cfg->loading_operators = make_alternative(t.at("loading_operators"));
        cfg->operators = make_alternative(t.at("operators"));
        cfg->operator_scope = lookup(scopes(), t.at("operator_scope").get<std::string>(), "operator scope");
        cfg->resource_scope = lookup(scopes(), t.at("resource_scope").get<std::string>(), "resource scope");
        cfg->min_carriers = t.at("min_carriers").get<int>();
        cfg->max_capacity = t.at("max_capacity").get<double>();
        cfg->timeout = parse_float(t.at("timeout"));
        cfg->priority = t.at("priority").get<int>();
        cfg->admin = t.value("admin", false);
        cfg->contiguous_carriers = t.at("contiguous_carriers").get<bool>();
        cfg->independent_carriers = t.at("independent_carriers").get<bool>();
        cfg->protocols = protocols;
    }

    void load_piece_tasks() {
        for (const json* tp : nodes_of("Task")) {
            const json& t = *tp;
            auto bundle = make_piece_protocols(t.at("policies"));
            auto cfg = std::make_shared<PieceTaskConfig>();
            fill_common_config(cfg.get(), t, bundle.shared);
            cfg->piece_exit_order = bundle.piece_exit_order;
            cfg->batch_model_choice = bundle.batch_model_choice;
            cfg->models_configs = make_models_configs(t.at("models_configs"));
            cfg->piece_collector_type =
                lookup(piece_collector_types(), t.at("collector_type").get<std::string>(), "collector type");
            cfg->association_type = lookup(association_types(),
                                           t.value("association_type", std::string("PASSIVE")),
                                           "association type");
            std::string id = t.at("id").get<std::string>();
            tasks[id] = sim::make<PieceTask>({.name = t.at("name").get<std::string>()}, cfg,
                                             resolve_buffers(t.at("bufs_in")), resolve_outlets(t.at("bufs_out")));
            task_order.push_back(id);
        }
    }

    void load_resource_tasks() {
        for (const json* tp : nodes_of("ResourceTask")) {
            const json& t = *tp;
            auto cfg = std::make_shared<ResourceTaskConfig>();
            fill_common_config(cfg.get(), t, make_protocols(t.at("policies")));
            for (const auto& r : t.at("non_transformed_resources"))
                cfg->non_transformed_resources.push_back(
                    {resources.at(r.at("resource").get<std::string>()), r.at("value").get<double>()});
            for (const auto& r : t.at("transformed_resources"))
                cfg->transformed_resources_salvageable.push_back(
                    TransformedResource{resources.at(r.at("resource").get<std::string>()),
                                        r.at("proportion").get<double>(), r.at("salvageable").get<bool>()});
            for (const auto& r : t.at("resources_out"))
                cfg->resources_out_distr.push_back(
                    {resources.at(r.at("resource").get<std::string>()),
                     Bounded{make_salabim_distribution(r.at("distribution")),
                             r.at("lowerbound").get<double>(), r.at("upperbound").get<double>()}});
            cfg->duration = make_distribution(t.at("duration"));
            cfg->resource_collector_type = lookup(resource_collector_types(),
                                                  t.at("resource_collector_type").get<std::string>(),
                                                  "resource collector type");
            cfg->min_carrier_capacity = t.at("min_carrier_capacity").get<double>();
            cfg->max_carrier_capacity = t.at("max_carrier_capacity").get<double>();
            std::string id = t.at("id").get<std::string>();
            tasks[id] = sim::make<ResourceTask>({.name = t.at("name").get<std::string>()}, cfg);
            task_order.push_back(id);
        }
    }

    void load_shutdowns() {
        std::vector<const json*> task_nodes = nodes_of("Task");
        for (const json* p : nodes_of("ResourceTask")) task_nodes.push_back(p);
        for (const json* tp : task_nodes) {
            const json& tn = *tp;
            Task* task = tasks.at(tn.at("id").get<std::string>());
            for (const auto& sid : tn.at("shutdowns")) {
                const json& sn = *by_id.at(sid.get<std::string>());
                Intervals intervals;
                std::string mode = canon_name(sn.value("mode", "custom"));
                if (mode == "custom") {
                    for (const auto& i : sn.at("intervals")) intervals.push_back(to_interval(i));
                } else if (mode == "generator") {
                    const json& g = sn.at("generator");
                    intervals = Shutdowns::generate_periodic_shutdown(
                        task, g.at("in_between").get<double>(), g.at("duration").get<double>(), sim_start,
                        parse_datetime(g.at("start").get<std::string>()),
                        parse_datetime(g.at("end").get<std::string>()));
                } else {
                    throw std::invalid_argument("unknown shutdowns mode: " + sn.at("mode").get<std::string>());
                }
                std::string stype = canon_name(sn.at("shutdown_type").get<std::string>());
                if (stype == "flexible") sim::make<FlexibleShutdowns>({}, task, intervals);
                else if (stype == "nonflexible") sim::make<NonFlexibleShutdowns>({}, task, intervals);
                else throw std::invalid_argument("unknown shutdown type: " +
                                                 sn.at("shutdown_type").get<std::string>());
            }
        }
    }

    void load_breakdowns() {
        for (const json* bp : nodes_of("Breakdown")) {
            const json& b = *bp;
            sim::make<Breakdown>({.name = b.at("name").get<std::string>()},
                                 tasks.at(b.at("task").get<std::string>()), make_mtbf(b.at("mtbf")),
                                 make_distribution(b.at("mttr")), resolve_outlets(b.at("outlets")));
        }
    }

    void load_stopping_criterion() {
        const json& criterion = data.at("stopping_criterion");
        std::string type = canon_name(criterion.at("type").get<std::string>());
        if (type == "bytime") {
            double minutes = double(ShiftManager::minutes_between(
                sim_start, parse_datetime(criterion.at("time").get<std::string>())));
            stopping_criterion = sim::make<ByTime>({}, minutes);
        } else if (type == "bypiecesproduced") {
            int total = 0;
            for (const auto& mg : criterion.at("models_goals")) total += mg.at("goal").get<int>();
            Buffer* exit_b = exit_buffer();
            if (exit_b == nullptr)
                throw std::invalid_argument("no enabled EXIT buffer to count produced pieces on");
            stopping_criterion =
                sim::make<ByPiecesProduced>({}, total, exit_b, parse_float(criterion.at("timeout")));
        } else {
            throw std::invalid_argument("unknown stopping criterion type: " +
                                        criterion.at("type").get<std::string>());
        }
        sim::make<SimulationStopper>({}, stopping_criterion);
    }
};

}
