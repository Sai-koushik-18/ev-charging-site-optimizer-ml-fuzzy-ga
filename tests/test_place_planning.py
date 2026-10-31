"""Generic-place logic: minimum budget, valid-plan search and OSM tag zones."""
import itertools
import random

import numpy as np
import pandas as pd
import pytest

from src.evaluation.evaluate import cheapest_plan, min_budget_table, valid_plans
from src.ga.optimizer import GeneticAlgorithmOptimizer
from src.pipeline.place_pipeline import slugify, zone_of


@pytest.fixture
def ga(tmp_path):
    rng = np.random.default_rng(7)
    n = 14
    df = pd.DataFrame({
        "candidate_id": [f"S{i}" for i in range(n)],
        "name": [f"Site {i}" for i in range(n)],
        "latitude": 16.49 + rng.uniform(0, 0.008, n),   # roughly a 900 m x 900 m area
        "longitude": 80.49 + rng.uniform(0, 0.008, n),
        "total_capex_inr_lakhs": rng.uniform(40, 120, n).round(2),
        "fuzzy_suitability_score": rng.uniform(0.3, 0.8, n),
    })
    path = tmp_path / "c.csv"
    df.to_csv(path, index=False)
    opt = GeneticAlgorithmOptimizer("config/ga_config_demo.yaml")
    opt.config["fitness_params"].update(budget_lakhs=250, min_station_distance_m=300)
    opt.load_candidates(str(path))
    return opt


def _brute(ga, k, use_budget):
    cost = ga.candidates_df["total_capex_inr_lakhs"].to_numpy()
    d = ga.distance_matrix
    out = []
    for idx in itertools.combinations(range(len(cost)), k):
        if k > 1 and d[np.ix_(idx, idx)][np.triu_indices(k, 1)].min() < 300:
            continue
        if use_budget and cost[list(idx)].sum() > 250:
            continue
        out.append((cost[list(idx)].sum(), idx))
    return out


@pytest.mark.parametrize("k", [1, 2, 3, 4])
def test_cheapest_plan_matches_brute_force(ga, k):
    brute = _brute(ga, k, use_budget=False)
    found = cheapest_plan(ga, k)
    if not brute:
        assert found is None
    else:
        assert found[0] == pytest.approx(min(c for c, _ in brute))


@pytest.mark.parametrize("k", [2, 3])
def test_valid_plans_match_brute_force(ga, k):
    plans, complete = valid_plans(ga, k)
    assert complete
    assert sorted(plans) == sorted(idx for _, idx in _brute(ga, k, use_budget=True))


def test_valid_plans_reports_when_capped(ga):
    plans, complete = valid_plans(ga, 2, cap=3)
    assert len(plans) == 3 and not complete


def test_min_budget_table_is_increasing_and_stops_when_impossible(ga):
    table = min_budget_table(ga, k_max=8)
    possible = table[table["possible"]]
    assert possible["min_cost_lakhs"].is_monotonic_increasing
    if not table["possible"].all():
        assert not table.iloc[-1]["possible"] and table["possible"].iloc[:-1].all()


@pytest.mark.parametrize("tags,zone", [
    ({"amenity": "food_court"}, "commercial"),
    ({"shop": "supermarket"}, "commercial"),
    ({"building": "dormitory"}, "residential"),
    ({"building": "university"}, "academic"),
    ({"amenity": "library"}, "academic"),
    ({"office": "administrative"}, "admin"),
    ({"leisure": "pitch"}, "sports"),
    ({"building": "yes"}, None),
    ({"building": "dormitory", "shop": "kiosk"}, "commercial"),  # priority order
])
def test_zone_rules(tags, zone):
    assert zone_of(tags) == zone


def test_slugify():
    assert slugify("IIT Madras") == "iit-madras"
    assert slugify("College of Engineering, Guindy!") == "college-of-engineering-guindy"
