---
name: field-season-storylines
description: Generate multi-panel dashboards showing how weather drove crop growth across the season for a single field.
version: 1.0.0
author: Assignment 3
tags: [strategy, weather, ndvi, storyline, dashboard, sentinel-2]
---

# Skill: field-season-storylines

## When to Use

- Understand the relationship between **weather** and **crop growth** for a specific field
- Visualize **NDVI time-series** alongside temperature, precipitation, and GDD
- Compare **year-to-year variability** (2021–2025) on a single dashboard
- Tell a data-driven story of **how the growing season unfolded**

## What It Produces

- **4-panel storyline dashboard** per field:
  1. NDVI time series (Sentinel-2 scenes, all years overlaid)
  2. Daily temperature with heat-stress highlights
  3. Daily precipitation + cumulative
  4. Cumulative GDD accumulation
- **NDVI timeseries CSV** with per-scene field-mean values

## Entrypoint

Open `src/generate_storyline.py` or read `GUIDE.md` for full usage.

## Inputs

| Input | Path (relative to grower/farm/field) |
|-------|---------------------------------------|
| Sentinel-2 NDVI rasters | `satellite/sentinel/YYYY/sentinel_YYYYMMDD/sentinel_YYYYMMDD_ndvi.tif` |
| Field boundary | `boundary/field_boundary.geojson` |
| Daily weather | `weather/daily_weather.csv` |
| CDL crop labels | `derived/tables/{farm}_YYYY_cdl.csv` |

## Dependencies

- Python: `rasterio`, `geopandas`, `matplotlib`, `numpy`, `pandas`
- Pipeline runtime: `DATA_PIPELINE_DATA_ROOT` pointing to `~/my-farm-advisor-runtime`
