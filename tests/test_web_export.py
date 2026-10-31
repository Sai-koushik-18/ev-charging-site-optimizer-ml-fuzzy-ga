"""The web dashboard data must be a faithful copy of the pipeline outputs."""
import json

import pandas as pd

from src.visualization.web_export import build_web_data


def _load(path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text[text.index("=") + 1:].rstrip().rstrip(";"))


def test_export_copies_pipeline_outputs_unchanged(tmp_path):
    out = tmp_path / "dashboard-data.js"
    build_web_data(out_js=str(out))
    data = _load(out)

    cands = pd.read_csv("data/processed/candidates_demo_fuzzy_scored.csv")
    sol = pd.read_csv("data/processed/ga_solution_demo.csv")
    assert len(data["candidates"]) == len(cands)
    by_id = {c["candidate_id"]: c for c in data["candidates"]}
    for _, r in cands.iterrows():
        c = by_id[r.candidate_id]
        assert c["total_capex_inr_lakhs"] == r.total_capex_inr_lakhs
        assert c["fuzzy_suitability_score"] == r.fuzzy_suitability_score
        assert c["predicted_annual_demand_kwh"] == r.predicted_annual_demand_kwh
    assert sorted(s["candidate_id"] for s in data["selected"]) == sorted(sol.candidate_id)

    summary = json.loads(open("outputs/evaluation/summary.json", encoding="utf-8").read())
    assert data["evaluation"]["summary"] == summary
    assert data["ml"]["ga_tuned_rf"]["test_rmse"] == json.loads(
        open("outputs/ml_results/metrics.json", encoding="utf-8").read())["ga_tuned_rf"]["test_rmse"]
    for layer in ["campus", "zones", "buildings", "roads", "parking", "chargers", "state"]:
        assert data["geo"][layer]["features"], layer
