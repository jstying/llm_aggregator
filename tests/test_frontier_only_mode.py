"""Tests for the Frontier-only mode toggle (2026-07-08): a request-body flag that lets
the client explicitly say "test zero free g4f providers this time" instead of relying on
an empty `providers` list, which has always meant "test all free providers" (see
compare_providers()/generate_images() in main.py). Covers both /api/compare and
/api/generate-images, plus the markup for the two new toggle buttons in index.html.
"""
import json
import unittest
from unittest.mock import patch, MagicMock

import main


class TestCompareFrontierOnlyMode(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_frontier_only_returns_zero_results_without_running_any_g4f_provider(self):
        with patch('main.test_g4f_provider') as mock_test:
            payload = {'prompt': 'hi', 'providers': [], 'frontier_only': True}
            response = self.client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['results'], [])
        self.assertEqual(data['total_providers'], 0)
        self.assertEqual(data['successful_providers'], 0)
        mock_test.assert_not_called()

    def test_frontier_only_ignores_a_nonempty_providers_list(self):
        """frontier_only always wins -- even if the client also sent explicit provider
        names (shouldn't happen from the UI since free checkboxes are disabled while the
        toggle is on, but the backend must not trust the client to enforce that)."""
        with patch('main.test_g4f_provider') as mock_test:
            payload = {'prompt': 'hi', 'providers': ['Yqcloud'], 'frontier_only': True}
            response = self.client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )
        data = json.loads(response.data)
        self.assertEqual(data['total_providers'], 0)
        mock_test.assert_not_called()

    def test_omitting_frontier_only_preserves_legacy_test_all_behavior(self):
        """Regression guard: an empty providers list without frontier_only must still mean
        "test all free providers", not zero -- this is the pre-existing contract the new
        flag must not disturb."""
        with patch('main.g4f.ChatCompletion.create', return_value='Mock response'):
            payload = {'prompt': 'hi', 'providers': []}
            response = self.client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )
        data = json.loads(response.data)
        self.assertEqual(data['total_providers'], len(main.G4F_PROVIDERS))

    @patch('main.save_chat_history')
    def test_frontier_only_still_creates_a_history_record_for_logged_in_user(self, mock_save):
        """Claude/ChatGPT/Gemini can only append into an already-existing history record
        (see CLAUDE.md section 6/9) -- frontier-only mode must still create that record
        with an empty results array so the frontend has a history_id to append into."""
        mock_save.return_value = {'id': 'hist123'}
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            payload = {'prompt': 'hi', 'providers': [], 'frontier_only': True}
            response = client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )
        self.assertEqual(response.status_code, 200)
        mock_save.assert_called_once_with('uid1', 'hi', [])
        data = json.loads(response.data)
        self.assertEqual(data['history_id'], 'hist123')


def _fake_image(url=None, b64_json=None):
    image = MagicMock()
    image.url = url
    image.b64_json = b64_json
    return image


def _fake_image_response(images):
    response = MagicMock()
    response.data = images
    return response


class TestGenerateImagesFrontierOnlyMode(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_frontier_only_returns_zero_results_without_running_any_g4f_provider(self):
        with patch('main.test_g4f_image_provider') as mock_test:
            payload = {'prompt': 'a cat', 'providers': [], 'frontier_only': True}
            response = self.client.post(
                '/api/generate-images', data=json.dumps(payload), content_type='application/json'
            )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['results'], [])
        self.assertEqual(data['total_providers'], 0)
        mock_test.assert_not_called()

    def test_omitting_frontier_only_preserves_legacy_test_all_behavior(self):
        with patch('main.G4FImageClient') as mock_client_cls:
            mock_client_cls.return_value.images.generate.return_value = _fake_image_response(
                [_fake_image(url='https://example.com/a.png')]
            )
            payload = {'prompt': 'a cat', 'providers': []}
            response = self.client.post(
                '/api/generate-images', data=json.dumps(payload), content_type='application/json'
            )
        data = json.loads(response.data)
        self.assertEqual(data['total_providers'], len(main.IMAGE_PROVIDERS))

    @patch('main.save_image_history')
    def test_frontier_only_still_creates_a_history_record_for_logged_in_user(self, mock_save):
        mock_save.return_value = {'id': 'histimg1'}
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            payload = {'prompt': 'a cat', 'providers': [], 'frontier_only': True}
            response = client.post(
                '/api/generate-images', data=json.dumps(payload), content_type='application/json'
            )
        self.assertEqual(response.status_code, 200)
        mock_save.assert_called_once_with('uid1', 'a cat', [])
        data = json.loads(response.data)
        self.assertEqual(data['history_id'], 'histimg1')


class TestFrontierOnlyToggleMarkup(unittest.TestCase):
    """The toggle buttons themselves are pure frontend state (no dedicated JS test
    framework in this project, per CLAUDE.md section 9) -- these checks only pin down the
    server-rendered markup contract: the buttons exist, are guest/anon-locked like every
    other frontier-only element, and don't reuse another provider's classes."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_logged_in_user_sees_both_toggles_enabled(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            html = client.get('/').data.decode()
        self.assertIn('id="frontierOnlyToggle"', html)
        self.assertIn('id="frontierOnlyToggleImage"', html)
        toggle_start = html.index('id="frontierOnlyToggle"')
        self.assertNotIn('disabled', html[toggle_start:toggle_start + 200])

    def test_guest_sees_both_toggles_disabled(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            html = client.get('/').data.decode()
        toggle_start = html.index('id="frontierOnlyToggle"')
        self.assertIn('disabled', html[toggle_start:toggle_start + 200])
        image_toggle_start = html.index('id="frontierOnlyToggleImage"')
        self.assertIn('disabled', html[image_toggle_start:image_toggle_start + 200])


if __name__ == '__main__':
    unittest.main()
