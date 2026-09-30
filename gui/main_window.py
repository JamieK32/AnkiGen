from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QDialog, QMainWindow, QMessageBox

from gui.workspace import Workspace
from gui.settings_dialog import SettingsDialog
from gui.workflow_actions import WorkflowActions
from gui.library_actions import LibraryActions
from services.vocabulary_workflow import sync_ready
from services.anki_api import AnkiAPI
from services.gpt_generator import DEFAULT_BASE_URL, DEFAULT_MODEL, GPTGenerator
from services.tts_generator import TTSGenerator
from utils.file_manager import (
    check_audio_exists,
    delete_word_assets,
    ensure_project_dirs,
    highlight_target_word,
    load_words,
    update_english_library,
    repair_word_data,
    rename_word_assets,
    save_words,
    sentence_audio_path,
    word_audio_path,
)
from utils.settings_manager import load_app_settings, sanitize_app_settings, save_app_settings


class TaskThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(int)
    log_message = Signal(str)

    def __init__(self, fn: Callable[[Callable[[int], None], Callable[[str], None]], object]) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            result = self.fn(self.progress_changed.emit, self.log_message.emit)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(Workspace, LibraryActions, WorkflowActions, QMainWindow):
    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _now_timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _normalize_timestamp_display(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text.replace("T", " ")[:19]

    def _ensure_imported_at_fields(self) -> bool:
        if not self.words:
            return False
        changed = False
        base = datetime.now().replace(microsecond=0)
        total = len(self.words)
        for idx, item in enumerate(self.words):
            normalized = self._normalize_timestamp_display(str(item.get("imported_at", "")))
            if normalized:
                if normalized != item.get("imported_at", ""):
                    item["imported_at"] = normalized
                    changed = True
                continue
            # Backfill missing timestamps while preserving current list order.
            item["imported_at"] = (base - timedelta(seconds=(total - idx))).strftime("%Y-%m-%d %H:%M:%S")
            changed = True
        return changed

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root
        self.data_dir, self.audio_dir, self.words_json_path = ensure_project_dirs(project_root)
        self.settings_path = self.data_dir / "settings.json"
        self.words: list[dict[str, str]] = []
        self._workers: set[TaskThread] = set()
        self._editor_baseline = None
        self._editing_word = None
        self._generating_words = set()
        self._task_busy = False
        self._busy_allows_browse_audio = False
        self._pending_progress: int | None = None
        self._last_progress = 0
        self._pending_logs: list[str] = []
        self._ui_flush_timer = QTimer(self)
        self._ui_flush_timer.setInterval(80)
        self._ui_flush_timer.timeout.connect(self._flush_worker_ui_updates)

        self.default_settings = {
            "api_key": os.getenv("YUNWU_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
            "base_url": os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL),
            "model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
            "anki_url": os.getenv("ANKI_CONNECT_URL", "http://localhost:8765"),
            "deck_name": os.getenv("ANKI_DECK_NAME", "AI Vocabulary"),
            "model_name": os.getenv("ANKI_MODEL_NAME", "AI Vocabulary Note"),
            "tts_voice": os.getenv("TTS_VOICE", "en-US-AriaNeural"),
            "metadata_batch_size": self._env_int("METADATA_BATCH_SIZE", 20),
            "tts_max_workers": self._env_int("TTS_MAX_WORKERS", 5),
            "anki_upload_workers": self._env_int("ANKI_UPLOAD_WORKERS", 8),
        }
        self.settings = load_app_settings(self.settings_path, self.default_settings)
        self._apply_runtime_settings(self.settings)

        self.audio_output = QAudioOutput(self)
        self.audio_player = QMediaPlayer(self)
        self.audio_player.setAudioOutput(self.audio_output)

        self.setWindowTitle("AnkiGen · 搭配词库")
        self.resize(1200, 720)
        self._build_ui()
        self._load_words_or_show_error()

    def _load_words_or_show_error(self) -> None:
        try:
            self.words = load_words(self.words_json_path)
            update_english_library(self.words_json_path, self.words)
            if self._ensure_imported_at_fields():
                save_words(self.words_json_path, self.words)
            self._sort_words()
            self._refresh_word_list()
            # Incomplete and failed entries stay visible for explicit completion/retry.
            self._summarize_audio_health()
        except Exception as exc:
            self._show_error(f"无法读取词库： {exc}")
            self.words = []
            self._refresh_word_list()

    def _sort_words(self) -> None:
        self.words.sort(
            key=lambda item: self._normalize_timestamp_display(item.get("imported_at", "")),
            reverse=True,
        )

    def _find_word(self, word: str) -> dict[str, str] | None:
        for item in self.words:
            if item["word"] == word:
                return item
        return None

    def _on_generate_all_from_toolbar(self) -> None:
        if self._current_selected_word() is None:
            self._show_error("请先选择搭配。")
            return
        self._on_generate_all_clicked()

    def _on_delete_word_clicked(self) -> None:
        if not self._confirm_unsaved():
            return
        selected_words = self._selected_words()
        if not selected_words:
            return

        count = len(selected_words)
        preview = ", ".join(selected_words[:8])
        if count > 8:
            preview += ", ..."
        reply = QMessageBox.question(
            self,
            "删除选中搭配",
            f"删除选中的 {count} 条搭配？\n\n{preview}\n\n词条及其音频将一并删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted_count = self._delete_words(selected_words)
        self.statusBar().showMessage(f"已删除 {deleted_count} 条搭配。", 4000)
        self._append_log(f"已删除 {deleted_count} 条搭配。")

    def _delete_words(self, words: list[str]) -> int:
        requested = {word.strip().lower() for word in words if word.strip()}
        if not requested:
            return 0
        existing = {item["word"] for item in self.words}
        delete_set = requested & existing
        if not delete_set:
            return 0
        self._editor_baseline = None
        self.words = [item for item in self.words if item["word"] not in delete_set]
        save_words(self.words_json_path, self.words)
        for word in delete_set:
            delete_word_assets(self.audio_dir, word)
        self._refresh_word_list()
        return len(delete_set)

    def _on_save_word_clicked(self, edited_data: dict[str, str], refresh_ui: bool = True) -> bool:
        current_word = self._editing_word
        if not current_word:
            return False
        if not self._validate_word_data(edited_data):
            return False
        current_item = self._find_word(current_word)
        if current_item is not None:
            for field in ('source_text', 'generation_error', 'generation_mode'):
                edited_data[field] = current_item.get(field, '')
            imported_at = self._normalize_timestamp_display(str(current_item.get("imported_at", "")))
            edited_data["imported_at"] = imported_at or self._now_timestamp()

        existing = self._find_word(edited_data["word"])
        if edited_data["word"] != current_word and existing is not None:
            self._show_error(f"搭配 '{edited_data['word']}' 已存在。")
            return False

        updated = [edited_data if item['word'] == current_word else item for item in self.words]
        renamed = edited_data['word'] != current_word
        try:
            if renamed:
                rename_word_assets(self.audio_dir, current_word, edited_data['word'])
            save_words(self.words_json_path, updated)
        except Exception as exc:
            if renamed:
                rename_word_assets(self.audio_dir, edited_data['word'], current_word)
            self._show_error(f'保存失败，编辑内容已保留：{exc}')
            return False
        self.words = updated
        self._editing_word = edited_data['word']
        self._editor_baseline = self.editor.get_word_data()
        self._update_dirty_indicator()
        self._sort_words()
        self._invalidate_sync()
        if refresh_ui:
            self._refresh_word_list(select_word=edited_data["word"])
            self._update_audio_status(edited_data["word"])
            self.statusBar().showMessage("修改已保存。", 3000)
        else:
            selected = [edited_data['word'] if word == current_word else word for word in self._selected_words()]
            self._selection_guard = True
            self.word_model.replace(self.words, self._generating_words)
            self._select_words(selected)
            self._selection_guard = False
        return True

    def _on_generate_all_clicked(self) -> None:
        self._generate_selected('complete')

    def _on_play_word_audio_clicked(self, word: str) -> None:
        if not word:
            return
        path = word_audio_path(self.audio_dir, word)
        self._play_audio(path)

    def _on_play_sentence_audio_clicked(self, word: str) -> None:
        if not word:
            return
        path = sentence_audio_path(self.audio_dir, word)
        self._play_audio(path)

    def _play_audio(self, path: Path) -> None:
        if not path.exists():
            self._show_error(f"音频文件不存在：{path.name}")
            return
        self.audio_player.setSource(QUrl.fromLocalFile(str(path)))
        self.audio_player.play()
        self.statusBar().showMessage(f"正在播放 {path.name}", 3000)

    def _on_sync_to_anki_clicked(self, approved_remote) -> None:

        snapshot = [dict(item) for item in self.words]

        def task(progress: Callable[[int], None], log: Callable[[str], None]) -> dict[str, object]:
            log("阶段：正在同步本地词库到 Anki…")
            self.anki_api.check_connection()
            current_remote = self.anki_api.get_deck_word_to_note_ids(self.deck_name)
            if current_remote != approved_remote:
                raise ValueError("Anki 内容已变化，请重新打开同步预览。")
            self.anki_api.ensure_deck(self.deck_name)
            self.anki_api.ensure_model(self.model_name)

            created = 0
            updated = 0
            deleted = 0
            skipped = 0
            errors: list[str] = []
            repaired_words: list[dict[str, str]] = []
            local_map: dict[str, dict[str, str]] = {}
            total_prepare = len(snapshot)

            for idx, item in enumerate(snapshot, start=1):
                word = item["word"]
                try:
                    # Sync should only use local data; AI/TTS generation belongs to Generate All.
                    repaired = repair_word_data(item, metadata_provider=None)
                except Exception as exc:
                    skipped += 1
                    errors.append(f"{word}: 准备本地内容失败 ({exc})")
                    repaired_words.append(item)
                    continue

                word = repaired["word"]
                if word in local_map:
                    skipped += 1
                    errors.append(f"{word}: 已跳过重复本地词条")
                    continue
                local_map[word] = repaired
                repaired_words.append(repaired)
                progress(int((idx / max(1, total_prepare)) * 40))

            anki_map = {word: list(ids) for word, ids in approved_remote.items()}
            local_words = set(local_map.keys())
            anki_words = set(anki_map.keys())

            words_to_create = local_words - anki_words
            words_to_update = local_words & anki_words
            words_to_delete = anki_words - local_words

            delete_note_ids: list[int] = []
            for word in words_to_delete:
                delete_note_ids.extend(anki_map.get(word, []))

            # If Anki has duplicates for a word, keep one and delete extras.
            for word in words_to_update:
                note_ids = anki_map.get(word, [])
                if len(note_ids) > 1:
                    delete_note_ids.extend(note_ids[1:])
                    anki_map[word] = note_ids[:1]

            if delete_note_ids:
                self.anki_api.delete_notes(delete_note_ids)
                deleted += len(delete_note_ids)
                log(f"已从 Anki 删除 {len(delete_note_ids)} 条笔记。")

            create_payloads: list[dict[str, object]] = []
            create_words: list[str] = []
            update_payloads: list[dict[str, object]] = []
            media_files_to_upload: list[Path] = []

            for word in sorted(words_to_create):
                item = local_map[word]
                word_audio = word_audio_path(self.audio_dir, word)
                sentence_audio = sentence_audio_path(self.audio_dir, word)
                if not sync_ready(item, self.audio_dir):
                    skipped += 1
                    errors.append(f"{word}: 缺少中文、例句或音频")
                    continue
                note_data = dict(item)
                note_data["example"] = highlight_target_word(note_data["example"], note_data["word"])
                create_payloads.append(
                    self.anki_api.build_note_payload(
                        deck_name=self.deck_name,
                        model_name=self.model_name,
                        word_data=note_data,
                        audio_word_filename=word_audio.name,
                        audio_sentence_filename=sentence_audio.name,
                    )
                )
                create_words.append(word)
                media_files_to_upload.extend([word_audio, sentence_audio])

            for word in sorted(words_to_update):
                note_ids = anki_map.get(word, [])
                if not note_ids:
                    skipped += 1
                    errors.append(f"{word}: 更新时笔记不存在")
                    continue
                item = local_map[word]
                word_audio = word_audio_path(self.audio_dir, word)
                sentence_audio = sentence_audio_path(self.audio_dir, word)
                if not sync_ready(item, self.audio_dir):
                    skipped += 1
                    errors.append(f"{word}: 缺少中文、例句或音频")
                    continue
                note_data = dict(item)
                note_data["example"] = highlight_target_word(note_data["example"], note_data["word"])
                update_payloads.append(
                    {
                        "word": word,
                        "note_id": note_ids[0],
                        "word_data": note_data,
                        "audio_word_filename": word_audio.name,
                        "audio_sentence_filename": sentence_audio.name,
                    }
                )
                media_files_to_upload.extend([word_audio, sentence_audio])

            upload_result = self.anki_api.upload_media_files_concurrently(
                media_files_to_upload,
                max_workers=self.anki_upload_workers,
            )
            progress(70)
            upload_failed = upload_result.get("failed", [])
            failed_media_names: set[str] = set()
            for row in upload_failed:
                # row format: "<filename>: <error>"
                failed_media_names.add(str(row).split(":", 1)[0].strip())
                errors.append(f"媒体上传失败 ({row})")

            valid_create_payloads: list[dict[str, object]] = []
            valid_create_words: list[str] = []
            for word, payload in zip(create_words, create_payloads):
                fields = payload.get("fields", {}) if isinstance(payload, dict) else {}
                audio_word_tag = str(fields.get("AudioWord", ""))
                audio_sentence_tag = str(fields.get("AudioSentence", ""))
                word_filename = audio_word_tag.replace("[sound:", "").replace("]", "")
                sentence_filename = audio_sentence_tag.replace("[sound:", "").replace("]", "")
                if word_filename in failed_media_names or sentence_filename in failed_media_names:
                    skipped += 1
                    errors.append(f"{word}: 媒体上传失败，已跳过")
                    continue
                valid_create_payloads.append(payload)
                valid_create_words.append(word)

            if valid_create_payloads:
                add_results = self.anki_api.add_notes(valid_create_payloads)
                for word, note_id in zip(valid_create_words, add_results):
                    if isinstance(note_id, int):
                        created += 1
                    else:
                        skipped += 1
                        errors.append(f"{word}: 创建失败")
                log(f"批量创建完成：请求 {len(valid_create_payloads)}，成功 {created}")

            valid_updates: list[dict[str, object]] = []
            for payload in update_payloads:
                word = str(payload.get("word", ""))
                word_filename = str(payload.get("audio_word_filename", ""))
                sentence_filename = str(payload.get("audio_sentence_filename", ""))
                if word_filename in failed_media_names or sentence_filename in failed_media_names:
                    skipped += 1
                    errors.append(f"{word}: 媒体上传失败，已跳过")
                    continue
                valid_updates.append(payload)

            if valid_updates:
                update_result = self.anki_api.update_note_fields_multi(valid_updates)
                updated += int(update_result.get("updated", 0))
                failed_updates = int(update_result.get("failed", 0))
                if failed_updates > 0:
                    skipped += failed_updates
                    errors.append(f"{failed_updates} 条笔记批量更新失败")
                log(
                    f"批量更新完成：请求 {len(valid_updates)} "
                    f"updated={int(update_result.get('updated', 0))} failed={failed_updates}"
                )

            progress(100)
            log(f"同步完成：新增 {created}，更新 {updated}，删除 {deleted}，跳过 {skipped}")

            return {
                "created": created,
                "updated": updated,
                "deleted": deleted,
                "skipped": skipped,
                "errors": errors,
                "words": repaired_words,
            }

        self._start_task(
            status_text="Uploading to Anki...",
            fn=task,
            on_success=self._finish_sync_to_anki,
            allow_browse_audio=True,
        )

    def _finish_sync_to_anki(self, result: object) -> None:
        if not isinstance(result, dict):
            self._show_error("同步返回了无法识别的结果。")
            return
        words = result.get("words")
        if isinstance(words, list):
            self.words = words
            self._ensure_imported_at_fields()
            self._sort_words()
            save_words(self.words_json_path, self.words)
            self._refresh_word_list()
        created = int(result.get("created", 0))
        updated = int(result.get("updated", 0))
        deleted = int(result.get("deleted", 0))
        skipped = int(result.get("skipped", 0))
        self.progress_label.setText(f'同步完成：新增 {created} · 更新 {updated} · 删除 {deleted} · 跳过 {skipped}')
        errors = result.get("errors", [])
        self.sync_outcome.setText(self.progress_label.text())
        self._append_log(self.progress_label.text())
        if isinstance(errors, list) and errors:
            self.sync_outcome.setText(self.progress_label.text() + '\n部分条目失败，请查看日志。')
            self._append_log('\n'.join(errors), 'ERROR')

    def _validate_word_data(self, data: dict[str, str]) -> bool:
        required = ["word"]
        for field in required:
            if not data.get(field, "").strip():
                self._show_error("英文搭配不能为空。")
                return False
        return True

    def _start_task(
        self,
        status_text: str,
        fn: Callable[[Callable[[int], None], Callable[[str], None]], object],
        on_success: Callable[[object], None],
        allow_browse_audio: bool = False,
    ) -> None:
        if self._task_busy:
            return
        worker = TaskThread(fn)
        self._workers.add(worker)
        self._pending_progress = None
        self._pending_logs.clear()
        self._set_busy(True, status_text, allow_browse_audio=allow_browse_audio)
        self._set_progress(0)
        self.progress_label.setText(f"{status_text} (0%)")
        self._append_log(status_text)
        self._ui_flush_timer.start()

        def cleanup() -> None:
            self._ui_flush_timer.stop()
            while self._pending_logs:
                self._flush_worker_ui_updates()
            self._flush_worker_ui_updates()
            self._set_busy(False, "就绪")
            self.progress_label.setText("任务完成")

        def on_ok(result: object) -> None:
            cleanup()
            try:
                on_success(result)
            except Exception as exc:
                self.progress_label.setText('结果处理失败')
                self._show_error(str(exc))

        def on_fail(message: str) -> None:
            cleanup()
            changed = bool(self._generating_words)
            for entry in self.words:
                if entry['word'] in self._generating_words:
                    entry['generation_error'] = message
            self._generating_words.clear()
            if changed:
                save_words(self.words_json_path, self.words)
            self._refresh_word_list()
            self.progress_label.setText("任务失败，可重试")
            self._show_error(message)

        worker.succeeded.connect(on_ok)
        worker.failed.connect(on_fail)
        worker.progress_changed.connect(self._enqueue_worker_progress)
        worker.log_message.connect(self._enqueue_worker_log)
        worker.finished.connect(lambda: self._workers.discard(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _enqueue_worker_progress(self, percent: int) -> None:
        self._pending_progress = max(0, min(100, int(percent)))

    def _enqueue_worker_log(self, message: str) -> None:
        text = str(message).strip()
        if text:
            self._pending_logs.append(text)
        if len(self._pending_logs) > 2000:
            del self._pending_logs[: len(self._pending_logs) - 2000]

    def _flush_worker_ui_updates(self) -> None:
        if self._pending_progress is not None:
            self._set_progress(self._pending_progress)
            self._pending_progress = None
        if not self._pending_logs:
            return
        # Flush a small batch each tick to keep the main thread responsive.
        batch = self._pending_logs[:12]
        del self._pending_logs[:12]
        for row in batch:
            self._append_log(row)

    def _apply_runtime_settings(self, settings: dict[str, object]) -> None:
        cleaned = sanitize_app_settings(settings, self.default_settings)
        self.settings = cleaned
        self.api_key = str(cleaned["api_key"])
        self.base_url = str(cleaned["base_url"])
        self.model = str(cleaned["model"])
        self.anki_url = str(cleaned["anki_url"])
        self.deck_name = str(cleaned["deck_name"])
        self.model_name = str(cleaned["model_name"])
        self.voice = str(cleaned["tts_voice"])
        self.metadata_batch_size = int(cleaned["metadata_batch_size"])
        self.tts_max_workers = int(cleaned["tts_max_workers"])
        self.anki_upload_workers = int(cleaned["anki_upload_workers"])

        self.gpt_generator = (
            GPTGenerator(api_key=self.api_key, base_url=self.base_url, model=self.model) if self.api_key else None
        )
        self.tts_generator = TTSGenerator(voice=self.voice, max_workers=self.tts_max_workers)
        self.anki_api = AnkiAPI(base_url=self.anki_url)

    def _current_settings_snapshot(self) -> dict[str, object]:
        return {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "anki_url": self.anki_url,
            "deck_name": self.deck_name,
            "model_name": self.model_name,
            "tts_voice": self.voice,
            "metadata_batch_size": self.metadata_batch_size,
            "tts_max_workers": self.tts_max_workers,
            "anki_upload_workers": self.anki_upload_workers,
        }

    def _on_open_settings_clicked(self) -> None:
        dialog = SettingsDialog(settings=self._current_settings_snapshot(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_settings = dialog.get_settings()
        self._apply_runtime_settings(new_settings)
        save_app_settings(self.settings_path, self.settings, self.default_settings)
        self.anki_status.setText('Anki：未检测')
        self._invalidate_sync()
        self.statusBar().showMessage("设置已保存并应用。", 5000)
        self._append_log(
            "设置已更新。"
            f"model={self.model}, batch={self.metadata_batch_size}, tts_workers={self.tts_max_workers}, "
            f"anki_upload_workers={self.anki_upload_workers}"
        )

    def _summarize_audio_health(self) -> None:
        if not self.words:
            return
        missing_word_audio = 0
        missing_sentence_audio = 0
        for item in self.words:
            has_word, has_sentence = check_audio_exists(self.audio_dir, item["word"])
            if not has_word:
                missing_word_audio += 1
            if not has_sentence:
                missing_sentence_audio += 1
        if missing_word_audio or missing_sentence_audio:
            self.statusBar().showMessage(
                f"音频检查：缺搭配音频 {missing_word_audio} 条，缺例句音频 {missing_sentence_audio} 条",
                7000,
            )

    def _update_audio_status(self, word: str) -> None:
        word_exists, sentence_exists = check_audio_exists(self.audio_dir, word)
        self.editor.set_audio_status(word_exists, sentence_exists)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QMenu, QWidget#AppRoot {
                background-color: #111318;
                color: #E5E7EB;
                font-size: 12px;
            }
            QWidget#Panel {
                background-color: #171A21;
                border: 1px solid #2A2F3A;
                border-radius: 10px;
            }
            QWidget { font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
            QDialog, QScrollArea, QStackedWidget { background: #111318; color: #E5E7EB; }
            QScrollArea { border: none; }
            QWidget#Editor { background: #171A21; }
            QLabel#PageTitle { font-size: 20px; font-weight: 600; padding: 5px 0; }
            QLabel#Muted { color: #9CA3AF; }
            QTableView { background: #171A21; alternate-background-color: #1b1f27; color: #E5E7EB;
                border: 1px solid #2A2F3A; selection-background-color: #314C68; outline: none; }
            QHeaderView::section { background: #20252e; color: #aeb8c9; border: none; padding: 8px; }
            QHeaderView { background: #20252e; }
            QTableCornerButton::section { background: #20252e; border: none; }
            QScrollBar:vertical { background: #171A21; width: 10px; }
            QScrollBar:horizontal { background: #171A21; height: 10px; }
            QScrollBar::handle { background: #414a5b; border-radius: 4px; min-height: 24px; min-width: 24px; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
            QTableView::item { padding: 4px; }
            QListWidget { color: #E5E7EB; }
            QListWidget#Navigation::item { padding: 12px 8px; margin: 3px; border-radius: 6px; }
            QListWidget::item:selected { background: #314C68; color: white; }
            QPushButton:disabled, QPushButton#PrimaryAction:disabled { color: #77808f; background: #1b1f27; border-color: #282d36; }
            QPushButton:checked { background: #314C68; }
            QComboBox QAbstractItemView { background: #20252e; color: #E5E7EB; selection-background-color: #314C68; }
            QSplitter::handle { background: #282d36; }
            QToolBar {
                background: #171A21;
                border: none;
                border-bottom: 1px solid #2A2F3A;
                spacing: 6px;
            }
            QPushButton#PrimaryAction {
                background-color: #3B82F6;
                color: #ffffff;
                border: 1px solid #316FD1;
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 600;
            }
            QPushButton#PrimaryAction:hover {
                background-color: #2563EB;
                border: 1px solid #1D4ED8;
            }
            QPushButton#SecondaryToolbarAction {
                background-color: #1E222B;
                color: #E5E7EB;
                border: 1px solid #2A2F3A;
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 500;
            }
            QPushButton#SecondaryToolbarAction:hover {
                background-color: #252B37;
            }
            QPushButton {
                background-color: #1E222B;
                color: #E5E7EB;
                border: 1px solid #2A2F3A;
                border-radius: 8px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #242A36;
            }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
                background-color: #1E222B;
                border: 1px solid #2A2F3A;
                border-radius: 8px;
                padding: 6px;
                color: #E5E7EB;
            }
            QLineEdit#SearchInput {
                padding-left: 28px;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #3B82F6;
            }
            QListWidget {
                background-color: #171A21;
                border: 1px solid #2A2F3A;
                border-radius: 10px;
                outline: 0;
            }
            QListWidget::item:hover {
                background-color: #1F2937;
            }
            QLabel {
                color: #E5E7EB;
            }
            QProgressBar {
                border: 1px solid #2A2F3A;
                border-radius: 6px;
                text-align: center;
                color: #E5E7EB;
                background: #171A21;
            }
            QProgressBar::chunk {
                background-color: #3A7FCD;
                border-radius: 5px;
            }
            QStatusBar {
                background-color: #111318;
                color: #A0ABBD;
                border-top: 1px solid #2A2F3A;
            }
            """
        )

    def _show_error(self, message: str) -> None:
        self.log_toggle.setChecked(True)
        self.statusBar().showMessage(message, 5000)
        self._append_log(message, level="ERROR")
        self.progress_label.setText("操作失败，请展开日志查看详情")
        if self.anki_status.text() == "Anki：检测中…":
            self.anki_status.setText("Anki：未连接")

    def _set_progress(self, percent: int) -> None:
        safe = max(0, min(100, int(percent)))
        self._last_progress = safe
        if not self._task_busy:
            self.progress_bar.setValue(safe)
        text = self.progress_label.text().split(" (", 1)[0]
        if text and text != "就绪":
            self.progress_label.setText(f"{text} ({safe}%)")

    def _toggle_log(self, expanded):
        self.log_widget.setVisible(expanded)
        self.clear_log_button.setVisible(expanded)
        self.log_toggle.setText("收起日志" if expanded else "展开日志")

    def _clear_log(self) -> None:
        self._pending_logs.clear()
        self.log_widget.clear()

    def _show_log_context_menu(self, position) -> None:
        menu = self.log_widget.createStandardContextMenu()
        menu.addSeparator()
        clear_action = menu.addAction("清空日志")
        clear_action.triggered.connect(self._clear_log)
        menu.exec(self.log_widget.mapToGlobal(position))
        menu.deleteLater()

    def _append_log(self, message: str, level: str = "INFO") -> None:
        if message.startswith('阶段：'):
            self.progress_label.setText(message.removeprefix('阶段：'))
        if level.upper() == "ERROR":
            self.log_toggle.setChecked(True)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.appendPlainText(f"[{stamp}] {level.upper()} {message}")
