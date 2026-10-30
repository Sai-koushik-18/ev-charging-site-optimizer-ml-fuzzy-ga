#!/usr/bin/env python3
"""
Test script to verify local mode functionality in OSMLoader.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from data.config_loader import get_config, get_data_sources_config
from data.osm_loader import OSMLoader
from loguru import logger

def test_local_mode():
    """Test OSMLoader in local mode."""
    print("Testing OSMLoader in local mode...")

    # Load normal config
    config = get_config("settings")
    sources_config = get_data_sources_config()

    # Temporarily override the mode to local
    sources_config.data_sources.openstreetmap.mode = "local"

    # Override get_data_sources_config to return our modified config
    import data.config_loader as config_loader
    original_get_data_sources_config = config_loader.get_data_sources_config
    config_loader.get_data_sources_config = lambda: sources_config

    try:
        # Create loader with modified config
        loader = OSMLoader(config)

        print(f"Loader mode: {loader.mode}")

        # Test fetching roads
        print("\nTesting fetch_roads()...")
        roads_gdf = loader.fetch_roads()
        print(f"Roads: {len(roads_gdf)} features")
        if len(roads_gdf) > 0:
            print(f"  Columns: {list(roads_gdf.columns)}")
            print(f"  Sample osm_id: {roads_gdf['osm_id'].iloc[0] if 'osm_id' in roads_gdf.columns else 'N/A'}")

        # Test fetching buildings
        print("\nTesting fetch_buildings()...")
        buildings_gdf = loader.fetch_buildings()
        print(f"Buildings: {len(buildings_gdf)} features")

        # Test fetching POIs
        print("\nTesting fetch_pois()...")
        pois_gdf = loader.fetch_pois()
        print(f"POIs: {len(pois_gdf)} features")

        # Test fetching power
        print("\nTesting fetch_power()...")
        power_gdf = loader.fetch_power()
        print(f"Power: {len(power_gdf)} features")

        # Test fetching parking
        print("\nTesting fetch_parking()...")
        parking_gdf = loader.fetch_parking()
        print(f"Parking: {len(parking_gdf)} features")

        # Test fetch_all
        print("\nTesting fetch_all()...")
        all_data = loader.fetch_all()
        for layer, gdf in all_data.items():
            print(f"{layer}: {len(gdf)} features")

        print("\nLocal mode test completed successfully!")
        return True
    except Exception as e:
        print(f"\nLocal mode test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Restore original function
        config_loader.get_data_sources_config = original_get_data_sources_config

if __name__ == "__main__":
    success = test_local_mode()
    sys.exit(0 if success else 1)