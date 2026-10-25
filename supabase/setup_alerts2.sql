-- 포트핏 알림 2단계 (Supabase SQL Editor에 통째로 붙여넣고 Run 한 번)
-- 1) '고점 대비 하락' 규칙 칸 추가  2) 아침 요약 알림 설정 표  3) 매시 3분마다 아침 요약 보내기 예약
-- setup_alerts.sql을 먼저 실행한 뒤에 실행하세요. 여러 번 실행해도 괜찮아요.

-- 1) 고점 대비 하락(트레일링) 규칙
alter table public.alert_rules drop constraint if exists alert_rules_kind_check;
alter table public.alert_rules add constraint alert_rules_kind_check check (kind in ('above', 'below', 'trail'));
alter table public.alert_rules add column if not exists trail_pct numeric check (trail_pct is null or (trail_pct > 0 and trail_pct < 100));
alter table public.alert_rules add column if not exists peak numeric;

-- 2) 아침 요약 알림 설정 (본인 것만)
create table if not exists public.alert_prefs (
  user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  digest boolean not null default false,
  digest_hour int not null default 8 check (digest_hour between 0 and 23),
  last_digest date,
  updated_at timestamptz not null default now()
);
alter table public.alert_prefs enable row level security;
drop policy if exists "own prefs" on public.alert_prefs;
create policy "own prefs" on public.alert_prefs for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- 3) 매시 3분: 그 시각(한국 시간)에 받기로 한 사람에게 아침 요약 보내기
select cron.schedule(
  'portfit-digest',
  '3 * * * *',
  $$ select net.http_post(
       url := 'https://qrtzhjleopsimxcekbgo.supabase.co/functions/v1/alerts',
       headers := jsonb_build_object('Content-Type', 'application/json'),
       body := jsonb_build_object('action', 'digest', 'key', (select v from public.app_secrets where k = 'cron_key')),
       timeout_milliseconds := 60000
     ) $$
);
