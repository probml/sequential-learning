import jax.numpy as jnp
from jax import random

import optax
from jaxopt import ScipyMinimize

from functools import partial
from matplotlib import pyplot as plt

from jsl.experimental.seql.agents.bayesian_lin_reg_agent import BayesianReg
from jsl.experimental.seql.agents.bfgs_agent import BFGSAgent
from jsl.experimental.seql.agents.blackjax_nuts_agent import BlackJaxNutsAgent
from jsl.experimental.seql.agents.kf_agent import KalmanFilterRegAgent
from jsl.experimental.seql.agents.laplace_agent import LaplaceAgent
from jsl.experimental.seql.agents.lbfgs_agent import LBFGSAgent
from jsl.experimental.seql.agents.sgd_agent import SGDAgent
from jsl.experimental.seql.agents.sgmcmc_sgld_agent import SGLDAgent
from jsl.experimental.seql.environments.base import make_evenly_spaced_x_sampler, \
    make_random_poly_regression_environment
from jsl.experimental.seql.experiments.experiment_utils import run_experiment
from jsl.experimental.seql.experiments.plotting import plot_regression_posterior_predictive
from jsl.experimental.seql.utils import mean_squared_error, train


plt.style.use("seaborn-poster")


def model_fn(w, x):
    return x @ w


def logprior_fn(params):
    return 0.


def negative_mean_square_error(params, inputs, outputs, model_fn, strength=0.):
    return -penalized_objective_fn(params, inputs, outputs, model_fn, strength=0.)


def penalized_objective_fn(params, inputs, outputs, model_fn, strength=0.):
    return mean_squared_error(params, inputs, outputs, model_fn) + strength * jnp.sum(params ** 2)


def energy_fn(params, data, model_fn, strength=0.):
    return mean_squared_error(params, *data, model_fn) + strength * jnp.sum(params ** 2)


def callback_fn(agent, env, agent_name, **kwargs):
    if "subplot_idx" not in kwargs and kwargs["t"] not in kwargs["timesteps"]:
        return
    elif "subplot_idx" not in kwargs:
        subplot_idx = kwargs["timesteps"].index(kwargs["t"]) + kwargs["idx"] * kwargs["ncols"] + 1
    else:
        subplot_idx = kwargs["subplot_idx"]

    ax = kwargs["fig"].add_subplot(kwargs["nrows"],
                                   kwargs["ncols"],
                                   subplot_idx)

    outs = agent.posterior_predictive_mean_and_var(random.PRNGKey(0),
                                                   kwargs["belief"],
                                                   env.X_test[kwargs["t"]])

    plot_regression_posterior_predictive(ax,
                                         outs,
                                         env,
                                         agent_name,
                                         t=kwargs["t"])
    if "title" in kwargs:
        ax.set_title(kwargs["title"], fontsize=32)
    else:
        ax.set_title("t={}".format(kwargs["t"]), fontsize=32)
    print(agent_name)
    plt.tight_layout()
    plt.savefig("jaks.png")
    plt.show()


def initialize_params(agent_name, **kwargs):
    nfeatures = kwargs["degree"] + 1
    mu0 = jnp.zeros((nfeatures, 1))
    if agent_name in ["exact bayes", "kf"]:
        mu0 = jnp.zeros((nfeatures, 1))
        Sigma0 = jnp.eye(nfeatures)
        initial_params = (mu0, Sigma0)
    else:
        initial_params = (mu0,)

    return initial_params


def sweep(agents, env, train_batch_size, ntrain, nsteps, figsize=(56, 48), **init_kwargs):
    batch_agents_included = "batch_agents" in init_kwargs

    nrows = len(agents)
    ncols = len(init_kwargs["timesteps"]) + int(batch_agents_included)
    fig, big_axes = plt.subplots(nrows=nrows,
                                 ncols=1,
                                 figsize=figsize)

    for idx, (big_ax, (agent_name, agent)) in enumerate(zip(big_axes, agents.items())):

        big_ax.set_title(agent_name.upper(), fontsize=36, y=1.2)
        # Turn off axis lines and ticks of the big subplot 
        # obs alpha is 0 in RGBA string!
        big_ax.tick_params(labelcolor=(1., 1., 1., 0.0),
                           top='off',
                           bottom='off',
                           left='off',
                           right='off')
        # removes the white frame
        big_ax._frameon = False

        params = initialize_params(agent_name, **init_kwargs)
        belief = agent.init_state(*params)

        partial_callback = lambda **kwargs: callback_fn(agent,
                                                        env(train_batch_size),
                                                        agent_name,
                                                        fig=fig,
                                                        nrows=nrows,
                                                        ncols=ncols,
                                                        idx=idx,
                                                        **init_kwargs,
                                                        **kwargs)

        train(belief, agent, env(train_batch_size),
              nsteps=nsteps, callback=partial_callback)

        if batch_agents_included:
            batch_agent = init_kwargs["batch_agents"][agent_name]
            partial_callback = lambda **kwargs: callback_fn(agent,
                                                            env(ntrain),
                                                            agent_name,
                                                            fig=fig,
                                                            nrows=nrows,
                                                            ncols=ncols,
                                                            idx=idx,
                                                            title="Batch Agent",
                                                            subplot_idx=(idx + 1) * ncols,
                                                            **init_kwargs,
                                                            **kwargs)
            train(belief, batch_agent, env(ntrain),
                  nsteps=1, callback=partial_callback)
    plt.savefig("ajsk.png")


def main():
    key = random.PRNGKey(0)

    min_val, max_val = -3, 3
    x_test_generator = make_evenly_spaced_x_sampler(max_val,
                                                    use_bias=False,
                                                    min_val=min_val)

    degree = 3
    ntrain = 50
    ntest = 50
    batch_size = 5
    obs_noise = 1.

    env_key, nuts_key, sgld_key, run_key = random.split(key, 4)
    env = lambda batch_size: make_random_poly_regression_environment(env_key,
                                                                     degree,
                                                                     ntrain,
                                                                     ntest,
                                                                     obs_noise=obs_noise,
                                                                     train_batch_size=batch_size,
                                                                     test_batch_size=batch_size,
                                                                     x_test_generator=x_test_generator)

    nsteps = 10

    buffer_size = ntrain

    kf = KalmanFilterRegAgent(obs_noise=obs_noise)

    bayes = BayesianReg(buffer_size=buffer_size,
                        obs_noise=obs_noise)
    batch_bayes = BayesianReg(buffer_size=ntrain,
                              obs_noise=obs_noise)

    optimizer = optax.adam(1e-1)

    nepochs = 4
    sgd = SGDAgent(mean_squared_error,
                   model_fn,
                   optimizer=optimizer,
                   obs_noise=obs_noise,
                   nepochs=nepochs,
                   buffer_size=buffer_size)

    batch_sgd = SGDAgent(mean_squared_error,
                         model_fn,
                         optimizer=optimizer,
                         obs_noise=obs_noise,
                         buffer_size=buffer_size,
                         nepochs=nepochs * nsteps)

    nsamples, nwarmup = 200, 100
    nuts = BlackJaxNutsAgent(
        negative_mean_square_error,
        model_fn,
        nsamples=nsamples,
        nwarmup=nwarmup,
        obs_noise=obs_noise,
        buffer_size=buffer_size)

    batch_nuts = BlackJaxNutsAgent(
        negative_mean_square_error,
        model_fn,
        nsamples=nsamples * nsteps,
        nwarmup=nwarmup,
        obs_noise=obs_noise,
        buffer_size=buffer_size)

    partial_logprob_fn = partial(negative_mean_square_error,
                                 model_fn=model_fn)
    dt = 1e-4
    sgld = SGLDAgent(
        partial_logprob_fn,
        logprior_fn,
        model_fn,
        dt=dt,
        batch_size=batch_size,
        nsamples=nsamples,
        obs_noise=obs_noise,
        buffer_size=buffer_size)

    dt = 1e-5
    batch_sgld = SGLDAgent(
        partial_logprob_fn,
        logprior_fn,
        model_fn,
        dt=dt,
        batch_size=batch_size,
        nsamples=nsamples * nsteps,
        obs_noise=obs_noise,
        buffer_size=buffer_size)

    tau = 1.
    strength = obs_noise / tau
    partial_objective_fn = partial(penalized_objective_fn, strength=strength)

    bfgs = BFGSAgent(partial_objective_fn,
                     obs_noise=obs_noise,
                     buffer_size=buffer_size)

    lbfgs = LBFGSAgent(partial_objective_fn,
                       obs_noise=obs_noise,
                       history_size=buffer_size)

    energy_fn = partial(partial_objective_fn, model_fn=model_fn)
    solver = ScipyMinimize(fun=energy_fn, method="BFGS")
    laplace = LaplaceAgent(solver,
                           energy_fn,
                           model_fn,
                           obs_noise=obs_noise,
                           buffer_size=buffer_size)

    agents = {
        "sgld": sgld,
        "kf": kf,
        "exact bayes": bayes,
        "sgd": sgd,
        "laplace": laplace,
        "bfgs": bfgs,
        "lbfgs": lbfgs,
        "nuts": nuts,
    }

    batch_agents = {
        "kf": kf,
        "exact bayes": batch_bayes,
        "sgd": batch_sgd,
        "laplace": laplace,
        "bfgs": bfgs,
        "lbfgs": lbfgs,
        "nuts": batch_nuts,
        "sgld": batch_sgld,
    }

    timesteps = list(range(nsteps))

    nrows = len(agents)
    ncols = len(timesteps) + 1

    run_experiment(run_key,
                   agents,
                   env,
                   initialize_params,
                   batch_size,
                   ntrain,
                   nsteps,
                   10, 10,
                   nrows,
                   ncols,
                   callback_fn=callback_fn,
                   degree=degree,
                   obs_noise=obs_noise,
                   timesteps=timesteps,
                   batch_agents=batch_agents
                   )

    env = lambda _: make_random_poly_regression_environment(env_key,
                                                            degree,
                                                            ntrain,
                                                            ntest,
                                                            obs_noise=obs_noise,
                                                            x_test_generator=x_test_generator)

    timesteps = list([1, 2, 5, 9, 19, 39])
    ncols = len(timesteps)
    run_experiment(run_key,
                   agents,
                   env,
                   initialize_params,
                   batch_size,
                   ntrain,
                   ntrain,
                   10, 10,
                   nrows,
                   ncols,
                   callback_fn=callback_fn,
                   degree=degree,
                   obs_noise=obs_noise,
                   timesteps=timesteps)


if __name__ == "__main__":
    main()