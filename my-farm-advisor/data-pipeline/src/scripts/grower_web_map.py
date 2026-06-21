#!/usr/bin/env python3
"""Generate interactive grower web maps for all pipeline growers.

Lightweight entrypoint — delegates to the subskill script at
grower_web_map/generate_grower_map.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(_SCRIPT.parent))

from grower_web_map.generate_grower_map import main

if __name__ == "__main__":
    main()
