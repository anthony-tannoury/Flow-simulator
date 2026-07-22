import salabim as sim
import numpy as np


SEED = 0
sim.yieldless(True)
env = sim.Environment(random_seed=SEED)
np.random.seed(SEED)


def reseed(seed: int) -> None:
    global SEED
    SEED = seed
    env.random_seed(seed)
