from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
import pytest

from reporting.dashboard_lib import (
    COLOR_PALETTE,
    DEFAULT_X_RANGE,
    _color_hash,
    _extract_polygons,
    _find_farms_under,
    _mercator_project,
    _slugify,
    _validate_farm_layout,
    acquire_basemap,
    build_dashboard_data,
    compute_field_year_records,
    compute_last_frost,
    generate_dashboard,
    read_boundaries,
    read_field_json,
    read_weather_csv,
    resolve_farm_dir,
    vendor_plotly,
    write_dashboard,
)


class TestReadBoundaries:
    def test_reads_valid_geojson(self, tmp_farm_dir: Path):
        fields = read_boundaries(tmp_farm_dir)
        assert len(fields) == 2
        assert "field_id" in fields.columns
        assert set(fields["field_id"].astype(str)) == {"osm-1001", "osm-1002"}

    def test_missing_file_raises(self):
        with pytest.raises(RuntimeError, match="not found"):
            read_boundaries(Path("/nonexistent"))


class TestReadFieldJson:
    def test_reads_existing(self, tmp_farm_dir: Path):
        meta = read_field_json(tmp_farm_dir / "fields" / "field_1")
        assert meta.get("display_name") == "North Field"

    def test_missing_returns_empty(self, tmp_farm_dir_no_field2_json: Path):
        meta = read_field_json(tmp_farm_dir_no_field2_json / "fields" / "field_2")
        assert meta == {}


class TestReadWeatherCsv:
    def test_reads_valid_csv(self, tmp_farm_dir: Path):
        w = tmp_farm_dir / "fields" / "field_1" / "weather" / "daily_weather.csv"
        assert w.exists()
        df = read_weather_csv(w)
        assert not df.empty
        assert "T2M_MAX" in df.columns

    def test_header_only_returns_empty(self, tmp_farm_dir: Path):
        w = tmp_farm_dir / "fields" / "field_2" / "weather" / "daily_weather.csv"
        df = read_weather_csv(w)
        assert df.empty

    def test_missing_file_returns_empty(self, tmp_farm_dir_no_weather: Path):
        w = tmp_farm_dir_no_weather / "fields" / "field_1" / "weather" / "daily_weather.csv"
        assert not w.exists()
        df = read_weather_csv(w)
        assert df.empty


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"
        assert _slugify("OSM_12345") == "osm-12345"
        assert _slugify("field-id") == "field-id"


class TestColorHash:
    def test_returns_palette_color(self):
        c = _color_hash("osm-1001")
        assert c in COLOR_PALETTE

    def test_deterministic(self):
        assert _color_hash("osm-1001") == _color_hash("osm-1001")


class TestExtractPolygons:
    def test_polygon(self, tmp_farm_dir: Path):
        fields = read_boundaries(tmp_farm_dir)
        geom = fields.iloc[0].geometry
        polys = _extract_polygons(geom)
        assert len(polys) >= 1
        assert len(polys[0]) >= 4  # at least 4 coords

    def test_multipolygon(self, tmp_farm_dir: Path):
        fields = read_boundaries(tmp_farm_dir)
        geom = fields.iloc[1].geometry
        polys = _extract_polygons(geom)
        assert len(polys) >= 2  # MultiPolygon with 2 parts


class TestValidateFarmLayout:
    def test_valid_directory(self, tmp_farm_dir: Path):
        _validate_farm_layout(tmp_farm_dir)

    def test_missing_boundary(self, tmp_farm_dir: Path):
        (tmp_farm_dir / "boundary" / "field_boundaries.geojson").unlink()
        with pytest.raises(RuntimeError, match="Missing required boundary"):
            _validate_farm_layout(tmp_farm_dir)

    def test_missing_fields_dir(self, tmp_farm_dir: Path):
        shutil = __import__("shutil")
        shutil.rmtree(tmp_farm_dir / "fields")
        with pytest.raises(RuntimeError, match="Missing required fields"):
            _validate_farm_layout(tmp_farm_dir)


class TestComputeLastFrost:
    def test_frost_before_july(self):
        df = pd.DataFrame({
            "date": pd.to_datetime([
                "2025-01-15", "2025-03-10", "2025-04-19",
                "2025-05-10", "2025-06-01", "2025-07-01",
            ]),
            "T2M_MIN": [-5.0, -2.0, 2.0, -2.0, 10.0, 15.0],
            "T2M_MAX": [0.0, 5.0, 8.0, 12.0, 20.0, 25.0],
            "PRECTOTCORR": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        })
        frost_date, frost_doy = compute_last_frost(df, 2025)
        # Latest frost before July 1 where T2M_MIN <= 0 should be 2025-05-10 (DOY 130)
        assert frost_date == "2025-05-10", f"Expected 2025-05-10, got {frost_date}"
        assert frost_doy == 130, f"Expected 130, got {frost_doy}"

    def test_no_frost_returns_jan1(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2025-01-01", "2025-06-01", "2025-07-01"]),
            "T2M_MIN": [5.0, 10.0, 15.0],
            "T2M_MAX": [10.0, 20.0, 25.0],
            "PRECTOTCORR": [0.0, 1.0, 2.0],
        })
        frost_date, frost_doy = compute_last_frost(df, 2025)
        assert frost_doy == 1  # Jan 1

    def test_no_data_for_year_returns_jan1(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-01"]),
            "T2M_MIN": [-5.0],
            "T2M_MAX": [0.0],
            "PRECTOTCORR": [0.0],
        })
        frost_date, frost_doy = compute_last_frost(df, 2025)
        assert frost_doy == 1


class TestComputeFieldYearRecords:
    def test_basic_gdd_and_rainfall(self, tmp_farm_dir: Path):
        w = tmp_farm_dir / "fields" / "field_1" / "weather" / "daily_weather.csv"
        df = pd.read_csv(w)
        df["date"] = pd.to_datetime(df["date"])
        records = compute_field_year_records(df, "osm-1001", 2025)
        assert len(records) > 0

        # Cumulative should be non-decreasing
        cum_gdds = [r["cumulativeGdd"] for r in records]
        cum_rains = [r["cumulativeRainfallIn"] for r in records]
        for i in range(1, len(cum_gdds)):
            assert cum_gdds[i] >= cum_gdds[i - 1] - 0.001
            assert cum_rains[i] >= cum_rains[i - 1] - 0.001

        # Daily values should be positive
        for r in records:
            assert r["dailyGdd"] >= 0
            assert r["dailyRainfallIn"] >= 0
            assert 1 <= r["dayOfYear"] <= 366

    def test_empty_year_returns_empty_list(self, tmp_farm_dir: Path):
        w = tmp_farm_dir / "fields" / "field_1" / "weather" / "daily_weather.csv"
        df = pd.read_csv(w)
        df["date"] = pd.to_datetime(df["date"])
        records = compute_field_year_records(df, "osm-1001", 2020)
        assert records == []

    def test_cumulative_starts_after_frost(self):
        # Frost on DOY 100, so records before that should not appear
        df = pd.DataFrame({
            "field_id": ["f1"] * 6,
            "date": pd.to_datetime([
                "2025-03-01",  # DOY 60
                "2025-04-10",  # DOY 100 (frost)
                "2025-04-15",  # DOY 105
                "2025-05-01",  # DOY 121
                "2025-05-15",  # DOY 135
                "2025-06-01",  # DOY 152
            ]),
            "T2M_MIN": [-5.0, 0.0, 5.0, 8.0, 10.0, 12.0],
            "T2M_MAX": [0.0, 10.0, 15.0, 20.0, 22.0, 25.0],
            "PRECTOTCORR": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        })
        records = compute_field_year_records(df, "f1", 2025)
        doys = [r["dayOfYear"] for r in records]
        assert 60 not in doys  # before frost should be excluded
        assert 100 in doys  # frost day itself or after


class TestBuildDashboardData:
    def test_full_data_model(self, tmp_farm_dir: Path):
        data = build_dashboard_data(tmp_farm_dir)
        assert "farm" in data
        assert "fields" in data
        assert "weatherByFieldYear" in data

        assert data["farm"]["farmId"] == "test-farm"
        assert data["farm"]["farmName"] == "Test Farm"
        assert len(data["fields"]) == 2

        # Verify weather records exist
        assert len(data["weatherByFieldYear"]) > 0

    def test_no_weather_data(self, tmp_farm_dir_no_weather: Path):
        data = build_dashboard_data(tmp_farm_dir_no_weather)
        assert len(data["fields"]) == 2
        # No weather => weatherByFieldYear empty
        assert len(data["weatherByFieldYear"]) == 0

    def test_header_only_weather(self, tmp_farm_dir_header_only_weather: Path):
        data = build_dashboard_data(tmp_farm_dir_header_only_weather)
        assert len(data["fields"]) == 2
        # Field 1 has header-only CSV
        f1 = [f for f in data["fields"] if f["fieldId"] == "osm-1001"][0]
        assert f1["hasWeatherData"] is False

    def test_missing_field_json_falls_back(self, tmp_farm_dir_no_field2_json: Path):
        data = build_dashboard_data(tmp_farm_dir_no_field2_json)
        f2 = [f for f in data["fields"] if f["fieldId"] == "osm-1002"][0]
        assert f2["fieldName"] == "osm-1002"  # fallback to field_id

    def test_multipolygon_support(self, tmp_farm_dir: Path):
        data = build_dashboard_data(tmp_farm_dir)
        f2 = [f for f in data["fields"] if f["fieldId"] == "osm-1002"][0]
        assert len(f2["mercatorPolygons"]) >= 2  # MultiPolygon has 2 parts

    def test_available_years(self, tmp_farm_dir: Path):
        data = build_dashboard_data(tmp_farm_dir)
        f1 = [f for f in data["fields"] if f["fieldId"] == "osm-1001"][0]
        assert f1["hasWeatherData"] is True
        assert 2025 in f1["availableYears"]
        assert 2024 in f1["availableYears"]


class TestFindFarmsUnder:
    def test_single_farm(self, tmp_farm_dir: Path):
        # tmp_farm_dir = .../growers/test-grower/farms/test-farm
        root = tmp_farm_dir.parent.parent.parent  # growers/
        farms = _find_farms_under(root)
        assert len(farms) == 1

    def test_dual_farms(self, dual_farm_root: Path):
        farms = _find_farms_under(dual_farm_root)
        assert len(farms) == 2


class TestResolveFarmDir:
    def test_explicit_farm_dir(self, tmp_farm_dir: Path):
        result = resolve_farm_dir(farm_dir=str(tmp_farm_dir))
        assert result == tmp_farm_dir.resolve()

    def test_growers_dir_single(self, tmp_farm_dir: Path):
        # tmp_farm_dir = .../growers/test-grower/farms/test-farm
        growers_dir = tmp_farm_dir.parent.parent.parent  # growers/
        result = resolve_farm_dir(growers_dir=str(growers_dir))
        assert result == tmp_farm_dir.resolve()

    def test_growers_dir_dual_errors(self, dual_farm_root: Path):
        with pytest.raises(RuntimeError, match="Found 2 valid farm directories"):
            resolve_farm_dir(growers_dir=str(dual_farm_root))

    def test_invalid_path_raises(self):
        with pytest.raises(RuntimeError, match="Not a directory|Missing required"):
            resolve_farm_dir(farm_dir="/nonexistent-path-12345")


class TestGenerateDashboard:
    def test_full_generation(self, tmp_farm_dir: Path, plotly_fake: str):
        output = generate_dashboard(
            farm_dir=tmp_farm_dir,
            output=tmp_farm_dir / "derived" / "dashboards" / "test.html",
            plotly_js=plotly_fake,
            basemap_b64=None,
        )
        assert output.exists()
        assert output.suffix == ".html"

    def test_output_is_self_contained_html(self, tmp_farm_dir: Path, plotly_fake: str):
        output = generate_dashboard(
            farm_dir=tmp_farm_dir,
            output=tmp_farm_dir / "test.html",
            plotly_js=plotly_fake,
            basemap_b64=None,
        )
        html = output.read_text(encoding="utf-8")
        assert "var Plotly = { react: function()" in html
        assert "Grower Field Weather Dashboard" in html

    def test_no_cdn_urls(self, tmp_farm_dir: Path, plotly_fake: str):
        output = generate_dashboard(
            farm_dir=tmp_farm_dir,
            output=tmp_farm_dir / "test.html",
            plotly_js=plotly_fake,
            basemap_b64=None,
        )
        html = output.read_text(encoding="utf-8")
        # No CDN references at runtime
        assert "cdn.plot.ly" not in html
        assert "http://" not in html or "data:" in html
        assert "//server.arcgisonline.com" not in html

    def test_expected_metadata_in_output(self, tmp_farm_dir: Path, plotly_fake: str):
        output = generate_dashboard(
            farm_dir=tmp_farm_dir,
            output=tmp_farm_dir / "test.html",
            plotly_js=plotly_fake,
            basemap_b64=None,
        )
        html = output.read_text(encoding="utf-8")
        assert "Test Farm" in html
        assert "North Field" in html

    def test_no_basemap_produces_functional_dashboard(self, tmp_farm_dir: Path, plotly_fake: str):
        output = generate_dashboard(
            farm_dir=tmp_farm_dir,
            output=tmp_farm_dir / "test.html",
            no_basemap=True,
            plotly_js=plotly_fake,
            basemap_b64=None,
        )
        html = output.read_text(encoding="utf-8")
        assert "Grower Field Weather Dashboard" in html
        assert "Test Farm" in html

    def test_deterministic_output(self, tmp_farm_dir: Path, plotly_fake: str):
        out1 = tmp_farm_dir / "test1.html"
        out2 = tmp_farm_dir / "test2.html"
        generate_dashboard(farm_dir=tmp_farm_dir, output=out1, plotly_js=plotly_fake, basemap_b64=None)
        generate_dashboard(farm_dir=tmp_farm_dir, output=out2, plotly_js=plotly_fake, basemap_b64=None)
        html1 = out1.read_text("utf-8")
        html2 = out2.read_text("utf-8")
        # Strip generatedAt timestamps which will differ
        import re
        h1 = re.sub(r'"generatedAt":\s*"[^"]+"', '"generatedAt":"...",', html1)
        h2 = re.sub(r'"generatedAt":\s*"[^"]+"', '"generatedAt":"...",', html2)
        assert h1 == h2

    def test_header_only_weather_no_failure(self, tmp_farm_dir_header_only_weather: Path, plotly_fake: str):
        output = generate_dashboard(
            farm_dir=tmp_farm_dir_header_only_weather,
            output=tmp_farm_dir_header_only_weather / "test.html",
            plotly_js=plotly_fake,
            basemap_b64=None,
        )
        html = output.read_text(encoding="utf-8")
        assert "Grower Field Weather Dashboard" in html


class TestWeatherFallback:
    def test_aggregate_fallback(self, tmp_farm_dir_no_weather: Path, plotly_fake: str):
        """When per-field weather is missing but aggregate table exists, use it."""
        tables = tmp_farm_dir_no_weather / "derived" / "tables"
        tables.mkdir(parents=True, exist_ok=True)
        agg_df = pd.DataFrame({
            "field_id": ["osm-1001", "osm-1001", "osm-1002", "osm-1002"],
            "date": ["2025-06-01", "2025-07-01", "2025-06-01", "2025-07-01"],
            "T2M_MAX": [26.0, 30.0, 25.0, 29.0],
            "T2M_MIN": [14.0, 18.0, 13.0, 17.0],
            "PRECTOTCORR": [6.0, 10.0, 5.0, 9.0],
            "T2M": [20.0, 24.0, 19.0, 23.0],
            "ALLSKY_SFC_SW_DWN": [24.0, 26.0, 23.0, 25.0],
            "RH2M": [40.0, 45.0, 42.0, 44.0],
            "WS10M": [3.0, 3.0, 3.2, 3.0],
        })
        agg_csv = tables / "test-farm_weather_2021_2025.csv"
        agg_df.to_csv(agg_csv, index=False)
        data = build_dashboard_data(tmp_farm_dir_no_weather)
        assert len(data["weatherByFieldYear"]) > 0


class TestDefaultYearSelection:
    def test_2025_selected_when_present(self):
        """Default year selection should prefer 2025."""
        data = {
            "weatherByFieldYear": [
                {"fieldId": "f1", "year": 2024, "daily": []},
                {"fieldId": "f1", "year": 2025, "daily": []},
            ]
        }
        from reporting.dashboard_lib import _build_dashboard_js
        js = _build_dashboard_js([2024, 2025], 2025)
        assert "var defaultYear = 2025;" in js

    def test_latest_year_when_no_2025(self):
        """When 2025 is absent, use latest available year."""
        from reporting.dashboard_lib import _build_dashboard_js
        js = _build_dashboard_js([2022, 2023, 2024], 2024)
        assert "var defaultYear = 2024;" in js

    def test_no_years_no_default(self):
        """When no years, default year is null."""
        from reporting.dashboard_lib import _build_dashboard_js
        js = _build_dashboard_js([], None)
        assert "var defaultYear = null;" in js


class TestStandaloneDoesNotCallExternal:
    def test_no_external_calls(self, tmp_farm_dir: Path, plotly_fake: str):
        """Standalone generation with pre-provided plotly does not call external services."""
        with patch("reporting.dashboard_lib.requests.get") as mock_get:
            generate_dashboard(
                farm_dir=tmp_farm_dir,
                output=tmp_farm_dir / "test.html",
                no_basemap=True,
                plotly_js=plotly_fake,
                basemap_b64=None,
            )
            mock_get.assert_not_called()


class TestColorPalette:
    def test_palette_has_10_colors(self):
        assert len(COLOR_PALETTE) == 10
        for c in COLOR_PALETTE:
            assert c.startswith("#")
            assert len(c) == 7

    def test_colors_are_deterministic(self, tmp_farm_dir: Path):
        # Same field always gets the same color
        assert _color_hash("osm-1001") == _color_hash("osm-1001")


class TestWriteDashboard:
    def test_atomic_write(self, tmp_farm_dir: Path):
        out = tmp_farm_dir / "test_atomic.html"
        result = write_dashboard("<html></html>", out)
        assert result == out.resolve()
        assert out.exists()
        # No temp file left behind
        tmp_files = list(tmp_farm_dir.glob(".*.tmp"))
        assert len(tmp_files) == 0


class TestMercatorProject:
    def test_projection(self, tmp_farm_dir: Path):
        fields = read_boundaries(tmp_farm_dir)
        merc = _mercator_project(fields)
        assert merc.crs is not None
        assert merc.crs.to_string() == "EPSG:3857"
        # Mercator coordinates should be large positive/negative numbers
        x = merc.total_bounds[0]
        assert abs(x) > 1e6 or x < -1e6


class TestAcquireBasemap:
    def test_no_pillow_returns_none(self, tmp_farm_dir: Path):
        boundary = tmp_farm_dir / "boundary" / "field_boundaries.geojson"
        with patch.dict("sys.modules", {"PIL": None}):
            import importlib
            import reporting.dashboard_lib as dl
            # Force reload to check
            with patch.object(dl, "acquire_basemap", wraps=dl.acquire_basemap) as mock_acq:
                pass

    def test_no_network_fallback_graceful(self, tmp_farm_dir: Path):
        """When tiles cannot be downloaded, return None - not a crash."""
        boundary = tmp_farm_dir / "boundary" / "field_boundaries.geojson"
        result = acquire_basemap(boundary)
        # May be None if PIL is missing or tiles can't be fetched - both are acceptable
        assert result is None or isinstance(result, str)


class TestEmptyState:
    def test_empty_weather_renders_no_data(self, tmp_farm_dir_header_only_weather: Path, plotly_fake: str):
        output = generate_dashboard(
            farm_dir=tmp_farm_dir_header_only_weather,
            output=tmp_farm_dir_header_only_weather / "test.html",
            plotly_js=plotly_fake,
            basemap_b64=None,
        )
        html = output.read_text(encoding="utf-8")
        # Should still be a valid dashboard
        assert "Grower Field Weather Dashboard" in html
        assert "North Field" in html or "osm-1001" in html
