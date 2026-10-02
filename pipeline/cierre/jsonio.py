"""Read and write the JSON files under data/."""

from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path):
    """The parsed file, or None when it does not exist."""
    return load_json(path) if path.exists() else None


def dump_json(path: Path, obj) -> None:
    """Pretty printed, LF line endings on every platform, so a rerun does not show up in git."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
