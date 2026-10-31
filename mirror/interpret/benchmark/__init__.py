"""Correction-compiler benchmark: frozen cases (cases.json), base specification
(base_spec.json) and scoring. Runner: scripts/bench_corrections.py."""
from mirror.interpret.benchmark.score import (BENCH_DIR, load_base_spec, load_cases, run_benchmark,
                                              score_case, summarize)

__all__ = ["BENCH_DIR", "load_cases", "load_base_spec", "run_benchmark", "score_case", "summarize"]
