#!/usr/bin/env python3
"""Generate lightweight interactive grower web map per farm using only stdlib."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FIELD_COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9a6324", "#800000", "#aaffc3",
]


def _discover(data_root: Path) -> list[tuple[str, str, Path]]:
    """Return (grower_slug, farm_slug, geojson_path) tuples."""
    results: list[tuple[str, str, Path]] = []
    growers_dir = data_root / "data-pipeline" / "growers"
    if not growers_dir.is_dir():
        return results
    for g_dir in sorted(growers_dir.iterdir()):
        if not g_dir.is_dir() or g_dir.name.startswith("."):
            continue
        farms_dir = g_dir / "farms"
        if not farms_dir.is_dir():
            continue
        for f_dir in sorted(farms_dir.iterdir()):
            if not f_dir.is_dir() or f_dir.name.startswith("."):
                continue
            geojson = f_dir / "boundary" / "field_boundaries.geojson"
            if geojson.exists():
                results.append((g_dir.name, f_dir.name, geojson))
    return results


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _center_from_coords(coords: list) -> tuple[float, float]:
    lats, lngs = [], []
    for feat in coords:
        _collect_coords(feat.get("geometry", {}), lats, lngs)
    if not lats:
        return 40.0, -90.0
    return (min(lats) + max(lats)) / 2, (min(lngs) + max(lngs)) / 2


def _collect_coords(geom: dict, lats: list, lngs: list) -> None:
    t = geom.get("type")
    c = geom.get("coordinates", [])
    if t == "Polygon":
        for ring in c:
            for pt in ring:
                if len(pt) >= 2:
                    lngs.append(pt[0])
                    lats.append(pt[1])
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                for pt in ring:
                    if len(pt) >= 2:
                        lngs.append(pt[0])
                        lats.append(pt[1])


def _build_html(
    grower: str,
    farm: str,
    farm_name: str,
    geojson: dict,
) -> str:
    features = geojson.get("features", [])
    colored: list[dict] = []
    for i, feat in enumerate(features):
        props = dict(feat.get("properties", {}))
        props["color"] = FIELD_COLORS[i % len(FIELD_COLORS)]
        colored.append({"type": "Feature", "properties": props, "geometry": feat.get("geometry", {})})

    geojson_str = json.dumps({"type": "FeatureCollection", "features": colored})

    center_lat, center_lng = _center_from_coords(features)

    field_items = []
    for i, feat in enumerate(features):
        p = feat.get("properties", {})
        fid = str(p.get("field_id", f"field-{i}"))
        color = FIELD_COLORS[i % len(FIELD_COLORS)]
        crop = str(p.get("crop_name", ""))
        acres = round(float(p.get("area_acres", 0)), 1)
        field_items.append(
            f'    <div class="field-item" onclick="zoomTo({i})" '
            f'style="border-left:4px solid {color}">'
            f'<span class="fn">{fid}</span>'
            f'<span class="fm">{crop or "\u2014"} &middot; {acres} ac</span></div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{farm_name} &mdash; Grower Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px}}
#wr{{display:flex;height:100%}}
#sb{{width:280px;min-width:280px;background:#f8f9fa;border-right:1px solid #dee2e6;display:flex;flex-direction:column;overflow:hidden}}
#sbh{{padding:14px 16px;background:#2c3e50;color:#fff}}
#sbh h1{{font-size:15px;font-weight:600;margin-bottom:2px}}
#sbh p{{font-size:11px;opacity:.85}}
#fl{{flex:1;overflow-y:auto;padding:8px}}
.field-item{{padding:8px 10px;margin-bottom:4px;background:#fff;border-radius:4px;cursor:pointer;box-shadow:0 1px 2px rgba(0,0,0,.06);transition:background .15s}}
.field-item:hover{{background:#e9ecef}}
.fn{{display:block;font-size:13px;font-weight:600;color:#212529}}
.fm{{display:block;font-size:11px;color:#6c757d;margin-top:1px}}
#map{{flex:1;height:100%}}
.lp-c{{font-size:13px;line-height:1.5}}
</style>
</head>
<body>
<div id="wr">
<div id="sb"><div id="sbh"><h1>{farm_name}</h1><p>{grower} &middot; {len(features)} fields</p></div><div id="fl">{"".join(field_items)}</div></div>
<div id="map"></div>
</div>
<script>
var map=L.map('map',{{zoomControl:true}}).setView([{center_lat},{center_lng}],13);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',{{attribution:'Esri, USDA, USGS',maxZoom:19}}).addTo(map);
var data={geojson_str};
var layers=[];
var gj=L.geoJSON(data,{{
style:function(f){{return{{fillColor:f.properties.color,weight:2,opacity:1,color:'#fff',fillOpacity:.65}}}},
onEachFeature:function(f,l){{
var p=f.properties;
l.bindPopup('<strong>'+p.field_id+'</strong><br>Grower: '+p.grower+'<br>Farm: '+p.farm_name+'<br>Crop: '+(p.crop_name||'')+'<br>County: '+(p.county_name||'')+'<br>Area: '+p.area_acres+' ac');
layers.push(l);
}}
}}).addTo(map);
map.fitBounds(gj.getBounds().pad(.1));
function zoomTo(i){{if(layers[i]){{map.fitBounds(layers[i].getBounds().pad(.3));layers[i].openPopup()}}}}
</script>
</body>
</html>"""


def main() -> None:
    data_root = os.environ.get("DATA_PIPELINE_DATA_ROOT", "").strip()
    if not data_root:
        print("ERROR: DATA_PIPELINE_DATA_ROOT is not set.", file=sys.stderr)
        sys.exit(1)

    root = Path(data_root).resolve()
    farms = _discover(root)

    if not farms:
        print(f"No grower farms found under {root / 'data-pipeline' / 'growers'}")
        sys.exit(0)

    for grower, farm, geojson_path in farms:
        print(f"{grower}/{farm} ...", end=" ", flush=True)

        farm_json_path = geojson_path.parents[1] / "farm.json"
        farm_info = _read_json(farm_json_path)
        farm_name = farm_info.get("display_name", farm)

        raw = _read_json(geojson_path)
        features = raw.get("features", [])
        if not features:
            print("skip (no features)")
            continue

        html = _build_html(grower, farm, farm_name, raw)

        out_dir = geojson_path.parents[1] / "derived" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{grower}_{farm}_grower_map.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"{len(html) / 1024:.0f} KB, {len(features)} fields -> {out_path}")


if __name__ == "__main__":
    main()
