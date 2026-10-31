#!/usr/bin/env python3
"""
Main pipeline entry point for VIT-AP EV Charging Infrastructure Planning.
Usage: python run_pipeline.py <stage> [options]

Stages (real VIT-AP campus):
  candidates   - Build zones and candidate sites from the OSM campus extract
  ml           - Train the GA-tuned demand model and predict annual demand per site
  fuzzy        - Score every site with the fuzzy suitability system
  ga           - Pick the station set with the genetic algorithm
  evaluate     - Compare the GA plan with baselines and the exhaustive optimum
  visualize    - Write a shareable campus map (outputs/maps/campus_map.html)
  web          - Export data for the web dashboard (web/index.html)
  all          - Run candidates -> ml -> fuzzy -> ga -> evaluate -> visualize -> web

Legacy stages (original study area, superseded by 'candidates'):
  data-prep    - Fetch real data, generate synthetic data, create candidates
  validate     - Validate all generated datasets
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
    level = "DEBUG" if verbose else "WARNING"
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


def run_candidates(args):
    """Build zones and candidate sites on the real campus."""
    from src.pipeline.campus_pipeline import stage_candidates
    stage_candidates(refresh_osm=args.force_refresh)
    return 0


def run_ml(args):
    """Train the GA-tuned demand model."""
    from src.pipeline.campus_pipeline import stage_ml
    stage_ml()
    return 0


def run_fuzzy(args):
    """Run fuzzy suitability scoring."""
    from src.pipeline.campus_pipeline import stage_fuzzy
    stage_fuzzy()
    return 0


def run_ga(args):
    """Run the placement genetic algorithm."""
    from src.pipeline.campus_pipeline import stage_ga
    solution = stage_ga()
    return 0 if len(solution) else 1


def run_evaluate(args):
    """Compare the GA plan with baselines."""
    from src.evaluation.evaluate import run_evaluation
    from src.pipeline.campus_pipeline import GA_CONFIG, SCORED_CSV, SOLUTION_CSV
    run_evaluation(SCORED_CSV, SOLUTION_CSV, GA_CONFIG)
    return 0


def run_visualize(args):
    """Write the shareable campus map."""
    from src.pipeline.campus_pipeline import SCORED_CSV, SOLUTION_CSV
    from src.visualization.campus_map import build_campus_map
    build_campus_map(SCORED_CSV, SOLUTION_CSV)
    return 0


def run_web(args):
    """Export pipeline outputs for the web dashboard."""
    from src.visualization.web_export import build_web_data
    build_web_data()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="VIT-AP EV Charging Infrastructure Planning Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "stage",
        choices=["candidates", "ml", "fuzzy", "ga", "evaluate", "visualize", "web", "all", "data-prep", "validate"],
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
        help="Force re-download of real data (OSM extract for 'candidates')",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logger.info(f"Starting pipeline stage: {args.stage}")

    stage_map = {
        "data-prep": run_data_prep,
        "validate": run_validate,
        "candidates": run_candidates,
        "ml": run_ml,
        "fuzzy": run_fuzzy,
        "ga": run_ga,
        "evaluate": run_evaluate,
        "visualize": run_visualize,
        "web": run_web,
    }

    if args.stage == "all":
        # Run all stages in sequence
        for stage in ["candidates", "ml", "fuzzy", "ga", "evaluate", "visualize", "web"]:
            logger.info(f"\n{'='*60}\nRunning stage: {stage}\n{'='*60}")
            if stage_map[stage](args) != 0:
                logger.error(f"Stage {stage} failed, stopping pipeline")
                return 1
        return 0

    return stage_map[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())