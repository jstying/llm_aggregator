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


if __name__ == '__main__':
    unittest.main(verbosity=2)
