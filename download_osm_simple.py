#!/usr/bin/env python3
"""
Simple script to download OSM data locally for VIT-AP study area.
Downloads roads, buildings, POIs, power infrastructure, and parking areas
using Overpass API and saves them as GeoJSON files without geopandas dependency.
"""

import json
import time
from pathlib import Path
import requests

def download_osm_layer(query, layer_name, output_path):
    """Download a specific OSM layer using Overpass API and save as GeoJSON."""
    print(f"Downloading {layer_name}...")

    headers = {
        "User-Agent": "VIT-AP-EV-Charging-Planner/1.0 (https://github.com/vitap-ev-charging-ml-fuzzy-ga)",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        response = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            headers=headers,
            timeout=300
        )
        response.raise_for_status()
        data = response.json()

        # Convert OSM JSON to GeoJSON FeatureCollection
        geojson = {
            "type": "FeatureCollection",
            "name": layer_name,
            "crs": { "type": "name", "properties": { "name": "urn:ogc:def:crs:OGC:1.3:CRS84" } },
            "features": []
        }

        for element in data.get("elements", []):
            feature = {
                "type": "Feature",
                "properties": {
                    "osm_id": element["id"],
                    "osm_type": element["type"]
                },
                "geometry": None
            }

            # Add tags to properties
            if "tags" in element:
                for key, value in element["tags"].items():
                    feature["properties"][key] = value

            # Handle geometry
            if element["type"] == "node":
                feature["geometry"] = {
                    "type": "Point",
                    "coordinates": [element["lon"], element["lat"]]
                }
            elif element["type"] == "way" and "geometry" in element:
                coordinates = [[node["lon"], node["lat"]] for node in element["geometry"]]
                # Determine if it's a LineString or Polygon
                if len(coordinates) >= 2:
                    # Check if it's a closed ring (polygon)
                    if coordinates[0] == coordinates[-1] and len(coordinates) >= 4:
                        feature["geometry"] = {
                            "type": "Polygon",
                            "coordinates": [coordinates]
                        }
                    else:
                        feature["geometry"] = {
                            "type": "LineString",
                            "coordinates": coordinates
                        }
            elif element["type"] == "relation":
                # Skip relations for simplicity
                continue

            if feature["geometry"] is not None:
                geojson["features"].append(feature)

        # Save to file
        with open(output_path, 'w') as f:
            json.dump(geojson, f, indent=2)

        print(f"  ✓ Saved {len(geojson['features'])} {layer_name} features to {output_path}")
        return True

    except Exception as e:
        print(f"  ✗ Error downloading {layer_name}: {e}")
        # Create empty GeoJSON file
        empty_geojson = {
            "type": "FeatureCollection",
            "name": layer_name,
            "crs": { "type": "name", "properties": { "name": "urn:ogc:def:crs:OGC:1.3:CRS84" } },
            "features": []
        }
        with open(output_path, 'w') as f:
            json.dump(empty_geojson, f, indent=2)
        return False

def main():
    # Bounding box for VIT-AP study area: [min_lon, min_lat, max_lon, max_lat]
    bbox = [80.455, 16.485, 80.505, 16.530]
    bbox_str = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"

    # Define queries for each layer
    queries = {
        "roads": f"""
        [out:json][timeout:300];
        (
          way["highway"~"motorway|trunk|primary|secondary|tertiary|residential|service|unclassified|path"]({bbox_str});
        );
        out body;
        >;
        out skel qt;
        """,

        "buildings": f"""
        [out:json][timeout:300];
        (
          way["building"]({bbox_str});
        );
        out body;
        >;
        out skel qt;
        """,

        "pois": f"""
        [out:json][timeout:300];
        (
          node["amenity"]({bbox_str});
          way["amenity"]({bbox_str});
          node["shop"]({bbox_str});
          way["shop"]({bbox_str});
          node["office"]({bbox_str});
          way["office"]({bbox_str});
          node["leisure"]({bbox_str});
          way["leisure"]({bbox_str});
          node["tourism"]({bbox_str});
          way["tourism"]({bbox_str});
          node["public_transport"]({bbox_str});
          way["public_transport"]({bbox_str});
        );
        out body;
        >;
        out skel qt;
        """,

        "power": f"""
        [out:json][timeout:300];
        (
          node["power"~"substation|transformer|pole|line|cable|plant"]({bbox_str});
          way["power"~"substation|transformer|pole|line|cable|plant"]({bbox_str});
          node["power"="substation"]({bbox_str});
          way["power"="substation"]({bbox_str});
        );
        out body;
        >;
        out skel qt;
        """,

        "parking": f"""
        [out:json][timeout:300];
        (
          way["amenity"="parking"]({bbox_str});
          relation["amenity"="parking"]({bbox_str});
        );
        out body;
        >;
        out skel qt;
        """
    }

    # Define output paths
    base_path = Path("/mnt/d/claude/vitap-ev-charging-ml-fuzzy-ga/data/raw/osm")
    base_path.mkdir(parents=True, exist_ok=True)

    output_paths = {
        "roads": base_path / "roads.geojson",
        "buildings": base_path / "buildings.geojson",
        "pois": base_path / "pois.geojson",
        "power": base_path / "power.geojson",
        "parking": base_path / "parking.geojson"
    }

    # Download each layer
    success_count = 0
    for layer_name, query in queries.items():
        if download_osm_layer(query, layer_name, output_paths[layer_name]):
            success_count += 1
        time.sleep(3)  # Be nice to the API

    print(f"\nDownload complete: {success_count}/{len(queries)} layers successful")

    if success_count == len(queries):
        print("All OSM data downloaded successfully!")
        return True
    else:
        print("Some layers failed to download.")
        return False

if __name__ == "__main__":
    main()