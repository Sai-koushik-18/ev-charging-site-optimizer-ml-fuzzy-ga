"""
Open-Meteo weather data fetcher for historical weather data.
"""

from pathlib import Path
from typing import Optional, Any, Dict, List
import requests
import pandas as pd
import geopandas as gpd
from loguru import logger

from .config_loader import get_config, get_data_sources_config
from .provenance import ProvenanceTracker, Provenance


class WeatherLoader:
    """Fetches historical weather data from Open-Meteo API."""

    def __init__(self, config: Optional[Any] = None):
        self.config = config or get_config("settings")
        self.sources_config = get_data_sources_config()
        self.weather_config = self.sources_config.data_sources.open_meteo
        self.output_dir = Path(self.config.paths.raw) / "weather"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _build_params(self, year: int) -> Dict[str, Any]:
        """Build API request parameters for a specific year."""
        params = {
            "latitude": self.weather_config.params.latitude,
            "longitude": self.weather_config.params.longitude,
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
            "hourly": ",".join(self.weather_config.params.hourly),
            "timezone": self.weather_config.params.timezone,
        }
        return params

    def fetch_year(self, year: int) -> pd.DataFrame:
        """Fetch weather data for a specific year."""
        logger.info(f"Fetching weather data for {year} from Open-Meteo...")
        url = self.weather_config.base_url
        params = self._build_params(year)

        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Open-Meteo API request failed for {year}: {e}")
            data = None

        if not data:
            logger.warning(f"No data returned from Open-Meteo API for {year}")
            # Try to load from cache for this year
            cache_file = self.output_dir / f"weather_hourly_{year}.csv"
            if cache_file.exists():
                logger.info(f"Loading cached weather data for {year} from {cache_file}")
                try:
                    df = pd.read_csv(cache_file, parse_dates=["timestamp"])
                    if not df.empty:
                        logger.info(f"Loaded {len(df)} hourly weather records for {year} from cache")
                        return df
                except Exception as cache_error:
                    logger.error(f"Failed to load cached weather data for {year}: {cache_error}")
            logger.warning(f"No cache available for weather data for {year}")
            return pd.DataFrame()

        return self._parse_weather(data, year)

    def _parse_weather(self, data: Dict, year: int) -> pd.DataFrame:
        """Parse API response into DataFrame."""
        hourly = data.get("hourly", {})
        if not hourly:
            logger.warning(f"No hourly data in response for {year}")
            return pd.DataFrame()

        # Create DataFrame
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(hourly.get("time", [])),
            "temperature_c": hourly.get("temperature_2m", []),
            "relative_humidity_pct": hourly.get("relative_humidity_2m", []),
            "precipitation_mm": hourly.get("precipitation", []),
            "wind_speed_kmph": hourly.get("wind_speed_10m", []),
            "cloud_cover_pct": hourly.get("cloud_cover", []),
        })

        if df.empty:
            return df

        # Add derived fields
        df["heat_index_c"] = df.apply(self._compute_heat_index, axis=1)
        df["data_provenance"] = "REAL"

        # Ensure hourly frequency
        df = df.set_index("timestamp").asfreq("h").reset_index()

        # Save
        output_file = self.output_dir / f"weather_hourly_{year}.csv"
        df.to_csv(output_file, index=False)
        logger.info(f"Saved {len(df)} hourly weather records for {year} to {output_file}")

        # Add provenance
        tracker = ProvenanceTracker(f"weather_hourly_{year}", "weather_loader.fetch_year")
        tracker.add_columns_batch(
            columns=["timestamp", "temperature_c", "relative_humidity_pct",
                     "precipitation_mm", "wind_speed_kmph", "cloud_cover_pct"],
            provenance=Provenance.REAL,
            source="open-meteo",
            description="Historical weather data from Open-Meteo API",
        )
        tracker.add_column(
            "heat_index_c",
            Provenance.DERIVED,
            "computed",
            "Heat index (feels-like temperature)",
            transformation="_compute_heat_index function",
        )
        tracker.add_column(
            "data_provenance",
            Provenance.META,
            "provenance_tracker",
            "Provenance tracking column",
        )
        df = tracker.finalize(df, source_files=[str(output_file)])

        return df

    def _compute_heat_index(self, row) -> float:
        """Compute heat index (feels-like temperature) in Celsius."""
        # Simplified heat index formula (Rothfusz regression)
        # Only valid for T >= 27°C and RH >= 40%
        T = row["temperature_c"]
        RH = row["relative_humidity_pct"]

        if T < 27 or RH < 40:
            return T

        # Rothfusz formula constants
        c1 = -8.78469475556
        c2 = 1.61139411
        c3 = 2.33854883889
        c4 = -0.14611605
        c5 = -0.012308094
        c6 = -0.0164248277778
        c7 = 2.211732e-3
        c8 = 7.2546e-4
        c9 = -3.582e-6

        hi = (c1 + c2*T + c3*RH + c4*T*RH + c5*T**2 + c6*RH**2 +
              c7*T**2*RH + c8*T*RH**2 + c9*T**2*RH**2)

        return round(hi, 1)

    def fetch_all_years(self, years: Optional[List[int]] = None) -> pd.DataFrame:
        """Fetch weather data for multiple years and combine."""
        if years is None:
            # Derive years from start_date and end_date in config
            start_str = self.weather_config.params.start_date
            end_str = self.weather_config.params.end_date
            start_year = int(start_str[:4])
            end_year = int(end_str[:4])
            years = list(range(start_year, end_year + 1))

        all_dfs = []
        missing_years = []
        for year in years:
            df = self.fetch_year(year)
            if df.empty:
                missing_years.append(year)
                logger.error(f"Missing weather data for year {year} (no cache and API failure)")
            else:
                all_dfs.append(df)

        if missing_years:
            logger.error(f"Missing weather data for years: {missing_years}. Stopping data preparation.")
            return pd.DataFrame()

        if not all_dfs:
            return pd.DataFrame()

        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined.sort_values("timestamp").reset_index(drop=True)

        # Save combined
        output_file = self.output_dir / "weather_hourly_combined.csv"
        combined.to_csv(output_file, index=False)
        logger.info(f"Saved combined weather data ({len(combined)} rows) to {output_file}")

        return combined


def fetch_weather_data(config: Optional[Any] = None, years: Optional[List[int]] = None) -> pd.DataFrame:
    """Convenience function to fetch weather data."""
    loader = WeatherLoader(config)
    return loader.fetch_all_years(years)