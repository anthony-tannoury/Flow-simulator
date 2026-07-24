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


class Buffer;
class Outlet;
class PieceGenerator;
class Resource;
class Task;
class PieceTask;
class ResourceTask;
struct Model;
class Piece;


inline long long SEED = 0;
inline sim::Environment* env = nullptr;


namespace counters {
inline int piece_id = 0;
inline int piece_generators = 0;
inline int exit_buffers = 0;
}


namespace kpis_state {
inline sim::Monitor* WIP = nullptr;
inline int wip_level = 0;
}


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


inline int weighted_choice(const std::vector<double>& p) {
    double u = sim::random_stream().random();
    double acc = 0.0;
    for (size_t i = 0; i < p.size(); ++i) {
        acc += p[i];
        if (u < acc) return static_cast<int>(i);
    }
    return static_cast<int>(p.size()) - 1;
}


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


class Component : public sim::Component {
  public:


    sim::Process request(std::vector<sim::ReqSpec> specs, sim::RequestOpts opts = {}) {
        co_await sim::Component::request(specs, std::move(opts));
        if (!failed()) after_request_(specs);
    }


    void request_nb(std::vector<sim::ReqSpec> specs, sim::RequestOpts opts = {}) {
        sim::Component::request(specs, opts);
        if (!failed()) after_request_(specs);
    }


    void release() {
        for (sim::Resource* r : claimed_resources()) trigger_if_(r);
        sim::Component::release();
    }
    void release(std::vector<sim::ReqSpec> specs) {
        if (specs.empty()) { release(); return; }
        for (auto& s : specs) trigger_if_(s.r);
        for (auto& s : specs) sim::Component::release(*s.r, s.q);
    }

  private:
    static void trigger_if_(sim::Resource* r);
    void after_request_(const std::vector<sim::ReqSpec>& specs);
};


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


using IntervalPtr = std::shared_ptr<Interval>;
using Intervals = std::vector<IntervalPtr>;

inline IntervalPtr interval(double start, double end) { return std::make_shared<Interval>(start, end); }


inline void check_disjoint_sorted_intervals(const Intervals& intervals) {
    for (size_t i = 1; i < intervals.size(); ++i)
        if (!intervals[i]->disjoint(*intervals[i - 1]))
            throw std::invalid_argument("Intervals must be pairwise disjoint");
}


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
        for (size_t i = 0; i < intervals.size(); ++i) {
            IntervalPtr iv = intervals[i];
            co_await hold(sim::HoldOpts{.till = iv->start, .cap_now = true});
            on_enter();
            co_await hold(sim::HoldOpts{.till = iv->end, .cap_now = true});
            on_leave();
        }
    }
};


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

struct ExponentialFn {
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


struct Sampler {
    virtual ~Sampler() = default;
    virtual double sample(double t) = 0;
    double sample_now() { return sample(env->now()); }


    virtual double mean(double ) { throw std::logic_error("mean() is not defined for this sampler"); }
    double mean_now() { return mean(env->now()); }
};

using SamplerPtr = std::shared_ptr<Sampler>;


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
            case DistType::Lognormal:   return p.at(0);
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
        double threshold = -std::log(sim::random_stream().random());
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


struct Bounded {
    SamplerPtr dist;
    double lowerbound;
    double upperbound;

    double sample() const {
        for (int i = 0; i < 100; ++i) {
            double s = dist->sample_now();
            if (s >= lowerbound && s <= upperbound) return s;
        }
        return lowerbound;
    }
};


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


    using days_t = std::chrono::sys_days;

    struct DateTime {
        days_t date;
        int hour = 0;
        int minute = 0;
        int weekday() const {
            std::chrono::weekday wd{date};
            return static_cast<int>(wd.iso_encoding()) - 1;
        }
    };

    static long long minutes_between(const DateTime& d1, const DateTime& d2) {
        auto day_delta = (d2.date - d1.date).count();
        long long delta = day_delta * 1440LL + (d2.hour - d1.hour) * 60LL + (d2.minute - d1.minute);
        return delta;
    }


    static Intervals generate_weekly_shifts(const DateTime& sim_start,
                                            const std::vector<std::vector<std::pair<double, double>>>& shifts_per_day,
                                            const std::vector<bool>& working_days,
                                            const std::set<long long>& days_off_rel_abs,
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
                                            const std::set<long long>& days_off ) {
        auto before = [](const DateTime& a, const DateTime& b) {
            return minutes_between(b, a) < 0;
        };

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

class Piece : public sim::Component {
  public:
    Model* model;
    std::string id;
    Piece* parent = nullptr;
    std::vector<Piece*> children;

    explicit Piece(Model* model_) : model(model_) {
        char buf[8];
        std::snprintf(buf, sizeof buf, "%06d", counters::piece_id);
        id = buf;
        counters::piece_id += 1;
        if (kpis_state::WIP) kpis_state::WIP->tally(++kpis_state::wip_level);
    }

    bool has_family() const { return parent != nullptr || !children.empty(); }

    std::vector<Piece*> family() {
        if (!has_family()) return {this};
        Piece* root = parent != nullptr ? parent : this;
        std::vector<Piece*> fam;
        fam.reserve(1 + root->children.size());
        fam.push_back(root);
        fam.insert(fam.end(), root->children.begin(), root->children.end());
        return fam;
    }

    bool has_model(const Model* m) {
        for (Piece* p : family())
            if (p->model == m) return true;
        return false;
    }

    void associate_with_parent(Piece* p) {
        parent = p;
        p->children.push_back(this);
    }

    void dissociate_from_parent() {
        if (parent == nullptr) throw std::runtime_error("Cannot dissociate an unassociated piece");
        auto& sibs = parent->children;
        sibs.erase(std::remove(sibs.begin(), sibs.end(), this), sibs.end());
        parent = nullptr;
    }

    static void associate_all(const std::vector<Piece*>& pieces) {
        for (Piece* p : pieces)
            if (p->has_family())
                throw std::runtime_error("Pieces to be associated should not be already related");
        for (size_t i = 1; i < pieces.size(); ++i) pieces[i]->associate_with_parent(pieces[0]);
    }

    static void dissociate_all(const std::vector<Piece*>& pieces) {
        Piece* root = nullptr;
        for (Piece* p : pieces)
            if (p->parent == nullptr) {
                if (root != nullptr)
                    throw std::runtime_error("Piece to be dissociated must be part of one family");
                root = p;
            }
        if (root == nullptr)
            throw std::runtime_error("Piece to be dissociated must be part of one family");
        for (Piece* p : pieces)
            if (p != root && p->parent != root)
                throw std::runtime_error("Piece to be dissociated must be part of one family");
        for (Piece* p : pieces)
            if (p != root) p->dissociate_from_parent();
    }

    void enter(Buffer& q);

    using sim::Component::leave;


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


class PieceGenerator : public Component, public PickyPieceTaker, public HasShifts, public Triggerable {
  public:
    std::vector<Model*> models;
    std::vector<Outlet*> outlets;
    std::vector<int> generated;
    std::vector<int> total_generated;
    ShiftManager* shift_manager = nullptr;

    PieceGenerator(std::vector<Model*> models_, Intervals shifts_, std::vector<Outlet*> outlets_);

    void emit(int idx);


    sim::Process hold_within_shift(double gap, bool* held_full);

    sim::Process process() override = 0;
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
    std::map<Model*, int> model_counts;

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


inline void Piece::enter(Buffer& q) {
    std::vector<Piece*> fam = family();
    for (Piece* p : fam) q.model_counts[p->model] += 1;
    q.trigger.trigger();
    if (q.piece_generator != nullptr) {
        auto& models = q.piece_generator->models;
        for (Piece* p : fam) {
            auto it = std::find(models.begin(), models.end(), p->model);
            if (it != models.end()) q.piece_generator->generated[it - models.begin()] -= 1;
        }
        q.piece_generator->trigger.trigger();
    }
    if (q.buffer_type == BufferType::EXIT || q.buffer_type == BufferType::SCRAP)
        if (kpis_state::WIP) {
            kpis_state::wip_level -= static_cast<long long>(fam.size());
            kpis_state::WIP->tally(kpis_state::wip_level);
        }
    sim::Component::enter(q);
}

inline sim::Component& Piece::leave(sim::Queue& q) {
    if (auto* b = dynamic_cast<Buffer*>(&q))
        for (Piece* p : family()) b->model_counts[p->model] -= 1;
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

    models = valid_models;
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


inline GoalPieceGenerator::GoalPieceGenerator(std::vector<std::pair<Model*, int>> models_goals,
                                              Intervals shifts_, std::vector<Outlet*> outlets_,
                                              double grace_period, std::optional<double> gap_)
    : PieceGenerator(models_of_(models_goals), std::move(shifts_), std::move(outlets_)) {
    for (auto& [m, g] : models_goals) goals.push_back(g);
    probs.assign(models.size(), 0.0);
    for (int g : goals) total_goal += g;

    if (gap_.has_value()) {
        if (grace_period != 0.0)
            throw std::invalid_argument("Grace period only applies to the automatic gap");
        if (*gap_ <= 0) throw std::invalid_argument("Gap must be > 0");
        gap = *gap_;
    } else {
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
    double g = std::holds_alternative<double>(gap) ? std::get<double>(gap)
                                                   : std::get<TimeFn>(gap)(env->now());
    if (g <= 0)


        throw std::invalid_argument("Rate generator gap must stay > 0; got " + std::to_string(g)
                                    + " at t=" + std::to_string(env->now()));
    return g;
}

inline std::vector<double> RatePieceGenerator::current_probs() {
    std::vector<double> probs(model_probs.size(), 0.0);
    for (size_t i = 0; i < model_probs.size(); ++i) {
        if (!model_probs[i].has_value()) continue;
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


class ExpiryManager;

class Resource : public sim::Resource, public Triggerable {
  public:
    std::vector<ExpiryManager*> expiry_managers;
    double lifespan;

    Resource(const std::string& name, double capacity, double initial_capacity = -1,
             double lifespan_ = sim::inf);

    void shave(double quantity);


    sim::Process replenish(Component* demander, double quantity);

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


class RestockableResource;

class Delivery : public Component {
  public:
    RestockableResource* stock;
    SamplerPtr delivery_duration;
    double order_duration = 0.0;

    Delivery(RestockableResource* stock_, SamplerPtr delivery_duration_, double order_duration_ = 0.0)
        : stock(stock_), delivery_duration(std::move(delivery_duration_)),
          order_duration(order_duration_) {}

    sim::Process process() override;
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


            double order = order_duration->sample_now();
            sim::make<Delivery>({}, this, delivery_duration, order);
            co_await demander->hold(order, {.mode = "wait_materials"});
        }
    }
};

inline sim::Process Delivery::process() {


    co_await hold(order_duration);
    double missing = stock->capacity() - stock->available_quantity();
    co_await hold(delivery_duration->sample_now());
    co_await call(stock->replenish(this, missing));
    stock->active_order = false;
}


class OperatorGroup;

class OperatorShiftManager : public ShiftManager {
  public:
    explicit OperatorShiftManager(OperatorGroup* operator_group);

    void on_enter() override;
    void on_leave() override;
};


class OperatorGroup : public sim::Resource, public Triggerable, public HasShifts {
  public:
    SamplerPtr productivity;
    double n_operators;
    OperatorShiftManager* manager = nullptr;
    std::vector<Task*> dependent_tasks;

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
                if (o->productivity != productivity)
                    throw std::invalid_argument("Operators do not have the same productivity");
        }
        for (const auto& alt : alternatives)
            for (const auto& [r, c] : alt) triggers.push_back(&r->trigger);
    }


    sim::Process request(Component* demander, std::optional<OpsList>* out, double fail_at = sim::inf,
                         std::optional<bool> cap_now = std::nullopt) {
        if (alternatives.empty()) {
            *out = OpsList{};
            co_return;
        }

        if (alternatives.size() == 1) {
            co_await demander->call(demander->request(
                reqspecs_(alternatives[0]),
                {.fail_at = fail_at, .mode = "wait_operators", .cap_now = cap_now}));
            *out = demander->failed() ? std::nullopt : std::optional<OpsList>(alternatives[0]);
            co_return;
        }

        while (true) {
            for (const auto& alt : alternatives) {
                co_await demander->call(
                    demander->request(reqspecs_(alt), {.fail_delay = 0, .mode = "wait_operators"}));
                if (!demander->failed()) {
                    *out = alt;
                    co_return;
                }
            }

            std::vector<sim::WaitSpec> specs;
            for (auto* t : triggers) specs.push_back(sim::WaitSpec(*t));
            co_await demander->sim::Component::wait(
                std::move(specs), {.fail_at = fail_at, .mode = "wait_operators", .cap_now = cap_now});
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

    void on_enter() override;
    void on_leave() override;
};

class FlexibleShutdowns : public Shutdowns {
  public:
    FlexibleShutdowns(Task* task_, Intervals intervals_);

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

    sim::Process process() override;
};

class NonFlexibleShutdowns : public Shutdowns {
  public:
    NonFlexibleShutdowns(Task* task_, Intervals intervals_);
};

class Breakdown : public Component {
  public:
    Task* task;
    SamplerPtr mtbf;
    SamplerPtr mttr;
    std::vector<Outlet*> outlets;

    Breakdown(Task* task_, SamplerPtr mtbf_, SamplerPtr mttr_, std::vector<Outlet*> outlets_ = {});

    sim::Process process() override;
};


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
    bool admin = false;

    Protocols protocols;

    virtual ~TaskConfig() = default;
};

class Carrier : public Component, public Dispatchable, public Donnable {
  public:
    Task* task;
    sim::State<bool> loaded{"", false};

    explicit Carrier(Task* task_) : task(task_) {}


    virtual void abort() = 0;
    virtual void abort_to(const std::vector<Outlet*>& outlets) = 0;


    virtual sim::Process handle_restock() = 0;
    virtual sim::Process freeze_abort_if(bool condition) = 0;
    virtual sim::Process wait_for_collector(double fail_at) = 0;
    virtual sim::Process request_resources(double fail_at) = 0;
    virtual sim::Process successfully_end_process() = 0;
    virtual double get_ideal_loading_duration() = 0;
    virtual double get_ideal_duration() = 0;


    sim::Process check_shift_fit(const Alternative::OpsList& operators, double duration);
    double operator_fit_deadline(const Alternative::OpsList& operators);

    sim::Process handle_operators(const Alternative::OpsList& operators, double ideal_duration,
                                  double* out);
    sim::Process handle_batch_operators(Alternative& operators, double earliest_deadline,
                                        double ideal_duration, double fail_before, bool do_restock,
                                        const char* work_mode);
    sim::Process handle_task_operators(double earliest_deadline, double ideal_duration);

    sim::Process process() override;
};

class TaskStarter : public Component, public Donnable {
  public:
    Task* task;
    explicit TaskStarter(Task* task_) : task(task_) {}
    sim::Process process() override;
};

class TaskShiftManager : public ShiftManager {
  public:
    explicit TaskShiftManager(HasShifts* entity_) : ShiftManager(entity_) {}
    void on_enter() override;
    void on_leave() override;
};

class Task : public Component, public HasShifts {
  public:
    std::shared_ptr<TaskConfig> config;
    double request_priority;
    std::map<Model*, bool> can_take_cache;
    TaskShiftManager* shift_manager = nullptr;
    NonFlexibleShutdowns* non_flexible_shutdowns = nullptr;
    FlexibleShutdowns* flexible_shutdowns = nullptr;
    sim::State<bool> is_in_breakdown{"", false};
    sim::State<bool> is_in_shutdown{"", false};
    sim::State<bool> is_frozen{"", false};

    Alternative::OpsList task_operators;
    std::unique_ptr<sim::Resource> vacant_slots;
    bool started_up = false;
    bool requested_per_task_operators = false;
    double labor_minutes = 0.0;
    std::optional<double> task_crew_since;
    CarrierTracker pending_carriers;
    CarrierTracker active_carriers;


    std::vector<Carrier*> all_carriers;
    sim::Monitor batch_sizes{"batch_sizes"};
    sim::Monitor cycle_times{"cycle_times"};
    sim::Monitor startup_times{"startup_times"};
    int pieces_in = 0;

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

    virtual Carrier* make_carrier() = 0;
    virtual void abort() = 0;
    virtual void abort_to(const std::vector<Outlet*>& outlets) { abort(); }

    const Interval* get_earliest_shutdown() const;
    double get_earliest_deadline() const {
        const Interval* s = get_earliest_shutdown();
        return s != nullptr ? s->start : sim::inf;
    }

    sim::Process handle_startup();
    sim::Process request_task_operators();
    void release_task_operators();
    double labor_minutes_total() const;
    bool any_task_operator_in_downtime() const {
        for (const auto& [g, c] : task_operators)
            if (g->is_in_downtime()) return true;
        return false;
    }


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
    task->startup_times.tally(duration);
    double booked = 0;
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
    static_cast<Task*>(dynamic_cast<Component*>(entity))->started_up = false;
    ShiftManager::on_leave();
}


inline void OperatorShiftManager::on_enter() {
    auto* g = static_cast<OperatorGroup*>(entity);
    g->set_capacity(g->n_operators);
    g->trigger.trigger();


    for (Task* task : g->dependent_tasks) task->is_frozen.set(false);
    ShiftManager::on_enter();
}

inline void OperatorShiftManager::on_leave() {
    auto* g = static_cast<OperatorGroup*>(entity);
    g->set_capacity(0);


    for (Task* task : g->dependent_tasks) {
        bool holds_this = false;
        for (const auto& [group, count] : task->task_operators)
            if (group == g) { holds_this = true; break; }
        if (holds_this) task->hand_off_crew_at_shift_end();
    }
    ShiftManager::on_leave();
}

inline sim::Process Task::handle_startup() {
    TaskStarter* task_starter = sim::make<TaskStarter>({}, this);
    co_await wait(task_starter->done);
    if (is_frozen()) co_return;
    started_up = true;
}


inline sim::Process Task::request_task_operators() {
    double deadline =
        std::min(non_flexible_shutdowns->get_deadline(), flexible_shutdowns->get_deadline());
    std::optional<Alternative::OpsList> got;
    co_await call(config->operators.request(this, &got, deadline));
    task_operators = got.value_or(Alternative::OpsList{});
    set_mode("");


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
        all_carriers.push_back(new_carrier);


        while (!new_carrier->loaded()) {
            co_await wait(new_carrier->loaded);
        }


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


        double base = earliest_deadline - duration - (fail_before - ideal_duration);
        double fit = operator_fit_deadline(*recuperated) - duration;
        co_await call(request_resources(std::min(base, fit)));
        co_await call(check_shift_fit(*recuperated, duration));
    }

    co_await hold(duration, {.mode = work_mode});
    double booked = 0;
    for (const auto& [g, c] : *recuperated) booked += c;
    task->labor_minutes += booked * duration;
    release(Alternative::reqspecs_(*recuperated));
}

inline sim::Process Carrier::handle_task_operators(double earliest_deadline, double ideal_duration) {
    double duration = 0;
    co_await call(handle_operators(task->task_operators, ideal_duration, &duration));
    co_await call(handle_restock());
    double fit = std::min(earliest_deadline, operator_fit_deadline(task->task_operators));
    co_await call(request_resources(fit - duration));
    co_await call(check_shift_fit(task->task_operators, duration));
    co_await hold(duration, {.mode = "processing"});
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
                   .mode = "wait_dispatch", .cap_now = true});
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

enum class AssociationType {
    ASSOCIATIVE,
    DISSOCIATIVE,
    PASSIVE,
};

struct ModelConfig {
    SamplerPtr duration;
    std::vector<std::pair<Resource*, double>> resources;
    int min_carrier_capacity = 1;
    int max_carrier_capacity = 1;
};

struct PieceTaskConfig : TaskConfig {
    std::vector<std::pair<Model*, ModelConfig>> models_configs;
    PieceCollectorType piece_collector_type = PieceCollectorType::NON_DISCRIMINATING_GREEDY;
    AssociationType association_type = AssociationType::PASSIVE;


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

    PieceTaskConfig& cfg();
    std::vector<sim::Store*> inlet_stores();
    sim::Resource& vacant_slots();

    int collected_weight() {
        int w = 0;
        for (Piece* p : collected_pieces) w += static_cast<int>(p->family().size());
        return w;
    }

    void check_piece_family_discrimination_compatibility(Piece* piece);
    void guard_carrier_capacity(Piece* piece, int max_carrier_capacity);

    sim::Process pick_piece(PieceFilter piece_filter, sim::StoreOpts opts, Piece** out);
    std::map<Model*, int> present_counts();
    Model* choose_focus_model(const std::map<Model*, int>& counts);
    sim::Process collect_until(double deadline, int target, PieceFilter piece_filter, bool* timed_out);
    sim::Process ensure_one();
    sim::Process top_up(int limit, PieceFilter piece_filter);
    sim::Process block_remainder(int max_carrier_capacity);
    sim::Process collect_batch(double deadline, int min_carrier_capacity, int max_carrier_capacity,
                               PieceFilter piece_filter, bool* timed_out);
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
    std::map<Model*, int> deposited;
    std::map<Model*, int> scrapped;

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
        release_task_operators();
        started_up = false;
    }

    static std::shared_ptr<PieceTaskConfig> validate_(const std::shared_ptr<PieceTaskConfig>& c) {
        if (!is_discriminating(c->piece_collector_type)) {
            const ModelConfig& first = c->models_configs.front().second;
            for (const auto& [m, cfg] : c->models_configs) {
                if (cfg.duration != first.duration)
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


inline PieceTaskConfig& PieceCollector::cfg() { return *task->pconfig(); }

inline std::vector<sim::Store*> PieceCollector::inlet_stores() {
    std::vector<sim::Store*> out;
    for (Buffer* b : task->inlets) out.push_back(b);
    return out;
}

inline sim::Resource& PieceCollector::vacant_slots() { return *task->vacant_slots; }

inline void PieceCollector::check_piece_family_discrimination_compatibility(Piece* piece) {
    if (!is_discriminating(cfg().piece_collector_type)) return;
    for (Piece* sibling : piece->family())
        if (sibling->model != piece->model)
            throw std::runtime_error(
                "Piece collector picked a cluster of different models for a discriminating task");
}

inline void PieceCollector::guard_carrier_capacity(Piece* piece, int max_carrier_capacity) {
    int weight = static_cast<int>(piece->family().size());
    if (weight > max_carrier_capacity || double(weight) > cfg().max_capacity) {
        std::ostringstream msg;
        msg << "incoherent task configs: task '" << task->name() << "' cannot digest a cluster of "
            << weight << " pieces formed upstream (max_carrier_capacity " << max_carrier_capacity
            << ", station capacity " << cfg().max_capacity << ")";
        throw std::runtime_error(msg.str());
    }
}


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


inline std::map<Model*, int> PieceCollector::present_counts() {
    std::map<Model*, int> counts;
    for (Buffer* inlet : task->inlets)
        for (const auto& [model, n] : inlet->model_counts) {
            if (n <= 0) continue;
            auto it = task->can_take_cache.find(model);
            if (it == task->can_take_cache.end())
                it = task->can_take_cache.emplace(model, task->can_take(model)).first;
            if (it->second) counts[model] += n;
        }
    return counts;
}

inline Model* PieceCollector::choose_focus_model(const std::map<Model*, int>& counts) {
    std::function<double(Model*)> key;
    switch (cfg().batch_model_choice->decide()) {
        case ModelChoice::MOST_PRESENT:
            key = [&counts](Model* m) { return -double(counts.at(m)); };
            break;
        case ModelChoice::FASTEST_TASK_DURATION:
            key = [this](Model* m) { return cfg().get_model_config(m).duration->mean_now(); };
            break;
        case ModelChoice::SMALLEST_GAP_TO_MIN_CARRIER_CAPACITY:
            key = [this, &counts](Model* m) {
                return cfg().get_model_config(m).min_carrier_capacity - double(counts.at(m));
            };
            break;
    }
    double best = sim::inf;
    for (const auto& [m, n] : counts) best = std::min(best, key(m));
    std::set<Model*> tied;
    for (const auto& [m, n] : counts)
        if (key(m) == best) tied.insert(m);
    if (tied.size() == 1) return *tied.begin();
    for (Buffer* inlet : task->inlets)
        for (sim::Component* c : *inlet) {
            Piece* p = static_cast<Piece*>(c);
            if (tied.count(p->model)) return p->model;
        }
    return *tied.begin();
}

inline sim::Process PieceCollector::collect_until(double deadline, int target, PieceFilter piece_filter,
                                                  bool* timed_out) {
    while (collected_weight() < target) {
        co_await call(request({{vacant_slots(), 1}}, {.fail_at = deadline,
                                                      .mode = "wait_slot",
                                                      .request_priority = task->request_priority}));
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
        check_piece_family_discrimination_compatibility(piece);
        int weight = static_cast<int>(piece->family().size());
        int max_carrier_capacity = cfg().get_model_config(piece->model).max_carrier_capacity;
        guard_carrier_capacity(piece, max_carrier_capacity);
        Buffer* origin = static_cast<Buffer*>(from_store_store());
        if (collected_weight() + weight > max_carrier_capacity) {
            release({{vacant_slots(), 1}});
            piece->enter(*origin);
            *timed_out = false;
            co_return;
        }

        if (weight > 1) {
            co_await call(request({{vacant_slots(), double(weight - 1)}},
                                  {.fail_at = deadline, .mode = "wait_slot",
                                   .request_priority = task->request_priority}));
            if (failed()) {
                release({{vacant_slots(), 1}});
                piece->enter(*origin);
                *timed_out = true;
                co_return;
            }
        }

        collected_pieces.push_back(piece);
        task->pieces_in += weight;
    }
    *timed_out = false;
}

inline sim::Process PieceCollector::ensure_one() {
    if (!collected_pieces.empty()) co_return;
    co_await call(request({{vacant_slots(), 1}},
                          {.mode = "wait_slot", .request_priority = task->request_priority}));
    Piece* piece = nullptr;
    co_await call(pick_piece([this](Piece* p) { return task->can_take(p); },
                             {.request_priority = task->request_priority}, &piece));
    check_piece_family_discrimination_compatibility(piece);
    int weight = static_cast<int>(piece->family().size());
    guard_carrier_capacity(piece, cfg().get_model_config(piece->model).max_carrier_capacity);
    if (weight > 1)
        co_await call(request({{vacant_slots(), double(weight - 1)}},
                              {.mode = "wait_slot", .request_priority = task->request_priority}));
    collected_pieces.push_back(piece);
    task->pieces_in += weight;
}

inline sim::Process PieceCollector::top_up(int limit, PieceFilter piece_filter) {
    while (collected_weight() < limit && vacant_slots().available_quantity() > 0) {
        Piece* piece = nullptr;
        co_await call(pick_piece(piece_filter,
                                 {.fail_delay = 0, .request_priority = task->request_priority}, &piece));
        if (failed()) break;
        check_piece_family_discrimination_compatibility(piece);
        int weight = static_cast<int>(piece->family().size());
        if (collected_weight() + weight > limit ||
            double(weight) > vacant_slots().available_quantity()) {
            piece->enter(*static_cast<Buffer*>(from_store_store()));
            break;
        }

        co_await call(request({{vacant_slots(), double(weight)}},
                              {.mode = "wait_slot", .request_priority = task->request_priority}));
        collected_pieces.push_back(piece);
        task->pieces_in += weight;
    }
}

inline sim::Process PieceCollector::block_remainder(int max_carrier_capacity) {
    if (!cfg().contiguous_carriers) {
        int remainder = max_carrier_capacity - collected_weight();
        co_await call(request({{vacant_slots(), double(remainder)}},
                              {.mode = "wait_slot", .request_priority = task->request_priority}));
    }
}

inline sim::Process PieceCollector::collect_batch(double deadline, int min_carrier_capacity,
                                                  int max_carrier_capacity, PieceFilter piece_filter,
                                                  bool* timed_out) {
    co_await call(request({{vacant_slots(), double(min_carrier_capacity)}},
                          {.fail_at = deadline, .mode = "wait_slot",
                           .request_priority = task->request_priority}));
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
        double available_extra = vacant_slots().available_quantity();
        std::vector<std::pair<Piece*, Buffer*>> selected;
        int weight_sum = 0;
        for (auto& [piece, buffer] : valid_pieces) {
            check_piece_family_discrimination_compatibility(piece);
            guard_carrier_capacity(piece, max_carrier_capacity);
            int weight = static_cast<int>(piece->family().size());
            if (weight_sum + weight > max_carrier_capacity) break;
            if (double(std::max(0, weight_sum + weight - min_carrier_capacity)) > available_extra) break;
            selected.push_back({piece, buffer});
            weight_sum += weight;
        }

        if (weight_sum >= min_carrier_capacity) {
            int additional = weight_sum - min_carrier_capacity;
            if (additional > 0) {
                co_await call(request({{vacant_slots(), double(additional)}},
                                      {.fail_delay = 0, .mode = "wait_slot",
                                       .request_priority = task->request_priority}));
                if (failed()) {
                    additional = 0;
                    std::vector<std::pair<Piece*, Buffer*>> trimmed;
                    weight_sum = 0;
                    for (auto& pb : selected) {
                        int w = static_cast<int>(pb.first->family().size());
                        if (weight_sum + w > min_carrier_capacity) break;
                        trimmed.push_back(pb);
                        weight_sum += w;
                    }
                    selected = std::move(trimmed);
                }
            }

            {
                std::vector<std::pair<Piece*, Buffer*>> still;
                for (auto& pb : selected)
                    if (pb.second->contains(pb.first)) still.push_back(pb);
                selected = std::move(still);
            }
            weight_sum = 0;
            for (auto& pb : selected) weight_sum += static_cast<int>(pb.first->family().size());
            if (weight_sum < min_carrier_capacity) {
                if (additional > 0) release({{vacant_slots(), double(additional)}});
                continue;
            }

            int surplus = (min_carrier_capacity + additional) - weight_sum;
            if (surplus > 0) release({{vacant_slots(), double(surplus)}});

            for (auto& [piece, buffer] : selected) {
                piece->leave(*buffer);
                collected_pieces.push_back(piece);
                task->pieces_in += static_cast<int>(piece->family().size());
            }

            if (!cfg().contiguous_carriers) {
                co_await call(request({{vacant_slots(), double(max_carrier_capacity - weight_sum)}},
                                      {.mode = "wait_slot",
                                       .request_priority = task->request_priority}));
            }
        } else {
            std::vector<sim::WaitSpec> specs;
            for (Buffer* inlet : task->inlets) specs.push_back(sim::WaitSpec(inlet->trigger));
            co_await sim::Component::wait(std::move(specs), {.fail_at = deadline, .mode = "wait_pieces"});
            if (failed()) {
                release({{vacant_slots(), double(min_carrier_capacity)}});
                *timed_out = true;
                co_return;
            }
        }
    }

    *timed_out = false;
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
    set_mode("");
    done.set(true);
    co_await passivate();
}

inline sim::Process DiscriminatingGreedyPieceCollector::process() {
    co_await wait(allow_dispatch);
    double deadline = env->now() + cfg().timeout;

    std::map<Model*, int> counts = present_counts();

    Model* focus_on = nullptr;
    if (!counts.empty()) {
        focus_on = choose_focus_model(counts);
    } else {
        bool timed_out = false;
        co_await call(collect_until(deadline, 1, [this](Piece* p) { return task->can_take(p); },
                                    &timed_out));
        if (timed_out) co_await call(ensure_one());
        focus_on = collected_pieces.front()->model;
    }

    const ModelConfig& model_config = cfg().get_model_config(focus_on);
    PieceFilter focus_filter = [this, focus_on](Piece* p) {
        return task->can_take(p) && p->has_model(focus_on);
    };

    bool timed_out = false;
    co_await call(collect_until(deadline, model_config.min_carrier_capacity, focus_filter, &timed_out));
    if (timed_out) {
        co_await call(ensure_one());
    } else {
        co_await call(top_up(model_config.max_carrier_capacity, focus_filter));
    }

    co_await call(block_remainder(model_config.max_carrier_capacity));
    set_mode("");
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

    set_mode("");
    done.set(true);
    co_await passivate();
}

inline sim::Process DiscriminatingAltruisticPieceCollector::process() {
    co_await wait(allow_dispatch);
    double deadline = env->now() + cfg().timeout;
    bool timed_out = false;

    std::map<Model*, int> counts;
    while (true) {
        counts = present_counts();
        if (!counts.empty()) break;

        std::vector<sim::WaitSpec> specs;
        for (Buffer* inlet : task->inlets) specs.push_back(sim::WaitSpec(inlet->trigger));
        co_await sim::Component::wait(std::move(specs), {.fail_at = deadline, .mode = "wait_pieces"});
        if (failed()) {
            timed_out = true;
            break;
        }
    }

    if (!timed_out) {
        Model* focus_on = choose_focus_model(counts);
        const ModelConfig& model_config = cfg().get_model_config(focus_on);
        co_await call(collect_batch(deadline, model_config.min_carrier_capacity,
                                    model_config.max_carrier_capacity,
                                    [this, focus_on](Piece* p) {
                                        return task->can_take(p) && p->has_model(focus_on);
                                    },
                                    &timed_out));
    }

    if (timed_out) co_await call(ensure_one());

    set_mode("");
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
    piece_collector->set_mode("");
    piece_collector->done.set(true);
    piece_collector->cancel();

    set_mode("");
    loaded.set(true);
    done.set(true);

    task->pending_carriers.remove(this);
    task->active_carriers.remove(this);


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
        abort();
        co_await sim::Yield{};
    }
}

inline sim::Process PieceCarrier::wait_for_collector(double fail_at) {
    piece_collector->allow_dispatch.set(true);
    co_await wait(piece_collector->done, {.fail_at = fail_at, .mode = "collecting"});
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
                      : double(piece_collector->collected_weight());
    std::vector<sim::ReqSpec> resources;
    for (const auto& [r, q] : ptask_()->pconfig()->get_model_config(model).resources)
        resources.push_back(sim::ReqSpec(*r, q * mult));
    co_await call(request(std::move(resources),
                          {.fail_at = fail_at, .mode = "wait_materials", .cap_now = true}));
    co_await call(freeze_abort_if(failed()));
}

inline sim::Process PieceCarrier::successfully_end_process() {
    set_mode("");
    piece_collector->cancel();

    auto& pieces = piece_collector->collected_pieces;
    task->batch_sizes.tally(static_cast<double>(piece_collector->collected_weight()));
    task->cycle_times.tally(env->now() - creation_time());

    std::vector<Piece*> tokens;
    switch (ptask_()->pconfig()->association_type) {
        case AssociationType::ASSOCIATIVE:
            Piece::associate_all(pieces);
            tokens = {pieces.front()};
            break;
        case AssociationType::DISSOCIATIVE:
            for (Piece* piece : pieces) {
                std::vector<Piece*> fam = piece->family();
                Piece::dissociate_all(fam);
                tokens.insert(tokens.end(), fam.begin(), fam.end());
            }
            break;
        case AssociationType::PASSIVE:
            tokens = pieces;
            break;
    }

    place(tokens, ptask_()->outlets);

    for (Piece* token : tokens) {
        bool scrapped_here = false;
        for (sim::Queue* q : token->queues())
            if (auto* b = dynamic_cast<Buffer*>(q);
                b != nullptr && b->buffer_type == BufferType::SCRAP) {
                scrapped_here = true;
                break;
            }
        for (Piece* member : token->family()) {
            ptask_()->deposited[member->model] += 1;
            if (scrapped_here) ptask_()->scrapped[member->model] += 1;
        }
    }
    done.set(true);

    task->pending_carriers.remove(this);
    task->active_carriers.remove(this);
    co_return;
}

inline Carrier* PieceTask::make_carrier() { return sim::make<PieceCarrier>({}, this); }


enum class ResourceCollectorType { GREEDY, ALTRUISTIC };

struct TransformedResource {
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
        release_task_operators();
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

    set_mode("");
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

    set_mode("");
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

    resource_collector->set_mode("");
    resource_collector->done.set(true);
    resource_collector->cancel();

    set_mode("");
    loaded.set(true);
    done.set(true);

    task->pending_carriers.remove(this);
    task->active_carriers.remove(this);

    if (task->active_carriers.empty()) task->release_task_operators();
    cancel();
}

inline sim::Process ResourceCarrier::freeze_abort_if(bool condition) {
    if (condition) {
        task->is_frozen.set(true);
        abort();
        co_await sim::Yield{};
    }
}

inline sim::Process ResourceCarrier::wait_for_collector(double fail_at) {
    co_await call(handle_restock());

    if (env->now() >= fail_at) {
        co_await call(freeze_abort_if(true));
        co_return;
    }

    resource_collector->allow_dispatch.set(true);
    co_await wait(resource_collector->done, {.fail_at = fail_at, .mode = "collecting", .cap_now = true});
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
                          {.fail_at = fail_at, .mode = "wait_materials", .cap_now = true}));
    co_await call(freeze_abort_if(failed()));
}

inline sim::Process ResourceCarrier::successfully_end_process() {
    for (const auto& [resource_out, distr] : rtask_()->rconfig()->resources_out_distr)
        co_await call(resource_out->replenish(this, distr.sample() * resource_collector->requested_quantity));

    task->batch_sizes.tally(resource_collector->requested_quantity);
    task->cycle_times.tally(env->now() - creation_time());

    resource_collector->set_mode("");
    resource_collector->cancel();
    set_mode("");
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

    static constexpr double NO_PROGRESS_GUARD_DAYS = 400.0;

    sim::Process process() override {
        co_await wait(allow_dispatch);
        double deadline = timeout + env->now();
        double guard = NO_PROGRESS_GUARD_DAYS * 1440.0;
        while (static_cast<int>(exit_buffer->size()) < total) {
            double fail_at = deadline != sim::inf ? deadline : env->now() + guard;
            co_await wait(exit_buffer->trigger, {.fail_at = fail_at});
            if (failed()) {
                if (deadline == sim::inf)
                    throw std::runtime_error(
                        "no piece reached the exit for " + std::to_string(int(NO_PROGRESS_GUARD_DAYS)) +
                        " simulated days while the timeout is infinite (" +
                        std::to_string(exit_buffer->size()) + "/" + std::to_string(total) +
                        " produced); stopping a run that can no longer progress");
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

}
