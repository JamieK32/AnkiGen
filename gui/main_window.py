from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from gui.word_editor import WordEditor
from services.anki_api import AnkiAPI
from services.gpt_generator import DEFAULT_BASE_URL, DEFAULT_MODEL, GPTGenerator
from services.tts_generator import TTSGenerator
from utils.file_manager import (
    check_audio_exists,
    delete_word_assets,
    ensure_project_dirs,
    highlight_target_word,
    load_words,
    parse_words_batch,
    repair_word_data,
    rename_word_assets,
    save_words,
    sentence_audio_path,
    word_audio_path,
)


class WordListDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()

        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg_color = QColor("#3A7FCD") if is_selected else (QColor("#1F2937") if is_hovered else QColor("#171A21"))
        border_color = QColor("#2A2F3A")
        word_color = QColor("#E5E7EB")
        phonetic_color = QColor("#9CA3AF")

        rect = option.rect.adjusted(4, 2, -4, -2)
        painter.fillRect(rect, bg_color)
        painter.setPen(border_color)
        painter.drawRect(rect)

        word = str(index.data(Qt.ItemDataRole.UserRole) or "")
        phonetic = str(index.data(Qt.ItemDataRole.UserRole + 1) or "")

        word_font = QFont(option.font)
        word_font.setBold(True)
        word_font.setPointSize(max(10, option.font.pointSize()))
        painter.setFont(word_font)
        painter.setPen(word_color)
        painter.drawText(rect.adjusted(10, 6, -10, -20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, word)

        if phonetic:
            phonetic_font = QFont(option.font)
            phonetic_font.setPointSize(max(9, option.font.pointSize() - 1))
            painter.setFont(phonetic_font)
            painter.setPen(phonetic_color)
            painter.drawText(rect.adjusted(10, 24, -10, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, phonetic)

        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        phonetic = str(index.data(Qt.ItemDataRole.UserRole + 1) or "").strip()
        return QSize(option.rect.width(), 52 if phonetic else 38)


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


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root
        self.data_dir, self.audio_dir, self.words_json_path = ensure_project_dirs(project_root)
        self.words: list[dict[str, str]] = []
        self._workers: set[TaskThread] = set()

        self.api_key = os.getenv("YUNWU_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL)
        self.model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.anki_url = os.getenv("ANKI_CONNECT_URL", "http://localhost:8765")
        self.deck_name = os.getenv("ANKI_DECK_NAME", "AI Vocabulary")
        self.model_name = os.getenv("ANKI_MODEL_NAME", "AI Vocabulary Note")
        self.voice = os.getenv("TTS_VOICE", "en-US-AriaNeural")

        self.gpt_generator = GPTGenerator(api_key=self.api_key, base_url=self.base_url, model=self.model) if self.api_key else None
        self.tts_generator = TTSGenerator(voice=self.voice)
        self.anki_api = AnkiAPI(base_url=self.anki_url)

        self.audio_output = QAudioOutput(self)
        self.audio_player = QMediaPlayer(self)
        self.audio_player.setAudioOutput(self.audio_output)

        self.setWindowTitle("AI Anki Card Generator")
        self.resize(1200, 720)
        self._build_ui()
        self._load_words_or_show_error()

    def _build_ui(self) -> None:
        self._apply_theme()

        self.top_toolbar = QToolBar("Main Toolbar", self)
        self.top_toolbar.setMovable(False)
        self.top_toolbar.setFloatable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.top_toolbar)

        toolbar_container = QWidget()
        toolbar_layout = QHBoxLayout(toolbar_container)
        toolbar_layout.setContentsMargins(6, 4, 6, 4)
        toolbar_layout.setSpacing(8)

        self.add_button = QPushButton("Add Word")
        self.generate_all_button = QPushButton("Generate All")
        self.sync_button = QPushButton("Sync to Anki")
        self.generate_all_button.setObjectName("PrimaryAction")
        for btn in (self.add_button, self.sync_button):
            btn.setObjectName("SecondaryToolbarAction")
        for btn in (self.add_button, self.generate_all_button, self.sync_button):
            btn.setMinimumHeight(32)
            toolbar_layout.addWidget(btn)
        toolbar_layout.addStretch(1)
        self.top_toolbar.addWidget(toolbar_container)

        splitter = QSplitter()
        left_panel = QWidget()
        left_panel.setObjectName("Panel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchInput")
        self.search_input.setPlaceholderText("Search words...")
        search_icon = QIcon.fromTheme("edit-find")
        if search_icon.isNull():
            search_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        self.search_input.addAction(search_icon, QLineEdit.ActionPosition.LeadingPosition)
        left_layout.addWidget(self.search_input, 0)

        self.word_list = QListWidget()
        self.word_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.word_list.setMouseTracking(True)
        self.word_list.setItemDelegate(WordListDelegate(self.word_list))
        left_layout.addWidget(self.word_list, 1)

        self.word_count_label = QLabel("Words: 0")
        left_layout.addWidget(self.word_count_label)

        buttons_row = QHBoxLayout()
        self.delete_button = QPushButton("Delete Selected")
        buttons_row.addWidget(self.delete_button)
        buttons_row.addStretch(1)
        left_layout.addLayout(buttons_row)

        self.editor = WordEditor()
        self.editor.setObjectName("Panel")
        self.editor.regenerate_audio_button.hide()
        self.editor.play_word_button.setText("Word")
        self.editor.play_sentence_button.setText("Sentence")
        self.editor.play_word_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.editor.play_sentence_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.editor.play_word_button.setToolTip("Play Word Audio")
        self.editor.play_sentence_button.setToolTip("Play Sentence Audio")
        self.editor.play_word_button.setMinimumWidth(90)
        self.editor.play_sentence_button.setMinimumWidth(110)

        splitter.addWidget(left_panel)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.progress_label = QLabel("Ready")
        self.progress_label.setStyleSheet("QLabel { color: #9CA3AF; font-size: 12px; font-weight: 500; }")

        self.log_title = QLabel("Activity Log")
        self.log_title.setStyleSheet("QLabel { color: #9CA3AF; font-weight: 600; padding-left: 2px; }")

        self.log_widget = QPlainTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setMaximumBlockCount(500)
        self.log_widget.setPlaceholderText("Runtime logs...")
        self.log_widget.setMinimumHeight(130)
        self.log_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.log_widget.setStyleSheet(
            "QPlainTextEdit { "
            "  background-color: #10141D; "
            "  color: #E5E7EB; "
            "  border: 1px solid #2A2F3A; "
            "  border-radius: 10px; "
            "  padding: 10px; "
            "  selection-background-color: #3A7FCD; "
            "  font-family: 'JetBrains Mono', Consolas, 'Courier New', monospace; "
            "  font-size: 12px; "
            "} "
            "QPlainTextEdit:focus { "
            "  border: 1px solid #2A2F3A; "
            "  outline: 0; "
            "}"
        )

        central = QWidget()
        central.setObjectName("AppRoot")
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 8, 10, 10)
        main_layout.setSpacing(8)
        main_layout.addWidget(splitter, 1)
        main_layout.addWidget(self.progress_label, 0)
        main_layout.addWidget(self.progress_bar, 0)
        main_layout.addWidget(self.log_title, 0)
        main_layout.addWidget(self.log_widget, 0)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Ready")
        self.editor.set_actions_enabled(False)
        self.delete_button.setEnabled(False)
        self.generate_all_button.setEnabled(False)
        self.sync_button.setEnabled(True)
        self._append_log("Application started.")

        self.search_input.textChanged.connect(self._refresh_word_list)
        self.word_list.itemSelectionChanged.connect(self._on_word_selected)
        self.add_button.clicked.connect(self._on_add_word_clicked)
        self.generate_all_button.clicked.connect(self._on_generate_all_from_toolbar)
        self.delete_button.clicked.connect(self._on_delete_word_clicked)
        self.sync_button.clicked.connect(self._on_sync_to_anki_clicked)

        self.editor.save_requested.connect(self._on_save_word_clicked)
        self.editor.regenerate_audio_requested.connect(lambda _: self._on_generate_all_clicked())
        self.editor.play_word_audio_requested.connect(self._on_play_word_audio_clicked)
        self.editor.play_sentence_audio_requested.connect(self._on_play_sentence_audio_clicked)

    def _load_words_or_show_error(self) -> None:
        try:
            self.words = load_words(self.words_json_path)
            self._sort_words()
            self._refresh_word_list()
            self._auto_repair_on_load()
            self._summarize_audio_health()
        except Exception as exc:
            self._show_error(f"Failed to load words.json: {exc}")
            self.words = []
            self._refresh_word_list()

    def _auto_repair_on_load(self) -> None:
        if not self.words:
            return
        if not self.gpt_generator:
            return
        if not any(self._has_missing_metadata(item) for item in self.words):
            return

        snapshot = [dict(item) for item in self.words]

        def task(progress: Callable[[int], None], log: Callable[[str], None]) -> dict[str, object]:
            repaired_words: list[dict[str, str]] = []
            repaired_count = 0
            errors: list[str] = []
            total = len(snapshot)
            for idx, item in enumerate(snapshot, start=1):
                if not self._has_missing_metadata(item):
                    repaired_words.append(item)
                    progress(int(idx * 100 / max(1, total)))
                    continue
                try:
                    fixed = repair_word_data(item, metadata_provider=self.gpt_generator.ensure_metadata)
                    repaired_words.append(fixed)
                    repaired_count += 1
                    log(f"Auto-repaired metadata: {fixed.get('word', '<unknown>')}")
                except Exception as exc:
                    repaired_words.append(item)
                    errors.append(f"{item.get('word', '<unknown>')}: {exc}")
                    log(f"Auto-repair failed: {item.get('word', '<unknown>')} ({exc})")
                progress(int(idx * 100 / max(1, total)))
            return {"words": repaired_words, "repaired_count": repaired_count, "errors": errors}

        self._start_task(
            status_text="Repairing missing metadata...",
            fn=task,
            on_success=self._finish_auto_repair_on_load,
        )

    def _finish_auto_repair_on_load(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        words = result.get("words")
        repaired_count = int(result.get("repaired_count", 0))
        errors = result.get("errors", [])
        if isinstance(words, list) and words:
            self.words = words
            self._sort_words()
            save_words(self.words_json_path, self.words)
            self._refresh_word_list()
        if repaired_count > 0:
            self.statusBar().showMessage(f"Auto-repaired metadata for {repaired_count} words.", 5000)
            self._append_log(f"Auto-repaired metadata for {repaired_count} words.")
        if isinstance(errors, list) and errors:
            self.statusBar().showMessage("Some words failed auto-repair.", 7000)
            self._append_log("Some words failed auto-repair.")
        self._summarize_audio_health()

    def _sort_words(self) -> None:
        self.words.sort(key=lambda item: item["word"])

    def _refresh_word_list(self, select_word: str | None = None) -> None:
        query = self.search_input.text().strip().lower()
        current_word = select_word or self._current_selected_word()
        self.word_list.clear()

        filtered = []
        for item in self.words:
            if query and query not in item["word"] and query not in item["translation"]:
                continue
            filtered.append(item)
            phonetic = item.get("phonetic", "").strip()
            qitem = QListWidgetItem(item["word"])
            qitem.setData(Qt.ItemDataRole.UserRole, item["word"])
            qitem.setData(Qt.ItemDataRole.UserRole + 1, phonetic)
            self.word_list.addItem(qitem)

        self.word_count_label.setText(f"Words: {len(self.words)} (Showing: {len(filtered)})")

        if self.word_list.count() == 0:
            self.editor.clear()
            self.editor.set_actions_enabled(False)
            self.delete_button.setEnabled(False)
            self.generate_all_button.setEnabled(False)
            return

        target = current_word or filtered[0]["word"]
        for i in range(self.word_list.count()):
            item = self.word_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == target:
                self.word_list.setCurrentRow(i)
                break

    def _current_selected_word(self) -> str | None:
        item = self.word_list.currentItem()
        if not item:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def _selected_words(self) -> list[str]:
        selected_items = self.word_list.selectedItems()
        if not selected_items:
            current = self._current_selected_word()
            return [current] if current else []
        words: list[str] = []
        seen: set[str] = set()
        for item in selected_items:
            word = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if word and word not in seen:
                words.append(word)
                seen.add(word)
        return words

    def _find_word(self, word: str) -> dict[str, str] | None:
        for item in self.words:
            if item["word"] == word:
                return item
        return None

    def _on_word_selected(self) -> None:
        selected_words = self._selected_words()
        if not selected_words:
            self.editor.clear()
            self.editor.set_actions_enabled(False)
            self.delete_button.setEnabled(False)
            self.generate_all_button.setEnabled(False)
            return
        self.delete_button.setEnabled(True)

        if len(selected_words) > 1:
            self.editor.clear()
            self.editor.set_actions_enabled(False)
            self.generate_all_button.setEnabled(False)
            return

        word = selected_words[0]
        item = self._find_word(word)
        if not item:
            return
        self.editor.set_word_data(item, False, False)
        self._update_audio_status(word)
        self.editor.set_actions_enabled(True)
        self.generate_all_button.setEnabled(True)

    def _on_generate_all_from_toolbar(self) -> None:
        if self._current_selected_word() is None:
            self._show_error("Please select a word first.")
            return
        self._on_generate_all_clicked()

    def _on_add_word_clicked(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Words", "Enter words separated by spaces:")
        if not ok or not text.strip():
            return
        words = parse_words_batch(text)
        if not words:
            self._show_error("No valid words found.")
            return

        existing = {item["word"] for item in self.words}
        to_generate = [word for word in words if word not in existing]
        if not to_generate:
            self.statusBar().showMessage("All input words already exist.", 4000)
            return
        if not self.gpt_generator:
            self._show_error("Missing API key. Set YUNWU_API_KEY or OPENAI_API_KEY in .env.")
            return

        def task(progress: Callable[[int], None], log: Callable[[str], None]) -> dict[str, object]:
            batch_result = self.gpt_generator.generate_words_batch(
                to_generate,
                batch_size=20,
                progress_callback=lambda p: progress(min(50, p // 2)),
                log_callback=log,
            )
            generated_items = batch_result["items"] if isinstance(batch_result.get("items"), list) else []
            audio_result = self.tts_generator.generate_audio_batch(
                generated_items,
                self.audio_dir,
                max_workers=5,
                progress_callback=lambda p: progress(50 + min(50, p // 2)),
                log_callback=log,
            )
            audio_errors = audio_result.get("errors", []) if isinstance(audio_result.get("errors"), list) else []
            progress(100)
            return {
                "items": generated_items,
                "errors": list(batch_result.get("errors", [])) + audio_errors,
            }

        self._start_task(
            status_text="Generating vocabulary and audio...",
            fn=task,
            on_success=self._finish_add_words,
        )

    def _finish_add_words(self, new_items: object) -> None:
        items: list[dict[str, str]] = []
        errors: list[str] = []
        if isinstance(new_items, dict):
            items = new_items.get("items", []) if isinstance(new_items.get("items"), list) else []
            errors = new_items.get("errors", []) if isinstance(new_items.get("errors"), list) else []
        elif isinstance(new_items, list):
            items = new_items
        if not items:
            self._show_error("No word data generated.")
            return
        known = {item["word"] for item in self.words}
        merged = [item for item in items if item.get("word") not in known]
        if not merged:
            self.statusBar().showMessage("No new words were added.", 4000)
            return
        self.words.extend(merged)
        self._sort_words()
        save_words(self.words_json_path, self.words)
        self._refresh_word_list(select_word=merged[0]["word"])
        self._append_log(f"Added {len(merged)} words.")
        if errors:
            self.statusBar().showMessage(f"Added {len(merged)} words, {len(errors)} failed.", 7000)
            QMessageBox.warning(self, "Batch Generation Completed", "\n".join(errors[:10]))
            self._append_log(f"Batch generation finished with {len(errors)} errors.")
            return
        self.statusBar().showMessage(f"Added {len(merged)} words.", 5000)

    def _on_delete_word_clicked(self) -> None:
        selected_words = self._selected_words()
        if not selected_words:
            return

        count = len(selected_words)
        preview = ", ".join(selected_words[:8])
        if count > 8:
            preview += ", ..."
        reply = QMessageBox.question(
            self,
            "Delete Selected Words",
            f"Delete {count} selected word(s)?\n\n{preview}\n\nThis will remove JSON entries and audio files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted_count = self._delete_words(selected_words)
        self.statusBar().showMessage(f"Deleted {deleted_count} selected word(s).", 4000)
        self._append_log(f"Deleted {deleted_count} selected word(s).")

    def _delete_words(self, words: list[str]) -> int:
        requested = {word.strip().lower() for word in words if word.strip()}
        if not requested:
            return 0
        existing = {item["word"] for item in self.words}
        delete_set = requested & existing
        if not delete_set:
            return 0
        self.words = [item for item in self.words if item["word"] not in delete_set]
        save_words(self.words_json_path, self.words)
        for word in delete_set:
            delete_word_assets(self.audio_dir, word)
        self._refresh_word_list()
        return len(delete_set)

    def _on_save_word_clicked(self, edited_data: dict[str, str]) -> bool:
        current_word = self._current_selected_word()
        if not current_word:
            return False
        if not self._validate_word_data(edited_data):
            return False

        existing = self._find_word(edited_data["word"])
        if edited_data["word"] != current_word and existing is not None:
            self._show_error(f"Word '{edited_data['word']}' already exists.")
            return False

        for idx, item in enumerate(self.words):
            if item["word"] == current_word:
                self.words[idx] = edited_data
                break

        if edited_data["word"] != current_word:
            rename_word_assets(self.audio_dir, current_word, edited_data["word"])
        self._sort_words()
        save_words(self.words_json_path, self.words)
        self._refresh_word_list(select_word=edited_data["word"])
        self._update_audio_status(edited_data["word"])
        self.statusBar().showMessage("Word data saved.", 3000)
        return True

    def _on_generate_all_clicked(self) -> None:
        edited_data = self.editor.get_word_data()
        if not self._on_save_word_clicked(edited_data):
            return
        word = edited_data["word"]
        target = self._find_word(word)
        if not target:
            self._show_error(f"Word not found: {word}")
            return
        snapshot = dict(target)

        def task(progress: Callable[[int], None], log: Callable[[str], None]) -> dict[str, str]:
            log(f"Generating all for '{snapshot.get('word', '<unknown>')}'...")
            progress(15)
            result = self._generate_all_for_entry(snapshot)
            progress(100)
            return result

        self._start_task(
            status_text="Generating metadata and audio...",
            fn=task,
            on_success=lambda updated: self._finish_audio_generation(word, updated),
        )

    def _generate_all_for_entry(self, entry: dict[str, str]) -> dict[str, str]:
        prepared = dict(entry)
        if self.gpt_generator is not None:
            prepared = self.gpt_generator.ensure_metadata(prepared)
        self.tts_generator.generate_audio(prepared, self.audio_dir)
        return prepared

    def _finish_audio_generation(self, old_word: str, updated: object) -> None:
        if isinstance(updated, dict):
            new_word = str(updated.get("word", old_word)).strip().lower() or old_word
            for idx, item in enumerate(self.words):
                if item["word"] == old_word:
                    self.words[idx] = updated
                    break
            if old_word != new_word:
                rename_word_assets(self.audio_dir, old_word, new_word)
            self._sort_words()
            save_words(self.words_json_path, self.words)
            self._refresh_word_list(select_word=new_word)
            self._update_audio_status(new_word)
        self.statusBar().showMessage("Generate All completed.", 4000)
        self._append_log(f"Generate All completed for '{old_word}'.")

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
            self._show_error(f"Missing audio file: {path.name}")
            return
        self.audio_player.setSource(QUrl.fromLocalFile(str(path)))
        self.audio_player.play()
        self.statusBar().showMessage(f"Playing {path.name}", 3000)

    def _on_sync_to_anki_clicked(self) -> None:
        if not self.words:
            self._show_error("No words available to sync.")
            return

        snapshot = [dict(item) for item in self.words]

        def task(progress: Callable[[int], None], log: Callable[[str], None]) -> dict[str, object]:
            log("Syncing cards with Anki...")
            self.anki_api.check_connection()
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
                    metadata_provider = self.gpt_generator.ensure_metadata if self.gpt_generator is not None else None
                    repaired = repair_word_data(item, metadata_provider=metadata_provider)
                    repaired = self._generate_all_for_entry(repaired)
                except Exception as exc:
                    skipped += 1
                    errors.append(f"{word}: local prepare failed ({exc})")
                    repaired_words.append(item)
                    continue

                word = repaired["word"]
                if word in local_map:
                    skipped += 1
                    errors.append(f"{word}: duplicate local entry skipped")
                    continue
                local_map[word] = repaired
                repaired_words.append(repaired)
                progress(int((idx / max(1, total_prepare)) * 40))

            anki_map = self.anki_api.get_deck_word_to_note_ids(self.deck_name)
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
                log(f"Deleted {len(delete_note_ids)} note(s) from Anki.")

            create_payloads: list[dict[str, object]] = []
            create_words: list[str] = []
            update_payloads: list[dict[str, object]] = []
            media_files_to_upload: list[Path] = []

            for word in sorted(words_to_create):
                item = local_map[word]
                word_audio = word_audio_path(self.audio_dir, word)
                sentence_audio = sentence_audio_path(self.audio_dir, word)
                if not word_audio.exists() or not sentence_audio.exists():
                    skipped += 1
                    errors.append(f"{word}: missing audio file(s)")
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
                    errors.append(f"{word}: note missing during update")
                    continue
                item = local_map[word]
                word_audio = word_audio_path(self.audio_dir, word)
                sentence_audio = sentence_audio_path(self.audio_dir, word)
                if not word_audio.exists() or not sentence_audio.exists():
                    skipped += 1
                    errors.append(f"{word}: missing audio file(s)")
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

            upload_result = self.anki_api.upload_media_files_concurrently(media_files_to_upload, max_workers=8)
            progress(70)
            upload_failed = upload_result.get("failed", [])
            failed_media_names: set[str] = set()
            for row in upload_failed:
                # row format: "<filename>: <error>"
                failed_media_names.add(str(row).split(":", 1)[0].strip())
                errors.append(f"media upload failed ({row})")

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
                    errors.append(f"{word}: skipped due to failed media upload")
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
                        errors.append(f"{word}: create failed")
                log(f"Create batch done. requested={len(valid_create_payloads)} created={created}")

            valid_updates: list[dict[str, object]] = []
            for payload in update_payloads:
                word = str(payload.get("word", ""))
                word_filename = str(payload.get("audio_word_filename", ""))
                sentence_filename = str(payload.get("audio_sentence_filename", ""))
                if word_filename in failed_media_names or sentence_filename in failed_media_names:
                    skipped += 1
                    errors.append(f"{word}: skipped due to failed media upload")
                    continue
                valid_updates.append(payload)

            if valid_updates:
                update_result = self.anki_api.update_note_fields_multi(valid_updates)
                updated += int(update_result.get("updated", 0))
                failed_updates = int(update_result.get("failed", 0))
                if failed_updates > 0:
                    skipped += failed_updates
                    errors.append(f"{failed_updates} notes failed during batch update")
                log(
                    f"Update batch done. requested={len(valid_updates)} "
                    f"updated={int(update_result.get('updated', 0))} failed={failed_updates}"
                )

            progress(100)
            log(f"Sync done. Created={created} Updated={updated} Deleted={deleted} Skipped={skipped}")

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
        )

    def _finish_sync_to_anki(self, result: object) -> None:
        if not isinstance(result, dict):
            self._show_error("Unexpected sync result.")
            return
        words = result.get("words")
        if isinstance(words, list) and words:
            self.words = words
            self._sort_words()
            save_words(self.words_json_path, self.words)
            self._refresh_word_list()
        created = int(result.get("created", 0))
        updated = int(result.get("updated", 0))
        deleted = int(result.get("deleted", 0))
        skipped = int(result.get("skipped", 0))
        errors = result.get("errors", [])
        self.statusBar().showMessage(
            f"Sync completed. Created: {created}, Updated: {updated}, Deleted: {deleted}, Skipped: {skipped}",
            7000,
        )
        self._append_log(
            f"Sync completed. Created={created} Updated={updated} Deleted={deleted} Skipped={skipped}"
        )
        msg = f"Created: {created}\nUpdated: {updated}\nDeleted: {deleted}\nSkipped: {skipped}"
        if isinstance(errors, list) and errors:
            msg += "\n\nSome entries failed:\n" + "\n".join(errors[:8])
        QMessageBox.information(self, "Sync Completed", msg)

    def _validate_word_data(self, data: dict[str, str]) -> bool:
        required = ["word"]
        for field in required:
            if not data.get(field, "").strip():
                self._show_error(f"Field '{field}' cannot be empty.")
                return False
        return True

    def _start_task(
        self,
        status_text: str,
        fn: Callable[[Callable[[int], None], Callable[[str], None]], object],
        on_success: Callable[[object], None],
    ) -> None:
        worker = TaskThread(fn)
        self._workers.add(worker)
        self._set_busy(True, status_text)
        self._set_progress(0)
        self.progress_label.setText(f"{status_text} (0%)")
        self._append_log(status_text)

        def cleanup() -> None:
            self._set_busy(False, "Ready")
            self.progress_label.setText("Ready")
            self._workers.discard(worker)
            worker.deleteLater()

        def on_ok(result: object) -> None:
            try:
                on_success(result)
            finally:
                cleanup()

        def on_fail(message: str) -> None:
            try:
                self._show_error(message)
            finally:
                cleanup()

        worker.succeeded.connect(on_ok)
        worker.failed.connect(on_fail)
        worker.progress_changed.connect(self._set_progress)
        worker.log_message.connect(self._append_log)
        worker.start()

    def _set_busy(self, busy: bool, status: str) -> None:
        selected_count = len(self._selected_words())
        has_selection = selected_count > 0
        has_single_selection = selected_count == 1
        self.add_button.setEnabled(not busy)
        self.generate_all_button.setEnabled((not busy) and has_single_selection)
        self.sync_button.setEnabled(not busy)
        self.search_input.setEnabled(not busy)
        self.word_list.setEnabled(not busy)
        self.delete_button.setEnabled((not busy) and has_selection)
        self.editor.set_actions_enabled((not busy) and has_single_selection)
        self.statusBar().showMessage(status)
        if not busy:
            self.progress_label.setText("Ready")

    @staticmethod
    def _has_missing_metadata(item: dict[str, str]) -> bool:
        return any(not str(item.get(field, "")).strip() for field in ("phonetic", "part_of_speech", "example", "analysis"))

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
                f"Audio check: word missing {missing_word_audio}, sentence missing {missing_sentence_audio}",
                7000,
            )

    def _update_audio_status(self, word: str) -> None:
        word_exists, sentence_exists = check_audio_exists(self.audio_dir, word)
        self.editor.word_audio_status.setText(f"Word Audio {'✓' if word_exists else '✗'}")
        self.editor.sentence_audio_status.setText(f"Sentence Audio {'✓' if sentence_exists else '✗'}")

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#AppRoot {
                background-color: #111318;
                color: #E5E7EB;
                font-size: 12px;
            }
            QWidget#Panel {
                background-color: #171A21;
                border: 1px solid #2A2F3A;
                border-radius: 10px;
            }
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
            QLineEdit, QTextEdit {
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
        self.statusBar().showMessage(message, 5000)
        self._append_log(message, level="ERROR")
        QMessageBox.critical(self, "Error", message)

    def _set_progress(self, percent: int) -> None:
        safe = max(0, min(100, int(percent)))
        self.progress_bar.setValue(safe)
        text = self.progress_label.text().split(" (", 1)[0]
        if text and text != "Ready":
            self.progress_label.setText(f"{text} ({safe}%)")

    def _append_log(self, message: str, level: str = "INFO") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.appendPlainText(f"[{stamp}] {level.upper()} {message}")
