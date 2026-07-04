import json
import sys
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestIndexPageSidebarMarkup(unittest.TestCase):
    """GET / must render the left-sidebar layout (Recents history UI, 2026-07-02)
    for authenticated/guest visitors, but must still route anonymous visitors
    to home.html (which has no sidebar) per the identity state contract."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_logged_in_user_sees_sidebar_markup(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
                sess['username'] = 'alice'
            response = client.get('/')
        html = response.data.decode()
        for marker in ('left-sidebar', 'sidebar-overlay', 'main-content',
                       'app-layout', 'hamburger-btn', 'sidebarRecents'):
            self.assertIn(marker, html, msg=f'"{marker}" missing from logged-in index page')

    def test_guest_sees_sidebar_markup(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        html = response.data.decode()
        self.assertIn('left-sidebar', html)
        self.assertIn('hamburger-btn', html)

    def test_anonymous_visitor_does_not_get_index_sidebar(self):
        """Anonymous (no user_id, no is_guest) must render home.html, which has
        no sidebar — the identity router must not regress to always showing index.html."""
        with main.app.test_client() as client:
            response = client.get('/')
        html = response.data.decode()
        self.assertNotIn('left-sidebar', html)

    def test_sidebar_contains_new_chat_button(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        self.assertIn('newChatBtn', response.data.decode())

    def test_sidebar_skeleton_present_before_js_hydration(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        self.assertIn('sidebar-skeleton', response.data.decode())

    def test_is_logged_in_js_flag_true_for_authenticated_user(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        self.assertIn('const isLoggedIn = true;', response.data.decode())

    def test_is_logged_in_js_flag_false_for_guest(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        self.assertIn('const isLoggedIn = false;', response.data.decode())


class TestViewHistoryPage(unittest.TestCase):
    """GET /history/<history_id> (2026-07-03): the read-only history.html detail page.
    Logged-in visitors get the entry fetched from Firestore by id (with the usual ownership
    check delegated to get_chat_history_by_id); guests get an empty shell that hydrates
    client-side from sessionStorage (nothing to fetch/verify server-side for them, since
    guest history is never persisted); anonymous visitors must be routed away entirely,
    matching the identity state contract used by index()."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _fake_entry(self, **overrides):
        entry = {
            'id': 'hist1',
            'user_id': 'uid1',
            'prompt': 'What is 2+2?',
            'title': 'What is 2+2?',
            'results': [
                {
                    'provider': 'Yqcloud', 'success': True, 'response': 'It is 4.',
                    'error': '', 'response_time': 1.1, 'model': 'gpt-3.5-turbo',
                    'type': 'g4f', 'peer_reviews': [],
                }
            ],
            'is_pinned': False,
        }
        entry.update(overrides)
        return entry

    def test_anonymous_visitor_is_redirected_away(self):
        with main.app.test_client() as client:
            response = client.get('/history/some-id')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/', response.headers['Location'])

    def test_anonymous_visitor_never_reaches_history_html(self):
        with main.app.test_client() as client:
            response = client.get('/history/some-id', follow_redirects=True)
        self.assertNotIn('isGuestView', response.data.decode())

    @patch('main.get_chat_history_by_id')
    def test_logged_in_found_returns_200(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/hist1')
        self.assertEqual(response.status_code, 200)

    @patch('main.get_chat_history_by_id')
    def test_logged_in_found_calls_db_with_session_user_id_not_url(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            client.get('/history/hist1')
        mock_get.assert_called_once_with('uid1', 'hist1')

    @patch('main.get_chat_history_by_id')
    def test_logged_in_found_embeds_prompt_and_results_for_js(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/hist1')
        html = response.data.decode()
        self.assertIn('What is 2+2?', html)
        self.assertIn('It is 4.', html)

    @patch('main.get_chat_history_by_id')
    def test_logged_in_found_sets_is_guest_view_false(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/hist1')
        self.assertIn('isGuestView = false', response.data.decode())

    @patch('main.get_chat_history_by_id')
    def test_logged_in_not_found_redirects_to_index(self, mock_get):
        mock_get.return_value = None
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/ghost')
        self.assertEqual(response.status_code, 302)

    @patch('main.get_chat_history_by_id')
    def test_logged_in_not_found_flashes_message(self, mock_get):
        mock_get.return_value = None
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/ghost', follow_redirects=True)
        self.assertIn('History entry not found', response.data.decode())

    def test_guest_gets_200_without_querying_firestore(self):
        with patch('main.get_chat_history_by_id') as mock_get:
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['is_guest'] = True
                response = client.get('/history/guest-abc-123')
            mock_get.assert_not_called()
        self.assertEqual(response.status_code, 200)

    def test_guest_sets_is_guest_view_true(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/guest-abc-123')
        self.assertIn('isGuestView = true', response.data.decode())

    def test_guest_embeds_target_history_id_from_url(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/guest-abc-123')
        self.assertIn('"guest-abc-123"', response.data.decode())

    def test_guest_has_no_compare_form_markup(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/guest-abc-123')
        html = response.data.decode()
        self.assertNotIn('id="compareForm"', html)
        self.assertNotIn('id="providerSelection"', html)
        self.assertNotIn('id="customSelectWrapper"', html)
        self.assertNotIn('id="clearBtn"', html)

    def test_logged_in_found_has_no_compare_form_markup(self):
        with patch('main.get_chat_history_by_id', return_value=self._fake_entry()):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid1'
                response = client.get('/history/hist1')
        html = response.data.decode()
        self.assertNotIn('id="compareForm"', html)
        self.assertNotIn('id="providerSelection"', html)
        self.assertNotIn('id="customSelectWrapper"', html)
        self.assertNotIn('id="clearBtn"', html)

    def test_guest_sidebar_markup_present(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/guest-abc-123')
        html = response.data.decode()
        for marker in ('left-sidebar', 'sidebarRecents', 'newChatBtn'):
            self.assertIn(marker, html)


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

    def test_has_routing_rules_loaded(self):
        response = self.client.get('/health')
        data = json.loads(response.data)
        self.assertIn('routing_rules_loaded', data)
        self.assertIsInstance(data['routing_rules_loaded'], bool)

    def test_has_peer_review_rules_loaded(self):
        response = self.client.get('/health')
        data = json.loads(response.data)
        self.assertIn('peer_review_rules_loaded', data)
        self.assertIsInstance(data['peer_review_rules_loaded'], bool)


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


class TestGetImageProvidersEndpoint(unittest.TestCase):
    """Mirrors TestGetProvidersEndpoint above, for the independent image-generation
    Provider list (GET /api/image-providers, backed by IMAGE_PROVIDER_MODELS_MAP)."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_returns_200(self):
        response = self.client.get('/api/image-providers')
        self.assertEqual(response.status_code, 200)

    def test_returns_list(self):
        response = self.client.get('/api/image-providers')
        data = json.loads(response.data)
        self.assertIsInstance(data, list)

    def test_each_provider_has_required_fields(self):
        response = self.client.get('/api/image-providers')
        data = json.loads(response.data)
        required_fields = {'name', 'models', 'default_model', 'type', 'status'}
        for provider in data:
            for field in required_fields:
                self.assertIn(field, provider,
                    msg=f"Field '{field}' missing from image provider: {provider.get('name')}")

    def test_provider_default_model_is_in_models(self):
        response = self.client.get('/api/image-providers')
        data = json.loads(response.data)
        for provider in data:
            self.assertIn(provider['default_model'], provider['models'],
                msg=f"default_model not in models list for {provider.get('name')}")

    def test_provider_type_is_g4f_image(self):
        response = self.client.get('/api/image-providers')
        data = json.loads(response.data)
        for provider in data:
            self.assertEqual(provider['type'], 'g4f_image')

    def test_expected_five_researched_providers_present(self):
        """The five combinations confirmed usable in availability_g4f's 2026-07-03 research
        (see availability_g4f/available_free_image_providers.txt) must all be exposed."""
        response = self.client.get('/api/image-providers')
        data = json.loads(response.data)
        names = {p['name'] for p in data}
        self.assertEqual(names, {
            'PollinationsImage', 'BlackForestLabs_Flux1Dev', 'AnyProvider',
            'StabilityAI_SD35Large', 'OperaAria',
        })


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

    @patch('main.g4f.ChatCompletion.create')
    def test_route_prompts_map_applied_in_test_single(self, mock_create):
        mock_create.return_value = 'ok'
        with patch('main.ROUTE_PROMPTS_MAP', {('Yqcloud', 'gpt-3.5-turbo'): '[ROUTE_SUFFIX]'}), \
             patch('main.PROVIDER_MODELS_MAP', {'Yqcloud': ['gpt-3.5-turbo'], 'OperaAria': ['aria']}):
            payload = {'prompt': 'hello world', 'provider': 'Yqcloud', 'model': 'gpt-3.5-turbo'}
            response = self.client.post(
                '/api/test-single',
                data=json.dumps(payload),
                content_type='application/json'
            )
        self.assertEqual(response.status_code, 200)
        sent_content = mock_create.call_args[1]['messages'][0]['content']
        self.assertIn('[ROUTE_SUFFIX]', sent_content)
        self.assertTrue(sent_content.startswith('hello world'))

    @patch('main.g4f.ChatCompletion.create')
    @patch('main.detect_and_truncate', return_value='TRUNCATED_RESPONSE')
    def test_detect_and_truncate_applied_in_test_single(self, mock_truncate, mock_create):
        mock_create.return_value = 'very long repetitive response'
        payload = {'prompt': 'test', 'provider': 'Yqcloud'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['response'], 'TRUNCATED_RESPONSE')
        mock_truncate.assert_called_once()


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


def _fake_image(url=None, b64_json=None):
    image = MagicMock()
    image.url = url
    image.b64_json = b64_json
    return image


def _fake_image_response(images):
    response = MagicMock()
    response.data = images
    return response


class TestGenerateImagesEndpoint(unittest.TestCase):
    """POST /api/generate-images -- the text-to-image counterpart of TestCompareEndpoint.
    Mocks main.G4FImageClient (the g4f.client.Client class reference) rather than
    main.g4f.ChatCompletion.create, since image generation is a completely separate g4f
    call path (see main.test_g4f_image_provider)."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    @patch('main.G4FImageClient')
    def test_success_returns_200(self, mock_client_cls):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

    @patch('main.G4FImageClient')
    def test_success_response_has_required_top_level_keys(self, mock_client_cls):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data)
        for key in ('prompt', 'total_providers', 'successful_providers', 'results'):
            self.assertIn(key, data)

    @patch('main.G4FImageClient')
    def test_no_history_id_field_leaked_into_image_response(self, mock_client_cls):
        """Image results are intentionally not persisted to chat history (see CLAUDE.md) --
        the response body must not carry a stray history_id key copied from /api/compare."""
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data)
        self.assertNotIn('history_id', data)

    @patch('main.G4FImageClient')
    def test_each_result_has_required_keys(self, mock_client_cls):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage', 'OperaAria']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data)
        required_keys = {'provider', 'success', 'url', 'b64_json', 'error', 'response_time', 'model', 'type'}
        for result in data['results']:
            self.assertTrue(required_keys.issubset(set(result.keys())),
                msg=f"Result missing keys: {required_keys - set(result.keys())}")

    @patch('main.G4FImageClient')
    def test_total_providers_matches_results_length(self, mock_client_cls):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage', 'OperaAria']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data)
        self.assertEqual(data['total_providers'], len(data['results']))

    @patch('main.G4FImageClient')
    def test_results_sorted_successful_before_failed(self, mock_client_cls):
        def side_effect(**kwargs):
            provider_arg = kwargs.get('provider')
            if provider_arg.__name__ == 'PollinationsImage':
                return _fake_image_response([_fake_image(url='https://example.com/a.png')])
            raise Exception('Simulated failure')

        mock_client_cls.return_value.images.generate.side_effect = side_effect

        payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage', 'OperaAria']}
        response = self.client.post(
            '/api/generate-images',
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

    @patch('main.G4FImageClient')
    def test_prompt_field_in_response_matches_input(self, mock_client_cls):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        original_prompt = 'a small cactus wearing sunglasses'
        payload = {'prompt': original_prompt, 'providers': ['PollinationsImage']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data)
        self.assertEqual(data['prompt'], original_prompt)

    @patch('main.G4FImageClient')
    def test_no_providers_selected_defaults_to_all_image_providers(self, mock_client_cls):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        payload = {'prompt': 'a red apple'}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data)
        self.assertEqual(data['total_providers'], len(main.IMAGE_PROVIDERS))

    def test_missing_prompt_returns_400(self):
        payload = {'providers': ['PollinationsImage']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_empty_body_returns_400(self):
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_no_valid_providers_in_list_returns_400(self):
        payload = {'prompt': 'a red apple', 'providers': ['GhostImageProvider']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    @patch('main.G4F_AVAILABLE', False)
    def test_g4f_unavailable_returns_503(self):
        payload = {'prompt': 'a red apple'}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 503)

    @patch('main.G4FImageClient')
    def test_provider_error_message_surfaced_on_failure(self, mock_client_cls):
        mock_client_cls.return_value.images.generate.side_effect = RuntimeError('backend exploded')
        payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data)
        self.assertFalse(data['results'][0]['success'])
        self.assertIn('backend exploded', data['results'][0]['error'])

    @patch('main.G4FImageClient')
    def test_gpu_quota_error_surfaced_as_friendly_message(self, mock_client_cls):
        """StabilityAI_SD35Large / BlackForestLabs_Flux1Dev run on HuggingFace ZeroGPU
        Spaces and raise a raw JSON-ish 'ZeroGPU quota' error once the free quota is
        exhausted -- end to end through the route, that must come back as an
        actionable friendly message, not the raw payload."""
        mock_client_cls.return_value.images.generate.side_effect = Exception(
            'GPU token limit exceeded: data: {"error": "You have exceeded your '
            'ZeroGPU quota (65s requested vs. 0s left)."}'
        )
        payload = {'prompt': 'a red apple', 'providers': ['StabilityAI_SD35Large']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data)
        self.assertFalse(data['results'][0]['success'])
        self.assertIn('GPU quota', data['results'][0]['error'])
        self.assertNotIn('ZeroGPU', data['results'][0]['error'])

    @patch('main.G4FImageClient')
    def test_text_provider_name_is_rejected_as_invalid(self, mock_client_cls):
        """Providers is scoped to IMAGE_PROVIDERS, not G4F_PROVIDERS -- passing a valid
        text-chat provider name here must not silently match anything."""
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        payload = {'prompt': 'a red apple', 'providers': ['Yqcloud']}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)


class TestServeGeneratedMediaEndpoint(unittest.TestCase):
    """GET /media/<filename> -- static file route added to serve the images that
    g4f.client.Client().images.generate() already downloads to get_media_dir() before
    returning. Image Result DTO 'url' values look like '/media/<filename>?url=<original>';
    without this route the frontend <img> tag and the download button both 404 (and the
    download button ends up saving the 404 HTML body as if it were image bytes)."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.get_media_dir_patcher = patch('main.get_media_dir', return_value=self.tmpdir.name)
        self.get_media_dir_patcher.start()
        self.addCleanup(self.get_media_dir_patcher.stop)

    def _write_file(self, name, content=b'fake-image-bytes'):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_existing_file_returns_200_with_bytes(self):
        self._write_file('sample.jpg', b'jpeg-bytes')
        response = self.client.get('/media/sample.jpg')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b'jpeg-bytes')

    def test_query_string_from_g4f_url_field_is_ignored_not_required(self):
        """Result DTO url values carry a '?url=<original external url>' suffix; the route
        must serve the local file regardless of (and without needing) that query string."""
        self._write_file('sample.png', b'png-bytes')
        response = self.client.get('/media/sample.png?url=https://example.com/original.png')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b'png-bytes')

    def test_missing_file_returns_404(self):
        response = self.client.get('/media/does-not-exist.jpg')
        self.assertEqual(response.status_code, 404)

    def test_content_type_matches_extension(self):
        self._write_file('sample.jpg', b'jpeg-bytes')
        response = self.client.get('/media/sample.jpg')
        self.assertIn('image/jpeg', response.content_type)

    def test_path_traversal_outside_media_dir_is_blocked(self):
        """A crafted filename must not escape get_media_dir() to read arbitrary files
        (e.g. main.py) elsewhere on disk."""
        response = self.client.get('/media/..%2F..%2Fmain.py')
        self.assertNotEqual(response.status_code, 200)

    def test_path_traversal_dotdot_segment_is_blocked(self):
        response = self.client.get('/media/%2e%2e/%2e%2e/main.py')
        self.assertNotEqual(response.status_code, 200)


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
    def test_default_judge_prefix_used_when_model_not_in_peer_review_map(self, mock_create):
        """When a reviewer model has no PEER_REVIEW_PROMPTS_MAP entry, the English
        default judge prefix must be prepended to the review prompt sent to g4f."""
        providers = self._make_two_providers()
        mock_create.return_value = '{"score": 80, "comment": "ok"}'
        with patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProviderA': ['gpt-3.5-turbo'], 'ProviderB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', {}):
            response = self._post_compare(providers=['ProviderA', 'ProviderB'])
        self.assertEqual(response.status_code, 200)
        sent_contents = [
            call.kwargs['messages'][0]['content'] for call in mock_create.call_args_list
        ]
        default_prefix_calls = [
            c for c in sent_contents
            if c.startswith('Please evaluate the quality of the following answer')
        ]
        self.assertEqual(len(default_prefix_calls), 2)

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


@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestCompareHistoryPersistence(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    @patch('main.save_chat_history')
    @patch('main.g4f.ChatCompletion.create')
    def test_logged_in_user_triggers_save_with_correct_args(self, mock_create, mock_save):
        mock_create.return_value = 'Mock response'
        mock_save.return_value = {'id': 'hist123'}
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            payload = {'prompt': 'Hello', 'providers': ['Yqcloud']}
            response = client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )
        self.assertEqual(response.status_code, 200)
        mock_save.assert_called_once()
        self.assertEqual(mock_save.call_args[0][0], 'uid1')
        self.assertEqual(mock_save.call_args[0][1], 'Hello')

    @patch('main.save_chat_history')
    @patch('main.g4f.ChatCompletion.create')
    def test_response_includes_history_id_from_save(self, mock_create, mock_save):
        mock_create.return_value = 'Mock response'
        mock_save.return_value = {'id': 'hist123'}
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            payload = {'prompt': 'Hello', 'providers': ['Yqcloud']}
            response = client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )
        data = json.loads(response.data)
        self.assertEqual(data['history_id'], 'hist123')

    @patch('main.save_chat_history')
    @patch('main.g4f.ChatCompletion.create')
    def test_guest_does_not_trigger_save(self, mock_create, mock_save):
        mock_create.return_value = 'Mock response'
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            payload = {'prompt': 'Hello', 'providers': ['Yqcloud']}
            response = client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )
        mock_save.assert_not_called()
        data = json.loads(response.data)
        self.assertIsNone(data['history_id'])

    @patch('main.save_chat_history')
    @patch('main.g4f.ChatCompletion.create')
    def test_anonymous_does_not_trigger_save(self, mock_create, mock_save):
        mock_create.return_value = 'Mock response'
        with main.app.test_client() as client:
            payload = {'prompt': 'Hello', 'providers': ['Yqcloud']}
            response = client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )
        mock_save.assert_not_called()
        data = json.loads(response.data)
        self.assertIsNone(data['history_id'])


class TestHistoryAuthGuard(unittest.TestCase):
    """All /api/history* routes must reject unauthenticated (incl. guest) requests with 401."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_get_history_without_session_returns_401(self):
        response = self.client.get('/api/history')
        self.assertEqual(response.status_code, 401)

    def test_get_history_as_guest_returns_401(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/api/history')
        self.assertEqual(response.status_code, 401)

    def test_update_title_without_session_returns_401(self):
        response = self.client.patch(
            '/api/history/hist1/title',
            data=json.dumps({'new_title': 'New'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)

    def test_delete_without_session_returns_401(self):
        response = self.client.delete('/api/history/hist1')
        self.assertEqual(response.status_code, 401)

    def test_toggle_pin_without_session_returns_401(self):
        response = self.client.post('/api/history/hist1/toggle-pin')
        self.assertEqual(response.status_code, 401)

    def test_401_response_has_error_json(self):
        response = self.client.get('/api/history')
        data = json.loads(response.data)
        self.assertIn('error', data)


class TestGetHistoryEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    @patch('main.get_chat_history_list')
    def test_returns_200_when_logged_in(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            response = client.get('/api/history')
        self.assertEqual(response.status_code, 200)

    @patch('main.get_chat_history_list')
    def test_response_contains_history_list(self, mock_get_list):
        mock_get_list.return_value = [{'id': 'h1', 'title': 'x...'}]
        with main.app.test_client() as client:
            self._login(client)
            response = client.get('/api/history')
        data = json.loads(response.data)
        self.assertEqual(data['history'], [{'id': 'h1', 'title': 'x...'}])

    @patch('main.get_chat_history_list')
    def test_default_pagination_values(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            client.get('/api/history')
        mock_get_list.assert_called_once_with('uid1', limit=20, offset=0)

    @patch('main.get_chat_history_list')
    def test_page_and_limit_params_map_to_offset(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            client.get('/api/history?page=3&limit=10')
        mock_get_list.assert_called_once_with('uid1', limit=10, offset=20)

    @patch('main.get_chat_history_list')
    def test_limit_clamped_to_100(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            client.get('/api/history?limit=99999')
        mock_get_list.assert_called_once_with('uid1', limit=100, offset=0)

    @patch('main.get_chat_history_list')
    def test_page_below_one_clamped_to_one(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            client.get('/api/history?page=0')
        mock_get_list.assert_called_once_with('uid1', limit=20, offset=0)

    @patch('main.get_chat_history_list')
    def test_non_numeric_query_params_fall_back_to_defaults(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            client.get('/api/history?page=abc&limit=xyz')
        mock_get_list.assert_called_once_with('uid1', limit=20, offset=0)

    @patch('main.get_chat_history_list', side_effect=RuntimeError('boom'))
    def test_internal_error_returns_500_friendly_message(self, mock_get_list):
        with main.app.test_client() as client:
            self._login(client)
            response = client.get('/api/history')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Service temporarily unavailable. Please try again later.')


class TestUpdateHistoryTitleEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def _patch_title(self, client, history_id, new_title):
        return client.patch(
            f'/api/history/{history_id}/title',
            data=json.dumps({'new_title': new_title}),
            content_type='application/json'
        )

    @patch('main.update_chat_history_title', return_value=True)
    def test_success_returns_200(self, mock_update):
        with main.app.test_client() as client:
            self._login(client)
            response = self._patch_title(client, 'hist1', 'New Title')
        self.assertEqual(response.status_code, 200)

    @patch('main.update_chat_history_title', return_value=True)
    def test_calls_update_with_session_user_id_not_client_supplied(self, mock_update):
        with main.app.test_client() as client:
            self._login(client, user_id='uid1')
            self._patch_title(client, 'hist1', 'New Title')
        mock_update.assert_called_once_with('uid1', 'hist1', 'New Title')

    @patch('main.update_chat_history_title', return_value=False)
    def test_not_owned_or_missing_returns_404(self, mock_update):
        with main.app.test_client() as client:
            self._login(client)
            response = self._patch_title(client, 'ghost', 'New Title')
        self.assertEqual(response.status_code, 404)

    def test_missing_new_title_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            response = client.patch(
                '/api/history/hist1/title',
                data=json.dumps({}),
                content_type='application/json'
            )
        self.assertEqual(response.status_code, 400)

    def test_blank_new_title_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            response = self._patch_title(client, 'hist1', '   ')
        self.assertEqual(response.status_code, 400)

    @patch('main.update_chat_history_title', side_effect=RuntimeError('boom'))
    def test_internal_error_returns_500_friendly_message(self, mock_update):
        with main.app.test_client() as client:
            self._login(client)
            response = self._patch_title(client, 'hist1', 'New Title')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Service temporarily unavailable. Please try again later.')


class TestDeleteHistoryEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    @patch('main.delete_chat_history', return_value=True)
    def test_success_returns_200(self, mock_delete):
        with main.app.test_client() as client:
            self._login(client)
            response = client.delete('/api/history/hist1')
        self.assertEqual(response.status_code, 200)

    @patch('main.delete_chat_history', return_value=True)
    def test_calls_delete_with_session_user_id(self, mock_delete):
        with main.app.test_client() as client:
            self._login(client, user_id='uid1')
            client.delete('/api/history/hist1')
        mock_delete.assert_called_once_with('uid1', 'hist1')

    @patch('main.delete_chat_history', return_value=False)
    def test_not_owned_or_missing_returns_404(self, mock_delete):
        with main.app.test_client() as client:
            self._login(client)
            response = client.delete('/api/history/ghost')
        self.assertEqual(response.status_code, 404)

    @patch('main.delete_chat_history', side_effect=RuntimeError('boom'))
    def test_internal_error_returns_500_friendly_message(self, mock_delete):
        with main.app.test_client() as client:
            self._login(client)
            response = client.delete('/api/history/hist1')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Service temporarily unavailable. Please try again later.')


class TestTogglePinEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    @patch('main.toggle_pin_chat_history', return_value=True)
    def test_success_returns_200(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/history/hist1/toggle-pin')
        self.assertEqual(response.status_code, 200)

    @patch('main.toggle_pin_chat_history', return_value=True)
    def test_response_contains_new_is_pinned_true(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/history/hist1/toggle-pin')
        data = json.loads(response.data)
        self.assertTrue(data['is_pinned'])

    @patch('main.toggle_pin_chat_history', return_value=False)
    def test_response_reflects_new_is_pinned_false_not_confused_with_failure(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/history/hist1/toggle-pin')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertFalse(data['is_pinned'])

    @patch('main.toggle_pin_chat_history', return_value=None)
    def test_not_owned_or_missing_returns_404(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/history/ghost/toggle-pin')
        self.assertEqual(response.status_code, 404)

    @patch('main.toggle_pin_chat_history', side_effect=RuntimeError('boom'))
    def test_internal_error_returns_500_friendly_message(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/history/hist1/toggle-pin')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Service temporarily unavailable. Please try again later.')


if __name__ == '__main__':
    unittest.main(verbosity=2)
