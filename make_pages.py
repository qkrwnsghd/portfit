"""포트핏 ETF별 공유·검색용 페이지 만들기.

lev_hist.json을 읽어서 레버리지 ETF마다 etf/<종목>/index.html을 만들어요.
- 카톡·커뮤니티에 링크를 붙이면 미리보기(제목·설명·이미지)가 뜨게 Open Graph 정보를 넣음
- 검색엔진이 읽을 수 있게 최근 1달 성적을 글로 적음
- 사람이 열면 바로 포트핏 앱의 해당 ETF 성적표로 이동
- sitemap.xml, robots.txt도 같이 만듦

GitHub Actions: python make_pages.py  (lev_hist.py 다음에 실행)
"""
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://qkrwnsghd.github.io/portfit/"
U_KO = {
    "BTC": "비트코인", "ETH": "이더리움", "SOL": "솔라나", "XRP": "리플(XRP)", "QQQ": "나스닥100", "SPY": "S&P500", "SOXX": "미국 반도체",
    "XLK": "미국 기술주", "IWM": "러셀2000", "XBI": "미국 바이오", "TLT": "미국 장기국채", "TSLA": "테슬라", "NVDA": "엔비디아", "COIN": "코인베이스",
    "MSTR": "스트래티지(MSTR)", "AAPL": "애플", "AMZN": "아마존", "GOOGL": "알파벳", "069500": "코스피200", "229200": "코스닥150",
    "005930": "삼성전자", "000660": "SK하이닉스",
}
KR_NAME = {"122630": "KODEX 레버리지", "123320": "TIGER 레버리지", "252670": "KODEX 200선물인버스2X", "114800": "KODEX 인버스",
           "233740": "KODEX 코스닥150레버리지", "251340": "KODEX 코스닥150선물인버스"}


def tr(dv, c, dates, i0, i1):
    """분배금·배당 넣은 총수익 배수 (앱 levTR과 같은 계산)."""
    f = 1.0
    pos = {d: j for j, d in enumerate(dates)}
    for d, D in dv or []:
        j = pos.get(d)
        if j is None or not (i0 < j <= i1) or not D or D <= 0:
            continue
        k = j - 1
        while k > 0 and not c[k]:
            k -= 1
        if c[k] and c[k] > D:
            f *= c[k] / (c[k] - D)
    return f


def stats(H, sym, n=21):
    e, L = H["etf"][sym]["c"], H["etf"][sym]["L"]
    U = H["under"][H["etf"][sym]["u"]]
    T = len(e) - 1
    while T > 0 and not (e[T] and U[T]):
        T -= 1
    i = max(0, T - n)
    while i < T and not (e[i] and U[i]):
        i += 1
    if T - i < 2:
        return None
    u = U[T] / U[i] * tr(H.get("udv", {}).get(H["etf"][sym]["u"]), U, H["dates"], i, T) - 1
    r = e[T] / e[i] * tr(H["etf"][sym].get("dv"), e, H["dates"], i, T) - 1
    exact = max(-1.0, L * u)
    return {"u": u, "e": r, "exact": exact, "decay": r - exact, "d0": H["dates"][i], "d1": H["dates"][T]}


def pct(v):
    return f"{'+' if v >= 0 else ''}{v * 100:.1f}%"


def page(sym, name, uko, L, s):
    lv = f"{L}배" if L > 0 else f"인버스 {-L}배"
    if s:
        tag = "변동성 손실" if s["decay"] < 0 else "추세 이득"
        title = f"{name} 레버리지 성적표 · 최근 1달 {tag} {s['decay'] * 100:+.1f}%p"
        desc = (f"{s['d0'][5:].replace('-', '/')} 이후 {uko} {pct(s['u'])}, {name} {pct(s['e'])}. "
                f"정확히 {abs(L)}배였다면 {pct(s['exact'])}. 내 매수가로 레버리지 효과·변동성 손실·환차손익을 계산해 보세요.")
    else:
        title = f"{name} 레버리지 손익 계산기"
        desc = f"{name}({uko} {lv})를 1배와 비교해 레버리지 효과와 변동성 손실(음의 복리)을 계산해 보세요."
    app = f"{SITE}?etf={sym}#lev"
    t, d = html.escape(title), html.escape(desc)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t} - 포트핏</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{SITE}etf/{sym}/">
<meta property="og:type" content="website"><meta property="og:site_name" content="포트핏 · 레버리지 손익 계산기">
<meta property="og:title" content="{t}"><meta property="og:description" content="{d}">
<meta property="og:url" content="{SITE}etf/{sym}/"><meta property="og:image" content="{SITE}og.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image"><meta name="theme-color" content="#07090D">
<style>body{{margin:0;background:#07090D;color:#E6EAF2;font:16px/1.6 system-ui,sans-serif;padding:32px 20px}}a{{color:#3CF2D0}}main{{max-width:560px;margin:auto}}</style>
<script>location.replace({json.dumps(app)});</script>
</head><body><main>
<h1>{html.escape(name)} <small>{html.escape(uko)} {lv}</small></h1>
<p>{d}</p>
<p><a href="{html.escape(app)}">포트핏에서 {html.escape(name)} 성적표 보기 →</a></p>
<p style="color:#8A93A6;font-size:13px">기간 {s['d0'] if s else ''} ~ {s['d1'] if s else ''} · 보수·추종오차 포함, 환율 제외 · 계산만 보여주며 매수·매도 권유가 아니에요.</p>
</main></body></html>
"""


def main():
    H = json.load(open(os.path.join(HERE, "lev_hist.json"), encoding="utf-8"))
    urls = [SITE]
    n = 0
    for grp in [H, H.get("kr")]:
        if not grp:
            continue
        for sym, e in grp["etf"].items():
            name = e.get("name") or KR_NAME.get(sym) or sym
            s = stats(grp, sym)
            d = os.path.join(HERE, "etf", sym)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
                f.write(page(sym, name, U_KO.get(e["u"], e["u"]), e["L"], s))
            urls.append(f"{SITE}etf/{sym}/")
            n += 1
    asof = H.get("asof", "")
    with open(os.path.join(HERE, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u in urls:
            f.write(f"  <url><loc>{u}</loc><lastmod>{asof}</lastmod></url>\n")
        f.write("</urlset>\n")
    with open(os.path.join(HERE, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE}sitemap.xml\n")
    print(f"ETF 페이지 {n}개, sitemap.xml, robots.txt 만들었어요.")


if __name__ == "__main__":
    main()
