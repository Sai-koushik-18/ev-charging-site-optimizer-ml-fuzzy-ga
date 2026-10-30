# GitHub Upload Checklist - VIT-AP EV Charging Infrastructure Planner

**Generated:** 2026-10-30  
**Purpose:** Pre-flight checklist before first GitHub commit/push  
**Status:** ✅ Ready for review

---

## 1. Security Audit - Credentials & Secrets

### 1.1 Configuration Files Scan
| File | Status | Notes |
|------|--------|-------|
| `config/settings.yaml` | ✅ Clean | `api_key: null`, no hardcoded secrets |
| `config/data_sources.yaml` | ✅ Clean | Uses `api_key_env: "OCM_API_KEY"` (env var) |
| `config/fuzzy_config.yaml` | ✅ Clean | No secrets |
| `config/ga_config.yaml` | ✅ Clean | No secrets |
| `config/*.yaml` (all) | ✅ Clean | No API keys, passwords, tokens |

### 1.2 Source Code Scan
| File | Status | Notes |
|------|--------|-------|
| `src/data/ocm_loader.py` | ✅ Clean | Reads `OCM_API_KEY` from environment |
| `src/data/config_loader.py` | ✅ Clean | Uses `os.environ.get()` for env vars |
| `src/data/osm_loader.py` | ✅ Clean | No secrets |
| `src/data/weather_loader.py` | ✅ Clean | No API key needed (Open-Meteo free) |
| `src/data/unified_loader.py` | ✅ Clean | No secrets |
| All `src/**/*.py` | ✅ Clean | No hardcoded credentials found |

### 1.3 Environment Files
| File | Status | Action |
|------|--------|--------|
| `.env` | ❌ Not present | OK - not committed |
| `.env.local` | ❌ Not present | OK - not committed |
| `.env.example` | ❌ Not present | Consider creating template |

### 1.4 Secret Pattern Grep Results
```bash
# No matches for hardcoded secrets in:
# - API_KEY, api_key (except env var references)
# - password, secret, token
# - Private keys
```

---

## 2. .gitignore Validation

### 2.1 Exclusion Patterns Verified
| Pattern | Test File | Result |
|---------|-----------|--------|
| `data/raw/` | `data/raw/osm/roads.geojson` (2.1MB) | ✅ Ignored |
| `data/processed/` | `data/processed/candidates.csv` (187KB) | ✅ Ignored |
| `data/synthetic/` | `data/synthetic/traffic_hourly.csv` (14GB) | ✅ Ignored |
| `outputs/` | (directory) | ✅ Ignored |
| `logs/` | (directory) | ✅ Ignored |
| `__pycache__/` | (directory) | ✅ Ignored |
| `.venv/` | (directory) | ✅ Ignored |
| `*.pkl` | `models/*.pkl` | ✅ Ignored |

### 2.2 Inclusion Patterns Verified (NOT ignored)
| File | Status |
|------|--------|
| `src/**/*.py` | ✅ Included |
| `config/*.yaml` | ✅ Included |
| `demo_dashboard.py` | ✅ Included |
| `tests/**/*.py` | ✅ Included |
| `README.md` | ✅ Included |
| `requirements.txt` | ✅ Included |
| `pyproject.toml` | ✅ Included |
| `Makefile` | ✅ Included |
| `data/external/*.geojson` | ✅ Included (small reference files) |
| `data/external/*.csv` | ✅ Included (small reference files) |

### 2.3 Cleanup Summary
- ✅ Removed duplicate entries (were 159 lines → now ~60 lines)
- ✅ Consolidated redundant patterns
- ✅ Kept all critical exclusions for large/sensitive data

---

## 3. Large Dataset Verification

### 3.1 Files Confirmed Excluded (>100KB = gitignored)
| Path | Size | Status |
|------|------|--------|
| `data/synthetic/traffic_hourly.csv` | 14 GB | ✅ Excluded |
| `data/synthetic/demand_history.csv` | 918 MB | ✅ Excluded |
| `data/raw/weather/weather_hourly_combined.csv` | 21 MB | ✅ Excluded |
| `data/raw/osm/roads.geojson` | 2.1 MB | ✅ Excluded |
| `data/processed/candidates.csv` | 187 KB | ✅ Excluded |
| `data/processed/candidates.geojson` | 189 KB | ✅ Excluded |
| `data/processed/candidates_fuzzy_scored.csv` | 190 KB | ✅ Excluded |

### 3.2 Small Reference Files (Included)
| Path | Size | Provenance | Include? |
|------|------|------------|----------|
| `data/external/indian_ev_adoption_curve.csv` | 899 B | REAL | ✅ Yes |
| `data/external/vitap_zone_polygons.geojson` | 7.4 KB | DERIVED | ✅ Yes |
| `data/external/vitap_zone_polygons_PROVISIONAL.geojson` | 1.1 KB | DERIVED | ✅ Yes |
| `data/external/vitap_demo_transformers.geojson` | 1.7 KB | SYNTHETIC | ✅ Yes |

---

## 4. Missing File Resolution

### 4.1 `indian_charging_patterns.csv` - MISSING
| Aspect | Detail |
|--------|--------|
| **Referenced in** | `config/data_sources.yaml:157` |
| **File exists** | ❌ No |
| **Related file exists** | ✅ `indian_ev_adoption_curve.csv` (899 B) |
| **Provenance listed** | REAL (NITI Aayog / Government Reports) |
| **Used in code** | Not found in Python source (grep negative) |
| **Resolution** | Document as "reference dataset - not required for pipeline execution" in README |

### 4.2 Decision Required
- [ ] **Option A**: Document as optional external reference (add note to README, keep in data_sources.yaml with "optional" flag)
- [ ] **Option B**: Remove from data_sources.yaml since not used in pipeline
- [ ] **Option C**: Create placeholder/documentation file explaining the missing dataset

---

## 5. Data Provenance Verification

### 5.1 Provenance Labels in Config
| Source | Provenance | Status |
|--------|------------|--------|
| OpenStreetMap | REAL | ✅ Correct |
| OpenChargeMap | REAL | ✅ Correct |
| Open-Meteo Weather | REAL | ✅ Correct |
| Validated Parking | DERIVED | ✅ Correct |
| Campus Zones | DERIVED | ✅ Correct (manually digitized) |
| Indian EV Adoption | REAL | ✅ Correct (reference) |
| Synthetic Demand | SYNTHETIC | ✅ Correct |
| Synthetic Traffic | SYNTHETIC | ✅ Correct |
| Demo Transformers | SYNTHETIC | ✅ Correct |

### 5.2 Provisional Data Clearly Marked
| File | Provisional? | Note |
|------|--------------|------|
| `vitap_zone_polygons_PROVISIONAL.geojson` | ✅ Yes | Filename + config note |
| `vitap_zone_polygons.geojson` | ⚠️ Yes | Config: "NOT from official VIT-AP master plan" |
| `vitap_demo_transformers.geojson` | ✅ Yes | Filename + config: "SYNTHETIC" |
| Study area bbox/center | ✅ Yes | Config: `provisional: true` |

---

## 6. Pipeline Functionality Verification

### 6.1 Tests Status
```bash
# Run: pytest tests/ -v
# Expected: All existing tests pass
```
| Test Suite | Status |
|------------|--------|
| `tests/test_candidate_generator_provenance.py` | ⏳ Pending |
| `tests/test_fuzzy_policy.py` | ⏳ Pending |
| `tests/test_api_fallback.py` | ⏳ Pending |

### 6.2 Key Scripts Verified
| Script | Purpose | Status |
|--------|---------|--------|
| `run_pipeline.py` | Main CLI entry | ✅ Present |
| `enrich_demo_grid_costs.py` | Grid cost enrichment | ✅ Present |
| `demo_dashboard.py` | Streamlit dashboard | ✅ Present |
| `download_osm_data.py` | OSM fetcher | ✅ Present |

---

## 7. README Accuracy Check

### 7.1 Current README vs Reality
| Section | README Claims | Actual Status |
|---------|---------------|---------------|
| Pipeline Architecture | Complete ML-Fuzzy-GA | ✅ Implemented (not just TODO) |
| ML Pipeline | ⏳ Next | ✅ Implemented (XGBoost in src/ml/) |
| Fuzzy Logic | ⏳ Pending | ✅ Implemented (scikit-fuzzy in src/fuzzy/) |
| GA Optimization | ⏳ Pending | ✅ Implemented (DEAP in src/ga/) |
| Study Area Center | 16.5075, 80.4800 | ⚠️ Updated to 16.4940, 80.4993 (provisional) |
| Zones | 10 zones | ✅ 10 zones defined |
| Budget | ₹300 lakh (10 stations) | ⚠️ Target: 5 stations (GA config) |

### 7.2 Required README Updates
- [ ] Update study area coordinates to provisional values
- [ ] Update status badges (Data Layer ✅, ML ✅, Fuzzy ✅, GA ✅, Evaluation ⏳, Viz ✅)
- [ ] Document GA feasibility issue (5 stations may be infeasible)
- [ ] Document provisional zone polygons
- [ ] Document demo transformers (SYNTHETIC)
- [ ] Document missing `indian_charging_patterns.csv`
- [ ] Add license information

---

## 8. License Decision Required

| Option | Recommendation |
|--------|----------------|
| **MIT** | Permissive, widely used for academic projects |
| **Apache-2.0** | Permissive with patent clause |
| **GPL-3.0** | Copyleft - may restrict commercial use |
| **BSD-3-Clause** | Permissive, minimal |

**Current:** No LICENSE file present  
**Decision:** User to select

---

## 9. Final Pre-Commit Verification Commands

```bash
# 1. Verify no secrets in staged files
git diff --cached --name-only | xargs grep -l -i "api_key\|secret\|password" 2>/dev/null && echo "SECRETS FOUND" || echo "Clean"

# 2. Verify large files not staged
git status --porcelain | grep -E "data/(raw|processed|synthetic)/" && echo "LARGE FILES STAGED" || echo "Clean"

# 3. Run tests
pytest tests/ -v

# 4. Verify .gitignore
git check-ignore -v data/raw/osm/roads.geojson data/processed/candidates.csv src/data/candidate_generator.py

# 5. Check file count for initial commit
git status --porcelain | wc -l
```

---

## 10. Approval Gates

| Gate | Status | Approver |
|------|--------|----------|
| Security audit complete | ✅ | User |
| .gitignore validated | ✅ | User |
| Large datasets excluded | ✅ | User |
| Missing file documented | ⏳ | User |
| README updated | ⏳ | User |
| License selected | ⏳ | User |
| **READY FOR `git add`** | ❌ | **User approval required** |

---

## Summary for User

**✅ Ready:**
- No hardcoded secrets in config or source
- .gitignore cleaned and validated
- Large synthetic datasets (14GB traffic, 918MB demand) properly excluded
- Small reference files (zones, EV adoption, demo transformers) will be included
- Provisional data clearly marked in config and filenames

**⏳ Requires Decision:**
1. **Missing `indian_charging_patterns.csv`** - Document as optional reference or remove from config?
2. **License** - MIT / Apache-2.0 / BSD-3 / Other?
3. **README updates** - Approve updated status badges and feasibility notes

**Next Steps After Approval:**
1. Update README with current status
2. Add LICENSE file
3. User runs: `git add . && git commit -m "Initial commit: VIT-AP EV Charging ML-Fuzzy-GA pipeline"`
4. User runs: `git push origin main`

---

**Do not run `git add`, `git commit`, or `git push` until all approval gates are ✅**