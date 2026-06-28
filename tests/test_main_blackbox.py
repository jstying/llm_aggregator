import json
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestHealthEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_returns_200(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)

    def test_status_is_healthy(self):
        response = self.client.get('/health')
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'healthy')

    def test_g4f_available_is_bool(self):
        response = self.client.get('/health')
        data = json.loads(response.data)
        self.assertIn('g4f_available', data)
        self.assertIsInstance(data['g4f_available'], bool)

    def test_has_timestamp(self):
        response = self.client.get('/health')
        data = json.loads(response.data)
        self.assertIn('timestamp', data)
        self.assertIsInstance(data['timestamp'], float)

    def test_has_providers_list(self):
        response = self.client.get('/health')
        data = json.loads(response.data)
        self.assertIn('providers', data)
        self.assertIsInstance(data['providers'], list)


class TestGetProvidersEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_returns_200(self):
        response = self.client.get('/api/providers')
        self.assertEqual(response.status_code, 200)

    def test_returns_list(self):
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        self.assertIsInstance(data, list)

    def test_each_provider_has_required_fields(self):
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        required_fields = {'name', 'models', 'default_model', 'type', 'status'}
        for provider in data:
            for field in required_fields:
                self.assertIn(field, provider,
                    msg=f"Field '{field}' missing from provider: {provider.get('name')}")

    def test_provider_name_is_string(self):
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        for provider in data:
            self.assertIsInstance(provider['name'], str)
            self.assertGreater(len(provider['name']), 0)

    def test_provider_models_is_nonempty_list(self):
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        for provider in data:
            self.assertIsInstance(provider['models'], list)
            self.assertGreater(len(provider['models']), 0)

    def test_provider_default_model_is_in_models(self):
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        for provider in data:
            self.assertIn(provider['default_model'], provider['models'],
                msg=f"default_model not in models list for {provider.get('name')}")

    def test_provider_type_is_g4f(self):
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        for provider in data:
            self.assertEqual(provider['type'], 'g4f')

    def test_provider_status_is_available(self):
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        for provider in data:
            self.assertEqual(provider['status'], 'available')


@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestTestSingleEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    @patch('main.g4f.ChatCompletion.create')
    def test_success_returns_200(self, mock_create):
        mock_create.return_value = 'Four.'

        payload = {'prompt': 'What is 2+2?', 'provider': 'Yqcloud'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)

    @patch('main.g4f.ChatCompletion.create')
    def test_success_response_body_has_required_keys(self, mock_create):
        mock_create.return_value = 'Four.'

        payload = {'prompt': 'What is 2+2?', 'provider': 'Yqcloud'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        required_keys = {'provider', 'success', 'response', 'error', 'response_time', 'model', 'type'}
        self.assertEqual(set(data.keys()), required_keys)

    @patch('main.g4f.ChatCompletion.create')
    def test_success_fields_are_correct_types(self, mock_create):
        mock_create.return_value = 'Four.'

        payload = {'prompt': 'What is 2+2?', 'provider': 'Yqcloud'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        self.assertIsInstance(data['success'], bool)
        self.assertTrue(data['success'])
        self.assertIsInstance(data['response'], str)
        self.assertIsInstance(data['response_time'], float)
        self.assertEqual(data['provider'], 'Yqcloud')
        self.assertEqual(data['type'], 'g4f')

    @patch('main.g4f.ChatCompletion.create')
    def test_success_model_falls_back_to_default(self, mock_create):
        mock_create.return_value = 'Four.'

        payload = {'prompt': 'Test', 'provider': 'Yqcloud'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        expected_default = main.PROVIDER_MODELS_MAP['Yqcloud'][0]
        self.assertEqual(data['model'], expected_default)

    @patch('main.g4f.ChatCompletion.create')
    def test_success_with_explicit_supported_model(self, mock_create):
        mock_create.return_value = 'Result.'

        payload = {'prompt': 'Test', 'provider': 'Yqcloud', 'model': 'gpt-4'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        self.assertEqual(data['model'], 'gpt-4')

    def test_missing_prompt_returns_400(self):
        payload = {'provider': 'Yqcloud'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_missing_provider_returns_400(self):
        payload = {'prompt': 'What is 2+2?'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_empty_body_returns_400(self):
        response = self.client.post(
            '/api/test-single',
            data=json.dumps({}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_unknown_provider_returns_404(self):
        payload = {'prompt': 'What is 2+2?', 'provider': 'NonExistentProvider'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertIn('error', data)


@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestCompareEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    @patch('main.g4f.ChatCompletion.create')
    def test_success_returns_200(self, mock_create):
        mock_create.return_value = 'Mock response'

        payload = {'prompt': 'What is 2+2?', 'providers': ['Yqcloud', 'OperaAria']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)

    @patch('main.g4f.ChatCompletion.create')
    def test_success_response_has_required_top_level_keys(self, mock_create):
        mock_create.return_value = 'Mock response'

        payload = {'prompt': 'What is 2+2?', 'providers': ['Yqcloud']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        self.assertIn('prompt', data)
        self.assertIn('total_providers', data)
        self.assertIn('successful_providers', data)
        self.assertIn('results', data)

    @patch('main.g4f.ChatCompletion.create')
    def test_success_results_is_list(self, mock_create):
        mock_create.return_value = 'Mock response'

        payload = {'prompt': 'Hello', 'providers': ['Yqcloud']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        self.assertIsInstance(data['results'], list)

    @patch('main.g4f.ChatCompletion.create')
    def test_total_providers_matches_results_length(self, mock_create):
        mock_create.return_value = 'Mock response'

        payload = {'prompt': 'Test', 'providers': ['Yqcloud', 'OperaAria']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        self.assertEqual(data['total_providers'], len(data['results']))

    @patch('main.g4f.ChatCompletion.create')
    def test_each_result_has_required_keys(self, mock_create):
        mock_create.return_value = 'Mock response'

        payload = {'prompt': 'Hello', 'providers': ['Yqcloud', 'OperaAria']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        required_keys = {'provider', 'success', 'response', 'error', 'response_time', 'model', 'type'}
        for result in data['results']:
            self.assertTrue(required_keys.issubset(set(result.keys())),
                msg=f"Result missing keys: {required_keys - set(result.keys())}")

    @patch('main.g4f.ChatCompletion.create')
    def test_results_sorted_successful_before_failed(self, mock_create):
        def side_effect(*args, **kwargs):
            provider_arg = kwargs.get('provider')
            if provider_arg.__name__ == 'Yqcloud':
                return 'Success'
            raise Exception('Simulated failure')

        mock_create.side_effect = side_effect

        payload = {'prompt': 'Test', 'providers': ['Yqcloud', 'OperaAria']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        results = data['results']

        if len(results) > 1:
            found_failure = False
            for result in results:
                if not result['success']:
                    found_failure = True
                if found_failure:
                    self.assertFalse(result['success'],
                        msg='A successful result appeared after a failed one')

    @patch('main.g4f.ChatCompletion.create')
    def test_prompt_field_in_response_matches_input(self, mock_create):
        mock_create.return_value = 'Mock response'

        original_prompt = 'What is the speed of light?'
        payload = {'prompt': original_prompt, 'providers': ['Yqcloud']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        data = json.loads(response.data)
        self.assertEqual(data['prompt'], original_prompt)

    def test_missing_prompt_returns_400(self):
        payload = {'providers': ['Yqcloud']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_empty_body_returns_400(self):
        response = self.client.post(
            '/api/compare',
            data=json.dumps({}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_no_valid_providers_in_list_returns_400(self):
        payload = {'prompt': 'Test', 'providers': ['GhostProvider']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)


@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestPeerReview(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def _post_compare(self, providers=None, prompt='test prompt'):
        payload = {'prompt': prompt}
        if providers:
            payload['providers'] = providers
        return self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

    def _make_two_providers(self):
        p1 = MagicMock()
        p1.__name__ = 'ProviderA'
        p2 = MagicMock()
        p2.__name__ = 'ProviderB'
        return [p1, p2]

    @patch('main.g4f.ChatCompletion.create')
    def test_each_result_has_peer_reviews_field(self, mock_create):
        mock_create.return_value = 'response'
        response = self._post_compare(providers=['Yqcloud'])
        data = json.loads(response.data)
        for result in data['results']:
            self.assertIn('peer_reviews', result)

    @patch('main.g4f.ChatCompletion.create')
    def test_peer_reviews_empty_with_single_provider(self, mock_create):
        mock_create.return_value = 'response'
        response = self._post_compare(providers=['Yqcloud'])
        data = json.loads(response.data)
        for result in data['results']:
            self.assertEqual(result['peer_reviews'], [])

    @patch('main.g4f.ChatCompletion.create')
    def test_peer_reviews_triggered_when_two_succeed(self, mock_create):
        providers = self._make_two_providers()
        mock_create.return_value = 'test response'
        with patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProviderA': ['gpt-3.5-turbo'], 'ProviderB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', {'gpt-3.5-turbo': 'Review:', 'aria': 'Review:'}):
            response = self._post_compare(providers=['ProviderA', 'ProviderB'])
        data = json.loads(response.data)
        successful = [r for r in data['results'] if r['success']]
        self.assertGreaterEqual(len(successful), 2)
        for result in successful:
            self.assertGreater(len(result['peer_reviews']), 0)

    @patch('main.g4f.ChatCompletion.create')
    def test_peer_reviews_empty_when_only_one_succeeds(self, mock_create):
        providers = self._make_two_providers()

        def side_effect(*args, **kwargs):
            if kwargs.get('provider').__name__ == 'ProviderA':
                return 'success'
            raise Exception('simulated failure')

        mock_create.side_effect = side_effect
        with patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProviderA': ['gpt-3.5-turbo'], 'ProviderB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', {'gpt-3.5-turbo': 'Review:', 'aria': 'Review:'}):
            response = self._post_compare(providers=['ProviderA', 'ProviderB'])
        data = json.loads(response.data)
        for result in data['results']:
            self.assertEqual(result['peer_reviews'], [])

    @patch('main.g4f.ChatCompletion.create')
    def test_peer_review_item_has_required_fields(self, mock_create):
        providers = self._make_two_providers()
        mock_create.return_value = '{"score": 80, "comment": "ok"}'
        with patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProviderA': ['gpt-3.5-turbo'], 'ProviderB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', {'gpt-3.5-turbo': 'Review:', 'aria': 'Review:'}):
            response = self._post_compare(providers=['ProviderA', 'ProviderB'])
        data = json.loads(response.data)
        successful = [r for r in data['results'] if r['success']]
        required = {'reviewer_provider', 'reviewer_model', 'score', 'comment'}
        for result in successful:
            for item in result['peer_reviews']:
                self.assertTrue(required.issubset(set(item.keys())))

    @patch('main.g4f.ChatCompletion.create')
    def test_peer_review_score_is_integer(self, mock_create):
        providers = self._make_two_providers()
        mock_create.return_value = '{"score": 75, "comment": "decent"}'
        with patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProviderA': ['gpt-3.5-turbo'], 'ProviderB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', {'gpt-3.5-turbo': 'Review:', 'aria': 'Review:'}):
            response = self._post_compare(providers=['ProviderA', 'ProviderB'])
        data = json.loads(response.data)
        successful = [r for r in data['results'] if r['success']]
        for result in successful:
            for item in result['peer_reviews']:
                self.assertIsInstance(item['score'], int)

    @patch('main.g4f.ChatCompletion.create')
    def test_provider_does_not_review_itself(self, mock_create):
        providers = self._make_two_providers()
        mock_create.return_value = 'test response'
        with patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProviderA': ['gpt-3.5-turbo'], 'ProviderB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', {'gpt-3.5-turbo': 'Review:', 'aria': 'Review:'}):
            response = self._post_compare(providers=['ProviderA', 'ProviderB'])
        data = json.loads(response.data)
        for result in data['results']:
            for item in result['peer_reviews']:
                self.assertNotEqual(item['reviewer_provider'], result['provider'])

    @patch('main.g4f.ChatCompletion.create')
    def test_two_successful_providers_each_reviewed_once(self, mock_create):
        providers = self._make_two_providers()
        mock_create.return_value = 'test response'
        with patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProviderA': ['gpt-3.5-turbo'], 'ProviderB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', {'gpt-3.5-turbo': 'Review:', 'aria': 'Review:'}):
            response = self._post_compare(providers=['ProviderA', 'ProviderB'])
        data = json.loads(response.data)
        successful = [r for r in data['results'] if r['success']]
        if len(successful) == 2:
            for result in successful:
                self.assertEqual(len(result['peer_reviews']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
