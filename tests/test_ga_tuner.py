"""Tests for the GA hyperparameter search used to tune the demand model."""
import numpy as np

from src.ml.ga_tuner import SEARCH_SPACE, tune_random_forest_ga


def test_tuner_returns_settings_from_the_search_space():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, 4))
    y = X[:, 0] * 2 + rng.normal(scale=0.1, size=400)
    timestamps = np.arange(400)

    best, history = tune_random_forest_ga(X, y, timestamps, population_size=4, generations=2, sample_frac=1.0)

    assert set(best) == set(SEARCH_SPACE)
    for key, value in best.items():
        assert value in SEARCH_SPACE[key]
    assert len(history) == 3
    best_rmse = [h["best_val_rmse"] for h in history]
    assert best_rmse == sorted(best_rmse, reverse=True)  # elitism: never gets worse
