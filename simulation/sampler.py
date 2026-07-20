import math

import salabim as sim
import numpy as np

from simulation import env
from typing import Protocol, Callable


class LogNormal:
    """Log-normal distribution parameterised by the mean and standard deviation
    of the values *themselves*, not of their logarithm.

    `LogNormal(120, 30)` draws strictly positive values that average 120 with a
    standard deviation of 30 -- exactly like `Normal(120, 30)` but skewed and
    positive. This matches the flow designer's `mean` / `sigma` fields and every
    other distribution in the tool, which all take real, physical parameters
    (a mean duration in minutes, etc.). Note this is deliberately NOT the
    numpy.random.lognormal convention, where `mean`/`sigma` are the log-space
    mu/sigma: passing a real mean of 120 there yields exp(120) ~ 1e52, which is
    why an MTTR entered that way never ended.

    Internally it converts (mean, std) to the underlying normal's (mu, sigma)
    -- sigma^2 = ln(1 + (std/mean)^2), mu = ln(mean) - sigma^2/2 -- and
    exponentiates a salabim Normal draw, so sampling stays on the shared seeded
    stream. It exposes the salabim-distribution `sample()` / `mean()` interface,
    so the parser's Distribution wrapper and sim.Bounded use it like any other.
    `mean` must be > 0 (a log-normal is strictly positive)."""

    def __init__(self, mean: float, sigma: float, randomstream=None, env=None) -> None:
        if mean <= 0:
            raise ValueError(f"LogNormal mean must be > 0 (got {mean})")
        if sigma < 0:
            raise ValueError(f"LogNormal standard deviation must be >= 0 (got {sigma})")
        self._mean = float(mean)
        self._std = float(sigma)
        sigma_sq = math.log(1.0 + (self._std * self._std) / (self._mean * self._mean))
        mu = math.log(self._mean) - sigma_sq / 2.0
        self._normal = sim.Normal(mu, math.sqrt(sigma_sq), randomstream=randomstream, env=env)

    def sample(self) -> float:
        return math.exp(self._normal.sample())

    def mean(self) -> float:
        return self._mean


class Sampler(Protocol):
    def sample(self, t: float) -> float: ...

    def sample_now(self) -> float:
        return self.sample(env.now())


class Distribution(Sampler):
    def __init__(self, distr_type: type[sim.Distribution], *params: float | Callable[[float], float]) -> None:
        self.distr_type = distr_type
        self.params = params

    def sample_params_at(self, t: float) -> list[float]:
        return [param if isinstance(param, (int, float)) else param(t) for param in self.params]
    
    def sample(self, t: float) -> float:
        return self.distr_type(*self.sample_params_at(t)).sample()
    
    def mean(self, t: float) -> float:
        return self.distr_type(*self.sample_params_at(t)).mean()
    
    def mean_now(self) -> float:
        return self.mean(env.now())
    

class FailureRate(Sampler):
    def __init__(self, failure_rate: Callable[[float], float], tolerance: float = 60, max_iters: int = 10000) -> None:
        self.failure_rate = failure_rate
        self.tolerance = tolerance
        self.max_iters = max_iters

    def sample(self, t: float) -> float:
        threshold = -np.log(env.random.random())
        integral = 0.0
        iters = 0

        while iters < self.max_iters and integral < threshold:
            integral += self.failure_rate(t) * self.tolerance
            t += self.tolerance
            iters += 1

        if integral < threshold:
            raise ValueError(f"Integral did not cross threshold after {self.max_iters} iterations")
        return t - env.now()
