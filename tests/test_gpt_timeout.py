import unittest
from unittest.mock import Mock, patch

from openai import APITimeoutError

from services.gpt_generator import GPTGenerator, GPTGenerationError
from utils.file_manager import parse_words_batch


class GPTTimeoutTests(unittest.TestCase):
    def test_client_uses_bounded_network_timeouts(self):
        with patch('services.gpt_generator.OpenAI') as client:
            GPTGenerator(api_key='test')
        timeout = client.call_args.kwargs['timeout']
        self.assertEqual(timeout.connect, 20)
        self.assertEqual(timeout.read, 120)
        self.assertEqual(client.call_args.kwargs['max_retries'], 1)

    def test_timeout_is_reported_as_network_failure(self):
        with patch('services.gpt_generator.OpenAI'):
            generator = GPTGenerator(api_key='test')
        generator.client.chat.completions.create.side_effect = APITimeoutError(request=Mock())
        with self.assertRaisesRegex(GPTGenerationError, 'connection limit: 20s'):
            generator.generate_word_data(['food quality'])

    def test_single_entry_retries_preserve_phrase_and_strict_prompts(self):
        with patch('services.gpt_generator.OpenAI'):
            generator = GPTGenerator(api_key='test')
        phrase = "improve people's health"
        item = {'word': phrase}
        generator._generate_word_data_with_mode = Mock(side_effect=[
            GPTGenerationError('timeout'), GPTGenerationError('invalid JSON'), [item],
        ])
        self.assertEqual(generator._retry_single_entry(phrase), item)
        calls = generator._generate_word_data_with_mode.call_args_list
        self.assertEqual([call.kwargs['strict_mode'] for call in calls], [False, True, True])
        self.assertTrue(all(call.args[0] == [phrase] for call in calls))

    def test_seven_phrases(self):
        phrases = parse_words_batch("local specialties from all over the country,There is no doubt that,food quality,dietary structure,the improvement of food quality and dietary structure,improve people's health,taste local specialties easily")
        self.assertEqual(len(phrases), 7)
        self.assertEqual(phrases[1], 'there is no doubt that')
        self.assertEqual(phrases[5], "improve people's health")


if __name__ == '__main__':
    unittest.main()
