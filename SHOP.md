# SHOP.md — the customer shop (Uplix Live, Member A)

The customer-facing half of the demo shop: sign up, browse 18 seeded products, search,
keep a cart in the signed cookie session, check out with cash on delivery only, and read
your own orders. Every page carries the banner *"Demo shop: nothing is delivered or
charged"*.

No admin route or admin link exists anywhere in the customer code — that is Member B's
side (`admin.py`, `stats.py`, `tags.py`, `templates/admin/`, `public/admin.*`, see `ADMIN.md`).
The merged app registers both blueprints and a customer session still cannot open `/admin`.
The shop header's **Sign in** link goes to `/login`, a neutral role chooser outside both
blueprints; that page offers customer and admin, so no shop page needs an `/admin` literal.

## Stack

Python 3.12, Flask 3, psycopg 3 with plain SQL through `db.py`, Jinja templates, plain
CSS in `public/shop.css`. No ORM, no JS framework, no third-party requests. Sessions are
Flask's signed cookie; secrets come from the environment only.

## Layout

| Path | What it is |
| --- | --- |
| `app.py` | Wires the blueprint, `/health`, `/shop.css`, friendly error pages |
| `shop.py` | Every customer route + helpers (events, coupon, throttle) |
| `db.py` | The kit's database helper (plain SQL, `query` / `execute`) |
| `templates/shop/` | Jinja templates (base, home, product, cart, checkout, order, orders, signup, signin, error, _coupon) |
| `public/shop.css` | The whole stylesheet |
| `tests/test_shop.py` | Acceptance checks A1–A14 (T) + extras, 19 tests |
| `requirements.txt`, `.env.example` | Install + every variable name with fake values |
| `sql/` | Schema and seed — read from there at test time, never copied |

## How to run

```powershell
# 1. database (any Postgres; for the tests a throwaway one is fine)
docker run -d --name uplix-pg -e POSTGRES_USER=uplix -e POSTGRES_PASSWORD=testpass `
           -e POSTGRES_DB=uplix_test -p 55432:55432 postgres:16

# 2. install
pip install -r requirements.txt

# 3. environment
$env:DATABASE_URL = "postgresql://uplix:testpass@127.0.0.1:55432/uplix_test"
$env:SECRET_KEY   = "change-me"
python -c "import psycopg; print('ok')"

# 4. schema + seed (once)
python -c "import os, psycopg; c=psycopg.connect(os.environ['DATABASE_URL'], autocommit=True); c.execute(open('sql/schema.sql', encoding='utf-8').read()); c.execute(open('sql/seed_products.sql', encoding='utf-8').read())"

# 5. run
python app.py            # http://127.0.0.1:5000/health
```

### Tests (acceptance checks)

```powershell
$env:TEST_DATABASE_URL = "postgresql://uplix:testpass@127.0.0.1:55432/uplix_test"
python -m pytest -q
```

The schema fixture drops and recreates the `public` schema in that database each run and
**refuses any non-local host** (unless `ALLOW_REMOTE_TEST_DB=1`), so a real database
cannot be wiped by accident. Tests never touch Supabase.

## Acceptance checks (README 5.7)

| # | Check | Status |
| --- | --- | --- |
| A1 (T) | Cart totals correct for 3 carts, incl. removal | ✅ `test_a1_cart_totals_are_correct_for_three_carts` |
| A2 (T) | Only "Cash on delivery"; demo banner everywhere | ✅ `test_a2_only_cash_on_delivery_and_banner_everywhere` |
| A3 (T) | Same `order_key` twice → exactly one order | ✅ `test_a3_the_same_order_key_creates_exactly_one_order` |
| A4 (T) | Browser-sent prices ignored | ✅ `test_a4_prices_sent_from_the_browser_are_ignored` |
| A5 (T) | No email/phone/address; PIN stored hashed | ✅ `test_a5_no_pii_fields_and_the_pin_is_stored_hashed` |
| A6 (T) | Each action logs its event with the right round | ✅ `test_a6_each_action_writes_its_event_with_the_right_round` |
| A7 (T) | Ordering refused in SETUP/BETWEEN/CLOSED, allowed in ROUND1/2 | ✅ `test_a7_ordering_is_refused_in_closed_states_and_accepted_in_rounds` |
| A8 (T) | Sign-up in BETWEEN → `excluded = true` | ✅ `test_a8_someone_who_signs_up_between_rounds_is_excluded` |
| A9 (T) | Sign-in rules + 6th try delayed | ✅ `test_a9_signin_rules` |
| A10 (T) | My orders shows only my orders (two customers) | ✅ `test_a10_my_orders_shows_only_my_own_orders` |
| A11 (T) | Search by name and by category | ✅ `test_a11_search_finds_items_by_name_and_by_category` |
| A12 (T) | Coupon maths 450→350, 250→250 + hint; control sees neither word | ✅ `test_a12_coupon_maths_and_the_control_group_sees_no_offer_text` |
| A13 (T) | `coupon_seen` logged exactly once | ✅ `test_a13_coupon_seen_is_logged_exactly_once` |
| A14 (T) | No `/admin` route or link in customer code | ✅ `test_a14_there_is_no_admin_link_on_customer_pages` |
| A15 | Deployed shop works on a phone over mobile data | ⏳ hand check — needs the Vercel deploy |
| A16 | Rows visible in Supabase table editor after an order | ⏳ hand check — needs the Supabase project |
| A17 | RLS on for every table; no Supabase public key in code | ⏳ hand check — needs the Supabase dashboard |
| A18 | Fresh clone runs from README using only `.env.example` + `requirements.txt` | ⏳ hand check — do once the repo is pushed |

5 extra tests beyond the brief also pass: sign-in prompt on home, signup validation,
empty-cart checkout, `/health`, and "no Flask static folder".

```text
$ python -m pytest -q
...................                                                      [100%]
19 passed in 24.16s
```

## Behaviour notes (the bits worth reading before you touch this)

- **State is read per request** from `app_state.state`: SETUP → ROUND1 → BETWEEN →
  ROUND2 → CLOSED controls what may be ordered and which words may appear.
- **Coupon words are ROUND2 + `assignments.t = 1` only.** Control pages must contain
  neither "coupon" nor "discount", case-insensitive — the test greps the HTML for both.
- **Events only inside a round**, tagged with the round number; `coupon_seen` is logged
  exactly once per coupon customer.
- **Totals are always server-computed.** Prices posted by the browser are ignored (A4).
- **Cart lives in the signed session cookie**, capped at 20 lines; `order_key` is unique
  so a double submit creates one order (A3).
- **PIN is hashed** (`hashlib` + per-participant salt), never stored or echoed plain.

## Test-harness gotcha (fixed — do not reintroduce)

The `client` fixture must **not** be written as `with flask_app.test_client() as c:`.
That context-manager form sets Flask's `preserve_context`, which leaves the application
context on the global stack after every request, so the next request from *any* client
reuses it and inherits the previous client's cached `g.me` — two customers then behave
as one (this is what made A10 and A12 fail). Plain `return flask_app.test_client()`
gives every request its own context, exactly like production.

## Open risks / not done yet

- Deploy to Vercel + the four hand checks (A15–A18) are still outstanding.
- `SIGNIN_DELAY_MAX` (default 60 s) caps the A9 delay so tests stay fast; set it in the
  environment if you want a different ceiling.
- The `round2_prices` fixture temporarily changes two product prices (450 / 250) because
  the seeded catalogue cannot add up to those totals; it restores them afterwards.
