"""
OpenStreetMap data fetcher supporting both Overpass API and local file sources.
Downloads or loads roads, buildings, POIs, power infrastructure, and parking for the study area.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
import requests
import geopandas as gpd
from shapely.geometry import shape, Point, LineString, Polygon
import pandas as pd
from loguru import logger

from .config_loader import get_config, get_data_sources_config
from .provenance import ProvenanceTracker, Provenance


class OSMLoader:
    """Fetches and processes OSM data for the study area."""

    def __init__(self, config: Optional[Any] = None):
        self.config = config or get_config("settings")
        self.sources_config = get_data_sources_config()
        self.osm_config = self.sources_config.data_sources.openstreetmap
        self.mode = getattr(self.osm_config, 'mode', 'api')  # Default to 'api' if not set
        self.bbox = self._get_bbox()
        self.output_dir = Path(self.config.paths.raw) / "osm"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_local_geojson(self, layer_name: str) -> gpd.GeoDataFrame:
        """Load OSM data from local GeoJSON file."""
        logger.info(f"Loading {layer_name} from local file...")
        file_path = self.output_dir / f"{layer_name}.geojson"

        if not file_path.exists():
            logger.warning(f"Local file not found: {file_path}")
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

        try:
            gdf = gpd.read_file(file_path)
            logger.info(f"Loaded {len(gdf)} {layer_name} features from {file_path}")
            return gdf
        except Exception as e:
            logger.error(f"Error loading {layer_name} from {file_path}: {e}")
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    def _get_bbox(self) -> List[float]:
        """Get bounding box as [min_lon, min_lat, max_lon, max_lat]."""
        bbox = self.config.project.study_area.bbox
        return [bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat]

    def _build_overpass_query(self, tags: Dict[str, Any], geometry_type: str) -> str:
        """Build Overpass QL query for given tags and geometry type."""
        # Convert tags to Overpass format
        tag_filters = []
        boolean_tags = []

        for key, values in tags.items():
            if values is True:
                boolean_tags.append(key)
            elif isinstance(values, list):
                vals = "|".join(str(v) for v in values)
                tag_filters.append(f'["{key}"~"{vals}"]')
            else:
                tag_filters.append(f'["{key}"="{values}"]')

        # Combine boolean tags into a single regex match
        if boolean_tags:
            boolean_pattern = "|".join(boolean_tags)
            tag_filters.append(f'["{boolean_pattern}"]')

        tag_str = "".join(tag_filters)

        # Geometry type filter
        if geometry_type == "node":
            geom_filter = "node"
        elif geometry_type == "way":
            geom_filter = "way"
        elif geometry_type == "relation":
            geom_filter = "relation"
        else:  # multiple types
            geom_filter = geometry_type.replace(",", "")

        bbox_str = f"{self.bbox[1]},{self.bbox[0]},{self.bbox[3]},{self.bbox[2]}"

        query = f"""
        [out:json][timeout:{self.osm_config.timeout_seconds}];
        (
          {geom_filter}{tag_str}({bbox_str});
        );
        out body;
        >;
        out skel qt;
        """
        return query.strip()

    def _execute_query(self, query: str) -> Dict[str, Any]:
        """Execute Overpass query with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                headers = {
                    "User-Agent": "VIT-AP-EV-Charging-Planner/1.0 (https://github.com/vitap-ev-charging-ml-fuzzy-ga)",
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                response = requests.post(
                    self.osm_config.endpoint,
                    data={"data": query},
                    headers=headers,
                    timeout=self.osm_config.timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f"Overpass query failed (attempt {attempt + 1}): {e}. Retrying...")
                time.sleep(2 ** attempt)
        return {}

    def _elements_to_geodataframe(self, elements: List[Dict], geometry_type: str) -> gpd.GeoDataFrame:
        """Convert OSM elements to GeoDataFrame.
        First pass: collect all node coordinates.
        Second pass: build geometries:
          - node: Point from (lon, lat)
          - way: LineString from looked-up node coordinates (skip if <2 valid nodes)
          - other: skip
        Preserves OSM IDs and tags.
        """
        # First pass: collect all node coordinates
        node_coords = {}
        for elem in elements:
            if elem["type"] == "node":
                node_coords[elem["id"]] = (elem["lon"], elem["lat"])

        records = []
        for elem in elements:
            # Process nodes only if explicitly requested in geometry_type
            if elem["type"] == "node" and "node" in geometry_type.split(","):
                geom = Point(elem["lon"], elem["lat"])
            # Process non-node elements only if their type is in geometry_type
            elif elem["type"] in geometry_type.split(","):
                if elem["type"] == "way":
                    node_ids = elem.get("nodes", [])
                    coords = []
                    # Check if all node IDs have coordinates (no missing nodes)
                    all_nodes_present = True
                    for nid in node_ids:
                        if nid not in node_coords:
                            all_nodes_present = False
                            break
                        coords.append(node_coords[nid])

                    tags = elem.get("tags", {})
                    is_building = tags.get("building") is not None

                    if is_building:
                        # Special handling for building-tagged ways: verify all nodes present
                        # and create polygon only for valid closed ways with sufficient coordinates

                        if all_nodes_present and len(coords) >= 2:
                            # Check if way is closed and has sufficient coordinates for a polygon
                            is_closed = len(node_ids) >= 2 and node_ids[0] == node_ids[-1]
                            has_min_coords = len(coords) >= 4  # Need ≥4 coords: [A,B,C,A] for triangle

                            if is_closed and has_min_coords:
                                # Validate that we can create a valid polygon
                                try:
                                    geom = Polygon(coords)
                                    if not geom.is_valid:
                                        # Invalid polygon - skip this building way entirely
                                        continue
                                    # Valid polygon - use it (fall through to record building)
                                except Exception:
                                    # Error creating polygon - skip this building way entirely
                                    continue
                            else:
                                # Not closed or insufficient coordinates - skip building way entirely
                                continue
                        else:
                            # Missing nodes or not enough coords - skip building way entirely
                            continue
                    else:
                        # Non-building way: preserve existing LineString behavior
                        if len(coords) >= 2:
                            geom = LineString(coords)
                        else:
                            continue  # skip way with fewer than 2 valid nodes
                else:
                    # For other element types (relation, etc.), skip geometry creation
                    # as they are not handled in this function
                    continue
            else:
                # Skip elements whose type is not in geometry_type
                continue

            record = {
                "osm_id": elem["id"],
                "osm_type": elem["type"],
                "geometry": geom,
            }
            # Add all tags
            for k, v in elem.get("tags", {}).items():
                record[k] = v
            records.append(record)

        if not records:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        return gdf

    def fetch_roads(self) -> gpd.GeoDataFrame:
        """Fetch road network."""
        if self.mode == "local":
            gdf = self._load_local_geojson("roads")
        else:
            logger.info("Fetching OSM roads...")
            geometry_type = self.osm_config.queries.roads._data['geometry_type']
            query = self._build_overpass_query(
                self.osm_config.queries.roads._data['tags'],
                geometry_type,
            )
            data = self._execute_query(query)
            gdf = self._elements_to_geodataframe(data.get("elements", []), geometry_type)
            # Filter to keep only the requested geometry types
            requested_types = [t.strip() for t in geometry_type.split(",")]
            gdf = gdf[gdf["osm_type"].isin(requested_types)].copy()

            if not gdf.empty:
                # Add derived fields
                gdf["length_m"] = gdf.geometry.length * 111000  # Approximate degrees to meters
                gdf["highway"] = gdf.get("highway", "unknown")

                # Save
                output_path = self.output_dir / "roads.geojson"
                gdf.to_file(output_path, driver="GeoJSON")
                logger.info(f"Saved {len(gdf)} roads to {output_path}")

                # Add provenance
                tracker = ProvenanceTracker("roads", "osm_loader.fetch_roads")
                tracker.add_columns_batch(
                    columns=["osm_id", "osm_type", "highway", "name", "length_m", "lanes", "maxspeed", "oneway", "surface"],
                    provenance=Provenance.REAL,
                    source="openstreetmap",
                    description="Road network from OSM via Overpass API",
                )
                tracker.add_column(
                    "geometry",
                    Provenance.REAL,
                    "openstreetmap",
                    "Road geometry (LineString)",
                )
                tracker.add_column(
                    "length_m",
                    Provenance.DERIVED,
                    "computed",
                    "Approximate length in meters",
                    transformation="geometry.length * 111000",
                )
                gdf = tracker.finalize(gdf, source_files=[str(output_path)])

        # Add provenance for local data (still REAL since it's the same source)
        if self.mode == "local" and not gdf.empty:
            tracker = ProvenanceTracker("roads", "osm_loader.fetch_roads")
            tracker.add_columns_batch(
                columns=["osm_id", "osm_type", "highway", "name", "geometry"],
                provenance=Provenance.REAL,
                source="openstreetmap",
                description="Road network from OSM (local file)",
            )
            # Re-add derived fields with DERIVED provenance
            if "length_m" not in gdf.columns:
                gdf["length_m"] = gdf.geometry.length * 111000
            if "highway" not in gdf.columns:
                gdf["highway"] = gdf.get("highway", "unknown")
            tracker.add_column(
                "length_m",
                Provenance.DERIVED,
                "computed",
                "Approximate length in meters",
                transformation="geometry.length * 111000",
            )
            gdf = tracker.finalize(gdf, source_files=[str(self.output_dir / "roads.geojson")])

        return gdf

    def fetch_buildings(self) -> gpd.GeoDataFrame:
        """Fetch building footprints."""
        if self.mode == "local":
            gdf = self._load_local_geojson("buildings")
        else:
            logger.info("Fetching OSM buildings...")
            geometry_type = self.osm_config.queries.buildings._data['geometry_type']
            query = self._build_overpass_query(
                self.osm_config.queries.buildings._data['tags'],
                geometry_type,
            )
            data = self._execute_query(query)
            gdf = self._elements_to_geodataframe(data.get("elements", []), geometry_type)
            # Filter to keep only the requested geometry types
            requested_types = [t.strip() for t in geometry_type.split(",")]
            gdf = gdf[gdf["osm_type"].isin(requested_types)].copy()

            if not gdf.empty:
                # Filter for polygons (buildings should be closed ways)
                gdf = gdf[gdf.geometry.type == "Polygon"].copy()

                # Add derived fields
                gdf["footprint_area_sqm"] = gdf.geometry.area * 111000 * 111000  # Approx
                gdf["building_type"] = gdf.get("building", "yes")

                output_path = self.output_dir / "buildings.geojson"
                gdf.to_file(output_path, driver="GeoJSON")
                logger.info(f"Saved {len(gdf)} buildings to {output_path}")

                tracker = ProvenanceTracker("buildings", "osm_loader.fetch_buildings")
                tracker.add_columns_batch(
                    columns=["osm_id", "osm_type", "building", "name", "building_type"],
                    provenance=Provenance.REAL,
                    source="openstreetmap",
                    description="Building footprints from OSM",
                )
                tracker.add_column(
                    "geometry",
                    Provenance.REAL,
                    "openstreetmap",
                    "Building footprint (Polygon)",
                )
                tracker.add_column(
                    "footprint_area_sqm",
                    Provenance.DERIVED,
                    "computed",
                    "Footprint area in square meters",
                    transformation="geometry.area * 111000^2",
                )
                gdf = tracker.finalize(gdf, source_files=[str(output_path)])

        # Add provenance for local data (still REAL since it's the same source)
        if self.mode == "local" and not gdf.empty:
            tracker = ProvenanceTracker("buildings", "osm_loader.fetch_buildings")
            tracker.add_columns_batch(
                columns=["osm_id", "osm_type", "building", "name", "geometry"],
                provenance=Provenance.REAL,
                source="openstreetmap",
                description="Building footprints from OSM (local file)",
            )
            # Re-add derived fields with DERIVED provenance
            if "footprint_area_sqm" not in gdf.columns:
                gdf["footprint_area_sqm"] = gdf.geometry.area * 111000 * 111000
            if "building_type" not in gdf.columns:
                gdf["building_type"] = gdf.get("building", "yes")
            tracker.add_column(
                "footprint_area_sqm",
                Provenance.DERIVED,
                "computed",
                "Footprint area in square meters",
                transformation="geometry.area * 111000^2",
            )
            gdf = tracker.finalize(gdf, source_files=[str(self.output_dir / "buildings.geojson")])

        return gdf

    def fetch_pois(self) -> gpd.GeoDataFrame:
        """Fetch Points of Interest."""
        if self.mode == "local":
            gdf = self._load_local_geojson("pois")
        else:
            logger.info("Fetching OSM POIs...")
            geometry_type = self.osm_config.queries.pois._data['geometry_type']
            query = self._build_overpass_query(
                self.osm_config.queries.pois._data['tags'],
                geometry_type,
            )
            data = self._execute_query(query)
            gdf = self._elements_to_geodataframe(data.get("elements", []), geometry_type)
            # Filter to keep only the requested geometry types
            requested_types = [t.strip() for t in geometry_type.split(",")]
            gdf = gdf[gdf["osm_type"].isin(requested_types)].copy()

            if not gdf.empty:
                # Determine primary category
                gdf["primary_category"] = gdf.apply(self._categorize_poi, axis=1)

                output_path = self.output_dir / "pois.geojson"
                gdf.to_file(output_path, driver="GeoJSON")
                logger.info(f"Saved {len(gdf)} POIs to {output_path}")

                tracker = ProvenanceTracker("pois", "osm_loader.fetch_pois")
                tracker.add_columns_batch(
                    columns=["osm_id", "osm_type", "amenity", "shop", "office", "leisure", "tourism", "public_transport", "name"],
                    provenance=Provenance.REAL,
                    source="openstreetmap",
                    description="POI tags from OSM",
                )
                tracker.add_column(
                    "geometry",
                    Provenance.REAL,
                    "openstreetmap",
                    "POI location (Point)",
                )
                tracker.add_column(
                    "primary_category",
                    Provenance.DERIVED,
                    "computed",
                    "Primary POI category for attractiveness scoring",
                    transformation="_categorize_poi function",
                )
                gdf = tracker.finalize(gdf, source_files=[str(output_path)])

        # Add provenance for local data (still REAL since it's the same source)
        if self.mode == "local" and not gdf.empty:
            tracker = ProvenanceTracker("pois", "osm_loader.fetch_pois")
            tracker.add_columns_batch(
                columns=["osm_id", "osm_type", "amenity", "shop", "office", "leisure", "tourism", "public_transport", "name"],
                provenance=Provenance.REAL,
                source="openstreetmap",
                description="POI tags from OSM (local file)",
            )
            # Re-add derived fields with DERIVED provenance
            if "primary_category" not in gdf.columns:
                gdf["primary_category"] = gdf.apply(self._categorize_poi, axis=1)
            tracker.add_column(
                "primary_category",
                Provenance.DERIVED,
                "computed",
                "Primary POI category for attractiveness scoring",
                transformation="_categorize_poi function",
            )
            gdf = tracker.finalize(gdf, source_files=[str(self.output_dir / "pois.geojson")])

        return gdf

    def _categorize_poi(self, row) -> str:
        """Categorize POI for attractiveness weighting."""
        # Priority order of tags
        for tag in ["amenity", "shop", "office", "leisure", "tourism", "public_transport"]:
            if tag in row and pd.notna(row[tag]):
                return f"{tag}={row[tag]}"
        return "other"

    def fetch_power(self) -> gpd.GeoDataFrame:
        """Fetch electrical infrastructure."""
        if self.mode == "local":
            gdf = self._load_local_geojson("power")
        else:
            logger.info("Fetching OSM power infrastructure...")
            geometry_type = self.osm_config.queries.power._data['geometry_type']
            query = self._build_overpass_query(
                self.osm_config.queries.power._data['tags'],
                geometry_type,
            )
            data = self._execute_query(query)
            gdf = self._elements_to_geodataframe(data.get("elements", []), geometry_type)
            # Filter to keep only the requested geometry types
            requested_types = [t.strip() for t in geometry_type.split(",")]
            gdf = gdf[gdf["osm_type"].isin(requested_types)].copy()

            if not gdf.empty:
                gdf["power_type"] = gdf.get("power", "unknown")
                gdf["voltage"] = gdf.get("voltage", "unknown")

                output_path = self.output_dir / "power.geojson"
                gdf.to_file(output_path, driver="GeoJSON")
                logger.info(f"Saved {len(gdf)} power elements to {output_path}")

                tracker = ProvenanceTracker("power", "osm_loader.fetch_power")
                tracker.add_columns_batch(
                    columns=["osm_id", "osm_type", "power", "voltage", "operator", "power_type"],
                    provenance=Provenance.REAL,
                    source="openstreetmap",
                    description="Electrical infrastructure from OSM",
                )
                tracker.add_column(
                    "geometry",
                    Provenance.REAL,
                    "openstreetmap",
                    "Power element geometry (Point or LineString)",
                )
                gdf = tracker.finalize(gdf, source_files=[str(output_path)])

        # Add provenance for local data (still REAL since it's the same source)
        if self.mode == "local" and not gdf.empty:
            tracker = ProvenanceTracker("power", "osm_loader.fetch_power")
            tracker.add_columns_batch(
                columns=["osm_id", "osm_type", "power", "voltage", "operator", "power_type"],
                provenance=Provenance.REAL,
                source="openstreetmap",
                description="Electrical infrastructure from OSM (local file)",
            )
            # Re-add derived fields with DERIVED provenance
            if "power_type" not in gdf.columns:
                gdf["power_type"] = gdf.get("power", "unknown")
            if "voltage" not in gdf.columns:
                gdf["voltage"] = gdf.get("voltage", "unknown")
            tracker.add_column(
                "geometry",
                Provenance.REAL,
                "openstreetmap",
                "Power element geometry (Point or LineString)",
            )
            gdf = tracker.finalize(gdf, source_files=[str(self.output_dir / "power.geojson")])

        return gdf

    def fetch_parking(self) -> gpd.GeoDataFrame:
        """Fetch parking areas."""
        if self.mode == "local":
            gdf = self._load_local_geojson("parking")
        else:
            logger.info("Fetching OSM parking areas...")
            geometry_type = self.osm_config.queries.parking._data['geometry_type']
            query = self._build_overpass_query(
                self.osm_config.queries.parking._data['tags'],
                geometry_type,
            )
            data = self._execute_query(query)
            gdf = self._elements_to_geodataframe(data.get("elements", []), geometry_type)
            # Filter to keep only the requested geometry types
            requested_types = [t.strip() for t in geometry_type.split(",")]
            gdf = gdf[gdf["osm_type"].isin(requested_types)].copy()

            if not gdf.empty:
                gdf = gdf[gdf.geometry.type == "Polygon"].copy()
                gdf["area_sqm"] = gdf.geometry.area * 111000 * 111000

                output_path = self.output_dir / "parking.geojson"
                gdf.to_file(output_path, driver="GeoJSON")
                logger.info(f"Saved {len(gdf)} parking areas to {output_path}")

                tracker = ProvenanceTracker("parking", "osm_loader.fetch_parking")
                tracker.add_columns_batch(
                    columns=["osm_id", "osm_type", "amenity", "capacity", "parking", "name", "area_sqm"],
                    provenance=Provenance.REAL,
                    source="openstreetmap",
                    description="Parking areas from OSM",
                )
                tracker.add_column(
                    "geometry",
                    Provenance.REAL,
                    "openstreetmap",
                    "Parking area (Polygon)",
                )
                tracker.add_column(
                    "area_sqm",
                    Provenance.DERIVED,
                    "computed",
                    "Parking area in square meters",
                )
                gdf = tracker.finalize(gdf, source_files=[str(output_path)])

        # Add provenance for local data (still REAL since it's the same source)
        if self.mode == "local" and not gdf.empty:
            tracker = ProvenanceTracker("parking", "osm_loader.fetch_parking")
            tracker.add_columns_batch(
                columns=["osm_id", "osm_type", "amenity", "capacity", "parking", "name", "area_sqm"],
                provenance=Provenance.REAL,
                source="openstreetmap",
                description="Parking areas from OSM (local file)",
            )
            # Re-add derived fields with DERIVED provenance
            if "area_sqm" not in gdf.columns:
                gdf["area_sqm"] = gdf.geometry.area * 111000 * 111000
            tracker.add_column(
                "area_sqm",
                Provenance.DERIVED,
                "computed",
                "Parking area in square meters",
            )
            gdf = tracker.finalize(gdf, source_files=[str(self.output_dir / "parking.geojson")])

        return gdf

    def fetch_all(self) -> Dict[str, gpd.GeoDataFrame]:
        """Fetch all OSM data layers."""
        results = {}
        results["roads"] = self.fetch_roads()
        results["buildings"] = self.fetch_buildings()
        results["pois"] = self.fetch_pois()
        results["power"] = self.fetch_power()
        results["parking"] = self.fetch_parking()
        return results


def fetch_osm_data(config: Optional[Any] = None) -> Dict[str, gpd.GeoDataFrame]:
    """Convenience function to fetch all OSM data."""
    loader = OSMLoader(config)
    return loader.fetch_all()