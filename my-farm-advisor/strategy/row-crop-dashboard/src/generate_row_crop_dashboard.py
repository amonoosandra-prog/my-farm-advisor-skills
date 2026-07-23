#!/usr/bin/env python3
"""Generate a grower-level Row Crop Intelligence Dashboard.

Produces a self-contained interactive HTML dashboard integrating field boundaries,
weather, NDVI, and soil health across all fields in a farm.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PLOTLY_VERSION = "2.35.2"
PLOTLY_URL = f"https://cdn.plot.ly/plotly-{PLOTLY_VERSION}.min.js"
CACHE_DIR = Path.home() / ".cache" / "my-farm-advisor"
COLOR_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]
GDD_BASE_C = 10.0
MM_TO_INCH = 0.0393701


# ── Helpers ───────────────────────────────────────────────────────────

def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower().replace("_", "-")).strip("-")


def _color_hash(field_id: str) -> str:
    h = hashlib.md5(field_id.encode()).hexdigest()
    return COLOR_PALETTE[int(h[:8], 16) % len(COLOR_PALETTE)]


def _ensure_cache_dir(*parts: str) -> Path:
    d = CACHE_DIR.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Data discovery ────────────────────────────────────────────────────

def _resolve_runtime_base() -> Path:
    env = os.environ.get("DATA_PIPELINE_DATA_ROOT")
    if env:
        p = Path(env).expanduser().resolve() / "data-pipeline"
        if p.is_dir():
            return p
    home = Path.home()
    for candidate in sorted(home.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        p = candidate / "data-pipeline"
        if p.is_dir():
            return p
    raise RuntimeError(
        "Cannot find runtime data directory. Set DATA_PIPELINE_DATA_ROOT."
    )


def _load_field_boundaries(farm_dir: Path) -> pd.DataFrame:
    path = farm_dir / "boundary" / "field_boundaries.geojson"
    if not path.exists():
        raise FileNotFoundError(f"Field boundaries not found: {path}")

    try:
        import geopandas as gpd
        gdf = gpd.read_file(str(path))
        if gdf.empty:
            raise ValueError("Boundary file contains no features")
        if "field_id" not in gdf.columns:
            str_cols = [c for c in gdf.columns if gdf[c].dtype == object]
            gdf["field_id"] = gdf[str_cols[0]].astype(str) if str_cols else [f"field-{i}" for i in range(len(gdf))]
        if "area_acres" not in gdf.columns:
            gdf = gdf.to_crs("EPSG:5070")
            gdf["area_acres"] = gdf.area * 0.000247105
        return gdf
    except ImportError:
        raise RuntimeError("geopandas is required to read field boundaries")


def _read_ndvi_card_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_soil_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
        if df.empty:
            return {}
        row = df.iloc[0].to_dict()
        return {k: v for k, v in row.items() if pd.notna(v)}
    except Exception:
        return {}


def _read_weather_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        return df
    except Exception:
        return pd.DataFrame()


def _read_farm_json(farm_dir: Path) -> dict[str, Any]:
    path = farm_dir / "farm.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ── Soil Health Score ─────────────────────────────────────────────────

DRAINAGE_RATING = {
    "well drained": 100,
    "moderately well drained": 80,
    "somewhat poorly drained": 60,
    "poorly drained": 40,
    "very poorly drained": 20,
    "excessively drained": 70,
    "somewhat excessively drained": 55,
}


def compute_soil_health_score(soil: dict[str, Any]) -> dict[str, float]:
    om_pct = float(soil.get("avg_om_pct", 0) or 0)
    ph = float(soil.get("avg_ph", 7.0) or 7.0)
    cec = float(soil.get("avg_cec", 0) or 0)
    drainage_raw = str(soil.get("drainage_class", "")).strip().lower()

    om_score = min(100, om_pct / 8.0 * 100)
    ph_score = max(0, 100 - abs(ph - 6.5) * 40)
    drainage_score = next(
        (v for k, v in DRAINAGE_RATING.items() if k in drainage_raw),
        50,
    )
    cec_score = min(100, cec / 40.0 * 100)

    total = (
        0.35 * om_score +
        0.25 * ph_score +
        0.20 * drainage_score +
        0.20 * cec_score
    )

    return {
        "score": round(total, 1),
        "om_score": round(om_score, 1),
        "ph_score": round(ph_score, 1),
        "drainage_score": round(drainage_score, 1),
        "cec_score": round(cec_score, 1),
        "om_pct": round(om_pct, 2),
        "ph": round(ph, 2),
        "cec": round(cec, 1),
        "drainage_class": drainage_raw,
    }


# ── GDD computation ───────────────────────────────────────────────────

def compute_field_year_gdd(weather_df: pd.DataFrame, year: int) -> list[dict[str, Any]]:
    ydf = weather_df[weather_df["date"].dt.year == year].copy()
    if ydf.empty:
        return []
    ydf = ydf.dropna(subset=["T2M_MAX", "T2M_MIN", "PRECTOTCORR"])
    ydf = ydf.sort_values("date").reset_index(drop=True)
    ydf["doy"] = ydf["date"].dt.dayofyear
    ydf["gdd"] = ((ydf["T2M_MAX"] + ydf["T2M_MIN"]) / 2 - GDD_BASE_C).clip(lower=0)
    ydf["gdd_cumul"] = ydf["gdd"].cumsum()
    ydf["precip_in"] = ydf["PRECTOTCORR"] * MM_TO_INCH
    ydf["precip_cumul"] = ydf["precip_in"].cumsum()
    return ydf[["doy", "gdd", "gdd_cumul", "precip_in", "precip_cumul"]].to_dict("records")


# ── Plotly vendoring ──────────────────────────────────────────────────

def vendor_plotly(cache_dir: str | Path | None = None) -> str:
    cache = Path(cache_dir) if cache_dir else _ensure_cache_dir("plotly")
    cache_file = cache / f"plotly-{PLOTLY_VERSION}.min.js"
    if cache_file.exists():
        content = cache_file.read_text(encoding="utf-8")
        if len(content) > 1000:
            return content

    import requests
    print(f"  Downloading Plotly v{PLOTLY_VERSION}...")
    resp = requests.get(PLOTLY_URL, timeout=30)
    resp.raise_for_status()
    content = resp.text
    cache_file.write_text(content, encoding="utf-8")
    print(f"  Cached Plotly bundle ({len(content)} bytes)")
    return content


# ── Dashboard HTML builder ────────────────────────────────────────────

DASHBOARD_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;background:#f0f2f5;color:#1e293b}
.header{background:linear-gradient(135deg,#0f172a,#1e3a5f);color:#fff;padding:1.25rem 2rem}
.header h1{font-size:1.5rem;font-weight:700;margin:0}
.header .subtitle{font-size:0.9rem;color:#94a3b8;margin-top:0.25rem}
.nav{background:#fff;border-bottom:1px solid #e2e8f0;display:flex;flex-wrap:wrap;padding:0 1rem}
.nav button{background:transparent;border:none;padding:0.75rem 1.25rem;font-size:0.85rem;font-weight:500;color:#64748b;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.2s}
.nav button:hover{color:#1e293b;background:#f8fafc}
.nav button.active{color:#1f77b4;border-bottom-color:#1f77b4}
.section{display:none}
.section.active{display:block}
.content{max-width:1400px;margin:0 auto;padding:1.5rem 2rem}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin-bottom:1.5rem}
.kpi-card{background:#fff;border-radius:10px;padding:1.25rem;box-shadow:0 1px 3px rgba(0,0,0,0.08);text-align:center}
.kpi-card .kpi-value{font-size:1.75rem;font-weight:700;color:#0f172a}
.kpi-card .kpi-label{font-size:0.8rem;color:#64748b;margin-top:0.25rem}
.kpi-card .kpi-desc{font-size:0.7rem;color:#94a3b8;margin-top:0.15rem}
.chart-container{background:#fff;border-radius:10px;padding:1rem;margin-bottom:1.5rem;box-shadow:0 1px 3px rgba(0,0,0,0.08)}
.chart-container h2{font-size:1.1rem;font-weight:600;color:#0f172a;margin-bottom:0.75rem;padding-bottom:0.5rem;border-bottom:1px solid #f1f5f9}
.chart-container .chart-plot{width:100%}
.chart-container .interpretation{margin-top:0.75rem;padding:0.75rem 1rem;background:#f8fafc;border-radius:8px;border-left:3px solid #1f77b4;font-size:0.85rem;color:#334155;line-height:1.6}
.controls-bar{display:flex;flex-wrap:wrap;gap:0.75rem;align-items:center;margin-bottom:1rem}
.controls-bar select,.controls-bar button{padding:0.4rem 0.75rem;font-size:0.8rem;border:1px solid #cbd5e1;border-radius:6px;background:#fff;color:#1e293b;cursor:pointer;font-family:inherit}
.controls-bar select:hover,.controls-bar button:hover{border-color:#94a3b8;background:#f8fafc}
.footer{text-align:center;padding:1.5rem;font-size:0.75rem;color:#94a3b8}
@media(max-width:768px){.header{padding:1rem}.content{padding:1rem}.kpi-grid{grid-template-columns:repeat(2,1fr)}}
"""


def _build_kpi_html(farm_name: str, fields_count: int, total_acres: float,
                    avg_ndvi: float | None, avg_rainfall: float | None,
                    avg_soil_score: float | None) -> str:
    def _kpi(value: str, label: str, desc: str = "") -> str:
        desc_html = f'<div class="kpi-desc">{desc}</div>' if desc else ""
        return f'<div class="kpi-card"><div class="kpi-value">{value}</div><div class="kpi-label">{label}</div>{desc_html}</div>'

    kpis = ""
    kpis += _kpi(str(fields_count), "Total Fields", "Across farm")
    kpis += _kpi(f"{total_acres:.1f}", "Total Acres", "All fields")
    kpis += _kpi(f"{avg_ndvi:.3f}" if avg_ndvi is not None else "N/A", "Avg NDVI", "All crops & years")
    kpis += _kpi(f"{avg_rainfall:.2f} in" if avg_rainfall is not None else "N/A", "Avg Rainfall", "Per season")
    kpis += _kpi(f"{avg_soil_score:.1f}" if avg_soil_score is not None else "N/A", "Soil Health Score", "Composite metric")
    return f'<div class="kpi-grid">{kpis}</div>'


def _build_interpretation_html(patterns: list[str]) -> str:
    if not patterns:
        return ""
    lines = "".join(f"<li>{p}</li>" for p in patterns)
    return (
        '<div class="interpretation">'
        "<strong>Key Insights</strong>"
        f"<ul style='margin-top:0.5rem;padding-left:1.25rem'>{lines}</ul>"
        "</div>"
    )


def _generate_insights(all_fields: list[dict[str, Any]]) -> list[str]:
    insights = []

    ndvi_vals = [(f["field_id"], f.get("avg_ndvi")) for f in all_fields if f.get("avg_ndvi") is not None]
    if len(ndvi_vals) >= 2:
        ndvi_vals.sort(key=lambda x: x[1])
        lowest, highest = ndvi_vals[0], ndvi_vals[-1]
        insights.append(
            f"Field <strong>{lowest[0]}</strong> has the lowest average NDVI ({lowest[1]:.3f}), "
            f"while <strong>{highest[0]}</strong> leads at {highest[1]:.3f} — a {((highest[1]-lowest[1])/lowest[1]*100):.0f}% difference."
        )

    om_vals = [(f["field_id"], f["soil_health"].get("om_pct", 0)) for f in all_fields if f.get("soil_health")]
    ndvi_by_om = [(f["field_id"], f["soil_health"].get("om_pct", 0), f.get("avg_ndvi"))
                  for f in all_fields if f.get("soil_health") and f.get("avg_ndvi")]
    if len(ndvi_by_om) >= 3:
        high_om = [n for n in ndvi_by_om if n[1] >= 5]
        low_om = [n for n in ndvi_by_om if n[1] < 5]
        if high_om and low_om:
            h_avg = np.mean([h[2] for h in high_om])
            l_avg = np.mean([l[2] for l in low_om])
            if h_avg > l_avg:
                insights.append(
                    f"Fields with higher organic matter (≥5%) average {h_avg:.3f} NDVI vs "
                    f"{l_avg:.3f} for lower-OM fields — a {((h_avg-l_avg)/l_avg*100):.0f}% advantage."
                )

    drainage_ndvi = {}
    for f in all_fields:
        sh = f.get("soil_health")
        nd = f.get("avg_ndvi")
        if sh and nd is not None:
            dc = sh.get("drainage_class", "unknown")
            drainage_ndvi.setdefault(dc, []).append(nd)
    if len(drainage_ndvi) >= 2:
        best_dc = max(drainage_ndvi, key=lambda k: np.mean(drainage_ndvi[k]))
        worst_dc = min(drainage_ndvi, key=lambda k: np.mean(drainage_ndvi[k]))
        if best_dc != worst_dc:
            insights.append(
                f"Fields classified as <strong>{best_dc}</strong> show the highest NDVI values, "
                f"while <strong>{worst_dc}</strong> fields tend to underperform."
            )

    scores = [(f["field_id"], f["soil_health"]["score"])
              for f in all_fields if f.get("soil_health")]
    if len(scores) >= 2:
        scores.sort(key=lambda x: x[1])
        if scores[-1][1] - scores[0][1] > 15:
            insights.append(
                f"Soil Health Scores range from {scores[0][1]:.1f} ({scores[0][0]}) to "
                f"{scores[-1][1]:.1f} ({scores[-1][0]}), indicating significant soil quality variability across the farm."
            )

    if not insights:
        insights.append("Sufficient data is available. Compare fields to identify performance patterns.")

    return insights


# ── Plotly figure builders (return JSON traces + layout) ──────────────

def _make_ndvi_comparison(all_fields: list[dict[str, Any]]) -> dict[str, Any]:
    fig = {"data": [], "layout": {}}
    fields_sorted = sorted(all_fields, key=lambda f: f.get("avg_ndvi", 0) or 0, reverse=True)

    corn_data = []
    soy_data = []
    labels = []
    colors = []

    for f in fields_sorted:
        fid = f["field_id"]
        labels.append(fid[-12:])
        colors.append(_color_hash(fid))
        cards = f.get("ndvi_cards", {})
        corn_ndvi = cards.get("corn", {}).get("mean_ndvi") if isinstance(cards.get("corn"), dict) else None
        soy_ndvi = cards.get("soybean", {}).get("mean_ndvi") if isinstance(cards.get("soybean"), dict) else None
        corn_data.append(corn_ndvi if corn_ndvi is not None else 0)
        soy_data.append(soy_ndvi if soy_ndvi is not None else 0)

    fig["data"] = [
        {
            "type": "bar", "name": "Corn",
            "x": labels, "y": corn_data,
            "marker": {"color": "#d62728", "opacity": 0.8},
            "hovertemplate": "<b>%{x}</b><br>Corn NDVI: %{y:.3f}<extra></extra>",
        },
        {
            "type": "bar", "name": "Soybean",
            "x": labels, "y": soy_data,
            "marker": {"color": "#2ca02c", "opacity": 0.8},
            "hovertemplate": "<b>%{x}</b><br>Soybean NDVI: %{y:.3f}<extra></extra>",
        },
    ]
    fig["layout"] = {
        "barmode": "group",
        "title": {"text": "Mean NDVI by Field and Crop Type", "font": {"size": 14}},
        "xaxis": {"title": "Field", "tickangle": -45},
        "yaxis": {"title": "Mean NDVI", "range": [0, 0.7]},
        "margin": {"l": 50, "r": 20, "t": 50, "b": 80},
        "legend": {"orientation": "h", "y": 1.05, "x": 0.5, "xanchor": "center"},
        "paper_bgcolor": "#fff", "plot_bgcolor": "#fff",
        "hovermode": "x unified",
    }
    return fig


def _make_om_ndvi_scatter(all_fields: list[dict[str, Any]]) -> dict[str, Any]:
    points = []
    for f in all_fields:
        sh = f.get("soil_health")
        nd = f.get("avg_ndvi")
        if sh and nd is not None:
            points.append({
                "field_id": f["field_id"],
                "om_pct": sh["om_pct"],
                "ndvi": nd,
                "drainage": sh["drainage_class"].title(),
                "score": sh["score"],
            })

    points.sort(key=lambda p: p["om_pct"])

    drainage_colors = {
        "very poorly drained": "#1f77b4",
        "poorly drained": "#ff7f0e",
        "somewhat poorly drained": "#2ca02c",
        "moderately well drained": "#d62728",
        "well drained": "#9467bd",
        "somewhat excessively drained": "#8c564b",
        "excessively drained": "#e377c2",
    }

    traces_dict: dict[str, dict] = {}
    for p in points:
        dc = p["drainage"].lower()
        color = drainage_colors.get(dc, "#7f7f7f")
        if dc not in traces_dict:
            traces_dict[dc] = {
                "type": "scatter", "mode": "markers",
                "name": dc.title(),
                "x": [], "y": [],
                "marker": {"color": color, "size": 10, "opacity": 0.8,
                           "line": {"color": "white", "width": 1}},
                "hovertemplate": "<b>%{customdata[0]}</b><br>OM: %{x:.1f}%<br>NDVI: %{y:.3f}<br>Drainage: %{customdata[1]}<extra></extra>",
                "customdata": [],
            }
        traces_dict[dc]["x"].append(p["om_pct"])
        traces_dict[dc]["y"].append(p["ndvi"])
        traces_dict[dc]["customdata"].append([p["field_id"], p["drainage"]])

    if len(points) >= 3:
        xs = [p["om_pct"] for p in points]
        ys = [p["ndvi"] for p in points]
        slope, intercept = np.polyfit(xs, ys, 1)
        trend_x = [min(xs), max(xs)]
        trend_y = [slope * x + intercept for x in trend_x]
        traces_dict["trend"] = {
            "type": "scatter", "mode": "lines",
            "name": "Trend",
            "x": trend_x, "y": trend_y,
            "line": {"color": "#333", "width": 2, "dash": "dash"},
            "hoverinfo": "skip",
        }

    data = list(traces_dict.values())
    fig = {"data": data, "layout": {}}
    fig["layout"] = {
        "title": {"text": "Soil Organic Matter vs NDVI (colored by drainage class)", "font": {"size": 14}},
        "xaxis": {"title": "Organic Matter (%)"},
        "yaxis": {"title": "Average NDVI"},
        "margin": {"l": 50, "r": 20, "t": 50, "b": 50},
        "legend": {"orientation": "h", "y": 1.05, "x": 0, "font": {"size": 9}},
        "paper_bgcolor": "#fff", "plot_bgcolor": "#fff",
        "hovermode": "closest",
    }
    return fig


def _make_soil_health_bars(all_fields: list[dict[str, Any]]) -> dict[str, Any]:
    fields_sorted = sorted(all_fields, key=lambda f: f.get("soil_health", {}).get("score", 0) or 0, reverse=True)

    labels = []
    scores = []
    om = []
    ph = []
    drainage = []
    cec = []
    colors = []

    for f in fields_sorted:
        sh = f.get("soil_health")
        if not sh:
            continue
        labels.append(f["field_id"][-12:])
        scores.append(sh["score"])
        om.append(sh["om_score"])
        ph.append(sh["ph_score"])
        drainage.append(sh["drainage_score"])
        cec.append(sh["cec_score"])
        colors.append(_color_hash(f["field_id"]))

    fig = {"data": [], "layout": {}}
    fig["data"] = [
        {"type": "bar", "name": "OM (35%)", "x": labels, "y": om,
         "marker": {"color": "#8B4513", "opacity": 0.85},
         "hovertemplate": "<b>%{x}</b><br>OM Score: %{y:.1f}<extra></extra>"},
        {"type": "bar", "name": "pH (25%)", "x": labels, "y": ph,
         "marker": {"color": "#FF8C00", "opacity": 0.85},
         "hovertemplate": "<b>%{x}</b><br>pH Score: %{y:.1f}<extra></extra>"},
        {"type": "bar", "name": "Drainage (20%)", "x": labels, "y": drainage,
         "marker": {"color": "#1f77b4", "opacity": 0.85},
         "hovertemplate": "<b>%{x}</b><br>Drainage Score: %{y:.1f}<extra></extra>"},
        {"type": "bar", "name": "CEC (20%)", "x": labels, "y": cec,
         "marker": {"color": "#2ca02c", "opacity": 0.85},
         "hovertemplate": "<b>%{x}</b><br>CEC Score: %{y:.1f}<extra></extra>"},
    ]
    fig["layout"] = {
        "barmode": "stack",
        "title": {"text": "Soil Health Score Breakdown by Field", "font": {"size": 14}},
        "xaxis": {"title": "Field", "tickangle": -45},
        "yaxis": {"title": "Score (0–100)", "range": [0, 105]},
        "margin": {"l": 50, "r": 20, "t": 50, "b": 80},
        "legend": {"orientation": "h", "y": 1.05, "x": 0.5, "xanchor": "center", "font": {"size": 9}},
        "paper_bgcolor": "#fff", "plot_bgcolor": "#fff",
        "hovermode": "x unified",
        "annotations": [
            {
                "x": labels[i] if i < len(labels) else "",
                "y": scores[i] + 2,
                "text": f"{scores[i]:.0f}",
                "showarrow": False,
                "font": {"size": 9, "color": "#333"},
                "xref": "x", "yref": "y",
            }
            for i in range(len(labels))
        ] if len(labels) <= 20 else [],
    }
    return fig


def _make_weather_charts(all_fields: list[dict[str, Any]], years: list[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    # GDD chart
    gdd_traces = []
    precip_traces = []

    for f in all_fields:
        wdf = f.get("weather_df")
        if wdf is None or wdf.empty:
            continue
        fid_short = f["field_id"][-12:]
        color = _color_hash(f["field_id"])

        for year in years:
            records = compute_field_year_gdd(wdf, year)
            if not records:
                continue
            doys = [r["doy"] for r in records]
            gdd_cumul = [r["gdd_cumul"] for r in records]
            precip_in = [r["precip_in"] for r in records]
            precip_cumul = [r["precip_cumul"] for r in records]

            gdd_traces.append({
                "type": "scatter", "mode": "lines",
                "name": f"{fid_short} {year}",
                "x": doys, "y": gdd_cumul,
                "line": {"color": color, "width": 1.5},
                "hovertemplate": f"<b>{fid_short}</b> {year}<br>DOY: %{{x}}<br>GDD: %{{y:.0f}}<extra></extra>",
                "legendgroup": f"{f['field_id']}-{year}",
                "showlegend": True,
            })

            # Only add precip trace for first year to avoid clutter
            if year == years[0]:
                precip_traces.append({
                    "type": "bar",
                    "name": f"{fid_short} {year}",
                    "x": doys, "y": precip_in,
                    "marker": {"color": color, "opacity": 0.35},
                    "hovertemplate": f"<b>{fid_short}</b><br>DOY: %{{x}}<br>Rain: %{{y:.2f}} in<extra></extra>",
                    "legendgroup": f"{f['field_id']}-{year}",
                    "showlegend": True,
                })

    gdd_layout = {
        "title": {"text": "Cumulative GDD by Field and Year (base 10°C)", "font": {"size": 14}},
        "xaxis": {"title": "Day of Year", "dtick": 30, "range": [60, 330]},
        "yaxis": {"title": "Cumulative GDD (°C-days)"},
        "margin": {"l": 50, "r": 20, "t": 50, "b": 50},
        "legend": {"orientation": "h", "y": 1.05, "x": 0, "font": {"size": 8}},
        "paper_bgcolor": "#fff", "plot_bgcolor": "#fff",
        "hovermode": "x unified",
    }
    gdd_fig = {"data": gdd_traces, "layout": gdd_layout}

    precip_layout = {
        "title": {"text": f"Daily Precipitation by Field ({years[0] if years else 'N/A'})", "font": {"size": 14}},
        "xaxis": {"title": "Day of Year", "dtick": 30, "range": [60, 330]},
        "yaxis": {"title": "Precipitation (inches)"},
        "margin": {"l": 50, "r": 20, "t": 50, "b": 50},
        "legend": {"orientation": "h", "y": 1.05, "x": 0, "font": {"size": 8}},
        "paper_bgcolor": "#fff", "plot_bgcolor": "#fff",
        "hovermode": "x unified",
        "barmode": "overlay",
    }
    precip_fig = {"data": precip_traces, "layout": precip_layout}

    return gdd_fig, precip_fig


def _make_map_figure(all_fields: list[dict[str, Any]], basemap_b64: str | None) -> dict[str, Any]:
    traces = []
    for f in all_fields:
        if not f.get("mercator_polygons"):
            continue
        sh = f.get("soil_health", {})
        score = sh.get("score", 50)
        field_id = f["field_id"]
        acres = f.get("acres", 0)
        ndvi = f.get("avg_ndvi")
        color = _color_hash(field_id)

        score_norm = max(0, min(100, score)) / 100.0
        r = int(255 * (1 - score_norm))
        g = int(255 * score_norm)
        fill_color = f"rgb({r},{g},50)"

        for poly in f["mercator_polygons"]:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            hover = (
                f"<b>{field_id}</b><br>"
                f"Acres: {acres:.1f}<br>"
                f"Soil Health: {score:.1f}<br>"
                f"Avg NDVI: {ndvi:.3f}" if ndvi else f"{ndvi}"
            )
            traces.append({
                "x": xs, "y": ys,
                "fill": "toself",
                "fillcolor": fill_color,
                "line": {"color": "#333", "width": 1.5},
                "mode": "lines",
                "type": "scatter",
                "name": field_id[-12:],
                "text": hover,
                "hoverinfo": "text",
                "showlegend": False,
            })

    layout = {
        "dragmode": "pan",
        "hovermode": "closest",
        "xaxis": {"visible": False, "showgrid": False, "zeroline": False, "scaleanchor": "y", "scaleratio": 1},
        "yaxis": {"visible": False, "showgrid": False, "zeroline": False},
        "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
        "paper_bgcolor": "#f4f5f7",
        "plot_bgcolor": "#f4f5f7",
        "title": {
            "text": "Geospatial Map — Fields Colored by Soil Health Score (green=higher)",
            "font": {"size": 14},
            "x": 0.5, "xanchor": "center",
        },
    }

    if basemap_b64:
        layout["images"] = [{
            "source": f"data:image/png;base64,{basemap_b64}",
            "xref": "x", "yref": "y",
            "x": 0, "y": 0,
            "sizex": 1, "sizey": 1,
            "xanchor": "left", "yanchor": "bottom",
            "sizing": "stretch",
            "layer": "below",
            "opacity": 1,
        }]

    return {"data": traces, "layout": layout}


def _compute_tile_bounds_from_fields(all_fields: list[dict[str, Any]]):
    import math
    all_x, all_y = [], []
    for f in all_fields:
        for poly in f.get("mercator_polygons", []):
            for p in poly:
                all_x.append(p[0])
                all_y.append(p[1])
    if not all_x:
        return None

    xmin, xmax = min(all_x), max(all_x)
    ymin, ymax = min(all_y), max(all_y)
    bx = (xmax - xmin) * 0.15
    by = (ymax - ymin) * 0.15
    xmin -= bx
    xmax += bx
    ymin -= by
    ymax += by

    world_size = xmax - xmin
    target_width = 1500
    zoom = max(2, min(18, int(math.floor(math.log2(target_width * 360.0 / (world_size * 256.0))))))

    def merc_to_tile(mx, my, z):
        lon = mx / 20037508.34 * 180.0
        lat = (math.atan(math.exp(my / 20037508.34 * math.pi)) * 360.0 / math.pi) - 90.0
        lat_rad = math.radians(lat)
        n = 2.0 ** z
        xtile = int((lon + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
        return xtile, ytile

    x0, y1 = merc_to_tile(xmin, ymin, zoom)
    x1f, y0f = merc_to_tile(xmax, ymax, zoom)
    x1 = max(x0, x1f)
    y1t = max(y1, y0f)
    x0 = min(x0, x1f)
    y0 = min(y1, y0f)

    return xmin, ymin, xmax, ymax, zoom, x0, y0, x1, y1t


# ── Basemap ───────────────────────────────────────────────────────────

def acquire_basemap_from_fields(all_fields: list[dict[str, Any]]) -> str | None:
    try:
        from PIL import Image
        import requests
    except ImportError:
        print("  WARNING: Pillow not installed. Skipping satellite basemap.")
        return None

    bounds = _compute_tile_bounds_from_fields(all_fields)
    if bounds is None:
        return None

    xmin, ymin, xmax, ymax, zoom, tx0, ty0, tx1, ty1 = bounds
    tile_cache = _ensure_cache_dir("tiles")
    tile_url = (
        "https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}"
    )
    max_tiles = 64

    tile_count = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    if tile_count > max_tiles:
        zoom = max(2, zoom - 1)
        import math
        lon = xmin / 20037508.34 * 180.0
        lat = (math.atan(math.exp(ymin / 20037508.34 * math.pi)) * 360.0 / math.pi) - 90.0
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        tx0 = int((lon + 180.0) / 360.0 * n)
        ty1 = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
        lon2 = xmax / 20037508.34 * 180.0
        lat2 = (math.atan(math.exp(ymax / 20037508.34 * math.pi)) * 360.0 / math.pi) - 90.0
        lat_rad2 = math.radians(lat2)
        tx1 = int((lon2 + 180.0) / 360.0 * n)
        ty0 = int((1.0 - math.log(math.tan(lat_rad2) + 1.0 / math.cos(lat_rad2)) / math.pi) / 2.0 * n)
        if tx1 < tx0: tx0, tx1 = tx1, tx0
        if ty1 < ty0: ty0, ty1 = ty1, ty0

    stitched = None
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            cache_path = tile_cache / f"{zoom}_{tx}_{ty}.png"
            data = None
            if cache_path.exists():
                age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
                if age_hours < 24:
                    data = cache_path.read_bytes()
            if data is None:
                url = tile_url.format(z=zoom, x=tx, y=ty)
                try:
                    resp = requests.get(url, timeout=10)
                    resp.raise_for_status()
                    data = resp.content
                    cache_path.write_bytes(data)
                except Exception as e:
                    print(f"  WARNING: Tile download failed: {e}")
                    continue
            if data:
                tile_img = Image.open(io.BytesIO(data)).convert("RGB")
                if stitched is None:
                    total_w = (tx1 - tx0 + 1) * 256
                    total_h = (ty1 - ty0 + 1) * 256
                    stitched = Image.new("RGB", (total_w, total_h))
                px = (tx - tx0) * 256
                py = (ty - ty0) * 256
                stitched.paste(tile_img, (px, py))

    if stitched is None:
        return None

    buf = io.BytesIO()
    stitched.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    print(f"  Basemap: {stitched.width}x{stitched.height} px, {len(b64) // 1024} KB")
    return b64


# ── Dashboard HTML assembly ───────────────────────────────────────────

def _serialize_fig(fig: dict[str, Any]) -> str:
    return json.dumps(fig, indent=None, separators=(",", ":"))


def build_dashboard_html(
    farm_name: str,
    kpi_html: str,
    ndvi_fig: dict[str, Any],
    om_fig: dict[str, Any],
    map_fig: dict[str, Any],
    gdd_fig: dict[str, Any],
    precip_fig: dict[str, Any],
    soil_fig: dict[str, Any],
    interpretation_html: str,
    plotly_js: str,
    all_fields: list[dict[str, Any]],
) -> str:
    sections = [
        ("kpi", "Summary", ""),
        ("ndvi", "NDVI Comparison", _serialize_fig(ndvi_fig)),
        ("om", "Soil vs NDVI", _serialize_fig(om_fig)),
        ("map", "Geospatial Map", _serialize_fig(map_fig)),
        ("weather", "Weather Analysis", ""),
        ("soil", "Soil Health Metric", _serialize_fig(soil_fig)),
        ("insights", "Interpretation", ""),
    ]

    gdd_serialized = _serialize_fig(gdd_fig)
    precip_serialized = _serialize_fig(precip_fig)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{farm_name} — Row Crop Intelligence Dashboard</title>
<style>{DASHBOARD_CSS}</style>
</head>
<body>
<div class="header">
  <h1>Row Crop Intelligence Data Dashboard</h1>
  <div class="subtitle">{farm_name} &middot; {len(all_fields)} fields &middot; Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</div>
</div>
<div class="nav" id="nav">
  <button class="active" data-section="kpi">Summary</button>
  <button data-section="ndvi">NDVI Comparison</button>
  <button data-section="om">Soil vs NDVI</button>
  <button data-section="map">Geospatial Map</button>
  <button data-section="weather">Weather Analysis</button>
  <button data-section="soil">Soil Health Metric</button>
  <button data-section="insights">Interpretation</button>
</div>

<div class="content">
  <div class="section active" id="section-kpi">
    {kpi_html}
  </div>
  <div class="section" id="section-ndvi">
    <div class="chart-container">
      <h2>Exploratory Visualization 1: NDVI Comparison Across Fields</h2>
      <p style="color:#64748b;font-size:0.85rem;margin-bottom:0.75rem">Comparing mean NDVI values for corn and soybean across all fields. Fields are sorted by average NDVI (descending).</p>
      <div class="chart-plot" id="ndvi-chart"></div>
    </div>
  </div>
  <div class="section" id="section-om">
    <div class="chart-container">
      <h2>Exploratory Visualization 2: Soil Organic Matter vs NDVI</h2>
      <p style="color:#64748b;font-size:0.85rem;margin-bottom:0.75rem">Scatter plot showing the relationship between soil organic matter content and average NDVI. Points are colored by drainage class. The dashed trendline shows the overall correlation.</p>
      <div class="chart-plot" id="om-chart"></div>
    </div>
  </div>
  <div class="section" id="section-map">
    <div class="chart-container">
      <h2>Geospatial Analysis: Field Boundaries Colored by Soil Health Score</h2>
      <p style="color:#64748b;font-size:0.85rem;margin-bottom:0.75rem">Interactive map with satellite basemap. Fields are colored on a green-yellow-red gradient based on their composite Soil Health Score. Hover over fields for details.</p>
      <div class="chart-plot" id="map-chart" style="height:600px"></div>
    </div>
  </div>
  <div class="section" id="section-weather">
    <div class="chart-container">
      <h2>Weather & Climate Analysis: Cumulative GDD</h2>
      <p style="color:#64748b;font-size:0.85rem;margin-bottom:0.75rem">Growing Degree Day accumulation curves for each field across all available years (base 10°C). Compare thermal time accumulation patterns across fields.</p>
      <div class="chart-plot" id="gdd-chart" style="height:400px"></div>
    </div>
    <div class="chart-container">
      <h2>Weather & Climate Analysis: Daily Precipitation</h2>
      <p style="color:#64748b;font-size:0.85rem;margin-bottom:0.75rem">Daily precipitation distribution across fields for the selected year. Bars show daily rainfall amounts.</p>
      <div class="chart-plot" id="precip-chart" style="height:400px"></div>
    </div>
  </div>
  <div class="section" id="section-soil">
    <div class="chart-container">
      <h2>Soil Health & Sustainability Metric</h2>
      <p style="color:#64748b;font-size:0.85rem;margin-bottom:0.75rem">
        <strong>Soil Health Score</strong> is a weighted composite metric (0–100) calculated from four soil properties:
        <strong>Organic Matter</strong> (35% weight), <strong>pH suitability</strong> (25%), <strong>Drainage class</strong> (20%), and <strong>CEC</strong> (20%).
        Higher scores indicate better overall soil quality for row crop production.
      </p>
      <div class="chart-plot" id="soil-chart"></div>
    </div>
  </div>
  <div class="section" id="section-insights">
    <div class="chart-container">
      <h2>Interpretation & Key Insights</h2>
      {interpretation_html}
      <div style="margin-top:1rem;padding:0.75rem 1rem;background:#f0fdf4;border-radius:8px;border-left:3px solid #16a34a;font-size:0.85rem;color:#166534;line-height:1.6">
        <strong>How to Use This Dashboard:</strong><br>
        Compare fields side-by-side in each section. Fields with low Soil Health Scores may benefit from
        targeted amendments (lime for pH, organic matter building). Fields with low NDVI despite good soil
        conditions may have management or pest issues. The weather section helps contextualize
        year-to-year variability in crop performance.
      </div>
    </div>
  </div>
</div>

<div class="footer">Row Crop Intelligence Data Dashboard &middot; Generated by My Farm Advisor</div>

<script>
var NDVI_FIG = {_serialize_fig(ndvi_fig)};
var OM_FIG = {_serialize_fig(om_fig)};
var MAP_FIG = {_serialize_fig(map_fig)};
var GDD_FIG = {gdd_serialized};
var PRECIP_FIG = {precip_serialized};
var SOIL_FIG = {_serialize_fig(soil_fig)};

function renderAll() {{
  Plotly.react("ndvi-chart", NDVI_FIG.data, NDVI_FIG.layout, {{responsive:true}});
  Plotly.react("om-chart", OM_FIG.data, OM_FIG.layout, {{responsive:true}});
  Plotly.react("map-chart", MAP_FIG.data, MAP_FIG.layout, {{responsive:true}});
  Plotly.react("gdd-chart", GDD_FIG.data, GDD_FIG.layout, {{responsive:true}});
  Plotly.react("precip-chart", PRECIP_FIG.data, PRECIP_FIG.layout, {{responsive:true}});
  Plotly.react("soil-chart", SOIL_FIG.data, SOIL_FIG.layout, {{responsive:true}});
}}

document.addEventListener("DOMContentLoaded", function() {{
  renderAll();

  var nav = document.getElementById("nav");
  nav.addEventListener("click", function(e) {{
    var btn = e.target.closest("button");
    if (!btn) return;
    var section = btn.getAttribute("data-section");
    nav.querySelectorAll("button").forEach(function(b) {{ b.classList.remove("active"); }});
    btn.classList.add("active");
    document.querySelectorAll(".section").forEach(function(s) {{ s.classList.remove("active"); }});
    var target = document.getElementById("section-" + section);
    if (target) target.classList.add("active");
    if (section === "map") {{
      Plotly.Plots.resize(document.getElementById("map-chart"));
    }}
  }});
}});
</script>
<script>{plotly_js}</script>
</body>
</html>"""


# ── Write dashboard ───────────────────────────────────────────────────

def write_dashboard(html: str, output_path: Path) -> Path:
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.parent / f".{output_path.name}.tmp"
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(output_path)
    return output_path


# ── Main ──────────────────────────────────────────────────────────────

def _mercator_project_polygons(field_row) -> list[list[tuple[float, float]]]:
    try:
        from shapely.geometry import Polygon, MultiPolygon
    except ImportError:
        return []
    geom = field_row.geometry
    if geom is None or geom.is_empty:
        return []

    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    except ImportError:
        transformer = None

    def _project_coords(coords):
        if transformer:
            return [(transformer.transform(x, y)[0], transformer.transform(x, y)[1]) for x, y in coords]
        return list(coords)

    if isinstance(geom, Polygon):
        return [_project_coords(list(geom.exterior.coords))]
    elif isinstance(geom, MultiPolygon):
        return [_project_coords(list(p.exterior.coords)) for p in geom.geoms if p and not p.is_empty]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Row Crop Intelligence Dashboard")
    parser.add_argument("--grower", default="ia-grower", help="Grower slug")
    parser.add_argument("--farm", default="ia-grower-iowa", help="Farm slug")
    parser.add_argument("--output", default=None, help="Output path for HTML dashboard")
    parser.add_argument("--no-basemap", action="store_true", help="Skip satellite basemap")
    parser.add_argument("--plotly-cache", default=None, help="Plotly cache directory")
    args = parser.parse_args()

    runtime_base = _resolve_runtime_base()
    farm_dir = runtime_base / "growers" / args.grower / "farms" / args.farm

    if not farm_dir.is_dir():
        print(f"ERROR: Farm directory not found: {farm_dir}")
        sys.exit(1)

    farm_meta = _read_farm_json(farm_dir)
    farm_name = farm_meta.get("display_name", args.farm.replace("-", " ").title())
    farm_slug = farm_meta.get("farm_slug", args.farm)

    print("=" * 60)
    print("  Row Crop Intelligence Data Dashboard Generator")
    print(f"  Grower: {args.grower}")
    print(f"  Farm: {args.farm}")
    print(f"  Farm directory: {farm_dir}")
    print("=" * 60)

    # 1. Load field boundaries
    print("\n1. Loading field boundaries...")
    boundaries = _load_field_boundaries(farm_dir)
    print(f"   Found {len(boundaries)} fields")

    # 2. Load per-field data
    print("\n2. Loading per-field data...")
    all_fields: list[dict[str, Any]] = []
    total_acres = 0.0
    all_ndvi_vals: list[float] = []
    all_rainfall: list[float] = []
    all_years: set[int] = set()
    field_dir_map: dict[str, Path] = {}

    fields_dir = farm_dir / "fields"
    if fields_dir.is_dir():
        for sub in sorted(fields_dir.iterdir()):
            if not sub.is_dir():
                continue
            jp = sub / "field.json"
            if jp.exists():
                try:
                    meta = json.loads(jp.read_text(encoding="utf-8"))
                    fid = meta.get("field_id", "")
                    if fid:
                        field_dir_map[fid] = sub
                except Exception:
                    pass

    for _, row in boundaries.iterrows():
        fid = str(row["field_id"])
        acres = float(row["area_acres"]) if "area_acres" in row and pd.notna(row["area_acres"]) else 0
        total_acres += acres
        field_dir = field_dir_map.get(fid)
        if field_dir is None:
            slug = _slugify(fid)
            field_dir = fields_dir / slug
            if not field_dir.is_dir():
                print(f"   WARNING: No field directory for {fid}, skipping")
                continue

        # NDVI cards
        ndvi_path = field_dir / "derived" / "summaries" / "ndvi_card_summary.json"
        ndvi_cards = _read_ndvi_card_summary(ndvi_path)

        avg_ndvi = None
        ndvi_vals = []
        for crop_key in ["corn", "soybean", "corn_peak_95", "soybean_peak_95"]:
            card = ndvi_cards.get("cards", {}).get(crop_key)
            if isinstance(card, dict):
                mv = card.get("mean_ndvi")
                if mv is not None:
                    ndvi_vals.append(float(mv))
        if ndvi_vals:
            avg_ndvi = float(np.mean(ndvi_vals))
            all_ndvi_vals.append(avg_ndvi)

        # Soil summary
        soil_path = field_dir / "soil" / "ssurgo_summary.csv"
        soil_data = _read_soil_summary(soil_path)
        soil_health = compute_soil_health_score(soil_data) if soil_data else {}

        # Weather
        weather_path = field_dir / "weather" / "daily_weather.csv"
        weather_df = _read_weather_csv(weather_path)
        if not weather_df.empty and "date" in weather_df.columns:
            for y in weather_df["date"].dt.year.unique():
                all_years.add(int(y))
            total_precip = weather_df["PRECTOTCORR"].sum() * MM_TO_INCH if "PRECTOTCORR" in weather_df else 0
            all_rainfall.append(total_precip)

        # Mercator polygons
        merc_polys = _mercator_project_polygons(row)

        field_info = {
            "field_id": fid,
            "acres": acres,
            "avg_ndvi": avg_ndvi,
            "ndvi_cards": ndvi_cards.get("cards", {}),
            "soil_data": soil_data,
            "soil_health": soil_health,
            "weather_df": weather_df,
            "mercator_polygons": merc_polys,
        }
        all_fields.append(field_info)
        print(f"   {fid}: {acres:.1f} ac, NDVI={avg_ndvi:.3f}, Soil={soil_health.get('score', 'N/A')}")

    if not all_fields:
        print("ERROR: No fields with data found")
        sys.exit(1)

    avg_ndvi_all = float(np.mean(all_ndvi_vals)) if all_ndvi_vals else None
    avg_rainfall_all = float(np.mean(all_rainfall)) if all_rainfall else None
    avg_soil_score = float(np.mean([f["soil_health"]["score"] for f in all_fields if f.get("soil_health")])) if any(f.get("soil_health") for f in all_fields) else None
    target_years = [y for y in range(2021, 2026) if y in all_years]
    sorted_years = target_years if target_years else sorted(all_years)

    print(f"\n   Total acres: {total_acres:.1f}")
    print(f"   Avg NDVI: {avg_ndvi_all:.4f}" if avg_ndvi_all else "   Avg NDVI: N/A")
    print(f"   Avg Soil Health: {avg_soil_score:.1f}" if avg_soil_score else "   Avg Soil Health: N/A")
    print(f"   Weather years used: {sorted_years}")

    # 3. Build KPI HTML
    print("\n3. Building KPI cards...")
    kpi_html = _build_kpi_html(farm_name, len(all_fields), total_acres, avg_ndvi_all, avg_rainfall_all, avg_soil_score)

    # 4. Build figures
    print("\n4. Building visualizations...")
    print("   NDVI comparison chart...")
    ndvi_fig = _make_ndvi_comparison(all_fields)

    print("   OM vs NDVI scatter...")
    om_fig = _make_om_ndvi_scatter(all_fields)

    print("   Geospatial map...")
    basemap_b64 = None
    if not args.no_basemap:
        print("   Acquiring satellite basemap...")
        basemap_b64 = acquire_basemap_from_fields(all_fields)
        if basemap_b64:
            print("   Basemap available")
        else:
            print("   Basemap not available")
    map_fig = _make_map_figure(all_fields, basemap_b64)

    print("   Weather charts...")
    gdd_fig, precip_fig = _make_weather_charts(all_fields, sorted_years)

    print("   Soil health breakdown...")
    soil_fig = _make_soil_health_bars(all_fields)

    print("   Generating insights...")
    insights = _generate_insights(all_fields)
    interpretation_html = _build_interpretation_html(insights)

    # 5. Vendor Plotly
    print("\n5. Vendoring Plotly.js...")
    plotly_js = vendor_plotly(args.plotly_cache)

    # 6. Build and write dashboard
    print("\n6. Rendering dashboard HTML...")
    html = build_dashboard_html(
        farm_name=farm_name,
        kpi_html=kpi_html,
        ndvi_fig=ndvi_fig,
        om_fig=om_fig,
        map_fig=map_fig,
        gdd_fig=gdd_fig,
        precip_fig=precip_fig,
        soil_fig=soil_fig,
        interpretation_html=interpretation_html,
        plotly_js=plotly_js,
        all_fields=all_fields,
    )

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = farm_dir / "derived" / "dashboards" / f"{farm_slug}_row_crop_dashboard.html"

    print(f"   Writing dashboard to {output_path}...")
    result = write_dashboard(html, output_path)
    size_kb = result.stat().st_size / 1024
    print(f"\n{'=' * 60}")
    print(f"  DONE: {result} ({size_kb:.0f} KB)")
    print(f"  Open in your browser to view the dashboard.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
