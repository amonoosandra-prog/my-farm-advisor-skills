## Summary
This PR introduces the `eda-field-level` subskill for **Assignment 2**, delivering
a comprehensive exploratory data analysis (EDA) pipeline across **30 agricultural
fields** in **3 U.S. Corn Belt states** (Illinois, Iowa, Nebraska).

## What Was Built

### Architecture
```mermaid
graph TD
    A[eda-field-level<br/>subskill] --> B[Per-Field EDA<br/>30 fields × 11 plots]
    A --> C[Cross-Grower<br/>9 comparison plots]
    A --> D[HTML Report<br/>growers/EDA/Assignment-2/]
    A --> E[Combined Map<br/>IL + IA + NE]

    B --> B1[Weather: temp profile,<br/>precip, GDD correlation,<br/>year comparison]
    B --> B2[CDL: rotation timeline,<br/>diversity trend,<br/>area persistence]
    B --> B3[Boundaries: size histogram,<br/>shape complexity,<br/>size-weather CV,<br/>geospatial map]

    C --> C01[cross-01: temp boxplot]
    C --> C02[cross-02: crop mix]
    C --> C03[cross-03: field size ± std]
    C --> C04[cross-04: combined map]
    C --> C05[cross-05: corn vs soy]
    C --> C06[cross-06: GDD-precip scatter]
    C --> C07[cross-07: area CDF]
    C --> C08[cross-08: area vs persistence]
    C --> C10[cross-10: precip boxplot]
```

### Data Flow
```mermaid
flowchart LR
    A[NASA POWER<br/>daily weather<br/>2021–2025] --> E[field_level_eda.py]
    B[USDA NASS CDL<br/>crop classification<br/>2021–2025] --> E
    C[OSM Boundaries<br/>GeoJSON polygons] --> E
    E --> F[330 per-field PNGs<br/>weather/ cdl/ boundaries/]
    E --> G[9 cross-grower PNGs<br/>cross-01..cross-10]
    E --> H[combined_map.png<br/>hulls + thick edges]
    E --> I[eda_report_assignment_2.html<br/>self-contained summary]
```

### Comparison Levels
```mermaid
graph LR
    A[Field-Level] --> A1[Single field<br/>time series]
    B[Field-Year] --> B1[Year-over-year<br/>within field]
    C[Grower-Level] --> C1[All fields<br/>within one state]
    D[Across-Grower] --> D1[IL vs IA vs NE<br/>cross-state]
```

### Output Structure
```
growers/EDA/Assignment-2/plots/
├── boundaries/          ← 120 boundary PNGs
├── cdl/                ← 91 CDL PNGs
├── weather/            ← 120 weather PNGs
├── combined_map.png    ← Enhanced IL+IA+NE map
└── cross-grower/
    ├── cross-01_grower_temp_boxplot.png
    ├── cross-02_grower_crop_mix.png
    ├── cross-03_grower_field_size_std.png
    ├── cross-04_combined_grower_map.png
    ├── cross-05_grower_corn_soy_share.png
    ├── cross-06_gdd_precip_correlation_by_state.png
    ├── cross-07_field_area_cdf.png
    ├── cross-08_field_area_persistence.png
    └── cross-10_precip_boxplot.png
```

## Files Changed
| File | Change |
|------|--------|
| `my-farm-advisor/eda/eda-field-level/` | **New subskill** — AGENTS.md, GUIDE.md, src/field_level_eda.py, src/generate_enhanced_map.py |
| `my-farm-advisor/eda/INDEX.md` | Updated to reference new subskill |
| `my-farm-advisor/data-pipeline/src/scripts/ingest/bootstrap_farm_from_county.py` | Fixed Overpass API User-Agent |

## Key Features
- **4 comparison levels**: field, field-year, grower, across-grower
- **2 statistical + 1 correlation per category**: weather, CDL, boundaries
- **No soil analysis**: explicitly excluded per assignment requirements
- **Self-contained report**: HTML with embedded CSS, relative image paths
- **Clean data organization**: all outputs under unified `EDA/Assignment-2/` folder

## Testing
- Full batch executed: 30 fields × 11 plots = 330 per-field outputs
- Cross-grower suite: 9 comparison plots verified
- Report validated: all image paths resolved, HTML well-formed
