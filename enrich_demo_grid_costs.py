#!/usr/bin/env python3
"""
Demo-only grid cost enrichment for EV Charging Infrastructure Planner.
Creates isolated demo candidates with hypothetical transformer distances and costs.

SAFETY:
- Reads ONLY real candidates.csv and demo transformers.geojson
- Writes ONLY new files with _demo suffix
- Never modifies real pipeline outputs
- Uses existing CandidateGenerator methods for consistent calculations
- Labels all demo-derived columns as SYNTHETIC with source "demo_transformer_assumptions"
"""
import sys
from pathlib import Path
import pandas as pd
import geopandas as gpd
import numpy as np
import json
from datetime import datetime

# Add src to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

from src.data.candidate_generator import CandidateGenerator
from src.data.config_loader import get_config
from src.data.provenance import ProvenanceTracker, Provenance

# Paths
REAL_CANDIDATES = "data/processed/candidates.csv"
DEMO_TRANSFORMERS = "data/external/vitap_demo_transformers.geojson"
DEMO_CANDIDATES_OUT = "data/processed/candidates_demo.csv"
DEMO_FUZZY_OUT = "data/processed/fuzzy_inputs_demo.csv"
DEMO_GA_OUT = "data/processed/ga_fitness_inputs_demo.csv"

# Verify outputs don't exist
for p in [DEMO_CANDIDATES_OUT, DEMO_FUZZY_OUT, DEMO_GA_OUT]:
    if Path(p).exists():
        raise FileExistsError(f"Demo output already exists: {p} - remove manually if intended")

def main():
    print("=" * 60)
    print("DEMO GRID COST ENRICHMENT")
    print("=" * 60)

    # 1. Load real candidates
    print(f"\n1. Loading real candidates from {REAL_CANDIDATES}...")
    candidates_df = pd.read_csv(REAL_CANDIDATES)
    print(f"   Loaded {len(candidates_df)} candidates")
    print(f"   Columns: {list(candidates_df.columns)}")

    # Verify all have NaN grid cost
    nan_grid = candidates_df['grid_connection_cost_inr_lakhs'].isna().sum()
    nan_capex = candidates_df['total_capex_inr_lakhs'].isna().sum()
    print(f"   Candidates with NaN grid_connection_cost_inr_lakhs: {nan_grid}")
    print(f"   Candidates with NaN total_capex_inr_lakhs: {nan_capex}")
    assert nan_grid == len(candidates_df), "Expected all candidates to have NaN grid cost"
    assert nan_capex == len(candidates_df), "Expected all candidates to have NaN CAPEX"

    # 2. Load demo transformers
    print(f"\n2. Loading demo transformers from {DEMO_TRANSFORMERS}...")
    transformers_gdf = gpd.read_file(DEMO_TRANSFORMERS)
    print(f"   Loaded {len(transformers_gdf)} demo transformers")
    print(f"   Transformers:")
    for _, row in transformers_gdf.iterrows():
        print(f"     {row['osm_id']}: {row['power_type']} @ {row.geometry.y:.6f}, {row.geometry.x:.6f} ({row['note']})")

    # 3. Convert candidates to GeoDataFrame
    print("\n3. Converting candidates to GeoDataFrame...")
    geometry = gpd.points_from_xy(candidates_df['longitude'], candidates_df['latitude'])
    candidates_gdf = gpd.GeoDataFrame(candidates_df, geometry=geometry, crs="EPSG:4326")

    # 4. Initialize CandidateGenerator to access calculation methods
    print("\n4. Initializing CandidateGenerator...")
    config = get_config("settings")
    generator = CandidateGenerator(config)

    # 5. Calculate grid connection using demo transformers
    print("\n5. Calculating grid connection distances (metric, UTM)...")
    candidates_gdf = generator._calculate_grid_connection(candidates_gdf, transformers_gdf)

    # Check results
    print(f"   grid_dist_m range: {candidates_gdf['grid_dist_m'].min():.1f} - {candidates_gdf['grid_dist_m'].max():.1f} m")
    print(f"   nearest_transformer_id unique: {candidates_gdf['nearest_transformer_id'].unique()}")
    assert not candidates_gdf['grid_dist_m'].isna().any(), "Expected all grid distances to be finite"

    # 6. Calculate costs (includes grid connection cost and total CAPEX)
    print("\n6. Calculating costs (land + grid + construction)...")
    candidates_gdf = generator._calculate_costs(candidates_gdf)

    # Check results
    print(f"   grid_connection_cost_inr_lakhs range: {candidates_gdf['grid_connection_cost_inr_lakhs'].min():.3f} - {candidates_gdf['grid_connection_cost_inr_lakhs'].max():.3f}")
    print(f"   total_capex_inr_lakhs range: {candidates_gdf['total_capex_inr_lakhs'].min():.3f} - {candidates_gdf['total_capex_inr_lakhs'].max():.3f}")
    assert not candidates_gdf['grid_connection_cost_inr_lakhs'].isna().any(), "Expected all grid costs to be finite"
    assert not candidates_gdf['total_capex_inr_lakhs'].isna().any(), "Expected all total CAPEX to be finite"

    # Verify budget constraint (GA budget = 300 lakhs)
    ga_budget = 300.0
    max_capex = candidates_gdf['total_capex_inr_lakhs'].max()
    print(f"   GA budget: {ga_budget} INR Lakhs")
    print(f"   Max candidate CAPEX: {max_capex:.2f} INR Lakhs")
    print(f"   All candidates within budget: {max_capex <= ga_budget}")

    # 7. Add provenance tracking for demo-derived columns
    print("\n7. Adding provenance tracking for demo-derived columns...")
    tracker = ProvenanceTracker("candidates_demo", "enrich_demo_grid_costs")

    # Copy existing provenance for unchanged columns (if available)
    # For demo columns, add explicit SYNTHETIC provenance
    demo_columns_provenance = {
        "grid_dist_m": (Provenance.SYNTHETIC, "demo_transformer_assumptions",
                       "Distance to nearest HYPOTHETICAL demo transformer (metric, UTM). NOT real grid distance."),
        "nearest_transformer_id": (Provenance.SYNTHETIC, "demo_transformer_assumptions",
                       "ID of nearest HYPOTHETICAL demo transformer. NOT real transformer."),
        "grid_connection_cost_inr_lakhs": (Provenance.SYNTHETIC, "demo_transformer_assumptions",
                       "Grid connection cost calculated from demo transformer distance using configured rate (2.0 INR Lakhs/km). NOT real cost."),
        "total_capex_inr_lakhs": (Provenance.SYNTHETIC, "demo_transformer_assumptions",
                       "Total CAPEX including demo grid connection cost. NOT real CAPEX."),
    }

    for col, (prov, source, desc) in demo_columns_provenance.items():
        if col in candidates_gdf.columns:
            tracker.add_column(col, prov, source, desc)

    # Also update provenance for land_cost_inr_lakhs and construction_cost_inr_lakhs if they were SYNTHETIC before
    # (They remain the same calculations, but now total_capex is finite)

    # Finalize provenance
    candidates_gdf = tracker.finalize(candidates_gdf, source_files=[REAL_CANDIDATES, DEMO_TRANSFORMERS],
                                       notes="DEMO SCENARIO: Grid costs derived from hypothetical transformer locations. NOT real VIT-AP infrastructure data.")

    # 8. Save demo candidates
    print(f"\n8. Saving demo candidates to {DEMO_CANDIDATES_OUT}...")
    Path(DEMO_CANDIDATES_OUT).parent.mkdir(parents=True, exist_ok=True)
    # Drop geometry for CSV
    candidates_gdf.drop(columns="geometry").to_csv(DEMO_CANDIDATES_OUT, index=False)
    print(f"   Saved {len(candidates_gdf)} candidates")

    # 9. Create enriched fuzzy inputs (demo version)
    print(f"\n9. Creating demo fuzzy inputs: {DEMO_FUZZY_OUT}...")
    fuzzy_inputs = candidates_gdf[[
        "candidate_id",
        "traffic_accessibility_score",
        "grid_connection_cost_inr_lakhs",
        "land_cost_tier",
        "poi_score",
        "existing_chargers_3km",
    ]].copy()

    # Map land_cost_tier to ordinal (same as UnifiedDataLoader._prepare_fuzzy_inputs)
    tier_map = {"very_low": 0, "low": 1, "medium": 2, "high": 3}
    fuzzy_inputs["land_cost_tier_ordinal"] = fuzzy_inputs["land_cost_tier"].map(tier_map).fillna(1)

    # Rename columns to match fuzzy config
    fuzzy_inputs = fuzzy_inputs.rename(columns={
        "traffic_accessibility_score": "traffic_accessibility",
        "grid_connection_cost_inr_lakhs": "grid_connection_cost",
        "poi_score": "poi_attractiveness",
        "existing_chargers_3km": "competition_index",
    })

    # Add predicted_demand from ML predictions (reuse fresh predictions)
    # Load fresh ML predictions
    ml_pred_path = "data/processed/ml_demand_predictions_fresh.csv"
    if Path(ml_pred_path).exists():
        ml_pred = pd.read_csv(ml_pred_path)
        pred_lookup = dict(zip(ml_pred["candidate_id"], ml_pred["predicted_annual_demand_kwh"]))
        fuzzy_inputs["predicted_demand"] = fuzzy_inputs["candidate_id"].map(pred_lookup) / 8760.0
        print(f"   Using fresh ML predictions for predicted_demand")
    else:
        fuzzy_inputs["predicted_demand"] = 0.0
        print(f"   WARNING: No ML predictions found, using 0.0")

    # Add provenance for fuzzy inputs
    fuzzy_tracker = ProvenanceTracker("fuzzy_inputs_demo", "enrich_demo_grid_costs")
    for col in fuzzy_inputs.columns:
        if col == "candidate_id":
            fuzzy_tracker.add_column(col, Provenance.DERIVED, "candidate_generator", "Candidate identifier from generation process")
        elif col in ["traffic_accessibility", "poi_attractiveness", "competition_index", "land_cost_tier", "land_cost_tier_ordinal"]:
            fuzzy_tracker.add_column(col, Provenance.DERIVED, "candidate_generator", "Candidate feature from generation process")
        elif col == "grid_connection_cost":
            fuzzy_tracker.add_column(col, Provenance.SYNTHETIC, "demo_transformer_assumptions",
                                    "Grid connection cost from HYPOTHETICAL demo transformers. NOT real cost.")
        elif col == "predicted_demand":
            fuzzy_tracker.add_column(col, Provenance.DERIVED, "fresh_ml_model", "ML predicted hourly demand from fresh model")

    fuzzy_inputs = fuzzy_tracker.finalize(fuzzy_inputs, source_files=[DEMO_CANDIDATES_OUT, ml_pred_path],
                                           notes="DEMO SCENARIO: grid_connection_cost from hypothetical transformers")
    fuzzy_inputs.to_csv(DEMO_FUZZY_OUT, index=False)
    print(f"   Saved {len(fuzzy_inputs)} fuzzy inputs")

    # 10. Create enriched GA fitness inputs (demo version)
    print(f"\n10. Creating demo GA fitness inputs: {DEMO_GA_OUT}...")
    ga_inputs = candidates_gdf[[
        "candidate_id", "predicted_annual_demand_kwh", "catchment_population",
        "total_capex_inr_lakhs", "latitude", "longitude", "zone"
    ]].copy()

    # Update predicted_annual_demand_kwh from fresh ML predictions
    if Path(ml_pred_path).exists():
        ga_inputs["predicted_annual_demand_kwh"] = ga_inputs["candidate_id"].map(pred_lookup)

    # Add provenance columns (same as enrich_ga_fuzzy_inputs.py)
    ga_inputs["demand_prediction_provenance"] = "DERIVED (from fresh ML model trained on synthetic demand history)"
    ga_inputs["demand_prediction_model"] = "demand_predictor_fresh"
    ga_inputs["demand_prediction_timestamp"] = datetime.now().isoformat()

    # Add provenance for GA inputs
    ga_tracker = ProvenanceTracker("ga_fitness_inputs_demo", "enrich_demo_grid_costs")
    for col in ga_inputs.columns:
        if col == "candidate_id":
            ga_tracker.add_column(col, Provenance.DERIVED, "candidate_generator", "Candidate identifier")
        elif col in ["predicted_annual_demand_kwh", "catchment_population", "latitude", "longitude", "zone"]:
            ga_tracker.add_column(col, Provenance.DERIVED, "candidate_generator", "Candidate feature")
        elif col == "total_capex_inr_lakhs":
            ga_tracker.add_column(col, Provenance.SYNTHETIC, "demo_transformer_assumptions",
                                 "Total CAPEX including HYPOTHETICAL demo grid connection cost. NOT real CAPEX.")
        elif col.startswith("demand_prediction"):
            ga_tracker.add_column(col, Provenance.META, "enrichment", "ML prediction provenance metadata")

    ga_inputs = ga_tracker.finalize(ga_inputs, source_files=[DEMO_CANDIDATES_OUT, ml_pred_path],
                                     notes="DEMO SCENARIO: total_capex_inr_lakhs from hypothetical transformers")
    ga_inputs.to_csv(DEMO_GA_OUT, index=False)
    print(f"   Saved {len(ga_inputs)} GA fitness inputs")

    # 11. Validation summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    print(f"\nDemo Candidates: {DEMO_CANDIDATES_OUT}")
    print(f"  Rows: {len(candidates_gdf)}")
    print(f"  Unique candidate_ids: {candidates_gdf['candidate_id'].nunique()}")
    print(f"  grid_dist_m: {candidates_gdf['grid_dist_m'].min():.1f} - {candidates_gdf['grid_dist_m'].max():.1f} m (all finite)")
    print(f"  grid_connection_cost_inr_lakhs: {candidates_gdf['grid_connection_cost_inr_lakhs'].min():.3f} - {candidates_gdf['grid_connection_cost_inr_lakhs'].max():.3f} (all finite)")
    print(f"  total_capex_inr_lakhs: {candidates_gdf['total_capex_inr_lakhs'].min():.3f} - {candidates_gdf['total_capex_inr_lakhs'].max():.3f} (all finite)")
    print(f"  All CAPEX <= budget ({ga_budget}): {(candidates_gdf['total_capex_inr_lakhs'] <= ga_budget).all()}")

    print(f"\nDemo Fuzzy Inputs: {DEMO_FUZZY_OUT}")
    print(f"  Rows: {len(fuzzy_inputs)}")
    print(f"  grid_connection_cost: {fuzzy_inputs['grid_connection_cost'].min():.3f} - {fuzzy_inputs['grid_connection_cost'].max():.3f} (all finite)")
    print(f"  predicted_demand: {fuzzy_inputs['predicted_demand'].min():.4f} - {fuzzy_inputs['predicted_demand'].max():.4f} kWh/h")

    print(f"\nDemo GA Fitness Inputs: {DEMO_GA_OUT}")
    print(f"  Rows: {len(ga_inputs)}")
    print(f"  total_capex_inr_lakhs: {ga_inputs['total_capex_inr_lakhs'].min():.3f} - {ga_inputs['total_capex_inr_lakhs'].max():.3f} (all finite)")
    print(f"  predicted_annual_demand_kwh: {ga_inputs['predicted_annual_demand_kwh'].min():.1f} - {ga_inputs['predicted_annual_demand_kwh'].max():.1f}")

    # Verify schema compatibility
    print("\n" + "=" * 60)
    print("SCHEMA COMPATIBILITY CHECK")
    print("=" * 60)

    # Fuzzy scorer expects these columns in candidates.csv
    fuzzy_required = ['candidate_id', 'traffic_accessibility_score', 'grid_connection_cost_inr_lakhs',
                      'land_cost_tier', 'poi_score', 'existing_chargers_3km']
    missing_fuzzy = [c for c in fuzzy_required if c not in candidates_gdf.columns]
    print(f"Fuzzy scorer required columns: {'ALL PRESENT' if not missing_fuzzy else f'MISSING: {missing_fuzzy}'}")

    # GA optimizer expects these in candidates_fuzzy_scored.csv (after fuzzy scoring)
    ga_required = ['candidate_id', 'fuzzy_suitability_score', 'total_capex_inr_lakhs',
                   'latitude', 'longitude', 'zone']
    # Note: fuzzy_suitability_score will be added by fuzzy scorer
    print(f"GA optimizer will need (after fuzzy): fuzzy_suitability_score + {ga_required}")

    print("\n" + "=" * 60)
    print("DEMO ENRICHMENT COMPLETE - READY FOR FUZZY + GA")
    print("=" * 60)
    print("\nNext commands to run (DO NOT EXECUTE YET):")
    print(f"  python -m src.fuzzy.scorer --input {DEMO_CANDIDATES_OUT} --output data/processed/candidates_demo_fuzzy_scored.csv")
    print(f"  python -m src.ga.optimizer --candidates data/processed/candidates_demo_fuzzy_scored.csv --output data/processed/ga_solution_demo.csv")

    return 0

if __name__ == "__main__":
    sys.exit(main())