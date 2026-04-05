from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def _preset_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    d = Path(base) / "presets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_presets() -> list[str]:
    d = _preset_dir()
    return sorted(p.stem for p in d.glob("*.json"))


def save_preset(name: str, state: dict) -> Path:
    path = _preset_dir() / f"{name}.json"
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    return path


def load_preset(name: str) -> dict:
    path = _preset_dir() / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def delete_preset(name: str) -> None:
    path = _preset_dir() / f"{name}.json"
    if path.exists():
        path.unlink()
