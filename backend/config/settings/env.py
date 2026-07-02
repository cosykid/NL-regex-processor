"""Shared helpers used across the settings package.

``BASE_DIR`` must resolve to the same ``backend/`` directory that the old
monolithic ``config/settings.py`` resolved to. That file lived at
``backend/config/settings.py``, so ``Path(__file__).resolve().parent.parent``
walked up two levels (``config/`` -> ``backend/``). This module lives one
level deeper, at ``backend/config/settings/env.py``, so it walks up three
levels (``settings/`` -> ``config/`` -> ``backend/``) to land on the same
directory.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}
