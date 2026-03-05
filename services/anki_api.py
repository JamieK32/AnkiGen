from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests


class AnkiConnectError(Exception):
    pass


class AnkiAPI:
    REQUIRED_FIELDS = [
        "Word",
        "Phonetic",
        "PartOfSpeech",
        "Translation",
        "Example",
        "Analysis",
        "AudioWord",
        "AudioSentence",
    ]
    CARD_TEMPLATE_NAME = "Card 1"

    def __init__(self, base_url: str = "http://localhost:8765", timeout: int = 15) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def _invoke(self, action: str, params: dict[str, Any] | None = None) -> Any:
        payload = {"action": action, "version": 6, "params": params or {}}
        try:
            response = requests.post(self.base_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise AnkiConnectError(
                "Cannot connect to AnkiConnect. Ensure Anki is running and AnkiConnect is installed."
            ) from exc
        except ValueError as exc:
            raise AnkiConnectError(f"Invalid response from AnkiConnect: {exc}") from exc

        if data.get("error"):
            raise AnkiConnectError(f"AnkiConnect error on '{action}': {data['error']}")
        return data.get("result")

    def check_connection(self) -> None:
        self._invoke("version")

    def ensure_deck(self, deck_name: str) -> None:
        self._invoke("createDeck", {"deck": deck_name})

    def ensure_model(self, model_name: str) -> None:
        model_names = self._invoke("modelNames")
        if model_name not in model_names:
            self._invoke(
                "createModel",
                {
                    "modelName": model_name,
                    "inOrderFields": self.REQUIRED_FIELDS,
                    "css": (
                        ".card { font-family: Arial; font-size: 22px; text-align: left; "
                        "color: black; background-color: white; line-height: 1.6; }"
                    ),
                    "cardTemplates": [
                        {"Name": self.CARD_TEMPLATE_NAME, "Front": self._front_template(), "Back": self._back_template()}
                    ],
                },
            )
            return

        existing_fields = self._invoke("modelFieldNames", {"modelName": model_name})
        for field in self.REQUIRED_FIELDS:
            if field not in existing_fields:
                self._invoke("modelFieldAdd", {"modelName": model_name, "fieldName": field})
        self._try_update_template(model_name)

    def _try_update_template(self, model_name: str) -> None:
        try:
            self._invoke(
                "updateModelTemplates",
                {
                    "model": {
                        "name": model_name,
                        "templates": {
                            self.CARD_TEMPLATE_NAME: {
                                "Front": self._front_template(),
                                "Back": self._back_template(),
                            }
                        },
                    }
                },
            )
        except AnkiConnectError:
            return

    @staticmethod
    def _front_template() -> str:
        return "{{Word}}<br>{{Phonetic}}<br>{{PartOfSpeech}}<br>{{AudioWord}}<br>{{AudioSentence}}"

    @staticmethod
    def _back_template() -> str:
        return "{{FrontSide}}<hr id=answer>{{Translation}}<br><br>{{Example}}<br>{{Analysis}}"

    def find_notes(self, deck_name: str, word: str) -> list[int]:
        query = f'deck:"{deck_name}" Word:"{word}"'
        result = self.find_notes_by_query(query)
        return result if isinstance(result, list) else []

    def find_notes_by_query(self, query: str) -> list[int]:
        result = self._invoke("findNotes", {"query": query})
        return result if isinstance(result, list) else []

    def note_exists(self, deck_name: str, word: str) -> bool:
        return bool(self.find_notes(deck_name=deck_name, word=word))

    def find_notes_in_deck(self, deck_name: str) -> list[int]:
        query = f'deck:"{deck_name}"'
        return self.find_notes_by_query(query)

    def notes_info(self, note_ids: list[int]) -> list[dict[str, Any]]:
        if not note_ids:
            return []
        result = self._invoke("notesInfo", {"notes": note_ids})
        return result if isinstance(result, list) else []

    def get_deck_word_to_note_ids(self, deck_name: str) -> dict[str, list[int]]:
        note_ids = self.find_notes_in_deck(deck_name)
        if not note_ids:
            return {}
        infos = self.notes_info(note_ids)
        mapping: dict[str, list[int]] = {}
        for info in infos:
            if not isinstance(info, dict):
                continue
            note_id = info.get("noteId")
            fields = info.get("fields", {})
            if not isinstance(note_id, int) or not isinstance(fields, dict):
                continue
            word_field = fields.get("Word", {})
            if not isinstance(word_field, dict):
                continue
            word = str(word_field.get("value", "")).strip().lower()
            if not word:
                continue
            mapping.setdefault(word, []).append(note_id)
        return mapping

    def delete_notes(self, note_ids: list[int]) -> None:
        if not note_ids:
            return
        self._invoke("deleteNotes", {"notes": note_ids})

    def upload_audio(self, file_path: Path) -> None:
        if not file_path.exists():
            raise AnkiConnectError(f"Missing audio file: {file_path}")
        self.upload_audio_if_needed(file_path)

    def retrieve_media_file(self, filename: str) -> str | None:
        result = self._invoke("retrieveMediaFile", {"filename": filename})
        if result is False or result is None:
            return None
        if isinstance(result, str):
            return result
        return None

    def media_file_exists(self, filename: str) -> bool:
        return self.retrieve_media_file(filename) is not None

    def upload_audio_if_needed(self, file_path: Path) -> bool:
        """Upload media file only when not present in Anki. Returns True if uploaded."""
        if not file_path.exists():
            raise AnkiConnectError(f"Missing audio file: {file_path}")
        if self.media_file_exists(file_path.name):
            return False
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        self._invoke("storeMediaFile", {"filename": file_path.name, "data": encoded})
        return True

    def upload_media_files_concurrently(
        self, file_paths: list[Path], max_workers: int = 8
    ) -> dict[str, list[str]]:
        unique_paths: dict[str, Path] = {}
        for path in file_paths:
            unique_paths[path.name] = path

        uploaded: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        def worker(path: Path) -> tuple[str, str | None]:
            try:
                did_upload = self.upload_audio_if_needed(path)
                return ("uploaded" if did_upload else "skipped"), path.name
            except Exception as exc:
                return "failed", f"{path.name}: {exc}"

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(worker, path) for path in unique_paths.values()]
            for future in as_completed(futures):
                status, payload = future.result()
                if status == "uploaded":
                    uploaded.append(str(payload))
                elif status == "skipped":
                    skipped.append(str(payload))
                else:
                    failed.append(str(payload))
        return {"uploaded": uploaded, "skipped": skipped, "failed": failed}

    def add_note(
        self,
        deck_name: str,
        model_name: str,
        word_data: dict[str, str],
        audio_word_filename: str,
        audio_sentence_filename: str,
    ) -> int:
        note = {
            "deckName": deck_name,
            "modelName": model_name,
            "fields": self._build_fields(
                word_data=word_data,
                audio_word_filename=audio_word_filename,
                audio_sentence_filename=audio_sentence_filename,
            ),
            "options": {"allowDuplicate": False},
            "tags": ["ai-anki"],
        }
        note_id = self._invoke("addNote", {"note": note})
        if not isinstance(note_id, int):
            raise AnkiConnectError(f"Unexpected note id: {note_id}")
        return note_id

    def add_notes(self, notes: list[dict[str, Any]]) -> list[int | None]:
        if not notes:
            return []
        result = self._invoke("addNotes", {"notes": notes})
        if not isinstance(result, list):
            raise AnkiConnectError(f"Unexpected addNotes response: {result}")
        output: list[int | None] = []
        for item in result:
            if isinstance(item, int):
                output.append(item)
            else:
                output.append(None)
        return output

    def update_note_fields(
        self,
        note_id: int,
        word_data: dict[str, str],
        audio_word_filename: str,
        audio_sentence_filename: str,
    ) -> None:
        fields = self._build_fields(
            word_data=word_data,
            audio_word_filename=audio_word_filename,
            audio_sentence_filename=audio_sentence_filename,
        )
        self._invoke("updateNoteFields", {"note": {"id": note_id, "fields": fields}})

    def update_note_fields_multi(
        self,
        updates: list[dict[str, Any]],
    ) -> dict[str, int]:
        """
        Batch update note fields via AnkiConnect multi.
        updates: [{"note_id": int, "word_data": {...}, "audio_word_filename": str, "audio_sentence_filename": str}]
        """
        if not updates:
            return {"updated": 0, "failed": 0}
        actions: list[dict[str, Any]] = []
        for item in updates:
            note_id = int(item["note_id"])
            fields = self._build_fields(
                word_data=item["word_data"],
                audio_word_filename=item["audio_word_filename"],
                audio_sentence_filename=item["audio_sentence_filename"],
            )
            actions.append(
                {
                    "action": "updateNoteFields",
                    "params": {"note": {"id": note_id, "fields": fields}},
                }
            )

        result = self._invoke("multi", {"actions": actions})
        if not isinstance(result, list):
            raise AnkiConnectError(f"Unexpected multi response: {result}")

        updated = 0
        failed = 0
        for item in result:
            if isinstance(item, dict):
                if item.get("error"):
                    failed += 1
                else:
                    updated += 1
            else:
                # multi may return plain result values for each action
                updated += 1
        return {"updated": updated, "failed": failed}

    def build_note_payload(
        self,
        deck_name: str,
        model_name: str,
        word_data: dict[str, str],
        audio_word_filename: str,
        audio_sentence_filename: str,
        allow_duplicate: bool = False,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "deckName": deck_name,
            "modelName": model_name,
            "fields": self._build_fields(
                word_data=word_data,
                audio_word_filename=audio_word_filename,
                audio_sentence_filename=audio_sentence_filename,
            ),
            "options": {"allowDuplicate": allow_duplicate},
            "tags": tags or ["ai-anki"],
        }

    @staticmethod
    def _build_fields(word_data: dict[str, str], audio_word_filename: str, audio_sentence_filename: str) -> dict[str, str]:
        return {
            "Word": word_data["word"],
            "Phonetic": word_data["phonetic"],
            "PartOfSpeech": word_data["part_of_speech"],
            "Translation": word_data["translation"],
            "Example": word_data["example"],
            "Analysis": word_data["analysis"],
            "AudioWord": f"[sound:{audio_word_filename}]",
            "AudioSentence": f"[sound:{audio_sentence_filename}]",
        }
