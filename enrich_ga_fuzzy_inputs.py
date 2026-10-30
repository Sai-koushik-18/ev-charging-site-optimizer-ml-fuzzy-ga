#!/usr/bin/env python3
"""
Create enriched copies of GA and Fuzzy input CSVs using fresh ML predictions.
Read-only operation - does not modify original files.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime

# Paths
GA_ORIGINAL = "data/processed/ga_fitness_inputs.csv"
FUZZY_ORIGINAL = "data/processed/fuzzy_inputs.csv"
PREDICTIONS = "data/processed/ml_demand_predictions_fresh.csv"

GA_OUTPUT = "data/processed/ga_fitness_inputs_enriched_fresh.csv"
FUZZY_OUTPUT = "data/processed/fuzzy_inputs_enriched_fresh.csv"

# Verify outputs don't exist
for p in [GA_OUTPUT, FUZZY_OUTPUT]:
    if Path(p).exists():
        raise FileExistsError(f"Output already exists: {p}")

# Load predictions
print("Loading fresh predictions...")
pred_df = pd.read_csv(PREDICTIONS)
print(f"  Loaded {len(pred_df)} predictions")
print(f"  Columns: {pred_df.columns.tolist()}")

# Create lookup dict
pred_lookup = dict(zip(pred_df["candidate_id"], pred_df["predicted_annual_demand_kwh"]))

# ===== ENRICH GA FITNESS INPUTS =====
print("\n=== Enriching GA Fitness Inputs ===")
ga_df = pd.read_csv(GA_ORIGINAL)
print(f"  Original GA rows: {len(ga_df)}")
print(f"  Original GA columns: {ga_df.columns.tolist()}")

# Verify all candidate_ids match
ga_ids = set(ga_df["candidate_id"])
pred_ids = set(pred_lookup.keys())
print(f"  GA IDs: {sorted(ga_ids)}")
print(f"  Pred IDs: {sorted(pred_ids)}")
print(f"  Match: {ga_ids == pred_ids}")

if ga_ids != pred_ids:
    missing_in_pred = ga_ids - pred_ids
    missing_in_ga = pred_ids - ga_ids
    if missing_in_pred:
        print(f"  WARNING: IDs in GA but not in predictions: {missing_in_pred}")
    if missing_in_ga:
        print(f"  WARNING: IDs in predictions but not in GA: {missing_in_ga}")

# Update predicted_annual_demand_kwh
ga_enriched = ga_df.copy()
ga_enriched["predicted_annual_demand_kwh"] = ga_enriched["candidate_id"].map(pred_lookup)

# Add provenance column
ga_enriched["demand_prediction_provenance"] = "DERIVED (from fresh ML model trained on synthetic demand history)"
ga_enriched["demand_prediction_model"] = "demand_predictor_fresh"
ga_enriched["demand_prediction_timestamp"] = datetime.now().isoformat()

# Validate
print(f"  Enriched GA rows: {len(ga_enriched)}")
print(f"  Any NaN demand: {ga_enriched['predicted_annual_demand_kwh'].isna().any()}")
print(f"  Demand range: {ga_enriched['predicted_annual_demand_kwh'].min():.2f} - {ga_enriched['predicted_annual_demand_kwh'].max():.2f}")

# Save
ga_enriched.to_csv(GA_OUTPUT, index=False)
print(f"  Saved to {GA_OUTPUT}")

# ===== ENRICH FUZZY INPUTS =====
print("\n=== Enriching Fuzzy Inputs ===")
fuzzy_df = pd.read_csv(FUZZY_ORIGINAL)
print(f"  Original Fuzzy rows: {len(fuzzy_df)}")
print(f"  Original Fuzzy columns: {fuzzy_df.columns.tolist()}")

# Verify all candidate_ids match
fuzzy_ids = set(fuzzy_df["candidate_id"])
print(f"  Fuzzy IDs: {sorted(fuzzy_ids)}")
print(f"  Match: {fuzzy_ids == pred_ids}")

if fuzzy_ids != pred_ids:
    missing_in_pred = fuzzy_ids - pred_ids
    missing_in_fuzzy = pred_ids - fuzzy_ids
    if missing_in_pred:
        print(f"  WARNING: IDs in Fuzzy but not in predictions: {missing_in_pred}")
    if missing_in_fuzzy:
        print(f"  WARNING: IDs in predictions but not in Fuzzy: {missing_in_fuzzy}")

# Update predicted_demand (convert annual to hourly average for fuzzy scorer)
# Fuzzy scorer expects predicted_demand in kWh/h (see fuzzy_config.yaml universe_max: 100)
# Annual / 8760 = hourly average
fuzzy_enriched = fuzzy_df.copy()
fuzzy_enriched["predicted_demand"] = fuzzy_enriched["candidate_id"].map(pred_lookup) / 8760.0

# Add provenance columns
fuzzy_enriched["demand_prediction_provenance"] = "DERIVED (from fresh ML model trained on synthetic demand history)"
fuzzy_enriched["demand_prediction_model"] = "demand_predictor_fresh"
fuzzy_enriched["demand_prediction_timestamp"] = datetime.now().isoformat()

# Validate
print(f"  Enriched Fuzzy rows: {len(fuzzy_enriched)}")
print(f"  Any NaN demand: {fuzzy_enriched['predicted_demand'].isna().any()}")
print(f"  Hourly demand range: {fuzzy_enriched['predicted_demand'].min():.4f} - {fuzzy_enriched['predicted_demand'].max():.4f} kWh/h")

# Save
fuzzy_enriched.to_csv(FUZZY_OUTPUT, index=False)
print(f"  Saved to {FUZZY_OUTPUT}")

# ===== VALIDATION SUMMARY =====
print("\n=== VALIDATION SUMMARY ===")
print(f"\nGA Fitness Inputs Enriched:")
print(f"  Output: {GA_OUTPUT}")
print(f"  Rows: {len(ga_enriched)}")
print(f"  Unique candidate_ids: {ga_enriched['candidate_id'].nunique()}")
print(f"  Columns: {list(ga_enriched.columns)}")
print(f"  Missing values per column:")
for col in ga_enriched.columns:
    n_missing = ga_enriched[col].isna().sum()
    if n_missing > 0:
        print(f"    {col}: {n_missing}")

print(f"\nFuzzy Inputs Enriched:")
print(f"  Output: {FUZZY_OUTPUT}")
print(f"  Rows: {len(fuzzy_enriched)}")
print(f"  Unique candidate_ids: {fuzzy_enriched['candidate_id'].nunique()}")
print(f"  Columns: {list(fuzzy_enriched.columns)}")
print(f"  Missing values per column:")
for col in fuzzy_enriched.columns:
    n_missing = fuzzy_enriched[col].isna().sum()
    if n_missing > 0:
        print(f"    {col}: {n_missing}")

# Save metadata
meta = {
    "created_at": datetime.now().isoformat(),
    "source_predictions": PREDICTIONS,
    "source_ga_original": GA_ORIGINAL,
    "source_fuzzy_original": FUZZY_ORIGINAL,
    "ga_output": GA_OUTPUT,
    "fuzzy_output": FUZZY_OUTPUT,
    "ga_validation": {
        "rows": int(len(ga_enriched)),
        "unique_ids": int(ga_enriched['candidate_id'].nunique()),
        "missing_values": {col: int(ga_enriched[col].isna().sum()) for col in ga_enriched.columns},
        "demand_range_kwh": [float(ga_enriched['predicted_annual_demand_kwh'].min()), float(ga_enriched['predicted_annual_demand_kwh'].max())],
    },
    "fuzzy_validation": {
        "rows": int(len(fuzzy_enriched)),
        "unique_ids": int(fuzzy_enriched['candidate_id'].nunique()),
        "missing_values": {col: int(fuzzy_enriched[col].isna().sum()) for col in fuzzy_enriched.columns},
        "demand_range_kwh_per_h": [float(fuzzy_enriched['predicted_demand'].min()), float(fuzzy_enriched['predicted_demand'].max())],
    },
    "provenance_note": "Demand predictions are DERIVED from fresh ML model (demand_predictor_fresh) trained on SYNTHETIC demand history. Unknown fields (grid cost, catchment population, POI attractiveness, competition, CAPEX) preserved as missing/zero from original."
}

meta_path = "data/processed/enrichment_metadata_fresh.json"
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=2)
print(f"\nMetadata saved to {meta_path}")