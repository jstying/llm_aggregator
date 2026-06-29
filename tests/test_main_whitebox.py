import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

MOCK_PROVIDER_MODELS_MAP = {
    'Yqcloud': ['gpt-3.5-turbo', 'gpt-4'],
    'OperaAria': ['aria'],
}

MOCK_G4F_PROVIDERS = []


class TestDetermineActualModel(unittest.TestCase):

    def setUp(self):
        self.patcher_map = patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP)
        self.patcher_map.start()

    def tearDown(self):
        self.patcher_map.stop()

    def test_rule_a_model_in_supported_list(self):
        import main
        result = main.determine_actual_model('Yqcloud', 'gpt-4')
        self.assertEqual(result, 'gpt-4')

    def test_rule_a_first_model_also_in_list(self):
        import main
        result = main.determine_actual_model('Yqcloud', 'gpt-3.5-turbo')
        self.assertEqual(result, 'gpt-3.5-turbo')

    def test_rule_b_model_not_in_list_falls_back_to_first(self):
        import main
        result = main.determine_actual_model('Yqcloud', 'gpt-9999')
        self.assertEqual(result, 'gpt-3.5-turbo')

    def test_rule_b_none_model_falls_back_to_first(self):
        import main
        result = main.determine_actual_model('Yqcloud', None)
        self.assertEqual(result, 'gpt-3.5-turbo')

    def test_rule_b_empty_string_model_falls_back_to_first(self):
        import main
        result = main.determine_actual_model('OperaAria', '')
        self.assertEqual(result, 'aria')

    def test_rule_b_opera_aria_only_model(self):
        import main
        result = main.determine_actual_model('OperaAria', 'aria')
        self.assertEqual(result, 'aria')

    def test_rule_b_opera_aria_unsupported_model(self):
        import main
        result = main.determine_actual_model('OperaAria', 'gpt-4')
        self.assertEqual(result, 'aria')

    def test_rule_c_unknown_provider_no_models(self):
        import main
        result = main.determine_actual_model('UnknownProvider', 'gpt-4')
        self.assertEqual(result, 'gpt-3.5-turbo')

    def test_rule_c_unknown_provider_none_model(self):
        import main
        result = main.determine_actual_model('NonExistent', None)
        self.assertEqual(result, 'gpt-3.5-turbo')

    def test_rule_c_unknown_provider_empty_string_model(self):
        import main
        result = main.determine_actual_model('Ghost', '')
        self.assertEqual(result, 'gpt-3.5-turbo')


class TestInitResultObject(unittest.TestCase):

    def setUp(self):
        import main
        self.init_result_object = main.init_result_object

    def test_key_set_is_complete(self):
        result = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        expected_keys = {'provider', 'success', 'response', 'error', 'response_time', 'model', 'type'}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_provider_field(self):
        result = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        self.assertEqual(result['provider'], 'Yqcloud')

    def test_model_field(self):
        result = self.init_result_object('OperaAria', 'aria')
        self.assertEqual(result['model'], 'aria')

    def test_success_default_false(self):
        result = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        self.assertFalse(result['success'])

    def test_response_default_empty_string(self):
        result = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        self.assertEqual(result['response'], '')

    def test_error_default_empty_string(self):
        result = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        self.assertEqual(result['error'], '')

    def test_response_time_default_zero(self):
        result = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        self.assertEqual(result['response_time'], 0)

    def test_type_always_g4f(self):
        result = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        self.assertEqual(result['type'], 'g4f')

    def test_no_extra_keys(self):
        result = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        self.assertEqual(len(result), 7)

    def test_independent_instances(self):
        r1 = self.init_result_object('Yqcloud', 'gpt-3.5-turbo')
        r2 = self.init_result_object('OperaAria', 'aria')
        r1['success'] = True
        self.assertFalse(r2['success'])


class TestTestG4fProvider(unittest.TestCase):

    def _make_mock_provider(self, name='Yqcloud'):
        mock_provider = MagicMock()
        mock_provider.__name__ = name
        return mock_provider

    def test_success_sets_correct_fields(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.return_value = 'Four'
            import main
            result = main.test_g4f_provider(mock_provider, 'What is 2+2?', 'gpt-3.5-turbo')

        self.assertTrue(result['success'])
        self.assertEqual(result['response'], 'Four')
        self.assertEqual(result['error'], '')
        self.assertEqual(result['provider'], 'Yqcloud')
        self.assertEqual(result['model'], 'gpt-3.5-turbo')
        self.assertEqual(result['type'], 'g4f')

    def test_success_response_time_is_positive_float(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.return_value = 'ok'
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertIsInstance(result['response_time'], float)
        self.assertGreaterEqual(result['response_time'], 0)

    def test_exception_sets_error_and_success_false(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.side_effect = RuntimeError('invalid token')
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertFalse(result['success'])
        self.assertIn('invalid token', result['error'])
        self.assertEqual(result['response'], '')

    def test_network_error_shows_friendly_message(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.side_effect = Exception('connection timed out')
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertFalse(result['success'])
        self.assertIn('系统正忙', result['error'])

    def test_rate_limit_error_shows_friendly_message(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.side_effect = Exception('rate limit exceeded')
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertFalse(result['success'])
        self.assertIn('系统正忙', result['error'])

    def test_exception_response_time_still_set(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.side_effect = Exception('timeout')
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertIsInstance(result['response_time'], float)
        self.assertGreaterEqual(result['response_time'], 0)

    def test_result_key_set_on_success(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.return_value = 'hi'
            import main
            result = main.test_g4f_provider(mock_provider, 'hi', 'gpt-3.5-turbo')

        expected_keys = {'provider', 'success', 'response', 'error', 'response_time', 'model', 'type'}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_result_key_set_on_exception(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.side_effect = ValueError('bad input')
            import main
            result = main.test_g4f_provider(mock_provider, 'hi', 'gpt-3.5-turbo')

        expected_keys = {'provider', 'success', 'response', 'error', 'response_time', 'model', 'type'}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_model_degradation_rule_b_applied(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.return_value = 'ok'
            import main
            result = main.test_g4f_provider(mock_provider, 'hi', 'gpt-unsupported')

        self.assertEqual(result['model'], 'gpt-3.5-turbo')

    def test_model_degradation_rule_c_applied(self):
        mock_provider = self._make_mock_provider('NoModelProvider')
        with patch('main.PROVIDER_MODELS_MAP', {}), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.return_value = 'ok'
            import main
            result = main.test_g4f_provider(mock_provider, 'hi', None)

        self.assertEqual(result['model'], 'gpt-3.5-turbo')

    def test_detect_and_truncate_result_stored_in_response(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f, \
             patch('main.detect_and_truncate', return_value='TRUNCATED_TEXT'):
            mock_g4f.ChatCompletion.create.return_value = 'some repetitive response'
            import main
            result = main.test_g4f_provider(mock_provider, 'hi', 'gpt-3.5-turbo')

        self.assertEqual(result['response'], 'TRUNCATED_TEXT')


class TestDetectAndTruncate(unittest.TestCase):

    def setUp(self):
        import main
        self.fn = main.detect_and_truncate

    def test_short_text_returned_unchanged(self):
        text = 'Too short.'
        self.assertEqual(self.fn(text), text)

    def test_normal_text_returned_unchanged(self):
        text = 'This is a perfectly normal response with no repeated patterns whatsoever.'
        self.assertEqual(self.fn(text), text)

    def test_sentence_repetition_triggers_truncation(self):
        sentence = 'This keeps repeating!'
        text = (sentence + ' ') * 3
        result = self.fn(text)
        self.assertIn('...（因文本重复已被系统自动截断）', result)

    def test_sentence_truncation_preserves_first_two_occurrences(self):
        sentence = 'Repeat.'
        text = (sentence + ' ') * 4
        result = self.fn(text)
        self.assertEqual(result.count(sentence), 2)
        self.assertIn('...（因文本重复已被系统自动截断）', result)

    def test_two_sentence_repetitions_do_not_trigger(self):
        text = 'Hello there! Hello there! This part is completely different.'
        result = self.fn(text)
        self.assertEqual(result, text)

    def test_window_repetition_triggers_truncation(self):
        chunk = 'abcdefghij'
        text = chunk * 3 + 'extra content that is unique'
        result = self.fn(text)
        self.assertIn('...（因文本重复已被系统自动截断）', result)

    def test_window_truncation_preserves_first_two_occurrences(self):
        chunk = 'abcdefghij'
        text = chunk * 3 + 'extra content that is unique'
        result = self.fn(text)
        self.assertTrue(result.startswith(chunk * 2))
        self.assertNotIn(chunk * 3, result)

    def test_two_window_repetitions_do_not_trigger(self):
        chunk = 'abcdefghij'
        text = chunk * 2 + 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        result = self.fn(text)
        self.assertEqual(result, text)

    def test_sensitive_keyword_returns_intercept_message(self):
        import main
        with patch('main.SENSITIVE_KEYWORDS', ['forbidden_word']):
            result = main.detect_and_truncate('This text contains forbidden_word content.')
        self.assertEqual(result, '内容涉及敏感信息，已拦截。')


class TestRoutePromptsMap(unittest.TestCase):

    def _make_mock_provider(self, name='Yqcloud'):
        mock = MagicMock()
        mock.__name__ = name
        return mock

    def test_known_combination_appends_suffix_to_g4f_call(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.ROUTE_PROMPTS_MAP', {('Yqcloud', 'gpt-4'): '[STYLE]'}), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.return_value = 'ok'
            import main
            main.test_g4f_provider(mock_provider, 'hello', 'gpt-4')

        sent_content = mock_g4f.ChatCompletion.create.call_args[1]['messages'][0]['content']
        self.assertIn('[STYLE]', sent_content)
        self.assertTrue(sent_content.startswith('hello'))

    def test_unknown_combination_sends_original_prompt(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.return_value = 'ok'
            import main
            main.test_g4f_provider(mock_provider, 'hello', 'gpt-3.5-turbo')

        sent_content = mock_g4f.ChatCompletion.create.call_args[1]['messages'][0]['content']
        self.assertEqual(sent_content, 'hello')

    def test_response_field_does_not_contain_style_suffix(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.ROUTE_PROMPTS_MAP', {('Yqcloud', 'gpt-3.5-turbo'): '[STYLE]'}), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.return_value = 'response text'
            import main
            result = main.test_g4f_provider(mock_provider, 'hello', 'gpt-3.5-turbo')

        self.assertNotIn('[STYLE]', result['response'])


class TestRunPeerReview(unittest.TestCase):
    """Whitebox tests for run_peer_review retry logic."""

    def _make_mock_provider(self, name='TestProvider'):
        mock_p = MagicMock()
        mock_p.__name__ = name
        return mock_p

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    @patch('main.g4f.ChatCompletion.create')
    def test_429_triggers_retry_and_succeeds(self, mock_create, mock_rand, mock_sleep):
        mock_create.side_effect = [
            Exception('Error 429: Queue full for IP'),
            '{"score": 90, "comment": "great"}'
        ]
        import main
        result = main.run_peer_review(self._make_mock_provider(), 'gpt-4', 'prompt')
        self.assertEqual(mock_create.call_count, 2)
        mock_sleep.assert_called_once()
        self.assertEqual(result['score'], 90)
        self.assertEqual(result['comment'], 'great')

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    @patch('main.g4f.ChatCompletion.create')
    def test_queue_full_error_also_triggers_retry(self, mock_create, mock_rand, mock_sleep):
        mock_create.side_effect = [
            Exception('queue is full'),
            '{"score": 75, "comment": "ok"}'
        ]
        import main
        result = main.run_peer_review(self._make_mock_provider(), 'gpt-4', 'prompt')
        self.assertEqual(mock_create.call_count, 2)
        self.assertEqual(result['score'], 75)

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    @patch('main.g4f.ChatCompletion.create')
    def test_non_429_error_does_not_retry(self, mock_create, mock_rand, mock_sleep):
        mock_create.side_effect = Exception('Some other error')
        import main
        result = main.run_peer_review(self._make_mock_provider(), 'gpt-4', 'prompt')
        self.assertEqual(mock_create.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn('点评失败', result['comment'])

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    @patch('main.g4f.ChatCompletion.create')
    def test_429_then_retry_also_fails_returns_friendly_comment(self, mock_create, mock_rand, mock_sleep):
        mock_create.side_effect = [
            Exception('Error 429: Queue full'),
            Exception('Error 429: Queue full'),
        ]
        import main
        result = main.run_peer_review(self._make_mock_provider(), 'gpt-4', 'prompt')
        self.assertEqual(mock_create.call_count, 2)
        self.assertIn('系统正忙', result['comment'])

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    @patch('main.g4f.ChatCompletion.create')
    def test_result_keys_always_present(self, mock_create, mock_rand, mock_sleep):
        mock_create.side_effect = Exception('Error 429: Queue full')
        import main
        result = main.run_peer_review(self._make_mock_provider('FakeP'), 'aria', 'p')
        self.assertIn('reviewer_provider', result)
        self.assertIn('reviewer_model', result)
        self.assertIn('score', result)
        self.assertIn('comment', result)
        self.assertEqual(result['reviewer_provider'], 'FakeP')
        self.assertEqual(result['reviewer_model'], 'aria')


class TestParsePeerReviewJson(unittest.TestCase):

    def setUp(self):
        import main
        self.fn = main.parse_peer_review_json

    def test_valid_json_returns_score_and_comment(self):
        score, comment = self.fn('{"score": 85, "comment": "很好的回答"}')
        self.assertEqual(score, 85)
        self.assertEqual(comment, '很好的回答')

    def test_score_clamped_above_100(self):
        score, _ = self.fn('{"score": 150, "comment": "超分了"}')
        self.assertEqual(score, 100)

    def test_score_clamped_below_1(self):
        score, _ = self.fn('{"score": -5, "comment": "负分"}')
        self.assertEqual(score, 1)

    def test_score_as_float_converted_to_int(self):
        score, _ = self.fn('{"score": 85.7, "comment": "还可以"}')
        self.assertIsInstance(score, int)
        self.assertEqual(score, 85)

    def test_json_with_surrounding_text_extracted(self):
        text = '这是我的评分：\n{"score": 70, "comment": "一般般"}\n谢谢。'
        score, comment = self.fn(text)
        self.assertEqual(score, 70)
        self.assertEqual(comment, '一般般')

    def test_malformed_json_returns_fallback(self):
        score, comment = self.fn('{score: 90, comment: good}')
        self.assertEqual(score, 80)
        self.assertIsInstance(comment, str)

    def test_non_json_text_returns_fallback_with_raw_text(self):
        text = '这是一段普通文字，没有JSON格式内容。'
        score, comment = self.fn(text)
        self.assertEqual(score, 80)
        self.assertEqual(comment, text.strip())

    def test_missing_score_returns_fallback_80(self):
        score, _ = self.fn('{"comment": "有comment但没有score"}')
        self.assertEqual(score, 80)

    def test_missing_comment_returns_empty_string(self):
        _, comment = self.fn('{"score": 75}')
        self.assertEqual(comment, '')

    def test_empty_string_returns_fallback(self):
        score, comment = self.fn('')
        self.assertEqual(score, 80)
        self.assertEqual(comment, '')

    def test_json_with_backslash_in_comment(self):
        text = '{"score": 80, "comment": "line1\\nline2"}'
        score, comment = self.fn(text)
        self.assertEqual(score, 80)
        self.assertIn('line1', comment)

    def test_return_types_are_int_and_str(self):
        score, comment = self.fn('{"score": 75, "comment": "ok"}')
        self.assertIsInstance(score, int)
        self.assertIsInstance(comment, str)


if __name__ == '__main__':
    unittest.main(verbosity=2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
