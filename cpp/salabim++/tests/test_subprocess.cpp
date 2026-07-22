#include "salabim.hpp"
#include <cassert>
#include <cstdio>
#include <vector>

static std::vector<std::string> log_;
static void logf(const std::string& s) { log_.push_back(s); }

sim::Resource* res;
sim::State<bool>* flag;

struct Worker : sim::Component {
    double got = -1;

    sim::Process inner(double* out) {
        logf("inner start t=" + std::to_string((int)env->now()));
        co_await hold(5);
        *out = env->now();
        logf("inner end t=" + std::to_string((int)env->now()));
    }

    sim::Process middle(double* out) {
        logf("middle start t=" + std::to_string((int)env->now()));
        co_await hold(1);
        co_await call(inner(out));
        co_await request(*res);
        logf("middle got res t=" + std::to_string((int)env->now()));
        release();
        co_await wait(*flag);
        logf("middle saw flag t=" + std::to_string((int)env->now()));
    }

    sim::Process instant() { logf("instant ran"); co_return; }

    sim::Process process() override {
        co_await call(instant());
        co_await hold(2);
        co_await call(middle(&got));
        logf("root end t=" + std::to_string((int)env->now()));
    }
};

struct Flagger : sim::Component {
    sim::Process process() override {
        co_await hold(20);
        flag->set(true);
    }
};

struct Thrower : sim::Component {
    bool caught = false;
    sim::Process boom() {
        co_await hold(1);
        throw std::runtime_error("kaboom");
    }
    sim::Process process() override {
        try {
            co_await call(boom());
        } catch (const std::runtime_error& e) {
            caught = true;
            logf(std::string("caught: ") + e.what());
        }
        co_await hold(1);
    }
};

struct Sleeper : sim::Component {
    sim::Process nap() { co_await hold(1000); }
    sim::Process process() override {
        co_await call(nap());
        logf("sleeper should never get here");
    }
};

struct Canceller : sim::Component {
    Sleeper* victim;
    explicit Canceller(Sleeper* v) : victim(v) {}
    sim::Process process() override {
        co_await hold(3);
        victim->cancel();
        logf("cancelled sleeper t=" + std::to_string((int)env->now()));
    }
};

int main() {
    {
        sim::Environment env({.random_seed = 0});
        sim::Resource r("res", 1);
        sim::State<bool> f("flag", false);
        res = &r; flag = &f;

        auto* w = sim::make<Worker>();
        sim::make<Flagger>();
        auto* t = sim::make<Thrower>();
        auto* s = sim::make<Sleeper>();
        sim::make<Canceller>({}, s);

        env.run(sim::RunOpts{.till = 100});

        assert(w->got == 8);
        assert(t->caught);
        assert(s->isdata());
        for (auto& l : log_) std::puts(l.c_str());
        assert(log_.size() == 9);
    }
    std::puts("ALL SUBPROCESS TESTS PASSED");
    return 0;
}
