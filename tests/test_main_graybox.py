import json
import sys
import os
import unittest
import concurrent.futures
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


def _make_result(provider, success, response_time, model='gpt-3.5-turbo',
                 response='ok', error=''):
    return {
        'provider': provider,
        'success': success,
        'response': response if success else '',
        'error': '' if success else error,
        'response_time': response_time,
        'model': model,
        'type': 'g4f'
    }


# ============================================================
# 1. 排序权重验证
# 通过 Mock 控制 test_g4f_provider 的返回值，
# 验证 /api/compare 是否严格执行"成功优先，耗时短优先"规则。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestSortOrderInvariant(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()
        self._original_g4f_available = main.G4F_AVAILABLE
        _mock_review = {'reviewer_provider': 'mock', 'reviewer_model': 'mock',
                        'score': 80, 'comment': 'mock review'}
        self._pr_patcher = patch('main.run_peer_review', return_value=_mock_review)
        self._pr_patcher.start()

    def tearDown(self):
        main.G4F_AVAILABLE = self._original_g4f_available
        self._pr_patcher.stop()

    def _post_compare(self, providers=None):
        payload = {
            'prompt': 'sort test',
            'providers': providers or ['Yqcloud', 'OperaAria']
        }
        return self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )

    def test_two_successes_faster_provider_ranks_first(self):
        """OperaAria (1.0s) must appear before Yqcloud (3.0s) when both succeed."""
        def mock_test(provider, prompt, requested_model=None):
            if provider.__name__ == 'Yqcloud':
                return _make_result('Yqcloud', True, 3.0, 'gpt-3.5-turbo')
            return _make_result('OperaAria', True, 1.0, 'aria')

        with patch('main.test_g4f_provider', side_effect=mock_test):
            response = self._post_compare()

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        results = data['results']
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['provider'], 'OperaAria')
        self.assertEqual(results[1]['provider'], 'Yqcloud')
        self.assertLessEqual(results[0]['response_time'], results[1]['response_time'])

    def test_success_ranks_before_failure_regardless_of_response_time(self):
        """Yqcloud (success, 5.0s) must rank before OperaAria (failure, 0.1s)."""
        def mock_test(provider, prompt, requested_model=None):
            if provider.__name__ == 'Yqcloud':
                return _make_result('Yqcloud', True, 5.0, 'gpt-3.5-turbo')
            return _make_result('OperaAria', False, 0.1, 'aria', error='network error')

        with patch('main.test_g4f_provider', side_effect=mock_test):
            response = self._post_compare()

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        results = data['results']
        self.assertEqual(results[0]['provider'], 'Yqcloud')
        self.assertTrue(results[0]['success'])
        self.assertEqual(results[1]['provider'], 'OperaAria')
        self.assertFalse(results[1]['success'])

    def test_two_failures_shorter_response_time_ranks_first(self):
        """Both fail: OperaAria (2.0s) must rank before Yqcloud (4.0s)."""
        def mock_test(provider, prompt, requested_model=None):
            if provider.__name__ == 'Yqcloud':
                return _make_result('Yqcloud', False, 4.0, 'gpt-3.5-turbo', error='err')
            return _make_result('OperaAria', False, 2.0, 'aria', error='err')

        with patch('main.test_g4f_provider', side_effect=mock_test):
            response = self._post_compare()

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        results = data['results']
        self.assertEqual(len(results), 2)
        self.assertFalse(results[0]['success'])
        self.assertFalse(results[1]['success'])
        self.assertLessEqual(results[0]['response_time'], results[1]['response_time'])

    def test_successful_providers_count_matches_sort_output(self):
        """successful_providers in response must equal actual success count."""
        def mock_test(provider, prompt, requested_model=None):
            if provider.__name__ == 'Yqcloud':
                return _make_result('Yqcloud', True, 2.0, 'gpt-3.5-turbo')
            return _make_result('OperaAria', False, 0.5, 'aria', error='fail')

        with patch('main.test_g4f_provider', side_effect=mock_test):
            response = self._post_compare()

        data = json.loads(response.data)
        self.assertEqual(data['successful_providers'], 1)
        self.assertEqual(data['total_providers'], 2)

    def test_sorted_results_are_stable_within_same_success_group(self):
        """Within the success group, response_time ordering must hold strictly."""
        def mock_test(provider, prompt, requested_model=None):
            if provider.__name__ == 'Yqcloud':
                return _make_result('Yqcloud', True, 0.8, 'gpt-3.5-turbo')
            return _make_result('OperaAria', True, 1.5, 'aria')

        with patch('main.test_g4f_provider', side_effect=mock_test):
            response = self._post_compare()

        data = json.loads(response.data)
        results = data['results']
        for i in range(len(results) - 1):
            if results[i]['success'] == results[i + 1]['success']:
                self.assertLessEqual(
                    results[i]['response_time'],
                    results[i + 1]['response_time']
                )


# ============================================================
# 2. 并发与超时降级测试
# 模拟子线程抛出 TimeoutError，验证主线程的 except 块能够
# 通过 init_result_object 正确组装 fallback 数据，接口不崩溃。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestThreadTimeoutFallback(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()
        self._original_g4f_available = main.G4F_AVAILABLE

    def tearDown(self):
        main.G4F_AVAILABLE = self._original_g4f_available

    def _compare_with_timeout(self, providers=None):
        payload = {
            'prompt': 'timeout test',
            'providers': providers or ['Yqcloud']
        }
        with patch('main.test_g4f_provider',
                   side_effect=concurrent.futures.TimeoutError('simulated timeout')):
            return self.client.post(
                '/api/compare',
                data=json.dumps(payload),
                content_type='application/json'
            )

    def test_timeout_exception_does_not_crash_route(self):
        """Route must return 200 even when a thread raises TimeoutError."""
        response = self._compare_with_timeout()
        self.assertEqual(response.status_code, 200)

    def test_timeout_fallback_result_has_required_keys(self):
        """Fallback result from /api/compare must contain all required keys including peer_reviews."""
        response = self._compare_with_timeout()
        data = json.loads(response.data)
        self.assertEqual(len(data['results']), 1)
        result = data['results'][0]
        expected_keys = {
            'provider', 'success', 'response', 'error',
            'response_time', 'model', 'type', 'peer_reviews'
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_timeout_fallback_success_is_false(self):
        """Fallback result for a timed-out thread must have success=False."""
        response = self._compare_with_timeout()
        data = json.loads(response.data)
        self.assertFalse(data['results'][0]['success'])

    def test_timeout_fallback_error_field_is_non_empty_string(self):
        """Fallback result error field must be a non-empty string."""
        response = self._compare_with_timeout()
        data = json.loads(response.data)
        error_val = data['results'][0]['error']
        self.assertIsInstance(error_val, str)
        self.assertGreater(len(error_val), 0)

    def test_timeout_fallback_model_follows_degradation_rules(self):
        """Fallback model must match determine_actual_model('Yqcloud', None)."""
        response = self._compare_with_timeout()
        data = json.loads(response.data)
        result = data['results'][0]
        expected_model = main.determine_actual_model('Yqcloud', None)
        self.assertEqual(result['model'], expected_model)

    def test_timeout_fallback_type_is_g4f(self):
        """Fallback result type field must be 'g4f'."""
        response = self._compare_with_timeout()
        data = json.loads(response.data)
        self.assertEqual(data['results'][0]['type'], 'g4f')

    def test_partial_timeout_one_success_one_fallback(self):
        """When one thread times out and one succeeds, both appear in results."""
        def selective_timeout(provider, prompt, requested_model=None):
            if provider.__name__ == 'Yqcloud':
                raise concurrent.futures.TimeoutError('simulated timeout')
            return _make_result('OperaAria', True, 1.0, 'aria')

        payload = {'prompt': 'partial timeout', 'providers': ['Yqcloud', 'OperaAria']}
        with patch('main.test_g4f_provider', side_effect=selective_timeout):
            response = self.client.post(
                '/api/compare',
                data=json.dumps(payload),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['total_providers'], 2)
        self.assertEqual(data['successful_providers'], 1)
        success_results = [r for r in data['results'] if r['success']]
        failure_results = [r for r in data['results'] if not r['success']]
        self.assertEqual(len(success_results), 1)
        self.assertEqual(len(failure_results), 1)
        self.assertEqual(data['results'][0]['provider'], 'OperaAria')


# ============================================================
# 3. max_workers 边界约束测试
# 感知内部 ThreadPoolExecutor 实例化参数，验证用户传入的
# max_workers 被双重约束（上限 5，不超过 provider 数量）。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestMaxWorkersConstraint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()
        self._original_g4f_available = main.G4F_AVAILABLE
        self._original_tpe = main.ThreadPoolExecutor

    def tearDown(self):
        main.G4F_AVAILABLE = self._original_g4f_available
        main.ThreadPoolExecutor = self._original_tpe

    def _build_capturing_tpe(self, captured_list):
        original_tpe = self._original_tpe

        class CapturingTPE:
            def __init__(self, max_workers=None):
                captured_list.append(max_workers)
                self._inner = original_tpe(max_workers=max_workers)

            def __enter__(self):
                return self._inner.__enter__()

            def __exit__(self, *args):
                return self._inner.__exit__(*args)

        return CapturingTPE

    def test_max_workers_capped_at_5_when_large_value_sent(self):
        """Sending max_workers: 100 must result in ThreadPoolExecutor(max_workers<=5)."""
        captured = []
        mock_result = _make_result('Yqcloud', True, 1.0)

        with patch('main.ThreadPoolExecutor', self._build_capturing_tpe(captured)), \
             patch('main.test_g4f_provider', return_value=mock_result):
            payload = {'prompt': 'test', 'providers': ['Yqcloud'], 'max_workers': 100}
            response = self.client.post(
                '/api/compare',
                data=json.dumps(payload),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(captured), 0)
        for w in captured:
            self.assertLessEqual(w, 5)

    def test_max_workers_not_exceed_provider_count(self):
        """When only 1 provider is tested, ThreadPoolExecutor must receive max_workers=1."""
        captured = []
        mock_result = _make_result('Yqcloud', True, 1.0)

        with patch('main.ThreadPoolExecutor', self._build_capturing_tpe(captured)), \
             patch('main.test_g4f_provider', return_value=mock_result):
            payload = {'prompt': 'test', 'providers': ['Yqcloud'], 'max_workers': 5}
            response = self.client.post(
                '/api/compare',
                data=json.dumps(payload),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(captured), 0)
        for w in captured:
            self.assertLessEqual(w, 1)


# ============================================================
# 4. 全局降级状态测试
# 直接修改 main.G4F_AVAILABLE 为 False，验证各路由返回
# 503 或空列表，并在 tearDown 中恢复原始全局状态。
# ============================================================
class TestGlobalDegradationState(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()
        self._original_g4f_available = main.G4F_AVAILABLE

    def tearDown(self):
        main.G4F_AVAILABLE = self._original_g4f_available

    def test_providers_returns_empty_list_when_degraded(self):
        """GET /api/providers must return [] with 200 when G4F_AVAILABLE is False."""
        main.G4F_AVAILABLE = False
        response = self.client.get('/api/providers')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), [])

    def test_compare_returns_503_when_degraded(self):
        """POST /api/compare must return 503 when G4F_AVAILABLE is False."""
        main.G4F_AVAILABLE = False
        payload = {'prompt': 'test', 'providers': ['Yqcloud']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_test_single_returns_503_when_degraded(self):
        """POST /api/test-single must return 503 when G4F_AVAILABLE is False."""
        main.G4F_AVAILABLE = False
        payload = {'prompt': 'test', 'provider': 'Yqcloud'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_health_still_returns_200_and_reports_false_when_degraded(self):
        """GET /health must still return 200 with g4f_available=false when degraded."""
        main.G4F_AVAILABLE = False
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertFalse(data['g4f_available'])

    def test_index_route_returns_200_when_degraded(self):
        """GET / must render the page (200) even when G4F_AVAILABLE is False."""
        main.G4F_AVAILABLE = False
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_teardown_restores_global_state_correctly(self):
        """Manually toggling and restoring G4F_AVAILABLE must leave no side effects."""
        original = main.G4F_AVAILABLE
        main.G4F_AVAILABLE = False
        self.assertFalse(main.G4F_AVAILABLE)
        main.G4F_AVAILABLE = original
        self.assertEqual(main.G4F_AVAILABLE, original)

    def test_compare_400_fires_before_503_check_on_missing_prompt(self):
        """Missing prompt triggers 400 before the G4F_AVAILABLE check is reached."""
        main.G4F_AVAILABLE = False
        payload = {'providers': ['Yqcloud']}
        response = self.client.post(
            '/api/compare',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)

    def test_restoring_state_re_enables_providers_endpoint(self):
        """After tearDown restores G4F_AVAILABLE, /api/providers must reflect the change."""
        main.G4F_AVAILABLE = False
        degraded = json.loads(self.client.get('/api/providers').data)
        self.assertEqual(degraded, [])

        main.G4F_AVAILABLE = self._original_g4f_available
        restored_response = self.client.get('/api/providers')
        restored_data = json.loads(restored_response.data)
        if self._original_g4f_available:
            self.assertIsInstance(restored_data, list)
            self.assertGreater(len(restored_data), 0)
        else:
            self.assertEqual(restored_data, [])


# ============================================================
# 5. 三路 Provider 互评局部失败测试
# 3 个 Provider，第 3 个在第一轮失败，验证只有 2 个成功者
# 进行双向互评，失败者不参与也不接收互评。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestPeerReviewPartialFailure(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def _make_three_providers(self):
        p1 = MagicMock(); p1.__name__ = 'PeerA'
        p2 = MagicMock(); p2.__name__ = 'PeerB'
        p3 = MagicMock(); p3.__name__ = 'PeerC'
        return [p1, p2, p3]

    def _post_compare(self):
        providers = self._make_three_providers()

        def side_effect(*args, **kwargs):
            name = kwargs.get('provider').__name__
            if name == 'PeerC':
                raise RuntimeError('simulated failure')
            return '{"score": 80, "comment": "ok"}'

        mock_map = {
            'PeerA': ['gpt-3.5-turbo'],
            'PeerB': ['aria'],
            'PeerC': ['gpt-3.5-turbo'],
        }
        peer_prompts = {'gpt-3.5-turbo': '评分JSON:', 'aria': '评分JSON:'}

        with patch('main.g4f.ChatCompletion.create', side_effect=side_effect), \
             patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', mock_map), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', peer_prompts):
            response = self.client.post(
                '/api/compare',
                data=json.dumps({'prompt': 'test', 'providers': ['PeerA', 'PeerB', 'PeerC']}),
                content_type='application/json'
            )
        return response

    def test_partial_failure_returns_200(self):
        response = self._post_compare()
        self.assertEqual(response.status_code, 200)

    def test_two_successful_providers_each_have_one_peer_review(self):
        data = json.loads(self._post_compare().data)
        successful = [r for r in data['results'] if r['success']]
        self.assertEqual(len(successful), 2)
        for result in successful:
            self.assertEqual(len(result['peer_reviews']), 1)

    def test_failed_provider_has_empty_peer_reviews(self):
        data = json.loads(self._post_compare().data)
        failed = next(r for r in data['results'] if r['provider'] == 'PeerC')
        self.assertEqual(failed['peer_reviews'], [])


# ============================================================
# 6. 互评阶段整体崩溃健壮性测试
# 模拟互评 phase 内部整体异常（如任务构建崩溃），验证第一轮
# 结果仍能安全返回 200，peer_reviews 字段保持空列表。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestPeerReviewPhaseRobustness(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def _make_two_providers(self):
        p1 = MagicMock(); p1.__name__ = 'ProvA'
        p2 = MagicMock(); p2.__name__ = 'ProvB'
        return [p1, p2]

    def _post_with_crashing_peer_review(self):
        """Two providers both succeed; PEER_REVIEW_PROMPTS_MAP.get raises during task build."""
        providers = self._make_two_providers()
        crashing_map = MagicMock()
        crashing_map.get.side_effect = RuntimeError('simulated peer review phase crash')
        with patch('main.g4f.ChatCompletion.create', return_value='ok response'), \
             patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProvA': ['gpt-3.5-turbo'], 'ProvB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.PEER_REVIEW_PROMPTS_MAP', crashing_map):
            return self.client.post(
                '/api/compare',
                data=json.dumps({'prompt': 'test', 'providers': ['ProvA', 'ProvB']}),
                content_type='application/json'
            )

    def test_peer_review_phase_crash_returns_200(self):
        """Outer peer review try-except must catch the crash and still return 200."""
        response = self._post_with_crashing_peer_review()
        self.assertEqual(response.status_code, 200)

    def test_peer_review_phase_crash_first_round_results_preserved(self):
        """First-round responses must be intact when peer review phase crashes."""
        data = json.loads(self._post_with_crashing_peer_review().data)
        self.assertEqual(data['total_providers'], 2)
        self.assertEqual(data['successful_providers'], 2)
        for result in data['results']:
            self.assertTrue(result['success'])
            self.assertEqual(result['response'], 'ok response')

    def test_peer_review_phase_crash_peer_reviews_are_empty(self):
        """peer_reviews must remain [] when the peer review phase crashes entirely."""
        data = json.loads(self._post_with_crashing_peer_review().data)
        for result in data['results']:
            self.assertIn('peer_reviews', result)
            self.assertEqual(result['peer_reviews'], [])


# ============================================================
# 7. /api/test-single 外层异常健壮性测试
# 通过令 test_g4f_provider 直接抛出异常来触发
# test_single_provider 的外层 except 块，验证返回中文友好消息。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestTestSingleRobustness(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    @patch('main.test_g4f_provider', side_effect=RuntimeError('unexpected crash'))
    def test_outer_exception_returns_500_chinese_message(self, _mock_fn):
        """Outer except in test_single_provider must return 500 with Chinese message."""
        payload = {'prompt': 'test', 'provider': 'Yqcloud'}
        response = self.client.post(
            '/api/test-single',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertIn('Service temporarily unavailable', data['error'])


# ============================================================
# 8. /health 配置标志状态测试
# 通过 patch 将两个 prompt 映射表置为空，验证 health 接口
# 的 routing_rules_loaded / peer_review_rules_loaded 字段
# 正确反映当前配置状态。
# ============================================================
class TestHealthConfigFlags(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_routing_rules_loaded_false_when_route_map_empty(self):
        """routing_rules_loaded must be False when ROUTE_PROMPTS_MAP is empty."""
        with patch('main.ROUTE_PROMPTS_MAP', {}):
            response = self.client.get('/health')
        data = json.loads(response.data)
        self.assertFalse(data['routing_rules_loaded'])

    def test_peer_review_rules_loaded_false_when_peer_map_empty(self):
        """peer_review_rules_loaded must be False when PEER_REVIEW_PROMPTS_MAP is empty."""
        with patch('main.PEER_REVIEW_PROMPTS_MAP', {}):
            response = self.client.get('/health')
        data = json.loads(response.data)
        self.assertFalse(data['peer_review_rules_loaded'])


# ============================================================
# 9. 对话历史持久化健壮性测试
# save_chat_history 的调用被独立 try-except 包裹，任何崩溃
# 都不应影响 /api/compare 本次对比结果的正常返回。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestSaveHistoryRobustness(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _post_compare_as_user(self, user_id='uid1'):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = user_id
            payload = {'prompt': 'Hello', 'providers': ['Yqcloud']}
            return client.post(
                '/api/compare', data=json.dumps(payload), content_type='application/json'
            )

    @patch('main.save_chat_history', side_effect=RuntimeError('firestore down'))
    @patch('main.g4f.ChatCompletion.create')
    def test_save_failure_does_not_break_compare_response(self, mock_create, mock_save):
        mock_create.return_value = 'Mock response'
        response = self._post_compare_as_user()
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('results', data)
        self.assertGreater(len(data['results']), 0)

    @patch('main.save_chat_history', side_effect=RuntimeError('firestore down'))
    @patch('main.g4f.ChatCompletion.create')
    def test_save_failure_leaves_history_id_null(self, mock_create, mock_save):
        mock_create.return_value = 'Mock response'
        response = self._post_compare_as_user()
        data = json.loads(response.data)
        self.assertIsNone(data['history_id'])

    @patch('main.save_chat_history', return_value=None)
    @patch('main.g4f.ChatCompletion.create')
    def test_save_returning_none_leaves_history_id_null(self, mock_create, mock_save):
        mock_create.return_value = 'Mock response'
        response = self._post_compare_as_user()
        data = json.loads(response.data)
        self.assertIsNone(data['history_id'])


class TestSaveImageHistoryRobustness(unittest.TestCase):
    """Image-generation counterpart of TestSaveHistoryRobustness above (2026-07-04) --
    save_image_history() failures must never break /api/generate-images' own response."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _post_generate_images_as_user(self, user_id='uid1'):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = user_id
            payload = {'prompt': 'a red apple', 'providers': ['PollinationsImage']}
            return client.post(
                '/api/generate-images', data=json.dumps(payload), content_type='application/json'
            )

    @patch('main.save_image_history', side_effect=RuntimeError('firestore down'))
    @patch('main.G4FImageClient')
    def test_save_failure_does_not_break_generate_images_response(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.images.generate.return_value = MagicMock(
            data=[MagicMock(url='https://example.com/a.png', b64_json=None)]
        )
        response = self._post_generate_images_as_user()
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('results', data)
        self.assertGreater(len(data['results']), 0)

    @patch('main.save_image_history', side_effect=RuntimeError('firestore down'))
    @patch('main.G4FImageClient')
    def test_save_failure_leaves_history_id_null(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.images.generate.return_value = MagicMock(
            data=[MagicMock(url='https://example.com/a.png', b64_json=None)]
        )
        response = self._post_generate_images_as_user()
        data = json.loads(response.data)
        self.assertIsNone(data['history_id'])

    @patch('main.save_image_history', return_value=None)
    @patch('main.G4FImageClient')
    def test_save_returning_none_leaves_history_id_null(self, mock_client_cls, mock_save):
        mock_client_cls.return_value.images.generate.return_value = MagicMock(
            data=[MagicMock(url='https://example.com/a.png', b64_json=None)]
        )
        response = self._post_generate_images_as_user()
        data = json.loads(response.data)
        self.assertIsNone(data['history_id'])


# ============================================================
# 8. 互评阶段超时时长回归测试（2026-07-03）
# 修复 Yqcloud 等较慢 Provider 在互评阶段被误判超时的问题：
# run_peer_review 内部 advisory timeout 从 15s 提升到 25s，
# 外层 future.result 的等待上限同步从 25s 提升到 32s。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestPeerReviewOuterTimeoutValue(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def _make_two_providers(self):
        p1 = MagicMock(); p1.__name__ = 'ProvA'
        p2 = MagicMock(); p2.__name__ = 'ProvB'
        return [p1, p2]

    def test_peer_review_future_result_waits_up_to_32_seconds(self):
        """future.result() for the peer review stage must be called with timeout=32,
        not the old 25, so slower providers get the extra advisory-timeout headroom."""
        providers = self._make_two_providers()
        original_result = concurrent.futures.Future.result
        captured_timeouts = []

        def spy_result(self_future, timeout=None):
            captured_timeouts.append(timeout)
            return original_result(self_future, timeout=timeout)

        with patch('main.g4f.ChatCompletion.create', return_value='ok response'), \
             patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProvA': ['gpt-3.5-turbo'], 'ProvB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch.object(concurrent.futures.Future, 'result', spy_result):
            response = self.client.post(
                '/api/compare',
                data=json.dumps({'prompt': 'test', 'providers': ['ProvA', 'ProvB']}),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(32, captured_timeouts)
        self.assertNotIn(25, captured_timeouts)

    def test_peer_review_timeout_error_does_not_crash_and_leaves_that_review_missing(self):
        """A simulated TimeoutError from run_peer_review must be caught per-future;
        the route still returns 200 and first-round results stay intact."""
        providers = self._make_two_providers()
        with patch('main.g4f.ChatCompletion.create', return_value='ok response'), \
             patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProvA': ['gpt-3.5-turbo'], 'ProvB': ['aria']}), \
             patch('main.ROUTE_PROMPTS_MAP', {}), \
             patch('main.run_peer_review', side_effect=concurrent.futures.TimeoutError('simulated')):
            response = self.client.post(
                '/api/compare',
                data=json.dumps({'prompt': 'test', 'providers': ['ProvA', 'ProvB']}),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        for result in data['results']:
            self.assertTrue(result['success'])
            self.assertEqual(result['peer_reviews'], [])


def _make_image_result(provider, success, response_time, model='auto', url=None,
                        b64_json=None, error=''):
    return {
        'provider': provider,
        'success': success,
        'url': url,
        'b64_json': b64_json,
        'error': '' if success else error,
        'response_time': response_time,
        'model': model,
        'type': 'g4f_image'
    }


# ============================================================
# 10. 文生图并发调度测试
# POST /api/generate-images 是与 compare_providers() 同构的单阶段
# 并发骨架（无互评），这里验证排序契约、max_workers 双重约束、
# 硬超时熔断降级、以及 G4F_AVAILABLE 全局降级对图片路由同样生效。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestImageSortOrderInvariant(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def _post_generate(self, providers=None):
        payload = {
            'prompt': 'sort test',
            'providers': providers or ['PollinationsImage', 'OperaAria']
        }
        return self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )

    def test_two_successes_faster_provider_ranks_first(self):
        def mock_test(provider, prompt, requested_model=None):
            if provider.__name__ == 'PollinationsImage':
                return _make_image_result('PollinationsImage', True, 3.0, url='https://x/a.png')
            return _make_image_result('OperaAria', True, 1.0, model='aria', url='https://x/b.png')

        with patch('main.test_g4f_image_provider', side_effect=mock_test):
            response = self._post_generate()

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        results = data['results']
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['provider'], 'OperaAria')
        self.assertEqual(results[1]['provider'], 'PollinationsImage')

    def test_success_ranks_before_failure_regardless_of_response_time(self):
        def mock_test(provider, prompt, requested_model=None):
            if provider.__name__ == 'PollinationsImage':
                return _make_image_result('PollinationsImage', True, 5.0, url='https://x/a.png')
            return _make_image_result('OperaAria', False, 0.1, model='aria', error='cold start failed')

        with patch('main.test_g4f_image_provider', side_effect=mock_test):
            response = self._post_generate()

        data = json.loads(response.data)
        results = data['results']
        self.assertTrue(results[0]['success'])
        self.assertEqual(results[0]['provider'], 'PollinationsImage')
        self.assertFalse(results[1]['success'])

    def test_successful_providers_count_matches_actual_successes(self):
        def mock_test(provider, prompt, requested_model=None):
            if provider.__name__ == 'PollinationsImage':
                return _make_image_result('PollinationsImage', True, 2.0, url='https://x/a.png')
            return _make_image_result('OperaAria', False, 0.5, model='aria', error='fail')

        with patch('main.test_g4f_image_provider', side_effect=mock_test):
            response = self._post_generate()

        data = json.loads(response.data)
        self.assertEqual(data['successful_providers'], 1)
        self.assertEqual(data['total_providers'], 2)


@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestImageThreadTimeoutFallback(unittest.TestCase):
    """Mirrors TestThreadTimeoutFallback: a thread raising TimeoutError must degrade to
    a safe fallback image result (8-key DTO) rather than crashing the route."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def _generate_with_timeout(self, providers=None):
        payload = {
            'prompt': 'timeout test',
            'providers': providers or ['PollinationsImage']
        }
        with patch('main.test_g4f_image_provider',
                   side_effect=concurrent.futures.TimeoutError('simulated timeout')):
            return self.client.post(
                '/api/generate-images',
                data=json.dumps(payload),
                content_type='application/json'
            )

    def test_timeout_exception_does_not_crash_route(self):
        response = self._generate_with_timeout()
        self.assertEqual(response.status_code, 200)

    def test_timeout_fallback_result_has_required_keys(self):
        response = self._generate_with_timeout()
        data = json.loads(response.data)
        self.assertEqual(len(data['results']), 1)
        result = data['results'][0]
        expected_keys = {
            'provider', 'success', 'url', 'b64_json', 'error',
            'response_time', 'model', 'type'
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_timeout_fallback_success_is_false(self):
        response = self._generate_with_timeout()
        data = json.loads(response.data)
        self.assertFalse(data['results'][0]['success'])

    def test_timeout_fallback_error_message_is_friendly(self):
        response = self._generate_with_timeout()
        data = json.loads(response.data)
        self.assertIn('system is busy', data['results'][0]['error'])

    def test_timeout_fallback_type_is_g4f_image(self):
        response = self._generate_with_timeout()
        data = json.loads(response.data)
        self.assertEqual(data['results'][0]['type'], 'g4f_image')

    def test_timeout_fallback_model_follows_image_degradation_rules(self):
        response = self._generate_with_timeout()
        data = json.loads(response.data)
        expected_model = main.determine_actual_image_model('PollinationsImage', None) or 'default'
        self.assertEqual(data['results'][0]['model'], expected_model)

    def test_non_timeout_execution_error_is_surfaced(self):
        payload = {'prompt': 'boom test', 'providers': ['PollinationsImage']}
        with patch('main.test_g4f_image_provider', side_effect=RuntimeError('kaboom')):
            response = self.client.post(
                '/api/generate-images',
                data=json.dumps(payload),
                content_type='application/json'
            )
        data = json.loads(response.data)
        self.assertFalse(data['results'][0]['success'])
        self.assertIn('kaboom', data['results'][0]['error'])


@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestImageMaxWorkersConstraint(unittest.TestCase):
    """Mirrors TestMaxWorkersConstraint for /api/generate-images."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()
        self._original_tpe = main.ThreadPoolExecutor

    def tearDown(self):
        main.ThreadPoolExecutor = self._original_tpe

    def _build_capturing_tpe(self, captured_list):
        original_tpe = self._original_tpe

        class CapturingTPE:
            def __init__(self, max_workers=None):
                captured_list.append(max_workers)
                self._inner = original_tpe(max_workers=max_workers)

            def __enter__(self):
                return self._inner.__enter__()

            def __exit__(self, *args):
                return self._inner.__exit__(*args)

        return CapturingTPE

    def test_max_workers_capped_at_5_when_large_value_sent(self):
        captured = []
        mock_result = _make_image_result('PollinationsImage', True, 1.0, url='https://x/a.png')

        with patch('main.ThreadPoolExecutor', self._build_capturing_tpe(captured)), \
             patch('main.test_g4f_image_provider', return_value=mock_result):
            payload = {'prompt': 'test', 'providers': ['PollinationsImage'], 'max_workers': 100}
            response = self.client.post(
                '/api/generate-images',
                data=json.dumps(payload),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(captured), 0)
        for w in captured:
            self.assertLessEqual(w, 5)

    def test_max_workers_not_exceed_provider_count(self):
        captured = []
        mock_result = _make_image_result('PollinationsImage', True, 1.0, url='https://x/a.png')

        with patch('main.ThreadPoolExecutor', self._build_capturing_tpe(captured)), \
             patch('main.test_g4f_image_provider', return_value=mock_result):
            payload = {'prompt': 'test', 'providers': ['PollinationsImage'], 'max_workers': 5}
            response = self.client.post(
                '/api/generate-images',
                data=json.dumps(payload),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(captured), 0)
        for w in captured:
            self.assertLessEqual(w, 1)


class TestImageGlobalDegradationState(unittest.TestCase):
    """Mirrors TestGlobalDegradationState: G4F_AVAILABLE=False must degrade the image
    routes the same way it does the text ones, not crash or leak stack traces."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()
        self._original_g4f_available = main.G4F_AVAILABLE

    def tearDown(self):
        main.G4F_AVAILABLE = self._original_g4f_available

    def test_image_providers_returns_empty_list_when_degraded(self):
        main.G4F_AVAILABLE = False
        response = self.client.get('/api/image-providers')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), [])

    def test_generate_images_returns_503_when_degraded(self):
        main.G4F_AVAILABLE = False
        payload = {'prompt': 'a red apple'}
        response = self.client.post(
            '/api/generate-images',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 503)


# ============================================================
# 11. 文生图硬超时数值回归测试
# 与互评阶段的 32s outer timeout 同理：future.result() 必须以
# IMAGE_GENERATION_OUTER_TIMEOUT（当前 85 = 2 * advisory(40) + 5s 重试调度缓冲，
# 2026-07-04 从"advisory + 固定 10s"公式改为"2 * advisory + 固定 5s"公式，因为
# test_g4f_image_provider 的 429/queue 重试可能让两次尝试各自都跑到接近满
# advisory_timeout 才结束，固定 10s 缓冲不足以覆盖第二次尝试本身的耗时）等待，
# 锁定该数值不被后续改动悄悄改小/改大而不同步更新 advisory timeout 与文档。
# ============================================================
@unittest.skipUnless(main.G4F_AVAILABLE, 'g4f not available in this environment')
class TestImageOuterTimeoutValue(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_future_result_waits_up_to_85_seconds(self):
        original_result = concurrent.futures.Future.result
        captured_timeouts = []

        def spy_result(self_future, timeout=None):
            captured_timeouts.append(timeout)
            return original_result(self_future, timeout=timeout)

        mock_client_instance = MagicMock()
        fake_response = MagicMock()
        fake_response.data = [MagicMock(url='https://x/a.png', b64_json=None)]
        mock_client_instance.images.generate.return_value = fake_response

        with patch('main.G4FImageClient', return_value=mock_client_instance), \
             patch.object(concurrent.futures.Future, 'result', spy_result):
            response = self.client.post(
                '/api/generate-images',
                data=json.dumps({'prompt': 'test', 'providers': ['PollinationsImage']}),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(main.IMAGE_GENERATION_OUTER_TIMEOUT, captured_timeouts)
        self.assertEqual(main.IMAGE_GENERATION_OUTER_TIMEOUT, 85)

    def test_outer_timeout_covers_two_full_advisory_attempts_plus_buffer(self):
        """The 429/queue retry means a provider can legitimately need up to two
        full advisory_timeout-budgeted attempts (first attempt slow-fails near
        the advisory limit, retry then also runs close to the advisory limit
        before succeeding). outer must not be tighter than that, or a
        successfully-generated image gets discarded as if it failed."""
        self.assertEqual(
            main.IMAGE_GENERATION_OUTER_TIMEOUT,
            2 * main.IMAGE_GENERATION_ADVISORY_TIMEOUT + main.IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER
        )

    def test_advisory_timeout_passed_to_generate_matches_constant(self):
        mock_client_instance = MagicMock()
        fake_response = MagicMock()
        fake_response.data = [MagicMock(url='https://x/a.png', b64_json=None)]
        mock_client_instance.images.generate.return_value = fake_response

        with patch('main.G4FImageClient', return_value=mock_client_instance):
            response = self.client.post(
                '/api/generate-images',
                data=json.dumps({'prompt': 'test', 'providers': ['PollinationsImage']}),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        call_kwargs = mock_client_instance.images.generate.call_args.kwargs
        self.assertEqual(call_kwargs.get('timeout'), main.IMAGE_GENERATION_ADVISORY_TIMEOUT)
        self.assertEqual(main.IMAGE_GENERATION_ADVISORY_TIMEOUT, 40)

    def test_any_provider_gets_overridden_longer_timeouts(self):
        """AnyProvider is a g4f meta-provider that chains through several real image
        backends internally, so it needs a much longer budget than the default —
        otherwise future.result() abandons it while the underlying call (and the
        image download to get_media_dir()) is still legitimately in progress."""
        advisory, outer = main.get_image_timeouts('AnyProvider')
        self.assertGreater(advisory, main.IMAGE_GENERATION_ADVISORY_TIMEOUT)
        self.assertGreater(outer, main.IMAGE_GENERATION_OUTER_TIMEOUT)
        # outer must retain a buffer over advisory for scheduling overhead, same
        # invariant as the default pair.
        self.assertGreater(outer, advisory)
        # AnyProvider only overrides advisory — outer must still be derived from
        # the same 2*advisory+buffer formula as every other provider, not a
        # separately hand-picked number that could drift out of sync.
        self.assertEqual(outer, 2 * advisory + main.IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER)

    def test_unlisted_provider_falls_back_to_default_timeouts(self):
        advisory, outer = main.get_image_timeouts('PollinationsImage')
        self.assertEqual(advisory, main.IMAGE_GENERATION_ADVISORY_TIMEOUT)
        self.assertEqual(outer, main.IMAGE_GENERATION_OUTER_TIMEOUT)

    def test_any_provider_override_used_as_outer_future_timeout(self):
        original_result = concurrent.futures.Future.result
        captured_timeouts = []

        def spy_result(self_future, timeout=None):
            captured_timeouts.append(timeout)
            return original_result(self_future, timeout=timeout)

        mock_client_instance = MagicMock()
        fake_response = MagicMock()
        fake_response.data = [MagicMock(url='https://x/a.png', b64_json=None)]
        mock_client_instance.images.generate.return_value = fake_response

        with patch('main.G4FImageClient', return_value=mock_client_instance), \
             patch.object(concurrent.futures.Future, 'result', spy_result):
            response = self.client.post(
                '/api/generate-images',
                data=json.dumps({'prompt': 'test', 'providers': ['AnyProvider']}),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        _, expected_outer = main.get_image_timeouts('AnyProvider')
        self.assertIn(expected_outer, captured_timeouts)
        self.assertNotIn(main.IMAGE_GENERATION_OUTER_TIMEOUT, captured_timeouts)


if __name__ == '__main__':
    unittest.main(verbosity=2)
