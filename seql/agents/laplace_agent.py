import jax.numpy as jnp
from jax import hessian, tree_map

import distrax

import chex
import typing_extensions
from typing import Any, NamedTuple, Optional
from functools import partial

import warnings

from jsl.experimental.seql.agents.agent_utils import Memory
from jsl.experimental.seql.agents.base import Agent

JaxOptSolver = Any
Params = Any
Info = NamedTuple


class BeliefState(NamedTuple):
    mu: Params
    Sigma: Params = None


class Info(NamedTuple):
    ...


class ModelFn(typing_extensions.Protocol):
    def __call__(self,
                 params: chex.Array,
                 inputs: chex.Array):
        ...


class EnergyFn(typing_extensions.Protocol):
    def __call__(self,
                 params: chex.Array,
                 inputs: chex.Array,
                 outputs: chex.Array,
                 model_fn: ModelFn):
        ...


class LaplaceAgent(Agent):

    def __init__(self,
                 solver: JaxOptSolver,
                 energy_fn: EnergyFn,
                 model_fn: ModelFn,
                 min_n_samples: int = 1,
                 buffer_size: int = 0,
                 obs_noise: float = 0.01,
                 is_classifier: bool = False):
        super(LaplaceAgent, self).__init__(is_classifier)

        self.memory = Memory(buffer_size)
        self.solver = solver
        self.energy_fn = energy_fn
        self.model_fn = model_fn
        self.obs_noise = obs_noise
        self.min_n_samples = min_n_samples
        self.buffer_size = buffer_size

    def init_state(self,
                   mu: chex.Array,
                   Sigma: Optional[chex.Array] = None):
        return BeliefState(mu, Sigma)

    def update(self,
               key: chex.PRNGKey,
               belief: BeliefState,
               x: chex.Array,
               y: chex.Array):

        x_, y_ = self.memory.update(x, y)

        if len(x_) < self.min_n_samples:
            warnings.warn("There should be more data.", UserWarning)
            return belief, None

        params, info = self.solver.run(belief.mu,
                                       inputs=x_,
                                       outputs=y_)
        partial_energy_fn = partial(self.energy_fn,
                                    inputs=x_,
                                    outputs=y_)

        Sigma = hessian(partial_energy_fn)(params)
        return BeliefState(params, tree_map(jnp.squeeze, Sigma)), info

    def sample_params(self,
                      key: chex.PRNGKey,
                      belief: BeliefState):
        mu, Sigma = belief.mu, belief.Sigma
        mvn = distrax.MultivariateNormalFullCovariance(jnp.squeeze(mu, axis=-1),
                                                       Sigma)
        theta = mvn.sample(seed=key)
        theta = theta.reshape(mu.shape)
        return theta