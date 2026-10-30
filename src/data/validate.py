"""
Data validation and reporting script.
Generates comprehensive validation reports for all datasets.
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
import pandas as pd
import geopandas as gpd
import json
from loguru import logger

from .config_loader import get_config
from .provenance import ProvenanceTracker, Provenance, DatasetProvenance


class DataValidator:
    """Validates datasets and generates validation reports."""

    def __init__(self, config: Optional[Any] = None):
        self.config = config or get_config("settings")
        self.processed_dir = Path(self.config.paths.processed)
        self.synthetic_dir = Path(self.config.paths.synthetic)
        self.raw_dir = Path(self.config.paths.raw)

    def validate_all(self) -> Dict[str, Any]:
        """Validate all datasets and return summary."""
        results = {}

        # Processed datasets
        datasets = {
            "candidates": self.processed_dir / "candidates.csv",
            "demand_history": self.synthetic_dir / "demand_history.csv",
            "traffic_hourly": self.synthetic_dir / "traffic_hourly.csv",
            "weather_hourly": self.raw_dir / "weather" / "weather_hourly_combined.csv",
            "existing_stations": self.raw_dir / "openchargemap" / "stations.csv",
            "zones": self.raw_dir.parent / "external" / "vitap_zone_polygons.geojson",
            "ga_fitness_inputs": self.processed_dir / "ga_fitness_inputs.csv",
            "fuzzy_inputs": self.processed_dir / "fuzzy_inputs.csv",
        }

        for name, path in datasets.items():
            if path.exists():
                logger.info(f"Validating {name}...")
                if path.suffix == ".geojson":
                    df = gpd.read_file(path)
                else:
                    df = pd.read_csv(path)
                results[name] = self._validate_dataset(df, name, path)
            else:
                logger.warning(f"Dataset not found: {name} at {path}")
                results[name] = {"status": "MISSING", "path": str(path)}

        # Generate summary report
        self._generate_summary_report(results)

        return results

    def _validate_dataset(self, df: pd.DataFrame, name: str, path: Path) -> Dict[str, Any]:
        """Validate a single dataset."""
        result = {
            "name": name,
            "path": str(path),
            "status": "OK",
            "rows": len(df),
            "columns": len(df.columns),
            "column_names": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "missing_values": df.isnull().sum().to_dict(),
            "missing_pct": (df.isnull().sum() / len(df) * 100).round(2).to_dict(),
            "sample_records": df.head(3).to_dict(orient="records"),
            "numeric_summary": {},
            "provenance": {},
            "geographic_coverage": {},
        }

        # Numeric summary for numeric columns
        numeric_cols = df.select_dtypes(include=["number"]).columns
        if len(numeric_cols) > 0:
            result["numeric_summary"] = df[numeric_cols].describe().round(3).to_dict()

        # Provenance check
        prov = ProvenanceTracker.from_dataframe(df)
        if prov:
            result["provenance"] = {
                "summary": prov.summary(),
                "dataset_name": prov.dataset_name,
                "generated_by": prov.generated_by,
                "generated_at": prov.generated_at,
            }
        else:
            result["provenance"] = {"status": "NO_PROVENANCE_TRACKING"}

        # Geographic coverage for GeoDataFrames
        if hasattr(df, "geometry") and df.geometry is not None:
            result["geographic_coverage"] = self._check_geographic_coverage(df)

        # Validation checks
        issues = self._run_validation_checks(df, name)
        if issues:
            result["validation_issues"] = issues
            result["status"] = "WARNING" if len(issues) < 3 else "FAIL"

        return result

    def _check_geographic_coverage(self, gdf: gpd.GeoDataFrame) -> Dict[str, Any]:
        """Check geographic bounds and coverage."""
        bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
        campus_bounds = self.config.project.study_area.bbox

        return {
            "bounds": {
                "min_lon": float(bounds[0]),
                "min_lat": float(bounds[1]),
                "max_lon": float(bounds[2]),
                "max_lat": float(bounds[3]),
            },
            "within_study_area": (
                bounds[0] >= campus_bounds.min_lon and
                bounds[1] >= campus_bounds.min_lat and
                bounds[2] <= campus_bounds.max_lon and
                bounds[3] <= campus_bounds.max_lat
            ),
            "centroid": {
                "lon": float(gdf.geometry.centroid.x.mean()),
                "lat": float(gdf.geometry.centroid.y.mean()),
            },
        }

    def _run_validation_checks(self, df: pd.DataFrame, name: str) -> List[str]:
        """Run dataset-specific validation checks."""
        issues = []

        if name == "candidates":
            issues.extend(self._check_candidates(df))
        elif name == "demand_history":
            issues.extend(self._check_demand_history(df))
        elif name == "weather_hourly":
            issues.extend(self._check_weather(df))

        return issues

    def _check_candidates(self, df: pd.DataFrame) -> List[str]:
        """Validate candidates dataset."""
        issues = []

        # Check candidate count
        target = self.config.candidates.target_count
        if len(df) < target * 0.8:
            issues.append(f"Candidate count {len(df)} below target {target}")

        # Check required columns
        required = ["candidate_id", "zone", "latitude", "longitude", "max_chargers"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            issues.append(f"Missing required columns: {missing}")

        # Check coordinate bounds
        bounds = self.config.project.study_area.bbox
        if "latitude" in df.columns:
            oob = df[~df["latitude"].between(bounds.min_lat, bounds.max_lat)]
            if len(oob) > 0:
                issues.append(f"{len(oob)} candidates with latitude outside study area")
        if "longitude" in df.columns:
            oob = df[~df["longitude"].between(bounds.min_lon, bounds.max_lon)]
            if len(oob) > 0:
                issues.append(f"{len(oob)} candidates with longitude outside study area")

        # Check max_chargers reasonable
        if "max_chargers" in df.columns:
            invalid = df[(df["max_chargers"] < 1) | (df["max_chargers"] > 50)]
            if len(invalid) > 0:
                issues.append(f"{len(invalid)} candidates with invalid max_chargers")

        # Check provenance
        prov = ProvenanceTracker.from_dataframe(df)
        if prov:
            summary = prov.summary()
            if summary.get("SYNTHETIC", 0) == 0 and summary.get("DERIVED", 0) == 0:
                issues.append("No SYNTHETIC or DERIVED columns tracked - check provenance")
        else:
            issues.append("No provenance tracking found")

        return issues

    def _check_demand_history(self, df: pd.DataFrame) -> List[str]:
        """Validate demand history."""
        issues = []

        # Check required columns
        required = ["candidate_id", "timestamp", "demand_kwh", "data_provenance"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            issues.append(f"Missing required columns: {missing}")

        # Check provenance - should be SYNTHETIC
        if "data_provenance" in df.columns:
            prov_values = df["data_provenance"].unique()
            if not all(v == "SYNTHETIC" for v in prov_values):
                issues.append(f"data_provenance contains non-SYNTHETIC values: {prov_values}")

        # Check demand values
        if "demand_kwh" in df.columns:
            neg = df[df["demand_kwh"] < 0]
            if len(neg) > 0:
                issues.append(f"{len(neg)} rows with negative demand")
            high = df[df["demand_kwh"] > 200]
            if len(high) > 0:
                issues.append(f"{len(high)} rows with demand > 200 kWh (unrealistic)")

        # Check timestamp coverage
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            date_range = df["timestamp"].max() - df["timestamp"].min()
            expected_days = 365 * len(self.config.weather.years)
            if date_range.days < expected_days * 0.9:
                issues.append(f"Date range {date_range.days} days < expected {expected_days}")

        return issues

    def _check_weather(self, df: pd.DataFrame) -> List[str]:
        """Validate weather data."""
        issues = []

        # Check provenance - should be REAL
        if "data_provenance" in df.columns:
            prov_values = df["data_provenance"].unique()
            if not all(v == "REAL" for v in prov_values):
                issues.append(f"Weather data_provenance should be REAL, found: {prov_values}")

        # Check temperature range (Amaravati typical)
        if "temperature_c" in df.columns:
            oob = df[(df["temperature_c"] < 10) | (df["temperature_c"] > 50)]
            if len(oob) > 0:
                issues.append(f"{len(oob)} rows with temperature outside realistic range (10-50°C)")

        # Check humidity
        if "relative_humidity_pct" in df.columns:
            oob = df[(df["relative_humidity_pct"] < 0) | (df["relative_humidity_pct"] > 100)]
            if len(oob) > 0:
                issues.append(f"{len(oob)} rows with humidity outside 0-100%")

        return issues

    def _generate_summary_report(self, results: Dict[str, Any]):
        """Generate human-readable validation summary."""
        report_lines = [
            "=" * 80,
            "DATA VALIDATION SUMMARY REPORT",
            "=" * 80,
            "",
        ]

        for name, result in results.items():
            report_lines.append(f"\n{'─' * 80}")
            report_lines.append(f"Dataset: {name}")
            report_lines.append(f"Status: {result.get('status', 'UNKNOWN')}")
            report_lines.append(f"Path: {result.get('path', 'N/A')}")
            if result.get('status') != 'MISSING':
                report_lines.append(f"Rows: {result.get('rows', 0):,}")
                report_lines.append(f"Columns: {result.get('columns', 0)}")
            else:
                report_lines.append("Rows: N/A (missing dataset)")
                report_lines.append("Columns: N/A (missing dataset)")

            if result.get("missing_values"):
                total_missing = sum(result["missing_values"].values())
                if total_missing > 0:
                    report_lines.append(f"Missing values: {total_missing:,} ({sum(result['missing_pct'].values()):.1f}% total)")

            if result.get("provenance", {}).get("summary"):
                prov_summary = result["provenance"]["summary"]
                report_lines.append(f"Provenance: {prov_summary}")

            if result.get("geographic_coverage"):
                geo = result["geographic_coverage"]
                report_lines.append(f"Geographic bounds: [{geo['bounds']['min_lon']:.4f}, {geo['bounds']['min_lat']:.4f}, {geo['bounds']['max_lon']:.4f}, {geo['bounds']['max_lat']:.4f}]")
                report_lines.append(f"Within study area: {geo['within_study_area']}")

            if result.get("validation_issues"):
                report_lines.append("Validation Issues:")
                for issue in result["validation_issues"]:
                    report_lines.append(f"  ⚠ {issue}")

            # Sample records
            if result.get("sample_records"):
                report_lines.append("Sample records (first 3):")
                for i, rec in enumerate(result["sample_records"]):
                    report_lines.append(f"  Record {i+1}: {json.dumps(rec, default=str)[:200]}...")

        report_lines.append("\n" + "=" * 80)
        report_lines.append("END OF REPORT")
        report_lines.append("=" * 80)

        # Print to console
        print("\n".join(report_lines))

        # Save to file
        report_path = Path(self.config.paths.logs) / "validation_report.txt"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            f.write("\n".join(report_lines))

        # Save JSON for programmatic access
        json_path = Path(self.config.paths.logs) / "validation_report.json"
        with open(json_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

        logger.info(f"Validation report saved to {report_path}")


def validate_data(config: Optional[Any] = None) -> Dict[str, Any]:
    """Convenience function to run validation."""
    validator = DataValidator(config)
    return validator.validate_all()