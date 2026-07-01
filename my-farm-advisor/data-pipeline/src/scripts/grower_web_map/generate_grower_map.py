#!/usr/bin/env python3
"""Generate interactive grower web map for each grower/farm in the pipeline."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import mapping as shapely_mapping

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from runtime_paths import resolve_runtime_paths

_RUNTIME_PATHS = resolve_runtime_paths()
_DATA_ROOT = _RUNTIME_PATHS.runtime_base

sys.path.insert(0, str(_RUNTIME_PATHS.runtime_scripts))
sys.path.insert(0, str(_RUNTIME_PATHS.runtime_scripts / "lib"))

from paths import farm_boundary_path, farm_dir, farm_reports_dir, grower_dir


FIELD_COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9a6324", "#800000", "#aaffc3",
]


def _discover_growers() -> list[str]:
    growers_root = _DATA_ROOT / "growers"
    if not growers_root.is_dir():
        return []
    return sorted(
        d.name for d in growers_root.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def _discover_farms(grower_slug: str) -> list[str]:
    farms_dir = grower_dir(grower_slug) / "farms"
    if not farms_dir.is_dir():
        return []
    return sorted(
        d.name for d in farms_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def _read_grower_json(grower_slug: str) -> dict:
    path = grower_dir(grower_slug) / "grower.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"grower_slug": grower_slug, "display_name": grower_slug}


def _read_farm_json(grower_slug: str, farm_slug: str) -> dict:
    path = farm_dir(grower_slug, farm_slug) / "farm.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"farm_slug": farm_slug, "display_name": farm_slug}


def _build_html(
    grower_slug: str,
    farm_slug: str,
    farm_name: str,
    state: str,
    fields_gdf: gpd.GeoDataFrame,
) -> str:
    features = []
    for i, (_, row) in enumerate(fields_gdf.iterrows()):
        color = FIELD_COLORS[i % len(FIELD_COLORS)]
        geom = shapely_mapping(row.geometry)
        props = {
            "field_id": str(row.get("field_id", "")),
            "grower": grower_slug,
            "farm": farm_slug,
            "farm_name": farm_name,
            "state": state,
            "crop_name": str(row.get("crop_name", "")),
            "county_name": str(row.get("county_name", "")),
            "area_acres": round(float(row.get("area_acres", 0)), 1),
            "state_fips": str(row.get("state_fips", "")),
            "county_fips": str(row.get("county_fips", "")),
            "source": str(row.get("source", "")),
            "color": color,
        }
        features.append({"type": "Feature", "properties": props, "geometry": geom})

    geojson_str = json.dumps({"type": "FeatureCollection", "features": features})

    field_list_items = []
    for i, (_, row) in enumerate(fields_gdf.iterrows()):
        fid = str(row.get("field_id", f"field-{i}"))
        color = FIELD_COLORS[i % len(FIELD_COLORS)]
        crop = str(row.get("crop_name", ""))
        acres = round(float(row.get("area_acres", 0)), 1)
        field_list_items.append(f"""
    <div class="field-item" onclick="zoomToField({i})" style="border-left: 4px solid {color};">
      <span class="field-name">{fid}</span>
      <span class="field-meta">{crop or "—"} &middot; {acres} ac</span>
    </div>""")

    field_list_html = "\n".join(field_list_items)

    total_bounds = fields_gdf.total_bounds
    center_lat = float((total_bounds[1] + total_bounds[3]) / 2)
    center_lng = float((total_bounds[0] + total_bounds[2]) / 2)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{farm_name} &mdash; Grower Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  #wrapper {{ display: flex; height: 100%; }}
  #sidebar {{
    width: 300px; min-width: 300px; background: #f8f9fa; border-right: 1px solid #dee2e6;
    display: flex; flex-direction: column; overflow: hidden;
  }}
  #sidebar-header {{
    padding: 16px; background: #2c3e50; color: #fff;
  }}
  #sidebar-header h1 {{ font-size: 16px; font-weight: 600; margin-bottom: 4px; }}
  #sidebar-header p {{ font-size: 12px; opacity: .85; }}
  #field-list {{ flex: 1; overflow-y: auto; padding: 8px; }}
  .field-item {{
    padding: 10px 12px; margin-bottom: 6px; background: #fff; border-radius: 4px;
    cursor: pointer; transition: background .15s;
    box-shadow: 0 1px 2px rgba(0,0,0,.06);
  }}
  .field-item:hover {{ background: #e9ecef; }}
  .field-name {{ display: block; font-size: 13px; font-weight: 600; color: #212529; }}
  .field-meta {{ display: block; font-size: 11px; color: #6c757d; margin-top: 2px; }}
  #map {{ flex: 1; height: 100%; }}
  .leaflet-popup-content {{ font-size: 13px; line-height: 1.5; }}
  .leaflet-popup-content strong {{ color: #2c3e50; }}
</style>
</head>
<body>
<div id="wrapper">
  <div id="sidebar">
    <div id="sidebar-header">
      <h1>{farm_name}</h1>
      <p>Grower: {grower_slug} &middot; {state or "—"} &middot; {len(fields_gdf)} fields</p>
    </div>
    <div id="field-list">{field_list_html}</div>
  </div>
  <div id="map"></div>
</div>
<script>
  var map = L.map('map').setView([{center_lat}, {center_lng}], 13);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19
  }}).addTo(map);

  var fields = {geojson_str};

  var fieldLayers = [];
  var geojsonLayer = L.geoJSON(fields, {{
    style: function(feature) {{
      return {{
        fillColor: feature.properties.color,
        weight: 2, opacity: 1, color: '#fff', fillOpacity: 0.65
      }};
    }},
    onEachFeature: function(feature, layer) {{
      var p = feature.properties;
      layer.bindPopup(
        '<strong>' + p.field_id + '</strong><br>' +
        'Grower: ' + p.grower + '<br>' +
        'Farm: ' + p.farm_name + '<br>' +
        'Crop: ' + p.crop_name + '<br>' +
        'County: ' + p.county_name + '<br>' +
        'Area: ' + p.area_acres + ' acres'
      );
      fieldLayers.push(layer);
    }}
  }}).addTo(map);
  map.fitBounds(geojsonLayer.getBounds().pad(0.1));

  function zoomToField(index) {{
    if (fieldLayers[index]) {{
      map.fitBounds(fieldLayers[index].getBounds().pad(0.3));
      fieldLayers[index].openPopup();
    }}
  }}
</script>
</body>
</html>"""
    return html


def _generate_for_grower(grower_slug: str) -> list[Path]:
    outputs: list[Path] = []
    farms = _discover_farms(grower_slug)
    grower_info = _read_grower_json(grower_slug)

    if not farms:
        print(f"  No farms found for grower '{grower_slug}'")
        return outputs

    for farm_slug in farms:
        farm_info = _read_farm_json(grower_slug, farm_slug)
        farm_name = farm_info.get("display_name", farm_slug)
        state = farm_info.get("state", "")
        boundaries_path = farm_boundary_path(grower_slug, farm_slug)

        if not boundaries_path.exists():
            print(f"  skip  {grower_slug}/{farm_slug} (no field boundaries)")
            continue

        fields_gdf = gpd.read_file(boundaries_path)
        if fields_gdf.empty:
            print(f"  skip  {grower_slug}/{farm_slug} (empty boundary file)")
            continue

        html = _build_html(grower_slug, farm_slug, farm_name, state, fields_gdf)
        out_dir = farm_reports_dir(grower_slug, farm_slug)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{grower_slug}_{farm_slug}_grower_map.html"
        out_path.write_text(html, encoding="utf-8")
        size_kb = len(html) / 1024
        print(f"  wrote {out_path} ({size_kb:.0f} KB, {len(fields_gdf)} fields)")
        outputs.append(out_path)

    return outputs


def main() -> None:
    print("=" * 60)
    print("Grower Web Map Generator")
    print("=" * 60)

    growers = _discover_growers()
    if not growers:
        print("No growers found under", _DATA_ROOT / "growers")
        sys.exit(1)

    total = 0
    for grower_slug in growers:
        print(f"\nGrower: {grower_slug}")
        outputs = _generate_for_grower(grower_slug)
        total += len(outputs)

    print(f"\n{'=' * 60}")
    print(f"Done. Generated {total} grower map(s).")


if __name__ == "__main__":
    main()
