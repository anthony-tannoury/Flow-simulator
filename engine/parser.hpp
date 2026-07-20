// parser++ — flow JSON -> simulation objects (mirror of parser/parser.py).
//
// This header is being built up in layers. This first layer is the leaf
// builders every load_* method reuses: name normalization + tolerant lookup,
// the string->enum tables, and the distribution / time-function / MTBF /
// protocol builders. The Parser class (load_models, load_shifts, ... load_all)
// is added on top of these.
//
// Read UTF-8 only: nlohmann::json::parse already decodes UTF-8, so the mojibake
// bug that bit the Python side (locale code page) cannot recur here (§15b).
#pragma once

#include "simulation.hpp"

#include "json.hpp"

#include <cctype>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace parser {

using json = nlohmann::json;
using namespace simulation;

// --- name normalization (§15) ----------------------------------------------
// The designer exports canonical identifiers (ByTime, PER_BATCH,
// AbortPendingCarriers), but a hand-edited file may use the sentence-case
// display forms (By time, Per batch). canon_name folds both to one key.
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

// table lookup accepting any spelling canon_name folds together.
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

// --- string -> enum tables (mirror parser.py) ------------------------------
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

inline const std::vector<std::pair<std::string, Scope>>& scopes() {
    static const std::vector<std::pair<std::string, Scope>> t = {
        {"PER_UNIT", Scope::PER_UNIT}, {"PER_BATCH", Scope::PER_BATCH}, {"PER_TASK", Scope::PER_TASK}};
    return t;
}

// --- time function (§10 make_callable) -------------------------------------
// Returns a Param: a bare double for constants, else a function of time.
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

// --- distributions (§18: full designer set incl. LogNormal) ----------------
inline SamplerPtr make_distribution(const json& d) {
    std::vector<Param> params;
    for (const auto& [name, param] : d.at("params").items()) params.push_back(make_callable(param));
    return distribution(lookup(distr_types(), d.at("dist_type").get<std::string>(), "distribution type"),
                        std::move(params));
}

// Output-resource distributions must have constant parameters (they feed a
// Bounded sampler); anything time-varying is rejected, as in Python.
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

// --- protocols (§1/§2 + the shared five) -----------------------------------
// Python's single make_protocol returns Any; C++ needs typed dispatch per slot.
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

// policies.get(field, {"type": default}) — the designer omits a slot to accept
// its default (ConstrainedByShift for the shift slots, etc.).
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

// piece tasks carry the shared five plus the two piece-only protocols; the C++
// config stores the latter two directly (see PieceTaskConfig).
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

}  // namespace parser
