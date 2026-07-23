# Field-Season Storylines

Generate per-year, 5-panel dashboards that combine Sentinel-2 NDVI time-series, daily weather, cumulative GDD, Standardized Precipitation Index (SPI-3), and CDL crop labels to show how weather events drove crop growth across each season.

## Assignment 3 — Field-Season Storyline Dashboards

**Skill / workflow:** `field-season-storylines`  
**Source:** `my-farm-advisor/strategy/field-season-storylines/src/generate_storyline.py`

### Inputs used from data-pipeline

| Source | Relative path under `${DATA_PIPELINE_DATA_ROOT}/data-pipeline` |
|--------|----------------------------------------------------------------|
| Sentinel-2 NDVI rasters | `growers/<grower>/farms/<farm>/fields/<field>/satellite/sentinel/YYYY/sentinel_YYYYMMDD/sentinel_YYYYMMDD_ndvi.tif` |
| Daily weather (NASA POWER) | `growers/<grower>/farms/<farm>/fields/<field>/weather/daily_weather.csv` |
| CDL crop labels | `derived/tables/<farm>_YYYY_cdl.csv` |

### Weather metrics calculated

- **Daily precipitation** (mm) and cumulative seasonal total
- **Daily mean, min, max temperature** (°C)
- **Cumulative Growing Degree Days** (GDD, base 10 °C)
- **SPI-3**: Standardized Precipitation Index at 3-month accumulation, fitted to a gamma distribution over the full climatological record (2000–2025)

### Dashboard panels

| # | Panel | Description |
|---|-------|-------------|
| 1 | **NDVI Time Series** | Sentinel-2 field-mean NDVI across the growing season |
| 2 | **Daily Precipitation** | Daily bars + cumulative line |
| 3 | **Daily Temperature & Extremes** | Min–max range fill, mean line, heat-stress reference at 30°C |
| 4 | **Cumulative GDD** | Growing degree day accumulation (base 10°C) |
| 5 | **SPI-3** | Standardized Precipitation Index with wet/dry fill and severity reference lines |

### Dashboard outputs

One PNG per year, saved to:

```
growers/<grower>/farms/<farm>/fields/<field>/derived/reports/storylines/<field_id>_storyline_<YYYY>.png
```

Example (prototype field `osm-1360316064`):

```
growers/ia-grower/farms/ia-grower-iowa/fields/osm-1360316064/derived/reports/storylines/osm-1360316064_storyline_2021.png
```

### How to rerun

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
source "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/activate"

python my-farm-advisor/strategy/field-season-storylines/src/generate_storyline.py \
  --grower ia-grower \
  --farm ia-grower-iowa \
  --field osm-1360316064
```

Replace `--grower`, `--farm`, and `--field` with any field that has Sentinel-2 scenes, NASA POWER weather, and CDL labels.

### Known data limitations

- Sentinel-2 scene counts vary by year and field (cloud cover gaps).
- NASA POWER weather is 0.5° gridded reanalysis — not station-level.
- CDL crop labels are annual and may miss within-field rotations or cover crops.
- NDVI zonal statistics use the field boundary as-is; headlands or in-field variation are averaged.
- GDD base temperature is fixed at 10 °C; crop-specific bases are not yet applied.
