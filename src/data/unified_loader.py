"""
Unified data loader orchestrating all data acquisition and generation.
Main entry point for the data pipeline.
"""

from pathlib import Path
from typing import Optional, Any, Dict, List
import pandas as pd
import geopandas as gpd
from loguru import logger

from .config_loader import get_config
from .osm_loader import OSMLoader, fetch_osm_data
from .ocm_loader import OCMDataLoader, fetch_ocm_data
from .weather_loader import WeatherLoader, fetch_weather_data
from .zone_loader import load_zone_polygons
from .synthetic_generator import DemandGenerator, TrafficGenerator, generate_synthetic_demand, generate_synthetic_traffic
from .candidate_generator import CandidateGenerator, generate_candidates
from .provenance import ProvenanceTracker, Provenance, DatasetProvenance


class UnifiedDataLoader:
    """
    Orchestrates the complete data pipeline:
    1. Fetch REAL data (OSM, OpenChargeMap, Weather)
    2. Load DERIVED data (Zone polygons)
    3. Generate SYNTHETIC data (Demand, Traffic)
    4. Generate candidates with all features
    5. Validate all outputs
    """

    def __init__(self, config: Optional[Any] = None, mode: str = "hybrid"):
        """
        Args:
            config: Configuration object
            mode: Data mode - "synthetic", "real", or "hybrid"
        """
        self.config = config or get_config("settings")
        self.mode = mode
        self.osm_loader = OSMLoader(self.config)
        self.ocm_loader = OCMDataLoader(self.config)
        self.weather_loader = WeatherLoader(self.config)
        self.candidate_generator = CandidateGenerator(self.config)
        self.demand_generator = DemandGenerator(self.config)
        self.traffic_generator = TrafficGenerator(self.config)

        # Data containers
        self.raw_data: Dict[str, Any] = {}
        self.processed_data: Dict[str, Any] = {}

    def run_full_pipeline(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Run the complete data acquisition and generation pipeline.

        Returns:
            Dictionary with all generated datasets
        """
        logger.info(f"Starting data pipeline in '{self.mode}' mode")

        # Step 1: Fetch/Load REAL data
        self._fetch_real_data(force_refresh)

        # Step 2: Load DERIVED data
        self._load_derived_data()

        # Step 3: Generate SYNTHETIC data
        self._generate_synthetic_data()

        # Step 4: Generate candidates
        self._generate_candidates()

        # Step 5: Validate all outputs
        self._validate_all()

        logger.info("Data pipeline completed successfully")
        return self.processed_data

    def _fetch_real_data(self, force_refresh: bool):
        """Fetch real data from external sources."""
        logger.info("Fetching REAL data...")

        # OSM Data
        base_raw_path = Path(self.config.paths.raw)
        osm_files = {
            "roads": base_raw_path / "osm" / "roads.geojson",
            "buildings": base_raw_path / "osm" / "buildings.geojson",
            "pois": base_raw_path / "osm" / "pois.geojson",
            "power": base_raw_path / "osm" / "power.geojson",
            "parking": base_raw_path / "osm" / "parking.geojson",  # Raw nodes (preserved, not used for candidates)
        }

        if force_refresh or not all(f.exists() for f in osm_files.values()):
            logger.info("Downloading OSM data...")
            osm_data = self.osm_loader.fetch_all()
            self.raw_data.update({f"osm_{k}": v for k, v in osm_data.items()})
        else:
            logger.info("Loading cached OSM data...")
            for key, path in osm_files.items():
                self.raw_data[f"osm_{key}"] = gpd.read_file(path)

        # Load validated parking (Polygon features) for candidate generation
        validated_parking_path = Path(self.config.paths.processed) / "parking_osm_validated.geojson"
        if validated_parking_path.exists():
            logger.info("Loading validated parking polygons (for candidate generation)...")
            self.raw_data["osm_parking_validated"] = gpd.read_file(validated_parking_path)
        else:
            logger.warning("Validated parking file not found, will use raw parking nodes")
            self.raw_data["osm_parking_validated"] = gpd.GeoDataFrame()

        # OpenChargeMap Data
        base_raw_path = Path(self.config.paths.raw)
        ocm_file = base_raw_path / "openchargemap" / "stations.geojson"
        if force_refresh or not ocm_file.exists():
            logger.info("Downloading OpenChargeMap data...")
            self.raw_data["existing_stations"] = self.ocm_loader.fetch_stations()
        else:
            logger.info("Loading cached OpenChargeMap data...")
            self.raw_data["existing_stations"] = gpd.read_file(ocm_file)

        # Weather Data
        base_raw_path = Path(self.config.paths.raw)
        weather_file = base_raw_path / "weather" / "weather_hourly_combined.csv"
        if force_refresh or not weather_file.exists():
            logger.info("Downloading weather data...")
            self.raw_data["weather"] = self.weather_loader.fetch_all_years()
        else:
            logger.info("Loading cached weather data...")
            self.raw_data["weather"] = pd.read_csv(weather_file, parse_dates=["timestamp"])

        # Check if weather data is empty (missing)
        if self.raw_data["weather"].empty:
            logger.error("Weather data is missing (no cache and API failure for one or more years). Stopping data preparation.")
            raise RuntimeError("Missing required weather data")

    def _load_derived_data(self):
        """Load derived data (zone polygons)."""
        logger.info("Loading DERIVED data...")
        # Try provisional zones first; if not found, load original with explicit warning
        from pathlib import Path
        prov_path = Path(self.config.paths.external) / "vitap_zone_polygons_PROVISIONAL.geojson"
        orig_path = Path(self.config.paths.external) / "vitap_zone_polygons.geojson"

        if prov_path.exists():
            logger.info("Loading PROVISIONAL zone polygons (aligned to landmarks)")
            self.raw_data["zones"] = gpd.read_file(prov_path)
            self.raw_data["zones_provenance"] = "PROVISIONAL"
        elif orig_path.exists():
            logger.warning("Loading ORIGINAL zone polygons (MISPLACED — 2.7km NW of campus landmarks)")
            self.raw_data["zones"] = gpd.read_file(orig_path)
            self.raw_data["zones_provenance"] = "ORIGINAL_MISPLACED"
        else:
            raise FileNotFoundError("No zone polygons found in external path")

    def _generate_synthetic_data(self):
        """Generate synthetic demand and traffic data."""
        logger.info("Generating SYNTHETIC data...")

        # Generate demand history
        candidates = self.processed_data.get("candidates")
        if candidates is not None and not candidates.empty:
            self.processed_data["demand_history"] = self.demand_generator.generate(
                candidates,
                self.raw_data["weather"],
                years=self.config.weather.years,
            )

        # Generate traffic
        roads = self.raw_data.get("osm_roads")
        if roads is not None and not roads.empty:
            self.processed_data["traffic_hourly"] = self.traffic_generator.generate(
                roads,
                years=self.config.weather.years,
            )

    def _generate_candidates(self):
        """Generate candidate locations with all features."""
        logger.info("Generating candidate locations...")

        # Use validated parking polygons (if available) instead of raw parking nodes
        parking_for_candidates = self.raw_data.get("osm_parking_validated", gpd.GeoDataFrame())
        if parking_for_candidates.empty:
            logger.warning("Validated parking empty, falling back to raw parking nodes")
            parking_for_candidates = self.raw_data.get("osm_parking", gpd.GeoDataFrame())

        # SPATIAL VALIDATION: Check parking-zone overlap before generation
        if not parking_for_candidates.empty and not self.raw_data["zones"].empty:
            parking_centroids = parking_for_candidates.geometry.centroid
            zone_union = self.raw_data["zones"].geometry.unary_union
            inside_count = parking_centroids.within(zone_union).sum()
            total_count = len(parking_for_candidates)
            if inside_count == 0:
                prov = self.raw_data.get("zones_provenance", "UNKNOWN")
                logger.error(
                    f"SPATIAL MISMATCH: 0/{total_count} parking centroids fall within zones. "
                    f"Parking centroid: ({parking_centroids.y.mean():.4f}, {parking_centroids.x.mean():.4f}). "
                    f"Zone bounds: {self.raw_data['zones'].total_bounds}. "
                    f"Zone provenance: {prov}. "
                    f"Fix zone geometry before proceeding."
                )
                raise ValueError(
                    f"Parking-zone spatial mismatch — cannot generate candidates. "
                    f"Provenance: {prov}. Use provisional zones or correct zone geometry."
                )
            elif inside_count < total_count:
                logger.warning(
                    f"Partial spatial overlap: {inside_count}/{total_count} parking centroids within zones. "
                    f"Zone provenance: {self.raw_data.get('zones_provenance', 'UNKNOWN')}"
                )
            else:
                logger.info(f"Spatial validation passed: {inside_count}/{total_count} parking centroids within zones. "
                           f"Zone provenance: {self.raw_data.get('zones_provenance', 'UNKNOWN')}")

        candidates = self.candidate_generator.generate(
            parking=parking_for_candidates,
            zones=self.raw_data["zones"],
            pois=self.raw_data.get("osm_pois", gpd.GeoDataFrame()),
            power=self.raw_data.get("osm_power", gpd.GeoDataFrame()),
            existing_stations=self.raw_data.get("existing_stations", gpd.GeoDataFrame()),
            roads=self.raw_data.get("osm_roads", gpd.GeoDataFrame()),
            buildings=self.raw_data.get("osm_buildings", gpd.GeoDataFrame()),
        )

        self.processed_data["candidates"] = candidates

        # Also save GA fitness inputs
        self._prepare_ga_inputs(candidates)

        # Prepare fuzzy inputs (will be updated after ML predictions)
        self._prepare_fuzzy_inputs(candidates)

    def _prepare_ga_inputs(self, candidates: gpd.GeoDataFrame):
        """Prepare GA fitness calculation inputs."""
        ga_inputs = candidates[[
            "candidate_id", "predicted_annual_demand_kwh", "catchment_population",
            "total_capex_inr_lakhs", "latitude", "longitude", "zone"
        ]].copy()

        # Add placeholder for predicted demand (will be filled after ML)
        if "predicted_annual_demand_kwh" not in ga_inputs.columns:
            ga_inputs["predicted_annual_demand_kwh"] = 0.0

        output_file = Path(self.config.paths.processed) / "ga_fitness_inputs.csv"
        ga_inputs.to_csv(output_file, index=False)
        self.processed_data["ga_fitness_inputs"] = ga_inputs

    def _prepare_fuzzy_inputs(self, candidates: gpd.GeoDataFrame):
        """Prepare fuzzy logic inputs (static per candidate)."""
        fuzzy_inputs = candidates[[
            "candidate_id",
            "traffic_accessibility_score",
            "grid_connection_cost_inr_lakhs",
            "land_cost_tier",
            "poi_score",
            "existing_chargers_3km",
        ]].copy()

        # Map land_cost_tier to ordinal
        tier_map = {"very_low": 0, "low": 1, "medium": 2, "high": 3}
        fuzzy_inputs["land_cost_tier_ordinal"] = fuzzy_inputs["land_cost_tier"].map(tier_map).fillna(1)

        # Rename columns to match fuzzy config
        fuzzy_inputs = fuzzy_inputs.rename(columns={
            "traffic_accessibility_score": "traffic_accessibility",
            "grid_connection_cost_inr_lakhs": "grid_connection_cost",
            "poi_score": "poi_attractiveness",
            "existing_chargers_3km": "competition_index",
        })

        # Placeholder for predicted demand (filled after ML)
        fuzzy_inputs["predicted_demand"] = 0.0

        output_file = Path(self.config.paths.processed) / "fuzzy_inputs.csv"
        fuzzy_inputs.to_csv(output_file, index=False)
        self.processed_data["fuzzy_inputs"] = fuzzy_inputs

    def _validate_all(self):
        """Validate all generated datasets."""
        logger.info("Validating all datasets...")

        validations = {
            "candidates": self._validate_candidates,
            "existing_stations": self._validate_existing_stations,
            "demand_history": self._validate_demand_history,
            "traffic_hourly": self._validate_traffic,
            "weather": self._validate_weather,
            "zones": self._validate_zones,
        }

        for name, validator in validations.items():
            if name in self.processed_data or name in self.raw_data:
                data = self.processed_data[name] if name in self.processed_data else self.raw_data.get(name)
                if data is not None:
                    validator(data, name)

    def _validate_candidates(self, df: pd.DataFrame, name: str):
        """Validate candidates dataset."""
        rules = self.config.validation_rules.get("candidates", {}) if hasattr(self.config, 'validation_rules') else {}

        required = [
            "candidate_id", "name", "zone", "latitude", "longitude",
            "area_sqm", "parking_type", "max_chargers", "land_cost_tier",
            "land_cost_inr_lakhs", "grid_dist_m", "grid_connection_cost_inr_lakhs",
            "nearest_transformer_id", "poi_score", "catchment_population",
            "existing_chargers_500m", "existing_chargers_1km", "existing_chargers_3km",
            "traffic_accessibility_score", "construction_cost_inr_lakhs",
            "total_capex_inr_lakhs", "data_provenance",
        ]

        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning(f"{name}: Missing columns: {missing}")

        # Check coordinate bounds
        bounds = self.config.project.study_area.bbox
        if "latitude" in df.columns:
            lat_ok = df["latitude"].between(bounds.min_lat, bounds.max_lat).all()
            if not lat_ok:
                logger.warning(f"{name}: Some latitudes outside study area bounds")
        if "longitude" in df.columns:
            lon_ok = df["longitude"].between(bounds.min_lon, bounds.max_lon).all()
            if not lon_ok:
                logger.warning(f"{name}: Some longitudes outside study area bounds")

        # Check provenance
        prov = ProvenanceTracker.from_dataframe(df)
        if prov:
            summary = prov.summary()
            logger.info(f"{name} provenance: {summary}")

        logger.info(f"{name}: {len(df)} rows, {len(df.columns)} columns - VALIDATED")

    def _validate_existing_stations(self, df: gpd.GeoDataFrame, name: str):
        """Validate existing stations."""
        required = [
            "station_id", "name", "latitude", "longitude", "operator",
            "power_kw", "num_connectors", "connector_types", "access_type",
            "is_operational", "source", "distance_to_campus_km",
        ]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning(f"{name}: Missing columns: {missing}")
        logger.info(f"{name}: {len(df)} rows - VALIDATED")

    def _validate_demand_history(self, df: pd.DataFrame, name: str):
        """Validate demand history."""
        required = [
            "candidate_id", "timestamp", "hour", "day_of_week", "month",
            "is_weekend", "is_holiday", "is_exam_period", "demand_kwh",
            "session_count", "avg_session_kwh", "temperature_c",
            "humidity_pct", "precipitation_mm", "data_provenance",
        ]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning(f"{name}: Missing columns: {missing}")

        # Check row count
        expected = self.config.validation_rules.get("demand_history", {}).get("row_count_expected", 219000)
        if len(df) < expected * 0.8:
            logger.warning(f"{name}: Row count {len(df)} below expected {expected}")

        logger.info(f"{name}: {len(df)} rows - VALIDATED")

    def _validate_traffic(self, df: pd.DataFrame, name: str):
        """Validate traffic data."""
        required = [
            "road_id", "road_name", "hierarchy", "timestamp",
            "volume_vph", "avg_speed_kmph", "congestion_level", "data_provenance",
        ]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning(f"{name}: Missing columns: {missing}")
        logger.info(f"{name}: {len(df)} rows - VALIDATED")

    def _validate_weather(self, df: pd.DataFrame, name: str):
        """Validate weather data."""
        required = [
            "timestamp", "temperature_c", "relative_humidity_pct",
            "precipitation_mm", "wind_speed_kmph", "cloud_cover_pct",
            "heat_index_c", "data_provenance",
        ]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning(f"{name}: Missing columns: {missing}")

        expected = 8760 * len(self.config.weather.years)
        if len(df) < expected * 0.9:
            logger.warning(f"{name}: Row count {len(df)} below expected {expected}")

        logger.info(f"{name}: {len(df)} rows - VALIDATED")

    def _validate_zones(self, df: gpd.GeoDataFrame, name: str):
        """Validate zone polygons."""
        required = ["zone_id", "zone_name", "land_cost_tier", "primary_use"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning(f"{name}: Missing columns: {missing}")
        logger.info(f"{name}: {len(df)} zones - VALIDATED")

    def get_dataset(self, name: str) -> Optional[Any]:
        """Get a dataset by name."""
        return self.processed_data.get(name) or self.raw_data.get(name)

    def list_datasets(self) -> List[str]:
        """List all available datasets."""
        return list(set(list(self.raw_data.keys()) + list(self.processed_data.keys())))


def run_data_pipeline(config: Optional[Any] = None, mode: str = "hybrid", force_refresh: bool = False) -> Dict[str, Any]:
    """Convenience function to run the full data pipeline."""
    loader = UnifiedDataLoader(config, mode)
    return loader.run_full_pipeline(force_refresh)