#!/usr/bin/env python3
"""
Test script to verify configuration loading structure.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from data.config_loader import get_data_sources_config

def test_config_structure():
    """Test the configuration structure."""
    print("Testing configuration structure...")

    # Load the data sources config
    config = get_data_sources_config()

    # Print the type and available attributes
    print(f"Config type: {type(config)}")
    print(f"Config data keys: {list(config._data.keys())}")

    # Check if 'data_sources' is a top-level key
    if 'data_sources' in config._data:
        print("'data_sources' found as top-level key")
        data_sources = config._data['data_sources']
        print(f"data_sources type: {type(data_sources)}")
        print(f"data_sources keys: {list(data_sources.keys())}")

        # Test accessing the nested structure
        try:
            osm_config = config.data_sources.openstreetmap
            print("✓ config.data_sources.openstreetmap accessible")
            print(f"  OpenStreetMap source_name: {osm_config.source_name}")
        except AttributeError as e:
            print(f"✗ config.data_sources.openstreetmap failed: {e}")

        try:
            ocm_config = config.data_sources.openchargemap
            print("✓ config.data_sources.openchargemap accessible")
            print(f"  OpenChargeMap source_name: {ocm_config.source_name}")
        except AttributeError as e:
            print(f"✗ config.data_sources.openchargemap failed: {e}")

        try:
            weather_config = config.data_sources.open_meteo
            print("✓ config.data_sources.open_meteo accessible")
            print(f"  Open-Meteo source_name: {weather_config.source_name}")
        except AttributeError as e:
            print(f"✗ config.data_sources.open_meteo failed: {e}")

        try:
            zones_config = config.data_sources.campus_zones
            print("✓ config.data_sources.campus_zones accessible")
            print(f"  Campus zones source_name: {zones_config.source_name}")
        except AttributeError as e:
            print(f"✗ config.data_sources.campus_zones failed: {e}")

    else:
        print("'data_sources' NOT found as top-level key")
        # Test direct access (the old way)
        try:
            osm_config = config.openstreetmap
            print("✓ config.openstreetmap accessible (direct access)")
        except AttributeError as e:
            print(f"✗ config.openstreetmap failed: {e}")

if __name__ == "__main__":
    test_config_structure()