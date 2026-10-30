# Smart EV Charging Infrastructure Planning for VIT-AP University Using ML–Fuzzy–GA
[![Status](https://img.shields.io/badge/Status-Full%20Pipeline%20Implemented-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)]()

> **University Project** — VIT-AP University, Amaravati, Andhra Pradesh, India

## Overview

This project implements a **hybrid ML–Fuzzy–GA pipeline** for optimal EV charging station placement at VIT-AP University campus and surrounding area (provisional 2km buffer).

### Pipeline Architecture

```
Input Data (Real + Derived + Synthetic)
    │
    ▼
Data Preprocessing & Feature Engineering
    │
    ▼
ML Demand Prediction (XGBoost) ──► Hourly demand per candidate location
    │
    ▼
Fuzzy Location Suitability (scikit-fuzzy) ──► Suitability score [0,1] per candidate
    │
    ▼
Genetic Algorithm Optimization (DEAP) ──► Optimal subset of K locations
    │
    ▼
Evaluation & Visualization ──► Metrics, comparisons, interactive Folium map
```

### Key Features

- **Provenance Tracking**: Every feature labeled as `REAL`, `DERIVED`, `SYNTHETIC`, or `META`
- **Hybrid Data Strategy**: Real OSM/OpenChargeMap/Weather + Synthetic demand/traffic
- **Configurable**: All parameters in YAML config files
- **Reproducible**: Fixed seeds, versioned config, complete provenance
- **Modular**: Clean separation of data, ML, fuzzy, GA, evaluation modules
- **Interactive Dashboard**: Streamlit + Folium for result exploration

## Quick Start

### Prerequisites

- Python 3.10+
- Git
- OpenChargeMap API key (free tier) — set as `OCM_API_KEY` environment variable

### Installation

```bash
git clone https://github.com/Gharsha-2006/vitap-ev-charging-ml-fuzzy-ga-.git
cd vitap-ev-charging-ml-fuzzy-ga-
# Install dependencies
make install

# Or manually:
pip install -r requirements.txt
```

### Configuration

1. **Set API key** (required for OpenChargeMap):
   ```bash
   export OCM_API_KEY="your_api_key_here"
   ```

2. **Review configuration** in `config/`:
   - `settings.yaml` — Main parameters (study area, costs, simulation)
   - `fuzzy_config.yaml` — Membership functions and rules
   - `ga_config.yaml` — GA hyperparameters
   - `data_sources.yaml` — Data source definitions

### Run Data Pipeline

```bash
# Fetch real data, generate synthetic data, create candidates
make data-prep

# Or with force refresh
make data-prep-force

# Validate all generated datasets
make validate
```

### Expected Outputs (After `make data-prep`)

```
data/
├── processed/
│   ├── candidates.csv              # 25 candidate locations with all features
│   ├── ga_fitness_inputs.csv       # GA fitness calculation inputs
│   └── fuzzy_inputs.csv            # Fuzzy logic inputs (static per candidate)
├── synthetic/
│   ├── demand_history.csv          # Hourly demand (25 × 8760 = 219,000 rows)
│   └── traffic_hourly.csv          # Hourly traffic per road segment
└── raw/
    ├── osm/                        # OSM roads, buildings, POIs, power, parking
    ├── openchargemap/              # Existing EV stations
    └── weather/                    # Open-Meteo historical weather
```

## Data Provenance

Every dataset includes a `data_provenance` column (JSON) tracking the origin of each column:

| Label | Meaning |
|-------|---------|
| **REAL** | Directly from authoritative source (OSM, OpenChargeMap, Open-Meteo) |
| **DERIVED** | Computed from REAL data (distances, scores, accessibility) |
| **SYNTHETIC** | Generated from statistical models (demand, traffic, costs) |
| **META** | Provenance tracking metadata |

### Example Provenance (candidates.csv)

```json
{
  "candidate_id": "REAL",
  "latitude": "REAL",
  "longitude": "REAL",
  "zone": "REAL",
  "area_sqm": "DERIVED",
  "max_chargers": "DERIVED",
  "land_cost_tier": "DERIVED",
  "land_cost_inr_lakhs": "SYNTHETIC",
  "grid_dist_m": "DERIVED",
  "grid_connection_cost_inr_lakhs": "DERIVED",
  "poi_score": "DERIVED",
  "catchment_population": "DERIVED",
  "existing_chargers_1km": "REAL",
  "traffic_accessibility_score": "DERIVED",
  "construction_cost_inr_lakhs": "SYNTHETIC",
  "total_capex_inr_lakhs": "DERIVED"
}
```

## Project Structure

```
vitap-ev-charging-ml-fuzzy-ga/
├── config/                 # YAML configuration files
├── data/
│   ├── raw/               # Downloaded real data (gitignored)
│   ├── processed/         # Pipeline outputs (candidates, GA inputs, fuzzy inputs)
│   ├── synthetic/         # Generated synthetic data (demand, traffic) (gitignored)
│   ├── external/          # Static reference data (zone polygons, EV adoption curves)
│   └── samples/           # Small samples for CI (gitignored)
├── src/
│   ├── data/              # Data acquisition & generation
│   ├── ml/                # ML demand prediction (XGBoost)
│   ├── fuzzy/             # Fuzzy suitability scoring (scikit-fuzzy)
│   ├── ga/                # Genetic algorithm optimization (DEAP)
│   ├── evaluation/        # Metrics & baselines
│   ├── visualization/     # Folium maps & charts
│   └── pipeline/          # Pipeline entry points
├── notebooks/             # Jupyter notebooks for exploration
├── tests/                 # Unit tests
├── outputs/               # Final results (models, maps, reports) (gitignored)
├── logs/                  # Pipeline logs (gitignored)
├── docs/                  # Documentation
├── requirements.txt
├── Makefile
├── run_pipeline.py        # Main CLI entry point
├── demo_dashboard.py      # Streamlit dashboard
└── README.md
```

## Configuration Highlights

### Study Area (PROVISIONAL)
- **Center**: VIT-AP University (16.4940°N, 80.4993°E) — *provisional, aligned to landmarks*
- **Bounds**: ~1.2km × 1.3km buffer around campus landmarks
- **Zones**: 10 manually digitized zones (academic, hostels, staff, admin, commercial, sports, gates)
- **Provenance**: `provisional: true` in config — NOT from official VIT-AP master plan

### Cost Assumptions (SYNTHETIC — Clearly Documented)
| Component | Assumption |
|-----------|------------|
| Land cost (academic/very_low) | ₹500/sqm |
| Land cost (commercial/high) | ₹15,000/sqm |
| Grid connection | ₹2 lakh/km |
| Construction base | ₹15 lakh |
| Per charger (60kW DC) | ₹3 lakh |
| Target budget | ₹300 lakh |
| Min station separation | 1000m |

### Demand Simulation (SYNTHETIC)
- Base demand per zone calibrated to Indian campus studies
- Temporal patterns: weekday/weekend/holiday/exam periods
- Weather sensitivity: HVAC load at temp <20°C or >38°C
- 20% annual EV growth rate

### GA Configuration
- **Target stations**: 5 (changed from 10)
- **Budget constraint**: ₹300 lakh (HARD)
- **Min separation**: 1000m (HARD)
- **Feasibility note**: Current demo shows max ~2-3 stations feasible under hard constraints; GA will report infeasibility explicitly if 5 stations cannot be placed

## Development Status

| Stage | Status | Description |
|-------|--------|-------------|
| **Data Layer** | ✅ Complete | OSM, OCM, Weather fetchers; synthetic generators; candidate generation; validation |
| **ML Pipeline** | ✅ Complete | XGBoost demand prediction with time-series CV, SHAP explainability |
| **Fuzzy Logic** | ✅ Complete | scikit-fuzzy Mamdani inference with 6 inputs, 20+ rules |
| **GA Optimization** | ✅ Complete | DEAP binary encoding, fixed cardinality, multi-objective fitness |
| **Evaluation** | ⏳ In Progress | Baselines (random, greedy, k-means), statistical comparison |
| **Visualization** | ✅ Complete | Interactive Folium map with all layers + Streamlit dashboard |

## Known Limitations

1. **Provisional Zone Boundaries**: Campus zones manually digitized from satellite imagery — NOT from official VIT-AP master plan
2. **Demo Transformers**: Electrical transformer locations are hypothetical (`vitap_demo_transformers.geojson`, provenance=SYNTHETIC) — real VIT-AP transformer data not available
3. **Missing Reference Data**: `indian_charging_patterns.csv` referenced in config but not present — `indian_ev_adoption_curve.csv` available as proxy
4. **GA Feasibility**: 5-station target may be infeasible under ₹300L budget + 1000m separation; pipeline reports constraint violations explicitly
5. **No Real Demand Data**: Campus-specific EV charging demand not available; synthetic model calibrated to Indian national averages

## Validation

Run validation to check all datasets:

```bash
make validate
```

Outputs:
- Console summary with provenance, row counts, missing values
- `logs/validation_report.txt` — Human-readable report
- `logs/validation_report.json` — Machine-readable results

## Dashboard

Launch interactive dashboard:

```bash
streamlit run demo_dashboard.py
```

Features:
- Campus zone polygons (DERIVED)
- Candidate locations with fuzzy scores
- GA-selected optimal stations
- Demand heatmaps
- Cost breakdowns
- Provenance-aware layer toggles

## Acknowledgments

- OpenStreetMap contributors
- OpenChargeMap for charging station data
- Open-Meteo for weather data
- VIT-AP University for campus context
- NITI Aayog / SIAM for EV adoption reference data

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

**Note**: This is a university project. Synthetic data is clearly labeled and documented. Real campus-specific EV demand and traffic data were not available at time of development. All assumptions are configurable via `config/settings.yaml`. Provisional campus boundaries and demo transformer locations are clearly marked — do not use for official planning without verification.