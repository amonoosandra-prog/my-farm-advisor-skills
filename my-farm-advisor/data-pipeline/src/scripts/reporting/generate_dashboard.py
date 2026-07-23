#!/usr/bin/env python3
"""Pipeline step: generate Grower Field Weather Dashboard.

Called by run_farm_pipeline.py via subprocess.  Uses environment variables
for parameter passing (AG_GROWER_SLUG, AG_FARM_SLUG, AG_NO_BASEMAP).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR / "lib"))
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "reporting"))

from runtime_paths import resolve_runtime_paths
from reporting.dashboard_lib import generate_dashboard, resolve_farm_dir, _validate_farm_layout


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def main() -> None:
    _RUNTIME_PATHS = resolve_runtime_paths()
    _RUNTIME_BASE = _RUNTIME_PATHS.runtime_base

    print("=" * 60)
    print("  Grower Field Weather Dashboard")
    print("=" * 60)

    grower_slug = _env("AG_GROWER_SLUG", "default-grower")
    farm_slug = _env("AG_FARM_SLUG", "default-farm")
    no_basemap = _env("AG_NO_BASEMAP", "0") == "1"

    farm_dir = _RUNTIME_BASE / "growers" / grower_slug / "farms" / farm_slug
    if not farm_dir.is_dir():
        print(f"ERROR: Farm directory not found: {farm_dir}")
        sys.exit(1)

    try:
        _validate_farm_layout(farm_dir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    generate_dashboard(
        farm_dir=farm_dir,
        output=None,
        no_basemap=no_basemap,
    )


if __name__ == "__main__":
    main()
