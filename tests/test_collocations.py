import unittest
from unittest.mock import Mock

from services.anki_api import AnkiAPI, AnkiConnectError
from services.gpt_generator import GPTGenerator
from utils.file_manager import parse_words_text


class CollocationTests(unittest.TestCase):
    def test_quoted_commas_and_ordinary_phrases(self):
        self.assertEqual(parse_words_text('abandon, take off, in charge of'),
                         ['abandon', 'take off', 'in charge of'])
        phrase = 'pursue safer, more nutritious and healthier food'
        self.assertEqual(parse_words_text(f'"{phrase}", take off'), [phrase, 'take off'])
        self.assertEqual(parse_words_text(''), [])

    def test_inflection_and_placeholders_preserve_phrase_frame(self):
        contains = GPTGenerator._example_contains_entry
        self.assertTrue(contains('Progress is fully reflected in daily life.', 'be fully reflected in'))
        phrase = 'make it easier for people to do sth'
        self.assertTrue(contains('Technology makes it easier for people to buy food.', phrase))
        self.assertFalse(contains('Technology makes people buy food.', phrase))
        self.assertFalse(contains('Progress is partially reflected in daily life.', 'be fully reflected in'))

    def test_answer_is_not_exposed_on_front(self):
        front = AnkiAPI._front_template()
        self.assertIn('{{Translation}}', front)
        self.assertIn('{{type:Word}}', front)
        for field in ('Word', 'Phonetic', 'Example', 'AudioWord', 'AudioSentence'):
            self.assertNotIn('{{' + field + '}}', front)
        self.assertIn('{{type:Word}}', AnkiAPI._back_template())

    def test_template_failure_is_reported(self):
        api = AnkiAPI()
        api._invoke = Mock(side_effect=AnkiConnectError('update rejected'))
        with self.assertRaises(AnkiConnectError):
            api._try_update_template('Test')


if __name__ == '__main__':
    unittest.main()
