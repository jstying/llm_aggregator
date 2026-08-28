"""Stage-2 concurrency benchmark (added 2026-08-28 with POST /api/frontier-chat): the three
frontier SDK calls run sequentially vs through ThreadPoolExecutor(3), the same fan-out the
batch route uses. LIVE: every round spends real API credit on all three vendor accounts
(the cheapest model of each), so keep --rounds small."""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import _bootstrap  # noqa: F401
import main

PROMPT = 'In one sentence, what does DNS do?'
CALLS = [
    (main.call_claude_model, 'claude-haiku-4-5'),
    (main.call_chatgpt_model, 'gpt-5.4-mini'),
    (main.call_gemini_text_model, 'gemini-3.1-flash-lite'),
]


def run_sequential():
    started = time.time()
    results = [fn(PROMPT, model) for fn, model in CALLS]
    return time.time() - started, results


def run_parallel():
    started = time.time()
    with ThreadPoolExecutor(max_workers=len(CALLS)) as executor:
        futures = [executor.submit(fn, PROMPT, model) for fn, model in CALLS]
        results = [f.result(timeout=main.FRONTIER_CHAT_TASK_TIMEOUT_SECONDS) for f in futures]
    return time.time() - started, results


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=3)
    args = parser.parse_args()

    seq_times, par_times, ok, total = [], [], 0, 0
    for round_no in range(args.rounds):
        seq_elapsed, seq_results = run_sequential()
        par_elapsed, par_results = run_parallel()
        seq_times.append(seq_elapsed)
        par_times.append(par_elapsed)
        for r in seq_results + par_results:
            total += 1
            ok += 1 if r.get('success') else 0
        print(f'round {round_no + 1}: sequential {seq_elapsed:.2f}s, parallel {par_elapsed:.2f}s')

    avg_seq = sum(seq_times) / len(seq_times)
    avg_par = sum(par_times) / len(par_times)
    print(f'\navg sequential: {avg_seq:.2f}s   avg parallel: {avg_par:.2f}s')
    print(f'latency reduction: {(1 - avg_par / avg_seq) * 100:.1f}%')
    print(f'call success rate: {ok}/{total}')


if __name__ == '__main__':
    main_cli()
