"""Loader for the structured catalog knowledge base (spec Phase 6.1).

`data/catalog_manifest.json` is the machine-readable decode of what the
user's loaded IronCAD catalogs actually contain — profile sizes in stock,
which parameters are real vs. inert, which catalog entries fail
InsertElement(), and which tool/technique applies to each family. It exists
so this information lives as data a tool call can hand back verbatim,
instead of only as prose in project memory the LLM has to recall correctly
every session. See PRODUCTION_READINESS_PLAN.md Phase 6 and
scripts/verify_catalog_manifest.py (which live-checks it for drift).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_MANIFEST_PATH = Path(__file__).parent / "data" / "catalog_manifest.json"


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    """Parse and cache the manifest for the life of the process."""
    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_catalog(name: str) -> dict[str, Any] | None:
    """Case-insensitive lookup of one catalog's entry, or None if not present
    in the manifest (a real loaded IronCAD catalog may still exist but not
    yet be documented here — that's a gap to note, not an error)."""
    catalogs = load_manifest().get("catalogs", {})
    for key, value in catalogs.items():
        if key.lower() == name.lower():
            return value
    return None
