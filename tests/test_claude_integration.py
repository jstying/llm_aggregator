"""Tests for the official Anthropic (Claude) integration (2026-07-04 新增).

Covers the third, fully independent call chain added alongside the two existing g4f
chains (text ChatCompletion / image generate()): `main.call_claude_model()` +
`POST /api/claude-chat`, plus the two new `auth/db.py` helpers
(`get_claude_free_tier_usage`/`increment_claude_free_tier_usage`) backing the
per-account free-tier gate.

Split mirrors the task spec's own split:
- White-box/unit: counter logic (auth/db.py), Key-routing logic
  (call_claude_model()/claude_chat() choosing developer vs. user-supplied key and
  whether that gates the counter).
- Black-box/integration: HTTP-driven scenarios through the Flask test client (guest
  rejection, first free-tier success, second free-tier rejection, developer-account
  billing exhaustion forwarded as SERVER_CREDITS_EXHAUSTED).
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import anthropic  # noqa: E402


def _make_credits_exhausted_error(status_code=400, error_type='invalid_request_error',
                                   message='Your credit balance is too low to access the Anthropic API. '
                                            'Please go to Plans & Billing to upgrade or purchase credits.'):
    """构造一个真实的 anthropic.APIStatusError 实例（而不是字符串近似），确保测试
    验证的是 SDK 实际暴露的 .status_code/.type/.message 属性，与 call_claude_model()
    里的判断逻辑完全对应。默认参数是**实测**的真实形状（用一个真实余额为 0 的
    Anthropic 账户直接调用 client.messages.create() 观察到）：400 + error.type ==
    'invalid_request_error' + message 含 "credit balance is too low"——而不是任务
    描述最初设想的 429 + 'insufficient_funds'，也不是通用文档字面暗示的
    403 + 'billing_error'（call_claude_model() 仍兼容性地检查这个 type 值，因此
    调用方可以传 error_type='billing_error' 来测那条分支，见下面的用例）。"""
    req = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
    body = {'error': {'type': error_type, 'message': message}}
    resp = httpx.Response(status_code, request=req, json=body)
    return anthropic.APIStatusError(message, response=resp, body=body)


def _fake_claude_response(text='Hello from Claude'):
    block = MagicMock()
    block.type = 'text'
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


# ==========================================================================
# 白盒/单元测试
# ==========================================================================

class TestCallClaudeModelKeyRouting(unittest.TestCase):
    """call_claude_model() 的 Key 路由与错误分类逻辑（不经过 Flask 路由）。"""

    def test_uses_default_client_when_no_user_key(self):
        with patch.object(main, 'anthropic') as mock_anthropic:
            mock_anthropic.APIStatusError = anthropic.APIStatusError
            mock_anthropic.APIConnectionError = anthropic.APIConnectionError
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _fake_claude_response()
            mock_anthropic.Anthropic.return_value = mock_client

            main.call_claude_model('hi', 'claude-sonnet-5', user_api_key=None)

            mock_anthropic.Anthropic.assert_called_once_with()

    def test_uses_user_key_when_provided(self):
        with patch.object(main, 'anthropic') as mock_anthropic:
            mock_anthropic.APIStatusError = anthropic.APIStatusError
            mock_anthropic.APIConnectionError = anthropic.APIConnectionError
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _fake_claude_response()
            mock_anthropic.Anthropic.return_value = mock_client

            main.call_claude_model('hi', 'claude-sonnet-5', user_api_key='sk-ant-user-supplied')

            mock_anthropic.Anthropic.assert_called_once_with(api_key='sk-ant-user-supplied')

    def test_success_result_shape(self):
        with patch.object(main, 'anthropic') as mock_anthropic:
            mock_anthropic.APIStatusError = anthropic.APIStatusError
            mock_anthropic.APIConnectionError = anthropic.APIConnectionError
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _fake_claude_response('42')
            mock_anthropic.Anthropic.return_value = mock_client

            result = main.call_claude_model('what is 6*7', 'claude-sonnet-5')

            self.assertEqual(result['provider'], 'Claude')
            self.assertTrue(result['success'])
            self.assertEqual(result['response'], '42')
            self.assertEqual(result['error'], '')
            self.assertEqual(result['model'], 'claude-sonnet-5')
            self.assertEqual(result['type'], 'anthropic')
            self.assertIsInstance(result['response_time'], float)

    def test_request_uses_mapped_model_id_not_the_ui_key(self):
        with patch.object(main, 'anthropic') as mock_anthropic:
            mock_anthropic.APIStatusError = anthropic.APIStatusError
            mock_anthropic.APIConnectionError = anthropic.APIConnectionError
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _fake_claude_response()
            mock_anthropic.Anthropic.return_value = mock_client

            main.call_claude_model('hi', 'claude-haiku-4-5')

            _, kwargs = mock_client.messages.create.call_args
            self.assertEqual(kwargs['model'], 'claude-haiku-4-5-20251001')

    def test_unknown_model_key_returns_error_without_calling_api(self):
        with patch.object(main, 'anthropic') as mock_anthropic:
            result = main.call_claude_model('hi', 'not-a-real-model')

        self.assertFalse(result['success'])
        self.assertIn('Unknown Claude model', result['error'])
        mock_anthropic.Anthropic.assert_not_called()

    def test_real_world_credit_balance_error_maps_to_server_credits_exhausted(self):
        """实测形状（用一个真实余额为 0 的账户直接调用官方 API 观察到）：
        400 + error.type == 'invalid_request_error' + message 含 "credit balance
        is too low"。这不是任务描述最初设想的 429 + 'insufficient_funds'（Anthropic
        的错误体系里没有这个组合），也不是通用文档字面暗示的 403 + 'billing_error'
        （见下一个用例的兼容性检查）——真正稳定的判断依据是 message 里的
        "credit balance" 关键词。"""
        with patch.object(main, 'anthropic') as mock_anthropic:
            mock_anthropic.APIStatusError = anthropic.APIStatusError
            mock_anthropic.APIConnectionError = anthropic.APIConnectionError
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = _make_credits_exhausted_error()
            mock_anthropic.Anthropic.return_value = mock_client

            result = main.call_claude_model('hi', 'claude-sonnet-5')

        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'SERVER_CREDITS_EXHAUSTED')
        self.assertEqual(result['error_code'], 'SERVER_CREDITS_EXHAUSTED')

    def test_billing_error_type_also_recognized_defensively(self):
        """兼容性检查：即使某个账户/未来 API 版本真的返回文档字面暗示的
        403 + error.type == 'billing_error' 形状，也应该识别为余额耗尽——判断
        逻辑对 message 关键词和 error.type 两条线索都做了检查，不依赖某一条。"""
        with patch.object(main, 'anthropic') as mock_anthropic:
            mock_anthropic.APIStatusError = anthropic.APIStatusError
            mock_anthropic.APIConnectionError = anthropic.APIConnectionError
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = _make_credits_exhausted_error(
                status_code=403, error_type='billing_error', message='Billing issue'
            )
            mock_anthropic.Anthropic.return_value = mock_client

            result = main.call_claude_model('hi', 'claude-sonnet-5')

        self.assertEqual(result['error_code'], 'SERVER_CREDITS_EXHAUSTED')

    def test_plain_rate_limit_error_is_not_credits_exhausted(self):
        """真正的 429 rate_limit_error（无 billing_error 类型、消息里也没有
        "credit balance"）是瞬时限流，不应该被误判为开发者余额耗尽。"""
        with patch.object(main, 'anthropic') as mock_anthropic:
            mock_anthropic.APIStatusError = anthropic.APIStatusError
            mock_anthropic.APIConnectionError = anthropic.APIConnectionError
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = _make_credits_exhausted_error(
                status_code=429, error_type='rate_limit_error', message='Too many requests'
            )
            mock_anthropic.Anthropic.return_value = mock_client

            result = main.call_claude_model('hi', 'claude-sonnet-5')

        self.assertFalse(result['success'])
        self.assertNotIn('error_code', result)
        self.assertNotEqual(result['error'], 'SERVER_CREDITS_EXHAUSTED')

    def test_connection_error_gives_friendly_message(self):
        with patch.object(main, 'anthropic') as mock_anthropic:
            mock_anthropic.APIStatusError = anthropic.APIStatusError
            mock_anthropic.APIConnectionError = anthropic.APIConnectionError
            mock_client = MagicMock()
            req = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
            mock_client.messages.create.side_effect = anthropic.APIConnectionError(request=req)
            mock_anthropic.Anthropic.return_value = mock_client

            result = main.call_claude_model('hi', 'claude-sonnet-5')

        self.assertFalse(result['success'])
        self.assertIn('busy', result['error'])


class TestClaudeFreeTierCounterDb(unittest.TestCase):
    """auth/db.py 的两个新增计数器函数（纯 db 层，mock Firestore，与
    tests/test_auth_whitebox.py 的既有 mocking 方式一致）。"""

    def test_get_usage_defaults_to_zero_when_field_missing(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'username': 'alice'}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch.object(auth_db, 'db', mock_db):
            usage = auth_db.get_claude_free_tier_usage('uid1')

        self.assertEqual(usage, 0)

    def test_get_usage_returns_existing_value(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'claude_free_tier_usage': 3}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch.object(auth_db, 'db', mock_db):
            usage = auth_db.get_claude_free_tier_usage('uid1')

        self.assertEqual(usage, 3)

    def test_get_usage_returns_zero_for_nonexistent_user(self):
        from auth import db as auth_db
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch.object(auth_db, 'db', mock_db):
            usage = auth_db.get_claude_free_tier_usage('ghost-uid')

        self.assertEqual(usage, 0)

    def test_get_usage_returns_zero_when_firebase_unavailable(self):
        from auth import db as auth_db
        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            usage = auth_db.get_claude_free_tier_usage('uid1')
        self.assertEqual(usage, 0)

    def test_increment_calls_firestore_increment_sentinel(self):
        from auth import db as auth_db
        from firebase_admin import firestore
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.increment_claude_free_tier_usage('uid1')

        self.assertTrue(result)
        mock_doc_ref.update.assert_called_once()
        call_kwargs = mock_doc_ref.update.call_args[0][0]
        self.assertIn('claude_free_tier_usage', call_kwargs)
        # firestore.Increment(1) 实例之间不支持 == 比较出 True，改为断言类型/字段名。
        self.assertIsInstance(call_kwargs['claude_free_tier_usage'], type(firestore.Increment(1)))

    def test_increment_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db
        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.increment_claude_free_tier_usage('uid1')
        self.assertIsNone(result)


class TestClaudeChatRouteKeyRoutingAndCounter(unittest.TestCase):
    """/api/claude-chat 路由本身的计数器 + Key 路由决策逻辑（mock 掉
    call_claude_model()/两个计数器函数，只验证路由层面"谁调用了谁、传了什么参数"）。"""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_successful_call_without_own_key_increments_counter(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_claude_model', return_value={
                     'provider': 'Claude', 'success': True, 'response': 'hi',
                     'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5',
                     'type': 'anthropic',
                 }):
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5'
                })

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_called_once_with('uid1')

    def test_failed_call_does_not_increment_counter(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_claude_model', return_value={
                     'provider': 'Claude', 'success': False, 'response': '',
                     'error': 'boom', 'response_time': 1.0, 'model': 'claude-sonnet-5',
                     'type': 'anthropic',
                 }):
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5'
                })

        self.assertEqual(resp.status_code, 200)
        mock_incr.assert_not_called()

    def test_own_key_header_bypasses_counter_check_and_increment(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage') as mock_get_usage, \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr, \
                 patch.object(main, 'call_claude_model', return_value={
                     'provider': 'Claude', 'success': True, 'response': 'hi',
                     'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5',
                     'type': 'anthropic',
                 }) as mock_call:
                resp = client.post(
                    '/api/claude-chat',
                    json={'prompt': 'hello', 'model': 'claude-sonnet-5'},
                    headers={'X-User-Claude-Key': 'sk-ant-mykey'}
                )

        self.assertEqual(resp.status_code, 200)
        mock_get_usage.assert_not_called()
        mock_incr.assert_not_called()
        mock_call.assert_called_once_with('hello', 'claude-sonnet-5', 'sk-ant-mykey')

    def test_no_own_key_forwards_none_to_call_claude_model(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value={
                     'provider': 'Claude', 'success': True, 'response': 'hi',
                     'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5',
                     'type': 'anthropic',
                 }) as mock_call:
                client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5'
                })

        mock_call.assert_called_once_with('hello', 'claude-sonnet-5', None)


# ==========================================================================
# 黑盒/集成测试
# ==========================================================================

class TestClaudeChatAuthGuard(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_guest_request_returns_401(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            resp = client.post('/api/claude-chat', json={
                'prompt': 'hello', 'model': 'claude-sonnet-5'
            })
        self.assertEqual(resp.status_code, 401)

    def test_anonymous_request_returns_401(self):
        with main.app.test_client() as client:
            resp = client.post('/api/claude-chat', json={
                'prompt': 'hello', 'model': 'claude-sonnet-5'
            })
        self.assertEqual(resp.status_code, 401)


class TestClaudeChatFreeTierFlow(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_first_request_succeeds(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage'), \
                 patch.object(main, 'call_claude_model', return_value={
                     'provider': 'Claude', 'success': True, 'response': '4',
                     'error': '', 'response_time': 0.5, 'model': 'claude-sonnet-5',
                     'type': 'anthropic',
                 }):
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'what is 2+2', 'model': 'claude-sonnet-5'
                })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['response'], '4')

    def test_request_at_limit_without_own_key_returns_free_tier_exhausted(self):
        """usage already == CLAUDE_FREE_TIER_LIMIT (10, 2026-07-05 raised from 1) --
        the (limit+1)th request without an own key must be rejected before ever calling
        call_claude_model(). Reads the limit off main.CLAUDE_FREE_TIER_LIMIT rather than
        hardcoding a number so this test doesn't silently stop testing "at the limit" if
        the constant changes again."""
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=main.CLAUDE_FREE_TIER_LIMIT), \
                 patch.object(main, 'call_claude_model') as mock_call:
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'another question', 'model': 'claude-sonnet-5'
                })

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()['error'], 'FREE_TIER_EXHAUSTED')
        mock_call.assert_not_called()

    def test_own_key_bypasses_exhausted_free_tier(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=99), \
                 patch.object(main, 'call_claude_model', return_value={
                     'provider': 'Claude', 'success': True, 'response': 'still works',
                     'error': '', 'response_time': 0.5, 'model': 'claude-sonnet-5',
                     'type': 'anthropic',
                 }):
                resp = client.post(
                    '/api/claude-chat',
                    json={'prompt': 'another question', 'model': 'claude-sonnet-5'},
                    headers={'X-User-Claude-Key': 'sk-ant-mykey'}
                )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])


class TestClaudeChatServerCreditsExhausted(unittest.TestCase):
    """端到端复现"Anthropic 服务端余额不足"场景（用真实的、余额为 0 的账户直接调用
    官方 API 验证过实际错误形状——见 _make_credits_exhausted_error() 的默认参数）：
    mock 掉真正的 anthropic.Anthropic 客户端使其抛出这个真实形状的 APIStatusError，
    验证一路传导到 HTTP 响应体的 SERVER_CREDITS_EXHAUSTED，而不是把原始异常文本
    （400/invalid_request_error 的英文提示）泄漏给前端。"""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id

    def test_billing_error_forwarded_as_server_credits_exhausted(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
                 patch.object(main, 'increment_claude_free_tier_usage') as mock_incr, \
                 patch.object(main, 'anthropic') as mock_anthropic:
                mock_anthropic.APIStatusError = anthropic.APIStatusError
                mock_anthropic.APIConnectionError = anthropic.APIConnectionError
                mock_client = MagicMock()
                mock_client.messages.create.side_effect = _make_credits_exhausted_error()
                mock_anthropic.Anthropic.return_value = mock_client

                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5'
                })

        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertEqual(data['error'], 'SERVER_CREDITS_EXHAUSTED')
        self.assertIn('message', data)
        # 余额不足是开发者账户侧的问题，不应该消耗用户的免费额度计数。
        mock_incr.assert_not_called()


class TestClaudeChatValidation(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 'uid1'

    def test_missing_prompt_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/claude-chat', json={'model': 'claude-sonnet-5'})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_model_returns_400(self):
        with main.app.test_client() as client:
            self._login(client)
            resp = client.post('/api/claude-chat', json={
                'prompt': 'hi', 'model': 'gpt-4'
            })
        self.assertEqual(resp.status_code, 400)

    def test_claude_unavailable_returns_503(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'CLAUDE_AVAILABLE', False):
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hi', 'model': 'claude-sonnet-5'
                })
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()['error'], 'CLAUDE_UNAVAILABLE')


class TestApikeyConfigPage(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_page_renders_for_anonymous_visitor(self):
        with main.app.test_client() as client:
            resp = client.get('/apikey-config')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode()
        self.assertIn('claudeKeyInput', html)
        self.assertIn('user_claude_key', html)

    def test_page_mentions_all_three_providers(self):
        with main.app.test_client() as client:
            resp = client.get('/apikey-config')
        html = resp.data.decode()
        self.assertIn('chatgptKeyInput', html)
        self.assertIn('geminiKeyInput', html)
        self.assertIn('claudeKeyInput', html)


class TestHealthCheckReportsClaudeAvailability(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_health_includes_claude_available_flag(self):
        with main.app.test_client() as client:
            resp = client.get('/health')
        data = resp.get_json()
        self.assertIn('claude_available', data)
        self.assertIn('claude_models', data)


if __name__ == '__main__':
    unittest.main()
