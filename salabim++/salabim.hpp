//  salabim.hpp — a C++20 port of salabim, the Python discrete event simulation library
//  =====================================================================================
//
//  This single-header library mimics salabim (https://www.salabim.org, version 26.0.8)
//  as closely as C++ allows:
//
//    * same process-interaction world view: Components with a process() describing
//      their behaviour over simulated time (C++20 coroutines instead of Python
//      generators: `co_await hold(10)` instead of `yield self.hold(10)`)
//    * same event-chain mechanics: (time, priority, sequence) ordered event list,
//      urgent scheduling, standby components
//    * same building blocks: Environment, Component, Queue, Resource (incl. anonymous),
//      State, Store, Monitor (level and non-level), ComponentGenerator
//    * same statistics, same print_statistics()/print_histogram() output format
//    * same tracing format
//    * same random distributions (seeded, reproducible streams via sim::Random)
//
//  Animation and the Python-specific facilities (string-eval wait conditions,
//  monitor slicing/merging, video export, ...) are intentionally not ported.
//
//  License: MIT. Not affiliated with the salabim project; salabim itself is
//  (c) Ruud van der Ham and contributors, MIT licensed.
//
#ifndef SALABIM_HPP
#define SALABIM_HPP

#include <algorithm>
#include <any>
#include <cmath>
#include <coroutine>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <bit>
#include <charconv>
#include <exception>
#include <functional>
#include <iomanip>
#include <initializer_list>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <queue>
#include <random>
#include <set>
#include <source_location>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <typeinfo>
#include <unordered_map>
#include <utility>
#include <vector>

#if defined(__GNUG__) || defined(__clang__)
#include <cxxabi.h>
#endif

namespace sim {

inline constexpr double inf = std::numeric_limits<double>::infinity();
inline constexpr double nan_ = std::numeric_limits<double>::quiet_NaN();

inline const char* version() { return "salabim++ 1.0.0 (API of salabim 26.0.8)"; }

// ---------------------------------------------------------------------------
// component statuses (mirroring salabim's module-level string constants)
// ---------------------------------------------------------------------------
enum Status : int {
    data = 0,
    current = 1,
    standby = 2,
    passive = 3,
    interrupted = 4,
    scheduled = 5,
    requesting = 6,
    waiting = 7,
};

inline const char* status_to_str(Status s) {
    static const char* names[] = {"data", "current", "standby", "passive",
                                  "interrupted", "scheduled", "requesting", "waiting"};
    return names[static_cast<int>(s)];
}

// ---------------------------------------------------------------------------
// forward declarations
// ---------------------------------------------------------------------------
class Environment;
class Component;
class Queue;
class Store;
class Resource;
class StateBase;
template <class T> class State;
class Monitor;
class Random;

class SalabimError : public std::runtime_error {
  public:
    using std::runtime_error::runtime_error;
};

class QueueFullError : public SalabimError {
  public:
    using SalabimError::SalabimError;
};

class SimulationStopped : public SalabimError {
  public:
    SimulationStopped() : SalabimError("simulation stopped") {}
};

// ---------------------------------------------------------------------------
// small string / formatting helpers (mirroring salabim's pad/rpad/fn)
// ---------------------------------------------------------------------------
namespace detail {

inline std::string pad(std::string_view txt, long n) {
    if (n <= 0) return "";
    std::string s(txt.substr(0, static_cast<size_t>(n)));
    if (static_cast<long>(s.size()) < n) s.append(static_cast<size_t>(n) - s.size(), ' ');
    return s;
}

inline std::string rpad(std::string_view txt, long n) {
    std::string s(txt);
    if (static_cast<long>(s.size()) < n) s.insert(0, static_cast<size_t>(n) - s.size(), ' ');
    if (static_cast<long>(s.size()) > n) s = s.substr(s.size() - static_cast<size_t>(n));
    return s;
}

inline std::string strip(std::string_view sv) {
    size_t b = sv.find_first_not_of(" \t\r\n");
    if (b == std::string_view::npos) return "";
    size_t e = sv.find_last_not_of(" \t\r\n");
    return std::string(sv.substr(b, e - b + 1));
}

template <class... Args>
inline std::string sprintf_str(const char* fmt, Args... args) {
    char buf[128];
    std::snprintf(buf, sizeof buf, fmt, args...);
    return buf;
}

// salabim's fn(x, length, d): fixed-width number formatting used in all statistics output
inline std::string fn(double x, int length, int d) {
    if (std::isnan(x)) return std::string(static_cast<size_t>(length), ' ');
    if (x >= std::pow(10.0, length - d - 1)) {
        std::string f = "%" + std::to_string(length) + "." + std::to_string(length - d - 3) + "e";
        return sprintf_str(f.c_str(), x);
    }
    if (x == std::floor(x) && std::abs(x) < 9.2e18) {
        std::string f = "%" + std::to_string(length - d - 1) + "lld";
        return sprintf_str(f.c_str(), static_cast<long long>(x)) + std::string(static_cast<size_t>(d) + 1, ' ');
    }
    std::string f = "%" + std::to_string(length) + "." + std::to_string(d) + "f";
    return sprintf_str(f.c_str(), x);
}

// merges all non-blank elements, separated by one blank (salabim's merge_blanks)
template <class... S>
inline std::string merge_blanks(S&&... parts) {
    std::string out;
    auto add = [&out](std::string_view p) {
        if (p.empty()) return;
        if (!out.empty()) out += ' ';
        out += p;
    };
    (add(parts), ...);
    return out;
}

inline std::string lowercase(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

// Thrown by Component::cancel when a component cancels ITSELF: Python's cancel
// switches to the scheduler greenlet and never returns, so all code between the
// cancel call and the end of the process is skipped. Unwinds through the
// coroutine chain; the scheduler destroys the frame silently (no 'ended' trace).
struct AbandonedByCancel {};

inline std::string demangle(const char* name) {
#if defined(__GNUG__) || defined(__clang__)
    int status = 0;
    char* dem = abi::__cxa_demangle(name, nullptr, nullptr, &status);
    if (status == 0 && dem) {
        std::string out(dem);
        std::free(dem);
        // strip namespaces / template arguments for the short class name
        if (auto pos = out.find('<'); pos != std::string::npos) out = out.substr(0, pos);
        if (auto pos = out.rfind("::"); pos != std::string::npos) out = out.substr(pos + 2);
        return out;
    }
#endif
    std::string out(name);
    while (!out.empty() && std::isdigit(static_cast<unsigned char>(out.front()))) out.erase(out.begin());
    return out;
}

// python-style repr / str for trace messages
inline std::string py_repr(bool v) { return v ? "True" : "False"; }
inline std::string py_repr(const std::string& v) { return "'" + v + "'"; }
inline std::string py_repr(const char* v) { return std::string("'") + v + "'"; }
// python repr() of a float: shortest round-trip digits, fixed notation
// for exponents in [-4, 16), otherwise scientific
inline std::string py_repr(double v) {
    if (std::isnan(v)) return "nan";
    if (std::isinf(v)) return v > 0 ? "inf" : "-inf";
    if (v == 0.0) return std::signbit(v) ? "-0.0" : "0.0";
    char buf[64];
    auto res = std::to_chars(buf, buf + sizeof buf, v, std::chars_format::scientific);
    std::string s(buf, res.ptr); // shortest, e.g. "1.3832e+01"
    bool neg = false;
    if (s[0] == '-') {
        neg = true;
        s.erase(0, 1);
    }
    auto epos = s.find('e');
    int exp = std::stoi(s.substr(epos + 1));
    std::string digits = s.substr(0, epos);
    digits.erase(std::remove(digits.begin(), digits.end(), '.'), digits.end());
    std::string out;
    if (exp >= 16 || exp < -4) {
        out = digits.substr(0, 1);
        if (digits.size() > 1) out += "." + digits.substr(1);
        out += sprintf_str("e%+03d", exp);
    } else if (exp >= static_cast<int>(digits.size()) - 1) {
        out = digits + std::string(static_cast<size_t>(exp) - digits.size() + 1, '0') + ".0";
    } else if (exp >= 0) {
        out = digits.substr(0, static_cast<size_t>(exp) + 1) + "." + digits.substr(static_cast<size_t>(exp) + 1);
    } else {
        out = "0." + std::string(static_cast<size_t>(-exp) - 1, '0') + digits;
    }
    return (neg ? "-" : "") + out;
}
template <class T>
    requires std::is_integral_v<T> && (!std::is_same_v<T, bool>)
inline std::string py_repr(T v) { return std::to_string(v); }

inline std::string py_str(bool v) { return v ? "True" : "False"; }
inline std::string py_str(const std::string& v) { return v; }
inline std::string py_str(const char* v) { return v; }
inline std::string py_str(double v) { return py_repr(v); }
template <class T>
    requires std::is_integral_v<T> && (!std::is_same_v<T, bool>)
inline std::string py_str(T v) { return std::to_string(v); }

} // namespace detail

// ---------------------------------------------------------------------------
// Random — a seeded random stream (std::mt19937_64) plus the sampling
// helpers the distributions below build on. Runs with the same seed are
// reproducible.
// ---------------------------------------------------------------------------
class Random {
  public:
    explicit Random(std::uint64_t seed_value = 5489u) { seed(seed_value); }

    void seed(std::uint64_t n) {
        eng_.seed(n);
        gauss_next_.reset();
    }

    // random() -> float in [0, 1), 53-bit resolution
    double random() {
        return static_cast<double>(eng_() >> 11) * (1.0 / 9007199254740992.0);
    }

    // getrandbits(k) for 1 <= k <= 64
    std::uint64_t getrandbits(int k) {
        if (k <= 0) throw SalabimError("getrandbits: k must be > 0");
        if (k > 64) throw SalabimError("getrandbits: k must be <= 64");
        return eng_() >> (64 - k);
    }

    // uniform integer in [0, n): rejection sampling on bit_length-sized draws
    std::uint64_t randbelow(std::uint64_t n) {
        if (n == 0) return 0;
        int k = 64 - std::countl_zero(n); // bit_length
        std::uint64_t r = getrandbits(k);
        while (r >= n) r = getrandbits(k);
        return r;
    }

    long long randint(long long a, long long b) { // randrange(a, b+1)
        if (b < a) throw SalabimError("randint: empty range");
        return a + static_cast<long long>(randbelow(static_cast<std::uint64_t>(b - a + 1)));
    }

    double uniform(double a, double b) {
        return a + (b - a) * random();
    }

    double expovariate(double lambd) {
        return -std::log(1.0 - random()) / lambd;
    }

    double normalvariate(double mu = 0.0, double sigma = 1.0) {
        static const double NV_MAGICCONST = 4 * std::exp(-0.5) / std::sqrt(2.0);
        double u1, u2, z;
        while (true) {
            u1 = random();
            u2 = 1.0 - random();
            z = NV_MAGICCONST * (u1 - 0.5) / u2;
            if (z * z / 4.0 <= -std::log(u2)) break;
        }
        return mu + z * sigma;
    }

    double gauss(double mu = 0.0, double sigma = 1.0) {
        static const double TWOPI = 2.0 * 3.14159265358979323846;
        std::optional<double> z = gauss_next_;
        gauss_next_.reset();
        if (!z) {
            double x2pi = random() * TWOPI;
            double g2rad = std::sqrt(-2.0 * std::log(1.0 - random()));
            z = std::cos(x2pi) * g2rad;
            gauss_next_ = std::sin(x2pi) * g2rad;
        }
        return mu + *z * sigma;
    }

    double triangular(double low = 0.0, double high = 1.0,
                      std::optional<double> mode = std::nullopt) {
        double u = random();
        if (high == low) return low;
        double c = mode ? (*mode - low) / (high - low) : 0.5;
        if (u > c) {
            u = 1.0 - u;
            c = 1.0 - c;
            std::swap(low, high);
        }
        return low + (high - low) * std::sqrt(u * c);
    }

    double gammavariate(double alpha, double beta) {
        static const double LOG4 = std::log(4.0);
        static const double SG_MAGICCONST = 1.0 + std::log(4.5);
        if (alpha <= 0.0 || beta <= 0.0)
            throw SalabimError("gammavariate: alpha and beta must be > 0.0");
        if (alpha > 1.0) {
            double ainv = std::sqrt(2.0 * alpha - 1.0);
            double bbb = alpha - LOG4;
            double ccc = alpha + ainv;
            while (true) {
                double u1 = random();
                if (!(1e-7 < u1 && u1 < 0.9999999)) continue;
                double u2 = 1.0 - random();
                double v = std::log(u1 / (1.0 - u1)) / ainv;
                double x = alpha * std::exp(v);
                double z = u1 * u1 * u2;
                double r = bbb + ccc * v - x;
                if (r + SG_MAGICCONST - 4.5 * z >= 0.0 || r >= std::log(z)) return x * beta;
            }
        } else if (alpha == 1.0) {
            return -std::log(1.0 - random()) * beta;
        } else {
            static const double E = 2.71828182845904523536;
            double x;
            while (true) {
                double u = random();
                double b = (E + alpha) / E;
                double p = b * u;
                if (p <= 1.0)
                    x = std::pow(p, 1.0 / alpha);
                else
                    x = -std::log((b - p) / alpha);
                double u1 = random();
                if (p > 1.0) {
                    if (u1 <= std::pow(x, alpha - 1.0)) break;
                } else if (u1 <= std::exp(-x)) {
                    break;
                }
            }
            return x * beta;
        }
    }

    double betavariate(double alpha, double beta) {
        double y = gammavariate(alpha, 1.0);
        if (y == 0.0) return 0.0;
        return y / (y + gammavariate(beta, 1.0));
    }

    double weibullvariate(double alpha, double beta) {
        double u = 1.0 - random();
        return alpha * std::pow(-std::log(u), 1.0 / beta);
    }

    // pick a uniform index in [0, n)
    std::size_t sample_index(std::size_t n) { return static_cast<std::size_t>(randbelow(n)); }

    template <class T>
    void shuffle(std::vector<T>& x) {
        if (x.size() < 2) return;
        for (std::size_t i = x.size() - 1; i >= 1; --i) {
            std::size_t j = static_cast<std::size_t>(randbelow(i + 1));
            std::swap(x[i], x[j]);
            if (i == 1) break;
        }
    }

  private:
    std::mt19937_64 eng_;
    std::optional<double> gauss_next_;
};

// ---------------------------------------------------------------------------
// global state (mirroring salabim's class g)
// ---------------------------------------------------------------------------
struct g {
    inline static Environment* default_env = nullptr;
    inline static Random random{};   // the shared default stream
    inline static bool default_cap_now = false;
};

inline Random& random_stream() { return g::random; }

inline void random_seed(std::uint64_t seed) { g::random.seed(seed); }

inline bool default_cap_now(std::optional<bool> value = std::nullopt) {
    if (value) g::default_cap_now = *value;
    return g::default_cap_now;
}

// ---------------------------------------------------------------------------
// time unit support (mirrors salabim's _time_unit_lookup / _time_unit_factor)
// ---------------------------------------------------------------------------
namespace detail {

inline double time_unit_lookup(std::string_view d) {
    if (d == "years") return 1.0 / 86400.0 / 365.0;
    if (d == "weeks") return 1.0 / 86400.0 / 7.0;
    if (d == "days") return 1.0 / 86400.0;
    if (d == "hours") return 1.0 / 3600.0;
    if (d == "minutes") return 1.0 / 60.0;
    if (d == "seconds") return 1.0;
    if (d == "milliseconds") return 1e3;
    if (d == "microseconds") return 1e6;
    if (d == "n/a") return 0.0;
    throw SalabimError("time unit '" + std::string(d) + "' not supported");
}

double time_unit_factor(std::string_view time_unit, const Environment* env); // defined after Environment

} // namespace detail

// ---------------------------------------------------------------------------
// distributions — every sample() draws from the shared (or given) Random stream.
// ---------------------------------------------------------------------------
class Distribution_ {
  public:
    virtual ~Distribution_() = default;
    virtual double sample() = 0;
    virtual double mean() const = 0;
    double operator()() { return sample(); }

    // rejection-sample within bounds; salabim's bounded_sample
    double bounded_sample(std::optional<double> lowerbound = std::nullopt,
                          std::optional<double> upperbound = std::nullopt,
                          std::optional<double> fail_value = std::nullopt,
                          int number_of_retries = 100,
                          bool include_lowerbound = true, bool include_upperbound = true) {
        double lb = lowerbound.value_or(-inf), ub = upperbound.value_or(inf);
        if (lb > ub) throw SalabimError("lowerbound > upperbound");
        double fail = fail_value.value_or(lowerbound ? lb : ub);
        for (int i = 0; i < std::max(1, number_of_retries); ++i) {
            double s = sample();
            bool ok_low = include_lowerbound ? (s >= lb) : (s > lb);
            bool ok_up = include_upperbound ? (s <= ub) : (s < ub);
            if (ok_low && ok_up) return s;
        }
        return fail;
    }

  protected:
    Random* stream_ = &g::random;
    std::string time_unit_{};
    mutable std::optional<double> tuf_cache_;
    const Environment* tu_env_ = nullptr;

    void set_stream(Random* rs) { stream_ = rs ? rs : &g::random; }
    double tuf() const; // time unit factor, lazily resolved (defined after Environment)
};

using Dist = Distribution_; // short alias

class Constant : public Distribution_ {
  public:
    explicit Constant(double value, std::string time_unit = {}, Random* randomstream = nullptr)
        : value_(value) {
        time_unit_ = std::move(time_unit);
        set_stream(randomstream);
    }
    double sample() override { return value_ * tuf(); }
    double mean() const override { return value_ * tuf(); }

  private:
    double value_;
};

class Uniform : public Distribution_ {
  public:
    explicit Uniform(double lowerbound, std::optional<double> upperbound = std::nullopt,
                     std::string time_unit = {}, Random* randomstream = nullptr)
        : lb_(lowerbound), ub_(upperbound.value_or(lowerbound)) {
        if (lb_ > ub_) throw SalabimError("lowerbound>upperbound");
        time_unit_ = std::move(time_unit);
        set_stream(randomstream);
    }
    double sample() override { return stream_->uniform(lb_, ub_) * tuf(); }
    double mean() const override { return (lb_ + ub_) / 2.0 * tuf(); }

  private:
    double lb_, ub_;
};

class IntUniform : public Distribution_ {
  public:
    explicit IntUniform(long long lowerbound, std::optional<long long> upperbound = std::nullopt,
                        std::string time_unit = {}, Random* randomstream = nullptr)
        : lb_(lowerbound), ub_(upperbound.value_or(lowerbound)) {
        if (lb_ > ub_) throw SalabimError("lowerbound>upperbound");
        time_unit_ = std::move(time_unit);
        set_stream(randomstream);
    }
    double sample() override { return static_cast<double>(stream_->randint(lb_, ub_)) * tuf(); }
    double mean() const override { return (static_cast<double>(lb_) + static_cast<double>(ub_)) / 2.0 * tuf(); }

  private:
    long long lb_, ub_;
};

struct ExpRate { double rate; };  // tag to construct Exponential from a rate

class Exponential : public Distribution_ {
  public:
    explicit Exponential(double mean, std::string time_unit = {}, Random* randomstream = nullptr)
        : mean_(mean) {
        if (mean <= 0) throw SalabimError("mean<=0");
        time_unit_ = std::move(time_unit);
        set_stream(randomstream);
    }
    explicit Exponential(ExpRate rate, std::string time_unit = {}, Random* randomstream = nullptr)
        : Exponential(1.0 / rate.rate, std::move(time_unit), randomstream) {
        if (rate.rate <= 0) throw SalabimError("rate<=0");
    }
    double sample() override { return stream_->expovariate(1.0 / mean_) * tuf(); }
    double mean() const override { return mean_ * tuf(); }

  private:
    double mean_;
};

struct NormalOpts {
    std::optional<double> coefficient_of_variation{};
    bool use_gauss = false;
    std::string time_unit{};
    Random* randomstream = nullptr;
};

class Normal : public Distribution_ {
  public:
    using Opts = NormalOpts;
    explicit Normal(double mean, std::optional<double> standard_deviation = std::nullopt,
                    NormalOpts opts = {})
        : mean_(mean), use_gauss_(opts.use_gauss) {
        if (standard_deviation) {
            if (opts.coefficient_of_variation)
                throw SalabimError("both standard_deviation and coefficient_of_variation specified");
            sd_ = *standard_deviation;
        } else if (opts.coefficient_of_variation) {
            if (mean == 0) throw SalabimError("coefficient_of_variation not allowed with mean = 0");
            sd_ = *opts.coefficient_of_variation * mean;
        } else {
            sd_ = 0.0;
        }
        if (sd_ < 0) throw SalabimError("standard_deviation < 0");
        time_unit_ = std::move(opts.time_unit);
        set_stream(opts.randomstream);
    }
    double sample() override {
        return (use_gauss_ ? stream_->gauss(mean_, sd_) : stream_->normalvariate(mean_, sd_)) * tuf();
    }
    double mean() const override { return mean_ * tuf(); }

  private:
    double mean_, sd_;
    bool use_gauss_;
};

class Triangular : public Distribution_ {
  public:
    explicit Triangular(double low, std::optional<double> high = std::nullopt,
                        std::optional<double> mode = std::nullopt,
                        std::string time_unit = {}, Random* randomstream = nullptr)
        : low_(low), high_(high.value_or(low)) {
        mode_ = mode.value_or((high_ + low_) / 2.0);
        if (low_ > high_) throw SalabimError("low>high");
        if (low_ > mode_) throw SalabimError("low>mode");
        if (high_ < mode_) throw SalabimError("high<mode");
        time_unit_ = std::move(time_unit);
        set_stream(randomstream);
    }
    double sample() override { return stream_->triangular(low_, high_, mode_) * tuf(); }
    double mean() const override { return (low_ + mode_ + high_) / 3.0 * tuf(); }

  private:
    double low_, high_, mode_;
};

class Poisson : public Distribution_ {
  public:
    explicit Poisson(double mean, Random* randomstream = nullptr) : mean_(mean) {
        if (mean <= 0) throw SalabimError("mean (lambda) should be > 0");
        set_stream(randomstream);
    }
    double sample() override {
        double t = std::exp(-mean_);
        double s = t;
        long long k = 0;
        double u = stream_->random();
        double last_s = inf;
        while (s < u) {
            ++k;
            t *= mean_ / static_cast<double>(k);
            s += t;
            if (last_s == s) return static_cast<double>(sample_fallback());
            last_s = s;
        }
        return static_cast<double>(k);
    }
    double mean() const override { return mean_; }

  private:
    double mean_;
    long long sample_fallback() {
        double t = 0;
        long long n = 0;
        while (true) {
            t += -std::log(stream_->random()) / mean_;
            if (t > 1) break;
            ++n;
        }
        return n;
    }
};

class Weibull : public Distribution_ {
  public:
    explicit Weibull(double scale, double shape, std::string time_unit = {},
                     Random* randomstream = nullptr)
        : scale_(scale), shape_(shape) {
        if (scale <= 0) throw SalabimError("scale<=0");
        if (shape <= 0) throw SalabimError("shape<=0");
        time_unit_ = std::move(time_unit);
        set_stream(randomstream);
        mean_cache_ = scale_ * std::tgamma(1.0 + 1.0 / shape_);
    }
    double sample() override { return scale_ * stream_->weibullvariate(1.0, shape_) * tuf(); }
    double mean() const override { return mean_cache_ * tuf(); }

  private:
    double scale_, shape_, mean_cache_;
};

class Gamma : public Distribution_ {
  public:
    explicit Gamma(double shape, double scale, std::string time_unit = {},
                   Random* randomstream = nullptr)
        : shape_(shape), scale_(scale) {
        if (shape <= 0) throw SalabimError("shape<=0");
        if (scale <= 0) throw SalabimError("scale<=0");
        time_unit_ = std::move(time_unit);
        set_stream(randomstream);
    }
    double sample() override { return stream_->gammavariate(shape_, scale_) * tuf(); }
    double mean() const override { return shape_ * scale_ * tuf(); }

  private:
    double shape_, scale_;
};

class Erlang : public Distribution_ {
  public:
    explicit Erlang(long long shape, double rate, std::string time_unit = {},
                    Random* randomstream = nullptr)
        : shape_(shape), scale_(1.0 / rate) {
        if (shape <= 0) throw SalabimError("shape<=0");
        if (rate <= 0) throw SalabimError("rate<=0");
        time_unit_ = std::move(time_unit);
        set_stream(randomstream);
    }
    double sample() override { return stream_->gammavariate(static_cast<double>(shape_), scale_) * tuf(); }
    double mean() const override { return static_cast<double>(shape_) * scale_ * tuf(); }

  private:
    long long shape_;
    double scale_;
};

class Beta : public Distribution_ {
  public:
    explicit Beta(double alpha, double beta, Random* randomstream = nullptr)
        : alpha_(alpha), beta_(beta) {
        if (alpha <= 0) throw SalabimError("alpha<=0");
        if (beta <= 0) throw SalabimError("beta<=0");
        set_stream(randomstream);
    }
    double sample() override { return stream_->betavariate(alpha_, beta_); }
    double mean() const override { return alpha_ / (alpha_ + beta_); }

  private:
    double alpha_, beta_;
};

// Pdf: discrete distribution given by value/probability pairs.
// Pdf({x1, p1, x2, p2, ...}) or Pdf(values, probabilities)
class Pdf : public Distribution_ {
  public:
    Pdf(std::initializer_list<double> pairs, Random* randomstream = nullptr) {
        if (pairs.size() % 2 != 0) throw SalabimError("uneven number of parameters specified");
        std::vector<double> xs, ps;
        bool isx = true;
        for (double v : pairs) (isx ? xs : ps).push_back(v), isx = !isx;
        init(xs, ps);
        set_stream(randomstream);
    }
    Pdf(std::vector<double> xs, std::vector<double> ps, Random* randomstream = nullptr) {
        if (xs.size() != ps.size())
            throw SalabimError("length of x-values does not match length of probabilities");
        init(xs, ps);
        set_stream(randomstream);
    }
    // equal probabilities for all values
    explicit Pdf(std::vector<double> xs, Random* randomstream = nullptr) {
        init(xs, std::vector<double>(xs.size(), 1.0));
        set_stream(randomstream);
    }
    double sample() override {
        if (supports_n_) {
            // salabim uses random.sample(x, 1)[0] here: exactly one randbelow(n) draw
            return x_[stream_->sample_index(x_.size())];
        }
        double r = stream_->random();
        double prev_cum = 0.0;
        for (size_t i = 0; i < cum_.size(); ++i) {
            if (r <= cum_[i]) return x_[i];
            prev_cum = cum_[i];
        }
        (void)prev_cum;
        return x_.back();
    }
    double mean() const override { return mean_; }

  private:
    std::vector<double> x_, cum_;
    double mean_ = 0;
    bool supports_n_ = false;

    void init(const std::vector<double>& xs, const std::vector<double>& ps) {
        if (xs.empty()) throw SalabimError("no arguments specified");
        double sump = 0, sumxp = 0;
        supports_n_ = std::adjacent_find(ps.begin(), ps.end(), std::not_equal_to<>()) == ps.end();
        for (size_t i = 0; i < xs.size(); ++i) {
            x_.push_back(xs[i]);
            sump += ps[i];
            cum_.push_back(sump);
            sumxp += xs[i] * ps[i];
        }
        if (sump == 0) throw SalabimError("at least one probability should be >0");
        for (auto& c : cum_) c /= sump;
        mean_ = sumxp / sump;
    }
};

using Pmf = Pdf; // salabim 23+ alias

// Cdf: cumulative (piecewise linear) distribution: Cdf({x1, c1, x2, c2, ...})
class Cdf : public Distribution_ {
  public:
    Cdf(std::initializer_list<double> pairs, Random* randomstream = nullptr) {
        if (pairs.size() % 2 != 0) throw SalabimError("uneven number of parameters specified");
        bool isx = true;
        double lastcum = 0, lastx = -inf;
        for (double v : pairs) {
            if (isx) {
                if (v < lastx) throw SalabimError("x value less than previous");
                lastx = v;
                x_.push_back(v);
            } else {
                if (v < lastcum) throw SalabimError("cumulative value less than previous");
                lastcum = v;
                cum_.push_back(v);
            }
            isx = !isx;
        }
        if (cum_.empty() || cum_.back() == 0) throw SalabimError("last cumulative value should be > 0");
        for (auto& c : cum_) c /= lastcum;
        double sumxp = 0;
        for (size_t i = 1; i < x_.size(); ++i)
            sumxp += (x_[i - 1] + x_[i]) / 2.0 * (cum_[i] - cum_[i - 1]);
        mean_ = sumxp;
        set_stream(randomstream);
    }
    double sample() override {
        double r = stream_->random();
        size_t i = 0;
        for (; i < cum_.size(); ++i) {
            if (r < cum_[i]) {
                if (i == 0) return x_[0];
                return x_[i - 1] + (x_[i] - x_[i - 1]) * (r - cum_[i - 1]) / (cum_[i] - cum_[i - 1]);
            }
        }
        return x_.back();
    }
    double mean() const override { return mean_; }

  private:
    std::vector<double> x_, cum_;
    double mean_ = 0;
};

// duration/time specifications may be a number, a distribution (sampled) or a callable
class DurationSpec {
  public:
    DurationSpec() : kind_(Kind::none) {}
    DurationSpec(double v) : kind_(Kind::value), value_(v) {}
    DurationSpec(int v) : kind_(Kind::value), value_(v) {}
    DurationSpec(long v) : kind_(Kind::value), value_(static_cast<double>(v)) {}
    DurationSpec(long long v) : kind_(Kind::value), value_(static_cast<double>(v)) {}
    DurationSpec(unsigned v) : kind_(Kind::value), value_(v) {}
    DurationSpec(Distribution_& d) : kind_(Kind::dist), dist_(&d) {}
    // rvalue distributions are copied and owned, so `.iat = sim::Exponential(10)` is safe
    template <class D>
        requires std::is_base_of_v<Distribution_, std::remove_cvref_t<D>> &&
                 (!std::is_lvalue_reference_v<D>)
    DurationSpec(D&& d)
        : kind_(Kind::dist), owned_(std::make_shared<std::remove_cvref_t<D>>(std::move(d))) {
        dist_ = owned_.get();
    }
    template <class F>
        requires std::invocable<F> && (!std::is_convertible_v<F, double>) &&
                 (!std::is_base_of_v<Distribution_, std::remove_cvref_t<F>>)
    DurationSpec(F&& f) : kind_(Kind::func), func_(std::forward<F>(f)) {}

    bool has_value() const { return kind_ != Kind::none; }
    double resolve() const {
        switch (kind_) {
            case Kind::value: return value_;
            case Kind::dist: return dist_->sample();
            case Kind::func: return func_();
            default: throw SalabimError("no value specified");
        }
    }

  private:
    enum class Kind { none, value, dist, func } kind_;
    double value_ = 0;
    Distribution_* dist_ = nullptr;
    std::shared_ptr<Distribution_> owned_{};
    std::function<double()> func_{};
};

// ---------------------------------------------------------------------------
// Monitor — collects statistics, either
//   * non-level (tally):    values (with optional weights), e.g. length of stay
//   * level (time-weighted): a value that persists over time, e.g. queue length
// Matches salabim's Monitor number-crunching and print formats exactly.
// ---------------------------------------------------------------------------
struct MonitorOpts {
    bool level = false;
    double initial_tally = 0.0;
    bool monitor = true;
    std::string weight_legend{};
    Environment* env = nullptr;
};

struct HistogramOpts {
    std::optional<int> number_of_bins{};
    std::optional<double> lowerbound{};
    std::optional<double> bin_width{};
    bool values = false;
    bool ex0 = false;
    bool as_str = false;
    double graph_scale = 80;
};

class Monitor {
  public:
    using Opts = MonitorOpts;
    using HistOpts = HistogramOpts;

    explicit Monitor(std::string name = "", MonitorOpts opts = {});
    virtual ~Monitor() = default;
    Monitor(const Monitor&) = delete;
    Monitor& operator=(const Monitor&) = delete;

    const std::string& name() const { return name_; }
    void rename(std::string name) { name_ = std::move(name); }
    bool is_level() const { return level_; }
    Environment* env() const { return env_; }

    // record a value; for level monitors this is "the value from now on"
    void tally(double value, double weight = 1.0);
    double get() const { return tally_; }        // current value (level monitors)
    double operator()() const { return tally_; }
    double t() const { return ttally_; }

    void monitor(std::optional<bool> value = std::nullopt);
    bool is_monitoring() const { return monitor_; }
    void reset(std::optional<bool> monitor_on = std::nullopt);
    double start_time() const { return start_; }

    // statistics (ex0: exclude zero values)
    double mean(bool ex0 = false) const;
    double std(bool ex0 = false) const;
    double minimum(bool ex0 = false) const;
    double maximum(bool ex0 = false) const;
    double median(bool ex0 = false) const { return percentile(50, ex0); }
    double percentile(double q, bool ex0 = false) const;
    long long number_of_entries(bool ex0 = false) const;
    long long number_of_entries_zero() const;
    double weight(bool ex0 = false) const;       // total weight (non-level)
    double duration(bool ex0 = false) const;     // total duration (level)
    double weight_zero() const;
    double duration_zero() const;

    // per-value / per-bin queries
    double value_number_of_entries(double v) const;
    double value_weight(double v) const;
    double value_duration(double v) const { return value_weight(v); }
    long long bin_number_of_entries(double lowerbound, double upperbound, bool ex0 = false) const;
    double bin_weight(double lowerbound, double upperbound) const;
    double bin_duration(double lowerbound, double upperbound) const { return bin_weight(lowerbound, upperbound); }
    std::vector<double> values(bool ex0 = false) const; // sorted unique values

    // x / t access (mirrors salabim's xt() / tx() spirit)
    const std::vector<double>& x_raw() const { return x_; }
    const std::vector<double>& t_raw() const { return t_; }
    std::pair<std::vector<double>, std::vector<double>> xweight(bool ex0 = false) const;
    std::vector<double> xduration_weights(bool ex0 = false) const { return xweight(ex0).second; }

    // output
    std::string print_statistics(bool show_header = true, bool show_legend = true,
                                 bool do_indent = false, bool as_str = false) const;
    std::string print_histogram(HistogramOpts opts = {}) const;
    std::tuple<double, double, int> histogram_autoscale(bool ex0 = false) const;

    // labels: used by status/mode/string-state monitors to display coded values
    void set_label_provider(std::function<std::string(double)> f) { label_of_ = std::move(f); }
    std::string label_for(double v) const {
        return label_of_ ? label_of_(v) : detail::py_str(v);
    }
    const std::string& weight_legend() const { return weight_legend_; }

    // internal (used by the library itself)
    void tally_internal_(double value, double weight = 1.0) { tally(value, weight); }
    double tally_raw_() const { return tally_; }
    void set_tally_raw_(double v) { tally_ = v; }

  protected:
    friend class Environment;
    friend class Queue;
    friend class Resource;
    friend class Component;

    std::string name_;
    Environment* env_ = nullptr;
    bool level_ = false;
    bool monitor_ = true;
    double tally_ = 0.0;      // current value (level)
    double ttally_ = 0.0;     // time of last tally
    double start_ = 0.0;      // reset time
    std::vector<double> x_;   // recorded values
    std::vector<double> t_;   // recording times
    std::vector<double> weight_; // non-level: lazily created weights (all-1 until a weight != 1 arrives)
    bool has_weights_ = false;
    std::string weight_legend_;
    std::function<std::string(double)> label_of_{};

    static constexpr double off_ = -inf;  // marker for "monitoring disabled" periods
    double now_() const;                  // environment time (defined after Environment)
};

// ---------------------------------------------------------------------------
// Process — the coroutine type returned by Component::process().
// `co_await hold(...)` in C++ corresponds to `yield self.hold(...)` in salabim.
// ---------------------------------------------------------------------------
class Process {
  public:
    struct promise_type;
    using Handle = std::coroutine_handle<promise_type>;

    // Sub-process support (salabim's yieldless world in coroutine form): a process
    // may `co_await call(helper())` where helper() is itself a Process coroutine.
    // The helper runs immediately (like a plain call); whenever it suspends on a
    // process interaction the whole logical stack suspends; the scheduler resumes
    // the innermost frame; on completion control returns to the awaiting frame.
    struct FinalAwaiter {
        bool await_ready() const noexcept { return false; }
        std::coroutine_handle<> await_suspend(Handle h) noexcept; // defined after Component
        void await_resume() const noexcept {}
    };

    struct promise_type {
        Component* component = nullptr;
        Handle continuation{};       // parent frame when running as a sub-process
        std::exception_ptr exception{};
        ~promise_type() {
            // an abandoned sub-frame (cancel/terminate mid-call) drags its
            // suspended parents down with it, so nothing leaks
            if (continuation) continuation.destroy();
        }
        Process get_return_object() {
            return Process{std::coroutine_handle<promise_type>::from_promise(*this)};
        }
        std::suspend_always initial_suspend() noexcept { return {}; }
        FinalAwaiter final_suspend() noexcept { return {}; }
        void return_void() noexcept {}
        void unhandled_exception() noexcept { exception = std::current_exception(); }
    };

    Process() = default;
    explicit Process(Handle h) : handle(h) {}
    Handle handle{};
};

// awaitable returned by all process interaction methods: hands control back to
// the scheduler (the scheduling itself already happened inside the method call,
// exactly like salabim's generator mode)
struct Yield {
    constexpr bool await_ready() const noexcept { return false; }
    constexpr void await_suspend(std::coroutine_handle<>) const noexcept {}
    constexpr void await_resume() const noexcept {}
};

// awaitable returned by from_store(): resumes with the retrieved item
struct YieldItem {
    Component* self_;
    constexpr bool await_ready() const noexcept { return false; }
    constexpr void await_suspend(std::coroutine_handle<>) const noexcept {}
    Component* await_resume() const noexcept; // returns the item (defined later)
};

// ---------------------------------------------------------------------------
// option structs for the process interaction methods (salabim keyword args).
// The source_location members default to the call site, giving salabim-style
// line numbers in the trace.
// ---------------------------------------------------------------------------
struct HoldOpts {
    DurationSpec till{};
    double priority = 0;
    bool urgent = false;
    std::optional<std::string> mode{};
    std::optional<bool> cap_now{};
    std::source_location loc = std::source_location::current();
};

struct ActivateOpts {
    DurationSpec at{};
    DurationSpec delay{};
    double priority = 0;
    bool urgent = false;
    bool keep_request = false;
    bool keep_wait = false;
    std::optional<std::string> mode{};
    std::optional<bool> cap_now{};
    std::source_location loc = std::source_location::current();
};

struct ModeOpts { // passivate / cancel / standby / interrupt
    std::optional<std::string> mode{};
    std::source_location loc = std::source_location::current();
};

struct ResumeOpts {
    bool all = false;
    std::optional<std::string> mode{};
    double priority = 0;
    bool urgent = false;
    std::source_location loc = std::source_location::current();
};

struct RequestOpts {
    DurationSpec fail_at{};
    DurationSpec fail_delay{};
    std::optional<std::string> mode{};
    bool urgent = false;
    double request_priority = 0; // priority in the requesters queue
    double priority = 0;         // schedule priority of the fail event
    bool oneof = false;
    std::optional<bool> cap_now{};
    std::source_location loc = std::source_location::current();
};

struct WaitOpts {
    DurationSpec fail_at{};
    DurationSpec fail_delay{};
    bool all = false;
    std::optional<std::string> mode{};
    bool urgent = false;
    std::optional<double> request_priority{};
    double priority = 0;
    std::optional<bool> cap_now{};
    std::source_location loc = std::source_location::current();
};

struct StoreOpts { // from_store / to_store
    DurationSpec fail_at{};
    DurationSpec fail_delay{};
    std::optional<std::string> mode{};
    bool urgent = true; // salabim: from_store/to_store default urgent=True
    double request_priority = 0;
    double priority = 0;    // to_store: priority of item in store; also fail event priority
    std::optional<bool> cap_now{};
    std::function<bool(Component*)> filter{}; // from_store only
    std::source_location loc = std::source_location::current();
};

// request specifier: a resource, optionally with quantity and requesters-queue priority
struct ReqSpec {
    Resource* r;
    double q = 1;
    std::optional<double> priority{};
    ReqSpec(Resource& res) : r(&res) {}
    ReqSpec(Resource& res, double quantity) : r(&res), q(quantity) {}
    ReqSpec(Resource& res, double quantity, double prio) : r(&res), q(quantity), priority(prio) {}
};

// wait specifier: a state with a value to compare or a predicate to satisfy
class WaitSpec {
  public:
    StateBase* state;
    std::function<bool()> test;
    std::optional<double> priority{};

    template <class T>
    WaitSpec(State<T>& s); // "truthy" test (salabim: wait for value True)

    template <class T, class V>
    WaitSpec(State<T>& s, V&& v); // equality (or predicate if callable)

    template <class T, class V>
    WaitSpec(State<T>& s, V&& v, double prio);
};

// ---------------------------------------------------------------------------
// Queue — priority-ordered doubly linked list with full statistics
// ---------------------------------------------------------------------------
class Qmember {
  public:
    Qmember* predecessor = nullptr;
    Qmember* successor = nullptr;
    double priority = 0;
    Component* component = nullptr;
    Queue* queue = nullptr;
    double enter_time = 0;
};

struct QueueOpts {
    double capacity = inf;
    bool monitor = true;
    Environment* env = nullptr;
};

class Queue {
  public:
    using Opts = QueueOpts;

    explicit Queue(std::string name = "", QueueOpts opts = {});
    virtual ~Queue();
    Queue(const Queue&) = delete;
    Queue& operator=(const Queue&) = delete;

    const std::string& name() const { return name_; }
    const std::string& base_name() const { return base_name_; }
    long long sequence_number() const { return sequence_number_; }
    Environment* env() const { return env_; }

    long long size() const { return length_; }
    bool empty() const { return length_ == 0; }
    bool contains(const Component* c) const;

    Component* head() const { return head_.successor->component; }
    Component* tail() const { return tail_.predecessor->component; }
    Component* pop();                    // remove and return head (nullptr if empty)
    Component* operator[](long long index) const; // python-style: negative = from tail
    long long index(const Component* c) const;    // -1 if not in queue

    // add components (equivalent to Component::enter*)
    Queue& add(Component& c);
    Queue& append(Component& c) { return add(c); }
    Queue& add_sorted(Component& c, double priority);
    Queue& add_at_head(Component& c);
    Queue& add_in_front_of(Component& c, Component& poscomponent);
    Queue& add_behind(Component& c, Component& poscomponent);
    Queue& remove(Component& c);
    void clear();

    Component* successor(const Component* c) const;
    Component* predecessor(const Component* c) const;

    void set_capacity(double cap);

    // iteration over components (safe against removing the current element)
    class iterator {
      public:
        iterator(const Qmember* m, const Qmember* tail) : tail_(tail) { set(m); }
        Component* operator*() const { return cur_; }
        iterator& operator++() { set(next_); return *this; }
        bool operator!=(const iterator& o) const { return cur_ != o.cur_; }
        bool operator==(const iterator& o) const { return cur_ == o.cur_; }
      private:
        void set(const Qmember* m) {
            if (m == tail_ || m == nullptr) { cur_ = nullptr; next_ = nullptr; }
            else { cur_ = m->component; next_ = m->successor; }
        }
        Component* cur_ = nullptr;
        const Qmember* next_ = nullptr;
        const Qmember* tail_;
    };
    iterator begin() const { return iterator(head_.successor, &tail_); }
    iterator end() const { return iterator(&tail_, &tail_); }
    std::vector<Component*> components() const; // snapshot

    // statistics
    Monitor length;              // level
    Monitor length_of_stay;      // non-level
    Monitor capacity;            // level
    Monitor available_quantity;  // level
    long long number_of_arrivals = 0;
    long long number_of_departures = 0;
    double arrival_rate(bool reset = false);
    double departure_rate(bool reset = false);
    void reset_monitors(std::optional<bool> monitor_on = std::nullopt);
    std::string print_statistics(bool as_str = false) const;
    std::string print_histograms(bool as_str = false) const;
    std::string print_info(bool as_str = false) const;

  protected:
    friend class Component;
    friend class Environment;
    friend class Resource;
    friend class Store;
    friend class StateBase;

    Queue(std::string name, Opts opts, int registry, const char* fallback);

    // insert c in a new Qmember in front of member m2 (salabim Qmember.insert_in_front_of)
    Qmember* insert_in_front_of_(Qmember* m2, Component* c, double priority);
    void register_leave_(Qmember* mx);

    std::string name_, base_name_;
    long long sequence_number_ = 0;
    Environment* env_ = nullptr;
    Qmember head_, tail_;
    long long length_ = 0;
    bool isinternal_ = false;
    bool isclaimers_ = false;
    double rate_reset_arrivals_t_ = 0, rate_reset_departures_t_ = 0;
    long long rate_arrivals_base_ = 0, rate_departures_base_ = 0;
};

// ---------------------------------------------------------------------------
// Component — the active object of the simulation.
// Subclass it and give it a process():
//
//     struct Car : sim::Component {
//         sim::Process process() override {
//             while (true) co_await hold(1.0);
//         }
//     };
//     sim::make<Car>();
// ---------------------------------------------------------------------------
struct ComponentOptions {
    std::string name{};
    DurationSpec at{};
    DurationSpec delay{};
    std::optional<double> priority{};
    std::optional<bool> urgent{};
    bool data_component = false; // like salabim's process="": never activate process()
    std::string process_name{}; // shown in the activate trace (default "process")
    std::string mode{};
    bool suppress_trace = false;
    bool skip_standby = false;
    std::optional<bool> cap_now{};
    Environment* env = nullptr;
    std::source_location loc = std::source_location::current();
};

namespace detail {
struct PendingComponent {
    bool active = false;
    Environment* env = nullptr;
    std::string name, base_name;
    long long sequence_number = 0;
    std::string mode;
    bool suppress_trace = false;
    bool skip_standby = false;
};
inline thread_local PendingComponent pending_component{};
} // namespace detail

class Component {
  public:
    Component();
    virtual ~Component();
    Component(const Component&) = delete;
    Component& operator=(const Component&) = delete;

    // ---- to be overridden --------------------------------------------------
    virtual Process process() { return Process{}; } // no process -> data component
    virtual void setup() {}                          // called right after creation

    // ---- identity -----------------------------------------------------------
    const std::string& name() const { return name_; }
    const std::string& base_name() const { return base_name_; }
    long long sequence_number() const { return sequence_number_; }

    Environment* env = nullptr;

    // ---- status -------------------------------------------------------------
    Status status() const { return status_; }
    Monitor& status_monitor() { return *status_mon_; }
    bool isdata() const { return status_ == Status::data; }
    bool iscurrent() const { return status_ == Status::current; }
    bool isscheduled() const { return status_ == Status::scheduled; }
    bool ispassive() const { return status_ == Status::passive; }
    bool isstandby() const { return status_ == Status::standby; }
    bool isinterrupted() const { return status_ == Status::interrupted; }
    bool isrequesting() const { return status_ == Status::requesting; }
    bool iswaiting() const { return status_ == Status::waiting; }
    bool ismain() const;

    const std::string& mode() const { return mode_; }
    void set_mode(const std::optional<std::string>& m);
    double mode_time() const { return mode_time_; }

    double creation_time() const { return creation_time_; }
    double scheduled_time() const { return scheduled_time_; }
    double scheduled_priority() const { return scheduled_priority_; }
    bool failed() const { return failed_; }
    int interrupt_level() const { return interrupt_level_; }
    Status interrupted_status() const { return interrupted_status_; }
    double remaining_duration() const { return remaining_duration_; }

    bool suppress_trace(std::optional<bool> value = std::nullopt) {
        if (value) suppress_trace_ = *value;
        return suppress_trace_;
    }
    bool skip_standby(std::optional<bool> value = std::nullopt) {
        if (value) skip_standby_ = *value;
        return skip_standby_;
    }

    // ---- sub-processes -------------------------------------------------------
    // co_await call(some_helper()) runs a Process coroutine as a nested part of
    // this component's process (salabim's yieldless helper-method pattern). The
    // helper may hold/request/wait/... on this component; results travel through
    // out-parameters or member state.
    struct CallAwaiter {
        Component* self;
        Process sub;
        bool await_ready() const noexcept { return !sub.handle || sub.handle.done(); }
        std::coroutine_handle<> await_suspend(std::coroutine_handle<> parent) noexcept {
            auto ph = Process::Handle::from_address(parent.address());
            sub.handle.promise().continuation = ph;
            sub.handle.promise().component = self;
            self->process_ = sub.handle; // the scheduler resumes the innermost frame
            return sub.handle;           // run the helper right now, like a plain call
        }
        void await_resume() {
            if (!sub.handle) return;
            std::exception_ptr ex = sub.handle.promise().exception;
            sub.handle.promise().continuation = {}; // break the cascade link before destroy
            sub.handle.destroy();
            if (ex) std::rethrow_exception(ex);
        }
    };
    CallAwaiter call(Process sub) { return CallAwaiter{this, sub}; }

    // ---- process interaction (use with co_await on the current component) ---
    Yield hold(DurationSpec duration, HoldOpts opts = {});
    Yield hold(HoldOpts opts); // till-form or "hold()": scheduled now
    Yield passivate(ModeOpts opts = {});
    Yield activate(ActivateOpts opts = {});
    Yield cancel(ModeOpts opts = {});
    Yield standby(ModeOpts opts = {});

    Yield request(std::initializer_list<ReqSpec> specs, RequestOpts opts = {});
    Yield request(std::vector<ReqSpec> specs, RequestOpts opts = {}) {
        return request_impl_(std::move(specs), opts);  // dynamic spec lists
    }
    Yield request(Resource& r, RequestOpts opts = {});
    Yield request(Resource& r, double q, RequestOpts opts = {});
    void release(); // release all claims
    void release(std::initializer_list<ReqSpec> specs);
    void release(Resource& r);
    void release(Resource& r, double q);

    Yield wait(std::initializer_list<WaitSpec> specs, WaitOpts opts = {});
    Yield wait(std::vector<WaitSpec> specs, WaitOpts opts = {}) {
        return wait_impl_(std::move(specs), opts);  // dynamic spec lists
    }
    template <class T>
    Yield wait(State<T>& s, WaitOpts opts = {}) { return wait({WaitSpec(s)}, std::move(opts)); }

    void interrupt(ModeOpts opts = {});
    void resume(ResumeOpts opts = {});

    YieldItem from_store(Store& store, StoreOpts opts = {});
    YieldItem from_store(std::initializer_list<Store*> stores, StoreOpts opts = {});
    YieldItem from_store(std::vector<Store*> stores, StoreOpts opts = {}) {
        return from_store_impl_(std::move(stores), opts);  // dynamic store lists
    }
    Yield to_store(Store& store, Component& item, StoreOpts opts = {});
    Yield to_store(std::initializer_list<Store*> stores, Component& item, StoreOpts opts = {});
    Component* from_store_item() const { return from_store_item_; }
    Store* from_store_store() const { return from_store_store_; }
    Store* to_store_store() const { return to_store_store_; }

    // ---- queue operations ----------------------------------------------------
    Component& enter(Queue& q);
    Component& enter_sorted(Queue& q, double priority);
    Component& enter_at_head(Queue& q);
    Component& enter_in_front_of(Queue& q, Component& poscomponent);
    Component& enter_behind(Queue& q, Component& poscomponent);
    Component& leave();          // leave all (non-internal) queues
    Component& leave(Queue& q);
    long long count(const Queue* q = nullptr) const; // membership count
    long long index(const Queue& q) const;
    double enter_time(const Queue& q) const;
    double priority(const Queue& q) const;
    void set_priority(Queue& q, double priority); // may reposition component
    std::vector<Queue*> queues() const;
    Component* successor(const Queue& q) const;
    Component* predecessor(const Queue& q) const;

    // ---- resource queries ------------------------------------------------------
    double claimed_quantity(const Resource* r = nullptr) const;
    double requested_quantity(const Resource* r = nullptr) const;
    std::vector<Resource*> claimed_resources() const;
    std::vector<Resource*> requested_resources() const;
    bool isclaiming(const Resource* r = nullptr) const;
    bool isbumped(const Resource* r = nullptr) const { return !isclaiming(r); }

    std::string print_info(bool as_str = false) const;

  protected:
    friend class Environment;
    friend class Queue;
    friend class Resource;
    friend class Store;
    friend class StateBase;
    template <class T> friend class State;

  public:
    // internal machinery (names follow the salabim source; not part of the public API)
    void finish_make_(const ComponentOptions& opts);
    std::string modetxt_() const;

  protected:
    // internal machinery (names follow the salabim source)
    void push_(double t, double priority, bool urgent);
    void remove_();
    void check_fail_();
    void reschedule_(double scheduled_time, double priority, bool urgent, const std::string& caller,
                     std::optional<bool> cap_now, const std::string& extra = "",
                     std::optional<std::string> s0 = std::nullopt);
    bool tryrequest_();  // Component._tryrequest
    bool trywait_();     // Component._trywait
    void release_(Resource* r, std::optional<double> q = std::nullopt,
                  std::optional<std::string> s0 = std::nullopt, Component* bumped_by = nullptr);
    std::vector<Resource*> honor_all_();
    std::vector<Resource*> honor_any_();
    Qmember* member_(const Queue& q) const;
    Qmember* checkinqueue_(const Queue& q) const;
    void checknotinqueue_(const Queue& q) const;
    void checkisnotdata_() const;
    void checkisnotmain_() const;
    std::string lineno_txt_(bool add_at = false) const;
    void set_line_(const std::source_location& loc) { last_line_ = loc.line(); last_file_ = loc.file_name(); }
    void set_status_(Status s);
    void hold_impl_(DurationSpec* duration, HoldOpts& opts);
    Yield request_impl_(std::vector<ReqSpec> specs, RequestOpts& opts);
    Yield wait_impl_(std::vector<WaitSpec> specs, WaitOpts& opts);
    YieldItem from_store_impl_(std::vector<Store*> stores, StoreOpts& opts);
    Yield to_store_impl_(std::vector<Store*> stores, Component& item, StoreOpts& opts);
    double fail_time_(DurationSpec& fail_at, DurationSpec& fail_delay);

    std::string name_, base_name_;
    long long sequence_number_ = 0;
    Status status_ = Status::data;
    std::unique_ptr<Monitor> status_mon_;
    friend struct Process::FinalAwaiter;
    Process::Handle process_{};
    bool process_abandoned_ = false;

    std::vector<std::pair<Queue*, Qmember*>> qmembers_; // insertion ordered
    std::vector<std::pair<Resource*, double>> requests_; // insertion ordered
    std::vector<std::pair<Resource*, double>> claims_;   // insertion ordered
    // anonymous-resource re-scan deferred until this component resumes: Python's
    // _push switches greenlets when self is current, so the re-scan at the end
    // of Component._tryrequest runs at resumption, not at request-call time
    std::vector<Resource*> deferred_anon_rescan_;
    std::vector<WaitSpec> waits_;
    bool wait_all_ = false;
    bool oneof_request_ = false;

    std::vector<Store*> from_stores_{};
    std::vector<Store*> to_stores_{};
    Component* from_store_item_ = nullptr;
    Store* from_store_store_ = nullptr;
    Component* to_store_item_ = nullptr;
    Store* to_store_store_ = nullptr;
    double to_store_priority_ = 0;
    std::function<bool(Component*)> from_store_filter_{};

    bool on_event_list_ = false;
    long long event_gen_ = 0;
    double scheduled_time_ = inf;
    double scheduled_priority_ = 0;
    double remaining_duration_ = 0;
    Status interrupted_status_ = Status::scheduled;
    int interrupt_level_ = 0;
    bool failed_ = false;
    double creation_time_ = 0;
    std::string mode_;
    double mode_time_ = 0;
    bool suppress_trace_ = false;
    bool skip_standby_ = false;
    std::uint_least32_t last_line_ = 0;   // last co_await site, for trace line numbers
    const char* last_file_ = nullptr;
    std::uint_least32_t creation_line_ = 0;
    const char* creation_file_ = nullptr;
};

// ---------------------------------------------------------------------------
// StateBase / State<T> — components can wait() for state values / conditions
// ---------------------------------------------------------------------------
struct StateOpts {
    bool monitor = true;
    Environment* env = nullptr;
};

class StateBase {
  public:
    virtual ~StateBase();
    StateBase(const StateBase&) = delete;
    StateBase& operator=(const StateBase&) = delete;

    const std::string& name() const { return name_; }
    const std::string& base_name() const { return base_name_; }
    long long sequence_number() const { return sequence_number_; }
    Environment* env() const { return env_; }
    Queue& waiters() { return *waiters_; }
    Monitor& value_monitor() { return *value_mon_; }
    std::string print_statistics(bool as_str = false) const;
    std::string print_histograms(bool as_str = false) const;
    std::string print_info(bool as_str = false) const;
    void reset_monitors(std::optional<bool> monitor_on = std::nullopt);

  protected:
    friend class Component;
    template <class T> friend class ::sim::State;

    StateBase() = default;
    void init_base_(std::string name, Environment* env);
    void trywait_(double max_honor = inf); // State._trywait: check all waiters
    virtual std::string value_str_() const = 0;

    std::string name_, base_name_;
    long long sequence_number_ = 0;
    Environment* env_ = nullptr;
    std::unique_ptr<Queue> waiters_;
    std::unique_ptr<Monitor> value_mon_;
};

// ---------------------------------------------------------------------------
// Resource — request/release with capacities; supports anonymous resources
// ---------------------------------------------------------------------------
struct ResourceOpts {
    double initial_claimed_quantity = 0;
    bool anonymous = false;
    bool preemptive = false;
    bool honor_only_first = false;
    bool honor_only_highest_priority = false;
    bool monitor = true;
    Environment* env = nullptr;
};

class Resource {
  public:
    using Opts = ResourceOpts;

    explicit Resource(std::string name = "", double capacity = 1, ResourceOpts opts = {});
    virtual ~Resource();
    Resource(const Resource&) = delete;
    Resource& operator=(const Resource&) = delete;

    const std::string& name() const { return name_; }
    const std::string& base_name() const { return base_name_; }
    long long sequence_number() const { return sequence_number_; }
    Environment* env() const { return env_; }

    Queue& requesters() { return *requesters_; }
    Queue& claimers() { return *claimers_; }
    bool isanonymous() const { return anonymous_; }
    bool ispreemptive() const { return preemptive_; }

    void set_capacity(double cap);
    void release(std::optional<double> quantity = std::nullopt);

    // level monitors; calling e.g. claimed_quantity() gives the current value
    Monitor capacity;
    Monitor claimed_quantity;
    Monitor available_quantity;
    Monitor occupancy;

    void reset_monitors(std::optional<bool> monitor_on = std::nullopt);
    std::string print_statistics(bool as_str = false) const;
    std::string print_histograms(bool as_str = false) const;
    std::string print_info(bool as_str = false) const;

  protected:
    friend class Component;
    friend class Environment;

    void tryrequest_(); // Resource._tryrequest
    void update_monitors_();

    std::string name_, base_name_;
    long long sequence_number_ = 0;
    Environment* env_ = nullptr;
    std::unique_ptr<Queue> requesters_;
    std::unique_ptr<Queue> claimers_;
    double capacity_ = 1;
    double claimed_quantity_ = 0;
    bool anonymous_ = false;
    bool preemptive_ = false;
    bool honor_only_first_ = false;
    bool honor_only_highest_priority_ = false;
    double minq_ = inf;
    bool trying_ = false;
};

// ---------------------------------------------------------------------------
// Store — a Queue of items that components can put to / get from
// ---------------------------------------------------------------------------
class Store : public Queue {
  public:
    explicit Store(std::string name = "", QueueOpts opts = {});
    ~Store() override;

    Queue& from_store_requesters() { return *from_requesters_; }
    Queue& to_store_requesters() { return *to_requesters_; }
    void set_capacity_store(double cap); // like salabim Store.set_capacity
    void rescan();

  protected:
    friend class Component;
    friend class Queue;
    void item_entered_(Component* item); // honor pending from_store requests
    void item_left_();                   // honor pending to_store requests
    std::unique_ptr<Queue> from_requesters_;
    std::unique_ptr<Queue> to_requesters_;
    int honor_depth_ = 0; // guards against salabim's store honor recursion
};

// ---------------------------------------------------------------------------
// Environment — the simulation environment and scheduler
// ---------------------------------------------------------------------------
inline constexpr std::int64_t seed_no_reseed = std::numeric_limits<std::int64_t>::min();
inline constexpr std::int64_t seed_random = std::numeric_limits<std::int64_t>::min() + 1;

struct EnvOptions {
    bool trace = false;
    std::int64_t random_seed = 1234567; // sim::seed_no_reseed / sim::seed_random sentinels
    std::string time_unit = "n/a";
    std::string name{};
    bool print_trace_header = true;
    bool isdefault_env = true;
    std::source_location loc = std::source_location::current();
};

struct RunOpts {
    DurationSpec till{};
    double priority = inf;
    bool urgent = false;
    std::optional<bool> cap_now{};
    std::source_location loc = std::source_location::current();
};

class Environment {
  public:
    explicit Environment(EnvOptions opts = {});
    virtual ~Environment();
    Environment(const Environment&) = delete;
    Environment& operator=(const Environment&) = delete;

    const std::string& name() const { return name_; }

    // --- time ---------------------------------------------------------------
    double now() const { return now_ - offset_; }
    double t() const { return now_; }
    double peek();
    void reset_now(double new_now = 0);

    Component* main() { return main_; }
    Component* current_component() { return current_; }

    // --- running ------------------------------------------------------------
    void run() { run_impl_(DurationSpec{}, RunOpts{}); }
    void run(DurationSpec duration, RunOpts opts = {}) { run_impl_(std::move(duration), std::move(opts)); }
    void run(RunOpts opts) { run_impl_(DurationSpec{}, std::move(opts)); }
    void step();

    // --- tracing ------------------------------------------------------------
    bool trace() const { return trace_; }
    void trace(bool value) {
        if (value && !trace_ && !header_printed_ && print_trace_header_) {
            trace_ = true;
            print_trace_header();
        }
        trace_ = value;
    }
    bool suppress_trace_linenumbers(std::optional<bool> value = std::nullopt) {
        if (value) suppress_trace_linenumbers_ = *value;
        return suppress_trace_linenumbers_;
    }
    bool suppress_trace_standby(std::optional<bool> value = std::nullopt) {
        if (value) suppress_trace_standby_ = *value;
        return suppress_trace_standby_;
    }
    void trace_to(std::ostream* os) { trace_out_ = os; }

    class TraceSuppressor {
      public:
        explicit TraceSuppressor(Environment* env) : env_(env), saved_(env->trace_) { env_->trace_ = false; }
        ~TraceSuppressor() { env_->trace_ = saved_; }
      private:
        Environment* env_;
        bool saved_;
    };
    TraceSuppressor suppress_trace() { return TraceSuppressor(this); }

    virtual std::string time_to_str(double t) const { return detail::sprintf_str("%10.3f", t); }
    virtual std::string duration_to_str(double d) const { return detail::sprintf_str("%.3f", d); }

    void print_trace(const std::string& s1, const std::string& s2, const std::string& s3,
                     const std::string& s4 = "", std::optional<std::string> s0 = std::nullopt,
                     bool optional_line = false);
    void print_trace_header();

    // --- randomness ---------------------------------------------------------
    void random_seed(std::int64_t seed = 1234567) {
        if (seed == seed_no_reseed) return;
        if (seed == seed_random) {
            std::random_device rd;
            g::random.seed((static_cast<std::uint64_t>(rd()) << 32) | rd());
        } else {
            g::random.seed(static_cast<std::uint64_t>(seed < 0 ? -seed : seed));
        }
    }

    // --- time units -----------------------------------------------------------
    const std::string& time_unit_name() const { return time_unit_name_; }
    double time_unit_factor_env() const { return time_unit_; } // lookup value of env unit
    double years(double t) const { return t * unit_factor("years"); }
    double weeks(double t) const { return t * unit_factor("weeks"); }
    double days(double t) const { return t * unit_factor("days"); }
    double hours(double t) const { return t * unit_factor("hours"); }
    double minutes(double t) const { return t * unit_factor("minutes"); }
    double seconds(double t) const { return t * unit_factor("seconds"); }
    double milliseconds(double t) const { return t * unit_factor("milliseconds"); }
    double microseconds(double t) const { return t * unit_factor("microseconds"); }
    double to_years(double t) const { return t / unit_factor("years"); }
    double to_weeks(double t) const { return t / unit_factor("weeks"); }
    double to_days(double t) const { return t / unit_factor("days"); }
    double to_hours(double t) const { return t / unit_factor("hours"); }
    double to_minutes(double t) const { return t / unit_factor("minutes"); }
    double to_seconds(double t) const { return t / unit_factor("seconds"); }
    double to_milliseconds(double t) const { return t / unit_factor("milliseconds"); }
    double to_microseconds(double t) const { return t / unit_factor("microseconds"); }
    double to_time_unit(const std::string& unit, double t) const { return t / unit_factor(unit); }

    double spec_to_duration(const DurationSpec& s) const { return s.resolve(); }
    double spec_to_time(const DurationSpec& s) const { return s.resolve(); }

    std::string print_info(bool as_str = false) const;

    // ------------------------------------------------------------------------
    // internals (public-ish for the library machinery; underscore suffix)
    // ------------------------------------------------------------------------
    enum class Registry { component, queue, resource, state, monitor, store };
    std::string set_name_(Registry reg, std::string name, const std::string& fallback_classname,
                          std::string* base_name, long long* sequence_number);
    void register_component_(Component* c) { components_.emplace_back(c); }
    std::string filename_lineno_to_str_(const char* filename, unsigned line);
    std::string frame_to_lineno_(const std::source_location& loc) {
        return filename_lineno_to_str_(loc.file_name(), loc.line());
    }
    void terminate_(Component* c);
    void resume_process_(Component* c);
    double now_raw_() const { return now_; }
    double offset_raw_() const { return offset_; }
    bool is_shutting_down_() const { return shutting_down_; }

    std::string last_s0_{};

  protected:
    friend class Component;
    friend class Queue;
    friend class Resource;
    friend class StateBase;
    friend class Monitor;
    friend class Store;

    struct EvtEntry {
        double t;
        double priority;
        long long seq;
        Component* c;
        long long gen;
        bool operator>(const EvtEntry& o) const {
            if (t != o.t) return t > o.t;
            if (priority != o.priority) return priority > o.priority;
            return seq > o.seq;
        }
    };

  public:
    double unit_factor(std::string_view unit) const {
        if (time_unit_ == 0.0) throw SalabimError("time_unit is not available");
        return time_unit_ / detail::time_unit_lookup(unit);
    }

  protected:
    void run_impl_(DurationSpec duration, RunOpts opts);
    void push_event_(double t, double priority, long long seq, Component* c, long long gen) {
        event_list_.push(EvtEntry{t, priority, seq, c, gen});
    }
    bool pop_valid_event_(EvtEntry* out);
    void print_legend_(int ref);

    std::string name_;
    double time_unit_ = 0.0; // lookup value; 0 = "n/a"
    std::string time_unit_name_ = "n/a";
    bool trace_ = false;
    bool header_printed_ = false;
    bool print_trace_header_ = true;
    bool suppress_trace_linenumbers_ = false;
    bool suppress_trace_standby_ = true;
    std::ostream* trace_out_ = &std::cout;
    std::optional<std::string> buffered_trace_{};

    double now_ = 0, offset_ = 0;
    long long seq_ = 0;
    std::priority_queue<EvtEntry, std::vector<EvtEntry>, std::greater<EvtEntry>> event_list_;
    std::vector<Component*> standbylist_, pendingstandbylist_;
    Component* main_ = nullptr;
    Component* current_ = nullptr;
    bool running_ = false;
    bool end_on_empty_eventlist_ = false;
    bool shutting_down_ = false;

    std::map<std::string, long long> registries_[6];
    inline static std::map<std::string, long long> env_registry_{};
    std::vector<std::unique_ptr<Component>> components_;
    std::vector<std::pair<std::string, int>> source_files_; // (filename, ref)
};

inline Environment* default_env() { return g::default_env; }

namespace detail {
inline Environment* need_env(Environment* env) {
    Environment* e = env ? env : g::default_env;
    if (!e)
        throw SalabimError(
            "no default environment. Create an Environment first (sim::Environment env;)");
    return e;
}
} // namespace detail

// ---------------------------------------------------------------------------
// State<T> — a state with a value; components can wait for values/conditions
// ---------------------------------------------------------------------------
template <class T>
class State : public StateBase {
  public:
    using Opts = StateOpts;

    explicit State(std::string name = "", T value = T{}, StateOpts opts = {})
        : value_(std::move(value)) {
        env_ = detail::need_env(opts.env);
        init_base_(std::move(name), env_);
        value_mon_ = std::make_unique<Monitor>(
            "Value of " + name_,
            MonitorOpts{.level = true, .initial_tally = proj_(value_), .monitor = opts.monitor, .env = env_});
        if constexpr (!std::is_arithmetic_v<T>)
            value_mon_->set_label_provider([this](double code) { return label_lookup_(code); });
        env_->print_trace("", "", name_ + " create", "value = " + detail::py_repr(value_));
    }

    const T& get() const { return value_; }
    const T& operator()() const { return value_; }

    void set(const T& value) { set_impl_(value, "set"); }
    void set() requires std::is_arithmetic_v<T> { set(static_cast<T>(1)); }
    void reset(const T& value) { set_impl_(value, "reset"); }
    void reset() requires std::is_arithmetic_v<T> { reset(static_cast<T>(0)); }

    void trigger(const T& value, std::optional<T> value_after = std::nullopt,
                 double max_honor = inf) {
        T after = value_after.value_or(value_);
        env_->print_trace("", "", name_ + " trigger",
                          " value = " + detail::py_str(value) + " --> " + detail::py_str(after) +
                              " allow " + (max_honor == inf ? "inf" : std::to_string(static_cast<long long>(max_honor))) +
                              " components");
        value_ = value;
        value_mon_->tally(proj_(value_));
        trywait_(max_honor);
        value_ = after;
        value_mon_->tally(proj_(value_));
        trywait_();
    }
    void trigger() requires std::is_arithmetic_v<T> { trigger(static_cast<T>(1)); }

  protected:
    std::string value_str_() const override { return detail::py_str(value_); }

  private:
    T value_;
    std::vector<std::string> labels_; // for non-arithmetic values: code registry

    void set_impl_(const T& value, const char* action) {
        env_->print_trace("", "", name_ + " " + action, "value = " + detail::py_repr(value));
        if (!(value_ == value)) {
            value_ = value;
            value_mon_->tally(proj_(value_));
            trywait_();
        }
    }

    double proj_(const T& v) {
        if constexpr (std::is_arithmetic_v<T>) {
            return static_cast<double>(v);
        } else {
            std::string s = detail::py_str(v);
            for (size_t i = 0; i < labels_.size(); ++i)
                if (labels_[i] == s) return static_cast<double>(i);
            labels_.push_back(s);
            return static_cast<double>(labels_.size() - 1);
        }
    }
    std::string label_lookup_(double code) const {
        auto i = static_cast<size_t>(code);
        return i < labels_.size() ? labels_[i] : "?";
    }
};

// WaitSpec constructors (declared with class Component)
template <class T>
WaitSpec::WaitSpec(State<T>& s) : state(&s) {
    if constexpr (std::is_arithmetic_v<T>)
        test = [st = &s] { return st->get() == static_cast<T>(1); }; // python: True == value
    else
        test = [] { return false; };
}

template <class T, class V>
WaitSpec::WaitSpec(State<T>& s, V&& v) : state(&s) {
    if constexpr (std::is_invocable_v<std::remove_cvref_t<V>, T>) {
        test = [st = &s, f = std::forward<V>(v)] { return static_cast<bool>(f(st->get())); };
    } else {
        T target = static_cast<T>(std::forward<V>(v));
        test = [st = &s, target] { return st->get() == target; };
    }
}

template <class T, class V>
WaitSpec::WaitSpec(State<T>& s, V&& v, double prio) : WaitSpec(s, std::forward<V>(v)) {
    priority = prio;
}

// ---------------------------------------------------------------------------
// make<T> — creates (and usually activates) a component, salabim-style:
//
//     sim::make<Customer>();                          // like Customer() in salabim
//     sim::make<Customer>({.at = 10, .name = "cust.ute"});
//     sim::make<Car>({}, ctor_args...);               // args go to Car's constructor
//
// The environment owns the component; the raw pointer stays valid.
// ---------------------------------------------------------------------------
template <class T, class... Args>
T* make(ComponentOptions opts, Args&&... args) {
    static_assert(std::is_base_of_v<Component, T>, "make<T>: T must derive from sim::Component");
    Environment* env = detail::need_env(opts.env);
    std::string classname = detail::lowercase(detail::demangle(typeid(T).name())) + ".";
    auto& pend = detail::pending_component;
    pend.active = true;
    pend.env = env;
    pend.mode = opts.mode;
    pend.suppress_trace = opts.suppress_trace;
    pend.skip_standby = opts.skip_standby;
    pend.name = env->set_name_(Environment::Registry::component, opts.name, classname,
                               &pend.base_name, &pend.sequence_number);
    T* p;
    try {
        p = new T(std::forward<Args>(args)...);
    } catch (...) {
        detail::pending_component.active = false;
        throw;
    }
    detail::pending_component.active = false;
    p->finish_make_(opts);
    return p;
}

template <class T>
T* make(std::source_location loc = std::source_location::current()) {
    ComponentOptions opts;
    opts.loc = loc;
    return make<T>(std::move(opts));
}

// ---------------------------------------------------------------------------
// Event — a component that executes a callable once at its scheduled time
// (salabim's Event class, useful for reneging etc.)
// ---------------------------------------------------------------------------
class Event : public Component {
  public:
    explicit Event(std::function<void()> action, std::string action_string = "action")
        : action_(std::move(action)), action_string_(std::move(action_string)) {}

    Process process() override {
        env->print_trace("", "", name() + " " + action_string_);
        action_();
        co_return;
    }

    void set_action(std::function<void()> action) { action_ = std::move(action); }

  private:
    std::function<void()> action_;
    std::string action_string_;
};

// ---------------------------------------------------------------------------
// ComponentGenerator<T> — generates components with a given inter-arrival
// time (or spread over an interval), like salabim's ComponentGenerator.
//
//     sim::Exponential iat(10);
//     sim::ComponentGenerator<Customer>({.iat = iat, .till = 1000});
// ---------------------------------------------------------------------------
struct GeneratorOpts {
    std::string generator_name{};
    DurationSpec at{};
    DurationSpec delay{};
    DurationSpec till{};
    DurationSpec duration{};
    std::optional<long long> number{};
    DurationSpec iat{};
    bool force_at = false;
    bool force_till = false;
    bool equidistant = false;
    bool suppress_trace = false;
    std::function<void()> at_end{};
    std::function<Component*()> factory{}; // if not given: sim::make<T>()
    Environment* env = nullptr;
    std::source_location loc = std::source_location::current();
};

namespace detail {

template <class T>
class ComponentGeneratorImpl : public Component {
  public:
    explicit ComponentGeneratorImpl(GeneratorOpts o) : o_(std::move(o)) {}

    Process process() override {
        if (mode_spread_) {
            for (double interval : intervals_) {
                co_await hold(interval);
                create_();
            }
            env->print_trace("", "", "all components generated");
            if (o_.at_end) o_.at_end();
            co_return;
        }
        long long n = 0;
        while (true) {
            create_();
            ++n;
            if (o_.number && n >= *o_.number) {
                env->print_trace("", "", std::to_string(n) + " components generated");
                if (o_.at_end) o_.at_end();
                co_return;
            }
            double t = env->now_raw_() + o_.iat.resolve();
            if (t > till_) {
                co_await hold(HoldOpts{.till = till_});
                env->print_trace("", "", "till reached");
                if (o_.at_end) o_.at_end();
                co_return;
            }
            co_await hold(HoldOpts{.till = t});
        }
    }

    // configuration computed before activation (see sim::ComponentGenerator<T>())
    GeneratorOpts o_;
    double till_ = inf;
    bool mode_spread_ = false;
    std::vector<double> intervals_;

  private:
    void create_() {
        if (o_.factory)
            o_.factory();
        else
            sim::make<T>();
    }
};

} // namespace detail

template <class T>
Component* ComponentGenerator(GeneratorOpts opts = {}) {
    Environment* env = detail::need_env(opts.env);
    std::string gname = opts.generator_name;
    if (gname.empty())
        gname = detail::demangle(typeid(T).name()) + ".generator."; // salabim keeps the class case here

    double at;
    double delay = opts.delay.has_value() ? opts.delay.resolve() : 0.0;
    if (opts.at.has_value())
        at = opts.at.resolve() + env->offset_raw_() + delay;
    else
        at = env->now_raw_() + delay;

    double till;
    if (opts.till.has_value()) {
        if (opts.duration.has_value()) throw SalabimError("till and duration specified.");
        till = opts.till.resolve() + env->offset_raw_();
    } else if (opts.duration.has_value()) {
        till = at + opts.duration.resolve();
    } else {
        till = inf;
    }
    if (till < at) throw SalabimError("at > till");

    GeneratorOpts o = opts;
    ComponentOptions co;
    co.name = gname;
    co.suppress_trace = opts.suppress_trace;
    co.env = env;
    co.loc = opts.loc;
    co.process_name = (!opts.iat.has_value() && !opts.equidistant) ? "do_spread" : "do_iat";

    bool spread = false;
    std::vector<double> intervals;
    long long number = opts.number.value_or(std::numeric_limits<long long>::max());
    if (number < 1) throw SalabimError("number < 1 not supported");

    if (opts.equidistant) {
        double duration = till - at;
        if (duration == inf) throw SalabimError("infinite duration not allowed for equidistant");
        if (number == std::numeric_limits<long long>::max())
            throw SalabimError("number required for equidistant");
        if (number == 1) throw SalabimError("number=1 not allowed for equidistant");
        o.iat = DurationSpec(duration / static_cast<double>(number - 1));
        o.force_at = true;
        till = inf;
        o.number = number;
    } else if (!opts.iat.has_value()) {
        // spread mode: number components uniformly over [at, till]
        if (till == inf || number == std::numeric_limits<long long>::max())
            throw SalabimError("iat not specified --> till and number need to be specified");
        std::vector<double> moments;
        Uniform u(at, till);
        for (long long i = 0; i < number; ++i) moments.push_back(u.sample());
        std::sort(moments.begin(), moments.end());
        if (opts.force_at || opts.force_till) {
            double v_at = opts.force_at ? at : moments.front();
            double v_till = opts.force_till ? till : moments.back();
            double mn = moments.front(), mx = moments.back();
            for (auto& m : moments)
                m = (mx == mn) ? v_at : v_at + (m - mn) * (v_till - v_at) / (mx - mn);
        }
        double prev = 0;
        for (double m : moments) {
            intervals.push_back(m - prev);
            prev = m;
        }
        at = intervals.empty() ? at : intervals.front();
        if (!intervals.empty()) intervals.front() = 0;
        spread = true;
    } else {
        if (opts.force_till) throw SalabimError("force_till is not allowed for iat generators");
        if (!opts.force_at) at += opts.iat.resolve();
        if (at > till) at = till;
    }
    co.at = at;

    auto* gen = make<detail::ComponentGeneratorImpl<T>>(co, std::move(o));
    gen->till_ = till;
    gen->mode_spread_ = spread;
    gen->intervals_ = std::move(intervals);
    return gen;
}

// ===========================================================================
//                            IMPLEMENTATION
// ===========================================================================

namespace detail {
inline double time_unit_factor(std::string_view time_unit, const Environment* env) {
    if (!env) throw SalabimError("time unit requires an environment");
    return env->unit_factor(time_unit);
}
inline std::string basename(std::string_view path) {
    auto pos = path.find_last_of("/\\");
    return std::string(pos == std::string_view::npos ? path : path.substr(pos + 1));
}
inline std::string fmt_num(double v) { // python str() of an int-or-float quantity
    if (v == std::floor(v) && std::abs(v) < 9.2e18 && !std::isinf(v))
        return std::to_string(static_cast<long long>(v));
    return py_repr(v);
}
} // namespace detail

inline double Distribution_::tuf() const {
    if (time_unit_.empty()) return 1.0;
    if (!tuf_cache_) tuf_cache_ = detail::time_unit_factor(time_unit_, tu_env_ ? tu_env_ : g::default_env);
    return *tuf_cache_;
}

// --------------------------------- Monitor ---------------------------------

inline double Monitor::now_() const { return env_->now_raw_(); }

inline Monitor::Monitor(std::string name, Opts opts) {
    env_ = detail::need_env(opts.env);
    level_ = opts.level;
    monitor_ = opts.monitor;
    weight_legend_ = !opts.weight_legend.empty() ? opts.weight_legend : (level_ ? "duration" : "weight");
    tally_ = opts.initial_tally;
    ttally_ = env_->now_raw_();
    std::string base;
    long long seqn = 0;
    name_ = env_->set_name_(Environment::Registry::monitor, std::move(name), "monitor.", &base, &seqn);
    reset(opts.monitor);
}

inline void Monitor::reset(std::optional<bool> monitor_on) {
    if (monitor_on) monitor_ = *monitor_on;
    start_ = env_->now_raw_();
    x_.clear();
    t_.clear();
    weight_.clear();
    has_weights_ = false;
    if (level_) {
        x_.push_back(monitor_ ? tally_ : off_);
        t_.push_back(env_->now_raw_());
    }
}

inline void Monitor::tally(double value, double weight) {
    double now = env_->now_raw_();
    if (level_) {
        if (weight != 1.0) throw SalabimError("level monitor supports only weight=1");
        tally_ = value;
        ttally_ = now;
        if (monitor_) {
            if (!t_.empty() && t_.back() == now)
                x_.back() = value;
            else {
                x_.push_back(value);
                t_.push_back(now);
            }
        }
    } else {
        if (monitor_ && weight != 0.0) {
            if (weight == 1.0) {
                if (has_weights_) weight_.push_back(1.0);
            } else {
                if (!has_weights_) {
                    weight_.assign(x_.size(), 1.0);
                    has_weights_ = true;
                }
                weight_.push_back(weight);
            }
            x_.push_back(value);
            t_.push_back(now);
        } else if (monitor_ && weight == 0.0) {
            // salabim ignores zero-weight tallies for stats_only; full monitors record them
            x_.push_back(value);
            t_.push_back(now);
            if (has_weights_) weight_.push_back(0.0);
        }
    }
}

inline void Monitor::monitor(std::optional<bool> value) {
    if (!value) return;
    if (*value) {
        if (!monitor_) {
            monitor_ = true;
            if (level_) tally(tally_);
        }
    } else {
        if (monitor_ && level_) {
            double now = env_->now_raw_();
            if (!t_.empty() && t_.back() == now)
                x_.back() = off_;
            else {
                x_.push_back(off_);
                t_.push_back(now);
            }
        }
        monitor_ = false;
    }
}

inline std::pair<std::vector<double>, std::vector<double>> Monitor::xweight(bool ex0) const {
    std::vector<double> xs, ws;
    if (level_) {
        double t_extra = env_->now_raw_();
        for (size_t i = 0; i < x_.size(); ++i) {
            double w = (i + 1 < t_.size() ? t_[i + 1] : t_extra) - t_[i];
            if (x_[i] == off_) continue;
            if (ex0 && x_[i] == 0.0) continue;
            xs.push_back(x_[i]);
            ws.push_back(w);
        }
    } else {
        for (size_t i = 0; i < x_.size(); ++i) {
            if (ex0 && x_[i] == 0.0) continue;
            xs.push_back(x_[i]);
            ws.push_back(has_weights_ ? weight_[i] : 1.0);
        }
    }
    return {std::move(xs), std::move(ws)};
}

inline double Monitor::mean(bool ex0) const {
    auto [x, w] = xweight(ex0);
    double sumw = 0, sumxw = 0;
    for (size_t i = 0; i < x.size(); ++i) sumw += w[i];
    for (size_t i = 0; i < x.size(); ++i) sumxw += x[i] * w[i];
    return sumw ? sumxw / sumw : nan_;
}

inline double Monitor::std(bool ex0) const {
    auto [x, w] = xweight(ex0);
    double sumw = 0;
    for (double v : w) sumw += v;
    if (!sumw) return nan_;
    double m = mean(ex0);
    double var = 0;
    for (size_t i = 0; i < x.size(); ++i) var += w[i] * ((x[i] - m) * (x[i] - m));
    return std::sqrt(var / sumw);
}

inline double Monitor::minimum(bool ex0) const {
    auto [x, w] = xweight(ex0);
    if (x.empty()) return nan_;
    return *std::min_element(x.begin(), x.end());
}

inline double Monitor::maximum(bool ex0) const {
    auto [x, w] = xweight(ex0);
    if (x.empty()) return nan_;
    return *std::max_element(x.begin(), x.end());
}

inline double Monitor::percentile(double q, bool ex0) const {
    q = std::max(0.0, std::min(q, 100.0));
    if (q == 0) return minimum(ex0);
    if (q == 100) return maximum(ex0);
    q /= 100.0;
    auto [x, weight] = xweight(ex0);
    if (x.empty()) return nan_;
    if (x.size() == 1) return x[0];
    double sum_weight = 0;
    for (double w : weight) sum_weight += w;
    if (!sum_weight) return nan_;

    std::vector<size_t> order(x.size());
    for (size_t i = 0; i < order.size(); ++i) order[i] = i;
    std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) { return x[a] < x[b]; });
    std::vector<double> xs, ws;
    for (size_t i : order) {
        xs.push_back(x[i]);
        ws.push_back(weight[i]);
    }
    size_t n = xs.size();

    if (level_ || has_weights_) { // weighted percentile (salabim: self._weight truthy)
        std::vector<double> cum;
        double c = 0;
        for (size_t k = 0; k < n; ++k) {
            c += ws[k];
            cum.push_back(c / sum_weight);
        }
        size_t k = 0;
        for (; k < n; ++k)
            if (cum[k] >= q) break;
        if (k >= n) k = n - 1;
        if (cum[k] != q) return xs[k];
        // exactly on a boundary: 'linear' -> midpoint of the two adjacent values
        if (k + 1 < n) return (xs[k] + xs[k + 1]) / 2.0;
        return xs[k];
    } else {
        std::vector<double> cum;
        for (size_t k = 0; k < n; ++k) cum.push_back(static_cast<double>(k) / static_cast<double>(n - 1));
        size_t k = 0;
        for (; k + 1 < n; ++k)
            if (cum[k + 1] > q) break;
        // linear interpolation between xs[k] and xs[k+1]
        double c0 = cum[k], c1 = cum[k + 1];
        return xs[k] + (xs[k + 1] - xs[k]) * ((q - c0) / (c1 - c0));
    }
}

inline long long Monitor::number_of_entries(bool ex0) const {
    return static_cast<long long>(xweight(ex0).first.size());
}

inline long long Monitor::number_of_entries_zero() const {
    return number_of_entries(false) - number_of_entries(true);
}

inline double Monitor::weight(bool ex0) const {
    auto [x, w] = xweight(ex0);
    double s = 0;
    for (double v : w) s += v;
    return s;
}

inline double Monitor::duration(bool ex0) const { return weight(ex0); }
inline double Monitor::weight_zero() const { return weight(false) - weight(true); }
inline double Monitor::duration_zero() const { return weight_zero(); }

inline double Monitor::value_number_of_entries(double v) const {
    auto [x, w] = xweight(false);
    long long n = 0;
    for (double xv : x)
        if (xv == v) ++n;
    return static_cast<double>(n);
}

inline double Monitor::value_weight(double v) const {
    auto [x, w] = xweight(false);
    double s = 0;
    for (size_t i = 0; i < x.size(); ++i)
        if (x[i] == v) s += w[i];
    return s;
}

inline long long Monitor::bin_number_of_entries(double lowerbound, double upperbound, bool ex0) const {
    auto [x, w] = xweight(ex0);
    long long n = 0;
    for (double xv : x)
        if (xv > lowerbound && xv <= upperbound) ++n;
    return n;
}

inline double Monitor::bin_weight(double lowerbound, double upperbound) const {
    auto [x, w] = xweight(false);
    double s = 0;
    for (size_t i = 0; i < x.size(); ++i)
        if (x[i] > lowerbound && x[i] <= upperbound) s += w[i];
    return s;
}

inline std::vector<double> Monitor::values(bool ex0) const {
    auto [x, w] = xweight(ex0);
    std::vector<double> uniq;
    for (double v : x)
        if (std::find(uniq.begin(), uniq.end(), v) == uniq.end()) uniq.push_back(v);
    std::sort(uniq.begin(), uniq.end());
    return uniq;
}

inline std::string Monitor::print_statistics(bool show_header, bool show_legend, bool do_indent,
                                             bool as_str) const {
    using detail::fn;
    using detail::pad;
    std::vector<std::string> result;
    long ll = do_indent ? 45 : 0;
    std::string indent = pad("", ll);

    if (show_header)
        result.push_back(indent + "Statistics of " + name_ + " at " +
                         fn(env_->now_raw_() - env_->offset_raw_(), 13, 3));
    if (show_legend) {
        result.push_back(indent + "                        all    excl.zero         zero");
        result.push_back(pad(std::string(ll > 0 ? static_cast<size_t>(ll - 1) : 0, '-') + " ", ll) +
                         "-------------- ------------ ------------ ------------");
    }
    bool weighted = level_ || has_weights_;
    if (weighted) {
        result.push_back(pad(name_, ll) + pad(weight_legend_, 14) + fn(weight(), 13, 3) +
                         fn(weight(true), 13, 3) + fn(weight_zero(), 13, 3));
    } else {
        result.push_back(pad(name_, ll) + pad("entries", 14) +
                         fn(static_cast<double>(number_of_entries()), 13, 3) +
                         fn(static_cast<double>(number_of_entries(true)), 13, 3) +
                         fn(static_cast<double>(number_of_entries_zero()), 13, 3));
    }
    result.push_back(indent + "mean          " + fn(mean(), 13, 3) + fn(mean(true), 13, 3));
    result.push_back(indent + "std.deviation " + fn(std(), 13, 3) + fn(std(true), 13, 3));
    result.push_back("");
    result.push_back(indent + "minimum       " + fn(minimum(), 13, 3) + fn(minimum(true), 13, 3));
    result.push_back(indent + "median        " + fn(percentile(50), 13, 3) + fn(percentile(50, true), 13, 3));
    result.push_back(indent + "90% percentile" + fn(percentile(90), 13, 3) + fn(percentile(90, true), 13, 3));
    result.push_back(indent + "95% percentile" + fn(percentile(95), 13, 3) + fn(percentile(95, true), 13, 3));
    result.push_back(indent + "maximum       " + fn(maximum(), 13, 3) + fn(maximum(true), 13, 3));

    std::string out;
    for (auto& l : result) out += l + "\n";
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

inline std::tuple<double, double, int> Monitor::histogram_autoscale(bool ex0) const {
    if (weight(ex0) == 0) return {1.0, 0.0, 0};
    double xmax = maximum(ex0), xmin = minimum(ex0);
    double bin_width = 1;
    double lowerbound = 0;
    int number_of_bins = 0;
    bool done = false;
    for (int i = 0; i < 10 && !done; ++i) {
        double exp10 = std::pow(10.0, i);
        for (double bw : {exp10, exp10 * 2, exp10 * 5}) {
            bin_width = bw;
            lowerbound = std::floor(xmin / bin_width) * bin_width;
            number_of_bins = static_cast<int>(std::ceil((xmax - lowerbound) / bin_width));
            if (number_of_bins <= 30) {
                done = true;
                break;
            }
        }
    }
    return {bin_width, lowerbound, number_of_bins};
}

inline std::string Monitor::print_histogram(HistOpts opts) const {
    using detail::fn;
    using detail::pad;
    using detail::rpad;
    std::vector<std::string> result;
    result.push_back("Histogram of " + name_ + (opts.ex0 ? "[ex0]" : ""));
    double graph_scale = opts.graph_scale;
    auto [x, w] = xweight(opts.ex0);
    double weight_total = 0;
    for (double v : w) weight_total += v;
    bool weighted = level_ || has_weights_;

    if (opts.values) {
        long long nentries = static_cast<long long>(x.size());
        if (weighted) result.push_back(pad(weight_legend_, 13) + fn(weight_total, 13, 3));
        if (!level_) result.push_back(pad("entries", 13) + fn(static_cast<double>(nentries), 13, 3));
        result.push_back("");
        if (level_)
            result.push_back("value                " + rpad(weight_legend_, 13) + "     %");
        else
            result.push_back("value               entries     %");

        for (double v : values(opts.ex0)) {
            double count = level_ ? value_duration(v) : value_number_of_entries(v);
            double perc = count / (weight_total ? weight_total : 1);
            int n = static_cast<int>(perc * graph_scale);
            std::string stars(static_cast<size_t>(n), '*');
            if (level_)
                result.push_back(pad(label_for(v), 20) + fn(count, 14, 3) + fn(perc * 100, 6, 1) + " " + stars);
            else
                result.push_back(pad(label_for(v), 20) + rpad(std::to_string(static_cast<long long>(count)), 7) +
                                 fn(perc * 100, 6, 1) + " " + stars);
        }
    } else {
        bool auto_scale = !opts.bin_width && !opts.lowerbound && !opts.number_of_bins;
        double bin_width = opts.bin_width.value_or(1.0);
        double lowerbound = opts.lowerbound.value_or(0.0);
        int number_of_bins = opts.number_of_bins.value_or(30);
        if (auto_scale) std::tie(bin_width, lowerbound, number_of_bins) = histogram_autoscale(opts.ex0);
        result.push_back(print_statistics(false, true, false, true));
        if (number_of_bins >= 0) {
            result.push_back("");
            if (weighted)
                result.push_back("           <= " + rpad(weight_legend_, 13) + "     %  cum%");
            else
                result.push_back("           <=       entries     %  cum%");
            double cumperc = 0;
            for (int i = -1; i <= number_of_bins; ++i) {
                double lb = (i == -1) ? -inf : lowerbound + i * bin_width;
                double ub = (i == number_of_bins) ? inf : lowerbound + (i + 1) * bin_width;
                double count = weighted ? bin_weight(lb, ub)
                                        : static_cast<double>(bin_number_of_entries(lb, ub, opts.ex0));
                double perc = count / (weight_total ? weight_total : 1);
                std::string s;
                if (weight_total != inf) {
                    cumperc += perc;
                    int gs = static_cast<int>(graph_scale);
                    int n = std::clamp(static_cast<int>(perc * graph_scale), 0, gs);
                    int ncum = static_cast<int>(cumperc * graph_scale) + 1;
                    s = std::string(static_cast<size_t>(n), '*') +
                        std::string(static_cast<size_t>(gs - n), ' ');
                    // python: s = s[:ncum-1] + "|" + s[ncum+1:]  (forgiving slices)
                    int cut1 = std::clamp(ncum - 1, 0, gs);
                    int cut2 = std::min(ncum + 1, gs);
                    s = s.substr(0, static_cast<size_t>(cut1)) + "|" +
                        (cut2 < gs ? s.substr(static_cast<size_t>(cut2)) : "");
                }
                result.push_back(fn(ub, 13, 3) + " " + fn(count, 13, 3) + fn(perc * 100, 6, 1) +
                                 fn(cumperc * 100, 6, 1) + " " + s);
            }
        }
    }
    result.push_back(""); // salabim ends every histogram with a blank line
    std::string out;
    for (auto& l : result) {
        // avoid double newline when embedding print_statistics output
        out += l;
        if (l.empty() || l.back() != '\n') out += "\n";
    }
    if (!opts.as_str) std::cout << out;
    return opts.as_str ? out : "";
}

// --------------------------------- Queue -----------------------------------

inline Queue::Queue(std::string name, Opts opts) : Queue(std::move(name), std::move(opts), 1, "queue.") {}

inline Queue::Queue(std::string name, Opts opts, int registry, const char* fallback)
    : length("", MonitorOpts{.level = true, .initial_tally = 0, .monitor = opts.monitor, .env = opts.env}),
      length_of_stay("", MonitorOpts{.monitor = opts.monitor, .env = opts.env}),
      capacity("", MonitorOpts{.level = true, .initial_tally = opts.capacity, .monitor = opts.monitor, .env = opts.env}),
      available_quantity("", MonitorOpts{.level = true, .initial_tally = opts.capacity, .monitor = opts.monitor, .env = opts.env}) {
    env_ = detail::need_env(opts.env);
    name_ = env_->set_name_(static_cast<Environment::Registry>(registry), std::move(name), fallback,
                            &base_name_, &sequence_number_);
    head_.successor = &tail_;
    head_.predecessor = nullptr;
    tail_.successor = nullptr;
    tail_.predecessor = &head_;
    head_.priority = 0;
    tail_.priority = 0;
    length_ = 0;
    length.rename("Length of " + name_);
    length_of_stay.rename("Length of stay in " + name_);
    capacity.rename("Capacity of " + name_);
    available_quantity.rename("Available quantity of " + name_);
    rate_reset_arrivals_t_ = rate_reset_departures_t_ = env_->now_raw_();
    env_->print_trace("", "", name_ + " create");
}

inline Queue::~Queue() {
    if (env_ && env_->is_shutting_down_()) return;
    // silently detach any remaining members
    Qmember* m = head_.successor;
    while (m && m != &tail_) {
        Qmember* nxt = m->successor;
        if (m->component) {
            auto& qm = m->component->qmembers_;
            qm.erase(std::remove_if(qm.begin(), qm.end(),
                                    [this](auto& p) { return p.first == this; }),
                     qm.end());
        }
        delete m;
        m = nxt;
    }
}

inline Qmember* Queue::insert_in_front_of_(Qmember* m2, Component* c, double priority) {
    double available = capacity.tally_raw_() - static_cast<double>(length_) - 1;
    if (available < 0)
        throw QueueFullError(name_ + " has reached capacity " + detail::fmt_num(capacity.tally_raw_()));
    available_quantity.tally(available);

    auto* m = new Qmember();
    Qmember* m1 = m2->predecessor;
    m1->successor = m;
    m2->predecessor = m;
    m->predecessor = m1;
    m->successor = m2;
    m->priority = priority;
    m->component = c;
    m->queue = this;
    m->enter_time = env_->now_raw_();
    ++length_;
    c->qmembers_.emplace_back(this, m);
    if (env_->trace() && !isinternal_) env_->print_trace("", "", c->name(), "enter " + name_);
    length.tally(static_cast<double>(length_));
    ++number_of_arrivals;
    if (auto* st = dynamic_cast<Store*>(this)) st->item_entered_(c);
    return m;
}

inline void Queue::register_leave_(Qmember* mx) {
    Component* c = mx->component;
    Qmember* m1 = mx->predecessor;
    Qmember* m2 = mx->successor;
    m1->successor = m2;
    m2->predecessor = m1;
    --length_;
    auto& qm = c->qmembers_;
    qm.erase(std::remove_if(qm.begin(), qm.end(), [this](auto& p) { return p.first == this; }),
             qm.end());
    if (env_->trace() && !isinternal_) env_->print_trace("", "", c->name(), "leave " + name_);
    length_of_stay.tally(env_->now_raw_() - mx->enter_time);
    length.tally(static_cast<double>(length_));
    available_quantity.tally(capacity.tally_raw_() - static_cast<double>(length_));
    ++number_of_departures;
    delete mx;
    if (auto* st = dynamic_cast<Store*>(this)) st->item_left_();
}

inline bool Queue::contains(const Component* c) const {
    for (auto& [q, m] : c->qmembers_)
        if (q == this) return true;
    return false;
}

inline Component* Queue::pop() {
    Component* c = head();
    if (c) c->leave(*this);
    return c;
}

inline Component* Queue::operator[](long long index) const {
    if (index >= 0) {
        Qmember* m = head_.successor;
        while (m != &tail_) {
            if (index == 0) return m->component;
            --index;
            m = m->successor;
        }
        return nullptr;
    }
    Qmember* m = tail_.predecessor;
    while (m != &head_) {
        if (index == -1) return m->component;
        ++index;
        m = m->predecessor;
    }
    return nullptr;
}

inline long long Queue::index(const Component* c) const {
    long long i = 0;
    for (Qmember* m = head_.successor; m != &tail_; m = m->successor, ++i)
        if (m->component == c) return i;
    return -1;
}

inline Queue& Queue::add(Component& c) {
    c.enter(*this);
    return *this;
}

inline Queue& Queue::add_sorted(Component& c, double priority) {
    c.enter_sorted(*this, priority);
    return *this;
}

inline Queue& Queue::add_at_head(Component& c) {
    c.enter_at_head(*this);
    return *this;
}

inline Queue& Queue::add_in_front_of(Component& c, Component& poscomponent) {
    c.enter_in_front_of(*this, poscomponent);
    return *this;
}

inline Queue& Queue::add_behind(Component& c, Component& poscomponent) {
    c.enter_behind(*this, poscomponent);
    return *this;
}

inline Queue& Queue::remove(Component& c) {
    c.leave(*this);
    return *this;
}

inline void Queue::clear() {
    while (Component* c = head()) c->leave(*this);
}

inline Component* Queue::successor(const Component* c) const {
    for (auto& [q, m] : c->qmembers_)
        if (q == this) return m->successor->component;
    throw SalabimError(c->name() + " not in queue " + name_);
}

inline Component* Queue::predecessor(const Component* c) const {
    for (auto& [q, m] : c->qmembers_)
        if (q == this) return m->predecessor->component;
    throw SalabimError(c->name() + " not in queue " + name_);
}

inline void Queue::set_capacity(double cap) {
    capacity.tally(cap);
    available_quantity.tally(cap - static_cast<double>(length_));
}

inline std::vector<Component*> Queue::components() const {
    std::vector<Component*> out;
    for (Qmember* m = head_.successor; m != &tail_; m = m->successor) out.push_back(m->component);
    return out;
}

inline double Queue::arrival_rate(bool reset) {
    if (reset) {
        rate_reset_arrivals_t_ = env_->now_raw_();
        rate_arrivals_base_ = number_of_arrivals;
        return nan_;
    }
    double dt = env_->now_raw_() - rate_reset_arrivals_t_;
    return dt ? static_cast<double>(number_of_arrivals - rate_arrivals_base_) / dt : nan_;
}

inline double Queue::departure_rate(bool reset) {
    if (reset) {
        rate_reset_departures_t_ = env_->now_raw_();
        rate_departures_base_ = number_of_departures;
        return nan_;
    }
    double dt = env_->now_raw_() - rate_reset_departures_t_;
    return dt ? static_cast<double>(number_of_departures - rate_departures_base_) / dt : nan_;
}

inline void Queue::reset_monitors(std::optional<bool> monitor_on) {
    length.reset(monitor_on);
    length_of_stay.reset(monitor_on);
    capacity.reset(monitor_on);
    available_quantity.reset(monitor_on);
}

inline std::string Queue::print_statistics(bool as_str) const {
    std::vector<std::string> result;
    result.push_back("Statistics of " + name_ + " at " +
                     detail::fn(env_->now_raw_() - env_->offset_raw_(), 13, 3));
    result.push_back(length.print_statistics(false, true, true, true));
    result.push_back("");
    result.push_back(length_of_stay.print_statistics(false, false, true, true));
    std::string out;
    for (auto& l : result) {
        out += l;
        if (l.empty() || l.back() != '\n') out += "\n";
    }
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

inline std::string Queue::print_histograms(bool as_str) const {
    std::string out = length.print_histogram({.as_str = true});
    out += length_of_stay.print_histogram({.as_str = true});
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

inline std::string Queue::print_info(bool as_str) const {
    std::vector<std::string> result;
    result.push_back("Queue " + detail::sprintf_str("%p", static_cast<const void*>(this)));
    result.push_back("  name=" + name_);
    if (length_ == 0)
        result.push_back("  no components");
    else {
        result.push_back("  component(s):");
        for (Qmember* m = head_.successor; m != &tail_; m = m->successor)
            result.push_back("    " + detail::pad(m->component->name(), 20) + " enter_time" +
                             env_->time_to_str(m->enter_time - env_->offset_raw_()) +
                             " priority=" + detail::fmt_num(m->priority));
    }
    std::string out;
    for (auto& l : result) out += l + "\n";
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

// ------------------------------- Component ---------------------------------

inline Component::Component() {
    auto& pend = detail::pending_component;
    if (!pend.active)
        throw SalabimError("Components must be created with sim::make<T>(...), not constructed directly");
    pend.active = false;
    env = pend.env;
    name_ = pend.name;
    base_name_ = pend.base_name;
    sequence_number_ = pend.sequence_number;
    mode_ = pend.mode;
    suppress_trace_ = pend.suppress_trace;
    skip_standby_ = pend.skip_standby;
    creation_time_ = env->now_raw_();
    mode_time_ = creation_time_;
    status_mon_ = std::make_unique<Monitor>(
        name_ + ".status", MonitorOpts{.level = true, .initial_tally = static_cast<double>(Status::data), .env = env});
    status_mon_->set_label_provider(
        [](double code) { return status_to_str(static_cast<Status>(static_cast<int>(code))); });
}

inline Component::~Component() {
    if (process_) {
        process_.destroy();
        process_ = {};
    }
    if (env && !env->is_shutting_down_()) {
        // silently leave any queues we are still in
        auto qs = qmembers_;
        for (auto& [q, m] : qs) {
            Qmember* m1 = m->predecessor;
            Qmember* m2 = m->successor;
            m1->successor = m2;
            m2->predecessor = m1;
            --q->length_;
            delete m;
        }
        qmembers_.clear();
    }
}

inline bool Component::ismain() const { return env && env->main() == this; }

inline std::string Component::modetxt_() const { return mode_.empty() ? "" : "mode=" + mode_; }

inline void Component::set_mode(const std::optional<std::string>& m) {
    if (m) {
        mode_time_ = env->now_raw_();
        mode_ = *m;
    }
}

inline void Component::finish_make_(const ComponentOptions& opts) {
    env->register_component_(this);
    creation_line_ = opts.loc.line();
    creation_file_ = opts.loc.file_name();
    last_line_ = opts.loc.line();
    last_file_ = opts.loc.file_name();
    Process pr = opts.data_component ? Process{} : process();
    if (pr.handle) {
        pr.handle.promise().component = this;
        process_ = pr.handle;
        env->print_trace("", "", name_ + " create", modetxt_(), env->frame_to_lineno_(opts.loc));
        double delay = opts.delay.has_value() ? opts.delay.resolve() : 0.0;
        double scheduled_time = opts.at.has_value() ? opts.at.resolve() + env->offset_raw_() + delay
                                                    : env->now_raw_() + delay;
        set_status_(Status::scheduled);
        reschedule_(scheduled_time, opts.priority.value_or(0.0), opts.urgent.value_or(false),
                    "activate", opts.cap_now,
                    "process=" + (opts.process_name.empty() ? "process" : opts.process_name),
                    env->frame_to_lineno_(opts.loc));
    } else {
        if (opts.at.has_value()) throw SalabimError("at is not allowed for a data component");
        if (opts.delay.has_value()) throw SalabimError("delay is not allowed for a data component");
        if (opts.urgent) throw SalabimError("urgent is not allowed for a data component");
        if (opts.priority) throw SalabimError("priority is not allowed for a data component");
        env->print_trace("", "",
                         name_ + (name_ == "main" ? " create" : " create data component"),
                         modetxt_(), env->frame_to_lineno_(opts.loc));
    }
    setup();
}

inline void Component::set_status_(Status s) {
    status_ = s;
    status_mon_->tally(static_cast<double>(s));
}

inline void Component::push_(double t, double priority, bool urgent) {
    scheduled_priority_ = priority;
    if (t != inf) {
        ++env->seq_;
        long long seq = urgent ? -env->seq_ : env->seq_;
        on_event_list_ = true;
        ++event_gen_;
        env->push_event_(t, priority, seq, this, event_gen_);
    }
}

inline void Component::remove_() {
    if (on_event_list_) {
        on_event_list_ = false;
        ++event_gen_;
        return;
    }
    if (status_ == Status::standby) {
        auto& sl = env->standbylist_;
        sl.erase(std::remove(sl.begin(), sl.end(), this), sl.end());
        auto& pl = env->pendingstandbylist_;
        pl.erase(std::remove(pl.begin(), pl.end(), this), pl.end());
    }
}

inline void Component::check_fail_() {
    if (!requests_.empty()) {
        if (env->trace()) env->print_trace("", "", name_, "request failed");
        for (auto& [r, q] : std::vector<std::pair<Resource*, double>>(requests_)) {
            leave(*r->requesters_);
            if (r->requesters_->length_ == 0) r->minq_ = inf;
        }
        requests_.clear();
        failed_ = true;
    }
    if (!waits_.empty()) {
        if (env->trace()) env->print_trace("", "", name_, "wait failed");
        for (auto& ws : waits_)
            if (member_(*ws.state->waiters_)) leave(*ws.state->waiters_);
        waits_.clear();
        failed_ = true;
    }
    if (!from_stores_.empty()) {
        if (env->trace()) env->print_trace("", "", name_, "from_store failed");
        for (Store* st : std::vector<Store*>(from_stores_)) leave(*st->from_requesters_);
        from_stores_.clear();
        failed_ = true;
    }
    if (!to_stores_.empty()) {
        if (env->trace()) env->print_trace("", "", name_, "to_store failed");
        for (Store* st : std::vector<Store*>(to_stores_)) leave(*st->to_requesters_);
        to_stores_.clear();
        failed_ = true;
    }
}

inline std::string Component::lineno_txt_(bool add_at) const {
    if (env->suppress_trace_linenumbers_) return "";
    if (isdata() && this != env->main_) return "";
    if (!last_file_) return "";
    std::string s0 = env->filename_lineno_to_str_(last_file_, last_line_);
    if (s0.empty()) return "";
    s0 += "+";
    return (add_at ? "@" : "") + s0;
}

inline void Component::reschedule_(double scheduled_time, double priority, bool urgent,
                                   const std::string& caller, std::optional<bool> cap_now,
                                   const std::string& extra, std::optional<std::string> s0) {
    if (scheduled_time < env->now_raw_()) {
        bool cap = cap_now.value_or(g::default_cap_now);
        if (cap)
            scheduled_time = env->now_raw_();
        else
            throw SalabimError("scheduled time (" + detail::sprintf_str("%0.3f", scheduled_time) +
                               ") before now (" + detail::sprintf_str("%0.3f", env->now_raw_()) + ")");
    }
    scheduled_time_ = scheduled_time;
    if (env->trace()) {
        std::string scheduled_time_str, extra2 = extra;
        if (extra == "*") {
            scheduled_time_str = "ends on no events left  ";
            extra2 = " ";
        } else {
            scheduled_time_str =
                "scheduled for " + detail::strip(env->time_to_str(scheduled_time - env->offset_raw_()));
        }
        std::string delta;
        if (scheduled_time != env->now_raw_() && scheduled_time != inf)
            delta = " +" + env->duration_to_str(scheduled_time - env->now_raw_());
        std::string lineno = lineno_txt_(true);
        env->print_trace("", "", name_ + " " + caller + delta,
                         detail::merge_blanks(scheduled_time_str + (urgent ? "!" : " ") + lineno,
                                              modetxt_(), extra2),
                         s0);
    }
    push_(scheduled_time, priority, urgent);
}

inline Yield Component::hold(DurationSpec duration, HoldOpts opts) {
    hold_impl_(&duration, opts);
    return {};
}

inline Yield Component::hold(HoldOpts opts) {
    hold_impl_(nullptr, opts);
    return {};
}

inline void Component::hold_impl_(DurationSpec* duration, HoldOpts& opts) {
    if (this == env->current_) set_line_(opts.loc);
    if (status_ != Status::passive && status_ != Status::current) {
        checkisnotdata_();
        remove_();
        check_fail_();
    }
    set_mode(opts.mode);
    double scheduled_time;
    if (opts.till.has_value()) {
        if (duration && duration->has_value()) throw SalabimError("both duration and till specified");
        scheduled_time = opts.till.resolve() + env->offset_raw_();
    } else {
        scheduled_time = (duration && duration->has_value()) ? env->now_raw_() + duration->resolve()
                                                             : env->now_raw_();
    }
    set_status_(Status::scheduled);
    reschedule_(scheduled_time, opts.priority, opts.urgent, "hold", opts.cap_now, "",
                env->frame_to_lineno_(opts.loc));
}

inline Yield Component::passivate(ModeOpts opts) {
    if (this == env->current_) set_line_(opts.loc);
    if (status_ == Status::current) {
        remaining_duration_ = 0.0;
    } else {
        checkisnotdata_();
        remove_();
        check_fail_();
        remaining_duration_ = scheduled_time_ - env->now_raw_();
    }
    scheduled_time_ = inf;
    set_mode(opts.mode);
    if (env->trace())
        env->print_trace("", "", name_ + " passivate", detail::merge_blanks(lineno_txt_(true), modetxt_()),
                         env->frame_to_lineno_(opts.loc));
    set_status_(Status::passive);
    return {};
}

inline Yield Component::activate(ActivateOpts opts) {
    bool restart = false;
    std::string extra;
    if (status_ == Status::data) {
        // restart the process (like salabim using the "process" method by default)
        Process pr = process();
        if (!pr.handle) throw SalabimError("no process for data component " + name_);
        pr.handle.promise().component = this;
        if (process_) process_.destroy();
        process_ = pr.handle;
        restart = true;
        extra = "process=process";
        last_line_ = creation_line_;
        last_file_ = creation_file_;
    }
    if (status_ != Status::current) {
        remove_();
        if (!restart) {
            if (!(opts.keep_request || opts.keep_wait)) check_fail_();
        } else {
            check_fail_();
        }
    }
    set_mode(opts.mode);
    if (this == env->current_) set_line_(opts.loc);
    double delay = opts.delay.has_value() ? opts.delay.resolve() : 0.0;
    double scheduled_time = opts.at.has_value() ? opts.at.resolve() + env->offset_raw_() + delay
                                                : env->now_raw_() + delay;
    set_status_(Status::scheduled);
    reschedule_(scheduled_time, opts.priority, opts.urgent, "activate", opts.cap_now, extra,
                env->frame_to_lineno_(opts.loc));
    return {};
}

inline Yield Component::cancel(ModeOpts opts) {
    if (this == env->current_) set_line_(opts.loc);
    if (status_ == Status::data) {
        if (env->trace())
            env->print_trace("", "", "cancel (on data component) " + name_ + " " + modetxt_(), "",
                             env->frame_to_lineno_(opts.loc));
        return {};
    }
    if (status_ != Status::current) {
        checkisnotdata_();
        remove_();
        check_fail_();
    }
    for (auto& [r, q] : std::vector<std::pair<Resource*, double>>(claims_)) release_(r);
    deferred_anon_rescan_.clear(); // Python: killing the greenlet drops the pending re-scan
    if (this != env->current_ && process_) {
        process_.destroy();
        process_ = {};
    }
    scheduled_time_ = inf;
    set_mode(opts.mode);
    if (env->trace())
        env->print_trace("", "", "cancel " + name_ + " " + modetxt_(), "",
                         env->frame_to_lineno_(opts.loc));
    set_status_(Status::data);
    if (this == env->current_) {
        // Python: cancel on the current component switches to the scheduler and
        // never returns — skip the rest of the process by unwinding the frames.
        process_abandoned_ = true;
        throw detail::AbandonedByCancel{};
    }
    return {};
}

inline Yield Component::standby(ModeOpts opts) {
    if (this == env->current_) set_line_(opts.loc);
    if (status_ != Status::current) {
        checkisnotdata_();
        checkisnotmain_();
        remove_();
        check_fail_();
    }
    scheduled_time_ = env->now_raw_();
    set_mode(opts.mode);
    env->standbylist_.push_back(this);
    set_status_(Status::standby);
    if (env->trace()) {
        if (env->buffered_trace_)
            env->buffered_trace_.reset();
        else
            env->print_trace("", "", "standby", detail::merge_blanks(lineno_txt_(true), modetxt_()),
                             env->frame_to_lineno_(opts.loc));
    }
    return {};
}

inline void Component::interrupt(ModeOpts opts) {
    if (status_ != Status::scheduled && status_ != Status::interrupted)
        throw SalabimError(name_ + " component not scheduled");
    set_mode(opts.mode);
    std::string extra;
    if (status_ == Status::interrupted) {
        ++interrupt_level_;
        extra = "." + std::to_string(interrupt_level_);
    } else {
        checkisnotdata_();
        remove_();
        remaining_duration_ = scheduled_time_ - env->now_raw_();
        interrupted_status_ = status_;
        interrupt_level_ = 1;
        set_status_(Status::interrupted);
    }
    env->print_trace("", "", name_ + " interrupt" + extra,
                     detail::merge_blanks(lineno_txt_(true), modetxt_()),
                     env->frame_to_lineno_(opts.loc));
}

inline void Component::resume(ResumeOpts opts) {
    if (status_ != Status::interrupted) throw SalabimError(name_ + " not interrupted");
    set_mode(opts.mode);
    --interrupt_level_;
    if (interrupt_level_ && !opts.all) {
        env->print_trace("", "", name_ + " resume (interrupted." + std::to_string(interrupt_level_) + ")",
                         detail::merge_blanks(modetxt_()), env->frame_to_lineno_(opts.loc));
    } else {
        interrupt_level_ = 0;
        set_status_(interrupted_status_);
        env->print_trace("", "", name_ + " resume (" + status_to_str(status_) + ")",
                         detail::merge_blanks(lineno_txt_(true), modetxt_()),
                         env->frame_to_lineno_(opts.loc));
        if (status_ == Status::scheduled) {
            reschedule_(env->now_raw_() + remaining_duration_, opts.priority, opts.urgent, "hold",
                        false, "", env->frame_to_lineno_(opts.loc));
        } else {
            throw SalabimError(name_ + " unexpected interrupted_status");
        }
    }
}

inline double Component::fail_time_(DurationSpec& fail_at, DurationSpec& fail_delay) {
    if (fail_at.has_value()) {
        if (fail_delay.has_value()) throw SalabimError("both fail_at and fail_delay specified");
        return fail_at.resolve() + env->offset_raw_();
    }
    if (fail_delay.has_value()) {
        double d = fail_delay.resolve();
        return d == inf ? inf : env->now_raw_() + d;
    }
    return inf;
}

inline Yield Component::request(std::initializer_list<ReqSpec> specs, RequestOpts opts) {
    return request_impl_(std::vector<ReqSpec>(specs), opts);
}

inline Yield Component::request(Resource& r, RequestOpts opts) {
    return request_impl_({ReqSpec(r)}, opts);
}

inline Yield Component::request(Resource& r, double q, RequestOpts opts) {
    return request_impl_({ReqSpec(r, q)}, opts);
}

inline Yield Component::request_impl_(std::vector<ReqSpec> specs, RequestOpts& opts) {
    if (this == env->current_) set_line_(opts.loc);
    if (status_ != Status::current) {
        checkisnotdata_();
        checkisnotmain_();
        remove_();
        check_fail_();
    }
    oneof_request_ = opts.oneof;
    double scheduled_time = fail_time_(opts.fail_at, opts.fail_delay);
    double schedule_priority = opts.priority;
    set_mode(opts.mode);
    failed_ = false;

    if (specs.empty()) {
        set_status_(Status::scheduled);
        reschedule_(env->now_raw_(), 0, false, "request honor -", false, "", env->last_s0_);
        return {};
    }

    for (auto& sp : specs) {
        Resource* r = sp.r;
        double q = sp.q;
        double prio = sp.priority.value_or(opts.request_priority);
        if (r->preemptive_ && specs.size() > 1)
            throw SalabimError("preemptive resources do not support multiple resource requests");
        if (q < 0 && !r->anonymous_)
            throw SalabimError("quantity " + detail::fmt_num(q) + " <0");
        bool found = false;
        for (auto& [rr, qq] : requests_)
            if (rr == r) {
                qq += q;
                found = true;
                break;
            }
        if (!found) requests_.emplace_back(r, q);

        std::string addstring = " priority=" + detail::fmt_num(prio);
        if (oneof_request_) addstring += " (oneof)";

        enter_sorted(*r->requesters_, prio);
        if (env->trace())
            env->print_trace("", "", name_,
                             "request " + detail::fmt_num(q) + " from " + r->name() + addstring,
                             env->frame_to_lineno_(opts.loc));

        if (r->preemptive_) {
            double av = r->capacity_ - r->claimed_quantity_;
            std::vector<Component*> bump_candidates;
            for (Qmember* mx = r->claimers_->tail_.predecessor; mx != &r->claimers_->head_;
                 mx = mx->predecessor) {
                if (av >= q) break;
                if (prio >= mx->priority) break;
                av += mx->component->claimed_quantity(r);
                bump_candidates.push_back(mx->component);
            }
            if (av >= 0) {
                for (Component* c : bump_candidates) {
                    c->release_(r, std::nullopt, std::nullopt, this);
                    c->activate();
                }
            }
        }
    }
    for (auto& [r, q] : requests_)
        if (q < r->minq_) r->minq_ = q;

    remaining_duration_ = scheduled_time - env->now_raw_();
    tryrequest_();

    if (!requests_.empty()) {
        set_status_(Status::requesting);
        reschedule_(scheduled_time, schedule_priority, opts.urgent, "request", opts.cap_now, "",
                    env->frame_to_lineno_(opts.loc));
    }
    return {};
}

inline std::vector<Resource*> Component::honor_all_() {
    for (auto& [r, q] : requests_) {
        if (r->honor_only_first_ && r->requesters_->head() != this) return {};
        if (r->honor_only_highest_priority_) {
            double self_prio = priority(*r->requesters_);
            if (self_prio != r->requesters_->head_.successor->priority) return {};
        }
        if (q > 0) {
            if (q > r->capacity_ - r->claimed_quantity_ + 1e-8) return {};
        } else {
            if (-q > r->claimed_quantity_ + 1e-8) return {};
        }
    }
    std::vector<Resource*> out;
    for (auto& [r, q] : requests_) out.push_back(r);
    return out;
}

inline std::vector<Resource*> Component::honor_any_() {
    for (auto& [r, q] : requests_) {
        if (r->honor_only_first_ && r->requesters_->head() != this) continue;
        if (r->honor_only_highest_priority_) {
            double self_prio = priority(*r->requesters_);
            if (self_prio != r->requesters_->head_.successor->priority) continue;
        }
        if (q > 0) {
            if (q <= r->capacity_ - r->claimed_quantity_ + 1e-8) return {r};
        } else {
            if (-q <= r->claimed_quantity_ + 1e-8) return {r};
        }
    }
    return {};
}

inline bool Component::tryrequest_() {
    if (status_ == Status::interrupted) return false;
    std::vector<Resource*> r_honor = oneof_request_ ? honor_any_() : honor_all_();
    if (r_honor.empty()) return false;

    std::vector<Resource*> anonymous_resources;
    auto requests_copy = requests_;
    for (auto& [r, q] : requests_copy) {
        if (r->anonymous_) anonymous_resources.push_back(r);
        if (std::find(r_honor.begin(), r_honor.end(), r) != r_honor.end()) {
            r->claimed_quantity_ += q;
            double this_prio = priority(*r->requesters_);
            std::string prio_trace;
            if (!r->anonymous_) {
                bool found = false;
                for (auto& [rr, qq] : claims_)
                    if (rr == r) {
                        qq += q;
                        found = true;
                        break;
                    }
                if (!found) claims_.emplace_back(r, q);
                if (!member_(*r->claimers_)) enter_sorted(*r->claimers_, this_prio);
                prio_trace = " priority=" + detail::fmt_num(this_prio);
            }
            r->update_monitors_();
            if (env->trace())
                env->print_trace("", "", name_,
                                 "claim " + detail::fmt_num(q) + " from " + r->name() + " " + prio_trace);
        }
        leave(*r->requesters_);
        if (r->requesters_->length_ == 0) r->minq_ = inf;
    }
    requests_.clear();
    remove_();
    std::string honoredstr = r_honor[0]->name() + (r_honor.size() > 1 ? " ++" : "");
    set_status_(Status::scheduled);
    reschedule_(env->now_raw_(), 0, false, "request honor " + honoredstr, false, "", env->last_s0_);
    if (env->current_component() == this) {
        // Python (yieldless): _reschedule on the current component switches to
        // the scheduler right away, so this re-scan runs when we resume.
        deferred_anon_rescan_ = std::move(anonymous_resources);
    } else {
        for (Resource* r : anonymous_resources) r->tryrequest_();
    }
    return true;
}

inline void Component::release_(Resource* r, std::optional<double> q_opt,
                                std::optional<std::string> s0, Component* bumped_by) {
    auto it = std::find_if(claims_.begin(), claims_.end(), [r](auto& p) { return p.first == r; });
    if (it == claims_.end())
        throw SalabimError(name_ + " not claiming from resource " + r->name());
    double q = q_opt.value_or(it->second);
    if (q > it->second) q = it->second;
    r->claimed_quantity_ -= q;
    it->second -= q;
    if (it->second < 1e-8) {
        leave(*r->claimers_);
        if (r->claimers_->length_ == 0) r->claimed_quantity_ = 0; // avoid rounding problems
        claims_.erase(std::find_if(claims_.begin(), claims_.end(), [r](auto& p) { return p.first == r; }));
    }
    r->update_monitors_();
    if (env->trace()) {
        if (bumped_by)
            env->print_trace("", "", name_,
                             "bumped from " + r->name() + " by " + bumped_by->name() + " (release " +
                                 detail::fmt_num(q) + ")",
                             s0);
        else
            env->print_trace("", "", name_, "release " + detail::fmt_num(q) + " from " + r->name(), s0);
    }
    if (!bumped_by) r->tryrequest_();
}

inline void Component::release() {
    for (auto& [r, q] : std::vector<std::pair<Resource*, double>>(claims_)) release_(r);
}

inline void Component::release(std::initializer_list<ReqSpec> specs) {
    for (const auto& sp : specs) {
        if (sp.r->anonymous_)
            throw SalabimError("not possible to release anonymous resources " + sp.r->name());
        release_(sp.r, sp.q);
    }
}

inline void Component::release(Resource& r) {
    if (r.anonymous_) throw SalabimError("not possible to release anonymous resources " + r.name());
    release_(&r);
}

inline void Component::release(Resource& r, double q) {
    if (r.anonymous_) throw SalabimError("not possible to release anonymous resources " + r.name());
    release_(&r, q);
}

inline Yield Component::wait(std::initializer_list<WaitSpec> specs, WaitOpts opts) {
    return wait_impl_(std::vector<WaitSpec>(specs), opts);
}

inline Yield Component::wait_impl_(std::vector<WaitSpec> specs, WaitOpts& opts) {
    if (this == env->current_) set_line_(opts.loc);
    if (status_ != Status::current) {
        checkisnotdata_();
        checkisnotmain_();
        remove_();
        check_fail_();
    }
    wait_all_ = opts.all;
    failed_ = false;
    double scheduled_time = fail_time_(opts.fail_at, opts.fail_delay);
    double schedule_priority = opts.priority;
    set_mode(opts.mode);

    for (auto& ws : specs) {
        std::optional<double> prio = ws.priority;
        if (!prio && opts.request_priority) prio = opts.request_priority;
        bool already = false;
        for (auto& existing : waits_)
            if (existing.state == ws.state) {
                already = true;
                break;
            }
        if (!already) {
            if (prio)
                enter_sorted(*ws.state->waiters_, *prio);
            else
                enter(*ws.state->waiters_);
        }
        waits_.push_back(ws);
    }
    if (waits_.empty()) throw SalabimError("no states specified");

    remaining_duration_ = scheduled_time - env->now_raw_();
    trywait_();
    if (!waits_.empty()) {
        set_status_(Status::waiting);
        reschedule_(scheduled_time, schedule_priority, opts.urgent, "wait", opts.cap_now, "",
                    env->frame_to_lineno_(opts.loc));
    }
    return {};
}

inline bool Component::trywait_() {
    if (status_ == Status::interrupted) return false;
    bool honored;
    if (wait_all_) {
        honored = true;
        for (auto& ws : waits_)
            if (!ws.test()) {
                honored = false;
                break;
            }
    } else {
        honored = false;
        for (auto& ws : waits_)
            if (ws.test()) {
                honored = true;
                break;
            }
    }
    if (honored) {
        for (auto& ws : waits_)
            if (member_(*ws.state->waiters_)) leave(*ws.state->waiters_);
        waits_.clear();
        remove_();
        set_status_(Status::scheduled);
        reschedule_(env->now_raw_(), 0, false, "wait honor", false, "", env->last_s0_);
    }
    return honored;
}

// ---- stores ----------------------------------------------------------------

inline Component* YieldItem::await_resume() const noexcept { return self_->from_store_item(); }

inline YieldItem Component::from_store(Store& store, StoreOpts opts) {
    return from_store_impl_({&store}, opts);
}

inline YieldItem Component::from_store(std::initializer_list<Store*> stores, StoreOpts opts) {
    return from_store_impl_(std::vector<Store*>(stores), opts);
}

inline YieldItem Component::from_store_impl_(std::vector<Store*> stores, StoreOpts& opts) {
    if (stores.empty()) throw SalabimError("no stores specified");
    if (this == env->current_) set_line_(opts.loc);
    if (status_ != Status::current) {
        checkisnotdata_();
        checkisnotmain_();
        remove_();
        check_fail_();
    }
    double scheduled_time = fail_time_(opts.fail_at, opts.fail_delay);
    set_mode(opts.mode);
    failed_ = false;
    auto filter = opts.filter ? opts.filter : [](Component*) { return true; };
    if (env->trace()) {
        std::string names;
        for (auto* st : stores) names += (names.empty() ? "" : ", ") + st->name();
        env->print_trace("", "", name_, "from_store (" + names + ")", env->frame_to_lineno_(opts.loc));
    }
    Component* found = nullptr;
    Store* found_store = nullptr;
    for (Store* st : stores) {
        for (Component* c : *st)
            if (filter(c)) {
                found = c;
                found_store = st;
                break;
            }
        if (found) break;
    }
    if (found) {
        found->leave(*found_store);
        from_store_item_ = found;
        from_store_store_ = found_store;
        remove_();
        set_status_(Status::scheduled);
        reschedule_(env->now_raw_(), 0, false,
                    "from_store (" + found_store->name() + ") honor with " + found->name(), false, "",
                    env->last_s0_);
        return YieldItem{this};
    }
    from_stores_ = stores;
    for (Store* st : stores) enter_sorted(*st->from_requesters_, opts.request_priority);
    set_status_(Status::requesting);
    from_store_item_ = nullptr;
    from_store_filter_ = filter;
    reschedule_(scheduled_time, opts.priority, opts.urgent, "request from_store", opts.cap_now, "",
                env->frame_to_lineno_(opts.loc));
    return YieldItem{this};
}

inline Yield Component::to_store(Store& store, Component& item, StoreOpts opts) {
    return to_store_impl_({&store}, item, opts);
}

inline Yield Component::to_store(std::initializer_list<Store*> stores, Component& item, StoreOpts opts) {
    return to_store_impl_(std::vector<Store*>(stores), item, opts);
}

inline Yield Component::to_store_impl_(std::vector<Store*> stores, Component& item, StoreOpts& opts) {
    if (stores.empty()) throw SalabimError("no stores specified");
    if (this == env->current_) set_line_(opts.loc);
    if (status_ != Status::current) {
        checkisnotdata_();
        checkisnotmain_();
        remove_();
        check_fail_();
    }
    double scheduled_time = fail_time_(opts.fail_at, opts.fail_delay);
    set_mode(opts.mode);
    failed_ = false;
    if (env->trace()) {
        std::string names;
        for (auto* st : stores) names += (names.empty() ? "" : ", ") + st->name();
        env->print_trace("", "", name_, item.name() + " to_store (" + names + ")",
                         env->frame_to_lineno_(opts.loc));
    }
    for (Store* st : stores) {
        if (st->capacity.tally_raw_() - static_cast<double>(st->length_) > 0) {
            item.enter_sorted(*st, opts.priority);
            to_store_item_ = nullptr;
            to_store_store_ = st;
            to_stores_.clear();
            remove_();
            set_status_(Status::scheduled);
            reschedule_(env->now_raw_(), 0, false,
                        "to_store (" + st->name() + ") honor with " + item.name(), false, "",
                        env->last_s0_);
            return {};
        }
    }
    for (Store* st : stores) enter_sorted(*st->to_requesters_, opts.request_priority);
    set_status_(Status::requesting);
    to_store_item_ = &item;
    to_store_priority_ = opts.priority;
    to_stores_ = stores;
    reschedule_(scheduled_time, opts.priority, opts.urgent, "request to_store", opts.cap_now, "",
                env->frame_to_lineno_(opts.loc));
    return {};
}

// ---- queue membership -------------------------------------------------------

inline Qmember* Component::member_(const Queue& q) const {
    for (auto& [qq, m] : qmembers_)
        if (qq == &q) return m;
    return nullptr;
}

inline Qmember* Component::checkinqueue_(const Queue& q) const {
    Qmember* m = member_(q);
    if (!m) throw SalabimError(name_ + " component not in queue " + q.name());
    return m;
}

inline void Component::checknotinqueue_(const Queue& q) const {
    if (member_(q)) throw SalabimError(name_ + " component already in queue " + q.name());
}

inline void Component::checkisnotdata_() const {
    if (status_ == Status::data) throw SalabimError(name_ + " data component not allowed");
}

inline void Component::checkisnotmain_() const {
    if (ismain()) throw SalabimError(name_ + " main component not allowed");
}

inline Component& Component::enter(Queue& q) {
    checknotinqueue_(q);
    double priority = q.tail_.predecessor->priority;
    q.insert_in_front_of_(&q.tail_, this, priority);
    return *this;
}

inline Component& Component::enter_sorted(Queue& q, double priority) {
    checknotinqueue_(q);
    Qmember* m2;
    if (q.length_ >= 1 && priority < q.head_.successor->priority) {
        m2 = q.head_.successor;
    } else {
        m2 = &q.tail_;
        while (m2->predecessor != &q.head_ && m2->predecessor->priority > priority)
            m2 = m2->predecessor;
    }
    q.insert_in_front_of_(m2, this, priority);
    return *this;
}

inline Component& Component::enter_at_head(Queue& q) {
    checknotinqueue_(q);
    double priority = q.head_.successor->priority;
    q.insert_in_front_of_(q.head_.successor, this, priority);
    return *this;
}

inline Component& Component::enter_in_front_of(Queue& q, Component& poscomponent) {
    checknotinqueue_(q);
    Qmember* m2 = poscomponent.checkinqueue_(q);
    q.insert_in_front_of_(m2, this, m2->priority);
    return *this;
}

inline Component& Component::enter_behind(Queue& q, Component& poscomponent) {
    checknotinqueue_(q);
    Qmember* mx = poscomponent.checkinqueue_(q);
    q.insert_in_front_of_(mx->successor, this, mx->priority);
    return *this;
}

inline Component& Component::leave() {
    for (auto& [q, m] : std::vector<std::pair<Queue*, Qmember*>>(qmembers_))
        if (!q->isinternal_) leave(*q);
    return *this;
}

inline Component& Component::leave(Queue& q) {
    Qmember* mx = checkinqueue_(q);
    q.register_leave_(mx);
    return *this;
}

inline long long Component::count(const Queue* q) const {
    if (q) return member_(*q) ? 1 : 0;
    long long n = 0;
    for (auto& [qq, m] : qmembers_)
        if (!qq->isinternal_) ++n;
    return n;
}

inline long long Component::index(const Queue& q) const { return q.index(this); }

inline double Component::enter_time(const Queue& q) const {
    Qmember* m = checkinqueue_(q);
    return m->enter_time - env->offset_raw_();
}

inline double Component::priority(const Queue& q) const {
    return checkinqueue_(q)->priority;
}

inline void Component::set_priority(Queue& q, double priority) {
    Qmember* mx = checkinqueue_(q);
    if (mx->priority == priority) return;
    // remove and re-insert sorted (salabim keeps stats untouched)
    Qmember* m1 = mx->predecessor;
    Qmember* m2 = mx->successor;
    m1->successor = m2;
    m2->predecessor = m1;
    Qmember* pos;
    if (q.length_ >= 2 && priority < q.head_.successor->priority) {
        pos = q.head_.successor;
    } else {
        pos = &q.tail_;
        while (pos->predecessor != &q.head_ && pos->predecessor->priority > priority)
            pos = pos->predecessor;
    }
    Qmember* p1 = pos->predecessor;
    p1->successor = mx;
    pos->predecessor = mx;
    mx->predecessor = p1;
    mx->successor = pos;
    mx->priority = priority;
}

inline std::vector<Queue*> Component::queues() const {
    std::vector<Queue*> out;
    for (auto& [q, m] : qmembers_)
        if (!q->isinternal_) out.push_back(q);
    return out;
}

inline Component* Component::successor(const Queue& q) const {
    return checkinqueue_(q)->successor->component;
}

inline Component* Component::predecessor(const Queue& q) const {
    return checkinqueue_(q)->predecessor->component;
}

// ---- resource queries -------------------------------------------------------

inline double Component::claimed_quantity(const Resource* r) const {
    if (!r) {
        double s = 0;
        for (auto& [rr, q] : claims_) s += q;
        return s;
    }
    for (auto& [rr, q] : claims_)
        if (rr == r) return q;
    return 0;
}

inline double Component::requested_quantity(const Resource* r) const {
    if (!r) {
        double s = 0;
        for (auto& [rr, q] : requests_) s += q;
        return s;
    }
    for (auto& [rr, q] : requests_)
        if (rr == r) return q;
    return 0;
}

inline std::vector<Resource*> Component::claimed_resources() const {
    std::vector<Resource*> out;
    for (auto& [r, q] : claims_) out.push_back(r);
    return out;
}

inline std::vector<Resource*> Component::requested_resources() const {
    std::vector<Resource*> out;
    for (auto& [r, q] : requests_) out.push_back(r);
    return out;
}

inline bool Component::isclaiming(const Resource* r) const {
    if (!r) {
        for (auto& [q, m] : qmembers_)
            if (q->isclaimers_) return true;
        return false;
    }
    for (auto& [rr, q] : claims_)
        if (rr == r) return true;
    return false;
}

inline std::string Component::print_info(bool as_str) const {
    std::vector<std::string> result;
    result.push_back("Component " + detail::sprintf_str("%p", static_cast<const void*>(this)));
    result.push_back("  name=" + name_);
    result.push_back("  class=" + detail::demangle(typeid(*this).name()));
    result.push_back("  suppress_trace=" + std::string(suppress_trace_ ? "True" : "False"));
    result.push_back("  status=" + std::string(status_to_str(status_)));
    result.push_back("  mode=" + mode_);
    result.push_back("  mode_time=" + env->time_to_str(mode_time_));
    result.push_back("  creation_time=" + env->time_to_str(creation_time_));
    result.push_back("  scheduled_time=" + env->time_to_str(scheduled_time_));
    if (!qmembers_.empty()) {
        result.push_back("  member of queue(s):");
        for (auto& [q, m] : qmembers_)
            result.push_back("    " + detail::pad(q->name(), 20) + " enter_time=" +
                             env->time_to_str(m->enter_time - env->offset_raw_()) +
                             " priority=" + detail::fmt_num(m->priority));
    }
    if (!requests_.empty()) {
        result.push_back("  requesting resource(s):");
        for (auto& [r, q] : requests_)
            result.push_back("    " + detail::pad(r->name(), 20) + " quantity=" + detail::fmt_num(q));
    }
    if (!claims_.empty()) {
        result.push_back("  claiming resource(s):");
        for (auto& [r, q] : claims_)
            result.push_back("    " + detail::pad(r->name(), 20) + " quantity=" + detail::fmt_num(q));
    }
    if (!waits_.empty()) {
        result.push_back(wait_all_ ? "  waiting for all of state(s):" : "  waiting for any of state(s):");
        for (auto& ws : waits_) result.push_back("    " + detail::pad(ws.state->name(), 20));
    }
    std::string out;
    for (auto& l : result) out += l + "\n";
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

// --------------------------------- Resource --------------------------------

inline Resource::Resource(std::string name, double cap, Opts opts)
    : capacity("", MonitorOpts{.level = true, .initial_tally = cap, .monitor = opts.monitor, .env = opts.env}),
      claimed_quantity("", MonitorOpts{.level = true, .initial_tally = opts.initial_claimed_quantity,
                                         .monitor = opts.monitor, .env = opts.env}),
      available_quantity("", MonitorOpts{.level = true, .initial_tally = cap - opts.initial_claimed_quantity,
                                           .monitor = opts.monitor, .env = opts.env}),
      occupancy("", MonitorOpts{.level = true, .initial_tally = 0, // salabim starts occupancy at 0
                                .monitor = opts.monitor, .env = opts.env}) {
    env_ = detail::need_env(opts.env);
    if (opts.initial_claimed_quantity != 0 && !opts.anonymous)
        throw SalabimError("initial_claimed_quantity != 0 only allowed for anonymous resources");
    name_ = env_->set_name_(Environment::Registry::resource, std::move(name), "resource.",
                            &base_name_, &sequence_number_);
    capacity.rename("Capacity of " + name_);
    claimed_quantity.rename("Claimed quantity of " + name_);
    available_quantity.rename("Available quantity of " + name_);
    occupancy.rename("Occupancy of " + name_);
    {
        auto sup = env_->suppress_trace();
        requesters_ = std::make_unique<Queue>("requesters of " + name_,
                                              QueueOpts{.monitor = opts.monitor, .env = env_});
        requesters_->isinternal_ = true;
        claimers_ = std::make_unique<Queue>("claimers of " + name_,
                                            QueueOpts{.monitor = opts.monitor, .env = env_});
        claimers_->isinternal_ = true;
        claimers_->isclaimers_ = true;
    }
    capacity_ = cap;
    claimed_quantity_ = opts.initial_claimed_quantity;
    anonymous_ = opts.anonymous;
    preemptive_ = opts.preemptive;
    honor_only_first_ = opts.honor_only_first;
    honor_only_highest_priority_ = opts.honor_only_highest_priority;
    if (env_->trace())
        env_->print_trace("", "", name_ + " create", "capacity=" + detail::fmt_num(capacity_) +
                                                         (anonymous_ ? " anonymous" : ""));
}

inline Resource::~Resource() = default;

inline void Resource::update_monitors_() {
    claimed_quantity.tally(claimed_quantity_);
    occupancy.tally(capacity_ <= 0 ? 0 : claimed_quantity_ / capacity_);
    available_quantity.tally(capacity_ - claimed_quantity_);
}

inline void Resource::tryrequest_() {
    if (anonymous_) {
        if (trying_) return;
        trying_ = true;
        Qmember* mx = requesters_->head_.successor;
        Qmember* mx_first = mx;
        double mx_first_priority = mx_first->priority;
        while (mx != &requesters_->tail_) {
            if (honor_only_first_ && mx != mx_first) break;
            if (honor_only_highest_priority_ && mx->priority != mx_first_priority) break;
            Component* c = mx->component;
            mx = mx->successor;
            c->tryrequest_();
            if (!requesters_->contains(c)) mx = requesters_->head_.successor; // start again
        }
        trying_ = false;
    } else {
        Qmember* mx = requesters_->head_.successor;
        Qmember* mx_first = mx;
        double mx_first_priority = mx_first->priority;
        while (mx != &requesters_->tail_) {
            if (honor_only_first_ && mx != mx_first) break;
            if (honor_only_highest_priority_ && mx->priority != mx_first_priority) break;
            if (minq_ > capacity_ - claimed_quantity_ + 1e-8) break; // no more honors possible
            Component* c = mx->component;
            mx = mx->successor;
            c->tryrequest_();
        }
    }
}

inline void Resource::release(std::optional<double> quantity) {
    if (anonymous_) {
        double q = quantity.value_or(claimed_quantity_);
        claimed_quantity_ -= q;
        if (claimed_quantity_ < 1e-8) claimed_quantity_ = 0;
        update_monitors_();
        tryrequest_();
    } else {
        if (quantity) throw SalabimError("no quantity allowed for non-anonymous resource");
        Qmember* mx = claimers_->head_.successor;
        while (mx != &claimers_->tail_) {
            Component* c = mx->component;
            mx = mx->successor;
            c->release(*this);
        }
    }
}

inline void Resource::set_capacity(double cap) {
    capacity_ = cap;
    capacity.tally(capacity_);
    available_quantity.tally(capacity_ - claimed_quantity_);
    occupancy.tally(capacity_ <= 0 ? 0 : claimed_quantity_ / capacity_);
    tryrequest_();
}

inline void Resource::reset_monitors(std::optional<bool> monitor_on) {
    requesters_->reset_monitors(monitor_on);
    claimers_->reset_monitors(monitor_on);
    capacity.reset(monitor_on);
    claimed_quantity.reset(monitor_on);
    available_quantity.reset(monitor_on);
    occupancy.reset(monitor_on);
}

inline std::string Resource::print_statistics(bool as_str) const {
    std::vector<std::string> result;
    result.push_back("Statistics of " + name_ + " at " +
                     detail::sprintf_str("%13.3f", env_->now_raw_() - env_->offset_raw_()));
    bool show_legend = true;
    for (Queue* q : {requesters_.get(), claimers_.get()}) {
        result.push_back(q->length.print_statistics(false, show_legend, true, true));
        show_legend = false;
        result.push_back("");
        result.push_back(q->length_of_stay.print_statistics(false, show_legend, true, true));
        result.push_back("");
    }
    for (const Monitor* m : {&capacity, &available_quantity, &claimed_quantity, &occupancy}) {
        result.push_back(m->print_statistics(false, show_legend, true, true));
        result.push_back("");
    }
    std::string out;
    for (auto& l : result) {
        out += l;
        if (l.empty() || l.back() != '\n') out += "\n";
    }
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

inline std::string Resource::print_histograms(bool as_str) const {
    std::string out;
    out += requesters_->length.print_histogram({.as_str = true});
    out += requesters_->length_of_stay.print_histogram({.as_str = true});
    out += claimers_->length.print_histogram({.as_str = true});
    out += claimers_->length_of_stay.print_histogram({.as_str = true});
    out += capacity.print_histogram({.as_str = true});
    out += available_quantity.print_histogram({.as_str = true});
    out += claimed_quantity.print_histogram({.as_str = true});
    out += occupancy.print_histogram({.as_str = true});
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

inline std::string Resource::print_info(bool as_str) const {
    std::vector<std::string> result;
    result.push_back("Resource " + detail::sprintf_str("%p", static_cast<const void*>(this)));
    result.push_back("  name=" + name_);
    result.push_back("  capacity=" + detail::fmt_num(capacity_));
    if (requesters_->empty())
        result.push_back("  no requesting components");
    else {
        result.push_back("  requesting component(s):");
        for (Qmember* m = requesters_->head_.successor; m != &requesters_->tail_; m = m->successor)
            result.push_back("    " + detail::pad(m->component->name(), 20) +
                             " quantity=" + detail::fmt_num(m->component->requested_quantity(this)));
    }
    result.push_back("  claimed_quantity=" + detail::fmt_num(claimed_quantity_));
    if (!anonymous_) {
        if (claimed_quantity_ > 0 && !claimers_->empty()) {
            result.push_back("  claimed by:");
            for (Qmember* m = claimers_->head_.successor; m != &claimers_->tail_; m = m->successor)
                result.push_back("    " + detail::pad(m->component->name(), 20) +
                                 " quantity=" + detail::fmt_num(m->component->claimed_quantity(this)));
        }
    }
    std::string out;
    for (auto& l : result) out += l + "\n";
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

// --------------------------------- Store -----------------------------------

inline Store::Store(std::string name, Opts opts) : Queue(std::move(name), opts, 5, "store.") {
    auto sup = env_->suppress_trace();
    from_requesters_ = std::make_unique<Queue>(name_ + ".from_store_requesters",
                                               QueueOpts{.monitor = opts.monitor, .env = env_});
    from_requesters_->isinternal_ = true;
    to_requesters_ = std::make_unique<Queue>(name_ + ".to_store_requesters",
                                             QueueOpts{.monitor = opts.monitor, .env = env_});
    to_requesters_->isinternal_ = true;
}

inline Store::~Store() = default;

inline void Store::item_entered_(Component* item) {
    if (!from_requesters_ || from_requesters_->empty()) return;
    // Python salabim dies with a RecursionError on the same pattern (an item
    // bouncing between a blocked to_store putter and a filtered from_store
    // getter); fail with a diagnosable error instead of a stack overflow.
    if (honor_depth_ > 200)
        throw SalabimError(name_ + ": store honor recursion (a blocked to_store together with a "
                                   "filtered from_store waiter bounces one item forever; salabim "
                                   "raises RecursionError here). Restructure the model, e.g. give "
                                   "the filtered getter its own unbounded store.");
    struct Depth {
        int& d;
        ~Depth() { --d; }
    } depth{++honor_depth_};
    for (Qmember* m = from_requesters_->head_.successor; m != &from_requesters_->tail_;
         m = m->successor) {
        Component* requester = m->component;
        if (!requester->from_store_filter_ || requester->from_store_filter_(item)) {
            item->leave(*this);
            for (Store* st : std::vector<Store*>(requester->from_stores_))
                requester->leave(*st->from_requesters_);
            requester->from_stores_.clear();
            requester->from_store_item_ = item;
            requester->from_store_store_ = this;
            requester->remove_();
            requester->set_status_(Status::scheduled);
            requester->reschedule_(env_->now_raw_(), 0, false,
                                   "from_store (" + name_ + ") honor with " + item->name(), false, "",
                                   env_->last_s0_);
            break;
        }
    }
}

inline void Store::item_left_() {
    if (!to_requesters_ || to_requesters_->empty()) return;
    double available = capacity.tally_raw_() - static_cast<double>(length_);
    if (available <= 0) return;
    Component* requester = to_requesters_->head();
    {
        auto sup = env_->suppress_trace();
        requester->to_store_item_->enter_sorted(*this, requester->to_store_priority_);
    }
    for (Store* st : std::vector<Store*>(requester->to_stores_))
        requester->leave(*st->to_requesters_);
    requester->to_stores_.clear();
    requester->remove_();
    requester->set_status_(Status::scheduled);
    requester->reschedule_(env_->now_raw_(), 0, false, "to_store (" + name_ + ") honor ", false, "",
                           env_->last_s0_);
    requester->to_store_item_ = nullptr;
    requester->to_store_store_ = this;
}

inline void Store::set_capacity_store(double cap) {
    double old_cap = capacity.tally_raw_();
    Queue::set_capacity(cap);
    if (cap >= old_cap) {
        while (!to_requesters_->empty() &&
               capacity.tally_raw_() - static_cast<double>(length_) > 0)
            item_left_();
    }
}

inline void Store::rescan() {
    for (Qmember* m = from_requesters_->head_.successor; m != &from_requesters_->tail_;) {
        Component* c = m->component;
        m = m->successor;
        Component* found = nullptr;
        for (Component* item : *this)
            if (!c->from_store_filter_ || c->from_store_filter_(item)) {
                found = item;
                break;
            }
        if (found) {
            for (Store* st : std::vector<Store*>(c->from_stores_))
                c->leave(*st->from_requesters_);
            {
                auto sup = env_->suppress_trace();
                found->leave(*this);
            }
            c->from_stores_.clear();
            c->from_store_item_ = found;
            c->from_store_store_ = this;
            c->remove_();
            c->set_status_(Status::scheduled);
            c->reschedule_(env_->now_raw_(), 0, false,
                           "from_store (" + name_ + ") honor with " + found->name(), false, "",
                           env_->last_s0_);
        }
    }
}

// --------------------------------- StateBase -------------------------------

inline void StateBase::init_base_(std::string name, Environment* env) {
    env_ = env;
    name_ = env_->set_name_(Environment::Registry::state, std::move(name), "state.", &base_name_,
                            &sequence_number_);
    auto sup = env_->suppress_trace();
    waiters_ = std::make_unique<Queue>("waiters of " + name_, QueueOpts{.env = env_});
    waiters_->isinternal_ = true;
}

inline StateBase::~StateBase() = default;

inline void StateBase::trywait_(double max_honor) {
    Qmember* mx = waiters_->head_.successor;
    while (mx != &waiters_->tail_) {
        Component* c = mx->component;
        mx = mx->successor;
        if (c->trywait_()) {
            max_honor -= 1;
            if (max_honor == 0) return;
        }
    }
}

inline void StateBase::reset_monitors(std::optional<bool> monitor_on) {
    waiters_->reset_monitors(monitor_on);
    value_mon_->reset(monitor_on);
}

inline std::string StateBase::print_statistics(bool as_str) const {
    std::vector<std::string> result;
    result.push_back("Statistics of " + name_ + " at " +
                     detail::fn(env_->now_raw_() - env_->offset_raw_(), 13, 3));
    result.push_back(waiters_->length.print_statistics(false, true, true, true));
    result.push_back("");
    result.push_back(waiters_->length_of_stay.print_statistics(false, false, true, true));
    result.push_back("");
    result.push_back(value_mon_->print_statistics(false, false, true, true));
    std::string out;
    for (auto& l : result) {
        out += l;
        if (l.empty() || l.back() != '\n') out += "\n";
    }
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

inline std::string StateBase::print_histograms(bool as_str) const {
    std::string out = waiters_->print_histograms(true);
    out += value_mon_->print_histogram({.as_str = true});
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

inline std::string StateBase::print_info(bool as_str) const {
    std::vector<std::string> result;
    result.push_back("State " + detail::sprintf_str("%p", static_cast<const void*>(this)));
    result.push_back("  name=" + name_);
    result.push_back("  value=" + value_str_());
    if (waiters_->empty())
        result.push_back("  no waiting components");
    else {
        result.push_back("  waiting component(s):");
        for (Qmember* m = waiters_->head_.successor; m != &waiters_->tail_; m = m->successor)
            result.push_back("    " + detail::pad(m->component->name(), 20));
    }
    std::string out;
    for (auto& l : result) out += l + "\n";
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

// --------------------------------- Environment -----------------------------

inline Environment::Environment(EnvOptions opts) {
    if (opts.name.empty()) {
        if (opts.isdefault_env) {
            name_ = "default environment";
        } else {
            auto it = env_registry_.find("environment.");
            long long seqn = (it != env_registry_.end()) ? ++it->second
                                                         : (env_registry_["environment."] = 0);
            name_ = "environment." + std::to_string(seqn);
        }
    } else {
        name_ = opts.name;
    }
    time_unit_ = detail::time_unit_lookup(opts.time_unit);
    time_unit_name_ = opts.time_unit;
    if (opts.isdefault_env) g::default_env = this;
    trace_ = opts.trace;
    print_trace_header_ = opts.print_trace_header;
    source_files_.emplace_back(opts.loc.file_name(), 0);
    random_seed(opts.random_seed);
    if (trace_ && print_trace_header_) {
        print_trace_header();
        header_printed_ = true;
    }
    if (trace_) print_trace("", "", name_ + " initialize", "", frame_to_lineno_(opts.loc));

    auto& pend = detail::pending_component;
    pend.active = true;
    pend.env = this;
    pend.name = "main";
    pend.base_name = "main";
    pend.sequence_number = 0;
    pend.mode = "";
    pend.suppress_trace = false;
    pend.skip_standby = false;
    main_ = new Component();
    components_.emplace_back(main_);
    main_->creation_line_ = opts.loc.line();
    main_->creation_file_ = opts.loc.file_name();
    main_->last_line_ = opts.loc.line();
    main_->last_file_ = opts.loc.file_name();
    if (trace_) print_trace("", "", "main create", "", frame_to_lineno_(opts.loc));
    main_->set_status_(Status::current);
    current_ = main_;
    if (trace_) print_trace(time_to_str(0), "main", "current", "", frame_to_lineno_(opts.loc));
}

inline Environment::~Environment() {
    shutting_down_ = true;
    components_.clear();
    if (g::default_env == this) g::default_env = nullptr;
}

inline std::string Environment::set_name_(Registry reg, std::string name,
                                          const std::string& fallback_classname,
                                          std::string* base_name, long long* sequence_number) {
    auto& registry = registries_[static_cast<int>(reg)];
    if (name.empty()) name = fallback_classname; // classname (lowercased) + "."
    if (name == "." || name == ",") name = fallback_classname.substr(0, fallback_classname.size() - 1) + name;
    if (name.ends_with(".") || name.ends_with(",")) {
        long long seq;
        auto it = registry.find(name);
        if (it != registry.end())
            seq = ++it->second;
        else {
            seq = name.ends_with(",") ? 1 : 0;
            registry[name] = seq;
        }
        *base_name = name;
        *sequence_number = seq;
        return name.substr(0, name.size() - 1) + "." + std::to_string(seq);
    }
    *base_name = name;
    *sequence_number = 0;
    return name;
}

inline void Environment::print_trace(const std::string& s1, const std::string& s2,
                                     const std::string& s3, const std::string& s4,
                                     std::optional<std::string> s0, bool optional_line) {
    if (!trace_) return;
    if (current_ && current_->suppress_trace_) return;
    std::string s0v = s0.value_or("");
    last_s0_ = s0v;
    long len_s1 = static_cast<long>(time_to_str(0).size());
    std::string line = detail::pad(s0v, 7) + detail::pad(s1, len_s1) + " " + detail::pad(s2, 20) +
                       " " + detail::pad(s3, std::max<long>(static_cast<long>(s3.size()), 36)) + " " +
                       detail::strip(s4);
    if (optional_line) {
        buffered_trace_ = line;
        return;
    }
    if (buffered_trace_) {
        (*trace_out_) << *buffered_trace_ << "\n";
        buffered_trace_.reset();
    }
    (*trace_out_) << line << "\n";
}

inline void Environment::print_legend_(int ref) {
    std::string s = ref ? ("line numbers prefixed by " + std::string(1, char('A' + ref - 1)) + " refer to")
                        : "line numbers refers to";
    for (auto& [fn, r] : source_files_)
        if (r == ref) {
            print_trace("", "", s, detail::basename(fn));
            break;
        }
}

inline void Environment::print_trace_header() {
    long len_s1 = static_cast<long>(time_to_str(0).size());
    print_trace(std::string(static_cast<size_t>(len_s1 - 4), ' ') + "time", "current component",
                "action", "information", std::string("line#"));
    print_trace(std::string(static_cast<size_t>(len_s1), '-'), std::string(20, '-'),
                std::string(35, '-'), std::string(48, '-'), std::string(6, '-'));
    for (size_t ref = 0; ref < source_files_.size(); ++ref) print_legend_(static_cast<int>(ref));
}

inline std::string Environment::filename_lineno_to_str_(const char* filename, unsigned line) {
    if (!filename) return "";
    std::string_view f(filename);
    if (f.ends_with("salabim.hpp")) return ""; // internal (salabim: "n/a")
    int ref = -1;
    for (auto& [fn, r] : source_files_)
        if (fn == f) {
            ref = r;
            break;
        }
    bool new_entry = false;
    if (ref < 0) {
        ref = static_cast<int>(source_files_.size());
        source_files_.emplace_back(std::string(f), ref);
        new_entry = true;
    }
    std::string pre = ref == 0 ? "" : std::string(1, char('A' + ref - 1));
    if (new_entry) print_legend_(ref);
    return detail::rpad(pre + std::to_string(line), 5);
}

inline double Environment::peek() {
    while (!event_list_.empty()) {
        const EvtEntry& e = event_list_.top();
        if (e.c->on_event_list_ && e.gen == e.c->event_gen_) return e.t - offset_;
        event_list_.pop();
    }
    return inf;
}

inline void Environment::reset_now(double new_now) {
    double offset_before = offset_;
    offset_ = now_ - new_now;
    if (trace_)
        print_trace("", "", "reset_now", "offset " + duration_to_str(offset_ - offset_before));
}

inline bool Environment::pop_valid_event_(EvtEntry* out) {
    while (!event_list_.empty()) {
        EvtEntry e = event_list_.top();
        event_list_.pop();
        if (e.c->on_event_list_ && e.gen == e.c->event_gen_) {
            *out = e;
            return true;
        }
    }
    return false;
}

inline void Environment::terminate_(Component* c) {
    std::string s0 = "";
    if (trace_ && !suppress_trace_linenumbers_ && c->last_file_)
        s0 = filename_lineno_to_str_(c->last_file_, c->last_line_) + "+";
    if (s0 == "+") s0 = "";
    for (auto& [r, q] : std::vector<std::pair<Resource*, double>>(c->claims_))
        c->release_(r, std::nullopt, s0);
    print_trace("", "", c->name() + " ended", "", s0);
    c->set_status_(Status::data);
    c->scheduled_time_ = inf;
    if (c->process_) {
        c->process_.destroy();
        c->process_ = {};
    }
}

// A finished sub-process hands its component's "current frame" back to the parent
// and symmetric-transfers into it; a finished root process suspends back to the
// scheduler (which detects done() and terminates the component).
inline std::coroutine_handle<> Process::FinalAwaiter::await_suspend(Process::Handle h) noexcept {
    auto& p = h.promise();
    if (p.continuation) {
        if (p.component) p.component->process_ = p.continuation;
        return p.continuation;
    }
    return std::noop_coroutine();
}

inline void Environment::resume_process_(Component* c) {
    if (!c->process_) {
        terminate_(c);
        return;
    }
    if (!c->deferred_anon_rescan_.empty()) {
        // re-scan deferred by tryrequest_ (Python runs it on greenlet resumption)
        auto rs = std::move(c->deferred_anon_rescan_);
        c->deferred_anon_rescan_.clear();
        for (Resource* r : rs) r->tryrequest_();
    }
    auto h = c->process_;
    h.resume();
    if (c->process_ && c->process_.done()) {
        // note: read the exception off c->process_, not h — with sub-processes the
        // frame the scheduler resumed may be an inner (already destroyed) frame
        std::exception_ptr ex = c->process_.promise().exception;
        if (ex) {
            c->process_.destroy();
            c->process_ = {};
            try {
                std::rethrow_exception(ex);
            } catch (const detail::AbandonedByCancel&) {
                // self-cancel: Python's abandoned greenlet — no 'ended', no terminate
                c->process_abandoned_ = false;
                return;
            } catch (...) {
                c->set_status_(Status::data);
                running_ = false;
                throw;
            }
        }
        terminate_(c);
    } else if (c->process_abandoned_) {
        c->process_abandoned_ = false;
        if (c->process_) {
            c->process_.destroy();
            c->process_ = {};
        }
    }
}

inline void Environment::step() {
    // standby components get a turn after every event
    if (!current_ || !current_->skip_standby_) {
        if (!pendingstandbylist_.empty()) {
            Component* c = pendingstandbylist_.front();
            pendingstandbylist_.erase(pendingstandbylist_.begin());
            if (c->status_ == Status::standby) { // skip cancelled components
                c->set_status_(Status::current);
                c->scheduled_time_ = inf;
                current_ = c;
                if (trace_)
                    print_trace(time_to_str(now_ - offset_), c->name(), "current (standby)", "",
                                c->lineno_txt_(), suppress_trace_standby_);
                resume_process_(c);
                return;
            }
        }
    }
    if (!standbylist_.empty()) {
        pendingstandbylist_ = std::move(standbylist_);
        standbylist_.clear();
    }

    Component* c;
    double t;
    EvtEntry e;
    if (pop_valid_event_(&e)) {
        c = e.c;
        t = e.t;
        c->on_event_list_ = false;
    } else {
        c = main_;
        if (end_on_empty_eventlist_) {
            t = now_;
            print_trace("", "", "run ended", "no events left", "");
        } else {
            t = inf;
        }
    }
    now_ = t;
    current_ = c;
    c->set_status_(Status::current);
    c->scheduled_time_ = inf;
    if (trace_) print_trace(time_to_str(now_ - offset_), c->name(), "current", "", c->lineno_txt_());
    if (c == main_) {
        running_ = false;
        return;
    }
    c->check_fail_();
    resume_process_(c);
}

inline void Environment::run_impl_(DurationSpec duration, RunOpts opts) {
    end_on_empty_eventlist_ = false;
    std::string extra;
    double scheduled_time;
    if (opts.till.has_value()) {
        if (duration.has_value()) throw SalabimError("both duration and till specified");
        scheduled_time = opts.till.resolve() + offset_;
    } else if (duration.has_value()) {
        double d = duration.resolve();
        scheduled_time = (d == inf) ? inf : now_ + d;
    } else {
        scheduled_time = inf;
        end_on_empty_eventlist_ = true;
        extra = "*";
    }
    main_->set_line_(opts.loc);
    main_->set_status_(Status::scheduled);
    main_->reschedule_(scheduled_time, opts.priority, opts.urgent, "run", opts.cap_now, extra,
                       frame_to_lineno_(opts.loc));
    running_ = true;
    while (running_) step();
}

inline std::string Environment::print_info(bool as_str) const {
    std::vector<std::string> result;
    result.push_back("Environment " + detail::sprintf_str("%p", static_cast<const void*>(this)));
    result.push_back("  name=" + name_);
    result.push_back("  now=" + time_to_str(now_ - offset_));
    result.push_back("  current_component=" + (current_ ? current_->name() : ""));
    result.push_back("  trace=" + std::string(trace_ ? "True" : "False"));
    std::string out;
    for (auto& l : result) out += l + "\n";
    if (!as_str) std::cout << out;
    return as_str ? out : "";
}

} // namespace sim

#endif // SALABIM_HPP