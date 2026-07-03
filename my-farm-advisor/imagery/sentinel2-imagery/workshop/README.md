# Workshop: Raster NDVI to Management Zones

Transform a cloud-filtered, peak-growing-season Sentinel-2 NDVI raster into
spatial management zones using nearest-neighbor gap filling, k-means clustering,
and polygon export to GeoPackage.

## What this workshop covers

1. **Scene search & pre-ranking (metadata)**  
   Query Sentinel-2 L2A for a full growing-season window (default: late June –
   August).  Pre-rank candidates by proximity to peak growth (July-centric),
   then cloud cover, then acquisition date.  Only the top **N** (default 5)
   candidates are kept for the expensive download step.

2. **Download, SCL coverage test, and final ranking**  
   Download the top N candidates, then compute *actual* valid-pixel coverage
   inside the field mask using the SCL band.  Scenes below the threshold are
   discarded.  Survivors are re-ranked by actual coverage (descending), then
   month-distance to July, then cloud cover.  The winner is selected
   automatically.  Rejected downloads are cleaned up to save disk space.

3. **Nearest-neighbor gap fill**  
   After masking the NDVI raster to the field polygon(s), small isolated
   nodata gaps (often caused by thin clouds or SCL misclassification) are
   filled by copying the value of the nearest valid pixel *inside* the field
   boundary.  Everything outside the field stays nodata.

4. **K-means clustering (k = 3)**  
   Run scikit-learn KMeans on the filled NDVI pixels to partition the field
   into three management zones (e.g. low / medium / high vigor).

5. **Polygonize & export**  
   Convert the integer cluster raster into vector polygons, dissolve by zone,
   clip to the field boundary extent, and write a GeoPackage for downstream
   GIS or machinery-import workflows.

## Files

| File | Purpose |
|------|---------|
| `workshop_ndvi_to_management_zones.py` | Complete runnable workflow script |

## Dependencies

Install the full geospatial + ML stack (assumes you already have `uv`):

```bash
cd my-farm-advisor/imagery/sentinel2-imagery
uv venv .venv
source .venv/bin/activate

uv pip install sentinelsat rasterio geopandas shapely pyproj numpy pandas matplotlib
uv pip install scipy scikit-learn scikit-image
```

## Credentials

Set your Copernicus Data Space credentials before running:

```bash
export COPERNICUS_USERNAME='your_username'
export COPERNICUS_PASSWORD='your_password'
export COPERNICUS_API_URL='https://apihub.copernicus.eu/apihub'
```

## Quick start (Iowa example fields)

```bash
cd my-farm-advisor/imagery/sentinel2-imagery
source .venv/bin/activate

export DATA_PIPELINE_DATA_ROOT=/tmp/my-farm-advisor-runtime

python workshop/workshop_ndvi_to_management_zones.py \
  --year 2024 \
  --k 3 \
  --cloud-max 20 \
  --coverage-min 0.90 \
  --max-candidates 5
```

Generated artifacts land under:

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/imagery/sentinel2/workshop_ndvi_zones/
  downloads/                # raw ZIP products
  extracted/                # .SAFE directories
  ndvi/
    <title>_NDVI.tif        # raw NDVI raster
    <title>_NDVI_filled.tif # field-masked + gap-filled NDVI
  clusters/
    <title>_zones_k3.tif    # integer zone raster
  export/
    <title>_management_zones_k3.gpkg  # final GeoPackage
```

## Script arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--aoi` | `examples/iowa_10_fields_aoi.geojson` | Search AOI GeoJSON |
| `--fields` | `../../field-management/field-boundaries/examples/real_10_fields_iowa.geojson` | Field boundary GeoJSON |
| `--year` | `2024` | Growing season year |
| `--k` | `3` | K-means cluster count |
| `--cloud-max` | `20.0` | Maximum scene-level cloud cover (%) |
| `--coverage-min` | `0.90` | Minimum valid-pixel coverage inside fields |
| `--max-candidates` | `5` | Top metadata-ranked scenes to download and test with SCL |
| `--output-root` | `${DATA_PIPELINE_DATA_ROOT}/.../workshop_ndvi_zones` | Root output directory |

## How the nearest-neighbor fill works

1. Rasterize the field polygons onto the NDVI grid → binary `field_mask`.
2. Set all NDVI pixels outside the mask to `NaN` (they stay `NaN`).
3. Compute a Euclidean distance transform on the *invalid* pixels that are
   still inside the mask.
4. Replace each invalid pixel with the value of the closest valid pixel.
5. The result is a completely filled NDVI layer *only* where fields exist,
   preserving crisp field edges.

## How k-means zoning works

1. Collect every valid (non-`NaN`) NDVI pixel into a 1-D feature vector.
2. Fit `KMeans(n_clusters=k)` with a fixed random seed for reproducibility.
3. Assign each pixel to zone `0 … k-1`.
4. The cluster center NDVI values are printed so you can label zones
   (e.g. zone 0 = low vigor, zone 2 = high vigor).

## How polygonize + export works

1. `rasterio.features.shapes` extracts contiguous polygons for each zone ID.
2. `geopandas` assembles them into a GeoDataFrame with a `zone_id` column.
3. The union of all field polygons clips the zones so nothing leaks outside
   the farm boundary.
4. `dissolve(by='zone_id')` merges touching polygons of the same zone,
   yielding clean, contiguous management zones.
5. Output is written with the GeoPackage driver (`.gpkg`).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ImportError: scipy` or `scikit-learn` | Run the dependency install block above |
| `No products found` | Widen `--year`, raise `--cloud-max`, or verify AOI size |
| Coverage < 90 % for all candidates | The script halts with an error. Widen the date window, raise `--cloud-max`, or lower `--coverage-min` |
| `CRS mismatch` | The script reprojects fields to the raster CRS automatically |
| Empty GeoPackage | Check that the field polygons overlap the raster extent; use the provided Iowa example to verify |

## References

- [Sentinel-2 Imagery Guide](../GUIDE.md) — base Sentinel-2/NDVI workflow
- [Field Boundaries Guide](../../field-management/field-boundaries/GUIDE.md) — boundary acquisition
- [sentinelsat docs](https://sentinelsat.readthedocs.io/)
- [rasterio docs](https://rasterio.readthedocs.io/)
