"""FastAPI service: predict a customer's uplift, and serve the stored model results for the dashboard graphs.

Run locally:  uvicorn main:app --reload --port 8000     (docs at /docs)
Vercel finds `app` in main.py with no configuration.
Environment: ML_API_KEY (optional but recommended), DATABASE_URL (only for the /predict/user and /graphs endpoints).
"""
import json
import os
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from uplix_ml import RAW, UpliftModel

app = FastAPI(title="Uplix ML service", version="1.0")
MODEL = UpliftModel(json.loads((Path(__file__).parent / "model.json").read_text()))


def check_key(x_api_key: str | None) -> None:
    key = os.environ.get("ML_API_KEY")
    if key and x_api_key != key:
        raise HTTPException(status_code=401, detail="missing or wrong X-API-Key")


class Features(BaseModel):
    """Round 1 behaviour of one customer (the same five numbers as the database view v_round1)."""
    n_views_r1: float = Field(ge=0)
    n_cart_adds_r1: float = Field(ge=0)
    avg_price_viewed_r1: float = Field(ge=0)
    n_orders_r1: float = Field(ge=0)
    r1_spend: float = Field(ge=0)


def _row(f: Features) -> list[float]:
    return [getattr(f, name) for name in RAW]


def _pack(out: dict, i: int) -> dict:
    return {k: (str(v[i]) if k == "lane" else float(v[i])) for k, v in out.items()}


@app.get("/health")
def health():
    return {"status": "ok", "model_trained_at": MODEL.spec["trained_at"], "features": RAW}


@app.get("/model")
def model_info(x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    s = MODEL.spec
    return {k: s.get(k) for k in ("version", "trained_at", "source", "n_train", "raw", "engineered", "economics", "f_star", "uplift_cutoff", "metrics")}


@app.post("/predict")
def predict(f: Features, x_api_key: str | None = Header(default=None)):
    """One customer in, one prediction out: purchase chance with and without a coupon, the uplift, and a lane."""
    check_key(x_api_key)
    return _pack(MODEL.predict(np.array([_row(f)])), 0)


@app.post("/predict/batch")
def predict_batch(items: list[Features], x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    if not 0 < len(items) <= 500:
        raise HTTPException(status_code=422, detail="send between 1 and 500 customers")
    out = MODEL.predict(np.array([_row(f) for f in items]))
    return [_pack(out, i) for i in range(len(items))]


def _db():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None, row_factory=dict_row, connect_timeout=10)


@app.get("/predict/user/{user_id}")
def predict_user(user_id: int, x_api_key: str | None = Header(default=None)):
    """Look a customer up in the database (view v_round1) and score them."""
    check_key(x_api_key)
    with _db() as conn:
        row = conn.execute(f"select {', '.join(RAW)}, source from v_round1 where user_id = %s", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such customer")
    feats = {k: float(row[k]) for k in RAW}
    return {"user_id": user_id, "source": row["source"], "features": feats, **_pack(MODEL.predict(np.array([[feats[k] for k in RAW]])), 0)}


@app.get("/graphs/latest")
def graphs_latest(source: str = Query("synthetic", pattern="^(real|synthetic)$"), x_api_key: str | None = Header(default=None)):
    """The newest stored model results (strategy bars, Qini curves, budget curves...) for the dashboard."""
    check_key(x_api_key)
    with _db() as conn:
        row = conn.execute("select results, created_at from model_runs where source = %s order by id desc limit 1", (source,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no model run stored for this source yet")
    return {"created_at": row["created_at"].isoformat(), "simulated": source == "synthetic", "results": row["results"]}
