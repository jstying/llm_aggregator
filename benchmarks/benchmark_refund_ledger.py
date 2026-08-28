"""Refund ledger exact-once benchmark: 200 trials, each recording one pending refund and
racing 25 threads to consume it with the identical request_id, against the real
_record_pending_frontier_refund() / _consume_pending_frontier_refund() (mutex-guarded).
Plus a 200-trial pass confirming a never-recorded request_id is always rejected. Pure
in-memory, no network. The committed unit-test twin is
tests/test_refund_ledger_concurrency.py."""
import threading
from concurrent.futures import ThreadPoolExecutor

import _bootstrap  # noqa: F401
import main

TRIALS = 200
RACING_THREADS = 25


def main_cli():
    duplicate = lost = 0
    for trial in range(TRIALS):
        request_id = f'bench-{trial}'
        main._record_pending_frontier_refund(request_id, 'user-1', 'claude')
        barrier = threading.Barrier(RACING_THREADS)

        def racer():
            barrier.wait()
            return main._consume_pending_frontier_refund(request_id, 'user-1', 'claude')

        with ThreadPoolExecutor(max_workers=RACING_THREADS) as pool:
            wins = sum(f.result() for f in [pool.submit(racer) for _ in range(RACING_THREADS)])
        if wins > 1:
            duplicate += 1
        if wins == 0:
            lost += 1

    rejected = sum(
        0 if main._consume_pending_frontier_refund(f'ghost-{i}', 'user-1', 'claude') else 1
        for i in range(TRIALS)
    )

    print(f'{TRIALS} trials x {RACING_THREADS} racing threads '
          f'({TRIALS * RACING_THREADS} total racing attempts)')
    print(f'duplicate refunds: {duplicate}   lost refunds: {lost}')
    print(f'never-recorded ids rejected: {rejected}/{TRIALS}')


if __name__ == '__main__':
    main_cli()
