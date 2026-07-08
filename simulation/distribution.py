import salabim as sim

from simulation import env
from typing import Callable


class Distribution:
    def __init__(self, distr_type: type[sim.Distribution], *params: float | Callable[[float], float]) -> None:
        self.distr_type = distr_type
        self.params = params

    def sample_params_at(self, t: float) -> list[float]:
        return [param if isinstance(param, (int, float)) else param(t) for param in self.params]
    
    def sample(self, t: float) -> float:
        return self.distr_type(*self.sample_params_at(t)).sample()
    
    def sample_now(self) -> float:
        return self.sample(env.now())
