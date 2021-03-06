import chex
import jax.numpy as jnp
from jax import random, jit, vmap

import optax
import haiku as hk
from flax.core import frozen_dict
from matplotlib import pyplot as plt

from seql.agents.bfgs_agent import BFGSAgent
from seql.agents.blackjax_nuts_agent import BlackJaxNutsAgent
from seql.agents.ensemble_agent import EnsembleAgent
from seql.agents.sgd_agent import SGDAgent
from seql.agents.sgmcmc_sgld_agent import SGLDAgent
from seql.environments.base import make_mlp, make_regression_mlp_environment
from seql.experiments.experiment_utils import run_experiment
from seql.utils import mean_squared_error
from seql.experiments.plotting import colors

plt.style.use("seaborn-poster")

model_fn = None

def logprior_fn(params):

    return 0.

def loglikelihood_fn(params, x, y, model_fn):
    return -mean_squared_error(params, x, y, model_fn)

losses = []
subplot_idx = 1
i = 0

def callback_fn(agent, env, agent_name, **kwargs):
    global losses, subplot_idx
    t = kwargs["t"]
    if kwargs["t"] == 0:
        losses = []
    belief = kwargs["belief"]
    nfeatures = kwargs["nfeatures"]
    out = 1

    inputs = env.X_test.reshape((-1, nfeatures))
    outputs = env.y_test.reshape((-1, out))
    theta = agent.sample_params(random.PRNGKey(t*42), belief)
    preds = model_fn(theta, inputs)
    loss = jnp.mean(jnp.power(preds - outputs, 2))
    
    losses.append(loss)
    fig = kwargs["fig"]
    nrows, ncols  = kwargs["nrows"], kwargs["ncols"]

    if kwargs["t"] == 9:
        ax = fig.add_subplot(nrows,
                         ncols,
                         subplot_idx)
        subplot_idx += 1
        ax.plot(losses, color=colors[agent_name])
        ax.set_title(agent_name.upper())
        plt.tight_layout()
        plt.savefig("asas.png")
        losses = []


def initialize_params(agent_name, **kwargs):

    key = random.PRNGKey(233)
    def get_params(key):
        nfeatures = kwargs["nfeatures"]
        transformed = kwargs["transformed"]
        dummy_input = jnp.zeros([1, nfeatures])
        params = transformed.init(key, dummy_input)
        return params
    if agent_name == "ensemble":
        keys = random.split(key, 8)
        trainable = vmap(get_params)(keys)
        keys = random.split(random.PRNGKey(9), 8)
        baseline = trainable = vmap(get_params)(keys)
        params = frozen_dict.freeze(
            {"params": {"baseline": baseline,
                        "trainable": trainable
                        }
                })
    else: 

        params = get_params(key)
    return (params,)


def main():
    global model_fn
    key = random.PRNGKey(0)
    model_key, env_key, init_key, run_key = random.split(key, 4)
    ntrain = 20
    ntest = 20
    batch_size = 2
    obs_noise = 1.
    hidden_layer_sizes = [5, 5]
    nfeatures = 100
    ntargets = 1
    temperature = 1.

    net_fn = make_mlp(model_key,
                      nfeatures,
                      ntargets,
                      temperature,
                      hidden_layer_sizes)

    transformed = hk.without_apply_rng(hk.transform(net_fn))

    assert temperature > 0.0

    def forward(params: chex.Array, x: chex.Array):
        return transformed.apply(params, x) / temperature

    model_fn = jit(forward)

    env = lambda batch_size: make_regression_mlp_environment(env_key,
                                                             nfeatures,
                                                             ntargets,
                                                             ntrain,
                                                             ntest,
                                                             temperature=1.,
                                                             hidden_layer_sizes=hidden_layer_sizes,
                                                             train_batch_size=batch_size,
                                                             test_batch_size=batch_size,
                                                             )

    nsteps = 10

    buffer_size = ntrain

    optimizer = optax.adam(1e-1)

    nepochs = 6
    sgd = SGDAgent(loglikelihood_fn,
                   model_fn,
                   logprior_fn,
                   optimizer=optimizer,
                   obs_noise=obs_noise,
                   nepochs=nepochs,
                   buffer_size=buffer_size)


    nsamples, nwarmup = 500, 200
    nuts = BlackJaxNutsAgent(
        loglikelihood_fn,
        model_fn,
        logprior=logprior_fn,
        nsamples=nsamples,
        nwarmup=nwarmup,
        obs_noise=obs_noise,
        buffer_size=buffer_size)

    dt = 1e-5
    sgld = SGLDAgent(
        loglikelihood_fn,
        model_fn,
        logprior=logprior_fn,
        dt=dt,
        batch_size=batch_size,
        nsamples=nsamples,
        obs_noise=obs_noise,
        buffer_size=buffer_size)

    # tau = 1.
    # strength = obs_noise / tau

    bfgs = BFGSAgent(loglikelihood_fn,
                     model_fn,
                     logprior_fn,
                     obs_noise=obs_noise,
                     buffer_size=buffer_size)

    def energy_fn(params, x, y):
        logprob = loglikelihood_fn(params, x, y, model_fn)
        logprob += logprior_fn(params)
        return -logprob/len(x)

    nensemble =  8

    ensemble = EnsembleAgent(loglikelihood_fn,
                             model_fn,
                             nensemble,
                             logprior_fn,
                             nepochs,
                             optimizer=optimizer)

    agents = {
        #"nuts": nuts,
        "sgld": sgld,
        "sgd": sgd,
        "bfgs": bfgs,
        #"ensemble": ensemble,
    }

    nrows = len(agents)
    ncols = 1

    nsamples_input = 10
    nsamples_output = 10
    njoint = 10

    run_experiment(run_key,
                   agents,
                   env,
                   initialize_params,
                   batch_size,
                   ntrain,
                   nsteps,
                   nsamples_input,
                   nsamples_output,
                   njoint,
                   nrows,
                   ncols,
                   callback_fn=callback_fn,
                   obs_noise=obs_noise,
                   timesteps=list(range(nsteps)),
                   nfeatures=nfeatures,
                   transformed=transformed)


if __name__ == "__main__":
    main()
