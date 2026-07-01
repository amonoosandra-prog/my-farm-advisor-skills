---
name: eda-field-level
description: Per-field exploratory analysis across weather, CDL/cropland data, and field boundary attributes.
version: 1.0.0
author: Assignment 2
tags: [eda, field-level, weather, cdl, boundaries, analysis]
---

# Workflow: eda-field-level

## Description

Generate static exploratory visualizations for a single field or compare across
growers. The subskill covers three categories — **weather**, **CDL/cropland
data**, and **field boundaries** — with 2 statistical plots and 1 correlation
analysis per category (9 per-field plots), plus 9 cross-grower comparison
plots.

## When to Use This Workflow

- **Understand field weather history**: temperature profiles, precipitation
  patterns, GDD accumulation
- **Analyze crop rotation**: timeline, diversity trends, crop persistence
- **Examine field geometry**: size distribution, shape complexity, links
  between size and weather variability
- **Compare across growers**: temperature/precip climates, crop mix, field
  sizes by state, GDD vs precip correlation

## Quick Start

### Per-field EDA (9 plots)

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
python src/field_level_eda.py \
  --grower il-grower \
  --farm il-grower-illinois \
  --field osm-1288035236
```

Output is written to:
```
growers/{grower}/farms/{farm}/fields/{field}/derived/reports/eda/
  01_weather_temp_profile.png
  02_weather_precip_distribution.png
  03_weather_gdd_precip_correlation.png
  04_cdl_rotation_timeline.png
  05_cdl_diversity_trend.png
  06_cdl_area_persistence.png
  07_boundary_size_distribution.png
  08_boundary_shape_complexity.png
  09_boundary_size_weather_cv.png
```

### Cross-grower comparison (9 plots)

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
python src/field_level_eda.py --cross-grower
```

Output is written to:
```
{data-pipeline}/eda-cross-grower/
  cross-01_grower_temp_boxplot.png
  cross-02_grower_crop_mix.png
  cross-03_grower_field_size_std.png
  cross-04_combined_grower_map.png
  cross-05_grower_corn_soy_share.png
  cross-06_gdd_precip_correlation_by_state.png
  cross-07_field_area_cdf.png
  cross-08_field_area_persistence.png
  cross-09_corn_soy_years_by_state.png
  cross-10_precip_boxplot.png
```

## Plot Catalog

### Weather

| # | Plot | Story | What to Look For |
|---|------|-------|------------------|
| 01 | Growing-season temperature profile + 7-day rolling average | *When does heat stress occur?* | Days above 30°C (heat-stress line); duration of warm periods |
| 02 | Monthly precipitation bars + cumulative line | *Is this field drought-prone?* | Dry vs wet months; total seasonal moisture; month-to-month variability |
| 03 | GDD vs precipitation scatter by year | *Do warm years correlate with wet years?* | Trend line direction; tight vs loose clustering; outlier years |

### CDL / Cropland Data

| # | Plot | Story | What to Look For |
|---|------|-------|------------------|
| 04 | Crop rotation timeline (color-coded bars) | *What is the rotation story?* | Crop sequences; mono-cropping vs diverse rotations; year-over-year changes |
| 05 | Unique crop count over years (farm-wide) | *Is the farm diversifying or intensifying?* | Upward (diversifying) vs flat/downward (specializing) trend |
| 06 | Field area vs consecutive same-crop years | *Do bigger fields stick to one crop longer?* | Positive slope means larger fields have more persistent cropping |

### Field Boundaries

| # | Plot | Story | What to Look For |
|---|------|-------|------------------|
| 07 | Field size histogram with target highlighted | *How big is this field relative to the farm?* | Position of target field in the distribution; smallest/largest fields |
| 08 | Shape complexity index (perimeter ratio) | *Is this field geometrically simple or complex?* | Values near 1 = near-circular; higher = more irregular (affects equipment efficiency) |
| 09 | Field area vs precipitation CV | *Does field size affect weather exposure?* | Larger fields may have higher precip variability (spatial scale effect) |

### Cross-Grower Comparisons

| # | Plot | Story |
|---|------|-------|
| Cross-01 | Growing-season temperature boxplot by state | *Which state has the warmest fields?* |
| Cross-02 | Crop mix composition (100% stacked bar) | *How does crop allocation differ by state?* |
| Cross-03 | Mean field size ± std dev by state | *Are fields larger in one state?* |
| Cross-04 | Combined geospatial map (30 fields) | *Where are all fields located across IL, IA, NE?* |
| Cross-05 | Corn vs soybean share by state | *Which state allocates more land to corn vs soy?* |
| Cross-06 | GDD vs precipitation scatter by state | *Do states with more heat accumulation also get more rain?* |
| Cross-07 | Field area cumulative distribution by state | *At what acreage does each state reach 50% of its fields?* |
| Cross-08 | Field area vs crop persistence by state | *Do larger fields stick to one crop longer?* |
| Cross-09 | Corn vs soybean years per field by state | *Which fields practice balanced rotation vs continuous cropping?* |

## Data Sources

| Plot | Data File | Path (relative to grower/farm/field) |
|------|-----------|---------------------------------------|
| 01-03 | Weather | `fields/{field}/weather/daily_weather.csv` |
| 04-06 | CDL crop rotation | `derived/tables/{farm}_crop_rotation.csv` |
| 04-06 | CDL per-year | `derived/tables/{farm}_{year}_cdl.csv` |
| 07-09 | Field boundaries | `boundary/field_boundaries.geojson` |
| Cross | All growers | Scans all growers under `growers/` |

## Dependencies

- Python: `pandas`, `geopandas`, `matplotlib`, `numpy`
- Pipeline runtime: `DATA_PIPELINE_DATA_ROOT` pointing to `~/my-farm-advisor-runtime`

## Output Conventions

- All plots at 150 dpi, `tight_layout()`, `bbox_inches="tight"`
- Files closed with `plt.close()` — no memory leaks
- Per-field plots follow `<num>_<category>_<description>.png` naming
- Cross-grower plots follow `cross-<num>_grower_<description>.png` naming
