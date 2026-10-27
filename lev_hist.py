"""포트핏 레버리지 ETF 시세 기록 수집기.

GitHub Actions가 매일 실행해 lev_hist.json을 만듭니다.
포트핏 '레버리지' 탭에서 "산 날부터 지금까지 기초자산(비트코인, 나스닥 등)이 얼마나 움직였고,
그동안 레버리지 ETF는 얼마나 움직였는지"를 계산할 때 써요.

- 미국 장 마감 날짜 기준으로 ETF 종가, 기초자산 가격, 원·달러 환율을 나란히 저장
- 코인은 24시간 거래되니까, 미국 장이 끝난 시각(뉴욕 16시)의 코인 가격을 1시간 봉에서 골라 씀
- 액면분할·병합 기록도 같이 저장 (레버리지 ETF는 병합이 잦아요)
- 실제 배율 확인: 매일 ETF 수익률 ÷ 기초자산 수익률의 기울기(beta)를 같이 저장

GitHub Actions: python lev_hist.py
직접 시험:     python lev_hist.py --dry   (파일을 쓰지 않고 결과만 출력)
"""
import datetime
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "lev_hist.json")
DAYS = 729            # 야후 1시간 봉은 최근 730일까지만 줘요

# ETF 티커: (기초자산, 배율). 배율이 음수면 인버스(반대로 움직임)
LEV = {
    "BITX": ("BTC", 2), "BITU": ("BTC", 2), "SBIT": ("BTC", -2),
    "ETHU": ("ETH", 2), "ETHT": ("ETH", 2), "SOLT": ("SOL", 2), "XXRP": ("XRP", 2),
    "TQQQ": ("QQQ", 3), "SQQQ": ("QQQ", -3), "QLD": ("QQQ", 2),
    "UPRO": ("SPY", 3), "SPXL": ("SPY", 3), "SSO": ("SPY", 2), "SPXU": ("SPY", -3),
    "SOXL": ("SOXX", 3), "SOXS": ("SOXX", -3), "TECL": ("XLK", 3),
    "TNA": ("IWM", 3), "LABU": ("XBI", 3), "TMF": ("TLT", 3),
    "TSLL": ("TSLA", 2), "NVDL": ("NVDA", 2), "NVDU": ("NVDA", 2),
    "CONL": ("COIN", 2), "MSTU": ("MSTR", 2), "MSTX": ("MSTR", 2),
    "AAPU": ("AAPL", 2), "AMZU": ("AMZN", 2), "GGLL": ("GOOGL", 2),
}
CRYPTO = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XRP": "XRP-USD"}
# 국내 레버리지 ETF: 종목코드 → (기초 1배 ETF 종목코드, 배율). 한국거래소 날짜로 따로 저장 (명절 휴장일이 미국과 달라서)
# 기초자산 지수 대신 같은 지수를 1배로 따라가는 ETF(KODEX 200, KODEX 코스닥150)의 종가를 써요.
KR = {
    "122630": ("069500", 2),    # KODEX 레버리지 (코스피200 2배)
    "123320": ("069500", 2),    # TIGER 레버리지
    "252670": ("069500", -2),   # KODEX 200선물인버스2X
    "114800": ("069500", -1),   # KODEX 인버스
    "233740": ("229200", 2),    # KODEX 코스닥150레버리지
    "251340": ("229200", -1),   # KODEX 코스닥150선물인버스
}


def r6(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    x = float(x)
    if x == 0:
        return 0
    d = max(0, 6 - int(math.floor(math.log10(abs(x)))) - 1)   # 유효숫자 6자리
    return round(x, d)


def closes(df, sym):
    """yf.download 결과에서 한 종목의 종가 열을 꺼냄."""
    try:
        s = df["Close"][sym] if ("Close", sym) in df.columns else df["Close"]
    except KeyError:
        return None
    s = s.dropna()
    return s if len(s) else None


SINGLE_U = {"삼성전자": "005930", "하이닉스": "000660"}


def discover_single():
    """한국거래소 ETF 목록에서 삼성전자·SK하이닉스 단일종목 레버리지·인버스를 이름으로 찾음 (새 상품이 나와도 자동 추가)."""
    out = {}
    try:
        import datetime as dt
        from pykrx import stock
        tickers = []
        for back in range(0, 8):
            day = (dt.date.today() - dt.timedelta(days=back)).strftime("%Y%m%d")
            try:
                tickers = stock.get_etf_ticker_list(day)
            except Exception:                  # noqa: BLE001
                tickers = []
            if tickers:
                break
        for t in tickers:
            n = str(stock.get_etf_ticker_name(t) or "")
            if "단일종목" not in n:
                continue
            u = next((c for k, c in SINGLE_U.items() if k in n), None)
            if not u:
                continue
            nn = n.replace(" ", "")
            L = -2 if "인버스2X" in nn else -1 if "인버스" in nn else 2
            out[t] = (u, L, n)
        print(f"단일종목 레버리지 {len(out)}개 찾음: " + ", ".join(f"{t} {v[2]}" for t, v in sorted(out.items())))
    except Exception as ex:                    # noqa: BLE001
        print(f"단일종목 레버리지 찾기 실패: {ex}")
    return out


def collect_kr(yf, pd, start, end):
    single = discover_single()
    kr_all = dict(KR)
    names = {}
    for t, (u, L, n) in single.items():
        kr_all[t] = (u, L)
        names[t] = n
    syms = sorted(set(kr_all) | {u for u, _ in kr_all.values()})
    ys = [s + ".KS" for s in syms]
    print(f"국내 일봉 받는 중: {len(ys)}개")
    df = yf.download(ys, start=start.isoformat(), end=end.isoformat(), interval="1d", auto_adjust=False, progress=False, group_by="column", threads=True)
    base = closes(df, "069500.KS")
    if base is None or len(base) < 100:
        print("KODEX 200 종가가 부족해서 국내는 건너뛰어요.")
        return None
    dates = [d.date() for d in base.index]

    def aligned(sym):
        s = closes(df, sym + ".KS")
        if s is None:
            return [None] * len(dates)
        m = {d.date(): float(v) for d, v in s.items()}
        return [r6(m.get(d)) for d in dates]

    under = {u: aligned(u) for u in {u for u, _ in kr_all.values()}}
    etf, check = {}, {}
    for e, (u, L) in sorted(kr_all.items()):
        c = aligned(e)
        if sum(1 for v in c if v) < 20:
            print(f"  {e}: 데이터 부족, 건너뜀")
            continue
        etf[e] = {"u": u, "L": L, "c": c}
        if e in names:
            etf[e]["name"] = names[e]
        xs, ys2 = [], []
        uc = under[u]
        for i in range(max(1, len(dates) - 250), len(dates)):
            if c[i] and c[i - 1] and uc[i] and uc[i - 1]:
                xs.append(uc[i] / uc[i - 1] - 1)
                ys2.append(c[i] / c[i - 1] - 1)
        if len(xs) > 30:
            mx, my = sum(xs) / len(xs), sum(ys2) / len(ys2)
            vx = sum((x - mx) ** 2 for x in xs)
            beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys2)) / vx if vx else None
            check[e] = {"beta": r6(beta), "n": len(xs)}
            flag = "" if beta is not None and abs(beta - L) < 0.25 else "  ← 배율 확인 필요"
            print(f"  {e}: 기초 {u} × {L}배, 실제 기울기 {beta:.2f} ({len(xs)}일){flag}")
    ds = [d.isoformat() for d in dates]
    close_ts = [int(pd.Timestamp(f"{d} 15:30", tz="Asia/Seoul").timestamp()) for d in ds]
    print(f"국내: ETF {len(etf)}개, {len(ds)}일, 마지막 날 {ds[-1]}")
    return {"asof": ds[-1], "dates": ds, "closeTs": close_ts, "fx": [1] * len(ds), "etf": etf, "under": under, "splits": {}, "check": check}


def main():
    dry = "--dry" in sys.argv
    import pandas as pd
    import yfinance as yf

    end = datetime.date.today() + datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=DAYS)
    etfs = sorted(LEV)
    stocks = sorted({u for u, _ in LEV.values() if u not in CRYPTO})
    daily_syms = etfs + stocks + ["KRW=X"]

    print(f"일봉 받는 중: {len(daily_syms)}개 ({start} ~ {end})")
    df = yf.download(daily_syms, start=start.isoformat(), end=end.isoformat(), interval="1d",
                     auto_adjust=False, progress=False, group_by="column", threads=True)

    # 미국 거래일 = SPY에 종가가 있는 날
    spy = closes(df, "SPY")
    if spy is None or len(spy) < 100:
        print("SPY 종가를 못 받아서 저장하지 않아요.")
        return 1
    dates = [d.date() for d in spy.index]
    ds = [d.isoformat() for d in dates]

    def aligned(s):
        if s is None:
            return [None] * len(dates)
        m = {d.date(): float(v) for d, v in s.items()}
        return [r6(m.get(d)) for d in dates]

    fx_raw = closes(df, "KRW=X")
    fx = aligned(fx_raw)
    last = None
    for i, v in enumerate(fx):                 # 환율이 빈 날은 앞 날 값으로
        if v is None:
            fx[i] = last
        else:
            last = v

    # 미국 장 마감 시각(뉴욕 16:00)의 유닉스 시각
    ny = "America/New_York"
    close_ts = [int(pd.Timestamp(f"{d} 16:00", tz=ny).timestamp()) for d in ds]

    under = {}
    for u in stocks:
        under[u] = aligned(closes(df, u))

    for u, ysym in CRYPTO.items():
        print(f"코인 1시간 봉: {ysym}")
        h = yf.download(ysym, period=f"{DAYS}d", interval="1h", auto_adjust=False, progress=False)
        s = closes(h, ysym)
        m = {}
        if s is not None:
            idx = s.index.tz_convert(ny) if s.index.tz is not None else s.index.tz_localize("UTC").tz_convert(ny)
            for t, v in zip(idx, s.values):
                if t.hour == 15:               # 15:00~16:00 봉의 종가 = 16:00 무렵 가격
                    m[t.date()] = float(v)
        # 1시간 봉이 빈 날은 일봉 종가(한국 시간 오전 9시 기준)로 메움
        dd = yf.download(ysym, start=start.isoformat(), end=end.isoformat(), interval="1d",
                         auto_adjust=False, progress=False)
        sd = closes(dd, ysym)
        dm = {d.date(): float(v) for d, v in sd.items()} if sd is not None else {}
        under[u] = [r6(m.get(d, dm.get(d))) for d in dates]
        print(f"  {u}: 뉴욕 마감 시각 가격 {sum(1 for d in dates if d in m)}일, 일봉으로 메운 날 {sum(1 for d in dates if d not in m and d in dm)}일")

    etf = {}
    check = {}
    splits = {}
    for e in etfs:
        c = aligned(closes(df, e))
        if sum(1 for v in c if v) < 20:
            print(f"  {e}: 데이터 부족, 건너뜀")
            continue
        u, L = LEV[e]
        etf[e] = {"u": u, "L": L, "c": c}
        # 실제 배율 확인 (최근 250거래일)
        xs, ys = [], []
        uc = under[u]
        for i in range(max(1, len(dates) - 250), len(dates)):
            if c[i] and c[i - 1] and uc[i] and uc[i - 1]:
                xs.append(uc[i] / uc[i - 1] - 1)
                ys.append(c[i] / c[i - 1] - 1)
        if len(xs) > 30:
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            vx = sum((x - mx) ** 2 for x in xs)
            beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx if vx else None
            check[e] = {"beta": r6(beta), "n": len(xs)}
            flag = "" if beta is not None and abs(beta - L) < 0.25 else "  ← 배율 확인 필요"
            print(f"  {e}: 기초 {u} × {L}배, 실제 기울기 {beta:.2f} ({len(xs)}일){flag}")
        try:
            sp = yf.Ticker(e).splits
            if sp is not None and len(sp):
                splits[e] = [[d.date().isoformat(), float(r)] for d, r in sp.items() if d.date() >= start]
        except Exception as ex:                # noqa: BLE001
            print(f"  {e} 분할 기록 실패: {ex}")

    kr = None
    try:
        kr = collect_kr(yf, pd, start, end)
    except Exception as ex:                    # noqa: BLE001
        print(f"국내 레버리지 실패: {ex}")

    data = {
        "kr": kr,
        "asof": ds[-1],
        "source": "Yahoo Finance (yfinance) 일봉 · 코인은 뉴욕 16시 1시간 봉",
        "dates": ds, "closeTs": close_ts, "fx": fx,
        "etf": etf, "under": under, "splits": {k: v for k, v in splits.items() if v}, "check": check,
    }
    txt = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    print(f"ETF {len(etf)}개, 기초자산 {len(under)}개, {len(ds)}일, 크기 {len(txt) // 1024}KB, 마지막 날 {ds[-1]}")
    if dry:
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"저장 완료: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
