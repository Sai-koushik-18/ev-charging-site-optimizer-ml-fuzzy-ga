#!/usr/bin/env python3
"""
Generate demand predictions for 25 candidates using the saved fresh model.
Read-only operation - does not modify any existing files.
Outputs to data/processed/ml_demand_predictions_fresh.csv
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime

from src.ml.demand_predictor import EVChargingDemandPredictor


def main():
    # Paths
    model_path = "models/demand_predictor_fresh"
    candidates_path = "data/processed/candidates.csv"
    weather_path = "data/raw/weather/weather_hourly_combined.csv"
    output_path = "data/processed/ml_demand_predictions_fresh.csv"

    # Verify output doesn't exist
    if Path(output_path).exists():
        raise FileExistsError(f"Output path already exists: {output_path}")

    print("Loading fresh model...")
    predictor = EVChargingDemandPredictor()
    predictor.load_model(model_path)
    print(f"Model loaded. Features: {predictor.feature_names}")
    print(f"Zone columns: {predictor.zone_columns}")

    print("Loading candidates...")
    candidates = pd.read_csv(candidates_path)
    print(f"Loaded {len(candidates)} candidates")
    print(f"Candidate IDs: {candidates['candidate_id'].tolist()}")
    print(f"Zones: {candidates['zone'].unique()}")

    print("Loading weather...")
    weather = pd.read_csv(weather_path, parse_dates=["timestamp"])
    print(f"Loaded {len(weather)} weather records")
    print(f"Weather columns: {weather.columns.tolist()}")
    print(f"Date range: {weather['timestamp'].min()} to {weather['timestamp'].max()}")

    # Rename weather column to match model expectation
    weather = weather.rename(columns={"relative_humidity_pct": "humidity_pct"})

    # Verify all 25 candidates have assigned zones (not UNASSIGNED)
    unassigned = candidates[candidates["zone"] == "UNASSIGNED"]
    if len(unassigned) > 0:
        print(f"WARNING: {len(unassigned)} candidates are UNASSIGNED")
    else:
        print("All 25 candidates have assigned zones")

    # Prepare predictions for each candidate
    all_predictions = []

    for _, candidate in candidates.iterrows():
        candidate_id = candidate["candidate_id"]
        zone = candidate["zone"]

        # Create feature rows for each hour of 2024 using actual weather
        # Weather data is for 2024 (8784 hours including leap day)
        pred_rows = []

        for _, wrow in weather.iterrows():
            ts = wrow["timestamp"]
            pred_rows.append({
                "candidate_id": candidate_id,
                "timestamp": ts,
                "hour": ts.hour,
                "day_of_week": ts.dayofweek,  # 0=Monday
                "month": ts.month,
                "is_weekend": int(ts.dayofweek in [5, 6]),
                "is_holiday": 0,  # Not in synthetic data
                "is_exam_period": 0,  # Not in synthetic data
                "temperature_c": wrow["temperature_c"],
                "humidity_pct": wrow["humidity_pct"],
                "precipitation_mm": wrow["precipitation_mm"],
                "zone": zone
            })

        pred_df = pd.DataFrame(pred_rows)

        # Predict hourly demand
        hourly_preds = predictor.predict(pred_df, candidates)

        # Ensure non-negative (model already does this, but verify)
        hourly_preds = np.maximum(hourly_preds, 0)

        # Aggregate to annual
        annual_demand = hourly_preds.sum()

        all_predictions.append({
            "candidate_id": candidate_id,
            "zone": zone,
            "predicted_annual_demand_kwh": float(annual_demand),
            "model_identifier": "demand_predictor_fresh",
            "model_type": "random_forest",
            "training_data_provenance": "SYNTHETIC (synthetic demand_history.csv generated from synthetic_demand_model)",
            "prediction_timestamp": datetime.now().isoformat(),
            "n_hours_predicted": len(hourly_preds),
            "prediction_provenance": "DERIVED (from fresh ML model trained on synthetic demand history)"
        })

        print(f"  {candidate_id} ({zone}): {annual_demand:.2f} kWh/yr")

    # Create output DataFrame
    out_df = pd.DataFrame(all_predictions)

    # Validation
    print("\n=== VALIDATION ===")
    print(f"Unique candidate_ids: {out_df['candidate_id'].nunique()} (expected 25)")
    print(f"Missing candidates: {25 - out_df['candidate_id'].nunique()}")
    print(f"Any NaN predictions: {out_df['predicted_annual_demand_kwh'].isna().any()}")
    print(f"Any infinite predictions: {np.isinf(out_df['predicted_annual_demand_kwh']).any()}")
    print(f"Any negative predictions: {(out_df['predicted_annual_demand_kwh'] < 0).any()}")

    # Summary statistics
    print(f"\nPrediction range: {out_df['predicted_annual_demand_kwh'].min():.2f} - {out_df['predicted_annual_demand_kwh'].max():.2f} kWh/yr")
    print(f"Mean: {out_df['predicted_annual_demand_kwh'].mean():.2f} kWh/yr")
    print(f"Median: {out_df['predicted_annual_demand_kwh'].median():.2f} kWh/yr")
    print(f"Std: {out_df['predicted_annual_demand_kwh'].std():.2f} kWh/yr")
    print(f"Sum: {out_df['predicted_annual_demand_kwh'].sum():.2f} kWh/yr")

    # Check for negative predictions (report, don't clip)
    neg = out_df[out_df["predicted_annual_demand_kwh"] < 0]
    if len(neg) > 0:
        print(f"\nWARNING: {len(neg)} negative predictions found:")
        for _, row in neg.iterrows():
            print(f"  {row['candidate_id']}: {row['predicted_annual_demand_kwh']:.2f} kWh/yr")

    # Save
    out_df.to_csv(output_path, index=False)
    print(f"\nSaved to {output_path}")

    # Also save metadata
    meta_path = output_path.replace(".csv", "_metadata.json")
    metadata = {
        "model_path": model_path,
        "model_type": "random_forest",
        "feature_names": predictor.feature_names,
        "zone_columns": predictor.zone_columns,
        "candidates_source": candidates_path,
        "weather_source": weather_path,
        "n_candidates": len(candidates),
        "n_hours_per_candidate": len(weather),
        "prediction_timestamp": datetime.now().isoformat(),
        "training_data_provenance": "SYNTHETIC",
        "prediction_provenance": "DERIVED",
        "validation": {
            "unique_candidates": int(out_df['candidate_id'].nunique()),
            "missing_candidates": int(25 - out_df['candidate_id'].nunique()),
            "nan_predictions": bool(out_df['predicted_annual_demand_kwh'].isna().any()),
            "infinite_predictions": bool(np.isinf(out_df['predicted_annual_demand_kwh']).any()),
            "negative_predictions": int((out_df['predicted_annual_demand_kwh'] < 0).sum()),
            "prediction_range_kwh": [float(out_df['predicted_annual_demand_kwh'].min()), float(out_df['predicted_annual_demand_kwh'].max())],
            "mean_kwh": float(out_df['predicted_annual_demand_kwh'].mean()),
            "sum_kwh": float(out_df['predicted_annual_demand_kwh'].sum())
        }
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved metadata to {meta_path}")


if __name__ == "__main__":
    main()