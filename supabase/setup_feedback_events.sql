-- 포트핏: 의견 보내기(feedback) + 익명 사용 통계(events)
-- 누구나 '쓰기'만 할 수 있고, 읽기는 Supabase 관리 화면(운영자)에서만 가능해요.
create table public.feedback (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  user_id uuid references auth.users(id) on delete set null,
  rating smallint check (rating between 1 and 4),
  message text not null check (char_length(message) between 1 and 2000),
  contact text check (char_length(contact) <= 100),
  page text check (char_length(page) <= 40),
  standalone boolean
);
alter table public.feedback enable row level security;
create policy "send feedback" on public.feedback for insert to anon, authenticated
  with check (user_id is null or user_id = auth.uid());

create table public.events (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  anon_id text not null check (char_length(anon_id) <= 64),
  event text not null check (char_length(event) <= 40),
  meta jsonb not null default '{}'::jsonb check (pg_column_size(meta) < 2000)
);
alter table public.events enable row level security;
create policy "send events" on public.events for insert to anon, authenticated with check (true);
create index events_created_idx on public.events (created_at);
