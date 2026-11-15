# ADMIN.md — the admin console (Member B, B1–B5)

The operations half of Uplix Live, sharing one Flask app and one Supabase database with the
customer shop. It runs the real experiment (Round 1 → randomize → Round 2 → results), shows
tag/behaviour dashboards, scores a customer through the ML service, and exports the data.

The customer side lives in `shop.py` + `templates/shop/` and never links here (A14).
The admin side lives in `admin.py` + `stats.py` + `tags.py` + `templates/admin/`.

## Routes

| Route | What it does |
| --- | --- |
| `/admin/login` | One password (`ADMIN_PASSWORD`), compared with `hmac.compare_digest`; 5 wrong tries then a pause |
| `/admin/` | Overview: KPI cards, funnel per round, orders over time, customer types, coupon effect, power calculator |
| `/admin/customers` | Search + tag filter, group, exclusion (only before randomizing) |
| `/admin/customers/<id>` | Profile, tag, group, Round 1 numbers, orders, events, **Score this customer** |
| `/admin/orders` | Order list filtered by round and coupon |
| `/admin/experiment` | State machine, randomization lock, balance table, type × group table, customer list |
| `/admin/results` | Group sizes, order-rate effect, exact p, bootstrap interval, revenue effect, verdict + reasons, power table |
| `/admin/model` | Synthetic model results from the ML service (sample fallback), every chart labelled SIMULATED |
| `/admin/data` | Row counts, CSV export links, reset real data |
| `/admin/export.csv?source=…` | One row per customer: Round 1 features, tag, group, Round 2 outcome |
| `/admin/api/score/<id>` (POST) | Server-to-server call to `ML_API_URL/predict/user/<id>` |
| `/admin/experiment/<action>` (POST) | `start-round1`, `close-round1`, `randomize`, `start-round2`, `close-round2` |
| `/admin/logout` (POST) | Clears `is_admin`; the shop's own session stays signed in |

Every `/admin` page and API endpoint requires the signed `is_admin` session flag. A customer
session (`participant_id` only) is redirected to the login page, and API calls get `401`.

## Environment

| Variable | Needed for |
| --- | --- |
| `DATABASE_URL` | Every page (Supabase **Transaction pooler** string, port 6543) |
| `SECRET_KEY` | Signing both the shop and admin cookies |
| `ADMIN_PASSWORD` | `/admin/login` |
| `ML_API_URL` | Model page and the Score button (project 2, e.g. `https://uplix-tp.vercel.app`) |
| `ML_API_KEY` | Same value as the ML project; sent as `X-API-Key`, never to a browser |
| `ADMIN_BLOCK_SECONDS` | Optional; pause after 5 wrong passwords (default 60). Tests set it to 1 |

Set `ML_API_URL` and `ML_API_KEY` on **project 1** as well; without them the Model page falls
back to `sample_data/model_run_synthetic.json` and the Score button answers `503` with a clear
message.

## Rules the code keeps

- **Sessions, not files.** The admin flag lives in Flask's signed cookie; data lives in Postgres.
  Nothing is written to disk at runtime.
- **Constant-time password check.** `hmac.compare_digest` on bytes; an unset `ADMIN_PASSWORD`
  denies every login.
- **Legal moves only.** `SETUP → ROUND1 → BETWEEN → ROUND2 → CLOSED`; anything else is refused by
  the server, not just hidden in the HTML.
- **Randomize once.** It works only in `BETWEEN`, takes an exact 50/50 split
  (`ceil(n/2)` treated), uses a stored integer seed, writes `app_state.randomized = true` as the
  lock in an atomic `UPDATE … WHERE randomized = false`, and stores `seed` + `e` on every
  assignment in one transaction. A second click cannot redraw the split.
- **Remove customers only before randomizing.** The exclude form disappears and the POST is
  refused once `randomized` is true.
- **Coupon text only in Round 2 for `assignments.t = 1`.** Control and Round 1 pages contain
  neither "coupon" nor "discount" (enforced by `shop.coupon_for`, checked in tests).
- **Reset real data** is refused while a randomized run is not `CLOSED`. It deletes only
  `participants.source = 'real'` and their rows, never synthetic data or `model_runs`, and
  returns the state to `SETUP` / `randomized = false` / `seed = null`.
- **Synthetic data is always labelled.** Switching to Synthetic shows a SIMULATED badge; the
  model page and every score say the model was trained on synthetic data.

## Results maths (`stats.py`)

- **Effect** = coupon order rate − control order rate.
- **Exact randomization test**: enumerates all splits when `C(n, n1) ≤ 50,000` (252 for 5 vs 5),
  otherwise draws 20,000 seeded splits. Binary outcomes use the hypergeometric distribution, so
  a 10,000-customer page stays in bounded memory. The same data always gives the same p.
- **Bootstrap interval**: percentile bootstrap, 2,000 resamples, seed 0.
- **Verdict**: `reliable` only with 40+ customers, exact p < 0.05 and an interval excluding zero.
  Otherwise `inconclusive` with the exact reasons.
- **Golden check**: treated orders `1,1,1,0,1` vs control `1,0,0,1,0` → effect **0.4**, exact p
  **0.5238** (132/252), interval **−0.2 to 1.0**, verdict **inconclusive**.
- **Power table** (per group): 20→30% 291; 20→40% 79; 30→50% 91; 30→60% 40; 40→80% 20;
  50→80% 36.

Tags (`tags.py`), in order: **Regular** (2+ Round 1 orders), **Big basket** (at least one order
and average order value at or above the median among customers who ordered), **Window shopper**
(5+ views, no order), **Newcomer**. Tags use Round 1 only, so they cannot leak the coupon effect.

## Charts

`public/admin.js` draws every chart as hand-written SVG from JSON already on the page:
funnel, orders-over-time with round shading, customer-type doughnut (click a slice → filtered
Customers page), strategy bars with whiskers, budget curve with a slider, Qini curve, and the
power calculator. No CDN, no Chart.js, no third-party request.

> Deviation from the brief's file list: it names `public/chart.umd.js`. Because this project may
> not make third-party requests and the chart needs are small, the charts are vanilla SVG in
> `public/admin.js` instead. Behaviour and the visible charts are the same.

## Tests

```powershell
$env:TEST_DATABASE_URL = "postgresql://uplix:testpass@127.0.0.1:55432/uplix_test"
python -m pytest -q
```

- `tests/test_admin.py` (27 tests): auth/401/redirect, constant-time compare, throttle and block
  expiry, customer session cannot open `/admin`, no `/admin` link in shop pages, legal state
  moves, randomize exact split/once/lock, exclude lock, coupon gating, golden results, verdict
  thresholds, statistics/power, CSV export, reset refusal and CLOSED reset, live-model payload
  unwrapping, score endpoint (200/503/502), single-group results.
- Never point `TEST_DATABASE_URL` at Supabase: the fixture drops the schema. It refuses non-local
  hosts unless `ALLOW_REMOTE_TEST_DB=1`.

## Deploy (project 1)

1. Project 1 already serves the shop from the repository root. Push `main`.
2. In Vercel → project 1 → Settings → Environment Variables add `ADMIN_PASSWORD` (if missing),
   `ML_API_URL` and `ML_API_KEY` (same key as project 2). Redeploy.
3. Check `https://<project-1>/health`, then `/admin/login`. Sign in and open every sidebar page.
4. If the ML project is asleep, the Model page shows the bundled sample; that is expected and
   labelled.

The database helper keeps **one connection per request** (cached on Flask's `g`, closed in the
app teardown). Without that, every widget on the admin pages would pay for its own TLS
handshake to Supabase.

## B6 — dress rehearsal (3 people)

1. **Before**: confirm `/health` on both projects and `/admin/login`; state should be `SETUP`.
   If old test data exists, open Data → Reset real data.
2. Start Round 1. Three people sign up in the shop and browse/order normally. Close Round 1.
3. Experiment → **Randomize**. Note the seed and the 2/1 (or 2/2) split; the lock is now on.
4. Start Round 2. The coupon group sees the coupon on cart/checkout; control sees no coupon text.
   Close Round 2.
5. Results: check group sizes, effect, exact p, interval and verdict. It will almost certainly be
   **inconclusive** with three people — that is the correct answer, not a bug.
6. Export CSV, then Data → Reset real data (allowed now that the state is `CLOSED`), and confirm
   the synthetic counts are unchanged.

## B7 — real run

1. **Before**: resume Supabase if paused; warm both projects; export/reset any rehearsal rows.
   Keep a copy of `app_state` and current counts. Do not reset after randomization.
2. Run Round 1 for the agreed window. Exclude test accounts from Experiment/Customers *before*
   randomizing.
3. Close Round 1 → Randomize (record the seed) → Start Round 2 → Close Round 2.
4. Results: read the verdict and the reasons. 40+ customers per the power table is the target.
5. Data → **Download real CSV**. In Supabase, copy the `participants`, `events`, `orders`,
   `assignments` and `app_state` tables (free plan has no backups).
6. After the class, reset real data from the Data page, or delete the personal rows in the
   Supabase table editor. Synthetic data and `model_runs` stay.

## Merge notes

- `app.py` registers both blueprints and serves `/admin.css` + `/admin.js` in local development;
  on Vercel the `public/` folder is served directly.
- `.vercelignore` no longer excludes `sample_data/`, so the model fallback ships to project 1.
- `tests/test_shop.py::test_a14…` now asserts that shop pages never link `/admin` **and** that
  `/admin` is locked behind the admin login (the merged project has both sides).
- The ML service returns `{created_at, simulated, results}` from `/graphs/latest`; the admin
  page accepts that shape and the flat sample-file shape.
