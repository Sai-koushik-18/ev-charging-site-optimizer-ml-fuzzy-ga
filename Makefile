# Makefile for VIT-AP EV Charging Infrastructure Planning
# Usage: make <target>

.PHONY: help install test lint format type-check data-prep validate ml fuzzy ga evaluate visualize all clean

# Default target
help:
	@echo "VIT-AP EV Charging Infrastructure Planning - ML-Fuzzy-GA"
	@echo ""
	@echo "Available targets:"
	@echo "  install       - Install Python dependencies"
	@echo "  test          - Run unit tests"
	@echo "  lint          - Run linting (ruff)"
	@echo "  format        - Format code (black)"
	@echo "  type-check    - Run type checking (mypy)"
	@echo "  data-prep     - Run data preparation pipeline"
	@echo "  validate      - Validate generated datasets"
	@echo "  ml            - Train ML model (TODO)"
	@echo "  fuzzy         - Run fuzzy logic (TODO)"
	@echo "  ga            - Run genetic algorithm (TODO)"
	@echo "  evaluate      - Evaluate results (TODO)"
	@echo "  visualize     - Generate maps (TODO)"
	@echo "  all           - Run full pipeline (TODO)"
	@echo "  clean         - Remove generated files"
	@echo ""

# Install dependencies
install:
	pip install --upgrade pip
	pip install -r requirements.txt
	pip install -e .

# Development install with dev dependencies
install-dev:
	pip install --upgrade pip
	pip install -r requirements.txt -r requirements-dev.txt
	pip install -e .
	pre-commit install

# Run tests
test:
	pytest tests/ -v --cov=src --cov-report=term-missing

# Linting
lint:
	ruff check src/ tests/

# Formatting
format:
	black src/ tests/ *.py
	ruff check --fix src/ tests/

# Type checking
type-check:
	mypy src/

# Data pipeline stages
data-prep:
	python run_pipeline.py data-prep

data-prep-force:
	python run_pipeline.py data-prep --force-refresh

validate:
	python run_pipeline.py validate

ml:
	python run_pipeline.py ml

fuzzy:
	python run_pipeline.py fuzzy

ga:
	python run_pipeline.py ga

evaluate:
	python run_pipeline.py evaluate

visualize:
	python run_pipeline.py visualize

# Full pipeline (when all stages implemented)
all: data-prep validate ml fuzzy ga evaluate visualize

# Clean generated files
clean:
	rm -rf data/processed/* data/synthetic/* outputs/* logs/*.log
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete

# Clean everything including raw data
clean-all: clean
	rm -rf data/raw/*

# Jupyter notebook
notebook:
	jupyter lab notebooks/

# Quick validation of data files
check-data:
	@echo "Checking data files..."
	@ls -la data/processed/*.csv 2>/dev/null | head -20
	@ls -la data/synthetic/*.csv 2>/dev/null | head -20
	@ls -la data/raw/osm/*.geojson 2>/dev/null | head -20
	@ls -la data/raw/openchargemap/*.csv 2>/dev/null | head -10
	@ls -la data/raw/weather/*.csv 2>/dev/null | head -10