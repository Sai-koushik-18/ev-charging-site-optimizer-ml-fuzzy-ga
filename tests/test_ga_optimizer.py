"""
Tests for the placement GA and its evaluation: hard constraints, feasible
results, and agreement with exhaustive search on a small problem.
"""
import random

import numpy as np
import pandas as pd
import pytest

from src.evaluation.evaluate import evaluate, greedy_plan, score_plan
from src.ga.optimizer import GeneticAlgorithmOptimizer, Individual

# ~0.0009 deg latitude is ~100 m
SITES = [
    ("S1", 16.4900, 80.4990, 40.0, 0.9),
    ("S2", 16.4909, 80.4990, 40.0, 0.8),  # ~100 m from S1
    ("S3", 16.4940, 80.4990, 40.0, 0.6),
    ("S4", 16.4980, 80.4990, 40.0, 0.5),
    ("S5", 16.4940, 80.5030, 250.0, 0.95),  # too expensive to pair with most
]


@pytest.fixture
def ga(tmp_path):
    df = pd.DataFrame(SITES, columns=["candidate_id", "latitude", "longitude",
                                      "total_capex_inr_lakhs", "fuzzy_suitability_score"])
    df["predicted_annual_demand_kwh"] = df["fuzzy_suitability_score"] * 1000
    path = tmp_path / "cands.csv"
    df.to_csv(path, index=False)
    opt = GeneticAlgorithmOptimizer("config/ga_config_demo.yaml")
    opt.config["n_stations_target"] = 2
    opt.config["fitness_params"]["min_station_distance_m"] = 300
    opt.config["fitness_params"]["budget_lakhs"] = 200
    opt.config["max_generations"] = 30
    opt.load_candidates(str(path))
    return opt


def _ind(n, idx):
    c = np.zeros(n, dtype=int)
    c[list(idx)] = 1
    return Individual(chromosome=c)


def test_stations_too_close_are_infeasible(ga):
    fitness, comps = ga._calculate_fitness(_ind(5, [0, 1]))
    assert fitness == 0.0
    assert comps["separation_violation"] == 1.0


def test_over_budget_is_infeasible(ga):
    fitness, comps = ga._calculate_fitness(_ind(5, [2, 4]))  # 40 + 250 > 200
    assert fitness == 0.0
    assert comps["budget_violation"] == 1.0


def test_optimize_returns_a_feasible_plan(ga):
    random.seed(0)
    np.random.seed(0)
    best, history, feasible = ga.optimize()
    assert feasible and best.is_feasible()
    idx = np.where(best.chromosome == 1)[0]
    assert len(idx) == 2
    assert ga.distance_matrix[idx[0], idx[1]] >= 300
    solution, ok = ga.get_solution_details(best)
    assert ok and len(solution) == 2


def test_greedy_respects_rules(ga):
    plan = greedy_plan(ga, np.argsort(-ga.candidates_df["fuzzy_suitability_score"].to_numpy()), 2)
    assert plan is not None
    assert score_plan(ga, plan)["feasible"]


def test_exhaustive_optimum_is_at_least_as_good_as_every_method(ga):
    random.seed(0)
    np.random.seed(0)
    best, _, _ = ga.optimize()
    ids = list(ga.candidates_df.iloc[np.where(best.chromosome == 1)[0]]["candidate_id"])
    result = evaluate(ga, ids)
    table = result["table"]
    optimum = table.loc[table["method"].str.startswith("Exhaustive"), "fitness"].iloc[0]
    assert (table["fitness"].dropna() <= optimum + 1e-9).all()
    assert result["summary"]["ga_matches_optimum"]
