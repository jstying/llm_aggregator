"""Tests for the 2026-07-08 peer review reliability fix.

Bug report: with 6+ providers running blind review, dense concurrent requests to the
same rate-limited free g4f backend (PollinationsAI/Groq/OpenRouterFree) triggered
repeated 429s. Two separate problems compounded:

1. `run_peer_review()` only retried once with a short fixed backoff, so a reviewer
   that needed slightly more time to clear a rate-limit window fell back to the
   canned 80-point "system is busy" score even though it had "retried".
2. `parse_peer_review_json()` used `text.find('{')`/`text.rfind('}')`, which grabs
   everything between the *first* opening brace and the *last* closing brace. When a
   reasoning-style reviewer emitted a draft JSON blob before its final answer, this
   spanned both blobs into one unparseable string, silently falling back to 80 +
   the raw text -- which is how a comment could contain "score: 88" while the
   displayed score stayed 80.

This file covers, white-box, only the changed logic:
- `_extract_balanced_json_candidates()` / `parse_peer_review_json()` correctly
  recovering the final valid JSON blob out of multi-blob text.
- `run_cross_peer_review()` now serializing same-reviewer tasks with a per-reviewer
  lock, so a busy free-tier provider is not hit concurrently within one batch.
- The outer `future.result()` timeout being genuinely formula-derived (queue depth *
  worst-case single review time + buffer), not a stale hardcoded number.
"""
import sys
import os
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _fake_g4f_provider(name):
    from unittest.mock import MagicMock
    p = MagicMock()
    p.__name__ = name
    return p


class TestParsePeerReviewJsonMultipleBlobs(unittest.TestCase):
    """Reproduces the Yqcloud bug report: a reasoning model emits a draft JSON blob
    before settling on a final one; the final (last) valid blob must win instead of
    falling back to 80 + raw text."""

    def test_self_correcting_response_uses_final_json_blob(self):
        text = (
            '{"score": 88, "comment": "Streamline the recommendation into one sentence '
            'to improve efficiency."} Wait, that comment is one sentence? Should be one '
            'sentence focusing on efficiency. Something like: "Consider tightening the '
            'recommendation paragraph to make the advice quicker to act upon." That is '
            'one sentence. Let\'s output. {"score": 88, "comment": "Consider tightening '
            'the recommendation into a concise paragraph to make the advice quicker to '
            'act upon."}'
        )
        score, comment = main.parse_peer_review_json(text)
        self.assertEqual(score, 88)
        self.assertEqual(
            comment,
            'Consider tightening the recommendation into a concise paragraph to make '
            'the advice quicker to act upon.'
        )

    def test_single_clean_json_blob_still_parses(self):
        score, comment = main.parse_peer_review_json('{"score": 70, "comment": "fine"}')
        self.assertEqual(score, 70)
        self.assertEqual(comment, 'fine')

    def test_no_json_at_all_falls_back_to_default_score(self):
        score, comment = main.parse_peer_review_json('just a plain sentence, no braces')
        self.assertEqual(score, 80)
        self.assertEqual(comment, 'just a plain sentence, no braces')

    def test_only_malformed_json_falls_back_to_default_score(self):
        # Unbalanced braces never form a complete candidate -> no candidate to try.
        score, comment = main.parse_peer_review_json('{"score": 90, "comment": "oops"')
        self.assertEqual(score, 80)

    def test_last_blob_missing_score_falls_back_to_earlier_valid_blob(self):
        text = '{"score": 65, "comment": "first"} then some trailing text {"comment": "no score here"}'
        score, comment = main.parse_peer_review_json(text)
        self.assertEqual(score, 65)
        self.assertEqual(comment, 'first')


class TestExtractBalancedJsonCandidates(unittest.TestCase):

    def test_extracts_each_top_level_object_separately(self):
        candidates = main._extract_balanced_json_candidates('{"a": 1} noise {"b": 2}')
        self.assertEqual(candidates, ['{"a": 1}', '{"b": 2}'])

    def test_no_braces_returns_empty_list(self):
        self.assertEqual(main._extract_balanced_json_candidates('no braces here'), [])

    def test_unbalanced_closing_brace_ignored(self):
        self.assertEqual(main._extract_balanced_json_candidates('} {"a": 1}'), ['{"a": 1}'])


class TestPeerReviewRetryConstantsAndFormula(unittest.TestCase):

    def test_worst_case_seconds_matches_current_constants(self):
        # Recomputed from the live constants (not hardcoded) so this test tracks
        # intentional future retuning of PEER_REVIEW_MAX_ATTEMPTS/PEER_REVIEW_REQUEST_TIMEOUT
        # instead of silently drifting from the real formula.
        backoff_upper_bound_total = sum(
            (i + 1) * 3 + 2 for i in range(main.PEER_REVIEW_MAX_ATTEMPTS - 1)
        )
        expected = main.PEER_REVIEW_MAX_ATTEMPTS * main.PEER_REVIEW_REQUEST_TIMEOUT + backoff_upper_bound_total
        self.assertEqual(main._peer_review_single_worst_case_seconds(), expected)

    def test_retry_wait_increases_with_attempt_number(self):
        with patch('main.random.uniform', return_value=0):
            wait0 = main._peer_review_retry_wait(0)
            wait1 = main._peer_review_retry_wait(1)
        self.assertLess(wait0, wait1)


class TestRunCrossPeerReviewReviewerSerialization(unittest.TestCase):
    """The actual fix for the 429-storm bug report: tasks aimed at the *same*
    reviewer must never run concurrently, even though different reviewers still run
    in parallel via the thread pool."""

    def test_same_reviewer_never_reviews_two_targets_concurrently(self):
        provider_names = ['ProviderA', 'ProviderB', 'ProviderC']
        providers = [_fake_g4f_provider(name) for name in provider_names]
        entries = [
            {'kind': 'g4f', 'provider': name, 'model': 'some-model',
             'response': f'response from {name}', 'user_api_key': None}
            for name in provider_names
        ]

        active = {}
        max_concurrent = {}
        guard = threading.Lock()

        def fake_run_peer_review(provider_obj, model, prompt):
            name = provider_obj.__name__
            with guard:
                active[name] = active.get(name, 0) + 1
                max_concurrent[name] = max(max_concurrent.get(name, 0), active[name])
            time.sleep(0.05)
            with guard:
                active[name] -= 1
            return {'reviewer_provider': name, 'reviewer_model': model, 'score': 80, 'comment': ''}

        with patch.object(main, 'G4F_PROVIDERS', providers), \
             patch.object(main, 'run_peer_review', side_effect=fake_run_peer_review):
            reviews = main.run_cross_peer_review(entries)

        for name in provider_names:
            self.assertEqual(max_concurrent.get(name), 1)
        for name in provider_names:
            self.assertEqual(len(reviews[name]), 2)


class TestRunCrossPeerReviewTimeoutFormula(unittest.TestCase):
    """Confirms the outer future.result() timeout is actually driven by
    `_peer_review_single_worst_case_seconds()` * queue depth + buffer, not some
    unrelated constant -- by shrinking the formula's inputs to near-zero and
    checking that a reviewer slower than that tiny budget is genuinely dropped."""

    def test_timeout_value_is_derived_from_formula_not_hardcoded(self):
        provider_names = ['ProviderA', 'ProviderB']
        providers = [_fake_g4f_provider(name) for name in provider_names]
        entries = [
            {'kind': 'g4f', 'provider': name, 'model': 'some-model',
             'response': f'response from {name}', 'user_api_key': None}
            for name in provider_names
        ]

        def slow_fake_run_peer_review(provider_obj, model, prompt):
            time.sleep(0.3)
            return {'reviewer_provider': provider_obj.__name__, 'reviewer_model': model,
                     'score': 95, 'comment': 'slow but done'}

        with patch.object(main, 'G4F_PROVIDERS', providers), \
             patch.object(main, 'run_peer_review', side_effect=slow_fake_run_peer_review), \
             patch.object(main, '_peer_review_single_worst_case_seconds', return_value=0.01), \
             patch.object(main, 'PEER_REVIEW_FUTURE_TIMEOUT_BUFFER', 0):
            reviews = main.run_cross_peer_review(entries)

        # With a near-zero formula budget the 0.3s reviewer must time out and be
        # dropped -- proving the timeout genuinely comes from the formula rather than
        # a generous fixed constant that would have swallowed this sleep.
        for name in provider_names:
            self.assertEqual(reviews[name], [])

    def test_queue_depth_scales_timeout_for_busy_reviewers(self):
        # 3 providers all reviewing each other -> each reviewer queues 2 tasks behind
        # its own lock, so max_reviewer_queue_depth must be 2, not 1.
        provider_names = ['ProviderA', 'ProviderB', 'ProviderC']
        providers = [_fake_g4f_provider(name) for name in provider_names]
        entries = [
            {'kind': 'g4f', 'provider': name, 'model': 'some-model',
             'response': f'response from {name}', 'user_api_key': None}
            for name in provider_names
        ]

        captured_timeouts = []
        import concurrent.futures
        original_result = concurrent.futures.Future.result

        def spy_result(self_future, timeout=None):
            captured_timeouts.append(timeout)
            return original_result(self_future, timeout=timeout)

        with patch.object(main, 'G4F_PROVIDERS', providers), \
             patch.object(main, 'run_peer_review', return_value={
                 'reviewer_provider': 'x', 'reviewer_model': 'y', 'score': 80, 'comment': ''
             }), \
             patch.object(concurrent.futures.Future, 'result', spy_result):
            main.run_cross_peer_review(entries)

        expected = 2 * main._peer_review_single_worst_case_seconds() + main.PEER_REVIEW_FUTURE_TIMEOUT_BUFFER
        self.assertTrue(all(t == expected for t in captured_timeouts))


if __name__ == '__main__':
    unittest.main()
