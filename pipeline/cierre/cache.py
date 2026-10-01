"""Disk cache for responses that can never change (historical blocks, finished log ranges).

Only immutable answers go in here. Callers decide what is immutable, never the cache.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from cierre import CACHE_DIR


class DiskCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CACHE_DIR / "responses.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        self._db.commit()

    @staticmethod
    def key(*parts: Any) -> str:
        raw = json.dumps(parts, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def put(self, key: str, value: Any) -> None:
        if value is None:
            raise ValueError("refusing to cache None: a missing answer is not a fact")
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (key, json.dumps(value))
            )
            self._db.commit()
