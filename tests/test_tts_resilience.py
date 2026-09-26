import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services.tts_generator import TTSGenerator, TTSGenerationError
from utils.file_manager import check_audio_exists


class TTSResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_regeneration_preserves_existing_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'phrase.mp3'
            target.write_bytes(b'original audio')

            async def fail(path):
                Path(path).write_bytes(b'partial')
                raise TimeoutError('wss://example.invalid/?token=private')

            with patch('services.tts_generator.edge_tts.Communicate') as communicate, \
                    patch('services.tts_generator.asyncio.sleep', new_callable=AsyncMock):
                communicate.return_value.save.side_effect = fail
                with self.assertRaises(TTSGenerationError) as caught:
                    await TTSGenerator()._generate_word_audio('phrase', root)
                self.assertEqual(communicate.call_count, 3)
                self.assertNotIn('token=', str(caught.exception))
            self.assertEqual(target.read_bytes(), b'original audio')
            self.assertEqual(list(root.glob('*.tmp')), [])

    async def test_retry_succeeds_and_replaces_only_complete_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'phrase.mp3'
            target.write_bytes(b'old')
            calls = 0

            async def save(path):
                nonlocal calls
                calls += 1
                self.assertEqual(target.read_bytes(), b'old')
                if calls == 1:
                    raise TimeoutError()
                Path(path).write_bytes(b'new audio')

            with patch('services.tts_generator.edge_tts.Communicate') as communicate, \
                    patch('services.tts_generator.asyncio.sleep', new_callable=AsyncMock):
                communicate.return_value.save.side_effect = save
                await TTSGenerator()._generate_word_audio('phrase', root)
            self.assertEqual(calls, 2)
            self.assertEqual(target.read_bytes(), b'new audio')
            self.assertEqual(list(root.glob('*.tmp')), [])

    async def test_empty_response_is_not_a_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch('services.tts_generator.edge_tts.Communicate') as communicate, \
                    patch('services.tts_generator.asyncio.sleep', new_callable=AsyncMock):
                communicate.return_value.save = AsyncMock()
                with self.assertRaises(TTSGenerationError):
                    await TTSGenerator()._generate_word_audio('phrase', root)
            self.assertFalse((root / 'phrase.mp3').exists())
            (root / 'phrase.mp3').touch()
            self.assertEqual(check_audio_exists(root, 'phrase'), (False, False))


if __name__ == '__main__':
    unittest.main()
