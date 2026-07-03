#!/usr/bin/env python3
"""Workshop: NDVI to Management Zones

Full workflow:
1. Search Sentinel-2 L2A scenes for a year, rank by valid-pixel coverage (>90 %)
   and cloud cover within a late-June-to-August growing window.
2. Download the best scene.
3. Compute NDVI, mask to field boundaries, and apply a nearest-neighbor fill
   for small, isolated nodata gaps inside the field mask.
4. Run k-means (k=3) on the filled NDVI raster to derive management zones.
5. Polygonize the cluster raster, clip to the field polygon extent, and save to
   a GeoPackage for export.

Dependencies (install with uv/pip):
    uv pip install sentinelsat rasterio geopandas shapely pyproj numpy pandas
    uv pip install scikit-image scikit-learn scipy matplotlib

Environment:
    export COPERNICUS_USERNAME='your_username'
    export COPERNICUS_PASSWORD='your_password'
    export COPERNICUS_API_URL='https://apihub.copernicus.eu/apihub'
    export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from rasterio.mask import mask as rio_mask
from rasterio.transform import from_origin
from sentinelsat import SentinelAPI, geojson_to_wkt, read_geojson
from shapely.geometry import mapping, shape

# Optional heavy deps: fail gracefully with instructions
try:
    import geopandas as gpd
    from scipy import ndimage
    from sklearn.cluster import KMeans

    HAS_ML = True
except ImportError as exc:
    HAS_ML = False
    MISSING = str(exc).split("'")[1] if "'" in str(exc) else str(exc)
    warnings.warn(
        f"Missing optional dependency: {MISSING}. "
        "Install: uv pip install scikit-learn scipy geopandas scikit-image"
    )


# ---------------------------------------------------------------------------
# Sentinel-2 helpers
# ---------------------------------------------------------------------------

def get_api() -> SentinelAPI:
    """Return a configured SentinelAPI client from env vars."""
    username = os.environ.get("COPERNICUS_USERNAME")
    password = os.environ.get("COPERNICUS_PASSWORD")
    api_url = os.environ.get("COPERNICUS_API_URL", "https://apihub.copernicus.eu/apihub")
    if not username or not password:
        raise SystemExit("Set COPERNICUS_USERNAME and COPERNICUS_PASSWORD env vars.")
    return SentinelAPI(username, password, api_url)


def find_band_jp2(safe_dir: Path, band: str, resolution: str = "10m") -> Path:
    """Locate a JP2 band inside a .SAFE directory."""
    pattern = f"*_{band}_{resolution}.jp2"
    matches = list(safe_dir.rglob(pattern))
    if not matches:
        raise FileNotFoundError(f"Could not find {pattern} under {safe_dir}")
    return matches[0]


def find_scl_jp2(safe_dir: Path, resolution: str = "20m") -> Path:
    """Locate the SCL (scene classification) band."""
    pattern = f"*_SCL_{resolution}.jp2"
    matches = list(safe_dir.rglob(pattern))
    if not matches:
        # fallback to 60m
        pattern = "*_SCL_60m.jp2"
        matches = list(safe_dir.rglob(pattern))
    if not matches:
        raise FileNotFoundError(f"Could not find SCL band under {safe_dir}")
    return matches[0]


def compute_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Compute NDVI with division-by-zero protection."""
    red = red.astype("float32")
    nir = nir.astype("float32")
    denom = nir + red
    ndvi = np.where(denom != 0, (nir - red) / denom, np.nan).astype("float32")
    return np.clip(ndvi, -1.0, 1.0)


# ---------------------------------------------------------------------------
# 1. Search & rank scenes
# ---------------------------------------------------------------------------

def search_and_rank_scenes(
    api: SentinelAPI,
    footprint_wkt: str,
    year: int,
    cloud_max: float = 20.0,
    coverage_min: float = 0.90,
    growing_start: str = "0621",
    growing_end: str = "0831",
    max_candidates: int | None = None,
) -> pd.DataFrame:
    """Search S2 L2A scenes for *year*, pre-rank by metadata for July-centricity.

    Returns a DataFrame sorted by proximity to July (ascending), then cloud
    cover, then acquisition date.  The ``max_candidates`` limit lets the
    caller download only the most promising subset for an expensive real
    SCL-based coverage check.
    """
    date_start = f"{year}{growing_start}"
    date_end = f"{year}{growing_end}"

    products = api.query(
        footprint_wkt,
        date=(date_start, date_end),
        platformname="Sentinel-2",
        producttype="S2MSI2A",
        cloudcoverpercentage=(0, cloud_max),
    )

    df = api.to_dataframe(products)
    if df.empty:
        raise SystemExit("No products found for the requested window/cloud limit.")

    # Ensure datetime and compute month distance to July
    df["beginposition"] = pd.to_datetime(df["beginposition"])
    df["month_distance"] = (df["beginposition"].dt.month - 7).abs()
    df = df.sort_values(["month_distance", "cloudcoverpercentage", "beginposition"])

    if max_candidates is not None:
        df = df.head(max_candidates)

    print(f"Found {len(df)} candidate scenes for {year} ({date_start} – {date_end}).")
    return df


def pick_best_scene(df: pd.DataFrame, target_month: int = 7) -> str:
    """Return the product ID of the best scene.

    Preference:
    1. Closest to target_month (July by default)
    2. Lowest cloud cover
    3. Most recent
    """
    df = df.copy()
    df["month_distance"] = (df["beginposition"].dt.month - target_month).abs()
    sort_cols = ["month_distance", "cloudcoverpercentage", "beginposition"]
    best = df.sort_values(sort_cols).iloc[0]
    print(
        f"Selected best scene: {best['title']} "
        f"(cloud={best['cloudcoverpercentage']:.1f}%, "
        f"date={best['beginposition'].date()})"
    )
    return best.name  # product_id is the index


# ---------------------------------------------------------------------------
# 2. Download & extract
# ---------------------------------------------------------------------------

def download_and_extract(
    api: SentinelAPI, product_id: str, download_dir: Path, extract_dir: Path
) -> Path:
    """Download product ZIP and extract .SAFE directory."""
    download_dir.mkdir(parents=True, exist_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    dl = api.download(product_id, directory_path=str(download_dir))
    zip_path = Path(dl["path"])
    print(f"Downloaded: {zip_path}")

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    safe_dirs = [p for p in extract_dir.iterdir() if p.suffix == ".SAFE"]
    if not safe_dirs:
        raise SystemExit(f"No .SAFE directory found under {extract_dir}")
    return safe_dirs[0]


def download_candidate(
    api: SentinelAPI,
    product_id: str,
    title: str,
    download_dir: Path,
    extract_base_dir: Path,
) -> tuple[Path, Path]:
    """Download one product ZIP and extract into ``extract_base_dir / title``.

    Returns ``(safe_dir, zip_path)``.
    """
    download_dir.mkdir(parents=True, exist_ok=True)
    candidate_extract_dir = extract_base_dir / title
    candidate_extract_dir.mkdir(parents=True, exist_ok=True)

    dl = api.download(product_id, directory_path=str(download_dir))
    zip_path = Path(dl["path"])
    print(f"Downloaded: {zip_path.name}")

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(candidate_extract_dir)

    safe_dirs = [p for p in candidate_extract_dir.iterdir() if p.suffix == ".SAFE"]
    if not safe_dirs:
        raise SystemExit(f"No .SAFE directory found under {candidate_extract_dir}")
    return safe_dirs[0], zip_path


# ---------------------------------------------------------------------------
# 3. Valid-pixel coverage check using SCL
# ---------------------------------------------------------------------------

def compute_field_coverage(
    safe_dir: Path, fields_gdf: gpd.GeoDataFrame, resolution: str = "20m"
) -> float:
    """Return fraction of field-mask pixels that are valid (not cloud/shadow).

    SCL classes considered *invalid*:
        3 = Cloud shadows
        8 = Cloud (medium probability)
        9 = Cloud (high probability)
        10 = Thin cirrus
    """
    scl_path = find_scl_jp2(safe_dir, resolution)
    with rasterio.open(scl_path) as src:
        scl = src.read(1)
        scl_profile = src.profile.copy()
        scl_crs = src.crs

    # Reproject fields to SCL CRS and create a binary mask
    fields_proj = fields_gdf.to_crs(scl_crs)
    field_mask = np.zeros_like(scl, dtype=bool)

    for geom in fields_proj.geometry:
        if geom is None or geom.is_empty:
            continue
        # Rasterize this polygon onto the SCL grid
        shapes = [(mapping(geom), 1)]
        burned = features.rasterize(
            shapes,
            out_shape=scl.shape,
            transform=scl_profile["transform"],
            fill=0,
            dtype=np.uint8,
        )
        field_mask |= burned.astype(bool)

    if not field_mask.any():
        return 0.0

    invalid_classes = {3, 8, 9, 10}
    invalid_mask = np.isin(scl, list(invalid_classes))
    valid_pixels = field_mask & ~invalid_mask

    coverage = valid_pixels.sum() / field_mask.sum()
    print(f"Field coverage (valid pixels): {coverage:.2%}")
    return float(coverage)


# ---------------------------------------------------------------------------
# 4. NDVI extraction + nearest-neighbor gap fill
# ---------------------------------------------------------------------------

def write_ndvi_geotiff(
    red_path: Path, nir_path: Path, out_path: Path
) -> tuple[Path, rasterio.DatasetReader]:
    """Compute NDVI from red & NIR JP2 and write GeoTIFF."""
    with rasterio.open(red_path) as red_src:
        red = red_src.read(1).astype("float32")
        profile = red_src.profile.copy()
    with rasterio.open(nir_path) as nir_src:
        nir = nir_src.read(1).astype("float32")

    ndvi = compute_ndvi(red, nir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(ndvi, 1)

    print(f"Wrote NDVI: {out_path}")
    return out_path, profile


def nearest_neighbor_fill(
    array: np.ndarray, mask: np.ndarray | None = None
) -> np.ndarray:
    """Fill NaN / nodata values using nearest-neighbor interpolation.

    Parameters
    ----------
    array : 2-D ndarray
        Input array that may contain NaN.
    mask : 2-D bool ndarray, optional
        If provided, only pixels where ``mask == True`` are filled.
        Pixels outside the mask are left untouched (e.g. keep background
        nodata outside the field boundaries).

    Returns
    -------
    filled : 2-D ndarray
        Array with gaps filled by nearest valid neighbor.
    """
    if not HAS_ML:
        raise ImportError("scipy is required. Run: uv pip install scipy")

    filled = array.copy()
    invalid = np.isnan(filled)

    if mask is not None:
        # Only fill holes *inside* the mask; keep everything outside as NaN
        invalid &= mask

    if not invalid.any():
        return filled

    # Distance transform: for each invalid pixel, find coordinates of nearest valid pixel
    valid_mask = ~np.isnan(filled)
    if mask is not None:
        valid_mask &= mask

    # indices of nearest valid pixel
    _, nearest_valid_idx = ndimage.distance_transform_edt(
        ~valid_mask, return_indices=True
    )

    filled[invalid] = filled[tuple(nearest_valid_idx[:, invalid])]
    return filled


def mask_and_fill_ndvi(
    ndvi_path: Path,
    fields_gdf: gpd.GeoDataFrame,
    out_filled_path: Path,
) -> Path:
    """Mask NDVI to field polygons, fill small gaps with nearest neighbor."""
    if not HAS_ML:
        raise ImportError("geopandas and scipy required.")

    with rasterio.open(ndvi_path) as src:
        ndvi = src.read(1)
        profile = src.profile.copy()
        crs = src.crs

        fields_proj = fields_gdf.to_crs(crs)

        # Rasterize field mask onto NDVI grid
        field_mask = np.zeros_like(ndvi, dtype=bool)
        for geom in fields_proj.geometry:
            if geom is None or geom.is_empty:
                continue
            burned = features.rasterize(
                [(mapping(geom), 1)],
                out_shape=ndvi.shape,
                transform=profile["transform"],
                fill=0,
                dtype=np.uint8,
            )
            field_mask |= burned.astype(bool)

        # Zero-out everything outside field mask so it stays NaN
        ndvi_masked = ndvi.copy()
        ndvi_masked[~field_mask] = np.nan

        # Fill gaps inside the field mask
        ndvi_filled = nearest_neighbor_fill(ndvi_masked, mask=field_mask)

    profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")
    out_filled_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_filled_path, "w", **profile) as dst:
        dst.write(ndvi_filled, 1)

    print(f"Wrote filled NDVI: {out_filled_path}")
    return out_filled_path


# ---------------------------------------------------------------------------
# 5. K-means clustering (k=3)
# ---------------------------------------------------------------------------

def kmeans_cluster_ndvi(
    filled_ndvi_path: Path, out_cluster_path: Path, k: int = 3, seed: int = 42
) -> Path:
    """Cluster filled NDVI raster into *k* management zones.

    Returns path to integer-labeled cluster GeoTIFF.
    """
    if not HAS_ML:
        raise ImportError("scikit-learn is required. Run: uv pip install scikit-learn")

    with rasterio.open(filled_ndvi_path) as src:
        ndvi = src.read(1)
        profile = src.profile.copy()

    valid_mask = ~np.isnan(ndvi)
    valid_pixels = ndvi[valid_mask].reshape(-1, 1)

    if valid_pixels.size == 0:
        raise SystemExit("No valid pixels to cluster.")

    kmeans = KMeans(n_clusters=k, random_state=seed, n_init="auto")
    labels = kmeans.fit_predict(valid_pixels)

    # Reconstruct 2-D label array
    clustered = np.full_like(ndvi, np.nan, dtype="float32")
    clustered[valid_mask] = labels.astype("float32")

    # Save
    profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")
    out_cluster_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_cluster_path, "w", **profile) as dst:
        dst.write(clustered, 1)

    print(f"Wrote cluster raster (k={k}): {out_cluster_path}")
    print(f"Cluster centers (NDVI): {kmeans.cluster_centers_.flatten()}")
    return out_cluster_path


# ---------------------------------------------------------------------------
# 6. Polygonize clusters to field boundary and export to GeoPackage
# ---------------------------------------------------------------------------

def polygonize_clusters_to_gpkg(
    cluster_raster_path: Path,
    fields_gdf: gpd.GeoDataFrame,
    out_gpkg_path: Path,
) -> Path:
    """Polygonize the cluster raster, clip to field boundaries, save to GPKG.

    Each polygon gets a ``zone_id`` attribute (0, 1, 2 for k=3).
    """
    if not HAS_ML:
        raise ImportError("geopandas is required. Run: uv pip install geopandas")

    with rasterio.open(cluster_raster_path) as src:
        clustered = src.read(1)
        transform = src.transform
        crs = src.crs

    # Extract shapes (value, polygon)
    shapes_gen = features.shapes(
        clustered.astype("float32"),
        mask=~np.isnan(clustered),
        transform=transform,
    )

    records = []
    for geom_dict, val in shapes_gen:
        records.append({"geometry": shape(geom_dict), "zone_id": int(val)})

    if not records:
        raise SystemExit("No cluster polygons generated.")

    zones_gdf = gpd.GeoDataFrame(records, crs=crs)

    # Reproject fields to raster CRS and union for clipping
    fields_proj = fields_gdf.to_crs(crs)
    field_union = fields_proj.unary_union

    # Clip zones to field extent
    zones_clipped = zones_gdf[zones_gdf.intersects(field_union)].copy()
    zones_clipped["geometry"] = zones_clipped.geometry.intersection(field_union)

    # Drop empty geometries
    zones_clipped = zones_clipped[~zones_clipped.is_empty].copy()

    if zones_clipped.empty:
        raise SystemExit("No zones after clipping to field boundaries.")

    # Optional: dissolve by zone_id to reduce fragmentation
    zones_dissolved = zones_clipped.dissolve(by="zone_id").reset_index()

    out_gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    zones_dissolved.to_file(out_gpkg_path, driver="GPKG")

    print(f"Wrote management zones GeoPackage: {out_gpkg_path}")
    print(zones_dissolved[["zone_id", "geometry"]].head())
    return out_gpkg_path


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_workflow(
    aoi_geojson: Path,
    fields_geojson: Path,
    year: int,
    output_root: Path,
    k: int = 3,
    cloud_max: float = 20.0,
    coverage_min: float = 0.90,
    max_candidates: int = 5,
) -> dict:
    """Run the complete NDVI-to-management-zones workshop workflow.

    Returns a dict with paths to generated artifacts.
    """
    if not HAS_ML:
        raise ImportError(
            "Workshop requires scipy, scikit-learn, and geopandas.\n"
            "Install: uv pip install scipy scikit-learn geopandas scikit-image"
        )

    print("=" * 60)
    print("NDVI → Management Zones Workshop")
    print("=" * 60)

    # Paths
    download_dir = output_root / "downloads"
    extract_dir = output_root / "extracted"
    ndvi_dir = output_root / "ndvi"
    cluster_dir = output_root / "clusters"
    export_dir = output_root / "export"

    # Load AOI and fields
    footprint = geojson_to_wkt(read_geojson(str(aoi_geojson)))
    fields_gdf = gpd.read_file(fields_geojson)

    # 1. Search candidates (top N by metadata: July-centric, low cloud)
    api = get_api()
    candidates = search_and_rank_scenes(
        api, footprint, year, cloud_max=cloud_max, coverage_min=coverage_min,
        max_candidates=max_candidates,
    )

    # 2. Download top N candidates
    downloaded = []
    for product_id, row in candidates.iterrows():
        title = row["title"]
        try:
            safe_dir, zip_path = download_candidate(
                api, product_id, title, download_dir, extract_dir
            )
            downloaded.append((safe_dir, zip_path, product_id, title, row))
        except Exception as exc:
            print(f"  SKIP download failed for {title}: {exc}")

    if not downloaded:
        raise SystemExit("No candidates could be downloaded.")

    # 3. Compute actual SCL coverage for each downloaded scene
    print("\nComputing actual field coverage (SCL) …")
    scored = []
    for safe_dir, zip_path, product_id, title, row in downloaded:
        try:
            coverage = compute_field_coverage(safe_dir, fields_gdf)
        except Exception as exc:
            print(f"  {title}: SCL analysis failed ({exc}) — skipping.")
            continue
        month_distance = abs(row["beginposition"].month - 7)
        scored.append({
            "safe_dir": safe_dir,
            "zip_path": zip_path,
            "title": title,
            "coverage": coverage,
            "month_distance": month_distance,
            "cloud": row["cloudcoverpercentage"],
        })
        print(
            f"  {title}: coverage={coverage:.1%}, cloud={row['cloudcoverpercentage']:.1f}%, "
            f"date={row['beginposition'].date()}"
        )

    # 4. Filter to scenes meeting coverage threshold and rank
    passing = [s for s in scored if s["coverage"] >= coverage_min]
    if not passing:
        raise SystemExit(
            f"No downloaded scenes met the {coverage_min:.0%} valid-pixel coverage threshold. "
            "Try widening the date window, raising --cloud-max, or lowering --coverage-min."
        )

    passing.sort(
        key=lambda s: (-s["coverage"], s["month_distance"], s["cloud"])
    )
    best = passing[0]
    best_safe_dir = best["safe_dir"]
    best_title = best["title"]
    print(
        f"\nSelected best scene: {best_title} "
        f"(coverage={best['coverage']:.1%}, cloud={best['cloud']:.1f}%)"
    )

    # 5. Clean up rejected candidate downloads
    for s in scored:
        if s["safe_dir"] != best_safe_dir:
            if s["zip_path"].exists():
                s["zip_path"].unlink()
                print(f"  Removed rejected ZIP: {s['zip_path'].name}")
            if s["safe_dir"].exists():
                shutil.rmtree(s["safe_dir"])
                print(f"  Removed rejected .SAFE: {s['safe_dir'].name}")

    # 6. Compute NDVI from the winning scene
    red_path = find_band_jp2(best_safe_dir, "B04", "10m")
    nir_path = find_band_jp2(best_safe_dir, "B08", "10m")
    ndvi_path = ndvi_dir / f"{best_title}_NDVI.tif"
    write_ndvi_geotiff(red_path, nir_path, ndvi_path)

    # 5. Mask to fields + nearest-neighbor fill
    filled_ndvi_path = ndvi_dir / f"{best_title}_NDVI_filled.tif"
    mask_and_fill_ndvi(ndvi_path, fields_gdf, filled_ndvi_path)

    # 6. K-means clustering
    cluster_raster_path = cluster_dir / f"{best_title}_zones_k{k}.tif"
    kmeans_cluster_ndvi(filled_ndvi_path, cluster_raster_path, k=k)

    # 7. Polygonize and export
    zones_gpkg = export_dir / f"{best_title}_management_zones_k{k}.gpkg"
    polygonize_clusters_to_gpkg(cluster_raster_path, fields_gdf, zones_gpkg)

    print("=" * 60)
    print("Workshop complete!")
    print(f"  NDVI:            {ndvi_path}")
    print(f"  Filled NDVI:     {filled_ndvi_path}")
    print(f"  Zones raster:    {cluster_raster_path}")
    print(f"  Zones GeoPackage: {zones_gpkg}")
    print("=" * 60)

    return {
        "ndvi": ndvi_path,
        "filled_ndvi": filled_ndvi_path,
        "cluster_raster": cluster_raster_path,
        "zones_gpkg": zones_gpkg,
        "safe_dir": best_safe_dir,
        "best_title": best_title,
    }


if __name__ == "__main__":
    # Defaults using the built-in Iowa example data
    repo_base = Path(__file__).resolve().parents[3]  # workshop → sentinel2-imagery → imagery → my-farm-advisor
    aoi_default = repo_base / "imagery" / "sentinel2-imagery" / "examples" / "iowa_10_fields_aoi.geojson"
    fields_default = repo_base / "field-management" / "field-boundaries" / "examples" / "real_10_fields_iowa.geojson"

    data_root = os.environ.get("DATA_PIPELINE_DATA_ROOT")
    if not data_root:
        data_root = "/tmp/my-farm-advisor-runtime"
        warnings.warn(f"DATA_PIPELINE_DATA_ROOT not set; defaulting to {data_root}")
    out_root = Path(data_root) / "data-pipeline" / "imagery" / "sentinel2" / "workshop_ndvi_zones"

    parser = argparse.ArgumentParser(description="NDVI → Management Zones Workshop")
    parser.add_argument("--aoi", type=Path, default=aoi_default, help="AOI GeoJSON")
    parser.add_argument("--fields", type=Path, default=fields_default, help="Field boundaries GeoJSON")
    parser.add_argument("--year", type=int, default=2024, help="Growing season year")
    parser.add_argument("--k", type=int, default=3, help="K-means clusters")
    parser.add_argument("--cloud-max", type=float, default=20.0, help="Max cloud cover %%")
    parser.add_argument("--coverage-min", type=float, default=0.90, help="Min valid-pixel coverage (0-1)")
    parser.add_argument("--max-candidates", type=int, default=5, help="Top N metadata-ranked scenes to download and test with SCL")
    parser.add_argument("--output-root", type=Path, default=out_root, help="Output directory")
    args = parser.parse_args()

    run_workflow(
        aoi_geojson=args.aoi,
        fields_geojson=args.fields,
        year=args.year,
        output_root=args.output_root,
        k=args.k,
        cloud_max=args.cloud_max,
        coverage_min=args.coverage_min,
        max_candidates=args.max_candidates,
    )
