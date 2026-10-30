#!/usr/bin/env python3
"""
Test script to check alternative Overpass API endpoints.
"""

import requests
import time

def test_endpoint(url, name):
    """Test an Overpass API endpoint with a simple query."""
    print(f"Testing {name}: {url}")

    # Simple query to count nodes in bounding box (provisional VIT-AP campus bbox)
    query = """
    [out:json][timeout:25];
    (
      node(16.4878,80.4935,16.5011,80.5055);
    );
    out count;
    """

    try:
        response = requests.post(url, data={"data": query}, timeout=30)
        if response.status_code == 200:
            result = response.json()
            count = result['elements'][0]['tags'] if result['elements'] else 'unknown'
            print(f"  ✓ Success! Response: {response.status_code}")
            return True
        else:
            print(f"  ✗ Failed! Status: {response.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False

if __name__ == "__main__":
    endpoints = [
        ("https://zillion.overpass-api.de/api/interpreter", "Zillion"),
        ("https://overpass.kb.info/api/interpreter", "KB.info"),
        ("https://maps.mail.ru/osm/tools/overpass/api/interpreter", "Mail.ru"),
        ("https://overpass-api.de/api/interpreter", "Main (default)")
    ]

    print("Testing Overpass API endpoints...")
    print("=" * 50)

    working_endpoints = []
    for url, name in endpoints:
        if test_endpoint(url, name):
            working_endpoints.append((url, name))
        print()
        time.sleep(1)  # Be gentle between tests

    print("=" * 50)
    print(f"Working endpoints: {len(working_endpoints)}/{len(endpoints)}")
    for url, name in working_endpoints:
        print(f"  ✓ {name}: {url}")