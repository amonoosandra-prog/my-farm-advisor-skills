# Row Crop Intelligence Data Dashboard

A grower-level analytical dashboard that integrates field boundaries,
weather data, NDVI time series, and soil properties to help growers
understand field-level variability across their farm.

## How to Run the Skill

```bash
# 1. Set environment
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
source "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/activate"

# 2. Run the dashboard generator
cd my-farm-advisor/strategy/row-crop-dashboard
python src/generate_row_crop_dashboard.py \
  --grower ia-grower \
  --farm ia-grower-iowa
```

## Where to Find the Dashboard in the Runtime Dataset

After generation, the dashboard HTML file is at:

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/derived/dashboards/<farm>_row_crop_dashboard.html
```

Example for the Iowa grower:

```
~/my-farm-advisor-runtime/data-pipeline/growers/ia-grower/farms/ia-grower-iowa/derived/dashboards/ia-grower-iowa_row_crop_dashboard.html
```

Open this file directly in any modern web browser. No server is required.

## Dependencies

- **Python packages**: `plotly`, `geopandas`, `pandas`, `numpy`, `pillow`, `requests`
- **Runtime**: The My Farm Advisor data pipeline must be installed and have a grower's farm data available under `DATA_PIPELINE_DATA_ROOT`.
- **Plotly.js**: Automatically downloaded and cached on first run (vendored locally).
- **Satellite basemap**: Automatically downloaded and cached on first run (optional, dashboard works without it).

### Installing missing dependencies

```bash
source "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/activate"
pip install plotly geopandas pandas numpy pillow requests
```

## Dashboard Sections

1. **KPI Summary** — Total fields, acreage, average NDVI, rainfall, and Soil Health Score
2. **NDVI Performance** — Cross-field comparison of corn and soybean NDVI
3. **Soil & NDVI Correlation** — Organic matter vs NDVI scatter plot
4. **Geospatial Map** — Interactive field map colored by Soil Health Score
5. **Weather Analysis** — GDD accumulation and seasonal precipitation comparison
6. **Soil Health Metric** — Composite score breakdown per field
7. **Interpretation** — Auto-generated analytical text

## Notes

- The dashboard works at the **grower/farm level** and analyzes all fields for that farm
- Data must exist for each field (weather CSV, NDVI card summary, SSURGO summary)
- Fields missing certain data are gracefully skipped with warnings
