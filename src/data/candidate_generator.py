"""
Candidate location generator.
Creates candidate charging station locations from OSM parking areas and zone polygons.
"""

import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path
from typing import Optional, Any, List, Dict, Tuple
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points, transform
import pyproj
from loguru import logger

from .config_loader import get_config
from .provenance import ProvenanceTracker, Provenance
from .osm_loader import OSMLoader
from .zone_loader import load_zone_polygons


class CandidateGenerator:
    """
    Generates candidate charging station locations by combining:
    - OSM parking areas (REAL)
    - Campus zone polygons (DERIVED)
    - Grid infrastructure distances (DERIVED)
    - POI attractiveness scores (DERIVED)
    - Cost assumptions (SYNTHETIC)
    """

    # UTM Zone 44N for Andhra Pradesh, India
    UTM_CRS = "EPSG:32644"
    WGS84_CRS = "EPSG:4326"

    def __init__(self, config: Optional[Any] = None):
        self.config = config or get_config("settings")
        self.candidates_config = self.config.candidates
        self.cost_config = self.config.costs
        self.osm_loader = OSMLoader(config)
        self.output_dir = Path(self.config.paths.processed)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create transformer for WGS84 -> UTM
        self._wgs84_to_utm = pyproj.Transformer.from_crs(
            self.WGS84_CRS, self.UTM_CRS, always_xy=True
        ).transform
        self._utm_to_wgs84 = pyproj.Transformer.from_crs(
            self.UTM_CRS, self.WGS84_CRS, always_xy=True
        ).transform

    def _to_utm(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Convert GeoDataFrame to UTM CRS for metric calculations."""
        if gdf.crs is None:
            gdf = gdf.set_crs(self.WGS84_CRS)
        if gdf.crs.to_epsg() != 32644:
            return gpd.GeoDataFrame(gdf, geometry=gdf.geometry, crs=gdf.crs).to_crs(self.UTM_CRS)
        return gdf

    def _to_wgs84(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Convert GeoDataFrame back to WGS84 CRS."""
        if gdf.crs is None:
            gdf = gdf.set_crs(self.UTM_CRS)
        if gdf.crs.to_epsg() != 4326:
            return gpd.GeoDataFrame(gdf, geometry=gdf.geometry, crs=gdf.crs).to_crs(self.WGS84_CRS)
        return gdf

    def generate(
        self,
        parking: gpd.GeoDataFrame,
        zones: gpd.GeoDataFrame,
        pois: gpd.GeoDataFrame,
        power: gpd.GeoDataFrame,
        existing_stations: gpd.GeoDataFrame,
        roads: gpd.GeoDataFrame,
        buildings: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """
        Generate candidate locations from parking areas intersected with zones.
        """
        logger.info("Generating candidate locations...")

        # 1. Filter parking to study area and minimum size
        candidates, prov_info = self._prepare_parking_candidates(parking)
        all_prov = {}
        all_prov.update(prov_info)

        # 2. Assign zones via spatial join (no nearest-neighbor fallback)
        candidates = self._assign_zones(candidates, zones)
        # Determine provenance for zone attributes based on zone data provenance
        zone_provenance = zones.get("provenance", pd.Series(["UNKNOWN"])).iloc[0] if not zones.empty else "UNKNOWN"
        is_provisional = zone_provenance == "PROVISIONAL" or zone_provenance == "DERIVED"  # Provisional zones marked DERIVED in file
        # After assigning zones, we add provenance for zone, land_cost_tier, primary_use
        all_prov["zone"] = (Provenance.DERIVED, "zone_loader", "Assigned zone via spatial join (within)")
        if is_provisional:
            all_prov["land_cost_tier"] = (Provenance.SYNTHETIC, "assumption", "PROVISIONAL — placeholder land cost tier for pipeline compatibility; not verified")
            all_prov["primary_use"] = (Provenance.SYNTHETIC, "assumption", "PROVISIONAL — placeholder primary use for pipeline compatibility; not verified")
        else:
            all_prov["land_cost_tier"] = (Provenance.DERIVED, "zone_loader", "Land cost tier from zone")
            all_prov["primary_use"] = (Provenance.DERIVED, "zone_loader", "Primary land use from zone")

        # 3. Calculate max chargers from area
        candidates = self._calculate_max_chargers(candidates)
        all_prov["max_chargers"] = (Provenance.DERIVED, "candidate_generator", "Maximum number of chargers based on area and config")

        # 4. Calculate grid connection distance and cost (metric, in UTM)
        candidates = self._calculate_grid_connection(candidates, power)
        # Determine provenance for grid_dist_m and nearest_transformer_id
        if power.empty or "power_type" not in power.columns:
            all_prov["grid_dist_m"] = (Provenance.SYNTHETIC, "assumption", "UNKNOWN - no transformer/substation data available")
            all_prov["nearest_transformer_id"] = (Provenance.SYNTHETIC, "assumption", "UNKNOWN - requires electrical survey")
        else:
            transformers = power[power["power_type"].isin(["transformer", "substation"])]
            if transformers.empty:
                all_prov["grid_dist_m"] = (Provenance.SYNTHETIC, "assumption", "UNKNOWN - no transformers/substations in power layer")
                all_prov["nearest_transformer_id"] = (Provenance.SYNTHETIC, "assumption", "UNKNOWN - requires electrical survey")
            else:
                all_prov["grid_dist_m"] = (Provenance.DERIVED, "osm_loader", "Distance to nearest transformer/substation (metric, UTM)")
                all_prov["nearest_transformer_id"] = (Provenance.DERIVED, "osm_loader", "OSM ID of nearest transformer/substation")

        # 5. Calculate POI attractiveness score (metric, in UTM)
        candidates = self._calculate_poi_score(candidates, pois)
        all_prov["poi_score"] = (Provenance.DERIVED, "candidate_generator", "POI attractiveness score within 1km radius (metric, UTM)")

        # 6. Calculate catchment population (metric, in UTM)
        candidates = self._calculate_catchment_population(candidates, buildings)
        if buildings.empty or "population_estimate" not in buildings.columns:
            all_prov["catchment_population"] = (Provenance.SYNTHETIC, "assumption", "Default catchment population of 0 - no building data")
        else:
            all_prov["catchment_population"] = (Provenance.DERIVED, "osm_loader", "Population within 1km catchment (metric, UTM)")

        # 7. Calculate competition (existing stations nearby) (metric, in UTM)
        candidates = self._calculate_competition(candidates, existing_stations)
        if existing_stations.empty:
            all_prov["existing_chargers_500m"] = (Provenance.SYNTHETIC, "assumption", "Default count of 0")
            all_prov["existing_chargers_1km"] = (Provenance.SYNTHETIC, "assumption", "Default count of 0")
            all_prov["existing_chargers_3km"] = (Provenance.SYNTHETIC, "assumption", "Default count of 0")
        else:
            all_prov["existing_chargers_500m"] = (Provenance.DERIVED, "osm_loader", "Count of existing charging stations within 500m (metric, UTM)")
            all_prov["existing_chargers_1km"] = (Provenance.DERIVED, "osm_loader", "Count of existing charging stations within 1km (metric, UTM)")
            all_prov["existing_chargers_3km"] = (Provenance.DERIVED, "osm_loader", "Count of existing charging stations within 3km (metric, UTM)")

        # 8. Calculate traffic accessibility (metric, in UTM)
        candidates = self._calculate_traffic_accessibility(candidates, roads)
        if roads.empty:
            all_prov["traffic_accessibility_score"] = (Provenance.SYNTHETIC, "assumption", "Default traffic accessibility score based on Euclidean distance to gates (metric, UTM)")
        else:
            all_prov["traffic_accessibility_score"] = (Provenance.DERIVED, "candidate_generator", "Traffic accessibility score based on distance to gates (metric, UTM)")

        # 9. Calculate costs (SYNTHETIC)
        candidates = self._calculate_costs(candidates)
        if is_provisional:
            all_prov["land_cost_inr_per_sqm"] = (Provenance.SYNTHETIC, "assumption", "PROVISIONAL — land cost per sqm derived from provisional tier; not verified")
            all_prov["land_cost_inr_lakhs"] = (Provenance.SYNTHETIC, "assumption", "PROVISIONAL — land cost in lakhs derived from provisional tier; not verified")
        else:
            all_prov["land_cost_inr_per_sqm"] = (Provenance.DERIVED, "cost_config", "Land cost per square meter from tier")
            all_prov["land_cost_inr_lakhs"] = (Provenance.DERIVED, "candidate_generator", "Land cost in lakhs")
        # grid_connection_cost and total_capex are UNKNOWN when grid_dist_m is unknown
        if power.empty or "power_type" not in power.columns:
            all_prov["grid_connection_cost_inr_lakhs"] = (Provenance.SYNTHETIC, "assumption", "UNKNOWN - no transformer/substation data available")
            all_prov["total_capex_inr_lakhs"] = (Provenance.SYNTHETIC, "assumption", "UNKNOWN - grid connection cost unknown")
        else:
            transformers = power[power["power_type"].isin(["transformer", "substation"])]
            if transformers.empty:
                all_prov["grid_connection_cost_inr_lakhs"] = (Provenance.SYNTHETIC, "assumption", "UNKNOWN - no transformers/substations in power layer")
                all_prov["total_capex_inr_lakhs"] = (Provenance.SYNTHETIC, "assumption", "UNKNOWN - grid connection cost unknown")
            else:
                all_prov["grid_connection_cost_inr_lakhs"] = (Provenance.DERIVED, "cost_config", "Grid connection cost in lakhs (metric, UTM)")
                all_prov["total_capex_inr_lakhs"] = (Provenance.DERIVED, "candidate_generator", "Total CAPEX in lakhs")
        all_prov["construction_cost_inr_lakhs"] = (Provenance.DERIVED, "cost_config", "Construction cost in lakhs")

        # 10. Select top N candidates (only those with assigned zones)
        candidates = self._select_top_candidates(candidates)
        all_prov["selection_score"] = (Provenance.DERIVED, "candidate_generator", "Composite score for candidate selection")

        # 11. Assign candidate IDs
        candidates = self._assign_candidate_ids(candidates)
        all_prov["candidate_id"] = (Provenance.SYNTHETIC, "candidate_generator", "Sequential candidate ID")
        all_prov["name"] = (Provenance.SYNTHETIC, "candidate_generator", "Generated name from zone and candidate ID")

        # Add latitude/longitude from geometry (DERIVED from REAL/SYNTHETIC geometry)
        candidates["latitude"] = candidates.geometry.y
        candidates["longitude"] = candidates.geometry.x
        all_prov["latitude"] = (Provenance.DERIVED, "candidate_generator", "Latitude extracted from geometry")
        all_prov["longitude"] = (Provenance.DERIVED, "candidate_generator", "Longitude extracted from geometry")

        # Add placeholder for predicted demand (will be filled by ML model later)
        candidates["predicted_annual_demand_kwh"] = 0.0
        all_prov["predicted_annual_demand_kwh"] = (Provenance.SYNTHETIC, "candidate_generator", "Placeholder for ML predicted annual demand in kWh")

        # Add provenance tracking
        tracker = ProvenanceTracker("candidates", "candidate_generator.generate")
        for col, (prov, source, description) in all_prov.items():
            if col in candidates.columns:
                tracker.add_column(col, prov, source, description)
        # Also add provenance for geometry column if it's not in all_prov
        if "geometry" not in all_prov:
            if "geometry" in candidates.columns:
                tracker.add_column("geometry", Provenance.REAL, "openstreetmap", "Geometry from OSM (point or centroid)")
        gdf_with_prov = tracker.finalize(candidates, source_files=[str(self.output_dir / "candidates.csv"), str(self.output_dir / "candidates.geojson")])

        # Save provenance-tracked data
        output_file = self.output_dir / "candidates.csv"
        gdf_with_prov.drop(columns="geometry").to_csv(output_file, index=False)
        output_geojson = self.output_dir / "candidates.geojson"
        gdf_with_prov.to_file(output_geojson, driver="GeoJSON")
        logger.info(f"Saved {len(gdf_with_prov)} candidates to {output_file}")

        return gdf_with_prov

    def _prepare_parking_candidates(self, parking: gpd.GeoDataFrame) -> Tuple[gpd.GeoDataFrame, Dict[str, Tuple[Provenance, str, str]]]:
        """Filter and prepare parking areas as candidate bases.
        Returns (prepared_parking, provenance_dict) where provenance_dict maps column names to
        (provenance, source, description) for columns that were added or modified with non-REAL provenance.
        """
        provenance_dict = {}
        if parking.empty:
            logger.warning("No parking data available, creating synthetic candidates from zones")
            synthetic_gdf, synth_prov = self._create_synthetic_candidates_from_zones(allow_synthetic_fallback=False)
            provenance_dict.update(synth_prov)
            return synthetic_gdf, provenance_dict

        # Check if we have real area data (from polygon features)
        has_area_data = "area_sqm" in parking.columns

        # Filter by area only if we have real area measurements with valid values
        if has_area_data:
            # Check if we have meaningful area data (not all zeros or invalid)
            valid_area_mask = (parking["area_sqm"] > 0) & (parking["area_sqm"] < float('inf'))
            if valid_area_mask.any():
                min_area = self.candidates_config.min_area_sqm
                max_area = self.candidates_config.max_area_sqm
                parking = parking[
                    (parking["area_sqm"] >= min_area) &
                    (parking["area_sqm"] <= max_area)
                ].copy()
                logger.info(f"Filtered parking by area: {len(parking)} features after filtering")
            else:
                # All area values are invalid (zero or negative), treat as no real area data
                logger.warning("Parking area_sqm column contains only invalid values. Treating as point data without area information.")
                has_area_data = False  # Reset to trigger the else branch logic

        if not has_area_data:
            # For point features without area data, we cannot apply area filters
            # Preserve all points but note that area-dependent calculations will use synthetic assumptions
            logger.warning("Parking data lacks valid area_sqm column (Point features or invalid values). Skipping area-based filtering.")

        # Track if we add synthetic area_sqm
        synth_area_added = False
        # Track if we set parking_type to default
        parking_type_default = False
        # Track if we convert geometry to centroid
        geometry_to_centroid = False

        # Ensure point geometry for candidate location
        # If polygons with area data, use centroid; if already points, keep as-is
        if has_area_data and len(parking) > 0 and parking.geometry.iloc[0].geom_type != "Point":
            parking["geometry"] = parking.geometry.centroid
            geometry_to_centroid = True
        # If already points or empty DataFrame, keep as-is

        # Add parking type - use existing if available, otherwise default
        if "parking" in parking.columns:
            parking["parking_type"] = parking["parking"]
        else:
            parking["parking_type"] = "surface"
            parking_type_default = True

        # Add synthetic area_sqm column if missing, clearly marked as assumption
        if not has_area_data:
            # Use median of allowed range as synthetic assumption for calculations
            synthetic_area = (self.candidates_config.min_area_sqm + self.candidates_config.max_area_sqm) / 2
            parking["area_sqm"] = synthetic_area
            synth_area_added = True
            logger.info(f"Added synthetic area_sqm = {synthetic_area} sqm for {len(parking)} point features")

        logger.info(f"Prepared {len(parking)} parking areas as candidate bases")

        # Build provenance dict for modified columns
        if synth_area_added:
            provenance_dict["area_sqm"] = (Provenance.SYNTHETIC, "assumption", "Synthetic area assumption for point features")
        if parking_type_default:
            provenance_dict["parking_type"] = (Provenance.SYNTHETIC, "assumption", "Default parking type for missing OSM parking tag")
        if geometry_to_centroid:
            provenance_dict["geometry"] = (Provenance.DERIVED, "candidate_generator", "Centroid of polygon geometry")

        return parking, provenance_dict

    def _create_synthetic_candidates_from_zones(self, allow_synthetic_fallback: bool = False) -> Tuple[gpd.GeoDataFrame, Dict[str, Tuple[Provenance, str, str]]]:
        """Create synthetic candidate points within each zone (fallback).
        Returns (gdf, provenance_dict) where provenance_dict maps column names to
        (provenance, source, description) for synthetic columns.

        Args:
            allow_synthetic_fallback: Must be explicitly True to enable.
                Default False prevents silent synthetic generation on spatial mismatch.
        """
        if not allow_synthetic_fallback:
            raise RuntimeError(
                "SYNTHETIC FALLBACK DISABLED. "
                "No candidates assigned to zones. "
                "Set allow_synthetic_fallback=True ONLY for testing without real parking data. "
                "Production runs must have real parking-zone overlap."
            )
        zones = load_zone_polygons(self.config)
        records = []

        for _, zone in zones.iterrows():
            # Create 2-4 candidates per zone
            n_candidates = np.random.randint(2, 5)
            for i in range(n_candidates):
                # Random point within zone polygon
                poly = zone.geometry
                minx, miny, maxx, maxy = poly.bounds
                for _ in range(100):  # Try up to 100 times
                    pt = Point(
                        np.random.uniform(minx, maxx),
                        np.random.uniform(miny, maxy)
                    )
                    if poly.contains(pt):
                        records.append({
                            "zone": zone["zone_id"],
                            "land_cost_tier": zone.get("land_cost_tier", "medium"),
                            "primary_use": zone.get("primary_use", "mixed"),
                            "parking_type": "surface",
                            "area_sqm": np.random.uniform(
                                self.candidates_config.min_area_sqm,
                                self.candidates_config.max_area_sqm
                            ),
                            "geometry": pt,
                        })
                        break

        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        logger.info(f"Created {len(gdf)} synthetic candidates from zones")

        # All columns in synthetic candidates are SYNTHETIC
        provenance_dict = {}
        for col in ["zone", "land_cost_tier", "primary_use", "parking_type", "area_sqm", "geometry"]:
            provenance_dict[col] = (Provenance.SYNTHETIC, "assumption", "Synthetic candidate from zone fallback")

        return gdf, provenance_dict

    def _assign_zones(self, candidates: gpd.GeoDataFrame, zones: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Assign zone to each candidate via spatial join.
        Candidates outside all zones are marked UNASSIGNED (no nearest-neighbor fallback).
        Raises ValueError if ALL candidates are unassigned, indicating a spatial mismatch.
        """
        # Ensure both have same CRS
        if candidates.crs != zones.crs:
            candidates = candidates.to_crs(zones.crs)

        # Spatial join - only keep candidates within zones
        joined = gpd.sjoin(candidates, zones[["zone_id", "land_cost_tier", "primary_use", "geometry"]],
                           how="left", predicate="within")

        # Handle candidates not in any zone - mark as UNASSIGNED
        unassigned = joined[joined["zone_id"].isna()]
        if not unassigned.empty:
            if len(unassigned) == len(candidates):
                # FAIL FAST: All candidates unassigned = spatial mismatch
                parking_centroid = candidates.geometry.centroid.iloc[0]
                zone_bounds = zones.total_bounds
                raise ValueError(
                    f"SPATIAL MISMATCH DETECTED: All {len(candidates)} candidates UNASSIGNED. "
                    f"Parking centroid: ({parking_centroid.y:.4f}, {parking_centroid.x:.4f}). "
                    f"Zone bounds: [{zone_bounds[1]:.4f}, {zone_bounds[0]:.4f}, {zone_bounds[3]:.4f}, {zone_bounds[2]:.4f}]. "
                    f"Fix zone geometry or use provisional zones. See: data/external/vitap_zone_polygons_PROVISIONAL.geojson"
                )
            logger.warning(f"{len(unassigned)} candidates not within any zone - marking UNASSIGNED")
            joined.loc[unassigned.index, "zone_id"] = "UNASSIGNED"
            joined.loc[unassigned.index, "land_cost_tier"] = "unknown"
            joined.loc[unassigned.index, "primary_use"] = "unknown"

        # Rename zone_id to zone to match expected schema
        joined = joined.rename(columns={"zone_id": "zone"})
        # Drop index column from sjoin
        joined = joined.drop(columns=["index_right"], errors="ignore")

        return joined

    def _calculate_max_chargers(self, candidates: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Calculate maximum number of chargers per candidate based on area."""
        area_per_charger = self.candidates_config.area_per_charger_sqm
        candidates["max_chargers"] = np.clip(
            np.floor(candidates["area_sqm"] / area_per_charger).astype(int),
            self.candidates_config.min_chargers,
            self.candidates_config.max_chargers,
        )
        return candidates

    def _calculate_grid_connection(
        self,
        candidates: gpd.GeoDataFrame,
        power: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """Calculate distance to nearest transformer/substation and connection cost using metric CRS."""
        if power.empty:
            logger.warning("No power infrastructure data, grid distance UNKNOWN")
            candidates["grid_dist_m"] = np.nan
            candidates["nearest_transformer_id"] = "UNKNOWN"
            return candidates

        # Filter to transformers and substations
        transformers = power[power["power_type"].isin(["transformer", "substation"])].copy()
        if transformers.empty:
            logger.warning("No transformers/substations found in power data, grid distance UNKNOWN")
            candidates["grid_dist_m"] = np.nan
            candidates["nearest_transformer_id"] = "UNKNOWN"
            return candidates

        # Convert to UTM for metric distance calculation
        candidates_utm = self._to_utm(candidates)
        transformers_utm = self._to_utm(transformers)

        # Calculate distances in meters
        distances = []
        nearest_ids = []
        for _, cand in candidates_utm.iterrows():
            dists = transformers_utm.geometry.distance(cand.geometry)
            min_idx = dists.idxmin()
            min_dist_m = dists[min_idx]  # Already in meters (UTM)
            distances.append(min_dist_m)
            nearest_ids.append(transformers.loc[min_idx, "osm_id"])

        candidates["grid_dist_m"] = distances
        candidates["nearest_transformer_id"] = nearest_ids

        return candidates

    def _calculate_poi_score(self, candidates: gpd.GeoDataFrame, pois: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Calculate POI attractiveness score within 1km radius using metric CRS."""
        if pois.empty:
            candidates["poi_score"] = 0.0
            return candidates

        # POI weights (from config/data_sources.yaml)
        poi_weights = {
            "amenity=restaurant": 1.0, "amenity=cafe": 1.0, "amenity=canteen": 1.0,
            "amenity=fast_food": 0.8, "amenity=food_court": 1.0,
            "shop=mall": 1.0, "shop=supermarket": 1.0, "shop=department_store": 0.9,
            "shop=convenience": 0.6,
            "amenity=university": 0.8, "amenity=college": 0.8, "amenity=library": 0.8,
            "amenity=hospital": 0.7, "amenity=clinic": 0.7, "amenity=pharmacy": 0.7,
            "leisure=stadium": 0.6, "leisure=fitness_centre": 0.6, "leisure=sports_centre": 0.6,
            "public_transport=bus_stop": 0.9, "amenity=bus_station": 0.9,
            "office=administrative": 0.7, "office=company": 0.7, "office=coworking_space": 0.7,
            "amenity=dormitory": 0.5, "building=dormitory": 0.5, "building=residential": 0.4,
        }

        # Convert to UTM for metric distance calculation
        candidates_utm = self._to_utm(candidates)
        pois_utm = self._to_utm(pois)

        scores = []
        for _, cand in candidates_utm.iterrows():
            # Buffer 1km (1000 meters in UTM)
            buffer = cand.geometry.buffer(1000)
            nearby = pois_utm[pois_utm.geometry.within(buffer)]

            score = 0.0
            for _, poi in nearby.iterrows():
                # Find matching weight
                for tag_key in ["amenity", "shop", "leisure", "public_transport", "office", "building"]:
                    if tag_key in poi and pd.notna(poi[tag_key]):
                        key = f"{tag_key}={poi[tag_key]}"
                        if key in poi_weights:
                            # Distance decay
                            dist = cand.geometry.distance(poi.geometry)  # In meters (UTM)
                            decay = np.exp(-dist / 300)  # 300m decay constant
                            score += poi_weights[key] * decay
                        break

            scores.append(min(score, 100))  # Cap at 100

        candidates["poi_score"] = scores
        return candidates

    def _calculate_catchment_population(
        self,
        candidates: gpd.GeoDataFrame,
        buildings: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """Calculate population within 1km catchment using metric CRS."""
        if buildings.empty or "population_estimate" not in buildings.columns:
            # Estimate from floor area
            candidates["catchment_population"] = 0
            return candidates

        # Convert to UTM for metric distance calculation
        candidates_utm = self._to_utm(candidates)
        buildings_utm = self._to_utm(buildings)

        populations = []
        for _, cand in candidates_utm.iterrows():
            buffer = cand.geometry.buffer(1000)  # 1km in meters
            nearby = buildings_utm[buildings_utm.geometry.within(buffer)]
            pop = nearby["population_estimate"].sum() if not nearby.empty else 0
            populations.append(int(pop))

        candidates["catchment_population"] = populations
        return candidates

    def _calculate_competition(
        self,
        candidates: gpd.GeoDataFrame,
        existing: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """Count existing chargers within various radii using metric CRS."""
        if existing.empty:
            for col in ["existing_chargers_500m", "existing_chargers_1km", "existing_chargers_3km"]:
                candidates[col] = 0
            return candidates

        # Convert to UTM for metric distance calculation
        candidates_utm = self._to_utm(candidates)
        existing_utm = self._to_utm(existing)

        for radius, col in [(500, "existing_chargers_500m"), (1000, "existing_chargers_1km"), (3000, "existing_chargers_3km")]:
            counts = []
            for _, cand in candidates_utm.iterrows():
                buffer = cand.geometry.buffer(radius)  # radius in meters (UTM)
                count = existing_utm[existing_utm.geometry.within(buffer)].shape[0]
                counts.append(count)
            candidates[col] = counts

        return candidates

    def _calculate_traffic_accessibility(
        self,
        candidates: gpd.GeoDataFrame,
        roads: gpd.GeoDataFrame,
        gates_wgs84: Optional[List[Point]] = None,
    ) -> gpd.GeoDataFrame:
        """Calculate traffic accessibility score (inverse of travel time from gates) using metric CRS.

        Args:
            gates_wgs84: Campus entry points (lon/lat). Defaults to the legacy
                approximate gates, which predate the campus realignment.
        """
        if gates_wgs84 is None:
            # Legacy approximate gates - in WGS84
            gates_wgs84 = [
                Point(80.4780, 16.5155),  # North gate
                Point(80.4780, 16.5015),  # South gate
                Point(80.4855, 16.5015),  # Main gate
            ]

        # Convert gates to UTM
        gates_gdf = gpd.GeoDataFrame(geometry=gates_wgs84, crs=self.WGS84_CRS)
        gates_utm = self._to_utm(gates_gdf)
        gates_utm_list = list(gates_utm.geometry)

        if roads.empty:
            # Use Euclidean distance in UTM as proxy
            scores = []
            for _, cand in candidates.iterrows():
                cand_utm = self._to_utm(gpd.GeoDataFrame([cand], crs=candidates.crs)).geometry.iloc[0]
                min_dist = min(cand_utm.distance(g) for g in gates_utm_list)  # In meters
                # Normalize: closer = higher score
                score = 1 / (1 + min_dist / 1000)
                scores.append(score)
            candidates["traffic_accessibility_score"] = scores
            return candidates

        # TODO: Use OSRM for actual routing when available
        # For now, use Euclidean distance in UTM
        scores = []
        for _, cand in candidates.iterrows():
            cand_utm = self._to_utm(gpd.GeoDataFrame([cand], crs=candidates.crs)).geometry.iloc[0]
            min_dist = min(cand_utm.distance(g) for g in gates_utm_list)  # In meters
            score = 1 / (1 + min_dist / 1000)
            scores.append(score)

        candidates["traffic_accessibility_score"] = scores
        return candidates

    def _calculate_costs(self, candidates: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Calculate all cost components (SYNTHETIC assumptions)."""
        # Land cost from tier
        tier_map = self.cost_config._data['land_cost_per_sqm_by_tier']
        candidates["land_cost_inr_per_sqm"] = candidates["land_cost_tier"].map(tier_map).fillna(tier_map["medium"])
        candidates["land_cost_inr_lakhs"] = (candidates["land_cost_inr_per_sqm"] * candidates["area_sqm"]) / 100000

        # Grid connection cost - handle UNKNOWN distances
        rate_per_km = self.cost_config._data['grid_connection']['rate_per_km']
        # For UNKNOWN grid distances (NaN), set cost to NaN (not calculable)
        candidates["grid_connection_cost_inr_lakhs"] = np.where(
            candidates["grid_dist_m"].isna(),
            np.nan,
            (candidates["grid_dist_m"] / 1000) * rate_per_km
        )

        # Construction cost
        base = self.cost_config._data['construction']['base_cost']
        per_charger = self.cost_config._data['construction']['per_charger_cost']
        candidates["construction_cost_inr_lakhs"] = base + per_charger * candidates["max_chargers"]

        # Total CAPEX - UNKNOWN when grid cost is unknown
        # NaN propagates: if grid_connection_cost is NaN, total_capex becomes NaN
        candidates["total_capex_inr_lakhs"] = (
            candidates["land_cost_inr_lakhs"] +
            candidates["grid_connection_cost_inr_lakhs"] +
            candidates["construction_cost_inr_lakhs"]
        )

        return candidates

    def _select_top_candidates(self, candidates: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Select top N candidates by composite score.
        Only considers candidates with assigned zones (not UNASSIGNED).
        """
        target = self.candidates_config.target_count

        # Filter to only assigned zones
        assigned = candidates[candidates["zone"] != "UNASSIGNED"].copy()
        logger.info(f"Candidates with assigned zones: {len(assigned)} / {len(candidates)}")

        if len(assigned) <= target:
            return assigned

        # Composite score: demand potential + accessibility - cost
        assigned["selection_score"] = (
            assigned["poi_score"] * 0.3 +
            assigned["traffic_accessibility_score"] * 100 * 0.3 +
            assigned["catchment_population"] / 100 * 0.2 -
            assigned["total_capex_inr_lakhs"] / 100 * 0.2
        )

        selected = assigned.nlargest(target, "selection_score").copy()
        logger.info(f"Selected top {len(selected)} candidates from {len(assigned)} assigned")
        return selected

    def _assign_candidate_ids(self, candidates: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Assign sequential candidate IDs."""
        if candidates.empty:
            logger.warning("_assign_candidate_ids called with empty DataFrame - returning empty")
            return candidates
        candidates = candidates.reset_index(drop=True)
        candidates["candidate_id"] = [f"CAND_{i+1:03d}" for i in range(len(candidates))]

        # Generate name from zone and index
        candidates["name"] = candidates.apply(
            lambda row: f"{row['zone'].replace('_', ' ').title()} Parking {row['candidate_id']}",
            axis=1
        )

        return candidates


def generate_candidates(
    parking: gpd.GeoDataFrame,
    zones: gpd.GeoDataFrame,
    pois: gpd.GeoDataFrame,
    power: gpd.GeoDataFrame,
    existing_stations: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
    buildings: gpd.GeoDataFrame,
    config: Optional[Any] = None,
) -> gpd.GeoDataFrame:
    """Convenience function to generate candidates."""
    generator = CandidateGenerator(config)
    return generator.generate(parking, zones, pois, power, existing_stations, roads, buildings)