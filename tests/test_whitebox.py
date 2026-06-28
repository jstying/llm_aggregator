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
            mock_g4f.ChatCompletion.create.side_effect = RuntimeError('network failure')
            import main
            result = main.test_g4f_provider(mock_provider, 'ping', 'gpt-3.5-turbo')

        self.assertFalse(result['success'])
        self.assertIn('network failure', result['error'])
        self.assertEqual(result['response'], '')

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
