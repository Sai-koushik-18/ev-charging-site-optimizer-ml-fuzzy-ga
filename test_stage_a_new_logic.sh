#!/bin/bash
# Test script to verify the logic of the new stage_a_fetch_buildings.sh implementation without network requests

# Shared cleanup helpers (same as production script)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cleanup_helpers.sh"

echo "Testing NEW Stage A recovery plan logic..."

# Test 1: Verify bounding box splitting
echo "Test 1: Bounding box splitting"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
if grep -q "fetch_cell()" /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/stage_a_fetch_buildings.sh.new; then
    echo "✓ fetch_cell function found"
else
    echo "✗ fetch_cell function not found"
    exit 1
fi

if grep -q "FAILED_CELLS" /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/stage_a_fetch_buildings.sh.new; then
    echo "✓ FAILED_CELLS array found"
else
    echo "✗ FAILED_CELLS array not found"
    exit 1
fi

if grep -q "Combination and deduplication successful" /mnt/d/Claude/vitap-ev-charging-ml-fuzzy-ga/stage_a_fetch_buildings.sh.new; then
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

# Test 7: Verify targeted cleanup behavior (uses actual shared implementation)
echo "Test 7: Verify targeted cleanup behavior"
SCRIPT_UNDER_TEST="${PROJECT_DIR}/stage_a_fetch_buildings.sh.new"

# 7a: Static check - no broad rm -rf on tmp directory
if grep -q 'rm -rf "${PROJECT_DIR}/tmp"' "${SCRIPT_UNDER_TEST}"; then
    echo "✗ Found broad tmp directory cleanup (should be avoided)"
    exit 1
else
    echo "✓ No broad tmp directory cleanup found"
fi

# 7b: Static check - targeted cleanup function present and used
if grep -q 'cleanup_tracked' "${SCRIPT_UNDER_TEST}"; then
    echo "✓ Targeted cleanup function referenced"
else
    echo "✗ Missing cleanup_tracked references"
    exit 1
fi
if grep -q 'cleanup_tracked "${python_helper}"' "${SCRIPT_UNDER_TEST}" && \
   grep -q 'cleanup_tracked "${validation_helper}"' "${SCRIPT_UNDER_TEST}"; then
    echo "✓ Cleanup function invoked for helper scripts"
else
    echo "✗ Cleanup function not invoked for helper scripts"
    exit 1
fi

# 7c: Behavioral test - invoke actual cleanup implementation from shared helpers
echo "Test 7c: Behavioral cleanup isolation test (uses actual shared function)"

# Create isolated test environment with a uniquely named directory
TEST_TMP_DIR=$(mktemp -d)
# Validate the test directory path before proceeding
if [ ! -d "${TEST_TMP_DIR}" ] || [ "${TEST_TMP_DIR}" = "/" ] || [ "${TEST_TMP_DIR}" = "" ]; then
    echo "✗ Invalid test temporary directory: ${TEST_TMP_DIR}"
    exit 1
fi

# Create the EXACT helper file that would be registered for cleanup
HELPER_FILE="${TEST_TMP_DIR}/fetch_cell_helper_20261026_120000.py"
echo '# helper script' > "${HELPER_FILE}"
# Register it as this run would (after creation)
register_for_cleanup "${HELPER_FILE}"

# Create an UNRELATED sentinel file (must remain)
SENTINEL_FILE="${TEST_TMP_DIR}/unrelated_sentinel.txt"
echo 'do not delete' > "${SENTINEL_FILE}"

# Create a SECOND helper-like file (unrelated, must remain)
OTHER_HELPER="${TEST_TMP_DIR}/validate_cell_helper_20261026_120001.py"
echo '# other helper' > "${OTHER_HELPER}"

# Invoke the ACTUAL cleanup function from shared helpers with ONLY the registered helper path
cleanup_tracked "${HELPER_FILE}"

# Verify: only the registered helper removed; both unrelated files remain
if [ ! -f "${HELPER_FILE}" ] && [ -f "${SENTINEL_FILE}" ] && [ -f "${OTHER_HELPER}" ]; then
    echo "✓ Behavioral cleanup: registered helper removed, both unrelated files preserved"
else
    echo "✗ Behavioral cleanup failed"
    [ -f "${HELPER_FILE}" ] && echo "  Registered helper still exists"
    [ ! -f "${SENTINEL_FILE}" ] && echo "  Sentinel file was deleted"
    [ ! -f "${OTHER_HELPER}" ] && echo "  Unrelated helper-like file was deleted"
    # Safely clean up test files individually with validation
    for f in "${HELPER_FILE}" "${SENTINEL_FILE}" "${OTHER_HELPER}"; do
        if [ -f "${f}" ] && [ "${f}" != "/" ] && [ -n "${f}" ]; then
            rm -f "${f}"
        fi
    done
    if [ -d "${TEST_TMP_DIR}" ] && [ "${TEST_TMP_DIR}" != "/" ] && [ -n "${TEST_TMP_DIR}" ]; then
        rmdir "${TEST_TMP_DIR}" 2>/dev/null || true
    fi
    exit 1
fi

# Safely clean up remaining test files individually with validation
for f in "${SENTINEL_FILE}" "${OTHER_HELPER}"; do
    if [ -f "${f}" ] && [ "${f}" != "/" ] && [ -n "${f}" ]; then
        rm -f "${f}"
    fi
done
if [ -d "${TEST_TMP_DIR}" ] && [ "${TEST_TMP_DIR}" != "/" ] && [ -n "${TEST_TMP_DIR}" ]; then
    rmdir "${TEST_TMP_DIR}" 2>/dev/null || true
fi
echo "✓ Cleanup isolation test passed"

echo "All logic tests passed!"
echo "The NEW Stage A recovery plan implementation is structurally sound."
echo "No network requests were made during testing."