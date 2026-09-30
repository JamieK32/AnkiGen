import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog

from gui.main_window import MainWindow
from gui.workflow_dialogs import ImportDialog
from services.vocabulary_workflow import generate_entries, sync_preview, entry_status, sync_ready
from utils.file_manager import load_words, save_words, parse_import_text, word_audio_path, sentence_audio_path


def entry(word, **extra):
    return dict(word=word, phonetic='/test/', part_of_speech='phrase', translation='测试',
                example='This is a test.\n这是测试。', analysis='测试搭配', imported_at='2026-09-26 12:00:00', **extra)


class WorkflowTests(unittest.TestCase):
    def test_line_import_keeps_commas_and_deduplicates(self):
        self.assertEqual(parse_import_text('safer, healthier food\ntake off\nTake Off', 'lines'),
                         ['safer, healthier food', 'take off'])
        with self.assertRaises(ValueError):
            parse_import_text('"unclosed phrase', 'comma')

    def test_failure_and_source_survive_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'words.json'
            save_words(path, [entry('test', source_text='文章原文', generation_error='timeout')])
            loaded = load_words(path)[0]
            self.assertEqual(loaded['source_text'], '文章原文')
            self.assertEqual(entry_status(loaded, Path(tmp)), '失败')

    def test_partial_generation_preserves_failed_entries_and_existing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            generator = Mock()
            generator.generate_words_batch.return_value = {'items': [entry('first')], 'errors': ['second failed']}
            factory = Mock(return_value=generator)
            tts = Mock(max_workers=1)
            result = generate_entries([
                {'word': 'first', 'translation': '原有释义', 'source_text': '原文'},
                {'word': 'second', 'source_text': '原文'},
            ], factory, tts, Path(tmp), 20, 'complete', Mock(), Mock())
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]['translation'], '原有释义')
            self.assertEqual(result[0]['source_text'], '原文')
            self.assertTrue(result[1]['generation_error'])
            self.assertEqual(tts.generate_for_entry.call_count, 1)
            factory.assert_called_once_with('原文')

    def test_missing_audio_does_not_regenerate_existing_file_or_call_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            word_audio_path(root, 'test').write_bytes(b'old')
            tts, factory = Mock(max_workers=1), Mock()
            generate_entries([entry('test')], factory, tts, root, 20, 'audio_missing', Mock(), Mock())
            factory.assert_not_called()
            self.assertFalse(tts.generate_for_entry.call_args.kwargs['generate_word'])
            self.assertTrue(tts.generate_for_entry.call_args.kwargs['generate_sentence'])

    def test_retry_remembers_explicit_audio_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            word_audio_path(root, 'test').write_bytes(b'old')
            sentence_audio_path(root, 'test').write_bytes(b'old')
            tts = Mock(max_workers=1)
            generate_entries([entry('test', generation_error='timeout', generation_mode='audio_all')],
                             Mock(), tts, root, 20, 'retry', Mock(), Mock())
            self.assertTrue(tts.generate_for_entry.call_args.kwargs['generate_word'])
            self.assertTrue(tts.generate_for_entry.call_args.kwargs['generate_sentence'])

    def test_sync_plan_lists_skips_and_duplicate_deletions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for word in ('new', 'existing'):
                word_audio_path(root, word).write_bytes(b'mp3')
                sentence_audio_path(root, word).write_bytes(b'mp3')
            plan = sync_preview([entry('new'), entry('existing'), entry('missing')],
                                {'existing': [1, 2], 'old': [3]}, root)
            self.assertEqual(plan['create'], ['new'])
            self.assertEqual(plan['update'], ['existing'])
            self.assertEqual(plan['skipped'], ['missing'])
            self.assertEqual(sorted(x for ids in plan['delete'].values() for x in ids), [2, 3])

    def test_optional_fields_do_not_block_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = entry('test')
            item['phonetic'] = item['part_of_speech'] = ''
            word_audio_path(root, 'test').write_bytes(b'mp3')
            sentence_audio_path(root, 'test').write_bytes(b'mp3')
            self.assertTrue(sync_ready(item, root))


class WorkflowUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # The offscreen Windows plugin does not discover system fonts itself.
        if os.name == 'nt':
            from PySide6.QtGui import QFontDatabase
            for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
                QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR'])/'Fonts'/name))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'data').mkdir()
        save_words(self.root/'data/words.json', [entry('first', source_text='原文'), entry('second')])
        self.window = MainWindow(self.root)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window._editor_baseline = None
        self.window.close()
        self.window._ui_settings().clear()
        self.app.processEvents()
        self.temp.cleanup()

    def test_save_before_selection_switch_preserves_correct_entry_and_source(self):
        self.window.editor.translation_edit.setText('修改后的释义')
        self.assertIn('*', self.window.editor.save_button.text())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Save):
            self.window.word_list.selectRow(1)
        items = {x['word']: x for x in load_words(self.root/'data/words.json')}
        self.assertEqual(items['first']['translation'], '修改后的释义')
        self.assertEqual(items['first']['source_text'], '原文')
        self.assertEqual(items['second']['translation'], '测试')

    def test_cancel_selection_keeps_edits_and_selection(self):
        self.window.editor.translation_edit.setText('未保存')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel):
            self.window.word_list.selectRow(1)
        self.assertEqual(self.window._current_selected_word(), 'first')
        self.assertEqual(self.window.editor.translation_edit.text(), '未保存')

    def test_shortcut_saves_without_switching(self):
        self.window.editor.translation_edit.setText('快捷键保存')
        self.window.editor.translation_edit.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.window.editor.translation_edit, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(load_words(self.root/'data/words.json')[0]['translation'], '快捷键保存')

    def test_import_preview_skips_existing_and_preserves_comma_phrase(self):
        dialog = ImportDialog(['first'], self.window)
        dialog.mode.setCurrentIndex(1)
        dialog.text.setPlainText('first\npursue safer, healthier food')
        self.assertEqual(dialog.selected_entries(), [{'word': 'pursue safer, healthier food'}])
        dialog.close()

    def test_failed_retry_targets_only_failures(self):
        self.window.words[0]['generation_error'] = 'timeout'
        with patch.object(self.window, '_run_generation') as run:
            self.window._retry_failed()
        self.assertEqual([x['word'] for x in run.call_args.args[0]], ['first'])
        self.assertEqual(run.call_args.args[1], 'retry')

    def test_library_file_import_does_not_trigger_ai(self):
        path = self.root/'incoming.txt'
        path.write_text('safer, healthier food\n', encoding='utf-8')
        def immediate(status, fn, on_success, **kwargs):
            on_success(fn(Mock(), Mock()))
        with patch('gui.library_actions.QFileDialog.getOpenFileName', return_value=(str(path), '')), \
             patch('gui.library_actions.ImportDialog.exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(self.window, '_start_task', side_effect=immediate), \
             patch.object(self.window, '_run_generation') as generate:
            self.window._import_library_file()
        self.assertIn('safer, healthier food', [x['word'] for x in self.window.words])
        generate.assert_not_called()
        self.assertIn('safer, healthier food', (self.root/'exports/vocabulary-english.txt').read_text(encoding='utf-8'))

    def test_preview_cancel_does_not_write_to_anki(self):
        api = Mock()
        api.get_deck_word_to_note_ids.return_value = {'old': [1]}
        self.window.anki_api = api
        def immediate(status, fn, on_success, **kwargs):
            on_success(fn(Mock(), Mock()))
        with patch.object(self.window, '_start_task', side_effect=immediate):
            self.window._preview_sync()
        api.ensure_deck.assert_not_called()
        api.delete_notes.assert_not_called()
        api.add_notes.assert_not_called()

    def test_sync_rechecks_remote_before_writing(self):
        api = Mock()
        api.get_deck_word_to_note_ids.return_value = {'changed': [2]}
        self.window.anki_api = api
        def immediate(status_text, fn, on_success, **kwargs):
            fn(Mock(), Mock())
        with patch.object(self.window, '_start_task', side_effect=immediate):
            with self.assertRaisesRegex(ValueError, '内容已变化'):
                self.window._on_sync_to_anki_clicked({'old': [1]})
        api.ensure_deck.assert_not_called()
        api.delete_notes.assert_not_called()

    def test_task_completion_hides_progress_and_keeps_summary(self):
        self.window.log_toggle.setChecked(False)
        self.window._append_log('error', 'ERROR')
        self.assertTrue(self.window.log_toggle.isChecked())
        self.window._start_task('test', lambda progress, log: [1, 2, 3],
                                lambda result: self.window.progress_label.setText(f'完成 {len(result)} 条'))
        deadline = time.monotonic() + 3
        while (self.window._task_busy or self.window._workers) and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertFalse(self.window._task_busy)
        self.assertTrue(self.window.progress_bar.isHidden())
        self.assertEqual(self.window.progress_label.text(), '完成 3 条')

    def test_sync_execution_matches_preview_without_generation(self):
        self.window.words = [entry('new', source_text='原文'), entry('existing'), entry('missing')]
        for word in ('new', 'existing'):
            word_audio_path(self.window.audio_dir, word).write_bytes(b'mp3')
            sentence_audio_path(self.window.audio_dir, word).write_bytes(b'mp3')
        remote = {'existing': [1, 2], 'removed': [3]}
        api = Mock()
        api.get_deck_word_to_note_ids.return_value = remote
        api.upload_media_files_concurrently.return_value = {'failed': []}
        api.build_note_payload.side_effect = lambda **kw: {'fields': {
            'AudioWord': '[sound:' + kw['audio_word_filename'] + ']',
            'AudioSentence': '[sound:' + kw['audio_sentence_filename'] + ']',
        }}
        api.add_notes.return_value = [100]
        api.update_note_fields_multi.return_value = {'updated': 1, 'failed': 0}
        self.window.anki_api = api
        self.window.gpt_generator = Mock()
        self.window.tts_generator = Mock()
        results = []
        def immediate(status_text, fn, on_success, **kwargs):
            results.append(fn(Mock(), Mock()))
        with patch.object(self.window, '_start_task', side_effect=immediate):
            self.window._on_sync_to_anki_clicked(remote)
        result = results[0]
        self.assertEqual((result['created'], result['updated'], result['deleted'], result['skipped']), (1, 1, 2, 1))
        self.assertEqual(sorted(api.delete_notes.call_args.args[0]), [2, 3])
        self.assertEqual(result['words'][0]['source_text'], '原文')
        self.assertFalse(self.window.gpt_generator.mock_calls)
        self.assertFalse(self.window.tts_generator.mock_calls)

    def test_filter_clears_selection_and_select_all_targets_visible_only(self):
        self.window.word_list.selectAll()
        self.assertEqual(set(self.window._selected_words()), {'first', 'second'})
        self.window.search_input.setText('SECOND')
        self.assertEqual(self.window._selected_words(), [])
        self.window.word_list.selectAll()
        with patch.object(self.window, '_run_generation') as run:
            self.window._generate_selected()
        self.assertEqual([x['word'] for x in run.call_args.args[0]], ['second'])
        self.window.search_input.setText('测试')
        self.assertEqual(self.window.word_proxy.rowCount(), 2)
        self.assertEqual(self.window._selected_words(), [])

    def test_status_filter_and_failed_retry_respect_selection(self):
        for row in self.window.words:
            row['generation_error'] = 'timeout'
        self.window._refresh_word_list()
        with patch.object(self.window, '_run_generation') as run:
            self.window._retry_failed()
        self.assertEqual([x['word'] for x in run.call_args.args[0]], ['first'])
        self.window.status_filter.setCurrentText('已完成')
        self.assertEqual(self.window.word_proxy.rowCount(), 0)
        self.assertFalse(self.window.generate_all_button.isEnabled())
        self.window.status_filter.setCurrentText('失败')
        self.assertEqual(self.window.word_proxy.rowCount(), 2)

    def test_cancel_filter_and_navigation_preserves_draft(self):
        self.window.editor.translation_edit.setText('未保存')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel):
            self.window.search_input.setText('second')
            self.window.navigation.setCurrentRow(1)
        self.assertEqual(self.window.search_input.text(), '')
        self.assertEqual(self.window.pages.currentIndex(), 0)
        self.assertEqual(self.window.navigation.currentRow(), 0)
        self.assertEqual(self.window.editor.translation_edit.text(), '未保存')

    def test_failed_save_retains_input_baseline_and_disk(self):
        before = load_words(self.root/'data/words.json')
        self.window.editor.translation_edit.setText('未写入磁盘')
        with patch('gui.main_window.save_words', side_effect=OSError('disk full')):
            saved = self.window._on_save_word_clicked(self.window.editor.get_word_data())
        self.assertFalse(saved)
        self.assertEqual(self.window.editor.translation_edit.text(), '未写入磁盘')
        self.assertTrue(self.window.editor.save_button.isEnabled())
        self.assertEqual(load_words(self.root/'data/words.json'), before)
        self.assertEqual(self.window.words[0]['translation'], '测试')

    def test_inline_sync_preview_invalidates_after_save_and_external_media_change(self):
        api = Mock()
        api.get_deck_word_to_note_ids.return_value = {}
        self.window.anki_api = api
        def immediate(status, fn, success, **kwargs):
            success(fn(Mock(), Mock()))
        with patch.object(self.window, '_start_task', side_effect=immediate):
            self.window._preview_sync()
            self.assertTrue(self.window.sync_confirm.isEnabled())
            self.window.editor.translation_edit.setText('新释义')
            self.window._on_save_word_clicked(self.window.editor.get_word_data())
            self.assertFalse(self.window.sync_confirm.isEnabled())
            self.window._preview_sync()
        word_audio_path(self.window.audio_dir, 'first').write_bytes(b'new audio')
        with patch.object(self.window, '_on_sync_to_anki_clicked') as sync:
            self.window._execute_preview()
        sync.assert_not_called()
        self.assertFalse(self.window.sync_confirm.isEnabled())

    def test_sync_delete_requires_confirmation(self):
        api = Mock()
        api.get_deck_word_to_note_ids.return_value = {'obsolete': [4]}
        self.window.anki_api = api
        with patch.object(self.window, '_start_task', side_effect=lambda s, fn, done, **kw: done(fn(Mock(), Mock()))):
            self.window._preview_sync()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No), \
             patch.object(self.window, '_on_sync_to_anki_clicked') as sync:
            self.window._execute_preview()
        sync.assert_not_called()
        self.assertTrue(self.window.sync_confirm.isEnabled())

    def test_article_extraction_persists_candidates_across_navigation(self):
        self.window.gpt_generator = Mock()
        self.window.article_text.setPlainText('文章原文')
        with patch('gui.workspace.extract_collocations', return_value=[{'word': 'new phrase', 'translation': '新搭配'}]), \
             patch.object(self.window, '_start_task', side_effect=lambda s, fn, done, **kw: done(fn(Mock(), Mock()))):
            self.window._extract_article()
        self.window.navigation.setCurrentRow(1)
        self.window.navigation.setCurrentRow(0)
        self.window.navigation.setCurrentRow(1)
        self.assertEqual(self.window.article_text.toPlainText(), '文章原文')
        self.assertEqual(self.window._chosen_candidates()[0]['source_text'], '文章原文')
        with patch.object(self.window, '_run_generation'):
            self.window._import_article_candidates()
        self.assertEqual(self.window._find_word('new phrase')['source_text'], '文章原文')
        self.assertFalse(self.window.article_import.isEnabled())

    def test_background_task_allows_navigation_and_audio_but_not_mutation(self):
        word_audio_path(self.window.audio_dir, 'first').write_bytes(b'audio')
        self.window._update_audio_status('first')
        self.window._set_busy(True, '生成中', allow_browse_audio=True)
        self.window.navigation.setCurrentRow(1)
        self.assertEqual(self.window.pages.currentIndex(), 1)
        self.assertTrue(self.window.word_list.isEnabled())
        self.assertTrue(self.window.editor.play_word_button.isEnabled())
        self.assertFalse(self.window.generate_all_button.isEnabled())
        self.assertFalse(self.window.backup_button.isEnabled())
        self.assertFalse(self.window.article_import.isEnabled())
        self.window._set_busy(False, '完成')

    def test_small_window_pages_and_log_default(self):
        self.window.resize(1200, 720)
        self.app.processEvents()
        self.assertTrue(self.window.log_widget.isHidden())
        for index in range(4):
            self.window.navigation.setCurrentRow(index)
            self.app.processEvents()
            self.assertLessEqual(self.window.minimumSizeHint().width(), 1200)
            self.assertLessEqual(self.window.minimumSizeHint().height(), 720)


if __name__ == '__main__':
    unittest.main()
