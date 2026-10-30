"""
Simple test for fuzzy scorer with ML model.
"""
import pandas as pd
import numpy as np
from src.ml.demand_predictor import EVChargingDemandPredictor
from src.fuzzy.scorer import FuzzyEVChargingScorer

def test_fuzzy_with_ml():
    """Test fuzzy scorer integration with ML predictor."""
    print("Testing fuzzy scorer with ML predictor...")

    # Create test candidate data
    candidates_df = pd.DataFrame({
        'candidate_id': ['CAND_001', 'CAND_002', 'CAND_003'],
        'name': ['Candidate 1', 'Candidate 2', 'Candidate 3'],
        'zone': ['academic', 'hostel_boys', 'commercial'],
        'poi_score': [0.8, 0.6, 0.9],
        'traffic_accessibility_score': [0.6, 0.4, 0.8],
        'grid_connection_cost_inr_lakhs': [2.5, 3.0, 1.5],
        'land_cost_tier': ['very_low', 'low', 'high'],
        'existing_chargers_3km': [0, 2, 1]
    })

    # Create simple weather data
    weather_df = pd.DataFrame({
        'timestamp': pd.date_range('2024-01-01 12:00:00', periods=1, freq='h'),
        'temperature_c': [25.0],
        'relative_humidity_pct': [60.0],
        'precipitation_mm': [0.0]
    })

    # Create simple demand history for training
    demand_history = pd.DataFrame({
        'candidate_id': ['CAND_001', 'CAND_002', 'CAND_003'] * 4,
        'timestamp': pd.date_range('2024-01-01 00:00:00', periods=12, freq='3h'),
        'hour': [0, 3, 6, 9, 12, 15, 18, 21, 0, 3, 6, 9],
        'day_of_week': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        'month': [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        'is_weekend': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        'is_holiday': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        'is_exam_period': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        'demand_kwh': [5.0, 3.0, 8.0, 6.0, 4.0, 9.0, 7.0, 2.0, 5.5, 3.5, 8.5, 6.5],
        'session_count': [0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0],
        'avg_session_kwh': [15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0],
        'temperature_c': [20.0, 20.0, 20.0, 22.0, 22.0, 22.0, 24.0, 24.0, 24.0, 25.0, 25.0, 25.0],
        'humidity_pct': [60.0, 60.0, 60.0, 55.0, 55.0, 55.0, 50.0, 50.0, 50.0, 45.0, 45.0, 45.0],
        'precipitation_mm': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        'zone': ['academic', 'hostel_boys', 'commercial'] * 4
    })

    # Test 1: Train ML predictor
    print("\n1. Training ML predictor...")
    predictor = EVChargingDemandPredictor(model_type="random_forest", seed=42)
    metrics = predictor.train(demand_history, candidates_df, test_size=0.2, temporal_split=False)
    print(f"   Training completed. Test RMSE: {metrics['test_rmse']:.4f}")

    # Test 2: Save and load model
    print("\n2. Testing model save/load...")
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_path = os.path.join(tmp_dir, "test_model")
        predictor.save_model(model_path)

        # Load model
        predictor_loaded = EVChargingDemandPredictor()
        predictor_loaded.load_model(model_path)
        print("   Model saved and loaded successfully")

    # Test 3: Fuzzy scorer with ML model
    print("\n3. Testing fuzzy scorer with ML model...")
    scorer = FuzzyEVChargingScorer(ml_model_path="models/demand_predictor")

    # Since we don't have a saved model yet, let's train one and save it first
    import os
    os.makedirs("models", exist_ok=True)
    predictor.save_model("models/demand_predictor")

    # Now test the scorer
    scored_df = scorer.score_candidates(candidates_df, weather_df=weather_df)

    print(f"   Scored {len(scored_df)} candidates")
    print(f"   Suitability scores: {scored_df['fuzzy_suitability_score'].tolist()}")
    print(f"   Demand prediction sources: {scored_df['demand_prediction_source'].tolist()}")

    # Test 4: Fuzzy scorer without ML (fallback)
    print("\n4. Testing fuzzy scorer without ML (fallback)...")
    scorer_no_ml = FuzzyEVChargingScorer()  # No ML model

    scored_df_no_ml = scorer_no_ml.score_candidates(candidates_df)

    print(f"   Scored {len(scored_df_no_ml)} candidates (no ML)")
    print(f"   Suitability scores: {scored_df_no_ml['fuzzy_suitability_score'].tolist()}")
    if 'demand_prediction_source' in scored_df_no_ml.columns:
        print(f"   Demand prediction sources: {scored_df_no_ml['demand_prediction_source'].tolist()}")

    print("\nAll tests completed successfully!")
    return True

if __name__ == "__main__":
    test_fuzzy_with_ml()