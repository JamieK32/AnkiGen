from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class WordEditor(QWidget):
    save_requested = Signal(dict)
    regenerate_audio_requested = Signal(dict)
    play_word_audio_requested = Signal(str)
    play_sentence_audio_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(14)
        form.setLabelAlignment(form.labelAlignment())

        self.word_edit = QLineEdit()
        self.phonetic_edit = QLineEdit()
        self.part_of_speech_edit = QLineEdit()
        self.translation_edit = QLineEdit()
        self.example_edit = QTextEdit()
        self.example_edit.setPlaceholderText("Example sentence")
        self.analysis_edit = QTextEdit()
        self.analysis_edit.setPlaceholderText("Chinese analysis")

        form.addRow("Word", self.word_edit)
        form.addRow("Phonetic", self.phonetic_edit)
        form.addRow("Part of Speech", self.part_of_speech_edit)
        form.addRow("Translation", self.translation_edit)
        form.addRow("Example", self.example_edit)
        form.addRow("Analysis", self.analysis_edit)

        for field in (
            self.word_edit,
            self.phonetic_edit,
            self.part_of_speech_edit,
            self.translation_edit,
            self.example_edit,
            self.analysis_edit,
        ):
            label = form.labelForField(field)
            if label is not None:
                label.setStyleSheet("QLabel { color: #9CA3AF; font-size: 12px; font-weight: 500; }")

        layout.addLayout(form)

        self.word_audio_status = QLabel("Word audio: -")
        self.sentence_audio_status = QLabel("Sentence audio: -")
        layout.addWidget(self.word_audio_status)
        layout.addWidget(self.sentence_audio_status)

        play_layout = QHBoxLayout()
        self.play_word_button = QPushButton("Play Word Audio")
        self.play_sentence_button = QPushButton("Play Sentence Audio")
        play_layout.addWidget(self.play_word_button)
        play_layout.addWidget(self.play_sentence_button)
        layout.addLayout(play_layout)

        action_layout = QHBoxLayout()
        self.regenerate_audio_button = QPushButton("Regenerate Audio")
        self.save_button = QPushButton("Save Changes")
        action_layout.addWidget(self.regenerate_audio_button)
        action_layout.addWidget(self.save_button)
        layout.addLayout(action_layout)

        layout.addStretch(1)

        self.save_button.clicked.connect(self._emit_save)
        self.regenerate_audio_button.clicked.connect(self._emit_regenerate_audio)
        self.play_word_button.clicked.connect(self._emit_play_word_audio)
        self.play_sentence_button.clicked.connect(self._emit_play_sentence_audio)

    def clear(self) -> None:
        self.word_edit.clear()
        self.phonetic_edit.clear()
        self.part_of_speech_edit.clear()
        self.translation_edit.clear()
        self.example_edit.clear()
        self.analysis_edit.clear()
        self.set_audio_status(False, False)

    def set_word_data(self, data: dict[str, str], word_audio_exists: bool, sentence_audio_exists: bool) -> None:
        self.word_edit.setText(data.get("word", ""))
        self.phonetic_edit.setText(data.get("phonetic", ""))
        self.part_of_speech_edit.setText(data.get("part_of_speech", ""))
        self.translation_edit.setText(data.get("translation", ""))
        self.example_edit.setPlainText(data.get("example", ""))
        self.analysis_edit.setPlainText(data.get("analysis", ""))
        self.set_audio_status(word_audio_exists, sentence_audio_exists)

    def get_word_data(self) -> dict[str, str]:
        return {
            "word": self.word_edit.text().strip().lower(),
            "phonetic": self.phonetic_edit.text().strip(),
            "part_of_speech": self.part_of_speech_edit.text().strip(),
            "translation": self.translation_edit.text().strip(),
            "example": self.example_edit.toPlainText().strip(),
            "analysis": self.analysis_edit.toPlainText().strip(),
        }

    def set_audio_status(self, word_exists: bool, sentence_exists: bool) -> None:
        self.word_audio_status.setText(f"Word audio: {'Exists' if word_exists else 'Missing'}")
        self.sentence_audio_status.setText(f"Sentence audio: {'Exists' if sentence_exists else 'Missing'}")

    def set_actions_enabled(self, enabled: bool) -> None:
        for widget in (
            self.word_edit,
            self.phonetic_edit,
            self.part_of_speech_edit,
            self.translation_edit,
            self.example_edit,
            self.analysis_edit,
            self.play_word_button,
            self.play_sentence_button,
            self.regenerate_audio_button,
            self.save_button,
        ):
            widget.setEnabled(enabled)

    def set_interaction_mode(self, can_edit: bool, can_play_audio: bool) -> None:
        for widget in (
            self.word_edit,
            self.phonetic_edit,
            self.part_of_speech_edit,
            self.translation_edit,
            self.example_edit,
            self.analysis_edit,
            self.regenerate_audio_button,
            self.save_button,
        ):
            widget.setEnabled(can_edit)
        self.play_word_button.setEnabled(can_play_audio)
        self.play_sentence_button.setEnabled(can_play_audio)

    def _emit_save(self) -> None:
        self.save_requested.emit(self.get_word_data())

    def _emit_regenerate_audio(self) -> None:
        self.regenerate_audio_requested.emit(self.get_word_data())

    def _emit_play_word_audio(self) -> None:
        self.play_word_audio_requested.emit(self.word_edit.text().strip().lower())

    def _emit_play_sentence_audio(self) -> None:
        self.play_sentence_audio_requested.emit(self.word_edit.text().strip().lower())
