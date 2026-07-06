"""Tests for two 2026-07-05 additions:

1. "Contact the developer" friendly-error copy: when the free-tier check has already
   let a call through (i.e. the user's trial quota isn't exhausted) but the
   developer's own Claude/Gemini account is out of credits/quota, the message must
   point the user at the developer rather than at "configure your personal API key"
   (which is the appropriate advice for a *different* case -- see the own-key branch).
2. The "Stop Generating" refund ledger: `main._record_pending_frontier_refund()` /
   `_consume_pending_frontier_refund()`, plus the `POST /api/claude-chat/refund` and
   `POST /api/gemini-image/refund` endpoints and the `auth/db.py`
   `decrement_claude_free_tier_usage()`/`decrement_gemini_free_tier_usage()` helpers
   they call.

White-box: the ledger helpers and the db decrement functions in isolation.
Black-box: the refund routes and the dev-account-exhausted message through the Flask
test client.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


# ==========================================================================
# White-box / unit tests: the refund ledger
# ==========================================================================

class TestPendingFrontierRefundLedger(unittest.TestCase):

    def setUp(self):
        main._PENDING_FRONTIER_REFUNDS.clear()

    def tearDown(self):
        main._PENDING_FRONTIER_REFUNDS.clear()

    def test_record_then_consume_matching_entry_succeeds(self):
        main._record_pending_frontier_refund('req-1', 'uid1', 'claude')
        self.assertTrue(main._consume_pending_frontier_refund('req-1', 'uid1', 'claude'))

    def test_consume_is_single_use(self):
        main._record_pending_frontier_refund('req-1', 'uid1', 'claude')
        self.assertTrue(main._consume_pending_frontier_refund('req-1', 'uid1', 'claude'))
        # Second call for the same request_id must be a no-op -- otherwise a user could
        # call the refund endpoint repeatedly with a stale request_id to drain quota.
        self.assertFalse(main._consume_pending_frontier_refund('req-1', 'uid1', 'claude'))

    def test_consume_rejects_wrong_user(self):
        main._record_pending_frontier_refund('req-1', 'uid1', 'claude')
        self.assertFalse(main._consume_pending_frontier_refund('req-1', 'uid2', 'claude'))
        # Entry must still be there for the rightful owner (a mismatched guess doesn't
        # burn the real entry).
        self.assertTrue(main._consume_pending_frontier_refund('req-1', 'uid1', 'claude'))

    def test_consume_rejects_wrong_provider(self):
        main._record_pending_frontier_refund('req-1', 'uid1', 'claude')
        self.assertFalse(main._consume_pending_frontier_refund('req-1', 'uid1', 'gemini'))

    def test_consume_unknown_request_id_is_noop(self):
        self.assertFalse(main._consume_pending_frontier_refund('never-recorded', 'uid1', 'claude'))

    def test_record_with_empty_request_id_is_noop(self):
        main._record_pending_frontier_refund(None, 'uid1', 'claude')
        main._record_pending_frontier_refund('', 'uid1', 'claude')
        self.assertEqual(len(main._PENDING_FRONTIER_REFUNDS), 0)

    def test_record_prunes_stale_entries(self):
        main._PENDING_FRONTIER_REFUNDS['old-req'] = {
            'user_id': 'uid1', 'provider': 'claude',
            'recorded_at': 0.0,  # far in the past -> always stale
        }
        main._record_pending_frontier_refund('new-req', 'uid1', 'claude')
        self.assertNotIn('old-req', main._PENDING_FRONTIER_REFUNDS)
        self.assertIn('new-req', main._PENDING_FRONTIER_REFUNDS)


# ==========================================================================
# White-box / unit tests: the quota decrement functions in auth/db.py
# ==========================================================================

class TestFreeTierCounterDecrement(unittest.TestCase):

    def test_claude_decrement_calls_firestore_increment_negative_one(self):
        from auth import db as auth_db
        from firebase_admin import firestore
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'claude_free_tier_usage': 3}
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.decrement_claude_free_tier_usage('uid1')

        self.assertTrue(result)
        mock_doc_ref.update.assert_called_once()
        call_kwargs = mock_doc_ref.update.call_args[0][0]
        self.assertIn('claude_free_tier_usage', call_kwargs)
        self.assertIsInstance(call_kwargs['claude_free_tier_usage'], type(firestore.Increment(-1)))

    def test_claude_decrement_refuses_to_go_negative(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'claude_free_tier_usage': 0}
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.decrement_claude_free_tier_usage('uid1')

        self.assertFalse(result)
        mock_doc_ref.update.assert_not_called()

    def test_claude_decrement_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db
        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.decrement_claude_free_tier_usage('uid1')
        self.assertIsNone(result)

    def test_gemini_decrement_calls_firestore_increment_negative_one(self):
        from auth import db as auth_db
        from firebase_admin import firestore
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'gemini_free_tier_usage': 2}
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.decrement_gemini_free_tier_usage('uid1')

        self.assertTrue(result)
        call_kwargs = mock_doc_ref.update.call_args[0][0]
        self.assertIn('gemini_free_tier_usage', call_kwargs)
        self.assertIsInstance(call_kwargs['gemini_free_tier_usage'], type(firestore.Increment(-1)))

    def test_gemini_decrement_refuses_to_go_negative(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'gemini_free_tier_usage': 0}
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.decrement_gemini_free_tier_usage('uid1')

        self.assertFalse(result)
        mock_doc_ref.update.assert_not_called()


# ==========================================================================
# Black-box / integration tests: the dev-account-exhausted messaging and the
# two refund routes
# ==========================================================================

class TestClaudeChatRefundRoute(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main._PENDING_FRONTIER_REFUNDS.clear()

    def tearDown(self):
        main._PENDING_FRONTIER_REFUNDS.clear()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_refund_requires_authentication(self):
        with main.app.test_client() as client:
            resp = client.post('/api/claude-chat/refund', json={'request_id': 'req-1'})
        self.assertEqual(resp.status_code, 401)

    def test_refund_noop_for_unknown_request_id(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/claude-chat/refund', json={'request_id': 'never-happened'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['refunded'])

    def test_successful_claude_call_then_refund_decrements_usage(self):
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi', 'error': '',
            'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=claude_result):
                chat_resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'request_id': 'req-42'
                })
            self.assertEqual(chat_resp.status_code, 200)

            with patch.object(main, 'decrement_claude_free_tier_usage', return_value=True) as mock_decr:
                refund_resp = client.post('/api/claude-chat/refund', json={'request_id': 'req-42'})

        self.assertTrue(refund_resp.get_json()['refunded'])
        mock_decr.assert_called_once_with('uid1')

    def test_refund_is_single_use_even_after_a_real_increment(self):
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi', 'error': '',
            'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=claude_result):
                client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'request_id': 'req-99'
                })

            with patch.object(main, 'decrement_claude_free_tier_usage', return_value=True) as mock_decr:
                first = client.post('/api/claude-chat/refund', json={'request_id': 'req-99'})
                second = client.post('/api/claude-chat/refund', json={'request_id': 'req-99'})

        self.assertTrue(first.get_json()['refunded'])
        self.assertFalse(second.get_json()['refunded'])
        mock_decr.assert_called_once()

    def test_own_key_call_never_becomes_refundable(self):
        # using_own_key calls skip the increment entirely (see claude_chat()), so no
        # ledger entry should ever be recorded for them -- refunding must be a no-op.
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi', 'error': '',
            'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'call_claude_model', return_value=claude_result), \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr:
                client.post('/api/claude-chat',
                            json={'prompt': 'hello', 'model': 'claude-sonnet-5', 'request_id': 'req-ownkey'},
                            headers={'X-User-Claude-Key': 'sk-user-key'})
            mock_incr.assert_not_called()

            refund_resp = client.post('/api/claude-chat/refund', json={'request_id': 'req-ownkey'})

        self.assertFalse(refund_resp.get_json()['refunded'])

    def test_server_credits_exhausted_via_developer_key_tells_user_to_contact_developer(self):
        # get_claude_free_tier_usage returns 0 (well under CLAUDE_FREE_TIER_LIMIT), so the
        # user's own trial quota is NOT exhausted -- the failure is purely supply-side
        # (the developer's Anthropic account is out of credits), so the message must
        # point at the developer, not at "configure your personal API key" (which would
        # be nonsensical advice here: the user never provided a key to configure-around).
        fake_result = {
            'provider': 'Claude', 'success': False, 'response': '', 'error': 'billing',
            'response_time': 0.1, 'model': 'claude-sonnet-5', 'type': 'anthropic',
            'error_code': 'SERVER_CREDITS_EXHAUSTED',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'call_claude_model', return_value=fake_result):
                resp = client.post('/api/claude-chat', json={'prompt': 'hello', 'model': 'claude-sonnet-5'})

        data = resp.get_json()
        self.assertIn('contact the developer', data['message'])
        self.assertNotIn('configure your personal API key', data['message'])

    def test_server_credits_exhausted_via_own_key_tells_user_their_key_is_out(self):
        # Here the caller supplied their own key, so an exhausted-credits error means
        # *their* key ran dry, not the developer's account -- "contact the developer"
        # would be wrong advice; the message must be scoped to the user's own key.
        fake_result = {
            'provider': 'Claude', 'success': False, 'response': '', 'error': 'billing',
            'response_time': 0.1, 'model': 'claude-sonnet-5', 'type': 'anthropic',
            'error_code': 'SERVER_CREDITS_EXHAUSTED',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'call_claude_model', return_value=fake_result):
                resp = client.post('/api/claude-chat',
                                    json={'prompt': 'hello', 'model': 'claude-sonnet-5'},
                                    headers={'X-User-Claude-Key': 'sk-user-key'})

        data = resp.get_json()
        self.assertIn('personal Claude API key', data['message'])
        self.assertNotIn('contact the developer', data['message'])


class TestGeminiImageRefundRoute(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main._PENDING_FRONTIER_REFUNDS.clear()

    def tearDown(self):
        main._PENDING_FRONTIER_REFUNDS.clear()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_refund_requires_authentication(self):
        with main.app.test_client() as client:
            resp = client.post('/api/gemini-image/refund', json={'request_id': 'req-1'})
        self.assertEqual(resp.status_code, 401)

    def test_successful_gemini_call_then_refund_decrements_usage(self):
        gemini_result = {
            'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
            'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro', 'type': 'google_genai',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage'), \
                 patch.object(main, 'call_gemini_image_model', return_value=gemini_result):
                client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro', 'request_id': 'req-7'
                })

            with patch.object(main, 'decrement_gemini_free_tier_usage', return_value=True) as mock_decr:
                refund_resp = client.post('/api/gemini-image/refund', json={'request_id': 'req-7'})

        self.assertTrue(refund_resp.get_json()['refunded'])
        mock_decr.assert_called_once_with('uid1')

    def test_claude_ledger_entry_cannot_be_refunded_via_gemini_route(self):
        # Cross-provider confusion must not let a Claude increment be refunded through
        # the Gemini endpoint (or vice versa) -- providers have independent counters.
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi', 'error': '',
            'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=claude_result):
                client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'request_id': 'req-cross'
                })

            resp = client.post('/api/gemini-image/refund', json={'request_id': 'req-cross'})

        self.assertFalse(resp.get_json()['refunded'])

    def test_server_quota_exhausted_via_developer_key_tells_user_to_contact_developer(self):
        fake_result = {
            'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
            'error': 'quota', 'response_time': 0.1, 'model': 'nano-banana-pro',
            'type': 'google_genai', 'error_code': 'SERVER_QUOTA_EXHAUSTED',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'call_gemini_image_model', return_value=fake_result):
                resp = client.post('/api/gemini-image', json={'prompt': 'a cat', 'model': 'nano-banana-pro'})

        data = resp.get_json()
        self.assertIn('contact the developer', data['message'])
        self.assertNotIn('configure your personal API key', data['message'])

    def test_server_quota_exhausted_via_own_key_tells_user_their_key_is_out(self):
        fake_result = {
            'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
            'error': 'quota', 'response_time': 0.1, 'model': 'nano-banana-pro',
            'type': 'google_genai', 'error_code': 'SERVER_QUOTA_EXHAUSTED',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'call_gemini_image_model', return_value=fake_result):
                resp = client.post('/api/gemini-image',
                                    json={'prompt': 'a cat', 'model': 'nano-banana-pro'},
                                    headers={'X-User-Gemini-Key': 'user-key'})

        data = resp.get_json()
        self.assertIn('personal Gemini API key', data['message'])
        self.assertNotIn('contact the developer', data['message'])


if __name__ == '__main__':
    unittest.main()
