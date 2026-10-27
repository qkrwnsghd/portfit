-- 포트핏 알림 3단계: 새 국내 종목코드(영문 섞인 6자리, 예: 0193W0 단일종목 레버리지)도 규칙에 쓸 수 있게
-- Supabase SQL Editor에 붙여넣고 Run 한 번. 여러 번 실행해도 괜찮아요.
alter table public.alert_rules drop constraint if exists alert_rules_sym_check;
alter table public.alert_rules add constraint alert_rules_sym_check check (sym ~ '^([A-Z]{1,6}(\.[A-Z])?|[0-9][0-9A-Z]{5})$');
