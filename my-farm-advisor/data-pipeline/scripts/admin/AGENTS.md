# Admin: Grower Web Map

## Purpose

Lightweight interactive HTML map generator for a single grower's fields.
Uses only Python standard library — no geopandas, pandas, or other
dependencies beyond Python 3.

## Usage

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
./scripts/admin/gen_grower_webmap.py <grower-slug>
```

### Example

```bash
./scripts/admin/gen_grower_webmap.py il-grower
./scripts/admin/gen_grower_webmap.py ia-grower
```

## Output

Generated HTML file at:

```
growers/<grower-slug>/farms/<farm-slug>/derived/reports/<grower>_<farm>_grower_map.html
```

Open in a browser. The map loads Leaflet.js and OpenStreetMap tiles from CDN.

## Requirements

- Python 3.8+
- `DATA_PIPELINE_DATA_ROOT` pointing to a seeded data pipeline runtime
- A web browser (to view the map)

## How it works

1. Reads `field_boundaries.geojson` directly with `json.load()`
2. Embeds coordinates inline as GeoJSON
3. Generates a self-contained HTML file using Leaflet.js from CDN
4. Each field is a colored polygon with click-to-zoom and popup metadata
