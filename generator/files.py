"""Paths and JSON writing shared by the generator, ground truth, and labels.

Every data file goes through dump_json so output is byte-identical across runs:
indent=2, ensure_ascii=False, keys in insertion order, Unix newlines, and a
trailing newline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
PROFILES = "profiles"
TRUTH = "truth"
LABELS = "labels"
SPLITS_FILE = "splits.json"
GROUND_TRUTH_FILE = "ground_truth.json"


def household_ids(count: int = 100) -> list[str]:
    return [f"H{n:03d}" for n in range(1, count + 1)]


def dump_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, obj: Any) -> None:
    write_text(path, dump_json(obj))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_truths(data_dir: Path = DATA_DIR) -> dict[str, dict[str, Any]]:
    """Every household truth file in data/truth/, keyed by household ID, in ID order."""
    paths = sorted((data_dir / TRUTH).glob("H[0-9][0-9][0-9].json"))
    return {p.stem: read_json(p) for p in paths}
