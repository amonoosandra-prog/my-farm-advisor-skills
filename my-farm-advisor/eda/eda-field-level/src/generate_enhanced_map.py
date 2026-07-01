#!/usr/bin/env python3
"""Generate enhanced combined geospatial map for Assignment-2 EDA output."""

import os
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.use("Agg")


def _resolve_paths() -> Path:
    data_root = os.environ.get("DATA_PIPELINE_DATA_ROOT", "")
    if not data_root:
        candidate = Path(__file__).resolve().parents[5] / "my-farm-advisor-runtime" / "data-pipeline"
        if candidate.is_dir():
            return candidate
        raise RuntimeError("DATA_PIPELINE_DATA_ROOT not set")
    return Path(data_root) / "data-pipeline"


_RUNTIME_BASE = _resolve_paths()
_LIB = _RUNTIME_BASE / "src" / "scripts" / "lib"
_SCRIPTS = _RUNTIME_BASE / "src" / "scripts"
for p in [str(_LIB), str(_SCRIPTS)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from paths import (  # noqa: E402
    DATA_ROOT,
    farm_boundary_path,
    shared_geoadmin_counties_dir,
    shared_geoadmin_states_dir,
)


def generate_enhanced_combined_map(output_path: Path) -> None:
    farm_map = {
        "il-grower": ("il-grower-illinois", "IL", "#1b9e77"),
        "ia-grower": ("ia-grower-iowa", "IA", "#d95f02"),
        "ne-grower": ("ne-grower-nebraska", "NE", "#7570b3"),
    }
    state_fips_map = {"IL": "17", "IA": "19", "NE": "31"}
    county_fips_map = {"il-grower": "17075", "ia-grower": "19109", "ne-grower": "31047"}

    all_fields = []
    for g, (fsl, abb, clr) in farm_map.items():
        bpath = farm_boundary_path(g, fsl)
        if not bpath.exists():
            continue
        gdf = gpd.read_file(bpath)
        gdf["grower_abb"] = abb
        gdf["grower_color"] = clr
        all_fields.append(gdf)

    if not all_fields:
        print("No field boundaries found")
        return

    combined = pd.concat(all_fields, ignore_index=True)
    combined = combined.to_crs("EPSG:4326")

    # Load state boundaries
    states_gdf = gpd.GeoDataFrame()
    states_path = shared_geoadmin_states_dir() / "states_usa.geojson"
    if states_path.exists():
        all_states = gpd.read_file(states_path).to_crs("EPSG:4326")
        target_fips = list(state_fips_map.values())
        states_gdf = all_states[all_states["state_fips"].isin(target_fips)].copy()

    # Load county boundaries
    counties_gdf = gpd.GeoDataFrame()
    counties_path = shared_geoadmin_counties_dir() / "counties_usa.geojson"
    if counties_path.exists():
        all_counties = gpd.read_file(counties_path).to_crs("EPSG:4326")
        target_cfips = list(county_fips_map.values())
        counties_gdf = all_counties[all_counties["fips"].isin(target_cfips)].copy()

    bounds = combined.total_bounds
    margin_lon = max((bounds[2] - bounds[0]) * 0.08, 0.5)
    margin_lat = max((bounds[3] - bounds[1]) * 0.08, 0.5)

    fig, ax = plt.subplots(figsize=(16, 10))

    # State outlines
    if not states_gdf.empty:
        states_gdf.boundary.plot(ax=ax, color="#333333", linewidth=2.0, alpha=0.7, zorder=1)

    # County outlines
    if not counties_gdf.empty:
        counties_gdf.boundary.plot(ax=ax, color="#666666", linewidth=1.0, alpha=0.5, linestyle="--", zorder=1)

    # Convex hulls per state
    for abb in ["IL", "IA", "NE"]:
        sub = combined[combined["grower_abb"] == abb]
        if sub.empty:
            continue
        color = sub.iloc[0]["grower_color"]
        # Create convex hull from all field geometries
        hull = sub.geometry.union_all().convex_hull
        if hull is not None and not hull.is_empty:
            hull_gdf = gpd.GeoDataFrame(geometry=[hull], crs="EPSG:4326")
            hull_gdf.plot(ax=ax, color=color, alpha=0.08, edgecolor=color, linewidth=1.5, linestyle="-.", zorder=2)

    # Field polygons with thick boundary edges
    for abb in ["IL", "IA", "NE"]:
        sub = combined[combined["grower_abb"] == abb]
        if sub.empty:
            continue
        color = sub.iloc[0]["grower_color"]
        # Fill
        sub.plot(ax=ax, color=color, alpha=0.25, edgecolor=color, linewidth=2.5, zorder=3)
        # Extra thick boundary highlight
        sub.boundary.plot(ax=ax, color=color, linewidth=2.5, alpha=0.9, zorder=4)

    # Field ID labels on edges/vertices
    for _, row in combined.iterrows():
        fid = str(row.get("field_id", ""))
        color = row["grower_color"]
        geom = row.geometry
        
        # Get exterior boundary points for label placement
        if geom.geom_type == "Polygon":
            exterior = geom.exterior
            # Place label on the northernmost point of the boundary
            coords = np.array(exterior.coords)
            northern_idx = np.argmax(coords[:, 1])
            label_x, label_y = coords[northern_idx]
        else:
            centroid = geom.centroid
            label_x, label_y = centroid.x, centroid.y

        ax.annotate(
            fid,
            (label_x, label_y),
            fontsize=6,
            ha="center",
            va="bottom",
            fontweight="bold",
            color="#222222",
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec=color, linewidth=1.2, alpha=0.85),
            zorder=5,
        )

    ax.set_xlim(bounds[0] - margin_lon, bounds[2] + margin_lon)
    ax.set_ylim(bounds[1] - margin_lat, bounds[3] + margin_lat)
    ax.set_xlabel("Longitude", fontsize=11)
    ax.set_ylabel("Latitude", fontsize=11)
    ax.set_title(
        "Assignment-2: Combined Field Map — IL / IA / NE (30 fields)\n"
        "Thick boundaries = field edges | Dashed outlines = state convex hulls",
        fontsize=13,
        fontweight="bold",
    )

    # Custom legend
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor="#1b9e77", alpha=0.4, edgecolor="#1b9e77", linewidth=2, label="Illinois (10 fields)"),
        Patch(facecolor="#d95f02", alpha=0.4, edgecolor="#d95f02", linewidth=2, label="Iowa (10 fields)"),
        Patch(facecolor="#7570b3", alpha=0.4, edgecolor="#7570b3", linewidth=2, label="Nebraska (10 fields)"),
    ]
    ax.legend(handles=legend_elements, fontsize=10, loc="lower right", framealpha=0.9)
    ax.grid(True, alpha=0.2)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    count_strs = [f"{abb}: {len(combined[combined['grower_abb']==abb])} fields" for abb in ["IL", "IA", "NE"]]
    print(f"Enhanced combined map saved: {output_path}")
    print(f"  {', '.join(count_strs)}")


if __name__ == "__main__":
    data_root = os.environ.get("DATA_PIPELINE_DATA_ROOT", "")
    if not data_root:
        print("ERROR: DATA_PIPELINE_DATA_ROOT not set")
        sys.exit(1)
    
    out = Path(data_root) / "data-pipeline" / "growers" / "EDA" / "Assignment-2" / "plots" / "combined_map.png"
    os.environ["DATA_PIPELINE_DATA_ROOT"] = str(data_root)
    
    if str(Path(data_root) / "data-pipeline" / "src" / "scripts" / "lib") not in sys.path:
        sys.path.insert(0, str(Path(data_root) / "data-pipeline" / "src" / "scripts" / "lib"))
    if str(Path(data_root) / "data-pipeline" / "src" / "scripts") not in sys.path:
        sys.path.insert(0, str(Path(data_root) / "data-pipeline" / "src" / "scripts"))
    
    # Re-import paths after setting env
    from paths import DATA_ROOT  # noqa: F401
    
    generate_enhanced_combined_map(out)
