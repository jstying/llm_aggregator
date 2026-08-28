"""Error classifier accuracy benchmark: runs the shared vendor-error fixture table (see
tests/test_error_classifiers.py -- every LIVE fixture there was captured from a real vendor
API response) through the real _classify_anthropic_error() / _classify_google_genai_error()
/ _classify_openai_error(), and compares against a naive status-code-only classifier to
show the concrete gap the real ones close. Pure in-memory, no network."""
import os
import sys

import _bootstrap  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tests'))

from test_error_classifiers import CLASSIFIERS, FIXTURES  # noqa: E402


def main_cli():
    correct = live_correct = live_total = 0
    for vendor, note, live, exc, expected_cls, _substring in FIXTURES:
        classification, _message = CLASSIFIERS[vendor](exc)
        hit = classification == expected_cls
        correct += hit
        if live:
            live_total += 1
            live_correct += hit
        marker = 'LIVE' if live else 'docs'
        print(f'[{vendor:9s}] [{marker}] {"OK " if hit else "MISS"} {note}')

    naive_correct = 0
    for _vendor, _note, _live, exc, expected_cls, _substring in FIXTURES:
        naive = {429: 'QUOTA_EXHAUSTED', 403: 'PERMISSION_DENIED'}.get(getattr(exc, 'status_code', None))
        naive_correct += naive == expected_cls

    print(f'\nreal classifiers:  {correct}/{len(FIXTURES)} correct '
          f'({live_correct}/{live_total} on live-captured shapes)')
    print(f'naive status-code table: {naive_correct}/{len(FIXTURES)} correct')


if __name__ == '__main__':
    main_cli()
