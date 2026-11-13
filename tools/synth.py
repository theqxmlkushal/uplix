"""Synthetic participants that follow the SAME schema as real ones (source = 'synthetic').

Why: you can build, test and train everything before any real user exists.
The hidden truth (who is persuadable) is NOT stored in the database; it goes to data/synthetic_truth.csv.

CLI:  python tools/synth.py --n 10000 --seed 0 [--effect none] [--reset]
Needs DATABASE_URL (see .env.example).
"""
from __future__ import annotations

import argparse
import io
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

MIN_BASKET = 300      # coupon applies from this subtotal
COUPON = 100          # discount in Round 2 for treated users


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def generate(products: pd.DataFrame, n: int = 5000, seed: int = 0, effect: str = "heterogeneous") -> dict:
    """Make n synthetic participants with Round 1 behaviour, an exact 50/50 assignment, and Round 2 outcomes.

    Hidden traits: a = engagement, b = price sensitivity, c = taste for bigger baskets.
    Round 2 order chance: logit(mu0) = -1.2 + 0.9a + 0.3c; the coupon adds delta = 1.4b - 0.8*max(a, 0)
    (price-sensitive, low-engagement users react; very engaged users are sleeping dogs). effect='none' gives delta = 0.
    Example: generate(products, n=100)['participants'] has 100 rows.
    """
    rng = np.random.default_rng(seed)
    pid = products["id"].to_numpy()
    price = products["price"].to_numpy(float)
    rel_price = price / price.mean()

    a, b, c = rng.standard_normal((3, n))
    base = -1.2 + 0.9 * a + 0.3 * c
    delta = (1.4 * b - 0.8 * np.maximum(a, 0)) if effect == "heterogeneous" else np.zeros(n)
    mu0, mu1 = sigmoid(base), sigmoid(base + delta)

    T = np.zeros(n, int)
    T[rng.permutation(n)[: n // 2]] = 1                     # exact split
    Y = (rng.random(n) < np.where(T == 1, mu1, mu0)).astype(int)

    events, orders = [], []

    def pick(i, k=1):
        w = np.exp((0.5 * c[i] - 0.9 * b[i]) * rel_price)    # price-sensitive users look at cheaper items
        return rng.choice(len(pid), size=k, p=w / w.sum())

    def add_event(i, rnd, typ, j=None, value=None, t=0):
        events.append((i, rnd, typ, None if j is None else int(pid[j]), value, t))

    for i in range(n):
        # ---- Round 1: behaviour before any coupon
        t = int(rng.integers(0, 600))
        carted = []
        for j in pick(i, int(rng.poisson(np.exp(1.2 + 0.4 * a[i])))):
            t += int(rng.integers(5, 40)); add_event(i, 1, "view_item", j, None, t)
            if rng.random() < sigmoid(-1.0 + 0.5 * a[i]):
                t += int(rng.integers(3, 20)); add_event(i, 1, "add_to_cart", j, None, t); carted.append(j)
        if carted and rng.random() < sigmoid(-0.3 + 0.9 * a[i] + 0.3 * c[i]):
            baskets = [carted[:3]]
            if rng.random() < 0.25:
                baskets.append(list(pick(i, int(rng.integers(1, 3)))))
            for k, items in enumerate(baskets):
                t += 10; add_event(i, 1, "checkout_start", None, None, t)
                sub = int(sum(price[j] for j in items)); t += 10
                add_event(i, 1, "order_placed", None, sub, t)
                orders.append((i, 1, k, [(int(pid[j]), 1, int(price[j])) for j in items], sub, 0, sub, False, t))
        # ---- Round 2: after the coupon assignment
        t = int(rng.integers(0, 600))
        views2 = pick(i, int(rng.poisson(np.exp(1.0 + 0.3 * a[i]))))
        for n_seen, j in enumerate(views2):
            t += int(rng.integers(5, 40)); add_event(i, 2, "view_item", j, None, t)
            if n_seen == 0 and T[i] == 1:
                add_event(i, 2, "coupon_seen", None, None, t + 1)
        if Y[i] == 1:
            items, sub = [], 0
            while sub < MIN_BASKET and len(items) < 4:
                j = int(pick(i, 1)[0]); items.append(j); sub += int(price[j])
                t += 8; add_event(i, 2, "add_to_cart", j, None, t)
            t += 10; add_event(i, 2, "checkout_start", None, None, t)
            disc = COUPON if (T[i] == 1 and sub >= MIN_BASKET) else 0
            t += 10; add_event(i, 2, "order_placed", None, sub - disc, t)
            orders.append((i, 2, 0, [(int(pid[j]), 1, int(price[j])) for j in items], sub, disc, sub - disc, disc > 0, t))

    e = float(T.sum() / n)
    return dict(
        n=n, products=products,
        participants=pd.DataFrame({"idx": np.arange(n), "nickname": [f"syn_{i:05d}" for i in range(n)]}),
        events=pd.DataFrame(events, columns=["idx", "round", "type", "product_id", "value", "t"]),
        orders=pd.DataFrame(orders, columns=["idx", "round", "k", "items", "subtotal", "discount", "total", "coupon_applied", "t"]),
        assignments=pd.DataFrame({"idx": np.arange(n), "t": T, "e": e, "seed": seed}),
        truth=pd.DataFrame({"idx": np.arange(n), "engagement": a, "price_sensitivity": b, "basket_taste": c,
                            "mu0": mu0, "mu1": mu1, "tau": mu1 - mu0}),
    )


def features_from_frames(d: dict) -> pd.DataFrame:
    """The same numbers as the SQL view v_features, computed in memory (used for robustness runs and tests)."""
    n = d["n"]
    price = d["products"].set_index("id")["price"]
    ev, od = d["events"], d["orders"]
    r1 = ev[ev["round"] == 1]
    views = r1[r1["type"] == "view_item"].assign(price=lambda x: x["product_id"].map(price))
    out = pd.DataFrame({"user_id": np.arange(n)}).set_index("user_id")
    out["T"] = d["assignments"].set_index("idx")["t"]
    o2 = od[od["round"] == 2].groupby("idx")
    out["Y_order_r2"] = (o2.size().reindex(out.index).fillna(0) > 0).astype(int)
    out["revenue_r2"] = o2["total"].sum().reindex(out.index).fillna(0)
    out["discount_r2"] = o2["discount"].sum().reindex(out.index).fillna(0)
    out["n_views_r1"] = views.groupby("idx").size().reindex(out.index).fillna(0)
    out["n_cart_adds_r1"] = r1[r1["type"] == "add_to_cart"].groupby("idx").size().reindex(out.index).fillna(0)
    out["avg_price_viewed_r1"] = views.groupby("idx")["price"].mean().reindex(out.index).fillna(0).round(1)
    o1 = od[od["round"] == 1].groupby("idx")
    out["n_orders_r1"] = o1.size().reindex(out.index).fillna(0)
    out["r1_spend"] = o1["total"].sum().reindex(out.index).fillna(0)
    return out.reset_index()


def write_to_db(conn, d: dict, reset: bool = True) -> None:
    """Write the generated frames into the real tables with source='synthetic' (COPY, so 10,000 users take seconds)."""
    base = datetime.now(timezone.utc) - timedelta(days=2)
    stamp = lambda rnd, t: base + timedelta(days=rnd - 1, seconds=int(t))
    with conn.cursor() as cur:
        if reset:
            cur.execute("delete from participants where source = 'synthetic'")
        with cur.copy("copy participants (nickname, source, created_at) from stdin") as cp:
            for nick in d["participants"]["nickname"]:
                cp.write_row((nick, "synthetic", base))
        cur.execute("select id, nickname from participants where source = 'synthetic'")
        pid = {nick: i for i, nick in cur.fetchall()}
        idx2pid = {i: pid[nick] for i, nick in zip(d["participants"]["idx"], d["participants"]["nickname"])}
        with cur.copy("copy assignments (participant_id, t, e, seed) from stdin") as cp:
            for r in d["assignments"].itertuples():
                cp.write_row((idx2pid[r.idx], int(r.t), float(r.e), int(r.seed)))
        with cur.copy("copy events (participant_id, round, type, product_id, value, created_at) from stdin") as cp:
            for r in d["events"].itertuples():
                cp.write_row((idx2pid[r.idx], int(r.round), r.type,
                              None if pd.isna(r.product_id) else int(r.product_id),
                              None if pd.isna(r.value) else int(r.value), stamp(r.round, r.t)))
        with cur.copy("copy orders (order_key, participant_id, round, items, subtotal, discount, total, coupon_applied, created_at) from stdin") as cp:
            for r in d["orders"].itertuples():
                items = json.dumps([{"product_id": p, "qty": q, "unit_price": u} for p, q, u in r.items])
                cp.write_row((f"syn-{idx2pid[r.idx]}-{r.round}-{r.k}", idx2pid[r.idx], int(r.round), items,
                              int(r.subtotal), int(r.discount), int(r.total), bool(r.coupon_applied), stamp(r.round, r.t)))
    conn.commit()
    d["idx2pid"] = idx2pid


def main() -> None:
    import psycopg
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--effect", choices=["heterogeneous", "none"], default="heterogeneous")
    ap.add_argument("--reset", action="store_true", help="delete existing synthetic rows first")
    args = ap.parse_args()
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, prepare_threshold=None) as conn:
        products = pd.read_sql("select id, name, category, price from products order by id", conn)
        if products.empty:
            raise SystemExit("products table is empty: run sql/seed_products.sql first")
        d = generate(products, args.n, args.seed, args.effect)
        write_to_db(conn, d, reset=args.reset)
    os.makedirs("data", exist_ok=True)
    truth = d["truth"].assign(user_id=d["truth"]["idx"].map(d["idx2pid"])).drop(columns="idx")
    truth.to_csv("data/synthetic_truth.csv", index=False)
    print(f"wrote {args.n} synthetic participants ({args.effect}), "
          f"{len(d['events'])} events, {len(d['orders'])} orders; truth in data/synthetic_truth.csv")


if __name__ == "__main__":
    main()
