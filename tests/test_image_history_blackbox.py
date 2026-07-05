"""Black-box (HTTP-level) tests for the image-history feature (2026-07-04):
- GET /image-history/<id> (read-only detail page, logged-in only -- no guest shell, unlike
  the chat /history/<id> route)
- /api/image-history* CRUD routes (guarded by the same _get_authenticated_user_id() as
  /api/history*, so guests/anonymous get 401)
- POST /api/generate-images now persisting to image_history for logged-in users and
  returning a history_id (mirrors tests/test_main_blackbox.py's TestCompareHistoryPersistence
  for the text/chat path)
- GET / sidebar markup for the image-mode Recents list (mode-switch entry points)

These mirror the equivalent classes in tests/test_main_blackbox.py
(TestViewHistoryPage/TestHistoryAuthGuard/TestGetHistoryEndpoint/
TestUpdateHistoryTitleEndpoint/TestDeleteHistoryEndpoint/TestTogglePinEndpoint) one-for-one
where the underlying behavior is the same, adjusted for the deliberate differences (no guest
path at all here).
"""
import json
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from test_main_blackbox import _fake_image, _fake_image_response  # noqa: E402


class TestViewImageHistoryPage(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _fake_entry(self, **overrides):
        entry = {
            'id': 'imghist1',
            'user_id': 'uid1',
            'prompt': 'a red apple on a table',
            'title': 'a red apple on ...',
            'results': [
                {
                    'provider': 'PollinationsImage', 'success': True,
                    'url': 'https://example.com/a.png', 'b64_json': None,
                    'error': '', 'response_time': 1.1, 'model': 'auto',
                    'type': 'g4f_image',
                }
            ],
            'is_pinned': False,
        }
        entry.update(overrides)
        return entry

    def test_anonymous_visitor_is_redirected_away(self):
        with main.app.test_client() as client:
            response = client.get('/image-history/some-id')
        self.assertEqual(response.status_code, 302)

    def test_anonymous_visitor_never_reaches_image_history_html(self):
        with main.app.test_client() as client:
            response = client.get('/image-history/some-id', follow_redirects=True)
        self.assertNotIn('Image Generation History', response.data.decode())

    def test_guest_is_redirected_away(self):
        """Unlike /history/<id>, guests get no client-side shell here at all -- image
        generation history is never persisted for them, not even ephemerally, so there is
        nothing to render."""
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/image-history/some-id')
        self.assertEqual(response.status_code, 302)

    def test_guest_never_queries_firestore(self):
        with patch('main.get_image_history_by_id') as mock_get:
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['is_guest'] = True
                client.get('/image-history/some-id')
            mock_get.assert_not_called()

    @patch('main.get_image_history_by_id')
    def test_logged_in_found_returns_200(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        self.assertEqual(response.status_code, 200)

    @patch('main.get_image_history_by_id')
    def test_logged_in_found_calls_db_with_session_user_id_not_url(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            client.get('/image-history/imghist1')
        mock_get.assert_called_once_with('uid1', 'imghist1')

    @patch('main.get_image_history_by_id')
    def test_logged_in_found_embeds_prompt_and_image_url_for_js(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        html = response.data.decode()
        self.assertIn('a red apple on a table', html)
        self.assertIn('https://example.com/a.png', html)

    @patch('main.get_image_history_by_id')
    def test_logged_in_not_found_redirects_to_index(self, mock_get):
        mock_get.return_value = None
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/ghost')
        self.assertEqual(response.status_code, 302)

    @patch('main.get_image_history_by_id')
    def test_logged_in_not_found_flashes_message(self, mock_get):
        mock_get.return_value = None
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/ghost', follow_redirects=True)
        self.assertIn('Image history entry not found', response.data.decode())

    @patch('main.get_image_history_by_id')
    def test_logged_in_found_has_no_form_markup(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        html = response.data.decode()
        self.assertNotIn('id="imageForm"', html)
        self.assertNotIn('id="imageProviderSelection"', html)

    @patch('main.get_image_history_by_id')
    def test_logged_in_found_sidebar_markup_present(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        html = response.data.decode()
        for marker in ('left-sidebar', 'sidebarRecents', 'newChatBtn', 'generateImageBtn'):
            self.assertIn(marker, html)


class TestImageHistoryAuthGuard(unittest.TestCase):
    """All /api/image-history* routes must reject unauthenticated (incl. guest) requests
    with 401 -- same guard function as /api/history* (_get_authenticated_user_id)."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_get_image_history_without_session_returns_401(self):
        response = self.client.get('/api/image-history')
        self.assertEqual(response.status_code, 401)

    def test_get_image_history_as_guest_returns_401(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/api/image-history')
        self.assertEqual(response.status_code, 401)

    def test_update_title_without_session_returns_401(self):
        response = self.client.patch(
            '/api/image-history/imghist1/title',
            data=json.dumps({'new_title': 'New'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)

    def test_delete_without_session_returns_401(self):
        response = self.client.delete('/api/image-history/imghist1')
        self.assertEqual(response.status_code, 401)

    def test_toggle_pin_without_session_returns_401(self):
        response = self.client.post('/api/image-history/imghist1/toggle-pin')
        self.assertEqual(response.status_code, 401)


class TestGetImageHistoryEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    @patch('main.get_image_history_list')
    def test_returns_200_when_logged_in(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            response = client.get('/api/image-history')
        self.assertEqual(response.status_code, 200)

    @patch('main.get_image_history_list')
    def test_response_contains_history_list(self, mock_get_list):
        mock_get_list.return_value = [{'id': 'h1', 'title': 'x...'}]
        with main.app.test_client() as client:
            self._login(client)
            response = client.get('/api/image-history')
        data = json.loads(response.data)
        self.assertEqual(data['history'], [{'id': 'h1', 'title': 'x...'}])

    @patch('main.get_image_history_list')
    def test_default_pagination_values(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            client.get('/api/image-history')
        mock_get_list.assert_called_once_with('uid1', limit=20, offset=0)

    @patch('main.get_image_history_list')
    def test_page_and_limit_params_map_to_offset(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            client.get('/api/image-history?page=3&limit=10')
        mock_get_list.assert_called_once_with('uid1', limit=10, offset=20)

    @patch('main.get_image_history_list')
    def test_limit_clamped_to_100(self, mock_get_list):
        mock_get_list.return_value = []
        with main.app.test_client() as client:
            self._login(client)
            client.get('/api/image-history?limit=99999')
        mock_get_list.assert_called_once_with('uid1', limit=100, offset=0)

    @patch('main.get_image_history_list', side_effect=RuntimeError('boom'))
    def test_internal_error_returns_500_friendly_message(self, mock_get_list):
        with main.app.test_client() as client:
            self._login(client)
            response = client.get('/api/image-history')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Service temporarily unavailable. Please try again later.')


class TestUpdateImageHistoryTitleEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def _patch_title(self, client, history_id, new_title):
        return client.patch(
            f'/api/image-history/{history_id}/title',
            data=json.dumps({'new_title': new_title}),
            content_type='application/json'
        )

    @patch('main.update_image_history_title', return_value=True)
    def test_success_returns_200(self, mock_update):
        with main.app.test_client() as client:
            self._login(client)
            response = self._patch_title(client, 'imghist1', 'New Title')
        self.assertEqual(response.status_code, 200)

    @patch('main.update_image_history_title', return_value=True)
    def test_calls_update_with_session_user_id_not_client_supplied(self, mock_update):
        with main.app.test_client() as client:
            self._login(client, user_id='uid1')
            self._patch_title(client, 'imghist1', 'New Title')
        mock_update.assert_called_once_with('uid1', 'imghist1', 'New Title')

    @patch('main.update_image_history_title', return_value=False)
    def test_not_owned_or_missing_returns_404(self, mock_update):
        with main.app.test_client() as client:
            self._login(client)
            response = self._patch_title(client, 'ghost', 'New Title')
        self.assertEqual(response.status_code, 404)

    def test_missing_new_title_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            response = client.patch(
                '/api/image-history/imghist1/title',
                data=json.dumps({}),
                content_type='application/json'
            )
        self.assertEqual(response.status_code, 400)

    def test_blank_new_title_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            response = self._patch_title(client, 'imghist1', '   ')
        self.assertEqual(response.status_code, 400)


class TestDeleteImageHistoryEndpoint(unittest.TestCase):
    """2026-07-05: deleting an image history entry also cleans up the local
    get_media_dir() files its g4f results referenced -- since the Firestore record is
    gone, nothing can 404 by removing them, and it stops generated_media from growing
    forever for entries the user has explicitly deleted (see CLAUDE.md's known-limitation
    note on unbounded local disk growth)."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def _fake_entry(self, **overrides):
        entry = {
            'id': 'imghist1',
            'user_id': 'uid1',
            'prompt': 'a red apple on a table',
            'title': 'a red apple on ...',
            'results': [
                {
                    'provider': 'PollinationsImage', 'success': True,
                    'url': '/media/abc123.jpg?url=https://example.com/a.png',
                    'b64_json': None, 'error': '', 'response_time': 1.1,
                    'model': 'auto', 'type': 'g4f_image',
                }
            ],
            'is_pinned': False,
        }
        entry.update(overrides)
        return entry

    @patch('main.get_image_history_by_id', return_value=None)
    @patch('main.delete_image_history', return_value=True)
    def test_success_returns_200(self, mock_delete, mock_get):
        with main.app.test_client() as client:
            self._login(client)
            response = client.delete('/api/image-history/imghist1')
        self.assertEqual(response.status_code, 200)

    @patch('main.get_image_history_by_id', return_value=None)
    @patch('main.delete_image_history', return_value=True)
    def test_calls_delete_with_session_user_id(self, mock_delete, mock_get):
        with main.app.test_client() as client:
            self._login(client, user_id='uid1')
            client.delete('/api/image-history/imghist1')
        mock_delete.assert_called_once_with('uid1', 'imghist1')

    @patch('main.get_image_history_by_id', return_value=None)
    @patch('main.delete_image_history', return_value=False)
    def test_not_owned_or_missing_returns_404(self, mock_delete, mock_get):
        with main.app.test_client() as client:
            self._login(client)
            response = client.delete('/api/image-history/ghost')
        self.assertEqual(response.status_code, 404)

    @patch('main._delete_local_media_files_for_image_results')
    @patch('main.get_image_history_by_id')
    @patch('main.delete_image_history', return_value=True)
    def test_success_triggers_local_media_cleanup_with_entrys_results(
        self, mock_delete, mock_get, mock_cleanup
    ):
        entry = self._fake_entry()
        mock_get.return_value = entry
        with main.app.test_client() as client:
            self._login(client)
            response = client.delete('/api/image-history/imghist1')
        self.assertEqual(response.status_code, 200)
        mock_cleanup.assert_called_once_with(entry['results'])

    @patch('main._delete_local_media_files_for_image_results')
    @patch('main.get_image_history_by_id')
    @patch('main.delete_image_history', return_value=True)
    def test_snapshot_is_taken_before_the_firestore_delete(
        self, mock_delete, mock_get, mock_cleanup
    ):
        # get_image_history_by_id() must run first -- once delete_image_history() has
        # removed the doc, there is nothing left to read the results/filenames back from.
        call_order = []
        mock_get.side_effect = lambda *a, **kw: call_order.append('get') or self._fake_entry()
        mock_delete.side_effect = lambda *a, **kw: call_order.append('delete') or True
        with main.app.test_client() as client:
            self._login(client)
            client.delete('/api/image-history/imghist1')
        self.assertEqual(call_order, ['get', 'delete'])

    @patch('main._delete_local_media_files_for_image_results')
    @patch('main.get_image_history_by_id', return_value=None)
    @patch('main.delete_image_history', return_value=False)
    def test_404_does_not_trigger_cleanup(self, mock_delete, mock_get, mock_cleanup):
        with main.app.test_client() as client:
            self._login(client)
            client.delete('/api/image-history/ghost')
        mock_cleanup.assert_not_called()


class TestToggleImageHistoryPinEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    @patch('main.toggle_pin_image_history', return_value=True)
    def test_success_returns_200(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/image-history/imghist1/toggle-pin')
        self.assertEqual(response.status_code, 200)

    @patch('main.toggle_pin_image_history', return_value=True)
    def test_response_contains_new_is_pinned_true(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/image-history/imghist1/toggle-pin')
        data = json.loads(response.data)
        self.assertTrue(data['is_pinned'])

    @patch('main.toggle_pin_image_history', return_value=False)
    def test_response_reflects_new_is_pinned_false_not_confused_with_failure(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/image-history/imghist1/toggle-pin')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertFalse(data['is_pinned'])

    @patch('main.toggle_pin_image_history', return_value=None)
    def test_not_owned_or_missing_returns_404(self, mock_toggle):
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/image-history/ghost/toggle-pin')
        self.assertEqual(response.status_code, 404)


class TestGenerateImagesHistoryPersistence(unittest.TestCase):
    """The image-generation counterpart of TestCompareHistoryPersistence in
    test_main_blackbox.py."""

    def setUp(self):
        main.app.config['TESTING'] = True

    @patch('main.save_image_history')
    @patch('main.G4FImageClient')
    def test_logged_in_user_triggers_save_with_correct_args(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        mock_save.return_value = {'id': 'imghist123'}
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage']}
            response = client.post(
                '/api/generate-images', data=json.dumps(payload), content_type='application/json'
            )
        self.assertEqual(response.status_code, 200)
        mock_save.assert_called_once()
        self.assertEqual(mock_save.call_args[0][0], 'uid1')
        self.assertEqual(mock_save.call_args[0][1], 'a red apple')

    @patch('main.save_image_history')
    @patch('main.G4FImageClient')
    def test_response_includes_history_id_from_save(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        mock_save.return_value = {'id': 'imghist123'}
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage']}
            response = client.post(
                '/api/generate-images', data=json.dumps(payload), content_type='application/json'
            )
        data = json.loads(response.data)
        self.assertEqual(data['history_id'], 'imghist123')

    @patch('main.save_image_history')
    @patch('main.G4FImageClient')
    def test_guest_does_not_trigger_save(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
            [_fake_image(url='https://example.com/a.png')]
        )
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage']}
            response = client.post(
                '/api/generate-images', data=json.dumps(payload), content_type='application/json'
            )
        mock_save.assert_not_called()
        data = json.loads(response.data)
        self.assertIsNone(data['history_id'])


class TestIndexPageImageSidebarMarkup(unittest.TestCase):
    """GET / must ship the mode-switch entry points (Generate Image / New Chat buttons) that
    drive the sidebar between chat and image Recents -- see templates/index.html's
    sidebarMode."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_generate_image_button_present_for_logged_in_user(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        self.assertIn('generateImageBtn', response.data.decode())

    def test_generate_image_button_present_for_guest(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        self.assertIn('generateImageBtn', response.data.decode())

    def test_image_mode_container_present(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        self.assertIn('imageModeContainer', response.data.decode())


if __name__ == '__main__':
    unittest.main()
