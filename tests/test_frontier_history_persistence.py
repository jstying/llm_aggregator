"""Tests for frontier model (Claude/Gemini) history persistence (added 2026-07-05).

Bug fixed by this feature: Claude/Gemini results were visible live (and downloadable,
for Gemini's generated image) right after a "Compare"/"Generate" submit, but reopening
the saved record later at /history/<id> or /image-history/<id> would show the g4f
results only -- the frontier model's card had silently vanished.

Root cause: /api/compare and /api/generate-images each call save_chat_history()/
save_image_history() (and return history_id) *before* the frontend even starts its
extra POST /api/claude-chat / POST /api/gemini-image request (see fetchClaudeResult()/
fetchGeminiImageResult() in templates/index.html). The old code only pushed the
frontier result into the in-memory `data.results` array for that page load's rendering
-- it never wrote back to Firestore, so the persisted document never contained it.

Fix: claude_chat()/gemini_image_chat() now accept an optional `history_id` field in
the request body (the frontend forwards the history_id it already got back from
/api/compare or /api/generate-images). Whenever the call actually happens (i.e. it
wasn't blocked by FREE_TIER_EXHAUSTED), the resulting Result dict -- computed by the
server itself, never client-supplied -- is appended into that already-saved history
entry via the two new auth/db.py functions `append_chat_history_result()`/
`append_image_history_result()`.

This is *not* a reversal of the previous "Claude/Gemini never call
save_chat_history()/save_image_history()" rule: these routes still cannot create a new
history entry on their own -- they can only append into one that already exists,
created by the g4f pipelines (/api/compare, /api/generate-images).

Split mirrors the project's established white-box/black-box split, plus one gray-box
end-to-end class:
- White-box/unit: the two new auth/db.py CRUD functions (ownership check, sort-on-
  append, Firebase-unavailable fallback), and main.py's two thin wrapper helpers
  (`_append_claude_result_to_history`/`_append_gemini_result_to_image_history`) that
  no-op when history_id is falsy and swallow/log failures.
- Black-box/integration: HTTP-driven scenarios through the Flask test client for both
  /api/claude-chat and /api/gemini-image -- history_id present vs. absent, success vs.
  failure vs. server-exhausted (credits/quota), and resilience when the append itself
  raises.
- Gray-box/end-to-end: a minimal in-memory Firestore double (patched onto `auth.db.db`)
  drives the *real* save_chat_history()/append_chat_history_result() implementations
  (not mocked) across two consecutive requests -- POST /api/compare then
  POST /api/claude-chat with the history_id it returned -- and then re-reads the
  document the way get_chat_history_by_id() would, proving the Claude card is actually
  present in the reloaded snapshot rather than just checking the right functions were
  called with the right arguments.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from auth import db as auth_db  # noqa: E402


# ==========================================================================
# White-box / unit tests: the two new functions in auth/db.py
# ==========================================================================

class TestAppendChatHistoryResultDb(unittest.TestCase):

    def _build_mock_db(self, exists=True, owner_id='uid1', existing_results=None):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = exists
        mock_doc.to_dict.return_value = {
            'user_id': owner_id,
            'results': existing_results if existing_results is not None else [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock_db

    def test_append_succeeds_when_owned(self):
        mock_db = self._build_mock_db(existing_results=[])
        new_result = {'provider': 'Claude', 'success': True, 'response_time': 1.0}

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.append_chat_history_result('uid1', 'hist1', new_result)

        self.assertTrue(result)
        mock_db.collection.return_value.document.return_value.update.assert_called_once()

    def test_appended_result_is_written_into_results_field(self):
        mock_db = self._build_mock_db(existing_results=[{'provider': 'Yqcloud', 'success': True, 'response_time': 2.0}])
        new_result = {'provider': 'Claude', 'success': True, 'response_time': 1.0}

        with patch.object(auth_db, 'db', mock_db):
            auth_db.append_chat_history_result('uid1', 'hist1', new_result)

        written = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        self.assertIn('results', written)
        providers = [r['provider'] for r in written['results']]
        self.assertIn('Claude', providers)
        self.assertIn('Yqcloud', providers)
        self.assertEqual(len(written['results']), 2)

    def test_existing_results_are_preserved_not_overwritten(self):
        existing = [
            {'provider': 'Yqcloud', 'success': True, 'response_time': 0.5},
            {'provider': 'OperaAria', 'success': False, 'response_time': 3.0},
        ]
        mock_db = self._build_mock_db(existing_results=existing)
        new_result = {'provider': 'Claude', 'success': True, 'response_time': 1.0}

        with patch.object(auth_db, 'db', mock_db):
            auth_db.append_chat_history_result('uid1', 'hist1', new_result)

        written = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        providers = {r['provider'] for r in written['results']}
        self.assertEqual(providers, {'Yqcloud', 'OperaAria', 'Claude'})

    def test_appended_results_are_sorted_success_first_then_faster_first(self):
        existing = [
            {'provider': 'SlowSuccess', 'success': True, 'response_time': 9.0},
            {'provider': 'Failure', 'success': False, 'response_time': 0.1},
        ]
        mock_db = self._build_mock_db(existing_results=existing)
        new_result = {'provider': 'Claude', 'success': True, 'response_time': 1.0}

        with patch.object(auth_db, 'db', mock_db):
            auth_db.append_chat_history_result('uid1', 'hist1', new_result)

        written = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        providers_in_order = [r['provider'] for r in written['results']]
        # Both successes must precede the failure, and among successes the faster
        # (Claude, 1.0s) must precede the slower (SlowSuccess, 9.0s) -- same contract
        # as compare_providers()'s sort key: (not success, response_time).
        self.assertEqual(providers_in_order, ['Claude', 'SlowSuccess', 'Failure'])

    def test_missing_results_field_defaults_to_empty_list_before_append(self):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'user_id': 'uid1'}  # no 'results' key at all
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        new_result = {'provider': 'Claude', 'success': True, 'response_time': 1.0}

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.append_chat_history_result('uid1', 'hist1', new_result)

        self.assertTrue(result)
        written = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        self.assertEqual(len(written['results']), 1)

    def test_denied_when_owned_by_another_user(self):
        mock_db = self._build_mock_db(owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.append_chat_history_result('uid1', 'hist1', {'provider': 'Claude'})

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.update.assert_not_called()

    def test_denied_when_document_does_not_exist(self):
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.append_chat_history_result('uid1', 'ghost', {'provider': 'Claude'})

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.update.assert_not_called()

    def test_returns_false_when_firebase_unavailable(self):
        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.append_chat_history_result('uid1', 'hist1', {'provider': 'Claude'})

        self.assertFalse(result)

    def test_writes_to_history_collection_not_image_history(self):
        mock_db = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            auth_db.append_chat_history_result('uid1', 'hist1', {'provider': 'Claude', 'success': True, 'response_time': 1.0})

        mock_db.collection.assert_any_call('history')
        self.assertNotIn(('image_history',), [c.args for c in mock_db.collection.call_args_list])


class TestAppendImageHistoryResultDb(unittest.TestCase):
    """Mirrors TestAppendChatHistoryResultDb exactly, but for the independent
    'image_history' collection -- append_image_history_result() is Gemini's
    counterpart to append_chat_history_result()."""

    def _build_mock_db(self, exists=True, owner_id='uid1', existing_results=None):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = exists
        mock_doc.to_dict.return_value = {
            'user_id': owner_id,
            'results': existing_results if existing_results is not None else [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock_db

    def test_append_succeeds_when_owned(self):
        mock_db = self._build_mock_db(existing_results=[])
        new_result = {'provider': 'Gemini', 'success': True, 'response_time': 1.0}

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.append_image_history_result('uid1', 'imghist1', new_result)

        self.assertTrue(result)
        mock_db.collection.return_value.document.return_value.update.assert_called_once()

    def test_existing_results_are_preserved_not_overwritten(self):
        existing = [{'provider': 'PollinationsImage', 'success': True, 'response_time': 2.0}]
        mock_db = self._build_mock_db(existing_results=existing)
        new_result = {'provider': 'Gemini', 'success': True, 'response_time': 1.0}

        with patch.object(auth_db, 'db', mock_db):
            auth_db.append_image_history_result('uid1', 'imghist1', new_result)

        written = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        providers = {r['provider'] for r in written['results']}
        self.assertEqual(providers, {'PollinationsImage', 'Gemini'})

    def test_appended_results_are_sorted_success_first_then_faster_first(self):
        existing = [
            {'provider': 'SlowSuccess', 'success': True, 'response_time': 9.0},
            {'provider': 'Failure', 'success': False, 'response_time': 0.1},
        ]
        mock_db = self._build_mock_db(existing_results=existing)
        new_result = {'provider': 'Gemini', 'success': True, 'response_time': 1.0}

        with patch.object(auth_db, 'db', mock_db):
            auth_db.append_image_history_result('uid1', 'imghist1', new_result)

        written = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        providers_in_order = [r['provider'] for r in written['results']]
        self.assertEqual(providers_in_order, ['Gemini', 'SlowSuccess', 'Failure'])

    def test_denied_when_owned_by_another_user(self):
        mock_db = self._build_mock_db(owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.append_image_history_result('uid1', 'imghist1', {'provider': 'Gemini'})

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.update.assert_not_called()

    def test_denied_when_document_does_not_exist(self):
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.append_image_history_result('uid1', 'ghost', {'provider': 'Gemini'})

        self.assertFalse(result)

    def test_returns_false_when_firebase_unavailable(self):
        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.append_image_history_result('uid1', 'imghist1', {'provider': 'Gemini'})

        self.assertFalse(result)

    def test_writes_to_image_history_collection_not_history(self):
        mock_db = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            auth_db.append_image_history_result('uid1', 'imghist1', {'provider': 'Gemini', 'success': True, 'response_time': 1.0})

        mock_db.collection.assert_any_call('image_history')
        self.assertNotIn(('history',), [c.args for c in mock_db.collection.call_args_list])


# ==========================================================================
# White-box / unit tests: the two _append_* wrapper helpers in main.py
# ==========================================================================

class TestAppendClaudeHelperInMain(unittest.TestCase):

    def test_noop_when_history_id_is_none(self):
        with patch.object(main, 'append_chat_history_result') as mock_append:
            main._append_claude_result_to_history('uid1', None, {'provider': 'Claude'})
        mock_append.assert_not_called()

    def test_noop_when_history_id_is_empty_string(self):
        with patch.object(main, 'append_chat_history_result') as mock_append:
            main._append_claude_result_to_history('uid1', '', {'provider': 'Claude'})
        mock_append.assert_not_called()

    def test_calls_append_function_when_history_id_present(self):
        result = {'provider': 'Claude', 'success': True, 'response_time': 1.0}
        with patch.object(main, 'append_chat_history_result', return_value=True) as mock_append:
            main._append_claude_result_to_history('uid1', 'hist1', result)
        mock_append.assert_called_once_with('uid1', 'hist1', result)

    def test_swallows_exception_raised_by_append_function(self):
        with patch.object(main, 'append_chat_history_result', side_effect=RuntimeError('boom')):
            try:
                main._append_claude_result_to_history('uid1', 'hist1', {'provider': 'Claude'})
            except RuntimeError:
                self.fail("_append_claude_result_to_history must swallow exceptions, not propagate them")

    def test_does_not_raise_when_append_function_returns_false(self):
        with patch.object(main, 'append_chat_history_result', return_value=False):
            try:
                main._append_claude_result_to_history('uid1', 'hist1', {'provider': 'Claude'})
            except Exception:
                self.fail("_append_claude_result_to_history must not raise when append returns False")


class TestAppendGeminiHelperInMain(unittest.TestCase):

    def test_noop_when_history_id_is_none(self):
        with patch.object(main, 'append_image_history_result') as mock_append:
            main._append_gemini_result_to_image_history('uid1', None, {'provider': 'Gemini'})
        mock_append.assert_not_called()

    def test_calls_append_function_when_history_id_present(self):
        result = {'provider': 'Gemini', 'success': True, 'response_time': 1.0}
        with patch.object(main, 'append_image_history_result', return_value=True) as mock_append:
            main._append_gemini_result_to_image_history('uid1', 'imghist1', result)
        mock_append.assert_called_once_with('uid1', 'imghist1', result)

    def test_swallows_exception_raised_by_append_function(self):
        with patch.object(main, 'append_image_history_result', side_effect=RuntimeError('boom')):
            try:
                main._append_gemini_result_to_image_history('uid1', 'imghist1', {'provider': 'Gemini'})
            except RuntimeError:
                self.fail("_append_gemini_result_to_image_history must swallow exceptions, not propagate them")

    def test_does_not_raise_when_append_function_returns_false(self):
        with patch.object(main, 'append_image_history_result', return_value=False):
            try:
                main._append_gemini_result_to_image_history('uid1', 'imghist1', {'provider': 'Gemini'})
            except Exception:
                self.fail("_append_gemini_result_to_image_history must not raise when append returns False")


# ==========================================================================
# Black-box / integration tests: history_id flow through POST /api/claude-chat
# and POST /api/gemini-image
# ==========================================================================

class TestClaudeChatHistoryPersistence(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_successful_call_with_history_id_appends_result(self):
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi',
            'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5',
            'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=claude_result), \
                 patch.object(main, 'append_chat_history_result', return_value=True) as mock_append:
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'history_id': 'hist1'
                })

        self.assertEqual(resp.status_code, 200)
        mock_append.assert_called_once_with('uid1', 'hist1', claude_result)

    def test_no_history_id_does_not_attempt_append(self):
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi',
            'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5',
            'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=claude_result), \
                 patch.object(main, 'append_chat_history_result') as mock_append:
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5'
                })

        self.assertEqual(resp.status_code, 200)
        mock_append.assert_not_called()

    def test_failed_call_with_history_id_still_appends_the_failure(self):
        # Symmetric with how g4f provider failures are recorded in history: a failed
        # attempt is still part of the comparison snapshot, not silently dropped.
        claude_result = {
            'provider': 'Claude', 'success': False, 'response': '',
            'error': 'The system is busy and trying to reconnect. Please try again shortly.',
            'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_claude_model', return_value=claude_result), \
                 patch.object(main, 'append_chat_history_result', return_value=True) as mock_append:
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'history_id': 'hist1'
                })

        self.assertEqual(resp.status_code, 200)
        mock_append.assert_called_once_with('uid1', 'hist1', claude_result)
        mock_incr.assert_not_called()

    def test_free_tier_exhausted_does_not_attempt_append(self):
        # Blocked before call_claude_model() is ever invoked -- there is no result to
        # append, regardless of whether history_id was supplied.
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=main.CLAUDE_FREE_TIER_LIMIT), \
                 patch.object(main, 'call_claude_model') as mock_call, \
                 patch.object(main, 'append_chat_history_result') as mock_append:
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'history_id': 'hist1'
                })

        self.assertEqual(resp.status_code, 403)
        mock_call.assert_not_called()
        mock_append.assert_not_called()

    def test_server_credits_exhausted_appends_friendly_message_not_raw_code(self):
        raw_result = {
            'provider': 'Claude', 'success': False, 'response': '',
            'error': 'SERVER_CREDITS_EXHAUSTED', 'error_code': 'SERVER_CREDITS_EXHAUSTED',
            'response_time': 0.3, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_claude_model', return_value=raw_result), \
                 patch.object(main, 'append_chat_history_result', return_value=True) as mock_append:
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'history_id': 'hist1'
                })

        self.assertEqual(resp.status_code, 503)
        mock_incr.assert_not_called()
        mock_append.assert_called_once()
        _, _, persisted_result = mock_append.call_args[0]
        self.assertNotIn('error_code', persisted_result)
        self.assertNotEqual(persisted_result['error'], 'SERVER_CREDITS_EXHAUSTED')
        self.assertIn("developer's Claude API account has run out of credits", persisted_result['error'])

    def test_append_failure_does_not_break_the_response(self):
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi',
            'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5',
            'type': 'anthropic',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value=claude_result), \
                 patch.object(main, 'append_chat_history_result', side_effect=RuntimeError('firestore down')):
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'history_id': 'hist1'
                })

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])


class TestGeminiImageHistoryPersistence(unittest.TestCase):
    """Mirrors TestClaudeChatHistoryPersistence exactly, for /api/gemini-image +
    append_image_history_result()."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_successful_call_with_history_id_appends_result(self):
        gemini_result = {
            'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
            'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro',
            'type': 'google_genai',
        }
        with main.app.test_client() as client:
            self._login(client)
            # This test is about the routing/wiring (does a successful call trigger an
            # append at all, with the right args), not about local-disk persistence --
            # that's covered separately by TestPersistImageResultLocalCopy -- so the
            # b64_json->url conversion step is stubbed out as a no-op here.
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage'), \
                 patch.object(main, 'call_gemini_image_model', return_value=gemini_result), \
                 patch.object(main, '_persist_image_result_local_copy', side_effect=lambda r: r), \
                 patch.object(main, 'append_image_history_result', return_value=True) as mock_append:
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro', 'history_id': 'imghist1'
                })

        self.assertEqual(resp.status_code, 200)
        mock_append.assert_called_once_with('uid1', 'imghist1', gemini_result)

    def test_no_history_id_does_not_attempt_append(self):
        gemini_result = {
            'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
            'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro',
            'type': 'google_genai',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage'), \
                 patch.object(main, 'call_gemini_image_model', return_value=gemini_result), \
                 patch.object(main, 'append_image_history_result') as mock_append:
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })

        self.assertEqual(resp.status_code, 200)
        mock_append.assert_not_called()

    def test_failed_call_with_history_id_still_appends_the_failure(self):
        gemini_result = {
            'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
            'error': 'The system is busy and trying to reconnect. Please try again shortly.',
            'response_time': 1.0, 'model': 'nano-banana-pro', 'type': 'google_genai',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_gemini_image_model', return_value=gemini_result), \
                 patch.object(main, 'append_image_history_result', return_value=True) as mock_append:
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro', 'history_id': 'imghist1'
                })

        self.assertEqual(resp.status_code, 200)
        mock_append.assert_called_once_with('uid1', 'imghist1', gemini_result)
        mock_incr.assert_not_called()

    def test_free_tier_exhausted_does_not_attempt_append(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=main.GEMINI_FREE_TIER_LIMIT), \
                 patch.object(main, 'call_gemini_image_model') as mock_call, \
                 patch.object(main, 'append_image_history_result') as mock_append:
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro', 'history_id': 'imghist1'
                })

        self.assertEqual(resp.status_code, 403)
        mock_call.assert_not_called()
        mock_append.assert_not_called()

    def test_server_quota_exhausted_appends_friendly_message_not_raw_code(self):
        raw_result = {
            'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
            'error': 'SERVER_QUOTA_EXHAUSTED', 'error_code': 'SERVER_QUOTA_EXHAUSTED',
            'response_time': 0.3, 'model': 'nano-banana-pro', 'type': 'google_genai',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_gemini_image_model', return_value=raw_result), \
                 patch.object(main, 'append_image_history_result', return_value=True) as mock_append:
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro', 'history_id': 'imghist1'
                })

        self.assertEqual(resp.status_code, 503)
        mock_incr.assert_not_called()
        mock_append.assert_called_once()
        _, _, persisted_result = mock_append.call_args[0]
        self.assertNotIn('error_code', persisted_result)
        self.assertNotEqual(persisted_result['error'], 'SERVER_QUOTA_EXHAUSTED')
        self.assertIn("developer's Gemini API account has run out of quota", persisted_result['error'])

    def test_append_failure_does_not_break_the_response(self):
        gemini_result = {
            'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
            'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro',
            'type': 'google_genai',
        }
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage'), \
                 patch.object(main, 'call_gemini_image_model', return_value=gemini_result), \
                 patch.object(main, 'append_image_history_result', side_effect=RuntimeError('firestore down')):
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro', 'history_id': 'imghist1'
                })

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])


# ==========================================================================
# Gray-box / end-to-end tests: drives the real save_chat_history()/
# save_image_history()/append_*_result() implementations (not mocked out)
# with a minimal in-memory "fake Firestore" stand-in, verifying across two
# real requests that the persisted document ends up containing both g4f and
# frontier-model results -- closer to a genuine regression check than merely
# asserting "some function was called with the right arguments."
# ==========================================================================

class _FakeFirestoreDoc:
    def __init__(self, data, doc_id, exists=True):
        self._data = data
        self.id = doc_id
        self.exists = exists

    def to_dict(self):
        return dict(self._data)


class _FakeDocRef:
    def __init__(self, collection_store, doc_id):
        self._collection_store = collection_store
        self.id = doc_id

    def get(self):
        data = self._collection_store.get(self.id)
        if data is None:
            return _FakeFirestoreDoc({}, self.id, exists=False)
        return _FakeFirestoreDoc(data, self.id, exists=True)

    def update(self, fields):
        self._collection_store[self.id].update(fields)


class _FakeCollectionRef:
    def __init__(self, collection_store):
        self._collection_store = collection_store
        self._next_id = 1

    def document(self, doc_id):
        return _FakeDocRef(self._collection_store, doc_id)

    def add(self, data):
        doc_id = f'fakedoc{self._next_id}'
        self._next_id += 1
        self._collection_store[doc_id] = dict(data)
        return (None, _FakeDocRef(self._collection_store, doc_id))


class _FakeFirestoreClient:
    """Just enough of the firestore.Client() surface for save_*/get_*/append_*
    (collection().document()/add()/get()/update()) -- no querying, no real network."""

    def __init__(self):
        self._collections = {}

    def collection(self, name):
        self._collections.setdefault(name, {})
        return _FakeCollectionRef(self._collections[name])


class TestEndToEndClaudeResultSurvivesInPersistedHistory(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.fake_db = _FakeFirestoreClient()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_claude_result_appears_when_history_is_reloaded(self):
        g4f_result = {
            'provider': 'Yqcloud', 'success': True, 'response': 'hi from g4f',
            'error': '', 'response_time': 0.4, 'model': 'gpt-3.5-turbo', 'type': 'g4f',
        }
        claude_result = {
            'provider': 'Claude', 'success': True, 'response': 'hi from Claude',
            'error': '', 'response_time': 1.2, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }
        fake_provider = MagicMock()
        fake_provider.__name__ = 'Yqcloud'

        with patch.object(auth_db, 'db', self.fake_db), \
                main.app.test_client() as client:
            self._login(client)

            with patch.object(main, 'test_g4f_provider', return_value=dict(g4f_result)), \
                    patch.object(main, 'G4F_AVAILABLE', True), \
                    patch.object(main, 'G4F_PROVIDERS', [fake_provider]):
                compare_resp = client.post('/api/compare', json={
                    'prompt': 'hello', 'providers': ['Yqcloud']
                })
            self.assertEqual(compare_resp.status_code, 200)
            history_id = compare_resp.get_json()['history_id']
            self.assertIsNotNone(history_id)

            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                    patch.object(main, 'increment_claude_free_tier_usage'), \
                    patch.object(main, 'call_claude_model', return_value=claude_result):
                claude_resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5', 'history_id': history_id
                })
            self.assertEqual(claude_resp.status_code, 200)

            reloaded = auth_db.get_chat_history_by_id('uid1', history_id)

        self.assertIsNotNone(reloaded)
        providers_in_reloaded_snapshot = [r['provider'] for r in reloaded['results']]
        self.assertIn('Yqcloud', providers_in_reloaded_snapshot)
        self.assertIn('Claude', providers_in_reloaded_snapshot)


class TestEndToEndGeminiResultSurvivesInPersistedImageHistory(unittest.TestCase):
    """Mirrors TestEndToEndClaudeResultSurvivesInPersistedHistory for the image
    generation pipeline: POST /api/generate-images then POST /api/gemini-image."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.fake_db = _FakeFirestoreClient()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_gemini_result_appears_when_image_history_is_reloaded(self):
        g4f_image_result = {
            'provider': 'PollinationsImage', 'success': True, 'url': '/media/x.png',
            'b64_json': None, 'error': '', 'response_time': 0.6, 'model': 'auto',
            'type': 'g4f_image',
        }
        gemini_result = {
            'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc123',
            'error': '', 'response_time': 1.5, 'model': 'nano-banana-pro', 'type': 'google_genai',
        }
        fake_provider = MagicMock()
        fake_provider.__name__ = 'PollinationsImage'

        with patch.object(auth_db, 'db', self.fake_db), \
                main.app.test_client() as client:
            self._login(client)

            with patch.object(main, 'test_g4f_image_provider', return_value=dict(g4f_image_result)), \
                    patch.object(main, 'G4F_AVAILABLE', True), \
                    patch.object(main, 'IMAGE_PROVIDERS', [fake_provider]), \
                    patch.object(main, 'get_image_timeouts', return_value=(40, 85)):
                generate_resp = client.post('/api/generate-images', json={
                    'prompt': 'a cat', 'providers': ['PollinationsImage']
                })
            self.assertEqual(generate_resp.status_code, 200)
            history_id = generate_resp.get_json()['history_id']
            self.assertIsNotNone(history_id)

            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                    patch.object(main, 'increment_gemini_free_tier_usage'), \
                    patch.object(main, 'call_gemini_image_model', return_value=gemini_result):
                gemini_resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro', 'history_id': history_id
                })
            self.assertEqual(gemini_resp.status_code, 200)

            reloaded = auth_db.get_image_history_by_id('uid1', history_id)

        self.assertIsNotNone(reloaded)
        providers_in_reloaded_snapshot = [r['provider'] for r in reloaded['results']]
        self.assertIn('PollinationsImage', providers_in_reloaded_snapshot)
        self.assertIn('Gemini', providers_in_reloaded_snapshot)


class TestEndToEndChatGPTImageSurvivesLocalDiskFailure(unittest.TestCase):
    """Regression for the 2026-07-08 production bug: a ChatGPT image generation that
    succeeded (and rendered fine on the live page) would silently vanish from the saved
    image_history record. Root cause: main._persist_image_result_local_copy() used to
    fall back to returning the *original*, still-multi-MB b64_json result whenever the
    local get_media_dir() write failed for any reason (e.g. a full/read-only disk on a
    real GAE instance). That oversized dict then crashed
    auth_db.append_image_history_result()'s Firestore write with 'Property array
    contains an invalid nested entity' -- the very error this persistence step exists to
    avoid -- and the caller only logged and swallowed that exception, so the record
    never made it into 'results' at all.

    This drives the real (non-mocked) save_image_history()/append_image_history_result()
    against the in-memory Firestore double, with the local disk write forced to fail,
    and proves the ChatGPT entry still survives into the reloaded document."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.fake_db = _FakeFirestoreClient()

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_chatgpt_image_result_appears_even_when_local_persist_fails(self):
        import base64

        g4f_image_result = {
            'provider': 'PollinationsImage', 'success': True, 'url': '/media/x.png',
            'b64_json': None, 'error': '', 'response_time': 0.6, 'model': 'auto',
            'type': 'g4f_image',
        }
        # A realistic gpt-image payload is well past Firestore's ~1MB array-entry
        # ceiling -- this is exactly the size that used to blow up the append once the
        # (failed) local persistence step left it inline.
        chatgpt_result = {
            'provider': 'ChatGPT', 'success': True, 'url': None,
            'b64_json': base64.b64encode(os.urandom(1500 * 1024)).decode(),
            'error': '', 'response_time': 2.1, 'model': 'gpt-image-2', 'type': 'openai_image',
        }
        fake_provider = MagicMock()
        fake_provider.__name__ = 'PollinationsImage'

        with patch.object(auth_db, 'db', self.fake_db), \
                main.app.test_client() as client:
            self._login(client)

            with patch.object(main, 'test_g4f_image_provider', return_value=dict(g4f_image_result)), \
                    patch.object(main, 'G4F_AVAILABLE', True), \
                    patch.object(main, 'IMAGE_PROVIDERS', [fake_provider]), \
                    patch.object(main, 'get_image_timeouts', return_value=(40, 85)):
                generate_resp = client.post('/api/generate-images', json={
                    'prompt': 'a cat', 'providers': ['PollinationsImage']
                })
            self.assertEqual(generate_resp.status_code, 200)
            history_id = generate_resp.get_json()['history_id']
            self.assertIsNotNone(history_id)

            # Simulate the real-GAE failure mode: the local get_media_dir() write
            # raises (e.g. a full or read-only instance disk), so the persistence step
            # cannot swap b64_json for a url.
            with patch.object(main, 'CHATGPT_AVAILABLE', True), \
                    patch.object(main, 'get_free_tier_usage', return_value=0), \
                    patch.object(main, 'increment_free_tier_usage'), \
                    patch.object(main, 'call_chatgpt_image_model', return_value=chatgpt_result), \
                    patch('builtins.open', side_effect=OSError('No space left on device')):
                chatgpt_resp = client.post('/api/chatgpt-image', json={
                    'prompt': 'a cat', 'model': 'gpt-image-2', 'history_id': history_id
                })
            self.assertEqual(chatgpt_resp.status_code, 200)
            # The response returned to the frontend for *this* request is untouched --
            # the user still sees their image render immediately.
            self.assertTrue(chatgpt_resp.get_json()['success'])

            reloaded = auth_db.get_image_history_by_id('uid1', history_id)

        self.assertIsNotNone(reloaded)
        providers_in_reloaded_snapshot = [r['provider'] for r in reloaded['results']]
        self.assertIn('PollinationsImage', providers_in_reloaded_snapshot)
        # The core regression check: the ChatGPT entry must still be present, even
        # though its local disk persistence failed -- previously it silently vanished.
        self.assertIn('ChatGPT', providers_in_reloaded_snapshot)
        chatgpt_entry = next(r for r in reloaded['results'] if r['provider'] == 'ChatGPT')
        self.assertFalse(chatgpt_entry['success'])
        self.assertIsNone(chatgpt_entry['b64_json'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
