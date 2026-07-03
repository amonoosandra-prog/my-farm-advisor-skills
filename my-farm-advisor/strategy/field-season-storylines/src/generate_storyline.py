#!/usr/bin/env python3
"""Generate field-season weather & NDVI storyline dashboards."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask

matplotlib.use("Agg")


# ── Constants ────────────────────────────────────────────────────────

GDD_BASE = 10.0
GS_MONTHS = list(range(4, 11))  # Apr–Oct
ACRE = 0.0002471053814671653
STATE_MAP = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
STATE_COLORS = {"IL": "#1b9e77", "IA": "#d95f02", "NE": "#7570b3"}
YEAR_COLORS = plt.cm.Set2(np.linspace(0, 1, 5))


# ── Paths ────────────────────────────────────────────────────────────

def _data_root() -> Path:
    root = os.environ.get("DATA_PIPELINE_DATA_ROOT", "/home/coder/my-farm-advisor-runtime")
    return Path(root) / "data-pipeline"


def field_weather_path(grower: str, farm: str, field: str) -> Path:
    return _data_root() / "growers" / grower / "farms" / farm / "fields" / field / "weather" / "daily_weather.csv"


def field_boundary_path(grower: str, farm: str, field: str) -> Path:
    return _data_root() / "growers" / grower / "farms" / farm / "fields" / field / "boundary" / "field_boundary.geojson"


def cdl_year_path(grower: str, farm: str, year: int) -> Path:
    prefix = farm.replace("-", "_")
    return _data_root() / "growers" / grower / "farms" / farm / "derived" / "tables" / f"{prefix}_{year}_cdl.csv"


def sentinel_ndvi_dir(grower: str, farm: str, field: str) -> Path:
    return _data_root() / "growers" / grower / "farms" / farm / "fields" / field / "satellite" / "sentinel"


# ── NDVI extraction ─────────────────────────────────────────────────

def extract_field_ndvi_timeseries(grower: str, farm: str, field: str) -> pd.DataFrame:
    """Extract field-mean NDVI per Sentinel-2 scene date."""
    boundary_path = field_boundary_path(grower, farm, field)
    sentinel_dir = sentinel_ndvi_dir(grower, farm, field)
    
    if not boundary_path.exists():
        raise FileNotFoundError(f"Boundary not found: {boundary_path}")
    if not sentinel_dir.is_dir():
        raise FileNotFoundError(f"Sentinel directory not found: {sentinel_dir}")
    
    boundary = gpd.read_file(boundary_path)
    
    records = []
    for year_dir in sorted(sentinel_dir.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        year = int(year_dir.name)
        for scene_dir in sorted(year_dir.iterdir()):
            if not scene_dir.is_dir():
                continue
            date_str = scene_dir.name.replace("sentinel_", "")
            ndvi_raster = scene_dir / f"{scene_dir.name}_ndvi.tif"
            if not ndvi_raster.exists():
                continue
            
            try:
                with rasterio.open(ndvi_raster) as src:
                    # Reproject boundary to raster CRS if needed
                    b = boundary.copy()
                    if b.crs != src.crs:
                        b = b.to_crs(src.crs)
                    
                    out_image, _ = mask(src, b.geometry, crop=True, nodata=np.nan)
                    valid = out_image[0][~np.isnan(out_image[0])]
                    
                    if len(valid) > 0:
                        records.append({
                            "date": pd.to_datetime(date_str),
                            "year": year,
                            "mean_ndvi": float(np.mean(valid)),
                            "min_ndvi": float(np.min(valid)),
                            "max_ndvi": float(np.max(valid)),
                            "std_ndvi": float(np.std(valid)),
                            "pixel_count": int(len(valid)),
                        })
            except Exception as e:
                print(f"  Warning: failed to process {ndvi_raster}: {e}")
                continue
    
    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("No NDVI scenes found or extracted")
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ── Weather processing ──────────────────────────────────────────────

def read_weather(grower: str, farm: str, field: str) -> pd.DataFrame:
    path = field_weather_path(grower, farm, field)
    if not path.exists():
        raise FileNotFoundError(f"Weather file not found: {path}")
    df = pd.read_csv(path, parse_dates=["date"])
    return df


def compute_gdd(df: pd.DataFrame) -> pd.DataFrame:
    """Add GDD and cumulative GDD columns."""
    df = df.copy()
    df["gdd"] = ((df["T2M_MIN"] + df["T2M_MAX"]) / 2 - GDD_BASE).clip(lower=0)
    df["gdd_cumul"] = df.groupby(df["date"].dt.year)["gdd"].cumsum()
    return df


# ── CDL crop labels ─────────────────────────────────────────────────

def read_crop_labels(grower: str, farm: str, field: str) -> dict[int, str]:
    """Read dominant crop per year for this field."""
    labels = {}
    for year in range(2021, 2026):
        path = cdl_year_path(grower, farm, year)
        if not path.exists():
            continue
        cdf = pd.read_csv(path)
        fr = cdf[cdf["field_id"] == field]
        if fr.empty:
            continue
        # Get crop with highest percentage
        dominant = fr.loc[fr["pct"].idxmax()]
        labels[year] = str(dominant["crop_name"])
    return labels


# ── Dashboard plotting ──────────────────────────────────────────────

def plot_storyline(
    ndvi_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    crop_labels: dict[int, str],
    field_id: str,
    output_path: Path,
) -> None:
    """Generate multi-panel storyline dashboard."""
    
    fig, axes = plt.subplots(4, 1, figsize=(14, 18), sharex=False)
    fig.suptitle(
        f"Field-Season Weather & NDVI Storyline — {field_id}\n"
        f"Prototype Year: 2022 (Corn) | All 5 Years Overlaid",
        fontsize=14, fontweight="bold", y=0.98,
    )
    
    years = sorted(ndvi_df["year"].unique())
    year_color_map = {y: YEAR_COLORS[i] for i, y in enumerate(years)}
    
    # ── Panel 1: NDVI Time Series ──
    ax = axes[0]
    for year in years:
        ydf = ndvi_df[ndvi_df["year"] == year].copy()
        ydf["doy"] = ydf["date"].dt.dayofyear
        color = year_color_map[year]
        crop = crop_labels.get(year, "Unknown")
        ax.scatter(ydf["doy"], ydf["mean_ndvi"], c=[color], s=40, alpha=0.8,
                   edgecolors="white", linewidth=0.5,
                   label=f"{year} ({crop})", zorder=3)
        # Optional: connect with line
        ax.plot(ydf["doy"], ydf["mean_ndvi"], color=color, linewidth=0.8,
                alpha=0.4, zorder=2)
    
    ax.set_ylabel("Mean NDVI", fontsize=10)
    ax.set_title("NDVI Time Series (Sentinel-2 scenes per year)", fontsize=11)
    ax.legend(fontsize=8, ncol=3, loc="lower right")
    ax.set_ylim(0, 1.0)
    ax.grid(True, alpha=0.15)
    ax.set_xlim(60, 330)
    
    # ── Panel 2: Daily Temperature ──
    ax = axes[1]
    for year in years:
        ydf = weather_df[(weather_df["date"].dt.year == year) &
                         (weather_df["date"].dt.month.isin(GS_MONTHS))].copy()
        ydf["doy"] = ydf["date"].dt.dayofyear
        color = year_color_map[year]
        ax.fill_between(ydf["doy"], ydf["T2M_MIN"], ydf["T2M_MAX"],
                        color=color, alpha=0.1)
        ax.plot(ydf["doy"], ydf["T2M"], color=color, linewidth=0.8, alpha=0.6,
                label=str(year))
    
    ax.axhline(30, color="red", linestyle="--", linewidth=0.8, alpha=0.5,
               label="30°C heat stress")
    ax.set_ylabel("Temperature (°C)", fontsize=10)
    ax.set_title("Daily Temperature (Growing Season)", fontsize=11)
    ax.legend(fontsize=8, ncol=3, loc="upper right")
    ax.grid(True, alpha=0.15)
    ax.set_xlim(60, 330)
    
    # ── Panel 3: Daily Precipitation ──
    ax = axes[2]
    ax_precip = ax
    ax_cumul = ax.twinx()
    
    for year in years:
        ydf = weather_df[(weather_df["date"].dt.year == year) &
                         (weather_df["date"].dt.month.isin(GS_MONTHS))].copy()
        ydf["doy"] = ydf["date"].dt.dayofyear
        color = year_color_map[year]
        
        # Bars for daily precip
        ax_precip.bar(ydf["doy"], ydf["PRECTOTCORR"], color=color,
                      alpha=0.3, width=1.0, label=str(year))
        
        # Cumulative line
        cumul = ydf["PRECTOTCORR"].cumsum()
        ax_cumul.plot(ydf["doy"], cumul, color=color, linewidth=1.2,
                      linestyle="--", alpha=0.6)
    
    ax_precip.set_ylabel("Daily precip (mm)", fontsize=10)
    ax_cumul.set_ylabel("Cumulative precip (mm)", fontsize=10, color="#333")
    ax_precip.set_title("Daily Precipitation + Cumulative (Growing Season)", fontsize=11)
    ax_precip.grid(True, alpha=0.15)
    ax_precip.set_xlim(60, 330)
    
    # ── Panel 4: Cumulative GDD ──
    ax = axes[3]
    for year in years:
        ydf = weather_df[(weather_df["date"].dt.year == year) &
                         (weather_df["date"].dt.month.isin(GS_MONTHS))].copy()
        ydf["doy"] = ydf["date"].dt.dayofyear
        color = year_color_map[year]
        crop = crop_labels.get(year, "Unknown")
        ax.plot(ydf["doy"], ydf["gdd_cumul"], color=color, linewidth=1.5,
                alpha=0.8, label=f"{year} ({crop})")
    
    ax.set_xlabel("Day of Year", fontsize=10)
    ax.set_ylabel("Cumulative GDD (°C-days, base 10°C)", fontsize=10)
    ax.set_title("Cumulative Growing Degree Days (Growing Season)", fontsize=11)
    ax.legend(fontsize=8, ncol=3, loc="lower right")
    ax.grid(True, alpha=0.15)
    ax.set_xlim(60, 330)
    
    fig.tight_layout(rect=[0, 0.02, 1, 0.95])
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Dashboard saved: {output_path}")


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Field-season weather & NDVI storyline")
    parser.add_argument("--grower", default="ia-grower", help="Grower slug")
    parser.add_argument("--farm", default="ia-grower-iowa", help="Farm slug")
    parser.add_argument("--field", default="osm-1360316064", help="Field ID")
    parser.add_argument("--output", default=None, help="Output directory")
    args = parser.parse_args()
    
    field_id = args.field
    grower = args.grower
    farm = args.farm
    
    if args.output:
        out_dir = Path(args.output)
    else:
        out_dir = _data_root() / "growers" / grower / "farms" / farm / "fields" / field_id / "derived" / "reports" / "storylines"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"=" * 60)
    print(f"Field-Season Storyline: {field_id}")
    print(f"=" * 60)
    
    # 1. Extract NDVI timeseries
    print("\n1. Extracting NDVI timeseries from Sentinel-2 scenes...")
    ndvi_df = extract_field_ndvi_timeseries(grower, farm, field_id)
    print(f"   Found {len(ndvi_df)} scenes across {ndvi_df['year'].nunique()} years")
    print(f"   Years: {sorted(ndvi_df['year'].unique())}")
    
    # Save NDVI CSV
    ndvi_csv = out_dir / f"{field_id}_ndvi_timeseries.csv"
    ndvi_df.to_csv(ndvi_csv, index=False)
    print(f"   Saved: {ndvi_csv}")
    
    # 2. Read weather
    print("\n2. Reading weather data...")
    weather_df = read_weather(grower, farm, field_id)
    weather_df = compute_gdd(weather_df)
    print(f"   Read {len(weather_df)} daily records ({weather_df['date'].min().year}-{weather_df['date'].max().year})")
    
    # 3. Read crop labels
    print("\n3. Reading CDL crop labels...")
    crop_labels = read_crop_labels(grower, farm, field_id)
    for year, crop in sorted(crop_labels.items()):
        print(f"   {year}: {crop}")
    
    # 4. Generate dashboard
    print("\n4. Generating storyline dashboard...")
    dashboard_path = out_dir / f"{field_id}_storyline.png"
    plot_storyline(ndvi_df, weather_df, crop_labels, field_id, dashboard_path)
    
    # 5. Summary stats
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Field: {field_id}")
    print(f"  NDVI scenes: {len(ndvi_df)} total")
    for year in sorted(ndvi_df["year"].unique()):
        n = len(ndvi_df[ndvi_df["year"] == year])
        crop = crop_labels.get(year, "Unknown")
        print(f"    {year}: {n:2d} scenes ({crop})")
    print(f"  Weather: {len(weather_df)} daily records")
    print(f"  Output: {out_dir}")
    print("\nDone.")


if __name__ == "__main__":
    main()
