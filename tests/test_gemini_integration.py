"""Tests for the official Google Gemini ("Nano Banana") image-generation integration
(2026-07-04 新增).

Covers the fourth, fully independent call chain added alongside the two g4f chains
(text ChatCompletion / image generate()) and the Claude chat chain:
`main.call_gemini_image_model()` + `POST /api/gemini-image`, plus the two new
`auth/db.py` helpers (`get_gemini_free_tier_usage`/`increment_gemini_free_tier_usage`)
backing the per-account free-tier gate. Deliberately mirrors
`tests/test_claude_integration.py`'s structure and split (white-box/unit vs.
black-box/integration) since the two features are architecturally identical --
"a paid frontier provider gated by a 1-use free tier, with a user-supplied-key
bypass" -- just applied to image generation instead of chat.

Mirrors `test_claude_integration.py`'s own real-world verification pattern: on
2026-07-05 a real (but zero-quota/free-tier) `GEMINI_API_KEY` was used to call
`call_gemini_image_model()` against all three Nano Banana model IDs
(gemini-3.1-flash-image / gemini-3-pro-image / gemini-3.1-flash-lite-image). All three
returned the identical real error shape: 429 + exception type name `RateLimitError` +
`.status_code == 429` (`.status` unset) + message
`"Error code: 429 - {'error': {'message': 'You do not have enough quota to make this
request.', 'code': 'too_many_requests'}}"`. This both confirms the three model IDs are
valid (an unknown model name would 404/400 instead of 429) and gives a real captured
shape for `test_real_world_quota_exhausted_error_maps_to_server_quota_exhausted` below
(mirroring Claude's `_make_credits_exhausted_error` default parameters). The
403/PERMISSION_DENIED ("invalid/missing key") branch is still verified only against the
officially published Gemini API HTTP status table
(https://ai.google.dev/gemini-api/docs/troubleshooting) -- the available key is a valid
key with zero quota, not an invalid one, so that branch has not been hit for real.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _FakeGeminiError(Exception):
    """Stand-in for whatever exception `google_genai.Client().interactions.create()`
    raises on failure. `call_gemini_image_model()` classifies errors purely via
    `getattr(e, 'status_code'/'code'/'status'/'message', ...)` duck typing rather than
    `isinstance` checks against a specific SDK exception class (see the long comment
    above `call_gemini_image_model()` in main.py for why: the real exception classes
    live in a private, underscore-prefixed submodule of `google-genai` with no stable
    public import path). A plain Exception subclass carrying just these attributes is
    therefore a faithful enough stand-in for exercising that classification logic."""

    def __init__(self, status_code=None, code=None, status=None, message=None):
        super().__init__(message or '')
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        if message is not None:
            self.message = message


def _fake_gemini_interaction(image_b64='ZmFrZWltYWdlYnl0ZXM='):
    image_content = MagicMock()
    image_content.data = image_b64
    interaction = MagicMock()
    interaction.output_image = image_content
    return interaction


def _fake_gemini_client(interaction=None, side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.interactions.create.side_effect = side_effect
    else:
        client.interactions.create.return_value = interaction or _fake_gemini_interaction()
    return client


def _make_real_quota_exhausted_error():
    """Reconstructs the *actual* error observed on 2026-07-05 calling all three Nano
    Banana models with a real, valid-but-zero-quota GEMINI_API_KEY (see this module's
    docstring). `type(e).__name__` was really `RateLimitError` in that run; since the
    real class lives in a private google-genai submodule with no stable import path
    (see the long comment above `call_gemini_image_model()` in main.py), this
    reconstructs an equivalent-shaped plain exception instead of importing it."""
    return _FakeGeminiError(
        status_code=429,
        message="Error code: 429 - {'error': {'message': 'You do not have enough quota "
                 "to make this request.', 'code': 'too_many_requests'}}",
    )


# ==========================================================================
# 白盒/单元测试
# ==========================================================================

class TestCallGeminiImageModelKeyRouting(unittest.TestCase):
    """call_gemini_image_model() 的 Key 路由与错误分类逻辑（不经过 Flask 路由）。"""

    def test_uses_default_client_when_no_user_key(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client()
            mock_genai.Client.return_value = mock_client

            main.call_gemini_image_model('a cat', 'nano-banana-pro', user_api_key=None)

            mock_genai.Client.assert_called_once_with()

    def test_uses_user_key_when_provided(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client()
            mock_genai.Client.return_value = mock_client

            main.call_gemini_image_model('a cat', 'nano-banana-pro', user_api_key='AIza-user-supplied')

            mock_genai.Client.assert_called_once_with(api_key='AIza-user-supplied')

    def test_success_result_shape(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client(_fake_gemini_interaction('c29tZWJhc2U2NA=='))
            mock_genai.Client.return_value = mock_client

            result = main.call_gemini_image_model('a cat', 'nano-banana-pro')

            self.assertEqual(result['provider'], 'Gemini')
            self.assertTrue(result['success'])
            self.assertEqual(result['b64_json'], 'c29tZWJhc2U2NA==')
            self.assertIsNone(result['url'])
            self.assertEqual(result['error'], '')
            self.assertEqual(result['model'], 'nano-banana-pro')
            self.assertEqual(result['type'], 'google_genai')
            self.assertIsInstance(result['response_time'], float)

    def test_request_uses_mapped_model_id_not_the_ui_key(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client()
            mock_genai.Client.return_value = mock_client

            main.call_gemini_image_model('a cat', 'nano-banana-pro')

            _, kwargs = mock_client.interactions.create.call_args
            self.assertEqual(kwargs['model'], 'gemini-3-pro-image')
            self.assertEqual(kwargs['input'], 'a cat')

    def test_nano_banana_2_and_lite_map_to_their_own_model_ids(self):
        """2026-07-05: Nano Banana 2/Lite were added alongside the already-wired Pro
        tier (see GEMINI_IMAGE_MODELS in main.py -- all three IDs were already verified
        legitimate via a real zero-quota API key before this test was written, but only
        Pro was actually reachable through call_gemini_image_model()/the UI dropdown).
        Confirms both new UI keys route to their own distinct official model IDs rather
        than accidentally falling through to Pro's."""
        for model_key, expected_model_id in (
            ('nano-banana-2', 'gemini-3.1-flash-image'),
            ('nano-banana-lite', 'gemini-3.1-flash-lite-image'),
        ):
            with self.subTest(model_key=model_key):
                with patch.object(main, 'google_genai') as mock_genai:
                    mock_client = _fake_gemini_client(_fake_gemini_interaction('YWJj'))
                    mock_genai.Client.return_value = mock_client

                    result = main.call_gemini_image_model('a cat', model_key)

                    _, kwargs = mock_client.interactions.create.call_args
                    self.assertEqual(kwargs['model'], expected_model_id)
                    self.assertTrue(result['success'])
                    self.assertEqual(result['model'], model_key)

    def test_unknown_model_key_returns_error_without_calling_api(self):
        with patch.object(main, 'google_genai') as mock_genai:
            result = main.call_gemini_image_model('a cat', 'not-a-real-model')

        self.assertFalse(result['success'])
        self.assertIn('Unknown Gemini model', result['error'])
        mock_genai.Client.assert_not_called()

    def test_no_image_in_response_is_a_failure(self):
        with patch.object(main, 'google_genai') as mock_genai:
            interaction = MagicMock()
            interaction.output_image = None
            mock_client = _fake_gemini_client(interaction)
            mock_genai.Client.return_value = mock_client

            result = main.call_gemini_image_model('a cat', 'nano-banana-pro')

        self.assertFalse(result['success'])
        self.assertIn('did not return an image', result['error'])

    def test_quota_exhausted_via_429_status_code_maps_to_server_quota_exhausted(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client(side_effect=_FakeGeminiError(
                status_code=429, message='Quota exceeded'
            ))
            mock_genai.Client.return_value = mock_client

            result = main.call_gemini_image_model('a cat', 'nano-banana-pro')

        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'SERVER_QUOTA_EXHAUSTED')
        self.assertEqual(result['error_code'], 'SERVER_QUOTA_EXHAUSTED')

    def test_real_world_quota_exhausted_error_maps_to_server_quota_exhausted(self):
        """实测形状（2026-07-05，用一个真实但零配额的 GEMINI_API_KEY 直接调用三个
        Nano Banana 模型观察到，三个模型返回完全相同的错误）：429 + 异常类型名
        RateLimitError + .status_code == 429（.status 属性不存在）+ message 形如
        "Error code: 429 - {'error': {'message': 'You do not have enough quota to
        make this request.', 'code': 'too_many_requests'}}"。这与官方 troubleshooting
        文档字面暗示的 "RESOURCE_EXHAUSTED 状态字符串" 不完全一致——真正稳定的信号是
        .status_code == 429，不是 .status 字符串（call_gemini_image_model() 对
        .status 的检查是防御性兜底，见其上方注释）。"""
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client(side_effect=_make_real_quota_exhausted_error())
            mock_genai.Client.return_value = mock_client

            result = main.call_gemini_image_model('a cat', 'nano-banana-pro')

        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'SERVER_QUOTA_EXHAUSTED')
        self.assertEqual(result['error_code'], 'SERVER_QUOTA_EXHAUSTED')

    def test_quota_exhausted_via_resource_exhausted_status_string(self):
        """The official troubleshooting doc's HTTP table names the status string
        RESOURCE_EXHAUSTED alongside 429 -- covered as an independent signal in case a
        wrapped error surfaces `.status`/`.code` instead of `.status_code`."""
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client(side_effect=_FakeGeminiError(
                code=429, status='RESOURCE_EXHAUSTED', message='Quota exceeded'
            ))
            mock_genai.Client.return_value = mock_client

            result = main.call_gemini_image_model('a cat', 'nano-banana-pro')

        self.assertEqual(result['error_code'], 'SERVER_QUOTA_EXHAUSTED')

    def test_permission_denied_gives_friendly_invalid_key_message(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client(side_effect=_FakeGeminiError(
                status_code=403, message="Your API key doesn't have the required permissions."
            ))
            mock_genai.Client.return_value = mock_client

            result = main.call_gemini_image_model('a cat', 'nano-banana-pro')

        self.assertFalse(result['success'])
        self.assertNotIn('error_code', result)
        self.assertIn('Invalid or missing Gemini API key', result['error'])

    def test_other_status_code_errors_are_not_misclassified_as_quota_exhausted(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_client = _fake_gemini_client(side_effect=_FakeGeminiError(
                status_code=500, message='An unexpected error occurred on the server side.'
            ))
            mock_genai.Client.return_value = mock_client

            result = main.call_gemini_image_model('a cat', 'nano-banana-pro')

        self.assertFalse(result['success'])
        self.assertNotIn('error_code', result)
        self.assertIn('500', result['error'])
        self.assertIn('unexpected error', result['error'])

    def test_error_with_no_status_attributes_falls_back_to_message(self):
        """Covers e.g. the real, empirically-observed `ValueError` google-genai raises
        at `Client()` construction time when no API key/env var is configured -- a
        plain exception with none of status_code/code/status set."""
        with patch.object(main, 'google_genai') as mock_genai:
            mock_genai.Client.side_effect = ValueError('No API key was provided.')

            result = main.call_gemini_image_model('a cat', 'nano-banana-pro')

        self.assertFalse(result['success'])
        self.assertNotIn('error_code', result)
        self.assertEqual(result['error'], 'No API key was provided.')


class TestGeminiFreeTierCounterDb(unittest.TestCase):
    """auth/db.py 的两个新增计数器函数（纯 db 层，mock Firestore，与
    tests/test_claude_integration.py::TestClaudeFreeTierCounterDb 逐一同构，只是字段名
    换成 gemini_free_tier_usage）。"""

    def test_get_usage_defaults_to_zero_when_field_missing(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'username': 'alice'}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch.object(auth_db, 'db', mock_db):
            usage = auth_db.get_gemini_free_tier_usage('uid1')

        self.assertEqual(usage, 0)

    def test_get_usage_returns_existing_value(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'gemini_free_tier_usage': 3}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch.object(auth_db, 'db', mock_db):
            usage = auth_db.get_gemini_free_tier_usage('uid1')

        self.assertEqual(usage, 3)

    def test_get_usage_returns_zero_for_nonexistent_user(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch.object(auth_db, 'db', mock_db):
            usage = auth_db.get_gemini_free_tier_usage('ghost-uid')

        self.assertEqual(usage, 0)

    def test_get_usage_returns_zero_when_firebase_unavailable(self):
        from auth import db as auth_db
        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            usage = auth_db.get_gemini_free_tier_usage('uid1')
        self.assertEqual(usage, 0)

    def test_increment_calls_firestore_increment_sentinel(self):
        from auth import db as auth_db
        from firebase_admin import firestore
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.increment_gemini_free_tier_usage('uid1')

        self.assertTrue(result)
        mock_doc_ref.update.assert_called_once()
        call_kwargs = mock_doc_ref.update.call_args[0][0]
        self.assertIn('gemini_free_tier_usage', call_kwargs)
        self.assertIsInstance(call_kwargs['gemini_free_tier_usage'], type(firestore.Increment(1)))

    def test_increment_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db
        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.increment_gemini_free_tier_usage('uid1')
        self.assertIsNone(result)

    def test_claude_and_gemini_counters_are_independent_fields(self):
        """Regression guard: the two free-tier counters must never collapse onto the
        same Firestore field -- a user's Claude trial and Gemini trial are independent
        (see CLAUDE.md), so incrementing one must not touch the other's key name."""
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            auth_db.increment_gemini_free_tier_usage('uid1')

        call_kwargs = mock_doc_ref.update.call_args[0][0]
        self.assertNotIn('claude_free_tier_usage', call_kwargs)


class TestGeminiImageRouteKeyRoutingAndCounter(unittest.TestCase):
    """/api/gemini-image 路由本身的计数器 + Key 路由决策逻辑（mock 掉
    call_gemini_image_model()/两个计数器函数，只验证路由层面"谁调用了谁、传了什么
    参数"），与 test_claude_integration.py::TestClaudeChatRouteKeyRoutingAndCounter
    逐一同构。"""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_successful_call_without_own_key_increments_counter(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_gemini_image_model', return_value={
                     'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
                     'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro',
                     'type': 'google_genai',
                 }):
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_called_once_with('uid1')

    def test_single_request_increments_counter_exactly_once_regardless_of_model_tier(self):
        """One generate click == one quota use, no matter which Nano Banana tier the
        request selected (2026-07-05 product requirement, alongside raising
        GEMINI_FREE_TIER_LIMIT to 10). There is only ever one /api/gemini-image call per
        click regardless of how many tiers GEMINI_IMAGE_MODELS lists -- the model picker
        is a single-select <select>, not a checkbox per model -- so this is really a
        regression guard against ever accidentally calling increment more than once."""
        for model_key in ('nano-banana-pro', 'nano-banana-2', 'nano-banana-lite'):
            with self.subTest(model_key=model_key):
                with main.app.test_client() as client:
                    self._login(client)
                    with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                         patch.object(main, 'increment_gemini_free_tier_usage') as mock_incr, \
                         patch.object(main, 'call_gemini_image_model', return_value={
                             'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
                             'error': '', 'response_time': 1.0, 'model': model_key,
                             'type': 'google_genai',
                         }):
                        resp = client.post('/api/gemini-image', json={
                            'prompt': 'a cat', 'model': model_key
                        })

                self.assertEqual(resp.status_code, 200)
                mock_incr.assert_called_once_with('uid1')

    def test_failed_call_does_not_increment_counter(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_gemini_image_model', return_value={
                     'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
                     'error': 'boom', 'response_time': 1.0, 'model': 'nano-banana-pro',
                     'type': 'google_genai',
                 }):
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_not_called()

    def test_own_key_header_bypasses_counter_check_and_increment(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage') as mock_get_usage, \
                 patch.object(main, 'increment_gemini_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_gemini_image_model', return_value={
                     'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
                     'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro',
                     'type': 'google_genai',
                 }) as mock_call:
                resp = client.post(
                    '/api/gemini-image',
                    json={'prompt': 'a cat', 'model': 'nano-banana-pro'},
                    headers={'X-User-Gemini-Key': 'AIza-mykey'}
                )

        self.assertEqual(resp.status_code, 200)
        mock_get_usage.assert_not_called()
        mock_incr.assert_not_called()
        mock_call.assert_called_once_with('a cat', 'nano-banana-pro', 'AIza-mykey')

    def test_no_own_key_forwards_none_to_call_gemini_image_model(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage'), \
                 patch.object(main, 'call_gemini_image_model', return_value={
                     'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
                     'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro',
                     'type': 'google_genai',
                 }) as mock_call:
                client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })

        mock_call.assert_called_once_with('a cat', 'nano-banana-pro', None)

    def test_successful_call_does_not_create_a_new_image_history_entry(self):
        """Danger-zone regression guard (CLAUDE.md): /api/gemini-image must never call
        save_image_history() -- it cannot create a brand-new image_history document on
        its own, unlike /api/generate-images. Since 2026-07-05 it *can* append its
        result into an *already-existing* entry when a history_id is supplied (see
        tests/test_frontier_history_persistence.py for that behavior) -- this test only
        pins down that save_image_history specifically is never touched."""
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage'), \
                 patch.object(main, 'save_image_history') as mock_save, \
                 patch.object(main, 'call_gemini_image_model', return_value={
                     'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc',
                     'error': '', 'response_time': 1.0, 'model': 'nano-banana-pro',
                     'type': 'google_genai',
                 }):
                client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })

        mock_save.assert_not_called()


# ==========================================================================
# 黑盒/集成测试
# ==========================================================================

class TestGeminiImageAuthGuard(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_guest_request_returns_401(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            resp = client.post('/api/gemini-image', json={
                'prompt': 'a cat', 'model': 'nano-banana-pro'
            })
        self.assertEqual(resp.status_code, 401)

    def test_anonymous_request_returns_401(self):
        with main.app.test_client() as client:
            resp = client.post('/api/gemini-image', json={
                'prompt': 'a cat', 'model': 'nano-banana-pro'
            })
        self.assertEqual(resp.status_code, 401)


class TestGeminiImageFreeTierFlow(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_first_request_succeeds(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage'), \
                 patch.object(main, 'call_gemini_image_model', return_value={
                     'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'abc123',
                     'error': '', 'response_time': 0.5, 'model': 'nano-banana-pro',
                     'type': 'google_genai',
                 }):
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a fancy cat', 'model': 'nano-banana-pro'
                })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['b64_json'], 'abc123')

    def test_request_at_limit_without_own_key_returns_free_tier_exhausted(self):
        """usage already == GEMINI_FREE_TIER_LIMIT (10, 2026-07-05 raised from 1) --
        the (limit+1)th request without an own key must be rejected before ever calling
        call_gemini_image_model(). Reads the limit off main.GEMINI_FREE_TIER_LIMIT rather
        than hardcoding a number so this test doesn't silently stop testing "at the limit"
        if the constant changes again."""
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=main.GEMINI_FREE_TIER_LIMIT), \
                 patch.object(main, 'call_gemini_image_model') as mock_call:
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'another cat', 'model': 'nano-banana-pro'
                })

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()['error'], 'FREE_TIER_EXHAUSTED')
        mock_call.assert_not_called()

    def test_own_key_bypasses_exhausted_free_tier(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=99), \
                 patch.object(main, 'call_gemini_image_model', return_value={
                     'provider': 'Gemini', 'success': True, 'url': None, 'b64_json': 'still-works',
                     'error': '', 'response_time': 0.5, 'model': 'nano-banana-pro',
                     'type': 'google_genai',
                 }):
                resp = client.post(
                    '/api/gemini-image',
                    json={'prompt': 'another cat', 'model': 'nano-banana-pro'},
                    headers={'X-User-Gemini-Key': 'AIza-mykey'}
                )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])


class TestGeminiImageServerQuotaExhausted(unittest.TestCase):
    """端到端复现"开发者账户配额耗尽"场景：mock 掉 call_gemini_image_model() 使其返回
    error_code == 'SERVER_QUOTA_EXHAUSTED'（call_gemini_image_model 自身的分类逻辑已在
    TestCallGeminiImageModelKeyRouting 里单独覆盖），验证一路传导到 HTTP 响应体的
    503 SERVER_QUOTA_EXHAUSTED，而不消耗用户的免费额度。"""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_quota_exhausted_forwarded_as_server_quota_exhausted(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_gemini_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_gemini_image_model', return_value={
                     'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
                     'error': 'SERVER_QUOTA_EXHAUSTED', 'error_code': 'SERVER_QUOTA_EXHAUSTED',
                     'response_time': 0.3, 'model': 'nano-banana-pro', 'type': 'google_genai',
                 }):
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })

        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertEqual(data['error'], 'SERVER_QUOTA_EXHAUSTED')
        self.assertIn('message', data)
        mock_incr.assert_not_called()


class TestGeminiImageValidation(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 'uid1'

    def test_missing_prompt_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/gemini-image', json={'model': 'nano-banana-pro'})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_model_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/gemini-image', json={
                'prompt': 'a cat', 'model': 'nano-banana-does-not-exist'
            })
        self.assertEqual(resp.status_code, 400)

    def test_gemini_unavailable_returns_503(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'GEMINI_AVAILABLE', False):
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()['error'], 'GEMINI_UNAVAILABLE')


class TestHealthCheckReportsGeminiAvailability(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_health_includes_gemini_available_flag(self):
        with main.app.test_client() as client:
            resp = client.get('/health')
        data = resp.get_json()
        self.assertIn('gemini_available', data)
        self.assertIn('gemini_models', data)
        self.assertIn('nano-banana-pro', data['gemini_models'])


if __name__ == '__main__':
    unittest.main()
