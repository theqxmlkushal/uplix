"""Customer side of Uplix Live (Member A): sign up, browse, cart, checkout, orders.

The shop is a fake: cash on delivery only, nothing is delivered or charged. The rules kept in
this file (README section 5):

  * nothing is written to disk - sessions live in the signed cookie, data lives in Postgres
  * every price, discount and total is computed here from database prices, never from the browser
  * events are logged only while ROUND1 or ROUND2 is open, with that round number
  * coupon text is rendered only for a Round 2 customer whose assignments.t = 1
"""
import os
import re
import secrets
import time
from functools import wraps

import psycopg.errors
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from psycopg.types.json import Jsonb
from werkzeug.security import check_password_hash, generate_password_hash

from db import query

shop = Blueprint("shop", __name__, template_folder="templates")

# Shown on the sign-up page, word for word (README 5.4).
CONSENT_NOTICE = ("This is a class experiment. Your shopping behaviour is recorded. "
                  "Nothing is delivered or charged. Data is deleted after the course.")

# Why ordering is open or closed, in plain words. Never mentions any offer.
STATE_NOTICES = {
    "SETUP": "The shop has not opened yet - you can browse, ordering is closed.",
    "ROUND1": "Round 1 is open - ordering is available.",
    "BETWEEN": "Short break - ordering is paused.",
    "ROUND2": "Round 2 is open - ordering is available.",
    "CLOSED": "The session is over - ordering is closed.",
}

ORDER_STATES = ("ROUND1", "ROUND2")          # the only states where orders are accepted
ROUNDS = {"ROUND1": 1, "ROUND2": 2}          # state -> round number, None when no round

CATEGORY_EMOJI = {
    "Clothing": "\U0001F455", "Footwear": "\U0001F45F", "Bags": "\U0001F392",
    "Home": "\U0001F3E0", "Stationery": "✏️", "Electronics": "\U0001F3A7",
    "Accessories": "\U0001F576️", "Fitness": "\U0001F9D8",
}

SIGNIN_FAIL_LIMIT = 5        # wrong tries in a row before the delay starts
SIGNIN_WINDOW = 60.0         # seconds the counter stays alive
MAX_CART_LINES = 20          # cookie size limit (README 5.5)
MAX_QTY = 99                 # per line, so one line cannot fill the cookie


# --------------------------------------------------------------------------- small helpers

def int_or(value, default):
    """Parse a form value as an int, or fall back to the default."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def money(amount):
    """Format rupees for display: 1499 -> 1,499."""
    return f"{int(amount):,}"


def emoji_for(category):
    """One emoji per category is the whole product picture (no image files)."""
    return CATEGORY_EMOJI.get(category, "\U0001F6CD️")


def app_state():
    """The single app_state row, read once per request."""
    if "app_state" not in g:
        g.app_state = query(
            "select state, randomized, seed, coupon_amount, coupon_min_subtotal "
            "from app_state where id = 1", one=True)
    return g.app_state


def current_round(state):
    """1 or 2 when a round is open, otherwise None (events are only logged inside a round)."""
    return ROUNDS.get(state)


def current_participant():
    """The signed-in real customer, or None. Cached for the length of one request."""
    if "me" not in g:
        g.me = None
        pid = session.get("participant_id")
        if pid:
            g.me = query(
                "select id, nickname, excluded from participants "
                "where id = %s and source = 'real'", (pid,), one=True)
            if g.me is None:              # stale cookie: forget it quietly
                session.pop("participant_id", None)
    return g.me


def login_required(view):
    """Send visitors who are not signed in to the sign-in page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_participant() is None:
            flash("Please sign in to shop.", "notice")
            return redirect(url_for("shop.signin"))
        return view(*args, **kwargs)
    return wrapped


@shop.context_processor
def shared_header():
    """Everything the header and banner need on every page."""
    cart = get_cart()
    return {
        "me": current_participant(),
        "cart_count": sum(cart.values()),
        "state": app_state()["state"],
        "state_notice": STATE_NOTICES[app_state()["state"]],
        "emoji_for": emoji_for,
        "money": money,
    }


# --------------------------------------------------------------------------- events

def log_event(me, event_type, product_id=None, value=None):
    """Write one event with the current round number - only while a round is open."""
    rnd = current_round(app_state()["state"])
    if rnd is None or me is None:
        return
    query(
        "insert into events (participant_id, round, type, product_id, value) "
        "values (%s, %s, %s, %s, %s) returning id",
        (me["id"], rnd, event_type, product_id, value), one=True)


def note_coupon_seen(me, coupon):
    """Log coupon_seen the first time this customer actually sees any such text (A13)."""
    if not coupon:
        return
    seen = query(
        "select 1 as seen from events where participant_id = %s and type = 'coupon_seen'",
        (me["id"],), one=True)
    if not seen:
        log_event(me, "coupon_seen")


# --------------------------------------------------------------------------- cart

def get_cart():
    """The session cart as {product_id: quantity}, with bad rows dropped."""
    cart = {}
    for key, qty in (session.get("cart") or {}).items():
        try:
            pid, n = int(key), int(qty)
        except (TypeError, ValueError):
            continue
        if n > 0:
            cart[pid] = min(n, MAX_QTY)
    return cart


def set_cart(cart):
    """Store the cart, at most 20 lines, so the cookie stays small."""
    session["cart"] = {str(pid): int(qty) for pid, qty in list(cart.items())[:MAX_CART_LINES]}


def cart_lines(cart):
    """Cart rows joined with live database prices: name, price, quantity, line total."""
    if not cart:
        return []
    rows = query(
        "select id, name, category, price from products where id = any(%s)",
        (list(cart.keys()),))
    by_id = {row["id"]: row for row in rows}
    lines = []
    for pid, qty in cart.items():
        product = by_id.get(pid)
        if product:
            lines.append({**product, "qty": qty, "line_total": product["price"] * qty})
    lines.sort(key=lambda line: line["id"])
    return lines


# --------------------------------------------------------------------------- coupon (Round 2 only)

def coupon_for(me, state, subtotal):
    """Discount details for a Round 2 coupon customer, otherwise None.

    Control customers (t = 0), people with no assignment, excluded accounts and everybody in
    Round 1 get None, so not one word of coupon text can reach their pages (A12).
    """
    if state != "ROUND2" or me["excluded"]:
        return None
    row = query("select t from assignments where participant_id = %s", (me["id"],), one=True)
    if not row or row["t"] != 1:
        return None
    st = app_state()
    amount, minimum = st["coupon_amount"], st["coupon_min_subtotal"]
    if subtotal >= minimum:
        return {"applied": True, "amount": amount, "minimum": minimum,
                "discount": min(amount, subtotal), "short": 0}
    return {"applied": False, "amount": amount, "minimum": minimum,
            "discount": 0, "short": minimum - subtotal}


# --------------------------------------------------------------------------- sign up / sign in

@shop.route("/signup", methods=["GET", "POST"])
def signup():
    """Create a customer with a hashed PIN and a consent tick (no email, phone or address)."""
    if request.method == "GET":
        return render_template("shop/signup.html", consent_notice=CONSENT_NOTICE)

    nickname = (request.form.get("nickname") or "").strip()
    pin = (request.form.get("pin") or "").strip()
    error = None
    if not request.form.get("consent"):
        error = "Please tick the consent box to continue."
    elif not re.fullmatch(r"[A-Za-z0-9 _\-]{2,20}", nickname):
        error = "Nickname: 2 to 20 letters, numbers, spaces, _ or -."
    elif not re.fullmatch(r"\d{4}", pin):
        error = "PIN must be exactly 4 digits."
    elif query("select 1 as x from participants where source = 'real' and lower(nickname) = lower(%s)",
               (nickname,), one=True):
        error = "That nickname is taken. Please choose another."
    if error:
        return render_template("shop/signup.html", error=error, nickname=nickname,
                               consent_notice=CONSENT_NOTICE), 400

    # Late visitors can shop but are never counted (README 5.5).
    excluded = app_state()["state"] not in ("SETUP", "ROUND1")
    try:
        row = query(
            "insert into participants (nickname, pin_hash, source, excluded) "
            "values (%s, %s, 'real', %s) returning id",
            (nickname, generate_password_hash(pin), excluded), one=True)
    except psycopg.errors.UniqueViolation:
        return render_template("shop/signup.html",
                               error="That nickname is taken. Please choose another.",
                               nickname=nickname, consent_notice=CONSENT_NOTICE), 400

    session.pop("signin_fails", None)
    session["participant_id"] = row["id"]
    flash(f"Welcome, {nickname}!", "ok")
    return redirect(url_for("shop.home"))


def record_signin_failure():
    """Count one wrong try; the run of failures only lives for one minute."""
    now = time.time()
    state = session.get("signin_fails") or {}
    if state.get("start") and now - state["start"] < SIGNIN_WINDOW:
        state = {"n": state.get("n", 0) + 1, "start": state["start"]}
    else:
        state = {"n": 1, "start": now}
    session["signin_fails"] = state


def throttle_delay():
    """After SIGNIN_FAIL_LIMIT wrong tries in a row, wait out the rest of the minute (A9).

    SIGNIN_DELAY_MAX (seconds, default 60) caps the wait; the tests lower it so they run fast.
    """
    state = session.get("signin_fails") or {}
    if state.get("n", 0) < SIGNIN_FAIL_LIMIT:
        return 0.0
    remaining = SIGNIN_WINDOW - (time.time() - state.get("start", 0))
    if remaining <= 0:
        session.pop("signin_fails", None)
        return 0.0
    delay = min(remaining, float(os.environ.get("SIGNIN_DELAY_MAX", SIGNIN_WINDOW)))
    time.sleep(delay)
    if delay >= remaining:               # we waited the whole minute: start a fresh run
        session.pop("signin_fails", None)
    return delay


@shop.route("/signin", methods=["GET", "POST"])
def signin():
    """Nickname plus PIN. Wrong PIN and unknown nickname give exactly the same message (A9)."""
    if request.method == "GET":
        return render_template("shop/signin.html")

    throttle_delay()                     # a slow answer after 5 wrong tries in a row
    nickname = (request.form.get("nickname") or "").strip()
    pin = (request.form.get("pin") or "").strip()
    row = query(
        "select id, pin_hash from participants where source = 'real' and lower(nickname) = lower(%s)",
        (nickname,), one=True)
    if row is None or not row["pin_hash"] or not check_password_hash(row["pin_hash"], pin):
        record_signin_failure()
        return render_template("shop/signin.html",
                               error="Wrong nickname or PIN. Please try again."), 401

    session.pop("signin_fails", None)
    session["participant_id"] = row["id"]
    return redirect(url_for("shop.home"))


@shop.route("/signout")
def signout():
    """Forget the session: identity, cart and the wrong-try counter all go."""
    session.clear()
    return redirect(url_for("shop.signin"))


# --------------------------------------------------------------------------- catalogue

@shop.route("/")
def home():
    """Search box, category chips and the product grid (open to visitors; buying needs a sign-in)."""
    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    conditions, params = [], []
    if q:
        conditions.append("(name ilike %s or category ilike %s)")
        params += [f"%{q}%", f"%{q}%"]
    if category:
        conditions.append("category = %s")
        params.append(category)
    sql = "select id, name, category, price from products"
    if conditions:
        sql += " where " + " and ".join(conditions)
    sql += " order by category, name"
    return render_template(
        "shop/home.html",
        products=query(sql, tuple(params)),
        categories=[r["category"] for r in query("select distinct category from products order by category")],
        q=q,
        active_category=category)


@shop.route("/product/<int:product_id>")
def product(product_id):
    """One product, its price and the quantity picker. Opening it is a view_item event."""
    row = query("select id, name, category, price from products where id = %s", (product_id,), one=True)
    if row is None:
        abort(404)
    log_event(current_participant(), "view_item", product_id=row["id"])
    in_cart = get_cart().get(row["id"], 0)
    return render_template("shop/product.html", product=row, in_cart=in_cart)


# --------------------------------------------------------------------------- cart actions

def back_to(target):
    """Where a cart action sends the browser back to (never an outside site)."""
    return redirect(url_for(target))


@shop.route("/cart/add", methods=["POST"])
@login_required
def cart_add():
    """Add a product (or add to its line), logging add_to_cart when the line appears."""
    me = current_participant()
    product_id = int_or(request.form.get("product_id"), 0)
    qty = max(1, int_or(request.form.get("qty"), 1))
    row = query("select id from products where id = %s", (product_id,), one=True)
    if row is None:
        flash("That item is not in the catalogue.", "error")
        return back_to("shop.home")
    cart = get_cart()
    if product_id not in cart and len(cart) >= MAX_CART_LINES:
        flash(f"Your cart is full ({MAX_CART_LINES} lines). Remove something first.", "error")
        return redirect(url_for("shop.product", product_id=product_id))
    entering = product_id not in cart or cart.get(product_id, 0) == 0
    cart[product_id] = min(cart.get(product_id, 0) + qty, MAX_QTY)
    set_cart(cart)
    if entering:
        log_event(me, "add_to_cart", product_id=product_id)
    flash("Added to cart.", "ok")
    return redirect(url_for("shop.product", product_id=product_id))


@shop.route("/cart/inc/<int:product_id>", methods=["POST"])
@login_required
def cart_inc(product_id):
    """One more of an item already in the cart."""
    cart = get_cart()
    if product_id in cart:
        cart[product_id] = min(cart[product_id] + 1, MAX_QTY)
        set_cart(cart)
    return back_to("shop.cart")


@shop.route("/cart/dec/<int:product_id>", methods=["POST"])
@login_required
def cart_dec(product_id):
    """One fewer; the line leaves the cart at zero, which is a removal."""
    me = current_participant()
    cart = get_cart()
    if product_id not in cart:
        return back_to("shop.cart")
    cart[product_id] -= 1
    if cart[product_id] <= 0:
        del cart[product_id]
        log_event(me, "remove_from_cart", product_id=product_id)
    set_cart(cart)
    return back_to("shop.cart")


@shop.route("/cart/remove/<int:product_id>", methods=["POST"])
@login_required
def cart_remove(product_id):
    """Take a whole line out of the cart."""
    me = current_participant()
    cart = get_cart()
    if cart.pop(product_id, None) is not None:
        set_cart(cart)
        log_event(me, "remove_from_cart", product_id=product_id)
    return back_to("shop.cart")


# --------------------------------------------------------------------------- cart, checkout, orders

@shop.route("/cart")
@login_required
def cart():
    """Cart lines, quantities, server-side subtotal and (for coupon customers) the offer line."""
    me = current_participant()
    st = app_state()
    lines = cart_lines(get_cart())
    subtotal = sum(line["line_total"] for line in lines)
    coupon = coupon_for(me, st["state"], subtotal)
    note_coupon_seen(me, coupon)
    return render_template("shop/cart.html", lines=lines, subtotal=subtotal, coupon=coupon,
                           can_order=st["state"] in ORDER_STATES)


@shop.route("/checkout")
@login_required
def checkout():
    """Order summary, the one payment option and the place-order form."""
    me = current_participant()
    st = app_state()
    lines = cart_lines(get_cart())
    if not lines:
        flash("Your cart is empty.", "notice")
        return redirect(url_for("shop.cart"))
    subtotal = sum(line["line_total"] for line in lines)
    coupon = coupon_for(me, st["state"], subtotal)
    note_coupon_seen(me, coupon)
    log_event(me, "checkout_start")
    return render_template(
        "shop/checkout.html",
        lines=lines,
        subtotal=subtotal,
        coupon=coupon,
        discount=coupon["discount"] if coupon else 0,
        total=subtotal - (coupon["discount"] if coupon else 0),
        can_order=st["state"] in ORDER_STATES,
        order_key=secrets.token_urlsafe(16))


@shop.route("/checkout/place", methods=["POST"])
@login_required
def place_order():
    """Create one order from server-side prices; a repeated order_key makes no second row (A3)."""
    me = current_participant()
    st = app_state()
    if st["state"] not in ORDER_STATES:
        return render_template("shop/error.html",
                               message="Ordering is closed right now. You can still browse the catalogue."), 403
    lines = cart_lines(get_cart())
    if not lines:
        flash("Your cart is empty.", "notice")
        return redirect(url_for("shop.cart"))

    order_key = (request.form.get("order_key") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", order_key):
        return render_template("shop/error.html",
                               message="That order form expired. Open your cart again and retry."), 400

    subtotal = sum(line["line_total"] for line in lines)
    coupon = coupon_for(me, st["state"], subtotal)
    discount = coupon["discount"] if coupon else 0
    total = subtotal - discount
    items = [{"product_id": line["id"], "qty": line["qty"], "unit_price": line["price"]}
             for line in lines]
    try:
        row = query(
            "insert into orders (order_key, participant_id, round, items, subtotal, discount, "
            "total, coupon_applied, payment_method) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, 'COD') returning id",
            (order_key, me["id"], current_round(st["state"]), Jsonb(items), subtotal,
             discount, total, bool(coupon and coupon["applied"])), one=True)
    except psycopg.errors.UniqueViolation:
        # Pressed twice: orders.order_key is unique, so the first order wins.
        existing = query("select id from orders where order_key = %s", (order_key,), one=True)
        return redirect(url_for("shop.order", order_id=existing["id"]))

    set_cart({})
    log_event(me, "order_placed", value=total)
    flash("Order placed (demo). Nothing is delivered or charged.", "ok")
    return redirect(url_for("shop.order", order_id=row["id"]))


def own_order(order_id):
    """One order, but only when it belongs to the signed-in customer (A10)."""
    me = current_participant()
    row = query("select * from orders where id = %s", (order_id,), one=True)
    if row is None or row["participant_id"] != me["id"]:
        abort(404)
    return row


def order_product_names(order):
    """Product names for the items stored in the order JSON."""
    ids = [item.get("product_id") for item in order["items"]]
    if not ids:
        return {}
    return {r["id"]: r["name"] for r in query("select id, name from products where id = any(%s)", (ids,))}


@shop.route("/orders/<int:order_id>")
@login_required
def order(order_id):
    """Confirmation and order detail: the same page, your own orders only."""
    row = own_order(order_id)
    return render_template("shop/order.html", order=row, names=order_product_names(row))


@shop.route("/orders")
@login_required
def orders():
    """The signed-in customer's orders, newest first. Nobody else's."""
    me = current_participant()
    rows = query("select * from orders where participant_id = %s order by created_at desc, id desc",
                 (me["id"],))
    return render_template("shop/orders.html", orders=rows,
                           show_discount=any(r["discount"] for r in rows))
