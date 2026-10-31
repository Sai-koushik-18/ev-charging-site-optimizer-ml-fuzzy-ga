"""
Genetic-algorithm hyperparameter search for the demand model (GA + ML hybrid).

The GA evolves Random Forest settings and scores each one by its error on a
time-based validation window. Only the first 80% of the timeline is used, so
the final 20% stays untouched as the test set for the trained model.
"""

import random
from typing import Dict, List, Tuple

import numpy as np
from loguru import logger
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error

SEARCH_SPACE = {
    "n_estimators": [50, 100, 150, 200, 300],
    "max_depth": [6, 8, 10, 12, 16, 20, None],
    "min_samples_leaf": [1, 2, 4, 8],
    "max_features": [1.0, 0.7, 0.5, "sqrt"],
}


def tune_random_forest_ga(
    X: np.ndarray,
    y: np.ndarray,
    timestamps: np.ndarray,
    population_size: int = 8,
    generations: int = 5,
    sample_frac: float = 0.3,
    seed: int = 42,
) -> Tuple[Dict, List[Dict]]:
    """
    Search Random Forest hyperparameters with a genetic algorithm.

    Args:
        X, y: Feature matrix and target (unscaled; Random Forests are scale-invariant)
        timestamps: Timestamp per row, used for the time-based split
        population_size: Settings evaluated per generation
        generations: Number of generations
        sample_frac: Fraction of tuning rows used to fit each candidate (for speed)
        seed: Random seed

    Returns:
        Tuple of (best_params, history) where history has one entry per generation
    """
    rng = random.Random(seed)
    order = np.argsort(timestamps)
    X, y = X[order], y[order]

    tune_end = int(len(X) * 0.8)  # last 20% reserved for the final test
    val_start = int(tune_end * 0.8)
    X_val, y_val = X[val_start:tune_end], y[val_start:tune_end]
    sample = np.random.default_rng(seed).choice(val_start, size=int(val_start * sample_frac), replace=False)
    X_fit, y_fit = X[sample], y[sample]

    keys = list(SEARCH_SPACE)
    cache: Dict[Tuple[int, ...], float] = {}

    def params_of(genes):
        return {k: SEARCH_SPACE[k][g] for k, g in zip(keys, genes)}

    def rmse(genes) -> float:
        key = tuple(genes)
        if key not in cache:
            model = RandomForestRegressor(random_state=seed, n_jobs=-1, **params_of(genes))
            model.fit(X_fit, y_fit)
            cache[key] = float(np.sqrt(mean_squared_error(y_val, model.predict(X_val))))
        return cache[key]

    def random_genes():
        return [rng.randrange(len(SEARCH_SPACE[k])) for k in keys]

    def tournament(pop):
        return min(rng.sample(pop, 3), key=rmse)

    population = [random_genes() for _ in range(population_size)]
    history = []
    for gen in range(generations):
        population.sort(key=rmse)
        best = population[0]
        history.append({"generation": gen, "best_val_rmse": rmse(best), "best_params": params_of(best)})
        logger.info(f"GA tuning gen {gen}: best validation RMSE {rmse(best):.4f} with {params_of(best)}")

        next_pop = [list(g) for g in population[:2]]  # elitism
        while len(next_pop) < population_size:
            p1, p2 = tournament(population), tournament(population)
            child = [a if rng.random() < 0.5 else b for a, b in zip(p1, p2)]  # uniform crossover
            child = [rng.randrange(len(SEARCH_SPACE[k])) if rng.random() < 0.2 else g
                     for k, g in zip(keys, child)]  # mutation
            next_pop.append(child)
        population = next_pop

    best = min(population, key=rmse)
    history.append({"generation": generations, "best_val_rmse": rmse(best), "best_params": params_of(best)})
    return params_of(best), history
