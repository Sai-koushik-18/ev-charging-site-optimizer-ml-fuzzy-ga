"""
Synthetic data generators for EV charging demand and traffic.
All generated data is clearly labeled as SYNTHETIC provenance.
"""

import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from loguru import logger

from .config_loader import get_config
from .provenance import ProvenanceTracker, Provenance


class DemandGenerator:
    """
    Generates synthetic hourly EV charging demand for candidate locations.
    Based on statistical models calibrated to Indian campus EV charging patterns.
    """

    def __init__(self, config: Optional[Any] = None, seed: int = 123):
        self.config = config or get_config("settings")
        self.demand_config = self.config._data['demand_simulation']
        self.cost_config = self.config.costs
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    def generate(
        self,
        candidates: gpd.GeoDataFrame,
        weather: pd.DataFrame,
        years: List[int] = [2024],
    ) -> pd.DataFrame:
        """
        Generate hourly demand for all candidates over specified years.

        Args:
            candidates: GeoDataFrame with candidate locations and zone info
            weather: DataFrame with hourly weather data
            years: List of years to generate

        Returns:
            DataFrame with hourly demand per candidate
        """
        logger.info(f"Generating synthetic demand for {len(candidates)} candidates over {len(years)} year(s)")

        all_dfs = []

        for year in years:
            # Filter weather for this year
            weather_year = weather[
                (weather["timestamp"].dt.year == year)
            ].copy()

            if weather_year.empty:
                logger.warning(f"No weather data for year {year}, skipping")
                continue

            # Generate for each candidate
            for _, cand in candidates.iterrows():
                df = self._generate_candidate_year(cand, weather_year, year)
                all_dfs.append(df)

        if not all_dfs:
            return pd.DataFrame()

        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined.sort_values(["candidate_id", "timestamp"]).reset_index(drop=True)

        # Save
        output_dir = Path(self.config.paths.synthetic)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "demand_history.csv"

        # Attach provenance tracking
        tracker = ProvenanceTracker("synthetic_demand", "demand_generator.generate")
        demand_prov = {
            "candidate_id": (Provenance.DERIVED, "candidate_generator", "Candidate identifier from generation process"),
            "timestamp": (Provenance.SYNTHETIC, "synthetic_demand_model", "Combined weather and temporal timestamp"),
            "hour": (Provenance.SYNTHETIC, "synthetic_demand_model", "Hour extracted from timestamp"),
            "day_of_week": (Provenance.SYNTHETIC, "synthetic_demand_model", "Day of week (0-6) extracted from timestamp"),
            "month": (Provenance.SYNTHETIC, "synthetic_demand_model", "Month (1-12) extracted from timestamp"),
            "is_weekend": (Provenance.SYNTHETIC, "synthetic_demand_model", "Weekend flag derived from timestamp"),
            "is_holiday": (Provenance.SYNTHETIC, "synthetic_demand_model", "Holiday flag derived from timestamp and calendar"),
            "is_exam_period": (Provenance.SYNTHETIC, "synthetic_demand_model", "Exam period flag derived from timestamp and academic calendar"),
            "demand_kwh": (Provenance.SYNTHETIC, "synthetic_demand_model", "Synthetic hourly demand in kWh"),
            "session_count": (Provenance.SYNTHETIC, "synthetic_demand_model", "Calculated charging sessions per hour"),
            "avg_session_kwh": (Provenance.META, "demand_config", "Average session kWh from configuration"),
            "temperature_c": (Provenance.REAL, "weather_loader", "Temperature from weather data"),
            "humidity_pct": (Provenance.REAL, "weather_loader", "Relative humidity from weather data"),
            "precipitation_mm": (Provenance.REAL, "weather_loader", "Precipitation from weather data"),
            "data_provenance": (Provenance.META, "provenance_tracking", "Provenance metadata column")
        }
        for col, (prov, source, description) in demand_prov.items():
            if col in combined.columns:
                tracker.add_column(col, prov, source, description)
        combined = tracker.finalize(combined, source_files=[str(output_file)])
        combined.to_csv(output_file, index=False)
        logger.info(f"Saved synthetic demand history ({len(combined)} rows) to {output_file}")

        return combined

    def _generate_candidate_year(
        self,
        candidate: pd.Series,
        weather: pd.DataFrame,
        year: int,
    ) -> pd.DataFrame:
        """Generate demand for one candidate for one year."""
        zone = candidate["zone"]
        base_demand = self.demand_config['base_demand_kwh_per_hour_by_zone'].get(zone, 3.0)

        # EV growth factor
        years_from_base = year - self.demand_config['base_year']
        growth_factor = (1 + self.demand_config['ev_growth_rate_annual']) ** years_from_base

        # Campus calendar
        exam_periods = self._get_exam_periods(year)

        records = []
        for _, wrow in weather.iterrows():
            timestamp = wrow["timestamp"]
            hour = timestamp.hour
            day_of_week = timestamp.dayofweek  # 0=Mon
            month = timestamp.month
            is_weekend = day_of_week >= 5
            is_holiday = self._is_holiday(timestamp)
            is_exam = self._is_exam_period(timestamp, exam_periods)

            # Temporal multiplier
            temporal_mult = self._get_temporal_multiplier(hour, is_weekend, is_holiday, is_exam)

            # Weather multiplier
            temp = wrow["temperature_c"]
            weather_mult = self._get_weather_multiplier(temp)

            # Noise
            noise = self.rng.lognormal(mean=0, sigma=self.demand_config['noise_cv'])

            # Final demand
            demand = base_demand * temporal_mult * weather_mult * growth_factor * noise
            demand = max(0, demand)  # No negative demand

            # Session count
            avg_session = self.demand_config['avg_session_kwh']
            session_count = int(np.round(demand / avg_session))
            session_count = max(0, session_count)

            records.append({
                "candidate_id": candidate["candidate_id"],
                "timestamp": timestamp,
                "hour": hour,
                "day_of_week": day_of_week,
                "month": month,
                "is_weekend": is_weekend,
                "is_holiday": is_holiday,
                "is_exam_period": is_exam,
                "demand_kwh": round(demand, 2),
                "session_count": session_count,
                "avg_session_kwh": avg_session,
                "temperature_c": wrow["temperature_c"],
                "humidity_pct": wrow["relative_humidity_pct"],
                "precipitation_mm": wrow["precipitation_mm"],
                "data_provenance": "SYNTHETIC",
            })

        return pd.DataFrame(records)

    def _get_temporal_multiplier(
        self,
        hour: int,
        is_weekend: bool,
        is_holiday: bool,
        is_exam: bool,
    ) -> float:
        """Get temporal demand multiplier."""
        if is_holiday:
            pattern = "holiday"
        elif is_weekend:
            pattern = "weekend"
        else:
            pattern = "weekday"

        multipliers = {
            "weekday": [
                0.05, 0.03, 0.02, 0.02, 0.03, 0.05, 0.15, 0.35, 0.55, 0.65,
                0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 0.95, 0.85, 0.75,
                0.60, 0.40, 0.20, 0.10
            ],
            "weekend": [
                0.05, 0.03, 0.02, 0.02, 0.02, 0.03, 0.05, 0.10, 0.25, 0.40,
                0.55, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.85, 0.75, 0.60,
                0.45, 0.30, 0.15, 0.08
            ],
            "holiday": [
                0.04, 0.02, 0.01, 0.01, 0.01, 0.02, 0.03, 0.07, 0.18, 0.28,
                0.39, 0.46, 0.49, 0.53, 0.56, 0.60, 0.63, 0.60, 0.53, 0.42,
                0.32, 0.21, 0.11, 0.06
            ],
        }

        mult = multipliers[pattern][hour]

        # Exam period boost
        if is_exam:
            mult *= self.demand_config['temporal_patterns']['exam_period_factor']

        return mult

    def _get_weather_multiplier(self, temp: float) -> float:
        """Get weather-based demand multiplier."""
        if temp < self.demand_config['weather_sensitivity']['temp_threshold_low']:
            return self.demand_config['weather_sensitivity']['hvac_multiplier']
        elif temp > self.demand_config['weather_sensitivity']['temp_threshold_high']:
            return self.demand_config['weather_sensitivity']['hvac_multiplier']
        elif temp > 35:
            return 1.10
        return 1.0

    def _get_exam_periods(self, year: int) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
        """Get exam periods for VIT-AP (approximate)."""
        # VIT typically has exams in Nov-Dec and Apr-May
        return [
            (pd.Timestamp(f"{year}-11-15"), pd.Timestamp(f"{year}-12-20")),
            (pd.Timestamp(f"{year}-04-15"), pd.Timestamp(f"{year}-05-20")),
        ]

    def _is_holiday(self, timestamp: pd.Timestamp) -> bool:
        """Check if date is a holiday (simplified - major Indian holidays)."""
        # Major fixed holidays
        holidays = [
            (1, 26),   # Republic Day
            (8, 15),   # Independence Day
            (10, 2),   # Gandhi Jayanti
            (12, 25),  # Christmas
        ]
        # Variable holidays would need a proper calendar
        return (timestamp.month, timestamp.day) in holidays

    def _is_exam_period(self, timestamp: pd.Timestamp, exam_periods: List) -> bool:
        """Check if date falls in exam period."""
        for start, end in exam_periods:
            if start <= timestamp <= end:
                return True
        return False


class TrafficGenerator:
    """
    Generates synthetic hourly traffic volume for road segments.
    Based on road hierarchy and temporal patterns.
    """

    def __init__(self, config: Optional[Any] = None, seed: int = 456):
        self.config = config or get_config("settings")
        self.traffic_config = self.config.traffic_simulation
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    def generate(
        self,
        roads: gpd.GeoDataFrame,
        years: List[int] = [2024],
    ) -> pd.DataFrame:
        """
        Generate hourly traffic for all road segments over specified years.

        Args:
            roads: GeoDataFrame with road network (must have 'highway' column)
            years: List of years to generate

        Returns:
            DataFrame with hourly traffic per road segment
        """
        logger.info(f"Generating synthetic traffic for {len(roads)} road segments over {len(years)} year(s)")

        all_dfs = []

        for year in years:
            # Create hourly timestamps for the year
            timestamps = pd.date_range(
                start=f"{year}-01-01",
                end=f"{year}-12-31 23:00:00",
                freq="h",
                tz="Asia/Kolkata",
            )

            for _, road in roads.iterrows():
                df = self._generate_road_year(road, timestamps, year)
                all_dfs.append(df)

        if not all_dfs:
            return pd.DataFrame()

        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined.sort_values(["road_id", "timestamp"]).reset_index(drop=True)

        # Save
        output_dir = Path(self.config.paths.synthetic)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "traffic_hourly.csv"

        # Attach provenance tracking
        tracker = ProvenanceTracker("synthetic_traffic", "traffic_generator.generate")
        traffic_prov = {
            "road_id": (Provenance.REAL, "openstreetmap", "Road identifier from OSM"),
            "road_name": (Provenance.REAL, "openstreetmap", "Road name from OSM"),
            "hierarchy": (Provenance.REAL, "openstreetmap", "Road hierarchy from OSM highway tag"),
            "timestamp": (Provenance.SYNTHETIC, "synthetic_traffic_model", "Generated timestamp"),
            "volume_vph": (Provenance.SYNTHETIC, "synthetic_traffic_model", "Synthetic vehicle volume per hour"),
            "avg_speed_kmph": (Provenance.SYNTHETIC, "synthetic_traffic_model", "Synthetic average speed in km/h"),
            "congestion_level": (Provenance.SYNTHETIC, "synthetic_traffic_model", "Synthetic congestion level category"),
            "data_provenance": (Provenance.META, "provenance_tracking", "Provenance metadata column")
        }
        for col, (prov, source, description) in traffic_prov.items():
            if col in combined.columns:
                tracker.add_column(col, prov, source, description)
        combined = tracker.finalize(combined, source_files=[str(output_file)])
        combined.to_csv(output_file, index=False)
        logger.info(f"Saved synthetic traffic history ({len(combined)} rows) to {output_file}")

        return combined

    def _generate_road_year(
        self,
        road: pd.Series,
        timestamps: pd.DatetimeIndex,
        year: int,
    ) -> pd.DataFrame:
        """Generate traffic for one road for one year."""
        hierarchy = road.get("highway", "residential")
        base_volume = self.traffic_config.base_volume_vph_by_hierarchy.get(hierarchy, 100)
        free_flow_speed = self.traffic_config.free_flow_speed_kmph_by_hierarchy.get(hierarchy, 30)

        records = []
        for ts in timestamps:
            hour = ts.hour
            day_of_week = ts.dayofweek
            is_weekend = day_of_week >= 5

            # Temporal pattern
            temporal_mult = self._get_traffic_temporal_multiplier(hour, is_weekend)

            # Volume with noise
            volume = base_volume * temporal_mult
            noise = self.rng.normal(1.0, self.traffic_config.noise_cv)
            volume = max(0, int(volume * noise))

            # Speed from Greenshields model
            jam_density = 150  # veh/km/lane (approx)
            capacity = base_volume * 1.5  # Approx capacity
            if volume >= capacity:
                speed = free_flow_speed * 0.1  # Severe congestion
            else:
                speed = free_flow_speed * (1 - volume / capacity)

            # Congestion level
            v_c_ratio = volume / capacity if capacity > 0 else 0
            if v_c_ratio < 0.3:
                congestion = "free"
            elif v_c_ratio < 0.6:
                congestion = "moderate"
            elif v_c_ratio < 0.9:
                congestion = "heavy"
            else:
                congestion = "severe"

            records.append({
                "road_id": road["osm_id"] if "osm_id" in road.index else f"road_{road.name}",
                "road_name": road["name"] if "name" in road.index else "",
                "hierarchy": hierarchy,
                "timestamp": ts,
                "volume_vph": volume,
                "avg_speed_kmph": round(max(5, speed), 1),
                "congestion_level": congestion,
                "data_provenance": "SYNTHETIC",
            })

        return pd.DataFrame(records)

    def _get_traffic_temporal_multiplier(self, hour: int, is_weekend: bool) -> float:
        """Get traffic temporal multiplier."""
        # Morning peak: 7-10, Evening peak: 17-20
        peak_hours = set(self.traffic_config.peak_hours)

        if hour in peak_hours:
            base = 1.5
        elif hour in [6, 11, 16, 21]:
            base = 1.1
        elif 22 <= hour or hour <= 5:
            base = 0.2
        else:
            base = 0.8

        if is_weekend:
            base *= self.traffic_config.weekend_factor

        return base


def generate_synthetic_demand(
    candidates: gpd.GeoDataFrame,
    weather: pd.DataFrame,
    config: Optional[Any] = None,
    years: List[int] = [2024],
    seed: int = 123,
) -> pd.DataFrame:
    """Convenience function to generate synthetic demand."""
    generator = DemandGenerator(config, seed)
    return generator.generate(candidates, weather, years)


def generate_synthetic_traffic(
    roads: gpd.GeoDataFrame,
    config: Optional[Any] = None,
    years: List[int] = [2024],
    seed: int = 456,
) -> pd.DataFrame:
    """Convenience function to generate synthetic traffic."""
    generator = TrafficGenerator(config, seed)
    return generator.generate(roads, years)