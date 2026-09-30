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
        self.example_edit.setPlaceholderText("英文例句\n中文翻译")
        self.analysis_edit = QTextEdit()
        self.analysis_edit.setPlaceholderText("搭配含义、用法或记忆提示")

        self.setObjectName('Editor')
        title = QLabel('搭配详情')
        title.setObjectName('PageTitle')
        layout.addWidget(title)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.addRow('英文搭配', self.word_edit)
        form.addRow('中文释义 / 默写提示', self.translation_edit)
        form.addRow('例句 · 第一行英文，第二行中文', self.example_edit)
        form.addRow('搭配解析', self.analysis_edit)
        self.example_edit.setFixedHeight(100)
        self.analysis_edit.setFixedHeight(76)
        layout.addLayout(form)
        auxiliary = QLabel('辅助信息（选填）')
        auxiliary.setObjectName('Muted')
        layout.addWidget(auxiliary)
        extra = QFormLayout()
        extra.addRow('音标', self.phonetic_edit)
        extra.addRow('词性', self.part_of_speech_edit)
        layout.addLayout(extra)
        self.source_button = QPushButton("查看来源原文")
        self.source_button.setCheckable(True)
        self.source_view = QTextEdit()
        self.source_view.setReadOnly(True)
        self.source_view.setMaximumHeight(100)
        self.source_view.hide()
        self.source_button.toggled.connect(self.source_view.setVisible)
        layout.addWidget(self.source_button)
        layout.addWidget(self.source_view)

        self.word_audio_status = QLabel("搭配音频")
        self.sentence_audio_status = QLabel("例句音频")
        self.word_audio_status.hide()
        self.sentence_audio_status.hide()

        play_layout = QHBoxLayout()
        self.play_word_button = QPushButton("播放搭配")
        self.play_sentence_button = QPushButton("播放例句")
        play_layout.addWidget(self.play_word_button)
        play_layout.addWidget(self.play_sentence_button)
        layout.addLayout(play_layout)

        action_layout = QHBoxLayout()
        self.regenerate_audio_button = QPushButton("重新生成音频")
        self.save_button = QPushButton("保存修改")
        action_layout.addWidget(self.regenerate_audio_button)
        action_layout.addWidget(self.save_button)
        layout.addLayout(action_layout)

        layout.addStretch(1)

        self.save_button.clicked.connect(self._emit_save)
        self.regenerate_audio_button.clicked.connect(self._emit_regenerate_audio)
        self.play_word_button.clicked.connect(self._emit_play_word_audio)
        self.play_sentence_button.clicked.connect(self._emit_play_sentence_audio)

    def clear(self) -> None:
        self.source_button.setChecked(False)
        self.source_button.setEnabled(False)
        self.source_view.clear()
        self.word_edit.clear()
        self.phonetic_edit.clear()
        self.part_of_speech_edit.clear()
        self.translation_edit.clear()
        self.example_edit.clear()
        self.analysis_edit.clear()
        self.set_audio_status(False, False)

    def set_word_data(self, data: dict[str, str], word_audio_exists: bool, sentence_audio_exists: bool) -> None:
        self.source_view.setPlainText(data.get("source_text", ""))
        self.source_button.setChecked(False)
        self.source_button.setEnabled(bool(data.get("source_text")))
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
        self.word_audio_status.setText('搭配音频：' + ('已生成' if word_exists else '缺失'))
        self.sentence_audio_status.setText('例句音频：' + ('已生成' if sentence_exists else '缺失'))
        self.play_word_button.setText('▶ 播放搭配' if word_exists else '搭配音频缺失')
        self.play_sentence_button.setText('▶ 播放例句' if sentence_exists else '例句音频缺失')
        self._word_audio_exists = word_exists
        self._sentence_audio_exists = sentence_exists
        self.play_word_button.setEnabled(word_exists)
        self.play_sentence_button.setEnabled(sentence_exists)

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
        self.play_word_button.setEnabled(can_play_audio and getattr(self, "_word_audio_exists", False))
        self.play_sentence_button.setEnabled(can_play_audio and getattr(self, "_sentence_audio_exists", False))

    def _emit_save(self) -> None:
        self.save_requested.emit(self.get_word_data())

    def _emit_regenerate_audio(self) -> None:
        self.regenerate_audio_requested.emit(self.get_word_data())

    def _emit_play_word_audio(self) -> None:
        self.play_word_audio_requested.emit(self.word_edit.text().strip().lower())

    def _emit_play_sentence_audio(self) -> None:
        self.play_sentence_audio_requested.emit(self.word_edit.text().strip().lower())
