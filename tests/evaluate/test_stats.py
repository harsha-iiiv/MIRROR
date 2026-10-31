import math

import numpy as np
import pytest

from mirror.evaluate.stats import (bootstrap_ci, mcnemar_exact, paired_binary,
                                   paired_continuous, wilson_ci)


def test_mcnemar_exact_values():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert mcnemar_exact(1, 5) == pytest.approx(2 * 7 / 64)
    assert mcnemar_exact(5, 1) == mcnemar_exact(1, 5)
    assert mcnemar_exact(3, 3) == 1.0


def test_mcnemar_matches_scipy_binomtest():
    from scipy.stats import binomtest
    for b, c in [(0, 9), (2, 10), (4, 7), (12, 3)]:
        assert mcnemar_exact(b, c) == pytest.approx(binomtest(min(b, c), b + c, 0.5).pvalue)


def test_wilson_interval():
    lo, hi = wilson_ci(6, 6)
    assert hi == pytest.approx(1.0) and 0.55 < lo < 0.62
    assert wilson_ci(0, 0) == (0.0, 1.0)


def test_bootstrap_deterministic_and_covers_mean():
    x = np.r_[np.ones(7), np.zeros(3)]
    a = bootstrap_ci(x, seed=1)
    assert a == bootstrap_ci(x, seed=1)
    assert a[0] <= 0.7 <= a[1]


def test_paired_binary_counts_and_power_note():
    r = paired_binary([False] * 4 + [True] * 2, [True] * 6)
    assert (r["both"], r["only_a"], r["only_b"], r["neither"]) == (2, 0, 4, 0)
    assert r["mcnemar_p_exact"] == pytest.approx(2 / 16)
    assert "cannot establish significance" in r["power_note"]
    with pytest.raises(ValueError):
        paired_binary([True], [True, False])


def test_paired_continuous():
    r = paired_continuous([8, 8, 8], [0, 0, 1])
    assert r["mean_diff"] == pytest.approx(-23 / 3)
