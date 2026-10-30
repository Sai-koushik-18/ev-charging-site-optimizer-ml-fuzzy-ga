#!/bin/bash
set -euo pipefail

# Stage A: Fetch buildings data into temporary file, validate, report results
# Does NOT modify original file - Stage B (replacement) requires separate explicit approval

# Configuration
PROJECT_DIR="/mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ORIGINAL_FILE="${PROJECT_DIR}/data/raw/osm/buildings.geojson"
BACKUP_FILE="${PROJECT_DIR}/data/raw/osm/buildings.geojson.backup_${TIMESTAMP}"

# Read configuration to get bounding box and endpoint
# Source the config loader if available, otherwise parse YAML directly
if [ -f "${PROJECT_DIR}/src/data/config_loader.sh" ]; then
    source "${PROJECT_DIR}/src/data/config_loader.sh"
    # Get config values
    STUDY_MIN_LON=$(get_config_value "data_sources.openstreetmap.bbox.min_lon" "80.455")
    STUDY_MIN_LAT=$(get_config_value "data_sources.openstreetmap.bbox.min_lat" "16.485")
    STUDY_MAX_LON=$(get_config_value "data_sources.openstreetmap.bbox.max_lon" "80.505")
    STUDY_MAX_LAT=$(get_config_value "data_sources.openstreetmap.bbox.max_lat" "16.530")
else
    # Fallback: parse YAML directly
    CONFIG_FILE="${PROJECT_DIR}/config/data_sources.yaml"
    if [[ ! -f "${CONFIG_FILE}" ]]; then
        echo "ERROR: Config file not found: ${CONFIG_FILE}"
        exit 1
    fi

    # Extract bbox from YAML (simple parsing)
    STUDY_MIN_LON=$(grep -A 4 "bbox:" "${CONFIG_FILE}" | grep "min_lon" | sed 's/[^0-9.-]*//g')
    STUDY_MIN_LAT=$(grep -A 4 "bbox:" "${CONFIG_FILE}" | grep "min_lat" | sed 's/[^0-9.-]*//g')
    STUDY_MAX_LON=$(grep -A 4 "bbox:" "${CONFIG_FILE}" | grep "max_lon" | sed 's/[^0-9.-]*//g')
    STUDY_MAX_LAT=$(grep -A 4 "bbox:" "${CONFIG_FILE}" | grep "max_lat" | sed 's/[^0-9.-]*//g')

    # Set defaults if parsing failed
    STUDY_MIN_LON=${STUDY_MIN_LON:-80.455}
    STUDY_MIN_LAT=${STUDY_MIN_LAT:-16.485}
    STUDY_MAX_LON=${STUDY_MAX_LON:-80.505}
    STUDY_MAX_LAT=${STUDY_MAX_LAT:-16.530}
fi

# Calculate midpoints for 2x2 grid using bc for floating point math
MID_LON=$(echo "(${STUDY_MIN_LON} + ${STUDY_MAX_LON}) / 2" | bc -l)
MID_LAT=$(echo "(${STUDY_MIN_LAT} + ${STUDY_MAX_LAT}) / 2" | bc -l)

# Define four cells: [south, west, north, east] for Overpass
declare -A CELLS
CELLS["sw"]="${STUDY_MIN_LAT},${STUDY_MIN_LON},${MID_LAT},${MID_LON}"      # Southwest
CELLS["se"]="${STUDY_MIN_LAT},${MID_LON},${MID_LAT},${STUDY_MAX_LON}"    # Southeast
CELLS["nw"]="${MID_LAT},${STUDY_MIN_LON},${STUDY_MAX_LAT},${MID_LON}"   # Northwest
CELLS["ne"]="${MID_LAT},${MID_LON},${STUDY_MAX_LAT},${STUDY_MAX_LON}"   # Northeast

# Temporary files for this run
declare -A TEMP_FILES
for cell in "${!CELLS[@]}"; do
    TEMP_FILES[$cell]="${PROJECT_DIR}/data/raw/osm/buildings.geojson.${cell}_${TIMESTAMP}"
done
COMBINED_TEMP_FILE="${PROJECT_DIR}/data/raw/osm/buildings.geojson.combined_${TIMESTAMP}"

# Pre-flight checks: refuse to overwrite existing timestamped files
for cell in "${!TEMP_FILES[@]}"; do
    if [ -f "${TEMP_FILES[$cell]}" ]; then
        echo "ERROR: Temporary file already exists: ${TEMP_FILES[$cell]}"
        exit 1
    fi
done
if [ -f "${COMBINED_TEMP_FILE}" ]; then
    echo "ERROR: Combined temporary file already exists: ${COMBINED_TEMP_FILE}"
    exit 1
fi
if [ -f "${BACKUP_FILE}" ]; then
    echo "ERROR: Backup file already exists: ${BACKUP_FILE}"
    exit 1
fi

# Verify original exists before backup
if [ ! -f "${ORIGINAL_FILE}" ]; then
    echo "ERROR: Original file ${ORIGINAL_FILE} does not exist"
    exit 1
fi

# Create backup with SHA-256 verification
cp "${ORIGINAL_FILE}" "${BACKUP_FILE}"
if [ ! -f "${BACKUP_FILE}" ]; then
    echo "ERROR: Backup creation failed"
    exit 1
fi
BACKUP_CHECKSUM=$(sha256sum "${BACKUP_FILE}" | cut -d' ' -f1)
ORIGINAL_CHECKSUM=$(sha256sum "${ORIGINAL_FILE}" | cut -d' ' -f1)
if [ "${BACKUP_CHECKSUM}" != "${ORIGINAL_CHECKSUM}" ]; then
    echo "ERROR: Backup checksum mismatch"
    echo "  Original: ${ORIGINAL_CHECKSUM}"
    echo "  Backup:   ${BACKUP_CHECKSUM}"
    exit 1
fi

# Function to fetch a single cell using the existing OSMLoader with modified bbox
fetch_cell() {
    local cell_name=$1
    local bbox=$2
    local temp_file=$3

    echo "Fetching buildings for cell ${cell_name} with bbox: ${bbox}"

    # Create a temporary Python script that uses the OSMLoader with overridden bbox
    local python_script="${PROJECT_DIR}/tmp/fetch_cell_${cell_name}_${TIMESTAMP}.py"
    mkdir -p "${PROJECT_DIR}/tmp"

    cat > "${python_script}" << EOF
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.data.osm_loader import OSMLoader
from src.data.provenance import ProvenanceTracker, Provenance
from src.data.config_loader import get_config, get_data_sources_config

# Initialize loader
loader = OSMLoader()
loader.mode = 'api'

# Override the bbox in the loader's config
# We need to modify the bbox that _get_bbox returns
original_get_bbox = loader._get_bbox
def custom_get_bbox():
    return [${bbox//,/ , }]
loader._get_bbox = custom_get_bbox

# Execute API query sequence manually (matches fetch_buildings API path)
geometry_type = loader.osm_config.queries.buildings._data['geometry_type']
tags = loader.osm_config.queries.buildings._data['tags']
query = loader._build_overpass_query(tags, geometry_type)
raw_data = loader._execute_query(query)
gdf = loader._elements_to_geodataframe(raw_data.get('elements', []), geometry_type)

# Filter to requested geometry types
requested_types = [t.strip() for t in geometry_type.split(',')]
gdf = gdf[gdf['osm_type'].isin(requested_types)].copy()

# Check for zero Polygon features before proceeding
if not gdf.empty:
    gdf = gdf[gdf.geometry.type == 'Polygon'].copy()
    if len(gdf) == 0:
        print('ERROR: Zero Polygon features found after filtering to Polygon geometries')
        sys.exit(1)
else:
    # If gdf is empty after filtering by requested types, we have zero features
    print('ERROR: Zero features found after filtering by requested geometry types')
    sys.exit(1)

gdf['building_type'] = gdf.get('building', 'yes')

# Validate CRS is defined before projection
if gdf.crs is None:
    print('ERROR: CRS is undefined in fetched GeoDataFrame')
    sys.exit(1)

# CORRECTED AREA CALCULATION: Use EPSG:32644 projection
try:
    gdf_utm = gdf.to_crs('EPSG:32644')
except Exception as e:
    print(f'ERROR: CRS conversion to EPSG:32644 failed: {e}')
    sys.exit(1)

gdf['footprint_area_sqm'] = gdf_utm.geometry.area

tracker = ProvenanceTracker('buildings', 'osm_loader.fetch_buildings')
tracker.add_columns_batch(
    columns=['osm_id', 'osm_type', 'building', 'name', 'building_type'],
    provenance=Provenance.REAL,
    source='openstreetmap',
    description='Building footprints from OSM',
)
tracker.add_column(
    'geometry',
    Provenance.REAL,
    'openstreetmap',
    'Building footprint (Polygon)',
)
tracker.add_column(
    'footprint_area_sqm',
    Provenance.DERIVED,
    'computed',
    'Footprint area in square meters',
    transformation='geometry.area in EPSG:32644 (UTM zone 44N)',
)
gdf = tracker.finalize(gdf)

gdf.to_file(r'${temp_file}', driver='GeoJSON')
print('FETCH_COMPLETE:' + str(len(gdf)))
EOF

    # Execute the Python script
    if .venv/bin/python "${python_script}"; then
        FETCH_SUCCESS=true
        rm -f "${python_script}"
    else
        FETCH_SUCCESS=false
        rm -f "${python_script}"
        echo "FETCH_FAILED: Python script exited with error for cell ${cell_name}"
    fi

    if [ "${FETCH_SUCCESS}" = false ]; then
        echo "ERROR: Fetch failed for cell ${cell_name} - cleaning up temporary file only"
        rm -f "${temp_file}"
        return 1
    fi

    # Validation of isolated result (READ-ONLY - NEVER MODIFIES ORIGINAL)
    if [ ! -f "${temp_file}" ]; then
        echo "ERROR: Fetch produced no output for cell ${cell_name}"
        rm -f "${temp_file}"
        return 1
    fi

    # Initialize validation results
    VALIDATION_RESULTS=""

    # VALIDATION 1: Feature count > 0
    if FEATURE_COUNT=$(.venv/bin/python -c "
import geopandas as gpd
print(len(gpd.read_file(r'${temp_file}')));"); then
        if [ -n "${FEATURE_COUNT}" ] && [ "${FEATURE_COUNT}" -gt 0 ]; then
            VALIDATION_RESULTS="${VALIDATION_RESULTS}FEATURE_COUNT:✓:${FEATURE_COUNT} "
        else
            echo "VALIDATION_FAILED: Feature count check failed or zero features for cell ${cell_name}"
            rm -f "${temp_file}"
            return 1
        fi
    else
        echo "VALIDATION_FAILED: Feature count command failed for cell ${cell_name}"
        rm -f "${temp_file}"
        return 1
    fi

    # VALIDATION 2: All geometries are Polygon
    if IS_ALL_POLYGON=$(.venv/bin/python -c "
import geopandas as gpd
gdf = gpd.read_file(r'${temp_file}')
result = (gdf.geometry.type == 'Polygon').all()
print(str(result).lower());"); then
        if [ "${IS_ALL_POLYGON}" = "true" ]; then
            VALIDATION_RESULTS="${VALIDATION_RESULTS}ALL_POLYGON:✓ "
        else
            echo "VALIDATION_FAILED: Not all geometries are Polygon for cell ${cell_name}"
            rm -f "${temp_file}"
            return 1
        fi
    else
        echo "VALIDATION_FAILED: Polygon geometry check command failed for cell ${cell_name}"
        rm -f "${temp_file}"
        return 1
    fi

    # VALIDATION 3: All geometries are valid
    if IS_ALL_VALID=$(.venv/bin/python -c "
import geopandas as gpd
gdf = gpd.read_file(r'${temp_file}')
result = gdf.is_valid.all()
print(str(result).lower());"); then
        if [ "${IS_ALL_VALID}" = "true" ]; then
            VALIDATION_RESULTS="${VALIDATION_RESULTS}ALL_VALID:✓ "
        else
            echo "VALIDATION_FAILED: Some geometries are invalid for cell ${cell_name}"
            rm -f "${temp_file}"
            return 1
        fi
    else
        echo "VALIDATION_FAILED: Geometry validity check command failed for cell ${cell_name}"
        rm -f "${temp_file}"
        return 1
    fi

    # VALIDATION 4: Required OSM attributes present
    if HAS_REQUIRED_ATTRS=$(.venv/bin/python -c "
import geopandas as gpd
gdf = gpd.read_file(r'${temp_file}')
required = ['osm_id', 'osm_type', 'building']
missing = [col for col in required if col not in gdf.columns]
print(str(len(missing) == 0).lower());"); then
        if [ "${HAS_REQUIRED_ATTRS}" = "true" ]; then
            VALIDATION_RESULTS="${VALIDATION_RESULTS}HAS_REQUIRED_ATTRS:✓ "
        else
            echo "VALIDATION_FAILED: Missing required OSM attributes for cell ${cell_name}"
            rm -f "${temp_file}"
            return 1
        fi
    else
        echo "VALIDATION_FAILED: Required attributes check command failed for cell ${cell_name}"
        rm -f "${temp_file}"
        return 1
    fi

    # VALIDATION 5: Provenance metadata persists and readable
    if PROVENANCE_READABLE=$(.venv/bin/python -c "
import geopandas as gpd
import json
gdf = gpd.read_file(r'${temp_file}')
if 'data_provenance' not in gdf.columns:
    print('false')
    exit()
try:
    sample = json.loads(gdf.iloc[0]['data_provenance'])
    has_osm_id = 'osm_id' in sample and 'provenance' in sample['osm_id']
    has_geometry = 'geometry' in sample and 'provenance' in sample['geometry']
    print(str(has_osm_id and has_geometry).lower())
except:
    print('false')
"); then
        if [ "${PROVENANCE_READABLE}" = "true" ]; then
            VALIDATION_RESULTS="${VALIDATION_RESULTS}PROVENANCE_READABLE:✓ "
        else
            echo "VALIDATION_FAILED: Provenance metadata not readable for cell ${cell_name}"
            rm -f "${temp_file}"
            return 1
        fi
    else
        echo "VALIDATION_FAILED: Provenance metadata check command failed for cell ${cell_name}"
        rm -f "${temp_file}"
        return 1
    fi

    # VALIDATION 6: Area values calculated correctly using EPSG:32644 with tolerance
    if AREA_VALIDATION=$(.venv/bin/python -c "
import geopandas as gpd
import numpy as np
gdf = gpd.read_file(r'${temp_file}')
if len(gdf) == 0 or 'footprint_area_sqm' not in gdf.columns:
    print('false')
    exit()
gdf_utm = gdf.to_crs('EPSG:32644')
recalculated_areas = gdf_utm.geometry.area
stored_areas = gdf['footprint_area_sqm'].values
print(str(np.allclose(stored_areas, recalculated_areas, rtol=1e-10, atol=1e-10)).lower());"); then
        if [ "${AREA_VALIDATION}" = "true" ]; then
            VALIDATION_RESULTS="${VALIDATION_RESULTS}AREA_VALIDATION:✓ "
        else
            echo "VALIDATION_FAILED: Area values incorrect for cell ${cell_name}"
            rm -f "${temp_file}"
            return 1
        fi
    else
        echo "VALIDATION_FAILED: Area validation command failed for cell ${cell_name}"
        rm -f "${temp_file}"
        return 1
    fi

    # Print validation summary for this cell
    echo "=== VALIDATION SUMMARY FOR CELL ${cell_name} ==="
    echo "Feature count: ${FEATURE_COUNT}"
    echo "All geometries Polygon: ✓"
    echo "All geometries valid: ✓"
    echo "Required OSM attributes present: ✓"
    echo "Provenance metadata readable: ✓"
    echo "Area values correct (EPSG:32644): ✓"
    echo "=========================="

    return 0
}

# Fetch all four cells
FAILED_CELLS=()
for cell in sw se nw ne; do
    if ! fetch_cell "${cell}" "${CELLS[$cell]}" "${TEMP_FILES[$cell]}"; then
        FAILED_CELLS+=("${cell}")
    fi
done

# Check if any cells failed
if [ ${#FAILED_CELLS[@]} -gt 0 ]; then
    echo "ERROR: Failed to fetch cells: ${FAILED_CELLS[*]}"
    echo "ERROR: Fetch failed - cleaning up temporary files only"
    # Clean up only the temporary files we created
    for cell in "${!TEMP_FILES[@]}"; do
        rm -f "${TEMP_FILES[$cell]}"
    done
    rm -f "${COMBINED_TEMP_FILE}"
    # Clean up tmp directory
    rm -rf "${PROJECT_DIR}/tmp"
    exit 1
fi

# All cells fetched successfully, now combine and deduplicate
echo "All four cells fetched successfully. Combining and deduplicating..."
if .venv/bin/python -c "
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
import os

# Load all four cell results
gdfs = []
for cell in ['sw', 'se', 'nw', 'ne']:
    temp_file = r'${TEMP_FILES[cell]}'
    if os.path.exists(temp_file):
        gdf = gpd.read_file(temp_file)
        gdfs.append(gdf)
        print(f'Loaded {len(gdf)} features from cell {cell}')
    else:
        print(f'ERROR: Missing temporary file for cell {cell}')
        exit(1)

if not gdfs:
    print('ERROR: No data loaded from any cell')
    exit(1)

# Combine all GeoDataFrames
combined_gdf = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs='EPSG:4326')
print(f'Combined before deduplication: {len(combined_gdf)} features')

# Deduplicate by (osm_type, osm_id) - keep first occurrence
if 'osm_type' in combined_gdf.columns and 'osm_id' in combined_gdf.columns:
    # Create a temporary key for deduplication
    combined_gdf['_dedup_key'] = combined_gdf['osm_type'].astype(str) + '_' + combined_gdf['osm_id'].astype(str)
    # Keep first occurrence of each key
    combined_gdf = combined_gdf.drop_duplicates(subset=['_dedup_key'], keep='first')
    # Remove the temporary key
    combined_gdf = combined_gdf.drop(columns=['_dedup_key'])
    print(f'After deduplication: {len(combined_gdf)} features')
else:
    print('WARNING: Could not deduplicate - missing osm_type or osm_id columns')

# Save combined result
combined_gdf.to_file(r'${COMBINED_TEMP_FILE}', driver='GeoJSON')
print(f'Saved combined result to ${COMBINED_TEMP_FILE}')
exit(0);"; then
    echo "Combination and deduplication successful"
else
    echo "ERROR: Failed to combine and deduplicate results"
    # Clean up temporary files
    for cell in "${!TEMP_FILES[@]}"; do
        rm -f "${TEMP_FILES[$cell]}"
    done
    rm -f "${COMBINED_TEMP_FILE}"
    # Clean up tmp directory
    rm -rf "${PROJECT_DIR}/tmp"
    exit 1
fi

# Validate the combined result using the existing validation logic
echo "Validating combined temporary file..."
if VALIDATION_OUTPUT=$(.venv/bin/python -c "
import geopandas as gpd
import json
import numpy as np
from shapely.geometry import box
import sys
import os

# Load the combined temporary file
temp_file = r'${COMBINED_TEMP_FILE}'
if not os.path.exists(temp_file):
    print('ERROR: Combined temporary file not found')
    sys.exit(1)

try:
    gdf = gpd.read_file(temp_file)
except Exception as e:
    print(f'ERROR: Failed to read GeoJSON file: {e}')
    sys.exit(1)

# Initialize validation results
validation_passed = True

# VALIDATION 1: Feature count > 0
feature_count = len(gdf)
if feature_count > 0:
    print('FEATURE_COUNT:✓:' + str(feature_count))
else:
    print('VALIDATION_FAILED: Feature count check failed or zero features')
    validation_passed = False

# VALIDATION 2: All geometries are Polygon
if (gdf.geometry.type == 'Polygon').all():
    print('ALL_POLYGON:✓')
else:
    print('VALIDATION_FAILED: Not all geometries are Polygon')
    validation_passed = False

# VALIDATION 3: All geometries are valid
if gdf.is_valid.all():
    print('ALL_VALID:✓')
else:
    print('VALIDATION_FAILED: Some geometries are invalid')
    validation_passed = False

# VALIDATION 4: Required OSM attributes present
required = ['osm_id', 'osm_type', 'building']
missing = [col for col in required if col not in gdf.columns]
if len(missing) == 0:
    print('HAS_REQUIRED_ATTRS:✓')
else:
    print('VALIDATION_FAILED: Missing required OSM attributes')
    validation_passed = False

# VALIDATION 5: Provenance metadata persists and readable
if 'data_provenance' in gdf.columns:
    try:
        sample = json.loads(gdf.iloc[0]['data_provenance'])
        has_osm_id = 'osm_id' in sample and 'provenance' in sample['osm_id']
        has_geometry = 'geometry' in sample and 'provenance' in sample['geometry']
        if has_osm_id and has_geometry:
            print('PROVENANCE_READABLE:✓')
        else:
            print('VALIDATION_FAILED: Provenance metadata not readable')
            validation_passed = False
    except:
        print('VALIDATION_FAILED: Provenance metadata not readable')
        validation_passed = False
else:
    print('VALIDATION_FAILED: Provenance metadata not readable')
    validation_passed = False

# VALIDATION 6: Area values calculated correctly using EPSG:32644 with tolerance
if len(gdf) > 0 and 'footprint_area_sqm' in gdf.columns:
    gdf_utm = gdf.to_crs('EPSG:32644')
    recalculated_areas = gdf_utm.geometry.area
    stored_areas = gdf['footprint_area_sqm'].values
    if np.allclose(stored_areas, recalculated_areas, rtol=1e-10, atol=1e-10):
        print('AREA_VALIDATION:✓')
    else:
        print('VALIDATION_FAILED: Area values incorrect')
        validation_passed = False
else:
    print('VALIDATION_FAILED: Area validation command failed')
    validation_passed = False

# BOUNDS CHECK: Use actual configured bbox polygon (REPORT ONLY - NOT PASS/FAIL)
if len(gdf) > 0:
    # Configured bounding box from data_sources.yaml
    study_min_lon, study_min_lat = ${STUDY_MIN_LON}, ${STUDY_MIN_LAT}
    study_max_lon, study_max_lat = ${STUDY_MAX_LON}, ${STUDY_MAX_LAT}
    study_area_box = box(study_min_lon, study_min_lat, study_max_lon, study_max_lat)
    completely_outside = 0
    partly_crossing = 0
    wholly_inside = 0
    for idx, row in gdf.iterrows():
        feat_geom = row.geometry
        if not feat_geom.intersects(study_area_box):
            completely_outside += 1
        elif study_area_box.contains(feat_geom):
            wholly_inside += 1
        else:
            partly_crossing += 1
    print(f'BOUNDS_CHECK:✓:{completely_outside},{partly_crossing},{wholly_inside}')
else:
    print('BOUNDS_CHECK:✓:0,0,0')

# Final validation check
if validation_passed:
    print('COMBINED_VALIDATION:PASSED')
    sys.exit(0)
else:
    print('COMBINED_VALIDATION:FAILED')
    sys.exit(1)
"); then
    echo "=== COMBINED VALIDATION SUMMARY ==="
    echo "$VALIDATION_OUTPUT"
    # Check if combined validation passed
    if echo "$VALIDATION_OUTPUT" | grep -q "COMBINED_VALIDATION:PASSED"; then
        echo "STAGE_A_SUCCESS: All validations passed"
        echo "Combined temporary file: ${COMBINED_TEMP_FILE}"
        echo "NEXT_STAGE: Await explicit approval for Stage B (replacement)"

        # Report bounds check results
        bounds_line=$(echo "$VALIDATION_OUTPUT" | grep "BOUNDS_CHECK:")
        if [[ $bounds_line =~ BOUNDS_CHECK:✓:([0-9]+),([0-9]+),([0-9]+) ]]; then
            OUTSIDE_COUNT=${BASH_REMATCH[1]}
            CROSSING_COUNT=${BASH_REMATCH[2]}
            INSIDE_COUNT=${BASH_REMATCH[3]}
            echo "Boundary analysis:"
            echo "  Completely outside study area: ${OUTSIDE_COUNT} features"
            echo "  Partly crossing boundary: ${CROSSING_COUNT} features"
            echo "  Wholly inside study area: ${INSIDE_COUNT} features"
        fi

        # Clean up tmp directory
        rm -rf "${PROJECT_DIR}/tmp"
        # Don't exit with error - success case
        exit 0
    else
        echo "ERROR: Combined validation failed"
        # Clean up temporary files
        for cell in "${!TEMP_FILES[@]}"; do
            rm -f "${TEMP_FILES[$cell]}"
        done
        rm -f "${COMBINED_TEMP_FILE}"
        # Clean up tmp directory
        rm -rf "${PROJECT_DIR}/tmp"
        exit 1
    fi
else
    echo "ERROR: Validation script failed"
    # Clean up temporary files
    for cell in "${!TEMP_FILES[@]}"; do
        rm -f "${TEMP_FILES[$cell]}"
    done
    rm -f "${COMBINED_TEMP_FILE}"
    # Clean up tmp directory
    rm -rf "${PROJECT_DIR}/tmp"
    exit 1
fi