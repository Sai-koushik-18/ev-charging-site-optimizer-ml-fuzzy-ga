#!/usr/bin/env python3
"""
Test script to verify settings configuration loading structure.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from data.config_loader import get_config

def test_settings_config_structure():
    """Test the settings configuration structure."""
    print("Testing settings configuration structure...")

    # Load the settings config
    config = get_config("settings")

    # Print the type and available attributes
    print(f"Config type: {type(config)}")
    print(f"Config data keys: {list(config._data.keys())}")

    # Check if 'project' is a top-level key
    if 'project' in config._data:
        print("'project' found as top-level key")
        project = config._data['project']
        print(f"project type: {type(project)}")
        print(f"project keys: {list(project.keys())}")

        # Test accessing the nested structure for study_area
        try:
            study_area = config.project.study_area
            print("✓ config.project.study_area accessible")
            print(f"  Study area name: {study_area.name}")

            # Test accessing bbox
            try:
                bbox = config.project.study_area.bbox
                print("✓ config.project.study_area.bbox accessible")
                print(f"  Bbox: {bbox}")

                # Test accessing individual bbox components
                try:
                    min_lon = config.project.study_area.bbox.min_lon
                    min_lat = config.project.study_area.bbox.min_lat
                    max_lon = config.project.study_area.bbox.max_lon
                    max_lat = config.project.study_area.bbox.max_lat
                    print("✓ Individual bbox components accessible")
                    print(f"  Bbox: [{min_lon}, {min_lat}, {max_lon}, {max_lat}]")
                except AttributeError as e:
                    print(f"✗ Failed to access bbox components: {e}")

            except AttributeError as e:
                print(f"✗ config.project.study_area.bbox failed: {e}")

        except AttributeError as e:
            print(f"✗ config.project.study_area failed: {e}")

        # Test accessing paths
        try:
            paths = config.paths
            print("✓ config.paths accessible")
            print(f"  Raw path: {paths.raw}")
            print(f"  Processed path: {paths.processed}")
        except AttributeError as e:
            print(f"✗ config.paths failed: {e}")

    else:
        print("'project' NOT found as top-level key")
        # Test direct access (checking if study_area is at top level)
        try:
            study_area = config.study_area
            print("✓ config.study_area accessible (direct access)")
        except AttributeError as e:
            print(f"✗ config.study_area failed: {e}")

if __name__ == "__main__":
    test_settings_config_structure()