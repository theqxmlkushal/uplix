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
