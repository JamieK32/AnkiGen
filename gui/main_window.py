from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QShortcut, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
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

from gui.settings_dialog import SettingsDialog
from gui.workflow_actions import WorkflowActions
from services.vocabulary_workflow import entry_status, sync_ready
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
    repair_word_data,
    rename_word_assets,
    save_words,
    sentence_audio_path,
    word_audio_path,
)
from utils.settings_manager import load_app_settings, sanitize_app_settings, save_app_settings


class WordListDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect.adjusted(4, 2, -4, -2)
        painter.fillRect(rect, QColor("#314C68" if selected else "#171A21"))
        painter.setClipRect(rect)
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#E5E7EB"))
        text_rect = rect.adjusted(10, 5, -10, 0)
        text_rect.setHeight(painter.fontMetrics().lineSpacing() * 2)
        painter.drawText(text_rect,
                         Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop,
                         str(index.data(Qt.ItemDataRole.UserRole) or ""))
        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor("#9CA3AF"))
        painter.drawText(rect.adjusted(10, 54, -10, -18), Qt.AlignmentFlag.AlignLeft,
                         str(index.data(Qt.ItemDataRole.UserRole + 1) or ""))
        painter.drawText(rect.adjusted(10, 75, -10, 0), Qt.AlignmentFlag.AlignLeft,
                         str(index.data(Qt.ItemDataRole.UserRole + 2) or ""))
        status = str(index.data(Qt.ItemDataRole.UserRole + 3) or "")
        painter.setPen(QColor("#F3A6A6" if status == "失败" else "#A5CDB2"))
        painter.drawText(rect.adjusted(10, 75, -10, 0), Qt.AlignmentFlag.AlignRight, status)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(280, 100)


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


class MainWindow(WorkflowActions, QMainWindow):
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
        self.generate_all_button = QPushButton("补全当前搭配")
        self.sync_button = QPushButton("Sync to Anki")
        self.settings_button = QPushButton("Settings")
        self.article_button = QPushButton("从文章提取")
        self.retry_button = QPushButton("仅重试失败项")
        self.audio_button = QPushButton("音频操作")
        menu = QMenu(self.audio_button)
        menu.addAction("仅补缺失音频", lambda: self._generate_selected('audio_missing'))
        menu.addAction("重新生成音频", lambda: self._generate_selected('audio_all'))
        self.audio_button.setMenu(menu)
        self.anki_status = QPushButton("Anki：未检测")
        self.anki_status.setToolTip("点击检测 AnkiConnect；请先启动 Anki 并安装 AnkiConnect 插件")
        self.anki_status.clicked.connect(self._check_anki_connection)
        self.article_button.clicked.connect(self._open_article)
        self.retry_button.clicked.connect(self._retry_failed)
        self.generate_all_button.setObjectName("PrimaryAction")
        for btn in (self.add_button, self.sync_button, self.settings_button):
            btn.setObjectName("SecondaryToolbarAction")
        for btn in (self.add_button, self.article_button, self.generate_all_button, self.audio_button, self.retry_button, self.sync_button, self.anki_status, self.settings_button):
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
        self.word_list.setMinimumWidth(330)
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
        self.progress_bar.hide()
        self.progress_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.progress_label = QLabel("Ready")
        self.progress_label.setStyleSheet("QLabel { color: #9CA3AF; font-size: 12px; font-weight: 500; }")

        self.log_title = QLabel("Activity Log")
        self.log_title.setStyleSheet("QLabel { color: #9CA3AF; font-weight: 600; padding-left: 2px; }")
        self.clear_log_button = QPushButton("Clear Log")
        self.clear_log_button.setToolTip("Clear displayed logs without interrupting the current task")
        self.clear_log_button.clicked.connect(self._clear_log)
        log_header = QHBoxLayout()
        self.log_toggle = QPushButton("收起日志")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setChecked(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        log_header.addWidget(self.log_title)
        log_header.addWidget(self.log_toggle)
        log_header.addStretch(1)
        log_header.addWidget(self.clear_log_button)

        self.log_widget = QPlainTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setMaximumBlockCount(500)
        self.log_widget.setPlaceholderText("Runtime logs...")
        self.log_widget.setMinimumHeight(130)
        self.log_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.log_widget.setToolTip("Ctrl+A: select all logs; Ctrl+C: copy selected logs")
        self.log_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.log_widget.customContextMenuRequested.connect(self._show_log_context_menu)
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
        main_layout.addLayout(log_header, 0)
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
        self.add_button.clicked.connect(self._open_import)
        self.generate_all_button.clicked.connect(self._on_generate_all_from_toolbar)
        self.delete_button.clicked.connect(self._on_delete_word_clicked)
        self.sync_button.clicked.connect(self._preview_sync)
        self.settings_button.clicked.connect(self._on_open_settings_clicked)

        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(lambda: self._on_save_word_clicked(self.editor.get_word_data()) if not self._task_busy else None)
        for field in (self.editor.word_edit, self.editor.phonetic_edit, self.editor.part_of_speech_edit,
                      self.editor.translation_edit, self.editor.example_edit, self.editor.analysis_edit):
            field.textChanged.connect(self._update_dirty_indicator)
        self.editor.save_requested.connect(self._on_save_word_clicked)
        self.editor.regenerate_audio_requested.connect(lambda _: self._on_generate_all_clicked())
        self.editor.play_word_audio_requested.connect(self._on_play_word_audio_clicked)
        self.editor.play_sentence_audio_requested.connect(self._on_play_sentence_audio_clicked)

    def _load_words_or_show_error(self) -> None:
        try:
            self.words = load_words(self.words_json_path)
            if self._ensure_imported_at_fields():
                save_words(self.words_json_path, self.words)
            self._sort_words()
            self._refresh_word_list()
            # Incomplete and failed entries stay visible for explicit completion/retry.
            self._summarize_audio_health()
        except Exception as exc:
            self._show_error(f"Failed to load words.json: {exc}")
            self.words = []
            self._refresh_word_list()

    def _sort_words(self) -> None:
        self.words.sort(
            key=lambda item: self._normalize_timestamp_display(item.get("imported_at", "")),
            reverse=True,
        )

    def _refresh_word_list(self, select_word: str | None = None) -> None:
        query = self.search_input.text().strip().lower()
        if not self._confirm_unsaved():
            return
        current_word = select_word or self._current_selected_word()
        self.word_list.blockSignals(True)
        self.word_list.clear()

        filtered = []
        for item in self.words:
            if query and query not in item["word"] and query not in item["translation"]:
                continue
            filtered.append(item)
            phonetic = item.get("phonetic", "").strip()
            imported_at = self._normalize_timestamp_display(item.get("imported_at", ""))
            qitem = QListWidgetItem(item["word"])
            qitem.setData(Qt.ItemDataRole.UserRole, item["word"])
            qitem.setData(Qt.ItemDataRole.UserRole + 1, phonetic)
            qitem.setData(Qt.ItemDataRole.UserRole + 2, imported_at)
            status = '生成中' if item['word'] in self._generating_words else entry_status(item, self.audio_dir)
            qitem.setData(Qt.ItemDataRole.UserRole + 3, status)
            qitem.setToolTip(item['word'] + '\n' + item.get('translation', '') + '\n' + status + '\n' + item.get('generation_error', ''))
            self.word_list.addItem(qitem)

        self.word_count_label.setText(f"Words: {len(self.words)} (Showing: {len(filtered)})")

        if self.word_list.count() == 0:
            self.word_list.blockSignals(False)
            self._editor_baseline = None
            self._editing_word = None
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
        if self.word_list.currentRow() < 0:
            self.word_list.setCurrentRow(0)
        self.word_list.blockSignals(False)
        self._on_word_selected()

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
        if not self._confirm_unsaved():
            self.word_list.blockSignals(True)
            self.word_list.clearSelection()
            for i in range(self.word_list.count()):
                if self.word_list.item(i).data(Qt.ItemDataRole.UserRole) == self._editing_word:
                    self.word_list.setCurrentRow(i)
                    break
            self.word_list.blockSignals(False)
            return
        self._editor_baseline = None
        self._editing_word = None
        selected_words = self._selected_words()
        if not selected_words:
            self.editor.clear()
            self.editor.set_interaction_mode(can_edit=False, can_play_audio=False)
            self.delete_button.setEnabled(False)
            self.generate_all_button.setEnabled(False)
            return

        if len(selected_words) > 1:
            self.editor.clear()
            self.editor.set_interaction_mode(can_edit=False, can_play_audio=False)
            self.delete_button.setEnabled(not self._task_busy)
            self.generate_all_button.setEnabled(False)
            return

        word = selected_words[0]
        item = self._find_word(word)
        if not item:
            return
        self.editor.set_word_data(item, False, False)
        self._editing_word = word
        self._editor_baseline = self.editor.get_word_data()
        self._update_dirty_indicator()
        self._update_audio_status(word)
        if self._task_busy:
            self.delete_button.setEnabled(False)
            self.generate_all_button.setEnabled(False)
            self.editor.set_interaction_mode(
                can_edit=False,
                can_play_audio=self._busy_allows_browse_audio,
            )
            return

        self.delete_button.setEnabled(True)
        self.editor.set_interaction_mode(can_edit=True, can_play_audio=True)
        self.generate_all_button.setEnabled(True)

    def _on_generate_all_from_toolbar(self) -> None:
        if self._current_selected_word() is None:
            self._show_error("Please select a word first.")
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
            self._show_error(f"Word '{edited_data['word']}' already exists.")
            return False

        for idx, item in enumerate(self.words):
            if item["word"] == current_word:
                self.words[idx] = edited_data
                break

        if edited_data["word"] != current_word:
            rename_word_assets(self.audio_dir, current_word, edited_data["word"])
            for row in range(self.word_list.count()):
                listed = self.word_list.item(row)
                if listed.data(Qt.ItemDataRole.UserRole) == current_word:
                    listed.setData(Qt.ItemDataRole.UserRole, edited_data['word'])
        self._editing_word = edited_data['word']
        self._editor_baseline = self.editor.get_word_data()
        self._update_dirty_indicator()
        self._sort_words()
        save_words(self.words_json_path, self.words)
        if refresh_ui:
            self._refresh_word_list(select_word=edited_data["word"])
            self._update_audio_status(edited_data["word"])
            self.statusBar().showMessage("Word data saved.", 3000)
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
            self._show_error(f"Missing audio file: {path.name}")
            return
        self.audio_player.setSource(QUrl.fromLocalFile(str(path)))
        self.audio_player.play()
        self.statusBar().showMessage(f"Playing {path.name}", 3000)

    def _on_sync_to_anki_clicked(self, approved_remote) -> None:

        snapshot = [dict(item) for item in self.words]

        def task(progress: Callable[[int], None], log: Callable[[str], None]) -> dict[str, object]:
            log("Syncing local cards to Anki without regenerating AI/TTS...")
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
                log(f"Deleted {len(delete_note_ids)} note(s) from Anki.")

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
                    errors.append(f"{word}: missing translation/example or audio file(s)")
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
                if not sync_ready(item, self.audio_dir):
                    skipped += 1
                    errors.append(f"{word}: missing translation/example or audio file(s)")
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
            allow_browse_audio=True,
        )

    def _finish_sync_to_anki(self, result: object) -> None:
        if not isinstance(result, dict):
            self._show_error("Unexpected sync result.")
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
        allow_browse_audio: bool = False,
    ) -> None:
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
            self._set_busy(False, "Ready")
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
            for entry in self.words:
                if entry['word'] in self._generating_words:
                    entry['generation_error'] = message
            self._generating_words.clear()
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

    def _set_busy(self, busy: bool, status: str, allow_browse_audio: bool = False) -> None:
        self._task_busy = busy
        # Native indeterminate animation keeps moving during network waits;
        # the adjacent label retains the real reported percentage.
        self.progress_bar.setVisible(busy)
        self.progress_bar.setRange(0, 0 if busy else 100)
        self.progress_bar.setTextVisible(not busy)
        if not busy:
            self.progress_bar.setValue(self._last_progress)
        self._busy_allows_browse_audio = bool(busy and allow_browse_audio)
        selected_count = len(self._selected_words())
        has_selection = selected_count > 0
        has_single_selection = selected_count == 1
        has_editor_word = bool(self.editor.word_edit.text().strip())
        self.add_button.setEnabled(not busy)
        self.generate_all_button.setEnabled((not busy) and has_single_selection)
        self.sync_button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        for button in (self.article_button, self.retry_button, self.audio_button, self.anki_status):
            button.setEnabled(not busy)
        self.delete_button.setEnabled((not busy) and has_selection)

        if self._busy_allows_browse_audio:
            # Keep browsing and playback available during long sync tasks.
            self.search_input.setEnabled(True)
            self.word_list.setEnabled(True)
            self.editor.set_interaction_mode(
                can_edit=False,
                can_play_audio=has_single_selection or has_editor_word,
            )
        else:
            self.search_input.setEnabled(not busy)
            self.word_list.setEnabled(not busy)
            self.editor.set_interaction_mode(
                can_edit=(not busy) and has_single_selection,
                can_play_audio=(not busy) and has_single_selection,
            )

        self.statusBar().showMessage(status)
        if not busy:
            self.progress_label.setText("Ready")

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
        self.statusBar().showMessage("Settings saved and applied.", 5000)
        self._append_log(
            "Settings updated. "
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
            QMainWindow,
    QMenu, QWidget#AppRoot {
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
        self.log_toggle.setChecked(True)
        self.statusBar().showMessage(message, 5000)
        self._append_log(message, level="ERROR")
        QMessageBox.critical(self, "Error", message)

    def _set_progress(self, percent: int) -> None:
        safe = max(0, min(100, int(percent)))
        self._last_progress = safe
        if not self._task_busy:
            self.progress_bar.setValue(safe)
        text = self.progress_label.text().split(" (", 1)[0]
        if text and text != "Ready":
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
        clear_action = menu.addAction("Clear Log")
        clear_action.triggered.connect(self._clear_log)
        menu.exec(self.log_widget.mapToGlobal(position))
        menu.deleteLater()

    def _append_log(self, message: str, level: str = "INFO") -> None:
        if level.upper() == "ERROR":
            self.log_toggle.setChecked(True)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.appendPlainText(f"[{stamp}] {level.upper()} {message}")
