from jax.config import config
config.update("jax_debug_nans", True)

import jax.numpy as jnp
from jax import lax, random

import distrax

import optax

import chex
from typing import NamedTuple, Callable, Optional, Tuple

from seql.agents.base import Agent
from seql.environments.sequential_data_env import SequentialDataEnvironment
from seql.metrics.regression import gaussian_sample_kl


Belief = NamedTuple


def onehot(labels: chex.Array,
           num_classes: int,
           on_value: float = 1.0,
           off_value: float = 0.0) -> chex.Array:
    # https://github.com/google/flax/blob/main/examples/imagenet/train.py
    x = (labels[..., None] == jnp.arange(num_classes).reshape((1,) * labels.ndim + (-1,)))
    x = lax.select(x, jnp.full(x.shape, on_value), jnp.full(x.shape, off_value))
    return x.astype(jnp.float32)


def binary_cross_entropy(labels: chex.Array,
                         logprobs: chex.Array) -> float:
    probs = jnp.exp(logprobs)
    loss = labels * logprobs + (1 - labels) * jnp.log(1 - probs)
    return -jnp.mean(loss)


def cross_entropy_loss(labels: chex.Array,
                       logprobs: chex.Array,
                       scale: chex.Array = None) -> float:
    nclasses = logprobs.shape[-1]
    if nclasses == 1:
        return binary_cross_entropy(labels, logprobs)
    one_hot_labels = onehot(jnp.squeeze(labels, axis=-1),
                            num_classes=nclasses)
    xentropy = optax.softmax_cross_entropy(logits=logprobs, labels=one_hot_labels)
    return jnp.mean(xentropy)


def categorical_log_likelihood(logprobs: chex.Array,
                               labels: chex.Array) -> float:
    """Computes joint log likelihood based on probs and labels."""
    num_data, nclasses = logprobs.shape
    assert len(labels) == num_data
    one_hot_labels = onehot(labels, num_classes=nclasses)
    assigned_probs = logprobs * one_hot_labels
    return jnp.sum(jnp.log(assigned_probs))


def gaussian_log_likelihood(mu: chex.Array,
                            cov: chex.Array,
                            predictions) -> float:
    return jnp.sum(distrax.MultivariateNormalFullCovariance(jnp.squeeze(mu), cov).log_prob(predictions))


def mean_squared_error(params: chex.ArrayTree,
                       inputs: chex.Array,
                       outputs: chex.Array,
                       model_fn: Callable) -> float:
    predictions = model_fn(params, inputs)
    return jnp.sum(jnp.power(predictions - outputs, 2))


# Main function
def train(key: chex.PRNGKey,
          initial_belief_state: Belief,
          agent: Agent,
          env: SequentialDataEnvironment,
          nsteps: int,
          nsamples_input: int,
          nsamples_output: int,
          njoint: int,
          callback: Optional[Callable] = None) -> Tuple[Belief, chex.Array]:

    rewards = []
    belief = initial_belief_state
    keys = random.split(key, nsteps)

    for t, rng_key in enumerate(keys):
        X_train, Y_train = env.get_data(t)
        
        update_key, joint_key = random.split(rng_key, 2)

        belief, info = agent.update(update_key,
                                    belief,
                                    X_train,
                                    Y_train)

        
        '''kl_div = env.evaluate_quality(joint_key, agent, belief, 2)
        print(kl_div)'''
        if callback:
            if not isinstance(callback, list):
                callback_list = [callback]
            else:
                callback_list = callback

            for f in callback_list:
                f(
                    agent=agent,
                    env=env,
                    belief=belief,
                    info=info,
                    X_train=X_train,
                    Y_train=Y_train,
                    #kl=kl_div,
                    t=t
                )

    

    return belief, rewards

