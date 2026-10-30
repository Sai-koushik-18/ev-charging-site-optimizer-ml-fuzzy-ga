#!/usr/bin/env python3
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# Insert the src directory into the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from data.unified_loader import UnifiedDataLoader

print("Starting mock test...")

try:
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Using temp dir: {temp_dir}")

        # Set up directory structure (missing "data/" prefix)
        os.makedirs(Path(temp_dir) / "raw" / "osm", exist_ok=True)
        os.makedirs(Path(temp_dir) / "raw" / "openchargemap", exist_ok=True)
        os.makedirs(Path(temp_dir) / "external", exist_ok=True)

        # Create dummy zone file
        dummy_zone = gpd.GeoDataFrame({
            "zone_id": ["academic"],
            "zone_name": ["Academic Zone"],
            "land_cost_tier": ["medium"],
            "primary_use": ["academic"],
            "geometry": [Point(80.4993, 16.4940).buffer(0.01)]
        }, crs="EPSG:4326")
        dummy_zone.to_file(Path(temp_dir) / "external" / "vitap_zone_polygons.geojson", driver="GeoJSON")
        print("Created zone file")

        # Mock config
        with patch('data.unified_loader.get_config') as mock_get_config:
            mock_config = MagicMock()
            mock_config.paths.raw = temp_dir
            mock_config.paths.processed = Path(temp_dir) / "processed"
            mock_config.paths.external = Path(temp_dir) / "external"
            mock_config.project.study_area.bbox.min_lat = 16.4878
            mock_config.project.study_area.bbox.max_lat = 16.5011
            mock_config.project.study_area.bbox.min_lon = 80.4935
            mock_config.project.study_area.bbox.max_lon = 80.5055
            mock_config.project.study_area.campus_center.latitude = 16.4940
            mock_config.project.study_area.campus_center.longitude = 80.4993
            mock_config.validation_rules = {
                "candidates": {"required_columns": []},
                "existing_stations": {"required_columns": []},
                "demand_history": {"row_count_expected": 219000},
                "weather": {}
            }
            mock_config.weather.years = [2024]
            mock_config.traffic_simulation = MagicMock()
            mock_config.traffic_simulation.base_volume_vph_by_hierarchy = {"residential": 100}
            mock_config.traffic_simulation.free_flow_speed_kmph_by_hierarchy = {"residential": 30}
            mock_config.traffic_simulation.noise_cv = 0.1
            mock_config.traffic_simulation.peak_hours = [7,8,9,10,17,18,19,20]
            mock_get_config.return_value = mock_config
            print("Set up config mock")

            # Patch the loaders
            print("Creating patches...")
            with patch('data.osm_loader.OSMLoader.fetch_all') as mock_osm_fetch, \
                 patch('data.ocm_loader.OCMDataLoader.fetch_stations') as mock_ocm_fetch, \
                 patch('data.weather_loader.WeatherLoader.fetch_all_years') as mock_weather_fetch:

                print("Patches created")

                # Configure mocks
                print("Configuring mocks...")
                mock_osm_fetch.return_value = {
                    "roads": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                    "buildings": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                    "pois": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                    "power": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                    "parking": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                }
                mock_ocm_fetch.return_value = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
                mock_weather_fetch.return_value = pd.DataFrame()
                print("Mocks configured")

                # Create loader
                print("Creating UnifiedDataLoader...")
                loader = UnifiedDataLoader()
                print("Created UnifiedDataLoader")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("Test completed")