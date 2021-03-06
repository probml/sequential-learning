import jax.numpy as jnp
from jax import jit, value_and_grad

import optax

import chex

import typing_extensions
from typing import Any, NamedTuple

import warnings

from seql.agents.agent_utils import Memory
from seql.agents.base import Agent, LoglikelihoodFn, LogpriorFn, ModelFn

Params = Any
Optimizer = NamedTuple


# https://github.com/deepmind/optax/blob/252d152660300fc7fe22d214c5adbe75ffab0c4a/optax/_src/transform.py#L35
class TraceState(NamedTuple):
    """Holds an aggregation of past updates."""
    trace: chex.ArrayTree


class ModelFn(typing_extensions.Protocol):
    def __call__(self,
                 params: Params,
                 x: chex.Array):
        ...


class LossFn(typing_extensions.Protocol):
    def __call__(self,
                 params: Params,
                 x: chex.Array,
                 y: chex.Array,
                 model_fn: ModelFn) -> float:
        ...


class BeliefState(NamedTuple):
    params: Params
    opt_state: TraceState


class Info(NamedTuple):
    loss: float


class SGDAgent(Agent):

    def __init__(self,
                 loglikelihood: LoglikelihoodFn,
                 model_fn: ModelFn,
                 logprior: LogpriorFn = lambda params: 0.,
                 nepochs: int = 20,
                 threshold: int = 1,
                 buffer_size: int = jnp.inf,
                 obs_noise: float = 0.1,
                 optimizer: Optimizer = optax.adam(1e-2),
                 is_classifier: bool = False):

        super(SGDAgent, self).__init__(is_classifier)
        assert threshold <= buffer_size
        self.buffer_size = buffer_size
        memory = Memory(buffer_size)
        self.memory = memory
        self.threshold = threshold
        self.model_fn = model_fn

        def loss_fn(params: Params,
                 x: chex.Array,
                 y: chex.Array):

            ll =  loglikelihood(params,
                                x, y,
                                self.model_fn)
            lp = logprior(params)
            return -(ll + lp)

        self.loss_fn = loss_fn
        value_and_grad_fn = jit(value_and_grad(self.loss_fn))
        self.value_and_grad_fn = value_and_grad_fn
        self.optimizer = optimizer
        self.nepochs = nepochs
        self.obs_noise = obs_noise

    def init_state(self,
                   params: Params):
        opt_state = self.optimizer.init(params)
        return BeliefState(params, opt_state)

    def update(self,
               key: chex.PRNGKey,
               belief: BeliefState,
               x: chex.Array,
               y: chex.Array):

        assert self.buffer_size >= len(x)
        x_, y_ = self.memory.update(x, y)

        if len(x_) < self.threshold:
            warnings.warn("There should be more data.", UserWarning)
            info = Info(False, -1, jnp.inf)
            return belief, info

        params = belief.params
        opt_state = belief.opt_state

        for _ in range(self.nepochs):
            loss, grads = self.value_and_grad_fn(params, x_, y_)
            updates, opt_state = self.optimizer.update(grads, opt_state)
            params = optax.apply_updates(params, updates)

        return BeliefState(params, opt_state), Info(loss)

    def sample_params(self,
                      key: chex.PRNGKey,
                      belief: BeliefState):
        return belief.params
