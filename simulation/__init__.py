import salabim as sim
import numpy as np


SEED = 0
sim.yieldless(True)
env = sim.Environment(random_seed=SEED)
np.random.seed(SEED)


def reseed(seed: int) -> None:
    """Re-seed the shared environment and record the seed so the report's `graine`
    reflects it. The parser calls this once it has read the flow's seed, before any
    object is built or any draw is made. env.random_seed() reseeds salabim's stream
    and (set_numpy_random_seed defaults True) numpy's, so every draw is reproducible
    for a given seed and differs between seeds."""
    global SEED
    SEED = seed
    env.random_seed(seed)
