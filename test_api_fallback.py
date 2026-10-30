#!/usr/bin/env python3
"""
Test script to verify API cache fallback functionality for OCM and Weather loaders.
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import requests

# Insert the src directory into the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from data.ocm_loader import OCMDataLoader
from data.weather_loader import WeatherLoader
from data.unified_loader import UnifiedDataLoader
from data.config_loader import get_config, get_data_sources_config


def create_dummy_ocm_cache(cache_dir):
    """Create a dummy OCM stations.geojson file for testing."""
    dummy_data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "station_id": "1",
                    "uuid": "test-uuid",
                    "name": "Test Station",
                    "address": "Test Address",
                    "town": "Test Town",
                    "state": "Test State",
                    "country": "Test Country",
                    "latitude": 16.4940,
                    "longitude": 80.4993,
                    "operator": "Test Operator",
                    "operator_id": "1",
                    "usage_type": "Public",
                    "status": "Operational",
                    "date_last_confirmed": "2024-01-01",
                    "num_connectors": 2,
                    "total_power_kw": 50,
                    "max_power_kw": 50,
                    "connector_types": '["Type 2"]',
                    "access_type": "public",
                    "is_operational": True,
                    "source": "openchargemap",
                    "distance_to_campus_km": 0.5,
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [80.4993, 16.4940]
                }
            }
        ]
    }
    cache_file = cache_dir / "stations.geojson"
    with open(cache_file, 'w') as f:
        import json
        json.dump(dummy_data, f)
    return cache_file


def create_dummy_weather_cache(cache_dir, year):
    """Create a dummy weather CSV file for testing."""
    dummy_data = pd.DataFrame({
        "timestamp": pd.date_range(start=f"{year}-01-01", end=f"{year}-01-02", freq="h"),
        "temperature_c": [25.0] * 25,
        "relative_humidity_pct": [60.0] * 25,
        "precipitation_mm": [0.0] * 25,
        "wind_speed_kmph": [10.0] * 25,
        "cloud_cover_pct": [50.0] * 25,
        "heat_index_c": [27.0] * 25,
        "data_provenance": ['{"column-level":{}}'] * 25,  # Simplified provenance string
    })
    cache_file = cache_dir / f"weather_hourly_{year}.csv"
    dummy_data.to_csv(cache_file, index=False)
    return cache_file


def test_ocm_fallback():
    """Test that OCM loader uses cache when API fails."""
    print("Testing OCM API fallback...")

    # Create a temporary directory for cache
    with tempfile.TemporaryDirectory() as temp_dir:
        cache_dir = Path(temp_dir) / "openchargemap"
        cache_dir.mkdir()

        # Create dummy cache
        create_dummy_ocm_cache(cache_dir)

        # Mock config to use our temporary directory
        with patch('data.ocm_loader.get_config') as mock_get_config, \
             patch('data.ocm_loader.get_data_sources_config') as mock_get_sources_config:

            # Mock the config objects
            mock_config = MagicMock()
            mock_config.paths.raw = temp_dir
            mock_config.project.study_area.campus_center.latitude = 16.4940
            mock_config.project.study_area.campus_center.longitude = 80.4993
            mock_get_config.return_value = mock_config

            mock_sources_config = MagicMock()
            mock_ocm_config = MagicMock()
            mock_ocm_config.base_url = "https://api.openchargemap.io/v3/poi/"
            mock_ocm_config.params.output = "json"
            mock_ocm_config.params.countrycode = "IN"
            mock_ocm_config.params.latitude = 16.4940
            mock_ocm_config.params.longitude = 80.4993
            mock_ocm_config.params.distance = 10
            mock_ocm_config.params.distanceunit = "KM"
            mock_ocm_config.params.maxresults = 500
            mock_ocm_config.params.compact = True
            mock_ocm_config.params.verbose = False
            mock_ocm_config.api_key_env = "OCM_API_KEY"
            mock_sources_config.data_sources.openchargemap = mock_ocm_config
            mock_get_sources_config.return_value = mock_sources_config

            # Create loader
            loader = OCMDataLoader()

            # Patch requests.get to simulate API failure
            with patch('data.ocm_loader.requests.get') as mock_get:
                mock_get.side_effect = requests.exceptions.RequestException("API failure")

                # Call fetch_stations
                result = loader.fetch_stations()

                # Verify that we got the cached data (not empty)
                assert not result.empty, "OCM loader should return cached data when API fails"
                assert len(result) == 1, "OCM loader should return one station from cache"
                assert result.iloc[0]["name"] == "Test Station", "OCM loader should return correct station name"

                print("  ✓ OCM loader correctly used cache when API failed")


def test_weather_fallback():
    """Test that Weather loader uses cache when API fails for a year."""
    print("Testing Weather API fallback...")

    with tempfile.TemporaryDirectory() as temp_dir:
        cache_dir = Path(temp_dir) / "weather"
        cache_dir.mkdir()

        # Create dummy cache for 2024
        create_dummy_weather_cache(cache_dir, 2024)

        # Mock config to use our temporary directory
        with patch('data.weather_loader.get_config') as mock_get_config, \
             patch('data.weather_loader.get_data_sources_config') as mock_get_sources_config:

            mock_config = MagicMock()
            mock_config.paths.raw = temp_dir
            mock_get_config.return_value = mock_config

            mock_sources_config = MagicMock()
            mock_weather_config = MagicMock()
            mock_weather_config.base_url = "https://archive-api.open-meteo.com/v1/archive"
            mock_weather_config.params.latitude = 16.4940
            mock_weather_config.params.longitude = 80.4993
            mock_weather_config.params.start_date = "2024-01-01"
            mock_weather_config.params.end_date = "2024-12-31"
            mock_weather_config.params.hourly = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m", "cloud_cover"]
            mock_weather_config.params.timezone = "Asia/Kolkata"
            mock_sources_config.data_sources.open_meteo = mock_weather_config
            mock_get_sources_config.return_value = mock_sources_config

            # Create loader
            loader = WeatherLoader()

            # Patch requests.get to simulate API failure
            with patch('data.weather_loader.requests.get') as mock_get:
                mock_get.side_effect = requests.exceptions.RequestException("API failure")

                # Call fetch_year for 2024
                result = loader.fetch_year(2024)

                # Verify that we got the cached data (not empty)
                assert not result.empty, "Weather loader should return cached data when API fails"
                assert len(result) == 25, "Weather loader should return 25 hourly records from cache"
                assert result.iloc[0]["temperature_c"] == 25.0, "Weather loader should return correct temperature"

                print("  ✓ Weather loader correctly used cache when API failed")


def test_weather_missing():
    """Test that Weather loader returns empty DataFrame when no cache and API fails."""
    print("Testing Weather missing data handling...")

    with tempfile.TemporaryDirectory() as temp_dir:
        cache_dir = Path(temp_dir) / "weather"
        cache_dir.mkdir()
        # Do not create any cache files

        # Mock config to use our temporary directory
        with patch('data.weather_loader.get_config') as mock_get_config, \
             patch('data.weather_loader.get_data_sources_config') as mock_get_sources_config:

            mock_config = MagicMock()
            mock_config.paths.raw = temp_dir
            mock_get_config.return_value = mock_config

            mock_sources_config = MagicMock()
            mock_weather_config = MagicMock()
            mock_weather_config.base_url = "https://archive-api.open-meteo.com/v1/archive"
            mock_weather_config.params.latitude = 16.4940
            mock_weather_config.params.longitude = 80.4993
            mock_weather_config.params.start_date = "2024-01-01"
            mock_weather_config.params.end_date = "2024-12-31"
            mock_weather_config.params.hourly = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m", "cloud_cover"]
            mock_weather_config.params.timezone = "Asia/Kolkata"
            mock_sources_config.data_sources.open_meteo = mock_weather_config
            mock_get_sources_config.return_value = mock_sources_config

            # Create loader
            loader = WeatherLoader()

            # Patch requests.get to simulate API failure
            with patch('data.weather_loader.requests.get') as mock_get:
                mock_get.side_effect = requests.exceptions.RequestException("API failure")

                # Call fetch_year for 2024
                result = loader.fetch_year(2024)

                # Verify that we got an empty DataFrame
                assert result.empty, "Weather loader should return empty DataFrame when no cache and API fails"

                print("  ✓ Weather loader correctly returned empty DataFrame when no cache and API failed")


def test_unified_weather_missing():
    """Test that Unified loader stops when weather data is missing."""
    print("Testing Unified loader weather missing data handling...")

    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up directory structure in temporary raw data directory
        os.makedirs(Path(temp_dir) / "osm", exist_ok=True)
        os.makedirs(Path(temp_dir) / "openchargemap", exist_ok=True)
        os.makedirs(Path(temp_dir) / "weather", exist_ok=True)
        os.makedirs(Path(temp_dir) / "external", exist_ok=True)
        os.makedirs(Path(temp_dir) / "processed", exist_ok=True)

        # Create dummy OSM cache files (empty but valid GeoJSON)
        for layer in ["roads", "buildings", "pois", "power", "parking"]:
            dummy_gdf = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
            dummy_gdf.to_file(Path(temp_dir) / "osm" / f"{layer}.geojson", driver="GeoJSON")

        # Create dummy OCM cache (empty GeoJSON)
        dummy_ocm_gdf = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
        dummy_ocm_gdf.to_file(Path(temp_dir) / "openchargemap" / "stations.geojson", driver="GeoJSON")

        # Do NOT create any weather cache (so weather will be missing)

        # Create a dummy zone file
        dummy_zone = gpd.GeoDataFrame({
            "zone_id": ["academic"],
            "zone_name": ["Academic Zone"],
            "land_cost_tier": ["medium"],
            "primary_use": ["academic"],
            "geometry": [Point(80.4993, 16.4940).buffer(0.01)]  # Small polygon
        }, crs="EPSG:4326")
        dummy_zone.to_file(Path(temp_dir) / "external" / "vitap_zone_polygons.geojson", driver="GeoJSON")

        # Mock config to use our temporary directory
        with patch('data.unified_loader.get_config') as mock_get_config, \
             patch('data.config_loader.get_data_sources_config') as mock_get_sources_config:
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

            # Mock the data sources config to point to our temporary external directory
            mock_sources_config = MagicMock()
            mock_zone_config = MagicMock()
            mock_zone_config.input_file = str(Path(temp_dir) / "external" / "vitap_zone_polygons.geojson")
            mock_zone_config.required_properties = ["zone_id", "zone_name", "land_cost_tier", "primary_use"]
            mock_sources_config.data_sources.campus_zones = mock_zone_config
            mock_get_sources_config.return_value = mock_sources_config

            # PATCH THE LOADERS FIRST
            with patch('data.osm_loader.OSMLoader.fetch_all') as mock_osm_fetch, \
                 patch('data.ocm_loader.OCMDataLoader.fetch_stations') as mock_ocm_fetch, \
                 patch('data.weather_loader.WeatherLoader.fetch_all_years') as mock_weather_fetch:

                # NOW CREATE THE UNIFIED LOADER (AFTER PATCHES ARE APPLIED)
                loader = UnifiedDataLoader()

                # Configure mocks to simulate API failures and missing data
                # OSM returns empty GeoDataFrames (simulating cached empty data)
                mock_osm_fetch.return_value = {
                    "roads": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                    "buildings": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                    "pois": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                    "power": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                    "parking": gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326"),
                }

                # OCM returns empty GeoDataFrame (simulating cached empty data)
                mock_ocm_fetch.return_value = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")

                # Weather: simulate API failure and no cache -> returns empty DataFrame
                mock_weather_fetch.return_value = pd.DataFrame()

                # Run the pipeline and expect it to stop with an error
                try:
                    loader.run_full_pipeline(force_refresh=False)
                    # If we reach here, the pipeline did not stop -> test failed
                    assert False, "Unified loader should have stopped due to missing weather data"
                except RuntimeError as e:
                    assert "Missing required weather data" in str(e)
                    print("  ✓ Unified loader correctly stopped when weather data was missing")
                except Exception as e:
                    assert False, f"Unified loader raised unexpected exception: {e}"


if __name__ == "__main__":
    try:
        test_ocm_fallback()
        test_weather_fallback()
        test_weather_missing()
        test_unified_weather_missing()
        print("\nAll tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)