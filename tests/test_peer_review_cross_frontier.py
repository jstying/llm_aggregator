"""Tests for the unified cross g4f/frontier peer-review feature (2026-07-07 新增).

Covers `main.run_frontier_peer_review()`, `main.run_cross_peer_review()`, and the new
`POST /api/peer-review` endpoint that replaced `compare_providers()`'s old inline,
g4f-only peer review phase (see CLAUDE.md 更新记录) so that free g4f providers and the
three frontier text providers (Claude/ChatGPT/Gemini) can review each other
bidirectionally.

Split mirrors the project's usual split:
- White-box/unit: `run_frontier_peer_review()`/`run_cross_peer_review()` dispatch logic,
  called directly (no Flask route involved).
- Black-box/integration: `POST /api/peer-review` through the Flask test client -- auth
  gating (only required when a frontier reviewer is in play), payload cap/validation,
  and persona-map completeness.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, ANY

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _fake_g4f_provider(name):
    p = MagicMock()
    p.__name__ = name
    return p


def _g4f_result(provider='ProviderA', model='gpt-3.5-turbo', success=True, response='hi'):
    return {
        'provider': provider, 'success': success, 'response': response, 'error': '',
        'response_time': 1.0, 'model': model, 'type': 'g4f', 'peer_reviews': [],
    }


def _claude_result(success=True, response='hi', model='claude-sonnet-5'):
    return {
        'provider': 'Claude', 'success': success, 'response': response, 'error': '',
        'response_time': 1.0, 'model': model, 'type': 'anthropic', 'peer_reviews': [],
    }


def _chatgpt_result(success=True, response='hi', model='gpt-5.5'):
    return {
        'provider': 'ChatGPT', 'success': success, 'response': response, 'error': '',
        'response_time': 1.0, 'model': model, 'type': 'openai', 'peer_reviews': [],
    }


# ==========================================================================
# 白盒/单元测试：run_frontier_peer_review()
# ==========================================================================

class TestRunFrontierPeerReview(unittest.TestCase):

    def test_claude_reviewer_success_parses_score_and_comment(self):
        with patch.object(main, 'call_claude_model', return_value={
            'provider': 'Claude', 'success': True,
            'response': '{"score": 88, "comment": "nuanced but hedges appropriately"}',
            'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }) as mock_call:
            result = main.run_frontier_peer_review('Claude', 'claude-sonnet-5', 'review this')

        self.assertEqual(result['reviewer_provider'], 'Claude')
        self.assertEqual(result['reviewer_model'], 'claude-sonnet-5')
        self.assertEqual(result['score'], 88)
        self.assertEqual(result['comment'], 'nuanced but hedges appropriately')
        mock_call.assert_called_once_with('review this', 'claude-sonnet-5', None, apply_persona=False)

    def test_chatgpt_reviewer_forwards_user_api_key(self):
        with patch.object(main, 'call_chatgpt_model', return_value={
            'provider': 'ChatGPT', 'success': True, 'response': '{"score": 70, "comment": "ok"}',
            'error': '', 'response_time': 1.0, 'model': 'gpt-5.5', 'type': 'openai',
        }) as mock_call:
            main.run_frontier_peer_review('ChatGPT', 'gpt-5.5', 'review this', user_api_key='sk-own-key')

        mock_call.assert_called_once_with('review this', 'gpt-5.5', 'sk-own-key', apply_persona=False)

    def test_gemini_reviewer_quota_exhausted_maps_to_friendly_comment(self):
        with patch.object(main, 'call_gemini_text_model', return_value={
            'provider': 'Gemini', 'success': False, 'response': '',
            'error': 'SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED',
            'error_code': 'SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED',
            'response_time': 1.0, 'model': 'gemini-3.5-flash', 'type': 'google_genai_text',
        }):
            result = main.run_frontier_peer_review('Gemini', 'gemini-3.5-flash', 'review this')

        self.assertEqual(result['score'], 80)
        self.assertIn('temporarily unavailable', result['comment'])
        self.assertNotIn('SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED', result['comment'])

    def test_generic_failure_included_verbatim_in_comment(self):
        with patch.object(main, 'call_claude_model', return_value={
            'provider': 'Claude', 'success': False, 'response': '',
            'error': 'The system is busy and trying to reconnect. Please try again shortly.',
            'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }):
            result = main.run_frontier_peer_review('Claude', 'claude-sonnet-5', 'review this')

        self.assertEqual(
            result['comment'],
            'Review failed: The system is busy and trying to reconnect. Please try again shortly.'
        )

    def test_malformed_json_response_falls_back_to_default_score(self):
        with patch.object(main, 'call_claude_model', return_value={
            'provider': 'Claude', 'success': True, 'response': 'not json at all',
            'error': '', 'response_time': 1.0, 'model': 'claude-sonnet-5', 'type': 'anthropic',
        }):
            result = main.run_frontier_peer_review('Claude', 'claude-sonnet-5', 'review this')

        self.assertEqual(result['score'], 80)
        self.assertEqual(result['comment'], 'not json at all')


# ==========================================================================
# 白盒/单元测试：run_cross_peer_review()
# ==========================================================================

class TestRunCrossPeerReview(unittest.TestCase):

    def test_no_entries_returns_empty_dict(self):
        self.assertEqual(main.run_cross_peer_review([]), {})

    def test_single_entry_produces_no_review_tasks(self):
        entries = [{'kind': 'g4f', 'provider': 'Solo', 'model': 'gpt-3.5-turbo',
                    'response': 'x', 'user_api_key': None}]
        with patch.object(main, 'run_peer_review') as mock_g4f_review, \
             patch.object(main, 'run_frontier_peer_review') as mock_frontier_review:
            reviews = main.run_cross_peer_review(entries)

        self.assertEqual(reviews, {'Solo': []})
        mock_g4f_review.assert_not_called()
        mock_frontier_review.assert_not_called()

    def test_every_entry_reviewed_by_every_other_no_self_review(self):
        providers = [_fake_g4f_provider('ProviderA'), _fake_g4f_provider('ProviderB')]
        entries = [
            {'kind': 'g4f', 'provider': 'ProviderA', 'model': 'gpt-3.5-turbo',
             'response': 'A says hi', 'user_api_key': None},
            {'kind': 'g4f', 'provider': 'ProviderB', 'model': 'aria',
             'response': 'B says hi', 'user_api_key': None},
            {'kind': 'Claude', 'provider': 'Claude', 'model': 'claude-sonnet-5',
             'response': 'Claude says hi', 'user_api_key': None},
        ]

        def fake_g4f_review(provider_obj, model, prompt):
            return {'reviewer_provider': provider_obj.__name__, 'reviewer_model': model,
                     'score': 80, 'comment': 'ok'}

        def fake_frontier_review(kind, model, prompt, user_api_key=None):
            return {'reviewer_provider': kind, 'reviewer_model': model, 'score': 80, 'comment': 'ok'}

        with patch.object(main, 'G4F_PROVIDERS', providers), \
             patch.object(main, 'run_peer_review', side_effect=fake_g4f_review) as mock_g4f_review, \
             patch.object(main, 'run_frontier_peer_review', side_effect=fake_frontier_review) as mock_frontier_review:
            reviews = main.run_cross_peer_review(entries)

        # 3 个 entry，两两互评（排除自评）= 3 * 2 = 6 次调用。
        self.assertEqual(mock_g4f_review.call_count + mock_frontier_review.call_count, 6)
        for provider in ('ProviderA', 'ProviderB', 'Claude'):
            self.assertEqual(len(reviews[provider]), 2)
            reviewer_names = [r['reviewer_provider'] for r in reviews[provider]]
            self.assertNotIn(provider, reviewer_names)

    def test_g4f_reviewer_dispatched_with_matching_provider_object(self):
        provider_obj = _fake_g4f_provider('ProviderA')
        entries = [
            {'kind': 'g4f', 'provider': 'ProviderA', 'model': 'gpt-3.5-turbo',
             'response': 'A', 'user_api_key': None},
            {'kind': 'Claude', 'provider': 'Claude', 'model': 'claude-sonnet-5',
             'response': 'C', 'user_api_key': 'sk-x'},
        ]
        with patch.object(main, 'G4F_PROVIDERS', [provider_obj]), \
             patch.object(main, 'run_peer_review', return_value={
                 'reviewer_provider': 'ProviderA', 'reviewer_model': 'gpt-3.5-turbo',
                 'score': 80, 'comment': ''}) as mock_g4f_review, \
             patch.object(main, 'run_frontier_peer_review', return_value={
                 'reviewer_provider': 'Claude', 'reviewer_model': 'claude-sonnet-5',
                 'score': 80, 'comment': ''}) as mock_frontier_review:
            main.run_cross_peer_review(entries)

        mock_g4f_review.assert_called_once()
        args, _ = mock_g4f_review.call_args
        self.assertIs(args[0], provider_obj)
        self.assertEqual(args[1], 'gpt-3.5-turbo')

        mock_frontier_review.assert_called_once_with('Claude', 'claude-sonnet-5', ANY, 'sk-x')

    def test_frontier_judge_persona_embedded_in_review_prompt(self):
        provider_obj = _fake_g4f_provider('ProviderA')
        entries = [
            {'kind': 'Claude', 'provider': 'Claude', 'model': 'claude-sonnet-5',
             'response': 'target text', 'user_api_key': None},
            {'kind': 'g4f', 'provider': 'ProviderA', 'model': 'gpt-3.5-turbo',
             'response': 'other', 'user_api_key': None},
        ]
        captured = {}

        def fake_frontier_review(kind, model, prompt, user_api_key=None):
            captured['prompt'] = prompt
            return {'reviewer_provider': kind, 'reviewer_model': model, 'score': 80, 'comment': ''}

        with patch.object(main, 'G4F_PROVIDERS', [provider_obj]), \
             patch.object(main, 'run_peer_review', return_value={
                 'reviewer_provider': 'ProviderA', 'reviewer_model': 'gpt-3.5-turbo',
                 'score': 80, 'comment': ''}), \
             patch.object(main, 'run_frontier_peer_review', side_effect=fake_frontier_review):
            main.run_cross_peer_review(entries)

        self.assertIn(main.PEER_REVIEW_PROMPTS_MAP['claude-sonnet-5'], captured['prompt'])
        self.assertIn('other', captured['prompt'])


# ==========================================================================
# 黑盒/集成测试：POST /api/peer-review
# ==========================================================================

class TestPeerReviewEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    @patch.object(main, 'run_cross_peer_review')
    def test_g4f_only_list_works_without_login(self, mock_run_review):
        mock_run_review.return_value = {'ProviderA': [], 'ProviderB': []}
        providers = [_fake_g4f_provider('ProviderA'), _fake_g4f_provider('ProviderB')]
        with main.app.test_client() as client, \
             patch.object(main, 'G4F_PROVIDERS', providers), \
             patch.object(main, 'PROVIDER_MODELS_MAP',
                          {'ProviderA': ['gpt-3.5-turbo'], 'ProviderB': ['aria']}):
            response = client.post('/api/peer-review', json={
                'results': [
                    _g4f_result('ProviderA', 'gpt-3.5-turbo'),
                    _g4f_result('ProviderB', 'aria'),
                ]
            })

        self.assertEqual(response.status_code, 200)
        mock_run_review.assert_called_once()

    def test_frontier_entry_present_guest_gets_401(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.post('/api/peer-review', json={
                'results': [_claude_result(), _g4f_result()]
            })
        self.assertEqual(response.status_code, 401)

    def test_frontier_entry_present_anonymous_gets_401(self):
        with main.app.test_client() as client:
            response = client.post('/api/peer-review', json={
                'results': [_claude_result(), _g4f_result()]
            })
        self.assertEqual(response.status_code, 401)

    @patch.object(main, 'run_cross_peer_review')
    def test_frontier_entry_present_logged_in_user_succeeds(self, mock_run_review):
        mock_run_review.return_value = {'Claude': [], 'ChatGPT': []}
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/peer-review', json={
                'results': [_claude_result(), _chatgpt_result()]
            })

        self.assertEqual(response.status_code, 200)
        mock_run_review.assert_called_once()

    @patch.object(main, 'run_cross_peer_review')
    def test_fewer_than_two_valid_entries_returns_empty_without_calling_review(self, mock_run_review):
        with main.app.test_client() as client, \
             patch.object(main, 'PROVIDER_MODELS_MAP', {'Yqcloud': ['gpt-3.5-turbo']}):
            response = client.post('/api/peer-review', json={
                'results': [_g4f_result('Yqcloud', 'gpt-3.5-turbo')]
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'peer_reviews': {}})
        mock_run_review.assert_not_called()

    @patch.object(main, 'run_cross_peer_review')
    def test_unrecognized_provider_model_combo_silently_dropped(self, mock_run_review):
        with main.app.test_client() as client, \
             patch.object(main, 'PROVIDER_MODELS_MAP', {'Yqcloud': ['gpt-3.5-turbo']}):
            response = client.post('/api/peer-review', json={
                'results': [
                    _g4f_result('Yqcloud', 'gpt-3.5-turbo'),
                    _g4f_result('TotallyMadeUpProvider', 'made-up-model'),
                ]
            })

        # 只剩 1 条有效结果，凑不够 2 条，不触发互评。
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'peer_reviews': {}})
        mock_run_review.assert_not_called()

    @patch.object(main, 'run_cross_peer_review')
    def test_entries_beyond_cap_are_ignored(self, mock_run_review):
        mock_run_review.side_effect = lambda entries: {e['provider']: [] for e in entries}
        many_names = [f'P{i}' for i in range(main.MAX_PEER_REVIEW_ENTRIES + 5)]
        providers = [_fake_g4f_provider(name) for name in many_names]
        models_map = {name: ['gpt-3.5-turbo'] for name in many_names}
        results = [_g4f_result(name, 'gpt-3.5-turbo') for name in many_names]

        with main.app.test_client() as client, \
             patch.object(main, 'G4F_PROVIDERS', providers), \
             patch.object(main, 'PROVIDER_MODELS_MAP', models_map):
            client.post('/api/peer-review', json={'results': results})

        mock_run_review.assert_called_once()
        (entries_arg,), _ = mock_run_review.call_args
        self.assertEqual(len(entries_arg), main.MAX_PEER_REVIEW_ENTRIES)

    @patch.object(main, 'run_cross_peer_review')
    def test_frontier_entry_dropped_when_provider_unavailable(self, mock_run_review):
        with main.app.test_client() as client, \
             patch.object(main, 'CLAUDE_AVAILABLE', False), \
             patch.object(main, 'PROVIDER_MODELS_MAP', {'Yqcloud': ['gpt-3.5-turbo']}):
            self._login(client)
            response = client.post('/api/peer-review', json={
                'results': [_claude_result(), _g4f_result('Yqcloud', 'gpt-3.5-turbo')]
            })

        # Claude 条目因为 CLAUDE_AVAILABLE=False 被丢弃，只剩 1 条有效结果，不触发互评。
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'peer_reviews': {}})
        mock_run_review.assert_not_called()

    @patch.object(main, 'update_chat_history_peer_reviews')
    @patch.object(main, 'run_cross_peer_review')
    def test_history_id_persists_final_peer_reviews_for_logged_in_user(self, mock_run_review, mock_update_history):
        mock_run_review.return_value = {'Claude': [{'reviewer_provider': 'ChatGPT', 'reviewer_model': 'gpt-5.5',
                                                      'score': 90, 'comment': 'nice'}], 'ChatGPT': []}
        with main.app.test_client() as client:
            self._login(client)
            response = client.post('/api/peer-review', json={
                'results': [_claude_result(), _chatgpt_result()],
                'history_id': 'hist123',
            })

        self.assertEqual(response.status_code, 200)
        mock_update_history.assert_called_once_with('uid1', 'hist123', mock_run_review.return_value)

    @patch.object(main, 'update_chat_history_peer_reviews')
    @patch.object(main, 'run_cross_peer_review')
    def test_no_history_id_skips_persistence(self, mock_run_review, mock_update_history):
        mock_run_review.return_value = {'Claude': [], 'ChatGPT': []}
        with main.app.test_client() as client:
            self._login(client)
            client.post('/api/peer-review', json={
                'results': [_claude_result(), _chatgpt_result()],
            })

        mock_update_history.assert_not_called()

    def test_results_not_a_list_returns_400(self):
        with main.app.test_client() as client:
            response = client.post('/api/peer-review', json={'results': 'not-a-list'})
        self.assertEqual(response.status_code, 400)


# ==========================================================================
# 白盒测试：前沿人设 map 完整性（防止未来编辑漏掉某个前沿模型）
# ==========================================================================

class TestFrontierPersonaMapsCoverAllModels(unittest.TestCase):

    def test_all_frontier_models_have_style_persona(self):
        frontier_model_keys = (
            set(main.CLAUDE_MODELS) | set(main.CHATGPT_MODELS) | set(main.GEMINI_TEXT_MODELS)
        )
        self.assertTrue(frontier_model_keys.issubset(main.FRONTIER_STYLE_PROMPTS_MAP.keys()))

    def test_all_frontier_models_have_judge_persona(self):
        frontier_model_keys = (
            set(main.CLAUDE_MODELS) | set(main.CHATGPT_MODELS) | set(main.GEMINI_TEXT_MODELS)
        )
        self.assertTrue(frontier_model_keys.issubset(main.PEER_REVIEW_PROMPTS_MAP.keys()))


if __name__ == '__main__':
    unittest.main()
