import jax.numpy as jnp
from jax import random, jit, nn, vmap

import haiku as hk
import distrax
import neural_tangents as nt

import chex
from typing import Callable, List, Optional, Tuple, Union

from sklearn.preprocessing import PolynomialFeatures

from seql.environments.sequential_classification_env import SequentialClassificationEnvironment
from seql.environments.sequential_regression_env import SequentialRegressionEnvironment
from seql.environments.sequential_torch_env import SequentialTorchEnvironment
from seql.experiments.base import PriorKnowledge


"""Specific neural tangent kernels."""

import dataclasses

from neural_tangents import stax
from neural_tangents._src.utils import typing as nt_types
import numpy as np
import typing_extensions


class KernelCtor(typing_extensions.Protocol):
  """Interface for generating a kernel for a given input dimension."""

  def __call__(self, input_dim: int) -> nt_types.AnalyticKernelFn:
    """Generates a kernel for a given input dimension."""


@dataclasses.dataclass
class MLPKernelCtor(KernelCtor):
  """Generates a GP kernel corresponding to an infinitely-wide MLP."""
  num_hidden_layers: int
  activation: nt_types.InternalLayer

  def __post_init__(self):
    assert self.num_hidden_layers >= 1, 'Must have at least one hidden layer.'

  def __call__(self, input_dim: int = 1) -> nt_types.AnalyticKernelFn:
    """Generates a kernel for a given input dimension."""
    limit_width = 50  # Implementation detail of neural_testbed, unused.
    layers = [
        stax.Dense(limit_width, W_std=1, b_std=1 / np.sqrt(input_dim))
    ]
    for _ in range(self.num_hidden_layers - 1):
      layers.append(self.activation)
      layers.append(stax.Dense(limit_width, W_std=1, b_std=0))
    layers.append(self.activation)
    layers.append(stax.Dense(1, W_std=1, b_std=0))
    _, _, kernel = stax.serial(*layers)
    return kernel


def make_benchmark_kernel(input_dim: int = 1) -> nt_types.AnalyticKernelFn:
  """Creates the benchmark kernel used in leaderboard = 2-layer ReLU."""
  kernel_ctor = MLPKernelCtor(num_hidden_layers=2, activation=stax.Relu())
  return kernel_ctor(input_dim)


def make_linear_kernel(input_dim: int = 1) -> nt_types.AnalyticKernelFn:
  """Generate a linear GP kernel for testing putposes."""
  layers = [
      stax.Dense(1, W_std=1, b_std=1 / np.sqrt(input_dim)),
  ]
  _, _, kernel = stax.serial(*layers)
  return kernel

def make_gaussian_sampler(loc: Union[chex.Array, float],
                          scale: Union[chex.Array, float]):
    def gaussian_sampler(key: chex.PRNGKey, shape: Tuple) -> chex.Array:
        return loc + scale * random.normal(key, shape)

    return gaussian_sampler


def make_evenly_spaced_x_sampler(max_val: float, use_bias: bool = True, min_val: float = 0) -> Callable:
    def eveny_spaced_x_sampler(key: chex.PRNGKey, shape: Tuple) -> chex.Array:
        if len(shape) == 1:
            shape = (shape[0], 1)
        nsamples, nfeatures = shape
        assert nfeatures == 1 or nfeatures == 2

        if nfeatures == 1:
            X = jnp.linspace(min_val, max_val, nsamples)
            if use_bias:
                X = jnp.c_[jnp.ones(nsamples), X]
            else:
                X = X.reshape((-1, 1))
        else:
            step_size = (max_val - min_val) / float(nsamples)
            # define the x and y scale
            x = jnp.arange(min_val, max_val, step_size)
            y = jnp.arange(min_val, max_val, step_size)

            # create all of the lines and rows of the grid
            xx, yy = jnp.meshgrid(x, y)

            # flatten each grid to a vector
            r1, r2 = xx.flatten(), yy.flatten()
            r1, r2 = r1.reshape((len(r1), 1)), r2.reshape((len(r2), 1))
            # horizontal stack vectors to create x1,x2 input for the model
            X = jnp.hstack((r1, r2))
        return X

    return eveny_spaced_x_sampler


def make_bimodel_sampler(mixing_parameter: float,
                         means: List[float],
                         variances: List[float]):
    mu1, mu2 = means
    sigma1, sigma2 = variances

    def check_unimodal():
        d = jnp.abs(mu1 - mu2) / 2 * jnp.sqrt(mu1 * mu2)
        lhs = jnp.abs(jnp.log(1 - mixing_parameter) - jnp.log(mixing_parameter))
        rhs = 2 * jnp.log(d - jnp.sqrt(d ** 2 - 1)) + 2 * d * jnp.sqrt(d ** 2 - 1)
        return lhs >= rhs

    is_unimodal = check_unimodal()
    if is_unimodal:
        raise TypeError("The mixture is unimodal.")

    def bimodel_sampler(key: chex.PRNGKey, shape: Tuple) -> chex.Array:
        nsamples, nfeatures = shape
        n1 = int(nsamples * mixing_parameter)
        n2 = int(nsamples * (1 - mixing_parameter))

        x1_key, x2_key = random.split(key)
        x1 = random.normal(x1_key, (n1, nfeatures)) * sigma1 + mu1
        x2 = random.normal(x2_key, (n2, nfeatures)) * sigma2 + mu2
        return jnp.vstack([x1, x2])

    return bimodel_sampler


def make_mixture_of_gaussians_sampler(loc: chex.Array,
                                      scale: chex.Array,
                                      probs: Optional[chex.Array]=None):
    assert len(loc) == len(scale)
    ngaussians = loc.size
    if probs is None:
        probs = jnp.ones((ngaussians,)) / ngaussians
    probs = probs
    mixture_dist = distrax.Categorical(probs=probs)
    components_dist = distrax.Normal(loc=loc, scale=scale)
    dist = distrax.MixtureSameFamily(mixture_dist,
                                     components_dist)

    def mixture_of_gaussians_sampler(key: chex.PRNGKey,
                                     shape: Tuple) -> chex.Array:
        return dist.sample(seed=key, sample_shape=shape)

    return mixture_of_gaussians_sampler


def make_sin_wave_regression_environment(key: chex.PRNGKey,
                                         ntrain: int,
                                         ntest: int,
                                         obs_noise: float = 0.01,
                                         train_batch_size: int = 1,
                                         test_batch_size: int = 1,
                                         x_train_generator: Callable = random.normal,
                                         x_test_generator: Callable = random.normal,
                                         bias: bool = True,
                                         shuffle: bool = False):
    train_key, test_key, noise_key, y_key, env_key = random.split(key, 4)
    X_train = x_train_generator(train_key, (ntrain, 1))
    X_test = x_test_generator(test_key, (ntest, 1))

    ntrain = len(X_train)
    ntest = len(X_test)

    X = jnp.vstack([X_train, X_test])

    if obs_noise > 0.0:
        nsamples = ntrain + ntest
        noise = random.normal(noise_key, (nsamples, 1)) * obs_noise
    else:
        noise = 1e-8

    Y = jnp.sin(X) + noise

    if bias:
        X = jnp.hstack([jnp.ones((len(X), 1)), X])

    X_train = X[:ntrain]
    X_test = X[ntrain:]

    y_train = Y[:ntrain]
    y_test = Y[ntrain:]

    if shuffle:
        env_key, key = random.split(key)
    else:
        env_key = None

    def true_model(x):
        return jnp.sin(x)

    env = SequentialRegressionEnvironment(X_train,
                                          y_train,
                                          X_test,
                                          y_test,
                                          true_model,
                                          train_batch_size,
                                          test_batch_size,
                                          classification=False,
                                          obs_noise=obs_noise,
                                          key=env_key)
    return env


def make_random_poly_classification_environment(key: chex.PRNGKey,
                                                degree: int,
                                                ntrain: int,
                                                ntest: int,
                                                nclasses: int = 2,
                                                nfeatures: int = 1,
                                                obs_noise: float = 0.01,
                                                train_batch_size: int = 1,
                                                test_batch_size: int = 1,
                                                x_train_generator: Callable = random.normal,
                                                x_test_generator: Callable = random.normal,
                                                shuffle: bool = False):
    train_key, test_key, env_key, output_key = random.split(key, 4)

    X_train = x_train_generator(train_key, (ntrain, nfeatures))
    X_test = x_test_generator(test_key, (ntest, nfeatures))

    ntrain = len(X_train)
    ntest = len(X_test)

    X = jnp.vstack([X_train, X_test])
    poly = PolynomialFeatures(degree)
    Phi = jnp.array(poly.fit_transform(X), dtype=jnp.float32)
    
    D = Phi.shape[-1]
    w = random.normal(key, (D, nclasses)) + 5
    if obs_noise > 0.0:
        nsamples = ntrain + ntest
        noise = random.normal(key, (nsamples, nclasses)) * obs_noise
    else:
        noise = 0.
    logprobs = nn.softmax(Phi @ w + noise)

    # Generate data.
    def sample_output(probs: chex.Array, key: chex.PRNGKey) -> chex.Array:
        return random.choice(key, nclasses, shape=(1,), p=probs)
    
    keys = random.split(output_key, ntrain + ntest)
    Y = vmap(sample_output)(logprobs, keys)

    X_train = Phi[:ntrain]
    X_test = Phi[ntrain:]
    y_train = Y[:ntrain]
    y_test = Y[ntrain:]

    if shuffle:
        env_key, key = random.split(key)
    else:
        env_key = None

    def true_model(x):
        return nn.log_softmax(x @ w)

    env = SequentialClassificationEnvironment(X_train,
                                              y_train,
                                              X_test,
                                              y_test,
                                              true_model,
                                              train_batch_size,
                                              logprobs=logprobs,
                                              key=env_key)

    prior_knowledge = PriorKnowledge(nfeatures,
                                     ntrain,
                                     1,
                                     nclasses,
                                     None,
                                     obs_noise,
                                     1.,
                                     )

    return prior_knowledge, env


def make_random_poly_regression_environment(key: chex.PRNGKey,
                                            degree: int,
                                            ntrain: int,
                                            ntest: int,
                                            nout: int = 1,
                                            obs_noise: float = 0.01,
                                            train_batch_size: int = 1,
                                            test_batch_size: int = 1,
                                            kernel_ridge: float = 1e-6,
                                            x_train_generator: Callable = random.normal,
                                            x_test_generator: Callable = random.normal,
                                            ntk: bool = False,
                                            shuffle: bool = False):
    
    train_key, test_key, y_key, noise_key = random.split(key, 4)

    X_train = x_train_generator(train_key, (ntrain, 1))
    X_test = x_test_generator(test_key, (ntest, 1))

    ntrain = len(X_train)
    ntest = len(X_test)

    X = jnp.vstack([X_train, X_test])

    poly = PolynomialFeatures(degree)
    Phi = jnp.array(poly.fit_transform(X), dtype=jnp.float32)

    N = ntrain + ntest
    get_kernel = 'ntk' if ntk else 'nngp'
    input_dim = X.shape[-1]
    kernel_fn = make_linear_kernel(input_dim)
    kernel = kernel_fn(X, x2=None, get=get_kernel)
    kernel += kernel_ridge * jnp.eye(len(kernel))
    mean = jnp.zeros((N,), dtype=jnp.float32)
    y_function = random.multivariate_normal(y_key, mean, kernel)
    print(y_function)
    chex.assert_shape(y_function[:ntrain], [ntrain,])

    # Form the training data
    y_noise = random.normal(noise_key, [ntrain, 1]) * obs_noise
    y_train = y_function[:ntrain, None] + y_noise

    X_train = Phi[:ntrain]
    X_test = Phi[ntrain:]


    # Form the posterior prediction at cached test data
    predict_fn = nt.predict.gradient_descent_mse_ensemble(
        kernel_fn, X_train, y_train, diag_reg=(obs_noise))
    _test_mean, _test_cov = predict_fn(
        t=None, x_test=X_test, get='nngp', compute_cov=True)
    _test_cov += kernel_ridge * jnp.eye(ntest)

    chex.assert_shape(_test_mean, [ntest, 1])
    chex.assert_shape(_test_cov, [ntest, ntest])
    

    if shuffle:
        train_key, test_key = random.split(key)
        train_indices = random.permutation(train_key,
                                           jnp.arange(ntrain))
        test_indices = random.permutation(test_key,
                                          jnp.arange(ntest))

        X_train = X_train[train_indices]
        y_train = y_train[train_indices]

        X_test = X_test[test_indices]

    env = SequentialRegressionEnvironment(X_train,
                                          y_train,
                                          X_test,
                                          _test_mean,
                                          _test_cov,
                                          None,
                                          y_function,
                                          train_batch_size,
                                          obs_noise=obs_noise)

    return env


def make_random_linear_classification_environment(key: chex.PRNGKey,
                                                  nfeatures: int,
                                                  ntrain: int,
                                                  ntest: int,
                                                  ntargets: int = 2,
                                                  bias: float = 0.0,
                                                  obs_noise: float = 0.0,
                                                  train_batch_size: int = 1,
                                                  test_batch_size: int = 1,
                                                  x_train_generator: Callable = random.normal,
                                                  x_test_generator: Callable = random.normal,
                                                  shuffle: bool = False):
    # https://github.com/scikit-learn/scikit-learn/blob/7e1e6d09bcc2eaeba98f7e737aac2ac782f0e5f1/sklearn/datasets/_samples_generator.py#L506

    # Randomly generate a well conditioned input set
    train_key, test_key, w_key, noise_key, env_key = random.split(key, 5)

    X_train = x_train_generator(train_key, (ntrain, nfeatures))
    X_test = x_test_generator(test_key, (ntest, nfeatures))

    ntrain = len(X_train)
    ntest = len(X_test)

    X = jnp.vstack([X_train, X_test])

    # Generate a ground truth model with only n_informative features being non
    # zeros (the other features are not correlated to y and should be ignored
    # by a sparsifying regularizers such as L1 or elastic net)
    w = 100 * random.normal(w_key, (nfeatures, ntargets))
    logprobs = nn.log_softmax(jnp.dot(X, w) + bias, axis=-1)
    Y = jnp.argmax(logprobs, axis=-1).reshape((-1, 1))

    if bias:
        X = jnp.hstack([jnp.ones((len(X), 1)), X])

    # Add noise
    if obs_noise > 0.0:
        Y += obs_noise * random.normal(noise_key, size=Y.shape)

    X_train = X[:ntrain]
    X_test = X[ntrain:]
    y_train = Y[:ntrain]
    y_test = Y[ntrain:]

    if shuffle:
        env_key, key = random.split(key)
    else:
        env_key = None

    def true_model(x):
        return nn.log_softmax(x @ w)

    env = SequentialClassificationEnvironment(X_train,
                                              y_train,
                                              X_test,
                                              y_test,
                                              true_model,
                                              train_batch_size,
                                              test_batch_size,
                                              logprobs=logprobs,
                                              key=env_key)
    return env


def make_random_linear_regression_environment(key: chex.PRNGKey,
                                              nfeatures: int,
                                              ntargets: int,
                                              ntrain: int,
                                              ntest: int,
                                              bias: float = 0.0,
                                              obs_noise: float = 0.0,
                                              train_batch_size: int = 1,
                                              test_batch_size: int = 1,
                                              x_train_generator: Callable = random.normal,
                                              x_test_generator: Callable = random.normal,
                                              shuffle: bool = False):
    # https://github.com/scikit-learn/scikit-learn/blob/7e1e6d09bcc2eaeba98f7e737aac2ac782f0e5f1/sklearn/datasets/_samples_generator.py#L506

    nsamples = ntrain + ntest
    # Randomly generate a well conditioned input set
    train_key, test_key, w_key, noise_key, env_key = random.split(key, 5)

    X_train = x_train_generator(train_key, (ntrain, nfeatures))
    X_test = x_test_generator(test_key, (ntest, nfeatures))

    ntrain = len(X_train)
    ntest = len(X_test)

    X = jnp.vstack([X_train, X_test])

    # Generate a ground truth model with only n_informative features being non
    # zeros (the other features are not correlated to y and should be ignored
    # by a sparsifying regularizers such as L1 or elastic net)
    w = 100 * random.normal(w_key, (nfeatures, ntargets))

    Y = jnp.dot(X, w) + bias
    if bias:
        X = jnp.hstack([jnp.ones((len(X), 1)), X])

    # Add noise
    if obs_noise > 0.0:
        Y += obs_noise * random.normal(noise_key, size=Y.shape)

    X_train = X[:ntrain]
    X_test = X[ntrain:]
    y_train = Y[:ntrain]
    y_test = Y[ntrain:]

    if shuffle:
        env_key, key = random.split(key)
    else:
        env_key = None

    def true_model(x):
        return x @ w

    env = SequentialRegressionEnvironment(X_train,
                                          y_train,
                                          X_test,
                                          y_test,
                                          true_model,
                                          train_batch_size,
                                          test_batch_size,
                                          classification=False,
                                          key=env_key)
    return env


def make_mlp(key: chex.PRNGKey,
             nfeatures: int,
             ntargets: int,
             temperature: float,
             hidden_layer_sizes: List[int]):
    assert hidden_layer_sizes != []

    # Generating the logit function
    def net_fn(x: chex.Array):
        """Defining the generative model MLP."""
        hidden = hidden_layer_sizes[0]
        y = hk.Linear(
            output_size=hidden,
            b_init=hk.initializers.RandomNormal(1. / jnp.sqrt(nfeatures)),
        )(x)
        y = nn.relu(y)

        for hidden in hidden_layer_sizes[1:]:
            y = hk.Linear(hidden)(y)
            y = nn.relu(y)
        return hk.Linear(ntargets)(y)

    return net_fn


def make_classification_mlp_environment(key: chex.PRNGKey,
                                        nfeatures: int,
                                        ntargets: int,
                                        ntrain: int,
                                        ntest: int,
                                        temperature: float,
                                        hidden_layer_sizes: List[int],
                                        train_batch_size: int = 1,
                                        test_batch_size: int = 1,
                                        x_train_generator: Callable = random.normal,
                                        x_test_generator: Callable = random.normal,
                                        shuffle: bool = False):
    train_key, test_key, y_key, env_key = random.split(key, 4)
    net_fn = make_mlp(y_key,
                      nfeatures,
                      ntargets,
                      temperature,
                      hidden_layer_sizes)

    transformed = hk.without_apply_rng(hk.transform(net_fn))

    dummy_input = jnp.zeros([1, nfeatures])
    params = transformed.init(key, dummy_input)

    assert temperature > 0.0

    def forward(x: chex.Array):
        return transformed.apply(params, x) / temperature

    y_predictor = jit(forward)

    # Generates training data for given problem
    X_train = x_train_generator(train_key, (ntrain, nfeatures))
    X_test = x_test_generator(test_key, (ntest, nfeatures))

    ntrain = len(X_train)
    ntest = len(X_test)

    X = jnp.vstack([X_train, X_test])

    # Generate environment function across x_train
    train_logits = y_predictor(X)  # [n_train, n_class]
    train_probs = nn.softmax(train_logits, axis=-1)

    # Generate training data.
    def sample_output(probs: chex.Array, key: chex.PRNGKey) -> chex.Array:
        return random.choice(key, ntargets, shape=(1,), p=probs)

    nsamples = ntrain + ntest
    y_keys = random.split(y_key, nsamples)

    Y = vmap(sample_output)(train_probs, y_keys)

    X_train = X[:ntrain]
    X_test = X[ntrain:]
    y_train = Y[:ntrain]

    if shuffle:
        env_key, key = random.split(key)
    else:
        env_key = None

    env = SequentialClassificationEnvironment(X_train,
                                              y_train,
                                              X_test,
                                              y_predictor,
                                              train_batch_size,
                                              logprobs=train_logits,
                                              key=env_key)
    
    prior_knowledge = PriorKnowledge(nfeatures,
                                     ntrain,
                                     1,
                                     ntargets,
                                     None,
                                     0.,
                                     1.,
                                     )
    return prior_knowledge, env


def make_regression_mlp_environment(key: chex.PRNGKey,
                                    nfeatures: int,
                                    ntargets: int,
                                    ntrain: int,
                                    ntest: int,
                                    temperature: float,
                                    hidden_layer_sizes: List[int],
                                    train_batch_size: int = 1,
                                    test_batch_size: int = 1,
                                    x_train_generator: Callable = random.normal,
                                    x_test_generator: Callable = random.normal,
                                    shuffle: bool = False):
    train_key, test_key, y_key, env_key = random.split(key, 4)
    net_fn = make_mlp(y_key,
                      nfeatures,
                      ntargets,
                      temperature,
                      hidden_layer_sizes)

    transformed = hk.without_apply_rng(hk.transform(net_fn))

    dummy_input = jnp.zeros([1, nfeatures])
    params = transformed.init(key, dummy_input)

    assert temperature > 0.0

    def forward(x: chex.Array):
        return transformed.apply(params, x) / temperature

    y_predictor = jit(forward)

    # Generates training data for given problem
    X_train = x_train_generator(train_key, (ntrain, nfeatures))
    X_test = x_test_generator(test_key, (ntest, nfeatures))

    ntrain = len(X_train)

    X = jnp.vstack([X_train, X_test])

    # Generate environment function across x_train
    Y = y_predictor(X)  # [n_train, output_dim]

    X_train = X[:ntrain]
    X_test = X[ntrain:]
    y_train = Y[:ntrain]
    y_test = Y[ntrain:]

    if shuffle:
        env_key, key = random.split(key)
    else:
        env_key = None

    env = SequentialRegressionEnvironment(X_train,
                                          y_train,
                                          X_test,
                                          y_test,
                                          y_predictor,
                                          train_batch_size,
                                          test_batch_size,
                                          obs_noise=0.,
                                          key=env_key)
    return env


def make_environment_from_torch_dataset(dataset: Callable,
                                        classification: bool,
                                        train_batch_size: int = 1,
                                        test_batch_size: int = 1):
    env = SequentialTorchEnvironment(dataset,
                                     train_batch_size,
                                     test_batch_size,
                                     classification)
    return env
