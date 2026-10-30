#!/usr/bin/env python3
"""
Script to refresh OSM roads cache with verification steps.
Follows the approved plan: pre-fetch verification, backup, fetch, validation, atomic replacement.
"""

import sys
import os
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point, LineString
import json
from loguru import logger

# Add the src directory to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from data.config_loader import get_config, get_data_sources_config
from data.osm_loader import OSMLoader
from data.provenance import ProvenanceTracker, Provenance

def main():
    logger.info("Starting OSM roads cache refresh...")

    # Step 1: Pre-Fetch Verification
    logger.info("=== PRE-FETCH VERIFICATION ===")

    # Load original configuration
    config = get_config("settings")
    sources_config = get_data_sources_config()
    osm_config = sources_config.data_sources.openstreetmap

    logger.info(f"Original mode from config: {osm_config.mode}")
    logger.info(f"Overpass endpoint: {osm_config.endpoint}")
    logger.info(f"Timeout: {osm_config.timeout_seconds} seconds")
    logger.info(f"Raw data path: {config.paths.raw}")
    logger.info(f"OSM output directory: {Path(config.paths.raw) / 'osm'}")
    logger.info(f"Roads query geometry_type: {osm_config.queries.roads._data['geometry_type']}")
    logger.info(f"Roads query tags: {list(osm_config.queries.roads._data['tags'].keys())}")

    # Verify fetch_roads will call API when mode != "local"
    # We'll create a loader instance and override its mode to 'api'
    logger.info("Creating OSMLoader instance and setting mode to 'api'...")
    loader = OSMLoader(config)  # Start with original config
    original_mode = loader.mode
    loader.mode = 'api'  # Override to force API call
    logger.info(f"Loader mode changed from '{original_mode}' to '{loader.mode}'")

    # Verify that when mode != 'local', fetch_roads will execute API call
    # (This is verified by inspecting the source code - lines 174-176 in osm_loader.py)
    logger.info("Verified: fetch_roads() will call Overpass API when mode != 'local'")

    # Step 2: Backup Existing Cache (MANDATORY before any fetch)
    logger.info("\n=== BACKUP EXISTING CACHE ===")
    backup_dir = Path(__file__).parent / "data" / "raw" / "osm"
    roads_file = backup_dir / "roads.geojson"

    # Create timestamped backup
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"roads.geojson.backup_{timestamp}"

    logger.info(f"Creating backup of existing cache: {backup_file}")
    try:
        import shutil
        shutil.copy2(roads_file, backup_file)
        logger.info(f"Backup created successfully: {backup_file}")
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        return 1

    # Step 3: Fetch Roads Data (API Mode)
    logger.info("\n=== FETCHING ROADS DATA (API MODE) ===")
    logger.info("Fetching roads data from Overpass API...")
    try:
        roads_gdf = loader.fetch_roads()
        logger.info(f"Fetched {len(roads_gdf)} road features from API")
    except Exception as e:
        logger.error(f"Failed to fetch roads data from API: {e}")
        return 1

    if roads_gdf.empty:
        logger.error("Fetched roads GeoDataFrame is empty!")
        return 1

    # Step 4: Validation of Fetched Data (In-Memory)
    logger.info("\n=== VALIDATING FETCHED DATA ===")

    # a. Geometry Type: All features must be LineString
    logger.info("Checking geometry types...")
    geom_types = roads_gdf.geometry.type.value_counts()
    logger.info(f"Geometry types distribution: {geom_types.to_dict()}")

    non_linestring = roads_gdf[roads_gdf.geometry.type != 'LineString']
    if len(non_linestring) > 0:
        logger.error(f"Validation FAILED: {len(non_linestring)} features are not LineString")
        logger.error(f"Non-LineString geometry types found: {non_linestring.geometry.type.unique()}")
        return 1
    logger.info("✓ Geometry type check PASSED: All features are LineString")

    # b. Coordinate Validity: All coordinates within valid ranges
    logger.info("Checking coordinate validity...")
    def coords_valid(geom):
        if geom.geom_type != 'LineString':
            return False
        for lon, lat in geom.coords:
            if not (-180 <= lon <= 180):
                return False
            if not (-90 <= lat <= 90):
                return False
        return True

    invalid_coords_mask = ~roads_gdf.geometry.apply(coords_valid)
    invalid_count = invalid_coords_mask.sum()
    if invalid_count > 0:
        logger.error(f"Validation FAILED: {invalid_count} features have invalid coordinates")
        invalid_geom = roads_gdf.loc[invalid_coords_mask, "geometry"]
        logger.error(f"Sample invalid geometries: {invalid_geom.head(3).to_list()}")
        return 1
    logger.info("✓ Coordinate validity check PASSED: All coordinates within [-180,180] longitude and [-90,90] latitude")

    # Check for null/invalid geometries
    null_geom = roads_gdf[roads_gdf.geometry.is_null()]
    if len(null_geom) > 0:
        logger.error(f"Validation FAILED: {len(null_geom)} features have null geometries")
        return 1
    logger.info("✓ Null geometry check PASSED: No null geometries")

    # c. Non-Zero Length (where applicable)
    logger.info("Checking segment lengths...")
    # Compute approximate length in meters: length_m = geometry.length * 111000 (EPSG:4326)
    roads_gdf['length_m'] = roads_gdf.geometry.length * 111000

    zero_length_mask = roads_gdf['length_m'] <= 0
    zero_length_count = zero_length_mask.sum()
    if zero_length_count > 0:
        zero_length_pct = (zero_length_count / len(roads_gdf)) * 100
        logger.warning(f"Found {zero_length_count} ways with zero or negative length ({zero_length_pct:.2f}%)")
        # According to plan, zero-length ways should be rare (<1% of features)
        if zero_length_pct > 1.0:
            logger.error(f"Validation FAILED: {zero_length_pct:.2f}% of ways have zero length (exceeds 1% threshold)")
            return 1
        else:
            logger.warning(f"Zero-length ways constitute {zero_length_pct:.2f}% (below 1% threshold, continuing)")
    logger.info("✓ Length check PASSED: Acceptable proportion of zero-length segments")

    # d. Preservation of OSM Identifiers and Tags
    logger.info("Checking OSM identifiers and tags...")
    # Check for non-null osm_id and osm_type
    if roads_gdf['osm_id'].isnull().any():
        logger.error("Validation FAILED: Some features have null osm_id")
        return 1
    if roads_gdf['osm_type'].isnull().any():
        logger.error("Validation FAILED: Some features have null osm_type")
        return 1
    # Check osm_type is 'way' for all (since we requested geometry_type="way")
    if not all(roads_gdf['osm_type'] == 'way'):
        logger.error("Validation FAILED: Some features have osm_type not equal to 'way'")
        return 1
    logger.info("✓ OSM identifiers check PASSED: All features have non-null osm_id and osm_type='way'")

    # Check that expected OSM tags are present (highway at least)
    if 'highway' not in roads_gdf.columns:
        logger.error("Validation FAILED: 'highway' column missing from fetched data")
        return 1
    null_highway = roads_gdf['highway'].isnull().sum()
    if null_highway > 0:
        logger.warning(f"Validation warning: {null_highway} features have null highway tag")
    logger.info("✓ OSM tags check PASSED: Highway column present")

    # e. Feature Count Sanity Check
    logger.info("Checking feature count sanity...")
    try:
        backup_gdf = gpd.read_file(backup_file)
        prev_count = len(backup_gdf)
        current_count = len(roads_gdf)
        logger.info(f"Previous feature count (from backup): {prev_count}")
        logger.info(f"Current feature count: {current_count}")

        # Allow range 50% to 200% of previous count
        lower_bound = prev_count * 0.5
        upper_bound = prev_count * 2.0
        if current_count < lower_bound or current_count > upper_bound:
            logger.error(f"Validation FAILED: Feature count {current_count} outside expected range [{lower_bound:.0f}, {upper_bound:.0f}]")
            return 1
        logger.info(f"✓ Feature count sanity check PASSED: {current_count} within 50%-200% of {prev_count}")
    except Exception as e:
        logger.warning(f"Could not read backup for feature count comparison: {e}")
        # Continue with validation - this is not critical

    logger.info("All validation checks PASSED!")

    # Step 5: Atomic Cache Replacement (Only After Validation Passes)
    logger.info("\n=== ATOMIC CACHE REPLACEMENT ===")
    logger.info("Preparing to replace cache with validated data...")

    # Write to temporary file in same directory with .geojson extension and timestamp
    temp_path = backup_dir / f"roads.geojson.new_{timestamp}.geojson"

    try:
        logger.info(f"Writing validated data to temporary file: {temp_path}")
        roads_gdf.to_file(temp_path, driver="GeoJSON")
        logger.info(f"Successfully wrote {len(roads_gdf)} features to {temp_path}")

        # Verify the temporary file was written correctly by reading it back
        logger.info("Verifying temporary file...")
        temp_gdf = gpd.read_file(temp_path)
        if len(temp_gdf) != len(roads_gdf):
            logger.error(f"Verification FAILED: Temporary file has {len(temp_gdf)} features, expected {len(roads_gdf)}")
            return 1
        logger.info("✓ Temporary file verification PASSED: Feature count matches")

        # Atomic replace: move temp file to original location
        logger.info(f"Atomically replacing original cache: {roads_file}")
        # On POSIX systems, os.replace is atomic if source and destination are on same filesystem
        os.replace(temp_path, roads_file)
        logger.info("Cache replacement SUCCESSFUL!")

        # Log success: record timestamp, feature count, and validation outcome
        logger.info(f"Refresh completed at {datetime.datetime.now().isoformat()}")
        logger.info(f"Feature count: {len(roads_gdf)}")
        logger.info("Validation: All checks passed (LineString, valid coordinates, OSM IDs, acceptable lengths)")

        return 0

    except Exception as e:
        logger.error(f"Failed during cache replacement: {e}")
        # Clean up temporary file if it exists
        if temp_path.exists():
            try:
                temp_path.unlink()
                logger.info(f"Cleaned up temporary file: {temp_path}")
            except Exception as e2:
                logger.error(f"Failed to clean up temporary file: {e2}")
        return 1

if __name__ == "__main__":
    sys.exit(main())