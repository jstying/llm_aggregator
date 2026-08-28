"""Stage-1 concurrency benchmark: sequential vs ThreadPoolExecutor(max_workers=5) over the
live G4F_PROVIDERS list, using the real test_g4f_provider() with live network calls.
Reproduces the CLAUDE.md section 12 "comparison stage concurrency" measurement."""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import _bootstrap  # noqa: F401
import main

PROMPT = 'In two sentences, what is the difference between a process and a thread?'


def run_sequential():
    started = time.time()
    results = [main.test_g4f_provider(p, PROMPT, None) for p in main.G4F_PROVIDERS]
    return time.time() - started, results


def run_parallel():
    started = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=min(5, len(main.G4F_PROVIDERS))) as executor:
        futures = [executor.submit(main.test_g4f_provider, p, PROMPT, None) for p in main.G4F_PROVIDERS]
        for future in futures:
            try:
                results.append(future.result(timeout=21))
            except Exception as e:
                results.append({'success': False, 'error': str(e)})
    return time.time() - started, results


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=5)
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
    print(f'\nproviders: {[p.__name__ for p in main.G4F_PROVIDERS]}')
    print(f'avg sequential: {avg_seq:.2f}s   avg parallel: {avg_par:.2f}s')
    print(f'latency reduction: {(1 - avg_par / avg_seq) * 100:.1f}%')
    print(f'call success rate: {ok}/{total}')


if __name__ == '__main__':
    main_cli()
