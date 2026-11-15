"""Customer types (tags) from Round 1 behaviour only - README section 10.

Plain rules, applied in this order:

  1. Regular        2 or more orders in Round 1
  2. Big basket     at least one order, and an average order value at or above the median
                    among customers who ordered
  3. Window shopper 5 or more views and no order
  4. Newcomer       everyone else

Tags describe past behaviour. They do NOT say who is persuadable; only the experiment can.
"""
from __future__ import annotations

import statistics

TAGS = ("Regular", "Big basket", "Window shopper", "Newcomer")


def classify(rows) -> dict:
    """Map user_id -> tag for rows with n_views_r1 / n_orders_r1 / r1_spend.

    The median for "Big basket" is taken among customers who ordered (README section 10), and
    every rule looks at Round 1 only, so the tag can never leak the coupon's effect.
    """
    rows = list(rows)
    averages = [r["r1_spend"] / r["n_orders_r1"] for r in rows if (r["n_orders_r1"] or 0) > 0]
    median = statistics.median(averages) if averages else 0.0

    out = {}
    for r in rows:
        orders = int(r["n_orders_r1"] or 0)
        views = int(r["n_views_r1"] or 0)
        if orders >= 2:
            out[r["user_id"]] = "Regular"
        elif orders == 1 and (r["r1_spend"] / orders) >= median:
            out[r["user_id"]] = "Big basket"
        elif orders == 0 and views >= 5:
            out[r["user_id"]] = "Window shopper"
        else:
            out[r["user_id"]] = "Newcomer"
    return out


def counts(tags: dict) -> list:
    """Tag counts in the brief's order, ready for the doughnut chart."""
    tally = {name: 0 for name in TAGS}
    for tag in tags.values():
        tally[tag] = tally.get(tag, 0) + 1
    return [{"tag": name, "n": tally.get(name, 0)} for name in TAGS]
