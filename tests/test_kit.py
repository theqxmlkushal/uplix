"""Kit tests. They need a throwaway PostgreSQL database: set TEST_DATABASE_URL (never your Supabase project)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
psycopg = pytest.importorskip("psycopg")
URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to a throwaway database")

from tools.synth import features_from_frames, generate, write_to_db  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(URL, prepare_threshold=None) as c:
        with c.cursor() as cur:
            cur.execute("drop schema public cascade; create schema public;")
            for f in ("schema.sql", "seed_products.sql"):
                cur.execute(open(os.path.join(ROOT, "sql", f), encoding="utf-8").read())
        c.commit()
        yield c


@pytest.fixture(scope="module")
def products(conn):
    with conn.cursor() as cur:
        cur.execute("select id, name, category, price from products order by id")
        return pd.DataFrame(cur.fetchall(), columns=["id", "name", "category", "price"])


def test_schema_has_expected_tables_and_rls(conn):
    with conn.cursor() as cur:
        cur.execute("select relname, relrowsecurity from pg_class where relkind='r' and relnamespace='public'::regnamespace")
        rows = dict(cur.fetchall())
    for t in ["participants", "products", "app_state", "events", "orders", "assignments", "model_runs"]:
        assert rows[t] is True, f"RLS is off on {t}"


def test_exact_split_and_known_e(products):
    d = generate(products, n=101, seed=1)
    t = d["assignments"]["t"]
    assert t.sum() == 50 and (d["assignments"]["e"] == 50 / 101).all()


def test_same_seed_same_data(products):
    a, b = generate(products, n=200, seed=3), generate(products, n=200, seed=3)
    pd.testing.assert_frame_equal(a["events"], b["events"])
    assert not a["assignments"]["t"].equals(generate(products, n=200, seed=4)["assignments"]["t"])


def test_participants_have_pin_hash_not_pin(conn):
    with conn.cursor() as cur:
        cur.execute("select column_name from information_schema.columns where table_name='participants'")
        cols = {r[0] for r in cur.fetchall()}
    assert "pin_hash" in cols and "pin" not in cols and "email" not in cols and "phone" not in cols


def test_no_truth_in_database_columns(conn):
    with conn.cursor() as cur:
        cur.execute("select column_name from information_schema.columns where table_schema='public'")
        cols = {r[0] for r in cur.fetchall()}
    assert not {"tau", "mu0", "mu1", "price_sensitivity", "engagement"} & cols


def test_null_world_has_zero_effect(products):
    d = generate(products, n=300, seed=2, effect="none")
    assert np.allclose(d["truth"]["tau"], 0)


def test_coupon_only_on_treated_round2_orders(products):
    d = generate(products, n=1500, seed=5)
    od = d["orders"].merge(d["assignments"][["idx", "t"]].rename(columns={"t": "treated"}), on="idx")
    assert (od.loc[od["round"] == 1, "discount"] == 0).all()
    assert (od.loc[(od["round"] == 2) & (od["treated"] == 0), "discount"] == 0).all()
    assert (od.loc[(od["round"] == 2) & (od["treated"] == 1), "discount"] == 100).all()


def test_sql_view_matches_in_memory_features(conn, products):
    d = generate(products, n=400, seed=7)
    write_to_db(conn, d, reset=True)
    mem = features_from_frames(d).assign(user_id=lambda x: x["user_id"].map(d["idx2pid"])).set_index("user_id").sort_index()
    with conn.cursor() as cur:
        cur.execute('select user_id, "T", "Y_order_r2", revenue_r2, discount_r2, n_views_r1, n_cart_adds_r1, '
                    "avg_price_viewed_r1, n_orders_r1, r1_spend from v_features where source='synthetic'")
        sql = pd.DataFrame(cur.fetchall(), columns=["user_id", "T", "Y_order_r2", "revenue_r2", "discount_r2", "n_views_r1",
                                                     "n_cart_adds_r1", "avg_price_viewed_r1", "n_orders_r1", "r1_spend"]).set_index("user_id").sort_index()
    assert list(sql.index) == list(mem.index)
    for col in sql.columns:
        # Postgres rounds .25 up and numpy rounds it to even, so the one-decimal average price can differ by 0.1
        tol = 0.11 if col == "avg_price_viewed_r1" else 1e-9
        assert np.allclose(sql[col].astype(float), mem[col].astype(float), atol=tol), col


def test_reset_replaces_synthetic_rows(conn, products):
    write_to_db(conn, generate(products, n=50, seed=1), reset=True)
    write_to_db(conn, generate(products, n=60, seed=2), reset=True)
    with conn.cursor() as cur:
        cur.execute("select count(*) from participants where source='synthetic'")
        assert cur.fetchone()[0] == 60


def test_one_order_key_per_order(conn):
    with conn.cursor() as cur:
        cur.execute("select count(*), count(distinct order_key) from orders")
        a, b = cur.fetchone()
    assert a == b
