// 포트핏 규칙 알림 서버 (Supabase Edge Function: alerts)
// - 사용자가 직접 정한 매도 규칙(목표가·손절가)에 가격이 닿으면 폰으로 알림(웹 푸시)을 보내요.
// - 2분마다 데이터베이스 예약 작업(pg_cron)이 action:"check"로 이 함수를 불러요. 예약 작업만 아는 열쇠(cron_key)로 확인해요.
// - 규칙 종류: above(이 가격 이상), below(이 가격 이하), trail(규칙을 만든 뒤 최고가에서 N% 떨어지면)
// - 매시 3분에 action:"digest"로 불리면, 그 시각(한국 시간)에 받기로 한 사람에게 '아침 요약'을 보내요.
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
// 야후가 안 될 때 대신 쓰는 곳: 국내는 네이버 증권, 코인은 코인베이스, 미국은 스투크(약 15분 지연)
async function backup(sym: string): Promise<Omit<Quote, "sym"> | null> {
  try {
    if (/^\d{6}$/.test(sym)) {
      const r = await fetch(`https://polling.finance.naver.com/api/realtime/domestic/stock/${sym}`, { headers: { "User-Agent": UA, Referer: "https://m.stock.naver.com/" } });
      const d = r.ok ? (await r.json())?.datas?.[0] : null, p = Number(String(d?.closePrice ?? "").replace(/,/g, ""));
      return p > 0 ? { p, t: Math.floor((Date.parse(d.localTradedAt ?? "") || Date.now()) / 1000), cur: "KRW", state: d.marketStatus === "OPEN" ? "REGULAR" : "CLOSED", name: d.stockName } : null;
    }
    if (CRYPTO.has(sym)) {
      const r = await fetch(`https://api.exchange.coinbase.com/products/${sym}-USD/ticker`, { headers: { "User-Agent": UA } });
      const p = r.ok ? Number((await r.json())?.price) : 0;
      return p > 0 ? { p, t: Math.floor(Date.now() / 1000), cur: "USD", state: "REGULAR" } : null;
    }
    if (sym === "KRW") return null;
    const r = await fetch(`https://stooq.com/q/l/?s=${sym.toLowerCase().replace(".", "-")}.us&f=sd2t2c&h&e=csv`, { headers: { "User-Agent": UA } });
    const c = r.ok ? Number((await r.text()).trim().split("\n")[1]?.split(",")[3]) : 0;
    return c > 0 ? { p: c, t: Math.floor(Date.now() / 1000), cur: "USD", state: "UNKNOWN" } : null;
  } catch (_) { return null; }
}
async function quote(sym: string): Promise<Quote | null> {
  const c = qcache.get(sym);
  if (c && Date.now() - c.at < 30_000) return c.q;
  const tries = sym === "KRW" ? ["KRW=X"] : CRYPTO.has(sym) ? [`${sym}-USD`] : /^\d{6}$/.test(sym) ? [`${sym}.KS`, `${sym}.KQ`] : [sym.replace(".", "-")];
  let q: Quote | null = null;
  // 국내는 네이버(실시간)를 먼저, 나머지는 야후를 먼저
  if (/^\d{6}$/.test(sym)) { const b = await backup(sym); if (b) q = { sym, ...b }; }
  if (!q) for (const y of tries) { const v = await yahoo(y); if (v) { q = { sym, ...v }; break; } }
  if (!q) { const b = await backup(sym); if (b) q = { sym, ...b }; }
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
    let hit: boolean, extra: Record<string, unknown> = {};
    if (r.kind === "trail") {
      // 최고가를 따라 올라가는 손절선: 최고가 × (1 − N%)
      const peak = Math.max(Number(r.peak) || q.p, q.p), line = peak * (1 - Number(r.trail_pct) / 100);
      extra = { peak, price: +line.toPrecision(8) };
      hit = q.p <= line;
    } else hit = r.kind === "above" ? q.p >= Number(r.price) : q.p <= Number(r.price);
    if (!hit) { await db.from("alert_rules").update({ last_price: q.p, checked_at: now, ...extra }).eq("id", r.id); continue; }
    // 먼저 '울림'으로 바꾼 쪽만 알림을 보냄 (예약 작업이 겹쳐도 한 번만)
    const { data: claimed } = await db.from("alert_rules").update({ active: false, fired_at: now, fired_price: q.p, last_price: q.p, checked_at: now, ...extra })
      .eq("id", r.id).eq("active", true).select("id");
    if (!claimed || !claimed.length) continue;
    const pnl = r.base ? ` · 평균단가 대비 ${q.p >= r.base ? "+" : ""}${((q.p / r.base - 1) * 100).toFixed(1)}%` : "";
    const lines = [`지금 ${fmtP(q.p, q.cur)}${pnl}`];
    if (r.kind === "trail") lines.push(`규칙을 만든 뒤 최고가 ${fmtP(Number(extra.peak), q.cur)}에서 ${r.trail_pct}% 넘게 떨어졌어요.`);
    if (r.note) lines.push(`내가 정한 규칙: ${r.note}`);
    if (r.hint) lines.push(r.hint);
    await pushTo(db, r.user_id, { title: `🔔 ${LEV_NAME[r.sym] ?? r.sym} ${r.label || (r.kind === "above" ? "목표가 도달" : r.kind === "trail" ? "고점 대비 하락" : "손절가 도달")}`, body: lines.join("\n"), url: SITE + "#lev", tag: `rule-${r.id}` });
    fired++;
  }
  return { rules: (rules ?? []).length, syms: syms.length, fired };
}

// ----- 아침 요약 -----
// 레버리지 ETF: [기초자산 야후 티커, 배율]  (사이트의 LEV_ETFS와 같은 표)
const LEV: Record<string, [string, number, string]> = {
  BITX: ["BTC-USD", 2, "비트코인"], BITU: ["BTC-USD", 2, "비트코인"], SBIT: ["BTC-USD", -2, "비트코인"], ETHU: ["ETH-USD", 2, "이더리움"], ETHT: ["ETH-USD", 2, "이더리움"],
  SOLT: ["SOL-USD", 2, "솔라나"], XXRP: ["XRP-USD", 2, "리플"], TQQQ: ["QQQ", 3, "나스닥100"], SQQQ: ["QQQ", -3, "나스닥100"], QLD: ["QQQ", 2, "나스닥100"],
  UPRO: ["SPY", 3, "S&P500"], SPXL: ["SPY", 3, "S&P500"], SSO: ["SPY", 2, "S&P500"], SPXU: ["SPY", -3, "S&P500"], SOXL: ["SOXX", 3, "반도체"], SOXS: ["SOXX", -3, "반도체"],
  TECL: ["XLK", 3, "기술주"], TNA: ["IWM", 3, "러셀2000"], LABU: ["XBI", 3, "바이오"], TMF: ["TLT", 3, "장기국채"], TSLL: ["TSLA", 2, "테슬라"], NVDL: ["NVDA", 2, "엔비디아"],
  NVDU: ["NVDA", 2, "엔비디아"], CONL: ["COIN", 2, "코인베이스"], MSTU: ["MSTR", 2, "스트래티지"], MSTX: ["MSTR", 2, "스트래티지"], AAPU: ["AAPL", 2, "애플"],
  AMZU: ["AMZN", 2, "아마존"], GGLL: ["GOOGL", 2, "알파벳"],
  // 국내: 기초자산은 같은 지수를 1배로 따라가는 ETF
  "122630": ["069500.KS", 2, "코스피200"], "123320": ["069500.KS", 2, "코스피200"], "252670": ["069500.KS", -2, "코스피200"], "114800": ["069500.KS", -1, "코스피200"],
  "233740": ["229200.KS", 2, "코스닥150"], "251340": ["229200.KS", -1, "코스닥150"],
};
const LEV_NAME: Record<string, string> = { "122630": "KODEX 레버리지", "123320": "TIGER 레버리지", "252670": "KODEX 200선물인버스2X", "114800": "KODEX 인버스", "233740": "KODEX 코스닥150레버리지", "251340": "KODEX 코스닥150선물인버스" };
type Daily = { ts: number[]; c: number[]; splits: { d: number; r: number }[]; last: number };
const dcache = new Map<string, { at: number; v: Daily | null }>();
async function daily(ysym: string, interval = "1d", range = "2y"): Promise<Daily | null> {
  const key = `${ysym}|${interval}|${range}`, hit = dcache.get(key);
  if (hit && Date.now() - hit.at < 600_000) return hit.v;
  let v: Daily | null = null;
  try {
    const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ysym)}?range=${range}&interval=${interval}&events=split`, { headers: { "User-Agent": UA } });
    if (r.ok) {
      const res = (await r.json())?.chart?.result?.[0];
      const ts: number[] = res?.timestamp ?? [], cl: (number | null)[] = res?.indicators?.quote?.[0]?.close ?? [];
      const T: number[] = [], C: number[] = [];
      ts.forEach((t, i) => { if (cl[i] != null && cl[i]! > 0) { T.push(t); C.push(cl[i]!); } });
      const sp = Object.values(res?.events?.splits ?? {}) as { date: number; numerator: number; denominator: number }[];
      if (C.length) v = { ts: T, c: C, splits: sp.map((x) => ({ d: x.date, r: x.numerator / x.denominator })), last: res?.meta?.regularMarketPrice ?? C[C.length - 1] };
    }
  } catch (_) { /* 없음 */ }
  dcache.set(key, { at: Date.now(), v });
  return v;
}
const kstDate = (ms = Date.now()) => new Date(ms + 9 * 3600_000).toISOString().slice(0, 10);
const pctS = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
const wonS = (v: number) => `${v >= 0 ? "+" : "-"}${Math.abs(Math.round(v)).toLocaleString("ko-KR")}원`;

async function digestFor(db: ReturnType<typeof admin>, userId: string) {
  const { data: ud } = await db.from("user_data").select("data").eq("user_id", userId).maybeSingle();
  let pos: { sym: string; date: string; qty: number; price: number; cur: string }[] = [];
  try { const raw = ud?.data?.["portfit-lev-v1"]; const j = typeof raw === "string" ? JSON.parse(raw) : raw; pos = Array.isArray(j?.pos) ? j.pos : []; } catch (_) { pos = []; }
  pos = pos.filter((p) => LEV[p.sym] && p.qty > 0 && p.price > 0);
  const fxq = await quote("KRW");                       // KRW → 야후 KRW=X
  const fx = fxq?.p ?? null;
  const lines: string[] = [];
  let totalPnl = 0, totalCost = 0, n = 0;
  for (const sym of [...new Set(pos.map((p) => p.sym))]) {
    const [usym, L, uko] = LEV[sym], kr = /^\d{6}$/.test(sym);
    const e = await daily(kr ? `${sym}.KS` : sym);
    if (!e || e.c.length < 2) continue;
    const k = e.c.length - 1, eRet = e.c[k] / e.c[k - 1] - 1;
    // 기초자산의 같은 기간 움직임: 주식은 일봉, 코인은 미국 장 마감 시각(시작 시각 + 6.5시간)의 1시간 봉
    let uRet: number | null = null;
    if (usym.endsWith("-USD")) {
      const h = await daily(usym, "1h", "5d");
      const at = (t: number): number | null => {
        if (!h) return null;
        let best: number | null = null;
        for (let i = 0; i < h.ts.length; i++) if (h.ts[i] <= t - 3600 + 60) best = h.c[i];
        return best;
      };
      const a = at(e.ts[k - 1] + 23400), b = at(e.ts[k] + 23400);
      if (a && b) uRet = b / a - 1;
    } else {
      const u = await daily(usym, "1d", "1mo");
      if (u && u.c.length >= 2) uRet = u.c[u.c.length - 1] / u.c[u.c.length - 2] - 1;
    }
    let pnl = 0, cost = 0;
    const rate = kr ? 1 : fx;
    if (rate) for (const p of pos.filter((x) => x.sym === sym)) {
      const t0 = Date.parse(p.date + "T00:00:00+09:00") / 1000;
      const ratio = e.splits.filter((s) => s.d > t0).reduce((r, s) => r * s.r, 1);     // 산 뒤 분할·병합 반영
      const c = p.cur === "USD" ? p.qty * p.price * rate : p.qty * p.price;
      cost += c; pnl += p.qty * ratio * e.last * rate - c;
    }
    totalPnl += pnl; totalCost += cost; n++;
    if (lines.length < 3) lines.push(`${LEV_NAME[sym] ?? sym}: ${uko} ${uRet == null ? "?" : pctS(uRet)} → ${pctS(eRet)}${uRet == null ? "" : ` (딱 ${L}배면 ${pctS(L * uRet)})`}${cost ? ` · 손익 ${wonS(pnl)}` : ""}`);
  }
  const { data: rules } = await db.from("alert_rules").select("sym, kind, price, last_price").eq("user_id", userId).eq("active", true);
  if (!n && !(rules ?? []).length) return null;
  const body: string[] = [];
  if (n) body.push(...lines, totalCost ? `전체 손익 약 ${wonS(totalPnl)} (${pctS(totalPnl / totalCost)})` : "");
  if ((rules ?? []).length) body.push(`지켜보는 규칙 ${(rules ?? []).length}개`);
  return { title: `☀️ 포트핏 아침 요약 · ${kstDate().slice(5).replace("-", "/")}`, body: body.filter(Boolean).join("\n"), url: SITE + "#lev", tag: "digest" };
}
async function digest(db: ReturnType<typeof admin>) {
  const hour = new Date(Date.now() + 9 * 3600_000).getUTCHours(), today = kstDate();
  const { data: prefs } = await db.from("alert_prefs").select("user_id, last_digest").eq("digest", true).eq("digest_hour", hour);
  let sent = 0;
  for (const p of prefs ?? []) {
    if (p.last_digest === today) continue;
    const { data: claimed } = await db.from("alert_prefs").update({ last_digest: today }).eq("user_id", p.user_id).or(`last_digest.is.null,last_digest.lt.${today}`).select("user_id");
    if (!claimed || !claimed.length) continue;          // 예약 작업이 겹쳐도 하루 한 번만
    const msg = await digestFor(db, p.user_id);
    if (msg) { await pushTo(db, p.user_id, msg); sent++; }
  }
  return { hour, users: (prefs ?? []).length, sent };
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
  if (body.action === "digest") {
    const { data } = await db.from("app_secrets").select("v").eq("k", "cron_key").maybeSingle();
    if (!data || !body.key || body.key !== data.v) return json({ error: "key" }, 401);
    return json({ ok: true, ...(await digest(db)) });
  }
  if (body.action === "digest_me") {               // 사용자가 '지금 한 번 받아보기'를 눌렀을 때
    const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
    const { data: auth } = await db.auth.getUser(token);
    if (!auth?.user) return json({ error: "login" }, 401);
    const msg = await digestFor(db, auth.user.id);
    if (!msg) return json({ ok: true, sent: 0, empty: true });
    return json({ ok: true, ...(await pushTo(db, auth.user.id, msg)) });
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
