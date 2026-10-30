"""
Data preparation pipeline entry point.
Runs the complete data acquisition and generation pipeline.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from src.data.config_loader import get_config
from src.data.unified_loader import run_data_pipeline
from src.data.validate import validate_data


def setup_logging():
    """Configure logging."""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO",
    )
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "data_prep.log",
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
    )


def main():
    """Main entry point for data preparation."""
    setup_logging()
    logger.info("Starting VIT-AP EV Charging Data Preparation Pipeline")

    # Load config
    config = get_config("settings")

    # Run pipeline
    logger.info("Running data pipeline in hybrid mode...")
    data = run_data_pipeline(config, mode="hybrid", force_refresh=False)

    # Validate
    logger.info("Validating generated datasets...")
    validation_results = validate_data(config)

    # Summary
    logger.info("=" * 60)
    logger.info("DATA PREPARATION COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Generated datasets: {list(data.keys())}")
    logger.info(f"Validation status: { {k: v['status'] for k, v in validation_results.items()} }")

    # Print key stats
    if "candidates" in data:
        cand = data["candidates"]
        logger.info(f"Candidates: {len(cand)}")
        logger.info(f"  Zones: {cand['zone'].nunique()}")
        logger.info(f"  Total CAPEX: ₹{cand['total_capex_inr_lakhs'].sum():.1f} lakhs")

    if "demand_history" in data:
        demand = data["demand_history"]
        logger.info(f"Demand history: {len(demand):,} rows")
        logger.info(f"  Candidates: {demand['candidate_id'].nunique()}")
        logger.info(f"  Date range: {demand['timestamp'].min()} to {demand['timestamp'].max()}")

    if "weather_hourly" in data:
        weather = data["weather_hourly"]
        logger.info(f"Weather: {len(weather):,} hourly records")

    if "existing_stations" in data:
        stations = data["existing_stations"]
        logger.info(f"Existing stations: {len(stations)}")

    logger.info("Data preparation completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())