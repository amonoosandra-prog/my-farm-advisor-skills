# Grower Web Map Subskill

## Purpose

Generates a lightweight, interactive HTML web map for each grower/farm in the
data pipeline. The map shows field polygon boundaries on an OpenStreetMap
basemap with clickable popups and a sidebar field list for navigation.

## Usage

```bash
export DATA_PIPELINE_DATA_ROOT=/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/grower_web_map/generate_grower_map.py
```

## Output

Each farm gets a self-contained HTML file at:

```
growers/<grower-slug>/farms/<farm-slug>/derived/reports/<grower>_<farm>_grower_map.html
```

The map is a single HTML file that loads Leaflet.js and OpenStreetMap tiles from
CDN. No external dependencies beyond a web browser with internet access.

## Requirements

- Leaflet.js (loaded from unpkg CDN at runtime in the browser)
- OpenStreetMap tile layer (loaded from tile.openstreetmap.org)
