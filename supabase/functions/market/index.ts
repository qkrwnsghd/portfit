// 포트핏 시세 서버 (Supabase Edge Function: market)
// - 레버리지 탭에서 미국 ETF·주식의 지금 가격과 원·달러 환율을 받아올 때 써요.
// - 브라우저는 보안 규칙(CORS) 때문에 야후 파이낸스에 직접 물어볼 수 없어서, 이 서버가 대신 물어봐요.
// - 정해 둔 종목만 조회할 수 있고, 같은 종목은 20초 동안 저장해 둔 값을 다시 써요.
// - 로그인·개인정보와 상관없는 공개 시세만 다뤄요. (Verify JWT는 꺼 두세요)
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};
const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" } });

const ALLOW = new Set([
  "BITX", "BITU", "SBIT", "ETHU", "ETHT", "SOLT", "XXRP",
  "TQQQ", "SQQQ", "QLD", "UPRO", "SPXL", "SSO", "SPXU", "SOXL", "SOXS", "TECL", "TNA", "LABU", "TMF",
  "TSLL", "NVDL", "NVDU", "CONL", "MSTU", "MSTX", "AAPU", "AMZU", "GGLL",
  "QQQ", "SPY", "SOXX", "XLK", "IWM", "XBI", "TLT", "TSLA", "NVDA", "COIN", "MSTR", "AAPL", "AMZN", "GOOGL",
  "KRW=X",
  // 국내 레버리지 ETF와 기초 1배 ETF (종목코드 → 야후는 코드.KS)
  "122630", "123320", "252670", "114800", "233740", "251340", "069500", "229200",
]);
const TTL = 20_000;
type Quote = { p: number; t: number; reg: number | null; regT: number | null; prev: number | null; state: string; src: string };
const cache = new Map<string, { at: number; q: Quote | null }>();
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15";

async function yahoo(sym: string): Promise<Quote | null> {
  for (const host of ["query1", "query2"]) {
    try {
      const ysym = /^\d{6}$/.test(sym) ? `${sym}.KS` : sym;
      const u = `https://${host}.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ysym)}?range=1d&interval=1m&includePrePost=true`;
      const r = await fetch(u, { headers: { "User-Agent": UA, Accept: "application/json" } });
      if (!r.ok) continue;
      const j = await r.json();
      const res = j?.chart?.result?.[0];
      const m = res?.meta;
      if (!m || !(m.regularMarketPrice > 0)) continue;
      // 프리장·애프터장까지 포함한 가장 최근 1분 봉 가격
      let p = m.regularMarketPrice, t = m.regularMarketTime;
      const ts: number[] = res.timestamp ?? [], cl: (number | null)[] = res.indicators?.quote?.[0]?.close ?? [];
      for (let i = ts.length - 1; i >= 0; i--) {
        if (cl[i] != null && cl[i]! > 0) { if (ts[i] > t) { p = cl[i]!; t = ts[i]; } break; }
      }
      const now = Date.now() / 1000, tp = m.currentTradingPeriod ?? {};
      const inP = (k: string) => tp[k] && now >= tp[k].start && now < tp[k].end;
      const state = inP("regular") ? "REGULAR" : inP("pre") ? "PRE" : inP("post") ? "POST" : "CLOSED";
      return { p, t, reg: m.regularMarketPrice, regT: m.regularMarketTime, prev: m.chartPreviousClose ?? m.previousClose ?? null, state, src: "yahoo" };
    } catch (_) { /* 다음 주소로 */ }
  }
  return null;
}

// 야후가 막히면 스투크(stooq, 약 15분 지연)로 대신
async function stooq(sym: string): Promise<Quote | null> {
  try {
    if (/^\d{6}$/.test(sym)) return null;              // 국내 종목은 스투크 대신 야후만
    const s = sym === "KRW=X" ? "usdkrw" : sym.toLowerCase() + ".us";
    const r = await fetch(`https://stooq.com/q/l/?s=${s}&f=sd2t2c&h&e=csv`, { headers: { "User-Agent": UA } });
    if (!r.ok) return null;
    const line = (await r.text()).trim().split("\n")[1] ?? "";
    const [, d, tm, c] = line.split(",");
    const p = Number(c);
    if (!(p > 0)) return null;
    // 스투크 시각은 폴란드 기준(여름 +2시간, 겨울 +1시간)
    const asUtc = Date.parse(`${d}T${tm}Z`);
    const off = asUtc - Date.parse(new Date(asUtc).toLocaleString("en-US", { timeZone: "Europe/Warsaw" }) + " UTC");
    const t = Number.isFinite(asUtc + off) ? Math.floor((asUtc + off) / 1000) : Math.floor(Date.now() / 1000);
    return { p, t, reg: p, regT: t, prev: null, state: "UNKNOWN", src: "stooq" };
  } catch (_) { return null; }
}

async function quote(sym: string): Promise<Quote | null> {
  const c = cache.get(sym);
  if (c && Date.now() - c.at < TTL) return c.q;
  const q = (await yahoo(sym)) ?? (await stooq(sym));
  cache.set(sym, { at: Date.now(), q });
  return q;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "GET") return json({ error: "method" }, 405);
  const syms = [...new Set((new URL(req.url).searchParams.get("s") ?? "").toUpperCase().split(",").map((s) => s.trim()))]
    .filter((s) => ALLOW.has(s)).slice(0, 16);
  if (!syms.length) return json({ error: "symbols" }, 400);
  const out: Record<string, Quote | null> = {};
  await Promise.all(syms.map(async (s) => { out[s] = await quote(s); }));
  return json({ ok: true, at: Math.floor(Date.now() / 1000), q: out });
});
