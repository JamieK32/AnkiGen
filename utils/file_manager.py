from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable


def ensure_project_dirs(project_root: Path) -> tuple[Path, Path, Path]:
    data_dir = project_root / "data"
    audio_dir = project_root / "audio"
    json_path = data_dir / "words.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    if not json_path.exists():
        json_path.write_text("[]", encoding="utf-8")
    return data_dir, audio_dir, json_path


def load_words(json_path: Path) -> list[dict[str, str]]:
    if not json_path.exists():
        return []
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid words.json format: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("words.json must be a JSON array.")
    items: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word", "")).strip().lower()
        if not word:
            continue
        items.append(
            {
                "word": word,
                "phonetic": str(item.get("phonetic", "")).strip(),
                "part_of_speech": str(item.get("part_of_speech", "")).strip(),
                "translation": str(item.get("translation", "")).strip(),
                "example": str(item.get("example", "")).strip(),
                "analysis": str(item.get("analysis", "")).strip(),
                "imported_at": str(item.get("imported_at", "")).strip(),
                "source_text": str(item.get("source_text", "")),
                "generation_error": str(item.get("generation_error", "")),
                "generation_mode": str(item.get("generation_mode", "complete")),
            }
        )
    return items


def repair_word_data(
    word_data: dict[str, Any],
    metadata_provider: Callable[[dict[str, str]], dict[str, str]] | None = None,
) -> dict[str, str]:
    """Normalize a word item and optionally auto-recover missing metadata."""
    repaired = {
        "word": str(word_data.get("word", "")).strip().lower(),
        "phonetic": str(word_data.get("phonetic", "")).strip(),
        "part_of_speech": str(word_data.get("part_of_speech", "")).strip(),
        "translation": str(word_data.get("translation", "")).strip(),
        "example": str(word_data.get("example", "")).strip(),
        "analysis": str(word_data.get("analysis", "")).strip(),
        "imported_at": str(word_data.get("imported_at", "")).strip(),
        "source_text": str(word_data.get("source_text", "")),
        "generation_error": str(word_data.get("generation_error", "")),
        "generation_mode": str(word_data.get("generation_mode", "complete")),
    }
    if not repaired["word"]:
        raise ValueError("Cannot repair word data: missing word.")
    if metadata_provider is not None:
        repaired = metadata_provider(repaired)
    return repaired


def save_words(json_path: Path, words: list[dict[str, str]]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(words, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(json_path)
    if json_path.name == 'words.json':
        update_english_library(json_path, words)


def update_english_library(json_path: Path, words: list[dict[str, str]]) -> Path:
    output = json_path.parent.parent / 'exports' / 'vocabulary-english.txt'
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = list(dict.fromkeys(item['word'].strip() for item in words if item.get('word', '').strip()))
    temporary = output.with_suffix('.txt.tmp')
    temporary.write_text(''.join(word + '\n' for word in entries), encoding='utf-8')
    temporary.replace(output)
    return output


def parse_import_text(text: str, mode: str = "comma") -> list[str]:
    if mode == "comma":
        return parse_words_text(text)
    if mode != "lines":
        raise ValueError("Unknown import mode")
    return list(dict.fromkeys(
        re.sub(r"\s+", " ", line).strip().lower()
        for line in text.splitlines() if line.strip()
    ))


def parse_words_text(input_text: str) -> list[str]:
    """Parse comma-separated words/phrases into unique normalized entries."""
    raw = (input_text or "").replace("，", ",")
    # Quoted CSV entries preserve commas inside a collocation. Keep whitespace
    # normalization and ordinary comma-separated input backward compatible.
    try:
        tokens = next(csv.reader([raw.replace("\r", " ").replace("\n", " ")], skipinitialspace=True, strict=True))
    except csv.Error as exc:
        raise ValueError('逗号格式无效，请检查双引号是否配对，或选择每行一条。') from exc
    output: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        value = re.sub(r"\s+", " ", token).strip().lower()
        if value not in seen:
            if value:
                output.append(value)
                seen.add(value)
    return output


def parse_words_batch(input_text: str) -> list[str]:
    """Parse comma-separated batch input into unique normalized words/phrases."""
    return parse_words_text(input_text)


def chunked(items: list[str], chunk_size: int) -> Iterable[list[str]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive.")
    for i in range(0, len(items), chunk_size):
        yield items[i : i + chunk_size]


def sanitize_filename(word: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", word.lower()).strip("_")
    return cleaned or "word"


def word_audio_path(audio_dir: Path, word: str) -> Path:
    return audio_dir / f"{sanitize_filename(word)}.mp3"


def sentence_audio_path(audio_dir: Path, word: str) -> Path:
    return audio_dir / f"{sanitize_filename(word)}_sentence.mp3"


def check_audio_exists(audio_dir: Path, word: str) -> tuple[bool, bool]:
    def usable(path: Path) -> bool:
        return path.is_file() and path.stat().st_size > 0

    return usable(word_audio_path(audio_dir, word)), usable(sentence_audio_path(audio_dir, word))


def delete_word_assets(audio_dir: Path, word: str) -> None:
    for path in (word_audio_path(audio_dir, word), sentence_audio_path(audio_dir, word)):
        if path.exists():
            path.unlink()


def rename_word_assets(audio_dir: Path, old_word: str, new_word: str) -> None:
    old_word_audio = word_audio_path(audio_dir, old_word)
    old_sentence_audio = sentence_audio_path(audio_dir, old_word)
    new_word_audio = word_audio_path(audio_dir, new_word)
    new_sentence_audio = sentence_audio_path(audio_dir, new_word)

    if old_word_audio.exists():
        old_word_audio.rename(new_word_audio)
    if old_sentence_audio.exists():
        old_sentence_audio.rename(new_sentence_audio)


def highlight_target_word(example: str, word: str) -> str:
    pattern = re.compile(rf"\b({re.escape(word)})\b", flags=re.IGNORECASE)
    return pattern.sub(lambda m: f"<b>{m.group(0)}</b>", example)


def extract_json_array(text: str) -> list[dict[str, Any]]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("AI response does not contain a valid JSON array.")
    candidate = raw[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # Some models emit literal newlines or tabs inside JSON strings.
        # Normalize only string-internal control characters, then parse again.
        sanitized = _escape_json_string_control_chars(candidate)
        if sanitized == candidate:
            raise
        try:
            parsed = json.loads(sanitized)
        except json.JSONDecodeError:
            raise exc
    if not isinstance(parsed, list):
        raise ValueError("AI response JSON is not a list.")
    output: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            output.append(item)
    if not output:
        raise ValueError("AI response JSON list is empty or invalid.")
    return output


def _escape_json_string_control_chars(raw: str) -> str:
    chars: list[str] = []
    in_string = False
    escaping = False

    for ch in raw:
        if escaping:
            chars.append(ch)
            escaping = False
            continue

        if ch == "\\":
            chars.append(ch)
            if in_string:
                escaping = True
            continue

        if ch == '"':
            chars.append(ch)
            in_string = not in_string
            continue

        if in_string:
            if ch == "\n":
                chars.append("\\n")
                continue
            if ch == "\r":
                chars.append("\\r")
                continue
            if ch == "\t":
                chars.append("\\t")
                continue
            if ord(ch) < 32:
                chars.append(f"\\u{ord(ch):04x}")
                continue

        chars.append(ch)

    return "".join(chars)
