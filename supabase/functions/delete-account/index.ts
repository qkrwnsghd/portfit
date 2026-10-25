// 포트핏 회원 탈퇴 서버 (Supabase Edge Function: delete-account)
// - 로그인한 본인만 자기 계정을 지울 수 있음 (요청에 담긴 로그인 토큰으로 확인)
// - 저장된 기록(user_data), AI 사용량(ai_usage)을 지운 뒤 계정(이메일)을 완전히 삭제
// - 계정 삭제는 관리자 권한이 필요해서 사이트가 아니라 서버에서만 할 수 있음
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { ...CORS, "Content-Type": "application/json" } });

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "method" }, 405);
  const body = await req.json().catch(() => null);
  if (!body || body.confirm !== true) return json({ error: "confirm" }, 400);

  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  const { data: auth } = await admin.auth.getUser(token);
  const user = auth?.user;
  if (!user) return json({ error: "login" }, 401);

  await admin.from("user_data").delete().eq("user_id", user.id);
  await admin.from("ai_usage").delete().eq("user_id", user.id);
  const { error } = await admin.auth.admin.deleteUser(user.id);
  if (error) return json({ error: "delete", detail: error.message }, 500);
  return json({ ok: true });
});
