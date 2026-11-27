"""Disk cache for profiled schemas (columns, keys, sample rows, allowed values, LLM descriptions).

Reconnecting to a database you've already loaded skips all of that work. Entries for SQLite files are
keyed on path + size + modified time, so editing the file invalidates them; MySQL entries expire after
CACHE_TTL_HOURS because a live server can change at any time.
"""
import hashlib
import json
import os
import time
from dataclasses import asdict

from app import config
from app.core.db import Column, Table

CACHE_DIR = os.path.join(config.DATA_DIR, "cache")


def _path(key: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.md5(key.encode()).hexdigest() + ".json")


def load(key: str) -> dict[str, Table] | None:
    path = _path(key)
    if not os.path.exists(path):
        return None
    if key.startswith("mysql|") and time.time() - os.path.getmtime(path) > config.CACHE_TTL_HOURS * 3600:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return {
            name: Table(
                name=t["name"],
                columns=[Column(**c) for c in t["columns"]],
                foreign_keys=[tuple(fk) for fk in t["foreign_keys"]],
                sample_row=t["sample_row"],
                row_count=t["row_count"],
                description=t["description"],
                value_hints=t["value_hints"],
            )
            for name, t in raw["tables"].items()
        }
    except Exception:
        return None  # corrupt / old format: just rebuild


def save(key: str, tables: dict[str, Table]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_path(key), "w", encoding="utf-8") as f:
        json.dump({"key": key, "saved_at": time.time(),
                   "tables": {n: asdict(t) for n, t in tables.items()}}, f, default=str)


def clear(key: str) -> None:
    try:
        os.remove(_path(key))
    except FileNotFoundError:
        pass
