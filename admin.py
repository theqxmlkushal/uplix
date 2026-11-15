"""Admin console (Member B): README section 10.

Rules kept in this file:

  * /admin/login takes an account id (ADMIN_ID) and a password (ADMIN_PASSWORD), both
    compared in constant time, with a throttle after 5 wrong tries. A signed is_admin flag
    lives in the session.
  * Everything under /admin and every /admin/api/ endpoint requires that flag; a shop
    session (participant_id only) never grants it.
  * The state only moves along SETUP -> ROUND1 -> BETWEEN -> ROUND2 -> CLOSED and illegal
    moves are refused on the server.
  * Randomize works only in BETWEEN and only once: app_state.randomized is the lock.
  * Launch is the one-click path: it draws the same locked split and moves to ROUND2 in a
    single transaction, so the treatment starts on real customers immediately.
  * Reset real data is allowed before randomization, or after the experiment is CLOSED;
    it never touches synthetic rows.
  * Nothing is written to disk. Sessions live in the cookie, data in Postgres.
  * The ML service is called server to server (ML_API_URL + ML_API_KEY), never the browser.
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import math
import os
import random
import secrets
import time
import urllib.error
import urllib.request
from functools import wraps
from pathlib import Path

from flask import (Blueprint, Response, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.routing import BuildError

import stats
import tags
from db import execute, query, transaction

admin = Blueprint("admin", __name__, url_prefix="/admin", template_folder="templates")

STATES = ("SETUP", "ROUND1", "BETWEEN", "ROUND2", "CLOSED")
ROUNDS = {"ROUND1": 1, "ROUND2": 2}
LEGAL_MOVES = {("SETUP", "ROUND1"), ("ROUND1", "BETWEEN"), ("BETWEEN", "ROUND2"), ("ROUND2", "CLOSED")}
ACTION_TARGET = {"start-round1": "ROUND1", "close-round1": "BETWEEN",
                 "start-round2": "ROUND2", "close-round2": "CLOSED"}

FAIL_LIMIT = 5                      # wrong credentials in a row before the throttle
DEFAULT_BLOCK_SECONDS = 60          # ADMIN_BLOCK_SECONDS can lower it (tests do)
DEFAULT_ADMIN_ID = "admin"          # ADMIN_ID can replace it in a deployment
CUSTOMER_PAGE_LIMIT = 400           # keep the synthetic page light; search narrows it


# --------------------------------------------------------------------------- auth helpers

def expected_id():
    return os.environ.get("ADMIN_ID", DEFAULT_ADMIN_ID)


def expected_password():
    return os.environ.get("ADMIN_PASSWORD", "")


def credentials_ok(candidate_id, candidate_password):
    """Check the account id and the password together, in constant time.

    Both comparisons always run, even when the first value is already wrong, so a guess
    cannot learn which half failed from the time the answer takes. An unset ADMIN_ID or
    ADMIN_PASSWORD denies every login.
    """
    expected_id_value = expected_id().encode("utf-8")
    expected_password_value = expected_password().encode("utf-8")
    id_ok = hmac.compare_digest((candidate_id or "").encode("utf-8"), expected_id_value)
    password_ok = hmac.compare_digest((candidate_password or "").encode("utf-8"),
                                      expected_password_value)
    configured = bool(expected_id_value) and bool(expected_password_value)
    return configured and bool(id_ok & password_ok)


def password_ok(candidate):
    """Backwards-compatible helper: check only the password for the configured admin id."""
    return credentials_ok(expected_id(), candidate)


def is_admin():
    return session.get("is_admin") is True


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return jsonify(error="admin login required"), 401
        return view(*args, **kwargs)
    return wrapped


@admin.before_request
def guard():
    """Login page is open; everything else needs the signed flag (redirect or 401 for JSON)."""
    if request.endpoint == "admin.login":
        return None
    if is_admin():
        return None
    if request.path.startswith("/admin/api/"):
        return jsonify(error="admin login required"), 401
    return redirect(url_for("admin.login", next=request.path))


@admin.context_processor
def shared():
    st = state_row()

    def switch(target):
        """Real/Synthetic link for the current page, even when the route needs a path id."""
        try:
            return url_for(request.endpoint, **(request.view_args or {}), source=target)
        except (BuildError, TypeError):
            return url_for("admin.customers", source=target)

    return {
        "state": st,
        "round_no": ROUNDS.get(st["state"]),
        "source": source_arg(),
        "switch_url": switch,
    }


def state_row():
    """The single app_state row, read once per request."""
    if "admin_state" not in g:
        g.admin_state = query(
            "select state, randomized, seed, coupon_amount, coupon_min_subtotal "
            "from app_state where id = 1", one=True)
    return g.admin_state


def source_arg():
    value = (request.args.get("source") or "real").lower()
    return value if value in ("real", "synthetic") else "real"


def money(amount):
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return "0"


def percent(value, digits=1):
    return f"{100 * float(value):.{digits}f}%" if value is not None else "—"


@admin.app_context_processor
def useful_filters():
    return {"money": money, "percent": percent}


# --------------------------------------------------------------------------- login / logout

@admin.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if is_admin():
            return redirect(url_for("admin.overview"))
        return render_template("admin/login.html", next=request.args.get("next", ""),
                               admin_id="")

    admin_id = request.form.get("admin_id", "")
    now = time.time()
    blocked_until = float(session.get("admin_blocked_until") or 0)
    if now < blocked_until:
        wait = math.ceil(blocked_until - now)
        return render_template("admin/login.html", next=request.form.get("next", ""),
                               admin_id=admin_id,
                               error=f"Too many wrong tries. Wait {wait} more seconds."), 429

    if credentials_ok(admin_id, request.form.get("password")):
        session.pop("admin_fails", None)
        session.pop("admin_blocked_until", None)
        session["is_admin"] = True
        target = request.form.get("next") or ""
        if not target.startswith("/admin"):
            target = url_for("admin.overview")
        return redirect(target)

    fails = int(session.get("admin_fails") or 0) + 1
    if fails >= FAIL_LIMIT:
        session.pop("admin_fails", None)
        block = int(os.environ.get("ADMIN_BLOCK_SECONDS", DEFAULT_BLOCK_SECONDS))
        session["admin_blocked_until"] = now + block
        return render_template("admin/login.html", next=request.form.get("next", ""),
                               admin_id=admin_id,
                               error=f"Too many wrong tries. Wait {block} seconds."), 429
    session["admin_fails"] = fails
    left = FAIL_LIMIT - fails
    return render_template("admin/login.html", next=request.form.get("next", ""),
                           admin_id=admin_id,
                           error=f"Wrong account ID or password. {left} tries left before a pause."), 401


@admin.route("/logout", methods=["POST"])
def logout():
    """Leave the console but not the shop: the customer's own session stays signed in."""
    session.pop("is_admin", None)
    return redirect(url_for("admin.login"))


# --------------------------------------------------------------------------- shared reads

def customer_rows(source):
    """v_round1 rows plus tag and group, for one source."""
    rows = query(
        "select user_id, source, nickname, excluded, n_views_r1, n_cart_adds_r1, "
        "avg_price_viewed_r1, n_orders_r1, r1_spend "
        "from v_round1 where source = %s order by user_id", (source,))
    tag_map = tags.classify(rows)
    groups = {r["participant_id"]: r["t"] for r in query("select participant_id, t from assignments")}
    for row in rows:
        row["tag"] = tag_map[row["user_id"]]
        row["group"] = groups.get(row["user_id"])
    return rows


def feature_rows(source):
    """Assignment + Round 2 outcome rows for the statistics pages."""
    return query(
        "select user_id, \"T\" as t, \"Y_order_r2\" as y, revenue_r2, discount_r2, "
        "n_views_r1, n_cart_adds_r1, n_orders_r1, r1_spend "
        "from v_features where source = %s order by user_id", (source,))


def effect_block(source):
    """The coupon effect (order rate + revenue) or None before randomization."""
    rows = feature_rows(source)
    if not rows:
        return None
    treated = [r for r in rows if r["t"] == 1]
    control = [r for r in rows if r["t"] == 0]
    block = stats.coupon_effect(
        [r["y"] for r in treated], [r["y"] for r in control],
        [r["revenue_r2"] for r in treated], [r["revenue_r2"] for r in control])
    block["rows"] = rows
    return block


# --------------------------------------------------------------------------- overview

@admin.route("/")
def overview():
    src, st = source_arg(), state_row()
    counts = query(
        "select "
        "(select count(*) from participants where source = %s) as customers, "
        "(select count(*) from orders o join participants p on p.id = o.participant_id "
        " where p.source = %s) as orders, "
        "(select count(*) from events e join participants p on p.id = e.participant_id "
        " where p.source = %s) as events", (src, src, src), one=True)
    funnel = query(
        "select e.round, e.type, count(distinct e.participant_id) as n "
        "from events e join participants p on p.id = e.participant_id "
        "where p.source = %s group by e.round, e.type", (src,))
    buckets = query(
        "select floor(extract(epoch from o.created_at) / 300)::bigint as bucket, "
        "o.round, count(*) as n from orders o join participants p on p.id = o.participant_id "
        "where p.source = %s group by bucket, o.round order by bucket", (src,))
    bounds = query(
        "select e.round, min(extract(epoch from e.created_at)) as start, "
        "max(extract(epoch from e.created_at)) as finish "
        "from events e join participants p on p.id = e.participant_id "
        "where p.source = %s group by e.round order by e.round", (src,))
    rows = customer_rows(src)
    by_tag = tags.counts({r["user_id"]: r["tag"] for r in rows if not r["excluded"]})
    effect = effect_block(src)

    stages = ["view_item", "add_to_cart", "checkout_start", "order_placed"]
    funnel_data = {rnd: {stage: 0 for stage in stages} for rnd in (1, 2)}
    for row in funnel:
        if row["round"] in funnel_data and row["type"] in stages:
            funnel_data[row["round"]][row["type"]] = row["n"]

    return render_template(
        "admin/overview.html",
        counts=counts, funnel=funnel_data, by_tag=by_tag, effect=effect,
        chart={
            "buckets": [{"epoch": int(b["bucket"]) * 300, "round": b["round"], "n": b["n"]}
                        for b in buckets],
            "bounds": [{"round": b["round"], "start": float(b["start"]), "finish": float(b["finish"])}
                       for b in bounds],
            "funnel": funnel_data,
            "by_tag": by_tag,
        },
        power_rows=stats.POWER_ROWS)


# --------------------------------------------------------------------------- customers

@admin.route("/customers")
def customers():
    src = source_arg()
    rows = customer_rows(src)
    search = (request.args.get("q") or "").strip().lower()
    tag = (request.args.get("tag") or "").strip()
    if search:
        rows = [r for r in rows if search in (r["nickname"] or "").lower()]
    if tag in tags.TAGS:
        rows = [r for r in rows if r["tag"] == tag]
    total = len(rows)
    return render_template("admin/customers.html", rows=rows[:CUSTOMER_PAGE_LIMIT],
                           total=total, shown=min(total, CUSTOMER_PAGE_LIMIT),
                           search=request.args.get("q", ""), active_tag=tag,
                           all_tags=tags.TAGS)


@admin.route("/customers/<int:user_id>")
def customer(user_id):
    row = query(
        "select p.id, p.nickname, p.source, p.excluded, p.created_at, "
        "r.n_views_r1, r.n_cart_adds_r1, r.avg_price_viewed_r1, r.n_orders_r1, r.r1_spend "
        "from participants p join v_round1 r on r.user_id = p.id where p.id = %s", (user_id,), one=True)
    if row is None:
        return render_template("admin/not_found.html"), 404
    row["user_id"] = row["id"]
    row["tag"] = tags.classify([row]).get(user_id, "Newcomer")
    group = query("select t, e, seed, assigned_at from assignments where participant_id = %s",
                  (user_id,), one=True)
    orders = query("select id, round, subtotal, discount, total, coupon_applied, created_at "
                   "from orders where participant_id = %s order by created_at, id", (user_id,))
    events = query("select round, type, product_id, value, created_at from events "
                   "where participant_id = %s order by created_at, id limit 400", (user_id,))
    return render_template("admin/customer.html", row=row, group=group, orders=orders, events=events)


@admin.route("/customers/<int:user_id>/exclude", methods=["POST"])
def exclude_customer(user_id):
    """Test accounts can be removed only before randomization (README section 10)."""
    if state_row()["randomized"]:
        flash("The split is locked: customers cannot be removed after randomizing.", "error")
        return redirect(request.form.get("back") or url_for("admin.customers"))
    action = request.form.get("action", "exclude")
    value = action == "exclude"
    query("update participants set excluded = %s where id = %s returning id", (value, user_id), one=True)
    flash("Customer excluded." if value else "Customer restored.", "ok")
    return redirect(request.form.get("back") or url_for("admin.customers"))


# --------------------------------------------------------------------------- orders

@admin.route("/orders")
def orders():
    src = source_arg()
    round_filter = request.args.get("round", "")
    coupon_filter = request.args.get("coupon", "")
    sql = ("select o.id, o.round, o.subtotal, o.discount, o.total, o.coupon_applied, "
           "o.created_at, p.nickname, p.id as participant_id "
           "from orders o join participants p on p.id = o.participant_id where p.source = %s")
    params = [src]
    if round_filter in ("1", "2"):
        sql += " and o.round = %s"
        params.append(int(round_filter))
    if coupon_filter in ("1", "0"):
        sql += " and o.coupon_applied = %s"
        params.append(coupon_filter == "1")
    sql += " order by o.created_at desc, o.id desc limit 500"
    return render_template("admin/orders.html", rows=query(sql, tuple(params)),
                           round_filter=round_filter, coupon_filter=coupon_filter)


# --------------------------------------------------------------------------- experiment

@admin.route("/experiment")
def experiment():
    st = state_row()
    real_rows = customer_rows("real")
    counted = [r for r in real_rows if not r["excluded"]]
    balance = {0: 0, 1: 0}
    for row in counted:
        if row["group"] is not None:
            balance[row["group"]] = balance.get(row["group"], 0) + 1
    tag_group = {name: {0: 0, 1: 0, None: 0} for name in tags.TAGS}
    for row in counted:
        tag_group[row["tag"]][row["group"]] += 1
    return render_template("admin/experiment.html", real=real_rows, counted=counted,
                           balance=balance, tag_group=tag_group, legal=LEGAL_MOVES,
                           action_target=ACTION_TARGET)


@admin.route("/experiment/<action>", methods=["POST"])
def move(action):
    if action == "randomize":
        return randomize()
    if action == "launch":
        return launch()
    target = ACTION_TARGET.get(action)
    st = state_row()
    if target is None or (st["state"], target) not in LEGAL_MOVES:
        flash(f"Cannot go from {st['state']} to {target or action}.", "error")
        return redirect(url_for("admin.experiment"))
    if target == "ROUND2" and not st["randomized"]:
        flash("Randomize the split before starting Round 2.", "error")
        return redirect(url_for("admin.experiment"))
    execute("update app_state set state = %s where id = 1", (target,))
    flash(f"State is now {target}.", "ok")
    return redirect(url_for("admin.experiment"))


def _lock_split(target_state, minimum, messages):
    """Draw the exact 50/50 split and lock it, optionally moving the state in the same commit.

    The atomic condition `randomized = false AND state = 'BETWEEN'` is the single-use lock:
    two clicks (or two tabs) cannot both win, and the assignments only exist when the lock
    update succeeded. Returns a small summary dict, or None when the request was refused.
    """
    st = state_row()
    if st["randomized"]:
        flash(messages["already"], "error")
        return None
    if st["state"] != "BETWEEN":
        flash(messages["state"], "error")
        return None

    ids = [r["id"] for r in query(
        "select id from participants where source = 'real' and excluded = false order by id")]
    if len(ids) < minimum:
        flash(messages["few"], "error")
        return None

    seed = secrets.randbelow(1_000_000_000)
    shuffled = ids[:]
    random.Random(seed).shuffle(shuffled)
    treated_count = math.ceil(len(ids) / 2)
    treated = set(shuffled[:treated_count])
    e = treated_count / len(ids)

    with transaction() as conn:
        locked = conn.execute(
            "update app_state set randomized = true, seed = %s, state = %s "
            "where id = 1 and randomized = false and state = 'BETWEEN' returning id",
            (seed, target_state)).fetchone()
        if locked is None:                       # a second click lost the race
            conn.rollback()
            flash(messages["race"], "error")
            return None
        with conn.cursor() as cur:
            cur.executemany(
                "insert into assignments (participant_id, t, e, seed) values (%s, %s, %s, %s) "
                "on conflict (participant_id) do nothing",
                [(pid, 1 if pid in treated else 0, e, seed) for pid in ids])

    return {"n": len(ids), "treated": treated_count, "control": len(ids) - treated_count,
            "seed": seed}


def randomize():
    """Exact 50/50 split of real, non-excluded customers; app_state.randomized is the lock."""
    result = _lock_split("BETWEEN", 1, {
        "already": "Already randomised. The split is locked and cannot be redrawn.",
        "state": "Randomize works only in BETWEEN (after Round 1 closes).",
        "few": "No real customers to randomise yet.",
        "race": "Already randomised. The split is locked.",
    })
    if result:
        flash(f"Randomised {result['n']} customers: {result['treated']} coupon, "
              f"{result['control']} control. Seed {result['seed']}. The split is locked.", "ok")
    return redirect(url_for("admin.experiment"))


def launch():
    """One click: lock the split and open Round 2, so the coupon goes live immediately."""
    result = _lock_split("ROUND2", 2, {
        "already": "Already randomised. The treatment is already live; the split is locked.",
        "state": "The treatment can only be launched in BETWEEN, after Round 1 closes.",
        "few": "Need at least 2 real customers before launching the treatment.",
        "race": "Someone else just launched it or the state changed. The split is locked.",
    })
    if result:
        flash(f"Treatment launched on {result['n']} real customers: {result['treated']} coupon, "
              f"{result['control']} control. State is now ROUND2 and the split is locked.", "ok")
    return redirect(url_for("admin.experiment"))


# --------------------------------------------------------------------------- results

@admin.route("/results")
def results():
    src, st = source_arg(), state_row()
    effect = effect_block(src)
    warning = None
    if effect:
        if effect["rate_treated"] in (0.0, 1.0) or effect["rate_control"] in (0.0, 1.0):
            warning = ("Every customer in one group did (or did not) order, so the order-rate "
                       "comparison cannot tell you anything yet.")
    provisional = src == "real" and st["state"] != "CLOSED"
    return render_template("admin/results.html", effect=effect, warning=warning,
                           provisional=provisional, power_rows=stats.POWER_ROWS)


# --------------------------------------------------------------------------- model (always synthetic)

def graphs_payload():
    """Newest synthetic model results: live ML service first, bundled sample as fallback."""
    base = (os.environ.get("ML_API_URL") or "").rstrip("/")
    if base:
        key = os.environ.get("ML_API_KEY") or ""
        headers = {"X-API-Key": key} if key else {}
        try:
            request_obj = urllib.request.Request(base + "/graphs/latest?source=synthetic", headers=headers)
            with urllib.request.urlopen(request_obj, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            # The ML service returns {created_at, simulated, results}; the sample file is the
            # results object on its own. Accept either shape.
            if isinstance(data, dict) and "results" in data and "by_coupon" not in data:
                data = data["results"]
            if isinstance(data, dict) and data.get("by_coupon"):
                return data, "live"
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError):
            pass
    path = Path(__file__).with_name("sample_data") / "model_run_synthetic.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), "sample"
    return None, "none"


@admin.route("/model")
def model():
    payload, origin = graphs_payload()
    return render_template("admin/model.html", graphs=payload, origin=origin,
                           coupons=[50, 100, 150])


@admin.route("/api/score/<int:user_id>", methods=["POST"])
@admin_required
def api_score(user_id):
    """Score button: the Flask server calls /predict/user/{id}; the key never reaches a browser."""
    base = (os.environ.get("ML_API_URL") or "").rstrip("/")
    if not base:
        return jsonify(error="ML_API_URL is not set for this deployment."), 503
    key = os.environ.get("ML_API_KEY") or ""
    headers = {"X-API-Key": key} if key else {}
    try:
        request_obj = urllib.request.Request(f"{base}/predict/user/{user_id}", headers=headers)
        with urllib.request.urlopen(request_obj, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return jsonify(error=f"The ML service answered {exc.code}."), 502
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return jsonify(error=f"The ML service is unreachable: {exc}."), 502
    data["simulated"] = True                    # trained on synthetic data, always labelled
    return jsonify(data)


# --------------------------------------------------------------------------- data + export

@admin.route("/data")
def data():
    counts = query(
        "select (select count(*) from participants where source = 'real') as real_n, "
        "(select count(*) from participants where source = 'synthetic') as synthetic_n, "
        "(select count(*) from orders where participant_id in "
        " (select id from participants where source = 'real')) as real_orders", one=True)
    return render_template("admin/data.html", counts=counts)


@admin.route("/export.csv")
def export_csv():
    """One row per customer: Round 1 features, tag, group and Round 2 outcome."""
    src = source_arg()
    rows = customer_rows(src)
    features = {r["user_id"]: r for r in feature_rows(src)}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["user_id", "nickname", "source", "excluded", "tag", "group_t",
                     "n_views_r1", "n_cart_adds_r1", "avg_price_viewed_r1", "n_orders_r1",
                     "r1_spend", "y_order_r2", "revenue_r2", "discount_r2"])
    for row in rows:
        feats = features.get(row["user_id"], {})
        writer.writerow([row["user_id"], row["nickname"], row["source"], row["excluded"],
                         row["tag"], "" if row["group"] is None else row["group"],
                         row["n_views_r1"], row["n_cart_adds_r1"], row["avg_price_viewed_r1"],
                         row["n_orders_r1"], row["r1_spend"],
                         feats.get("y", ""), feats.get("revenue_r2", ""), feats.get("discount_r2", "")])
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=uplix-{src}.csv"})


@admin.route("/data/reset", methods=["POST"])
def reset_real_data():
    """Wipe real customers only. Refused during a randomised run; synthetic rows never touched."""
    st = state_row()
    if st["randomized"] and st["state"] != "CLOSED":
        flash("Reset is refused: the split is locked until the experiment is CLOSED.", "error")
        return redirect(url_for("admin.data"))
    with transaction() as conn:
        for table in ("orders", "events", "assignments"):
            conn.execute(f"delete from {table} where participant_id in "
                         f"(select id from participants where source = 'real')")
        conn.execute("delete from participants where source = 'real'")
        conn.execute("update app_state set state = 'SETUP', randomized = false, seed = null where id = 1")
    flash("Real data deleted. Synthetic rows are untouched; state is back to SETUP.", "ok")
    return redirect(url_for("admin.data"))
