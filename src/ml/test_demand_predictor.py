"""
Tests for the demand prediction ML pipeline.
"""

import unittest
import pandas as pd
import numpy as np
from pathlib import Path
import tempfile
import os

from src.ml.demand_predictor import EVChargingDemandPredictor, predict_demand_for_candidates
from src.data.config_loader import get_config


class TestDemandPredictor(unittest.TestCase):
    """Test cases for the EVChargingDemandPredictor demand predictor."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = get_config("settings")
        self.predictor = EVChargingDemandPredictor(model_type="random_forest", config=self.config, seed=42)

        # Create small synthetic dataset for testing
        np.random.seed(42)
        n_samples = 100

        self.test_df = pd.DataFrame({
            'candidate_id': [f'CAND_{i:03d}' for i in range(n_samples)],
            'timestamp': pd.date_range('2024-01-01', periods=n_samples, freq='h'),
            'hour': list(range(24)) * (n_samples // 24) + list(range(n_samples % 24)),
            'day_of_week': np.random.randint(0, 7, n_samples),
            'month': np.random.randint(1, 13, n_samples),
            'is_weekend': np.random.randint(0, 2, n_samples),
            'is_holiday': np.random.randint(0, 2, n_samples),
            'is_exam_period': np.random.randint(0, 2, n_samples),
            'demand_kwh': np.random.uniform(0, 20, n_samples),
            'session_count': np.random.randint(0, 10, n_samples),
            'avg_session_kwh': 15.0,
            'temperature_c': np.random.uniform(15, 35, n_samples),
            'humidity_pct': np.random.uniform(30, 90, n_samples),
            'precipitation_mm': np.random.uniform(0, 20, n_samples),
            'zone': np.random.choice(['academic', 'hostel_boys', 'admin', 'commercial'], n_samples)
        })

        # Create candidate GeoDataFrame-like structure
        self.test_candidates = pd.DataFrame({
            'candidate_id': ['CAND_001', 'CAND_002', 'CAND_003'],
            'zone': ['academic', 'hostel_boys', 'commercial'],
            'pois_score': [0.8, 0.6, 0.9]
        })

    def test_feature_engineering(self):
        """Test feature engineering functionality."""
        features_df = self.predictor.engineer_features(self.test_df, self.test_candidates)

        # Check that expected features are present
        expected_features = ['hour', 'day_of_week', 'month', 'is_weekend', 'is_holiday', 'is_exam_period',
                           'temperature_c', 'humidity_pct', 'precipitation_mm']

        for feature in expected_features:
            self.assertIn(feature, features_df.columns, f"Missing feature: {feature}")

        # Check that zone one-hot encoding was added
        zone_features = [col for col in features_df.columns if col.startswith('zone_')]
        self.assertGreater(len(zone_features), 0, "No zone one-hot encoded features found")

        # Check that pois_score was added if available
        self.assertIn('pois_score', features_df.columns, "pois_score feature not found")

        # Check that feature_names attribute is set
        self.assertIsNotNone(self.predictor.feature_names, "feature_names not set")
        self.assertGreater(len(self.predictor.feature_names), 0, "No feature names stored")

    def test_prepare_training_data(self):
        """Test training data preparation."""
        # First engineer features
        features_df = self.predictor.engineer_features(self.test_df, self.test_candidates)

        # Prepare training data
        X, y = self.predictor.prepare_training_data(features_df)

        # Check shapes
        self.assertEqual(len(X), len(y), "X and y length mismatch")
        self.assertEqual(len(X), len(features_df), "X length doesn't match dataframe length")
        self.assertEqual(X.shape[1], len(self.predictor.feature_names),
                        "Number of features doesn't match feature_names length")

        # Check for NaN values
        self.assertFalse(np.any(np.isnan(X)), "NaN values found in features")
        self.assertFalse(np.any(np.isnan(y)), "NaN values found in target")

    def test_train_and_predict(self):
        """Test model training and prediction."""
        # Train the model
        metrics = self.predictor.train(self.test_df, self.test_candidates, test_size=0.2, temporal_split=False)

        # Check that training completed successfully
        self.assertTrue(self.predictor.is_trained, "Model not marked as trained")
        self.assertIsNotNone(self.predictor.model, "Model object is None")

        # Check that metrics are reasonable
        self.assertIn('test_rmse', metrics, "test_rmse not in metrics")
        self.assertIn('test_mae', metrics, "test_mae not in metrics")
        self.assertIn('test_r2', metrics, "test_r2 not in metrics")

        # RMSE and MAE should be non-negative
        self.assertGreaterEqual(metrics['test_rmse'], 0, "test_rmse should be non-negative")
        self.assertGreaterEqual(metrics['test_mae'], 0, "test_mae should be non-negative")

        # R² should be between -inf and 1 (but for a reasonable model should be > 0)
        self.assertLessEqual(metrics['test_r2'], 1.0, "test_r2 should be <= 1.0")

        # Test prediction
        predictions = self.predictor.predict(self.test_df.iloc[:10], self.test_candidates.iloc[:3])

        # Check predictions
        self.assertEqual(len(predictions), 10, "Wrong number of predictions")
        self.assertTrue(np.all(predictions >= 0), "Negative predictions found")

        # Check that predictions are finite
        self.assertTrue(np.all(np.isfinite(predictions)), "Non-finite predictions found")

    def test_save_and_load_model(self):
        """Test model saving and loading."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            model_path = Path(tmp_dir) / "test_model"

            # Train a model
            self.predictor.train(self.test_df, self.test_candidates, test_size=0.2, temporal_split=False)

            # Save the model
            self.predictor.save_model(str(model_path))

            # Check that files were created
            self.assertTrue((model_path.parent / (model_path.name + "_model.pkl")).exists(),
                           "Model file not saved")
            self.assertTrue((model_path.parent / (model_path.name + "_scaler.pkl")).exists(),
                           "Scaler file not saved")
            self.assertTrue((model_path.parent / (model_path.name + "_metadata.json")).exists(),
                           "Metadata file not saved")

            # Create a new predictor and load the model
            new_predictor = EVChargingDemandPredictor(model_type="random_forest", config=self.config, seed=42)
            new_predictor.load_model(str(model_path))

            # Check that the loaded predictor is trained
            self.assertTrue(new_predictor.is_trained, "Loaded model not marked as trained")

            # Check that feature names match
            self.assertEqual(self.predictor.feature_names, new_predictor.feature_names,
                            "Feature names don't match after load")

            # Test that predictions are the same
            orig_pred = self.predictor.predict(self.test_df.iloc[:5], self.test_candidates.iloc[:2])
            loaded_pred = new_predictor.predict(self.test_df.iloc[:5], self.test_candidates.iloc[:2])

            np.testing.assert_array_almost_equal(orig_pred, loaded_pred, decimal=6,
                                                err_msg="Predictions don't match after save/load")

    def test_temporal_split(self):
        """Test temporal split functionality."""
        # Train with temporal split
        metrics_temp = self.predictor.train(self.test_df, self.test_candidates,
                                          test_size=0.3, temporal_split=True)

        # Train with random split for comparison
        predictor2 = EVChargingDemandPredictor(model_type="random_forest", config=self.config, seed=42)
        metrics_rand = predictor2.train(self.test_df, self.test_candidates,
                                      test_size=0.3, temporal_split=False)

        # Both should produce valid metrics
        self.assertIn('test_rmse', metrics_temp)
        self.assertIn('test_rmse', metrics_rand)

        # The metrics might be different but both should be valid numbers
        self.assertIsInstance(metrics_temp['test_rmse'], (int, float))
        self.assertIsInstance(metrics_rand['test_rmse'], (int, float))


if __name__ == '__main__':
    unittest.main()