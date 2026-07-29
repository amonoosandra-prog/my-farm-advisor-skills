# Supplementary Dashboard Information

## Project Overview

**Project:** Row Crop Intelligence Data Dashboard
**Course:** Final Project
**Author:** My Farm Advisor
**Date:** July 2026
**Repository:** `my-farm-advisor-skills`
**Skill:** `my-farm-advisor/strategy/row-crop-dashboard/`

The Row Crop Intelligence Data Dashboard is a grower-level analytical tool
that integrates field boundaries, weather data, NDVI time series, and soil
properties into a single interactive HTML dashboard. It is designed to help
growers understand field-level variability across their farm and make
data-informed management decisions.

### Dashboard Sections

| Section | Type | Description |
|---------|------|-------------|
| **KPI Summary** | Metrics | Total fields, acreage, average NDVI, rainfall, soil health score |
| **NDVI Comparison** | Exploratory (1) | Grouped bar chart comparing corn vs soybean NDVI per field |
| **Soil vs NDVI** | Exploratory (2) | Scatter plot of organic matter vs NDVI colored by drainage class |
| **Geospatial Map** | Geospatial | Field boundaries colored by Soil Health Score with satellite basemap |
| **Weather Analysis** | Weather/Climate | GDD cumulative curves and daily precipitation per field |
| **Soil Health Metric** | Sustainability | Composite soil health score with component breakdown per field |
| **Interpretation** | Context | Auto-generated analytical insights and usage guidance |

## Dataset Description

The dashboard uses data from the My Farm Advisor data pipeline runtime,
located at `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/`.

### Primary Data Sources

| Dataset | Source | Description | Fields Used |
|---------|--------|-------------|-------------|
| Field Boundaries | USDA/NASS | GeoJSON polygons of field perimeters | `field_id`, `area_acres`, geometry |
| Daily Weather | NASA POWER | Gridded daily meteorological data (2021–2025) | `T2M_MAX`, `T2M_MIN`, `PRECTOTCORR`, `date` |
| NDVI Card Summaries | Sentinel-2 / Landsat | Per-crop mean NDVI from satellite imagery | `mean_ndvi`, `years`, `crop_name` |
| SSURGO Soil | USDA/NRCS | Soil survey geographic database | `avg_om_pct`, `avg_ph`, `avg_cec`, `drainage_class` |
| Farm Metadata | Pipeline | Farm-level descriptive metadata | `display_name`, `farm_slug` |

### Study Area

| Attribute | Value |
|-----------|-------|
| Grower | `ia-grower` (Iowa) |
| Farm | `ia-grower-iowa` |
| Number of Fields | 10 |
| Total Acreage | 791.1 acres |
| Data Span | 2021–2025 |
| Primary Crops | Corn (rotation), Soybeans (rotation) |
| Location | North-central Iowa, US Corn Belt |

### Key Variables

**NDVI (Normalized Difference Vegetation Index):**
- Derived from Sentinel-2 and Landsat satellite imagery
- Per-field mean values calculated via zonal statistics
- Separated by crop type (corn vs soybean)
- Range: 0 (bare soil) to 1 (dense vegetation)
- Available as mean NDVI and 95th percentile peak NDVI

**Weather Variables:**
- T2M_MAX: Daily maximum temperature at 2m (°C)
- T2M_MIN: Daily minimum temperature at 2m (°C)
- PRECTOTCORR: Corrected precipitation (mm/day)
- GDD: Growing Degree Days (base 10°C)
- SPI-3: Standardized Precipitation Index (3-month)

**Soil Properties:**
- avg_om_pct: Average organic matter percentage
- avg_ph: Average soil pH
- avg_cec: Average cation exchange capacity (meq/100g)
- drainage_class: Soil drainage classification
- dominant_soil: Dominant soil series name

## Dashboard Explanation

### Technology Stack

- **Plotly.js** (v2.35.2): Interactive chart rendering
- **Python**: Data processing and figure generation
- **Geopandas / Shapely**: Geospatial data processing
- **Pillow**: Satellite basemap tile stitching
- **Self-contained HTML**: No server or internet required

### Interactivity Features

- **Tab navigation**: Switch between dashboard sections
- **Hover tooltips**: Detailed data on hover for all charts
- **Zoom/Pan**: Interactive exploration in the geospatial map
- **Responsive layout**: Adapts to desktop and mobile screens

### Reusability

The dashboard generator script (`generate_row_crop_dashboard.py`) is designed
to work at the grower level. It can be run for any grower/farm in the
data pipeline by changing the `--grower` and `--farm` arguments. The script
automatically discovers all fields, their data, and generates the dashboard
without hardcoded field references.

## Analytical Interpretation

### Key Findings from the Iowa Farm

1. **NDVI Variability**: There is significant variation in NDVI across fields,
   with the top-performing field showing up to 30% higher NDVI than the
   lowest-performing field. This suggests opportunities for targeted
   management interventions.

2. **Soil Organic Matter Effect**: Fields with higher organic matter content
   (≥5%) consistently show higher NDVI values. This correlation highlights
   the importance of soil health for crop productivity.

3. **Drainage Impact**: Poorly drained fields tend to have lower average NDVI
   values, particularly in wetter years. This suggests that drainage
   improvements could benefit yields in affected fields.

4. **Soil Health Scores**: The composite Soil Health Score ranges from
   approximately 35 to 75 across the farm, indicating substantial soil
   quality variability that could inform precision management zones.

5. **Weather Context**: GDD accumulation patterns are consistent across
   fields within the same year, suggesting that observed NDVI differences
   are driven more by soil and management factors than by weather variation
   across the farm.

### How to Use the Dashboard

- **Identify underperforming fields**: Sort fields by NDVI or Soil Health Score
- **Diagnose causes**: Use the Soil vs NDVI scatter plot to see if soil
  quality explains performance gaps
- **Plan interventions**: Fields with low OM may benefit from cover crops;
  low pH fields may need lime; poorly drained fields may need tile drainage
- **Monitor trends**: Regenerate the dashboard after each season to track
  changes in field performance

## AI Usage Documentation

### Tools Used

- **AI Assistant (opencode)**: Used for code generation, testing, and
  debugging assistance throughout the development process.

### How AI Was Used

1. **Code Generation**: The dashboard generator script was written with AI
   assistance, including the Plotly figure builders, HTML template, and
   data loading functions.

2. **Debugging**: AI helped identify and fix issues with data path
   resolution, GeoJSON parsing, and Plotly figure configuration.

3. **Documentation**: README files, skill definitions, and this
   supplementary document were created with AI assistance.

4. **Testing**: AI helped create test scenarios and verify the dashboard
   output.

### Human Oversight

All AI-generated code was reviewed and tested by the human developer.
The dashboard design, analytical approach, and interpretation were
developed by the human developer. AI was used as a productivity tool
to accelerate implementation.
