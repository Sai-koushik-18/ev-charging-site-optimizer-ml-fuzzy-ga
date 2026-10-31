"""
OpenChargeMap API client for fetching existing EV charging stations.
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from loguru import logger

from .config_loader import get_config, get_data_sources_config, require_env
from .provenance import ProvenanceTracker, Provenance


class OCMDataLoader:
    """Fetches EV charging station data from OpenChargeMap."""

    def __init__(self, config: Optional[Any] = None, api_key: Optional[str] = None):
        self.config = config or get_config("settings")
        self.sources_config = get_data_sources_config()
        self.ocm_config = self.sources_config.data_sources.openchargemap
        self.api_key = api_key or self._get_api_key()
        self.output_dir = Path(self.config.paths.raw) / "openchargemap"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_api_key(self) -> str:
        """Get API key from environment or config."""
        # Try environment variable first
        env_key = self.ocm_config.api_key_env
        if env_key:
            key = os.environ.get(env_key)
            if key:
                return key
        # Fallback to config (not recommended for production)
        return getattr(self.ocm_config, "api_key", "") or ""

    def _build_params(self) -> Dict[str, Any]:
        """Build API request parameters (no distance = every station in the country)."""
        params = {
            "output": self.ocm_config.params.output,
            "countrycode": self.ocm_config.params.countrycode,
            "maxresults": self.ocm_config.params.maxresults,
            "compact": str(self.ocm_config.params.compact).lower(),
            "verbose": str(self.ocm_config.params.verbose).lower(),
        }
        if self.search_radius_km is not None:
            params.update({
                "latitude": self.ocm_config.params.latitude,
                "longitude": self.ocm_config.params.longitude,
                "distance": self.search_radius_km,
                "distanceunit": "KM",
            })
        return params

    @property
    def search_radius_km(self) -> Optional[float]:
        distance = getattr(self.ocm_config.params, "distance", None)
        return None if distance is None else float(distance)

    def _scope(self) -> str:
        radius = self.search_radius_km
        return f"within {radius:g} km" if radius is not None else f"across country {self.ocm_config.params.countrycode}"

    def _build_headers(self) -> Dict[str, str]:
        """Send the key as a header so it never appears in URLs, errors or logs."""
        headers = {"User-Agent": "vitap-ev-charging-planner/1.0"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def fetch_stations(self) -> gpd.GeoDataFrame:
        """Fetch charging stations from OpenChargeMap."""
        if not self.api_key:
            logger.warning("No OpenChargeMap API key provided. Using demo mode (limited results).")
            logger.warning("Set OCM_API_KEY environment variable for full access.")

        logger.info("Fetching EV charging stations from OpenChargeMap...")
        url = self.ocm_config.base_url
        params = self._build_params()

        try:
            response = requests.get(url, params=params, headers=self._build_headers(), timeout=60)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            logger.error(f"OpenChargeMap API request failed ({type(e).__name__}, HTTP {status})")
            data = None

        if data is None:
            logger.warning("No stations returned from OpenChargeMap API")
            # Try to load from cache
            cache_geojson = self.output_dir / "stations.geojson"
            cache_csv = self.output_dir / "stations.csv"
            if cache_geojson.exists():
                logger.info(f"Loading cached OpenChargeMap data from {cache_geojson}")
                try:
                    gdf = gpd.read_file(cache_geojson)
                    if not gdf.empty:
                        logger.info(f"Loaded {len(gdf)} stations from cache")
                        return gdf
                except Exception as cache_error:
                    logger.error(f"Failed to load cached GeoJSON: {cache_error}")
            if cache_csv.exists():
                logger.info(f"Loading cached OpenChargeMap data from {cache_csv}")
                try:
                    df = pd.read_csv(cache_csv)
                    if not df.empty:
                        # Convert to GeoDataFrame
                        geometry = [Point(lon, lat) for lon, lat in zip(df["longitude"], df["latitude"])]
                        gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
                        logger.info(f"Loaded {len(gdf)} stations from cache")
                        return gdf
                except Exception as cache_error:
                    logger.error(f"Failed to load cached CSV: {cache_error}")
            logger.warning("No cache available for OpenChargeMap data")
            # Return empty GeoDataFrame with expected CRS
            return gpd.GeoDataFrame(columns=[], geometry=[], crs="EPSG:4326")

        logger.info(f"Received {len(data)} stations from OpenChargeMap {self._scope()}")
        return self._parse_stations(data)

    def _parse_stations(self, data: List[Dict]) -> pd.DataFrame:
        """Parse raw API response into DataFrame."""
        records = []
        for station in data:
            try:
                addr = station.get("AddressInfo") or {}
                connections = station.get("Connections", [])

                # Calculate total power and connector info
                total_power = 0
                connector_types = set()
                for conn in connections:
                    power = conn.get("PowerKW") or 0
                    total_power += power
                    conn_type = (conn.get("ConnectionType") or {}).get("Title", "")
                    if conn_type:
                        connector_types.add(conn_type)

                record = {
                    "station_id": str(station.get("ID", "")),
                    "uuid": station.get("UUID", ""),
                    "name": addr.get("Title", "Unknown"),
                    "address": addr.get("AddressLine1", ""),
                    "town": addr.get("Town", ""),
                    "state": addr.get("StateOrProvince", ""),
                    "country": (addr.get("Country") or {}).get("Title", ""),
                    "latitude": addr.get("Latitude"),
                    "longitude": addr.get("Longitude"),
                    "operator": (station.get("OperatorInfo") or {}).get("Title", ""),
                    "operator_id": (station.get("OperatorInfo") or {}).get("ID", ""),
                    "usage_type": (station.get("UsageType") or {}).get("Title", ""),
                    "status": (station.get("StatusType") or {}).get("Title", ""),
                    "date_last_confirmed": station.get("DateLastConfirmed", ""),
                    "num_connectors": station.get("NumberOfPoints", len(connections)),
                    "total_power_kw": total_power,
                    "max_power_kw": max([c.get("PowerKW", 0) for c in connections], default=0),
                    "connector_types": json.dumps(list(connector_types)),
                    "access_type": (station.get("UsageType") or {}).get("Title", "").lower(),
                    # IsOperational is None when OpenChargeMap has no status for the station
                    "is_operational": (station.get("StatusType") or {}).get("IsOperational"),
                    "source": "openchargemap",
                }
                records.append(record)
            except Exception as e:
                logger.warning(f"Failed to parse station: {e}")
                continue

        df = pd.DataFrame(records)

        if df.empty:
            empty = gpd.GeoDataFrame(columns=["station_id", "name", "distance_to_campus_km"],
                                     geometry=[], crs="EPSG:4326")
            empty.to_file(self.output_dir / "stations.geojson", driver="GeoJSON")
            return empty

        # Drop stations OpenChargeMap marks as not operational; keep those with unknown status
        df = df[df["is_operational"] != False].copy()  # noqa: E712

        # Calculate distance to campus center
        campus_lat = self.config.project.study_area.campus_center.latitude
        campus_lon = self.config.project.study_area.campus_center.longitude
        df["distance_to_campus_km"] = df.apply(
            lambda row: self._haversine(row["latitude"], row["longitude"], campus_lat, campus_lon),
            axis=1
        )

        # Keep stations within the configured search radius, if one is set
        if self.search_radius_km is not None:
            df = df[df["distance_to_campus_km"] <= self.search_radius_km].copy()

        # Create geometry
        geometry = [Point(lon, lat) for lon, lat in zip(df["longitude"], df["latitude"])]
        gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

        # Save raw JSON
        output_json = self.output_dir / "stations.json"
        with open(output_json, "w") as f:
            json.dump(data, f, indent=2)

        # Save processed GeoJSON
        output_geojson = self.output_dir / "stations.geojson"
        gdf.to_file(output_geojson, driver="GeoJSON")

        # Save CSV
        output_csv = self.output_dir / "stations.csv"
        df.to_csv(output_csv, index=False)

        logger.info(f"Saved {len(gdf)} stations {self._scope()} to {output_geojson}")

        # Add provenance
        tracker = ProvenanceTracker("existing_stations", "ocm_loader.fetch_stations")
        tracker.add_columns_batch(
            columns=["station_id", "uuid", "name", "address", "town", "state", "country",
                     "latitude", "longitude", "operator", "usage_type", "status",
                     "date_last_confirmed", "num_connectors", "total_power_kw",
                     "max_power_kw", "connector_types", "access_type", "is_operational"],
            provenance=Provenance.REAL,
            source="openchargemap",
            description="EV charging station data from OpenChargeMap API",
        )
        tracker.add_columns_batch(
            columns=["source", "distance_to_campus_km"],
            provenance=Provenance.DERIVED,
            source="computed",
            description="Derived fields",
            transformation="haversine distance to campus center",
        )
        tracker.add_column(
            "geometry",
            Provenance.REAL,
            "openchargemap",
            "Station location (Point)",
        )
        gdf = tracker.finalize(gdf, source_files=[str(output_json), str(output_geojson)])

        return gdf

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate haversine distance in km."""
        from math import radians, sin, cos, sqrt, atan2
        R = 6371.0
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        return R * c


def fetch_ocm_data(config: Optional[Any] = None, api_key: Optional[str] = None) -> gpd.GeoDataFrame:
    """Convenience function to fetch OCM data."""
    loader = OCMDataLoader(config, api_key)
    return loader.fetch_stations()