"""Read a box's result file as HOSTILE data (spec §3b, INV4): regular file only
(no symlink out of the results dir), size-capped, JSON, and a list. Never import
or execute what a box wrote."""
from __future__ import annotations

import json
from pathlib import Path


class ResultError(RuntimeError):
    """The result file is missing, a symlink, too big, or not a JSON list."""


def read_result_json(out_dir: Path, name: str = "results.json", *,
                     max_bytes: int = 8_000_000) -> list:
    path = Path(out_dir) / name
    if path.is_symlink() or not path.is_file():
        raise ResultError(f"{name} is missing or not a regular file")
    size = path.stat().st_size
    if size > max_bytes:
        raise ResultError(f"{name} is {size} bytes (> {max_bytes})")
    try:
        data = json.loads(path.read_text())
    except (ValueError, UnicodeDecodeError) as e:
        raise ResultError(f"{name} is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise ResultError(f"{name} is not a JSON list")
    return data
