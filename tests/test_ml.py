"""ML tests: feature engineering, numpy-only serving parity with scikit-learn, and the FastAPI service."""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "ml_service"))
sys.path.insert(0, ROOT)

from uplix_ml import ENGINEERED, RAW, UpliftModel, engineer, to_spec  # noqa: E402


def test_engineer_shape_and_known_values():
    x = engineer(np.array([[4, 2, 500, 1, 900], [0, 0, 0, 0, 0]], float))
    assert x.shape == (2, len(ENGINEERED))
    assert x[0, ENGINEERED.index("cart_rate")] == 0.5
    assert x[0, ENGINEERED.index("has_order")] == 1 and x[1, ENGINEERED.index("no_views")] == 1
    assert np.isfinite(x).all()                       # zero views / zero orders must not divide by zero


@pytest.fixture(scope="module")
def trained():
    pd = pytest.importorskip("pandas")
    sk = pytest.importorskip("sklearn.linear_model")
    from sklearn.preprocessing import StandardScaler
    from tools.synth import features_from_frames, generate
    products = pd.DataFrame({"id": range(1, 19), "name": "x", "category": "c", "price": [299, 1499, 1999, 999, 799, 349, 649, 199, 149, 499, 1199, 249, 199, 1299, 249, 599, 1799, 699]})
    d = generate(products, n=3000, seed=11)
    df = features_from_frames(d)
    raw = df[RAW].to_numpy(float)
    X = engineer(raw)
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    T, Y = df["T"].to_numpy(int), df["Y_order_r2"].to_numpy(int)
    mk = lambda: sk.LogisticRegression(C=1.0, max_iter=500)
    trad, m0, m1 = mk().fit(Xs, Y), mk().fit(Xs[T == 0], Y[T == 0]), mk().fit(Xs[T == 1], Y[T == 1])
    spec = to_spec(sc, trad, m0, m1, economics=dict(m=980.0, d=100.0, c=0.0), meta=dict(source="test"))
    return dict(raw=raw, Xs=Xs, trad=trad, m0=m0, m1=m1, model=UpliftModel(spec))


def test_numpy_serving_matches_scikit_learn(trained):
    out = trained["model"].predict(trained["raw"])
    assert np.allclose(out["p_buy"], trained["trad"].predict_proba(trained["Xs"])[:, 1], atol=1e-9)
    assert np.allclose(out["mu0"], trained["m0"].predict_proba(trained["Xs"])[:, 1], atol=1e-9)
    assert np.allclose(out["mu1"], trained["m1"].predict_proba(trained["Xs"])[:, 1], atol=1e-9)
    assert np.allclose(out["uplift"], out["mu1"] - out["mu0"])


def test_lane_rule(trained):
    out = trained["model"].predict(trained["raw"])
    treat, dnd = out["lane"] == "Treat", out["lane"] == "Do not disturb"
    assert (out["gain"][treat] > 0).all() and (out["uplift"][dnd] < 0).all()
    assert (out["uplift"][treat] > 0).all()           # a positive gain needs a positive uplift when the discount is >= 0
    assert set(np.unique(out["lane"])) <= {"Treat", "Skip", "Do not disturb"}


def test_service_does_not_import_scikit_learn():
    code = "import sys; sys.path.insert(0, 'ml_service'); import main; assert 'sklearn' not in sys.modules and 'pandas' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


@pytest.fixture()
def client(monkeypatch):
    pytest.importorskip("httpx")
    monkeypatch.delenv("ML_API_KEY", raising=False)
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app)


GOOD = dict(n_views_r1=6, n_cart_adds_r1=2, avg_price_viewed_r1=520, n_orders_r1=1, r1_spend=799)


def test_health_and_model_endpoints(client):
    assert client.get("/health").json()["status"] == "ok"
    info = client.get("/model").json()
    assert info["raw"] == RAW and info["f_star"] is not None


def test_predict_returns_probabilities_and_lane(client):
    r = client.post("/predict", json=GOOD).json()
    assert 0 <= r["mu0"] <= 1 and 0 <= r["mu1"] <= 1 and abs(r["uplift"] - (r["mu1"] - r["mu0"])) < 1e-12
    assert r["lane"] in {"Treat", "Skip", "Do not disturb"}


def test_predict_rejects_bad_input(client):
    assert client.post("/predict", json={**GOOD, "n_views_r1": -1}).status_code == 422
    assert client.post("/predict", json={"n_views_r1": 1}).status_code == 422
    assert client.post("/predict/batch", json=[]).status_code == 422


def test_batch_matches_single(client):
    batch = client.post("/predict/batch", json=[GOOD, {**GOOD, "n_views_r1": 0, "n_cart_adds_r1": 0, "n_orders_r1": 0, "r1_spend": 0}]).json()
    assert batch[0] == client.post("/predict", json=GOOD).json() and len(batch) == 2


def test_api_key_is_enforced_when_set(client, monkeypatch):
    monkeypatch.setenv("ML_API_KEY", "secret")
    assert client.post("/predict", json=GOOD).status_code == 401
    assert client.post("/predict", json=GOOD, headers={"X-API-Key": "secret"}).status_code == 200
    assert client.get("/health").status_code == 200   # health stays open for uptime checks


def test_model_file_is_small():
    assert os.path.getsize(os.path.join(ROOT, "ml_service", "model.json")) < 50_000


# ---------- endpoints that read the database (need TEST_DATABASE_URL) ----------
DB_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture()
def db_client(monkeypatch):
    pytest.importorskip("httpx")
    psycopg = pytest.importorskip("psycopg")
    pd = pytest.importorskip("pandas")
    from psycopg.types.json import Jsonb
    from tools.synth import generate, write_to_db
    with psycopg.connect(DB_URL, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("drop schema public cascade; create schema public;")
            for f in ("schema.sql", "seed_products.sql"):
                cur.execute(open(os.path.join(ROOT, "sql", f), encoding="utf-8").read())
            cur.execute("select id, name, category, price from products order by id")
            products = pd.DataFrame(cur.fetchall(), columns=["id", "name", "category", "price"])
        conn.commit()
        d = generate(products, n=60, seed=1)
        write_to_db(conn, d, reset=True)
        conn.execute("insert into model_runs (source, results) values ('synthetic', %s)", (Jsonb({"hello": "dashboard"}),))
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.delenv("ML_API_KEY", raising=False)
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app), d


@pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL")
def test_predict_user_reads_features_from_the_database(db_client):
    client, d = db_client
    uid = next(iter(d["idx2pid"].values()))
    r = client.get(f"/predict/user/{uid}").json()
    assert r["user_id"] == uid and r["source"] == "synthetic" and set(r["features"]) == set(RAW)
    direct = client.post("/predict", json=r["features"]).json()
    assert direct["uplift"] == r["uplift"]
    assert client.get("/predict/user/99999999").status_code == 404


@pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL")
def test_graphs_latest_returns_the_stored_run(db_client):
    client, _ = db_client
    r = client.get("/graphs/latest?source=synthetic").json()
    assert r["simulated"] is True and r["results"] == {"hello": "dashboard"}
    assert client.get("/graphs/latest?source=real").status_code == 404
    assert client.get("/graphs/latest?source=bogus").status_code == 422
