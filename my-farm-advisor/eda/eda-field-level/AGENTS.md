# Local Instructions

## Purpose

This folder owns the field-level EDA subskill for per-field exploratory analysis
across weather, CDL/cropland data, and field boundary attributes.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly
asks for a broader skill change. Do not change parent `INDEX.md`, sibling EDA
workflows, or root policy from a subskill task unless explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first. For routing context read `../INDEX.md` and
`../../SKILL.md`.

## Local validation

Run the per-field EDA for one field:

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
python src/field_level_eda.py \
  --grower il-grower --farm il-grower-illinois --field osm-1288035236
```

Verify 9 PNG files appear under `derived/reports/eda/`.

Run cross-grower comparison:

```bash
python src/field_level_eda.py --cross-grower
```

Verify 3 PNG files appear under `eda-cross-grower/` in the runtime base.
