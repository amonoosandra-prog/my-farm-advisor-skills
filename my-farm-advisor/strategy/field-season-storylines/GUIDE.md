---
name: field-season-storylines
description: Generate multi-panel dashboards showing how weather drove crop growth across the season for a single field.
version: 1.0.0
author: Assignment 3
tags: [strategy, weather, ndvi, storyline, dashboard, sentinel-2]
---

# Workflow: field-season-storylines

## Description

Generate a single multi-panel dashboard per field combining:
- **NDVI scene values** (Sentinel-2) plotted across the calendar year
- **Daily weather** (temperature, precipitation) in a connected timeline
- **Cumulative GDD accumulation**
- **Crop type labels** per year (from CDL tables)
- **All five years (2021–2025) overlaid** to show year-to-year variability

The result is a self-contained visual story of how weather drove crop growth.

## Prototype Selection

For Assignment 3, a single field-year was selected to prototype the dashboard:

| Attribute | Value |
|-----------|-------|
| **Field ID** | `osm-1360316064` |
| **Grower** | `ia-grower` (Iowa) |
| **Farm** | `ia-grower-iowa` |
| **Prototype Year** | **2022** |
| **CDL Crop** | **Corn** (100.0% purity, 351/351 pixels) |
| **Rotation Context** | Soy (2021) → **Corn (2022)** → Soy (2023) → Corn (2024) → Soy (2025) |
| **NDVI Scenes (2022)** | **9** Sentinel-2 acquisitions |
| **NDVI Scenes (Total)** | **42** across 2021–2025 |
| **Weather Coverage** | 1,826 daily rows, **0 gaps** |
| **Field Area** | 79.0 acres |
| **Shape Complexity** | 1.195 (simple, regular) |
| **Location** | 43.27°N, 94.28°W — North-central Iowa, Corn Belt |

### Why this field-year?

- **Cleanest CDL in the dataset** — 100% Corn, no mixed pixels
- **Textbook corn-soy rotation** — clear year-to-year contrast
- **Dense NDVI coverage** — 9 scenes in 2022; 42 total across 5 years
- **Complete weather** — no missing values in any variable
- **Simple geometry** — minimal boundary artifacts in NDVI extraction
- **Corn year chosen** — corn exhibits a more pronounced NDVI phenological curve (lower early-season, higher peak, sharper senescence) than soybeans, making the weather-NDVI storyline more visually and analytically compelling

## Quick Start

### Generate dashboard for prototype field

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
python src/generate_storyline.py \
  --grower ia-grower \
  --farm ia-grower-iowa \
  --field osm-1360316064
```

### Output

```
growers/ia-grower/farms/ia-grower-iowa/fields/osm-1360316064/derived/reports/storylines/
  osm-1360316064_storyline.png          ← 4-panel dashboard
  osm-1360316064_ndvi_timeseries.csv    ← Per-scene NDVI values
```

## Dashboard Panels

| # | Panel | Story | What to Look For |
|---|-------|-------|------------------|
| 1 | **NDVI Time Series** | *How did vegetation health change through the season?* | Peak timing, senescence rate, year-to-year peak differences |
| 2 | **Daily Temperature** | *When did heat stress occur?* | Days above 30°C (red dashed line), min-max range |
| 3 | **Precipitation** | *Was the season wet or dry?* | Daily bars + cumulative line; drought vs excess rain |
| 4 | **Cumulative GDD** | *Did heat accumulation match crop needs?* | S-curve shape; corn needs ~1200–1400 GDD for maturity |

## Data Sources

| Input | File | Description |
|-------|------|-------------|
| Sentinel-2 NDVI | `satellite/sentinel/YYYY/sentinel_YYYYMMDD/sentinel_YYYYMMDD_ndvi.tif` | Per-scene NDVI rasters |
| Field boundary | `boundary/field_boundary.geojson` | Polygon for masking/zonal stats |
| Daily weather | `weather/daily_weather.csv` | NASA POWER: T2M, T2M_MIN, T2M_MAX, PRECTOTCORR |
| CDL crop labels | `derived/tables/{farm}_YYYY_cdl.csv` | Dominant crop per field per year |

## Dependencies

- Python: `rasterio`, `geopandas`, `matplotlib`, `numpy`, `pandas`
- Pipeline runtime: `DATA_PIPELINE_DATA_ROOT` pointing to `~/my-farm-advisor-runtime`

## Output Conventions

- Dashboards at 150 dpi, `tight_layout()`, `bbox_inches="tight"`
- Files closed with `plt.close()` — no memory leaks
- Naming: `{field_id}_storyline.png`, `{field_id}_ndvi_timeseries.csv`
