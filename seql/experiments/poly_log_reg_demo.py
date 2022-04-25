import jax.numpy as jnp
from jax import random, tree_leaves, tree_map
from jax import nn

from matplotlib import pyplot as plt
import optax
from sklearn.preprocessing import PolynomialFeatures

from jsl.experimental.seql.agents.eekf_agent import EEKFAgent
from jsl.experimental.seql.agents.bfgs_agent import BFGSAgent
from jsl.experimental.seql.agents.blackjax_nuts_agent import BlackJaxNutsAgent
from jsl.experimental.seql.agents.lbfgs_agent import LBFGSAgent
from jsl.experimental.seql.agents.sgd_agent import SGDAgent
from jsl.experimental.seql.agents.sgmcmc_sgld_agent import SGLDAgent
from jsl.experimental.seql.environments.base import make_random_poly_classification_environment
from jsl.experimental.seql.experiments.experiment_utils import run_experiment
from jsl.experimental.seql.experiments.plotting import sort_data
from jsl.experimental.seql.utils import cross_entropy_loss
from jsl.nlds.base import NLDS


def fz(x): return x

def fx(w, x):
    return (x @ w)[None, ...]


def Rt(w, x): return (x @ w * (1 - x @ w))[None, None]


def model_fn(w, x):
    return nn.log_softmax(x @ w, axis=-1)


def logprior_fn(params, strength=0.2):
    leaves = tree_leaves(params)
    return -sum(tree_map(lambda x: jnp.sum(x ** 2), leaves)) * strength


def loglikelihood_fn(params, x, y, model_fn):
    logprobs = model_fn(params, x)
    return -cross_entropy_loss(y, logprobs)


def print_accuracy(logprobs, ytest):
    ytest_ = jnp.squeeze(ytest)
    predictions = jnp.where(logprobs > jnp.log(0.5), 1, 0)
    print("Accuracy: ", jnp.mean(jnp.argmax(predictions, axis=-1) == ytest_))


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

    belief = kwargs["belief"]

    poly = PolynomialFeatures(kwargs["degree"])
    *_, nfeatures = env.X_train.shape
    X = env.X_test.reshape((-1, nfeatures))
    X = jnp.vstack([env.X_train.reshape((-1, nfeatures)), X])
    min_x, max_x = jnp.min(X[:, 1]), jnp.max(X[:, 1])
    min_y, max_y = jnp.min(X[:, 2]), jnp.max(X[:, 2])

    # define the x and y scale
    x1grid = jnp.arange(min_x, max_x, 0.1)
    x2grid = jnp.arange(min_y, max_y, 0.1)

    # create all of the lines and rows of the grid
    xx, yy = jnp.meshgrid(x1grid, x2grid)

    # flatten each grid to a vector
    r1, r2 = xx.flatten(), yy.flatten()
    r1, r2 = r1.reshape((len(r1), 1)), r2.reshape((len(r2), 1))
    # horizontal stack vectors to create x1,x2 input for the model
    grid = jnp.hstack((r1, r2))
    x = poly.fit_transform(grid)

    grid_preds = agent.posterior_predictive_mean(random.PRNGKey(0),
                                    belief,
                                    x,
                                    10,
                                    5)

    # keep just the probabilities for class 0
    grid_preds = grid_preds[:, 0]
    # reshape the predictions back into a grid
    grid_preds = grid_preds.reshape(xx.shape)

    # plot the grid of x, y and z values as a surface
    c = ax.contourf(xx, yy, grid_preds, cmap='RdBu')
    plt.colorbar(c)

    if "title" in kwargs:
        ax.set_title(kwargs["title"], fontsize=32)
    else:
        ax.set_title("t={}".format(kwargs["t"]), fontsize=32)

    t = kwargs["t"]

    x, y = sort_data(env.X_test[:t + 1], env.y_test[:t + 1])
    nclasses = y.max()

    for cls in range(nclasses + 1):
        indices = jnp.argwhere(y == cls)

        # Plot training data
        ax.scatter(x[indices, 1],
                   x[indices, 2])
    plt.savefig("jakjs.png")


def initialize_params(agent_name, **kwargs):
    nfeatures = kwargs["nfeatures"]
    mu0 = random.normal(random.PRNGKey(0), (nfeatures, 2))
    if agent_name == "bayes" or agent_name == "eekf":
        mu0 = jnp.zeros((nfeatures, 2))
        Sigma0 = jnp.eye(nfeatures)
        initial_params = (mu0, Sigma0)
    else:
        initial_params = (mu0,)

    return initial_params


def main():
    key = random.PRNGKey(0)

    degree = 3
    ntrain, ntest = 100, 100
    batch_size = 10
    nsteps = 10
    nfeatures, nclasses = 2, 2

    env_key, experiment_key = random.split(key, 2)
    obs_noise = 1.
    env = lambda batch_size: make_random_poly_classification_environment(env_key,
                                                                         degree,
                                                                         ntrain,
                                                                         ntest,
                                                                         nfeatures=nfeatures,
                                                                         nclasses=nclasses,
                                                                         obs_noise=obs_noise,
                                                                         train_batch_size=batch_size,
                                                                         test_batch_size=batch_size,
                                                                         shuffle=False)

    buffer_size = ntrain

    input_dim = 10
    Pt = jnp.eye(input_dim) * 0.0
    P0 = jnp.eye(input_dim) * 2.0
    mu0 = jnp.zeros((input_dim,))
    nlds = NLDS(fz, fx, Pt, Rt, mu0, P0)
    is_classifier = True

    eekf = EEKFAgent(nlds,
                     model_fn,
                     obs_noise,
                     is_classifier=is_classifier)

    optimizer = optax.adam(1e-2)

    #tau = 1.
    #strength = obs_noise / tau

    nepochs = 20
    sgd = SGDAgent(loglikelihood_fn,
    model_fn,
    logprior_fn,
    nepochs=nepochs,
    buffer_size=buffer_size,
    obs_noise=obs_noise,
    optimizer=optimizer,
    is_classifier=is_classifier)

    batch_sgd = SGDAgent(loglikelihood_fn,
    model_fn,
    logprior_fn,
    nepochs=nepochs * nsteps,
    buffer_size=buffer_size,
    obs_noise=obs_noise,
    optimizer=optimizer,
    is_classifier=is_classifier)



    nsamples, nwarmup = 500, 300

    nuts = BlackJaxNutsAgent(loglikelihood_fn,
    model_fn,
    nsamples,
    nwarmup,
    logprior_fn,
    obs_noise=obs_noise,
    buffer_size=buffer_size,
    is_classifier=is_classifier)
    batch_nuts = BlackJaxNutsAgent(loglikelihood_fn,
    model_fn,
    nsamples * nsteps,
    nwarmup,
    logprior_fn,
    obs_noise=obs_noise,
    buffer_size=buffer_size,
    is_classifier=is_classifier)


    dt = 1e-5

    sgld = SGLDAgent(loglikelihood_fn,
                     model_fn,
                     dt,
                     batch_size,
                     nsamples,
                     logprior_fn,
                     buffer_size=buffer_size,
                     obs_noise=obs_noise,
                     is_classifier=is_classifier)
    
    bfgs = BFGSAgent(loglikelihood_fn,
                     model_fn,
                     logprior_fn,
                     buffer_size=buffer_size,
                     obs_noise=obs_noise)

    lbfgs = LBFGSAgent(loglikelihood_fn,
    model_fn,
    logprior_fn,
    buffer_size=buffer_size,
    obs_noise=obs_noise)


    agents = {
        #"eekf": eekf,
        "sgld": sgld,
        #"scikit": scikit_agent,
        "sgd": sgd,
        "bfgs": bfgs,
        "lbfgs": lbfgs,
        "nuts":nuts,
    }

    batch_agents = {
        "eekf": eekf,
        "sgd": batch_sgd,
        "nuts": batch_nuts,
        "sgld": sgld,
        "bfgs": bfgs,
        "lbfgs": lbfgs,
    }

    timesteps = list(range(nsteps))
    nrows = len(agents)
    ncols = len(timesteps)
    njoint = 10
    nsamples_input, nsamples_output = 1, 1
    run_experiment(experiment_key,
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
                   nfeatures=input_dim,
                   obs_noise=obs_noise,
                   batch_agents=batch_agents,
                   timesteps=timesteps,
                   degree=degree)


if __name__ == "__main__":
    main()
