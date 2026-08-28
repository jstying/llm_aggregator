"""Tests for POST /api/frontier-chat (added 2026-08-28), the stage-2 server-side concurrent
scheduler: one request fans out every checked frontier provider (Claude/ChatGPT/Gemini text)
through a ThreadPoolExecutor, with per-provider semantics kept identical to the standalone
/api/claude-chat, /api/chatgpt-chat, and /api/gemini-chat routes (auth guard, personal-key
bypass, quota gate, increment + refund ledger on success, friendly SERVER_*_EXHAUSTED
messages), and history appends serialized in the request thread after the batch gathers.
"""

import threading
import time
import unittest
from unittest.mock import patch

import main


def _ok_result(provider, model, rtype, response_time=0.5):
    return {
        'provider': provider, 'success': True, 'response': 'hi', 'error': '',
        'response_time': response_time, 'model': model, 'type': rtype,
    }


ALL_THREE = [
    {'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'rid-claude'},
    {'provider': 'chatgpt', 'model': 'gpt-5.5', 'request_id': 'rid-chatgpt'},
    {'provider': 'gemini_text', 'model': 'gemini-3.5-flash', 'request_id': 'rid-gemini'},
]


class FrontierChatBatchBase(unittest.TestCase):
    def setUp(self):
        main.app.config['TESTING'] = True
        main._PENDING_FRONTIER_REFUNDS.clear()
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def tearDown(self):
        main._PENDING_FRONTIER_REFUNDS.clear()
        main._CANCELLED_HISTORY_REQUESTS.clear()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id


class TestFrontierChatAuthAndValidation(FrontierChatBatchBase):
    def test_guest_and_anonymous_get_401(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            resp = client.post('/api/frontier-chat', json={'prompt': 'hi', 'providers': ALL_THREE})
        self.assertEqual(resp.status_code, 401)

        with main.app.test_client() as client:
            resp = client.post('/api/frontier-chat', json={'prompt': 'hi', 'providers': ALL_THREE})
        self.assertEqual(resp.status_code, 401)

    def test_missing_prompt_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/frontier-chat', json={'providers': ALL_THREE})
        self.assertEqual(resp.status_code, 400)

    def test_empty_providers_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/frontier-chat', json={'prompt': 'hi', 'providers': []})
        self.assertEqual(resp.status_code, 400)

    def test_unknown_provider_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/frontier-chat', json={
                'prompt': 'hi',
                'providers': [{'provider': 'not-real', 'model': 'x'}],
            })
        self.assertEqual(resp.status_code, 400)

    def test_invalid_model_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/frontier-chat', json={
                'prompt': 'hi',
                'providers': [{'provider': 'claude', 'model': 'not-real'}],
            })
        self.assertEqual(resp.status_code, 400)


class TestFrontierChatConcurrencyAndResults(FrontierChatBatchBase):
    def test_all_three_providers_run_concurrently_not_sequentially(self):
        """Three 0.3s calls must finish in well under 0.9s -- the whole reason this route
        exists is that the old browser-side flow awaited them one after another."""
        delay = 0.3

        def slow(provider, rtype):
            def _call(prompt, model_key, key):
                time.sleep(delay)
                return _ok_result(provider, model_key, rtype, response_time=delay)
            return _call

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'increment_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', side_effect=slow('Claude', 'anthropic')), \
                 patch.object(main, 'call_chatgpt_model', side_effect=slow('ChatGPT', 'openai')), \
                 patch.object(main, 'call_gemini_text_model', side_effect=slow('Gemini', 'google_genai_text')):
                started = time.time()
                resp = client.post('/api/frontier-chat', json={'prompt': 'hi', 'providers': ALL_THREE})
                elapsed = time.time() - started

        self.assertEqual(resp.status_code, 200)
        results = resp.get_json()['results']
        self.assertEqual(set(results), {'claude', 'chatgpt', 'gemini_text'})
        for outcome in results.values():
            self.assertTrue(outcome['success'])
        self.assertLess(
            elapsed, delay * 3,
            f'batch of three {delay}s calls took {elapsed:.2f}s -- they ran sequentially'
        )

    def test_success_increments_counter_and_records_refund_ledger(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_claude_model',
                              return_value=_ok_result('Claude', 'claude-sonnet-5', 'anthropic')):
                resp = client.post('/api/frontier-chat', json={
                    'prompt': 'hi',
                    'providers': [{'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'rid-1'}],
                })

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_called_once_with('uid1')
        self.assertTrue(main._consume_pending_frontier_refund('rid-1', 'uid1', 'claude'))

    def test_quota_exhausted_provider_blocked_while_others_still_run(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=main.CLAUDE_FREE_TIER_LIMIT), \
                 patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_free_tier_usage'), \
                 patch.object(main, 'call_claude_model') as mock_claude, \
                 patch.object(main, 'call_chatgpt_model',
                              return_value=_ok_result('ChatGPT', 'gpt-5.5', 'openai')):
                resp = client.post('/api/frontier-chat', json={
                    'prompt': 'hi',
                    'providers': [
                        {'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'r1'},
                        {'provider': 'chatgpt', 'model': 'gpt-5.5', 'request_id': 'r2'},
                    ],
                })

        self.assertEqual(resp.status_code, 200)
        results = resp.get_json()['results']
        self.assertEqual(results['claude'], {'error': 'FREE_TIER_EXHAUSTED'})
        self.assertTrue(results['chatgpt']['success'])
        mock_claude.assert_not_called()

    def test_own_key_header_bypasses_quota_check_and_increment(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage') as mock_get, \
                 patch.object(main, 'increment_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_chatgpt_model',
                              return_value=_ok_result('ChatGPT', 'gpt-5.5', 'openai')) as mock_call:
                resp = client.post(
                    '/api/frontier-chat',
                    json={'prompt': 'hi', 'providers': [{'provider': 'chatgpt', 'model': 'gpt-5.5', 'request_id': 'r1'}]},
                    headers={'X-User-ChatGPT-Key': 'sk-user-own'},
                )

        self.assertEqual(resp.status_code, 200)
        mock_get.assert_not_called()
        mock_incr.assert_not_called()
        self.assertEqual(mock_call.call_args[0][2], 'sk-user-own')
        self.assertFalse(main._consume_pending_frontier_refund('r1', 'uid1', 'chatgpt'))

    def test_server_credits_exhausted_maps_to_friendly_message(self):
        exhausted = dict(_ok_result('Claude', 'claude-sonnet-5', 'anthropic'),
                         success=False, error='SERVER_CREDITS_EXHAUSTED',
                         error_code='SERVER_CREDITS_EXHAUSTED')
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_claude_model', return_value=exhausted), \
                 patch.object(main, '_append_claude_result_to_history') as mock_append:
                resp = client.post('/api/frontier-chat', json={
                    'prompt': 'hi', 'history_id': 'h1',
                    'providers': [{'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'r1'}],
                })

        self.assertEqual(resp.status_code, 200)
        outcome = resp.get_json()['results']['claude']
        self.assertEqual(outcome['error'], 'SERVER_CREDITS_EXHAUSTED')
        self.assertIn("developer's Claude", outcome['message'])
        mock_incr.assert_not_called()
        # The history append carries the friendly message, never the raw marker or error_code.
        appended = mock_append.call_args[0][2]
        self.assertNotIn('error_code', appended)
        self.assertIn("developer's Claude", appended['error'])

    def test_unavailable_sdk_degrades_only_its_own_entry(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'CLAUDE_AVAILABLE', False), \
                 patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_free_tier_usage'), \
                 patch.object(main, 'call_chatgpt_model',
                              return_value=_ok_result('ChatGPT', 'gpt-5.5', 'openai')):
                resp = client.post('/api/frontier-chat', json={
                    'prompt': 'hi',
                    'providers': [
                        {'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'r1'},
                        {'provider': 'chatgpt', 'model': 'gpt-5.5', 'request_id': 'r2'},
                    ],
                })

        self.assertEqual(resp.status_code, 200)
        results = resp.get_json()['results']
        self.assertEqual(results['claude']['error'], 'CLAUDE_UNAVAILABLE')
        self.assertTrue(results['chatgpt']['success'])

    def test_duplicate_provider_entries_run_once(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model',
                              return_value=_ok_result('Claude', 'claude-sonnet-5', 'anthropic')) as mock_call:
                resp = client.post('/api/frontier-chat', json={
                    'prompt': 'hi',
                    'providers': [
                        {'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'r1'},
                        {'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'r2'},
                    ],
                })

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_call.call_count, 1)


class TestFrontierChatHistoryAppends(FrontierChatBatchBase):
    def test_appends_run_serialized_in_the_request_thread_not_in_workers(self):
        """The workers must never touch the history document -- append_chat_history_result()
        is an unguarded read-modify-write, so concurrent appends could lose one. The route
        serializes them after the batch gathers; assert no two appends overlap in time and
        that they all run on the same (request) thread."""
        append_calls = []
        append_lock = threading.Lock()

        def tracking_append(user_id, history_id, result, provider_label=None):
            with append_lock:
                append_calls.append((threading.get_ident(), time.time(), result['provider']))
            return True

        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'increment_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=_ok_result('Claude', 'claude-sonnet-5', 'anthropic')), \
                 patch.object(main, 'call_chatgpt_model', return_value=_ok_result('ChatGPT', 'gpt-5.5', 'openai')), \
                 patch.object(main, 'call_gemini_text_model', return_value=_ok_result('Gemini', 'gemini-3.5-flash', 'google_genai_text')), \
                 patch.object(main, '_append_claude_result_to_history', side_effect=tracking_append), \
                 patch.object(main, '_append_frontier_chat_result', side_effect=tracking_append):
                resp = client.post('/api/frontier-chat', json={
                    'prompt': 'hi', 'history_id': 'h1', 'providers': ALL_THREE,
                })

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(append_calls), 3)
        self.assertEqual(len({thread_id for thread_id, _, _ in append_calls}), 1,
                         'all history appends must run on one thread')
        # Request order is preserved: claude, chatgpt, gemini_text.
        self.assertEqual([p for _, _, p in append_calls], ['Claude', 'ChatGPT', 'Gemini'])

    def test_cancelled_request_id_skips_the_append(self):
        main._mark_request_cancelled('rid-cancelled')
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=_ok_result('Claude', 'claude-sonnet-5', 'anthropic')), \
                 patch.object(main, '_append_claude_result_to_history') as mock_append:
                resp = client.post('/api/frontier-chat', json={
                    'prompt': 'hi', 'history_id': 'h1',
                    'providers': [{'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'rid-cancelled'}],
                })

        self.assertEqual(resp.status_code, 200)
        mock_append.assert_not_called()

    def test_refund_endpoint_reconciles_a_batch_consumed_use(self):
        """End-to-end with the unchanged standalone refund endpoint: a use consumed through
        the batch route is refundable through /api/claude-chat/refund with the same
        request_id, exactly once."""
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=_ok_result('Claude', 'claude-sonnet-5', 'anthropic')):
                client.post('/api/frontier-chat', json={
                    'prompt': 'hi',
                    'providers': [{'provider': 'claude', 'model': 'claude-sonnet-5', 'request_id': 'rid-batch'}],
                })

            with patch.object(main, 'decrement_claude_free_tier_usage', return_value=True) as mock_decr:
                first = client.post('/api/claude-chat/refund', json={'request_id': 'rid-batch'})
                second = client.post('/api/claude-chat/refund', json={'request_id': 'rid-batch'})

        self.assertTrue(first.get_json()['refunded'])
        self.assertFalse(second.get_json()['refunded'])
        mock_decr.assert_called_once()


if __name__ == '__main__':
    unittest.main()
