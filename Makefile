# Makefile for VIT-AP EV Charging Infrastructure Planning
# Usage: make <target>

.PHONY: help install install-dev test lint all candidates ml fuzzy ga evaluate visualize web place serve-api deploy-vercel deploy-space dashboard data-prep validate clean

help:
	@echo "VIT-AP EV Charging Infrastructure Planning - ML-Fuzzy-GA"
	@echo ""
	@echo "Setup:"
	@echo "  install       - Install runtime dependencies"
	@echo "  install-dev   - Install runtime and development dependencies"
	@echo "  test          - Run unit tests with coverage"
	@echo "  lint          - Run ruff"
	@echo ""
	@echo "Campus pipeline:"
	@echo "  all           - candidates -> ml -> fuzzy -> ga -> evaluate -> visualize"
	@echo "  candidates    - Build zones and candidate sites from OpenStreetMap"
	@echo "  ml            - Train the GA-tuned demand model"
	@echo "  fuzzy         - Score sites with the fuzzy system"
	@echo "  ga            - Select stations with the genetic algorithm"
	@echo "  evaluate      - Compare the GA plan with baselines"
	@echo "  visualize     - Write outputs/maps/campus_map.html"
	@echo "  web           - Export data for the web dashboard (open web/index.html)"
	@echo "  place         - Plan any place: make place PLACE=\"IIT Madras\" BUDGET=300"
	@echo "  serve-api     - Run the live planner API locally on port 7860"
	@echo "  deploy-vercel - Publish the website and the planner API to Vercel (needs VERCEL_TOKEN)"
	@echo "  deploy-space  - Publish the API to Hugging Face: make deploy-space HF_USER=<name> (needs HF_TOKEN)"
	@echo "  dashboard     - Launch the Streamlit dashboard"
	@echo ""
	@echo "Legacy data pipeline:"
	@echo "  data-prep     - Original data preparation pipeline"
	@echo "  validate      - Validate legacy datasets"
	@echo ""
	@echo "  clean         - Remove caches and large regenerable files"

install:
	pip install -r requirements.txt

install-dev: install
	pip install -r requirements-dev.txt

test:
	pytest tests/ --cov=src --cov-report=term-missing

lint:
	ruff check src/ tests/

all:
	python run_pipeline.py all

candidates:
	python run_pipeline.py candidates

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

web:
	python run_pipeline.py web

PLACE ?= IIT Madras
BUDGET ?= 300
place:
	python run_pipeline.py place --place "$(PLACE)" --budget $(BUDGET)

serve-api:
	uvicorn server.app:app --port 7860

deploy-vercel:
	python deploy/deploy_vercel.py

deploy-space:
	python deploy/deploy_space.py --user $(HF_USER)

dashboard:
	streamlit run demo_dashboard.py

data-prep:
	python run_pipeline.py data-prep

validate:
	python run_pipeline.py validate

# Removes caches and the large files the pipeline regenerates; keeps committed results
clean:
	rm -rf data/synthetic/* models/*.pkl logs/
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
