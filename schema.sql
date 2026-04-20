-- Run this in your Supabase SQL editor (supabase.com → project → SQL Editor)
-- Safe to re-run — uses IF NOT EXISTS and ADD COLUMN IF NOT EXISTS

create table if not exists signals (
  id              uuid        default gen_random_uuid() primary key,
  symbol          text        not null,
  direction       text        not null,  -- BULLISH | BEARISH
  timeframe       text        not null default 'daily',  -- daily | 15min
  daily_direction text,                  -- daily momentum direction when 15min fires
  close           numeric     not null,
  momentum        numeric     not null,
  momentum_prev   numeric     not null,
  bb_upper        numeric     not null,
  bb_lower        numeric     not null,
  kc_upper        numeric     not null,
  kc_lower        numeric     not null,
  claude_analysis text,
  sim_mode        boolean     default false,
  created_at      timestamptz default now()
);

create table if not exists trades (
  id         uuid        default gen_random_uuid() primary key,
  symbol     text        not null,
  side       text        not null,
  quantity   integer     not null,
  price      numeric,
  order_id   text,
  sim_mode   boolean     default false,
  created_at timestamptz default now()
);

create table if not exists squeeze_status (
  symbol      text        primary key,
  squeeze_on  boolean     not null,
  momentum    numeric     not null,
  close       numeric     not null,
  direction   text,
  updated_at  timestamptz default now()
);

-- Migrate existing signals table if columns are missing
alter table signals add column if not exists timeframe       text default 'daily';
alter table signals add column if not exists daily_direction text;

-- Enable realtime (safe no-op if already added)
do $$ begin
  alter publication supabase_realtime add table signals;
exception when others then null; end $$;
do $$ begin
  alter publication supabase_realtime add table trades;
exception when others then null; end $$;
do $$ begin
  alter publication supabase_realtime add table squeeze_status;
exception when others then null; end $$;
