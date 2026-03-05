from __future__ import annotations

from typing import Any, Callable

from openai import APIStatusError, AuthenticationError, OpenAI

from utils.file_manager import chunked, extract_json_array


DEFAULT_BASE_URL = "https://yunwu.ai/v1"
DEFAULT_MODEL = "gpt-5-mini"


class GPTGenerationError(Exception):
    pass


class GPTGenerator:
    AUTO_METADATA_FIELDS = ("phonetic", "part_of_speech", "example", "analysis")

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def generate_word_data(self, words: list[str]) -> list[dict[str, str]]:
        if not words:
            return []
        prompt = self._build_prompt(words)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a vocabulary assistant. Return strict JSON only. "
                            "No markdown. No extra text."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
        except AuthenticationError as exc:
            raise GPTGenerationError(
                "Authentication failed (401). Please check YUNWU_API_KEY / OPENAI_API_KEY."
            ) from exc
        except APIStatusError as exc:
            status = getattr(exc, "status_code", None)
            if status == 401:
                raise GPTGenerationError("Authentication failed (401). Invalid token or base_url.") from exc
            raise GPTGenerationError(f"GPT API request failed (status={status}): {exc}") from exc
        except Exception as exc:
            raise GPTGenerationError(f"Failed to call GPT API: {exc}") from exc

        try:
            content = response.choices[0].message.content or ""
            parsed = extract_json_array(content)
            return self._normalize_items(parsed, words)
        except Exception as exc:
            raise GPTGenerationError(f"Failed to parse GPT JSON: {exc}") from exc

    def generate_words_batch(
        self,
        words: list[str],
        batch_size: int = 20,
        progress_callback: Callable[[int], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """Generate metadata in batched API requests (default batch size: 20)."""
        normalized_words: list[str] = []
        seen: set[str] = set()
        for word in words:
            value = str(word).strip().lower()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized_words.append(value)

        generated: list[dict[str, str]] = []
        errors: list[str] = []
        total = len(normalized_words)
        processed = 0
        if log_callback is not None:
            log_callback(f"Generating metadata for {total} words (batch size={batch_size})...")
        for batch_words in chunked(normalized_words, batch_size):
            if log_callback is not None:
                log_callback(f"Metadata batch start: {', '.join(batch_words)}")
            try:
                batch_result = self.generate_word_data(batch_words)
            except Exception as exc:
                errors.extend([f"{word}: batch generation failed ({exc})" for word in batch_words])
                if log_callback is not None:
                    log_callback(f"Metadata batch failed: {exc}")
                processed += len(batch_words)
                if progress_callback is not None:
                    progress_callback(int(processed * 100 / max(1, total)))
                continue

            batch_map = {item["word"]: item for item in batch_result if item.get("word")}
            for word in batch_words:
                item = batch_map.get(word)
                if item is None:
                    errors.append(f"{word}: missing from batch response")
                    continue
                generated.append(item)
            processed += len(batch_words)
            if progress_callback is not None:
                progress_callback(int(processed * 100 / max(1, total)))

        if not generated:
            raise GPTGenerationError("Failed to generate metadata for all input words.")
        if log_callback is not None:
            log_callback(f"Metadata generation finished. success={len(generated)} failed={len(errors)}")
        return {"items": generated, "errors": errors, "batch_size": batch_size}

    @staticmethod
    def _build_prompt(words: list[str]) -> str:
        word_list = " ".join(words)
        return f"""Generate vocabulary learning data for the following English words.

Return STRICT JSON.

Fields required:
- word
- phonetic (IPA transcription)
- part_of_speech
- translation (Chinese meaning)
- example sentence
- analysis (Chinese explanation)

Rules:
1) Translation must be concise
2) Example sentence must include the word
3) Example must be natural English
4) Analysis must be short
5) Return valid JSON only

Example output:
[
  {{
    "word": "abandon",
    "phonetic": "/əˈbændən/",
    "part_of_speech": "verb",
    "translation": "放弃；遗弃",
    "example": "He decided to abandon the plan.",
    "analysis": "表示彻底停止或遗弃某事"
  }}
]

Words:
{word_list}
"""

    @staticmethod
    def _normalize_items(items: list[dict[str, Any]], requested_words: list[str]) -> list[dict[str, str]]:
        requested = set(requested_words)
        results: list[dict[str, str]] = []
        seen: set[str] = set()

        for item in items:
            word = str(item.get("word", "")).strip().lower()
            if not word or word not in requested or word in seen:
                continue

            example_value = item.get("example", item.get("example sentence", ""))
            pos_value = item.get("part_of_speech", item.get("part of speech", ""))
            result = {
                "word": word,
                "phonetic": str(item.get("phonetic", "")).strip(),
                "part_of_speech": str(pos_value).strip().lower(),
                "translation": str(item.get("translation", "")).strip(),
                "example": str(example_value).strip(),
                "analysis": str(item.get("analysis", "")).strip(),
            }
            if not all(result.values()):
                continue
            if word not in result["example"].lower():
                continue
            seen.add(word)
            results.append(result)

        if not results:
            raise ValueError("No valid word entries generated by AI.")
        return results

    def ensure_metadata(self, word_data: dict[str, str]) -> dict[str, str]:
        """Fill missing metadata fields using AI generation."""
        word = str(word_data.get("word", "")).strip().lower()
        if not word:
            raise GPTGenerationError("Cannot generate metadata: missing word.")

        updated = dict(word_data)
        missing = [field for field in self.AUTO_METADATA_FIELDS if not str(updated.get(field, "")).strip()]
        if not missing:
            return updated

        generated_items = self.generate_word_data([word])
        generated = generated_items[0]
        for field in missing:
            value = str(generated.get(field, "")).strip()
            if value:
                updated[field] = value

        # Keep translation recoverable as well, without making it mandatory.
        if not str(updated.get("translation", "")).strip():
            updated["translation"] = str(generated.get("translation", "")).strip()
        updated["word"] = word
        return updated

    def repair_word_data(self, word_data: dict[str, str]) -> dict[str, str]:
        """Normalize schema then auto-recover missing metadata from AI."""
        normalized = {
            "word": str(word_data.get("word", "")).strip().lower(),
            "phonetic": str(word_data.get("phonetic", "")).strip(),
            "part_of_speech": str(word_data.get("part_of_speech", "")).strip(),
            "translation": str(word_data.get("translation", "")).strip(),
            "example": str(word_data.get("example", "")).strip(),
            "analysis": str(word_data.get("analysis", "")).strip(),
        }
        if not normalized["word"]:
            raise GPTGenerationError("Cannot repair word data: empty word.")
        return self.ensure_metadata(normalized)
