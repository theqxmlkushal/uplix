"""Member B acceptance tests - README section 10, checks B1 to B5.

They use the same throwaway local PostgreSQL database as the shop tests and never Supabase:

    $env:TEST_DATABASE_URL = "postgresql://uplix:testpass@127.0.0.1:55432/uplix_test"
    python -m pytest -q

The schema fixture drops and recreates the public schema, and refuses any non-local host
(unless ALLOW_REMOTE_TEST_DB=1), so a real database cannot be wiped by accident.
"""
import csv
import io
import json
import math
import os
import sys
from urllib.parse import urlparse

import psycopg
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # app.py, shop.py, admin.py, sql/
sys.path.insert(0, ROOT)
from db import execute, query                    # noqa: E402

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to a throwaway database")

ADMIN_ID = "admin-test"
ADMIN_PASSWORD = "admin-test-password"
SAMPLE = os.path.join(ROOT, "sample_data", "model_run_synthetic.json")


# --------------------------------------------------------------------------- fixtures

def _local(url):
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


@pytest.fixture(scope="session")
def schema():
    if not _local(URL) and os.environ.get("ALLOW_REMOTE_TEST_DB") != "1":
        pytest.fail("TEST_DATABASE_URL is not local - refusing to drop a remote database")
    conn = psycopg.connect(URL, autocommit=True, connect_timeout=10)
    conn.execute("drop schema if exists public cascade")
    conn.execute("create schema public")
    for name in ("schema.sql", "seed_products.sql"):
        with open(os.path.join(ROOT, "sql", name), encoding="utf-8") as handle:
            conn.execute(handle.read())
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def clean(schema):
    """Every test starts from an empty real/synthetic dataset and SETUP."""
    os.environ["DATABASE_URL"] = URL
    os.environ["ADMIN_ID"] = ADMIN_ID
    os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    os.environ["ADMIN_BLOCK_SECONDS"] = "1"
    os.environ.pop("ML_API_URL", None)
    execute("truncate participants restart identity cascade")
    execute("truncate model_runs restart identity")
    execute("update app_state set state = 'SETUP', randomized = false, seed = null, "
            "coupon_amount = 100, coupon_min_subtotal = 300 where id = 1")
    yield


@pytest.fixture()
def flask_app(schema):
    saved = {name: sys.modules.get(name) for name in ("app", "shop", "admin")}
    for name in ("app", "shop", "admin"):
        sys.modules.pop(name, None)
    from app import app as flask_app_object
    flask_app_object.config["TESTING"] = True
    yield flask_app_object
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def admin_client(client):
    response = client.post("/admin/login",
                           data={"admin_id": ADMIN_ID, "password": ADMIN_PASSWORD})
    assert response.status_code == 302
    return client


@pytest.fixture()
def admin_module(flask_app):
    return sys.modules["admin"]


# --------------------------------------------------------------------------- helpers

def state():
    return query("select state, randomized, seed from app_state where id = 1", one=True)


def set_state(value, randomized=False, seed=None):
    execute("update app_state set state = %s, randomized = %s, seed = %s where id = 1",
            (value, randomized, seed))


def add_people(n, source="real", prefix="person", excluded=False):
    ids = []
    with psycopg.connect(URL) as conn:
        for i in range(n):
            row = conn.execute(
                "insert into participants (nickname, source, excluded) values (%s, %s, %s) "
                "returning id", (f"{prefix}{i + 1}", source, excluded)).fetchone()
            ids.append(row[0])
    return ids


def assign(participant_id, t, e=0.5, seed=1):
    execute("insert into assignments (participant_id, t, e, seed) values (%s, %s, %s, %s)",
            (participant_id, t, e, seed))


def add_order(participant_id, round_no, total=500, discount=0, coupon=False):
    execute(
        "insert into orders (order_key, participant_id, round, items, subtotal, discount, total, "
        "coupon_applied) values (%s, %s, %s, '[]'::jsonb, %s, %s, %s, %s)",
        (f"key-{participant_id}-{round_no}-{total}", participant_id, round_no,
         total, discount, total - discount, coupon))


def signin_shop(client, participant_id):
    with client.session_transaction() as sess:
        sess["participant_id"] = participant_id


def html(response):
    return response.get_data(as_text=True)


def flash_errors(text):
    return " ".join(line for line in text.split("<") if line.strip())


# --------------------------------------------------------------------------- B1: auth

def test_admin_requires_login(client):
    for path in ("/admin/", "/admin/customers", "/admin/orders", "/admin/experiment",
                 "/admin/results", "/admin/model", "/admin/data", "/admin/export.csv",
                 "/admin/?source=synthetic"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert "/admin/login" in response.headers["Location"], path
    assert client.post("/admin/api/score/1").status_code == 401
    assert client.post("/admin/data/reset").status_code == 302


def test_customer_session_can_never_open_admin(client):
    (pid,) = add_people(1)
    signin_shop(client, pid)
    assert client.get("/").status_code == 200

    assert client.get("/admin/").status_code == 302
    assert client.get("/admin/experiment").status_code == 302
    assert client.post("/admin/experiment/randomize").status_code == 302
    assert client.post("/admin/api/score/1").status_code == 401
    assert client.post("/admin/data/reset").status_code == 302

    assert state()["randomized"] is False
    assert query("select count(*) as n from assignments", one=True)["n"] == 0


def test_shop_pages_never_link_to_admin(client):
    (pid,) = add_people(1)
    signin_shop(client, pid)
    add_order(pid, 1)
    for path in ("/", "/product/1", "/cart", "/orders", "/signup", "/signin"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "/admin" not in html(response).lower(), path


def test_login_compares_id_and_password_in_constant_time(admin_module):
    assert admin_module.credentials_ok(ADMIN_ID, ADMIN_PASSWORD) is True
    assert admin_module.credentials_ok("wrong", ADMIN_PASSWORD) is False
    assert admin_module.credentials_ok(ADMIN_ID, "wrong") is False
    assert admin_module.credentials_ok("", "") is False

    seen = []
    real = admin_module.hmac.compare_digest

    def spy(candidate, expected):
        seen.append((candidate, expected))
        return real(candidate, expected)

    admin_module.hmac.compare_digest = spy
    try:
        assert admin_module.credentials_ok(ADMIN_ID, ADMIN_PASSWORD) is True
    finally:
        admin_module.hmac.compare_digest = real
    assert len(seen) == 2, "both halves must be compared"
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in seen)


def test_login_needs_both_the_admin_id_and_password(client):
    wrong_id = client.post("/admin/login",
                           data={"admin_id": "root", "password": ADMIN_PASSWORD})
    wrong_password = client.post("/admin/login",
                                 data={"admin_id": ADMIN_ID, "password": "nope"})
    assert wrong_id.status_code == wrong_password.status_code == 401
    assert "Wrong account ID or password" in html(wrong_id)
    assert "Wrong account ID or password" in html(wrong_password)

    with client.session_transaction() as sess:
        sess.pop("admin_fails", None)
    correct = client.post("/admin/login",
                          data={"admin_id": ADMIN_ID, "password": ADMIN_PASSWORD})
    assert correct.status_code == 302


def test_login_needs_a_configured_id_and_password(client):
    os.environ["ADMIN_PASSWORD"] = ""
    response = client.post("/admin/login", data={"admin_id": ADMIN_ID, "password": ""})
    assert response.status_code == 401

    os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    os.environ["ADMIN_ID"] = ""
    response = client.post("/admin/login", data={"admin_id": "", "password": ADMIN_PASSWORD})
    assert response.status_code == 401


def test_login_throttles_after_five_wrong_tries(client):
    for _ in range(4):
        assert client.post("/admin/login",
                           data={"admin_id": ADMIN_ID, "password": "wrong"}).status_code == 401
    assert client.post("/admin/login",
                       data={"admin_id": ADMIN_ID, "password": "wrong"}).status_code == 429

    blocked = client.post("/admin/login",
                          data={"admin_id": ADMIN_ID, "password": ADMIN_PASSWORD})
    assert blocked.status_code == 429
    assert "Wait" in html(blocked)
    assert client.get("/admin/").status_code == 302

    with client.session_transaction() as sess:
        sess["admin_blocked_until"] = 0
    assert client.post("/admin/login",
                       data={"admin_id": ADMIN_ID, "password": ADMIN_PASSWORD}).status_code == 302


def test_login_redirects_only_within_admin(client):
    response = client.post("/admin/login",
                           data={"admin_id": ADMIN_ID, "password": ADMIN_PASSWORD,
                                 "next": "/admin/data"})
    assert response.headers["Location"].endswith("/admin/data")

    with client.session_transaction() as sess:
        sess.pop("is_admin", None)
    response = client.post("/admin/login",
                           data={"admin_id": ADMIN_ID, "password": ADMIN_PASSWORD,
                                 "next": "https://evil.example"})
    assert response.headers["Location"].endswith("/admin/")


def test_neutral_login_page_offers_both_roles(client):
    response = client.get("/login")
    assert response.status_code == 200
    body = html(response)
    assert 'href="/signin"' in body
    assert "/admin/login" in body
    assert "customer" in body.lower() and "admin" in body.lower()

    signin = html(client.get("/signin"))
    assert 'href="/login"' in signin
    assert "/admin" not in signin.lower()


def test_logout_leaves_the_shop_session_alone(client):
    (pid,) = add_people(1)
    signin_shop(client, pid)
    client.post("/admin/login", data={"admin_id": ADMIN_ID, "password": ADMIN_PASSWORD})
    assert client.get("/admin/").status_code == 200

    client.post("/admin/logout")
    assert client.get("/admin/").status_code == 302
    with client.session_transaction() as sess:
        assert sess["participant_id"] == pid
    assert client.get("/orders").status_code == 200


def test_overview_and_sidebar_render(admin_client):
    response = admin_client.get("/admin/")
    assert response.status_code == 200
    body = html(response)
    for label in ("Overview", "Customers", "Orders", "Experiment", "Results", "Model", "Data"):
        assert label in body
    assert "SETUP" in body

    response = admin_client.get("/admin/?source=synthetic")
    assert "SIMULATED" in html(response)


# --------------------------------------------------------------------------- B2: state + split

def test_state_machine_refuses_illegal_moves(admin_client):
    add_people(2)
    response = admin_client.post("/admin/experiment/close-round1", follow_redirects=True)
    assert "Cannot go from SETUP to BETWEEN" in html(response)
    assert state()["state"] == "SETUP"

    assert admin_client.post("/admin/experiment/close-round2").status_code == 302
    assert state()["state"] == "SETUP"

    admin_client.post("/admin/experiment/start-round1")
    assert state()["state"] == "ROUND1"
    admin_client.post("/admin/experiment/close-round1")
    assert state()["state"] == "BETWEEN"

    response = admin_client.post("/admin/experiment/start-round2", follow_redirects=True)
    assert "Randomize the split before starting Round 2" in html(response)
    assert state()["state"] == "BETWEEN"

    admin_client.post("/admin/experiment/randomize")
    admin_client.post("/admin/experiment/start-round2")
    assert state()["state"] == "ROUND2"
    admin_client.post("/admin/experiment/close-round2")
    assert state()["state"] == "CLOSED"


def test_randomize_only_in_between(admin_client):
    add_people(4)
    response = admin_client.post("/admin/experiment/randomize", follow_redirects=True)
    assert "Randomize works only in BETWEEN" in html(response)
    assert query("select count(*) as n from assignments", one=True)["n"] == 0


def test_randomize_exact_split_once_and_locked(admin_client):
    ids = add_people(7)
    excluded = add_people(2, prefix="test", excluded=True)
    add_people(3, source="synthetic", prefix="syn")

    set_state("BETWEEN")
    response = admin_client.post("/admin/experiment/randomize", follow_redirects=True)
    body = html(response)
    assert "Randomised 7 customers" in body
    assert "4 coupon, 3 control" in body
    assert "split is locked" in body

    rows = query("select participant_id, t, e, seed from assignments order by participant_id")
    assert len(rows) == 7
    assert {row["participant_id"] for row in rows} == set(ids)
    assert not ({row["participant_id"] for row in rows} & set(excluded))
    treated = sum(row["t"] for row in rows)
    assert treated == math.ceil(len(ids) / 2) == 4
    assert all(row["e"] == pytest.approx(treated / len(ids)) for row in rows)

    stored = state()
    assert stored["randomized"] is True
    assert stored["seed"] is not None
    assert all(row["seed"] == stored["seed"] for row in rows)

    before = [(row["participant_id"], row["t"]) for row in rows]
    response = admin_client.post("/admin/experiment/randomize", follow_redirects=True)
    assert "Already randomised" in html(response)
    after = [(row["participant_id"], row["t"]) for row in
             query("select participant_id, t from assignments order by participant_id")]
    assert after == before
    assert state()["seed"] == stored["seed"]


def test_excluding_customers_is_closed_after_randomizing(admin_client):
    pid, other = add_people(2)
    response = admin_client.post(f"/admin/customers/{pid}/exclude",
                                 data={"action": "exclude"}, follow_redirects=True)
    assert "Customer excluded" in html(response)
    assert query("select excluded from participants where id = %s", (pid,), one=True)["excluded"] is True

    set_state("BETWEEN")
    admin_client.post("/admin/experiment/randomize")
    assert state()["randomized"] is True
    response = admin_client.post(f"/admin/customers/{other}/exclude",
                                 data={"action": "exclude"}, follow_redirects=True)
    assert "split is locked" in html(response)
    assert query("select excluded from participants where id = %s", (other,), one=True)["excluded"] is False


def test_one_click_launch_randomizes_and_opens_round2(admin_client):
    ids = add_people(5)
    excluded = add_people(1, prefix="test", excluded=True)
    add_people(2, source="synthetic", prefix="syn")
    set_state("BETWEEN")

    response = admin_client.post("/admin/experiment/launch", follow_redirects=True)
    body = html(response)
    assert "Treatment launched on 5 real customers" in body
    assert "3 coupon, 2 control" in body
    assert "State is now ROUND2" in body

    stored = state()
    assert stored["state"] == "ROUND2"
    assert stored["randomized"] is True
    assert stored["seed"] is not None

    rows = query("select participant_id, t, e, seed from assignments order by participant_id")
    assert len(rows) == 5
    assert {row["participant_id"] for row in rows} == set(ids)
    assert not ({row["participant_id"] for row in rows} & set(excluded))
    assert sum(row["t"] for row in rows) == 3
    assert all(row["seed"] == stored["seed"] for row in rows)

    before = [(row["participant_id"], row["t"]) for row in rows]
    again = admin_client.post("/admin/experiment/launch", follow_redirects=True)
    assert "already live" in html(again)
    assert state()["state"] == "ROUND2"
    assert [(row["participant_id"], row["t"]) for row in
            query("select participant_id, t from assignments order by participant_id")] == before


def test_launch_refuses_before_between_and_with_too_few_customers(admin_client):
    add_people(3)
    response = admin_client.post("/admin/experiment/launch", follow_redirects=True)
    assert "can only be launched in BETWEEN" in html(response)
    assert state()["state"] == "SETUP"
    assert query("select count(*) as n from assignments", one=True)["n"] == 0

    set_state("BETWEEN")
    execute("delete from participants where source = 'real'")
    (one,) = add_people(1, prefix="solo")
    response = admin_client.post("/admin/experiment/launch", follow_redirects=True)
    assert "at least 2 real customers" in html(response)
    stored = state()
    assert stored["state"] == "BETWEEN"
    assert stored["randomized"] is False
    assert query("select count(*) as n from assignments", one=True)["n"] == 0


def test_overview_and_experiment_show_the_one_click_launch(admin_client):
    add_people(3)
    set_state("BETWEEN")

    for path in ("/admin/", "/admin/experiment"):
        body = html(admin_client.get(path))
        assert "Launch treatment" in body, path
        assert 'action="/admin/experiment/launch"' in body, path

    set_state("BETWEEN", randomized=True, seed=7)
    assert "Launch treatment" not in html(admin_client.get("/admin/"))


def test_launch_cannot_start_from_a_row_that_moved_underneath_it(admin_client, monkeypatch):
    """The SQL lock is conditional on BETWEEN and randomized = false, not just the cached row."""
    ids = add_people(4)
    set_state("BETWEEN")
    admin_module = sys.modules["admin"]

    real_state_row = admin_module.state_row
    monkeypatch.setattr(admin_module, "state_row", lambda: {"state": "BETWEEN", "randomized": False,
                                                            "seed": None, "coupon_amount": 100,
                                                            "coupon_min_subtotal": 300})
    set_state("ROUND1")
    response = admin_client.post("/admin/experiment/launch", follow_redirects=True)
    assert "state changed" in html(response).lower()
    stored = state()
    assert stored["state"] == "ROUND1"
    assert stored["randomized"] is False
    assert query("select count(*) as n from assignments", one=True)["n"] == 0
    monkeypatch.setattr(admin_module, "state_row", real_state_row)


def test_coupon_text_only_for_treated_customers_in_round_two(client):
    treated, control = add_people(2)
    assign(treated, 1)
    assign(control, 0)

    for path in ("/cart", "/checkout"):
        for pid in (treated, control):
            signin_shop(client, pid)
            client.post("/cart/add", data={"product_id": 1, "qty": 1})

        set_state("ROUND2", randomized=True)
        signin_shop(client, control)
        body = html(client.get(path)).lower()
        assert "coupon" not in body, path
        assert "discount" not in body, path

        signin_shop(client, treated)
        assert "coupon" in html(client.get(path)).lower(), path

        set_state("ROUND1")
        signin_shop(client, treated)
        body = html(client.get(path)).lower()
        assert "coupon" not in body, path


# --------------------------------------------------------------------------- B3: results + export

def _golden_split():
    ids = add_people(10)
    treated_outcomes = [1, 1, 1, 0, 1]
    control_outcomes = [1, 0, 0, 1, 0]
    for pid, outcome in zip(ids[:5], treated_outcomes):
        assign(pid, 1)
        if outcome:
            add_order(pid, 2, total=500, discount=100, coupon=True)
    for pid, outcome in zip(ids[5:], control_outcomes):
        assign(pid, 0)
        if outcome:
            add_order(pid, 2, total=500)
    return ids


def test_results_match_the_golden_check(admin_client):
    _golden_split()
    set_state("ROUND2", randomized=True)

    body = html(admin_client.get("/admin/results?source=real"))
    assert "+40.0 percentage points" in body
    assert "0.5238" in body
    assert "-20.0 to +100.0 pp" in body
    assert "inconclusive" in body
    assert "only 10 customers; 40 or more are needed" in body
    assert "Provisional" in body

    set_state("CLOSED", randomized=True)
    body = html(admin_client.get("/admin/results?source=real"))
    assert "Provisional" not in body


def test_overview_shows_the_uplift_in_big_bold_type(admin_client):
    _golden_split()
    set_state("ROUND2", randomized=True)

    body = html(admin_client.get("/admin/?source=real"))
    assert 'class="uplift-hero' in body
    assert "+40.0" in body
    assert "pp" in body
    assert "5 vs 5 customers" in body
    assert "provisional" in body.lower()
    assert "Launch treatment" not in body

    set_state("CLOSED", randomized=True)
    final = html(admin_client.get("/admin/?source=real"))
    assert "provisional" not in final.lower()


def test_results_shows_the_uplift_in_big_bold_type(admin_client):
    _golden_split()
    set_state("ROUND2", randomized=True)

    body = html(admin_client.get("/admin/results?source=real"))
    assert 'class="uplift-hero' in body
    assert "+40.0" in body
    assert "5 vs 5 customers" in body


def test_results_warn_when_everyone_orders_or_nobody_does(admin_client):
    ids = add_people(8)
    for pid in ids:
        assign(pid, 1 if pid in ids[:4] else 0)
        add_order(pid, 2)
    set_state("ROUND2", randomized=True)
    body = html(admin_client.get("/admin/results?source=real"))
    assert "cannot tell you anything yet" in body


def test_results_survive_a_one_group_split(admin_client):
    """A tiny real group can put everyone on one side; the page must not crash on no interval."""
    (pid,) = add_people(1)
    assign(pid, 1)
    add_order(pid, 2)
    set_state("ROUND2", randomized=True)
    response = admin_client.get("/admin/results?source=real")
    assert response.status_code == 200
    assert "Bootstrap interval" in html(response)


def test_verdict_thresholds():
    import stats

    short = stats.verdict(39, 0.001, 0.1, 0.5)
    assert short["verdict"] == "inconclusive"
    assert any("40 or more" in reason for reason in short["reasons"])
    assert stats.verdict(40, 0.049, 0.01, 0.5)["verdict"] == "reliable"
    assert stats.verdict(40, 0.05, 0.01, 0.5)["verdict"] == "inconclusive"
    assert stats.verdict(40, 0.01, -0.1, 0.5)["verdict"] == "inconclusive"


def test_statistics_golden_and_power_table():
    import stats

    treated, control = [1, 1, 1, 0, 1], [1, 0, 0, 1, 0]
    block = stats.coupon_effect(treated, control)
    assert block["effect"] == pytest.approx(0.4)
    assert block["p_value"] == pytest.approx(132 / 252)
    assert block["lo"] == pytest.approx(-0.2)
    assert block["hi"] == pytest.approx(1.0)
    assert block["verdict"] == "inconclusive"
    for baseline, target, expected in stats.POWER_ROWS:
        assert stats.power_n(baseline, target) == expected


def test_exact_test_handles_large_groups_in_bounded_memory():
    import stats

    symmetric = stats.exact_randomization_test([1] * 500 + [0] * 500,
                                               [0] * 500 + [1] * 500)
    assert symmetric == pytest.approx(1.0)

    separated = stats.exact_randomization_test([1] * 1000, [0] * 1000)
    assert separated < 0.001
    assert separated == stats.exact_randomization_test([1] * 1000, [0] * 1000)


def test_export_csv_has_one_row_per_real_customer(admin_client):
    first, second = add_people(2)
    add_people(1, source="synthetic", prefix="syn")
    assign(first, 1)
    assign(second, 0)
    add_order(first, 2, total=400, discount=50, coupon=True)
    set_state("ROUND2", randomized=True)

    response = admin_client.get("/admin/export.csv?source=real")
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert "uplix-real.csv" in response.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(html(response))))
    assert rows[0] == ["user_id", "nickname", "source", "excluded", "tag", "group_t",
                       "n_views_r1", "n_cart_adds_r1", "avg_price_viewed_r1", "n_orders_r1",
                       "r1_spend", "y_order_r2", "revenue_r2", "discount_r2"]
    assert len(rows) == 3
    assert {rows[1][1], rows[2][1]} == {"person1", "person2"}
    assert all(row[2] == "real" for row in rows[1:])
    first_row = next(row for row in rows[1:] if row[1] == "person1")
    assert first_row[5] == "1" and first_row[11] == "1" and first_row[12] == "350"

    synthetic = list(csv.reader(io.StringIO(html(
        admin_client.get("/admin/export.csv?source=synthetic")))))
    assert [row[1] for row in synthetic[1:]] == ["syn1"]


# --------------------------------------------------------------------------- B4: model page

def test_model_page_falls_back_to_the_bundled_sample(admin_client, monkeypatch):
    monkeypatch.setenv("ML_API_URL", "")
    body = html(admin_client.get("/admin/model"))
    assert "SIMULATED" in body
    assert "bundled sample run" in body
    assert "Never treat" in body
    assert "Honesty check" in body


def test_model_payload_unwraps_the_live_service_shape(admin_module, monkeypatch):
    payload = json.load(open(SAMPLE, encoding="utf-8"))
    wrapped = {"created_at": "2026-01-01T00:00:00", "simulated": True, "results": payload}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(wrapped).encode("utf-8")

    monkeypatch.setenv("ML_API_URL", "https://ml.test")
    monkeypatch.setenv("ML_API_KEY", "secret")
    monkeypatch.setattr(admin_module.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    graphs, origin = admin_module.graphs_payload()
    assert origin == "live"
    assert set(graphs["by_coupon"]) == {"50", "100", "150"}
    assert graphs["simulated"] is True


# --------------------------------------------------------------------------- B5: score

def test_score_endpoint_labels_the_answer_simulated(admin_client, admin_module, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"p_buy": 0.42, "mu0": 0.41, "mu1": 0.44, "uplift": 0.03,
                               "lane": "Persuadable", "gain": 12}).encode("utf-8")

    monkeypatch.setenv("ML_API_URL", "https://ml.test")
    monkeypatch.setenv("ML_API_KEY", "secret")
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["key"] = request.get_header("X-api-key")
        return FakeResponse()

    monkeypatch.setattr(admin_module.urllib.request, "urlopen", fake_urlopen)
    response = admin_client.post("/admin/api/score/7")
    assert response.status_code == 200
    data = response.get_json()
    assert data["simulated"] is True
    assert data["lane"] == "Persuadable"
    assert seen["url"].endswith("/predict/user/7")
    assert seen["key"] == "secret"


def test_score_endpoint_reports_configuration_and_network_errors(admin_client, admin_module, monkeypatch):
    monkeypatch.setenv("ML_API_URL", "")
    response = admin_client.post("/admin/api/score/7")
    assert response.status_code == 503

    monkeypatch.setenv("ML_API_URL", "https://ml.test")

    def down(*args, **kwargs):
        raise admin_module.urllib.error.URLError("down")

    monkeypatch.setattr(admin_module.urllib.request, "urlopen", down)
    response = admin_client.post("/admin/api/score/7")
    assert response.status_code == 502


# --------------------------------------------------------------------------- data + reset

def test_reset_is_refused_while_the_split_is_locked(admin_client):
    (pid,) = add_people(1)
    assign(pid, 1)
    add_order(pid, 1)
    set_state("ROUND2", randomized=True)

    response = admin_client.post("/admin/data/reset", follow_redirects=True)
    assert "Reset is refused" in html(response)
    assert query("select count(*) as n from participants where source = 'real'", one=True)["n"] == 1
    assert query("select count(*) as n from assignments", one=True)["n"] == 1


def test_reset_after_close_deletes_real_rows_only(admin_client):
    real = add_people(2)
    (synthetic,) = add_people(1, source="synthetic", prefix="syn")
    assign(real[0], 1)
    assign(synthetic, 1)
    add_order(real[0], 1)
    add_order(synthetic, 1)
    execute("insert into model_runs (source, results) values ('synthetic', '{}'::jsonb)")
    set_state("CLOSED", randomized=True, seed=123)

    response = admin_client.post("/admin/data/reset", follow_redirects=True)
    assert "Real data deleted" in html(response)
    assert query("select count(*) as n from participants where source = 'real'", one=True)["n"] == 0
    assert query("select count(*) as n from participants where source = 'synthetic'",
                 one=True)["n"] == 1
    assert query("select count(*) as n from assignments", one=True)["n"] == 1
    assert query("select count(*) as n from model_runs", one=True)["n"] == 1

    stored = state()
    assert stored["state"] == "SETUP"
    assert stored["randomized"] is False
    assert stored["seed"] is None


# --------------------------------------------------------------------------- Vercel readiness

def test_vercel_config_and_cookie_settings(flask_app):
    with open(os.path.join(ROOT, "vercel.json"), encoding="utf-8") as handle:
        config = json.load(handle)
    assert config["functions"]["app.py"]["maxDuration"] == 60

    with open(os.path.join(ROOT, ".vercelignore"), encoding="utf-8") as handle:
        ignored = [line.strip() for line in handle if line.strip()]
    assert ".env" in ignored
    assert ".env.*" in ignored
    assert "sample_data" not in ignored

    assert flask_app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert flask_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert flask_app.config["SESSION_COOKIE_NAME"] == "uplix_session"


def test_admin_css_uses_the_shop_palette():
    with open(os.path.join(ROOT, "public", "admin.css"), encoding="utf-8") as handle:
        css = handle.read().lower()
    for colour in ("#14161c", "#22b8cf", "#f4f6f8", "#e3e7ec"):
        assert colour in css
