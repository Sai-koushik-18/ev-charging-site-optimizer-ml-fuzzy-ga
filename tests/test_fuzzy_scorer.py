"""
Tests for FuzzyEVChargingScorer: rule coverage, monotonic demand response,
input mapping, and the ML demand index path.
"""
import itertools

import pandas as pd
import pytest
from skfuzzy import control as ctrl

from src.fuzzy.scorer import FuzzyEVChargingScorer


@pytest.fixture(scope="module")
def scorer():
    return FuzzyEVChargingScorer()


def _score(scorer, **inputs):
    sim = ctrl.ControlSystemSimulation(scorer.fuzzy_system)
    for k, v in inputs.items():
        sim.input[k] = v
    sim.compute()
    return sim.output.get("suitability")


BASE = dict(predicted_demand=50, traffic_accessibility=0.5, grid_connection_cost=20,
            land_cost_tier=1.5, poi_attractiveness=50, competition_index=4)


def test_every_input_combination_gets_a_score(scorer):
    """The covering rules mean at least one rule fires anywhere in the input space."""
    grid = itertools.product([0, 20, 50, 80, 100], [0.1, 0.4, 0.6, 0.85, 1.0],
                             [1, 45], [0.0, 2.5], [5, 90], [0, 9])
    for d, t, g, l, p, c in grid:
        s = _score(scorer, predicted_demand=d, traffic_accessibility=t, grid_connection_cost=g,
                   land_cost_tier=l, poi_attractiveness=p, competition_index=c)
        assert s is not None and 0.0 <= s <= 1.0, (d, t, g, l, p, c, s)


def test_higher_demand_scores_higher(scorer):
    low = _score(scorer, **{**BASE, "predicted_demand": 10})
    high = _score(scorer, **{**BASE, "predicted_demand": 90})
    assert high > low


def test_better_access_scores_higher(scorer):
    poor = _score(scorer, **{**BASE, "traffic_accessibility": 0.1})
    good = _score(scorer, **{**BASE, "traffic_accessibility": 0.9})
    assert good > poor


@pytest.mark.parametrize("tier,expected", [("very_low", 0.0), ("low", 0.5), ("medium", 1.5), ("high", 2.5)])
def test_land_cost_tier_mapping(scorer, tier, expected):
    assert scorer._convert_land_cost_tier(tier) == expected


def test_demand_index_is_used_and_recorded(scorer):
    df = pd.DataFrame([{
        "candidate_id": "CAND_T1", "demand_index": 80.0, "traffic_accessibility_score": 0.8,
        "grid_connection_cost_inr_lakhs": 1.0, "land_cost_tier": "very_low", "poi_score": 0.7,
        "existing_chargers_3km": 0,
    }])
    scored = scorer.score_candidates(df)
    assert scored.loc[0, "demand_prediction_source"] == "ML_DEMAND_INDEX"
    assert scored.loc[0, "predicted_demand"] == 80.0
    assert 0.0 <= scored.loc[0, "fuzzy_suitability_score"] <= 1.0


def test_missing_grid_cost_is_unscored_not_fabricated(scorer):
    df = pd.DataFrame([{
        "candidate_id": "CAND_T2", "demand_index": 50.0, "traffic_accessibility_score": 0.5,
        "grid_connection_cost_inr_lakhs": float("nan"), "land_cost_tier": "medium", "poi_score": 0.5,
        "existing_chargers_3km": 0,
    }])
    scored = scorer.score_candidates(df)
    assert pd.isna(scored.loc[0, "fuzzy_suitability_score"])
    assert scored.loc[0, "demand_prediction_source"].startswith("UNSCORABLE")
