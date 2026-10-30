#!/usr/bin/env python3
"""
Main pipeline entry point for VIT-AP EV Charging Infrastructure Planning.
Usage: python run_pipeline.py <stage> [options]

Stages:
  data-prep    - Fetch real data, generate synthetic data, create candidates
  validate     - Validate all generated datasets
  ml           - Train ML demand prediction model (TODO)
  fuzzy        - Run fuzzy suitability scoring (TODO)
  ga           - Run genetic algorithm optimization (TODO)
  evaluate     - Evaluate and compare results (TODO)
  visualize    - Generate interactive map (TODO)
  all          - Run full pipeline (TODO)
"""

import sys
import argparse
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger


def setup_logging(verbose: bool = False):
    """Configure logging."""
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=level,
    )
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "pipeline.log",
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
    )


def run_data_prep(args):
    """Run data preparation pipeline."""
    from src.pipeline.run_data_prep import main as run_data_prep_main
    return run_data_prep_main()


def run_validate(args):
    """Run data validation."""
    from src.data.validate import validate_data
    from src.data.config_loader import get_config

    config = get_config("settings")
    results = validate_data(config)

    # Check for failures
    failed = [k for k, v in results.items() if v.get("status") == "FAIL"]
    if failed:
        logger.error(f"Validation FAILED for: {failed}")
        return 1

    logger.info("All validations passed!")
    return 0


def run_ml(args):
    """Train ML model (placeholder)."""
    logger.warning("ML stage not yet implemented")
    return 0


def run_fuzzy(args):
    """Run fuzzy logic (placeholder)."""
    logger.warning("Fuzzy stage not yet implemented")
    return 0


def run_ga(args):
    """Run genetic algorithm (placeholder)."""
    logger.warning("GA stage not yet implemented")
    return 0


def run_evaluate(args):
    """Run evaluation (placeholder)."""
    logger.warning("Evaluation stage not yet implemented")
    return 0


def run_visualize(args):
    """Generate visualizations (placeholder)."""
    logger.warning("Visualization stage not yet implemented")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="VIT-AP EV Charging Infrastructure Planning Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "stage",
        choices=["data-prep", "validate", "ml", "fuzzy", "ga", "evaluate", "visualize", "all"],
        help="Pipeline stage to run",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force re-download of real data",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logger.info(f"Starting pipeline stage: {args.stage}")

    stage_map = {
        "data-prep": run_data_prep,
        "validate": run_validate,
        "ml": run_ml,
        "fuzzy": run_fuzzy,
        "ga": run_ga,
        "evaluate": run_evaluate,
        "visualize": run_visualize,
    }

    if args.stage == "all":
        # Run all stages in sequence
        for stage in ["data-prep", "validate", "ml", "fuzzy", "ga", "evaluate", "visualize"]:
            logger.info(f"\n{'='*60}\nRunning stage: {stage}\n{'='*60}")
            if stage_map[stage](args) != 0:
                logger.error(f"Stage {stage} failed, stopping pipeline")
                return 1
        return 0

    return stage_map[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())