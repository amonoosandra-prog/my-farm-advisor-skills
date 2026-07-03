#!/usr/bin/env python3
"""Generate field-season weather & NDVI storyline dashboards with notable event detection."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask

matplotlib.use("Agg")


# ── Constants ────────────────────────────────────────────────────────

GDD_BASE = 10.0
GS_MONTHS = list(range(4, 11))  # Apr–Oct
YEAR_COLORS = plt.cm.Set2(np.linspace(0, 1, 5))
YEAR_MAP = {2021: 0, 2022: 1, 2023: 2, 2024: 3, 2025: 4}

# Event detection thresholds
HEAVY_RAIN_MM = 40.0
PROLONGED_WET_MM = 10.0
PROLONGED_WET_DAYS = 3
HOT_DAY_MAX = 35.0
HEAT_WAVE_MEAN = 28.0
HEAT_WAVE_DAYS = 5
COOL_PERIOD_MEAN = 15.0
COOL_PERIOD_DAYS = 5
NDVI_GREENUP_DELTA = 0.20
NDVI_DIP_DELTA = 0.12

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
    return df.sort_values("date").reset_index(drop=True)


# ── Weather processing ──────────────────────────────────────────────

def read_weather(grower: str, farm: str, field: str) -> pd.DataFrame:
    path = field_weather_path(grower, farm, field)
    if not path.exists():
        raise FileNotFoundError(f"Weather file not found: {path}")
    df = pd.read_csv(path, parse_dates=["date"])
    return df


def compute_gdd(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["gdd"] = ((df["T2M_MIN"] + df["T2M_MAX"]) / 2 - GDD_BASE).clip(lower=0)
    df["gdd_cumul"] = df.groupby(df["date"].dt.year)["gdd"].cumsum()
    return df


# ── Data validation ─────────────────────────────────────────────────

def validate_data(weather_df: pd.DataFrame, ndvi_df: pd.DataFrame,
                  field_id: str) -> dict:
    """Check for missing or sparse dates and return a validation report."""
    report = {"status": "pass", "warnings": [], "field_id": field_id}
    
    for year in range(2021, 2026):
        # Weather completeness
        wdf = weather_df[weather_df["date"].dt.year == year]
        gs = wdf[wdf["date"].dt.month.isin(GS_MONTHS)]
        
        if not gs.empty:
            date_range = pd.date_range(
                start=gs["date"].min(), end=gs["date"].max(), freq="D"
            )
            missing_dates = date_range.difference(gs["date"])
            if len(missing_dates) > 3:
                report["warnings"].append(
                    f"{year}: {len(missing_dates)} missing weather dates"
                )
                report["status"] = "warn"
            
            for col in ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"]:
                n_nan = gs[col].isna().sum()
                if n_nan > 0:
                    report["warnings"].append(
                        f"{year}: {n_nan} NaN values in {col}"
                    )
                    report["status"] = "warn"
        
        # NDVI completeness
        ndvi_yr = ndvi_df[ndvi_df["year"] == year]
        gs_ndvi = ndvi_yr[ndvi_yr["date"].dt.month.isin(GS_MONTHS)]
        
        if len(gs_ndvi) < 3:
            report["warnings"].append(
                f"{year}: Only {len(gs_ndvi)} growing-season NDVI scenes (sparse)"
            )
            report["status"] = "warn"
        
        # Check peak-season coverage (Jun–Aug)
        peak = ndvi_yr[ndvi_yr["date"].dt.month.isin([6, 7, 8])]
        if peak.empty:
            report["warnings"].append(
                f"{year}: No NDVI scenes in Jun–Aug (missing peak)"
            )
            report["status"] = "warn"
    
    return report


def compute_growing_season_window(weather_df: pd.DataFrame,
                                  ndvi_df: pd.DataFrame) -> tuple[int, int]:
    """Compute shared DOY window from actual data across all years."""
    all_doy = []
    
    for year in range(2021, 2026):
        wdf = weather_df[weather_df["date"].dt.year == year]
        gs = wdf[wdf["date"].dt.month.isin(GS_MONTHS)]
        if not gs.empty:
            all_doy.extend(gs["date"].dt.dayofyear.tolist())
        
        ndvi_yr = ndvi_df[ndvi_df["year"] == year]
        if not ndvi_yr.empty:
            all_doy.extend(ndvi_yr["date"].dt.dayofyear.tolist())
    
    if not all_doy:
        return (60, 330)  # Fallback
    
    doy_min = max(min(all_doy) - 10, 1)
    doy_max = min(max(all_doy) + 10, 366)
    return (doy_min, doy_max)


def format_doy_axis(ax, doy_range: tuple[int, int]):
    """Format x-axis with month labels aligned to DOY."""
    month_doy = {
        91: "Apr", 121: "May", 152: "Jun",
        182: "Jul", 213: "Aug", 244: "Sep", 274: "Oct",
    }
    ticks = [doy for doy in month_doy if doy_range[0] <= doy <= doy_range[1]]
    labels = [month_doy[t] for t in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    ax.set_xlim(doy_range)


# ── CDL crop labels ─────────────────────────────────────────────────

def read_crop_labels(grower: str, farm: str, field: str) -> dict[int, str]:
    labels = {}
    for year in range(2021, 2026):
        path = cdl_year_path(grower, farm, year)
        if not path.exists():
            continue
        cdf = pd.read_csv(path)
        fr = cdf[cdf["field_id"] == field]
        if fr.empty:
            continue
        dominant = fr.loc[fr["pct"].idxmax()]
        labels[year] = str(dominant["crop_name"])
    return labels


# ── Strategy guide loading (optional, graceful fallback) ────────────

def load_strategy_wording(strategy_path: Path, crop: str) -> dict[str, str]:
    """Load event wording from optional strategy guide. Returns empty dict if not found."""
    if not strategy_path or not strategy_path.exists():
        return {}
    try:
        text = strategy_path.read_text()
        wording = {}
        # Extract generic agronomic phrases based on crop
        if "corn" in crop.lower():
            if "32" in text and "silking" in text.lower():
                wording["heat_stress"] = "Temperatures above 32°C during silking reduce kernel set."
            if "GDD" in text and "1200" in text:
                wording["gdd_target"] = "Full-season corn target: ~1,200–1,400 GDD."
        if "soy" in crop.lower():
            if "GDD" in text and "1000" in text:
                wording["gdd_target"] = "Soybean maturity target: ~1,000–1,200 GDD."
        return wording
    except Exception:
        return {}


def get_event_caption(event: dict, strategy_wording: dict) -> str:
    """Build event caption using strategy wording or generic fallback."""
    etype = event["type"]
    val = event.get("value", 0)
    
    # Generic fallbacks
    fallbacks = {
        "heavy_rain": f"Heavy rain — {val:.1f} mm",
        "prolonged_wet": "Prolonged wet period",
        "hot_day": f"Hot day — {val:.1f}°C",
        "heat_wave": "Heat wave",
        "cool_period": "Cool period",
        "rapid_greenup": f"Rapid green-up (+{val:.2f} NDVI)",
        "ndvi_dip": f"NDVI dip (-{abs(val):.2f})",
        "season_peak": f"NDVI peak ({val:.3f})",
    }
    
    caption = fallbacks.get(etype, etype.replace("_", " "))
    
    # Override with strategy wording if available
    if etype == "hot_day" and "heat_stress" in strategy_wording:
        caption = strategy_wording["heat_stress"] + f" ({val:.1f}°C)"
    if etype == "season_peak" and "gdd_target" in strategy_wording:
        caption = strategy_wording["gdd_target"] + f" Peak NDVI: {val:.3f}"
    
    return caption


# ── Event detection ─────────────────────────────────────────────────

def detect_notable_events(ndvi_df: pd.DataFrame, weather_df: pd.DataFrame,
                          crop_labels: dict[int, str]) -> list[dict]:
    """Detect notable weather and NDVI events across all years."""
    events = []
    
    for year in range(2021, 2026):
        # Weather events
        wdf = weather_df[(weather_df["date"].dt.year == year) &
                         (weather_df["date"].dt.month.isin(GS_MONTHS))].copy()
        if wdf.empty:
            continue
        wdf["doy"] = wdf["date"].dt.dayofyear
        
        # Heavy rain
        heavy = wdf[wdf["PRECTOTCORR"] > HEAVY_RAIN_MM]
        for _, row in heavy.iterrows():
            events.append({
                "type": "heavy_rain", "year": year, "doy": int(row["doy"]),
                "panel": "precip", "severity": "high", "value": row["PRECTOTCORR"],
                "label": f"{row['PRECTOTCORR']:.0f}mm",
            })
        
        # Prolonged wet (skipped — medium severity, shown in caption only if needed)
        
        # Hot days — only the max temp day from each contiguous block
        hot_mask = wdf["T2M_MAX"] > HOT_DAY_MAX
        if hot_mask.any():
            hot_blocks = []
            block_start = None
            block_rows = []
            for _, row in wdf.iterrows():
                if row["T2M_MAX"] > HOT_DAY_MAX:
                    if block_start is None:
                        block_start = int(row["doy"])
                    block_rows.append(row)
                else:
                    if block_rows:
                        max_row = max(block_rows, key=lambda r: r["T2M_MAX"])
                        hot_blocks.append(max_row)
                    block_start = None
                    block_rows = []
            if block_rows:
                max_row = max(block_rows, key=lambda r: r["T2M_MAX"])
                hot_blocks.append(max_row)
            for row in hot_blocks:
                events.append({
                    "type": "hot_day", "year": year, "doy": int(row["doy"]),
                    "panel": "temp", "severity": "high", "value": row["T2M_MAX"],
                    "label": f"{row['T2M_MAX']:.0f}°C",
                })
        
        # Heat wave (skipped — medium severity)
        
        # Cool period (skipped — medium severity)
        
        # NDVI events
        ndvi_year = ndvi_df[ndvi_df["year"] == year].copy().reset_index(drop=True)
        if len(ndvi_year) < 2:
            continue
        
        # Compute differences
        ndvi_year["ndvi_diff"] = ndvi_year["mean_ndvi"].diff()
        ndvi_year["doy"] = ndvi_year["date"].dt.dayofyear
        
        # Data-relative thresholds for this field-year
        ndvi_range = ndvi_year["mean_ndvi"].max() - ndvi_year["mean_ndvi"].min()
        greenup_threshold = max(0.15, ndvi_range * 0.25)
        dip_threshold = max(0.08, ndvi_range * 0.15)
        
        # Rapid green-up — only the largest per year
        greenup = ndvi_year[ndvi_year["ndvi_diff"] >= greenup_threshold]
        if not greenup.empty:
            max_row = greenup.loc[greenup["ndvi_diff"].idxmax()]
            events.append({
                "type": "rapid_greenup", "year": year, "doy": int(max_row["doy"]),
                "panel": "ndvi", "severity": "high", "value": max_row["ndvi_diff"],
                "label": f"+{max_row['ndvi_diff']:.2f}",
            })
        
        # NDVI dip — only the largest per year
        dip = ndvi_year[ndvi_year["ndvi_diff"] <= -dip_threshold]
        if not dip.empty:
            min_row = dip.loc[dip["ndvi_diff"].idxmin()]
            events.append({
                "type": "ndvi_dip", "year": year, "doy": int(min_row["doy"]),
                "panel": "ndvi", "severity": "high", "value": min_row["ndvi_diff"],
                "label": f"{min_row['ndvi_diff']:.2f}",
            })
        
        # Season peak
        peak_idx = ndvi_year["mean_ndvi"].idxmax()
        peak_row = ndvi_year.iloc[peak_idx]
        events.append({
            "type": "season_peak", "year": year, "doy": int(peak_row["doy"]),
            "panel": "ndvi", "severity": "low", "value": peak_row["mean_ndvi"],
            "label": f"Peak {peak_row['mean_ndvi']:.2f}",
        })
    
    # Sort by severity then year/doy
    severity_order = {"high": 0, "medium": 1, "low": 2}
    events.sort(key=lambda e: (severity_order.get(e["severity"], 1), e["year"], e["doy"]))
    return events


# ── Dashboard plotting ──────────────────────────────────────────────

def annotate_panel(ax, events, panel_name, year_colors, year_map,
                   doy_range=(60, 330), max_annotations=2):
    """Add annotations to a single panel, limiting to top events per year."""
    panel_events = [ev for ev in events if ev["panel"] == panel_name]
    
    # Group by year, only annotate HIGH severity events, top N per year
    for year in sorted(year_map.keys()):
        year_events = [ev for ev in panel_events if ev["year"] == year and ev["severity"] == "high"]
        if not year_events:
            continue
        year_events = year_events[:max_annotations]
        color = year_colors[year_map[year]]
        
        for i, ev in enumerate(year_events):
            doy = ev["doy"]
            val = ev.get("value", 0)
            label = ev["label"]
            
            # Determine y-position based on panel type
            if panel_name == "ndvi":
                if ev["type"] == "season_peak":
                    y = val
                    offset_y = 0.08
                elif ev["type"] in ("rapid_greenup", "ndvi_dip"):
                    y = val
                    offset_y = 0.05 if ev["type"] == "rapid_greenup" else -0.05
                else:
                    y = val
                    offset_y = 0.05
                # Keep annotations within doy_range
                offset_x = min(15 + (i * 8), doy_range[1] - doy - 5)
            elif panel_name == "temp":
                y = val if ev["type"] == "hot_day" else 30
                offset_y = 2 + (i * 1.5)
                offset_x = min(12 + (i * 6), doy_range[1] - doy - 5)
            elif panel_name == "precip":
                y = val if ev["type"] == "heavy_rain" else 20
                offset_y = 5 + (i * 3)
                offset_x = min(12 + (i * 6), doy_range[1] - doy - 5)
            else:
                continue
            
            ax.annotate(
                label,
                xy=(doy, y),
                xytext=(doy + offset_x, y + offset_y),
                arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
                fontsize=6.5,
                fontweight="bold",
                color="#333",
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color,
                          alpha=0.9, linewidth=1.5),
                zorder=10,
            )


def build_caption_text(events, crop_labels, strategy_wording):
    """Build caption text string for the top caption area."""
    lines = []
    for year in range(2021, 2026):
        year_events = [ev for ev in events if ev["year"] == year and ev["severity"] == "high"]
        if not year_events:
            continue
        crop = crop_labels.get(year, "Unknown")
        # Take top 3 HIGH severity events for caption
        top_events = year_events[:3]
        event_strs = []
        for i, ev in enumerate(top_events, 1):
            caption = get_event_caption(ev, strategy_wording)
            doy_str = f"DOY{ev['doy']}"
            event_strs.append(f"{i}. {doy_str}: {caption}")
        lines.append(f"{year} ({crop[:3]}): " + " | ".join(event_strs))
    return "\n".join(lines)


def plot_storyline(
    ndvi_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    crop_labels: dict[int, str],
    field_id: str,
    year: int,
    output_path: Path,
    events: list[dict] | None = None,
    strategy_wording: dict[str, str] | None = None,
    doy_range: tuple[int, int] | None = None,
) -> None:
    """Generate enhanced multi-panel storyline dashboard with event annotations."""
    
    if doy_range is None:
        doy_range = compute_growing_season_window(weather_df, ndvi_df)
    
    fig = plt.figure(figsize=(16, 20))
    gs = gridspec.GridSpec(5, 1, height_ratios=[0.06, 1, 1, 1, 1],
                           hspace=0.12, top=0.93, bottom=0.03)
    
    ax_caption = fig.add_subplot(gs[0])
    ax_ndvi = fig.add_subplot(gs[1])
    ax_precip = fig.add_subplot(gs[2])
    ax_temp = fig.add_subplot(gs[3])
    ax_gdd = fig.add_subplot(gs[4])
    
    # Hide caption axis
    ax_caption.axis("off")
    
    # Build and render caption
    if events:
        caption_text = build_caption_text(events, crop_labels, strategy_wording or {})
        ax_caption.text(
            0.02, 0.5, caption_text,
            transform=ax_caption.transAxes,
            fontsize=8, va="center", ha="left",
            fontfamily="monospace",
            linespacing=1.4,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#f8f9fa",
                      edgecolor="#dee2e6", linewidth=1),
        )
    
    crop = crop_labels.get(year, "Unknown")
    color = YEAR_COLORS[YEAR_MAP[year]]
    
    # ── Panel 1: NDVI ──
    ax = ax_ndvi
    ydf = ndvi_df[ndvi_df["year"] == year].copy()
    ydf["doy"] = ydf["date"].dt.dayofyear
    ax.scatter(ydf["doy"], ydf["mean_ndvi"], c=[color], s=45, alpha=0.85,
               edgecolors="white", linewidth=0.5,
               label=f"{year} ({crop})", zorder=3)
    ax.plot(ydf["doy"], ydf["mean_ndvi"], color=color, linewidth=0.9,
            alpha=0.5, zorder=2)
    
    if events:
        annotate_panel(ax, events, "ndvi", YEAR_COLORS, YEAR_MAP, doy_range)
    
    ax.set_ylabel("Mean NDVI", fontsize=11, fontweight="bold")
    ax.set_title(f"1. NDVI Time Series — {year} ({crop})", fontsize=12, fontweight="bold", pad=8, loc="left")
    ax.legend(fontsize=8, ncol=3, loc="lower right")
    ax.set_ylim(0, 1.0)
    ax.grid(True, alpha=0.15)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    format_doy_axis(ax, doy_range)
    
    # ── Panel 2: Precipitation ──
    ax_precip_panel = ax_precip
    ax_cumul = ax_precip.twinx()
    
    ydf = weather_df[(weather_df["date"].dt.year == year) &
                     (weather_df["date"].dt.month.isin(GS_MONTHS))].copy()
    ydf["doy"] = ydf["date"].dt.dayofyear
    ax_precip_panel.bar(ydf["doy"], ydf["PRECTOTCORR"], color=color,
                        alpha=0.25, width=1.0)
    cumul = ydf["PRECTOTCORR"].cumsum()
    ax_cumul.plot(ydf["doy"], cumul, color=color, linewidth=1.2,
                  linestyle="--", alpha=0.6)
    
    if events:
        annotate_panel(ax_precip_panel, events, "precip", YEAR_COLORS, YEAR_MAP, doy_range)
    
    ax_precip_panel.set_ylabel("Daily precip (mm)", fontsize=11, fontweight="bold")
    ax_cumul.set_ylabel("Cumulative (mm)", fontsize=10, color="#555")
    ax_precip_panel.set_title(f"2. Daily Precipitation — {year} ({crop})", fontsize=12,
                               fontweight="bold", pad=8, loc="left")
    ax_precip_panel.grid(True, alpha=0.15)
    format_doy_axis(ax_precip_panel, doy_range)
    
    # ── Panel 3: Temperature / Extremes ──
    ax = ax_temp
    ydf = weather_df[(weather_df["date"].dt.year == year) &
                     (weather_df["date"].dt.month.isin(GS_MONTHS))].copy()
    ydf["doy"] = ydf["date"].dt.dayofyear
    ax.fill_between(ydf["doy"], ydf["T2M_MIN"], ydf["T2M_MAX"],
                    color=color, alpha=0.1)
    ax.plot(ydf["doy"], ydf["T2M"], color=color, linewidth=0.8, alpha=0.6,
            label=str(year))
    
    ax.axhline(30, color="red", linestyle="--", linewidth=1, alpha=0.4,
               label="30°C heat stress")
    
    if events:
        annotate_panel(ax, events, "temp", YEAR_COLORS, YEAR_MAP, doy_range)
    
    ax.set_ylabel("Temperature (°C)", fontsize=11, fontweight="bold")
    ax.set_title(f"3. Daily Temperature & Extremes — {year}", fontsize=12, fontweight="bold", pad=8, loc="left")
    ax.legend(fontsize=8, ncol=3, loc="upper right")
    ax.grid(True, alpha=0.15)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    format_doy_axis(ax, doy_range)
    
    # ── Panel 4: Cumulative GDD ──
    ax = ax_gdd
    ydf = weather_df[(weather_df["date"].dt.year == year) &
                     (weather_df["date"].dt.month.isin(GS_MONTHS))].copy()
    ydf["doy"] = ydf["date"].dt.dayofyear
    ax.plot(ydf["doy"], ydf["gdd_cumul"], color=color, linewidth=1.8,
            alpha=0.85, label=f"{year} ({crop})")
    
    ax.set_xlabel("Day of Year", fontsize=11, fontweight="bold")
    ax.set_ylabel("Cumulative GDD (°C-days, base 10°C)", fontsize=11, fontweight="bold")
    ax.set_title(f"4. Cumulative Growing Degree Days — {year} ({crop})", fontsize=12, fontweight="bold", pad=8, loc="left")
    ax.legend(fontsize=8, ncol=3, loc="lower right")
    ax.grid(True, alpha=0.15)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    format_doy_axis(ax, doy_range)
    
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
    parser.add_argument("--strategy-guide", default=None,
                        help="Optional path to crop strategy guide markdown")
    args = parser.parse_args()
    
    field_id = args.field
    grower = args.grower
    farm = args.farm
    
    if args.output:
        out_dir = Path(args.output)
    else:
        out_dir = _data_root() / "growers" / grower / "farms" / farm / "fields" / field_id / "derived" / "reports" / "storylines"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print(f"Field-Season Storyline: {field_id}")
    print("=" * 60)
    
    # 1. Extract NDVI
    print("\n1. Extracting NDVI timeseries from Sentinel-2 scenes...")
    ndvi_df = extract_field_ndvi_timeseries(grower, farm, field_id)
    print(f"   Found {len(ndvi_df)} scenes across {ndvi_df['year'].nunique()} years")
    ndvi_csv = out_dir / f"{field_id}_ndvi_timeseries.csv"
    ndvi_df.to_csv(ndvi_csv, index=False)
    print(f"   Saved: {ndvi_csv}")
    
    # 2. Read weather
    print("\n2. Reading weather data...")
    weather_df = read_weather(grower, farm, field_id)
    weather_df = compute_gdd(weather_df)
    print(f"   Read {len(weather_df)} daily records")
    
    # 3. Read crop labels
    print("\n3. Reading CDL crop labels...")
    crop_labels = read_crop_labels(grower, farm, field_id)
    for year, crop in sorted(crop_labels.items()):
        print(f"   {year}: {crop}")
    
    # 4. Validate data completeness
    print("\n4. Validating data completeness...")
    validation = validate_data(weather_df, ndvi_df, field_id)
    print(f"   Status: {validation['status']}")
    if validation["warnings"]:
        for w in validation["warnings"]:
            print(f"   ⚠ {w}")
    else:
        print("   ✓ All checks passed")
    
    # 5. Compute shared growing-season window
    print("\n5. Computing growing-season DOY window...")
    doy_range = compute_growing_season_window(weather_df, ndvi_df)
    print(f"   DOY range: {doy_range[0]}–{doy_range[1]}")
    
    # 6. Load optional strategy wording
    strategy_wording = {}
    if args.strategy_guide:
        strategy_path = Path(args.strategy_guide)
        print(f"\n4. Loading strategy guide: {strategy_path}")
        strategy_wording = load_strategy_wording(strategy_path, crop_labels.get(2022, "corn"))
        if strategy_wording:
            print(f"   Loaded {len(strategy_wording)} wording phrases")
        else:
            print("   No strategy phrases found, using generic wording")
    
    # 7. Detect notable events
    print("\n7. Detecting notable events...")
    events = detect_notable_events(ndvi_df, weather_df, crop_labels)
    print(f"   Detected {len(events)} events")
    for ev in sorted(events, key=lambda e: (e["year"], e["doy"])):
        print(f"    {ev['year']} DOY {ev['doy']:3d} [{ev['severity']:>6}] {ev['label']}")
    
    # 8. Generate per-year dashboards
    print("\n8. Generating per-year storyline dashboards...")
    years = sorted(ndvi_df["year"].unique())
    for year in years:
        print(f"\n  Year {year} — {crop_labels.get(year, 'Unknown')}")
        year_events = [e for e in events if e["year"] == year]
        dashboard_path = out_dir / f"{field_id}_storyline_{year}.png"
        plot_storyline(ndvi_df, weather_df, crop_labels, field_id, year, dashboard_path,
                       events=year_events, strategy_wording=strategy_wording,
                       doy_range=doy_range)
    
    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Field: {field_id}")
    print(f"  NDVI scenes: {len(ndvi_df)}")
    for year in years:
        n = len(ndvi_df[ndvi_df["year"] == year])
        crop = crop_labels.get(year, "Unknown")
        print(f"    {year}: {n:2d} scenes ({crop})")
    print(f"  Events detected: {len(events)}")
    print(f"  Output: {out_dir}")
    print("\nDone.")


if __name__ == "__main__":
    main()
