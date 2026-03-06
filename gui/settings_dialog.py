from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class SettingsDialog(QDialog):
    def __init__(self, settings: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(560, 420)
        self._settings = dict(settings)
        self._build_ui()
        self._fill_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("OpenAI-compatible API key")

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://yunwu.ai/v1")
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(["gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"])

        self.anki_url_edit = QLineEdit()
        self.anki_url_edit.setPlaceholderText("http://localhost:8765")
        self.deck_name_edit = QLineEdit()
        self.model_name_edit = QLineEdit()

        self.tts_voice_edit = QLineEdit()
        self.tts_voice_edit.setPlaceholderText("en-US-AriaNeural")

        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(1, 200)

        self.tts_workers_spin = QSpinBox()
        self.tts_workers_spin.setRange(1, 32)

        self.anki_workers_spin = QSpinBox()
        self.anki_workers_spin.setRange(1, 32)

        form.addRow("API Key", self.api_key_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("Model", self.model_combo)
        form.addRow("AnkiConnect URL", self.anki_url_edit)
        form.addRow("Deck Name", self.deck_name_edit)
        form.addRow("Model Name", self.model_name_edit)
        form.addRow("TTS Voice", self.tts_voice_edit)
        form.addRow("Metadata Batch Size", self.batch_size_spin)
        form.addRow("TTS Workers", self.tts_workers_spin)
        form.addRow("Anki Upload Workers", self.anki_workers_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _fill_values(self) -> None:
        self.api_key_edit.setText(str(self._settings.get("api_key", "")))
        self.base_url_edit.setText(str(self._settings.get("base_url", "")))
        self.model_combo.setCurrentText(str(self._settings.get("model", "")))
        self.anki_url_edit.setText(str(self._settings.get("anki_url", "")))
        self.deck_name_edit.setText(str(self._settings.get("deck_name", "")))
        self.model_name_edit.setText(str(self._settings.get("model_name", "")))
        self.tts_voice_edit.setText(str(self._settings.get("tts_voice", "")))
        self.batch_size_spin.setValue(int(self._settings.get("metadata_batch_size", 20)))
        self.tts_workers_spin.setValue(int(self._settings.get("tts_max_workers", 5)))
        self.anki_workers_spin.setValue(int(self._settings.get("anki_upload_workers", 8)))

    def get_settings(self) -> dict[str, Any]:
        return {
            "api_key": self.api_key_edit.text().strip(),
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_combo.currentText().strip(),
            "anki_url": self.anki_url_edit.text().strip(),
            "deck_name": self.deck_name_edit.text().strip(),
            "model_name": self.model_name_edit.text().strip(),
            "tts_voice": self.tts_voice_edit.text().strip(),
            "metadata_batch_size": int(self.batch_size_spin.value()),
            "tts_max_workers": int(self.tts_workers_spin.value()),
            "anki_upload_workers": int(self.anki_workers_spin.value()),
        }
