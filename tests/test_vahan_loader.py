"""Real EV adoption from the Vahan exports in data/external/vahan/ and the demand scale it gives."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data.vahan_loader import REFERENCE_EV_SHARE, ev_adoption, state_table, yearly_table

pytestmark = pytest.mark.skipif(not Path("data/external/vahan/india_states_2025.xlsx").exists(),
                                reason="Vahan exports not present")


def test_state_table_adds_up():
    t = state_table(2025)
    assert len(t) == 37  # 36 states / UTs and the Total row
    states = t.drop(index="Total")
    assert states["total"].sum() == t.loc["Total", "total"]
    assert states["ev"].sum() == t.loc["Total", "ev"]
    assert states["ev_share"].between(0, 1).all()


def test_state_and_yearly_files_agree():
    # the all-India state report and the Andhra Pradesh yearly report are separate exports
    ap = yearly_table("ap").set_index("year")
    for year in (2024, 2025):
        assert ap.loc[year, "total"] == state_table(year).loc["Andhra Pradesh", "total"]
        assert abs(ap.loc[year, "ev"] - state_table(year).loc["Andhra Pradesh", "ev"]) <= 1


@pytest.mark.parametrize("asked,matched", [
    ("Andhra Pradesh", "Andhra Pradesh"),
    ("Tamil Nadu", "Tamil Nadu"),
    ("National Capital Territory of Delhi", "Delhi"),
    ("Andaman and Nicobar Islands", "Andaman & Nicobar Island"),
    ("Dadra and Nagar Haveli and Daman and Diu", "UT of DNH and DD"),
    ("Odisha", "Odisha"),
])
def test_state_names_from_openstreetmap_match(asked, matched):
    a = ev_adoption(asked)
    assert a["matched"] and a["state"] == matched
    assert a["demand_scale"] == pytest.approx(a["ev_share"] / REFERENCE_EV_SHARE)


@pytest.mark.parametrize("asked", [None, "Narnia"])
def test_unknown_state_uses_all_india(asked):
    a = ev_adoption(asked)
    assert a["state"] == "All India" and not a["matched"]


def test_missing_files_give_none(tmp_path):
    assert ev_adoption("Andhra Pradesh", folder=str(tmp_path)) is None


def test_demand_scale_multiplies_demand():
    from src.data.config_loader import get_config
    from src.data.synthetic_generator import DemandGenerator
    weather = pd.DataFrame({"timestamp": pd.date_range("2024-03-01", periods=48, freq="h"),
                            "temperature_c": 30.0, "relative_humidity_pct": 60.0, "precipitation_mm": 0.0})
    cand = pd.Series({"candidate_id": "C1", "zone": "academic"})
    config = get_config("settings")
    base = DemandGenerator(config, seed=1)._generate_candidate_year(cand, weather, 2024)["demand_kwh"]
    scaled = DemandGenerator(config, seed=1, demand_scale=2.0)._generate_candidate_year(cand, weather, 2024)["demand_kwh"]
    assert np.allclose(scaled, 2 * base, atol=0.011)  # same noise draws; demand is rounded to 0.01
