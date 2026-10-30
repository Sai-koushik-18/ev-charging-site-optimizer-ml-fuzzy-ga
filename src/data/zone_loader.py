"""
Campus zone polygon loader.
Loads manually digitized zone polygons (DERIVED provenance).
"""

from pathlib import Path
from typing import Optional, Any
import geopandas as gpd
import pandas as pd
from loguru import logger

from .config_loader import get_config, get_data_sources_config
from .provenance import ProvenanceTracker, Provenance


class ZoneLoader:
    """Loads campus zone polygons from GeoJSON file."""

    def __init__(self, config: Optional[Any] = None):
        self.config = config or get_config("settings")
        self.sources_config = get_data_sources_config()
        self.zone_config = self.sources_config.data_sources.campus_zones
        self.input_file = Path(self.zone_config.input_file)

    def load(self) -> gpd.GeoDataFrame:
        """Load zone polygons from GeoJSON."""
        if not self.input_file.exists():
            raise FileNotFoundError(f"Zone polygons file not found: {self.input_file}")

        logger.info(f"Loading campus zones from {self.input_file}")
        gdf = gpd.read_file(self.input_file)

        # Validate required columns
        required = self.zone_config.required_properties
        missing = [c for c in required if c not in gdf.columns]
        if missing:
            raise ValueError(f"Zone polygons missing required columns: {missing}")

        # Ensure CRS
        if gdf.crs is None:
            gdf.set_crs("EPSG:4326", inplace=True)
        elif gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        # Add provenance
        tracker = ProvenanceTracker("zone_polygons", "zone_loader.load")
        tracker.add_columns_batch(
            columns=["zone_id", "zone_name", "land_cost_tier", "primary_use", "description"],
            provenance=Provenance.DERIVED,
            source="manual_digitization",
            description="Zone boundaries manually digitized from satellite imagery and public maps",
            transformation="Manual digitization in QGIS/GEE from satellite imagery",
            parameters={"note": "NOT from official VIT-AP master plan unless verified"},
        )
        tracker.add_columns_batch(
            columns=["provenance", "source", "note"],
            provenance=Provenance.META,
            source="provenance_tracker",
            description="Provenance metadata embedded in source GeoJSON",
        )
        tracker.add_column(
            "geometry",
            Provenance.DERIVED,
            "manual_digitization",
            "Zone boundary polygon",
            transformation="Manual polygon digitization",
        )
        gdf = tracker.finalize(gdf, source_files=[str(self.input_file)])

        logger.info(f"Loaded {len(gdf)} zones: {list(gdf['zone_id'])}")
        return gdf


def load_zone_polygons(config: Optional[Any] = None) -> gpd.GeoDataFrame:
    """Convenience function to load zone polygons."""
    loader = ZoneLoader(config)
    return loader.load()