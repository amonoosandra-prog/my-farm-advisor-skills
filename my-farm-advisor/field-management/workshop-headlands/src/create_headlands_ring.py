#!/usr/bin/env python3
"""Create headlands ring for a single pipeline field with GeoPackage output."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import geopandas as gpd

_HEADLANDS_SRC = Path(__file__).resolve().parents[2] / "headlands-ring" / "src"
sys.path.insert(0, str(_HEADLANDS_SRC))

from headlands_ring import create_headlands_ring, summarize_headlands

ACRES_PER_SQM = 0.0002471053814671653


def _utm_crs(centroid_lon: float) -> str:
    return "EPSG:32615" if centroid_lon < -90 else "EPSG:32616"


def create_workshop_headlands(
    grower_slug: str,
    farm_slug: str,
    field_slug: str,
    data_root: Path,
    headlands_width: float = 21.0,
) -> dict:
    field_dir = (
        data_root
        / "growers"
        / grower_slug
        / "farms"
        / farm_slug
        / "fields"
        / field_slug
    )
    boundary_path = field_dir / "boundary" / "field_boundary.geojson"
    if not boundary_path.exists():
        raise FileNotFoundError(f"Field boundary not found: {boundary_path}")

    original_gdf = gpd.read_file(boundary_path)
    if original_gdf.empty:
        raise ValueError(f"Empty boundary file: {boundary_path}")

    total_bounds = original_gdf.total_bounds
    centroid_lon = (total_bounds[0] + total_bounds[2]) / 2
    utm_code = _utm_crs(centroid_lon)
    utm_gdf = original_gdf.to_crs(utm_code)

    field_area_sqm = float(utm_gdf.geometry.area.sum())
    field_area_acres = field_area_sqm * ACRES_PER_SQM

    original_gdf["meters_squared"] = round(field_area_sqm, 2)
    original_gdf["acres"] = round(field_area_acres, 4)

    ring_utm = create_headlands_ring(utm_gdf, width_m=headlands_width)
    headlands_area_sqm = float(ring_utm.geometry.area.sum()) if not ring_utm.empty else 0.0
    headlands_area_acres = headlands_area_sqm * ACRES_PER_SQM

    ring_utm["meters_squared"] = round(headlands_area_sqm, 2)
    ring_utm["acres"] = round(headlands_area_acres, 4)

    ring_4326 = ring_utm.to_crs("EPSG:4326")

    out_dir = field_dir / "derived" / "headlands"
    out_dir.mkdir(parents=True, exist_ok=True)

    original_gdf.to_file(out_dir / "field_boundary.gpkg", layer="field_boundary", driver="GPKG")
    ring_4326.to_file(out_dir / "headlands_ring.gpkg", layer="headlands_ring", driver="GPKG")

    return {
        "grower": grower_slug,
        "farm": farm_slug,
        "field": field_slug,
        "utm_crs": utm_code,
        "field_area_sqm": round(field_area_sqm, 2),
        "field_area_acres": round(field_area_acres, 4),
        "headlands_width_m": headlands_width,
        "headlands_area_sqm": round(headlands_area_sqm, 2),
        "headlands_area_acres": round(headlands_area_acres, 4),
        "headlands_pct": round(headlands_area_acres / field_area_acres * 100, 2) if field_area_acres else 0.0,
        "output_dir": str(out_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create headlands ring for a pipeline field")
    parser.add_argument("--grower", required=True, help="Grower slug (e.g. il-grower)")
    parser.add_argument("--farm", required=True, help="Farm slug (e.g. il-grower-illinois)")
    parser.add_argument("--field", required=True, help="Field slug (e.g. osm-1288035236)")
    parser.add_argument(
        "--headlands-width", type=float, default=21.0, help="Headlands ring width in meters"
    )
    parser.add_argument(
        "--data-root",
        default=os.environ.get("DATA_PIPELINE_DATA_ROOT", ""),
        help="Data pipeline root (default: $DATA_PIPELINE_DATA_ROOT)",
    )
    args = parser.parse_args()

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        parser.error(f"DATA_PIPELINE_DATA_ROOT not found: {data_root}")

    runtime_base = data_root / "data-pipeline"
    result = create_workshop_headlands(
        grower_slug=args.grower,
        farm_slug=args.farm,
        field_slug=args.field,
        data_root=runtime_base,
        headlands_width=args.headlands_width,
    )

    print(f"Field:        {result['grower']}/{result['farm']}/{result['field']}")
    print(f"UTM CRS:      {result['utm_crs']}")
    print(f"Field area:   {result['field_area_acres']} ac  ({result['field_area_sqm']} m\u00b2)")
    print(f"Headlands:    {result['headlands_area_acres']} ac  ({result['headlands_area_sqm']} m\u00b2)")
    print(f"Headlands %:  {result['headlands_pct']}%")
    print(f"Output:       {result['output_dir']}/")


if __name__ == "__main__":
    main()
