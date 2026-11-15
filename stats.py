"""Statistics for the real experiment (README section 10).

Four things live here, all numpy-only and small enough to stay in Flask:

  * exact_randomization_test - re-runs every possible split of the same people and counts how
    often a gap this big appears by chance (252 splits for 5 and 5, exactly as the brief says)
  * bootstrap_interval       - a rough range for the effect, from resampling
  * verdict                  - "reliable" only with 40+ customers, exact p < 0.05 and an
    interval that excludes zero; otherwise "inconclusive" with the reasons
  * power_n                  - customers needed per group for a given baseline and target rate

Golden check (README section 10): treated orders 1,1,1,0,1 versus control 1,0,0,1,0 gives an
effect of 0.4, an exact p of 0.5238 over 252 splits and an interval of -0.2 to 1.0.
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np

# Fixed table from the brief: (baseline rate, target rate, customers needed per group).
POWER_ROWS = [
    (0.20, 0.30, 291),
    (0.20, 0.40, 79),
    (0.30, 0.50, 91),
    (0.30, 0.60, 40),
    (0.40, 0.80, 20),
    (0.50, 0.80, 36),
]

# More splits than this and enumerating every one becomes slow, so we draw random splits instead.
EXACT_SPLIT_LIMIT = 50_000
MC_DRAWS = 20_000
BOOTSTRAP_REPS = 2_000
Z_ALPHA, Z_POWER = 1.959963985, 0.8416212336      # two-sided 5%, 80% power


def _arrays(treated, control):
    return np.asarray(treated, float), np.asarray(control, float)


def exact_randomization_test(treated, control, seed: int = 0) -> float:
    """Two-sided p-value for "the groups differ by this much or more" under random allocation.

    Enumerates every split when there are not too many (252 for 5 vs 5). With more people it
    draws MC_DRAWS seeded random splits, so the same data always gives the same p.
    """
    t, c = _arrays(treated, control)
    n1, n0 = len(t), len(c)
    if n1 == 0 or n0 == 0:
        return 1.0
    observed = float(t.mean() - c.mean())
    pool = np.concatenate([t, c])
    n = n1 + n0
    reach = abs(observed) - 1e-12                      # float-safe "at least this big"

    if math.comb(n, n1) <= EXACT_SPLIT_LIMIT:
        hits = total = 0
        for pick in combinations(range(n), n1):
            mask = np.zeros(n, dtype=bool)
            mask[list(pick)] = True
            total += 1
            if abs(pool[mask].mean() - pool[~mask].mean()) >= reach:
                hits += 1
        return hits / total

    rng = np.random.default_rng(seed)
    # Binary outcomes (the order rate) are the normal case. Under random allocation the number
    # of successes in the treated group is hypergeometric, so draw those directly: O(draws)
    # memory instead of the draws x n matrix (which would be gigabytes at 10,000 customers).
    if np.all((pool == 0) | (pool == 1)):
        successes = int(pool.sum())
        treated_hits = rng.hypergeometric(successes, n - successes, n1, size=MC_DRAWS)
        diffs = treated_hits / n1 - (successes - treated_hits) / n0
        return float((np.abs(diffs) >= reach).mean())

    # Continuous outcomes: shuffle in batches so the same data always gives the same p.
    chunk = max(1, min(MC_DRAWS, 2_000_000 // n))
    hits = done = 0
    while done < MC_DRAWS:
        size = min(chunk, MC_DRAWS - done)
        order = rng.random((size, n)).argsort(axis=1)
        diffs = pool[order[:, :n1]].mean(axis=1) - pool[order[:, n1:]].mean(axis=1)
        hits += int((np.abs(diffs) >= reach).sum())
        done += size
    return hits / MC_DRAWS


def bootstrap_interval(treated, control, reps: int = BOOTSTRAP_REPS, seed: int = 0):
    """Percentile bootstrap interval for the difference in means (2.5% to 97.5%)."""
    t, c = _arrays(treated, control)
    if len(t) == 0 or len(c) == 0:
        return None, None
    rng = np.random.default_rng(seed)
    diffs = np.empty(reps)
    for i in range(reps):
        diffs[i] = rng.choice(t, len(t), replace=True).mean() - rng.choice(c, len(c), replace=True).mean()
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(lo), float(hi)


def verdict(n_customers: int, p_value: float, lo: float, hi: float) -> dict:
    """Reliable only when all three hold; otherwise inconclusive and say exactly why."""
    reasons = []
    if n_customers < 40:
        reasons.append(f"only {n_customers} customers; 40 or more are needed")
    if p_value >= 0.05:
        reasons.append(f"exact p = {p_value:.4f}; must be below 0.05")
    if lo is not None and not (lo > 0 or hi < 0):
        reasons.append("the interval includes zero")
    if reasons:
        return {"verdict": "inconclusive", "reasons": reasons}
    return {"verdict": "reliable", "reasons": []}


def power_n(baseline: float, target: float) -> int:
    """Customers per group to detect baseline -> target (two-sided 5%, 80% power).

    The brief's table comes out of exactly this formula: 20% -> 30% needs 291, 20% -> 40%
    needs 79, and so on.
    """
    if not (0 < baseline < 1) or not (0 < target < 1) or baseline == target:
        return 0
    gap = abs(target - baseline)
    variance = baseline * (1 - baseline) + target * (1 - target)
    return math.ceil((Z_ALPHA + Z_POWER) ** 2 * variance / gap ** 2)


def coupon_effect(treated_outcomes, control_outcomes, treated_revenue=None, control_revenue=None) -> dict:
    """Order-rate effect with exact p and bootstrap interval, plus the revenue effect."""
    t, c = _arrays(treated_outcomes, control_outcomes)
    n1, n0 = len(t), len(c)
    rate1 = float(t.mean()) if n1 else 0.0
    rate0 = float(c.mean()) if n0 else 0.0
    effect = rate1 - rate0
    p = exact_randomization_test(t, c) if n1 and n0 else 1.0
    lo, hi = bootstrap_interval(t, c) if n1 and n0 else (None, None)
    out = {
        "n_treated": n1, "n_control": n0, "n": n1 + n0,
        "rate_treated": rate1, "rate_control": rate0,
        "effect": effect, "p_value": p, "lo": lo, "hi": hi,
        **verdict(n1 + n0, p, lo, hi),
    }
    if treated_revenue is not None and control_revenue is not None and n1 and n0:
        r1, r0 = _arrays(treated_revenue, control_revenue)
        rlo, rhi = bootstrap_interval(r1, r0)
        out["revenue"] = {
            "treated": float(r1.mean()), "control": float(r0.mean()),
            "effect": float(r1.mean() - r0.mean()), "lo": rlo, "hi": rhi,
        }
    return out
