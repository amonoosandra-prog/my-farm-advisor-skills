# Data Pipeline Runtime Setup

This subskill ships the scripts that build the data-pipeline reports and
posters. Each runtime host creates its own virtualenv inside the data tree on
first run; the scripts auto-bootstrap that environment before continuing.

## Quick start

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/ingest/bootstrap_farm_from_county.py \
  --state-fips 17 \
  --county-name DeKalb \
  --count 5 \
  --seed 77 \
  --grower-slug il-dekalb-grower \
  --farm-slug dekalb-demo-farm \
  --farm-name "DeKalb Demo Farm" \
  --run-pipeline \
  --force
```

For a first run that also initializes shared data and seeds fields for a grower in a state, use the installer directly from the checkout:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh \
  --prepare-shared-data \
  --seed-grower-slug acme-grower \
  --seed-state Illinois \
  --seed-field-count 12 \
  --seed-farm-name "Acme Illinois Farm"
```

That command installs the runtime source and venv, builds shared geoadmin L0/L1/L2 payloads, shared NASA POWER county weather, GDD, annual corn RM, annual soybean MG, five-year FIPS-average corn RM and soybean MG datasets, and last-five-year CONUS CDL rasters. It then selects a top-crop county in the requested state, samples the requested number of OSM fields, and runs the full farm pipeline so derived tables, field weather, soil outputs, CDL history, satellite/NDVI products, reports, cards, posters, and HTML/Markdown farm reports are generated automatically.

If the runtime is already installed, run the equivalent from the runtime source copy:

```bash
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/farm_dashboard.py create \
  --prepare-shared-data \
  --grower-slug acme-grower \
  --state Illinois \
  --field-count 12 \
  --farm-name "Acme Illinois Farm"
```

`DATA_PIPELINE_DATA_ROOT` is required. Set it to an absolute writable path outside the skill checkout before running the installer or any pipeline entrypoint. There is no implicit fallback to a platform workspace path or to a checkout-local `data/` directory.

The installer creates and refreshes the runtime tree under:

- runtime base: `${DATA_PIPELINE_DATA_ROOT}/data-pipeline`
- runtime source copy: `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src`
- default runtime venv: `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv`

Generated outputs, manifests, reports, logs, and downloaded payloads belong under the runtime base, for example `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers` and `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/shared`. The committed checkout remains the source for installer scripts and baseline `src/` files, but runtime execution happens from the copied source.

Farm weather now uses NASA POWER's public S3 Zarr stores by default at actual field centroids. The default farm weather controls are `--weather-backend zarr`, `--weather-start-year 2021`, `--weather-end-year 2025`, and `--weather-time-standard lst`. The output path and CSV schema stay compatible with existing reports:

```text
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/derived/tables/<farm>_weather_2021_2025.csv
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/fields/<field>/weather/daily_weather.csv
```

Run or override those defaults from the runtime source copy:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/run_farm_pipeline.py \
  --grower-slug il-dekalb-grower \
  --farm-slug dekalb-demo-farm \
  --farm-name "DeKalb Demo Farm" \
  --weather-backend zarr \
  --weather-start-year 2021 \
  --weather-end-year 2025 \
  --weather-time-standard lst
```

Use `--weather-backend api` only when explicitly debugging the legacy NASA POWER point API path for small field sets.

Shared county weather for maturity-by-FIPS uses NASA POWER's public S3 Zarr stores by default instead of issuing one `power.larc.nasa.gov` point API request per county grid cell. This avoids API rate-limit failures for L2 geoadmin scopes while preserving the existing output path and schema:

```text
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/shared/weather/nasa-power/<year>/daily_weather_by_fips.parquet
```

For the full shared lower48 baseline, initialize the runtime with multi-year county weather, GDD, corn RM, soybean MG, corn/soybean five-year FIPS averages, and CDL raster outputs. The default shared maturity range is 2021-2025 to match the farm weather and CDL helper defaults; CDL initialization fetches the last five available CONUS rasters by default:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh --prepare-shared-data
```

That install flag runs the equivalent of:

```bash
python scripts/run_maturity_years_by_fips.py \
  --start-year 2021 \
  --end-year 2025 \
  --coverage lower48 \
  --weather-backend zarr \
  --weather-time-standard lst
python scripts/ingest/download_cdl.py \
  --raster-only \
  --cdl-scope conus \
  --cdl-latest-year 2025 \
  --cdl-window-years 5
```

`--prepare-shared-maturity` remains available for weather/GDD/corn/soy maturity only, but it does not prepare CDL rasters.

The maturity runner writes annual files like `shared/corn_maturity/tables/rm_by_fips_2025.parquet` and final five-year average files like `shared/corn_maturity/tables/rm_by_fips_2021_2025_average.parquet` and `shared/soybean_maturity/tables/mg_by_fips_2021_2025_average.parquet`.

For a single annual refresh, run:

```bash
python scripts/run_maturity_by_fips.py \
  --year 2025 \
  --coverage lower48 \
  --weather-backend zarr \
  --weather-time-standard lst
```

Use `--weather-backend api` only when explicitly debugging the legacy NASA POWER point API path for county weather.

To persist the default data root for future login sessions, write the user environment file and still export the variable in the current shell before running commands:

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/environment.d"
cat > "${XDG_CONFIG_HOME:-$HOME/.config}/environment.d/60-my-farm-advisor.conf" <<'EOF'
DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
EOF
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
```

The `environment.d` file applies to future sessions only. It does not update an already-running shell.

---

## Dashboard Generation

The Grower Field Weather Dashboard is an interactive, self-contained single-file HTML page with zero runtime external dependencies. It uses inlined Plotly.js for interactive charts and (optionally) a stitched satellite basemap from Esri ArcGIS World Imagery — all embedded at build time.

### What it produces

- A single self-contained HTML file (no CDN references, no external fonts, no API calls)
- Interactive Plotly map with field polygons
- Growing Degree Days (GDD) chart with cumulative traces per field-year
- Rainfall chart with daily bars and cumulative line traces per field-year
- Custom checkbox dropdowns for field and year selection
- Auto-synchronized X-axes between GDD and rainfall charts
- Satellite basemap (when available) or neutral background
- Works when opened directly from disk via `file://`

### Two modes

**Full pipeline mode** — Dashboard generation runs as an optional final stage of `run_farm_pipeline.py`:

```bash
python run_farm_pipeline.py \
  --grower-slug il-dekalb-grower \
  --farm-slug dekalb-demo-farm \
  --generate-dashboard \
  [--no-basemap]
```

The pipeline step is opt-in only. Add `--generate-dashboard` to enable it. Without this flag, the pipeline runs identically to previous versions (backward compatible).

**Standalone mode** — Generate a dashboard for an existing farm data directory without rerunning any pipeline stages:

```bash
# Using farm_dashboard.py (recommended)
python farm_dashboard.py dashboard generate \
  --farm-dir ${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>

# Auto-discover a single farm
python farm_dashboard.py dashboard generate

# Custom output path
python farm_dashboard.py dashboard generate \
  --farm-dir /path/to/farm \
  --output /path/to/custom_dashboard.html

# Skip satellite basemap
python farm_dashboard.py dashboard generate \
  --farm-dir /path/to/farm \
  --no-basemap
```

Standalone mode reads only existing outputs — it does not call NASA POWER, field-boundary downloads, or any external data processing.

### Farm directory discovery (precedence)

1. `--farm-dir <path>` — use exactly this path; validate and fail if invalid.
2. `--growers-dir <path>` — search under this root for exactly one valid farm.
3. `$DATA_PIPELINE_DATA_ROOT` — search under `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/`.
4. Automatic discovery — walk `~` for directories containing `growers/<id>/farms/<id>/` with `boundary/field_boundaries.geojson` and `fields/`.

If zero farms are found, an error lists the searched paths. If more than one is found, an error lists candidates and instructs use of `--farm-dir`.

### Input data assumptions

A valid farm directory must contain:

```
<farm-dir>/
├── boundary/
│   └── field_boundaries.geojson    # GeoJSON FeatureCollection (EPSG:4326)
├── fields/
│   └── <field-id>/
│       ├── field.json              # Per-field metadata (optional)
│       └── weather/
│           └── daily_weather.csv   # Per-field daily weather (optional)
└── derived/
    └── tables/
        └── <farm>_weather_*.csv    # Aggregate fallback (optional)
```

- `field_boundaries.geojson` is required. Field IDs are read from `field_id` property. `area_acres` is read from feature properties when available.
- `field.json` is optional; `display_name` is used when present, falling back to the field ID.
- `daily_weather.csv` is optional per field. A header-only CSV (no data rows) is treated as "no data" — not an error. Missing weather files are handled similarly.
- The aggregate weather table in `derived/tables/` is used only as a documented fallback when no per-field weather has data.

### Weather calculations

For every field-year combination with usable daily data:

| Calculation | Formula |
|---|---|
| Daily GDD | `max((T2M_MAX + T2M_MIN) / 2 - 10.0, 0)` |
| Daily rainfall (in) | `PRECTOTCORR * 0.0393701` |
| Last frost date | Latest day before July 1 where `T2M_MIN <= 0.0`; fallback January 1 |
| Cumulative GDD | Running total from the last frost date |
| Cumulative rainfall | Running total from the last frost date |

- Dates are parsed robustly with leap-year support.
- All records sorted chronologically before cumulative computation.
- Rows missing required columns (`T2M_MAX`, `T2M_MIN`, `PRECTOTCORR`) are skipped.
- Cumulative totals span only valid observations (no zero-filling).

### Offline/runtime dependency guarantee

The generated HTML file has **zero runtime external dependencies**:

- Plotly.js v2.35.2 is downloaded once at build time and inlined in the HTML.
- No CDN references, no API calls, no externally loaded fonts.
- No external JavaScript, CSS, CSV, GeoJSON, JSON, image, or tile files.
- Works when opened directly from `file://` without a local web server.

### Build-time satellite imagery

When `--no-basemap` is not specified:

1. Field boundaries are projected to Web Mercator (EPSG:3857).
2. A full-farm Mercator extent with 15% buffer is computed.
3. Tile zoom is chosen so the stitched result is approximately 1500 px wide (subject to tile-count limits).
4. Tiles are downloaded from `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`.
5. Tiles are stitched with Pillow, cropped to exact buffered bounds, serialized as PNG, base64-encoded, and inlined.
6. On failure (network down, service unavailable, or Pillow missing), a neutral map background is used and a non-alarming note is displayed.
7. Tiles are cached in `~/.cache/my-farm-advisor/tiles/` for 24 hours.

The generated HTML never contacts Esri or any tile provider at runtime.

### Output

Default output location:

```
<farm-dir>/derived/dashboards/<farm-slug>_dashboard.html
```

Use `--output <path>` for explicit placement. The file is written atomically (generated to `.tmp` then renamed).

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "No valid farm directories found" | No directory matches the expected layout | Use `--farm-dir` to specify the exact path |
| "Found N valid farm directories" | Multiple farms match | Use `--farm-dir` to select one |
| "Missing required boundary file" | `boundary/field_boundaries.geojson` does not exist | Run the field boundary download stage first |
| "No weather data found for any field" | No per-field or aggregate weather CSVs | Run weather download, or check CSV headers |
| "WARNING: Tile download failed" | Network unreachable or Esri service unavailable | Use `--no-basemap` for offline use |
| "Pillow not installed" | Image library missing | Add `pillow>=10` to requirements and reinstall |
| WARNING about invalid geometry | A field has null/empty geometry | Inspect the boundary GeoJSON; field is skipped |

---

## Running inside OpenClaw CLI

When invoking the pipeline from the control UI or `openclaw-cli`, you can still
activate the environment explicitly, but the entrypoints will install and re-exec
themselves if the runtime venv is missing.

```bash
bash -lc 'export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime && \
  cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src" && \
  "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
    scripts/run_farm_pipeline.py --grower-slug ... --farm-slug ...'
```

This ensures every pipeline step (including geopandas/rasterio operations) uses
the shared environment that lives alongside the replicated scripts.
