#!/usr/bin/env python3
"""Field-level EDA: weather, CDL, field boundaries — per-field and cross-grower."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.use("Agg")


def _resolve_paths() -> Path:
    data_root = os.environ.get("DATA_PIPELINE_DATA_ROOT", "")
    if not data_root:
        # Try common checkout-relative path
        candidate = Path(__file__).resolve().parents[5] / "my-farm-advisor-runtime" / "data-pipeline"
        if candidate.is_dir():
            return candidate
        msg = "DATA_PIPELINE_DATA_ROOT not set and no default runtime found"
        raise RuntimeError(msg)
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
    farm_dir,
    farm_tables_dir,
    field_weather_path,
    grower_dir,
    shared_geoadmin_counties_dir,
    shared_geoadmin_states_dir,
)

ACRES_PER_SQM = 0.0002471053814671653
GROWING_SEASON_MONTHS = list(range(4, 11))
DEFAULT_GDD_BASE = 10.0
BLUE = "#2166ac"
RED = "#b2182b"
GREEN = "#4daf4a"
ORANGE = "#ff7f00"
GRAY = "#969696"


def _discover_growers() -> list[str]:
    growers_root = DATA_ROOT / "growers"
    if not growers_root.is_dir():
        return []
    _exclude = {"eda", "EDA", "shared", "logs", "manifests", "reference"}
    return sorted(d.name for d in growers_root.iterdir()
                  if d.is_dir() and not d.name.startswith(".") and d.name not in _exclude)


def _discover_farms(grower_slug: str) -> list[str]:
    farms_dir = grower_dir(grower_slug) / "farms"
    if not farms_dir.is_dir():
        return []
    return sorted(d.name for d in farms_dir.iterdir()
                  if d.is_dir() and not d.name.startswith("."))


def _read_weather(field_weather_path: Path) -> pd.DataFrame:
    if not field_weather_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(field_weather_path, parse_dates=["date"])
    return df


def _read_crop_rotation(farm_tables_dir: Path, farm_slug: str) -> pd.DataFrame:
    path = farm_tables_dir / f"{farm_slug.replace('-', '_')}_crop_rotation.csv"
    # also try without replacement
    if not path.exists():
        path = farm_tables_dir / f"{farm_slug}_crop_rotation.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _read_cdl_year(farm_tables_dir: Path, farm_slug: str, year: int) -> pd.DataFrame:
    prefix = farm_slug.replace("-", "_")
    path = farm_tables_dir / f"{prefix}_{year}_cdl.csv"
    if not path.exists():
        path = farm_tables_dir / f"{farm_slug}_{year}_cdl.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _growing_season_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return df
    return df[df["date"].dt.month.isin(GROWING_SEASON_MONTHS)]


def _gdd(tmin: pd.Series, tmax: pd.Series, base: float = DEFAULT_GDD_BASE) -> pd.Series:
    avg = (tmin + tmax) / 2
    return (avg - base).clip(lower=0)


def _perimeter_from_geom(geom) -> float:
    try:
        return geom.length
    except Exception:
        return 0.0


def _area_from_geom(geom) -> float:
    try:
        return geom.area
    except Exception:
        return 0.0


# ── Weather plots ────────────────────────────────────────────────────

def plot_temp_profile(
    df: pd.DataFrame, field_id: str, output_dir: Path, prefix: str = "01"
) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    gs = _growing_season_df(df).copy()
    if gs.empty:
        ax.text(0.5, 0.5, "No growing-season data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_weather_temp_profile.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    daily = gs.groupby("date")["T2M"].mean()
    rolling = daily.rolling(7, center=True).mean()
    ax.plot(daily.index, daily.values, color=GRAY, linewidth=0.5, alpha=0.4, label="Daily mean")
    ax.plot(rolling.index, rolling.values, color=RED, linewidth=1.5, label="7-day rolling")
    ax.axhline(30, color=ORANGE, linestyle="--", linewidth=0.8, alpha=0.7, label="30°C (heat stress)")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title(f"Growing-Season Temperature Profile — {field_id}")
    ax.legend(fontsize=8)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_weather_temp_profile.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_precip_distribution(
    df: pd.DataFrame, field_id: str, output_dir: Path, prefix: str = "02"
) -> None:
    fig, ax1 = plt.subplots(figsize=(10, 4))
    gs = _growing_season_df(df).copy()
    if gs.empty:
        ax1.text(0.5, 0.5, "No growing-season data", transform=ax1.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_weather_precip_distribution.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    gs["month"] = gs["date"].dt.month
    monthly = gs.groupby(["date", "month"])["PRECTOTCORR"].sum().reset_index()
    monthly["year"] = monthly["date"].dt.year
    monthly_avg = monthly.groupby("month")["PRECTOTCORR"].mean()
    cumul = monthly_avg.cumsum()

    ax1.bar(monthly_avg.index, monthly_avg.values, color=BLUE, width=0.7, label="Mean monthly precip")
    ax1.set_ylabel("Mean precipitation (mm)")
    ax1.set_xlabel("Month")
    ax1.set_xticks(range(4, 11))
    ax1.set_xticklabels(["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"])

    ax2 = ax1.twinx()
    ax2.plot(cumul.index, cumul.values, color=RED, marker="o", linewidth=1.5, label="Cumulative")
    ax2.set_ylabel("Cumulative precip (mm)")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc="upper left")
    ax1.set_title(f"Growing-Season Precipitation — {field_id}")
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_weather_precip_distribution.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_gdd_precip_correlation(
    df: pd.DataFrame, field_id: str, output_dir: Path, prefix: str = "03"
) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    gs = _growing_season_df(df).copy()
    if gs.empty or "T2M_MIN" not in gs.columns or "T2M_MAX" not in gs.columns:
        ax.text(0.5, 0.5, "Insufficient data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_weather_gdd_precip_correlation.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    gs["year"] = gs["date"].dt.year
    yearly = {}
    for year, grp in gs.groupby("year"):
        gdd = _gdd(grp["T2M_MIN"], grp["T2M_MAX"]).sum()
        precip = grp["PRECTOTCORR"].sum()
        yearly[year] = {"gdd": gdd, "precip": precip}

    years_df = pd.DataFrame.from_dict(yearly, orient="index")
    if years_df.empty:
        ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_weather_gdd_precip_correlation.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    ax.scatter(years_df["gdd"], years_df["precip"], c=BLUE, s=40)
    for year, row in years_df.iterrows():
        ax.annotate(str(year), (row["gdd"], row["precip"]), fontsize=7, xytext=(4, 4),
                    textcoords="offset points")
    if len(years_df) > 2:
        z = np.polyfit(years_df["gdd"], years_df["precip"], 1)
        p = np.poly1d(z)
        x_line = np.linspace(years_df["gdd"].min(), years_df["gdd"].max(), 100)
        ax.plot(x_line, p(x_line), color=RED, linewidth=1, alpha=0.6)
    ax.set_xlabel("Cumulative GDD (°C-days)")
    ax.set_ylabel("Cumulative precip (mm)")
    ax.set_title(f"GDD vs Precipitation — {field_id}")
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_weather_gdd_precip_correlation.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# ── CDL plots ────────────────────────────────────────────────────────

def plot_rotation_timeline(
    cdl_dfs: dict[int, pd.DataFrame], field_id: str, output_dir: Path, prefix: str = "04"
) -> None:
    fig, ax = plt.subplots(figsize=(8, 3))
    crop_colors = {
        "Corn": "#f0e442", "Soybeans": "#44aa44", "Winter Wheat": "#d55e00",
        "Alfalfa": "#56b4e9", "Grass/Pasture": "#cccccc", "Fallow/Idle Cropland": "#aa6e28",
        "Sorghum": "#e69f00", "Cotton": "#0072b2", "Rice": "#cc79a7",
        "Sunflower": "#ffcc00", "Peas": "#66c2a5", "Oats": "#fc8d62",
        "Barley": "#8da0cb", "Rye": "#e78ac3", "Millet": "#a6d854",
        "Canola": "#ffd92f", "Dry Beans": "#e5c494", "Potatoes": "#b3b3b3",
        "Sugarbeets": "#ccebc5", "Other Hay/Non Alfalfa": "#fbb4ae",
    }
    default_color = "#b3b3b3"

    years = sorted(cdl_dfs.keys())
    field_rows = []
    for y in years:
        df = cdl_dfs[y]
        row = df[df["field_id"] == field_id]
        if row.empty:
            continue
        row = row.iloc[0]
        field_rows.append({"year": y, "crop": row.get("crop_name", "Unknown"),
                           "pct": row.get("pct", 0)})

    if not field_rows:
        ax.text(0.5, 0.5, "No CDL data for this field", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_cdl_rotation_timeline.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    fr = pd.DataFrame(field_rows)
    for i, (_, r) in enumerate(fr.iterrows()):
        color = crop_colors.get(r["crop"], default_color)
        ax.barh(0, 1, left=i, height=0.5, color=color, edgecolor="white", linewidth=1)
        ax.text(i + 0.5, 0, str(r["year"]), ha="center", va="center", fontsize=8, color="white",
                fontweight="bold")

    ax.set_yticks([])
    ax.set_xlim(0, len(fr))
    ax.set_title(f"Crop Rotation Timeline — {field_id}")
    unique_crops = fr["crop"].unique()
    handles = [plt.Rectangle((0, 0), 1, 1, color=crop_colors.get(c, default_color))
               for c in unique_crops]
    ax.legend(handles, list(unique_crops), fontsize=7, ncol=3,
              loc="lower center", bbox_to_anchor=(0.5, -0.5))
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_cdl_rotation_timeline.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_diversity_trend(
    cdl_dfs: dict[int, pd.DataFrame], farm_slug: str, output_dir: Path, prefix: str = "05"
) -> None:
    fig, ax = plt.subplots(figsize=(8, 3))
    years = sorted(cdl_dfs.keys())
    diversities = []
    for y in years:
        df = cdl_dfs[y]
        n_unique = df["crop_name"].nunique() if "crop_name" in df.columns else 0
        diversities.append({"year": y, "n_crops": n_unique})

    if not diversities:
        ax.text(0.5, 0.5, "No CDL data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_cdl_diversity_trend.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    div_df = pd.DataFrame(diversities)
    ax.plot(div_df["year"], div_df["n_crops"], marker="o", color=GREEN, linewidth=1.5)
    ax.fill_between(div_df["year"], 0, div_df["n_crops"], alpha=0.15, color=GREEN)
    ax.set_xlabel("Year")
    ax.set_ylabel("Unique crop types (farm-wide)")
    ax.set_title(f"Crop Diversity Trend — {farm_slug}")
    ax.set_xticks(years)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_cdl_diversity_trend.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_area_persistence(
    cdl_dfs: dict[int, pd.DataFrame],
    boundaries_gdf: gpd.GeoDataFrame,
    farm_slug: str, field_id: str,
    output_dir: Path, prefix: str = "06"
) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    years = sorted(cdl_dfs.keys())
    crop_persistence = {}
    for _, row in boundaries_gdf.iterrows():
        fid = str(row["field_id"])
        area = row.get("area_acres", 0) or (_area_from_geom(row.geometry) * ACRES_PER_SQM)
        last_crop = None
        consecutive = 1
        for y in years:
            df = cdl_dfs[y]
            fr = df[df["field_id"] == fid]
            if fr.empty:
                continue
            crop = fr.iloc[0].get("crop_name", "")
            if crop == last_crop:
                consecutive += 1
            else:
                consecutive = 1
            last_crop = crop
        crop_persistence[fid] = {"area_acres": area, "consecutive": consecutive}

    if not crop_persistence:
        ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_cdl_area_persistence.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    cp = pd.DataFrame.from_dict(crop_persistence, orient="index")
    target_area = cp.loc[field_id, "area_acres"] if field_id in cp.index else None
    target_persist = cp.loc[field_id, "consecutive"] if field_id in cp.index else None

    ax.scatter(cp["area_acres"], cp["consecutive"], color=GRAY, s=30, alpha=0.6, label="Other fields")
    if field_id in cp.index:
        ax.scatter(target_area, target_persist, color=RED, s=60, zorder=5, label=f"Target ({field_id[:16]}...)")

    if len(cp) > 2:
        z = np.polyfit(cp["area_acres"], cp["consecutive"], 1)
        p = np.poly1d(z)
        x_line = np.linspace(cp["area_acres"].min(), cp["area_acres"].max(), 100)
        ax.plot(x_line, p(x_line), color=RED, linewidth=1, alpha=0.4)

    ax.set_xlabel("Field area (acres)")
    ax.set_ylabel("Consecutive same-crop years (max)")
    ax.set_title(f"Field Area vs Crop Persistence — {farm_slug}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_cdl_area_persistence.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# ── Boundary plots ───────────────────────────────────────────────────

def plot_size_distribution(
    boundaries_gdf: gpd.GeoDataFrame, field_id: str,
    output_dir: Path, prefix: str = "07"
) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    areas = []
    target_area = None
    for _, row in boundaries_gdf.iterrows():
        fid = str(row["field_id"])
        a = row.get("area_acres", 0) or (_area_from_geom(row.geometry) * ACRES_PER_SQM)
        areas.append(a)
        if fid == field_id:
            target_area = a

    if not areas:
        ax.text(0.5, 0.5, "No field data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_boundary_size_distribution.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    ax.hist(areas, bins=min(8, len(areas)), color=GRAY, alpha=0.6, edgecolor="white")
    if target_area is not None:
        ax.axvline(target_area, color=RED, linewidth=2, linestyle="--",
                   label=f"Target field: {target_area:.1f} ac")
    ax.set_xlabel("Field area (acres)")
    ax.set_ylabel("Number of fields")
    ax.set_title("Field Size Distribution (farm-wide)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_boundary_size_distribution.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_shape_complexity(
    boundaries_gdf: gpd.GeoDataFrame, field_id: str,
    output_dir: Path, prefix: str = "08"
) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    ratios = []
    target_ratio = None
    utm_gdf = boundaries_gdf.to_crs(boundaries_gdf.estimate_utm_crs())
    for _, row in utm_gdf.iterrows():
        fid = str(row.get("field_id", ""))
        area = _area_from_geom(row.geometry)
        perim = _perimeter_from_geom(row.geometry)
        ratio = perim / (2 * np.sqrt(np.pi * area)) if area > 0 else 0
        ratios.append(ratio)
        if fid == field_id:
            target_ratio = ratio

    if not ratios:
        ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_boundary_shape_complexity.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    ax.hist(ratios, bins=min(8, len(ratios)), color=GRAY, alpha=0.6, edgecolor="white")
    if target_ratio is not None:
        ax.axvline(target_ratio, color=RED, linewidth=2, linestyle="--",
                   label=f"Target field: {target_ratio:.3f} (circle=1)")
    ax.set_xlabel("Shape complexity index (perimeter ratio)")
    ax.set_ylabel("Number of fields")
    ax.set_title("Field Shape Complexity (farm-wide)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_boundary_shape_complexity.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_size_weather_cv(
    boundaries_gdf: gpd.GeoDataFrame,
    weather_by_field: dict[str, pd.DataFrame],
    field_id: str,
    output_dir: Path, prefix: str = "09"
) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    field_areas = {}
    for _, row in boundaries_gdf.iterrows():
        fid = str(row["field_id"])
        a = row.get("area_acres", 0) or (_area_from_geom(row.geometry) * ACRES_PER_SQM)
        field_areas[fid] = a

    gs_cv = {}
    for fid, wdf in weather_by_field.items():
        if wdf.empty:
            continue
        gs = _growing_season_df(wdf)
        if gs.empty:
            continue
        cv = gs["PRECTOTCORR"].std() / gs["PRECTOTCORR"].mean() if gs["PRECTOTCORR"].mean() > 0 else 0
        gs_cv[fid] = cv

    common = set(field_areas.keys()) & set(gs_cv.keys())
    if not common:
        ax.text(0.5, 0.5, "Insufficient data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_boundary_size_weather_cv.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    areas_vec = [field_areas[f] for f in common]
    cv_vec = [gs_cv[f] for f in common]
    colors = [RED if f == field_id else GRAY for f in common]

    ax.scatter(areas_vec, cv_vec, c=colors, s=30, alpha=0.7)
    if field_id in common:
        idx = list(common).index(field_id)
        ax.scatter(areas_vec[idx], cv_vec[idx], color=RED, s=60, zorder=5)

    if len(common) > 2:
        z = np.polyfit(areas_vec, cv_vec, 1)
        p = np.poly1d(z)
        x_line = np.linspace(min(areas_vec), max(areas_vec), 100)
        ax.plot(x_line, p(x_line), color=RED, linewidth=1, alpha=0.4)

    ax.set_xlabel("Field area (acres)")
    ax.set_ylabel("Precipitation CV (growing season)")
    ax.set_title("Field Size vs Weather Variability")
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_boundary_size_weather_cv.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# ── Cross-grower plots ───────────────────────────────────────────────

def _gather_per_field_data() -> pd.DataFrame:
    rows = []
    for g in _discover_growers():
        farms = _discover_farms(g)
        for f in farms:
            bpath = farm_boundary_path(g, f)
            if not bpath.exists():
                continue
            bgdf = gpd.read_file(bpath)
            tdir = farm_tables_dir(g, f)
            utm_bgdf = bgdf.to_crs(bgdf.estimate_utm_crs()) if bgdf.crs and bgdf.crs.is_geographic else bgdf

            for _, frow in bgdf.iterrows():
                fid = str(frow["field_id"])
                area = frow.get("area_acres", 0) or (utm_bgdf[utm_bgdf["field_id"] == fid].area.iloc[0] * ACRES_PER_SQM if not utm_bgdf.empty else 0)
                weather_path = field_weather_path(g, f, fid)
                wdf = _read_weather(weather_path)
                gs = _growing_season_df(wdf)
                mean_temp = gs["T2M"].mean() if not gs.empty else None
                total_precip = gs["PRECTOTCORR"].sum() if not gs.empty else None
                gdd = _gdd(gs["T2M_MIN"], gs["T2M_MAX"]).sum() if not gs.empty and "T2M_MIN" in gs.columns else None

                shape_ratio = None
                if not utm_bgdf.empty:
                    match = utm_bgdf[utm_bgdf["field_id"] == fid]
                    if not match.empty:
                        area_m = match.area.iloc[0]
                        perim = match.geometry.length.iloc[0]
                        shape_ratio = perim / (2 * np.sqrt(np.pi * area_m)) if area_m > 0 else None

                row_data = {
                    "grower": g,
                    "farm": f,
                    "field_id": fid,
                    "area_acres": round(area, 2),
                    "mean_gs_temp": round(mean_temp, 2) if mean_temp is not None else None,
                    "total_gs_precip": round(total_precip, 1) if total_precip is not None else None,
                    "gdd_sum": round(gdd, 0) if gdd is not None else None,
                    "shape_complexity": round(shape_ratio, 4) if shape_ratio is not None else None,
                }

                cdl_corn = None
                cdl_soy = None
                for year in range(2021, 2026):
                    cdf = _read_cdl_year(tdir, f, year)
                    if not cdf.empty and "crop_name" in cdf.columns:
                        fr = cdf[cdf["field_id"] == fid]
                        if not fr.empty:
                            crop = str(fr.iloc[0].get("crop_name", ""))
                            if "corn" in crop.lower():
                                cdl_corn = year
                            elif "soy" in crop.lower():
                                cdl_soy = year

                row_data["dominant_crop"] = "corn" if cdl_corn else ("soy" if cdl_soy else "other")
                rows.append(row_data)

    return pd.DataFrame(rows)


def _gather_grower_summaries() -> list[dict]:
    summaries = []
    for g in _discover_growers():
        farms = _discover_farms(g)
        all_areas = []
        crop_mix: dict[str, float] = {}
        for f in farms:
            bpath = farm_boundary_path(g, f)
            if not bpath.exists():
                continue
            bgdf = gpd.read_file(bpath)
            for _, row in bgdf.iterrows():
                a = row.get("area_acres", 0) or (_area_from_geom(row.geometry) * ACRES_PER_SQM)
                all_areas.append(a)

            tdir = farm_tables_dir(g, f)
            for year in range(2021, 2026):
                cdf = _read_cdl_year(tdir, f, year)
                if "crop_name" in cdf.columns and "pct" in cdf.columns:
                    for _, cr in cdf.iterrows():
                        crop = str(cr["crop_name"])
                        pct = float(cr.get("pct", 0))
                        crop_mix[crop] = crop_mix.get(crop, 0) + pct / 5

        summaries.append({
            "grower": g,
            "mean_field_acres": np.mean(all_areas) if all_areas else 0,
            "median_field_acres": np.median(all_areas) if all_areas else 0,
            "std_field_acres": np.std(all_areas) if all_areas else 0,
            "n_fields": len(all_areas),
            "crop_mix": crop_mix,
        })
    return summaries


def plot_temp_boxplot_by_state(output_dir: Path, prefix: str = "cross-01") -> None:
    pdata = _gather_per_field_data()
    valid = pdata.dropna(subset=["mean_gs_temp"]).copy()
    if valid.empty:
        print("  No weather data for boxplot")
        return
    state_map = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
    valid["state"] = valid["grower"].map(state_map)

    fig, ax = plt.subplots(figsize=(8, 5))
    states = sorted(valid["state"].unique())
    for i, state in enumerate(states):
        sdata = valid[valid["state"] == state]["mean_gs_temp"]
        bp = ax.boxplot(sdata, positions=[i + 1], widths=0.5, patch_artist=True)
        bp["boxes"][0].set_facecolor(plt.cm.Set2(i / max(len(states) - 1, 1)))
        jitter = np.random.uniform(-0.12, 0.12, len(sdata))
        ax.plot([i + 1 + j for j in jitter], sdata.values, "o",
                color="gray", alpha=0.5, markersize=4)

    ax.set_xticks(range(1, len(states) + 1))
    ax.set_xticklabels(states)
    ax.set_ylabel("Mean growing-season temperature (°C)")
    ax.set_title("Growing-season temperature by state", fontweight="bold")
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_grower_temp_boxplot.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {prefix}_grower_temp_boxplot.png ({len(valid)} fields)")


def plot_corn_soy_share(output_dir: Path, prefix: str = "cross-05") -> None:
    summaries = _gather_grower_summaries()
    state_map = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
    rows = []
    for s in summaries:
        state = state_map.get(s["grower"], "??")
        corn = s["crop_mix"].get("Corn", 0)
        soy = s["crop_mix"].get("Soybeans", 0)
        rows.append({"state": state, "corn_pct": corn, "soy_pct": soy})

    df = pd.DataFrame(rows)
    if df.empty:
        print("  No CDL data for corn/soy share")
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    states = sorted(df["state"].unique())
    x = np.arange(len(states))
    width = 0.35
    corn_vals = [df[df["state"] == s]["corn_pct"].sum() for s in states]
    soy_vals = [df[df["state"] == s]["soy_pct"].sum() for s in states]
    bars1 = ax.bar(x - width / 2, corn_vals, width, label="Corn", color="#d4a017", alpha=0.85)
    bars2 = ax.bar(x + width / 2, soy_vals, width, label="Soybeans", color="#2e8b57", alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(states)
    ax.set_ylabel("Mean share of cropland (%)")
    ax.set_title("Corn vs Soybean share by state", fontweight="bold")
    ax.legend()
    for bar in bars1:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                f"{bar.get_height():.0f}%", ha="center", fontsize=8)
    for bar in bars2:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                f"{bar.get_height():.0f}%", ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_grower_corn_soy_share.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {prefix}_grower_corn_soy_share.png")


def plot_field_size_with_std(output_dir: Path, prefix: str = "cross-03") -> None:
    pdata = _gather_per_field_data()
    valid = pdata.dropna(subset=["area_acres"]).copy()
    if valid.empty:
        print("  No boundary data for field size")
        return
    state_map = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
    valid["state"] = valid["grower"].map(state_map)

    fig, ax = plt.subplots(figsize=(8, 5))
    states = sorted(valid["state"].unique())
    means = [valid[valid["state"] == s]["area_acres"].mean() for s in states]
    stds = [valid[valid["state"] == s]["area_acres"].std() for s in states]
    counts = [len(valid[valid["state"] == s]) for s in states]
    colors = plt.cm.Set2(np.linspace(0, 1, len(states)))

    bars = ax.bar(range(len(states)), means, yerr=stds, capsize=5, color=colors,
                  alpha=0.85, edgecolor="gray")
    ax.set_xticks(range(len(states)))
    ax.set_xticklabels(states)
    ax.set_ylabel("Mean field area (acres)")
    ax.set_title("Mean field size by state", fontweight="bold")
    for i, (bar, m) in enumerate(zip(bars, means)):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + (stds[i] if stds[i] else 0) + 1,
                f"{m:.0f} ac", ha="center", fontsize=9, fontweight="bold")
    for i in range(len(states)):
        ax.text(i, 2, f"n={counts[i]}", ha="center", fontsize=7, color="white", fontweight="bold")
    ax.text(0.98, 0.95, "Bars show mean ± std dev", transform=ax.transAxes,
            fontsize=8, ha="right", va="top",
            bbox=dict(facecolor="white", alpha=0.7, edgecolor="none"))
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_grower_field_size_std.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {prefix}_grower_field_size_std.png")


def plot_grower_crop_mix(
    summaries: list[dict], output_dir: Path, prefix: str = "cross-02"
) -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    all_crops: list[str] = []
    for s in summaries:
        for c in s["crop_mix"]:
            if c not in all_crops:
                all_crops.append(c)

    bottom = np.zeros(len(summaries))
    for crop in all_crops:
        values = [s["crop_mix"].get(crop, 0) for s in summaries]
        ax.bar([s["grower"] for s in summaries], values, bottom=bottom, label=crop)
        bottom += values

    ax.set_ylabel("Average crop share (%)")
    ax.set_title("Crop Mix Composition by Grower")
    ax.legend(fontsize=7, ncol=2, loc="lower right")
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_grower_crop_mix.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {prefix}_grower_crop_mix.png")


def plot_gdd_precip_correlation_by_state(output_dir: Path, prefix: str = "cross-06") -> None:
    pdata = _gather_per_field_data()
    valid = pdata.dropna(subset=["gdd_sum", "total_gs_precip"]).copy()
    if valid.empty:
        print("  No weather data for GDD vs precip scatter")
        return
    state_map = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
    state_colors = {"IL": "#1b9e77", "IA": "#d95f02", "NE": "#7570b3"}
    valid["state"] = valid["grower"].map(state_map)
    valid["state_color"] = valid["state"].map(state_colors)
    valid["gdd_k"] = valid["gdd_sum"] / 1000

    fig, ax = plt.subplots(figsize=(8, 6))
    states = sorted(valid["state"].unique())
    for state in states:
        sdata = valid[valid["state"] == state]
        ax.scatter(sdata["gdd_k"], sdata["total_gs_precip"],
                   c=state_colors[state], s=50, alpha=0.8,
                   edgecolors="white", linewidth=0.5, label=f"{state}", zorder=3)
        if len(sdata) > 2:
            z = np.polyfit(sdata["gdd_k"], sdata["total_gs_precip"], 1)
            p = np.poly1d(z)
            x_line = np.linspace(sdata["gdd_k"].min(), sdata["gdd_k"].max(), 100)
            ax.plot(x_line, p(x_line), color=state_colors[state],
                    linewidth=1.5, linestyle="--", alpha=0.6)

    if len(valid) > 2:
        z_all = np.polyfit(valid["gdd_k"], valid["total_gs_precip"], 1)
        p_all = np.poly1d(z_all)
        x_all = np.linspace(valid["gdd_k"].min(), valid["gdd_k"].max(), 100)
        ax.plot(x_all, p_all(x_all), color="gray", linewidth=1,
                linestyle=":", alpha=0.5, label="Overall trend")

    ax.set_xlabel("Cumulative GDD (thousands °C-days)")
    ax.set_ylabel("Total growing-season precip (mm)")
    ax.set_title("GDD vs Precipitation by State", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.15)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_gdd_precip_correlation_by_state.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    counts = ", ".join(f"{s}: {len(valid[valid['state']==s])} fields" for s in states)
    print(f"  {prefix}_gdd_precip_correlation_by_state.png ({counts})")




def plot_field_area_cdf_by_state(output_dir: Path, prefix: str = "cross-07") -> None:
    pdata = _gather_per_field_data()
    valid = pdata.dropna(subset=["area_acres"]).copy()
    if valid.empty:
        print("  No boundary data for area CDF")
        return
    state_map = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
    state_colors = {"IL": "#1b9e77", "IA": "#d95f02", "NE": "#7570b3"}
    valid["state"] = valid["grower"].map(state_map)

    fig, ax = plt.subplots(figsize=(8, 6))
    states = sorted(valid["state"].unique())
    for state in states:
        sdata = valid[valid["state"] == state]["area_acres"].sort_values().reset_index(drop=True)
        n = len(sdata)
        if n == 0:
            continue
        y = np.arange(1, n + 1) / n * 100
        ax.step(sdata, y, where="post", color=state_colors[state], linewidth=2,
                label=f"{state} (n={n})", alpha=0.85)

    ax.axhline(50, color="gray", linewidth=1, linestyle="--", alpha=0.5, label="50% median")
    ax.set_xlabel("Field area (acres)")
    ax.set_ylabel("Cumulative percentage of fields (%)")
    ax.set_title("Field Area Distribution by State — Cumulative", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.15)
    ax.set_ylim(0, 105)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_field_area_cdf.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    counts = ", ".join(f"{s}: {len(valid[valid['state']==s])} fields" for s in states)
    print(f"  {prefix}_field_area_cdf.png ({counts})")

def plot_field_area_persistence_by_state(output_dir: Path, prefix: str = "cross-08") -> None:
    pdata = _gather_per_field_data()
    valid = pdata.dropna(subset=["area_acres"]).copy()
    if valid.empty:
        print("  No data for area persistence cross-state plot")
        return

    state_map = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
    state_colors = {"IL": "#1b9e77", "IA": "#d95f02", "NE": "#7570b3"}
    valid["state"] = valid["grower"].map(state_map)

    # Compute max consecutive same-crop years per field from raw CDL
    persistence = {}
    for g in _discover_growers():
        if g not in state_map:
            continue
        farms = _discover_farms(g)
        for f in farms:
            tdir = farm_tables_dir(g, f)
            for _, row in valid[valid["grower"] == g].iterrows():
                fid = str(row["field_id"])
                years = sorted(range(2021, 2026))
                last_crop = None
                consecutive = 1
                max_consecutive = 1
                for y in years:
                    cdf = _read_cdl_year(tdir, f, y)
                    if cdf.empty:
                        continue
                    fr = cdf[cdf["field_id"] == fid]
                    if fr.empty:
                        continue
                    crop = str(fr.iloc[0].get("crop_name", ""))
                    if crop == last_crop and crop:
                        consecutive += 1
                    else:
                        consecutive = 1
                    max_consecutive = max(max_consecutive, consecutive)
                    last_crop = crop
                persistence[fid] = max_consecutive

    valid["persistence"] = valid["field_id"].map(persistence)
    valid = valid.dropna(subset=["persistence"]).copy()
    if valid.empty:
        print("  No persistence data available")
        return

    fig, ax = plt.subplots(figsize=(8, 6))
    states = sorted(valid["state"].unique())
    for state in states:
        sdata = valid[valid["state"] == state]
        ax.scatter(sdata["area_acres"], sdata["persistence"],
                   c=state_colors[state], s=50, alpha=0.8,
                   edgecolors="white", linewidth=0.5, label=f"{state}", zorder=3)
        if len(sdata) > 2:
            z = np.polyfit(sdata["area_acres"], sdata["persistence"], 1)
            p = np.poly1d(z)
            x_line = np.linspace(sdata["area_acres"].min(), sdata["area_acres"].max(), 100)
            ax.plot(x_line, p(x_line), color=state_colors[state],
                    linewidth=1.5, linestyle="--", alpha=0.6)

    if len(valid) > 2:
        z_all = np.polyfit(valid["area_acres"], valid["persistence"], 1)
        p_all = np.poly1d(z_all)
        x_all = np.linspace(valid["area_acres"].min(), valid["area_acres"].max(), 100)
        ax.plot(x_all, p_all(x_all), color="gray", linewidth=1,
                linestyle=":", alpha=0.5, label="Overall trend")

    ax.set_xlabel("Field area (acres)")
    ax.set_ylabel("Max consecutive same-crop years (2021–2025)")
    ax.set_title("Field Area vs Crop Persistence by State", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.15)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_field_area_persistence.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    counts = ", ".join(f"{s}: {len(valid[valid['state']==s])} fields" for s in states)
    print(f"  {prefix}_field_area_persistence.png ({counts})")


def plot_corn_soy_years_by_state(output_dir: Path, prefix: str = "cross-09") -> None:
    state_map = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
    state_colors = {"IL": "#1b9e77", "IA": "#d95f02", "NE": "#7570b3"}

    rows = []
    for g in _discover_growers():
        if g not in state_map:
            continue
        farms = _discover_farms(g)
        for f in farms:
            tdir = farm_tables_dir(g, f)
            # Get field IDs from boundaries
            bpath = farm_boundary_path(g, f)
            if not bpath.exists():
                continue
            bgdf = gpd.read_file(bpath)
            for _, brow in bgdf.iterrows():
                fid = str(brow["field_id"])
                corn_years = 0
                soy_years = 0
                for y in range(2021, 2026):
                    cdf = _read_cdl_year(tdir, f, y)
                    if cdf.empty or "crop_name" not in cdf.columns:
                        continue
                    fr = cdf[cdf["field_id"] == fid]
                    if fr.empty:
                        continue
                    # Get the row with highest pct for this field
                    top = fr.loc[fr["pct"].idxmax()]
                    crop = str(top.get("crop_name", "")).lower()
                    if "corn" in crop:
                        corn_years += 1
                    elif "soy" in crop:
                        soy_years += 1
                rows.append({
                    "grower": g,
                    "state": state_map[g],
                    "field_id": fid,
                    "corn_years": corn_years,
                    "soy_years": soy_years,
                })

    df = pd.DataFrame(rows)
    if df.empty:
        print("  No CDL data for corn/soy years scatter")
        return

    fig, ax = plt.subplots(figsize=(7, 7))
    states = sorted(df["state"].unique())
    np.random.seed(42)
    for state in states:
        sdata = df[df["state"] == state].copy()
        # Add jitter to separate overlapping integer points
        sdata["corn_jitter"] = sdata["corn_years"] + np.random.uniform(-0.12, 0.12, len(sdata))
        sdata["soy_jitter"] = sdata["soy_years"] + np.random.uniform(-0.12, 0.12, len(sdata))
        ax.scatter(sdata["corn_jitter"], sdata["soy_jitter"],
                   c=state_colors[state], s=60, alpha=0.75,
                   edgecolors="white", linewidth=0.8, label=f"{state} (n={len(sdata)})", zorder=3)

    # Diagonal balance line (x=y)
    ax.plot([0, 5], [0, 5], color="gray", linewidth=1.5, linestyle="--", alpha=0.5, label="Perfect balance (corn=soy)")
    
    # Grid lines at integer values
    for i in range(6):
        ax.axvline(i, color="#ddd", linewidth=0.5, alpha=0.5)
        ax.axhline(i, color="#ddd", linewidth=0.5, alpha=0.5)

    ax.set_xlabel("Corn years (2021–2025)")
    ax.set_ylabel("Soybean years (2021–2025)")
    ax.set_title("Corn vs Soybean Years per Field by State", fontweight="bold")
    ax.set_xlim(-0.5, 5.5)
    ax.set_ylim(-0.5, 5.5)
    ax.set_xticks(range(6))
    ax.set_yticks(range(6))
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(True, alpha=0.1)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_corn_soy_years_by_state.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    counts = ", ".join(f"{s}: {len(df[df['state']==s])} fields" for s in states)
    print(f"  {prefix}_corn_soy_years_by_state.png ({counts})")


def plot_precip_boxplot_by_state(output_dir: Path, prefix: str = "cross-10") -> None:
    pdata = _gather_per_field_data()
    valid = pdata.dropna(subset=["total_gs_precip"]).copy()
    if valid.empty:
        print("  No weather data for precip boxplot")
        return
    state_map = {"il-grower": "IL", "ia-grower": "IA", "ne-grower": "NE"}
    state_colors = {"IL": "#1b9e77", "IA": "#d95f02", "NE": "#7570b3"}
    valid["state"] = valid["grower"].map(state_map)
    valid["mean_annual_precip"] = valid["total_gs_precip"] / 5

    fig, ax = plt.subplots(figsize=(8, 5))
    states = sorted(valid["state"].unique())
    for i, state in enumerate(states):
        sdata = valid[valid["state"] == state]["mean_annual_precip"]
        bp = ax.boxplot(sdata, positions=[i + 1], widths=0.5, patch_artist=True)
        bp["boxes"][0].set_facecolor(state_colors[state])
        bp["boxes"][0].set_alpha(0.6)
        jitter = np.random.uniform(-0.12, 0.12, len(sdata))
        ax.plot([i + 1 + j for j in jitter], sdata.values, "o",
                color="gray", alpha=0.5, markersize=4)

    ax.set_xticks(range(1, len(states) + 1))
    ax.set_xticklabels(states)
    ax.set_ylabel("Mean annual growing-season precip (mm/year)")
    ax.set_title("Growing-Season Precipitation by State", fontweight="bold")
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_precip_boxplot.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {prefix}_precip_boxplot.png ({len(valid)} fields)")




# ── Geospatial map ───────────────────────────────────────────────────

def plot_grower_fields_map(
    boundaries_gdf: gpd.GeoDataFrame,
    grower: str, farm: str,
    output_dir: Path, prefix: str = "10"
) -> None:
    fig, ax = plt.subplots(figsize=(10, 8))
    utm_gdf = boundaries_gdf.to_crs(boundaries_gdf.estimate_utm_crs())
    bounds = utm_gdf.total_bounds
    margin = max((bounds[2] - bounds[0]), (bounds[3] - bounds[1])) * 0.1

    colors = plt.cm.Set2(np.linspace(0, 1, len(utm_gdf)))
    for i, (_, row) in enumerate(utm_gdf.iterrows()):
        fid = str(row.get("field_id", f"field-{i}"))
        ac = row.get("area_acres", 0) or round(_area_from_geom(row.geometry) * ACRES_PER_SQM, 1)
        fgdf = gpd.GeoDataFrame([row], geometry="geometry", crs=utm_gdf.crs)
        fgdf.boundary.plot(ax=ax, color=colors[i], linewidth=1.5)
        fgdf.plot(ax=ax, color=colors[i], alpha=0.2)
        centroid = row.geometry.centroid
        ax.annotate(fid.split("-")[-1][:6], (centroid.x, centroid.y),
                    fontsize=6, ha="center", va="center", fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.7))

    ax.set_xlim(bounds[0] - margin, bounds[2] + margin)
    ax.set_ylim(bounds[1] - margin, bounds[3] + margin)
    ax.set_title(f"Field Map — {grower} / {farm} ({len(utm_gdf)} fields)")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_boundary_geospatial_map.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# ── Field-year weather comparison ────────────────────────────────────

def plot_weather_year_comparison(
    df: pd.DataFrame, field_id: str,
    output_dir: Path, prefix: str = "11"
) -> None:
    gs = _growing_season_df(df).copy()
    if gs.empty:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, "No growing-season data", transform=ax.transAxes, ha="center")
        fig.savefig(output_dir / f"{prefix}_weather_year_comparison.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    gs["year"] = gs["date"].dt.year
    years = sorted(gs["year"].unique())
    n = len(years)
    fig, axes = plt.subplots(n, 1, figsize=(10, 2.5 * n), sharex=False)

    if n == 1:
        axes = [axes]

    for i, year in enumerate(years):
        ax = axes[i]
        yr = gs[gs["year"] == year].set_index("date")
        ax.plot(yr.index, yr["T2M"], color=BLUE, linewidth=0.6, alpha=0.5, label="Mean temp")
        ax.fill_between(yr.index, yr["T2M_MIN"], yr["T2M_MAX"], color=BLUE, alpha=0.1, label="Min–Max range")
        rolling = yr["T2M"].rolling(7, center=True).mean()
        ax.plot(rolling.index, rolling.values, color=RED, linewidth=1.2)
        ax.axhline(30, color=ORANGE, linestyle="--", linewidth=0.6, alpha=0.5)
        ax.set_ylabel("°C")
        ax.set_ylim(-10, 40)
        ax.text(0.02, 0.9, str(year), transform=ax.transAxes, fontsize=10, fontweight="bold",
                bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8))
        if i == 0:
            ax.legend(fontsize=7, loc="upper right")
        if i < n - 1:
            ax.tick_params(labelbottom=False)

    fig.suptitle(f"Year-over-Year Growing Season Temperature — {field_id}", fontsize=12)
    fig.tight_layout()
    fig.savefig(output_dir / f"{prefix}_weather_year_comparison.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# ── Per-field runner ─────────────────────────────────────────────────

def run_field_eda(grower: str, farm: str, field: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    field_id = field

    # Weather
    wpath = field_weather_path(grower, farm, field)
    wdf = _read_weather(wpath)
    print(f"  Weather: {len(wdf)} rows")

    # CDL
    tdir = farm_tables_dir(grower, farm)
    cdl_dfs: dict[int, pd.DataFrame] = {}
    for year in range(2021, 2026):
        cdf = _read_cdl_year(tdir, farm, year)
        if not cdf.empty:
            cdl_dfs[year] = cdf
    print(f"  CDL: {len(cdl_dfs)} years")

    # Boundaries
    bpath = farm_boundary_path(grower, farm)
    bgdf = gpd.read_file(bpath) if bpath.exists() else gpd.GeoDataFrame()
    print(f"  Boundaries: {len(bgdf)} fields")

    # ── Weather plots ──
    print("  Generating weather plots...")
    plot_temp_profile(wdf, field_id, output_dir)
    plot_precip_distribution(wdf, field_id, output_dir)
    plot_gdd_precip_correlation(wdf, field_id, output_dir)

    # ── CDL plots ──
    print("  Generating CDL plots...")
    plot_rotation_timeline(cdl_dfs, field_id, output_dir)
    plot_diversity_trend(cdl_dfs, farm, output_dir)
    plot_area_persistence(cdl_dfs, bgdf, farm, field_id, output_dir)

    # ── Boundary plots ──
    print("  Generating boundary plots...")
    plot_size_distribution(bgdf, field_id, output_dir)
    plot_shape_complexity(bgdf, field_id, output_dir)

    weather_by_field: dict[str, pd.DataFrame] = {}
    for _, row in bgdf.iterrows():
        fid = str(row["field_id"])
        fp = field_weather_path(grower, farm, fid)
        weather_by_field[fid] = _read_weather(fp)
    plot_size_weather_cv(bgdf, weather_by_field, field_id, output_dir)

    # ── Geospatial map ──
    print("  Generating geospatial map...")
    plot_grower_fields_map(bgdf, grower, farm, output_dir)

    # ── Field-year weather comparison ──
    print("  Generating weather year comparison...")
    plot_weather_year_comparison(wdf, field_id, output_dir)

    print(f"  Output: {output_dir}/")


# ── Combined geospatial map (IL + IA + NE) ──────────────────────────

def plot_combined_grower_map(grower_slugs: list[str], output_dir: Path) -> None:
    farm_map = {
        "il-grower": ("il-grower-illinois", "IL", "#1b9e77"),
        "ia-grower": ("ia-grower-iowa", "IA", "#d95f02"),
        "ne-grower": ("ne-grower-nebraska", "NE", "#7570b3"),
    }
    state_fips_map = {"IL": "17", "IA": "19", "NE": "31"}
    county_fips_map = {"il-grower": "17075", "ia-grower": "19109", "ne-grower": "31047"}

    all_fields = []
    for g in grower_slugs:
        if g not in farm_map:
            continue
        fsl, abb, clr = farm_map[g]
        fips = state_fips_map[abb]
        bpath = farm_boundary_path(g, fsl)
        if not bpath.exists():
            continue
        gdf = gpd.read_file(bpath)
        gdf["grower_abb"] = abb
        gdf["grower_color"] = clr
        all_fields.append(gdf)

    if not all_fields:
        print("  Warning: no field boundaries found for combined map")
        return

    combined = pd.concat(all_fields, ignore_index=True)
    combined = combined.to_crs("EPSG:4326")

    # State boundaries
    states_gdf = gpd.GeoDataFrame()
    states_path = shared_geoadmin_states_dir() / "states_usa.geojson"
    if states_path.exists():
        all_states = gpd.read_file(states_path).to_crs("EPSG:4326")
        target_fips = list(state_fips_map.values())
        states_gdf = all_states[all_states["state_fips"].isin(target_fips)].copy()

    # County boundaries
    counties_gdf = gpd.GeoDataFrame()
    counties_path = shared_geoadmin_counties_dir() / "counties_usa.geojson"
    if counties_path.exists():
        all_counties = gpd.read_file(counties_path).to_crs("EPSG:4326")
        target_cfips = list(county_fips_map.values())
        counties_gdf = all_counties[all_counties["fips"].isin(target_cfips)].copy()

    bounds = combined.total_bounds
    margin_lon = max((bounds[2] - bounds[0]) * 0.08, 0.5)
    margin_lat = max((bounds[3] - bounds[1]) * 0.08, 0.5)

    fig, ax = plt.subplots(figsize=(14, 8))

    # State outlines
    if not states_gdf.empty:
        states_gdf.boundary.plot(ax=ax, color="#333333", linewidth=1.5, alpha=0.6)

    # County outlines
    if not counties_gdf.empty:
        counties_gdf.boundary.plot(ax=ax, color="#888888", linewidth=0.8, alpha=0.5, linestyle="--")

    # Field polygons
    for abb in ["IL", "IA", "NE"]:
        sub = combined[combined["grower_abb"] == abb]
        if sub.empty:
            continue
        color = sub.iloc[0]["grower_color"]
        sub.plot(ax=ax, color=color, alpha=0.4, edgecolor=color, linewidth=1.2, label=abb)

    # Field labels
    for _, row in combined.iterrows():
        fid = str(row.get("field_id", ""))
        centroid = row.geometry.centroid
        ax.annotate(fid, (centroid.x, centroid.y),
                    fontsize=5, ha="center", va="center", fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.6))

    ax.set_xlim(bounds[0] - margin_lon, bounds[2] + margin_lon)
    ax.set_ylim(bounds[1] - margin_lat, bounds[3] + margin_lat)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Combined Grower Field Map — IL / IA / NE (30 fields)", fontsize=13)
    ax.legend(fontsize=10, loc="lower right")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_dir / "cross-04_combined_grower_map.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    count_strs = [f"{abb}: {len(combined[combined['grower_abb']==abb])} fields" for abb in ["IL", "IA", "NE"]]
    print(f"  Combined map: {', '.join(count_strs)}")


# ── Cross-grower runner ────────────────────────────────────────────────

def run_cross_grower(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = _gather_grower_summaries()
    if not summaries:
        print("  No growers found for cross-grower comparison")
        return
    grower_slugs = [s["grower"] for s in summaries]
    print(f"  Comparing {len(summaries)} growers: {grower_slugs}")
    plot_temp_boxplot_by_state(output_dir)
    plot_grower_crop_mix(summaries, output_dir)
    plot_field_size_with_std(output_dir)
    plot_combined_grower_map(grower_slugs, output_dir)
    plot_corn_soy_share(output_dir)
    plot_gdd_precip_correlation_by_state(output_dir)
    plot_field_area_cdf_by_state(output_dir)
    plot_field_area_persistence_by_state(output_dir)
    plot_corn_soy_years_by_state(output_dir)
    plot_precip_boxplot_by_state(output_dir)
    print(f"  Output: {output_dir}/")


def run_all_fields(grower: str, data_root: Path) -> None:
    farms = _discover_farms(grower)
    if not farms:
        print(f"  No farms found for {grower}")
        return
    for farm in farms:
        bpath = farm_boundary_path(grower, farm)
        if not bpath.exists():
            continue
        bgdf = gpd.read_file(bpath)
        for _, frow in bgdf.iterrows():
            fid = str(frow["field_id"])
            out = data_root / "data-pipeline" / "growers" / grower / "farms" / farm \
                  / "fields" / fid / "derived" / "reports" / "eda"
            print(f"  {grower}/{farm}/{fid} -> {out}")
            run_field_eda(grower, farm, fid, out)


def write_summary_csv(grower: str, data_root: Path) -> None:
    pdata = _gather_per_field_data()
    if pdata.empty:
        print(f"  No data for {grower}")
        return
    gdata = pdata[pdata["grower"] == grower]
    if gdata.empty:
        print(f"  No data for {grower}")
        return
    out_dir = data_root / "data-pipeline" / "growers" / grower / "derived" / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "eda_field_summary.csv"
    gdata.to_csv(out_path, index=False)
    print(f"  Wrote {out_path} ({len(gdata)} fields)")


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Field-level EDA: weather, CDL, boundaries")
    parser.add_argument("--grower", default=None, help="Grower slug")
    parser.add_argument("--farm", default=None, help="Farm slug")
    parser.add_argument("--field", default=None, help="Field slug")
    parser.add_argument("--all-fields", action="store_true", help="Run EDA for all fields in a grower")
    parser.add_argument("--summary-csv", action="store_true", help="Write per-grower summary CSV")
    parser.add_argument("--cross-grower", action="store_true", help="Cross-grower comparison")
    parser.add_argument(
        "--data-root",
        default=os.environ.get("DATA_PIPELINE_DATA_ROOT", ""),
    )
    parser.add_argument("--output", default=None, help="Output directory override")
    args = parser.parse_args()

    if not args.data_root:
        parser.error("DATA_PIPELINE_DATA_ROOT is not set")

    data_root = Path(args.data_root)
    if not (data_root / "data-pipeline").is_dir():
        parser.error(f"data-pipeline not found under {data_root}")

    os.environ["DATA_PIPELINE_DATA_ROOT"] = str(data_root)
    if str(data_root / "data-pipeline" / "src" / "scripts" / "lib") not in sys.path:
        sys.path.insert(0, str(data_root / "data-pipeline" / "src" / "scripts" / "lib"))
    if str(data_root / "data-pipeline" / "src" / "scripts") not in sys.path:
        sys.path.insert(0, str(data_root / "data-pipeline" / "src" / "scripts"))

    print("=" * 60)
    print("Field-Level EDA")
    print("=" * 60)

    if args.cross_grower:
        out = Path(args.output) if args.output else data_root / "data-pipeline" / "eda-cross-grower"
        print(f"\nCross-grower comparison -> {out}")
        run_cross_grower(out)
        print("\nDone.")
        return

    if args.all_fields:
        if not args.grower:
            parser.error("--grower is required with --all-fields")
        print(f"\nRunning EDA for all fields of {args.grower}...")
        run_all_fields(args.grower, data_root)
        print("\nDone.")
        return

    if args.summary_csv:
        if not args.grower:
            parser.error("--grower is required with --summary-csv")
        print(f"\nWriting summary CSV for {args.grower}...")
        write_summary_csv(args.grower, data_root)
        print("\nDone.")
        return

    if not all([args.grower, args.farm, args.field]):
        parser.error("--grower, --farm, --field are required (or use --cross-grower / --all-fields / --summary-csv)")

    out = Path(args.output) if args.output else (
        data_root / "data-pipeline" / "growers" / args.grower / "farms" / args.farm
        / "fields" / args.field / "derived" / "reports" / "eda"
    )
    print(f"\nPer-field EDA -> {out}")
    run_field_eda(args.grower, args.farm, args.field, out)
    print("\nDone.")


if __name__ == "__main__":
    main()
