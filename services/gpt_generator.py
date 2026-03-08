from __future__ import annotations

import re
from typing import Any, Callable

from openai import APIStatusError, AuthenticationError, OpenAI

from services.models import DEFAULT_AI_MODEL
from utils.file_manager import chunked, extract_json_array


DEFAULT_BASE_URL = "https://yunwu.ai/v1"
DEFAULT_MODEL = DEFAULT_AI_MODEL


class GPTGenerationError(Exception):
    pass


class GPTGenerator:
    AUTO_METADATA_FIELDS = ("phonetic", "part_of_speech", "example", "analysis")
    SINGLE_RETRY_ATTEMPTS = 3

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def generate_word_data(self, words: list[str]) -> list[dict[str, str]]:
        return self._generate_word_data_with_mode(words, strict_mode=False)

    def _generate_word_data_with_mode(
        self,
        words: list[str],
        strict_mode: bool = False,
    ) -> list[dict[str, str]]:
        if not words:
            return []
        prompt = self._build_prompt(words, strict_mode=strict_mode)
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

    def _retry_single_entry(
        self,
        word: str,
        log_callback: Callable[[str], None] | None = None,
    ) -> dict[str, str] | None:
        for attempt in range(1, self.SINGLE_RETRY_ATTEMPTS + 1):
            strict_mode = attempt >= 2
            mode_label = "strict" if strict_mode else "standard"
            try:
                single_result = self._generate_word_data_with_mode([word], strict_mode=strict_mode)
                if single_result:
                    if log_callback is not None:
                        log_callback(f"Recovered missing entry via {mode_label} retry {attempt}: {word}")
                    return single_result[0]
            except Exception as exc:
                if log_callback is not None:
                    log_callback(f"Single retry {attempt} ({mode_label}) failed for '{word}': {exc}")
        return None

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
                if log_callback is not None:
                    log_callback(f"Metadata batch failed: {exc}")
                for word in batch_words:
                    recovered = self._retry_single_entry(word, log_callback=log_callback)
                    if recovered is not None:
                        generated.append(recovered)
                        continue
                    errors.append(f"{word}: batch generation failed ({exc})")
                processed += len(batch_words)
                if progress_callback is not None:
                    progress_callback(int(processed * 100 / max(1, total)))
                continue

            batch_map: dict[str, dict[str, str]] = {}
            for item in batch_result:
                key = self._normalize_entry_key(item.get("word", ""))
                if key and key not in batch_map:
                    batch_map[key] = item
            for word in batch_words:
                item = batch_map.get(self._normalize_entry_key(word))
                if item is None:
                    recovered = self._retry_single_entry(word, log_callback=log_callback)
                    if recovered is not None:
                        generated.append(recovered)
                        continue
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
    def _build_prompt(words: list[str], strict_mode: bool = False) -> str:
        entries = "\n".join(f'- "{word}"' for word in words)
        if strict_mode:
            return f"""Generate vocabulary learning data for the following English entries (single words or phrases).

Return STRICT JSON ARRAY ONLY.

Hard requirements:
1) Output must be a valid JSON array only
2) No markdown, no explanations, no headings, no code fences
3) Return exactly one object for each input entry
4) The "word" field must exactly match the input entry text
5) Do not split phrases into separate words
6) Every object must include: word, phonetic, part_of_speech, translation, example, analysis
7) example must contain exactly two lines:
   first line = English sentence containing the exact word or phrase
   second line = Chinese translation of that sentence
8) translation must be concise Chinese
9) analysis must be short Chinese explanation

Example output:
[
  {{
    "word": "internal unrest",
    "phonetic": "/ɪnˈtɜːrnəl ʌnˈrest/",
    "part_of_speech": "noun phrase",
    "translation": "内部动荡",
    "example": "The country faced internal unrest after the election.\n选举后，这个国家陷入了内部动荡。",
    "analysis": "表示国家或组织内部出现不稳定和骚乱。"
  }}
]

Entries:
{entries}
"""
        return f"""Generate vocabulary learning data for the following English entries (single words or phrases).

Return STRICT JSON.

Fields required:
- word
- phonetic (IPA transcription)
- part_of_speech
- translation (Chinese meaning)
- example (format: "English sentence\nChinese translation")
- analysis (Chinese explanation)

Rules:
1) Translation must be concise
2) Example English sentence must include the word
3) Example must be natural English
4) Analysis must be short
5) Return valid JSON only
6) The "word" field must match one input entry exactly (keep phrase spaces, do not split into separate words)
7) Return one object for each input entry
8) The example field must contain exactly two lines: first line English, second line Chinese translation

Example output:
[
  {{
    "word": "abandon",
    "phonetic": "/əˈbændən/",
    "part_of_speech": "verb",
    "translation": "放弃；遗弃",
    "example": "He decided to abandon the plan.\n他决定放弃这个计划。",
    "analysis": "表示彻底停止或遗弃某事"
  }}
]

Entries:
{entries}
"""

    @staticmethod
    def _normalize_items(items: list[dict[str, Any]], requested_words: list[str]) -> list[dict[str, str]]:
        requested_lookup = {GPTGenerator._normalize_entry_key(word): word for word in requested_words}
        requested = set(requested_lookup.keys())
        results: list[dict[str, str]] = []
        seen: set[str] = set()

        for item in items:
            candidate_key = GPTGenerator._normalize_entry_key(item.get("word", ""))
            if not candidate_key or candidate_key not in requested or candidate_key in seen:
                continue

            example_value = item.get("example", item.get("example sentence", ""))
            pos_value = item.get("part_of_speech", item.get("part of speech", ""))
            matched_word = requested_lookup[candidate_key]
            result = {
                "word": matched_word,
                "phonetic": str(item.get("phonetic", "")).strip(),
                "part_of_speech": str(pos_value).strip().lower(),
                "translation": str(item.get("translation", "")).strip(),
                "example": str(example_value).strip(),
                "analysis": str(item.get("analysis", "")).strip(),
            }
            if not all(result.values()):
                continue
            if not GPTGenerator._example_contains_entry(result["example"], matched_word):
                continue
            seen.add(candidate_key)
            results.append(result)

        if not results:
            raise ValueError("No valid word entries generated by AI.")
        return results

    @staticmethod
    def _normalize_entry_key(value: Any) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip().lower())
        return text.strip("\"'`")

    @staticmethod
    def _example_contains_entry(example: str, entry: str) -> bool:
        english_line = GPTGenerator._extract_example_english(example)
        normalized_example = re.sub(r"\s+", " ", english_line).strip()
        normalized_entry = re.sub(r"\s+", " ", entry or "").strip()
        if not normalized_example or not normalized_entry:
            return False
        if " " in normalized_entry:
            tokens = [re.escape(token) for token in normalized_entry.split(" ") if token]
            if not tokens:
                return False
            pattern = r"\b" + r"\s+".join(tokens) + r"\b"
            return re.search(pattern, normalized_example, flags=re.IGNORECASE) is not None

        candidate_forms = GPTGenerator._entry_match_forms(normalized_entry)
        pattern = r"\b(?:" + "|".join(re.escape(form) for form in candidate_forms) + r")\b"
        return re.search(pattern, normalized_example, flags=re.IGNORECASE) is not None

    @staticmethod
    def _extract_example_english(example: str) -> str:
        lines = [line.strip() for line in str(example or "").splitlines() if line.strip()]
        if not lines:
            return ""
        return lines[0]

    @staticmethod
    def _entry_match_forms(entry: str) -> list[str]:
        forms = {entry}
        if not entry:
            return []

        forms.add(f"{entry}'s")

        if entry.endswith("y") and len(entry) > 1 and entry[-2] not in "aeiou":
            stem = entry[:-1]
            forms.update({f"{stem}ies", f"{stem}ied"})
        elif entry.endswith("e"):
            stem = entry[:-1]
            forms.update({f"{entry}s", f"{entry}d", f"{stem}ing"})
        else:
            forms.update({f"{entry}s", f"{entry}es", f"{entry}ed", f"{entry}ing"})

        forms.update({f"{entry}er", f"{entry}est", f"{entry}ly"})
        return sorted(forms, key=len, reverse=True)

    def ensure_metadata(self, word_data: dict[str, str]) -> dict[str, str]:
        """Fill missing metadata fields using AI generation."""
        word = str(word_data.get("word", "")).strip().lower()
        if not word:
            raise GPTGenerationError("Cannot generate metadata: missing word.")

        updated = dict(word_data)
        missing = [field for field in self.AUTO_METADATA_FIELDS if not str(updated.get(field, "")).strip()]
        if not missing:
            return updated

        generated = self._retry_single_entry(word)
        if generated is None:
            generated_items = self._generate_word_data_with_mode([word], strict_mode=True)
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
            "imported_at": str(word_data.get("imported_at", "")).strip(),
        }
        if not normalized["word"]:
            raise GPTGenerationError("Cannot repair word data: empty word.")
        return self.ensure_metadata(normalized)
