# Local Instructions

## Purpose

This folder owns the workshop-headlands subskill for creating headlands-ring
geometries with GeoPackage output from pipeline field boundaries.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly
asks for a broader skill change.

## Read nearby docs first

Read `GUIDE.md` first. For routing context read `../INDEX.md` and `../../SKILL.md`.

## Local validation

Run the script against a single field from the runtime data pipeline:

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
python src/create_headlands_ring.py \
  --grower il-grower \
  --farm il-grower-illinois \
  --field osm-1288035236 \
  --headlands-width 21
```

Verify GeoPackage output appears under `derived/headlands/`.
