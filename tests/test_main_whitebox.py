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
        self.assertIn('system is busy', result['error'])

    def test_rate_limit_error_shows_friendly_message(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.side_effect = Exception('rate limit exceeded')
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertFalse(result['success'])
        self.assertIn('system is busy', result['error'])

    def test_content_policy_error_shows_friendly_message(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            mock_g4f.ChatCompletion.create.side_effect = Exception(
                "Error 400: Error BAD_REQUEST: 400 Bad Request: azure-openai error: "
                "The response was filtered due to the prompt triggering Azure OpenAI's "
                "content management policy. Please modify your prompt and retry."
            )
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertFalse(result['success'])
        self.assertIn('content filter', result['error'])
        self.assertNotIn('system is busy', result['error'])

    def test_content_policy_error_takes_priority_over_network_wording(self):
        mock_provider = self._make_mock_provider('Yqcloud')
        with patch('main.PROVIDER_MODELS_MAP', MOCK_PROVIDER_MODELS_MAP), \
             patch('main.g4f') as mock_g4f:
            # "remote" (a network keyword) also appears alongside the content-filter
            # phrasing; content-policy wording must win since retrying won't help.
            mock_g4f.ChatCompletion.create.side_effect = Exception(
                'remote server: blocked by content_filter'
            )
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertIn('content filter', result['error'])

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
        self.assertIn('... (truncated automatically due to repeated content)', result)

    def test_sentence_truncation_preserves_first_two_occurrences(self):
        sentence = 'Repeat.'
        text = (sentence + ' ') * 4
        result = self.fn(text)
        self.assertEqual(result.count(sentence), 2)
        self.assertIn('... (truncated automatically due to repeated content)', result)

    def test_two_sentence_repetitions_do_not_trigger(self):
        text = 'Hello there! Hello there! This part is completely different.'
        result = self.fn(text)
        self.assertEqual(result, text)

    def test_window_repetition_triggers_truncation(self):
        chunk = 'abcdefghij'
        text = chunk * 3 + 'extra content that is unique'
        result = self.fn(text)
        self.assertIn('... (truncated automatically due to repeated content)', result)

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
        self.assertEqual(result, 'Content contains sensitive information and has been blocked.')


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
        self.assertIn('Review failed', result['comment'])

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
        self.assertIn('system is busy', result['comment'])

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    @patch('main.g4f.ChatCompletion.create')
    def test_content_policy_error_does_not_retry_and_returns_friendly_comment(
        self, mock_create, mock_rand, mock_sleep
    ):
        mock_create.side_effect = Exception(
            "Error 400: azure-openai error: The response was filtered due to the "
            "prompt triggering Azure OpenAI's content management policy."
        )
        import main
        result = main.run_peer_review(self._make_mock_provider(), 'gpt-4', 'prompt')
        self.assertEqual(mock_create.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn('content filter', result['comment'])
        self.assertNotIn('system is busy', result['comment'])

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    @patch('main.g4f.ChatCompletion.create')
    def test_advisory_timeout_passed_to_g4f_is_25_seconds(self, mock_create, mock_rand, mock_sleep):
        mock_create.return_value = '{"score": 88, "comment": "fine"}'
        import main
        main.run_peer_review(self._make_mock_provider(), 'gpt-4', 'prompt')
        self.assertEqual(mock_create.call_args.kwargs['timeout'], 25)

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


MOCK_IMAGE_PROVIDER_MODELS_MAP = {
    'PollinationsImage': ['auto'],
    'BlackForestLabs_Flux1Dev': ['flux-dev'],
    'OperaAria': ['aria'],
}


class TestDetermineActualImageModel(unittest.TestCase):
    """Image-generation counterpart of TestDetermineActualModel. Only rules A/B apply
    here (see main.determine_actual_image_model's docstring-style comment): there is no
    rule-C universal fallback model for text-to-image, since there's no image model name
    that works across arbitrary providers the way 'gpt-3.5-turbo' does for chat."""

    def setUp(self):
        self.patcher_map = patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP)
        self.patcher_map.start()

    def tearDown(self):
        self.patcher_map.stop()

    def test_rule_a_model_in_supported_list(self):
        import main
        result = main.determine_actual_image_model('BlackForestLabs_Flux1Dev', 'flux-dev')
        self.assertEqual(result, 'flux-dev')

    def test_rule_b_model_not_in_list_falls_back_to_first(self):
        import main
        result = main.determine_actual_image_model('OperaAria', 'gpt-4')
        self.assertEqual(result, 'aria')

    def test_rule_b_none_model_falls_back_to_first(self):
        import main
        result = main.determine_actual_image_model('PollinationsImage', None)
        self.assertEqual(result, 'auto')

    def test_unknown_provider_returns_none_no_universal_fallback(self):
        import main
        result = main.determine_actual_image_model('GhostImageProvider', None)
        self.assertIsNone(result)


class TestInitImageResultObject(unittest.TestCase):

    def setUp(self):
        import main
        self.init_image_result_object = main.init_image_result_object

    def test_key_set_is_complete(self):
        result = self.init_image_result_object('PollinationsImage', 'auto')
        expected_keys = {'provider', 'success', 'url', 'b64_json', 'error', 'response_time', 'model', 'type'}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_provider_and_model_fields(self):
        result = self.init_image_result_object('BlackForestLabs_Flux1Dev', 'flux-dev')
        self.assertEqual(result['provider'], 'BlackForestLabs_Flux1Dev')
        self.assertEqual(result['model'], 'flux-dev')

    def test_success_default_false(self):
        result = self.init_image_result_object('PollinationsImage', 'auto')
        self.assertFalse(result['success'])

    def test_url_and_b64_json_default_none(self):
        result = self.init_image_result_object('PollinationsImage', 'auto')
        self.assertIsNone(result['url'])
        self.assertIsNone(result['b64_json'])

    def test_error_default_empty_string(self):
        result = self.init_image_result_object('PollinationsImage', 'auto')
        self.assertEqual(result['error'], '')

    def test_response_time_default_zero(self):
        result = self.init_image_result_object('PollinationsImage', 'auto')
        self.assertEqual(result['response_time'], 0)

    def test_type_always_g4f_image(self):
        result = self.init_image_result_object('PollinationsImage', 'auto')
        self.assertEqual(result['type'], 'g4f_image')

    def test_no_extra_keys(self):
        result = self.init_image_result_object('PollinationsImage', 'auto')
        self.assertEqual(len(result), 8)

    def test_independent_instances(self):
        r1 = self.init_image_result_object('PollinationsImage', 'auto')
        r2 = self.init_image_result_object('OperaAria', 'aria')
        r1['success'] = True
        self.assertFalse(r2['success'])


class TestTestG4fImageProvider(unittest.TestCase):
    """test_g4f_image_provider() is a fully separate call path from test_g4f_provider():
    it goes through g4f.client.Client().images.generate() (patched here as
    main.G4FImageClient) rather than g4f.ChatCompletion.create(), and returns the 8-key
    image DTO rather than the 7-key text DTO."""

    def _make_mock_provider(self, name='PollinationsImage'):
        mock_provider = MagicMock()
        mock_provider.__name__ = name
        return mock_provider

    def _make_fake_client(self, data=None, side_effect=None):
        fake_client_instance = MagicMock()
        if side_effect is not None:
            fake_client_instance.images.generate.side_effect = side_effect
        else:
            fake_response = MagicMock()
            fake_response.data = data
            fake_client_instance.images.generate.return_value = fake_response
        return fake_client_instance

    def _make_fake_image(self, url=None, b64_json=None):
        image = MagicMock()
        image.url = url
        image.b64_json = b64_json
        return image

    def test_success_with_url_sets_correct_fields(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(data=[self._make_fake_image(url='https://example.com/a.png')])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'a red apple')

        self.assertTrue(result['success'])
        self.assertEqual(result['url'], 'https://example.com/a.png')
        self.assertIsNone(result['b64_json'])
        self.assertEqual(result['error'], '')
        self.assertEqual(result['provider'], 'PollinationsImage')
        self.assertEqual(result['model'], 'auto')
        self.assertEqual(result['type'], 'g4f_image')

    def test_success_with_b64_json_sets_correct_fields(self):
        mock_provider = self._make_mock_provider('OperaAria')
        fake_client = self._make_fake_client(data=[self._make_fake_image(b64_json='ZmFrZWJhc2U2NA==')])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'a blue whale', 'aria')

        self.assertTrue(result['success'])
        self.assertIsNone(result['url'])
        self.assertEqual(result['b64_json'], 'ZmFrZWJhc2U2NA==')

    def test_url_preferred_when_both_present(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(
            data=[self._make_fake_image(url='https://example.com/a.png', b64_json='ignored')]
        )
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertEqual(result['url'], 'https://example.com/a.png')
        self.assertIsNone(result['b64_json'])

    def test_empty_data_list_sets_error(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(data=[])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertFalse(result['success'])
        self.assertIn('no image data', result['error'])

    def test_no_url_or_b64_json_sets_error(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(data=[self._make_fake_image()])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertFalse(result['success'])
        self.assertIn('no image data', result['error'])

    def test_exception_sets_error_and_success_false(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(side_effect=RuntimeError('backend exploded'))
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertFalse(result['success'])
        self.assertIn('backend exploded', result['error'])

    def test_network_error_shows_friendly_message(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(side_effect=Exception('connection timed out'))
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertFalse(result['success'])
        self.assertIn('system is busy', result['error'])

    def test_response_time_is_positive_float(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(data=[self._make_fake_image(url='https://example.com/a.png')])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertIsInstance(result['response_time'], float)
        self.assertGreaterEqual(result['response_time'], 0)

    def test_model_omitted_from_kwargs_when_auto(self):
        """PollinationsImage's 'auto' placeholder must NOT be forwarded as model= to
        images.generate() -- it signals "let the provider use its own default", and g4f
        has no model literally named 'auto'."""
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(data=[self._make_fake_image(url='https://example.com/a.png')])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            main.test_g4f_image_provider(mock_provider, 'prompt')

        call_kwargs = fake_client.images.generate.call_args.kwargs
        self.assertNotIn('model', call_kwargs)

    def test_model_forwarded_when_not_auto(self):
        mock_provider = self._make_mock_provider('BlackForestLabs_Flux1Dev')
        fake_client = self._make_fake_client(data=[self._make_fake_image(url='https://example.com/a.png')])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            main.test_g4f_image_provider(mock_provider, 'prompt')

        call_kwargs = fake_client.images.generate.call_args.kwargs
        self.assertEqual(call_kwargs.get('model'), 'flux-dev')

    def test_provider_instance_forwarded_to_generate(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(data=[self._make_fake_image(url='https://example.com/a.png')])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            main.test_g4f_image_provider(mock_provider, 'prompt')

        call_kwargs = fake_client.images.generate.call_args.kwargs
        self.assertIs(call_kwargs.get('provider'), mock_provider)

    def test_result_key_set_on_success(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(data=[self._make_fake_image(url='https://example.com/a.png')])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        expected_keys = {'provider', 'success', 'url', 'b64_json', 'error', 'response_time', 'model', 'type'}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_result_key_set_on_exception(self):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = self._make_fake_client(side_effect=ValueError('bad input'))
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        expected_keys = {'provider', 'success', 'url', 'b64_json', 'error', 'response_time', 'model', 'type'}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_display_model_falls_back_to_default_for_unknown_provider(self):
        mock_provider = self._make_mock_provider('GhostImageProvider')
        fake_client = self._make_fake_client(data=[self._make_fake_image(url='https://example.com/a.png')])
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', {}), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertEqual(result['model'], 'default')

    def test_gpu_quota_error_shows_friendly_message(self):
        """StabilityAI_SD35Large / BlackForestLabs_Flux1Dev run on HuggingFace ZeroGPU
        Spaces: when the free GPU quota is exhausted they raise a raw JSON-ish error
        string mentioning 'ZeroGPU quota'. That must be translated into an actionable
        friendly message instead of leaking the raw payload to the frontend."""
        mock_provider = self._make_mock_provider('StabilityAI_SD35Large')
        fake_client = self._make_fake_client(side_effect=Exception(
            'GPU token limit exceeded: data: {"error": "You have exceeded your '
            'ZeroGPU quota (65s requested vs. 0s left)."}'
        ))
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertFalse(result['success'])
        self.assertIn('GPU quota', result['error'])
        self.assertNotIn('ZeroGPU', result['error'])
        self.assertNotIn('{"error"', result['error'])

    def test_gpu_quota_error_takes_priority_over_network_keywords(self):
        """'exceeded your zerogpu quota... 0s left' does not contain a NETWORK_ERROR_
        KEYWORDS hit today, but this pins the intended priority so a future keyword
        addition (e.g. 'unavailable') can't silently swap the message back to the
        generic 'system is busy' text."""
        mock_provider = self._make_mock_provider('BlackForestLabs_Flux1Dev')
        fake_client = self._make_fake_client(side_effect=Exception(
            'You have exceeded your ZeroGPU quota (112s requested vs. 0s left).'
        ))
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertIn('GPU quota', result['error'])
        self.assertNotIn('system is busy', result['error'])


class TestTestG4fImageProviderRetryAndTimeouts(unittest.TestCase):
    """Whitebox tests for the 429/queue retry logic and per-provider timeout lookup
    added to test_g4f_image_provider (mirrors run_peer_review's retry pattern)."""

    def _make_mock_provider(self, name='PollinationsImage'):
        mock_provider = MagicMock()
        mock_provider.__name__ = name
        return mock_provider

    def _make_fake_image(self, url=None, b64_json=None):
        image = MagicMock()
        image.url = url
        image.b64_json = b64_json
        return image

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    def test_429_triggers_retry_and_succeeds(self, mock_rand, mock_sleep):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_success = MagicMock()
        fake_success.data = [self._make_fake_image(url='https://example.com/a.png')]
        fake_client = MagicMock()
        fake_client.images.generate.side_effect = [
            Exception('Error 429: Queue full for IP'),
            fake_success,
        ]
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertEqual(fake_client.images.generate.call_count, 2)
        mock_sleep.assert_called_once()
        self.assertTrue(result['success'])
        self.assertEqual(result['url'], 'https://example.com/a.png')

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    def test_queue_full_error_also_triggers_retry(self, mock_rand, mock_sleep):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_success = MagicMock()
        fake_success.data = [self._make_fake_image(url='https://example.com/a.png')]
        fake_client = MagicMock()
        fake_client.images.generate.side_effect = [
            Exception('queue is full'),
            fake_success,
        ]
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertEqual(fake_client.images.generate.call_count, 2)
        self.assertTrue(result['success'])

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    def test_non_retryable_error_does_not_retry(self, mock_rand, mock_sleep):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = MagicMock()
        fake_client.images.generate.side_effect = Exception('backend exploded')
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertEqual(fake_client.images.generate.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertFalse(result['success'])
        self.assertIn('backend exploded', result['error'])

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    def test_gpu_quota_error_does_not_retry(self, mock_rand, mock_sleep):
        """Retrying immediately against an exhausted GPU quota just wastes another
        request against an already-strained free resource -- must not retry."""
        mock_provider = self._make_mock_provider('StabilityAI_SD35Large')
        fake_client = MagicMock()
        fake_client.images.generate.side_effect = Exception(
            'You have exceeded your ZeroGPU quota (65s requested vs. 0s left).'
        )
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertEqual(fake_client.images.generate.call_count, 1)
        mock_sleep.assert_not_called()

    @patch('main.time.sleep', return_value=None)
    @patch('main.random.uniform', return_value=0.5)
    def test_429_then_retry_also_fails_returns_friendly_message(self, mock_rand, mock_sleep):
        mock_provider = self._make_mock_provider('PollinationsImage')
        fake_client = MagicMock()
        fake_client.images.generate.side_effect = [
            Exception('Error 429: Queue full'),
            Exception('Error 429: Queue full'),
        ]
        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            result = main.test_g4f_image_provider(mock_provider, 'prompt')

        self.assertEqual(fake_client.images.generate.call_count, 2)
        self.assertIn('system is busy', result['error'])

    def test_any_provider_gets_longer_advisory_timeout_forwarded(self):
        """AnyProvider is a g4f meta/aggregator provider that internally chains through
        several real backends, so it must be given the longer IMAGE_PROVIDER_TIMEOUT_
        OVERRIDES advisory budget rather than the default -- otherwise g4f itself may
        give up on it before the underlying (legitimately slower) call can finish."""
        mock_provider = self._make_mock_provider('AnyProvider')
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.data = [self._make_fake_image(url='https://example.com/a.png')]
        fake_client.images.generate.return_value = fake_response

        with patch('main.IMAGE_PROVIDER_MODELS_MAP', MOCK_IMAGE_PROVIDER_MODELS_MAP), \
             patch('main.G4FImageClient', return_value=fake_client):
            import main
            main.test_g4f_image_provider(mock_provider, 'prompt')

        call_kwargs = fake_client.images.generate.call_args.kwargs
        expected_advisory, _ = main.get_image_timeouts('AnyProvider')
        self.assertEqual(call_kwargs.get('timeout'), expected_advisory)
        self.assertGreater(expected_advisory, main.IMAGE_GENERATION_ADVISORY_TIMEOUT)


if __name__ == '__main__':
    unittest.main(verbosity=2)
