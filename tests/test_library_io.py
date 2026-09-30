import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from services.library_io import create_backup, inspect_backup, restore_backup, read_exchange, export_exchange, merge_entries
from utils.file_manager import ensure_project_dirs, save_words, load_words


def vocabulary(word='safer, healthier food'):
    return {'word': word, 'phonetic': '/test/', 'part_of_speech': 'phrase', 'translation': '更安全、健康的食物',
            'example': 'We want safer, healthier food.\n我们想要更安全、健康的食物。', 'analysis': '测试',
            'source_text': '原文第一行\n原文第二行', 'imported_at': '2026-09-27 10:00:00',
            'generation_error': '', 'generation_mode': 'complete'}


class LibraryIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        ensure_project_dirs(self.root)
        save_words(self.root/'data/words.json', [vocabulary()])

    def tearDown(self):
        self.temp.cleanup()

    def test_english_library_tracks_save_rename_and_delete(self):
        output = self.root/'exports/vocabulary-english.txt'
        self.assertEqual(output.read_text(encoding='utf-8'), 'safer, healthier food\n')
        save_words(self.root/'data/words.json', [vocabulary('new phrase')])
        self.assertEqual(output.read_text(encoding='utf-8'), 'new phrase\n')
        save_words(self.root/'data/words.json', [])
        self.assertEqual(output.read_text(encoding='utf-8'), '')
        save_words(self.root/'data/articles.json', [{'text': 'article'}])
        self.assertEqual(output.read_text(encoding='utf-8'), '')

    def test_csv_and_excel_roundtrip_keep_all_fields(self):
        for extension in ('csv', 'xlsx'):
            with self.subTest(extension=extension):
                output = self.root/f'vocabulary.{extension}'
                item = vocabulary()
                item['analysis'] = '=not a formula'
                export_exchange(output, [item])
                self.assertEqual(read_exchange(output), [item])

    def test_txt_keeps_literal_word_and_internal_comma(self):
        output = self.root/'vocabulary.txt'
        export_exchange(output, [vocabulary('word'), vocabulary()])
        self.assertEqual([x['word'] for x in read_exchange(output)], ['word', 'safer, healthier food'])

    def test_chinese_headers_and_gbk(self):
        output = self.root/'legacy.csv'
        output.write_bytes('英文,中文\nfood quality,食品质量\n'.encode('gb18030'))
        self.assertEqual(read_exchange(output)[0]['translation'], '食品质量')

    def test_duplicate_policy(self):
        old, incoming = vocabulary(), vocabulary()
        incoming['translation'] = '修改'
        self.assertEqual(merge_entries([old], [incoming])[0]['translation'], old['translation'])
        self.assertEqual(merge_entries([old], [incoming], True)[0]['translation'], '修改')

    def test_backup_restore_preserves_local_secrets_and_media(self):
        (self.root/'audio/test.mp3').write_bytes(b'valid audio')
        save_words(self.root/'data/articles.json', [{'text': '原文'}])
        (self.root/'data/settings.json').write_text('{"api_key":"local-secret"}')
        archive = self.root/'backup.zip'
        create_backup(self.root, archive)
        with zipfile.ZipFile(archive) as z:
            self.assertNotIn('data/settings.json', z.namelist())
        save_words(self.root/'data/words.json', [vocabulary('different')])
        (self.root/'audio/extra.mp3').write_bytes(b'extra')
        recovery = restore_backup(self.root, archive)
        self.assertEqual(load_words(self.root/'data/words.json')[0]['word'], vocabulary()['word'])
        self.assertEqual((self.root/'audio/test.mp3').read_bytes(), b'valid audio')
        self.assertFalse((self.root/'audio/extra.mp3').exists())
        self.assertIn('local-secret', (self.root/'data/settings.json').read_text())
        self.assertEqual(inspect_backup(recovery)['entries'][0]['word'], 'different')
        self.assertEqual((self.root/'exports/vocabulary-english.txt').read_text(encoding='utf-8'), vocabulary()['word']+'\n')

    def test_malicious_archive_rejected_before_mutation(self):
        archive = self.root/'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('../outside.txt', 'bad')
        before = (self.root/'data/words.json').read_bytes()
        with self.assertRaises(ValueError):
            restore_backup(self.root, archive)
        self.assertEqual((self.root/'data/words.json').read_bytes(), before)

    def test_failed_restore_rolls_back_both_directories(self):
        archive = self.root/'backup.zip'
        create_backup(self.root, archive)
        save_words(self.root/'data/words.json', [vocabulary('keep me')])
        (self.root/'audio/keep.mp3').write_bytes(b'keep')
        with patch('services.library_io.update_english_library', side_effect=OSError('file locked')):
            with self.assertRaises(OSError):
                restore_backup(self.root, archive)
        self.assertEqual(load_words(self.root/'data/words.json')[0]['word'], 'keep me')
        self.assertEqual((self.root/'audio/keep.mp3').read_bytes(), b'keep')

    def test_excel_rejects_overlong_field_without_truncating(self):
        item = vocabulary()
        item['source_text'] = 'x' * 32768
        with self.assertRaises(ValueError):
            export_exchange(self.root/'long.xlsx', [item])


if __name__ == '__main__':
    unittest.main()
