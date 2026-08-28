"""High-concurrency exact-once tests for the Stop Generating refund ledger.

The ledger's consume path (_consume_pending_frontier_refund) is a check-then-pop compound
operation guarded by _PENDING_FRONTIER_REFUNDS_LOCK. These tests are the committed,
repeatable version of the ad hoc benchmark recorded in CLAUDE.md section 12: for each of
200 trials, one pending refund is recorded and 25 threads race to consume it with the
identical request_id; exactly one thread must win each trial, with zero duplicate refunds
and zero lost refunds. A second 200-trial pass confirms a request_id that was never
recorded is always rejected. Everything here is in-memory, no network, so it runs as part
of the ordinary unit test suite.
"""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

import main


TRIALS = 200
RACING_THREADS = 25


class TestRefundLedgerExactOnce(unittest.TestCase):
    def setUp(self):
        main._PENDING_FRONTIER_REFUNDS.clear()

    def tearDown(self):
        main._PENDING_FRONTIER_REFUNDS.clear()

    def test_exactly_one_winner_across_200_high_concurrency_trials(self):
        """200 trials x 25 racing threads: every trial has exactly 1 winner, 0 duplicates."""
        for trial in range(TRIALS):
            request_id = f'race-{trial}'
            main._record_pending_frontier_refund(request_id, 'user-1', 'claude')

            barrier = threading.Barrier(RACING_THREADS)

            def racer():
                # All 25 threads release from the barrier together so the check-then-pop
                # window is hit as densely as the scheduler allows.
                barrier.wait()
                return main._consume_pending_frontier_refund(request_id, 'user-1', 'claude')

            with ThreadPoolExecutor(max_workers=RACING_THREADS) as pool:
                outcomes = [f.result() for f in [pool.submit(racer) for _ in range(RACING_THREADS)]]

            self.assertEqual(
                sum(outcomes), 1,
                f'trial {trial}: expected exactly 1 winning refund, got {sum(outcomes)}'
            )
            self.assertNotIn(
                request_id, main._PENDING_FRONTIER_REFUNDS,
                f'trial {trial}: consumed entry must be removed from the ledger'
            )

    def test_never_recorded_request_id_always_rejected_200_trials(self):
        """A request_id that was never recorded can never produce a refund."""
        for trial in range(TRIALS):
            self.assertFalse(
                main._consume_pending_frontier_refund(f'ghost-{trial}', 'user-1', 'claude')
            )

    def test_wrong_user_or_provider_never_wins_under_concurrency(self):
        """Racing consumers with mismatched user/provider never steal another user's refund."""
        request_id = 'cross-identity'
        main._record_pending_frontier_refund(request_id, 'user-1', 'claude')

        barrier = threading.Barrier(RACING_THREADS)
        identities = [
            ('user-1', 'claude') if i == 0 else ('user-2', 'claude') if i % 2 else ('user-1', 'chatgpt')
            for i in range(RACING_THREADS)
        ]

        def racer(identity):
            barrier.wait()
            return main._consume_pending_frontier_refund(request_id, identity[0], identity[1])

        with ThreadPoolExecutor(max_workers=RACING_THREADS) as pool:
            outcomes = [f.result() for f in [pool.submit(racer, ident) for ident in identities]]

        self.assertEqual(sum(outcomes), 1)
        self.assertTrue(outcomes[0], 'only the matching (user, provider) identity may win')

    def test_concurrent_record_and_consume_distinct_ids_no_cross_talk(self):
        """Interleaved record/consume on distinct request_ids stays exact-once per id."""
        ids = [f'mixed-{i}' for i in range(TRIALS)]
        for rid in ids:
            main._record_pending_frontier_refund(rid, 'user-1', 'gemini_text')

        def consume_twice(rid):
            first = main._consume_pending_frontier_refund(rid, 'user-1', 'gemini_text')
            second = main._consume_pending_frontier_refund(rid, 'user-1', 'gemini_text')
            return first, second

        with ThreadPoolExecutor(max_workers=RACING_THREADS) as pool:
            outcomes = list(pool.map(consume_twice, ids))

        for rid, (first, second) in zip(ids, outcomes):
            self.assertTrue(first, f'{rid}: the first consume must win')
            self.assertFalse(second, f'{rid}: the second consume must be rejected')


if __name__ == '__main__':
    unittest.main()
