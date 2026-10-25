// 포트핏 규칙 알림 서버 (Supabase Edge Function: alerts)
// - 사용자가 직접 정한 매도 규칙(목표가·손절가)에 가격이 닿으면 폰으로 알림(웹 푸시)을 보내요.
// - 2분마다 데이터베이스 예약 작업(pg_cron)이 action:"check"로 이 함수를 불러요. 예약 작업만 아는 열쇠(cron_key)로 확인해요.
// - 알림 암호화 열쇠(VAPID)는 처음 한 번 이 함수가 스스로 만들어 app_secrets 표에 저장해요. (채팅·코드에 비밀 열쇠가 나오지 않음)
// - 종목을 추천하거나 사고팔라고 하지 않아요. 사용자가 정한 규칙과 사용자가 남긴 말만 다시 보여줘요.
// - Verify JWT는 꺼 두세요. (로그인 확인은 함수 안에서 직접 해요)
import { createClient } from "jsr:@supabase/supabase-js@2";
import webpush from "npm:web-push@3.6.7";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};
const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" } });
const admin = () => createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const SITE = "https://qkrwnsghd.github.io/portfit/";
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";
const SYM_OK = /^([A-Z]{1,6}(\.[A-Z])?|\d{6})$/;
const CRYPTO = new Set(["BTC", "ETH", "SOL", "XRP", "DOGE"]);

// ----- 알림 열쇠 -----
let vapid: { pub: string; priv: string } | null = null;
async function getVapid(db: ReturnType<typeof admin>) {
  if (vapid) return vapid;
  const { data } = await db.from("app_secrets").select("k, v").in("k", ["vapid_public", "vapid_private"]);
  const m = Object.fromEntries((data ?? []).map((r: { k: string; v: string }) => [r.k, r.v]));
  if (m.vapid_public && m.vapid_private) vapid = { pub: m.vapid_public, priv: m.vapid_private };
  else {
    const k = webpush.generateVAPIDKeys();
    await db.from("app_secrets").upsert([{ k: "vapid_public", v: k.publicKey }, { k: "vapid_private", v: k.privateKey }], { onConflict: "k", ignoreDuplicates: true });
    const { data: again } = await db.from("app_secrets").select("k, v").in("k", ["vapid_public", "vapid_private"]);   // 동시에 두 번 만들어졌으면 먼저 저장된 것을 씀
    const a = Object.fromEntries((again ?? []).map((r: { k: string; v: string }) => [r.k, r.v]));
    vapid = { pub: a.vapid_public, priv: a.vapid_private };
  }
  webpush.setVapidDetails(SITE, vapid.pub, vapid.priv);
  return vapid;
}

// ----- 시세 (야후 파이낸스, 프리·애프터장 포함 가장 최근 가격) -----
type Quote = { sym: string; p: number; t: number; cur: string; state: string; name?: string };
const qcache = new Map<string, { at: number; q: Quote | null }>();
async function yahoo(ysym: string): Promise<Omit<Quote, "sym"> | null> {
  for (const host of ["query1", "query2"]) {
    try {
      const r = await fetch(`https://${host}.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ysym)}?range=1d&interval=1m&includePrePost=true`, { headers: { "User-Agent": UA } });
      if (!r.ok) continue;
      const res = (await r.json())?.chart?.result?.[0], m = res?.meta;
      if (!m || !(m.regularMarketPrice > 0)) continue;
      let p = m.regularMarketPrice, t = m.regularMarketTime;
      const ts: number[] = res.timestamp ?? [], cl: (number | null)[] = res.indicators?.quote?.[0]?.close ?? [];
      for (let i = ts.length - 1; i >= 0; i--) if (cl[i] != null && cl[i]! > 0) { if (ts[i] > t) { p = cl[i]!; t = ts[i]; } break; }
      const now = Date.now() / 1000, tp = m.currentTradingPeriod ?? {};
      const inP = (k: string) => tp[k] && now >= tp[k].start && now < tp[k].end;
      return { p, t, cur: m.currency ?? "USD", state: inP("regular") ? "REGULAR" : inP("pre") ? "PRE" : inP("post") ? "POST" : "CLOSED", name: m.shortName ?? m.longName };
    } catch (_) { /* 다음 주소 */ }
  }
  return null;
}
async function quote(sym: string): Promise<Quote | null> {
  const c = qcache.get(sym);
  if (c && Date.now() - c.at < 30_000) return c.q;
  const tries = CRYPTO.has(sym) ? [`${sym}-USD`] : /^\d{6}$/.test(sym) ? [`${sym}.KS`, `${sym}.KQ`] : [sym.replace(".", "-")];
  let q: Quote | null = null;
  for (const y of tries) { const v = await yahoo(y); if (v) { q = { sym, ...v }; break; } }
  qcache.set(sym, { at: Date.now(), q });
  return q;
}

// ----- 알림 보내기 -----
async function pushTo(db: ReturnType<typeof admin>, userId: string, payload: Record<string, unknown>) {
  await getVapid(db);
  const { data: subs } = await db.from("push_subs").select("endpoint, p256dh, auth").eq("user_id", userId);
  let sent = 0, failed = 0;
  for (const s of subs ?? []) {
    try {
      await webpush.sendNotification({ endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } }, JSON.stringify(payload), { TTL: 3600, urgency: "high" });
      sent++;
    } catch (e) {
      failed++;
      const code = (e as { statusCode?: number }).statusCode;
      if (code === 404 || code === 410) await db.from("push_subs").delete().eq("endpoint", s.endpoint);   // 알림을 끈 기기는 지움
    }
  }
  return { sent, failed, devices: (subs ?? []).length };
}
const fmtP = (p: number, cur: string) => cur === "KRW" ? `${Math.round(p).toLocaleString("ko-KR")}원` : `$${p >= 1000 ? Math.round(p).toLocaleString("en-US") : p.toFixed(2)}`;

async function check(db: ReturnType<typeof admin>) {
  const { data: rules } = await db.from("alert_rules").select("*").eq("active", true).limit(1000);
  const syms = [...new Set((rules ?? []).map((r: { sym: string }) => r.sym))];
  const quotes: Record<string, Quote | null> = {};
  await Promise.all(syms.map(async (s) => { quotes[s] = await quote(s); }));
  let fired = 0;
  const now = new Date().toISOString();
  for (const r of rules ?? []) {
    const q = quotes[r.sym];
    if (!q) continue;
    const hit = r.kind === "above" ? q.p >= Number(r.price) : q.p <= Number(r.price);
    if (!hit) { await db.from("alert_rules").update({ last_price: q.p, checked_at: now }).eq("id", r.id); continue; }
    // 먼저 '울림'으로 바꾼 쪽만 알림을 보냄 (예약 작업이 겹쳐도 한 번만)
    const { data: claimed } = await db.from("alert_rules").update({ active: false, fired_at: now, fired_price: q.p, last_price: q.p, checked_at: now })
      .eq("id", r.id).eq("active", true).select("id");
    if (!claimed || !claimed.length) continue;
    const pnl = r.base ? ` · 평균단가 대비 ${q.p >= r.base ? "+" : ""}${((q.p / r.base - 1) * 100).toFixed(1)}%` : "";
    const lines = [`지금 ${fmtP(q.p, q.cur)}${pnl}`];
    if (r.note) lines.push(`내가 정한 규칙: ${r.note}`);
    if (r.hint) lines.push(r.hint);
    await pushTo(db, r.user_id, { title: `🔔 ${r.sym} ${r.label || (r.kind === "above" ? "목표가 도달" : "손절가 도달")}`, body: lines.join("\n"), url: SITE + "#lev", tag: `rule-${r.id}` });
    fired++;
  }
  return { rules: (rules ?? []).length, syms: syms.length, fired };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const db = admin();
  const url = new URL(req.url);
  if (req.method === "GET") {
    if (url.searchParams.has("vapid")) return json({ key: (await getVapid(db)).pub });
    const s = (url.searchParams.get("quote") ?? "").toUpperCase().trim();
    if (!SYM_OK.test(s)) return json({ error: "symbol" }, 400);
    const q = await quote(s);
    return q ? json({ ok: true, q }) : json({ error: "notfound" }, 404);
  }
  if (req.method !== "POST") return json({ error: "method" }, 405);
  const body = await req.json().catch(() => ({}));
  if (body.action === "check") {
    const { data } = await db.from("app_secrets").select("v").eq("k", "cron_key").maybeSingle();
    if (!data || !body.key || body.key !== data.v) return json({ error: "key" }, 401);
    return json({ ok: true, ...(await check(db)) });
  }
  if (body.action === "test") {
    const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
    const { data: auth } = await db.auth.getUser(token);
    if (!auth?.user) return json({ error: "login" }, 401);
    const r = await pushTo(db, auth.user.id, { title: "🔔 포트핏 알림 시험", body: "이렇게 알림이 와요. 내가 정한 규칙에 가격이 닿으면 이 기기로 알려드릴게요.", url: SITE + "#lev", tag: "test" });
    return json({ ok: true, ...r });
  }
  return json({ error: "action" }, 400);
});
