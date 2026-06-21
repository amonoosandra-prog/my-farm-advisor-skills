# Grower Web Map

## Purpose

Lightweight interactive HTML map generator for a single grower's fields. Each
map is a self-contained HTML file that renders field polygon boundaries from the
data pipeline on an OpenStreetMap basemap with clickable popups and a sidebar
field list.

## Scripts

- `gen_grower_webmap.py` — reads `field_boundaries.geojson` from each
  grower/farm and writes a `<5 KB` HTML map to `derived/reports/`.

## Usage

```bash
export DATA_PIPELINE_DATA_ROOT=/path/to/my-farm-advisor-runtime
./gen_grower_webmap.py
```

## Output

```
growers/<grower>/farms/<farm>/derived/reports/<grower>_<farm>_grower_map.html
```

## Dependencies

- **Python:** stdlib only (json, os, pathlib)
- **Browser runtime:** Leaflet.js + OpenStreetMap tiles (loaded from CDN)
