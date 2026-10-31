"""
Evaluate the GA placement against simple baselines and the exhaustive optimum.

Every method picks the same number of sites under the same hard rules (budget,
minimum spacing, known costs) and is scored with the GA's own fitness
function, so the numbers are directly comparable.
"""

import itertools
import json
import math
import random
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from src.ga.optimizer import GeneticAlgorithmOptimizer, Individual

MAX_EXHAUSTIVE = 200_000  # combinations; beyond this the exhaustive check is skipped
N_RANDOM = 1000


def score_plan(ga: GeneticAlgorithmOptimizer, idx: Sequence[int]) -> Dict:
    """Score one set of site indices with the GA fitness function."""
    idx = list(idx)
    chromosome = np.zeros(len(ga.candidates_df), dtype=int)
    chromosome[idx] = 1
    fitness, comps = ga._calculate_fitness(Individual(chromosome=chromosome))
    feasible = (comps.get("budget_violation", 1.0) == 0.0
                and comps.get("separation_violation", 0.0) == 0.0
                and comps.get("unknown_cost", True) is False)
    sel = ga.candidates_df.iloc[idx]
    pairs = ga.distance_matrix[np.ix_(idx, idx)][np.triu_indices(len(idx), 1)]
    return {
        "feasible": feasible,
        "fitness": fitness,
        "demand_coverage": comps.get("demand_coverage", 0.0),
        "spatial_coverage": comps.get("spatial_coverage", 0.0),
        "total_cost_lakhs": float(sel["total_capex_inr_lakhs"].sum()),
        "annual_demand_kwh": float(sel["predicted_annual_demand_kwh"].sum())
        if "predicted_annual_demand_kwh" in sel else float("nan"),
        "min_spacing_m": float(pairs.min()) if len(pairs) else float("nan"),
        "sites": ", ".join(sel["candidate_id"]),
    }


def greedy_plan(ga: GeneticAlgorithmOptimizer, order: Sequence[int], k: int) -> Optional[List[int]]:
    """Take sites in the given order, skipping any that break budget or spacing."""
    budget = ga.config["fitness_params"]["budget_lakhs"]
    min_dist = ga.config["fitness_params"]["min_station_distance_m"]
    capex = ga.candidates_df["total_capex_inr_lakhs"].to_numpy()
    chosen: List[int] = []
    for i in order:
        if np.isnan(capex[i]) or capex[chosen + [i]].sum() > budget:
            continue
        if any(ga.distance_matrix[i, j] < min_dist for j in chosen):
            continue
        chosen.append(i)
        if len(chosen) == k:
            return chosen
    return None


def evaluate(ga: GeneticAlgorithmOptimizer, ga_site_ids: Sequence[str], seed: int = 42) -> Dict:
    """Compare the GA plan with baselines and, when small enough, the exhaustive optimum."""
    df = ga.candidates_df
    n, k = len(df), ga.config["n_stations_target"]
    rows = []

    def add(method: str, idx: Optional[Sequence[int]], note: str = ""):
        if idx is None:
            rows.append({"method": method, "feasible": False, "note": note or "no plan meets the rules"})
        else:
            rows.append({"method": method, **score_plan(ga, idx), "note": note})

    id_to_idx = {cid: i for i, cid in enumerate(df["candidate_id"])}
    ga_idx = [id_to_idx[c] for c in ga_site_ids]
    add("GA (this project)", ga_idx if ga_idx else None)

    fuzzy = df["fuzzy_suitability_score"].fillna(-1).to_numpy()
    add("Top fuzzy score (greedy)", greedy_plan(ga, np.argsort(-fuzzy), k))
    if "predicted_annual_demand_kwh" in df:
        add("Highest ML demand (greedy)", greedy_plan(ga, np.argsort(-df["predicted_annual_demand_kwh"].to_numpy()), k))
    add("Cheapest sites (greedy)", greedy_plan(ga, np.argsort(df["total_capex_inr_lakhs"].fillna(np.inf).to_numpy()), k))

    rng = random.Random(seed)
    random_scores = [score_plan(ga, rng.sample(range(n), k)) for _ in range(N_RANDOM)]
    feasible_random = [r for r in random_scores if r["feasible"]]
    if feasible_random:
        rows.append({
            "method": f"Random placement (mean of {len(feasible_random)} valid draws)",
            "feasible": True,
            **{m: float(np.mean([r[m] for r in feasible_random]))
               for m in ["fitness", "demand_coverage", "spatial_coverage", "total_cost_lakhs",
                         "annual_demand_kwh", "min_spacing_m"]},
            "sites": "",
            "note": f"{len(feasible_random) / N_RANDOM:.0%} of random plans meet the rules",
        })

    summary = {"n_sites": n, "n_stations": k, "random_valid_share": len(feasible_random) / N_RANDOM}
    n_combos = math.comb(n, k)
    summary["n_combinations"] = n_combos
    if n_combos <= MAX_EXHAUSTIVE:
        all_scores = [(idx, score_plan(ga, idx)) for idx in itertools.combinations(range(n), k)]
        valid = [(idx, s) for idx, s in all_scores if s["feasible"]]
        summary["n_valid_plans"] = len(valid)
        if valid:
            best_idx, _ = max(valid, key=lambda t: t[1]["fitness"])
            add("Exhaustive search (true optimum)", best_idx, f"checked all {n_combos:,} plans")
            if ga_idx:
                ga_fit = score_plan(ga, ga_idx)["fitness"]
                summary["ga_matches_optimum"] = bool(abs(ga_fit - score_plan(ga, best_idx)["fitness"]) < 1e-9)
                summary["ga_beats_share_of_valid_plans"] = float(np.mean([s["fitness"] <= ga_fit for _, s in valid]))
    return {"table": pd.DataFrame(rows), "summary": summary}


def run_evaluation(scored_csv: str, solution_csv: str, ga_config: str,
                   out_dir: str = "outputs/evaluation") -> Dict:
    """Evaluate the saved GA solution and write comparison.csv and summary.json."""
    ga = GeneticAlgorithmOptimizer(ga_config)
    ga.load_candidates(scored_csv)
    solution = pd.read_csv(solution_csv)
    result = evaluate(ga, list(solution["candidate_id"]) if len(solution) else [])

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["table"].to_csv(out / "comparison.csv", index=False)
    (out / "summary.json").write_text(json.dumps(result["summary"], indent=2), encoding="utf-8")

    cols = ["method", "feasible", "fitness", "demand_coverage", "spatial_coverage", "total_cost_lakhs", "min_spacing_m"]
    print("\n=== GA vs baselines (same budget, spacing and station count) ===")
    print(result["table"][[c for c in cols if c in result["table"]]].round(3).to_string(index=False))
    print(json.dumps(result["summary"], indent=2))
    return result
