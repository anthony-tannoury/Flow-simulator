import salabim as sim
import numpy as np


SEED = 0
sim.yieldless(True)
env = sim.Environment(random_seed=SEED)
np.random.seed(SEED)
