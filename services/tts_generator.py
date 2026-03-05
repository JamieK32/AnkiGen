from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import edge_tts

from utils.file_manager import check_audio_exists, sanitize_filename


class TTSGenerationError(Exception):
    pass


class TTSGenerator:
    def __init__(self, voice: str = "en-US-AriaNeural", max_workers: int = 5) -> None:
        self.voice = voice
        self.max_workers = max_workers

    async def _generate_word_audio(self, word: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{sanitize_filename(word)}.mp3"
        try:
            communicate = edge_tts.Communicate(text=word, voice=self.voice)
            await communicate.save(str(file_path))
        except Exception as exc:
            raise TTSGenerationError(f"Failed to generate word audio for '{word}': {exc}") from exc
        return file_path

    async def _generate_sentence_audio(self, word: str, sentence: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{sanitize_filename(word)}_sentence.mp3"
        try:
            communicate = edge_tts.Communicate(text=sentence, voice=self.voice)
            await communicate.save(str(file_path))
        except Exception as exc:
            raise TTSGenerationError(f"Failed to generate sentence audio for '{word}': {exc}") from exc
        return file_path

    def _generate_word_audio_sync(self, word: str, output_dir: Path) -> Path:
        return asyncio.run(self._generate_word_audio(word=word, output_dir=output_dir))

    def _generate_sentence_audio_sync(self, word: str, sentence: str, output_dir: Path) -> Path:
        return asyncio.run(self._generate_sentence_audio(word=word, sentence=sentence, output_dir=output_dir))

    def generate_audio(self, entry: dict[str, str], output_dir: Path) -> dict[str, Path | None]:
        """Generate word/sentence audio in parallel for a single entry."""
        word = entry["word"]
        sentence = entry.get("example", "").strip()
        word_audio: Path | None = None
        sentence_audio: Path | None = None

        futures: dict[object, str] = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures[executor.submit(self._generate_word_audio_sync, word, output_dir)] = "word_audio"
            if sentence:
                futures[executor.submit(self._generate_sentence_audio_sync, word, sentence, output_dir)] = "sentence_audio"

            errors: list[str] = []
            for future in as_completed(futures):
                kind = futures[future]
                try:
                    path = future.result()
                    if kind == "word_audio":
                        word_audio = path
                    else:
                        sentence_audio = path
                except Exception as exc:
                    errors.append(str(exc))

        if errors:
            raise TTSGenerationError("\n".join(errors))
        return {"word_audio": word_audio, "sentence_audio": sentence_audio}

    def generate_audio_batch(
        self,
        entries: list[dict[str, str]],
        output_dir: Path,
        max_workers: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        workers = max_workers or self.max_workers
        results: dict[str, dict[str, Path | None]] = {}
        failures: list[str] = []

        def worker(entry: dict[str, str]) -> tuple[str, dict[str, Path | None] | None, str | None]:
            word = entry["word"]
            try:
                result = self.generate_audio(entry=entry, output_dir=output_dir)
                return word, result, None
            except TTSGenerationError as exc:
                return word, None, str(exc)
            except Exception as exc:
                return word, None, f"Unexpected error: {exc}"

        if log_callback is not None:
            log_callback(f"Generating audio for {len(entries)} words with {workers} workers...")

        completed = 0
        total = len(entries)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(worker, entry) for entry in entries]
            for future in as_completed(futures):
                word, result, error = future.result()
                if error is not None:
                    failures.append(f"{word}: {error}")
                    if log_callback is not None:
                        log_callback(f"Audio failed for '{word}': {error}")
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(int(completed * 100 / max(1, total)))
                    continue
                if result is not None:
                    results[word] = result
                    if log_callback is not None:
                        log_callback(f"Audio generated for '{word}'")
                completed += 1
                if progress_callback is not None:
                    progress_callback(int(completed * 100 / max(1, total)))
        if log_callback is not None:
            log_callback(f"Audio generation finished. success={len(results)} failed={len(failures)}")
        return {"results": results, "errors": failures}

    def generate_audio_for_entries(self, entries: list[dict[str, str]], output_dir: Path) -> dict[str, dict[str, Path | None]]:
        batch_result = self.generate_audio_batch(entries=entries, output_dir=output_dir)
        results = batch_result.get("results", {})
        errors = batch_result.get("errors", [])
        if isinstance(errors, list) and errors:
            raise TTSGenerationError("Some audio files failed:\n" + "\n".join(errors))
        return results if isinstance(results, dict) else {}

    def generate_missing_for_entry(self, entry: dict[str, str], output_dir: Path) -> dict[str, Path | None]:
        """Backward compatible helper: keep existing audio and generate only missing files."""
        word = entry["word"]
        has_word, has_sentence = check_audio_exists(output_dir, word)
        if has_word and (has_sentence or not entry.get("example", "").strip()):
            return {"word_audio": None, "sentence_audio": None}
        return self.generate_audio(entry=entry, output_dir=output_dir)

    def generate_for_entry(self, entry: dict[str, str], output_dir: Path, generate_word: bool = True, generate_sentence: bool = True) -> dict[str, Path | None]:
        """Backward compatible wrapper."""
        if generate_word and generate_sentence:
            return self.generate_audio(entry=entry, output_dir=output_dir)
        # Keep optional mode for older callers; still avoid dependency on phonetic metadata.
        word = entry["word"]
        sentence = entry.get("example", "").strip()
        word_audio: Path | None = None
        sentence_audio: Path | None = None
        if generate_word:
            word_audio = asyncio.run(self._generate_word_audio(word=word, output_dir=output_dir))
        if generate_sentence and sentence:
            sentence_audio = asyncio.run(self._generate_sentence_audio(word=word, sentence=sentence, output_dir=output_dir))
        return {"word_audio": word_audio, "sentence_audio": sentence_audio}

    def generate_for_entries(self, entries: list[dict[str, str]], output_dir: Path) -> dict[str, dict[str, Path | None]]:
        """Backward compatible wrapper."""
        return self.generate_audio_for_entries(entries=entries, output_dir=output_dir)
