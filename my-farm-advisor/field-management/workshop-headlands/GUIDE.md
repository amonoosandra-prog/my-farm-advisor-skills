---
name: workshop-headlands
description: Create headlands-ring geometries with GeoPackage output from pipeline field boundaries.
version: 1.0.0
author: Custom
tags: [geospatial, headlands, field-operations, workshop]
---

# Workshop: Headlands Ring Creation

## Description

Reads a single field boundary from the data pipeline runtime, computes a
headlands ring at a configurable width (default 21 m), and writes two GeoPackage
files: `field_boundary.gpkg` with area attributes and `headlands_ring.gpkg` with
headlands area attributes.

Uses the existing `headlands-ring` skill for core geometry operations.

## Quick Start

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
python src/create_headlands_ring.py \
  --grower il-grower \
  --farm il-grower-illinois \
  --field osm-1288035236 \
  --headlands-width 21
```

## Output

```
growers/{grower}/farms/{farm}/fields/{field}/derived/headlands/
  field_boundary.gpkg   -- field polygon with meters_squared + acres
  headlands_ring.gpkg   -- headlands polygon with meters_squared + acres
```

## Dependencies

- geopandas
- Existing `headlands-ring` skill (sibling under `field-management/`)
