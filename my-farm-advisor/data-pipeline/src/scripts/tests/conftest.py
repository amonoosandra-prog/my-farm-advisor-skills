from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def tmp_farm_dir() -> Generator[Path, None, None]:
    """Build a complete test farm directory from fixtures and yield the path."""
    tmp = Path(tempfile.mkdtemp())
    farm_dir = tmp / "growers" / "test-grower" / "farms" / "test-farm"
    farm_dir.mkdir(parents=True, exist_ok=True)

    # boundary
    (farm_dir / "boundary").mkdir()
    shutil.copy2(FIXTURES_DIR / "sample_boundary.geojson", farm_dir / "boundary" / "field_boundaries.geojson")

    # farm.json
    shutil.copy2(FIXTURES_DIR / "farm.json", farm_dir / "farm.json")

    # fields
    for fid in ("field_1", "field_2"):
        src = FIXTURES_DIR / fid
        dst = farm_dir / "fields" / fid
        shutil.copytree(src, dst)

    yield farm_dir

    shutil.rmtree(tmp)


@pytest.fixture
def tmp_farm_dir_no_field2_json(tmp_farm_dir: Path) -> Path:
    """Remove field_2's field.json to test fallback behavior."""
    fj = tmp_farm_dir / "fields" / "field_2" / "field.json"
    if fj.exists():
        fj.unlink()
    return tmp_farm_dir


@pytest.fixture
def tmp_farm_dir_no_weather(tmp_farm_dir: Path) -> Path:
    """Clear weather data for both fields to test empty/no-data scenario."""
    for fid in ("field_1", "field_2"):
        w = tmp_farm_dir / "fields" / fid / "weather" / "daily_weather.csv"
        if w.exists():
            w.unlink()
    return tmp_farm_dir


@pytest.fixture
def tmp_farm_dir_header_only_weather(tmp_farm_dir: Path) -> Path:
    """Replace field_1 weather with header-only CSV."""
    w = tmp_farm_dir / "fields" / "field_1" / "weather" / "daily_weather.csv"
    w.write_text("field_id,date,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,WS10M\n")
    return tmp_farm_dir


@pytest.fixture
def dual_farm_root() -> Generator[Path, None, None]:
    """Create two valid farm dirs under a single growers root for discovery tests."""
    tmp = Path(tempfile.mkdtemp())
    growers_dir = tmp / "growers"

    for gslug, fslug in [("grower-a", "farm-x"), ("grower-b", "farm-y")]:
        fd = growers_dir / gslug / "farms" / fslug
        fd.mkdir(parents=True, exist_ok=True)
        (fd / "boundary").mkdir()
        shutil.copy2(FIXTURES_DIR / "sample_boundary.geojson", fd / "boundary" / "field_boundaries.geojson")
        (fd / "fields").mkdir()
        for fid in ("field_1", "field_2"):
            shutil.copytree(FIXTURES_DIR / fid, fd / "fields" / fid)

    yield growers_dir

    shutil.rmtree(tmp)


@pytest.fixture
def plotly_fake() -> str:
    return "/* Fake Plotly bundle for testing */ var Plotly = { react: function() {}, relayout: function() {} };"
