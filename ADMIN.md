# ADMIN.md — the admin console (Member B, B1–B6)

The operations half of Uplix Live, sharing one Flask app and one Supabase database with the
customer shop. It runs the real experiment (Round 1 → randomize/launch → Round 2 → results),
shows tag/behaviour dashboards, scores a customer through the ML service, and exports the data.

The customer side lives in `shop.py` + `templates/shop/` and never links here (A14). The
neutral role chooser lives at `/login` (outside both blueprints); shop pages link there, and
only that page offers the `/admin` choice.

The admin side lives in `admin.py` + `stats.py` + `tags.py` + `templates/admin/`.

## Routes

| Route | What it does |
| --- | --- |
| `/login` | Neutral, shop-styled role chooser: customer → `/signin`, admin → `/admin/login` |
| `/admin/login` | Account ID (`ADMIN_ID`) **and** password (`ADMIN_PASSWORD`), both compared with `hmac.compare_digest`; 5 wrong tries then a pause |
| `/admin/` | Overview: big bold uplift hero, next-step CTA, KPI cards, funnel, orders over time, types, coupon effect, power calculator |
| `/admin/customers` | Search + tag filter, group, exclusion (only before randomizing) |
| `/admin/customers/<id>` | Profile, tag, group, Round 1 numbers, orders, events, **Score this customer** |
| `/admin/orders` | Order list filtered by round and coupon |
| `/admin/experiment` | One-click **Launch treatment**, state machine, randomization lock, balance table, type × group table, customer list |
| `/admin/results` | Big bold uplift hero, group sizes, order-rate effect, exact p, bootstrap interval, revenue effect, verdict + reasons, power table |
| `/admin/model` | Synthetic model results from the ML service (sample fallback), every chart labelled SIMULATED |
| `/admin/data` | Row counts, CSV export links, reset real data |
| `/admin/export.csv?source=…` | One row per customer: Round 1 features, tag, group, Round 2 outcome |
| `/admin/api/score/<id>` (POST) | Server-to-server call to `ML_API_URL/predict/user/<id>` |
| `/admin/experiment/<action>` (POST) | `start-round1`, `close-round1`, `randomize`, `launch`, `start-round2`, `close-round2` |
| `/admin/logout` (POST) | Clears `is_admin`; the shop's own session stays signed in |

**One-click treatment.** `launch` is the shortcut the overview and experiment pages show in
`BETWEEN`: it draws the exact 50/50 split and moves the state to `ROUND2` in one transaction,
so the coupon is live for treated customers immediately. The split is locked on the first
click. The separate `randomize` + `start-round2` pair still works for a controlled pause.

Every `/admin` page and API endpoint requires the signed `is_admin` session flag. A customer
session (`participant_id` only) is redirected to the login page, and API calls get `401`.

## Environment

| Variable | Needed for |
| --- | --- |
| `DATABASE_URL` | Every page (Supabase **Transaction pooler** string, port 6543) |
| `SECRET_KEY` | Signing both the shop and admin cookies |
| `ADMIN_ID` | Admin account ID at `/admin/login` (defaults to `admin`) |
| `ADMIN_PASSWORD` | Admin password at `/admin/login` |
| `ML_API_URL` | Model page and the Score button (project 2, e.g. `https://uplix-tp.vercel.app`) |
| `ML_API_KEY` | Same value as the ML project; sent as `X-API-Key`, never to a browser |
| `ADMIN_BLOCK_SECONDS` | Optional; pause after 5 wrong credentials (default 60). Tests set it to 1 |
| `SESSION_COOKIE_SECURE` | Optional override. On Vercel the cookie is `Secure` automatically (`VERCEL=1`) |

Set `ADMIN_ID` and `ADMIN_PASSWORD` together. An empty value for either denies every login.

Set `ML_API_URL` and `ML_API_KEY` on **project 1** as well; without them the Model page falls
back to `sample_data/model_run_synthetic.json` and the Score button answers `503` with a clear
message.

## Rules the code keeps

- **Sessions, not files.** The admin flag lives in Flask's signed cookie; data lives in Postgres.
  Nothing is written to disk at runtime. Cookies are `HttpOnly`, `SameSite=Lax`, and `Secure`
  on Vercel.
- **Constant-time credential check.** `hmac.compare_digest` runs on both the account ID and the
  password, even when the first half already failed. An unset `ADMIN_ID` or `ADMIN_PASSWORD`
  denies every login.
- **Legal moves only.** `SETUP → ROUND1 → BETWEEN → ROUND2 → CLOSED`; anything else is refused by
  the server, not just hidden in the HTML.
- **Randomize/launch once.** Both work only in `BETWEEN`, take an exact 50/50 split
  (`ceil(n/2)` treated), use a stored integer seed, and write `app_state.randomized = true` as
  the lock in an atomic `UPDATE … WHERE randomized = false AND state = 'BETWEEN'`. The
  assignments and the optional move to `ROUND2` land in the same transaction. A second click
  cannot redraw the split. Launch requires at least two real customers so both groups exist.
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

- `tests/test_admin.py` (36 tests): ID + password auth/401/redirect, constant-time compare of
  both halves, throttle and block expiry, neutral `/login` chooser, customer session cannot
  open `/admin`, no `/admin` link in shop pages, legal state moves, randomize/launch exact
  split/once/lock, one-click `BETWEEN → ROUND2`, launch refusal and atomic race, exclude lock,
  coupon gating, golden results, big uplift hero, verdict thresholds, statistics/power, CSV
  export, reset refusal and CLOSED reset, live-model payload unwrapping, score endpoint
  (200/503/502), single-group results, `vercel.json` + cookie readiness, shop palette.
- Never point `TEST_DATABASE_URL` at Supabase: the fixture drops the schema. It refuses non-local
  hosts unless `ALLOW_REMOTE_TEST_DB=1`.

## Deploy (project 1, merged app)

1. Push `main`. Project 1 serves the merged app from the repository root; no build command and
   no output directory are needed.
2. Vercel → project 1 → Settings → Environment Variables:
   `DATABASE_URL`, `SECRET_KEY`, `ADMIN_ID`, `ADMIN_PASSWORD`, `ML_API_URL`, `ML_API_KEY`.
   Optional: `ADMIN_BLOCK_SECONDS`, `SIGNIN_DELAY_MAX`, `SESSION_COOKIE_SECURE`.
3. Redeploy after changing any variable. `vercel.json` sets `app.py` to `maxDuration: 60`.
   `.vercelignore` keeps `.env`, tests, notebooks and the ML service out of the function, while
   `sample_data/` still ships for the Model fallback.
4. Check `https://<project-1>/health`, then `/login`, then sign in at `/admin/login` with the
   account ID and password. Open every sidebar page and switch Real/Synthetic.
5. If the ML project is asleep, the Model page shows the bundled sample; that is expected and
   labelled.
6. Run the one-click rehearsal in **B6** before the real window.

The database helper keeps **one connection per request** (cached on Flask's `g`, closed in the
app teardown). Without that, every widget on the admin pages would pay for its own TLS
handshake to Supabase. Use Supabase's **Transaction pooler** URL (port 6543), not the direct
connection.

## B6 — dress rehearsal (3 people)

1. **Before**: confirm `/health` on both projects and `/admin/login`; state should be `SETUP`.
   If old test data exists, open Data → Reset real data.
2. Start Round 1. Three people sign up in the shop and browse/order normally. Close Round 1.
3. Experiment → **Launch treatment** (or Overview → the same button). It randomizes and opens
   Round 2 together. Note the seed and the 2/1 (or 2/2) split; the lock is now on.
4. The coupon group sees the coupon on cart/checkout; control sees no coupon text. Close Round 2
   from Overview or Experiment.
5. Results: check group sizes, effect, exact p, interval and verdict. It will almost certainly be
   **inconclusive** with three people — that is the correct answer, not a bug.
6. Export CSV, then Data → Reset real data (allowed now that the state is `CLOSED`), and confirm
   the synthetic counts are unchanged.

## B7 — real run

1. **Before**: resume Supabase if paused; warm both projects; export/reset any rehearsal rows.
   Keep a copy of `app_state` and current counts. Do not reset after randomization.
2. Run Round 1 for the agreed window. Exclude test accounts from Experiment/Customers *before*
   randomizing.
3. Close Round 1 → **Launch treatment** (records the seed and opens Round 2 in one click).
   Close Round 2 when the window ends.
4. Results/Overview: read the big uplift number, the verdict and the reasons. 40+ customers per
   the power table is the target.
5. Data → **Download real CSV**. In Supabase, copy the `participants`, `events`, `orders`,
   `assignments` and `app_state` tables (free plan has no backups).
6. After the class, reset real data from the Data page, or delete the personal rows in the
   Supabase table editor. Synthetic data and `model_runs` stay.

## Merge notes and visual language

- `app.py` registers both blueprints, serves `/login` (the neutral role chooser), and serves
  `/shop.css`, `/admin.css`, `/admin.js` and `/favicon.svg` in local development; on Vercel the
  `public/` folder is served directly.
- The shop header's **Sign in** now goes to `/login`, where the user picks customer or admin.
  Shop pages still contain no literal `/admin` link (A14); only the neutral chooser does.
- Admin styling mirrors `public/shop.css`: ink `#14161c`, accent cyan `#22b8cf`, accent-dark
  `#147d91`, canvas `#f4f6f8`, white 14px cards, cyan primary buttons, emoji nav icons, and
  the same banner/flash language. `public/admin.js` uses the same chart palette.
- `.vercelignore` no longer excludes `sample_data/`, so the model fallback ships to project 1.
  It now also excludes `.env` and `.env.*`.
- `tests/test_shop.py::test_a14…` now asserts that shop pages never link `/admin` **and** that
  `/admin` is locked behind the admin login (the merged project has both sides).
- The ML service returns `{created_at, simulated, results}` from `/graphs/latest`; the admin
  page accepts that shape and the flat sample-file shape.
