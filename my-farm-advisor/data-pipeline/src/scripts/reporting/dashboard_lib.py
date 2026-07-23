from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon

PLOTLY_VERSION = "2.35.2"
PLOTLY_URL = f"https://cdn.plot.ly/plotly-{PLOTLY_VERSION}.min.js"
TILE_URL_TEMPLATE = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
CACHE_DIR = Path.home() / ".cache" / "my-farm-advisor"
COLOR_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]
REQUIRED_FIELD_PROPERTIES = {"field_id"}
REQUIRED_BOUNDARY_COLS = {"field_id"}
WEATHER_REQUIRED_COLS = {"T2M_MAX", "T2M_MIN", "PRECTOTCORR"}
MM_TO_INCH = 0.0393701
GDD_BASE_C = 10.0
FROST_CUTOFF_MONTH = 7
FROST_CUTOFF_DAY = 1
DEFAULT_X_RANGE = [80, 320]
MAX_TILES = 64
TARGET_PX_WIDTH = 1500
BASEMAP_TIMEOUT = 10
BASEMAP_RETRIES = 3
TILE_CACHE_TTL_HOURS = 24


def _color_for_index(idx: int) -> str:
    return COLOR_PALETTE[idx % len(COLOR_PALETTE)]


def _color_hash(field_id: str) -> str:
    h = hashlib.md5(field_id.encode()).hexdigest()
    idx = int(h[:8], 16) % len(COLOR_PALETTE)
    return COLOR_PALETTE[idx]


def _ensure_cache_dir(*parts: str) -> Path:
    d = CACHE_DIR.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return normalized.strip("-")


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------

def resolve_farm_dir(
    farm_dir: str | Path | None = None,
    growers_dir: str | Path | None = None,
    data_root: str | Path | None = None,
) -> Path:
    if farm_dir is not None:
        path = Path(farm_dir).expanduser().resolve()
        _validate_farm_layout(path)
        return path

    candidates: list[Path] = []
    roots: list[Path] = []

    if growers_dir is not None:
        roots.append(Path(growers_dir).expanduser().resolve())
    elif data_root is not None:
        roots.append(Path(data_root).expanduser().resolve() / "data-pipeline" / "growers")

    env_root = os.environ.get("DATA_PIPELINE_DATA_ROOT")
    if env_root:
        candidate = Path(env_root).expanduser().resolve() / "data-pipeline" / "growers"
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)

    if not roots:
        roots = _auto_discover_roots()

    for root in roots:
        if not root.is_dir():
            continue
        found = _find_farms_under(root)
        candidates.extend(found)

    if not candidates:
        searched = ", ".join(str(r) for r in roots) if roots else "~"
        raise RuntimeError(
            f"No valid farm directories found under: {searched}\n"
            "A farm directory must contain boundary/field_boundaries.geojson and fields/.\n"
            "Use --farm-dir to specify a path directly."
        )

    if len(candidates) == 1:
        return candidates[0]

    lines = "\n".join(f"  {p}" for p in candidates)
    raise RuntimeError(
        f"Found {len(candidates)} valid farm directories. Use --farm-dir to select one:\n{lines}"
    )


def _auto_discover_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []
    for candidate in sorted(home.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        growers_dir = candidate / "data-pipeline" / "growers"
        if growers_dir.is_dir():
            roots.append(growers_dir)
        growers_dir = candidate / "growers"
        if growers_dir.is_dir():
            roots.append(growers_dir)
    return roots


def _find_farms_under(root: Path) -> list[Path]:
    farms: list[Path] = []
    for grower_dir in sorted(root.iterdir()):
        if not grower_dir.is_dir():
            continue
        farms_dir = grower_dir / "farms"
        if not farms_dir.is_dir():
            continue
        for farm_dir in sorted(farms_dir.iterdir()):
            if not farm_dir.is_dir():
                continue
            try:
                _validate_farm_layout(farm_dir)
                farms.append(farm_dir)
            except RuntimeError:
                continue
    return farms


def _validate_farm_layout(farm_dir: Path) -> None:
    if not farm_dir.is_dir():
        raise RuntimeError(f"Not a directory: {farm_dir}")
    boundary = farm_dir / "boundary" / "field_boundaries.geojson"
    if not boundary.exists():
        raise RuntimeError(
            f"Missing required boundary file: {boundary}"
        )
    fields_dir = farm_dir / "fields"
    if not fields_dir.is_dir():
        raise RuntimeError(
            f"Missing required fields directory: {fields_dir}"
        )


def discover_under(root: Path) -> list[Path]:
    return _find_farms_under(root)


# ---------------------------------------------------------------------------
# Farm data reading
# ---------------------------------------------------------------------------

def read_farm_json(farm_dir: Path) -> dict[str, Any]:
    farm_json = farm_dir / "farm.json"
    if farm_json.exists():
        return json.loads(farm_json.read_text(encoding="utf-8"))
    return {}


def _build_field_dir_map(farm_dir: Path) -> dict[str, Path]:
    """Build a mapping from field_id to field directory path.

    Scans `fields/<dir>/field.json` to find matching field_ids.  Falls back
    to slugified field_id as directory name when field.json is unavailable.
    """
    fields_dir = farm_dir / "fields"
    if not fields_dir.is_dir():
        return {}

    # First pass: read field.json in each subdirectory
    id_to_dir: dict[str, Path] = {}
    for sub in sorted(fields_dir.iterdir()):
        if not sub.is_dir():
            continue
        meta = read_field_json(sub)
        fid = meta.get("field_id")
        if fid:
            id_to_dir[str(fid)] = sub

    # Second pass: match by slugifying field_id as fallback
    for sub in sorted(fields_dir.iterdir()):
        if not sub.is_dir():
            continue
        dir_name = sub.name
        # If this dir is already mapped to some field_id, skip
        if dir_name in id_to_dir.values():
            continue
        # Try to find a boundary field_id that slugifies to this dir name
        boundary_path = farm_dir / "boundary" / "field_boundaries.geojson"
        if boundary_path.exists():
            try:
                bdf = gpd.read_file(str(boundary_path))
                for fid in bdf["field_id"].astype(str).unique():
                    if _slugify(fid) == dir_name and fid not in id_to_dir:
                        id_to_dir[fid] = sub
            except Exception:
                pass

    return id_to_dir


def read_boundaries(farm_dir: Path) -> gpd.GeoDataFrame:
    boundary = farm_dir / "boundary" / "field_boundaries.geojson"
    if not boundary.exists():
        raise RuntimeError(f"Boundary file not found: {boundary}")
    fields = gpd.read_file(str(boundary))
    if fields.empty:
        raise RuntimeError(f"Boundary file contains no features: {boundary}")
    if "field_id" not in fields.columns:
        # fall back to first string column or index
        str_cols = [c for c in fields.columns if fields[c].dtype == object]
        if not str_cols:
            fields["field_id"] = [f"field-{i}" for i in range(len(fields))]
        else:
            fields["field_id"] = fields[str_cols[0]].astype(str)
    return fields


def _extract_polygons(geom) -> list[list[tuple[float, float]]]:
    if geom is None:
        return []
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        coords = list(geom.exterior.coords)
        return [[(x, y) for x, y in coords]]
    elif isinstance(geom, MultiPolygon):
        result = []
        for poly in geom.geoms:
            if poly and not poly.is_empty:
                coords = list(poly.exterior.coords)
                result.append([(x, y) for x, y in coords])
        return result
    return []


def read_field_json(field_dir: Path) -> dict[str, Any]:
    field_json = field_dir / "field.json"
    if field_json.exists():
        return json.loads(field_json.read_text(encoding="utf-8"))
    return {}


def read_weather_csv(weather_path: Path) -> pd.DataFrame:
    if not weather_path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(weather_path)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def _mercator_project(fields: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return fields.to_crs("EPSG:3857")


# ---------------------------------------------------------------------------
# Weather computation
# ---------------------------------------------------------------------------

def compute_last_frost(df: pd.DataFrame, year: int) -> tuple[str, int]:
    year_df = df[df["date"].dt.year == year].copy()
    if year_df.empty:
        cutoff = date(year, FROST_CUTOFF_MONTH, FROST_CUTOFF_DAY)
        return (cutoff.isoformat(), 1)

    before_july = year_df[
        (year_df["date"].dt.month < FROST_CUTOFF_MONTH) |
        ((year_df["date"].dt.month == FROST_CUTOFF_MONTH) & (year_df["date"].dt.day <= FROST_CUTOFF_DAY))
    ]
    frost_days = before_july[before_july["T2M_MIN"] <= 0.0]
    if frost_days.empty:
        cutoff = date(year, FROST_CUTOFF_MONTH, FROST_CUTOFF_DAY)
        return (cutoff.isoformat(), 1)

    latest_frost = frost_days.sort_values("date").iloc[-1]
    frost_date = latest_frost["date"]
    if isinstance(frost_date, pd.Timestamp):
        d = frost_date.to_pydatetime()
    else:
        d = frost_date
    doy = d.timetuple().tm_yday
    return (d.strftime("%Y-%m-%d"), doy)


def compute_field_year_records(df: pd.DataFrame, field_id: str, year: int) -> list[dict[str, Any]]:
    year_df = df[
        (df["field_id"] == field_id) & (df["date"].dt.year == year)
    ].copy()
    if year_df.empty:
        return []

    year_df = year_df.dropna(subset=list(WEATHER_REQUIRED_COLS))
    if year_df.empty:
        return []

    year_df = year_df.sort_values("date").reset_index(drop=True)
    last_frost_date, last_frost_doy = compute_last_frost(year_df, year)

    records: list[dict[str, Any]] = []
    cumulative_gdd = 0.0
    cumulative_rain = 0.0
    started = False

    for _, row in year_df.iterrows():
        d = row["date"]
        if isinstance(d, pd.Timestamp):
            dt = d.to_pydatetime()
        else:
            dt = d
        doy = dt.timetuple().tm_yday

        if not started and doy < last_frost_doy:
            continue
        started = True

        tmax = float(row["T2M_MAX"])
        tmin = float(row["T2M_MIN"])
        precip_mm = float(row["PRECTOTCORR"])

        daily_gdd = max((tmax + tmin) / 2.0 - GDD_BASE_C, 0.0)
        daily_rain = precip_mm * MM_TO_INCH
        cumulative_gdd += daily_gdd
        cumulative_rain += daily_rain

        records.append({
            "date": dt.strftime("%Y-%m-%d"),
            "dayOfYear": doy,
            "dailyGdd": round(daily_gdd, 4),
            "cumulativeGdd": round(cumulative_gdd, 4),
            "dailyRainfallIn": round(daily_rain, 4),
            "cumulativeRainfallIn": round(cumulative_rain, 4),
        })

    return records


def _read_aggregate_weather(farm_dir: Path) -> pd.DataFrame:
    tables_dir = farm_dir / "derived" / "tables"
    if not tables_dir.is_dir():
        return pd.DataFrame()
    for f in sorted(tables_dir.glob("*weather*.csv")):
        try:
            df = pd.read_csv(f)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
            return df
        except Exception:
            continue
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Data model builder
# ---------------------------------------------------------------------------

def build_dashboard_data(farm_dir: Path) -> dict[str, Any]:
    farm_dir = Path(farm_dir).expanduser().resolve()
    _validate_farm_layout(farm_dir)

    farm_meta = read_farm_json(farm_dir)
    farm_slug = farm_meta.get("farm_slug", farm_dir.name)
    farm_name = farm_meta.get("display_name", farm_slug.replace("-", " ").title())

    fields = read_boundaries(farm_dir)
    fields_merc = _mercator_project(fields)

    farm_data: dict[str, Any] = {
        "farmId": farm_slug,
        "farmName": farm_name,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "basemapAvailable": False,
    }

    field_list: list[dict[str, Any]] = []
    weather_by_field_year: list[dict[str, Any]] = []
    field_dir_map = _build_field_dir_map(farm_dir)

    # Read all per-field weather first
    all_weather_dfs: dict[str, pd.DataFrame] = {}
    for _, frow in fields.iterrows():
        fid = str(frow["field_id"])
        field_dir = field_dir_map.get(fid) or farm_dir / "fields" / _slugify(fid)
        field_meta = read_field_json(field_dir)
        display_name = field_meta.get("display_name", fid)

        weather_path = field_dir / "weather" / "daily_weather.csv"
        wdf = read_weather_csv(weather_path)
        has_weather = not wdf.empty and "field_id" in wdf.columns
        if has_weather:
            all_weather_dfs[fid] = wdf

        acres = None
        if "area_acres" in frow and not pd.isna(frow["area_acres"]):
            acres = round(float(frow["area_acres"]), 4)

        geom = frow.geometry
        merc_polys = _extract_polygons(geom)
        if not merc_polys:
            print(f"  WARNING: Skipping field {fid} with invalid geometry")
            continue

        available_years: list[int] = []
        if has_weather and "date" in wdf.columns:
            available_years = sorted(wdf["date"].dt.year.unique().tolist())

        field_list.append({
            "fieldId": fid,
            "fieldName": display_name,
            "acres": acres,
            "color": _color_hash(fid),
            "mercatorPolygons": merc_polys,
            "hasWeatherData": has_weather,
            "availableYears": available_years,
        })

    # No per-field weather: try aggregate
    if not all_weather_dfs:
        agg_df = _read_aggregate_weather(farm_dir)
        if not agg_df.empty and "field_id" in agg_df.columns:
            for fid in set(agg_df["field_id"].astype(str).unique()):
                mask = agg_df["field_id"].astype(str) == fid
                fdf = agg_df[mask].copy()
                if "date" in fdf.columns:
                    fdf["date"] = pd.to_datetime(fdf["date"], errors="coerce")
                all_weather_dfs[fid] = fdf
            if all_weather_dfs:
                print("  INFO: Using aggregate weather table as fallback.")
                # Update field hasWeatherData
                for f in field_list:
                    if f["fieldId"] in all_weather_dfs:
                        f["hasWeatherData"] = True
                        w = all_weather_dfs[f["fieldId"]]
                        if "date" in w.columns:
                            f["availableYears"] = sorted(
                                w["date"].dt.year.unique().tolist()
                            )

    # Build field-year weather records
    for f in field_list:
        fid = f["fieldId"]
        if fid not in all_weather_dfs:
            continue
        wdf = all_weather_dfs[fid]
        years = sorted(wdf["date"].dt.year.unique().tolist())
        for yr in years:
            records = compute_field_year_records(wdf, fid, yr)
            if records:
                weather_by_field_year.append({
                    "fieldId": fid,
                    "year": yr,
                    "lastFrostDate": records[0]["date"],
                    "lastFrostDoy": records[0]["dayOfYear"],
                    "daily": records,
                })

    if not weather_by_field_year:
        print("  WARNING: No weather data found for any field.")

    return {
        "farm": farm_data,
        "fields": field_list,
        "weatherByFieldYear": weather_by_field_year,
    }


# ---------------------------------------------------------------------------
# Plotly vendoring
# ---------------------------------------------------------------------------

def vendor_plotly(cache_dir: str | Path | None = None) -> str:
    cache = Path(cache_dir) if cache_dir else _ensure_cache_dir("plotly")
    cache_file = cache / f"plotly-{PLOTLY_VERSION}.min.js"
    if cache_file.exists():
        content = cache_file.read_text(encoding="utf-8")
        if len(content) > 1000:
            return content

    print(f"  Downloading Plotly v{PLOTLY_VERSION}...")
    resp = requests.get(PLOTLY_URL, timeout=30)
    resp.raise_for_status()
    content = resp.text
    cache_file.write_text(content, encoding="utf-8")
    print(f"  Cached Plotly bundle ({len(content)} bytes)")
    return content


# ---------------------------------------------------------------------------
# Satellite basemap
# ---------------------------------------------------------------------------

def _compute_tile_bounds(
    fields: gpd.GeoDataFrame,
) -> tuple[float, float, float, float, int, int, int, int, int]:
    merc = _mercator_project(fields)
    xmin, ymin, xmax, ymax = merc.total_bounds
    bx = (xmax - xmin) * 0.15
    by = (ymax - ymin) * 0.15
    xmin -= bx
    xmax += bx
    ymin -= by
    ymax += by

    world_size = xmax - xmin
    zoom = max(2, min(18, int(np.floor(np.log2(TARGET_PX_WIDTH * 360.0 / (world_size * 256.0))))))

    def _merc_to_tile(mx: float, my: float, z: int) -> tuple[int, int]:
        import math
        lon = mx / 20037508.34 * 180.0
        lat = (math.atan(math.exp(my / 20037508.34 * math.pi)) * 360.0 / math.pi) - 90.0
        lat_rad = math.radians(lat)
        n = 2.0 ** z
        xtile = int((lon + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
        return (xtile, ytile)

    x0, y1 = _merc_to_tile(xmin, ymin, zoom)
    x1f, y0f = _merc_to_tile(xmax, ymax, zoom)

    x1 = max(x0, x1f)
    y1t = max(y1, y0f)
    x0 = min(x0, x1f)
    y0 = min(y1, y0f)

    tile_count = (x1 - x0 + 1) * (y1t - y0 + 1)
    if tile_count > MAX_TILES:
        zoom = max(2, zoom - 1)
        x0, y1 = _merc_to_tile(xmin, ymin, zoom)
        x1f, y0f = _merc_to_tile(xmax, ymax, zoom)
        x1 = max(x0, x1f)
        y1t = max(y1, y0f)
        x0 = min(x0, x1f)
        y0 = min(y1, y0f)

    return xmin, ymin, xmax, ymax, zoom, x0, y0, x1, y1t


def _download_tile(url: str) -> bytes | None:
    for attempt in range(BASEMAP_RETRIES):
        try:
            resp = requests.get(url, timeout=BASEMAP_TIMEOUT)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            if attempt < BASEMAP_RETRIES - 1:
                time.sleep(1)
            else:
                print(f"  WARNING: Tile download failed after {BASEMAP_RETRIES} retries: {exc}")
    return None


def acquire_basemap(geojson_path: str | Path, cache_dir: str | Path | None = None) -> str | None:
    try:
        from PIL import Image
    except ImportError:
        print("  WARNING: Pillow not installed. Skipping satellite basemap.")
        return None

    fields = gpd.read_file(str(geojson_path))
    if fields.empty:
        return None

    try:
        xmin, ymin, xmax, ymax, zoom, tx0, ty0, tx1, ty1 = _compute_tile_bounds(fields)
    except Exception as exc:
        print(f"  WARNING: Could not compute tile bounds: {exc}")
        return None

    tile_cache = _ensure_cache_dir("tiles")
    stitched: Any = None

    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            cache_path = tile_cache / f"{zoom}_{tx}_{ty}.png"
            if cache_path.exists():
                age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
                if age_hours < TILE_CACHE_TTL_HOURS:
                    data = cache_path.read_bytes()
                else:
                    data = _download_tile(TILE_URL_TEMPLATE.format(z=zoom, x=tx, y=ty))
                    if data:
                        cache_path.write_bytes(data)
            else:
                data = _download_tile(TILE_URL_TEMPLATE.format(z=zoom, x=tx, y=ty))
                if data:
                    cache_path.write_bytes(data)

            if data is None:
                print(f"  WARNING: Could not load tile {zoom}/{tx}/{ty}")
                continue

            tile_img = Image.open(io.BytesIO(data)).convert("RGB")
            if stitched is None:
                total_w = (tx1 - tx0 + 1) * 256
                total_h = (ty1 - ty0 + 1) * 256
                stitched = Image.new("RGB", (total_w, total_h))
            px = (tx - tx0) * 256
            py = (ty - ty0) * 256
            stitched.paste(tile_img, (px, py))

    if stitched is None:
        print("  WARNING: No satellite tiles could be downloaded.")
        return None

    # Crop to exact Mercator bounds
    def _merc_to_tile_px(mx: float, my: float, z: int) -> tuple[float, float]:
        import math
        lon = mx / 20037508.34 * 180.0
        lat = (math.atan(math.exp(my / 20037508.34 * math.pi)) * 360.0 / math.pi) - 90.0
        lat_rad = math.radians(lat)
        n = 2.0 ** z
        xtile = (lon + 180.0) / 360.0 * n
        ytile = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
        xtile_px = (xtile - tx0) * 256.0
        ytile_px = (ytile - ty0) * 256.0
        return (xtile_px, ytile_px)

    crop_x0, crop_y1 = _merc_to_tile_px(xmin, ymin, zoom)
    crop_x1, crop_y0 = _merc_to_tile_px(xmax, ymax, zoom)
    crop_x0_i = max(0, int(crop_x0))
    crop_y0_i = max(0, int(crop_y0))
    crop_x1_i = min(stitched.width, int(crop_x1))
    crop_y1_i = min(stitched.height, int(crop_y1))

    if crop_x1_i > crop_x0_i and crop_y1_i > crop_y0_i:
        stitched = stitched.crop((crop_x0_i, crop_y0_i, crop_x1_i, crop_y1_i))

    buf = io.BytesIO()
    stitched.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    print(f"  Basemap: {stitched.width}x{stitched.height} px, {len(b64) // 1024} KB base64")
    return b64


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

DASHBOARD_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;background:#f4f5f7;color:#1e293b;line-height:1.5}
.header{background:#fff;border-bottom:1px solid #e2e8f0;padding:1rem 1.5rem;display:flex;flex-wrap:wrap;align-items:center;gap:1rem}
.header h1{font-size:1.25rem;font-weight:700;color:#0f172a;margin:0;flex:1}
.header .subtitle{font-size:0.85rem;color:#64748b;width:100%;margin-top:-0.5rem}
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem}
.controls button,.controls .dropdown-trigger{padding:0.4rem 0.75rem;font-size:0.8rem;border:1px solid #cbd5e1;border-radius:6px;background:#fff;color:#1e293b;cursor:pointer;font-family:inherit}
.controls button:hover,.controls .dropdown-trigger:hover{border-color:#94a3b8;background:#f8fafc}
.controls .dropdown-wrap{position:relative;display:inline-block}
.controls .dropdown-menu{display:none;position:absolute;top:100%;left:0;z-index:100;min-width:200px;max-height:280px;overflow-y:auto;background:#fff;border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.1);padding:0.25rem 0;margin-top:2px}
.controls .dropdown-menu.open{display:block}
.controls .dropdown-menu .dropdown-item{padding:0.35rem 0.75rem;font-size:0.8rem;cursor:pointer;display:flex;align-items:center;gap:0.5rem;white-space:nowrap}
.controls .dropdown-menu .dropdown-item:hover{background:#f1f5f9}
.controls .dropdown-menu .dropdown-item input[type=checkbox]{margin:0;accent-color:#1f77b4}
.controls .dropdown-menu .dropdown-item .no-data{color:#94a3b8;font-style:italic;font-size:0.75rem;margin-left:auto}
.controls .dropdown-menu .dropdown-action{padding:0.3rem 0.75rem;font-size:0.75rem;color:#2563eb;cursor:pointer;border-bottom:1px solid #f1f5f9}
.controls .dropdown-menu .dropdown-action:hover{background:#eff6ff}
.controls .sel-summary{font-size:0.78rem;color:#64748b}
.main{display:flex;height:calc(100vh - 70px);gap:0}
@media(max-width:900px){.main{flex-direction:column;height:auto}.map-pane{height:50vh}}
.map-pane{flex:1;min-width:0}
.chart-pane{flex:1;min-width:0;display:flex;flex-direction:column;border-left:1px solid #e2e8f0}
@media(max-width:900px){.chart-pane{border-left:none}}
.chart-pane .chart{flex:1;min-height:0}
.no-basemap-note{position:absolute;bottom:8px;left:8px;font-size:0.7rem;color:#94a3b8;background:rgba(255,255,255,0.85);padding:2px 6px;border-radius:4px;pointer-events:none;z-index:10}
"""


def _build_dashboard_html(
    data: dict[str, Any],
    plotly_js: str,
    basemap_b64: str | None,
) -> str:
    farm = data["farm"]
    fields = data["fields"]
    weather = data["weatherByFieldYear"]

    # Determine available years across all weather records
    all_years: list[int] = sorted(set(w["year"] for w in weather))
    default_year = 2025 if 2025 in all_years else (all_years[-1] if all_years else None)

    farm_id = farm["farmId"]
    farm_name = farm["farmName"]
    generated = farm["generatedAt"]
    basemap_available = basemap_b64 is not None

    data_json = json.dumps(data, indent=None, separators=(",", ":"))

    basemap_js = ""
    if basemap_b64:
        data_str = json.dumps(data, indent=None, separators=(",", ":"))
        # We inject basemap info into farm data
        data_json_for_embed = json.dumps(
            dict(data, farm={**data["farm"], "basemapAvailable": True}),
            indent=None, separators=(",", ":")
        )
        basemap_js = f"""
  var BASEMAP_B64 = "data:image/png;base64,{basemap_b64}";
  var BASEMAP_BOUNDS = null; // set dynamically
"""
    else:
        data_json_for_embed = json.dumps(
            dict(data, farm={**data["farm"], "basemapAvailable": False}),
            indent=None, separators=(",", ":")
        )
        basemap_js = "var BASEMAP_B64 = null; var BASEMAP_BOUNDS = null;"

    # Build the JS that sets up the dashboard
    dashboard_js = _build_dashboard_js(all_years, default_year)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{farm_name} — Grower Field Weather Dashboard</title>
<style>{DASHBOARD_CSS}</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Grower Field Weather Dashboard</h1>
    <div class="subtitle">{farm_name} &middot; {len(fields)} fields &middot; Generated {generated}</div>
  </div>
  <div class="controls" id="controls">
    <div class="dropdown-wrap" id="field-dropdown-wrap">
      <button class="dropdown-trigger" id="field-dropdown-trigger" aria-haspopup="listbox" aria-label="Select fields">Fields</button>
      <div class="dropdown-menu" id="field-dropdown-menu" role="listbox" aria-multiselectable="true"></div>
    </div>
    <div class="dropdown-wrap" id="year-dropdown-wrap">
      <button class="dropdown-trigger" id="year-dropdown-trigger" aria-haspopup="listbox" aria-label="Select years">Years</button>
      <div class="dropdown-menu" id="year-dropdown-menu" role="listbox" aria-multiselectable="true"></div>
    </div>
    <span class="sel-summary" id="sel-summary"></span>
    <button id="reset-btn">Reset view</button>
  </div>
</div>
<div class="main">
  <div class="map-pane" id="map-container"></div>
  <div class="chart-pane">
    <div class="chart" id="gdd-chart"></div>
    <div class="chart" id="rain-chart"></div>
  </div>
</div>
<script>
var DASHBOARD_DATA = {data_json_for_embed};
{basemap_js}
{dashboard_js}
</script>
<script>{plotly_js}</script>
<script>initDashboard(DASHBOARD_DATA);</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Dashboard JavaScript
# ---------------------------------------------------------------------------

def _build_dashboard_js(all_years: list[int], default_year: int | None) -> str:
    return f"""
var _updating = false;

function initDashboard(data) {{
  var farm = data.farm;
  var fields = data.fields;
  var weather = data.weatherByFieldYear;

  var allFieldIds = fields.map(function(f) {{ return f.fieldId; }});
  var allAvailableYears = [{",".join(str(y) for y in all_years)}];
  var defaultYear = {default_year if default_year is not None else "null"};

  var selectedFields = allFieldIds.slice();
  var selectedYears = defaultYear !== null ? [defaultYear] : [];

  function getColor(fid) {{
    for (var i = 0; i < fields.length; i++) {{
      if (fields[i].fieldId === fid) return fields[i].color;
    }}
    return "#1f77b4";
  }}

  function getFieldName(fid) {{
    for (var i = 0; i < fields.length; i++) {{
      if (fields[i].fieldId === fid) return fields[i].fieldName;
    }}
    return fid;
  }}

  function getField(fid) {{
    for (var i = 0; i < fields.length; i++) {{
      if (fields[i].fieldId === fid) return fields[i];
    }}
    return null;
  }}

  function filterWeather() {{
    return weather.filter(function(w) {{
      return selectedFields.indexOf(w.fieldId) >= 0 && selectedYears.indexOf(w.year) >= 0;
    }});
  }}

  // Build map traces
  function buildMapTraces(extentReset) {{
    var traces = [];
    var showAll = selectedFields.length === 0;
    for (var i = 0; i < fields.length; i++) {{
      var f = fields[i];
      var selected = showAll || selectedFields.indexOf(f.fieldId) >= 0;
      var fillColor = f.color + (selected ? "99" : "22");
      var lineWidth = selected ? 2.5 : 1;
      var polys = f.mercatorPolygons;
      for (var p = 0; p < polys.length; p++) {{
        var coords = polys[p];
        var xs = coords.map(function(c) {{ return c[0]; }});
        var ys = coords.map(function(c) {{ return c[1]; }});
        traces.push({{
          x: xs, y: ys,
          fill: "toself",
          fillcolor: fillColor,
          line: {{ color: f.color, width: lineWidth }},
          mode: "lines",
          type: "scatter",
          name: f.fieldName + " (" + (f.acres != null ? f.acres.toFixed(1) + " ac" : "") + ")",
          text: f.fieldName + (f.acres != null ? "\\\\n" + f.acres.toFixed(1) + " ac" : ""),
          hoverinfo: "text",
          customdata: f.fieldId,
          showlegend: false,
          legendgroup: f.fieldId,
        }});
      }}
    }}
    return traces;
  }}

  function buildMapLayout() {{
    var images = [];
    if (BASEMAP_B64) {{
      images.push({{
        source: BASEMAP_B64,
        xref: "x", yref: "y",
        x: 0, y: 0,
        sizex: 1, sizey: 1,
        xanchor: "left", yanchor: "bottom",
        sizing: "stretch",
        layer: "below",
        opacity: 1,
      }});
    }}
    return {{
      images: images,
      dragmode: "pan",
      hovermode: "closest",
      xaxis: {{
        visible: false, showgrid: false, zeroline: false,
        scaleanchor: "y", scaleratio: 1, constrain: "domain",
      }},
      yaxis: {{ visible: false, showgrid: false, zeroline: false }},
      margin: {{ l: 0, r: 0, t: 0, b: 0 }},
      paper_bgcolor: "#f4f5f7",
      plot_bgcolor: "#f4f5f7",
    }};
  }}

  function computeExtent(fids) {{
    if (!fids || fids.length === 0) {{
      fids = allFieldIds;
    }}
    var xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
    for (var i = 0; i < fields.length; i++) {{
      if (fids.indexOf(fields[i].fieldId) < 0) continue;
      var polys = fields[i].mercatorPolygons;
      for (var p = 0; p < polys.length; p++) {{
        for (var c = 0; c < polys[p].length; c++) {{
          var x = polys[p][c][0], y = polys[p][c][1];
          if (x < xmin) xmin = x; if (x > xmax) xmax = x;
          if (y < ymin) ymin = y; if (y > ymax) ymax = y;
        }}
      }}
    }}
    var bx = (xmax - xmin) * 0.2, by = (ymax - ymin) * 0.2;
    if (bx < 1) bx = 1; if (by < 1) by = 1;
    return [xmin - bx, xmax + bx, ymin - by, ymax + by];
  }}

  // GDD traces
  function buildGddTraces() {{
    var fw = filterWeather();
    var traces = [];
    for (var i = 0; i < fw.length; i++) {{
      var rec = fw[i];
      var doys = rec.daily.map(function(d) {{ return d.dayOfYear; }});
      var cumGdd = rec.daily.map(function(d) {{ return d.cumulativeGdd; }});
      var dates = rec.daily.map(function(d) {{ return d.date; }});
      var fid = rec.fieldId;
      var fn = getFieldName(fid);
      var c = getColor(fid);
      traces.push({{
        x: doys, y: cumGdd,
        mode: "lines",
        type: "scatter",
        name: fn + " " + rec.year,
        line: {{ color: c, width: 2 }},
        customdata: dates,
        hovertemplate: "<b>" + fn + "</b> " + rec.year + "<br>Date: %{{customdata}}<br>DOY: %{{x}}<br>Cumulative GDD: %{{y:.1f}}<extra></extra>",
        legendgroup: fid + "-" + rec.year,
      }});
      // Frost marker
      traces.push({{
        x: [rec.lastFrostDoy, rec.lastFrostDoy],
        y: [0, 1],
        mode: "lines",
        type: "scatter",
        name: fn + " " + rec.year + " frost",
        line: {{ color: c, width: 1.5, dash: "dot" }},
        yaxis: "y",
        yref: "paper",
        showlegend: false,
        hoverinfo: "skip",
        legendgroup: fid + "-" + rec.year + "-frost",
      }});
    }}
    if (traces.length === 0) {{
      traces.push({{
        x: [], y: [],
        mode: "text",
        type: "scatter",
        text: ["No data for selected fields/years"],
        textposition: "middle center",
        hoverinfo: "skip",
        showlegend: false,
      }});
    }}
    return traces;
  }}

  function buildGddLayout() {{
    return {{
      title: {{ text: "Growing Degree Days", font: {{ size: 14 }} }},
      xaxis: {{
        title: "Day of Year", dtick: 30,
        range: [{DEFAULT_X_RANGE[0]}, {DEFAULT_X_RANGE[1]}],
      }},
      yaxis: {{ title: "Cumulative GDD (base 10\\\\u00b0C)" }},
      margin: {{ l: 50, r: 20, t: 40, b: 50 }},
      legend: {{ orientation: "h", y: 1.02, x: 0, font: {{ size: 10 }} }},
      paper_bgcolor: "#fff",
      plot_bgcolor: "#fff",
    }};
  }}

  // Rainfall traces
  function buildRainTraces() {{
    var fw = filterWeather();
    var barTraces = [];
    var lineTraces = [];
    for (var i = 0; i < fw.length; i++) {{
      var rec = fw[i];
      var doys = rec.daily.map(function(d) {{ return d.dayOfYear; }});
      var dailyRain = rec.daily.map(function(d) {{ return d.dailyRainfallIn; }});
      var cumRain = rec.daily.map(function(d) {{ return d.cumulativeRainfallIn; }});
      var dates = rec.daily.map(function(d) {{ return d.date; }});
      var fid = rec.fieldId;
      var fn = getFieldName(fid);
      var c = getColor(fid);
      var legGroup = fid + "-" + rec.year;
      barTraces.push({{
        x: doys, y: dailyRain,
        type: "bar",
        name: fn + " " + rec.year + " (daily)",
        marker: {{ color: c, opacity: 0.25 }},
        customdata: dates,
        hovertemplate: "<b>" + fn + "</b> " + rec.year + "<br>Date: %{{customdata}}<br>DOY: %{{x}}<br>Daily rain: %{{y:.2f}} in<extra></extra>",
        yaxis: "y",
        legendgroup: legGroup,
      }});
      lineTraces.push({{
        x: doys, y: cumRain,
        mode: "lines",
        type: "scatter",
        name: fn + " " + rec.year + " (cumulative)",
        line: {{ color: c, width: 2 }},
        customdata: dates,
        hovertemplate: "<b>" + fn + "</b> " + rec.year + "<br>Date: %{{customdata}}<br>DOY: %{{x}}<br>Cumulative rain: %{{y:.2f}} in<extra></extra>",
        yaxis: "y2",
        legendgroup: legGroup,
      }});
    }}
    if (barTraces.length === 0 && lineTraces.length === 0) {{
      barTraces.push({{
        x: [], y: [], type: "bar", name: "",
        hoverinfo: "skip", showlegend: false,
      }});
    }}
    return barTraces.concat(lineTraces);
  }}

  function buildRainLayout() {{
    return {{
      title: {{ text: "Rainfall", font: {{ size: 14 }} }},
      xaxis: {{
        title: "Day of Year", dtick: 30,
        range: [{DEFAULT_X_RANGE[0]}, {DEFAULT_X_RANGE[1]}],
      }},
      yaxis: {{ title: "Daily rainfall (in)", side: "left" }},
      yaxis2: {{
        title: "Cumulative rainfall (in)", overlaying: "y", side: "right",
      }},
      barmode: "overlay",
      margin: {{ l: 50, r: 50, t: 40, b: 50 }},
      legend: {{ orientation: "h", y: 1.02, x: 0, font: {{ size: 10 }} }},
      paper_bgcolor: "#fff",
      plot_bgcolor: "#fff",
    }};
  }}

  function renderAll(resetExtent) {{
    if (_updating) return;
    var mapTraces = buildMapTraces();
    var mapLayout = buildMapLayout();
    var extent = computeExtent(selectedFields.length > 0 ? selectedFields : null);
    mapLayout.xaxis.range = [extent[0], extent[1]];
    mapLayout.yaxis.range = [extent[2], extent[3]];
    if (BASEMAP_B64) {{
      mapLayout.images[0].sizex = extent[1] - extent[0];
      mapLayout.images[0].sizey = extent[3] - extent[2];
      mapLayout.images[0].x = extent[0];
      mapLayout.images[0].y = extent[2];
    }}
    Plotly.react("map-container", mapTraces, mapLayout, {{ responsive: true }});

    var gddTraces = buildGddTraces();
    var gddLayout = buildGddLayout();
    if (resetExtent) {{
      gddLayout.xaxis.range = [{DEFAULT_X_RANGE[0]}, {DEFAULT_X_RANGE[1]}];
    }}
    Plotly.react("gdd-chart", gddTraces, gddLayout, {{ responsive: true }});

    var rainTraces = buildRainTraces();
    var rainLayout = buildRainLayout();
    if (resetExtent) {{
      rainLayout.xaxis.range = [{DEFAULT_X_RANGE[0]}, {DEFAULT_X_RANGE[1]}];
    }}
    Plotly.react("rain-chart", rainTraces, rainLayout, {{ responsive: true }});

    buildDropdowns();
    updateSummary();
  }}

  // Click map to toggle field
  document.getElementById("map-container").on("plotly_click", function(evt) {{
    if (!evt.points || evt.points.length === 0) return;
    var customdata = evt.points[0].customdata;
    if (!customdata) return;
    var fid = customdata;
    var idx = selectedFields.indexOf(fid);
    if (idx >= 0) {{
      if (selectedFields.length > 1) selectedFields.splice(idx, 1);
    }} else {{
      selectedFields.push(fid);
    }}
    renderAll(false);
  }});

  // X-axis sync
  var _syncingGdd = false;
  var _syncingRain = false;
  document.getElementById("gdd-chart").on("plotly_relayout", function(evt) {{
    if (_syncingGdd) return;
    if (evt["xaxis.range[0]"] !== undefined && evt["xaxis.range[1]"] !== undefined) {{
      _syncingRain = true;
      Plotly.relayout("rain-chart", {{ "xaxis.range[0]": evt["xaxis.range[0]"], "xaxis.range[1]": evt["xaxis.range[1]"] }});
      _syncingRain = false;
    }}
  }});
  document.getElementById("rain-chart").on("plotly_relayout", function(evt) {{
    if (_syncingRain) return;
    if (evt["xaxis.range[0]"] !== undefined && evt["xaxis.range[1]"] !== undefined) {{
      _syncingGdd = true;
      Plotly.relayout("gdd-chart", {{ "xaxis.range[0]": evt["xaxis.range[0]"], "xaxis.range[1]": evt["xaxis.range[1]"] }});
      _syncingGdd = false;
    }}
  }});

  // Dropdown logic
  function buildDropdowns() {{
    // Fields dropdown
    var menu = document.getElementById("field-dropdown-menu");
    var html = "";
    html += '<div class="dropdown-action" data-action="select-all-fields">Select all</div>';
    html += '<div class="dropdown-action" data-action="clear-all-fields">Clear all</div>';
    for (var i = 0; i < fields.length; i++) {{
      var f = fields[i];
      var checked = selectedFields.indexOf(f.fieldId) >= 0 ? "checked" : "";
      var noData = !f.hasWeatherData ? ' <span class="no-data">(no data)</span>' : "";
      html += '<label class="dropdown-item"><input type="checkbox" ' + checked + ' data-field-id="' + f.fieldId + '">' + f.fieldName + noData + '</label>';
    }}
    menu.innerHTML = html;

    // Years dropdown
    var ymenu = document.getElementById("year-dropdown-menu");
    var yhtml = "";
    yhtml += '<div class="dropdown-action" data-action="select-all-years">Select all</div>';
    yhtml += '<div class="dropdown-action" data-action="clear-all-years">Clear all</div>';
    for (var i = 0; i < allAvailableYears.length; i++) {{
      var yr = allAvailableYears[i];
      var ychecked = selectedYears.indexOf(yr) >= 0 ? "checked" : "";
      yhtml += '<label class="dropdown-item"><input type="checkbox" ' + ychecked + ' data-year="' + yr + '">' + yr + '</label>';
    }}
    ymenu.innerHTML = yhtml;

    // Re-bind events
    menu.querySelectorAll("input[type=checkbox]").forEach(function(cb) {{
      cb.addEventListener("change", function(ev) {{
        var fid = ev.target.getAttribute("data-field-id");
        if (fid) {{
          var idx = selectedFields.indexOf(fid);
          if (ev.target.checked && idx < 0) selectedFields.push(fid);
          else if (!ev.target.checked && idx >= 0) selectedFields.splice(idx, 1);
        }}
        var yr = parseInt(ev.target.getAttribute("data-year"));
        if (!isNaN(yr)) {{
          var yidx = selectedYears.indexOf(yr);
          if (ev.target.checked && yidx < 0) selectedYears.push(yr);
          else if (!ev.target.checked && yidx >= 0) selectedYears.splice(yidx, 1);
        }}
        selectedYears.sort();
        renderAll(false);
      }});
    }});
    menu.querySelectorAll("[data-action=select-all-fields]").forEach(function(el) {{
      el.addEventListener("click", function() {{ selectedFields = allFieldIds.slice(); renderAll(false); }});
    }});
    menu.querySelectorAll("[data-action=clear-all-fields]").forEach(function(el) {{
      el.addEventListener("click", function() {{ selectedFields = []; renderAll(false); }});
    }});
    var ymenuEl = document.getElementById("year-dropdown-menu");
    ymenuEl.querySelectorAll("[data-action=select-all-years]").forEach(function(el) {{
      el.addEventListener("click", function() {{ selectedYears = allAvailableYears.slice(); renderAll(false); }});
    }});
    ymenuEl.querySelectorAll("[data-action=clear-all-years]").forEach(function(el) {{
      el.addEventListener("click", function() {{ selectedYears = []; renderAll(false); }});
    }});
  }}

  function updateSummary() {{
    var el = document.getElementById("sel-summary");
    el.textContent = selectedFields.length + " field(s), " + selectedYears.length + " year(s)";
  }}

  // Dropdown toggle
  function setupDropdown(triggerId, menuId) {{
    var trigger = document.getElementById(triggerId);
    var menu = document.getElementById(menuId);
    trigger.addEventListener("click", function(ev) {{
      ev.stopPropagation();
      var open = menu.classList.toggle("open");
    }});
    document.addEventListener("click", function(ev) {{
      if (!menu.contains(ev.target) && ev.target !== trigger) {{
        menu.classList.remove("open");
      }}
    }});
  }}
  setupDropdown("field-dropdown-trigger", "field-dropdown-menu");
  setupDropdown("year-dropdown-trigger", "year-dropdown-menu");

  // Reset
  document.getElementById("reset-btn").addEventListener("click", function() {{
    selectedFields = allFieldIds.slice();
    selectedYears = defaultYear !== null ? [defaultYear] : [];
    renderAll(true);
  }});

  // Initial render
  renderAll(true);
}}
"""


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

def write_dashboard(html: str, output_path: Path) -> Path:
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.parent / f".{output_path.name}.tmp"
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(output_path)
    return output_path


def generate_dashboard(
    farm_dir: str | Path,
    output: str | Path | None = None,
    no_basemap: bool = False,
    cache_dir: str | Path | None = None,
    plotly_js: str | None = None,
    basemap_b64: str | None = None,
) -> Path:
    farm_dir = Path(farm_dir).expanduser().resolve()
    _validate_farm_layout(farm_dir)

    farm_meta = read_farm_json(farm_dir)
    farm_slug = farm_meta.get("farm_slug", farm_dir.name)

    if output is None:
        output = farm_dir / "derived" / "dashboards" / f"{farm_slug}_dashboard.html"

    output_path = Path(output).expanduser().resolve()

    print("=" * 60)
    print("  Grower Field Weather Dashboard Generator")
    print(f"  Farm directory: {farm_dir}")
    print(f"  Output: {output_path}")
    print("=" * 60)

    print("  Building dashboard data model...")
    data = build_dashboard_data(farm_dir)
    n_fields = len(data["fields"])
    n_weather = len(data["weatherByFieldYear"])
    print(f"  Fields: {n_fields}")
    print(f"  Field-year weather records: {n_weather}")

    if plotly_js is None:
        print("  Vendoring Plotly bundle...")
        plotly_js = vendor_plotly(cache_dir)
    print(f"  Plotly bundle: {len(plotly_js)} chars")

    if not no_basemap and basemap_b64 is None:
        boundary_path = farm_dir / "boundary" / "field_boundaries.geojson"
        if boundary_path.exists():
            print("  Acquiring satellite basemap...")
            basemap_b64 = acquire_basemap(boundary_path, cache_dir)
        else:
            print("  SKIP: No boundary file for basemap.")

    if basemap_b64:
        print("  Basemap: available")
    else:
        print("  Basemap: not available (functional dashboard will be generated)")

    print("  Rendering HTML...")
    html = _build_dashboard_html(data, plotly_js, basemap_b64)

    print("  Writing dashboard...")
    result = write_dashboard(html, output_path)
    size_kb = result.stat().st_size / 1024
    print(f"  DONE: {result} ({size_kb:.0f} KB)")
    print("=" * 60)
    return result
