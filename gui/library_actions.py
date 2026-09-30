from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QFileDialog, QDialog, QMessageBox

from gui.workflow_dialogs import ImportDialog
from services.library_io import read_exchange, export_exchange, merge_entries, create_backup, inspect_backup, restore_backup
from utils.file_manager import save_words, load_words


class LibraryActions:
    def _import_library_file(self):
        if not self._confirm_unsaved():
            return
        filename, _ = QFileDialog.getOpenFileName(self, '导入词汇库', '', '词汇文件 (*.xlsx *.csv *.txt)')
        if not filename:
            return

        def finished(entries):
            if not entries:
                self.statusBar().showMessage('文件没有词条。', 5000)
                return
            known = {x['word'] for x in self.words}
            duplicates = sum(x['word'] in known for x in entries)
            replace = False
            if duplicates:
                question = QMessageBox(self)
                question.setWindowTitle('重复词条处理')
                question.setText(f'文件含 {duplicates} 条已有搭配。\n覆盖会替换词条字段（包含空字段），不会自动调用 AI。')
                skip = question.addButton('跳过已有', QMessageBox.ButtonRole.AcceptRole)
                overwrite = question.addButton('覆盖已有', QMessageBox.ButtonRole.DestructiveRole)
                question.addButton('取消', QMessageBox.ButtonRole.RejectRole)
                question.setDefaultButton(skip)
                question.exec()
                if question.clickedButton() not in (skip, overwrite):
                    return
                replace = question.clickedButton() == overwrite
            preview = ImportDialog([] if replace else known, self, candidates=entries)
            preview.setWindowTitle('文件导入预览')
            preview.buttons.button(preview.buttons.StandardButton.Ok).setText('确认导入（不自动生成）')
            if replace:
                preview.summary.setText('已选择覆盖模式：勾选项将新增或覆盖同名词条，未勾选的保持不变。')
            if preview.exec() != QDialog.DialogCode.Accepted:
                return
            incoming = preview.selected_entries()
            snapshot = [dict(x) for x in self.words]

            def task(progress, log):
                if replace:
                    backup = self.project_root / 'backups' / ('before-import-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.zip')
                    create_backup(self.project_root, backup)
                    log(f'覆盖前备份：{backup}')
                merged = merge_entries(snapshot, incoming, replace)
                old = {x['word']: x for x in snapshot}
                for item in merged:
                    previous = old.get(item['word'])
                    if previous and previous.get('example') != item.get('example'):
                        item['generation_error'] = '导入更新了例句，请重新生成音频。'
                        item['generation_mode'] = 'audio_all'
                save_words(self.words_json_path, merged)
                return merged

            def applied(merged):
                self.words = merged
                self._editor_baseline = None
                self._sort_words()
                self._refresh_word_list()
                self.progress_label.setText(f'导入完成：{len(incoming)} 条；可用“补全所选”补生成缺失内容')

            self._start_task('正在导入词汇库…', task, applied)

        self._start_task('正在读取词汇文件…', lambda p, log: read_exchange(Path(filename)), finished)

    def _export_library_file(self, selected=False):
        if not self._confirm_unsaved():
            return
        chosen = set(self._selected_words())
        entries = [dict(x) for x in self.words if not selected or x['word'] in chosen]
        if not entries:
            self.statusBar().showMessage('没有可导出的词条。', 4000)
            return
        filename, kind = QFileDialog.getSaveFileName(
            self, '导出词汇库', str(self.project_root / 'exports' / 'vocabulary.xlsx'),
            'Excel (*.xlsx);;CSV (*.csv);;纯英文 TXT (*.txt)')
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix('.xlsx' if 'xlsx' in kind else '.csv' if 'csv' in kind else '.txt')
        if path.resolve() == (self.project_root / 'exports' / 'vocabulary-english.txt').resolve():
            self._show_error('vocabulary-english.txt 是自动维护的完整英文词库，请使用其他文件名导出。')
            return
        def task(progress, log):
            export_exchange(path, entries)
            return path
        self._start_task('正在导出词汇库…', task,
                         lambda result: self._library_completed(f'已导出 {len(entries)} 条', result))

    def _backup_library(self):
        if not self._confirm_unsaved():
            return
        filename, _ = QFileDialog.getSaveFileName(self, '完整备份词汇库',
            str(self.project_root / 'backups' / ('AnkiGen-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip')),
            'AnkiGen 备份 (*.zip)')
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix('.zip')
        def task(progress, log):
            create_backup(self.project_root, path)
            return path
        self._start_task('正在完整备份…', task, lambda result: self._library_completed('完整备份完成', result))

    def _restore_library(self):
        if not self._confirm_unsaved():
            return
        filename, _ = QFileDialog.getOpenFileName(self, '恢复完整备份', '', 'AnkiGen 备份 (*.zip)')
        if not filename:
            return
        path = Path(filename)
        def reviewed(info):
            answer = QMessageBox.question(self, '替换当前词库？',
                f"备份包含 {len(info['entries'])} 条词汇、{len(info['articles'])} 篇文章、{info['audio_count']} 个音频。\n"
                '将替换当前词库、文章和音频，保留本机 API 设置。\n恢复前会自动备份当前内容；不会修改 Anki。',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.audio_player.stop()
            self.audio_player.setSource(QUrl())
            def restored(recovery):
                self.words = load_words(self.words_json_path)
                self._editor_baseline = None
                self._editing_word = None
                self._generating_words.clear()
                self._sort_words()
                self.search_input.blockSignals(True)
                self.search_input.clear()
                self.search_input.blockSignals(False)
                self.status_filter.setCurrentIndex(0)
                self._refresh_word_list()
                self._load_article_history()
                self.article_candidates.clear()
                self._library_completed(f'已恢复 {len(self.words)} 条；恢复前的回退备份', recovery)
            self._start_task('正在恢复词汇库…', lambda p, log: restore_backup(self.project_root, path), restored)
        self._start_task('正在校验备份…', lambda p, log: inspect_backup(path), reviewed)

    def _library_completed(self, message, path):
        self.progress_label.setText(message)
        self._append_log(f'{message}：{path}')
        self.file_outcome.setText(f'{message}\n{path}')
