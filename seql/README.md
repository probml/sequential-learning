# Sequential Learning

We extend the [neural testbed](https://github.com/deepmind/neural_testbed) from DeepMind to handle online or continual supervised learning.
 

## Agents

An agent is a likelihood model of the form p(y|x,theta) and an inference algorithm for the posterior p(theta|D(1:t))
Examples:

### Models
- **Linear regression(with fixed basis function)**

<img src="https://latex.codecogs.com/svg.image?N(y|w'\phi(x),&space;\sigma^2)" title="https://latex.codecogs.com/svg.image?N(y|w'\phi(x), \sigma^2)" /> where <img src="https://latex.codecogs.com/svg.image?\phi(x)" title="https://latex.codecogs.com/svg.image?\phi(x)" /> is specified.
- **Logistic regression (with fixed basis function)**

 <img src="https://latex.codecogs.com/svg.image?Cat(y&space;|&space;softmax(w'&space;\phi(x))" title="https://latex.codecogs.com/svg.image?Cat(y | softmax(w' \phi(x))" />, where <img src="https://latex.codecogs.com/svg.image?\phi(x)" title="https://latex.codecogs.com/svg.image?\phi(x)" /> is specified. 
- **MLP**
- **CNN**

### Posterior inference algorithms:

- SGD 
- "Deep ensembles" ie SGD on multiple copies of the model
- (Extended) Kalman Filter 
- Sequential VI

## Environments

There are two different environment types which stand for the type of supervised learning probleem. They not only produce synthetic data given the distribution of training data and test data but also use any available dataset.

- Classification Environment
- Regression Environment

In order to use your own dataset, 

1. You should define two functions which returns `x_train_generator` and  `x_test_generator`, respectively. Both of them should look like 

```
def make_x_sampler(input_dim: int):
    def x_train_sampler(key: chex.PRNGKey, num_samples: int):
        ...
        return X
    return x_sampler
```

2.  You should define sample_fn as follows:

```
def sample_fn(apply_fn: Callable,
                         x_generator: Callable,
                         num_train: int,
                         key: chex.PRNGKey):
                
  x_train = x_generator(key,  num_train)
  y_train = ...
   ...
  data = (x_train, y_train)
  return data
```
Note that `apply_fn` is usually used for creating synthetic dataset.

## How to run

You can either create your own config file or use the predefined ones. Then, you should run

```
python3 -m jsl.gym_envs.run --config <path-of-config-file>
```
