---
title: EV Charging Site Planner
emoji: ⚡
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Plan EV chargers for any campus and budget (ML, fuzzy, GA)
---

# EV Charging Site Planner (API)

This Space is the backend for the live planner on the project's web dashboard.
You send it a campus or area name and a budget. It runs this pipeline:

1. OpenStreetMap area and features
2. Candidate sites
3. Random Forest demand model
4. 60-rule fuzzy suitability score
5. Genetic-algorithm placement
6. Exhaustive check of the GA's plan

It returns the dashboard data for that plan.

- API docs: `/api/docs`
- Health check: `/api/health`

**Fast mode.** Runs use these settings:

- 3 GA runs per station count
- Demand history sampled on 1 day in 4
- 100 trees
- Up to 8 stations
- Areas up to 12 km²

**Data sources:** OpenStreetMap, Open-Meteo and OpenChargeMap.

**Optional secret:** `OCM_API_KEY`, a free OpenChargeMap key used to show nearby public chargers.
