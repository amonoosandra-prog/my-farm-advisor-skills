# Local Instructions

## Purpose

This folder owns the **field-season storyline** subskill for Assignment 3.
It generates multi-panel dashboards combining Sentinel-2 NDVI time-series,
daily weather, cumulative GDD, and CDL crop labels to tell the story of how
weather drove crop growth across the season.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly
asks for a broader skill change. Do not change parent SKILL.md, sibling
strategy workflows, or root policy from a subskill task unless explicitly
requested.

## Read nearby docs first

Read GUIDE.md first for usage. For routing context read ../INDEX.md and
../../SKILL.md.

## Local validation

Run the storyline generator for the prototype field:

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
python src/generate_storyline.py \
  --grower ia-grower \
  --farm ia-grower-iowa \
  --field osm-1360316064
```

Verify 4-panel PNG and NDVI CSV appear under `derived/reports/storylines/`.

## Output conventions

- Dashboards at 150 dpi, `tight_layout()`, `bbox_inches="tight"`
- Files closed with `plt.close()` — no memory leaks
- Naming: `{field_id}_storyline.png`, `{field_id}_ndvi_timeseries.csv`
