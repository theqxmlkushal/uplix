"""Member A acceptance tests - README section 5.7, checks A1 to A14.

They run against a throwaway local PostgreSQL database, never against Supabase:

    $env:TEST_DATABASE_URL = "postgresql://uplix:testpass@127.0.0.1:55432/uplix_test"
    python -m pytest -q          # from this folder (member-a-shop)

The schema fixture drops and recreates the public schema in that database and refuses any
non-local host (unless ALLOW_REMOTE_TEST_DB=1), so a real database cannot be wiped by accident.
"""
import os
import re
import sys
import time
from urllib.parse import urlparse

import psycopg
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SHOP_DIR = os.path.dirname(HERE)                 # repository root: app.py, shop.py, sql/
KIT_DIR = SHOP_DIR                               # sql/ lives right here at the root

sys.path.insert(0, SHOP_DIR)
from db import execute, query                    # noqa: E402  (path must be set first)

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to a throwaway database")

BANNER = "Demo shop: nothing is delivered or charged"


# --------------------------------------------------------------------------- fixtures

def _local(url):
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


@pytest.fixture(scope="session")
def schema():
    """Drop and recreate the schema in the throwaway database, then seed the 18 products."""
    if not _local(URL) and os.environ.get("ALLOW_REMOTE_TEST_DB") != "1":
        pytest.fail("TEST_DATABASE_URL is not local - refusing to drop a remote database")
    conn = psycopg.connect(URL, autocommit=True, connect_timeout=10)
    conn.execute("drop schema if exists public cascade")
    conn.execute("create schema public")
    for name in ("schema.sql", "seed_products.sql"):
        with open(os.path.join(KIT_DIR, "sql", name), encoding="utf-8") as handle:
            conn.execute(handle.read())
    yield conn
    conn.close()


@pytest.fixture()
def flask_app(schema):
    """A fresh app object per test, importing THIS folder's shop.py and app.py."""
    os.environ["DATABASE_URL"] = URL
    saved = {name: sys.modules.get(name) for name in ("app", "shop")}
    sys.path.insert(0, SHOP_DIR)
    for name in ("app", "shop"):
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
    """A shopper client.

    Deliberately NOT used as a context manager: `with app.test_client()` turns on
    Flask's preserve_context, which leaves the app context on the global stack after
    every request, so the next request from ANY client reuses it and inherits the
    previous client's cached `g.me` (the shopper identity). Without `with`, each
    request gets its own app context, exactly like production.
    """
    return flask_app.test_client()


@pytest.fixture()
def round2_prices(schema):
    """Prices the seeded catalogue cannot add up to: 450 and 250 (README check A12)."""
    execute("update products set price = 450 where id = 9")     # Gel pens
    execute("update products set price = 250 where id = 12")    # Phone stand
    yield
    execute("update products set price = 149 where id = 9")
    execute("update products set price = 249 where id = 12")


# --------------------------------------------------------------------------- helpers

def set_state(state):
    execute("update app_state set state = %s where id = 1", (state,))


def signup(client, nickname, pin="1234", consent="on"):
    return client.post("/signup", data={"nickname": nickname, "pin": pin, "consent": consent})


def signout(client):
    return client.get("/signout")


def add_to_cart(client, product_id, qty=1):
    return client.post("/cart/add", data={"product_id": product_id, "qty": qty})


def place(client, order_key):
    return client.post("/checkout/place", data={"order_key": order_key})


def participant_id(nickname):
    return query("select id from participants where source = 'real' and nickname = %s",
                 (nickname,), one=True)["id"]


def orders_of(pid):
    return query("select * from orders where participant_id = %s order by id", (pid,))


def last_order(pid):
    rows = orders_of(pid)
    return rows[-1] if rows else None


def order_count():
    return query("select count(*) as n from orders", one=True)["n"]


def rounds(pid, event_type):
    """Round numbers of one event type for one customer, in order."""
    return [row["round"] for row in query(
        "select round from events where participant_id = %s and type = %s order by id",
        (pid, event_type))]


def event_count(pid, event_type):
    return query("select count(*) as n from events where participant_id = %s and type = %s",
                 (pid, event_type), one=True)["n"]


def subtotal_of(html):
    match = re.search(r'data-subtotal="(\d+)"', html)
    return int(match.group(1)) if match else None


def flash_of(html):
    match = re.search(r'class="flash error">([^<]+)</div>', html)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- A1 - A14

def test_a1_cart_totals_are_correct_for_three_carts(client):
    """A1: three hand-made carts, including removal, add up correctly."""
    set_state("ROUND1")
    signup(client, "cart_one")

    # cart 1: 2 x Cotton T-shirt (299) + 1 x Notebook pack (199) = 797
    add_to_cart(client, 1, 2)
    add_to_cart(client, 8, 1)
    assert subtotal_of(client.get("/cart").get_data(as_text=True)) == 797

    # cart 2: remove the notebook -> 598
    client.post("/cart/remove/8")
    assert subtotal_of(client.get("/cart").get_data(as_text=True)) == 598

    # cart 3: 3 x Gel pens (149) = 447, minus one = 298, then the line leaves = empty
    client.post("/cart/remove/1")
    add_to_cart(client, 9, 3)
    assert subtotal_of(client.get("/cart").get_data(as_text=True)) == 447
    client.post("/cart/dec/9")
    assert subtotal_of(client.get("/cart").get_data(as_text=True)) == 298
    client.post("/cart/dec/9")
    client.post("/cart/dec/9")
    html = client.get("/cart").get_data(as_text=True)
    assert "Your cart is empty" in html


def test_a2_only_cash_on_delivery_and_banner_everywhere(client):
    """A2: checkout offers exactly one payment option, and every page shows the demo banner."""
    set_state("ROUND1")
    signup(client, "banner_fan")
    add_to_cart(client, 1, 1)

    checkout_html = client.get("/checkout").get_data(as_text=True)
    assert checkout_html.count('type="radio"') == 1
    assert "Cash on delivery" in checkout_html
    for other in ("credit card", "paypal", "upi", "card number", "wallet"):
        assert other not in checkout_html.lower()

    place(client, "banner-" + "a" * 10)
    for path in ("/", "/product/1", "/cart", "/checkout", "/orders", "/signup", "/signin"):
        html = client.get(path, follow_redirects=True).get_data(as_text=True)
        assert BANNER in html, f"no banner on {path}"


def test_a3_the_same_order_key_creates_exactly_one_order(client):
    """A3: double submit is stopped by the unique orders.order_key."""
    set_state("ROUND1")
    signup(client, "double_tap")
    pid = participant_id("double_tap")

    add_to_cart(client, 1, 1)
    key = "once-" + "a" * 10
    before = order_count()
    first = place(client, key)
    assert first.status_code == 302
    assert order_count() == before + 1

    # the cart is filled again and the same button is pressed a second time
    add_to_cart(client, 8, 2)
    second = place(client, key)
    assert order_count() == before + 1
    assert second.status_code == 302
    assert second.headers["Location"].endswith(f"/orders/{last_order(pid)['id']}")


def test_a4_prices_sent_from_the_browser_are_ignored(client):
    """A4: totals come from the database, whatever the form claims."""
    set_state("ROUND1")
    signup(client, "cheater")
    pid = participant_id("cheater")

    client.post("/cart/add", data={"product_id": 1, "qty": 2, "price": "1"})
    place(client, "cheat-" + "b" * 10, )
    row = last_order(pid)
    assert row["subtotal"] == 598 and row["total"] == 598 and row["discount"] == 0
    assert row["coupon_applied"] is False
    assert row["items"] == [{"product_id": 1, "qty": 2, "unit_price": 299}]

    # a second attempt that posts fake totals cannot change anything either
    add_to_cart(client, 8, 1)
    client.post("/checkout/place", data={
        "order_key": "cheat2-" + "c" * 8,
        "subtotal": "1", "total": "1", "discount": "999",
        "coupon_applied": "true", "items": "[]",
    })
    row2 = last_order(pid)
    assert row2["subtotal"] == 199 and row2["total"] == 199 and row2["discount"] == 0


def test_a5_no_pii_fields_and_the_pin_is_stored_hashed(client):
    """A5: no email/phone/address anywhere, and the PIN is never stored in the clear."""
    set_state("ROUND1")
    signup(client, "shy_shopper", pin="4242")
    pid = participant_id("shy_shopper")

    stored = query("select pin_hash from participants where id = %s", (pid,), one=True)["pin_hash"]
    assert stored and stored != "4242" and "4242" not in stored

    add_to_cart(client, 1, 1)
    place(client, "shy----" + "d" * 8)
    for path in ("/", "/product/1", "/cart", "/checkout", "/orders", "/signup", "/signin"):
        html = client.get(path, follow_redirects=True).get_data(as_text=True).lower()
        assert 'type="email"' not in html, path
        assert 'type="tel"' not in html, path
        for word in ("email", "phone", "address"):
            assert f'name="{word}"' not in html, f"{word} field on {path}"


def test_a6_each_action_writes_its_event_with_the_right_round(client):
    """A6: view, add, remove, checkout and order are logged with the open round number."""
    set_state("ROUND1")
    signup(client, "logger")
    pid = participant_id("logger")

    client.get("/product/1")
    add_to_cart(client, 1, 1)
    client.post("/cart/remove/1")
    add_to_cart(client, 1, 1)
    client.get("/checkout")
    place(client, "log----" + "e" * 8)

    assert rounds(pid, "view_item") == [1]
    assert rounds(pid, "add_to_cart") == [1, 1]
    assert rounds(pid, "remove_from_cart") == [1]
    assert rounds(pid, "checkout_start") == [1]
    assert rounds(pid, "order_placed") == [1]
    assert query("select value from events where participant_id = %s and type = 'order_placed'",
                 (pid,), one=True)["value"] == 299

    # the same actions in Round 2 are logged with round 2
    set_state("ROUND2")
    client.get("/product/2")
    add_to_cart(client, 2, 1)
    assert rounds(pid, "view_item") == [1, 2]
    assert rounds(pid, "add_to_cart") == [1, 1, 2]

    # outside a round nothing is logged
    set_state("BETWEEN")
    before = event_count(pid, "view_item")
    client.get("/product/3")
    assert event_count(pid, "view_item") == before


def test_a7_ordering_is_refused_in_closed_states_and_accepted_in_rounds(client):
    """A7: SETUP/BETWEEN/CLOSED refuse the order, ROUND1/ROUND2 accept it."""
    cases = [("SETUP", False), ("BETWEEN", False), ("CLOSED", False),
             ("ROUND1", True), ("ROUND2", True)]
    for index, (state, allowed) in enumerate(cases):
        set_state(state)
        signout(client)
        signup(client, f"state_{index}")
        add_to_cart(client, 1, 1)
        before = order_count()
        response = place(client, f"st{index}-" + "f" * 8)
        after = order_count()
        if allowed:
            assert response.status_code == 302 and after == before + 1, state
        else:
            assert response.status_code == 403 and after == before, state
            # the catalogue still opens, and it says why ordering is closed
            html = client.get("/", follow_redirects=True).get_data(as_text=True)
            assert "ordering is closed" in html or "ordering is paused" in html, state


def test_a8_someone_who_signs_up_between_rounds_is_excluded(client):
    """A8: a late visitor shops normally but is never counted."""
    set_state("BETWEEN")
    signup(client, "late_joiner")
    assert query("select excluded from participants where nickname = 'late_joiner'",
                 one=True)["excluded"] is True

    signout(client)
    set_state("ROUND1")
    signup(client, "early_bird")
    assert query("select excluded from participants where nickname = 'early_bird'",
                 one=True)["excluded"] is False


def test_a9_signin_rules(client, flask_app):
    """A9: right PIN works, wrong PIN and unknown nickname look identical, 6th try is delayed."""
    set_state("ROUND1")
    signup(client, "pin_woman", pin="4242")
    signout(client)

    response = client.post("/signin", data={"nickname": "pin_woman", "pin": "4242"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/")
    signout(client)

    wrong_pin = client.post("/signin", data={"nickname": "pin_woman", "pin": "0000"})
    stranger = flask_app.test_client()
    unknown = stranger.post("/signin", data={"nickname": "nobody_here", "pin": "0000"})
    assert wrong_pin.status_code == unknown.status_code == 401
    assert flash_of(wrong_pin.get_data(as_text=True)) == flash_of(unknown.get_data(as_text=True))

    # five wrong tries, then the sixth answer is delayed (the cap keeps the test quick)
    os.environ["SIGNIN_DELAY_MAX"] = "1"
    try:
        throttled = flask_app.test_client()
        for _ in range(5):
            assert throttled.post("/signin",
                                  data={"nickname": "pin_woman", "pin": "0000"}).status_code == 401
        started = time.monotonic()
        response = throttled.post("/signin", data={"nickname": "pin_woman", "pin": "0000"})
        took = time.monotonic() - started
        assert response.status_code == 401
        assert took >= 0.9, f"the 6th try inside a minute was not delayed ({took:.2f}s)"
    finally:
        os.environ.pop("SIGNIN_DELAY_MAX", None)


def test_a10_my_orders_shows_only_my_own_orders(client, flask_app):
    """A10: two customers, each sees only their own orders, and cannot open the other's."""
    set_state("ROUND1")
    signup(client, "alice")
    add_to_cart(client, 1, 1)
    place(client, "alice--" + "g" * 8)
    alice_order = last_order(participant_id("alice"))["id"]

    other = flask_app.test_client()
    signup(other, "bob")
    add_to_cart(other, 8, 1)
    place(other, "bob-----" + "h" * 8)
    bob_order = last_order(participant_id("bob"))["id"]
    assert alice_order != bob_order

    html = client.get("/orders").get_data(as_text=True)
    assert f"/orders/{alice_order}" in html
    assert f"/orders/{bob_order}" not in html

    assert client.get(f"/orders/{bob_order}").status_code == 404
    assert other.get(f"/orders/{alice_order}").status_code == 404


def test_a11_search_finds_items_by_name_and_by_category(client):
    """A11: the search box matches names and categories."""
    set_state("ROUND1")
    signup(client, "searcher")

    html = client.get("/?q=jacket").get_data(as_text=True)
    assert "Denim jacket" in html and "Running shoes" not in html

    html = client.get("/?q=footwear").get_data(as_text=True)
    assert "Running shoes" in html and "Canvas sneakers" in html and "Backpack" not in html

    html = client.get("/?category=Bags").get_data(as_text=True)
    assert "Backpack" in html and "Cotton T-shirt" not in html

    html = client.get("/?q=zzzznothing").get_data(as_text=True)
    assert "Nothing matches" in html


def test_a12_coupon_maths_and_the_control_group_sees_no_offer_text(client, flask_app, round2_prices):
    """A12: 450 pays 350, 250 pays 250 with the hint, and the control pages have no such words."""
    set_state("ROUND1")
    signup(client, "coupon_fan")
    fan = participant_id("coupon_fan")
    set_state("BETWEEN")
    execute("insert into assignments (participant_id, t, e, seed) values (%s, 1, 0.5, 1)", (fan,))
    set_state("ROUND2")

    # subtotal 450 -> pays 350
    add_to_cart(client, 9, 1)
    html = client.get("/cart").get_data(as_text=True)
    assert "Coupon applied: 100 off" in html
    client.get("/checkout")
    place(client, "fan450--" + "i" * 8)
    row = last_order(fan)
    assert (row["subtotal"], row["discount"], row["total"]) == (450, 100, 350)
    assert row["coupon_applied"] is True

    # subtotal 250 -> pays 250 and sees the hint
    add_to_cart(client, 12, 1)
    html = client.get("/cart").get_data(as_text=True)
    assert "Add items worth 50 more to use your coupon" in html
    client.get("/checkout")
    place(client, "fan250--" + "j" * 8)
    row = last_order(fan)
    assert (row["subtotal"], row["discount"], row["total"]) == (250, 0, 250)
    assert row["coupon_applied"] is False

    # a no-coupon customer with the same basket sees neither word on any page
    control = flask_app.test_client()
    signup(control, "no_offer")
    cid = participant_id("no_offer")
    execute("insert into assignments (participant_id, t, e, seed) values (%s, 0, 0.5, 1)", (cid,))
    add_to_cart(control, 9, 1)
    add_to_cart(control, 12, 1)
    control.get("/checkout")
    place(control, "ctrl0---" + "c" * 8)
    control_order = last_order(cid)["id"]
    for path in ("/", "/product/9", "/cart", "/checkout", "/orders", f"/orders/{control_order}"):
        html = control.get(path, follow_redirects=True).get_data(as_text=True).lower()
        assert "coupon" not in html, f"coupon text on {path}"
        assert "discount" not in html, f"discount text on {path}"

    # and the control order really is untouched
    row = last_order(cid)
    assert (row["subtotal"], row["discount"], row["total"]) == (700, 0, 700)


def test_a13_coupon_seen_is_logged_exactly_once(client):
    """A13: one coupon_seen event however many times the customer looks."""
    set_state("ROUND1")
    signup(client, "seen_once")
    pid = participant_id("seen_once")
    set_state("BETWEEN")
    execute("insert into assignments (participant_id, t, e, seed) values (%s, 1, 0.5, 1)", (pid,))
    set_state("ROUND2")

    add_to_cart(client, 9, 1)
    for _ in range(3):
        client.get("/cart")
        client.get("/checkout")
    assert event_count(pid, "coupon_seen") == 1
    assert rounds(pid, "coupon_seen") == [2]


def test_a14_there_is_no_admin_route_and_no_admin_link(client):
    """A14: no customer page links to /admin, and /admin does not exist here."""
    set_state("ROUND1")
    signup(client, "normie")
    add_to_cart(client, 1, 1)
    place(client, "normie--" + "k" * 8)
    for path in ("/", "/product/1", "/cart", "/checkout", "/orders", "/signup", "/signin"):
        html = client.get(path, follow_redirects=True).get_data(as_text=True)
        assert 'href="/admin' not in html and 'action="/admin' not in html, path
    assert client.get("/admin").status_code == 404
    for filename in ("shop.py", "app.py"):
        with open(os.path.join(SHOP_DIR, filename), encoding="utf-8") as handle:
            source = handle.read()
        assert '"/admin' not in source and "'/admin" not in source, filename


# --------------------------------------------------------------------------- extra guards

def test_home_is_public_but_buying_needs_a_sign_in(client):
    """Visitors may browse (kit rule: anonymous `/` shows the catalogue); cart and checkout wait for them."""
    html = client.get("/").get_data(as_text=True)
    assert BANNER in html
    assert html.count('class="card"') == 18

    for path in ("/cart", "/checkout", "/orders"):
        response = client.get(path)
        assert response.status_code == 302 and response.headers["Location"].endswith("/signin"), path


def test_signup_needs_consent_a_valid_pin_and_a_free_nickname(client):
    set_state("ROUND1")
    assert signup(client, "no_consent", consent=None).status_code == 400
    assert "consent" in flash_of(signup(client, "no_consent", consent=None).get_data(as_text=True))

    signout(client)
    response = client.post("/signup", data={"nickname": "bad_pin", "pin": "12", "consent": "on"})
    assert response.status_code == 400
    assert "4 digits" in flash_of(response.get_data(as_text=True))

    signout(client)
    assert signup(client, "first_one").status_code == 302
    signout(client)
    response = signup(client, "first_one")
    assert response.status_code == 400
    assert "taken" in flash_of(response.get_data(as_text=True))


def test_checkout_with_an_empty_cart_goes_back_to_the_cart(client):
    set_state("ROUND1")
    signup(client, "empty_cart")
    response = client.get("/checkout")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/cart")


def test_health_counts_tables_and_reports_the_state(client):
    data = client.get("/health").get_json()
    assert data["status"] == "ok"
    assert data["counts"]["products"] == 18
    assert data["state"] in {"SETUP", "ROUND1", "BETWEEN", "ROUND2", "CLOSED"}


def test_no_flask_static_folder_is_used(flask_app):
    assert flask_app.static_folder is None
