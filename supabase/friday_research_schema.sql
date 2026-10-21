-- Friday research-only persistence. Apply only to a dedicated research Supabase project.
-- Keep the last 50 lifecycle event rows; cron diagnostics are retained separately.
create table if not exists public.friday_trade_events (
  id bigint generated always as identity primary key,
  event_key text not null unique,
  pair text not null,
  timeframe text not null,
  strategy_version text not null,
  event_type text not null,
  direction text,
  signal_time timestamptz,
  entry_reference numeric,
  line_kind text,
  line_price numeric,
  outcome text not null default 'pending',
  retest_confirmed boolean not null default false,
  continuation_add_time timestamptz,
  continuation_add_close numeric,
  failure_time timestamptz,
  run_id text not null,
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists friday_trade_events_created_at_idx
  on public.friday_trade_events (created_at desc);
create index if not exists friday_trade_events_pair_signal_idx
  on public.friday_trade_events (pair, signal_time desc);

create table if not exists public.friday_cron_runs (
  id bigint generated always as identity primary key,
  run_id text not null unique,
  started_at timestamptz not null,
  finished_at timestamptz,
  status text not null,
  pairs_checked integer not null default 0,
  pair_results jsonb not null default '[]'::jsonb,
  error text,
  created_at timestamptz not null default now()
);

create index if not exists friday_cron_runs_started_at_idx
  on public.friday_cron_runs (started_at desc);

-- Internal server-side tables only. No public/anon/authenticated access.
alter table public.friday_trade_events enable row level security;
alter table public.friday_cron_runs enable row level security;
revoke all on public.friday_trade_events from anon, authenticated;
revoke all on public.friday_cron_runs from anon, authenticated;

create or replace function public.friday_prune_trade_events()
returns trigger language plpgsql security invoker as $$
begin
  delete from public.friday_trade_events
  where id in (
    select id from public.friday_trade_events
    order by created_at desc, id desc
    offset 50
  );
  return null;
end;
$$;

drop trigger if exists friday_trade_events_keep_50 on public.friday_trade_events;
create trigger friday_trade_events_keep_50
after insert or update on public.friday_trade_events
for each statement execute function public.friday_prune_trade_events();

-- Retain the latest 500 cron run records for debugging.
create or replace function public.friday_prune_cron_runs()
returns trigger language plpgsql security invoker as $$
begin
  delete from public.friday_cron_runs
  where id in (
    select id from public.friday_cron_runs
    order by started_at desc, id desc
    offset 500
  );
  return null;
end;
$$;

drop trigger if exists friday_cron_runs_keep_500 on public.friday_cron_runs;
create trigger friday_cron_runs_keep_500
after insert on public.friday_cron_runs
for each statement execute function public.friday_prune_cron_runs();


create table if not exists public.friday_alert_deliveries (
  alert_key text primary key,
  pair text not null,
  alert_type text not null,
  signal_time timestamptz,
  status text not null default 'sending',
  telegram_message_id bigint,
  error text,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
alter table public.friday_alert_deliveries enable row level security;
revoke all on public.friday_alert_deliveries from anon, authenticated;


-- Long-horizon archive: unlike the operational ledger, this table is not pruned.
-- One row per setup lifecycle or per candle's Friday decision snapshot.
create table if not exists public.friday_research_archive (
  record_key text primary key,
  record_type text not null,
  pair text not null,
  timeframe text not null,
  strategy_version text not null,
  signal_time timestamptz,
  payload jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists friday_research_archive_pair_time_idx
  on public.friday_research_archive (pair, signal_time desc);
create index if not exists friday_research_archive_type_idx
  on public.friday_research_archive (record_type, signal_time desc);
alter table public.friday_research_archive enable row level security;
revoke all on public.friday_research_archive from anon, authenticated;
