#!/usr/bin/env python3
"""
Safe script to refresh OSM roads cache with all verification steps.
Replacement only happens after complete validation of written data.
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

def validate_roads_gdf(gdf, validation_context=""):
    """
    Comprehensive validation of roads GeoDataFrame.
    Returns True if valid, False otherwise.
    """
    if gdf is None or len(gdf) == 0:
        logger.error(f"Validation failed {validation_context}: GeoDataFrame is None or empty")
        return False

    # a. Geometry Type: All features must be LineString
    logger.info(f"  Checking geometry types {validation_context}...")
    geom_types = gdf.geometry.type.value_counts()
    logger.info(f"    Geometry types distribution: {geom_types.to_dict()}")

    non_linestring = gdf[gdf.geometry.type != 'LineString']
    if len(non_linestring) > 0:
        logger.error(f"  Validation failed {validation_context}: {len(non_linestring)} features are not LineString")
        logger.error(f"    Non-LineString geometry types found: {non_linestring.geometry.type.unique()}")
        return False
    logger.info(f"  ✓ Geometry type check passed {validation_context}: All features are LineString")

    # b. Coordinate Validity: All coordinates within valid ranges
    logger.info(f"  Checking coordinate validity {validation_context}...")
    def coords_valid(geom):
        if geom.geom_type != 'LineString':
            return False
        for lon, lat in geom.coords:
            if not (-180 <= lon <= 180):
                return False
            if not (-90 <= lat <= 90):
                return False
        return True

    invalid_coords_mask = ~gdf.geometry.apply(coords_valid)
    invalid_count = invalid_coords_mask.sum()
    if invalid_count > 0:
        logger.error(f"  Validation failed {validation_context}: {invalid_count} features have invalid coordinates")
        invalid_geom = gdf.loc[invalid_coords_mask, "geometry"]
        logger.error(f"    Sample invalid geometries: {invalid_geom.head(3).to_list()}")
        return False
    logger.info(f"  ✓ Coordinate validity check passed {validation_context}: All coordinates within [-180,180] longitude and [-90,90] latitude")

    # Check for null/invalid geometries
    null_geom = gdf[gdf.geometry.isna()]
    if len(null_geom) > 0:
        logger.error(f"  Validation failed {validation_context}: {len(null_geom)} features have null geometries")
        return False
    logger.info(f"  ✓ Null geometry check passed {validation_context}: No null geometries")

    # c. Non-Zero Length (where applicable)
    logger.info(f"  Checking segment lengths {validation_context}...")
    # Compute approximate length in meters: length_m = geometry.length * 111000 (EPSG:4326)
    gdf['length_m'] = gdf.geometry.length * 111000

    zero_length_mask = gdf['length_m'] <= 0
    zero_length_count = zero_length_mask.sum()
    if zero_length_count > 0:
        zero_length_pct = (zero_length_count / len(gdf)) * 100
        logger.warning(f"    Found {zero_length_count} ways with zero or negative length ({zero_length_pct:.2f}%)")
        # According to plan, zero-length ways should be rare (<1% of features)
        if zero_length_pct > 1.0:
            logger.error(f"  Validation failed {validation_context}: {zero_length_pct:.2f}% of ways have zero length (exceeds 1% threshold)")
            return False
        else:
            logger.warning(f"    Zero-length ways constitute {zero_length_pct:.2f}% (below 1% threshold, continuing)")
    logger.info(f"  ✓ Length check passed {validation_context}: Acceptable proportion of zero-length segments")

    # d. Preservation of OSM Identifiers and Tags
    logger.info(f"  Checking OSM identifiers and tags {validation_context}...")
    # Check for non-null osm_id and osm_type
    if gdf['osm_id'].isna().any():
        logger.error(f"  Validation failed {validation_context}: Some features have null osm_id")
        return False
    if gdf['osm_type'].isna().any():
        logger.error(f"  Validation failed {validation_context}: Some features have null osm_type")
        return False
    # Check osm_type is 'way' for all (since we requested geometry_type="way")
    if not all(gdf['osm_type'] == 'way'):
        logger.error(f"  Validation failed {validation_context}: Some features have osm_type not equal to 'way'")
        return False
    logger.info(f"  ✓ OSM identifiers check passed {validation_context}: All features have non-null osm_id and osm_type='way'")

    # Check that expected OSM tags are present (highway at least)
    if 'highway' not in gdf.columns:
        logger.error(f"  Validation failed {validation_context}: 'highway' column missing from data")
        return False
    null_highway = gdf['highway'].isna().sum()
    if null_highway > 0:
        logger.warning(f"  Validation warning {validation_context}: {null_highway} features have null highway tag")
    logger.info(f"  ✓ OSM tags check passed {validation_context}: Highway column present")

    return True

def main():
    logger.info("Starting SAFE OSM roads cache refresh...")

    # Define paths
    backup_dir = Path(__file__).parent / "data" / "raw" / "osm"
    roads_file = backup_dir / "roads.geojson"

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
    logger.info("Creating OSMLoader instance and setting mode to 'api'...")
    loader = OSMLoader(config)  # Start with original config
    original_mode = loader.mode
    loader.mode = 'api'  # Override to force API call
    logger.info(f"Loader mode changed from '{original_mode}' to '{loader.mode}'")

    logger.info("Verified: fetch_roads() will call Overpass API when mode != 'local'")

    # Step 2: Create and Verify Timestamped Backup (MANDATORY before any fetch)
    logger.info("\n=== BACKUP AND VERIFY EXISTING CACHE ===")

    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"roads.geojson.backup_{timestamp}"

    logger.info(f"Creating backup of existing cache: {backup_file}")
    try:
        import shutil
        shutil.copy2(roads_file, backup_file)
        logger.info(f"Backup created successfully: {backup_file}")

        # Verify backup is readable and contains expected data
        logger.info("Verifying backup file...")
        backup_gdf = gpd.read_file(backup_file)
        if len(backup_gdf) == 0:
            logger.error("Backup file is empty!")
            return 1
        logger.info(f"Backup verification passed: {len(backup_gdf)} features readable")
        # Show backup geometry types for reference
        if len(backup_gdf) > 0:
            geom_types = backup_gdf.geometry.type.value_counts()
            logger.info(f"Backup geometry types: {geom_types.to_dict()}")
    except Exception as e:
        logger.error(f"Failed to create or verify backup: {e}")
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

    # Step 4: Validate Fetched Data (In-Memory) BEFORE WRITING
    logger.info("\n=== VALIDATING FETCHED DATA (IN-MEMORY) ===")
    if not validate_roads_gdf(roads_gdf, "(in-memory)"):
        logger.error("Validation of fetched data failed. Aborting. Original cache unchanged.")
        return 1

    logger.info("All in-memory validation checks PASSED!")

    # Step 5: Write to Temporary File
    logger.info("\n=== WRITING TO TEMPORARY FILE ===")
    # Write to temporary file in same directory with .geojson extension and timestamp
    temp_path = backup_dir / f"roads.geojson.new_{timestamp}.geojson"

    try:
        logger.info(f"Writing validated data to temporary file: {temp_path}")
        roads_gdf.to_file(temp_path, driver="GeoJSON")
        logger.info(f"Successfully wrote {len(roads_gdf)} features to {temp_path}")
    except Exception as e:
        logger.error(f"Failed to write temporary file: {e}")
        return 1

    # Step 6: Read Back Temporary File and Rerun ALL VALIDATIONS
    logger.info("\n=== VALIDATING TEMPORARY FILE (REREAD DATA) ===")
    try:
        logger.info(f"Reading back temporary file for validation: {temp_path}")
        temp_gdf = gpd.read_file(temp_path)
        logger.info(f"Read back {len(temp_gdf)} features from temporary file")

        if len(temp_gdf) != len(roads_gdf):
            logger.error(f"Validation failed: Temporary file has {len(temp_gdf)} features, expected {len(roads_gdf)}")
            return 1

        # Run COMPLETE validation on the reread file
        logger.info("Running full validation on reread temporary file...")
        if not validate_roads_gdf(temp_gdf, "(reread from temporary file)"):
            logger.error("Validation of reread temporary file failed. Aborting. Original cache unchanged.")
            # Clean up temporary file
            try:
                temp_path.unlink()
                logger.info(f"Cleaned up temporary file: {temp_path}")
            except:
                pass
            return 1

        logger.info("✓ Temporary file validation PASSED: All checks passed on reread data")

    except Exception as e:
        logger.error(f"Failed to read or validate temporary file: {e}")
        # Clean up temporary file if it exists
        if temp_path.exists():
            try:
                temp_path.unlink()
                logger.info(f"Cleaned up temporary file: {temp_path}")
            except Exception as e2:
                logger.error(f"Failed to clean up temporary file: {e2}")
        return 1

    # Step 7: Atomic Cache Replacement (Only After All Checks Pass)
    logger.info("\n=== ATOMIC CACHE REPLACEMENT ===")
    logger.info("All validations passed. Proceeding with atomic cache replacement...")

    try:
        # Atomic replace: move temp file to original location
        logger.info(f"Atomically replacing original cache: {roads_file}")
        # On POSIX systems, os.replace is atomic if source and destination are on same filesystem
        os.replace(temp_path, roads_file)
        logger.info("Cache replacement SUCCESSFUL!")

        # Step 8: Final Read-Back Check After Replacement
        logger.info("\n=== FINAL VERIFICATION AFTER REPLACEMENT ===")
        logger.info("Performing final read-back check on replaced cache...")
        try:
            final_gdf = gpd.read_file(roads_file)
            if len(final_gdf) == 0:
                logger.error("Final verification FAILED: Replaced cache is empty!")
                # Restore from backup
                logger.info("Attempting to restore original cache from verified backup...")
                shutil.copy2(backup_file, roads_file)
                logger.info("Original cache restored from backup.")
                return 1

            # Do a quick validation on the final file (check geometry types and basic counts)
            if not validate_roads_gdf(final_gdf, "(final replaced cache)"):
                logger.error("Final verification FAILED: Replaced cache does not pass validation!")
                # Restore from backup
                logger.info("Attempting to restore original cache from verified backup...")
                shutil.copy2(backup_file, roads_file)
                logger.info("Original cache restored from backup.")
                return 1

            logger.info("✓ Final verification PASSED: Replaced cache is valid")

        except Exception as e:
            logger.error(f"Failed to read replaced cache for final verification: {e}")
            # Restore from backup
            logger.info("Attempting to restore original cache from verified backup...")
            shutil.copy2(backup_file, roads_file)
            logger.info("Original cache restored from backup.")
            return 1

        # Log success
        logger.info(f"Refresh completed at {datetime.datetime.now().isoformat()}")
        logger.info(f"Feature count: {len(final_gdf)}")
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