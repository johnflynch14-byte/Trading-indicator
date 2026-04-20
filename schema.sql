-- Run this in your Supabase SQL editor (supabase.com → project → SQL Editor)

create table if not exists signals (
  id           uuid        default gen_random_uuid() primary key,
  symbol       text        not null,
  direction    text        not null,  -- BULLISH | BEARISH
  close        numeric     not null,
  momentum     numeric     not null,
  momentum_prev numeric    not null,
  bb_upper     numeric     not null,
  bb_lower     numeric     not null,
  kc_upper     numeric     not null,
  kc_lower     numeric     not null,
  claude_analysis text,
  sim_mode     boolean     default false,
  created_at   timestamptz default now()
);

create table if not exists trades (
  id         uuid        default gen_random_uuid() primary key,
  symbol     text        not null,
  side       text        not null,  -- buy | sell_short
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
  direction   text,                  -- BULLISH | BEARISH | null
  updated_at  timestamptz default now()
);

-- Enable realtime for live dashboard updates
alter publication supabase_realtime add table signals;
alter publication supabase_realtime add table trades;
alter publication supabase_realtime add table squeeze_status;
