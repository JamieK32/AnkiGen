from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_APP_SETTINGS: dict[str, Any] = {
    "api_key": "",
    "base_url": "",
    "model": "",
    "anki_url": "",
    "deck_name": "",
    "model_name": "",
    "tts_voice": "",
    "metadata_batch_size": 20,
    "tts_max_workers": 5,
    "anki_upload_workers": 8,
}


def _as_str(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _as_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        val = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, val))


def load_app_settings(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    if not path.exists():
        return sanitize_app_settings(merged, defaults)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return sanitize_app_settings(merged, defaults)
    if not isinstance(raw, dict):
        return sanitize_app_settings(merged, defaults)
    merged.update(raw)
    return sanitize_app_settings(merged, defaults)


def save_app_settings(path: Path, settings: dict[str, Any], defaults: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = sanitize_app_settings(settings, defaults)
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")


def sanitize_app_settings(settings: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_key": _as_str(settings.get("api_key"), str(defaults.get("api_key", ""))),
        "base_url": _as_str(settings.get("base_url"), str(defaults.get("base_url", ""))),
        "model": _as_str(settings.get("model"), str(defaults.get("model", ""))),
        "anki_url": _as_str(settings.get("anki_url"), str(defaults.get("anki_url", ""))),
        "deck_name": _as_str(settings.get("deck_name"), str(defaults.get("deck_name", ""))),
        "model_name": _as_str(settings.get("model_name"), str(defaults.get("model_name", ""))),
        "tts_voice": _as_str(settings.get("tts_voice"), str(defaults.get("tts_voice", ""))),
        "metadata_batch_size": _as_int(
            settings.get("metadata_batch_size"),
            int(defaults.get("metadata_batch_size", 20)),
            minimum=1,
            maximum=200,
        ),
        "tts_max_workers": _as_int(
            settings.get("tts_max_workers"),
            int(defaults.get("tts_max_workers", 5)),
            minimum=1,
            maximum=32,
        ),
        "anki_upload_workers": _as_int(
            settings.get("anki_upload_workers"),
            int(defaults.get("anki_upload_workers", 8)),
            minimum=1,
            maximum=32,
        ),
    }
