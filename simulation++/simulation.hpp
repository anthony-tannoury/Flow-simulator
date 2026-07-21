// ============================================================================
// simulation++ — the factory piece-flow simulation, translated from Python
// (simulation/*.py) onto salabim++ (salabim.hpp).
//
// The translation mirrors the Python modules section by section — same class
// names, same logic, same validation messages — so the two codebases read in
// parallel. Runs are seeded and deterministic, but they do not reproduce
// Python runs draw for draw; only the behaviour is the same. Python runs
// salabim in yieldless mode where helper methods block internally; here every
// such helper is a sim::Process coroutine executed with `co_await call(...)`
// (the sub-process facility of salabim++).
//
// Conventions carried across the whole file:
//   * Python `self.hold/wait/from_store(...)`      -> `co_await hold/wait/from_store(...)`
//   * blocking helper method                        -> sim::Process + `co_await call(...)`
//   * results of blocking helpers                   -> out-parameters
//   * interaction on ANOTHER component              -> plain call (salabim rule)
//   * Python object identity ('is', list sharing)   -> shared_ptr / raw pointers
//   * ValueError                                    -> std::invalid_argument (same text)
//   * yieldless "cancel kills the greenlet"         -> cancel(); co_await sim::Yield{};
//     (the scheduler reaps the abandoned coroutine chain, so — like the dead
//     greenlet — code after an abort never runs)
// ============================================================================
#pragma once

#include "salabim.hpp"

#include <algorithm>
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

namespace simulation {

// fwd decls (Python resolves these with deferred imports)
class Buffer;
class Outlet;
class PieceGenerator;
class Resource;
class Task;
class PieceTask;
class ResourceTask;
struct Model;
class Piece;

// ============================================================================
// __init__.py — module globals: the environment and the seed
// ============================================================================

inline long long SEED = 0;
inline sim::Environment* env = nullptr;

// Python draws model/router choices from numpy's RNG (a stream separate from
// salabim's); here we simply use salabim's stream. Same seeded determinism and
// the same distributions — but C++ runs do not reproduce Python runs draw for
// draw, only their behaviour.

// Class-level counters (Python class attributes); reset by init().
namespace counters {
inline int piece_id = 0;          // Piece.ID
inline int piece_generators = 0;  // PieceGenerator.COUNT
inline int exit_buffers = 0;      // Buffer.EXIT_BUFFERS
}  // namespace counters

// Global work-in-progress level monitor (kpis.WIP): +1 when a piece is born,
// -1 when it reaches an EXIT or SCRAP buffer. A level Monitor integrates the
// running level over time (mean/maximum); wip_level is the current level.
namespace kpis_state {
inline sim::Monitor* WIP = nullptr;
inline int wip_level = 0;
}  // namespace kpis_state

// Create a fresh environment + reset all module state (Python: import time).
inline sim::Environment& init(long long seed = 0, bool trace = false) {
    delete env;
    SEED = seed;
    env = new sim::Environment({.trace = trace, .random_seed = seed});
    counters::piece_id = 0;
    counters::piece_generators = 0;
    counters::exit_buffers = 0;
    kpis_state::WIP = new sim::Monitor("wip", sim::MonitorOpts{.level = true});
    kpis_state::wip_level = 0;
    return *env;
}

// np.random.choice(len(p), p=p): pick an index with the given probabilities.
inline int weighted_choice(const std::vector<double>& p) {
    double u = sim::random_stream().random();
    double acc = 0.0;
    for (size_t i = 0; i < p.size(); ++i) {
        acc += p[i];
        if (u < acc) return static_cast<int>(i);
    }
    return static_cast<int>(p.size()) - 1;
}

// ============================================================================
// ables.py
// ============================================================================

struct Triggerable {
    sim::State<bool> trigger{};
    virtual ~Triggerable() = default;
};

struct Dispatchable {
    sim::State<bool> allow_dispatch{"", false};
    virtual ~Dispatchable() = default;
};

struct Donnable {
    sim::State<bool> done{"", false};
    virtual ~Donnable() = default;
};

// ============================================================================
// component.py — Component with the request/release post-processing hooks
// (shave expiring resources; trigger Triggerable resources on put/release)
// ============================================================================

class Component : public sim::Component {
  public:
    // Python: overridden request(); after a successful request, shave expiring
    // resources and trigger on negative quantities. Blocking form (current
    // component): co_await call(request({...}, {...})).
    sim::Process request(std::vector<sim::ReqSpec> specs, sim::RequestOpts opts = {}) {
        co_await sim::Component::request(specs, std::move(opts));
        if (!failed()) after_request_(specs);
    }

    // Non-blocking form for interactions issued on a NON-current component
    // (only the replenish-during-abort path needs this; mirrors Python, where
    // that request is honored immediately or dies with the cancelled carrier).
    void request_nb(std::vector<sim::ReqSpec> specs, sim::RequestOpts opts = {}) {
        sim::Component::request(specs, opts);  // not awaited: no suspension
        if (!failed()) after_request_(specs);
    }

    // Python: overridden release() — trigger Triggerable resources, then release.
    void release() {
        for (sim::Resource* r : claimed_resources()) trigger_if_(r);
        sim::Component::release();
    }
    void release(std::vector<sim::ReqSpec> specs) {
        if (specs.empty()) { release(); return; }  // Python release(*[]) == release()
        for (auto& s : specs) trigger_if_(s.r);
        for (auto& s : specs) sim::Component::release(*s.r, s.q);
    }

  private:
    static void trigger_if_(sim::Resource* r);      // defined after Resource
    void after_request_(const std::vector<sim::ReqSpec>& specs);  // defined after Resource
};

// ============================================================================
// interval.py
// ============================================================================

inline double Time(double h, double m, double s = 0) { return 60 * h + m + s / 60; }

struct Interval {
    double start;
    double end;

    Interval(double start_, double end_) : start(start_), end(end_) {
        if (end < start) throw std::invalid_argument("Interval start must be before interval end");
    }

    double length() const { return end - start; }
    void translate(double t) { start += t; end += t; }
    bool disjoint(const Interval& other) const {
        return std::min(end, other.end) < std::max(start, other.start);
    }
    Interval copy() const { return Interval(start, end); }
};

// Python interval objects are shared/mutated by reference (flexible shutdowns
// translate and remove them); shared_ptr mirrors that identity semantics.
using IntervalPtr = std::shared_ptr<Interval>;
using Intervals = std::vector<IntervalPtr>;

inline IntervalPtr interval(double start, double end) { return std::make_shared<Interval>(start, end); }

// ============================================================================
// helpers.py (checks; the outlet-related helpers are defined after outlet.py)
// ============================================================================

inline void check_disjoint_sorted_intervals(const Intervals& intervals) {
    for (size_t i = 1; i < intervals.size(); ++i)
        if (!intervals[i]->disjoint(*intervals[i - 1]))
            throw std::invalid_argument("Intervals must be pairwise disjoint");
}

// Intervals that touch exactly on the border become one (a night shift crossing
// midnight, an operator's back-to-back schedules). Untouched entries keep their
// identity; merged ones are fresh objects. Strict overlaps still fail the check.
inline Intervals merge_touching_sorted_intervals(const Intervals& intervals) {
    Intervals merged;
    for (const IntervalPtr& iv : intervals) {
        if (!merged.empty() && iv->start == merged.back()->end)
            merged.back() = interval(merged.back()->start, iv->end);
        else
            merged.push_back(iv);
    }
    return merged;
}

inline void check_probabilities(const std::vector<double>& probs) {
    for (double p : probs)
        if (!(0 <= p && p <= 1)) throw std::invalid_argument("Probabilities must be in [0,1]");
    double sum = 0;
    for (double p : probs) sum += p;
    if (std::abs(sum - 1) > 1e-6) throw std::invalid_argument("Probabilities must sum to 1");
}

// (check_outlet_validity and place are defined after Outlet/Buffer, like the
// Python deferred imports)

// ---------------------------------------------------------------------------
// interval.py — IntervalWaiter
// ---------------------------------------------------------------------------

class IntervalWaiter : public Component {
  public:
    Intervals intervals;

    explicit IntervalWaiter(Intervals intervals_) {
        std::sort(intervals_.begin(), intervals_.end(),
                  [](const IntervalPtr& a, const IntervalPtr& b) { return a->start < b->start; });
        intervals_ = merge_touching_sorted_intervals(intervals_);
        check_disjoint_sorted_intervals(intervals_);
        intervals = std::move(intervals_);
    }

    virtual void on_enter() = 0;
    virtual void on_leave() = 0;

    sim::Process process() override {
        for (size_t i = 0; i < intervals.size(); ++i) {  // index loop: intervals may shrink
            IntervalPtr iv = intervals[i];
            co_await hold(sim::HoldOpts{.till = iv->start, .cap_now = true});
            on_enter();
            co_await hold(sim::HoldOpts{.till = iv->end, .cap_now = true});
            on_leave();
        }
    }
};

// ============================================================================
// function_generator.py
// ============================================================================

using TimeFn = std::function<double(double)>;

struct Linear {
    static TimeFn generate(double x1, double y1, double x2, double y2) {
        if (x1 == x2) throw std::invalid_argument("Cannot generate vertical line function");
        return [=](double t) {
            double slope = (y1 - y2) / (x1 - x2);
            double intercept = y1 - slope * x1;
            return slope * t + intercept;
        };
    }
};

struct ExponentialFn {  // Python class name: Exponential (renamed: clashes with sim::Exponential)
    static TimeFn generate(double x1, double y1, double x2, double y2, double limit) {
        if (x1 == x2) throw std::invalid_argument("Cannot generate vertical exponential function");
        if ((y1 - limit) * (y2 - limit) <= 0)
            throw std::invalid_argument(
                "y1 and y2 in exponential function must be on the same side compared to limit");
        return [=](double t) {
            double beta = std::log((y1 - limit) / (y2 - limit)) / (x1 - x2);
            double alpha = (y1 - limit) / std::exp(beta * x1);
            return alpha * std::exp(beta * t) + limit;
        };
    }
};

struct Bathtub {
    static TimeFn generate(double a, double tau, double c, double beta, double eta) {
        return [=](double t) {
            return a * std::exp(t / tau) + c + (beta / eta) * std::pow(t / eta, beta - 1);
        };
    }
};

// Staircase following the line through (x1,y1)-(x2,y2) but holding each value
// for `step_size` on the x axis (Python: function_generator.Step).
struct Step {
    static TimeFn generate(double x1, double y1, double x2, double y2, double step_size) {
        if (x1 == x2) throw std::invalid_argument("Cannot generate a step function over a vertical span");
        if (step_size <= 0) throw std::invalid_argument("Step size must be > 0");
        return [=](double t) {
            double slope = (y2 - y1) / (x2 - x1);
            double anchor = x1 + std::floor((t - x1) / step_size) * step_size;
            return y1 + slope * (anchor - x1);
        };
    }
};

// ============================================================================
// sampler.py
// ============================================================================

struct Sampler {
    virtual ~Sampler() = default;
    virtual double sample(double t) = 0;
    double sample_now() { return sample(env->now()); }
    // Distribution.mean(t): the mean at the params evaluated at t (used by
    // kpis for tc_ideal and by the fastest-duration focus policy).
    virtual double mean(double /*t*/) { throw std::logic_error("mean() is not defined for this sampler"); }
    double mean_now() { return mean(env->now()); }
};

using SamplerPtr = std::shared_ptr<Sampler>;

// Python: Distribution(sim.Constant, *params) — the distribution type plus
// parameters that are numbers or functions of time.
enum class DistType { Constant, Uniform, Normal, Exponential, Triangular, Lognormal, IntUniform };

using Param = std::variant<double, TimeFn>;

class Distribution : public Sampler {
  public:
    DistType distr_type;
    std::vector<Param> params;

    Distribution(DistType type, std::vector<Param> params_)
        : distr_type(type), params(std::move(params_)) {}

    std::vector<double> sample_params_at(double t) const {
        std::vector<double> out;
        out.reserve(params.size());
        for (const auto& p : params)
            out.push_back(std::holds_alternative<double>(p) ? std::get<double>(p)
                                                            : std::get<TimeFn>(p)(t));
        return out;
    }

    double sample(double t) override {
        auto p = sample_params_at(t);
        switch (distr_type) {
            case DistType::Constant:    return sim::Constant(p.at(0)).sample();
            case DistType::Uniform:     return sim::Uniform(p.at(0), p.at(1)).sample();
            case DistType::Normal:      return sim::Normal(p.at(0), p.at(1)).sample();
            case DistType::Exponential: return sim::Exponential(p.at(0)).sample();
            case DistType::Triangular:  return sim::Triangular(p.at(0), p.at(1), p.at(2)).sample();
            case DistType::IntUniform:  return sim::IntUniform((long long)p.at(0), (long long)p.at(1)).sample();
            case DistType::Lognormal: {
                // Real-space parameters: p[0]=mean, p[1]=std of the VALUES (like
                // Normal), converted to the underlying normal's (mu, sigma). Must
                // match simulation/sampler.py LogNormal.
                double m = p.at(0), sd = p.at(1);
                if (m <= 0) throw std::invalid_argument("LogNormal mean must be > 0");
                if (sd < 0) throw std::invalid_argument("LogNormal standard deviation must be >= 0");
                double sigma_sq = std::log(1.0 + (sd * sd) / (m * m));
                double mu = std::log(m) - sigma_sq / 2.0;
                return std::exp(sim::Normal(mu, std::sqrt(sigma_sq)).sample());
            }
        }
        throw std::invalid_argument("unknown distribution type");
    }

    double mean(double t) override {
        auto p = sample_params_at(t);
        switch (distr_type) {
            case DistType::Constant:    return sim::Constant(p.at(0)).mean();
            case DistType::Uniform:     return sim::Uniform(p.at(0), p.at(1)).mean();
            case DistType::Normal:      return sim::Normal(p.at(0), p.at(1)).mean();
            case DistType::Exponential: return sim::Exponential(p.at(0)).mean();
            case DistType::Triangular:  return sim::Triangular(p.at(0), p.at(1), p.at(2)).mean();
            case DistType::IntUniform:  return sim::IntUniform((long long)p.at(0), (long long)p.at(1)).mean();
            case DistType::Lognormal:   return p.at(0);  // real-space mean parameter
        }
        throw std::invalid_argument("unknown distribution type");
    }
};

inline SamplerPtr distribution(DistType t, std::vector<Param> params) {
    return std::make_shared<Distribution>(t, std::move(params));
}

inline SamplerPtr distribution(DistType t, std::initializer_list<double> params) {
    std::vector<Param> p;
    for (double v : params) p.emplace_back(v);
    return std::make_shared<Distribution>(t, std::move(p));
}

class FailureRate : public Sampler {
  public:
    TimeFn failure_rate;
    double tolerance;
    int max_iters;

    explicit FailureRate(TimeFn rate, double tolerance_ = 60, int max_iters_ = 10000)
        : failure_rate(std::move(rate)), tolerance(tolerance_), max_iters(max_iters_) {}

    double sample(double t) override {
        double threshold = -std::log(sim::random_stream().random());  // env.random.random()
        double integral = 0.0;
        int iters = 0;
        while (iters < max_iters && integral < threshold) {
            integral += failure_rate(t) * tolerance;
            t += tolerance;
            iters += 1;
        }
        if (integral < threshold)
            throw std::invalid_argument("Integral did not cross threshold after " +
                                        std::to_string(max_iters) + " iterations");
        return t - env->now();
    }
};

// sim.Bounded — a distribution rejection-sampled into [lowerbound, upperbound].
struct Bounded {
    SamplerPtr dist;
    double lowerbound;
    double upperbound;

    double sample() const {
        for (int i = 0; i < 100; ++i) {  // salabim's number_of_retries default
            double s = dist->sample_now();
            if (s >= lowerbound && s <= upperbound) return s;
        }
        return lowerbound;  // salabim's fail_value default (the lowerbound)
    }
};

// ============================================================================
// shift_manager.py
// ============================================================================

struct HasShifts {
    Intervals shifts;
    sim::State<bool> is_in_downtime{"", true};

    explicit HasShifts(Intervals shifts_) {
        std::sort(shifts_.begin(), shifts_.end(),
                  [](const IntervalPtr& a, const IntervalPtr& b) { return a->start < b->start; });
        shifts_ = merge_touching_sorted_intervals(shifts_);
        check_disjoint_sorted_intervals(shifts_);
        shifts = std::move(shifts_);
    }
    virtual ~HasShifts() = default;

    const Interval* current_or_last_shift() const {
        for (size_t i = 0; i < shifts.size(); ++i) {
            const auto& shift = shifts[i];
            if (shift->start > env->now()) return i > 0 ? shifts[i - 1].get() : nullptr;
            if (shift->end >= env->now()) return shift.get();
        }
        return shifts.empty() ? nullptr : shifts.back().get();
    }

    const Interval* next_or_current_shift_from(double cursor) const {
        for (const auto& shift : shifts)
            if (shift->end > cursor) return shift.get();
        return nullptr;
    }
};

class ShiftManager : public IntervalWaiter {
  public:
    HasShifts* entity;

    explicit ShiftManager(HasShifts* entity_) : IntervalWaiter(entity_->shifts), entity(entity_) {}

    void on_enter() override { entity->is_in_downtime.set(false); }
    void on_leave() override { entity->is_in_downtime.set(true); }

    // ---- static date helpers (Python: datetime; here: std::chrono) --------
    using days_t = std::chrono::sys_days;

    struct DateTime {  // a parsed "dd-mm-yyyy hh:mm"
        days_t date;
        int hour = 0;
        int minute = 0;
        int weekday() const {  // Monday == 0 (Python datetime.weekday())
            std::chrono::weekday wd{date};
            return static_cast<int>(wd.iso_encoding()) - 1;
        }
    };

    static long long minutes_between(const DateTime& d1, const DateTime& d2) {
        auto day_delta = (d2.date - d1.date).count();
        long long delta = day_delta * 1440LL + (d2.hour - d1.hour) * 60LL + (d2.minute - d1.minute);
        return delta;
    }

    // generate_weekly_shifts(sim_start, shifts_per_day, working_days, days_off, start, end)
    // shifts_per_day: 7 lists of (start_minutes, end_minutes) within the day (Mon..Sun)
    static Intervals generate_weekly_shifts(const DateTime& sim_start,
                                            const std::vector<std::vector<std::pair<double, double>>>& shifts_per_day,
                                            const std::vector<bool>& working_days,
                                            const std::set<long long>& days_off_rel_abs,  // sys_days count
                                            days_t start, days_t end) {
        if (shifts_per_day.size() != 7)
            throw std::invalid_argument("There must be 7 lists of shifts per week, one for each day");
        if (working_days.size() != 7)
            throw std::invalid_argument("There must be 7 working days per week");

        int week_offset = sim_start.weekday();
        double time_offset = 60.0 * sim_start.hour + sim_start.minute;
        std::set<long long> days_off_rel;
        for (long long d : days_off_rel_abs)
            days_off_rel.insert(d - sim_start.date.time_since_epoch().count());

        Intervals all_shifts;
        long long from = (start - sim_start.date).count();
        long long to = (end - sim_start.date).count();
        for (long long i = from; i <= to; ++i) {
            int day = static_cast<int>(((i + week_offset) % 7 + 7) % 7);
            if (working_days[day] && !days_off_rel.count(i)) {
                for (const auto& [s, e] : shifts_per_day[day]) {
                    auto shift = interval(s, e);
                    shift->translate(i * 1440.0 - time_offset);
                    all_shifts.push_back(shift);
                }
            }
        }
        return all_shifts;
    }

    static Intervals generate_custom_shifts(const DateTime& sim_start,
                                            const std::vector<std::pair<DateTime, DateTime>>& shifts,
                                            const std::set<long long>& days_off /* sys_days counts */) {
        auto before = [](const DateTime& a, const DateTime& b) {
            return minutes_between(b, a) < 0;  // a < b
        };
        // each day off is subtracted from the pieces the previous days off left
        std::vector<std::pair<DateTime, DateTime>> ranges;
        for (const auto& [start, end] : shifts) {
            std::vector<std::pair<DateTime, DateTime>> pieces{{start, end}};
            for (long long day_off : days_off) {
                DateTime d_start{days_t(std::chrono::days(day_off)), 0, 0};
                DateTime d_end{days_t(std::chrono::days(day_off + 1)), 0, 0};
                std::vector<std::pair<DateTime, DateTime>> new_pieces;
                for (const auto& [s, e] : pieces) {
                    if (before(s, d_start)) new_pieces.push_back({s, before(e, d_start) ? e : d_start});
                    if (before(d_end, e)) new_pieces.push_back({before(s, d_end) ? d_end : s, e});
                }
                pieces = std::move(new_pieces);
            }
            ranges.insert(ranges.end(), pieces.begin(), pieces.end());
        }
        Intervals out;
        for (const auto& [s, e] : ranges)
            out.push_back(interval(static_cast<double>(minutes_between(sim_start, s)),
                                   static_cast<double>(minutes_between(sim_start, e))));
        return out;
    }
};

// ============================================================================
// piece.py
// ============================================================================

struct Model {
    std::string name;
    Model* parent = nullptr;
    std::vector<Model*> children;

    explicit Model(std::string name_) : name(std::move(name_)) {}
    void set_parent(Model* p) {
        parent = p;
        parent->children.push_back(this);
    }
};

class Piece : public sim::Component {  // data component (no process)
  public:
    Model* model;
    std::string id;
    struct JournalEntry { std::string kind, name; double t; };  // 'in' | 'out' | 'task'
    std::vector<JournalEntry> journal;  // §5: buffer in/out + task stamps, for trajectory graphs

    explicit Piece(Model* model_) : model(model_) {
        char buf[8];
        std::snprintf(buf, sizeof buf, "%06d", counters::piece_id);
        id = buf;
        counters::piece_id += 1;
        if (kpis_state::WIP) kpis_state::WIP->tally(++kpis_state::wip_level);  // §4 WIP +1
    }

    void enter(Buffer& q);  // defined after Buffer (trigger + scrap-return + base enter)

    using sim::Component::leave;
    // §5: mirror Python Piece.leave — stamp 'out' whenever the piece leaves a Buffer.
    // leave(Queue&) is virtual in salabim++, and both removal paths (explicit
    // piece->leave(buffer) and from_store) route through it. Defined after Buffer.
    sim::Component& leave(sim::Queue& q) override;
};

class PickyPieceTaker {
  public:
    std::vector<Model*> valid_models;

    explicit PickyPieceTaker(std::vector<Model*> valid_models_) : valid_models(std::move(valid_models_)) {
        if (valid_models.empty())
            throw std::invalid_argument("PickyPieceTaker must have at least one valid model");
    }
    virtual ~PickyPieceTaker() = default;

    bool can_take(const Model* model) const {
        bool ok = false;
        while (model != nullptr && !ok) {
            ok |= std::find(valid_models.begin(), valid_models.end(), model) != valid_models.end();
            model = model->parent;
        }
        return ok;
    }
    bool can_take(const Piece* piece) const { return can_take(piece->model); }

    bool can_flush_into(const PickyPieceTaker& ppt) const {
        for (const Model* m : valid_models)
            if (!ppt.can_take(m)) return false;
        return true;
    }

    bool disjoint(const PickyPieceTaker& other) const {
        for (const Model* m : other.valid_models)
            if (can_take(m)) return false;
        for (const Model* m : valid_models)
            if (other.can_take(m)) return false;
        return true;
    }
};

// §9: abstract base shared by GoalPieceGenerator (a fixed goal paced over the
// shifts) and RatePieceGenerator (a stream at a given gap + mix, until ByTime).
// §13: Triggerable — scrap buffers pulse `trigger` when they take a piece so a
// goal generator sleeping with nothing left to make wakes for the remake.
class PieceGenerator : public Component, public PickyPieceTaker, public HasShifts, public Triggerable {
  public:
    std::vector<Model*> models;
    std::vector<Outlet*> outlets;
    std::vector<int> generated;
    std::vector<int> total_generated;  // per-model physical births (scrap remakes included)
    ShiftManager* shift_manager = nullptr;

    PieceGenerator(std::vector<Model*> models_, Intervals shifts_, std::vector<Outlet*> outlets_);

    void emit(int idx);  // build a Piece, place it, bump both counters (defined after place())

    // hold for gap, unless it would spill past the current shift — then hold to
    // the shift end and report false via *held_full so the caller re-checks.
    sim::Process hold_within_shift(double gap, bool* held_full);

    sim::Process process() override = 0;  // abstract
};

class GoalPieceGenerator : public PieceGenerator {
  public:
    std::vector<int> goals;
    std::vector<double> probs;
    int total_goal = 0;
    double gap = 0;

    GoalPieceGenerator(std::vector<std::pair<Model*, int>> models_goals, Intervals shifts_,
                       std::vector<Outlet*> outlets_, double grace_period = 0.0,
                       std::optional<double> gap_ = std::nullopt);

    void update_probs();
    sim::Process process() override;
};

class RatePieceGenerator : public PieceGenerator {
  public:
    // gap and per-model probabilities are each a constant or a function of time;
    // exactly one probability may be nullopt = the freeloader (1 - sum(others)).
    std::variant<double, TimeFn> gap;
    std::vector<std::optional<std::variant<double, TimeFn>>> model_probs;
    int freeloader_index = -1;

    RatePieceGenerator(std::vector<Model*> models_, Intervals shifts_, std::vector<Outlet*> outlets_,
                       std::variant<double, TimeFn> gap_,
                       std::vector<std::optional<std::variant<double, TimeFn>>> model_probs_);

    double current_gap();
    std::vector<double> current_probs();
    sim::Process process() override;
};

// ============================================================================
// outlet.py
// ============================================================================

enum class BufferType { PASSAGE, SCRAP, EXIT };

class Outlet : public PickyPieceTaker {
  public:
    explicit Outlet(std::vector<Model*> valid_models_) : PickyPieceTaker(std::move(valid_models_)) {}
    virtual Buffer* get() = 0;
};

class Buffer : public sim::Store, public Outlet, public Triggerable {
  public:
    BufferType buffer_type;
    PieceGenerator* piece_generator;

    Buffer(const std::string& name, std::vector<Model*> valid_models_, BufferType buffer_type_,
           PieceGenerator* piece_generator_ = nullptr)
        : sim::Store(name), Outlet(std::move(valid_models_)),
          buffer_type(buffer_type_), piece_generator(piece_generator_) {
        if (buffer_type == BufferType::SCRAP && piece_generator == nullptr)
            throw std::invalid_argument("Scrap buffer must be connected to piece generator");
        if (buffer_type != BufferType::SCRAP && piece_generator != nullptr)
            throw std::invalid_argument("Non-scrap buffer must not be connected to piece generator");
        if (buffer_type == BufferType::EXIT) {
            if (counters::exit_buffers == 1)
                throw std::invalid_argument("Simulation cannot have more than 1 exit buffer");
            counters::exit_buffers += 1;
        }
    }

    Buffer* get() override { return this; }
};

class Router : public Outlet {
  public:
    // Python: dict[Outlet, float | Callable | None]; None marks the freeloader.
    using Prob = std::optional<Param>;

    std::vector<Outlet*> outlets;
    std::vector<Prob> probs;
    int freeloader_index = -1;

    explicit Router(std::vector<std::pair<Outlet*, Prob>> outlets_probs)
        : Outlet(intersect_models_(outlets_probs)) {
        int none_count = 0;
        for (auto& [o, p] : outlets_probs)
            if (!p.has_value()) none_count += 1;
        if (none_count > 1) throw std::invalid_argument("At most one freeloader are allowed in router");

        for (auto& [o, p] : outlets_probs) {
            outlets.push_back(o);
            probs.push_back(p);
        }
        for (size_t i = 0; i < probs.size(); ++i)
            if (!probs[i].has_value()) { freeloader_index = static_cast<int>(i); break; }
    }

    Buffer* get() override {
        std::vector<double> p;
        p.reserve(probs.size());
        for (const auto& prob : probs) {
            if (!prob.has_value()) p.push_back(0);
            else if (std::holds_alternative<double>(*prob)) p.push_back(std::get<double>(*prob));
            else p.push_back(std::get<TimeFn>(*prob)(env->now()));
        }
        if (freeloader_index != -1) {
            double sum = 0;
            for (double v : p) sum += v;
            p[freeloader_index] = 1 - sum;
        }
        check_probabilities(p);
        return outlets[weighted_choice(p)]->get();
    }

  private:
    static std::vector<Model*> intersect_models_(const std::vector<std::pair<Outlet*, Prob>>& ops) {
        std::vector<Model*> inter;
        if (!ops.empty()) {
            for (Model* m : ops.front().first->valid_models) {
                bool in_all = true;
                for (const auto& [o, p] : ops) {
                    const auto& vm = o->valid_models;
                    if (std::find(vm.begin(), vm.end(), m) == vm.end()) { in_all = false; break; }
                }
                if (in_all) inter.push_back(m);
            }
        }
        if (inter.empty())
            throw std::invalid_argument("Router outlets must have at least one valid model in common");
        return inter;
    }
};

// ---- deferred bodies from piece.py / helpers.py ---------------------------

inline void Piece::enter(Buffer& q) {
    q.trigger.trigger();
    if (q.piece_generator != nullptr) {  // scrap buffer: re-open the model's goal
        auto& models = q.piece_generator->models;
        auto it = std::find(models.begin(), models.end(), model);
        assert(it != models.end());
        q.piece_generator->generated[it - models.begin()] -= 1;
        q.piece_generator->trigger.trigger();  // §13: wake a generator sleeping between remakes
    }
    if (q.buffer_type == BufferType::EXIT || q.buffer_type == BufferType::SCRAP)  // §4 WIP -1
        if (kpis_state::WIP) kpis_state::WIP->tally(--kpis_state::wip_level);
    journal.push_back({"in", q.name(), env->now()});  // §5
    sim::Component::enter(q);
}

inline sim::Component& Piece::leave(sim::Queue& q) {
    if (auto* b = dynamic_cast<Buffer*>(&q))
        journal.push_back({"out", b->name(), env->now()});
    return sim::Component::leave(q);
}

inline void check_outlet_validity(const PickyPieceTaker& giver, const std::vector<Outlet*>& outlets) {
    if (outlets.empty()) throw std::invalid_argument("Giver must have at least one outlet");

    for (size_t i = 0; i < outlets.size(); ++i)
        for (size_t j = i + 1; j < outlets.size(); ++j)
            if (!outlets[i]->disjoint(*outlets[j]))
                throw std::invalid_argument("Outlets must have disjoint valid models sets");

    std::vector<Model*> uni;
    for (const Outlet* o : outlets)
        for (Model* m : o->valid_models)
            if (std::find(uni.begin(), uni.end(), m) == uni.end()) uni.push_back(m);

    if (!giver.can_flush_into(PickyPieceTaker(uni)))
        throw std::invalid_argument("Giver must be able to flush all models into outlets");
}

inline void place(const std::vector<Piece*>& pieces, const std::vector<Outlet*>& outlets) {
    for (Piece* piece : pieces) {
        bool placed = false;
        for (Outlet* outlet : outlets) {
            if (outlet->can_take(piece)) {
                piece->enter(*outlet->get());
                placed = true;
                break;
            }
        }
        assert(placed);
    }
}

inline std::vector<Model*> models_of_(const std::vector<std::pair<Model*, int>>& mg) {
    std::vector<Model*> out;
    out.reserve(mg.size());
    for (auto& [m, g] : mg) out.push_back(m);
    return out;
}

inline PieceGenerator::PieceGenerator(std::vector<Model*> models_, Intervals shifts_,
                                      std::vector<Outlet*> outlets_)
    : PickyPieceTaker(std::move(models_)), HasShifts(std::move(shifts_)) {
    if (counters::piece_generators > 0)
        throw std::invalid_argument("Cannot have more than one piece generator");
    counters::piece_generators += 1;

    models = valid_models;  // PickyPieceTaker was built from the models list
    check_outlet_validity(*this, outlets_);

    shift_manager = sim::make<ShiftManager>({}, static_cast<HasShifts*>(this));

    outlets = std::move(outlets_);
    generated.assign(models.size(), 0);
    total_generated.assign(models.size(), 0);
}

inline void PieceGenerator::emit(int idx) {
    Piece* piece = sim::make<Piece>({}, models[idx]);
    place({piece}, outlets);
    generated[idx] += 1;
    total_generated[idx] += 1;
}

inline sim::Process PieceGenerator::hold_within_shift(double gap, bool* held_full) {
    const Interval* current_shift = current_or_last_shift();
    double shift_time_left = current_shift != nullptr ? current_shift->end - env->now() : sim::inf;
    if (gap > shift_time_left) {
        co_await hold(shift_time_left);
        *held_full = false;
        co_return;
    }
    co_await hold(gap);
    *held_full = true;
}

// ---- GoalPieceGenerator ----
inline GoalPieceGenerator::GoalPieceGenerator(std::vector<std::pair<Model*, int>> models_goals,
                                              Intervals shifts_, std::vector<Outlet*> outlets_,
                                              double grace_period, std::optional<double> gap_)
    : PieceGenerator(models_of_(models_goals), std::move(shifts_), std::move(outlets_)) {
    for (auto& [m, g] : models_goals) goals.push_back(g);
    probs.assign(models.size(), 0.0);
    for (int g : goals) total_goal += g;

    if (gap_.has_value()) {  // user-fixed pacing: may finish early or spill past the shifts
        if (grace_period != 0.0)
            throw std::invalid_argument("Grace period only applies to the automatic gap");
        if (*gap_ <= 0) throw std::invalid_argument("Gap must be > 0");
        gap = *gap_;
    } else {  // automatic: pace the goal over the shifts minus the grace-period reserve
        double working_time = 0;
        for (const auto& s : shifts) working_time += s->length();
        if (grace_period < 0) throw std::invalid_argument("Grace period must be >= 0");
        if (grace_period >= working_time)
            throw std::invalid_argument(
                "Grace period must be smaller than the generator's total shift time");
        gap = (working_time - grace_period) / total_goal;
    }
}

inline void GoalPieceGenerator::update_probs() {
    int total_gen = 0;
    for (int g : generated) total_gen += g;
    if (total_goal == total_gen) {
        probs.assign(models.size(), 0.0);
    } else {
        for (size_t i = 0; i < models.size(); ++i)
            probs[i] = double(goals[i] - generated[i]) / double(total_goal - total_gen);
    }
}

inline sim::Process GoalPieceGenerator::process() {
    while (true) {
        co_await wait({{is_in_downtime, false}});

        // everything asked for is out: sleep until a scrap buffer takes a piece
        // (its trigger pulse re-opens that model's goal), instead of polling
        update_probs();
        double sum_probs = 0;
        for (double p : probs) sum_probs += p;
        if (sum_probs == 0) {
            co_await wait(trigger);
            continue;
        }

        const Interval* current_shift = current_or_last_shift();
        double shift_time_left =
            current_shift != nullptr ? current_shift->end - env->now() : sim::inf;
        if (gap > shift_time_left) {
            co_await hold(shift_time_left);
            continue;
        }

        co_await hold(gap);
        int idx = weighted_choice(probs);
        emit(idx);
    }
}

// ---- RatePieceGenerator ----
inline RatePieceGenerator::RatePieceGenerator(
    std::vector<Model*> models_, Intervals shifts_, std::vector<Outlet*> outlets_,
    std::variant<double, TimeFn> gap_,
    std::vector<std::optional<std::variant<double, TimeFn>>> model_probs_)
    : PieceGenerator(std::move(models_), std::move(shifts_), std::move(outlets_)),
      gap(std::move(gap_)), model_probs(std::move(model_probs_)) {
    int none_count = 0;
    for (size_t i = 0; i < model_probs.size(); ++i)
        if (!model_probs[i].has_value()) {
            ++none_count;
            freeloader_index = static_cast<int>(i);
        }
    if (none_count > 1)
        throw std::invalid_argument("At most one model can be the freeloader in a rate generator");
}

inline double RatePieceGenerator::current_gap() {
    return std::holds_alternative<double>(gap) ? std::get<double>(gap)
                                               : std::get<TimeFn>(gap)(env->now());
}

inline std::vector<double> RatePieceGenerator::current_probs() {
    std::vector<double> probs(model_probs.size(), 0.0);
    for (size_t i = 0; i < model_probs.size(); ++i) {
        if (!model_probs[i].has_value()) continue;  // freeloader stays 0 for now
        const auto& p = *model_probs[i];
        probs[i] = std::holds_alternative<double>(p) ? std::get<double>(p)
                                                     : std::get<TimeFn>(p)(env->now());
    }
    if (freeloader_index != -1) {
        double sum = 0;
        for (double p : probs) sum += p;
        probs[freeloader_index] = 1 - sum;
    }
    check_probabilities(probs);
    return probs;
}

inline sim::Process RatePieceGenerator::process() {
    while (true) {
        co_await wait({{is_in_downtime, false}});
        bool held_full = false;
        co_await call(hold_within_shift(current_gap(), &held_full));
        if (!held_full) continue;
        int idx = weighted_choice(current_probs());
        emit(idx);
    }
}

// ============================================================================
// resource.py
// ============================================================================

class ExpiryManager;

class Resource : public sim::Resource, public Triggerable {
  public:
    std::vector<ExpiryManager*> expiry_managers;
    double lifespan;

    Resource(const std::string& name, double capacity, double initial_capacity = -1,
             double lifespan_ = sim::inf);  // body below (needs ExpiryManager)

    void shave(double quantity);  // body below

    // Blocking replenish (current component): co_await call(r.replenish(this, q)).
    sim::Process replenish(Component* demander, double quantity);
    // Non-blocking replenish for abort paths (demander is not current).
    void replenish_nb(Component* demander, double quantity);
};

class ExpiryManager : public Component {
  public:
    Resource* resource;
    double quantity;

    ExpiryManager(Resource* resource_, double quantity_) : resource(resource_), quantity(quantity_) {}

    sim::Process process() override {
        co_await call(request({{*resource, -quantity}}));
        co_await hold(resource->lifespan);
        co_await call(request({{*resource, quantity}}));
    }
};

inline Resource::Resource(const std::string& name, double capacity, double initial_capacity,
                          double lifespan_)
    : sim::Resource(name, capacity,
                    sim::ResourceOpts{.initial_claimed_quantity = capacity, .anonymous = true}),
      lifespan(lifespan_) {
    if (initial_capacity < 0) initial_capacity = this->capacity();
    expiry_managers.push_back(sim::make<ExpiryManager>({}, this, initial_capacity));
}

inline void Resource::shave(double quantity) {
    double shaved_quantity = 0.0;
    while (shaved_quantity < quantity) {
        assert(!expiry_managers.empty());
        ExpiryManager* em = expiry_managers.front();
        if (em->quantity > quantity - shaved_quantity) {
            em->quantity -= quantity - shaved_quantity;
            break;
        }
        shaved_quantity += em->quantity;
        em->cancel();
        expiry_managers.erase(expiry_managers.begin());
    }
}

inline sim::Process Resource::replenish(Component* demander, double quantity) {
    if (lifespan == sim::inf) {
        co_await demander->call(demander->request({{*this, -quantity}}));
    } else {
        expiry_managers.push_back(sim::make<ExpiryManager>({}, this, quantity));
    }
}

inline void Resource::replenish_nb(Component* demander, double quantity) {
    if (lifespan == sim::inf) {
        demander->request_nb({{*this, -quantity}});
    } else {
        expiry_managers.push_back(sim::make<ExpiryManager>({}, this, quantity));
    }
}

// ---- deferred bodies from component.py -------------------------------------

inline void Component::trigger_if_(sim::Resource* r) {
    if (auto* t = dynamic_cast<Triggerable*>(r)) t->trigger.trigger();
}

inline void Component::after_request_(const std::vector<sim::ReqSpec>& specs) {
    for (const auto& s : specs) {
        if (auto* r = dynamic_cast<Resource*>(s.r); r && r->lifespan < sim::inf) r->shave(s.q);
        if (s.q < 0)
            if (auto* t = dynamic_cast<Triggerable*>(s.r)) t->trigger.trigger();
    }
}

// ---------------------------------------------------------------------------
// resource.py — restockable resources
// ---------------------------------------------------------------------------

class RestockableResource;

class Delivery : public Component {
  public:
    RestockableResource* stock;
    SamplerPtr delivery_duration;

    Delivery(RestockableResource* stock_, SamplerPtr delivery_duration_)
        : stock(stock_), delivery_duration(std::move(delivery_duration_)) {}

    sim::Process process() override;  // body below
};

class RestockableResource : public Resource {
  public:
    SamplerPtr order_duration;
    SamplerPtr delivery_duration;
    double threshold;
    bool active_order = false;

    RestockableResource(const std::string& name, double capacity, SamplerPtr order_duration_,
                        SamplerPtr delivery_duration_, double threshold_,
                        double initial_capacity = -1, double lifespan_ = sim::inf)
        : Resource(name, capacity, initial_capacity, lifespan_),
          order_duration(std::move(order_duration_)),
          delivery_duration(std::move(delivery_duration_)),
          threshold(threshold_) {}

    sim::Process restock(Component* demander) {
        if (!active_order && available_quantity() < threshold) {
            active_order = true;
            co_await demander->hold(order_duration->sample_now(), {.mode = "wait_materials"});  // §4
            sim::make<Delivery>({}, this, delivery_duration);
        }
    }
};

inline sim::Process Delivery::process() {
    double missing = stock->capacity() - stock->available_quantity();
    co_await hold(delivery_duration->sample_now());
    co_await call(stock->replenish(this, missing));
    stock->active_order = false;
}

// ============================================================================
// operator.py
// ============================================================================

class OperatorGroup;

class OperatorShiftManager : public ShiftManager {
  public:
    explicit OperatorShiftManager(OperatorGroup* operator_group);

    void on_enter() override;
    void on_leave() override;
};

// base order = Python state-creation order: Resource, Triggerable, HasShifts
// (operator.py setup calls Triggerable.__init__ before HasShifts.__init__)
class OperatorGroup : public sim::Resource, public Triggerable, public HasShifts {
  public:
    SamplerPtr productivity;
    double n_operators;
    OperatorShiftManager* manager = nullptr;
    std::vector<Task*> dependent_tasks;  // tasks to unfreeze / hand off when this group returns/leaves

    OperatorGroup(const std::string& name, double capacity, Intervals shifts_, SamplerPtr productivity_)
        : sim::Resource(name, capacity, sim::ResourceOpts{.anonymous = false}),
          HasShifts(std::move(shifts_)),
          productivity(std::move(productivity_)) {
        n_operators = this->capacity();
        set_capacity(0);
        manager = sim::make<OperatorShiftManager>({}, this);
    }
};

inline OperatorShiftManager::OperatorShiftManager(OperatorGroup* operator_group)
    : ShiftManager(static_cast<HasShifts*>(operator_group)) {}
// on_enter / on_leave bodies are defined after Task (they touch Task members).

class Alternative {
  public:
    using OpsList = std::vector<std::pair<OperatorGroup*, int>>;

    std::vector<OpsList> alternatives;
    std::vector<sim::State<bool>*> triggers;

    Alternative() = default;
    explicit Alternative(std::vector<OpsList> alternatives_) : alternatives(std::move(alternatives_)) {
        if (alternatives.empty()) return;
        for (const auto& alt : alternatives) {
            SamplerPtr productivity = alt.at(0).first->productivity;
            for (const auto& [o, c] : alt)
                if (o->productivity != productivity)  // Python: identity comparison
                    throw std::invalid_argument("Operators do not have the same productivity");
        }
        for (const auto& alt : alternatives)
            for (const auto& [r, c] : alt) triggers.push_back(&r->trigger);
    }

    // Python returns [] (no alternatives) | the granted alt | None (failed).
    // Here: *out = OpsList{} | granted | std::nullopt.
    sim::Process request(Component* demander, std::optional<OpsList>* out, double fail_at = sim::inf,
                         std::optional<bool> cap_now = std::nullopt) {
        if (alternatives.empty()) {
            *out = OpsList{};
            co_return;
        }

        if (alternatives.size() == 1) {
            co_await demander->call(demander->request(
                reqspecs_(alternatives[0]),
                {.fail_at = fail_at, .mode = "wait_operators", .cap_now = cap_now}));  // §4
            *out = demander->failed() ? std::nullopt : std::optional<OpsList>(alternatives[0]);
            co_return;
        }

        while (true) {
            for (const auto& alt : alternatives) {
                co_await demander->call(
                    demander->request(reqspecs_(alt), {.fail_delay = 0, .mode = "wait_operators"}));  // §4
                if (!demander->failed()) {
                    *out = alt;
                    co_return;
                }
            }

            std::vector<sim::WaitSpec> specs;
            for (auto* t : triggers) specs.push_back(sim::WaitSpec(*t));
            co_await demander->sim::Component::wait(
                std::move(specs), {.fail_at = fail_at, .mode = "wait_operators", .cap_now = cap_now});  // §4
            if (demander->failed()) {
                *out = std::nullopt;
                co_return;
            }
        }
    }

    explicit operator bool() const { return !alternatives.empty(); }

    static std::vector<sim::ReqSpec> reqspecs_(const OpsList& alt) {
        std::vector<sim::ReqSpec> out;
        for (const auto& [r, c] : alt) out.push_back(sim::ReqSpec(*r, double(c)));
        return out;
    }
};

// ============================================================================
// protocols.py
// ============================================================================

enum class Action { ABORT, WAIT, LAUNCH };
enum class ConsciousnessState { CONSCIOUS, UNCONSCIOUS };
enum class ExitOrder { FIRST_IN_FIRST_OUT, FIRST_CREATED_FIRST_OUT };
enum class ModelChoice { MOST_PRESENT, FASTEST_TASK_DURATION, SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY };

struct PendingCarriers {
    virtual ~PendingCarriers() = default;
    virtual Action decide(int min_carriers, int pending_carriers) const = 0;
};

struct AbortPendingCarriers : PendingCarriers {
    Action decide(int, int) const override { return Action::ABORT; }
};

struct WaitForCarriers : PendingCarriers {
    Action decide(int, int) const override { return Action::WAIT; }
};

struct AbortOrWaitForCarriers : PendingCarriers {
    double tolerance_fraction;
    explicit AbortOrWaitForCarriers(double tf) : tolerance_fraction(tf) {}
    Action decide(int min_carriers, int pending_carriers) const override {
        return pending_carriers < min_carriers * tolerance_fraction ? Action::ABORT : Action::WAIT;
    }
};

struct ShiftConstraint {
    virtual ~ShiftConstraint() = default;
    virtual Action decide(const Interval* current_shift, double duration) const = 0;
    virtual double deadline(const Interval* current_shift) const = 0;
};

struct ConstrainedByShift : ShiftConstraint {
    Action decide(const Interval* current_shift, double duration) const override {
        if (current_shift == nullptr) return Action::ABORT;
        return env->now() + duration > current_shift->end ? Action::ABORT : Action::LAUNCH;
    }
    double deadline(const Interval* current_shift) const override {
        return current_shift != nullptr ? current_shift->end : sim::inf;
    }
};

struct NotConstrainedByShift : ShiftConstraint {
    Action decide(const Interval*, double) const override { return Action::LAUNCH; }
    double deadline(const Interval*) const override { return sim::inf; }
};

struct PartiallyConstrainedByShift : ShiftConstraint {
    double tolerance;
    explicit PartiallyConstrainedByShift(double tolerance_) : tolerance(tolerance_) {}
    Action decide(const Interval* current_shift, double duration) const override {
        if (current_shift == nullptr) return Action::ABORT;
        return env->now() + duration > current_shift->end + tolerance ? Action::ABORT : Action::LAUNCH;
    }
    double deadline(const Interval* current_shift) const override {
        return current_shift != nullptr ? current_shift->end + tolerance : sim::inf;
    }
};

struct SelfConsciousness {
    virtual ~SelfConsciousness() = default;
    virtual ConsciousnessState decide() const = 0;
};

struct Conscious : SelfConsciousness {
    ConsciousnessState decide() const override { return ConsciousnessState::CONSCIOUS; }
};

struct Unconscious : SelfConsciousness {
    ConsciousnessState decide() const override { return ConsciousnessState::UNCONSCIOUS; }
};

struct PieceExitOrder {
    virtual ~PieceExitOrder() = default;
    virtual ExitOrder decide() const = 0;
};

struct FirstInFirstOut : PieceExitOrder {
    ExitOrder decide() const override { return ExitOrder::FIRST_IN_FIRST_OUT; }
};

struct FirstCreatedFirstOut : PieceExitOrder {
    ExitOrder decide() const override { return ExitOrder::FIRST_CREATED_FIRST_OUT; }
};

struct ModelChoiceCriteria {
    virtual ~ModelChoiceCriteria() = default;
    virtual ModelChoice decide() const = 0;
};

struct MostPresent : ModelChoiceCriteria {
    ModelChoice decide() const override { return ModelChoice::MOST_PRESENT; }
};

struct FastestTaskDuration : ModelChoiceCriteria {
    ModelChoice decide() const override { return ModelChoice::FASTEST_TASK_DURATION; }
};

struct SmallestGapToMinCarrierCapacity : ModelChoiceCriteria {
    ModelChoice decide() const override { return ModelChoice::SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY; }
};

// ============================================================================
// interrupters.py (declarations; Shutdowns bodies need Task, defined below)
// ============================================================================

class Shutdowns : public IntervalWaiter {
  public:
    Task* task;

    Shutdowns(Task* task_, Intervals intervals_) : IntervalWaiter(std::move(intervals_)), task(task_) {}

    const Interval* get_next_shutdown() const {
        for (const auto& iv : intervals)
            if (iv->end > env->now()) return iv.get();
        return nullptr;
    }

    double get_deadline() const {
        const Interval* next = get_next_shutdown();
        return next != nullptr ? next->start : sim::inf;
    }

    // Periodic shutdown calendar: every in_between minutes a shutdown of
    // shutdown_duration minutes, placed inside the entity's (merged) shifts;
    // a shutdown that no longer fits moves into the next shift, randomized
    // uniformly when it enters a fresh shift. Mirrors the Python line by line
    // (Python's `task` parameter is only used as a HasShifts).
    static Intervals generate_periodic_shutdown(const HasShifts* task, double in_between,
                                                double shutdown_duration,
                                                const ShiftManager::DateTime& sim_start,
                                                const ShiftManager::DateTime& start,
                                                const ShiftManager::DateTime& end) {
        if (ShiftManager::minutes_between(sim_start, start) < 0)
            throw std::invalid_argument("Periodic shutdowns start must be after simulation start");
        if (ShiftManager::minutes_between(start, end) <= 0)
            throw std::invalid_argument("Periodic shutdowns start must be before end");

        double cursor = static_cast<double>(ShiftManager::minutes_between(sim_start, start));
        double horizon_end = static_cast<double>(ShiftManager::minutes_between(sim_start, end));
        Intervals intervals;

        while (cursor < horizon_end) {
            const Interval* current_or_next_shift = task->next_or_current_shift_from(cursor);
            if (current_or_next_shift == nullptr) break;

            cursor = std::max(cursor, current_or_next_shift->start);

            if (cursor > current_or_next_shift->start &&
                cursor + shutdown_duration <= current_or_next_shift->end) {
                intervals.push_back(interval(cursor, cursor + shutdown_duration));
                cursor += in_between;
            } else if (double wiggle_room = current_or_next_shift->end - cursor - shutdown_duration;
                       wiggle_room >= 0) {
                cursor += sim::Uniform(0, wiggle_room).sample();
                intervals.push_back(interval(cursor, cursor + shutdown_duration));
                cursor += in_between;
            } else {
                cursor = current_or_next_shift->end;
            }
        }
        return intervals;
    }

    void on_enter() override;  // needs Task
    void on_leave() override;
};

class FlexibleShutdowns : public Shutdowns {
  public:
    FlexibleShutdowns(Task* task_, Intervals intervals_);  // registers on the task

    void rearrange(size_t idx) {
        while (idx + 1 < intervals.size() && intervals[idx + 1]->end < intervals[idx]->start)
            intervals.erase(intervals.begin() + idx + 1);
    }

    bool adapt(const Interval& operation_interval) {
        for (size_t i = 0; i < intervals.size(); ++i) {
            Interval& iv = *intervals[i];
            if (!operation_interval.disjoint(iv) && iv.start <= operation_interval.end) {
                iv.translate(operation_interval.end - iv.start);
                rearrange(i);
                return true;
            }
        }
        return false;
    }

    sim::Process process() override;  // needs Task
};

class NonFlexibleShutdowns : public Shutdowns {
  public:
    NonFlexibleShutdowns(Task* task_, Intervals intervals_);  // registers on the task
};

class Breakdown : public Component {
  public:
    Task* task;
    SamplerPtr mtbf;
    SamplerPtr mttr;
    std::vector<Outlet*> outlets;

    Breakdown(Task* task_, SamplerPtr mtbf_, SamplerPtr mttr_, std::vector<Outlet*> outlets_ = {});

    sim::Process process() override;  // needs Task
};

// ============================================================================
// task.py
// ============================================================================

class Carrier;

class CarrierTracker {
  public:
    std::vector<Carrier*> carriers;
    sim::State<int> num_carriers{"", 0};

    void add(Carrier* c) {
        carriers.push_back(c);
        num_carriers.set(num_carriers() + 1);
    }
    void remove(Carrier* c) {
        auto it = std::find(carriers.begin(), carriers.end(), c);
        if (it != carriers.end()) {
            carriers.erase(it);
            num_carriers.set(num_carriers() - 1);
        }
    }
    Carrier* pop() {
        num_carriers.set(num_carriers() - 1);
        Carrier* c = carriers.back();
        carriers.pop_back();
        return c;
    }
    size_t size() const { return carriers.size(); }
    bool empty() const { return carriers.empty(); }
    auto begin() { return carriers.begin(); }
    auto end() { return carriers.end(); }
    Carrier* operator[](size_t i) { return carriers[i]; }
};

enum class Scope { PER_UNIT, PER_BATCH, PER_TASK };

struct Protocols {
    std::shared_ptr<PendingCarriers> pending_carriers_pre_flexible_shutdowns;
    std::shared_ptr<PendingCarriers> pending_carrier_pre_task_shift_end;
    std::shared_ptr<ShiftConstraint> operator_shift_constraint;
    std::shared_ptr<ShiftConstraint> task_shift_constraint;
    std::shared_ptr<SelfConsciousness> operators_self_conscious;
};

struct TaskConfig {
    Intervals task_shifts;
    SamplerPtr startup_duration;
    SamplerPtr loading_duration;

    Alternative startup_operators;
    Alternative loading_operators;
    Alternative operators;
    Scope operator_scope = Scope::PER_BATCH;
    Scope resource_scope = Scope::PER_BATCH;

    int min_carriers = 1;
    double max_capacity = 1;
    bool contiguous_carriers = false;
    bool independent_carriers = false;
    double timeout = sim::inf;
    int priority = 5;
    bool admin = false;  // §19: administrative task — a reporting classification only, no behaviour

    Protocols protocols;

    virtual ~TaskConfig() = default;
};

class Carrier : public Component, public Dispatchable, public Donnable {
  public:
    Task* task;
    sim::State<bool> loaded{"", false};

    explicit Carrier(Task* task_) : task(task_) {}

    // plain (non-blocking) — callable from other components, mirrors greenlet kill
    virtual void abort() = 0;
    virtual void abort_to(const std::vector<Outlet*>& outlets) = 0;

    // blocking helpers (sub-processes)
    virtual sim::Process handle_restock() = 0;
    virtual sim::Process freeze_abort_if(bool condition) = 0;
    virtual sim::Process wait_for_collector(double fail_at) = 0;
    virtual sim::Process request_resources(double fail_at) = 0;
    virtual sim::Process successfully_end_process() = 0;
    virtual double get_ideal_loading_duration() = 0;
    virtual double get_ideal_duration() = 0;

    // §17: shift-fit decision, re-run after the materials step; deadline helper.
    sim::Process check_shift_fit(const Alternative::OpsList& operators, double duration);
    double operator_fit_deadline(const Alternative::OpsList& operators);

    sim::Process handle_operators(const Alternative::OpsList& operators, double ideal_duration,
                                  double* out);
    sim::Process handle_batch_operators(Alternative& operators, double earliest_deadline,
                                        double ideal_duration, double fail_before, bool do_restock,
                                        const char* work_mode);  // §4: "loading" / "processing"
    sim::Process handle_task_operators(double earliest_deadline, double ideal_duration);

    sim::Process process() override;
};

class TaskStarter : public Component, public Donnable {
  public:
    Task* task;
    explicit TaskStarter(Task* task_) : task(task_) {}
    sim::Process process() override;  // needs Task
};

class TaskShiftManager : public ShiftManager {
  public:
    explicit TaskShiftManager(HasShifts* entity_) : ShiftManager(entity_) {}
    void on_enter() override;  // needs Task
    void on_leave() override;  // needs Task (resets started_up: re-warm each shift)
};

class Task : public Component, public HasShifts {
  public:
    std::shared_ptr<TaskConfig> config;
    double request_priority;
    TaskShiftManager* shift_manager = nullptr;
    NonFlexibleShutdowns* non_flexible_shutdowns = nullptr;
    FlexibleShutdowns* flexible_shutdowns = nullptr;
    sim::State<bool> is_in_breakdown{"", false};
    sim::State<bool> is_in_shutdown{"", false};
    sim::State<bool> is_frozen{"", false};

    Alternative::OpsList task_operators;
    std::unique_ptr<sim::Resource> vacant_slots;
    bool started_up = false;
    bool requested_per_task_operators = false;      // whether the PER_TASK crew is currently held
    double labor_minutes = 0.0;                     // operator-minutes booked on this task, all crews
    std::optional<double> task_crew_since;          // claim start of the held PER_TASK crew (Python None)
    CarrierTracker pending_carriers;
    CarrierTracker active_carriers;

    // §4 KPI instrumentation: finished carriers stay readable, tallies fill on deposit
    std::vector<Carrier*> all_carriers;
    sim::Monitor batch_sizes{"batch_sizes"};
    sim::Monitor cycle_times{"cycle_times"};
    sim::Monitor startup_times{"startup_times"};
    int pieces_in = 0;  // pieces physically taken from the inlets (retries included)

    bool skip_frozen_check = false;
    bool skip_downtime_check = false;

    explicit Task(std::shared_ptr<TaskConfig> config_)
        : HasShifts(config_->task_shifts), config(std::move(config_)) {
        if (config->operator_scope == Scope::PER_UNIT)
            throw std::invalid_argument("Operator scope cannot be PER_UNIT");
        if (config->resource_scope == Scope::PER_TASK)
            throw std::invalid_argument("Resource scope cannot be PER_TASK");
        if (!(0 <= config->priority && config->priority <= 10))
            throw std::invalid_argument("Task priority must be in [0,10]");
        if (dynamic_cast<ConstrainedByShift*>(config->protocols.task_shift_constraint.get()) &&
            dynamic_cast<WaitForCarriers*>(config->protocols.pending_carrier_pre_task_shift_end.get()))
            throw std::invalid_argument(
                "Task cannot be constrained by shift and wait for carrier completion pre task "
                "shift end at the same time");

        request_priority = 10 - config->priority;
        shift_manager = sim::make<TaskShiftManager>({}, static_cast<HasShifts*>(this));
        non_flexible_shutdowns = sim::make<NonFlexibleShutdowns>({}, this, Intervals{});
        flexible_shutdowns = sim::make<FlexibleShutdowns>({}, this, Intervals{});
        vacant_slots = std::make_unique<sim::Resource>("", config->max_capacity);

        // Register on every operator group so it can unfreeze this task (on return)
        // or hand off its PER_TASK crew (on leave). Dedup is per-alternative-object
        // (Python dict.fromkeys within each alternative; a group in two of the three
        // alternatives is appended once per alternative), and iteration order follows
        // insertion so the shift-boundary wake-up order stays process-independent.
        for (Alternative* alt : {&config->operators, &config->loading_operators,
                                 &config->startup_operators}) {
            std::vector<OperatorGroup*> seen;
            for (const auto& a : alt->alternatives)
                for (const auto& [g, c] : a)
                    if (std::find(seen.begin(), seen.end(), g) == seen.end()) {
                        seen.push_back(g);
                        g->dependent_tasks.push_back(this);
                    }
        }
    }

    virtual Carrier* make_carrier() = 0;  // Python: self.carrier_type(task=self)
    virtual void abort() = 0;
    virtual void abort_to(const std::vector<Outlet*>& outlets) { abort(); }  // PieceTask overrides

    const Interval* get_earliest_shutdown() const;
    double get_earliest_deadline() const {
        const Interval* s = get_earliest_shutdown();
        return s != nullptr ? s->start : sim::inf;
    }

    sim::Process handle_startup();  // §7: warm-up only (PER_TASK crew handled separately)
    sim::Process request_task_operators();  // §7: request + hold the PER_TASK crew
    void release_task_operators();          // §7/§16: release the held crew (idempotent)
    double labor_minutes_total() const;     // §12: booked minutes incl. a still-held crew
    bool any_task_operator_in_downtime() const {
        for (const auto& [g, c] : task_operators)
            if (g->is_in_downtime()) return true;
        return false;
    }
    // §16: a group this task may hold as its PER_TASK crew has just gone off shift.
    // Release the crew when it is held, idle (no carrier mid-run) and shift-
    // constrained past this boundary, so an idle crew is not reserved — and booked
    // as off-shift labour — while the task sits parked between carriers (waiting for
    // one to load, or blocked at the top of its loop). A crew that may work past the
    // boundary (NotConstrainedByShift, or PartiallyConstrained within tolerance) is
    // kept: it starts/finishes the carrier and is released afterwards instead.
    void hand_off_crew_at_shift_end() {
        if (config->operator_scope != Scope::PER_TASK || !requested_per_task_operators ||
            task_operators.empty() || !active_carriers.empty())
            return;
        const Interval* crew_shift = task_operators[0].first->current_or_last_shift();
        if (config->protocols.operator_shift_constraint->deadline(crew_shift) <= env->now())
            release_task_operators();
    }
    sim::Process process() override;
};

// ---- task.py bodies ---------------------------------------------------------

inline const Interval* Task::get_earliest_shutdown() const {
    const Interval* fs = flexible_shutdowns->get_next_shutdown();
    const Interval* nfs = non_flexible_shutdowns->get_next_shutdown();
    if (fs != nullptr && nfs != nullptr) return fs->start <= nfs->start ? fs : nfs;
    if (nfs == nullptr) return fs;
    return nfs;
}

inline sim::Process TaskStarter::process() {
    double duration = task->config->startup_duration->sample_now();
    while (true) {
        const Interval* next_shutdown = task->get_earliest_shutdown();
        if (next_shutdown == nullptr || env->now() + duration <= next_shutdown->start) break;
        co_await hold(sim::HoldOpts{.till = next_shutdown->end});
    }

    double deadline = task->get_earliest_deadline();
    std::optional<Alternative::OpsList> got;
    co_await call(task->config->startup_operators.request(this, &got, deadline - duration));
    if (failed()) {
        task->is_frozen.set(true);
        done.set(true);
        co_return;
    }

    co_await hold(duration);
    task->startup_times.tally(duration);  // §6: the setup work itself, not the wait for the crew
    double booked = 0;  // §12: the startup crew's operator-minutes
    if (got.has_value())
        for (const auto& [g, c] : *got) booked += c;
    task->labor_minutes += booked * duration;
    done.set(true);
}

inline void TaskShiftManager::on_enter() {
    static_cast<Task*>(dynamic_cast<Component*>(entity))->is_frozen.set(false);
    ShiftManager::on_enter();
}

inline void TaskShiftManager::on_leave() {
    static_cast<Task*>(dynamic_cast<Component*>(entity))->started_up = false;  // re-warm next shift
    ShiftManager::on_leave();
}

// §7/§16: OperatorShiftManager on_enter/on_leave — defined here (they touch Task).
inline void OperatorShiftManager::on_enter() {
    auto* g = static_cast<OperatorGroup*>(entity);
    g->set_capacity(g->n_operators);
    g->trigger.trigger();
    // a task frozen because these operators left resumes when they come back,
    // instead of staying frozen until its own (possibly weeks-away) shift start
    for (Task* task : g->dependent_tasks) task->is_frozen.set(false);
    ShiftManager::on_enter();
}

inline void OperatorShiftManager::on_leave() {
    auto* g = static_cast<OperatorGroup*>(entity);
    g->set_capacity(0);
    // A crew supervising a task PER_TASK goes home when its group leaves shift.
    // Release it from any dependent task that is holding it as its crew while idle
    // (no carrier mid-run) — whether the task is parked in its loaded-wait starving
    // for pieces or blocked at the top of its loop — so the crew is not reserved
    // (and booked as off-shift labour) past the boundary. The task re-acquires
    // whichever group is on shift when it next needs one.
    for (Task* task : g->dependent_tasks) {
        bool holds_this = false;
        for (const auto& [group, count] : task->task_operators)
            if (group == g) { holds_this = true; break; }
        if (holds_this) task->hand_off_crew_at_shift_end();
    }
    ShiftManager::on_leave();
}

inline sim::Process Task::handle_startup() {  // §7: warm-up only
    TaskStarter* task_starter = sim::make<TaskStarter>({}, this);
    co_await wait(task_starter->done);
    if (is_frozen()) co_return;
    started_up = true;
}

// §7: PER_TASK crew — one crew supervises every carrier; requested once, reused
// across carriers, re-requested only after a release (its shift end). The flag
// keeps it from being claimed twice without a release (which used to hoard pools).
inline sim::Process Task::request_task_operators() {
    double deadline =
        std::min(non_flexible_shutdowns->get_deadline(), flexible_shutdowns->get_deadline());
    std::optional<Alternative::OpsList> got;
    co_await call(config->operators.request(this, &got, deadline));
    task_operators = got.value_or(Alternative::OpsList{});
    if (failed()) {
        is_frozen.set(true);
    } else {
        requested_per_task_operators = true;
        task_crew_since = env->now();
    }
}

inline void Task::release_task_operators() {
    if (!task_operators.empty() && task_crew_since.has_value()) {
        double booked = 0;
        for (const auto& [g, c] : task_operators) booked += c;
        labor_minutes += booked * (env->now() - *task_crew_since);
    }
    task_crew_since.reset();
    if (!task_operators.empty()) release(Alternative::reqspecs_(task_operators));
    task_operators.clear();
    requested_per_task_operators = false;
}

inline double Task::labor_minutes_total() const {
    double total = labor_minutes;
    if (task_crew_since.has_value()) {
        double booked = 0;
        for (const auto& [g, c] : task_operators) booked += c;
        total += booked * (env->now() - *task_crew_since);
    }
    return total;
}

inline sim::Process Task::process() {
    while (true) {
        std::vector<sim::WaitSpec> specs;
        specs.push_back(sim::WaitSpec(is_in_breakdown, false));
        specs.push_back(sim::WaitSpec(is_in_shutdown, false));
        if (!skip_frozen_check) specs.push_back(sim::WaitSpec(is_frozen, false));
        if (!skip_downtime_check) specs.push_back(sim::WaitSpec(is_in_downtime, false));
        co_await wait(std::move(specs), {.all = true});

        // §16: PER_TASK crew hands off at operator-shift boundaries — once no
        // carrier is mid-run, drop a crew that has gone off shift so the next
        // round re-picks whichever group is on shift now.
        if (config->operator_scope == Scope::PER_TASK && requested_per_task_operators &&
            active_carriers.empty() && any_task_operator_in_downtime())
            release_task_operators();

        if (!started_up) co_await call(handle_startup());

        if (config->operator_scope == Scope::PER_TASK && started_up && !is_frozen() &&
            !requested_per_task_operators)
            co_await call(request_task_operators());

        if ((is_frozen() && !skip_frozen_check) || !started_up) continue;

        Carrier* new_carrier = make_carrier();
        pending_carriers.add(new_carrier);
        all_carriers.push_back(new_carrier);  // §4: finished carriers stay readable for KPIs
        // §16: a held PER_TASK crew that goes off shift while we wait for the carrier
        // to load is released at the boundary by the operator shift manager's hand-off
        // (see OperatorShiftManager::on_leave); here we just wait until it loads.
        while (!new_carrier->loaded()) {
            co_await wait(new_carrier->loaded);
        }

        // a crew handed off during the wait is re-acquired before dispatching
        // (whichever group is on shift now); a failed request freezes as usual
        if (config->operator_scope == Scope::PER_TASK && started_up && !is_frozen() &&
            !requested_per_task_operators) {
            co_await call(request_task_operators());
            if (is_frozen() && !skip_frozen_check) continue;
        }

        if (static_cast<int>(pending_carriers.size()) >= config->min_carriers) {
            std::vector<Carrier*> dispatched;
            while (!pending_carriers.empty()) {
                Carrier* carrier = pending_carriers.pop();
                carrier->allow_dispatch.set(true);
                dispatched.push_back(carrier);
                active_carriers.add(carrier);
            }

            skip_frozen_check = false;
            skip_downtime_check = false;

            if (!config->independent_carriers) {
                std::vector<sim::WaitSpec> dones;
                for (Carrier* c : dispatched) dones.push_back(sim::WaitSpec(c->done));
                co_await wait(std::move(dones), {.all = true});
            }
        } else if (is_frozen() && flexible_shutdowns->get_deadline() <= env->now()) {
            Action decision = config->protocols.pending_carriers_pre_flexible_shutdowns->decide(
                config->min_carriers, static_cast<int>(pending_carriers.size()));
            switch (decision) {
                case Action::ABORT:
                    while (!pending_carriers.empty()) pending_carriers[0]->abort();
                    break;
                case Action::WAIT:
                    skip_frozen_check = true;
                    break;
                default:
                    break;
            }
        }
    }
}

// ---- Carrier bodies ---------------------------------------------------------

inline sim::Process Carrier::handle_operators(const Alternative::OpsList& operators,
                                              double ideal_duration, double* out) {
    double duration = ideal_duration;
    if (!operators.empty()) {
        SamplerPtr productivity = operators[0].first->productivity;
        switch (task->config->protocols.operators_self_conscious->decide()) {
            case ConsciousnessState::CONSCIOUS: duration = ideal_duration / productivity->sample_now(); break;
            case ConsciousnessState::UNCONSCIOUS: duration = ideal_duration; break;
        }
    }
    co_await call(check_shift_fit(operators, duration));
    *out = duration;
}

// §17: the shift-fit decision, split out of handle_operators so it can be re-run
// after the materials step (a restock order or stock-out wait can outdate the
// first approval). Same decisions, same order: task constraint only when no crew,
// operator + task constraints otherwise.
inline sim::Process Carrier::check_shift_fit(const Alternative::OpsList& operators, double duration) {
    Action task_d =
        task->config->protocols.task_shift_constraint->decide(task->current_or_last_shift(), duration);
    if (operators.empty()) {
        co_await call(freeze_abort_if(task_d == Action::ABORT));
        co_return;
    }
    const Interval* current_operator_shift = operators[0].first->current_or_last_shift();
    Action op_d =
        task->config->protocols.operator_shift_constraint->decide(current_operator_shift, duration);
    co_await call(freeze_abort_if(op_d == Action::ABORT || task_d == Action::ABORT));
}

inline double Carrier::operator_fit_deadline(const Alternative::OpsList& operators) {
    return operators.empty()
               ? sim::inf
               : task->config->protocols.operator_shift_constraint->deadline(
                     operators[0].first->current_or_last_shift());
}

inline sim::Process Carrier::handle_batch_operators(Alternative& operators, double earliest_deadline,
                                                    double ideal_duration, double fail_before,
                                                    bool do_restock, const char* work_mode) {
    std::optional<Alternative::OpsList> recuperated;
    co_await call(operators.request(this, &recuperated, earliest_deadline - fail_before, true));
    co_await call(freeze_abort_if(failed()));
    assert(recuperated.has_value());

    double duration = 0;
    co_await call(handle_operators(*recuperated, ideal_duration, &duration));

    if (do_restock) {
        co_await call(handle_restock());
        // §17: a materials wait that cannot end before the crew's fit deadline can
        // never pass the re-check below — give up (freeing the crew) the moment
        // success becomes impossible, not when the materials show up.
        double base = earliest_deadline - duration - (fail_before - ideal_duration);
        double fit = operator_fit_deadline(*recuperated) - duration;
        co_await call(request_resources(std::min(base, fit)));
        co_await call(check_shift_fit(*recuperated, duration));  // §17: re-check after materials
    }

    co_await hold(duration, {.mode = work_mode});  // §4: "loading" / "processing"
    double booked = 0;  // §12: operator-minutes booked by this batch crew
    for (const auto& [g, c] : *recuperated) booked += c;
    task->labor_minutes += booked * duration;
    release(Alternative::reqspecs_(*recuperated));
}

inline sim::Process Carrier::handle_task_operators(double earliest_deadline, double ideal_duration) {
    double duration = 0;
    co_await call(handle_operators(task->task_operators, ideal_duration, &duration));
    co_await call(handle_restock());
    double fit = std::min(earliest_deadline, operator_fit_deadline(task->task_operators));
    co_await call(request_resources(fit - duration));               // §17: tighten by the crew deadline
    co_await call(check_shift_fit(task->task_operators, duration));  // §17: re-check after materials
    co_await hold(duration, {.mode = "processing"});                // §4
}

inline sim::Process Carrier::process() {
    double start_time = env->now();
    double non_flexible_shutdown_deadline = task->non_flexible_shutdowns->get_deadline();
    const Interval* task_current_shift = task->current_or_last_shift();
    double earliest_deadline =
        std::min(non_flexible_shutdown_deadline,
                 task->config->protocols.task_shift_constraint->deadline(task_current_shift));
    co_await call(freeze_abort_if(env->now() >= earliest_deadline));

    co_await call(wait_for_collector(earliest_deadline));
    co_await call(freeze_abort_if(failed()));
    loaded.set(true);

    double ideal_loading_duration = get_ideal_loading_duration();
    double ideal_duration = get_ideal_duration();

    switch (task->config->protocols.pending_carrier_pre_task_shift_end->decide(
        task->config->min_carriers, static_cast<int>(task->pending_carriers.size()))) {
        case Action::WAIT: task->skip_downtime_check = true; break;
        case Action::ABORT: task->skip_downtime_check = false; break;
        default: break;
    }

    co_await call(freeze_abort_if(env->now() > earliest_deadline - (ideal_duration + ideal_loading_duration)));
    co_await wait(allow_dispatch,
                  {.fail_at = earliest_deadline - (ideal_duration + ideal_loading_duration),
                   .mode = "wait_dispatch", .cap_now = true});  // §4
    co_await call(freeze_abort_if(failed()));

    bool delegate_restock_to_loading = !static_cast<bool>(task->config->operators);
    co_await call(handle_batch_operators(task->config->loading_operators, earliest_deadline,
                                         ideal_loading_duration, ideal_duration + ideal_loading_duration,
                                         delegate_restock_to_loading, "loading"));
    if (task->config->operator_scope == Scope::PER_BATCH) {
        co_await call(handle_batch_operators(task->config->operators, earliest_deadline, ideal_duration,
                                             ideal_duration, !delegate_restock_to_loading, "processing"));
    } else {
        co_await call(handle_task_operators(earliest_deadline, ideal_duration));
    }

    if (task->flexible_shutdowns->adapt(Interval(start_time, env->now()))) task->is_frozen.set(true);

    if (task->is_frozen() && !task->skip_frozen_check && !task->skip_downtime_check)
        task->release_task_operators();

    co_await call(successfully_end_process());
}

// ---- interrupters.py bodies --------------------------------------------------

inline void Shutdowns::on_enter() {
    task->abort();
    task->is_in_shutdown.set(true);
}

inline void Shutdowns::on_leave() {
    task->is_in_shutdown.set(false);
    task->is_frozen.set(false);
}

inline FlexibleShutdowns::FlexibleShutdowns(Task* task_, Intervals intervals_)
    : Shutdowns(task_, std::move(intervals_)) {
    task->flexible_shutdowns = this;
}

inline NonFlexibleShutdowns::NonFlexibleShutdowns(Task* task_, Intervals intervals_)
    : Shutdowns(task_, std::move(intervals_)) {
    task->non_flexible_shutdowns = this;
}

inline sim::Process FlexibleShutdowns::process() {
    while (true) {
        const Interval* next_shutdown = get_next_shutdown();
        if (next_shutdown == nullptr) break;

        if (env->now() < next_shutdown->start) {
            co_await hold(sim::HoldOpts{.till = next_shutdown->start});
            continue;
        }

        if (task->config->protocols.pending_carriers_pre_flexible_shutdowns->decide(
                task->config->min_carriers, static_cast<int>(task->pending_carriers.size())) ==
            Action::WAIT) {
            co_await wait({{task->active_carriers.num_carriers, 0},
                           {task->pending_carriers.num_carriers, 0}},
                          {.all = true});
        } else {
            co_await wait({{task->active_carriers.num_carriers, 0}});
        }

        const Interval* current = get_next_shutdown();
        if (current == nullptr || env->now() < current->start) continue;

        task->abort();
        task->is_in_shutdown.set(true);
        co_await hold(sim::HoldOpts{.till = current->end, .cap_now = true});
        task->is_in_shutdown.set(false);
        task->is_frozen.set(false);
        for (size_t i = 0; i < intervals.size(); ++i)
            if (intervals[i].get() == current) {
                intervals.erase(intervals.begin() + i);
                break;
            }
    }
}

// Breakdown's constructor body lives after piece_task.py/resource_task.py —
// it dynamic_casts to PieceTask/ResourceTask, which must be complete types.

inline sim::Process Breakdown::process() {
    while (true) {
        co_await wait({{task->is_in_shutdown, false}});
        co_await hold(mtbf->sample_now());

        if (task->is_in_shutdown.get()) continue;

        task->abort_to(outlets);
        task->is_in_breakdown.set(true);
        co_await hold(mttr->sample_now());
        task->is_in_breakdown.set(false);
    }
}

// ============================================================================
// piece_task.py
// ============================================================================

enum class PieceCollectorType {
    DISCRIMINATING_GREEDY,
    NON_DISCRIMINATING_GREEDY,
    DISCRIMINATING_ALTRUISTIC,
    NON_DISCRIMINATING_ALTRUISTIC,
};

inline bool is_discriminating(PieceCollectorType bct) {
    return bct == PieceCollectorType::DISCRIMINATING_GREEDY ||
           bct == PieceCollectorType::DISCRIMINATING_ALTRUISTIC;
}

struct ModelConfig {
    SamplerPtr duration;
    std::vector<std::pair<Resource*, double>> resources;
    int min_carrier_capacity = 1;
    int max_carrier_capacity = 1;
};

struct PieceTaskConfig : TaskConfig {
    std::vector<std::pair<Model*, ModelConfig>> models_configs;  // dict, insertion-ordered
    PieceCollectorType piece_collector_type = PieceCollectorType::NON_DISCRIMINATING_GREEDY;

    // Python has `PieceProtocols(Protocols)` — piece tasks carry two extra
    // protocols on top of the shared five in `protocols`. C++ stores TaskConfig's
    // `protocols` by value (would slice a derived struct), so the two piece-only
    // protocols live here on the config instead. Defaults mirror the parser's
    // (FirstInFirstOut / MostPresent) so scenario tests that don't set them build.
    std::shared_ptr<PieceExitOrder> piece_exit_order = std::make_shared<FirstInFirstOut>();
    std::shared_ptr<ModelChoiceCriteria> batch_model_choice = std::make_shared<MostPresent>();

    const ModelConfig& get_model_config(const Model* model) const {
        const Model* m = model;
        while (m != nullptr) {
            for (const auto& [mm, cfg] : models_configs)
                if (mm == m) return cfg;
            m = m->parent;
        }
        throw std::out_of_range("No model config for " + model->name + " or any of its ancestors");
    }
};

using PieceFilter = std::function<bool(Piece*)>;

class PieceCollector : public Component, public Dispatchable, public Donnable {
  public:
    PieceTask* task;
    std::vector<Piece*> collected_pieces;

    explicit PieceCollector(PieceTask* task_) : task(task_) {}

    PieceTaskConfig& cfg();                     // helper: the task's config
    std::vector<sim::Store*> inlet_stores();    // the task's inlets as stores
    sim::Resource& vacant_slots();

    // snapshot the inlets, pick one piece by the exit-order policy, take it via
    // from_store (result in *out; check failed() after). Replaces the direct
    // from_store calls so FIFO/FCFO ordering is honored.
    sim::Process pick_piece(PieceFilter piece_filter, sim::StoreOpts opts, Piece** out);
    Model* get_focus_model(const std::vector<Model*>& present_models);
    sim::Process collect_until(double deadline, int target, PieceFilter piece_filter, bool* timed_out);
    sim::Process ensure_one();
    sim::Process top_up(int limit, PieceFilter piece_filter);
    sim::Process block_remainder(int max_carrier_capacity);
    sim::Process collect_batch(double deadline, int min_carrier_capacity, int max_carrier_capacity,
                               PieceFilter piece_filter, bool* timed_out);  // AltruisticMixin
};

class NonDiscriminatingGreedyPieceCollector : public PieceCollector {
  public:
    using PieceCollector::PieceCollector;
    sim::Process process() override;
};

class DiscriminatingGreedyPieceCollector : public PieceCollector {
  public:
    using PieceCollector::PieceCollector;
    sim::Process process() override;
};

class NonDiscriminatingAltruisticPieceCollector : public PieceCollector {
  public:
    using PieceCollector::PieceCollector;
    sim::Process process() override;
};

class DiscriminatingAltruisticPieceCollector : public PieceCollector {
  public:
    using PieceCollector::PieceCollector;
    sim::Process process() override;
};

class PieceCarrier : public Carrier {
  public:
    PieceCollector* piece_collector = nullptr;

    explicit PieceCarrier(PieceTask* task_);

    sim::Process handle_restock() override;
    void abort() override;
    void abort_to(const std::vector<Outlet*>& outlets) override;
    sim::Process freeze_abort_if(bool condition) override;
    sim::Process wait_for_collector(double fail_at) override;
    double get_ideal_loading_duration() override;
    double get_ideal_duration() override;
    sim::Process request_resources(double fail_at) override;
    sim::Process successfully_end_process() override;

  private:
    PieceTask* ptask_();
};

class PieceTask : public Task, public PickyPieceTaker {
  public:
    std::vector<Buffer*> inlets;
    std::vector<Outlet*> outlets;
    std::map<Model*, int> deposited;  // §4: pieces deposited per model
    std::map<Model*, int> scrapped;   // §4: of those, how many landed in a SCRAP buffer

    PieceTask(std::shared_ptr<PieceTaskConfig> config_, std::vector<Buffer*> inlets_,
              std::vector<Outlet*> outlets_)
        : Task(validate_(config_)), PickyPieceTaker(keys_(config_)) {
        check_outlet_validity(*this, outlets_);
        inlets = std::move(inlets_);
        outlets = std::move(outlets_);
    }

    std::shared_ptr<PieceTaskConfig> pconfig() const {
        return std::static_pointer_cast<PieceTaskConfig>(config);
    }

    Carrier* make_carrier() override;

    void abort() override {
        abort_to_impl_(nullptr);
    }
    void abort_to(const std::vector<Outlet*>& outs) override { abort_to_impl_(&outs); }

  private:
    void abort_to_impl_(const std::vector<Outlet*>* outs) {
        std::vector<Carrier*> all;
        for (Carrier* c : pending_carriers) all.push_back(c);
        for (Carrier* c : active_carriers) all.push_back(c);
        std::reverse(all.begin(), all.end());
        for (Carrier* c : all) {
            if (outs != nullptr) c->abort_to(*outs);
            else c->abort();
        }
        release_task_operators();  // §7/§16: release the held PER_TASK crew (idempotent)
        started_up = false;
    }

    static std::shared_ptr<PieceTaskConfig> validate_(const std::shared_ptr<PieceTaskConfig>& c) {
        if (!is_discriminating(c->piece_collector_type)) {
            const ModelConfig& first = c->models_configs.front().second;
            for (const auto& [m, cfg] : c->models_configs) {
                if (cfg.duration != first.duration)  // Python: identity comparison
                    throw std::invalid_argument(
                        "Piece task cannot have different durations for models and not discriminate");
                if (cfg.min_carrier_capacity != first.min_carrier_capacity)
                    throw std::invalid_argument(
                        "Piece task cannot have different min_carrrier_capacity for models and not "
                        "discriminate");
                if (cfg.max_carrier_capacity != first.max_carrier_capacity)
                    throw std::invalid_argument(
                        "Piece task cannot have different max_carrrier_capacity for models and not "
                        "discriminate");
            }
        }
        return c;
    }

    static std::vector<Model*> keys_(const std::shared_ptr<PieceTaskConfig>& c) {
        std::vector<Model*> out;
        for (const auto& [m, cfg] : c->models_configs) out.push_back(m);
        return out;
    }
};

// ---- piece_task.py bodies -----------------------------------------------------

inline PieceTaskConfig& PieceCollector::cfg() { return *task->pconfig(); }

inline std::vector<sim::Store*> PieceCollector::inlet_stores() {
    std::vector<sim::Store*> out;
    for (Buffer* b : task->inlets) out.push_back(b);
    return out;
}

inline sim::Resource& PieceCollector::vacant_slots() { return *task->vacant_slots; }

inline Model* most_common_model_(const std::vector<Model*>& models);  // defined below

// Python PieceCollector.pick_piece: snapshot the (piece, buffer) pairs passing
// the filter; if any, choose the one the exit-order policy prefers (FIFO by
// buffer enter_time, FCFO by creation_time) and narrow the from_store filter to
// that exact piece (no scheduling point between the snapshot and the take, so it
// is honored immediately); if none, a plain from_store with the original filter
// keeps fail_at / fail_delay working. Result goes to *out; check failed() after.
inline sim::Process PieceCollector::pick_piece(PieceFilter piece_filter, sim::StoreOpts opts,
                                               Piece** out) {
    std::vector<std::pair<Piece*, Buffer*>> pieces;
    for (Buffer* buffer : task->inlets)
        for (sim::Component* c : *buffer) {
            Piece* p = static_cast<Piece*>(c);
            if (piece_filter(p)) pieces.push_back({p, buffer});
        }

    PieceFilter effective = piece_filter;
    if (!pieces.empty()) {
        Piece* target = nullptr;
        double best = sim::inf;
        // min(pieces, key=...) — first piece achieving the minimum wins on ties.
        switch (cfg().piece_exit_order->decide()) {
            case ExitOrder::FIRST_IN_FIRST_OUT:
                for (auto& [p, b] : pieces) {
                    double t = p->enter_time(*b);
                    if (t < best) { best = t; target = p; }
                }
                break;
            case ExitOrder::FIRST_CREATED_FIRST_OUT:
                for (auto& [p, b] : pieces) {
                    double t = p->creation_time();
                    if (t < best) { best = t; target = p; }
                }
                break;
        }
        effective = [target](Piece* p) { return p == target; };
    }

    if (!opts.mode) opts.mode = "wait_pieces";
    opts.filter = [effective](sim::Component* c) { return effective(static_cast<Piece*>(c)); };
    sim::Component* piece = co_await from_store(inlet_stores(), opts);
    if (!failed()) *out = static_cast<Piece*>(piece);
}

// Python PieceCollector.get_focus_model: the model the discriminating collectors
// build the batch around, per the batch_model_choice policy.
inline Model* PieceCollector::get_focus_model(const std::vector<Model*>& present_models) {
    switch (cfg().batch_model_choice->decide()) {
        case ModelChoice::MOST_PRESENT:
            return most_common_model_(present_models);
        case ModelChoice::FASTEST_TASK_DURATION: {
            Model* best = nullptr;
            double best_key = sim::inf;
            for (Model* m : present_models) {
                double k = cfg().get_model_config(m).duration->mean_now();
                if (k < best_key) { best_key = k; best = m; }
            }
            return best;
        }
        case ModelChoice::SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY: {
            Model* best = nullptr;
            double best_key = sim::inf;
            for (Model* m : present_models) {
                int count = 0;
                for (Model* mm : present_models)
                    if (mm == m) ++count;
                double k = cfg().get_model_config(m).min_carrier_capacity - count;
                if (k < best_key) { best_key = k; best = m; }
            }
            return best;
        }
    }
    return nullptr;  // unreachable
}

inline sim::Process PieceCollector::collect_until(double deadline, int target, PieceFilter piece_filter,
                                                  bool* timed_out) {
    while (static_cast<int>(collected_pieces.size()) < target) {
        co_await call(request({{vacant_slots(), 1}}, {.fail_at = deadline,
                                                      .mode = "wait_slot",
                                                      .request_priority = task->request_priority}));  // §4
        if (failed()) {
            *timed_out = true;
            co_return;
        }
        Piece* piece = nullptr;
        co_await call(pick_piece(piece_filter,
                                 {.fail_at = deadline, .request_priority = task->request_priority},
                                 &piece));
        if (failed()) {
            release({{vacant_slots(), 1}});
            *timed_out = true;
            co_return;
        }
        collected_pieces.push_back(piece);
        task->pieces_in += 1;  // §4
    }
    *timed_out = false;
}

inline sim::Process PieceCollector::ensure_one() {
    if (collected_pieces.empty()) {
        co_await call(request({{vacant_slots(), 1}},
                              {.mode = "wait_slot", .request_priority = task->request_priority}));  // §4
        Piece* piece = nullptr;
        co_await call(pick_piece([this](Piece* p) { return task->can_take(p); },
                                 {.request_priority = task->request_priority}, &piece));
        collected_pieces.push_back(piece);
        task->pieces_in += 1;  // §4
    }
}

inline sim::Process PieceCollector::top_up(int limit, PieceFilter piece_filter) {
    while (vacant_slots().available_quantity() > 0 &&
           static_cast<int>(collected_pieces.size()) < limit) {
        Piece* piece = nullptr;
        co_await call(pick_piece(piece_filter,
                                 {.fail_delay = 0, .request_priority = task->request_priority}, &piece));
        if (failed()) break;

        co_await call(request({{vacant_slots(), 1}},
                              {.mode = "wait_slot", .request_priority = task->request_priority}));  // §4
        collected_pieces.push_back(piece);
        task->pieces_in += 1;  // §4
    }
}

inline sim::Process PieceCollector::block_remainder(int max_carrier_capacity) {
    if (!cfg().contiguous_carriers) {
        int remainder = max_carrier_capacity - static_cast<int>(collected_pieces.size());
        co_await call(request({{vacant_slots(), double(remainder)}},
                              {.mode = "wait_slot", .request_priority = task->request_priority}));  // §4
    }
}

inline sim::Process PieceCollector::collect_batch(double deadline, int min_carrier_capacity,
                                                  int max_carrier_capacity, PieceFilter piece_filter,
                                                  bool* timed_out) {
    co_await call(request({{vacant_slots(), double(min_carrier_capacity)}},
                          {.fail_at = deadline, .mode = "wait_slot",
                           .request_priority = task->request_priority}));  // §4
    if (failed()) {
        *timed_out = true;
        co_return;
    }

    while (collected_pieces.empty()) {
        std::vector<std::pair<Piece*, Buffer*>> valid_pieces;
        for (Buffer* buffer : task->inlets)
            for (sim::Component* c : *buffer)
                if (piece_filter(static_cast<Piece*>(c)))
                    valid_pieces.push_back({static_cast<Piece*>(c), buffer});
        // exit-order policy before truncation (stable_sort: Python list.sort is stable)
        switch (cfg().piece_exit_order->decide()) {
            case ExitOrder::FIRST_IN_FIRST_OUT:
                std::stable_sort(valid_pieces.begin(), valid_pieces.end(),
                                 [](const auto& a, const auto& b) {
                                     return a.first->enter_time(*a.second) <
                                            b.first->enter_time(*b.second);
                                 });
                break;
            case ExitOrder::FIRST_CREATED_FIRST_OUT:
                std::stable_sort(valid_pieces.begin(), valid_pieces.end(),
                                 [](const auto& a, const auto& b) {
                                     return a.first->creation_time() < b.first->creation_time();
                                 });
                break;
        }
        int truncate = static_cast<int>(std::min(
            double(max_carrier_capacity), vacant_slots().available_quantity() + min_carrier_capacity));
        if (static_cast<int>(valid_pieces.size()) > truncate) valid_pieces.resize(truncate);

        if (static_cast<int>(valid_pieces.size()) >= min_carrier_capacity) {
            int additional = static_cast<int>(valid_pieces.size()) - min_carrier_capacity;
            if (additional > 0) {
                co_await call(request({{vacant_slots(), double(additional)}},
                                      {.fail_delay = 0, .mode = "wait_slot",
                                       .request_priority = task->request_priority}));  // §4
                if (failed()) {
                    additional = 0;
                    valid_pieces.resize(min_carrier_capacity);
                }
            }

            {  // re-check the pieces are still in their buffers
                std::vector<std::pair<Piece*, Buffer*>> still;
                for (auto& pb : valid_pieces)
                    if (pb.second->contains(pb.first)) still.push_back(pb);
                valid_pieces = std::move(still);
            }
            if (static_cast<int>(valid_pieces.size()) < min_carrier_capacity) {
                if (additional > 0) release({{vacant_slots(), double(additional)}});
                continue;
            }

            int surplus = additional - (static_cast<int>(valid_pieces.size()) - min_carrier_capacity);
            if (surplus > 0) release({{vacant_slots(), double(surplus)}});

            for (auto& [piece, buffer] : valid_pieces) {
                piece->leave(*buffer);
                collected_pieces.push_back(piece);
                task->pieces_in += 1;  // §4
            }

            if (!cfg().contiguous_carriers) {
                co_await call(
                    request({{vacant_slots(), double(max_carrier_capacity -
                                                     static_cast<int>(valid_pieces.size()))}},
                            {.mode = "wait_slot", .request_priority = task->request_priority}));  // §4
            }
        } else {
            std::vector<sim::WaitSpec> specs;
            for (Buffer* inlet : task->inlets) specs.push_back(sim::WaitSpec(inlet->trigger));
            co_await sim::Component::wait(std::move(specs), {.fail_at = deadline, .mode = "wait_pieces"});  // §4
            if (failed()) {
                release({{vacant_slots(), double(min_carrier_capacity)}});
                *timed_out = true;
                co_return;
            }
        }
    }

    *timed_out = false;
}

// Counter(...).most_common(1)[0][0] — highest count, first-seen tie-break.
inline Model* most_common_model_(const std::vector<Model*>& models) {
    std::vector<std::pair<Model*, int>> counts;
    for (Model* m : models) {
        bool found = false;
        for (auto& [mm, c] : counts)
            if (mm == m) {
                c += 1;
                found = true;
                break;
            }
        if (!found) counts.push_back({m, 1});
    }
    Model* best = nullptr;
    int best_count = -1;
    for (auto& [m, c] : counts)
        if (c > best_count) {
            best = m;
            best_count = c;
        }
    return best;
}

inline sim::Process NonDiscriminatingGreedyPieceCollector::process() {
    const ModelConfig& model_config = cfg().models_configs.front().second;

    co_await wait(allow_dispatch);
    double deadline = env->now() + cfg().timeout;

    PieceFilter take = [this](Piece* p) { return task->can_take(p); };
    bool timed_out = false;
    co_await call(collect_until(deadline, model_config.min_carrier_capacity, take, &timed_out));
    if (timed_out) {
        co_await call(ensure_one());
    } else {
        co_await call(top_up(model_config.max_carrier_capacity, take));
    }

    co_await call(block_remainder(model_config.max_carrier_capacity));
    set_mode("");  // §4: stop the last wait mode accruing to now after passivate
    done.set(true);
    co_await passivate();
}

inline sim::Process DiscriminatingGreedyPieceCollector::process() {
    co_await wait(allow_dispatch);
    double deadline = env->now() + cfg().timeout;

    std::vector<Model*> present_models;
    for (Buffer* inlet : task->inlets)
        for (sim::Component* c : *inlet)
            if (task->can_take(static_cast<Piece*>(c)))
                present_models.push_back(static_cast<Piece*>(c)->model);

    Model* focus_on = nullptr;
    if (!present_models.empty()) {
        focus_on = get_focus_model(present_models);
    } else {
        bool timed_out = false;
        co_await call(collect_until(deadline, 1, [this](Piece* p) { return task->can_take(p); },
                                    &timed_out));
        if (timed_out) co_await call(ensure_one());
        focus_on = collected_pieces.front()->model;
    }

    const ModelConfig& model_config = cfg().get_model_config(focus_on);
    PieceFilter focus_filter = [this, focus_on](Piece* p) {
        return task->can_take(p) && p->model == focus_on;
    };

    bool timed_out = false;
    co_await call(collect_until(deadline, model_config.min_carrier_capacity, focus_filter, &timed_out));
    if (timed_out) {
        co_await call(ensure_one());
    } else {
        co_await call(top_up(model_config.max_carrier_capacity, focus_filter));
    }

    co_await call(block_remainder(model_config.max_carrier_capacity));
    set_mode("");  // §4: stop the last wait mode accruing to now after passivate
    done.set(true);
    co_await passivate();
}

inline sim::Process NonDiscriminatingAltruisticPieceCollector::process() {
    const ModelConfig& model_config = cfg().models_configs.front().second;

    co_await wait(allow_dispatch);
    double deadline = env->now() + cfg().timeout;

    bool timed_out = false;
    co_await call(collect_batch(deadline, model_config.min_carrier_capacity,
                                model_config.max_carrier_capacity,
                                [this](Piece* p) { return task->can_take(p); }, &timed_out));
    if (timed_out) co_await call(ensure_one());

    set_mode("");  // §4: stop the last wait mode accruing to now after passivate
    done.set(true);
    co_await passivate();
}

inline sim::Process DiscriminatingAltruisticPieceCollector::process() {
    co_await wait(allow_dispatch);
    double deadline = env->now() + cfg().timeout;
    bool timed_out = false;

    std::vector<Model*> present_models;
    while (true) {
        present_models.clear();
        for (Buffer* inlet : task->inlets)
            for (sim::Component* c : *inlet)
                if (task->can_take(static_cast<Piece*>(c)))
                    present_models.push_back(static_cast<Piece*>(c)->model);
        if (!present_models.empty()) break;

        std::vector<sim::WaitSpec> specs;
        for (Buffer* inlet : task->inlets) specs.push_back(sim::WaitSpec(inlet->trigger));
        co_await sim::Component::wait(std::move(specs), {.fail_at = deadline, .mode = "wait_pieces"});  // §4
        if (failed()) {
            timed_out = true;
            break;
        }
    }

    if (!timed_out) {
        Model* focus_on = get_focus_model(present_models);
        const ModelConfig& model_config = cfg().get_model_config(focus_on);
        co_await call(collect_batch(deadline, model_config.min_carrier_capacity,
                                    model_config.max_carrier_capacity,
                                    [this, focus_on](Piece* p) {
                                        return task->can_take(p) && p->model == focus_on;
                                    },
                                    &timed_out));
    }

    if (timed_out) co_await call(ensure_one());

    set_mode("");  // §4: stop the last wait mode accruing to now after passivate
    done.set(true);
    co_await passivate();
}

inline PieceCarrier::PieceCarrier(PieceTask* task_) : Carrier(task_) {
    switch (task_->pconfig()->piece_collector_type) {
        case PieceCollectorType::DISCRIMINATING_GREEDY:
            piece_collector = sim::make<DiscriminatingGreedyPieceCollector>({}, task_);
            break;
        case PieceCollectorType::NON_DISCRIMINATING_GREEDY:
            piece_collector = sim::make<NonDiscriminatingGreedyPieceCollector>({}, task_);
            break;
        case PieceCollectorType::DISCRIMINATING_ALTRUISTIC:
            piece_collector = sim::make<DiscriminatingAltruisticPieceCollector>({}, task_);
            break;
        case PieceCollectorType::NON_DISCRIMINATING_ALTRUISTIC:
            piece_collector = sim::make<NonDiscriminatingAltruisticPieceCollector>({}, task_);
            break;
    }
}

inline PieceTask* PieceCarrier::ptask_() { return static_cast<PieceTask*>(task); }

inline sim::Process PieceCarrier::handle_restock() {
    for (const auto& [m, config] : ptask_()->pconfig()->models_configs)
        for (const auto& [resource, q] : config.resources)
            if (auto* rr = dynamic_cast<RestockableResource*>(resource))
                co_await call(rr->restock(this));
}

inline void PieceCarrier::abort_to(const std::vector<Outlet*>& outlets) {
    place(piece_collector->collected_pieces, outlets);
    piece_collector->set_mode("");  // §4: stop accruing the last mode on abort
    piece_collector->done.set(true);
    piece_collector->cancel();

    set_mode("");  // §4
    loaded.set(true);
    done.set(true);

    task->pending_carriers.remove(this);
    task->active_carriers.remove(this);
    // §16: a freeze-abort must not keep the PER_TASK crew reserved through the
    // frozen wait, but only once no other carrier is still mid-run with it.
    if (task->active_carriers.empty()) task->release_task_operators();
    cancel();
}

inline void PieceCarrier::abort() {
    std::vector<Outlet*> inlets_as_outlets;
    for (Buffer* b : ptask_()->inlets) inlets_as_outlets.push_back(b);
    abort_to(inlets_as_outlets);
}

inline sim::Process PieceCarrier::freeze_abort_if(bool condition) {
    if (condition) {
        task->is_frozen.set(true);
        abort();                 // ends with cancel(); we are the current component
        co_await sim::Yield{};   // never resumes: the scheduler reaps the abandoned chain
    }
}

inline sim::Process PieceCarrier::wait_for_collector(double fail_at) {
    piece_collector->allow_dispatch.set(true);
    co_await wait(piece_collector->done, {.fail_at = fail_at, .mode = "collecting"});  // §4
}

inline double PieceCarrier::get_ideal_loading_duration() {
    return task->config->loading_duration->sample_now();
}

inline double PieceCarrier::get_ideal_duration() {
    Model* model = piece_collector->collected_pieces.front()->model;
    const ModelConfig& model_config = ptask_()->pconfig()->get_model_config(model);
    return model_config.duration->sample_now();
}

inline sim::Process PieceCarrier::request_resources(double fail_at) {
    Model* model = piece_collector->collected_pieces.front()->model;
    double mult = task->config->resource_scope == Scope::PER_BATCH
                      ? 1.0
                      : double(piece_collector->collected_pieces.size());
    std::vector<sim::ReqSpec> resources;
    for (const auto& [r, q] : ptask_()->pconfig()->get_model_config(model).resources)
        resources.push_back(sim::ReqSpec(*r, q * mult));
    co_await call(request(std::move(resources),
                          {.fail_at = fail_at, .mode = "wait_materials", .cap_now = true}));  // §4
    co_await call(freeze_abort_if(failed()));
}

inline sim::Process PieceCarrier::successfully_end_process() {
    set_mode("");  // §4: stop the last work mode accruing to now
    piece_collector->cancel();

    auto& pieces = piece_collector->collected_pieces;
    task->batch_sizes.tally(static_cast<double>(pieces.size()));            // §4
    task->cycle_times.tally(env->now() - creation_time());                 // §4
    for (Piece* piece : pieces)                                            // §5: trajectory stamp
        piece->journal.push_back({"task", task->name(), env->now()});
    place(pieces, ptask_()->outlets);
    for (Piece* piece : pieces) {                                          // §4
        ptask_()->deposited[piece->model] += 1;
        for (sim::Queue* q : piece->queues())
            if (auto* b = dynamic_cast<Buffer*>(q);
                b != nullptr && b->buffer_type == BufferType::SCRAP) {
                ptask_()->scrapped[piece->model] += 1;
                break;
            }
    }
    done.set(true);

    task->pending_carriers.remove(this);
    task->active_carriers.remove(this);
    co_return;
}

inline Carrier* PieceTask::make_carrier() { return sim::make<PieceCarrier>({}, this); }

// ============================================================================
// resource_task.py
// ============================================================================

enum class ResourceCollectorType { GREEDY, ALTRUISTIC };

struct TransformedResource {  // (resource, proportion, salvageable)
    Resource* resource;
    double proportion;
    bool salvageable;
};

struct ResourceTaskConfig : TaskConfig {
    std::vector<std::pair<Resource*, double>> non_transformed_resources;
    std::vector<TransformedResource> transformed_resources_salvageable;
    std::vector<std::pair<Resource*, Bounded>> resources_out_distr;
    SamplerPtr duration;
    ResourceCollectorType resource_collector_type = ResourceCollectorType::GREEDY;
    double min_carrier_capacity = 1;
    double max_carrier_capacity = 1;
};

class ResourceCollector : public Component, public Dispatchable, public Donnable {
  public:
    ResourceTask* task;
    double requested_quantity = 0.0;
    std::vector<sim::State<bool>*> triggers;
    std::vector<double> requested_quantities;

    explicit ResourceCollector(ResourceTask* task_);

    ResourceTaskConfig& cfg();
    sim::Resource& vacant_slots();
    double request_priority();

    sim::Process balance_mix();
    sim::Process top_up();
};

class GreedyResourceCollector : public ResourceCollector {
  public:
    using ResourceCollector::ResourceCollector;
    sim::Process process() override;
};

class AltruisticResourceCollector : public ResourceCollector {
  public:
    using ResourceCollector::ResourceCollector;
    sim::Process process() override;
};

class ResourceCarrier : public Carrier {
  public:
    ResourceCollector* resource_collector = nullptr;

    explicit ResourceCarrier(ResourceTask* task_);

    sim::Process handle_restock() override;
    void abort() override;
    void abort_to(const std::vector<Outlet*>&) override { abort(); }
    sim::Process freeze_abort_if(bool condition) override;
    sim::Process wait_for_collector(double fail_at) override;
    double get_ideal_loading_duration() override;
    double get_ideal_duration() override;
    sim::Process request_resources(double fail_at) override;
    sim::Process successfully_end_process() override;

  private:
    ResourceTask* rtask_();
};

class ResourceTask : public Task {
  public:
    explicit ResourceTask(std::shared_ptr<ResourceTaskConfig> config_) : Task(validate_(config_)) {}

    std::shared_ptr<ResourceTaskConfig> rconfig() const {
        return std::static_pointer_cast<ResourceTaskConfig>(config);
    }

    Carrier* make_carrier() override;

    void abort() override {
        std::vector<Carrier*> all;
        for (Carrier* c : pending_carriers) all.push_back(c);
        for (Carrier* c : active_carriers) all.push_back(c);
        for (Carrier* c : all) c->abort();
        release_task_operators();  // §7/§16: release the held PER_TASK crew (idempotent)
        started_up = false;
    }

  private:
    static std::shared_ptr<ResourceTaskConfig> validate_(const std::shared_ptr<ResourceTaskConfig>& c) {
        std::vector<double> probs;
        for (const auto& t : c->transformed_resources_salvageable) probs.push_back(t.proportion);
        check_probabilities(probs);

        for (const auto& [r, distr] : c->resources_out_distr)
            if (distr.lowerbound < 0 || distr.upperbound == sim::inf)
                throw std::invalid_argument("Output resource distribution must be bounded in [0, +inf[");
        return c;
    }
};

// ---- resource_task.py bodies ---------------------------------------------------

inline ResourceCollector::ResourceCollector(ResourceTask* task_) : task(task_) {
    for (const auto& t : task->rconfig()->transformed_resources_salvageable) {
        triggers.push_back(&t.resource->trigger);
        requested_quantities.push_back(0.0);
    }
}

inline ResourceTaskConfig& ResourceCollector::cfg() { return *task->rconfig(); }
inline sim::Resource& ResourceCollector::vacant_slots() { return *task->vacant_slots; }
inline double ResourceCollector::request_priority() { return task->request_priority; }

inline sim::Process ResourceCollector::balance_mix() {
    const auto& transformed = cfg().transformed_resources_salvageable;
    double limiting_factor = sim::inf;
    for (size_t i = 0; i < transformed.size(); ++i)
        limiting_factor = std::min(limiting_factor, requested_quantities[i] / transformed[i].proportion);

    double sum_requested = 0;
    for (double q : requested_quantities) sum_requested += q;
    double excess_slots = sum_requested - limiting_factor;

    for (size_t i = 0; i < transformed.size(); ++i) {
        double excess = requested_quantities[i] - limiting_factor * transformed[i].proportion;
        if (transformed[i].salvageable && excess > 0)
            co_await call(transformed[i].resource->replenish(this, excess));
        requested_quantities[i] = limiting_factor * transformed[i].proportion;
    }

    if (excess_slots > 0) release({{vacant_slots(), excess_slots}});
    requested_quantity = limiting_factor;
}

inline sim::Process ResourceCollector::top_up() {
    const auto& transformed = cfg().transformed_resources_salvageable;
    double available = sim::inf;
    for (const auto& t : transformed)
        available = std::min(available, t.resource->available_quantity() / t.proportion);

    double additional_request = 0;
    if (vacant_slots().available_quantity() > 0 && available > 0) {
        additional_request = std::min({vacant_slots().available_quantity(),
                                       cfg().max_carrier_capacity - cfg().min_carrier_capacity,
                                       available});
        std::vector<sim::ReqSpec> specs;
        for (const auto& t : transformed)
            specs.push_back(sim::ReqSpec(*t.resource, t.proportion * additional_request));
        co_await call(request(std::move(specs),
                              {.fail_delay = 0, .request_priority = request_priority()}));
        assert(!failed());
        for (size_t i = 0; i < transformed.size(); ++i)
            requested_quantities[i] += transformed[i].proportion * additional_request;
        requested_quantity += additional_request;
    }

    double additional_slots_to_request;
    if (cfg().contiguous_carriers) {
        additional_slots_to_request = additional_request;
    } else {
        additional_slots_to_request = cfg().max_carrier_capacity - cfg().min_carrier_capacity;
    }
    co_await call(request({{vacant_slots(), additional_slots_to_request}},
                          {.request_priority = request_priority()}));
}

inline sim::Process GreedyResourceCollector::process() {
    co_await wait(allow_dispatch);
    double deadline = env->now() + cfg().timeout;
    bool timed_out = false;

    const auto& transformed = cfg().transformed_resources_salvageable;

    while (true) {
        double sum_requested = 0;
        for (double q : requested_quantities) sum_requested += q;
        if (sum_requested >= cfg().min_carrier_capacity) break;

        for (size_t i = 0; i < transformed.size(); ++i) {
            const auto& t = transformed[i];
            double per_resource = std::min(
                t.proportion * cfg().min_carrier_capacity - requested_quantities[i],
                t.resource->available_quantity());
            co_await call(request({{vacant_slots(), per_resource}},
                                  {.request_priority = request_priority()}));
            co_await call(request({{*t.resource, per_resource}},
                                  {.request_priority = request_priority()}));
            requested_quantities[i] += per_resource;
        }

        double sum2 = 0;
        for (double q : requested_quantities) sum2 += q;
        if (sum2 >= cfg().min_carrier_capacity) break;

        std::vector<sim::WaitSpec> specs;
        for (auto* t : triggers) specs.push_back(sim::WaitSpec(*t));
        co_await sim::Component::wait(std::move(specs), {.fail_at = deadline});
        if (failed()) {
            timed_out = true;
            break;
        }
    }

    if (timed_out) {
        co_await call(balance_mix());
    } else {
        double sum_requested = 0;
        for (double q : requested_quantities) sum_requested += q;
        requested_quantity = sum_requested;
        co_await call(top_up());
    }

    set_mode("");  // §4: stop the last wait mode accruing to now after passivate
    done.set(true);
    co_await passivate();
}

inline sim::Process AltruisticResourceCollector::process() {
    co_await wait(allow_dispatch);
    double deadline = env->now() + cfg().timeout;

    co_await call(request({{vacant_slots(), cfg().min_carrier_capacity}},
                          {.fail_at = deadline, .request_priority = request_priority()}));
    bool timed_out = failed();

    const auto& transformed = cfg().transformed_resources_salvageable;

    if (!timed_out) {
        std::vector<sim::ReqSpec> specs;
        for (const auto& t : transformed)
            specs.push_back(sim::ReqSpec(*t.resource, t.proportion * cfg().min_carrier_capacity));
        co_await call(request(std::move(specs),
                              {.fail_at = deadline, .request_priority = request_priority()}));
        if (failed()) {
            timed_out = true;
            release({{vacant_slots(), cfg().min_carrier_capacity}});
        }
    }

    if (!timed_out) {
        for (size_t i = 0; i < transformed.size(); ++i)
            requested_quantities[i] = transformed[i].proportion * cfg().min_carrier_capacity;
        requested_quantity = cfg().min_carrier_capacity;
        co_await call(top_up());
    }

    set_mode("");  // §4: stop the last wait mode accruing to now after passivate
    done.set(true);
    co_await passivate();
}

inline ResourceCarrier::ResourceCarrier(ResourceTask* task_) : Carrier(task_) {
    switch (task_->rconfig()->resource_collector_type) {
        case ResourceCollectorType::GREEDY:
            resource_collector = sim::make<GreedyResourceCollector>({}, task_);
            break;
        case ResourceCollectorType::ALTRUISTIC:
            resource_collector = sim::make<AltruisticResourceCollector>({}, task_);
            break;
    }
}

inline ResourceTask* ResourceCarrier::rtask_() { return static_cast<ResourceTask*>(task); }

inline sim::Process ResourceCarrier::handle_restock() {
    for (const auto& [resource, q] : rtask_()->rconfig()->non_transformed_resources)
        if (auto* rr = dynamic_cast<RestockableResource*>(resource))
            co_await call(rr->restock(this));

    for (const auto& t : rtask_()->rconfig()->transformed_resources_salvageable)
        if (auto* rr = dynamic_cast<RestockableResource*>(t.resource))
            co_await call(rr->restock(this));
}

inline void ResourceCarrier::abort() {
    const auto& transformed = rtask_()->rconfig()->transformed_resources_salvageable;
    for (size_t i = 0; i < transformed.size(); ++i)
        if (transformed[i].salvageable)
            transformed[i].resource->replenish_nb(this, resource_collector->requested_quantities[i]);

    resource_collector->set_mode("");  // §4
    resource_collector->done.set(true);
    resource_collector->cancel();

    set_mode("");  // §4
    loaded.set(true);
    done.set(true);

    task->pending_carriers.remove(this);
    task->active_carriers.remove(this);
    // §16: free the PER_TASK crew on a freeze-abort once no carrier is mid-run.
    if (task->active_carriers.empty()) task->release_task_operators();
    cancel();
}

inline sim::Process ResourceCarrier::freeze_abort_if(bool condition) {
    if (condition) {
        task->is_frozen.set(true);
        abort();
        co_await sim::Yield{};  // never resumes (yieldless: the greenlet dies here)
    }
}

inline sim::Process ResourceCarrier::wait_for_collector(double fail_at) {
    co_await call(handle_restock());

    if (env->now() >= fail_at) {
        co_await call(freeze_abort_if(true));
        co_return;  // unreachable, mirrors the Python `return`
    }

    resource_collector->allow_dispatch.set(true);
    co_await wait(resource_collector->done, {.fail_at = fail_at, .mode = "collecting", .cap_now = true});  // §4
}

inline double ResourceCarrier::get_ideal_loading_duration() {
    return task->config->loading_duration->sample_now();
}

inline double ResourceCarrier::get_ideal_duration() {
    return rtask_()->rconfig()->duration->sample_now();
}

inline sim::Process ResourceCarrier::request_resources(double fail_at) {
    double mult = task->config->resource_scope == Scope::PER_BATCH
                      ? 1.0
                      : resource_collector->requested_quantity;
    std::vector<sim::ReqSpec> resources;
    for (const auto& [r, q] : rtask_()->rconfig()->non_transformed_resources)
        resources.push_back(sim::ReqSpec(*r, q * mult));
    co_await call(request(std::move(resources),
                          {.fail_at = fail_at, .mode = "wait_materials", .cap_now = true}));  // §4
    co_await call(freeze_abort_if(failed()));
}

inline sim::Process ResourceCarrier::successfully_end_process() {
    for (const auto& [resource_out, distr] : rtask_()->rconfig()->resources_out_distr)
        co_await call(resource_out->replenish(this, distr.sample() * resource_collector->requested_quantity));

    task->batch_sizes.tally(resource_collector->requested_quantity);  // §4
    task->cycle_times.tally(env->now() - creation_time());            // §4

    resource_collector->set_mode("");  // §4: stop accruing modes to now
    resource_collector->cancel();
    set_mode("");  // §4
    done.set(true);
    task->pending_carriers.remove(this);
    task->active_carriers.remove(this);
}

inline Carrier* ResourceTask::make_carrier() { return sim::make<ResourceCarrier>({}, this); }

inline Breakdown::Breakdown(Task* task_, SamplerPtr mtbf_, SamplerPtr mttr_,
                            std::vector<Outlet*> outlets_)
    : task(task_), mtbf(std::move(mtbf_)), mttr(std::move(mttr_)), outlets(std::move(outlets_)) {
    if (!outlets.empty() && dynamic_cast<ResourceTask*>(task) != nullptr)
        throw std::invalid_argument("Breakdown on resource task cannot have outlets");
    if (outlets.empty() && dynamic_cast<PieceTask*>(task) != nullptr)
        throw std::invalid_argument("Breakdowns on piece tasks must have outlets");
}

// ============================================================================
// judgement_day.py
// ============================================================================

class StoppingCriterion : public Component, public Dispatchable, public Donnable {};

class ByTime : public StoppingCriterion {
  public:
    double time;
    explicit ByTime(double time_) : time(time_) {}

    sim::Process process() override {
        co_await wait(allow_dispatch);
        co_await hold(time);
        done.set(true);
    }
};

class ByPiecesProduced : public StoppingCriterion {
  public:
    int total;
    Buffer* exit_buffer;
    double timeout;

    ByPiecesProduced(int total_, Buffer* exit_buffer_, double timeout_ = sim::inf)
        : total(total_), exit_buffer(exit_buffer_), timeout(timeout_) {
        if (exit_buffer->buffer_type != BufferType::EXIT)
            throw std::invalid_argument("Stopping criterion must take an EXIT buffer");
    }

    sim::Process process() override {
        co_await wait(allow_dispatch);
        double deadline = timeout + env->now();
        while (static_cast<int>(exit_buffer->size()) < total) {
            co_await wait(exit_buffer->trigger, {.fail_at = deadline});
            if (failed()) {
                done.set(true);
                break;
            }
        }
        done.set(true);
    }
};

class SimulationStopper : public Component {
  public:
    StoppingCriterion* criterion;
    explicit SimulationStopper(StoppingCriterion* criterion_) : criterion(criterion_) {}

    sim::Process process() override {
        criterion->allow_dispatch.set(true);
        co_await wait(criterion->done);
        env->main()->activate();
    }
};

}  // namespace simulation
