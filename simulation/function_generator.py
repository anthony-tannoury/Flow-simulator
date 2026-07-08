import numpy as np

from simulation import env
from typing import Callable



class Linear:
    @staticmethod
    def generate(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
        if x1 == x2:
            raise ValueError("Cannot generate vertical line function")
        
        def linear(t: float) -> float:
            slope = (y1 - y2) / (x1 - x2)
            intercept = y1 - slope * x1
            return slope * t + intercept
        
        return linear
    

class Exponential:
    @staticmethod
    def generate(x1: float, y1: float, x2: float, y2: float, limit: float) -> Callable[[float], float]:
        if x1 == x2:
            raise ValueError("Cannot generate vertical exponential function")
        if (y1 - limit) * (y2 - limit) <= 0:
            raise ValueError("y1 and y2 in exponential function must be on the same side compared to limit")
        
        def exponential(t: float) -> float:
            beta = np.log((y1 - limit) / (y2 - limit)) / (x1 - x2)
            alpha = (y1 - limit) / np.exp(beta * x1)
            return alpha * np.exp(beta * t) + limit
        
        return exponential


class Bathtub:
    @staticmethod
    def generate(a: float, tau: float, c: float, beta: float, eta: float) -> Callable[[float], float]:
        def bathtub(t: float) -> float:
            return a * np.exp(t / tau) + c + (beta / eta) * np.pow(t / eta, beta - 1)
        
        return bathtub
    
    @staticmethod
    def sample_mttr(bathtub_curve: Callable[[float], float], tolerance: float = 60, max_iters: int = 100) -> float:
        threshold = -np.log(env.random.random())
        integral = 0.0
        t = env.now()
        iters = 0

        while iters < max_iters and integral < threshold:
            integral += bathtub_curve(t) * tolerance
            t += tolerance
            iters += 1

        if integral < threshold:
            raise ValueError(f"Integral did not cross threshold after {max_iters} iterations")
        return t
