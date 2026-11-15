# Uplix Live

A fake-money shopping app that runs a **randomized coupon experiment** on real people, plus an **admin console** that shows what the coupon did, and a small **machine-learning service** that predicts *who a coupon would actually change*.

This README is for everyone on the team. Read sections 1 to 3 to understand the project. Then read your own part: **Member A** reads section 5, **Member B** reads sections 6 to 12.

---

## 0. Status at a glance

| Part | Status |
| --- | --- |
| Database schema (7 tables, 2 views, row-level security) | **Tested** on a local PostgreSQL 16 |
| Synthetic data generator (10,000 users, same tables as real data) | **Tested** |
| Feature engineering (one function, used for training and serving) | **Tested** |
| Model training notebook (traditional vs uplift, honesty checks, writes `model.json`) | **Tested** (runs in about 20 seconds) |
| FastAPI prediction service (numpy only) | **Tested** locally (12 ML and service tests) |
| 25 automated tests in total | All pass locally |
| Anything running on **Supabase** or **Vercel** | **Not tested yet.** The first job on Day 1 is to prove the link with the `/health` page |
| Customer shopping app | **To build** (Member A, section 5) |
| Admin console, experiment controls, dashboard graphs | **To build** (Member B, sections 10 and 11) |

Facts that depend on outside services (Vercel limits, Supabase free plan) come from their 2026 documentation and pricing pages and are marked **[Likely]** when I could not confirm them on a live account.

---

## 1. The idea in plain words

**Most targeting asks: "who is likely to buy?"** That sends coupons to people who would have bought anyway, so the discount is wasted, and sometimes annoys people the offer pushes away.

**Uplift asks: "whose behaviour changes because of the coupon?"** Those are different people.

| Customer type | Buys without a coupon? | Buys with a coupon? | What a coupon does |
| --- | --- | --- | --- |
| Persuadable | No | Yes | Creates a sale. The only place the discount pays off |
| Sure thing | Yes | Yes | Gives the discount away for nothing |
| Lost cause | No | No | Wastes the cost of reaching them |
| Sleeping dog | Yes | No | Loses a sale the user would have made |

A "likely buyer" model puts sure things at the top. An uplift model puts persuadables at the top.

**Illustrative example (hand-picked numbers, not evidence).** 1,000 customers: 100 persuadables, 300 sure things, 500 lost causes, 100 sleeping dogs. A purchase is worth 300 and the coupon takes 100 off.

| Strategy | Total profit |
| --- | --- |
| No coupons | 87,000 |
| Coupon for everyone | 65,000 |
| Coupon for the likeliest buyers (the sure things) | 63,000 |
| Coupon only for persuadables | 96,000 |

**You cannot see a person's uplift directly**, because nobody can be seen both with and without the coupon. The only trustworthy way to measure it is a **randomized experiment**: give coupons to a random half, compare the two halves.

### What this project delivers

1. **A customer shopping app** where real people shop (cash on delivery only, fake money).
2. **An experiment:** Round 1 shopping (no coupons), customer types, a one-click exact random coupon assignment, Round 2 shopping.
3. **An admin console** with interactive graphs: what the experiment showed, how sure we can be, and what the model says.
4. **A small ML model** trained on **synthetic data** (same tables as real data) that predicts each customer's uplift, served by a FastAPI service.

### Honesty rules (these are part of the grade, not decoration)

- **With 10 to 40 real users nobody can prove the coupon works.** The app must show the estimate, its uncertainty, and say **"inconclusive"** when that is the truth.
- **The ML model is trained on synthetic data** because a model fitted on 20 real people would only memorise noise. Everything built on synthetic data is labelled **SIMULATED**.
- **Real and synthetic data never mix in one analysis.** Every row has `source = 'real'` or `'synthetic'`.
- **The simulation is built with an effect inside it**, so it proves the *method works when an effect exists*. The proof it does not invent wins is the "coupon does nothing" world, where it must say inconclusive (section 8).

---

## 2. Architecture

```text
                      Phones (customers)                     Your laptop (admin)
                            |                                       |
                            v                                       v
                 +-------------------------------+      +-------------------------------+
                 |  VERCEL PROJECT 1: Flask app  |      |  same Flask app, /admin/*     |
                 |  Customer side (Member A)     |<---->|  Admin side (Member B)        |
                 |  shop.py, templates/shop      |      |  admin.py, templates/admin    |
                 +---------------+---------------+      +---------------+---------------+
                                 |                                      |
                                 | SQL (database URL, server only)      | HTTPS + X-API-Key
                                 v                                      v
                 +-------------------------------+      +-------------------------------+
                 |  SUPABASE POSTGRES            |<-----|  VERCEL PROJECT 2: FastAPI    |
                 |  participants, events,        | read |  ml_service/ (numpy only)     |
                 |  orders, assignments,         |      |  /predict, /graphs/latest ... |
                 |  app_state, model_runs        |      |  loads model.json             |
                 +---------------+---------------+      +-------------------------------+
                                 ^                                      ^
                                 | write results (model_runs)           | commit model.json, redeploy
                                 |                                      |
                 +---------------+--------------------------------------+---+
                 |  YOUR LAPTOP: notebooks/train_and_duel.ipynb + tools/synth.py |
                 |  synthetic data -> features -> train -> evaluate -> publish   |
                 +---------------------------------------------------------------+
```

### Components

| Component | Where it runs | Owner | What it does |
| --- | --- | --- | --- |
| Customer app | Vercel (project 1) | Member A | Sign up and in, browse, cart, cash-on-delivery checkout, My orders, logs every action as an event |
| Admin console | Vercel (project 1, same app, `/admin`) | Member B | Customers, orders, experiment controls, results, dashboard graphs, data export |
| Database | Supabase Postgres | Shared | The single source of truth. Real and synthetic rows side by side |
| Synthetic generator | Laptop | Member B | Creates fake participants with Round 1 behaviour, an exact 50/50 coupon split and Round 2 outcomes, in the real tables |
| Training notebook | Laptop | Member B | Reads features from the database, trains, evaluates, writes `model.json` and a `model_runs` row |
| ML service (FastAPI) | Vercel (project 2) | Member B | Predicts uplift for one or many customers, looks a customer up by id, serves stored graph data |

### Data flow, step by step

1. A customer taps around in the shop. Each tap writes an **event**. Checkout writes an **order**.
2. The admin presses **Randomize**. One row per customer is written to **assignments** (coupon or not, with the seed).
3. In Round 2 the shop reads `assignments` and shows a coupon only to coupon customers. Orders record the discount.
4. SQL **views** turn raw events and orders into one row per customer: Round 1 behaviour (`v_round1`) and the full analysis table (`v_features`).
5. The notebook reads `v_features`, trains the model and writes `model.json` plus a results row to `model_runs`.
6. The ML service answers predictions from `model.json` and graph data from `model_runs`. The admin console calls it.
7. The dashboard draws everything with interactive charts.

### Deployment settings

| Item | Value |
| --- | --- |
| Vercel project 1 | Repo root. Flask app in `app.py` with a top-level `app` variable. Static files in `public/` |
| Vercel project 2 | Same repo, **Root Directory = `ml_service`**. FastAPI in `main.py` with `app` [Likely: set in the project's settings at import time] |
| Why two projects | The bundle limit for a Vercel Python function is 250 MB, so the shop never carries scikit-learn. The ML service carries only numpy and FastAPI |
| Runtime facts | Flask and FastAPI deploy with no configuration; no files can be written; Vercel's own docs state the 250 MB limit. The starter pins Python 3.12 in `.python-version`; if the Vercel build complains, use a version from Vercel's Python page (3.13 is listed) [Likely] |

### Environment variables

| Variable | Project 1 (Flask) | Project 2 (FastAPI) | Notes |
| --- | --- | --- | --- |
| `DATABASE_URL` | Yes | Only for `/predict/user` and `/graphs` | Supabase **Transaction pooler** string (port 6543), password included |
| `SECRET_KEY` | Yes | No | Long random string, signs session cookies |
| `ADMIN_ID` | Yes | No | Admin account ID (defaults to `admin`) |
| `ADMIN_PASSWORD` | Yes | No | Admin password; both halves are required |
| `ML_API_URL` | Yes | No | Base URL of project 2 |
| `ML_API_KEY` | Yes | Yes | Same random value in both; sent as `X-API-Key` |

Never commit `.env`. Never use Supabase's public (anon) key anywhere; this project only uses the database URL from servers.

---

## 3. Roles and timeline

| | Member A | Member B (you) |
| --- | --- | --- |
| Builds | The **customer side** of the shop, the Supabase project and schema, and the Vercel deployment of project 1 | The **admin side**, experiment controls, results, the ML pipeline, the FastAPI service and the dashboard |
| Owns these files | `app.py`, `shop.py`, `templates/shop/`, `public/shop.css` | `admin.py`, `templates/admin/`, `public/admin.css`, `ml_service/`, `tools/`, `notebooks/`, `stats.py`, `tags.py` |
| Shared (ask before changing) | `db.py`, `sql/schema.sql`, `sql/seed_products.sql` | same |
| Hands over | A deployed customer app + repo + acceptance checks passing | The full project |

**The two tracks run in parallel.** The ML work does not need the shop, because it runs on synthetic data in the same tables.

| Day | Member A | Member B |
| --- | --- | --- |
| 1 | Supabase project, run the schema, deploy the starter app, `/health` works. Start the shop | Load synthetic data, run the notebook, understand the results. Test the FastAPI service locally |
| 2 | Finish the customer app, deploy, run the acceptance checks, **hand over by the end of the day** | Deploy the FastAPI service. Start the admin shell against the synthetic data |
| 3 | Available for fixes | Admin pages, experiment controls, results, dashboard graphs. Dress rehearsal with 3 people |
| 4 | Helps run the class | The real experiment, export, rehearsal |

Time estimates are guesses. [Guessing] If you fall behind, use the cut list in section 14.

---

## 4. Database schema

One Postgres database in Supabase. Run the SQL below once in the Supabase **SQL editor**, then run the seed.

**Design rules**

- Real and synthetic data share the same tables and are told apart by `participants.source`.
- Customers store a **nickname and a hashed PIN only**. No email, phone or address.
- Every behaviour is an **event** with a **round** (1 or 2). The experiment logic uses rounds and `app_state`, never the clock.
- Row-level security is **on** for every table, with no policies, so Supabase's public API cannot read anything. The app connects with the database URL from the server.
- The two **views** do the heavy lifting: they turn raw events and orders into one row per customer.

| Table | Purpose |
| --- | --- |
| `participants` | One row per customer. `source`, `excluded` (late visitors and test accounts), `pin_hash` |
| `products` | The 18 fake items |
| `app_state` | One row: `state` (SETUP, ROUND1, BETWEEN, ROUND2, CLOSED), `randomized`, `seed`, coupon amount and minimum |
| `events` | Every action: view, add to cart, remove, checkout start, order placed, coupon seen |
| `orders` | One row per order, items stored as JSON, `order_key` unique to stop double submits |
| `assignments` | The coupon group of each customer (`t` = 1 coupon, 0 none) with `e` and the seed |
| `model_runs` | Results written by the notebook (JSON), read by the dashboard |
| `v_round1` (view) | Round 1 behaviour per customer: views, cart adds, average price viewed, orders, spend |
| `v_features` (view) | Assignment, Round 2 outcome and Round 1 behaviour: everything the analysis needs |

**State machine:** `SETUP -> ROUND1 -> BETWEEN -> ROUND2 -> CLOSED`. Orders are accepted only in ROUND1 and ROUND2. Randomization is allowed only in BETWEEN, and only once.

### `sql/schema.sql` (tested)

```sql
-- Uplix Live schema (PostgreSQL / Supabase). Run once in the Supabase SQL editor.
-- Real participants and synthetic participants live in the SAME tables, told apart by participants.source.

create table if not exists participants (
  id          bigint generated always as identity primary key,
  nickname    text not null,
  pin_hash    text,                                  -- hashed 4-digit PIN so a customer can sign back in (null for synthetic)
  source      text not null default 'real' check (source in ('real', 'synthetic')),
  consent_at  timestamptz not null default now(),
  created_at  timestamptz not null default now(),
  excluded    boolean not null default false,        -- late joiners, test accounts
  unique (source, nickname)
);

create table if not exists products (
  id        int primary key,
  name      text not null,
  category  text not null,
  price     int  not null check (price > 0)
);

-- One row only: the state of the live (real) experiment.
create table if not exists app_state (
  id                   int primary key default 1 check (id = 1),
  state                text not null default 'SETUP'
                       check (state in ('SETUP', 'ROUND1', 'BETWEEN', 'ROUND2', 'CLOSED')),
  randomized           boolean not null default false,
  seed                 int,
  coupon_amount        int not null default 100,
  coupon_min_subtotal  int not null default 300
);
insert into app_state (id) values (1) on conflict do nothing;

create table if not exists events (
  id              bigint generated always as identity primary key,
  participant_id  bigint not null references participants(id) on delete cascade,
  round           int not null check (round in (1, 2)),
  type            text not null check (type in
                    ('view_item', 'add_to_cart', 'remove_from_cart', 'checkout_start', 'order_placed', 'coupon_seen')),
  product_id      int references products(id),
  value           int,
  created_at      timestamptz not null default now()
);
create index if not exists events_participant_round on events (participant_id, round);

create table if not exists orders (
  id              bigint generated always as identity primary key,
  order_key       text not null unique,               -- stops double submits
  participant_id  bigint not null references participants(id) on delete cascade,
  round           int not null check (round in (1, 2)),
  items           jsonb not null,                     -- [{"product_id":1,"qty":2,"unit_price":299}]
  subtotal        int not null,
  discount        int not null default 0,
  total           int not null,
  coupon_applied  boolean not null default false,
  payment_method  text not null default 'COD' check (payment_method = 'COD'),
  created_at      timestamptz not null default now()
);
create index if not exists orders_participant_round on orders (participant_id, round);

create table if not exists assignments (
  participant_id  bigint primary key references participants(id) on delete cascade,
  t               smallint not null check (t in (0, 1)),   -- 1 = coupon
  e               real not null,                           -- known share treated = treated / n
  seed            int not null,
  assigned_at     timestamptz not null default now()
);

-- The notebook writes its results here; the dashboard reads them.
create table if not exists model_runs (
  id          bigint generated always as identity primary key,
  source      text not null,
  created_at  timestamptz not null default now(),
  results     jsonb not null
);

-- Round 1 behaviour only (measured BEFORE any coupon). Used for tags and as model features.
create or replace view v_round1 with (security_invoker = true) as
select
  p.id as user_id,
  p.source,
  p.nickname,
  p.excluded,
  coalesce(v.n_views, 0)          as n_views_r1,
  coalesce(v.n_cart_adds, 0)      as n_cart_adds_r1,
  coalesce(v.avg_price_viewed, 0) as avg_price_viewed_r1,
  coalesce(o.n_orders, 0)         as n_orders_r1,
  coalesce(o.spend, 0)            as r1_spend
from participants p
left join (
  select e.participant_id,
         count(*) filter (where e.type = 'view_item')   as n_views,
         count(*) filter (where e.type = 'add_to_cart') as n_cart_adds,
         round(avg(pr.price) filter (where e.type = 'view_item'), 1) as avg_price_viewed
  from events e left join products pr on pr.id = e.product_id
  where e.round = 1
  group by e.participant_id
) v on v.participant_id = p.id
left join (
  select participant_id, count(*) as n_orders, sum(total) as spend
  from orders where round = 1 group by participant_id
) o on o.participant_id = p.id;

-- Everything the analysis needs: assignment, Round 2 outcome, Round 1 features.
create or replace view v_features with (security_invoker = true) as
select
  r.user_id, r.source,
  a.t as "T",
  (coalesce(o2.n_orders, 0) > 0)::int as "Y_order_r2",
  coalesce(o2.revenue, 0)  as revenue_r2,
  coalesce(o2.discount, 0) as discount_r2,
  r.n_views_r1, r.n_cart_adds_r1, r.avg_price_viewed_r1, r.n_orders_r1, r.r1_spend
from v_round1 r
join assignments a on a.participant_id = r.user_id
left join (
  select participant_id, count(*) as n_orders, sum(total) as revenue, sum(discount) as discount
  from orders where round = 2 group by participant_id
) o2 on o2.participant_id = r.user_id
where not r.excluded;

-- Lock the tables away from Supabase's public API. We only ever connect with the database URL, from the server.
alter table participants enable row level security;
alter table products     enable row level security;
alter table app_state    enable row level security;
alter table events       enable row level security;
alter table orders       enable row level security;
alter table assignments  enable row level security;
alter table model_runs   enable row level security;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on all tables in schema public from anon, authenticated';
  end if;
end $$;
```

### `sql/seed_products.sql` (tested)

```sql
insert into products (id, name, category, price) values
 (1,'Cotton T-shirt','Clothing',299),(2,'Denim jacket','Clothing',1499),(3,'Running shoes','Footwear',1999),
 (4,'Canvas sneakers','Footwear',999),(5,'Backpack','Bags',799),(6,'Water bottle','Home',349),
 (7,'Desk lamp','Home',649),(8,'Notebook pack','Stationery',199),(9,'Gel pens','Stationery',149),
 (10,'Earphones','Electronics',499),(11,'Power bank','Electronics',1199),(12,'Phone stand','Electronics',249),
 (13,'Coffee mug','Home',199),(14,'Hoodie','Clothing',1299),(15,'Cap','Clothing',249),
 (16,'Sunglasses','Accessories',599),(17,'Wrist watch','Accessories',1799),(18,'Yoga mat','Fitness',699)
on conflict (id) do nothing;
```

---

## 5. Brief for Member A: build the customer side

*Hand this whole section to Member A. It is written to him.*

### 5.1 Your goal

Build a **normal-feeling, phone-first shopping app** for class participants, connect it to the Supabase database above, and deploy it on Vercel. You build **only the customer side**. Another person builds the admin side later, in the same repository, so keep your code tidy and separate.

The shop is a fake: **cash on delivery only, nothing is delivered or charged.** It exists to collect honest behaviour data for an experiment.

### 5.2 Start here

1. Get the project folder (`uplix-live-4day-kit`). It already has a starter Flask app (`app.py`, `db.py`, `templates/`, `public/`), the SQL, and tests.
2. Create a **Supabase** project (free plan). Write down the database password.
3. Open the Supabase SQL editor and run `sql/schema.sql`, then `sql/seed_products.sql`.
4. Press **Connect** in Supabase and copy the **Transaction pooler** connection string (port 6543). Put it in a local `.env` as `DATABASE_URL`. Never commit `.env`.
5. Run the starter app locally (`python app.py`) and open `/health`. It should list the table counts.
6. Create a private GitHub repo, push the folder, then import the repo at vercel.com. Vercel detects Flask. Add `DATABASE_URL`, `SECRET_KEY`, `ADMIN_ID`, `ADMIN_PASSWORD`, `ML_API_URL` and `ML_API_KEY`. `vercel.json` gives `app.py` a 60-second budget. Deploy.
7. Open `https://<project>.vercel.app/health`. **Do not continue until this works.** It proves Vercel can reach Supabase.

### 5.3 Rules for how you build

| Rule | Why |
| --- | --- |
| Python 3.12, Flask, psycopg 3, plain SQL through `db.py`. No ORM | Simple, easy to debug, small bundle |
| Customer code in a Flask blueprint in `shop.py`; templates in `templates/shop/`; styles in `public/shop.css` | Keeps the admin side from colliding with yours |
| Static files (CSS, JS, images) go in `public/`, not Flask's `static` folder | That is how Vercel serves them |
| **Never write files.** Keep sessions in the signed cookie, keep data in the database | Vercel's file system is not permanent |
| One database connection per request through `db.py` (it already turns off prepared statements for Supabase's pooler) | Serverless rule |
| No ORM, no JavaScript framework, no analytics, no third-party requests (fonts, CDNs) from shop pages | Privacy and speed. A tiny bit of plain JavaScript is fine |
| Do not change table or column names. If you need a schema change, ask first | The ML code reads these tables |
| Do not build any `/admin` route or put any link to the admin in the shop | Separate sides |
| Every secret comes from an environment variable | Safety |

### 5.4 Screens to build

| Screen | Route | What it must do |
| --- | --- | --- |
| Sign up | `/signup` | Nickname, a 4-digit PIN, and a **consent tick box** with this notice: *"This is a class experiment. Your shopping behaviour is recorded. Nothing is delivered or charged. Data is deleted after the course."* Store only a **hash** of the PIN (`werkzeug.security`). A taken nickname asks for another |
| Sign in / out | `/signin`, `/signout` | Nickname plus PIN. A wrong PIN and an unknown nickname show the **same** message. After 5 wrong tries in a row, delay further tries for a minute. The signed session cookie holds only `participant_id` |
| Home | `/` | Search box (matches name and category), category chips, product grid, one emoji per category as the picture (no image files), cart icon with item count |
| Product | `/product/<id>` | Name, category, price, quantity picker, Add to cart |
| Cart | `/cart` | Lines with plus, minus and remove, subtotal, Checkout button |
| Checkout | `/checkout` | Order summary, **one** payment option "Cash on delivery" (fixed), the line "Delivery: classroom" (no address field), Place order |
| Confirmed | `/orders/<id>` | Confirmation, and the same page works as the order detail |
| My orders | `/orders` | The signed-in customer's orders (date, items, total, status "Placed (demo)"). A customer sees **only their own** orders |
| Health | `/health` | Already in the starter app. Keep it |

On **every page**: a header (shop name, search, cart, My orders, Sign out) and the banner **"Demo shop: nothing is delivered or charged"**. Single column, big tap targets, a friendly page for errors.

### 5.5 Business rules (the important part)

**The round.** Read `app_state.state` on every request.

| State | Round number | Ordering allowed? |
| --- | --- | --- |
| SETUP | none | No |
| ROUND1 | 1 | Yes |
| BETWEEN | none | No |
| ROUND2 | 2 | Yes |
| CLOSED | none | No |

When ordering is closed, still show the catalogue and say why ordering is closed. Events are logged only in ROUND1 and ROUND2, with the round number.

**Late visitors.** If someone signs up when the state is not SETUP or ROUND1, create them with `excluded = true`. They can shop but are never counted in the experiment.

**Events** (insert into `events` with the right `round`):

| Type | When | Fields |
| --- | --- | --- |
| `view_item` | The product page opens | `product_id` |
| `add_to_cart` | Add to cart | `product_id` |
| `remove_from_cart` | Remove | `product_id` |
| `checkout_start` | The checkout page opens | none |
| `order_placed` | An order is created | `value` = order total |
| `coupon_seen` | The **first** time a coupon customer sees any coupon text | none |

**Cart.** Keep it in the session cookie as `{product_id: quantity}`, at most 20 lines (cookies are small).

**Orders.**

- The checkout form carries a one-time `order_key` (a random string). The `orders.order_key` column is **unique**, so submitting twice creates **one** order. Also disable the Place order button after the first click.
- **Totals are computed on the server** from database prices. Ignore any price, discount or total sent by the browser.
- Store the items as JSON in `orders.items`: `[{"product_id": 1, "qty": 2, "unit_price": 299}]`.

**Coupon.** Read the customer's row in `assignments` and `app_state.coupon_amount` and `coupon_min_subtotal`. **Only when** the state is ROUND2 **and** the customer's `t = 1`:

- If the cart subtotal is at least `coupon_min_subtotal`: discount = `coupon_amount`, total = subtotal minus discount, show a clear **"Coupon applied: 100 off"** line on the cart and checkout pages, and save `discount` and `coupon_applied = true` on the order.
- If the subtotal is below the minimum: show **"Add items worth X more to use your coupon"**.
- Log one `coupon_seen` event the first time that customer sees coupon text.

**Everyone else** (`t = 0`, no assignment, excluded, or any customer in ROUND1) must get **no coupon text, no coupon field, no hidden input and no discount anywhere in the HTML**. The two groups must have identical experiences apart from the coupon.

### 5.6 How to test the coupon without the admin side

The admin does not exist yet, so use the Supabase SQL editor:

```sql
-- pretend Round 1 is open, then shop as a customer
update app_state set state = 'ROUND1' where id = 1;

-- later: pretend randomization happened and Round 2 is open
update app_state set state = 'ROUND2' where id = 1;
insert into assignments (participant_id, t, e, seed) values (<customer id>, 1, 0.5, 1);   -- coupon customer
insert into assignments (participant_id, t, e, seed) values (<other id>,    0, 0.5, 1);   -- no-coupon customer

-- put everything back
delete from assignments where seed = 1;
update app_state set state = 'SETUP' where id = 1;
```

### 5.7 Acceptance checks (all must pass before you hand over)

Write automated tests for items marked (T) using a **throwaway local PostgreSQL database**, never the Supabase project. Do the others by hand.

| # | Check |
| --- | --- |
| A1 (T) | Cart totals are correct for 3 hand-made carts, including removal |
| A2 (T) | Checkout offers only "Cash on delivery", and every page has the demo banner |
| A3 (T) | Posting the same `order_key` twice creates exactly one order |
| A4 (T) | Prices sent from the browser are ignored |
| A5 (T) | No page asks for email, phone or address; the stored PIN is a hash, not the PIN |
| A6 (T) | Each action writes its event with the right round |
| A7 (T) | Ordering is refused in SETUP, BETWEEN and CLOSED and accepted in ROUND1 and ROUND2 |
| A8 (T) | A customer who signs up when the state is BETWEEN has `excluded = true` |
| A9 (T) | Sign-in works with the right PIN; wrong PIN and unknown nickname give the same message; the 6th try inside a minute is delayed |
| A10 (T) | My orders shows only the signed-in customer's orders (two customers) |
| A11 (T) | Search finds items by name and by category |
| A12 (T) | In ROUND2 a coupon customer with subtotal 450 pays 350; with subtotal 250 pays 250 and sees the hint; a no-coupon customer's pages contain neither "coupon" nor "discount" (case-insensitive) |
| A13 (T) | `coupon_seen` is logged exactly once per coupon customer |
| A14 (T) | No customer page links to `/admin`, and no `/admin` route exists in your code |
| A15 | The **deployed** shop works on a phone over **mobile data**, not only Wi-Fi |
| A16 | After an order, you can see the rows in the Supabase table editor (`participants`, `events`, `orders`) |
| A17 | Row-level security shows **on** for every table in the Supabase dashboard, and no code uses the Supabase public key |
| A18 | A fresh clone runs from the README using only `.env.example` and `requirements.txt` |

### 5.8 What to hand over

| Item | Detail |
| --- | --- |
| Repository | The private GitHub repo with your work, branch `main`, and a short "how to run" section in its README |
| Live URL | The Vercel URL of the shop, working |
| Access | Add Member B as a collaborator on GitHub, Vercel and Supabase (do **not** send passwords in chat) |
| `.env.example` | Every variable name, with fake values |
| Tests | The terminal output of all (T) tests passing, and a note saying which hand checks (A15 to A18) you did |
| Notes | A short list of anything you were unsure about or left out |

### 5.9 If you use an AI coding agent

Paste this first, then the sections above:

```text
ROLE: senior Flask engineer and teacher. I am a student who must explain every line.
PROJECT: Uplix Live, a fake-money cash-on-delivery shop. I build ONLY the customer side.
STACK (nothing else): Python 3.12, Flask, psycopg 3 with plain SQL through db.py, Jinja templates, plain CSS in public/. Deployed on Vercel; database is Supabase Postgres (schema in sql/schema.sql, do not rename anything).
RULES: small plain functions with short docstrings; tests with the code and real terminal output, never edit or skip a test; never write files (serverless); sessions in the signed cookie only; all secrets from environment variables; nickname + hashed PIN + consent only; checkout is cash on delivery only; every page shows 'Demo shop: nothing is delivered or charged'; all discount maths on the server; control customers never see coupon text; no admin routes or links; no third-party requests from pages.
Finish each step with: files changed, test output, open risks, 3 questions I should be able to answer.
```

---

## 6. Synthetic data: how it is created and why it lines up with the app

**Why we need it.** With 10 to 40 real users, a model would only memorise noise. Synthetic data lets us build and test the whole pipeline, and train the model, at a realistic size (10,000 customers), before anyone uses the app.

**How it lines up.** The generator writes **into the real tables** (`participants`, `events`, `orders`, `assignments`), with `source = 'synthetic'`. So the same SQL views, the same feature code and the same model code work on synthetic and real data. When real users arrive, you change one filter (`source = 'real'`), nothing else.

**What the generator does** (`tools/synth.py`):

1. Each synthetic customer gets three **hidden traits** that are *not* stored in the database: *engagement* (how active they are), *price sensitivity* (how much a discount moves them), *basket taste* (how big their baskets are).
2. **Round 1 behaviour** is generated from the traits, using the same event types as the real shop: how many items they view (more engaged means more), which items (price-sensitive people look at cheaper items), what they add to the cart, and whether they place an order.
3. An **exact 50/50 coupon split** is made, like the real Randomize button. `e = 0.5` is stored in `assignments`.
4. **Round 2 outcomes** come from the truth: the chance to order is `sigmoid(-1.2 + 0.9*engagement + 0.3*basket)`. The coupon adds `delta = 1.4*price_sensitivity - 0.8*max(engagement, 0)`. So price-sensitive, low-engagement people react to the coupon (persuadables), and very engaged people react negatively (sleeping dogs). Coupon customers' orders get a discount of 100.
5. Timestamps are spread over two days so the time charts look real.

The hidden traits go to `data/synthetic_truth.csv` (never into the database), so we can later check how well the model recovered the truth.

**One real synthetic customer** (`syn_00007`, id 68), copied from the generated database:

| Table | Rows |
| --- | --- |
| `participants` | `68 | syn_00007 | synthetic | excluded = false` |
| `assignments` | `participant 68 | t = 1 | e = 0.5 | seed 0` (coupon customer) |
| `events`, Round 1 | 9 `view_item` (products 5, 13, 14, 2, 3, 17, 13, 10, 17), 2 `add_to_cart` (14, 17), `checkout_start`, `order_placed` with value 3098 |
| `events`, Round 2 | `view_item` 7, `coupon_seen`, `add_to_cart` 14, `checkout_start`, `order_placed` with value 1199 |
| `orders`, Round 1 | items products 14 and 17, subtotal 3098, discount 0, total 3098, `coupon_applied = false` |
| `orders`, Round 2 | item product 14, subtotal 1299, discount 100, total 1199, `coupon_applied = true` |
| `v_round1` | `n_views_r1 = 9, n_cart_adds_r1 = 2, avg_price_viewed_r1 = 1121.2, n_orders_r1 = 1, r1_spend = 3098` |
| `v_features` | `T = 1, Y_order_r2 = 1, revenue_r2 = 1199, discount_r2 = 100` plus the Round 1 numbers |

A real customer produces rows of exactly the same shape.

**Size of the generated database (10,000 customers):** 96,529 events and 6,308 orders, about 24 MB, which fits Supabase's 500 MB free plan. Loading takes a few seconds.

**Commands**

```text
python tools/synth.py --reset                    # 10,000 synthetic customers (default)
python tools/synth.py --n 2000 --seed 5 --reset  # smaller, different seed
python tools/synth.py --effect none --reset      # "coupon does nothing" world
```

`--reset` deletes only `source = 'synthetic'` rows. Real rows are never touched.

**Limits to say out loud.** The effect is built in by us. A win on synthetic data proves the **method works when an effect exists**. It says nothing about your real users.

---

## 7. Feature engineering: from the schema to model inputs

**Rule 1: only Round 1 behaviour.** Features must be measured *before* the coupon. Anything from Round 2 would be affected by the treatment (leakage) and would make the model look better than it is.

**Rule 2: one function, used everywhere.** `engineer()` in `ml_service/uplix_ml.py` is used by the training notebook and by the FastAPI service. Training and serving therefore always compute features the same way.

### Step 1: raw features from SQL (`v_round1`)

| Raw feature | Meaning | Where it comes from |
| --- | --- | --- |
| `n_views_r1` | Items viewed in Round 1 | count of `view_item` events, round 1 |
| `n_cart_adds_r1` | Items added to the cart | count of `add_to_cart` events, round 1 |
| `avg_price_viewed_r1` | Average price of viewed items | `view_item` events joined to `products.price` |
| `n_orders_r1` | Orders placed in Round 1 | count of `orders`, round 1 |
| `r1_spend` | Money spent in Round 1 | sum of `orders.total`, round 1 |

Why these five: they are cheap to compute for any customer, they cover interest (views), intent (cart), price sensitivity (average price viewed) and value (orders, spend), and a real customer produces them just like a synthetic one.

### Step 2: engineered features in Python (`engineer()`)

| Feature | Formula | Why |
| --- | --- | --- |
| `log_views` | log(1 + views) | Counts are skewed; the log tames big values |
| `cart_rate` | cart adds / max(views, 1) | Intent per view, not just volume |
| `log_cart` | log(1 + cart adds) | Same reason as views |
| `avg_price_k` | average price viewed / 1000 | Price taste; this is the clearest clue to price sensitivity |
| `has_order` | 1 if at least one Round 1 order | A step change between buyers and non-buyers |
| `n_orders` | orders in Round 1 | Repeat behaviour |
| `aov_k` | (spend / max(orders, 1)) / 1000 | Average order value |
| `log_spend` | log(1 + spend) | Total value, tamed |
| `no_views` | 1 if views = 0 | Flags customers with no data (a clean signal instead of a fake zero) |

Division by zero is handled (customers with no views or orders get safe values; a test checks this).

### Step 3: standardise

The scaler (mean and spread) is **fitted on the training part only**, then applied to everyone. Its numbers are stored in `model.json`, so serving applies the identical scaling.

### Worked example (a customer with 6 views, 2 cart adds, average price 520, 1 order, spend 799)

```text
log_views 1.946 | cart_rate 0.333 | log_cart 1.099 | avg_price_k 0.52 | has_order 1
n_orders 1 | aov_k 0.799 | log_spend 6.685 | no_views 0
```

This customer's prediction from the current model: chance to order without a coupon 40.2%, with a coupon 44.3%, uplift +4.1 points, lane **Skip**.

### How this works when real users arrive

The same two steps run on `v_round1` rows of real customers. Nothing is recomputed differently. The FastAPI endpoint `/predict/user/{id}` reads one customer's `v_round1` row and calls the same `engineer()`.

---

## 8. The model, and how it is trained and checked

### The model (kept simple on purpose)

A **T-learner built from logistic regressions**, plus a "traditional" baseline:

| Head | Fitted on | Predicts |
| --- | --- | --- |
| `traditional` | all training customers | chance to order in Round 2, **never sees who got a coupon** |
| `control` | training customers **without** a coupon | `mu0`: chance to order without a coupon |
| `treated` | training customers **with** a coupon | `mu1`: chance to order with a coupon |

**uplift = mu1 - mu0.** Gain of giving a coupon = `m * uplift - d * mu1` (m = value of an order, d = coupon amount). Lane for one customer: **Treat** if gain > 0, **Do not disturb** if uplift < 0, otherwise **Skip**.

A logistic regression was chosen because it is explainable, trains in a second, and the whole model is 1.7 KB of numbers, so the ML service needs only numpy.

### The duel: traditional versus uplift at the same budget

Both strategies give coupons to the same share of customers:

- **Traditional:** the highest predicted orderers.
- **Uplift:** the biggest predicted change.
- **Random:** the benchmark. Also compared with "never" and "everyone".

### Rules that keep it honest

1. Split into train (60%), validation (20%), test (20%), stratified on coupon group and outcome.
2. Fit models and the scaler on **train** only.
3. Choose the budget (share of customers to treat) on **validation**.
4. Report everything on **test**, once, with 95% intervals.
5. Estimate profit with a **doubly robust** estimate that uses the known 50/50 randomization, so it stays fair even if the models are imperfect.
6. **Verdict:** "reliable" only if uplift beats never, everyone and random, each with an interval above zero. Otherwise "inconclusive".
7. Repeat on 10 fresh random crowds, and also in a **world where the coupon does nothing**, to prove it does not invent wins.

### Results from the tested run (SIMULATED, 10,000 customers, coupon 100, 2,000 in the test part)

| Strategy | Profit per customer | 95% interval |
| --- | --- | --- |
| Never treat | 261.5 | 235.1 to 287.8 |
| Treat everyone | 234.0 | 210.4 to 257.7 |
| Random, same budget | 254.6 | 234.0 to 275.2 |
| Traditional: likeliest buyers | 233.0 | 207.9 to 258.1 |
| **Uplift: biggest change** | **303.8** | **276.6 to 331.0** |

Budget chosen on validation: treat the top **25%**. Uplift minus traditional: **+70.9** (47.5 to 94.3). Verdict: **reliable**.

| Check | Result |
| --- | --- |
| Observed coupon effect by model-score group (best to worst) | +23.7, +9.3, -4.3, -5.8, -23.8 points. The ranking really separates people |
| 10 random crowds, coupon matters | "reliable" in 10 of 10; uplift clearly beat traditional in 9 of 10 |
| 10 random crowds, **coupon does nothing** | "reliable" in **0 of 10**; uplift beat traditional in 0 of 10 |
| Correlation of predicted uplift with the hidden truth | 0.64 |
| Average coupon effect over everyone | near zero. Persuadables and sleeping dogs cancel out, so an average-only analysis would say "the coupon does nothing" |

**Size matters.** With 5,000 synthetic customers, an earlier version of the model earned "reliable" in only 4 of 10 crowds. That is why the default is 10,000.

### Train it

```text
pip install -r requirements-notebook.txt -r requirements.txt
$env:DATABASE_URL = "<Supabase transaction pooler string>"
python tools/synth.py --reset
jupyter notebook notebooks/train_and_duel.ipynb        # run all cells
```

The notebook reads `v_features`, trains, evaluates, writes a row to `model_runs`, and saves `ml_service/model.json`. Without a `DATABASE_URL` it generates data in memory, so it always runs.

**The real-user model is refused below 200 real users.** With fewer, the notebook reports only the experiment statistics (effect, exact test, interval, power).

---

## 9. The ML service (FastAPI)

Small, numpy-only, deployed as its own Vercel project (**Root Directory = `ml_service`**). Interactive docs are at `/docs`.

| Endpoint | Method | What it does |
| --- | --- | --- |
| `/health` | GET | Status, when the model was trained. Open (for uptime checks) |
| `/model` | GET | Model metadata: features, economics, chosen budget, metrics |
| `/predict` | POST | One customer's five Round 1 numbers in; `p_buy`, `mu0`, `mu1`, `uplift`, `gain`, `lane` out |
| `/predict/batch` | POST | 1 to 500 customers at once |
| `/predict/user/{user_id}` | GET | Looks the customer up in `v_round1` and scores them (needs `DATABASE_URL`) |
| `/graphs/latest?source=synthetic` | GET | The newest stored model results for the dashboard graphs (needs `DATABASE_URL`) |

All endpoints except `/health` require the header `X-API-Key` when `ML_API_KEY` is set.

**Example**

```text
POST /predict
{"n_views_r1": 6, "n_cart_adds_r1": 2, "avg_price_viewed_r1": 520, "n_orders_r1": 1, "r1_spend": 799}

-> {"p_buy": 0.4188, "mu0": 0.4018, "mu1": 0.4428, "uplift": 0.041, "gain": -4.13, "lane": "Skip"}
```

Inputs are validated (negative numbers are refused with a 422). The service imports no pandas and no scikit-learn (a test checks it).

**Run locally:** `cd ml_service`, `pip install -r requirements.txt`, `uvicorn main:app --reload --port 8000`.

**Refresh the model:** retrain in the notebook, which rewrites `ml_service/model.json`, commit the file, and redeploy project 2.

**How the admin console uses it:** the Flask admin calls the service **server to server** (so no key reaches a browser), using `ML_API_URL` and `ML_API_KEY`. The Customers page gets a "Score" button for `/predict/user/{id}`. The Model page reads `/graphs/latest`.

**Honest caveat:** the model is trained on synthetic data. Scoring *real* customers with it is an illustration of the method, not a validated prediction. Label it "model trained on synthetic data".

---

## 10. Admin console and dashboard (Member B builds)

A desktop-first console in the same Flask app, under `/admin`, with its own templates and CSS.

| Rule | Detail |
| --- | --- |
| Access | `/login` is a neutral role chooser (customer or admin); `/admin/login` takes an account ID (`ADMIN_ID`) and a password (`ADMIN_PASSWORD`), both compared in constant time, with a throttle after 5 wrong tries. A signed `is_admin` flag lives in the session. Everything else under `/admin` and every `/api/` data endpoint requires it (redirect or 401). A customer session never grants admin access |
| Layout | Left sidebar: Overview, Customers, Orders, Experiment, Results, Model, Data, using the same palette, cards, buttons and emoji icons as the shop. A top bar with the current state and round, a Real or Synthetic source switch, and a SIMULATED badge when Synthetic is selected |

| Page | Content |
| --- | --- |
| **Overview** | A big bold uplift hero (effect in percentage points, group sizes, exact p, interval, verdict, provisional badge) with a next-step CTA; KPI cards (customers, orders per round, state); funnel chart per round (view, cart, checkout, order); orders over time with the rounds shaded; customer-type doughnut (click a slice to filter); coupon effect with intervals, exact p-value and a verdict badge; a power calculator (two number inputs) |
| **Customers** | Table with nickname, type tag, views, cart adds, orders, spend, group, excluded flag; search; a click opens the customer's orders and event timeline; a **Score** button using the ML service |
| **Orders** | Every order with customer, round, subtotal, discount, total, coupon flag; filters for round and coupon |
| **Experiment** | **Launch treatment** in one click (exact 50/50 split plus Round 2 in one transaction), plus the state buttons (Start Round 1, Close Round 1, Randomize, Start Round 2, Close Round 2). Illegal moves are refused on the server. Randomize/launch work only in BETWEEN and only once. Shows the seed, a balance table and a tag-by-group table. Remove-customer (test accounts) only before randomization |
| **Results** | The same big bold uplift hero, group sizes, effect on order rate and revenue, exact p-value, bootstrap interval, verdict and reason, power table, "provisional" banner until the state is CLOSED, a warning when the Round 2 order rate is 0% or 100% |
| **Model** (always SIMULATED) | Coupon-size dropdown (50, 100, 150); strategy bars with interval whiskers; a **budget slider** showing profit of uplift, traditional and random at the chosen share; Qini curves with hover and a clickable legend; observed effect by score group; the honesty table (matters versus does nothing) |
| **Data** | Export CSV, and "Reset real data" (allowed only before randomization; never touches synthetic rows) |

### Customer types (tags)

Plain rules from Round 1 only, in this order. With few customers a clustering model would be noise.

| Tag | Rule |
| --- | --- |
| Regular | 2 or more orders in Round 1 |
| Big basket | At least 1 order with average order value at or above the median among customers with orders |
| Window shopper | 5 or more views and no order |
| Newcomer | Everyone else |

Tags describe past behaviour. They do **not** show who is persuadable. Only the experiment can.

### Statistics for the real experiment

| Method | Why |
| --- | --- |
| Exact randomization test | Re-runs every possible split of the same people into two groups (252 for 5 and 5) and counts how often a gap this big appears by chance. Right for tiny groups |
| Bootstrap interval | A rough range for the effect. Very rough at this size |
| Verdict | "reliable" only with 40 or more customers, an exact p below 0.05, and an interval that excludes zero. Otherwise "inconclusive" with the reason |
| Power table | Customers needed per group: 20% to 30% order rate needs 291; 20% to 40%, 79; 30% to 50%, 91; 30% to 60%, 40; 40% to 80%, 20; 50% to 80%, 36 |

**Golden check:** for 5 coupon and 5 no-coupon customers with orders `1,1,1,0,1` versus `1,0,0,1,0`, the effect is 0.4, the exact p is 0.5238 over 252 splits and the interval is -0.2 to 1.0. If your code gives different numbers, something is wrong.

---

## 11. What Member B does, in order

### Track 1: ML (starts on Day 1, needs nothing from Member A)

| Step | What | Done when |
| --- | --- | --- |
| M1 | Create or share the Supabase project, run the schema and seed, run `python tools/synth.py --reset` | Supabase shows about 10,000 synthetic participants |
| M2 | Run `notebooks/train_and_duel.ipynb` against Supabase | A row in `model_runs` and `ml_service/model.json` exists |
| M3 | Run the tests (`pytest`) and the FastAPI service locally | `/docs` opens and `/predict` answers |
| M4 | Deploy `ml_service` as Vercel project 2 (Root Directory `ml_service`); set `ML_API_KEY` and `DATABASE_URL` | `GET /health` works on the live URL; `POST /predict` returns a prediction |
| M5 | Read the notebook outputs and be able to explain each chart | You can answer the questions in section 15 |

### Track 2: after Member A hands over

| Step | What | Done when |
| --- | --- | --- |
| B0 | **Accept the delivery.** Pull the repo, run the tests, and do hand checks A15 to A18 yourself. Read the code until you could explain it | All pass. You have access to GitHub, Vercel and Supabase |
| B1 | **Admin shell:** login, sidebar layout, Customers page (with tags) and Orders page | You can see every customer and order, and a customer session cannot open `/admin` |
| B2 | **Experiment page:** state buttons, exact randomization with the lock, balance table | Randomize works once, a second try is refused, the shop shows the coupon only to coupon customers |
| B3 | **Results page and Export** with the statistics from section 10 | The golden check matches; the verdict says inconclusive at 10 customers |
| B4 | **Dashboard:** Overview graphs, then the Model page using `/graphs/latest`. Build against `sample_data/model_run_synthetic.json` first | Both pages render, the budget slider and coupon dropdown change the numbers |
| B5 | **Connect the ML service:** the Score button on the Customers page (`/predict/user/{id}`) | A customer gets a lane and uplift, labelled as a synthetic-trained model |
| B6 | **Dress rehearsal** with 3 people: Round 1, randomize, Round 2, results. Then Reset real data | The whole flow works on the deployed site |
| B7 | **Real run** (section 12). Export the CSV and copy the tables out of Supabase | Real results on the dashboard and a clean export |

**Cut list if you fall behind** (cut in this order): results by tag and the tag doughnut; the coupon dropdown and budget slider (keep static charts); the revenue outcome; the power calculator inputs (keep a fixed table); the pilot with friends.
**Never cut:** the exact random split with its lock, the coupon being invisible to the no-coupon group, the exact test with an interval, the "inconclusive" verdict, the SIMULATED labels, the CSV export.

---

## 12. Running the real experiment

| Step | What to do |
| --- | --- |
| Before | Resume Supabase if it paused. Open `/health` on both projects and the dashboard to warm them up. Reset real data. State must be SETUP |
| Round 1 | Start Round 1. Share the shop link as a QR of the **link** (never a payment QR). 5 minutes of shopping |
| Between | Close Round 1. Show the tags and the balance table. Press **Randomize** and say out loud that it can only be pressed once |
| Round 2 | Start Round 2. 5 minutes. Close Round 2 |
| Results | Open the dashboard. Say what the data can and cannot show. Show the power table |
| After | Export the CSV and the Supabase tables (the free plan has no backups). Delete personal data after the course, as the consent text promises |

Ask people to use their own phones, not to compare screens until the end, and to order **only if they really want something** (skipping is fine). Otherwise everyone orders and nothing can be measured.

**Run of show (about 20 minutes):** 0 to 3 idea; 3 to 8 Round 1; 8 to 10 tags, balance and randomize live; 10 to 14 Round 2; 14 to 17 live results and the power table; 17 to 20 the Model page (SIMULATED) and questions.

**What you can claim:** "I ran a randomized experiment on real users end to end. Here is the estimated coupon effect with its uncertainty and how many users I would need. Here is the same method on a simulated crowd, where uplift targeting beats targeting likely buyers, and where it correctly says inconclusive when the coupon does nothing."
**What you cannot claim:** that the coupon works (or does not) from 10 to 40 users, that tags identify persuadables, or that the simulated result describes your real users.

---

## 13. Testing

All tests run locally and pass (25).

| File | What it checks |
| --- | --- |
| `tests/test_kit.py` | Schema and row-level security; exact split and known `e`; same seed gives the same data; no hidden truth in the database; coupon only on treated Round 2 orders; **the SQL view matches the in-memory feature computation**; reset only replaces synthetic rows |
| `tests/test_app.py` | Starter app: catalogue, `/health`, no Flask static folder |
| `tests/test_ml.py` | Feature engineering; **numpy serving equals scikit-learn to 1e-9**; lane rules; the service imports no scikit-learn or pandas; every endpoint; the API key; input validation; the database-backed endpoints |

```text
# use a throwaway local PostgreSQL database, NEVER your Supabase project (the tests drop the schema)
$env:TEST_DATABASE_URL = "postgresql://user:pass@localhost:5432/uplix_test"
python -m pytest -q
```

**Not tested yet:** anything on real Supabase or Vercel, the customer app (not built), the admin console (not built).

---

## 14. Failure cases that matter in this stack

| Failure | Prevention | If it happens |
| --- | --- | --- |
| Supabase free project paused after about a week of inactivity [Likely] | Open the project the day before and resume it | Resume takes minutes; do not start class until `/health` works |
| No backups on the free plan [Likely] | Export CSVs and tables after every session | Re-import from the export |
| "prepared statement does not exist" errors | `db.py` turns off prepared statements and you use the transaction pooler (port 6543) | Check the connection string and port |
| First request on Vercel is slow | Open `/health` and the dashboard 5 minutes before class | Wait and refresh |
| Bundle too big for Vercel | No pandas or scikit-learn in either deployed project | Remove the dependency |
| Cart too big for the cookie | At most 20 lines, stored as `{id: qty}` | Ask the user to check out |
| Data exposed through Supabase's public API | Row-level security is on for every table; no public key in code | Check the tables in the Supabase dashboard |
| Admin password guessed | Long random value, throttle, no admin link in the shop | Change the variable and redeploy |
| Real and synthetic mixed | Every query filters by `source`; Reset real data only touches `real` | Re-run with the filter |
| Too few users, or nobody orders / everybody orders | Power table, "inconclusive" verdict, 40 or more users if possible, a warning at 0% or 100% | Say so honestly |
| No-coupon users find out about the coupon | Own phones; coupon text never rendered for them (tested) | Mention possible contamination |
| Late joiners and duplicate accounts | Late visitors are excluded; test accounts removable before randomizing | Report the count |
| Someone really pays through a QR | Never use a real payment QR | Refund immediately |
| ML service and model drift apart | Retrain, commit `model.json`, redeploy; `/health` shows the training time | Redeploy project 2 |
| Plan slips | The cut list in section 11 | Cut, never rush the randomization |

---

## 15. Questions you will be asked

1. **Why train on fake data?** With 10 to 40 real users a model learns noise. Synthetic data with the same tables lets the method be built, tested and checked at a realistic size, and it is labelled SIMULATED everywhere.
2. **Does the coupon work on your real users?** With this many people nobody can tell. The dashboard shows the estimate, the interval and how many users it would take.
3. **Isn't the simulation designed to win?** The hidden effect is built in, so it proves the method works when an effect exists. The proof it does not invent wins is the world where the coupon does nothing: 0 of 10 "reliable".
4. **Why not just predict who buys?** Those people include customers who would buy anyway, so the coupon is wasted on them. Uplift ranks by the change the coupon causes.
5. **Why random coupons?** Only randomization makes the two groups comparable, so a difference between them can be put down to the coupon.
6. **Why choose the budget on validation data?** The best point of a noisy curve is lucky. Choosing on one part and reporting on another keeps the result honest.
7. **Why a logistic regression?** It is simple, explainable, trains in a second, and is 1.7 KB, so the ML service needs only numpy.
8. **How do features avoid leakage?** They use Round 1 only; Round 2 would be affected by the coupon.
9. **How do training and serving stay consistent?** One `engineer()` function and one stored scaler are used in both places, and a test checks the numpy predictions equal scikit-learn's.
10. **Is the average effect not enough?** On the simulated crowd the average effect is near zero because persuadables and sleeping dogs cancel. Uplift looks inside the average.

---

## 16. Glossary

| Term | Meaning |
| --- | --- |
| Uplift (CATE) | The change in a person's chance to buy caused by the coupon: chance with it minus chance without it |
| Persuadable, sure thing, lost cause, sleeping dog | The four customer types in section 1 |
| Randomized experiment | Coupons given by chance, so the two groups differ only in the coupon |
| `e` | The share of people who got the coupon. Known by design (about 0.5) |
| T-learner | Two models, one per group; the gap between their predictions is the uplift |
| Traditional model | A model that predicts who will buy, ignoring the coupon |
| Qini curve | Extra orders caused by the coupon as you treat more customers, best first. Higher is better |
| Doubly robust estimate | A fair estimate of profit that uses the known randomization, so it stays correct even if the models are imperfect |
| Validation / test | Validation is used to make choices (the budget); test is used once to report |
| Exact randomization test | Re-runs every possible group split to see how unusual the result is |
| Power | The chance of detecting a real effect of a given size with a given number of users |
| RLS | Row-level security in Postgres. On with no policies means the public API sees nothing |
| Transaction pooler | Supabase's connection string (port 6543) meant for serverless apps |
| Synthetic data | Made-up customers in the same tables as real ones. Labelled SIMULATED |
| `source` | Column that says whether a participant is `real` or `synthetic` |

---

## 17. Repository layout and commands

```text
uplix-live/
  app.py  db.py  requirements.txt  .env.example  .python-version
  shop.py                     # customer blueprint           (Member A)
  admin.py  stats.py  tags.py # admin blueprint + statistics (Member B)
  templates/shop/  templates/admin/
  public/                     # static files: shop.css, admin.css, chart.umd.js
  sql/schema.sql  sql/seed_products.sql
  tools/synth.py              # synthetic data generator
  notebooks/train_and_duel.ipynb
  ml_service/                 # FastAPI service (its own Vercel project)
    main.py  uplix_ml.py  model.json  requirements.txt
  sample_data/model_run_synthetic.json   # a real notebook output, to build the dashboard against
  tests/                      # 25 tests
  requirements-notebook.txt
```

| Task | Command (PowerShell) |
| --- | --- |
| Create the environment | `python -m venv .venv` then `.venv\Scripts\Activate.ps1` |
| Install everything | `pip install -r requirements-notebook.txt -r requirements.txt` |
| Point at the database | `$env:DATABASE_URL = "<transaction pooler string>"` |
| Make synthetic data | `python tools/synth.py --reset` |
| Train and publish | `jupyter notebook notebooks/train_and_duel.ipynb` |
| Run the shop locally | `python app.py` |
| Run the ML service locally | `cd ml_service` then `uvicorn main:app --reload --port 8000` |
| Run the tests | set `TEST_DATABASE_URL` to a throwaway database, then `python -m pytest -q` |
