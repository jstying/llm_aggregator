"""Tests for the three new frontier integrations added 2026-07-06, all built by
mirroring the existing Claude/Gemini-image architecture (see CLAUDE.md 第 10 节
"安全区：新增前沿 provider"):

- ChatGPT text (call_chatgpt_model() + POST /api/chatgpt-chat)
- Gemini text (call_gemini_text_model() + POST /api/gemini-chat)
- ChatGPT image (call_chatgpt_image_model() + POST /api/chatgpt-image)

Deliberately mirrors tests/test_claude_integration.py and
tests/test_gemini_integration.py's structure (white-box call-function unit tests,
then black-box route/auth/quota integration tests) rather than re-testing the parts
that are unchanged copies of already-covered logic (e.g. the refund ledger and the
cancellation registry are fully generic and already covered by
tests/test_stop_generating_refund.py / tests/test_stop_generating_history_cancel.py).
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _FakeOpenAIError(Exception):
    """Stand-in for whatever the openai SDK raises on failure. Mirrors
    _FakeGeminiError in test_gemini_integration.py -- call_chatgpt_model()/
    call_chatgpt_image_model() classify errors via getattr() duck typing
    (status_code/code/message), not isinstance checks against SDK exception
    classes, so a plain Exception subclass carrying just these attributes is a
    faithful stand-in."""

    def __init__(self, status_code=None, code=None, message=None):
        super().__init__(message or '')
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        if message is not None:
            self.message = message


def _fake_openai_chat_client(text='hello there', side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.chat.completions.create.side_effect = side_effect
    else:
        message = MagicMock()
        message.content = text
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response
    return client


def _fake_openai_image_client(b64_json='ZmFrZWltYWdlYnl0ZXM=', side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.images.generate.side_effect = side_effect
    else:
        image_data = MagicMock()
        image_data.b64_json = b64_json
        response = MagicMock()
        response.data = [image_data]
        client.images.generate.return_value = response
    return client


def _fake_gemini_text_client(text='hello there', side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.interactions.create.side_effect = side_effect
    else:
        interaction = MagicMock()
        interaction.output_text = text
        client.interactions.create.return_value = interaction
    return client


# ==========================================================================
# 白盒/单元测试：错误分类共享 helper
# ==========================================================================

class TestClassifyOpenAiError(unittest.TestCase):

    def test_insufficient_quota_code_is_quota_exhausted(self):
        e = _FakeOpenAIError(code='insufficient_quota', message='You exceeded your quota.')
        classification, message = main._classify_openai_error(e)
        self.assertEqual(classification, 'QUOTA_EXHAUSTED')

    def test_quota_message_text_without_code_is_still_quota_exhausted(self):
        e = _FakeOpenAIError(status_code=429, message='You exceeded your current quota, please check your plan.')
        classification, _ = main._classify_openai_error(e)
        self.assertEqual(classification, 'QUOTA_EXHAUSTED')

    def test_401_is_permission_denied(self):
        e = _FakeOpenAIError(status_code=401, message='Incorrect API key provided.')
        classification, _ = main._classify_openai_error(e)
        self.assertEqual(classification, 'PERMISSION_DENIED')

    def test_other_status_code_falls_back_to_formatted_message(self):
        e = _FakeOpenAIError(status_code=500, message='Server error.')
        classification, message = main._classify_openai_error(e)
        self.assertIsNone(classification)
        self.assertIn('500', message)

    def test_no_status_attributes_falls_back_to_str(self):
        classification, message = main._classify_openai_error(ValueError('boom'))
        self.assertIsNone(classification)
        self.assertEqual(message, 'boom')


class TestClassifyGoogleGenaiErrorExtraction(unittest.TestCase):
    """Regression guard: this helper was extracted out of the pre-existing
    call_gemini_image_model() (2026-07-06) so call_gemini_text_model() could reuse it
    without duplicating the classification logic -- confirms the extraction preserved
    exact behavior for all three branches."""

    def test_429_status_code_is_quota_exhausted(self):
        e = main.__dict__  # no-op placeholder to keep imports flat below
        from tests.test_gemini_integration import _FakeGeminiError
        classification, _ = main._classify_google_genai_error(
            _FakeGeminiError(status_code=429, message='Quota exceeded')
        )
        self.assertEqual(classification, 'QUOTA_EXHAUSTED')

    def test_403_is_permission_denied(self):
        from tests.test_gemini_integration import _FakeGeminiError
        classification, _ = main._classify_google_genai_error(
            _FakeGeminiError(status_code=403, message='no permission')
        )
        self.assertEqual(classification, 'PERMISSION_DENIED')


# ==========================================================================
# 白盒/单元测试：call_chatgpt_model()
# ==========================================================================

class TestCallChatGptModel(unittest.TestCase):

    def test_uses_default_client_when_no_user_key(self):
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_chat_client()
            main.call_chatgpt_model('hi', 'gpt-5.5', user_api_key=None)
            mock_openai.OpenAI.assert_called_once_with()

    def test_uses_user_key_when_provided(self):
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_chat_client()
            main.call_chatgpt_model('hi', 'gpt-5.5', user_api_key='sk-user-supplied')
            mock_openai.OpenAI.assert_called_once_with(api_key='sk-user-supplied')

    def test_success_result_shape(self):
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_chat_client('a cheerful reply')
            result = main.call_chatgpt_model('hi', 'gpt-5.5')

        self.assertEqual(result['provider'], 'ChatGPT')
        self.assertTrue(result['success'])
        self.assertEqual(result['response'], 'a cheerful reply')
        self.assertEqual(result['error'], '')
        self.assertEqual(result['model'], 'gpt-5.5')
        self.assertEqual(result['type'], 'openai')
        self.assertIsInstance(result['response_time'], float)

    def test_model_key_maps_to_official_model_id(self):
        with patch.object(main, 'openai') as mock_openai:
            client = _fake_openai_chat_client()
            mock_openai.OpenAI.return_value = client
            main.call_chatgpt_model('hi', 'gpt-5.4-mini')
            _, kwargs = client.chat.completions.create.call_args
            self.assertEqual(kwargs['model'], 'gpt-5.4-mini')

    def test_uses_max_completion_tokens_not_max_tokens(self):
        """Regression guard: gpt-5.5/gpt-5.4-mini reject the legacy 'max_tokens' param
        with a 400 (must use 'max_completion_tokens' instead) -- every real call was
        failing until this was fixed."""
        with patch.object(main, 'openai') as mock_openai:
            client = _fake_openai_chat_client()
            mock_openai.OpenAI.return_value = client
            main.call_chatgpt_model('hi', 'gpt-5.5')
            _, kwargs = client.chat.completions.create.call_args
            self.assertNotIn('max_tokens', kwargs)
            self.assertEqual(kwargs['max_completion_tokens'], main.CHATGPT_MAX_TOKENS)

    def test_unknown_model_key_returns_error_without_calling_api(self):
        with patch.object(main, 'openai') as mock_openai:
            result = main.call_chatgpt_model('hi', 'not-a-real-model')
        self.assertFalse(result['success'])
        self.assertIn('Unknown ChatGPT model', result['error'])
        mock_openai.OpenAI.assert_not_called()

    def test_quota_exhausted_maps_to_server_chatgpt_quota_exhausted(self):
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_chat_client(
                side_effect=_FakeOpenAIError(code='insufficient_quota', message='no quota')
            )
            result = main.call_chatgpt_model('hi', 'gpt-5.5')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'SERVER_CHATGPT_QUOTA_EXHAUSTED')
        self.assertEqual(result['error_code'], 'SERVER_CHATGPT_QUOTA_EXHAUSTED')

    def test_permission_denied_gives_friendly_invalid_key_message(self):
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_chat_client(
                side_effect=_FakeOpenAIError(status_code=401, message='bad key')
            )
            result = main.call_chatgpt_model('hi', 'gpt-5.5')
        self.assertFalse(result['success'])
        self.assertNotIn('error_code', result)
        self.assertIn('Invalid or missing ChatGPT API key', result['error'])


# ==========================================================================
# 白盒/单元测试：call_gemini_text_model()
# ==========================================================================

class TestCallGeminiTextModel(unittest.TestCase):

    def test_uses_default_client_when_no_user_key(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_genai.Client.return_value = _fake_gemini_text_client()
            main.call_gemini_text_model('hi', 'gemini-3.5-flash', user_api_key=None)
            mock_genai.Client.assert_called_once_with()

    def test_success_result_shape(self):
        with patch.object(main, 'google_genai') as mock_genai:
            mock_genai.Client.return_value = _fake_gemini_text_client('a helpful reply')
            result = main.call_gemini_text_model('hi', 'gemini-3.5-flash')

        self.assertEqual(result['provider'], 'Gemini')
        self.assertTrue(result['success'])
        self.assertEqual(result['response'], 'a helpful reply')
        self.assertEqual(result['type'], 'google_genai_text')
        self.assertNotIn('peer_reviews', result)

    def test_model_key_maps_to_official_model_id(self):
        with patch.object(main, 'google_genai') as mock_genai:
            client = _fake_gemini_text_client()
            mock_genai.Client.return_value = client
            # apply_persona=False：这条测试关心的是 model_key -> 官方 model ID 的映射，
            # 不是 2026-07-07 新增的 FRONTIER_STYLE_PROMPTS_MAP 人设后缀（见
            # tests/test_peer_review_cross_frontier.py 覆盖人设本身）。
            main.call_gemini_text_model('hi', 'gemini-3.1-flash-lite', apply_persona=False)
            _, kwargs = client.interactions.create.call_args
            self.assertEqual(kwargs['model'], 'gemini-3.1-flash-lite')
            self.assertEqual(kwargs['input'], 'hi')

    def test_unknown_model_key_returns_error_without_calling_api(self):
        with patch.object(main, 'google_genai') as mock_genai:
            result = main.call_gemini_text_model('hi', 'not-a-real-model')
        self.assertFalse(result['success'])
        mock_genai.Client.assert_not_called()

    def test_no_text_in_response_is_a_failure(self):
        with patch.object(main, 'google_genai') as mock_genai:
            interaction = MagicMock()
            interaction.output_text = None
            client = MagicMock()
            client.interactions.create.return_value = interaction
            mock_genai.Client.return_value = client
            result = main.call_gemini_text_model('hi', 'gemini-3.5-flash')
        self.assertFalse(result['success'])
        self.assertIn('did not return any text', result['error'])

    def test_quota_exhausted_maps_to_server_gemini_text_quota_exhausted(self):
        from tests.test_gemini_integration import _FakeGeminiError
        with patch.object(main, 'google_genai') as mock_genai:
            client = MagicMock()
            client.interactions.create.side_effect = _FakeGeminiError(status_code=429, message='quota')
            mock_genai.Client.return_value = client
            result = main.call_gemini_text_model('hi', 'gemini-3.5-flash')
        self.assertEqual(result['error_code'], 'SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED')

    def test_gemini_text_and_image_quota_error_codes_are_distinct(self):
        """Regression guard: text and image are two independent frontier providers with
        independent free-tier counters (gemini_text_free_tier_usage vs
        gemini_free_tier_usage) -- their exhausted-quota error codes must not collapse
        onto the same string, or the frontend/history-append friendly-message branches
        in main.py would misfire for the wrong scenario."""
        from tests.test_gemini_integration import _FakeGeminiError
        with patch.object(main, 'google_genai') as mock_genai:
            client = MagicMock()
            client.interactions.create.side_effect = _FakeGeminiError(status_code=429, message='quota')
            mock_genai.Client.return_value = client
            text_result = main.call_gemini_text_model('hi', 'gemini-3.5-flash')
            image_result = main.call_gemini_image_model('hi', 'nano-banana-pro')
        self.assertNotEqual(text_result['error_code'], image_result['error_code'])


# ==========================================================================
# 白盒/单元测试：call_chatgpt_image_model()
# ==========================================================================

class TestCallChatGptImageModel(unittest.TestCase):

    def test_success_result_shape(self):
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_image_client('c29tZWJhc2U2NA==')
            result = main.call_chatgpt_image_model('a cat', 'gpt-image-2')

        self.assertEqual(result['provider'], 'ChatGPT')
        self.assertTrue(result['success'])
        self.assertEqual(result['b64_json'], 'c29tZWJhc2U2NA==')
        self.assertIsNone(result['url'])
        self.assertEqual(result['type'], 'openai_image')

    def test_model_key_maps_to_official_model_id(self):
        with patch.object(main, 'openai') as mock_openai:
            client = _fake_openai_image_client()
            mock_openai.OpenAI.return_value = client
            main.call_chatgpt_image_model('a cat', 'gpt-image-1.5')
            _, kwargs = client.images.generate.call_args
            self.assertEqual(kwargs['model'], 'gpt-image-1.5')

    def test_no_image_in_response_is_a_failure(self):
        with patch.object(main, 'openai') as mock_openai:
            response = MagicMock()
            response.data = []
            client = MagicMock()
            client.images.generate.return_value = response
            mock_openai.OpenAI.return_value = client
            result = main.call_chatgpt_image_model('a cat', 'gpt-image-2')
        self.assertFalse(result['success'])

    def test_quota_exhausted_maps_to_server_chatgpt_image_quota_exhausted(self):
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_image_client(
                side_effect=_FakeOpenAIError(code='insufficient_quota', message='no quota')
            )
            result = main.call_chatgpt_image_model('a cat', 'gpt-image-2')
        self.assertEqual(result['error_code'], 'SERVER_CHATGPT_IMAGE_QUOTA_EXHAUSTED')

    def test_chatgpt_text_and_image_quota_error_codes_are_distinct(self):
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_chat_client(
                side_effect=_FakeOpenAIError(code='insufficient_quota', message='no quota')
            )
            text_result = main.call_chatgpt_model('hi', 'gpt-5.5')
        with patch.object(main, 'openai') as mock_openai:
            mock_openai.OpenAI.return_value = _fake_openai_image_client(
                side_effect=_FakeOpenAIError(code='insufficient_quota', message='no quota')
            )
            image_result = main.call_chatgpt_image_model('a cat', 'gpt-image-2')
        self.assertNotEqual(text_result['error_code'], image_result['error_code'])


# ==========================================================================
# 白盒/单元测试：auth/db.py 通用额度计数器
# ==========================================================================

class TestGenericFreeTierCounterDb(unittest.TestCase):

    def test_get_usage_defaults_to_zero_when_field_missing(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'username': 'alice'}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch.object(auth_db, 'db', mock_db):
            usage = auth_db.get_free_tier_usage('uid1', 'chatgpt_free_tier_usage')
        self.assertEqual(usage, 0)

    def test_get_usage_returns_existing_value_for_requested_field_only(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            'chatgpt_free_tier_usage': 3,
            'gemini_text_free_tier_usage': 7,
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch.object(auth_db, 'db', mock_db):
            self.assertEqual(auth_db.get_free_tier_usage('uid1', 'chatgpt_free_tier_usage'), 3)
            self.assertEqual(auth_db.get_free_tier_usage('uid1', 'gemini_text_free_tier_usage'), 7)
            self.assertEqual(auth_db.get_free_tier_usage('uid1', 'chatgpt_image_free_tier_usage'), 0)

    def test_get_usage_returns_zero_when_firebase_unavailable(self):
        from auth import db as auth_db
        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            usage = auth_db.get_free_tier_usage('uid1', 'chatgpt_free_tier_usage')
        self.assertEqual(usage, 0)

    def test_increment_writes_the_requested_field_name(self):
        from auth import db as auth_db
        from firebase_admin import firestore
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.increment_free_tier_usage('uid1', 'chatgpt_image_free_tier_usage')

        self.assertTrue(result)
        call_kwargs = mock_doc_ref.update.call_args[0][0]
        self.assertIn('chatgpt_image_free_tier_usage', call_kwargs)
        self.assertIsInstance(call_kwargs['chatgpt_image_free_tier_usage'], type(firestore.Increment(1)))

    def test_decrement_does_not_go_below_zero(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'chatgpt_free_tier_usage': 0}
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.decrement_free_tier_usage('uid1', 'chatgpt_free_tier_usage')

        self.assertFalse(result)
        mock_doc_ref.update.assert_not_called()


# ==========================================================================
# 黑盒/集成测试：三条新路由的认证守卫 + 额度流程 + 校验
# ==========================================================================

class TestNewFrontierRoutesAuthGuard(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _assert_guest_and_anon_get_401(self, path, body):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            resp = client.post(path, json=body)
        self.assertEqual(resp.status_code, 401)

        with main.app.test_client() as client:
            resp = client.post(path, json=body)
        self.assertEqual(resp.status_code, 401)

    def test_chatgpt_chat_blocks_guest_and_anonymous(self):
        self._assert_guest_and_anon_get_401('/api/chatgpt-chat', {'prompt': 'hi', 'model': 'gpt-5.5'})

    def test_gemini_chat_blocks_guest_and_anonymous(self):
        self._assert_guest_and_anon_get_401('/api/gemini-chat', {'prompt': 'hi', 'model': 'gemini-3.5-flash'})

    def test_chatgpt_image_blocks_guest_and_anonymous(self):
        self._assert_guest_and_anon_get_401('/api/chatgpt-image', {'prompt': 'a cat', 'model': 'gpt-image-2'})


class TestNewFrontierRoutesValidationAndAvailability(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 'uid1'

    def test_chatgpt_chat_missing_prompt_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/chatgpt-chat', json={'model': 'gpt-5.5'})
        self.assertEqual(resp.status_code, 400)

    def test_chatgpt_chat_invalid_model_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/chatgpt-chat', json={'prompt': 'hi', 'model': 'not-real'})
        self.assertEqual(resp.status_code, 400)

    def test_chatgpt_chat_unavailable_returns_503(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'CHATGPT_AVAILABLE', False):
                resp = client.post('/api/chatgpt-chat', json={'prompt': 'hi', 'model': 'gpt-5.5'})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()['error'], 'CHATGPT_UNAVAILABLE')

    def test_gemini_chat_invalid_model_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/gemini-chat', json={'prompt': 'hi', 'model': 'not-real'})
        self.assertEqual(resp.status_code, 400)

    def test_gemini_chat_unavailable_returns_503(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'GEMINI_AVAILABLE', False):
                resp = client.post('/api/gemini-chat', json={'prompt': 'hi', 'model': 'gemini-3.5-flash'})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()['error'], 'GEMINI_UNAVAILABLE')

    def test_chatgpt_image_invalid_model_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/chatgpt-image', json={'prompt': 'a cat', 'model': 'not-real'})
        self.assertEqual(resp.status_code, 400)

    def test_chatgpt_image_unavailable_returns_503(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'CHATGPT_AVAILABLE', False):
                resp = client.post('/api/chatgpt-image', json={'prompt': 'a cat', 'model': 'gpt-image-2'})
        self.assertEqual(resp.status_code, 503)


class TestNewFrontierRoutesFreeTierFlow(unittest.TestCase):
    """一次点击 = 一次额度、own-key 绕过检查/递增、失败不消耗额度 -- 与
    test_claude_integration.py::TestClaudeChatRouteKeyRoutingAndCounter /
    test_gemini_integration.py::TestGeminiImageRouteKeyRoutingAndCounter 逐一同构。"""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_chatgpt_chat_success_without_own_key_increments_counter(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_chatgpt_model', return_value={
                     'provider': 'ChatGPT', 'success': True, 'response': 'hi', 'error': '',
                     'response_time': 0.5, 'model': 'gpt-5.5', 'type': 'openai',
                 }):
                resp = client.post('/api/chatgpt-chat', json={'prompt': 'hi', 'model': 'gpt-5.5'})

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_called_once_with('uid1', main.CHATGPT_FREE_TIER_FIELD)

    def test_chatgpt_chat_at_limit_without_own_key_returns_free_tier_exhausted(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage', return_value=main.CHATGPT_FREE_TIER_LIMIT), \
                 patch.object(main, 'call_chatgpt_model') as mock_call:
                resp = client.post('/api/chatgpt-chat', json={'prompt': 'hi', 'model': 'gpt-5.5'})

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()['error'], 'FREE_TIER_EXHAUSTED')
        mock_call.assert_not_called()

    def test_chatgpt_chat_own_key_bypasses_counter_check_and_increment(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage') as mock_get, \
                 patch.object(main, 'increment_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_chatgpt_model', return_value={
                     'provider': 'ChatGPT', 'success': True, 'response': 'hi', 'error': '',
                     'response_time': 0.5, 'model': 'gpt-5.5', 'type': 'openai',
                 }) as mock_call:
                resp = client.post(
                    '/api/chatgpt-chat',
                    json={'prompt': 'hi', 'model': 'gpt-5.5'},
                    headers={'X-User-ChatGPT-Key': 'sk-mykey'}
                )

        self.assertEqual(resp.status_code, 200)
        mock_get.assert_not_called()
        mock_incr.assert_not_called()
        mock_call.assert_called_once_with('hi', 'gpt-5.5', 'sk-mykey')

    def test_chatgpt_chat_failed_call_does_not_increment_counter(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_chatgpt_model', return_value={
                     'provider': 'ChatGPT', 'success': False, 'response': '', 'error': 'boom',
                     'response_time': 0.5, 'model': 'gpt-5.5', 'type': 'openai',
                 }):
                resp = client.post('/api/chatgpt-chat', json={'prompt': 'hi', 'model': 'gpt-5.5'})

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_not_called()

    def test_gemini_chat_success_without_own_key_increments_its_own_field(self):
        """Regression guard: gemini_text's counter field must be distinct from both
        claude's and gemini-image's -- otherwise a Gemini text call would silently
        drain the image quota or vice versa."""
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_gemini_text_model', return_value={
                     'provider': 'Gemini', 'success': True, 'response': 'hi', 'error': '',
                     'response_time': 0.5, 'model': 'gemini-3.5-flash', 'type': 'google_genai_text',
                 }):
                resp = client.post('/api/gemini-chat', json={'prompt': 'hi', 'model': 'gemini-3.5-flash'})

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_called_once_with('uid1', main.GEMINI_TEXT_FREE_TIER_FIELD)
        self.assertNotEqual(main.GEMINI_TEXT_FREE_TIER_FIELD, 'gemini_free_tier_usage')

    def test_chatgpt_image_success_increments_its_own_field_distinct_from_text(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_chatgpt_image_model', return_value={
                     'provider': 'ChatGPT', 'success': True, 'url': None, 'b64_json': 'abc',
                     'error': '', 'response_time': 0.5, 'model': 'gpt-image-2', 'type': 'openai_image',
                 }):
                resp = client.post('/api/chatgpt-image', json={'prompt': 'a cat', 'model': 'gpt-image-2'})

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_called_once_with('uid1', main.CHATGPT_IMAGE_FREE_TIER_FIELD)
        self.assertNotEqual(main.CHATGPT_IMAGE_FREE_TIER_FIELD, main.CHATGPT_FREE_TIER_FIELD)

    def test_chatgpt_chat_does_not_create_a_new_chat_history_entry(self):
        """Danger-zone regression guard (CLAUDE.md): the new routes must never call
        save_chat_history()/save_image_history() -- they can only append into an
        already-existing entry via history_id, never create one themselves."""
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_free_tier_usage'), \
                 patch.object(main, 'save_chat_history') as mock_save, \
                 patch.object(main, 'call_chatgpt_model', return_value={
                     'provider': 'ChatGPT', 'success': True, 'response': 'hi', 'error': '',
                     'response_time': 0.5, 'model': 'gpt-5.5', 'type': 'openai',
                 }):
                client.post('/api/chatgpt-chat', json={'prompt': 'hi', 'model': 'gpt-5.5'})
        mock_save.assert_not_called()

    def test_chatgpt_image_does_not_create_a_new_image_history_entry(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_free_tier_usage'), \
                 patch.object(main, 'save_image_history') as mock_save, \
                 patch.object(main, 'call_chatgpt_image_model', return_value={
                     'provider': 'ChatGPT', 'success': True, 'url': None, 'b64_json': 'abc',
                     'error': '', 'response_time': 0.5, 'model': 'gpt-image-2', 'type': 'openai_image',
                 }):
                client.post('/api/chatgpt-image', json={'prompt': 'a cat', 'model': 'gpt-image-2'})
        mock_save.assert_not_called()


class TestQuotaStatusIncludesNewFields(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_quota_status_includes_all_five_providers(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            resp = client.get('/api/quota-status')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for key in ('claude', 'gemini', 'chatgpt', 'gemini_text', 'chatgpt_image'):
            self.assertIn(key, data)
            self.assertIn('used', data[key])
            self.assertIn('limit', data[key])

    def test_quota_status_still_blocks_guest_and_anonymous(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            resp = client.get('/api/quota-status')
        self.assertEqual(resp.status_code, 401)


class TestHealthCheckReportsNewProviders(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_health_includes_chatgpt_and_gemini_text_info(self):
        with main.app.test_client() as client:
            resp = client.get('/health')
        data = resp.get_json()
        self.assertIn('chatgpt_available', data)
        self.assertIn('chatgpt_models', data)
        self.assertIn('gpt-5.5', data['chatgpt_models'])
        self.assertIn('chatgpt_image_models', data)
        self.assertIn('gpt-image-2', data['chatgpt_image_models'])
        self.assertIn('gemini_text_models', data)
        self.assertIn('gemini-3.5-flash', data['gemini_text_models'])


if __name__ == '__main__':
    unittest.main()
