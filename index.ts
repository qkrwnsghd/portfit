// 포트핏 AI 캡처 인식 서버 (Supabase Edge Function: read-screens)
// - 로그인한 사람만 사용 가능
// - 1인당 하루 DAILY_PER_USER장(기본 20), 전체 하루 DAILY_TOTAL장(기본 300) 제한
// - Claude API 키(ANTHROPIC_API_KEY)는 Supabase Secrets에만 저장하고, 사이트 코드에는 넣지 않음
// - 캡처 이미지는 Claude에게 읽혀서 결과만 돌려주고, 어디에도 저장하지 않음
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};
const PER_USER = Number(Deno.env.get("DAILY_PER_USER") ?? 20);
const TOTAL = Number(Deno.env.get("DAILY_TOTAL") ?? 300);
const MODEL = Deno.env.get("ANTHROPIC_MODEL") ?? "claude-haiku-5-5";
const KEY = Deno.env.get("ANTHROPIC_API_KEY") ?? "";
const PROMPT = `첨부한 이미지는 토스증권 앱 「주문내역」 화면 캡처예요. 이미지는 위에서 아래로 스크롤하며 찍은 순서대로 붙어 있어요.
화면 구조: 왼쪽에 날짜(예: 10.22, 같은 날의 두 번째 거래부터는 날짜를 생략), 가운데에 종목명과 그 아래 "N주 구매 완료" / "N원 판매 완료" 같은 줄, 오른쪽에 "주당 N원".
할 일: 모든 거래를 화면 위에서 아래 순서로 빠짐없이 뽑아 주세요.
- date: 그 거래가 속한 날짜를 "월.일" 형식으로 (날짜가 생략된 거래는 위쪽 가장 가까운 날짜). {{PREV}}
- name: 종목명 그대로 (예: SOXL, 테슬라, 스페이스X)
- side: "구매" 또는 "판매"
- amount: 숫자만 (쉼표 없이, 예: 84135 또는 3)
- unit: "주"(수량으로 거래) 또는 "원"(금액으로 거래)
- price: 주당 가격 숫자만 (쉼표 없이)
규칙: 캡처끼리 겹쳐서 두 번 보이는 거래는 한 번만 넣으세요. 화면 끝에 잘려서 정보가 다 보이지 않는 거래와 취소된 주문은 빼세요. 추측해서 지어내지 마세요.
JSON 배열만 답하세요. 예: [{"date":"10.22","name":"SOXL","side":"판매","amount":3,"unit":"주","price":134659}]`;

const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { ...CORS, "Content-Type": "application/json" } });

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  const { data: auth } = await admin.auth.getUser(token);
  const user = auth?.user;
  if (!user) return json({ error: "login" }, 401);

  const day = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10); // 한국 날짜
  const { data: mine } = await admin.from("ai_usage").select("count").eq("user_id", user.id).eq("day", day).maybeSingle();
  const used = mine?.count ?? 0;
  if (req.method === "GET") return json({ ready: KEY.length > 0, left: Math.max(0, PER_USER - used), perDay: PER_USER });
  if (!KEY) return json({ error: "no_key" }, 503);

  const body = await req.json().catch(() => null);
  const imgs = (Array.isArray(body?.images) ? body.images : [])
    .filter((i: any) => /^image\/(jpeg|png|webp)$/.test(i?.media_type) && typeof i?.data === "string" && i.data.length < 5_000_000)
    .slice(0, 5);
  if (!imgs.length) return json({ error: "no_images" }, 400);
  if (used + imgs.length > PER_USER) return json({ error: "user_limit", left: Math.max(0, PER_USER - used) }, 429);
  const { data: all } = await admin.from("ai_usage").select("count").eq("day", day);
  const total = (all ?? []).reduce((s: number, r: any) => s + (r.count ?? 0), 0);
  if (total + imgs.length > TOTAL) return json({ error: "global_limit" }, 429);

  // 먼저 사용량을 올려 두고, 실패하면 되돌림
  await admin.from("ai_usage").upsert({ user_id: user.id, day, count: used + imgs.length });
  const refund = () => admin.from("ai_usage").upsert({ user_id: user.id, day, count: used });

  const prev = typeof body?.prevDate === "string" && /^\d{1,2}\.\d{1,2}$/.test(body.prevDate)
    ? `첫 이미지 맨 위에 날짜 없이 시작하는 거래는 ${body.prevDate} 날짜예요.` : "";
  const content = [
    ...imgs.map((i: any) => ({ type: "image", source: { type: "base64", media_type: i.media_type, data: i.data } })),
    { type: "text", text: PROMPT.replace("{{PREV}}", prev) },
  ];
  let r: Response;
  try {
    r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": KEY, "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model: MODEL, max_tokens: 4000, messages: [{ role: "user", content }] }),
    });
  } catch (_) { await refund(); return json({ error: "ai_unreachable" }, 502); }
  if (!r.ok) { await refund(); return json({ error: "ai", status: r.status, detail: (await r.text()).slice(0, 300) }, 502); }
  const out = await r.json();
  const text = (out.content ?? []).filter((c: any) => c.type === "text").map((c: any) => c.text).join("");
  const m = text.match(/\[[\s\S]*\]/);
  try {
    const items = m ? JSON.parse(m[0]) : [];
    return json({ items, left: Math.max(0, PER_USER - used - imgs.length) });
  } catch (_) {
    await refund(); return json({ error: "invalid_json" }, 502);
  }
});
