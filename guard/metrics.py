"""JSONL metrics compatible with repo-hygiene ollama_metrics pattern."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def metrics_path() -> Path:
    raw = os.getenv("GUARD_METRICS_PATH", "/logs/metrics.jsonl")
    return Path(raw)


def log_metric(entry: dict[str, Any], *, path: str | None = None) -> None:
    p = Path(path) if path else metrics_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    entry.setdefault("timestamp", int(time.time() * 1000))
    entry.setdefault("source", "ollama-guard")
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
