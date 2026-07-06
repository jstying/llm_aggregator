"""Tests for hiding failed blind reviews instead of forcing a fallback comment
(2026-07-05 fix). Previously a peer review that exhausted retries or hit a
non-retryable error still produced a visible entry like "80 pts: The system is
busy and trying to reconnect...". That forced users to wait through the full
retry chain just to see a useless card, and cluttered results with junk scores.

Now `run_peer_review()`/`run_frontier_peer_review()` return `None` on any
failure, and `run_cross_peer_review()` drops `None` entries entirely -- the
target's real response is unaffected, only the failed reviewer's card is
hidden. Retry attempts were also trimmed (PEER_REVIEW_MAX_ATTEMPTS 3 -> 2) to
prioritize provider response speed over exhausting retries for one reviewer.
"""
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _fake_g4f_provider(name):
    p = MagicMock()
    p.__name__ = name
    return p


def _g4f_result(provider, model='gpt-3.5-turbo'):
    return {
        'provider': provider, 'success': True, 'response': f'{provider} says hi',
        'error': '', 'response_time': 1.0, 'model': model, 'type': 'g4f',
        'peer_reviews': [],
    }


class TestFailedPeerReviewHiddenFromCrossReview(unittest.TestCase):
    """run_cross_peer_review() must drop a failed reviewer's entry while keeping
    every successful review from other reviewers intact."""

    def test_one_failing_reviewer_among_several_is_dropped_not_shown(self):
        providers = [_fake_g4f_provider(n) for n in ('ProviderA', 'ProviderB', 'ProviderC')]
        entries = [_g4f_result(p.__name__) for p in providers]
        for e in entries:
            e['kind'] = 'g4f'
            e['user_api_key'] = None

        def fake_run_peer_review(provider_obj, model, prompt):
            if provider_obj.__name__ == 'ProviderB':
                return None  # ProviderB is the flaky reviewer that exhausted retries
            return {
                'reviewer_provider': provider_obj.__name__, 'reviewer_model': model,
                'score': 90, 'comment': 'solid answer',
            }

        with patch.object(main, 'G4F_PROVIDERS', providers), \
             patch.object(main, 'run_peer_review', side_effect=fake_run_peer_review):
            reviews = main.run_cross_peer_review(entries)

        # ProviderB never produces a visible review, so any target *other than*
        # ProviderB loses that one contribution (1 review instead of 2). ProviderB
        # itself is still reviewed normally by ProviderA/ProviderC (2 reviews) --
        # only its own outgoing reviews are hidden.
        for target in ('ProviderA', 'ProviderC'):
            self.assertEqual(len(reviews[target]), 1)
            reviewer_names = [r['reviewer_provider'] for r in reviews[target]]
            self.assertNotIn('ProviderB', reviewer_names)
            self.assertNotIn(target, reviewer_names)
        self.assertEqual(len(reviews['ProviderB']), 2)

    def test_all_reviewers_failing_yields_empty_list_not_error(self):
        providers = [_fake_g4f_provider(n) for n in ('ProviderA', 'ProviderB')]
        entries = [_g4f_result(p.__name__) for p in providers]
        for e in entries:
            e['kind'] = 'g4f'
            e['user_api_key'] = None

        with patch.object(main, 'G4F_PROVIDERS', providers), \
             patch.object(main, 'run_peer_review', return_value=None):
            reviews = main.run_cross_peer_review(entries)

        self.assertEqual(reviews, {'ProviderA': [], 'ProviderB': []})

    def test_failing_frontier_reviewer_dropped_alongside_healthy_g4f_reviewer(self):
        provider_obj = _fake_g4f_provider('ProviderA')
        entries = [
            {'kind': 'g4f', 'provider': 'ProviderA', 'model': 'gpt-3.5-turbo',
             'response': 'A', 'user_api_key': None},
            {'kind': 'Claude', 'provider': 'Claude', 'model': 'claude-sonnet-5',
             'response': 'C', 'user_api_key': None},
        ]
        with patch.object(main, 'G4F_PROVIDERS', [provider_obj]), \
             patch.object(main, 'run_peer_review', return_value={
                 'reviewer_provider': 'ProviderA', 'reviewer_model': 'gpt-3.5-turbo',
                 'score': 85, 'comment': 'good'}), \
             patch.object(main, 'run_frontier_peer_review', return_value=None):
            reviews = main.run_cross_peer_review(entries)

        # Claude's review of ProviderA failed and must be hidden.
        self.assertEqual(reviews['ProviderA'], [])
        # ProviderA's review of Claude succeeded and must still show up.
        self.assertEqual(len(reviews['Claude']), 1)
        self.assertEqual(reviews['Claude'][0]['reviewer_provider'], 'ProviderA')


class TestPeerReviewEndpointHidesFailedReviews(unittest.TestCase):
    """Black-box: POST /api/peer-review response only contains reviews that
    actually succeeded; a failed reviewer simply produces a shorter array,
    never a fabricated 'system is busy' entry."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_response_omits_entry_for_failed_reviewer(self):
        providers = [MagicMock(), MagicMock()]
        providers[0].__name__ = 'ProvA'
        providers[1].__name__ = 'ProvB'

        def fake_create(model, messages, provider, timeout):
            if provider.__name__ == 'ProvB':
                raise Exception('Some persistent non-retryable error')
            return '{"score": 95, "comment": "clear and correct"}'

        with patch('main.g4f.ChatCompletion.create', side_effect=fake_create), \
             patch('main.G4F_PROVIDERS', providers), \
             patch('main.PROVIDER_MODELS_MAP', {'ProvA': ['gpt-3.5-turbo'], 'ProvB': ['aria']}):
            response = self.client.post(
                '/api/peer-review',
                data=json.dumps({'results': [
                    {'provider': 'ProvA', 'success': True, 'response': 'ok A', 'error': '',
                     'response_time': 1.0, 'model': 'gpt-3.5-turbo', 'type': 'g4f', 'peer_reviews': []},
                    {'provider': 'ProvB', 'success': True, 'response': 'ok B', 'error': '',
                     'response_time': 1.0, 'model': 'aria', 'type': 'g4f', 'peer_reviews': []},
                ]}),
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        # ProvB (the reviewer) always errors -> its review of ProvA never appears.
        self.assertEqual(data['peer_reviews']['ProvA'], [])
        # ProvA (the reviewer) always succeeds -> its review of ProvB is present.
        self.assertEqual(len(data['peer_reviews']['ProvB']), 1)
        self.assertNotIn('system is busy', json.dumps(data['peer_reviews']))


if __name__ == '__main__':
    unittest.main()
