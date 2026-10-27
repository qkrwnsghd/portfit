-- 포트핏 규칙 알림 준비 (Supabase SQL Editor에 통째로 붙여넣고 Run 한 번)
-- 1) 규칙 표  2) 알림 받을 기기 표  3) 서버만 보는 비밀 표  4) 2분마다 알림 서버를 부르는 예약 작업
-- 여러 번 실행해도 괜찮아요.

-- 1) 내가 정한 매도 규칙 (본인 것만 보고 고칠 수 있음)
create table if not exists public.alert_rules (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  sym text not null check (sym ~ '^([A-Z]{1,6}(\.[A-Z])?|[0-9][0-9A-Z]{5})$'),
  kind text not null check (kind in ('above', 'below')),
  price numeric not null check (price > 0),
  base numeric check (base is null or base > 0),
  label text check (char_length(label) <= 40),
  note text check (char_length(note) <= 200),
  hint text check (char_length(hint) <= 200),
  active boolean not null default true,
  fired_at timestamptz,
  fired_price numeric,
  last_price numeric,
  checked_at timestamptz,
  created_at timestamptz not null default now()
);
alter table public.alert_rules enable row level security;
drop policy if exists "own rules" on public.alert_rules;
create policy "own rules" on public.alert_rules for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index if not exists alert_rules_active on public.alert_rules (active) where active;

-- 한 사람당 규칙 30개까지
create or replace function public.alert_rules_limit() returns trigger language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from public.alert_rules where user_id = new.user_id) >= 30 then
    raise exception '규칙은 30개까지 만들 수 있어요';
  end if;
  return new;
end $$;
drop trigger if exists alert_rules_limit on public.alert_rules;
create trigger alert_rules_limit before insert on public.alert_rules for each row execute function public.alert_rules_limit();

-- 2) 알림 받을 기기 (브라우저가 주는 알림 주소, 본인 것만)
create table if not exists public.push_subs (
  endpoint text primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  p256dh text not null,
  auth text not null,
  ua text,
  created_at timestamptz not null default now()
);
alter table public.push_subs enable row level security;
drop policy if exists "own subs" on public.push_subs;
create policy "own subs" on public.push_subs for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- 3) 서버만 보는 비밀 (정책이 없어서 사이트에서는 못 읽음)
create table if not exists public.app_secrets (k text primary key, v text not null);
alter table public.app_secrets enable row level security;
insert into public.app_secrets (k, v) values ('cron_key', replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''))
  on conflict (k) do nothing;

-- 4) 2분마다 알림 서버 부르기
create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net with schema extensions;
select cron.schedule(
  'portfit-alerts',
  '*/2 * * * *',
  $$ select net.http_post(
       url := 'https://qrtzhjleopsimxcekbgo.supabase.co/functions/v1/alerts',
       headers := jsonb_build_object('Content-Type', 'application/json'),
       body := jsonb_build_object('action', 'check', 'key', (select v from public.app_secrets where k = 'cron_key'))
     ) $$
);
