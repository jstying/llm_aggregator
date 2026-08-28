"""Stage-3 concurrency benchmark: the real run_cross_peer_review() scheduler
(ThreadPoolExecutor(10) + per-reviewer lock) vs a hand-rolled sequential loop over the same
N x (N-1) review grid built from live g4f answers. Reproduces the CLAUDE.md section 12
"peer review grid concurrency" measurement."""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import _bootstrap  # noqa: F401
import main

PROMPT = 'In two sentences, why do rainbows appear after rain?'

FRONTIER_ANSWERERS = [
    ('Claude', main.call_claude_model, 'claude-haiku-4-5'),
    ('ChatGPT', main.call_chatgpt_model, 'gpt-5.4-mini'),
    ('Gemini', main.call_gemini_text_model, 'gemini-3.1-flash-lite'),
]


def collect_entries(include_frontier=False):
    """One live pooled compare round to get real answers, shaped as
    run_cross_peer_review() entries. With --frontier, the three frontier SDKs answer too
    (spends real API credit) so the grid spans both worlds like a real compare click."""
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(main.test_g4f_provider, p, PROMPT, None) for p in main.G4F_PROVIDERS]
        for future in futures:
            try:
                results.append(future.result(timeout=21))
            except Exception:
                pass
    entries = [
        {'kind': 'g4f', 'provider': r['provider'], 'model': r['model'],
         'response': r['response'], 'user_api_key': None}
        for r in results if r.get('success')
    ]
    if include_frontier:
        for kind, call_fn, model_key in FRONTIER_ANSWERERS:
            r = call_fn(PROMPT, model_key)
            if r.get('success'):
                entries.append({'kind': kind, 'provider': r['provider'], 'model': model_key,
                                'response': r['response'], 'user_api_key': None})
    return entries


def build_grid(entries):
    """The same ordered (reviewer, review_prompt, target) tasks run_cross_peer_review()
    builds internally."""
    tasks = []
    for target in entries:
        for reviewer in entries:
            if reviewer['provider'] == target['provider']:
                continue
            judge_prefix = main.PEER_REVIEW_PROMPTS_MAP.get(
                reviewer['model'],
                'Please evaluate the quality of the following answer, noting its strengths and weaknesses.'
            )
            review_prompt = f"{judge_prefix}\n\nHere is the anonymous text to review:\n{target['response']}"
            tasks.append((reviewer, review_prompt, target['provider']))
    return tasks


def run_sequential_grid(entries):
    """The same per-task dispatch run_cross_peer_review() uses (g4f vs frontier reviewer),
    minus the thread pool and the per-reviewer locks."""
    g4f_provider_obj_map = {p.__name__: p for p in main.G4F_PROVIDERS}
    tasks = build_grid(entries)
    started = time.time()
    landed = 0
    for reviewer, review_prompt, _target in tasks:
        if reviewer['kind'] == 'g4f':
            provider_obj = g4f_provider_obj_map[reviewer['provider']]
            review = main.run_peer_review(provider_obj, reviewer['model'], review_prompt)
        else:
            review = main.run_frontier_peer_review(reviewer['kind'], reviewer['model'], review_prompt)
        if review is not None:
            landed += 1
    return time.time() - started, landed, len(tasks)


def run_parallel_grid(entries):
    started = time.time()
    reviews = main.run_cross_peer_review(entries)
    landed = sum(len(v) for v in reviews.values())
    total = len(entries) * (len(entries) - 1)
    return time.time() - started, landed, total


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--frontier', action='store_true',
                        help='include the three frontier SDKs as answerers/reviewers (spends real API credit)')
    args = parser.parse_args()

    seq_times, par_times = [], []
    landed_all, total_all = 0, 0
    for round_no in range(args.rounds):
        entries = collect_entries(include_frontier=args.frontier)
        if len(entries) < 2:
            print(f'round {round_no + 1}: only {len(entries)} successful answers, skipping')
            continue
        seq_elapsed, seq_landed, seq_total = run_sequential_grid(entries)
        par_elapsed, par_landed, par_total = run_parallel_grid(entries)
        seq_times.append(seq_elapsed)
        par_times.append(par_elapsed)
        landed_all += seq_landed + par_landed
        total_all += seq_total + par_total
        print(f'round {round_no + 1}: N={len(entries)}, grid={seq_total} tasks, '
              f'sequential {seq_elapsed:.2f}s ({seq_landed}/{seq_total}), '
              f'parallel {par_elapsed:.2f}s ({par_landed}/{par_total})')

    if seq_times:
        avg_seq = sum(seq_times) / len(seq_times)
        avg_par = sum(par_times) / len(par_times)
        print(f'\navg sequential: {avg_seq:.2f}s   avg parallel: {avg_par:.2f}s')
        print(f'latency reduction: {(1 - avg_par / avg_seq) * 100:.1f}%')
        print(f'reviews landed: {landed_all}/{total_all}')


if __name__ == '__main__':
    main_cli()
