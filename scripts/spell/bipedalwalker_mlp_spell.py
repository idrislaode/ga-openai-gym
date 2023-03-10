import copy
from abc import ABC, abstractmethod
from collections import OrderedDict
from datetime import datetime
from typing import List, Tuple, Callable
import sys

import gym
import numpy as np
import torch
import torch.nn as nn


class NeuralNetwork(ABC):
    @abstractmethod
    def get_weights_biases(self) -> np.array:
        pass

    @abstractmethod
    def update_weights_biases(self, weights_biases: np.array) -> None:
        pass

    def load(self, file):
        self.update_weights_biases(np.load(file))


class MLPTorch(nn.Module, NeuralNetwork):
    def __init__(self, input_size, hidden_size1, hidden_size2, output_size, p=0.1):
        super(MLPTorch, self).__init__()
        self.linear1 = nn.Linear(input_size, hidden_size1)
        self.linear2 = nn.Linear(hidden_size1, hidden_size2)
        self.dropout = nn.Dropout(p=p)
        self.linear3 = nn.Linear(hidden_size2, output_size)

    def forward(self, x) -> torch.Tensor:
        output = torch.relu(self.linear1(x))
        output = torch.relu(self.linear2(output))
        output = self.dropout(output)
        output = self.linear3(output)
        return output

    def get_weights_biases(self) -> np.array:
        parameters = self.state_dict().values()
        parameters = [p.flatten() for p in parameters]
        parameters = torch.cat(parameters, 0)
        return parameters.detach().numpy()

    def update_weights_biases(self, weights_biases: np.array) -> None:
        weights_biases = torch.from_numpy(weights_biases)
        shapes = [x.shape for x in self.state_dict().values()]
        shapes_prod = [torch.tensor(s).numpy().prod() for s in shapes]

        partial_split = weights_biases.split(shapes_prod)
        model_weights_biases = []
        for i in range(len(shapes)):
            model_weights_biases.append(partial_split[i].view(shapes[i]))
        state_dict = OrderedDict(zip(self.state_dict().keys(), model_weights_biases))
        self.load_state_dict(state_dict)


class Individual(ABC):
    def __init__(self, input_size, hidden_size, output_size):
        self.nn = self.get_model(input_size, hidden_size, output_size)
        self.fitness = 0.0
        self.weights_biases: np.array = None

    def calculate_fitness(self, env) -> None:
        self.fitness, self.weights_biases = self.run_single(env)

    def update_model(self) -> None:
        self.nn.update_weights_biases(self.weights_biases)

    @abstractmethod
    def get_model(self, input_size, hidden_size, output_size) -> NeuralNetwork:
        pass

    @abstractmethod
    def run_single(self, env, n_episodes=300, render=False) -> Tuple[float, np.array]:
        pass


class MLPTorchIndividual(Individual):
    def __init__(self, input_size, hidden_size, output_size, model):
        self.model = model
        super().__init__(input_size, hidden_size, output_size)
        self.input_size = input_size

    def get_model(self, input_size, hidden_size, output_size) -> NeuralNetwork:
        # return MLPTorch(input_size, hidden_size, 12, output_size, p=0.2)
        return self.model

    def run_single(self, env, n_episodes=1000, render=False) -> Tuple[float, np.array]:
        obs = env.reset()
        fitness = 0
        for episode in range(n_episodes):
            if render:
                env.render()
            obs = obs[:self.input_size]
            obs = torch.from_numpy(obs).float()
            action = self.nn.forward(obs)
            action = action.detach().numpy()
            obs, reward, done, _ = env.step(action)
            fitness += reward
            if done:
                break
        return fitness, self.nn.get_weights_biases()


def inversion(child_weights_biases: np.array):
    return np.flip(child_weights_biases).copy()


def statistics(population: List[Individual]):
    population_fitness = [individual.fitness for individual in population]
    return np.mean(population_fitness), np.min(population_fitness), np.max(population_fitness)


def ranking_selection(population: List[Individual]) -> Tuple[Individual, Individual]:
    sorted_population = sorted(population, key=lambda individual: individual.fitness, reverse=True)
    parent1, parent2 = sorted_population[:2]
    return parent1, parent2


def blx_alpha(parent1_weights_biases: np.array, parent2_weights_biases: np.array, alpha=0.1):
    """
    Crossover:
     https://ai.stackexchange.com/questions/3428/mutation-and-crossover-in-a-genetic-algorithm-with-real-numbers/6323#6323
    random number from in [min - range * ??, max + range * ??]
    """
    child1_weights_biases = np.copy(parent1_weights_biases)
    child2_weights_biases = np.copy(parent2_weights_biases)
    for i in range(len(parent1_weights_biases)):
        xi = parent1_weights_biases[i]
        yi = parent2_weights_biases[i]
        min_gen = np.min([xi, yi])
        max_gen = np.max([xi, yi])
        range_gen = np.abs(max_gen - min_gen)
        child1_weights_biases[i] = np.random.uniform(min_gen - range_gen * alpha, max_gen + range_gen * alpha)
        child2_weights_biases[i] = np.random.uniform(min_gen - range_gen * alpha, max_gen + range_gen * alpha)
    return child1_weights_biases, child2_weights_biases


def mutation(parent_weights_biases: np.array, p: float):
    child_weight_biases = np.copy(parent_weights_biases)
    if np.random.rand() < p:
        position = np.random.randint(0, parent_weights_biases.shape[0])
        n = np.random.normal(np.mean(child_weight_biases), np.std(child_weight_biases))
        child_weight_biases[position] = n + np.random.randint(-10, 10)
    return child_weight_biases


def generation(env, old_population, new_population, p_mutation, p_crossover, p_inversion):
    for i in range(0, len(old_population) - 1, 2):
        # Selection
        parent1, parent2 = ranking_selection(old_population)

        # Crossover
        child1 = copy.deepcopy(parent1)
        child2 = copy.deepcopy(parent2)

        if np.random.rand() < p_crossover:
            child1.weights_biases, child2.weights_biases = blx_alpha(parent1.weights_biases,
                                                                     parent2.weights_biases)
        # Mutation
        child1.weights_biases = mutation(child1.weights_biases, p_mutation)
        child2.weights_biases = mutation(child2.weights_biases, p_mutation)

        if np.random.randn() < p_inversion:
            child1.weights_biases = inversion(child1.weights_biases)
            child2.weights_biases = inversion(child2.weights_biases)

        # Update model weights and biases
        child1.update_model()
        child2.update_model()

        child1.calculate_fitness(env)
        child2.calculate_fitness(env)

        # If children fitness is greater thant parents update population
        if child1.fitness + child2.fitness > parent1.fitness + parent2.fitness:
            new_population[i] = child1
            new_population[i + 1] = child2
        else:
            new_population[i] = parent1
            new_population[i + 1] = parent2


class Population:
    def __init__(self, individual, pop_size, max_generation, p_mutation, p_crossover, p_inversion):
        self.pop_size = pop_size
        self.max_generation = max_generation
        self.p_mutation = p_mutation
        self.p_crossover = p_crossover
        self.p_inversion = p_inversion
        self.old_population = [individual for _ in range(pop_size)]
        self.new_population = [None] * pop_size

    def run(self, env, run_generation: Callable, verbose=False, log=False, output_folder=None):
        for i in range(self.max_generation):
            [p.calculate_fitness(env) for p in self.old_population]
            run_generation(env,
                           self.old_population,
                           self.new_population,
                           self.p_mutation,
                           self.p_crossover,
                           self.p_inversion)

            if log:
                self.save_logs(i, output_folder)

            if verbose:
                max_score = self.show_stats(i)
                if max_score > 80:
                    self.save_model_parameters(output_folder, i, max_score)

            self.update_old_population()

        self.save_model_parameters(output_folder, self.max_generation, '')

    def save_logs(self, n_gen, output_folder):
        """
        CSV format -> date,n_generation,mean,min,max
        """
        date = self.now()
        file_name = 'logs.csv'
        mean, min, max = statistics(self.new_population)
        stats = f'{date},{n_gen},{mean},{min},{max}\n'
        with open(output_folder + file_name, 'a') as f:
            f.write(stats)

    def show_stats(self, n_gen):
        mean, min, max = statistics(self.new_population)
        date = self.now()
        stats = f"{date} - generation {n_gen + 1} | mean: {mean}\tmin: {min}\tmax: {max}\n"
        print(stats)
        return max

    def update_old_population(self):
        self.old_population = copy.deepcopy(self.new_population)

    def save_model_parameters(self, output_folder, iteration, max_score):
        best_model = self.get_best_model_parameters()
        date = self.now()
        file_name = self.get_file_name(date) + f'_I={iteration}_SCORE={max_score}.npy'
        np.save(output_folder + file_name, best_model)

    def get_best_model_parameters(self) -> np.array:
        """
        :return: Weights and biases of the best individual
        """
        individual = sorted(self.new_population, key=lambda ind: ind.fitness, reverse=True)[0]
        return individual.weights_biases

    def get_file_name(self, date):
        return '{}_NN={}_POPSIZE={}_GEN={}_PMUTATION_{}_PCROSSOVER_{}'.format(date,
                                                                              self.new_population[0].__class__.__name__,
                                                                              self.pop_size,
                                                                              self.max_generation,
                                                                              self.p_mutation,
                                                                              self.p_crossover)

    @staticmethod
    def now():
        return datetime.now().strftime('%m-%d-%Y_%H-%M')


if __name__ == '__main__':
    env = gym.make('BipedalWalkerHardcore-v2')
    env.seed(123)

    POPULATION_SIZE = 30
    MAX_GENERATION = 6000
    MUTATION_RATE = 0.6
    CROSSOVER_RATE = 0.85
    INVERSION_RATE = 1e-20

    # 10 - 16 - 12 - 4
    INPUT_SIZE = 10
    HIDDEN_SIZE = 16
    OUTPUT_SIZE = 4

    modelName = sys.argv[1]
    mlp_torch = MLPTorch(INPUT_SIZE, HIDDEN_SIZE, 12, OUTPUT_SIZE)
    mlp_torch.load(modelName)

    p = Population(MLPTorchIndividual(INPUT_SIZE, HIDDEN_SIZE, OUTPUT_SIZE, mlp_torch),
                   POPULATION_SIZE,
                   MAX_GENERATION,
                   MUTATION_RATE,
                   CROSSOVER_RATE,
                   INVERSION_RATE)
    p.run(env, generation, verbose=True, log=True, output_folder='')

    env.close()
