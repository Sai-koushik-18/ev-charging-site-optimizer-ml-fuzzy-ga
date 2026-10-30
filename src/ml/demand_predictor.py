"""
Demand prediction model for EV charging infrastructure planning.
Trains ML models to predict hourly EV charging demand based on temporal,
weather, and candidate-specific features.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import joblib
import json
from loguru import logger

from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.preprocessing import StandardScaler

from ..data.config_loader import get_config
from ..data.provenance import ProvenanceTracker, Provenance


class EVChargingDemandPredictor:
    """
    Machine learning model for predicting EV charging demand at candidate locations.

    Features engineered:
    - Temporal: hour, day_of_week, month, is_weekend, is_holiday, is_exam_period
    - Weather: temperature_c, humidity_pct, precipitation_mm
    - Candidate-specific: zone (one-hot encoded), pois_score (from candidates)
    """

    def __init__(self, model_type: str = "random_forest", config: Optional[Any] = None, seed: int = 42):
        """
        Initialize the demand predictor.

        Args:
            model_type: Type of model ("random_forest" or "gradient_boosting")
            config: Configuration object
            seed: Random seed for reproducibility
        """
        self.config = config or get_config("settings")
        self.model_type = model_type
        self.seed = seed
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = None
        self.is_trained = False
        self.zone_columns = None  # To store expected zone dummy columns from training

        # Initialize model
        if model_type == "random_forest":
            self.model = RandomForestRegressor(
                n_estimators=100,
                max_depth=10,
                min_samples_split=5,
                min_samples_leaf=2,
                random_state=seed,
                n_jobs=-1
            )
        elif model_type == "gradient_boosting":
            self.model = GradientBoostingRegressor(
                n_estimators=100,
                learning_rate=0.1,
                max_depth=6,
                min_samples_split=5,
                min_samples_leaf=2,
                random_state=seed
            )
        else:
            raise ValueError(f"Unsupported model_type: {model_type}")

        logger.info(f"Initialized {model_type} demand predictor with seed {seed}")

    def engineer_features(self, df: pd.DataFrame, candidates_gdf: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Engineer features for demand prediction.

        Args:
            df: DataFrame with demand_history data
            candidates_gdf: GeoDataFrame with candidate information (for zone info)

        Returns:
            DataFrame with engineered features
        """
        logger.info("Engineering features for demand prediction")

        # Create a copy to avoid modifying original
        features_df = df.copy()

        # Ensure timestamp is datetime
        if 'timestamp' in features_df.columns:
            features_df['timestamp'] = pd.to_datetime(features_df['timestamp'])

            # Temporal features
            features_df['hour'] = features_df['timestamp'].dt.hour
            features_df['day_of_week'] = features_df['timestamp'].dt.dayofweek  # 0=Monday
            features_df['month'] = features_df['timestamp'].dt.month
            features_df['is_weekend'] = features_df['day_of_week'].isin([5, 6]).astype(int)

            # Note: is_holiday and is_exam_period should already be in the data from synthetic generation

        # Weather features (should already be present)
        weather_features = ['temperature_c', 'humidity_pct', 'precipitation_mm']
        for feat in weather_features:
            if feat not in features_df.columns:
                logger.warning(f"Weather feature {feat} not found in data")

        # Candidate-specific features
        if candidates_gdf is not None and 'candidate_id' in features_df.columns:
            logger.info(f"Processing candidate-specific features")
            logger.info(f"features_df columns before merge: {list(features_df.columns)}")
            logger.info(f"candidates_gdf columns: {list(candidates_gdf.columns)}")
            # Merge with candidate data to get zone and other features
            # Rename zone column in features_df to avoid conflict with candidate_features
            features_df_renamed = features_df.rename(columns={'zone': 'demand_zone'})
            candidate_features = candidates_gdf[['candidate_id', 'zone']].copy()

            # Add POI score if available
            if 'pois_score' in candidates_gdf.columns:
                candidate_features['pois_score'] = candidates_gdf['pois_score']

            # Merge
            features_df = features_df_renamed.merge(candidate_features, on='candidate_id', how='left')
            logger.info(f"features_df columns after merge: {list(features_df.columns)}")
            if 'zone' in features_df.columns:
                logger.info(f"Zone column values after merge: {features_df['zone'].unique()}")
            if 'demand_zone' in features_df.columns:
                logger.info(f"Demand zone column values after merge: {features_df['demand_zone'].unique()}")

            # One-hot encode zone
            if 'zone' in features_df.columns:
                logger.info(f"About to one-hot encode zone column")
                zone_dummies = pd.get_dummies(features_df['zone'], prefix='zone')
                # DEBUG: Print zone info
                logger.info(f"Zone column values: {features_df['zone'].unique()}")
                logger.info(f"Zone dummies columns: {list(zone_dummies.columns)}")
                features_df = pd.concat([features_df, zone_dummies], axis=1)
                logger.info(f"features_df columns after zone encoding: {list(features_df.columns)}")

        # Define base features for modeling (always the same)
        base_features = [
            'hour', 'day_of_week', 'month', 'is_weekend', 'is_holiday', 'is_exam_period',
            'temperature_c', 'humidity_pct', 'precipitation_mm'
        ]

        # Ensure consistency of zone columns between training and prediction
        # First, identify existing zone columns in the data
        zone_cols = [col for col in features_df.columns if col.startswith('zone_')]

        if self.zone_columns is not None:
            # During prediction, ensure all expected zone columns are present
            logger.info(f"Checking zone consistency. Expected zone columns: {self.zone_columns}")
            logger.info(f"Current features_df columns: {list(features_df.columns)}")
            for zone_col in self.zone_columns:
                if zone_col not in features_df.columns:
                    logger.info(f"Adding missing zone column: {zone_col} with value 0")
                    features_df[zone_col] = 0
                else:
                    logger.info(f"Zone column {zone_col} already present")
            # Also remove any unexpected zone columns (shouldn't happen but be safe)
            current_zone_cols = [col for col in features_df.columns if col.startswith('zone_')]
            unexpected_zone_cols = [col for col in current_zone_cols if col not in self.zone_columns]
            logger.info(f"Current zone columns: {current_zone_cols}")
            logger.info(f"Unexpected zone columns: {unexpected_zone_cols}")
            for col in unexpected_zone_cols:
                if col in features_df.columns:
                    logger.info(f"Removing unexpected zone column from features_df: {col}")
                    features_df = features_df.drop(columns=[col])
        elif zone_cols and not self.is_trained:
            # During training, store the zone columns we saw
            self.zone_columns = sorted(zone_cols)

        # Define feature columns for modeling
        if self.feature_names is None:
            # During training, determine the final feature order
            self.feature_names = base_features.copy()

            # Add zone columns that were seen during training
            if self.zone_columns is not None:
                self.feature_names.extend(self.zone_columns)

            # Add pois_score if available in the data
            if 'pois_score' in features_df.columns:
                self.feature_names.append('pois_score')

            logger.info(f"Training feature set determined: {self.feature_names}")
        else:
            # During prediction, use the saved feature order
            # Ensure features_df has all expected features (add missing ones with default values)
            missing_features = [f for f in self.feature_names if f not in features_df.columns]
            for feature in missing_features:
                logger.info(f"Adding missing feature {feature} with default value 0")
                features_df[feature] = 0

            # Remove any unexpected features that weren't in training
            unexpected_features = [f for f in features_df.columns if f not in self.feature_names and f not in base_features]
            if unexpected_features:
                logger.info(f"Removing unexpected features: {unexpected_features}")
                features_df = features_df.drop(columns=[col for col in unexpected_features if col in features_df.columns])

        # Final verification - ensure no duplicate feature names
        if len(self.feature_names) != len(set(self.feature_names)):
            duplicates = [name for name in self.feature_names if self.feature_names.count(name) > 1]
            logger.warning(f"Duplicate feature names detected: {set(duplicates)}")
            # Remove duplicates while preserving order
            seen = set()
            self.feature_names = [x for x in self.feature_names if not (x in seen or seen.add(x))]

        logger.info(f"Engineered {len(self.feature_names)} features: {self.feature_names}")

        return features_df

    def prepare_training_data(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """
        Prepare features and target for training.

        Args:
            df: DataFrame with features and target

        Returns:
            Tuple of (X, y) arrays
        """
        if self.feature_names is None:
            raise ValueError("Features not engineered. Call engineer_features first.")

        # Extract features and target
        # Ensure we only get numeric values by selecting columns and converting
        feature_data = df[self.feature_names].copy()

        # DEBUG: Log info about feature data
        logger.info(f"Feature data columns: {list(feature_data.columns)}")
        logger.info(f"Feature data dtypes:\n{feature_data.dtypes}")
        logger.info(f"Feature data head:\n{feature_data.head()}")

        # Convert all feature columns to numeric, coercing errors to NaN
        for col in feature_data.columns:
            original_dtype = feature_data[col].dtype
            feature_data[col] = pd.to_numeric(feature_data[col], errors='coerce')
            new_dtype = feature_data[col].dtype
            if original_dtype != new_dtype:
                logger.info(f"Column '{col}' converted from {original_dtype} to {new_dtype}")

        # Convert to float to ensure we have a proper numeric array for isnan checks
        feature_data = feature_data.astype(float)

        X = feature_data.values
        logger.info(f"X shape: {X.shape}, X dtype: {X.dtype}")
        logger.info(f"X sample: {X[:2] if len(X) > 0 else X}")

        y = pd.to_numeric(df['demand_kwh'], errors='coerce').values
        logger.info(f"y shape: {y.shape}, y dtype: {y.dtype}")

        # Handle any missing values
        if np.any(np.isnan(X)):
            logger.warning("Found NaN values in features, filling with column means")
            # Compute column means ignoring NaN values
            col_means = np.nanmean(X, axis=0)
            # Replace NaN means with 0 (for columns that are all NaN)
            col_means = np.where(np.isnan(col_means), 0, col_means)
            # Where NaN, replace with column mean
            inds = np.where(np.isnan(X))
            X[inds] = np.take(col_means, inds[1])

        if np.any(np.isnan(y)):
            logger.warning("Found NaN values in target, filling with median")
            y_median = np.nanmedian(y)
            # If all values are NaN, use 0 as median
            if np.isnan(y_median):
                y_median = 0
            y = np.where(np.isnan(y), y_median, y)

        logger.info(f"Prepared training data: X shape {X.shape}, y shape {y.shape}")
        return X, y

    def train(self, df: pd.DataFrame, candidates_gdf: Optional[pd.DataFrame] = None,
              test_size: float = 0.2, temporal_split: bool = True) -> Dict[str, float]:
        """
        Train the demand prediction model.

        Args:
            df: DataFrame with demand_history data
            candidates_gdf: GeoDataFrame with candidate information
            test_size: Fraction of data to use for testing
            temporal_split: If True, split by time (train on earlier, test on later)

        Returns:
            Dictionary with evaluation metrics
        """
        logger.info(f"Training {self.model_type} model with temporal_split={temporal_split}")

        # Engineer features
        features_df = self.engineer_features(df, candidates_gdf)

        # Prepare data
        X, y = self.prepare_training_data(features_df)

        # Split data
        if temporal_split and 'timestamp' in features_df.columns:
            # Sort by timestamp for temporal split
            sorted_idx = np.argsort(features_df['timestamp'].values)
            X_sorted = X[sorted_idx]
            y_sorted = y[sorted_idx]

            # Split chronologically
            split_idx = int(len(X_sorted) * (1 - test_size))
            X_train, X_test = X_sorted[:split_idx], X_sorted[split_idx:]
            y_train, y_test = y_sorted[:split_idx], y_sorted[split_idx:]

            logger.info(f"Temporal split: train on earlier {len(X_train)} samples, test on later {len(X_test)} samples")
        else:
            # Random split
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=test_size, random_state=self.seed
            )
            logger.info(f"Random split: train on {len(X_train)} samples, test on {len(X_test)} samples")

        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train model
        logger.info(f"Training {self.model_type} model...")
        self.model.fit(X_train_scaled, y_train)
        self.is_trained = True

        # Evaluate
        y_train_pred = self.model.predict(X_train_scaled)
        y_test_pred = self.model.predict(X_test_scaled)

        metrics = {
            'train_rmse': np.sqrt(mean_squared_error(y_train, y_train_pred)),
            'test_rmse': np.sqrt(mean_squared_error(y_test, y_test_pred)),
            'train_mae': mean_absolute_error(y_train, y_train_pred),
            'test_mae': mean_absolute_error(y_test, y_test_pred),
            'train_r2': r2_score(y_train, y_train_pred),
            'test_r2': r2_score(y_test, y_test_pred)
        }

        logger.info(f"Training completed. Test RMSE: {metrics['test_rmse']:.4f}, Test R²: {metrics['test_r2']:.4f}")

        # Feature importance (if available)
        if hasattr(self.model, 'feature_importances_'):
            feature_importance = dict(zip(self.feature_names, self.model.feature_importances_))
            logger.info(f"Top 5 features: {sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)[:5]}")

        return metrics

    def predict(self, df: pd.DataFrame, candidates_gdf: Optional[pd.DataFrame] = None) -> np.ndarray:
        """
        Predict demand for new data.

        Args:
            df: DataFrame with features (same format as training data)
            candidates_gdf: GeoDataFrame with candidate information

        Returns:
            Array of predicted demand values
        """
        if not self.is_trained:
            raise ValueError("Model not trained. Call train() first.")

        # Engineer features
        features_df = self.engineer_features(df, candidates_gdf)

        # Extract features and ensure they are numeric
        feature_data = features_df[self.feature_names].copy()

        # Convert all feature columns to numeric, coercing errors to NaN
        for col in feature_data.columns:
            feature_data[col] = pd.to_numeric(feature_data[col], errors='coerce')

        X = feature_data.values

        # Handle missing values
        try:
            if np.any(np.isnan(X)):
                logger.warning("Found NaN values in features for prediction, filling with column means")
                col_means = np.nanmean(X, axis=0)
                # Replace NaN means with 0 (for columns that are all NaN)
                col_means = np.where(np.isnan(col_means), 0, col_means)
                inds = np.where(np.isnan(X))
                X[inds] = np.take(col_means, inds[1])
        except (TypeError, ValueError) as e:
            logger.warning(f"Error handling NaN values: {e}. Checking array dtype: {X.dtype}")
            # If we can't handle NaNs due to dtype issues, replace with zeros
            if X.dtype == object:
                logger.warning("Array has object dtype, converting to float and replacing NaNs with zeros")
                try:
                    X = X.astype(float)
                except:
                    X = np.zeros_like(X, dtype=float)
                # Now handle NaNs
                if np.any(np.isnan(X)):
                    col_means = np.nanmean(X, axis=0)
                    col_means = np.where(np.isnan(col_means), 0, col_means)
                    inds = np.where(np.isnan(X))
                    X[inds] = np.take(col_means, inds[1])

        # Scale features
        X_scaled = self.scaler.transform(X)

        # Predict
        predictions = self.model.predict(X_scaled)

 # Ensure non-negative predictions
        predictions = np.maximum(predictions, 0)

        logger.info(f"Generated {len(predictions)} demand predictions")
        return predictions

    def save_model(self, filepath: str):
        """
        Save the trained model and scaler.

        Args:
            filepath: Path to save model (without extension)
        """
        if not self.is_trained:
            raise ValueError("No trained model to save.")

        model_path = Path(filepath)
        model_path.parent.mkdir(parents=True, exist_ok=True)

        # Save model and scaler
        joblib.dump(self.model, f"{filepath}_model.pkl")
        joblib.dump(self.scaler, f"{filepath}_scaler.pkl")

        # Save metadata
        metadata = {
            'model_type': self.model_type,
            'feature_names': self.feature_names,
            'seed': self.seed,
            'is_trained': self.is_trained,
            'zone_columns': self.zone_columns
        }
        with open(f"{filepath}_metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Model saved to {filepath}_*")

    def load_model(self, filepath: str):
        """
        Load a trained model and scaler.

        Args:
            filepath: Path to load model (without extension)
        """
        try:
            self.model = joblib.load(f"{filepath}_model.pkl")
            self.scaler = joblib.load(f"{filepath}_scaler.pkl")

            # Load metadata
            with open(f"{filepath}_metadata.json", 'r') as f:
                metadata = json.load(f)

            self.feature_names = metadata['feature_names']
            self.is_trained = metadata['is_trained']
            self.zone_columns = metadata.get('zone_columns', None)

            logger.info(f"Model loaded from {filepath}_*")
        except FileNotFoundError as e:
            logger.error(f"Could not load model: {e}")
            raise


def create_demand_predictor(model_type: str = "random_forest", config: Optional[Any] = None, seed: int = 42) -> EVChargingDemandPredictor:
    """
    Factory function to create a demand predictor.

    Args:
        model_type: Type of model ("random_forest" or "gradient_boosting")
        config: Configuration object
        seed: Random seed

    Returns:
        Configured EVChargingDemandPredictor instance
    """
    return EVChargingDemandPredictor(model_type=model_type, config=config, seed=seed)


def predict_demand_for_candidates(candidates_gdf: pd.DataFrame,
                                 weather_df: pd.DataFrame,
                                 model_path: str = None,
                                 years: List[int] = [2024]) -> pd.DataFrame:
    """
    Convenience function to predict demand for candidates using a trained model.

    Args:
        candidates_gdf: GeoDataFrame with candidate locations
        weather_df: DataFrame with hourly weather data
        model_path: Path to saved model (if None, will train new model)
        years: List of years to predict for

    Returns:
        DataFrame with predictions
    """
    logger.info("Creating demand predictions for candidates")

    # If model path provided, load existing model
    if model_path and Path(f"{model_path}_model.pkl").exists():
        predictor = EVChargingDemandPredictor()
        predictor.load_model(model_path)
        logger.info(f"Loaded existing model from {model_path}")
    else:
        # Train new model
        logger.info("Training new demand prediction model")

        # First generate synthetic demand data for training
        from ..data.synthetic_generator import generate_synthetic_demand

        demand_history = generate_synthetic_demand(candidates_gdf, weather_df)

        # Create and train predictor
        predictor = EVChargingDemandPredictor()
        metrics = predictor.train(demand_history, candidates_gdf)
        logger.info(f"Model training completed with metrics: {metrics}")

        # Save model
        model_dir = Path("models")
        model_dir.mkdir(exist_ok=True)
        predictor.save_model(str(model_dir / "demand_predictor"))

    # Generate features for prediction (similar to training data generation)
    # We'll create a minimal dataset with all combinations
    predictions_list = []

    for _, candidate in candidates_gdf.iterrows():
        candidate_id = candidate['candidate_id']

        # Create timestamps for prediction period
        for year in years:
            timestamps = pd.date_range(
                start=f"{year}-01-01 00:00:00",
                end=f"{year}-12-31 23:00:00",
                freq="h"
            )

            for ts in timestamps:
                # Get weather data for this timestamp (simplified - use average or nearest)
                # In practice, you'd join with actual weather data
                weather_row = weather_df.iloc[0] if len(weather_df) > 0 else pd.Series({
                    'temperature_c': 25.0,
                    'relative_humidity_pct': 70.0,
                    'precipitation_mm': 0.0
                })

                predictions_list.append({
                    'candidate_id': candidate_id,
                    'timestamp': ts,
                    'hour': ts.hour,
                    'day_of_week': ts.dayofweek,
                    'month': ts.month,
                    'is_weekend': ts.dayofweek in [5, 6],
                    'is_holiday': False,  # Simplified
                    'is_exam_period': False,  # Simplified
                    'temperature_c': weather_row.get('temperature_c', 25.0),
                    'humidity_pct': weather_row.get('relative_humidity_pct', 70.0),
                    'precipitation_mm': weather_row.get('precipitation_mm', 0.0),
                    'zone': candidate.get('zone', 'unknown')
                })

    pred_df = pd.DataFrame(predictions_list)

    # Make predictions
    pred_df['predicted_demand_kwh'] = predictor.predict(pred_df, candidates_gdf)

    logger.info(f"Generated {len(pred_df)} demand predictions")
    return pred_df


if __name__ == "__main__":
    # Example usage
    logger.info("EV charging demand predictor module")
    logger.info("Usage: from src.ml.demand_predictor import EVChargingDemandPredictor, predict_demand_for_candidates")