from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATEGORY_FILE = Path(__file__).with_name("youtube_categories.json")


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


@lru_cache(maxsize=1)
def category_map() -> dict[str, str]:
    values = json.loads(CATEGORY_FILE.read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise ValueError("YouTube category mapping must be a JSON object")
    return {str(name): str(identifier) for name, identifier in values.items()}


def resolve_category(value: str | None) -> str | None:
    """Resolve a numeric ID or case-insensitive category name to an API ID."""
    if value is None or not value.strip():
        return None
    category = value.strip()
    if category.isdigit():
        return category

    normalized = _normalize(category)
    matches = {_normalize(name): identifier for name, identifier in category_map().items()}
    if normalized not in matches:
        available = ", ".join(category_map())
        raise ValueError(f"Unknown YouTube category '{value}'. Available categories: {available}")
    return matches[normalized]
