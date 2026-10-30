"""
Fuzzy Logic Scoring System for EV Charging Station Suitability
Implements Mamdani fuzzy inference system for scoring candidate locations.

Provenance: SYNTHETIC (expert-defined parameters with synthetic data application)
"""

import numpy as np
import pandas as pd
import yaml
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import skfuzzy as fuzz
from skfuzzy import control as ctrl

logger = logging.getLogger(__name__)


class FuzzyEVChargingScorer:
    """
    Fuzzy logic scorer for EV charging station suitability.
    Uses Mamdani inference system with expert-defined membership functions.
    Integrates with ML demand prediction model when available.
    """

    def __init__(self, config_path: str = "config/fuzzy_config.yaml",
                 ml_model_path: Optional[str] = None):
        """
        Initialize the fuzzy logic scorer.

        Args:
            config_path: Path to fuzzy configuration YAML file
            ml_model_path: Path to saved ML demand prediction model (optional)
        """
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.ml_model_path = ml_model_path
        self.ml_predictor = None
        self.fuzzy_system = None
        self._build_fuzzy_system()
        self._load_ml_model_if_available()

    def _load_config(self) -> Dict[str, Any]:
        """Load fuzzy configuration from YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"Loaded fuzzy config from {self.config_path}")
            return config['fuzzy']
        except Exception as e:
            logger.error(f"Failed to load fuzzy config: {e}")
            raise

    def _load_ml_model_if_available(self):
        """Load ML demand prediction model if path is provided and file exists."""
        if self.ml_model_path:
            try:
                from ..ml.demand_predictor import EVChargingDemandPredictor
                model_path = Path(self.ml_model_path)
                if model_path.with_suffix('.pkl').exists() or \
                   (model_path.parent / (model_path.name + '_model.pkl')).exists():
                    self.ml_predictor = EVChargingDemandPredictor()
                    # Try to find the model file
                    model_file = str(model_path) if model_path.with_suffix('.pkl').exists() \
                        else str(model_path.parent / (model_path.name + '_model.pkl'))
                    self.ml_predictor.load_model(model_file.replace('_model.pkl', ''))
                    logger.info(f"Loaded ML demand prediction model from {model_file}")
                else:
                    logger.warning(f"ML model path provided but model not found: {self.ml_model_path}")
            except Exception as e:
                logger.warning(f"Failed to load ML model: {e}")
                self.ml_predictor = None
        else:
            logger.info("No ML model path provided, will use POI proxy for demand prediction")

    def _build_fuzzy_system(self):
        """Build the fuzzy control system based on configuration."""
        try:
            # Create antecedents (inputs)
            antecedents = {}

            for input_name, input_config in self.config['inputs'].items():
                # Create universe of discourse
                universe = np.linspace(
                    input_config['universe_min'],
                    input_config['universe_max'],
                    self.config['universe_resolution']
                )

                # Create antecedent
                antecedent = ctrl.Antecedent(universe, input_name)

                # Add membership functions
                for term_name, term_config in input_config['terms'].items():
                    if term_config['type'] == 'trimf':
                        antecedent[term_name] = fuzz.trimf(
                            antecedent.universe,
                            term_config['params']
                        )
                    elif term_config['type'] == 'trapmf':
                        antecedent[term_name] = fuzz.trapmf(
                            antecedent.universe,
                            term_config['params']
                        )

                antecedents[input_name] = antecedent

            # Create consequent (output)
            output_config = self.config['output']['suitability']
            output_universe = np.linspace(
                output_config['universe_min'],
                output_config['universe_max'],
                self.config['universe_resolution']
            )
            suitability = ctrl.Consequent(output_universe, 'suitability')

            # Add output membership functions
            for term_name, term_config in output_config['terms'].items():
                if term_config['type'] == 'trimf':
                    suitability[term_name] = fuzz.trimf(
                        suitability.universe,
                        term_config['params']
                    )
                elif term_config['type'] == 'trapmf':
                    suitability[term_name] = fuzz.trapmf(
                        suitability.universe,
                        term_config['params']
                    )

            # Create rules
            rules = []
            for rule_config in self.config['rules']:
                # Parse rule format: [demand, accessibility, grid_cost, land_cost, poi, competition] -> suitability
                antecedent_terms = rule_config[:-1]  # All except last element
                consequent_term = rule_config[-1]    # Last element

                # Build antecedent (handle wildcards)
                antecedent_conditions = []
                input_names = list(self.config['inputs'].keys())

                for i, term in enumerate(antecedent_terms):
                    input_name = input_names[i]
                    if term == "*":
                        # Wildcard - create a term that matches everything (membership = 1 everywhere)
                        # Use the first term's universe to create a universal term
                        ant = antecedents[input_name]
                        universe_min = ant.universe[0]
                        universe_max = ant.universe[-1]
                        # Check if "any" term already exists, else create it
                        if "any" not in ant.terms:
                            ant["any"] = fuzz.trapmf(ant.universe, [universe_min, universe_min, universe_max, universe_max])
                        antecedent_conditions.append(ant["any"])
                    else:
                        antecedent_conditions.append(antecedents[input_name][term])

                # Combine antecedent conditions with AND
                if antecedent_conditions:
                    antecedent_combined = antecedent_conditions[0]
                    for condition in antecedent_conditions[1:]:
                        antecedent_combined = antecedent_combined & condition

                    # Create rule
                    rule = ctrl.Rule(antecedent_combined, suitability[consequent_term])
                    rules.append(rule)

            # Create control system
            self.fuzzy_system = ctrl.ControlSystem(rules)
            logger.info(f"Built fuzzy system with {len(rules)} rules")

        except Exception as e:
            logger.error(f"Failed to build fuzzy system: {e}")
            raise

    def score_candidates(self, candidates_df: pd.DataFrame,
                        weather_df: Optional[pd.DataFrame] = None,
                        demand_history_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Score candidate locations using fuzzy logic.

        Args:
            candidates_df: DataFrame containing candidate locations with required features
            weather_df: DataFrame with hourly weather data (for ML model features)
            demand_history_df: DataFrame with historical demand (for ML model training/validation)

        Returns:
            DataFrame with added fuzzy suitability scores
        """
        try:
            # Create a copy to avoid modifying original
            df_scored = candidates_df.copy()

            # Prepare input data for fuzzy system
            simulator = ctrl.ControlSystemSimulation(self.fuzzy_system)

            # Initialize suitability scores list
            suitability_scores = []
            demand_sources = []  # Track where demand predictions came from

            # Process each candidate
            for idx, row in df_scored.iterrows():
                # Initialize default values
                predicted_demand = 0.0
                demand_source = "FAILED"

                try:
                    # Get predicted demand - try ML model first, fall back to POI proxy
                    predicted_demand, demand_source = self._get_predicted_demand_with_source(
                        row, weather_df, demand_history_df
                    )
                except Exception as e:
                    logger.warning(f"ML prediction failed for candidate {row.get('candidate_id', idx)}: {e}")
                    # Keep default values (0.0, "FAILED")

                try:
                    # Check for missing/NaN grid_connection_cost_inr_lakhs (minimal fix)
                    grid_cost = row.get('grid_connection_cost_inr_lakhs')
                    if pd.isna(grid_cost):
                        # Candidate has UNKNOWN grid cost - cannot score reliably
                        # Mark as UNSCORABLE with explicit provenance flag
                        logger.warning(f"Candidate {row.get('candidate_id', idx)} has UNKNOWN grid_connection_cost_inr_lakhs - marking UNSCORABLE")
                        suitability = None
                        demand_source = "UNSCORABLE (missing grid cost)"
                        # Skip to provenance recording
                        raise ValueError("Missing grid cost - unscorable")

                    # Set inputs for fuzzy system
                    simulator.input['predicted_demand'] = predicted_demand
                    simulator.input['traffic_accessibility'] = row['traffic_accessibility_score']
                    simulator.input['grid_connection_cost'] = grid_cost
                    simulator.input['land_cost_tier'] = self._convert_land_cost_tier(row['land_cost_tier'])
                    poi_value = row.get('poi_score', row.get('pois_score', 0.0))
                    simulator.input['poi_attractiveness'] = float(poi_value) * 100
                    simulator.input['competition_index'] = row['existing_chargers_3km']

                    # Compute fuzzy inference
                    simulator.compute()

                    # Get output suitability score
                    suitability = simulator.output['suitability']

                except ValueError as e:
                    # Expected: missing grid cost or other explicit unscorable condition
                    if "Missing grid cost" in str(e):
                        pass  # Already logged above
                    else:
                        logger.warning(f"Failed to compute fuzzy score for candidate {row.get('candidate_id', idx)}: {e}")
                        demand_source = "FAILED"
                    suitability = None  # Mark as no-output; do NOT fabricate fallback

                # Append results for this candidate
                suitability_scores.append(suitability)
                demand_sources.append(demand_source)

            # Add suitability scores to dataframe
            df_scored['fuzzy_suitability_score'] = suitability_scores
            df_scored['demand_prediction_source'] = demand_sources

            # Add provenance information
            df_scored['fuzzy_provenance'] = df_scored.apply(
                lambda row: self._create_fuzzy_provenance(row), axis=1
            )

            logger.info(f"Scored {len(df_scored)} candidates using fuzzy logic")
            # Log demand source distribution
            source_counts = pd.Series(demand_sources).value_counts()
            logger.info(f"Demand prediction sources: {source_counts.to_dict()}")

            return df_scored

        except Exception as e:
            logger.error(f"Failed to score candidates: {e}")
            raise

    def _get_predicted_demand_with_source(self, row: pd.Series,
                                         weather_df: Optional[pd.DataFrame] = None,
                                         demand_history_df: Optional[pd.DataFrame] = None) -> Tuple[float, str]:
        """
        Get predicted demand for a candidate and identify its source.

        Args:
            row: Candidate data row
            weather_df: DataFrame with hourly weather data
            demand_history_df: DataFrame with historical demand data

        Returns:
            Tuple of (predicted_demand_kwh, source_description)
        """
        # Try to use ML model if available
        if self.ml_predictor is not None and self.ml_predictor.is_trained:
            try:
                # Prepare features for this candidate
                # We need to create a minimal dataset with temporal and weather features
                # For simplicity, we'll use average conditions or generate synthetic features

                # Create a single-row DataFrame with required features
                # Candidate-specific features (zone, pois_score) will be obtained from candidates_gdf
                features_dict = {
                    'candidate_id': row['candidate_id'],
                    'timestamp': pd.Timestamp('2024-06-15 12:00:00'),  # Mid-year noon as representative
                    'hour': 12,
                    'day_of_week': 5,  # Saturday
                    'month': 6,  # June
                    'is_weekend': 1,
                    'is_holiday': 0,
                    'is_exam_period': 0,
                    'temperature_c': 25.0,  # Average temperature
                    'humidity_pct': 70.0,   # Average humidity
                    'precipitation_mm': 0.0 # No precipitation
                }

                features_df = pd.DataFrame([features_dict])

                # Get prediction from ML model
                prediction = self.ml_predictor.predict(features_df,
                                                      pd.DataFrame([row.to_dict()]) if hasattr(row, 'to_dict') else None)
                predicted_demand = float(prediction[0])

                # Ensure reasonable bounds
                predicted_demand = max(0, min(predicted_demand, 50))  # Cap at 50 kWh

                return predicted_demand, "ML_MODEL"

            except Exception as e:
                logger.warning(f"ML prediction failed for candidate {row.get('candidate_id', 'unknown')}: {e}")
                # Fall back to POI proxy

        # Fall back to POI score proxy; support both column names.
        poi_value = row.get('poi_score', row.get('pois_score', None))
        if poi_value is not None and not pd.isna(poi_value):
            poi_based_demand = min(float(poi_value) * 100, 100)
        else:
            logger.warning(
                f"POI score column missing or invalid for candidate "
                f"{row.get('candidate_id', 'unknown')}. Available columns: {list(row.index)}"
            )
            poi_based_demand = 0.0
        return poi_based_demand, "POI_PROXY"

    def _get_predicted_demand(self, row: pd.Series) -> float:
        """
        Get predicted demand for a candidate (backward compatibility).
        Since ML model integration may not be available, use POI score as proxy.

        Args:
            row: Candidate data row

        Returns:
            Predicted demand in kWh/h (scaled from POI score)
        """
        predicted_demand, _ = self._get_predicted_demand_with_source(row)
        return predicted_demand

    def _convert_land_cost_tier(self, tier_str: str) -> float:
        """
        Convert land cost tier string to numeric value.

        Args:
            tier_str: Land cost tier (low, medium, high)

        Returns:
            Numeric tier value (0-3 scale)
        """
        tier_mapping = {
            'low': 0.5,
            'medium': 1.5,
            'high': 2.5
        }
        return tier_mapping.get(tier_str.lower(), 1.5)  # Default to medium

    def _create_fuzzy_provenance(self, row: pd.Series) -> str:
        """
        Create provenance JSON for fuzzy scoring.

        Args:
            row: Candidate data row (includes demand_prediction_source from scoring loop)

        Returns:
            JSON string with provenance information
        """
        # Use demand_prediction_source from scoring loop (includes UNSCORABLE flag)
        demand_source = row.get('demand_prediction_source', 'UNKNOWN')

        demand_source_info = {
            "ML_MODEL": {
                "source": "ML demand prediction model",
                "provenance": "DERIVED"
            },
            "POI_PROXY": {
                "source": "POI score proxy (ML not available/failed)",
                "provenance": "DERIVED"
            },
            "FAILED": {
                "source": "Prediction failed, using default",
                "provenance": "DERIVED"
            },
            "UNSCORABLE (missing grid cost)": {
                "source": "Candidate unscorable due to missing grid connection cost",
                "provenance": "META"
            }
        }.get(demand_source, {
            "source": "Unknown source",
            "provenance": "DERIVED"
        })

        # Get predicted demand from row (set during scoring) or default
        predicted_demand = row.get('predicted_demand', 0.0)

        provenance = {
            "method": "SYNTHETIC",
            "components": {
                "predicted_demand": {
                    "value": predicted_demand,
                    "source": demand_source_info["source"],
                    "provenance": demand_source_info["provenance"]
                },
                "traffic_accessibility": {
                    "value": row['traffic_accessibility_score'],
                    "source": "OSM road network analysis",
                    "provenance": "DERIVED"
                },
                "grid_connection_cost": {
                    "value": row['grid_connection_cost_inr_lakhs'],
                    "source": "Distance to nearest transformer",
                    "provenance": "DERIVED"
                },
                "land_cost_tier": {
                    "value": self._convert_land_cost_tier(row['land_cost_tier']),
                    "source": "Config-based tier mapping",
                    "provenance": "SYNTHETIC"
                },
                "poi_attractiveness": {
                    "value": float(row.get('poi_score', row.get('pois_score', 0.0))) * 100,
                    "source": "OSM POI density analysis",
                    "provenance": "DERIVED"
                },
                "competition_index": {
                    "value": row['existing_chargers_3km'],
                    "source": "OpenChargeMap data (demo mode)",
                    "provenance": "REAL" if row['existing_chargers_3km'] > 0 else "DERIVED"
                }
            },
            "fuzzy_system": {
                "inference_method": self.config['inference_method'],
                "aggregation_method": self.config['aggregation_method'],
                "defuzzification_method": self.config['defuzzification_method'],
                "universe_resolution": self.config['universe_resolution']
            },
            "demand_prediction_source": demand_source,
            "scored_at": pd.Timestamp.now().isoformat()
        }

        return json.dumps(provenance)


def main():
    """Main function to run fuzzy scoring on candidate data."""
    import argparse

    parser = argparse.ArgumentParser(description='Score EV charging candidates using fuzzy logic')
    parser.add_argument('--input', '-i', default='data/processed/candidates.csv',
                        help='Input candidates CSV file')
    parser.add_argument('--output', '-o', default='data/processed/candidates_fuzzy_scored.csv',
                        help='Output scored candidates CSV file')
    parser.add_argument('--config', '-c', default='config/fuzzy_config.yaml',
                        help='Fuzzy configuration file')
    parser.add_argument('--ml-model', '-m', default=None,
                        help='Path to saved ML demand prediction model (optional)')
    parser.add_argument('--log-level', default='INFO',
                        help='Logging level')

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s'
    )

    try:
        # Load candidates
        logger.info(f"Loading candidates from {args.input}")
        candidates_df = pd.read_csv(args.input)
        logger.info(f"Loaded {len(candidates_df)} candidates")

        # Initialize scorer
        scorer = FuzzyEVChargingScorer(args.config, args.ml_model)

        # Score candidates
        scored_df = scorer.score_candidates(candidates_df)

        # Save results
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        scored_df.to_csv(args.output, index=False)
        logger.info(f"Saved scored candidates to {args.output}")

        # Print summary statistics (handle NaN suitability scores)
        print("\n=== Fuzzy Scoring Results ===")
        print(f"Candidates processed: {len(scored_df)}")

        # Filter valid scores for statistics
        valid_scores = scored_df['fuzzy_suitability_score'].dropna()
        if len(valid_scores) > 0:
            print(f"Suitability score range: {valid_scores.min():.3f} - {valid_scores.max():.3f}")
            print(f"Mean suitability score: {valid_scores.mean():.3f}")
            print(f"Std suitability score: {valid_scores.std():.3f}")
            print(f"Scorable candidates: {len(valid_scores)}")
        else:
            print("Suitability score range: N/A (no scorable candidates)")
            print("Mean suitability score: N/A")
            print(f"Std suitability score: N/A")
            print(f"Scorable candidates: 0")

        # Show demand prediction sources
        if 'demand_prediction_source' in scored_df.columns:
            print(f"\nDemand prediction sources:")
            for source, count in scored_df['demand_prediction_source'].value_counts().items():
                print(f"  {source}: {count}")

        # Show top 5 candidates (only scorable ones)
        print("\nTop 5 candidates by fuzzy suitability:")
        scorable_df = scored_df.dropna(subset=['fuzzy_suitability_score'])
        if len(scorable_df) > 0:
            top_5 = scorable_df.nlargest(5, 'fuzzy_suitability_score')[
                ['candidate_id', 'name', 'fuzzy_suitability_score', 'zone_id', 'demand_prediction_source']
            ]
            print(top_5.to_string(index=False))
        else:
            print("  No scorable candidates to rank")

    except Exception as e:
        logger.error(f"Fuzzy scoring failed: {e}")
        raise


if __name__ == "__main__":
    main()