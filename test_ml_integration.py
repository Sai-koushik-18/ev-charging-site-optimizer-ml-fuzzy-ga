"""
Quick integration test for ML pipeline with fuzzy scorer.
"""
import pandas as pd
import numpy as np
from src.ml.demand_predictor import EVChargingDemandPredictor
from src.fuzzy.scorer import FuzzyEVChargingScorer
from src.data.config_loader import get_config

def test_ml_fuzzy_integration():
    """Test that ML predictor works with fuzzy scorer."""
    print("Testing ML-Fuzzy integration...")

    # Create minimal test data
    np.random.seed(42)
    n_samples = 20

    # Test candidates data
    candidates_df = pd.DataFrame({
        'candidate_id': [f'CAND_{i:03d}' for i in range(n_samples)],
        'name': [f'Candidate {i}' for i in range(n_samples)],
        'zone': np.random.choice(['academic', 'hostel_boys', 'admin', 'commercial'], n_samples),
        'pois_score': np.random.uniform(0.5, 1.0, n_samples),
        'traffic_accessibility_score': np.random.uniform(20, 80, n_samples),
        'grid_connection_cost_inr_lakhs': np.random.uniform(1.0, 10.0, n_samples),
        'land_cost_tier': np.random.choice(['low', 'medium', 'high'], n_samples),
        'existing_chargers_3km': np.random.randint(0, 5, n_samples)
    })

    # Test weather data
    weather_df = pd.DataFrame({
        'timestamp': pd.date_range('2024-01-01', periods=24, freq='h'),
        'temperature_c': np.random.uniform(15, 35, 24),
        'relative_humidity_pct': np.random.uniform(30, 90, 24),
        'precipitation_mm': np.random.uniform(0, 10, 24)
    })

    # Test 1: ML predictor alone
    print("\n1. Testing ML predictor alone...")
    predictor = EVChargingDemandPredictor(model_type="random_forest", seed=42)

    # Create synthetic demand data for training
    from src.data.synthetic_generator import generate_synthetic_demand
    import geopandas as gpd

    # Convert candidates to GeoDataFrame for synthetic generator
    candidates_gdf = gpd.GeoDataFrame(
        candidates_df,
        geometry=gpd.points_from_xy([80.48]*n_samples, [16.51]*n_samples),
        crs="EPSG:4326"
    )

    demand_history = generate_synthetic_demand(candidates_gdf, weather_df)
    print(f"Generated demand history with {len(demand_history)} rows")

    # Train predictor
    metrics = predictor.train(demand_history, candidates_gdf, test_size=0.2, temporal_split=False)
    print(f"Training completed. Test RMSE: {metrics['test_rmse']:.4f}")

    # Test 2: Fuzzy scorer with ML model
    print("\n2. Testing fuzzy scorer with ML model...")
    scorer = FuzzyEVChargingScorer(ml_model_path="models/demand_predictor")

    # Score candidates
    scored_df = scorer.score_candidates(candidates_df, weather_df=weather_df, demand_history_df=demand_history)

    print(f"Scored {len(scored_df)} candidates")
    print(f"Suitability score range: {scored_df['fuzzy_suitability_score'].min():.3f} - {scored_df['fuzzy_suitability_score'].max():.3f}")
    print(f"Demand prediction sources: {scored_df['demand_prediction_source'].value_counts().to_dict()}")

    # Test 3: Fuzzy scorer without ML model (should fall back to POI proxy)
    print("\n3. Testing fuzzy scorer without ML model (fallback)...")
    scorer_no_ml = FuzzyEVChargingScorer()  # No ML model path

    scored_df_no_ml = scorer_no_ml.score_candidates(candidates_df)

    print(f"Scored {len(scored_df_no_ml)} candidates (no ML)")
    print(f"Suitability score range: {scored_df_no_ml['fuzzy_suitability_score'].min():.3f} - {scored_df_no_ml['fuzzy_suitability_score'].max():.3f}")
    if 'demand_prediction_source' in scored_df_no_ml.columns:
        print(f"Demand prediction sources: {scored_df_no_ml['demand_prediction_source'].value_counts().to_dict()}")

    print("\nIntegration test completed successfully!")
    return True

if __name__ == "__main__":
    test_ml_fuzzy_integration()