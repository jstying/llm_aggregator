"""Tests for the 2026-07-06 "Stop Generating must not save to history" fix.

Root cause: this Flask deployment is synchronous, so aborting a fetch client-side does
not stop the server from finishing compare_providers()/generate_images() and calling
save_chat_history()/save_image_history(), nor from finishing claude_chat()/
gemini_image_chat() and appending into an already-saved history entry. A cancelled
generation could therefore still show up later in the user's Recents list.

Fix: a request_id-keyed in-memory cancellation registry (main._CANCELLED_HISTORY_REQUESTS,
_mark_request_cancelled()/_is_request_cancelled()), mirroring the existing free-tier
refund ledger. compare_providers()/generate_images() check it right before persisting;
claude_chat()/gemini_image_chat() check it right before appending. Two new endpoints,
POST /api/compare/cancel and POST /api/generate-images/cancel, let the frontend mark the
g4f-phase request_id cancelled; the existing /api/claude-chat/refund and
/api/gemini-image/refund endpoints now also mark their request_id cancelled.

White-box: the registry helpers in isolation.
Black-box: the two new cancel routes, and the skip-persistence behavior of
compare_providers()/generate_images()/claude_chat()/gemini_image_chat() through the
Flask test client.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


# ==========================================================================
# White-box / unit tests: the cancellation registry
# ==========================================================================

class TestCancelledHistoryRequestRegistry(unittest.TestCase):

    def setUp(self):
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def tearDown(self):
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def test_mark_then_is_cancelled_is_true(self):
        main._mark_request_cancelled('req-1')
        self.assertTrue(main._is_request_cancelled('req-1'))

    def test_unmarked_request_id_is_not_cancelled(self):
        self.assertFalse(main._is_request_cancelled('never-marked'))

    def test_mark_with_empty_request_id_is_noop(self):
        main._mark_request_cancelled(None)
        main._mark_request_cancelled('')
        self.assertEqual(len(main._CANCELLED_HISTORY_REQUESTS), 0)

    def test_is_cancelled_false_for_empty_request_id(self):
        # A route that never received a request_id (e.g. an older client) must not be
        # treated as cancelled.
        self.assertFalse(main._is_request_cancelled(None))
        self.assertFalse(main._is_request_cancelled(''))

    def test_mark_prunes_stale_entries(self):
        main._CANCELLED_HISTORY_REQUESTS['old-req'] = 0.0  # far in the past -> stale
        main._mark_request_cancelled('new-req')
        self.assertNotIn('old-req', main._CANCELLED_HISTORY_REQUESTS)
        self.assertIn('new-req', main._CANCELLED_HISTORY_REQUESTS)


# ==========================================================================
# Black-box tests: /api/compare/cancel, /api/generate-images/cancel
# ==========================================================================

class TestCancelRoutes(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def tearDown(self):
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def test_compare_cancel_requires_no_login_and_marks_registry(self):
        # Guests use /api/compare too, so the cancel route must not require auth.
        with main.app.test_client() as client:
            resp = client.post('/api/compare/cancel', json={'request_id': 'req-a'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(main._is_request_cancelled('req-a'))

    def test_generate_images_cancel_marks_registry(self):
        with main.app.test_client() as client:
            resp = client.post('/api/generate-images/cancel', json={'request_id': 'req-b'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(main._is_request_cancelled('req-b'))

    def test_cancel_with_missing_request_id_does_not_error(self):
        with main.app.test_client() as client:
            resp = client.post('/api/compare/cancel', json={})
        self.assertEqual(resp.status_code, 200)


# ==========================================================================
# Black-box tests: compare_providers()/generate_images() skip persisting to
# the DB when the cancellation registry has a hit
# ==========================================================================

class TestComparePersistenceSkippedWhenCancelled(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def tearDown(self):
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_cancelled_request_id_skips_save_chat_history(self):
        g4f_result = {
            'provider': 'Yqcloud', 'success': True, 'response': 'hi',
            'error': '', 'response_time': 0.4, 'model': 'gpt-3.5-turbo', 'type': 'g4f',
        }
        fake_provider = MagicMock()
        fake_provider.__name__ = 'Yqcloud'
        main._mark_request_cancelled('cancelled-req')

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'test_g4f_provider', return_value=dict(g4f_result)), \
                    patch.object(main, 'G4F_AVAILABLE', True), \
                    patch.object(main, 'G4F_PROVIDERS', [fake_provider]), \
                    patch.object(main, 'save_chat_history') as mock_save:
                resp = client.post('/api/compare', json={
                    'prompt': 'hello', 'providers': ['Yqcloud'], 'request_id': 'cancelled-req'
                })

        self.assertEqual(resp.status_code, 200)
        mock_save.assert_not_called()
        self.assertIsNone(resp.get_json()['history_id'])

    def test_uncancelled_request_id_still_saves(self):
        g4f_result = {
            'provider': 'Yqcloud', 'success': True, 'response': 'hi',
            'error': '', 'response_time': 0.4, 'model': 'gpt-3.5-turbo', 'type': 'g4f',
        }
        fake_provider = MagicMock()
        fake_provider.__name__ = 'Yqcloud'

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'test_g4f_provider', return_value=dict(g4f_result)), \
                    patch.object(main, 'G4F_AVAILABLE', True), \
                    patch.object(main, 'G4F_PROVIDERS', [fake_provider]), \
                    patch.object(main, 'save_chat_history', return_value={'id': 'hist1'}) as mock_save:
                resp = client.post('/api/compare', json={
                    'prompt': 'hello', 'providers': ['Yqcloud'], 'request_id': 'not-cancelled-req'
                })

        self.assertEqual(resp.status_code, 200)
        mock_save.assert_called_once()
        self.assertEqual(resp.get_json()['history_id'], 'hist1')

    def test_missing_request_id_still_saves(self):
        # Older/other callers that never send request_id must keep working exactly as
        # before -- absence of a request_id must never be interpreted as "cancelled".
        g4f_result = {
            'provider': 'Yqcloud', 'success': True, 'response': 'hi',
            'error': '', 'response_time': 0.4, 'model': 'gpt-3.5-turbo', 'type': 'g4f',
        }
        fake_provider = MagicMock()
        fake_provider.__name__ = 'Yqcloud'

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'test_g4f_provider', return_value=dict(g4f_result)), \
                    patch.object(main, 'G4F_AVAILABLE', True), \
                    patch.object(main, 'G4F_PROVIDERS', [fake_provider]), \
                    patch.object(main, 'save_chat_history', return_value={'id': 'hist1'}) as mock_save:
                resp = client.post('/api/compare', json={
                    'prompt': 'hello', 'providers': ['Yqcloud']
                })

        self.assertEqual(resp.status_code, 200)
        mock_save.assert_called_once()


class TestGenerateImagesPersistenceSkippedWhenCancelled(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def tearDown(self):
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_cancelled_request_id_skips_save_image_history(self):
        image_result = {
            'provider': 'FakeImageProvider', 'success': True, 'url': '/media/x.png',
            'b64_json': None, 'error': '', 'response_time': 0.4,
            'model': 'flux', 'type': 'g4f_image',
        }
        fake_provider = MagicMock()
        fake_provider.__name__ = 'FakeImageProvider'
        main._mark_request_cancelled('cancelled-img-req')

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'test_g4f_image_provider', return_value=dict(image_result)), \
                    patch.object(main, 'G4F_AVAILABLE', True), \
                    patch.object(main, 'IMAGE_PROVIDERS', [fake_provider]), \
                    patch.object(main, 'get_image_timeouts', return_value=(40, 85)), \
                    patch.object(main, 'save_image_history') as mock_save:
                resp = client.post('/api/generate-images', json={
                    'prompt': 'a cat', 'providers': ['FakeImageProvider'],
                    'request_id': 'cancelled-img-req'
                })

        self.assertEqual(resp.status_code, 200)
        mock_save.assert_not_called()
        self.assertIsNone(resp.get_json()['history_id'])

    def test_uncancelled_request_id_still_saves(self):
        image_result = {
            'provider': 'FakeImageProvider', 'success': True, 'url': '/media/x.png',
            'b64_json': None, 'error': '', 'response_time': 0.4,
            'model': 'flux', 'type': 'g4f_image',
        }
        fake_provider = MagicMock()
        fake_provider.__name__ = 'FakeImageProvider'

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'test_g4f_image_provider', return_value=dict(image_result)), \
                    patch.object(main, 'G4F_AVAILABLE', True), \
                    patch.object(main, 'IMAGE_PROVIDERS', [fake_provider]), \
                    patch.object(main, 'get_image_timeouts', return_value=(40, 85)), \
                    patch.object(main, 'save_image_history', return_value={'id': 'imghist1'}) as mock_save:
                resp = client.post('/api/generate-images', json={
                    'prompt': 'a cat', 'providers': ['FakeImageProvider'],
                    'request_id': 'not-cancelled-img-req'
                })

        self.assertEqual(resp.status_code, 200)
        mock_save.assert_called_once()
        self.assertEqual(resp.get_json()['history_id'], 'imghist1')


# ==========================================================================
# Black-box tests: claude_chat()/gemini_image_chat() skip appending to history
# when the cancellation registry has a hit, and both refund endpoints now
# unconditionally mark the request as cancelled
# ==========================================================================

class TestClaudeChatSkipsAppendWhenCancelled(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main._CANCELLED_HISTORY_REQUESTS.clear()
        main._PENDING_FRONTIER_REFUNDS.clear()

    def tearDown(self):
        main._CANCELLED_HISTORY_REQUESTS.clear()
        main._PENDING_FRONTIER_REFUNDS.clear()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_append_skipped_when_request_id_already_cancelled(self):
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi',
            'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        main._mark_request_cancelled('req-cancel-1')

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=claude_result), \
                 patch.object(main, 'append_chat_history_result') as mock_append:
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5',
                    'history_id': 'hist1', 'request_id': 'req-cancel-1'
                })

        # The result itself is still returned successfully -- only persistence is skipped.
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])
        mock_append.assert_not_called()

    def test_refund_endpoint_marks_cancelled_even_without_a_ledger_hit(self):
        # An own-key call never records a refund ledger entry, but Stop Generating still
        # needs to suppress the history append if claude_chat() is still in flight.
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/claude-chat/refund', json={'request_id': 'req-ownkey-stop'})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['refunded'])
        self.assertTrue(main._is_request_cancelled('req-ownkey-stop'))

    def test_server_credits_exhausted_branch_also_skips_append_when_cancelled(self):
        raw_result = {
            'provider': 'Claude', 'success': False, 'response': '',
            'error': 'SERVER_CREDITS_EXHAUSTED', 'error_code': 'SERVER_CREDITS_EXHAUSTED',
            'response_time': 0.3, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        main._mark_request_cancelled('req-cancel-2')

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'call_claude_model', return_value=raw_result), \
                 patch.object(main, 'append_chat_history_result') as mock_append:
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5',
                    'history_id': 'hist1', 'request_id': 'req-cancel-2'
                })

        self.assertEqual(resp.status_code, 503)
        mock_append.assert_not_called()


class TestGeminiImageChatSkipsAppendWhenCancelled(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main._CANCELLED_HISTORY_REQUESTS.clear()
        main._PENDING_FRONTIER_REFUNDS.clear()

    def tearDown(self):
        main._CANCELLED_HISTORY_REQUESTS.clear()
        main._PENDING_FRONTIER_REFUNDS.clear()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_append_skipped_when_request_id_already_cancelled(self):
        gemini_result = {
            'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
            'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro', 'type': 'google_genai',
        }
        main._mark_request_cancelled('req-cancel-3')

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage'), \
                 patch.object(main, 'call_gemini_image_model', return_value=gemini_result), \
                 patch.object(main, 'append_image_history_result') as mock_append:
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro',
                    'history_id': 'imghist1', 'request_id': 'req-cancel-3'
                })

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])
        mock_append.assert_not_called()

    def test_refund_endpoint_marks_cancelled_even_without_a_ledger_hit(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/gemini-image/refund', json={'request_id': 'req-ownkey-stop-g'})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['refunded'])
        self.assertTrue(main._is_request_cancelled('req-ownkey-stop-g'))


if __name__ == '__main__':
    unittest.main()
