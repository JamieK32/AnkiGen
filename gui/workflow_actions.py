from PySide6.QtWidgets import QDialog, QMessageBox

from gui.workflow_dialogs import ImportDialog
from services.gpt_generator import GPTGenerator
from services.vocabulary_workflow import generate_entries
from utils.file_manager import repair_word_data, save_words


class WorkflowActions:
    def _open_import(self):
        if not self._confirm_unsaved():
            return
        dialog = ImportDialog([x['word'] for x in self.words], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._import_entries(dialog.selected_entries())

    def _import_entries(self, entries):
        known = {x['word'] for x in self.words}
        new = [repair_word_data(x) for x in entries if x['word'] not in known]
        if not new:
            return
        for item in new:
            item['imported_at'] = self._now_timestamp()
        self.words.extend(new)
        self._sort_words()
        save_words(self.words_json_path, self.words)
        self._refresh_word_list(select_word=new[0]['word'])
        self._run_generation(new)

    def _run_generation(self, entries, mode='complete'):
        if not entries or self._task_busy:
            return
        snapshot = [dict(x) for x in entries]
        self._generating_words = {x['word'] for x in snapshot}
        key, base, model = self.api_key, self.base_url, self.model
        tts, audio_dir, batch_size = self.tts_generator, self.audio_dir, self.metadata_batch_size

        def factory(source):
            return GPTGenerator(key, base, model, source_text=source) if key else None

        def task(progress, log):
            return generate_entries(snapshot, factory, tts, audio_dir, batch_size, mode, progress, log)

        def finished(items):
            self._generating_words.clear()
            updates = {x['word']: x for x in items}
            self.words = [updates.get(x['word'], x) for x in self.words]
            save_words(self.words_json_path, self.words)
            self._refresh_word_list()
            failed = sum(bool(x.get('generation_error')) for x in items)
            summary = f'完成 {len(items) - failed} 条' + (f'，失败 {failed} 条（可仅重试失败项）' if failed else '')
            self.progress_label.setText(summary)
            self._append_log(summary, 'ERROR' if failed else 'INFO')
            self.statusBar().showMessage(summary, 10000)

        self._start_task(f'正在处理 {len(snapshot)} 条搭配…', task, finished, allow_browse_audio=True)
        self._refresh_word_list()

    def _generate_selected(self, mode='complete'):
        if not self._confirm_unsaved():
            return
        selected = set(self._selected_words())
        entries = [x for x in self.words if x['word'] in selected]
        if not entries:
            self.statusBar().showMessage('请先选择搭配。', 4000)
            return
        self._run_generation(entries, mode)

    def _retry_failed(self):
        if self._confirm_unsaved():
            selected = set(self._selected_words())
            failed = [x for x in self.words if x['word'] in selected and x.get('generation_error')]
            if not failed:
                self.statusBar().showMessage('没有失败条目。', 4000)
            else:
                self._run_generation(failed, 'retry')

    def _check_anki_connection(self):
        self.anki_status.setText('Anki：检测中…')

        def task(progress, log):
            try:
                self.anki_api.check_connection()
                return True
            except Exception as exc:
                log(str(exc))
                return False

        self._start_task('检测 Anki 连接…', task, lambda ok: self.anki_status.setText(
            'Anki：已连接' if ok else 'Anki：未连接'), allow_browse_audio=True)

    def _confirm_unsaved(self):
        if not getattr(self, '_editor_baseline', None) or self.editor.get_word_data() == self._editor_baseline:
            return True
        answer = QMessageBox.question(self, '未保存的修改', '保存当前搭配的修改？',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._on_save_word_clicked(self.editor.get_word_data(), refresh_ui=False)
        original = self._find_word(self._editing_word)
        if original:
            self._editor_baseline = None
            self.editor.set_word_data(original, False, False)
            self._update_audio_status(original['word'])
        self._editor_baseline = self.editor.get_word_data()
        self._update_dirty_indicator()
        return True

    def _update_dirty_indicator(self):
        dirty = bool(getattr(self, '_editor_baseline', None)) and self.editor.get_word_data() != self._editor_baseline
        self.editor.save_button.setText('保存修改 *' if dirty else '已保存')
        self.editor.save_button.setEnabled(dirty and not self._task_busy)
        self.editor.save_button.setObjectName('PrimaryAction' if dirty else '')
        self.editor.save_button.style().unpolish(self.editor.save_button)
        self.editor.save_button.style().polish(self.editor.save_button)

    def closeEvent(self, event):
        if self._task_busy or any(worker.isRunning() for worker in self._workers):
            QMessageBox.information(self, '任务进行中', '请等待当前任务结束后再关闭，以免丢失生成结果。')
            event.ignore()
        elif not self._confirm_unsaved():
            event.ignore()
        else:
            self._save_ui_state()
            event.accept()
