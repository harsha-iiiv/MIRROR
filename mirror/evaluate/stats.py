"""Paired statistics for original vs corrected specification trials."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant counts.

    ``b`` = pairs where only condition A succeeded, ``c`` = only B succeeded.
    Under H0 the discordant pairs split Binomial(b + c, 0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2.0 ** n
    return min(1.0, 2.0 * tail)


def wilson_ci(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci(values: Sequence[float], *, n_boot: int = 10_000, alpha: float = 0.05,
                 seed: int = 0, stat=np.mean) -> tuple[float, float]:
    """Percentile bootstrap CI of ``stat`` over per-seed values (resampling seeds)."""
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, x.size, size=(n_boot, x.size))
    boots = stat(x[idx], axis=1)
    return (float(np.quantile(boots, alpha / 2)), float(np.quantile(boots, 1 - alpha / 2)))


def paired_binary(a: Sequence[bool], b: Sequence[bool], *, n_boot: int = 10_000,
                  seed: int = 0) -> dict:
    """Paired comparison of a binary outcome over the same seeds."""
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    if a.shape != b.shape:
        raise ValueError("paired outcomes must have equal length")
    n = int(a.size)
    both = int((a & b).sum())
    only_a = int((a & ~b).sum())
    only_b = int((~a & b).sum())
    neither = int((~a & ~b).sum())
    diff = b.astype(float) - a.astype(float)
    out = dict(n=n, both=both, only_a=only_a, only_b=only_b, neither=neither,
               rate_a=float(a.mean()) if n else float("nan"),
               rate_b=float(b.mean()) if n else float("nan"),
               rate_a_ci95=list(wilson_ci(int(a.sum()), n)),
               rate_b_ci95=list(wilson_ci(int(b.sum()), n)),
               diff=float(diff.mean()) if n else float("nan"),
               diff_ci95_bootstrap=list(bootstrap_ci(diff, n_boot=n_boot, seed=seed)),
               mcnemar_p_exact=mcnemar_exact(only_a, only_b))
    out["power_note"] = power_note(only_a, only_b, n)
    return out


def power_note(only_a: int, only_b: int, n: int) -> str:
    """Plain statement of what the sample can and cannot show."""
    disc = only_a + only_b
    if n == 0:
        return "no trials"
    # smallest number of discordant pairs, all in one direction, that reaches p < 0.05
    need = next(k for k in range(1, 64) if mcnemar_exact(0, k) < 0.05)
    if disc < need:
        return (f"{disc} discordant pair(s) of {n}; exact McNemar needs at least {need} discordant "
                f"pairs all in one direction to reach p < 0.05, so this sample cannot establish "
                f"significance either way")
    return f"{disc} discordant pair(s) of {n}"


def paired_continuous(a: Sequence[float], b: Sequence[float], *, n_boot: int = 10_000,
                      seed: int = 0) -> dict:
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    d = b - a
    return dict(n=int(d.size), mean_a=float(np.mean(a)) if a.size else float("nan"),
                mean_b=float(np.mean(b)) if b.size else float("nan"),
                mean_diff=float(np.mean(d)) if d.size else float("nan"),
                mean_diff_ci95_bootstrap=list(bootstrap_ci(d, n_boot=n_boot, seed=seed)))
