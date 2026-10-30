## Issue Analysis: test_unified_weather_missing takes too long

### Root Cause
In `test_api_fallback.py`, the test `test_unified_weather_missing` takes too long instead of quickly raising the expected `RuntimeError` because of incorrect patch timing.

Specifically:
1. The test creates a mock for `get_config` in an outer `with` statement
2. It then creates a `UnifiedDataLoader` instance **before** applying patches to the data loader methods
3. Only after creating the loader does it apply patches to:
   - `data.osm_loader.OSMLoader.fetch_all`
   - `data.ocm_loader.OCMDataLoader.fetch_stations` 
   - `data.weather_loader.WeatherLoader.fetch_all_years`
4. Since the loader was created before the patches, its internal loader instances (`self.osm_loader`, `self.ocm_loader`, `self.weather_loader`) were instantiated using the **real, unpatched** classes
5. When `run_full_pipeline()` is called, it invokes the real API methods instead of the mocked ones, causing slow network calls or timeouts

### Expected Behavior
The test should:
1. Mock all external API calls
2. Simulate missing weather data (empty DataFrame from WeatherLoader)
3. Quickly detect the missing data and raise `RuntimeError("Missing required weather data")`
4. Complete in milliseconds, not seconds/minutes

### Smallest Safe Fix
Move the `UnifiedDataLoader` instantiation inside the innermost `with` statement where the method patches are applied, so the loader uses the mocked methods.

**Change in test_api_fallback.py:**
```python
def test_unified_weather_missing():
    """Test that Unified loader stops when weather data is missing."""
    print("Testing Unified loader weather missing data handling...")

    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up directory structure
        os.makedirs(Path(temp_dir) / "raw" / "osm", exist_ok=True)
        os.makedirs(Path(temp_dir) / "raw" / "openchargemap", exist_ok=True)
        os.makedirs(Path(temp_dir) / "raw" / "weather", exist_ok=True)
        os.makedirs(Path(temp_dir) / "external", exist_ok=True)
        os.makedirs(Path(temp_dir) / "processed", exist_ok=True)

        # Create dummy OSM cache files (empty but valid GeoJSON)
        for layer in ["roads", "buildings", "pois", "power", "parking"]:
            dummy_gdf = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
            dummy_gdf.to_file(Path(temp_dir) / "raw" / "osm" / f"{layer}.geojson", driver="GeoJSON")

        # Create dummy OCM cache (empty GeoJSON)
        dummy_ocm_gdf = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
        dummy_ocm_gdf.to_file(Path(temp_dir) / "raw" / "openchargemap" / "stations.geojson", driver="GeoJSON")

        # Do NOT create any weather cache (so weather will be missing)

        # Create a dummy zone file
        dummy_zone = gpd.GeoDataFrame({
            "zone_id": ["academic"],
            "zone_name": ["Academic Zone"],
            "land_cost_tier": ["medium"],
            "primary_use": ["academic"],
            "geometry": [Point(80.4800, 16.5075).buffer(0.01)]  # Small polygon
        }, crs="EPSG:4326")
        dummy_zone.to_file(Path(temp_dir) / "external" / "vitap_zone_polygons.geojson", driver="GeoJSON")

        # Mock config to use our temporary directory
        with patch('data.unified_loader.get_config') as mock_get_config:
            mock_config = MagicMock()
            mock_config.paths.raw = temp_dir
            mock_config.paths.processed = Path(temp_dir) / "processed"
            mock_config.project.study_area.bbox.min_lat = 16.485
            mock_config.project.study_area.bbox.max_lat = 16.530
            mock_config.project.study_area.bbox.min_lon = 80.455
            mock_config.project.study_area.bbox.max_lon = 80.505
            mock_config.project.study_area.campus_center.latitude = 16.5075
            mock_config.project.study_area.campus_center.longitude = 80.4800
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
```

### Why This Fix Works
1. **Correct Patch Timing**: By creating the `UnifiedDataLoader` after applying the patches, its internal loader instances use the mocked methods
2. **Fast Execution**: All mocked methods return immediately with empty data
3. **Proper Error Detection**: The unified loader correctly identifies empty weather data and raises the expected `RuntimeError`
4. **Minimal Change**: Only moves one line of code and adjusts comment placement - no logic changes

### Verification
After applying this fix, the test should:
- Complete in <1 second (vs potentially timing out)
- Pass the assertion that a `RuntimeError` is raised with "Missing required weather data"
- Not require any changes to the production code in `unified_loader.py`

<em>Note: A secondary issue exists where the test creates cache files in the wrong directory structure (missing `data/` prefix), but this is irrelevant to the core timing problem since the mocked methods bypass cache loading entirely when properly timed.</em>