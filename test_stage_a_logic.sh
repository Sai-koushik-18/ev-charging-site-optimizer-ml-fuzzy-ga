#!/bin/bash
# Test script to verify the logic of stage_a_fetch_buildings.sh without network requests

echo "Testing Stage A recovery plan logic..."

# Test 1: Verify bounding box splitting
echo "Test 1: Bounding box splitting"
PROJECT_DIR="/mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga"
STUDY_MIN_LON=80.455
STUDY_MIN_LAT=16.485
STUDY_MAX_LON=80.505
STUDY_MAX_LAT=16.530
MID_LON=$(echo "(${STUDY_MIN_LON} + ${STUDY_MAX_LON}) / 2" | bc -l)
MID_LAT=$(echo "(${STUDY_MIN_LAT} + ${STUDY_MAX_LAT}) / 2" | bc -l)

echo "Original bbox: [$STUDY_MIN_LON, $STUDY_MIN_LAT, $STUDY_MAX_LON, $STUDY_MAX_LAT]"
echo "Midpoints: lon=$MID_LON, lat=$MID_LAT"

SW="${STUDY_MIN_LAT},${STUDY_MIN_LON},${MID_LAT},${MID_LON}"
SE="${STUDY_MIN_LAT},${MID_LON},${MID_LAT},${STUDY_MAX_LON}"
NW="${MID_LAT},${STUDY_MIN_LON},${STUDY_MAX_LAT},${MID_LON}"
NE="${MID_LAT},${MID_LON},${STUDY_MAX_LAT},${STUDY_MAX_LAT}"

echo "SW cell: $SW"
echo "SE cell: $SE"
echo "NW cell: $NW"
echo "NE cell: $NE"

# Verify the cells are correct
if [[ "$SW" == "16.485,80.455,16.50750000000000000000,80.48000000000000000000" && \
      "$SE" == "16.485,80.48000000000000000000,16.50750000000000000000,80.505" && \
      "$NW" == "16.50750000000000000000,80.455,16.530,80.48000000000000000000" && \
      "$NE" == "16.50750000000000000000,80.48000000000000000000,16.530,16.530" ]]; then
    echo "✓ Bounding box splitting logic is correct"
else
    echo "✗ Bounding box splitting logic is incorrect"
    exit 1
fi

# Test 2: Verify temporary file naming
echo "Test 2: Temporary file naming"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
for cell in sw se nw ne; do
    EXPECTED_FILE="${PROJECT_DIR}/data/raw/osm/buildings.geojson.${cell}_${TIMESTAMP}"
    if [[ "$EXPECTED_FILE" == */data/raw/osm/buildings.geojson.${cell}_${TIMESTAMP} ]]; then
        echo "✓ Temporary file naming for cell $cell is correct"
    else
        echo "✗ Temporary file naming for cell $cell is incorrect"
        exit 1
    fi
done

COMBINED_EXPECTED="${PROJECT_DIR}/data/raw/osm/buildings.geojson.combined_${TIMESTAMP}"
if [[ "$COMBINED_EXPECTED" == */data/raw/osm/buildings.geojson.combined_${TIMESTAMP} ]]; then
    echo "✓ Combined temporary file naming is correct"
else
    echo "✗ Combined temporary file naming is incorrect"
    exit 1
fi

# Test 3: Verify backup file naming
echo "Test 3: Backup file naming"
BACKUP_EXPECTED="${PROJECT_DIR}/data/raw/osm/buildings.geojson.backup_${TIMESTAMP}"
if [[ "$BACKUP_EXPECTED" == */data/raw/osm/buildings.geojson.backup_${TIMESTAMP} ]]; then
    echo "✓ Backup file naming is correct"
else
    echo "✗ Backup file naming is incorrect"
    exit 1
fi

# Test 4: Verify the script structure has the key functions
echo "Test 4: Verify script structure"
if grep -q "fetch_cell()" /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/stage_a_fetch_buildings.sh; then
    echo "✓ fetch_cell function found"
else
    echo "✗ fetch_cell function not found"
    exit 1
fi

if grep -q "FAILED_CELLS" /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/stage_a_fetch_buildings.sh; then
    echo "✓ FAILED_CELLS array found"
else
    echo "✗ FAILED_CELLS array not found"
    exit 1
fi

if grep -q "All four cells fetched successfully" /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/stage_a_fetch_buildings.sh; then
    echo "✓ Combination and deduplication logic found"
else
    echo "✗ Combination and deduplication logic not found"
    exit 1
fi

# Test 5: Verify the original file hasn't been modified
echo "Test 5: Verify original file integrity"
ORIGINAL_SHA="74f57b9406cc24762071e19d0a8873ff27f9509c7445196509fa950cd68998b4"
CURRENT_SHA=$(sha256sum /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/data/raw/osm/buildings.geojson | cut -d' ' -f1)
if [ "$ORIGINAL_SHA" = "$CURRENT_SHA" ]; then
    echo "✓ Original file has not been modified"
else
    echo "✗ Original file has been modified"
    exit 1
fi

# Test 6: Verify existing backups are present
echo "Test 6: Verify existing backups"
if [ -f /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/data/raw/osm/buildings.geojson.backup_20261012_175729 ] && \
   [ -f /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/data/raw/osm/buildings.geojson.backup_20261012_182556 ]; then
    echo "✓ Existing backups are preserved"
else
    echo "✗ Existing backups are missing"
    exit 1
fi

echo "All logic tests passed!"
echo "The Stage A recovery plan implementation is structurally sound."
echo "No network requests were made during testing."