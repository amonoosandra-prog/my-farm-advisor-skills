---
name: row-crop-dashboard
description: Generate a grower-level Row Crop Intelligence Dashboard integrating field boundaries, weather, NDVI, and soil health across all farm fields.
version: 1.0.0
author: Final Project
tags: [strategy, dashboard, soil, weather, ndvi, plotly, geospatial]
---

# Workflow: row-crop-dashboard

## Description

Generate a self-contained interactive HTML dashboard for a grower that brings
together all field-level data to tell the story of how weather, soil, and
management interact to influence crop health across the farm.

### Dashboard Sections

| # | Section | Content |
|---|---------|---------|
| 1 | **KPI Header** | Total fields, total acreage, avg NDVI, avg rainfall, avg Soil Health Score |
| 2 | **NDVI Comparison** | Grouped bar chart comparing corn vs soybean NDVI per field, sorted by performance |
| 3 | **Soil vs NDVI** | Scatter plot of organic matter vs NDVI, colored by drainage class |
| 4 | **Geospatial Map** | Field boundaries colored by Soil Health Score with satellite basemap |
| 5 | **Weather Analysis** | GDD cumulative curves and total seasonal precipitation per field per year |
| 6 | **Soil Health Metric** | Composite score with per-component breakdown per field |
| 7 | **Interpretation** | Auto-generated analytical text highlighting key patterns |

## Quick Start

### Generate dashboard for Iowa grower

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
source "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/activate"

python src/generate_row_crop_dashboard.py \
  --grower ia-grower \
  --farm ia-grower-iowa
```

### Output

```
growers/ia-grower/farms/ia-grower-iowa/derived/dashboards/
  ia-grower-iowa_row_crop_dashboard.html   ← Interactive dashboard (open in browser)
```

## Soil Health Score Formula

The Soil Health Score is a weighted composite of four factors:

| Component | Weight | Scale | Description |
|-----------|--------|-------|-------------|
| Organic Matter | 35% | 0–100 | Linear from 0% to 8% OM |
| pH Rating | 25% | 0–100 | Inverse distance from optimum 6.5 |
| Drainage Class | 20% | 0–100 | Well-drained=100 → Very poorly=20 |
| CEC Rating | 20% | 0–100 | Linear from 0 to 40 meq/100g |

## Data Sources

| Input | File | Description |
|-------|------|-------------|
| Field boundaries | `boundary/field_boundaries.geojson` | All field polygons for the farm |
| Daily weather | `fields/<id>/weather/daily_weather.csv` | NASA POWER: T2M, T2M_MIN, T2M_MAX, PRECTOTCORR |
| NDVI summaries | `fields/<id>/derived/summaries/ndvi_card_summary.json` | Per-crop mean NDVI |
| SSURGO soil | `fields/<id>/soil/ssurgo_summary.csv` | OM%, pH, CEC, drainage class, texture |
| Events | `fields/<id>/derived/reports/storylines/<id>_events_*.json` | Notable weather/NDVI events |

## Dependencies

- Python: `plotly`, `geopandas`, `pandas`, `numpy`, `pillow`, `requests`
- Pipeline runtime: `DATA_PIPELINE_DATA_ROOT` pointing to `~/my-farm-advisor-runtime`

## Output Conventions

- Self-contained HTML dashboard, no external server needed
- Plotly.js vendored locally (cached in `~/.cache/my-farm-advisor/plotly/`)
- Satellite basemap cached in `~/.cache/my-farm-advisor/tiles/`
- Responsive layout adapts to desktop and mobile
