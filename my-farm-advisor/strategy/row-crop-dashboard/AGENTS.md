# Local Instructions

## Purpose

This folder owns the **row-crop-dashboard** subskill for the Final Project.
It generates a grower-level interactive Plotly dashboard integrating field boundaries,
weather, NDVI, and soil health across all fields in a farm.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly
asks for a broader skill change. Do not change parent SKILL.md, sibling
strategy workflows, or root policy from a subskill task unless explicitly
requested.

## Read nearby docs first

Read GUIDE.md first for usage. For routing context read ../INDEX.md and
../../SKILL.md.

## Local validation

Run the dashboard generator for the prototype grower:

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
cd src
python generate_row_crop_dashboard.py \
  --grower ia-grower \
  --farm ia-grower-iowa
```

Verify the HTML dashboard appears under `derived/dashboards/`.

## Output conventions

- Self-contained HTML with vendored Plotly.js
- Dashboard at responsive layout, single-file output
- Naming: `{farm_slug}_row_crop_dashboard.html`
