# Benchmarks

The measurement scripts behind the numbers in CLAUDE.md section 12. They exercise
`main.py`'s real functions directly — the two `*_concurrency` scripts and the peer-review
grid make live network calls (the frontier one spends real API credit, pennies per run);
the ledger, classifier, and truncation scripts are pure in-memory and instant.

These are point-in-time measurement tools, not a CI regression gate: live-provider numbers
drift with provider load, and reruns will not reproduce a past run exactly. Run from the
repo root with the virtualenv active, e.g.:

```
python benchmarks/benchmark_compare_concurrency.py --rounds 5
python benchmarks/benchmark_peer_review_grid.py --rounds 3
python benchmarks/benchmark_frontier_stage_concurrency.py --rounds 3
python benchmarks/benchmark_refund_ledger.py
python benchmarks/benchmark_error_classifiers.py
python benchmarks/benchmark_truncation_guard.py
```
