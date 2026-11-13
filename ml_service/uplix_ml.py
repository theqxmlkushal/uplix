"""Serving-safe ML core: numpy only (no pandas, no scikit-learn), so it fits a small Vercel bundle.

The SAME `engineer` function is used when training and when serving, which removes train/serve skew.
Model = a simple T-learner built from logistic regressions:
  traditional : P(order in Round 2 | Round 1 behaviour)            (never sees the coupon)
  control     : P(order | behaviour, no coupon)    -> mu0
  treated     : P(order | behaviour, coupon)       -> mu1
  uplift = mu1 - mu0 (the estimated change the coupon causes)
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

RAW = ["n_views_r1", "n_cart_adds_r1", "avg_price_viewed_r1", "n_orders_r1", "r1_spend"]
ENGINEERED = ["log_views", "cart_rate", "log_cart", "avg_price_k", "has_order", "n_orders", "aov_k", "log_spend", "no_views"]


def engineer(raw: np.ndarray) -> np.ndarray:
    """(n, 5) raw Round 1 numbers in RAW order -> (n, 9) model features.

    Rules: logs tame big counts; cart_rate = cart adds / views; avg_price_k and aov_k are in thousands of rupees.
    Example: engineer(np.array([[4, 2, 500, 1, 900]]))[0, 1] == 0.5 (2 cart adds out of 4 views).
    """
    raw = np.atleast_2d(np.asarray(raw, float))
    views, carts, avg_price, orders, spend = raw.T
    return np.column_stack([
        np.log1p(views),
        carts / np.clip(views, 1, None),
        np.log1p(carts),
        avg_price / 1000.0,
        (orders > 0).astype(float),
        orders,
        (spend / np.clip(orders, 1, None)) / 1000.0,
        np.log1p(spend),
        (views == 0).astype(float),
    ])


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


class UpliftModel:
    """Loads the JSON written by `to_spec` and predicts with numpy only."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.mean = np.array(spec["scaler"]["mean"], float)
        self.scale = np.array(spec["scaler"]["scale"], float)
        self.heads = {k: (np.array(v["coef"], float), float(v["intercept"])) for k, v in spec["heads"].items()}
        self.m = float(spec["economics"]["m"])
        self.d = float(spec["economics"]["d"])
        self.c = float(spec["economics"].get("c", 0.0))

    def _p(self, head: str, Xs: np.ndarray) -> np.ndarray:
        coef, b = self.heads[head]
        return _sigmoid(Xs @ coef + b)

    def predict(self, raw: np.ndarray) -> dict:
        """Raw Round 1 numbers (n, 5) -> dict of arrays: p_buy, mu0, mu1, uplift, gain, lane."""
        Xs = (engineer(raw) - self.mean) / self.scale
        mu0, mu1 = self._p("control", Xs), self._p("treated", Xs)
        tau = mu1 - mu0
        gain = self.m * tau - self.d * mu1 - self.c             # money gained by giving this person the coupon
        lane = np.where(gain > 0, "Treat", np.where(tau < 0, "Do not disturb", "Skip"))
        return dict(p_buy=self._p("traditional", Xs), mu0=mu0, mu1=mu1, uplift=tau, gain=gain, lane=lane)


def to_spec(scaler, traditional, control, treated, *, economics: dict, meta: dict) -> dict:
    """Turn fitted scikit-learn objects (StandardScaler + 3 LogisticRegression) into a small JSON-able dict."""
    head = lambda m: dict(coef=[float(x) for x in m.coef_[0]], intercept=float(m.intercept_[0]))
    return dict(
        version=1, trained_at=datetime.now(timezone.utc).isoformat(),
        raw=RAW, engineered=ENGINEERED,
        scaler=dict(mean=[float(x) for x in scaler.mean_], scale=[float(x) for x in scaler.scale_]),
        heads=dict(traditional=head(traditional), control=head(control), treated=head(treated)),
        economics=economics, **meta,
    )
