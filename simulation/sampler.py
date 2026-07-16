import salabim as sim
import numpy as np

from simulation import env
from typing import Protocol, Callable


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
