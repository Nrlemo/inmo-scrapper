from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(os.environ.get("INMO_CONFIG", "config/profiles.yaml"))
DEFAULT_DB = os.environ.get("INMO_DB", "data/inmo.sqlite")
INACTIVE_AFTER = int(os.environ.get("INMO_INACTIVE_AFTER", "3"))  # consultas completas sin ver el aviso


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not cfg.get("profiles"):
        raise ValueError(f"{path}: falta la lista 'profiles'")
    cfg.setdefault("politeness", {})
    return cfg
