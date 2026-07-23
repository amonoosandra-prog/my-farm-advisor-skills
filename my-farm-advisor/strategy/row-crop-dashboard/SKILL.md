---
name: row-crop-dashboard
description: Generate a grower-level Row Crop Intelligence Dashboard integrating field boundaries, weather, NDVI, and soil health across all farm fields.
version: 1.0.0
author: Final Project
tags: [strategy, dashboard, soil, weather, ndvi, plotly, geospatial]
---

# Skill: row-crop-dashboard

## When to Use

- View a **grower-level summary** of all fields in one dashboard
- Compare **NDVI performance** across fields by crop type
- Explore **soil health** patterns (organic matter, pH, drainage) across the farm
- Understand **weather impacts** on crop health at farm scale
- Identify **high-priority fields** for management intervention

## What It Produces

A self-contained **interactive HTML dashboard** with:

1. **KPI header** — total fields, acreage, average NDVI, rainfall, soil health score
2. **Exploratory charts** — NDVI comparison across fields, soil OM vs NDVI correlation
3. **Geospatial map** — field boundaries colored by Soil Health Score
4. **Weather analysis** — GDD and rainfall comparisons per field
5. **Soil Health Metric** — composite score with component breakdown
6. **Interpretation** — auto-generated analytical text

## Entrypoint

Open `src/generate_row_crop_dashboard.py` or read `GUIDE.md` for full usage.

## Inputs

| Input | Path (relative to grower/farm root) |
|-------|--------------------------------------|
| Field boundaries | `boundary/field_boundaries.geojson` |
| Per-field weather | `fields/<id>/weather/daily_weather.csv` |
| Per-field NDVI cards | `fields/<id>/derived/summaries/ndvi_card_summary.json` |
| Per-field SSURGO soil | `fields/<id>/soil/ssurgo_summary.csv` |
| Farm metadata | `farm.json` |

## Dependencies

- Python: `plotly`, `geopandas`, `pandas`, `numpy`, `pillow`, `requests`
- Pipeline runtime: `DATA_PIPELINE_DATA_ROOT` pointing to `~/my-farm-advisor-runtime`
