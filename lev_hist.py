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
OUT_OLD = os.path.join(HERE, "lev_old.json")   # 2년보다 오래전에 산 것용 (앱이 필요할 때만 받아요)
OLD_START = datetime.date(2015, 1, 1)
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


def closes(df, sym, col="Close"):
    """yf.download 결과에서 한 종목의 종가 열을 꺼냄. col="Adj Close"면 배당·분배금까지 반영한 종가."""
    try:
        s = df[col][sym] if (col, sym) in df.columns else df[col]
    except KeyError:
        return closes(df, sym) if col != "Close" else None
    if col != "Close" and getattr(s, "ndim", 1) != 1:
        return closes(df, sym)
    s = s.dropna()
    return s if len(s) else (closes(df, sym) if col != "Close" else None)


def dists(df, sym, dates):
    """분배금(배당) 기록: [[날짜, 1주당 금액], ...]. 야후 수정종가(Adj Close)와 종가의 비율이 바뀐 날에서 거꾸로 계산.
    (야후 방식: 분배락일 j의 하루 총수익 = C[j] / (C[j-1] − D) 이므로 D = C[j-1] − C[j] × A[j-1] / A[j])"""
    c, a = closes(df, sym), closes(df, sym, "Adj Close")
    if c is None or a is None:
        return []
    cm = {d.date(): float(v) for d, v in c.items()}
    am = {d.date(): float(v) for d, v in a.items()}
    out, prev = [], None
    for d in dates:
        if d in cm and d in am and cm[d] > 0 and am[d] > 0:
            if prev:
                c0, a0 = prev
                D = c0 - cm[d] * a0 / am[d]
                if D > c0 * 2e-4:                # 0.02% 미만은 반올림 잡음
                    out.append([d.isoformat(), r6(D)])
            prev = (cm[d], am[d])
    return out


SINGLE_U = {"삼성전자": "005930", "하이닉스": "000660"}
# 2026-05-27 상장한 단일종목 레버리지·인버스 (새 종목코드는 영문이 섞여 있어요). 데이터로 실제 배율을 확인해서 맞는 것만 씀
SINGLE = {
    "0193W0": ("005930", 2, "KODEX 삼성전자단일종목레버리지"), "0195R0": ("005930", 2, "TIGER 삼성전자단일종목레버리지"),
    "0194M0": ("005930", 2, "ACE 삼성전자단일종목레버리지"), "0192M0": ("005930", 2, "RISE 삼성전자단일종목레버리지"),
    "0193K0": ("005930", 2, "PLUS 삼성전자단일종목레버리지"), "0194N0": ("005930", 2, "KIWOOM 삼성전자선물단일종목레버리지"),
    "0198B0": ("005930", 2, "1Q 삼성전자선물단일종목레버리지"), "0193L0": ("005930", -2, "PLUS 삼성전자선물단일종목인버스2X"),
    "0193T0": ("000660", 2, "KODEX SK하이닉스단일종목레버리지"), "0195S0": ("000660", 2, "TIGER SK하이닉스단일종목레버리지"),
    "0194T0": ("000660", 2, "ACE SK하이닉스단일종목레버리지"), "0192L0": ("000660", 2, "RISE SK하이닉스단일종목레버리지"),
    "0197W0": ("000660", 2, "SOL SK하이닉스단일종목레버리지"), "0194R0": ("000660", 2, "KIWOOM SK하이닉스선물단일종목레버리지"),
    "0198D0": ("000660", 2, "1Q SK하이닉스선물단일종목레버리지"), "0197X0": ("000660", -2, "SOL SK하이닉스선물단일종목인버스2X"),
}


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


def drop_open_day(dates, tz, hh, mm):
    """장이 아직 안 끝났을 때 받은 오늘 일봉(장중 가격)은 종가가 아니라서 뺌."""
    from zoneinfo import ZoneInfo
    now = datetime.datetime.now(ZoneInfo(tz))
    if dates and dates[-1] == now.date() and (now.hour, now.minute) < (hh, mm):
        print(f"  {tz} 오늘({now.date()})은 장 마감 전이라 빼요.")
        return dates[:-1]
    return dates


def collect_kr(yf, pd, start, end):
    found = discover_single()
    single = dict(SINGLE)
    single.update(found)                       # 거래소 목록에서 찾은 이름이 있으면 그걸 씀
    kr_all = dict(KR)
    names = {}
    debug = {"discovered": sorted(found), "pykrx": [], "missing": [], "badBeta": []}
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
    dates = drop_open_day(dates, "Asia/Seoul", 15, 40)   # 장중에 돌면 오늘 '종가'는 아직 확정 전

    def pykrx_closes(sym):
        try:
            from pykrx import stock
            o = stock.get_etf_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), sym)
            if o is None or not len(o):
                o = stock.get_market_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), sym)
            if o is not None and len(o):
                col = "종가" if "종가" in o.columns else "Close"
                debug["pykrx"].append(sym)
                return {d.date(): float(v) for d, v in o[col].items() if float(v) > 0}
        except Exception as ex:                # noqa: BLE001
            print(f"  {sym} pykrx 실패: {ex}")
        return {}

    def aligned(sym, col="Close"):
        s = closes(df, sym + ".KS", col)
        m = {d.date(): float(v) for d, v in s.items()} if s is not None else {}
        if len(m) < 20:                        # 야후에 없으면 거래소(pykrx)에서
            m = pykrx_closes(sym) or m
        return [r6(m.get(d)) for d in dates]

    under = {u: aligned(u) for u in {u for u, _ in kr_all.values()}}
    udv = {u: v for u in under if (v := dists(df, u + ".KS", dates))}   # 기초자산 배당 (앱이 총수익으로 바꿔 비교)
    etf, check = {}, {}
    for e, (u, L) in sorted(kr_all.items()):
        c = aligned(e)
        if sum(1 for v in c if v) < 20:
            print(f"  {e}: 데이터 부족, 건너뜀")
            debug["missing"].append(e)
            continue
        etf[e] = {"u": u, "L": L, "c": c}
        dv = dists(df, e + ".KS", dates)
        if dv:
            etf[e]["dv"] = dv
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
            if e in names and flag:            # 종목코드가 틀렸을 수 있으니 빼 둠
                debug["badBeta"].append([e, r6(beta)])
                del etf[e]
    ds = [d.isoformat() for d in dates]
    close_ts = [int(pd.Timestamp(f"{d} 15:30", tz="Asia/Seoul").timestamp()) for d in ds]
    print(f"국내: ETF {len(etf)}개, {len(ds)}일, 마지막 날 {ds[-1]}")
    return {"asof": ds[-1], "dates": ds, "closeTs": close_ts, "fx": [1] * len(ds), "etf": etf, "under": under, "udv": udv, "splits": {}, "check": check, "debug": debug}


def trim(c):
    """앞쪽 빈 값(상장 전)을 잘라서 크기 줄임: {"o": 시작 위치, "c": 값들}"""
    k = next((i for i, v in enumerate(c) if v), len(c))
    return {"o": k, "c": c[k:]}


def build_old(yf, pd, end_old):
    """OLD_START ~ end_old(최근 기록 첫날 전) 일봉. 코인은 일봉 종가로 대신 (오래된 매수는 이 정도면 충분)."""
    etfs = sorted(LEV)
    stocks = sorted({u for u, _ in LEV.values() if u not in CRYPTO})
    syms = etfs + stocks + list(CRYPTO.values()) + ["KRW=X"]
    print(f"오래된 일봉 받는 중: {OLD_START} ~ {end_old}")
    df = yf.download(syms, start=OLD_START.isoformat(), end=end_old.isoformat(), interval="1d",
                     auto_adjust=False, progress=False, group_by="column", threads=True)
    spy = closes(df, "SPY")
    if spy is None or len(spy) < 100:
        return None
    dates = [d.date() for d in spy.index if d.date() < end_old]

    def aligned(s):
        m = {d.date(): float(v) for d, v in s.items()} if s is not None else {}
        return [r6(m.get(d)) for d in dates]
    fx = aligned(closes(df, "KRW=X"))
    last = None
    for i, v in enumerate(fx):
        if v is None:
            fx[i] = last
        else:
            last = v
    first = next((v for v in fx if v), None)
    fx = [v if v else first for v in fx]
    under, udv = {}, {}
    for u in stocks:
        under[u] = trim(aligned(closes(df, u)))
        dv = dists(df, u, dates)
        if dv:
            udv[u] = dv
    for u, ysym in CRYPTO.items():
        under[u] = trim(aligned(closes(df, ysym)))
    etf = {}
    for e in etfs:
        c = aligned(closes(df, e))
        if sum(1 for v in c if v) < 5:
            continue
        etf[e] = trim(c)
        dv = dists(df, e, dates)
        if dv:
            etf[e]["dv"] = dv
    ds = [d.isoformat() for d in dates]
    out = {"dates": ds, "closeTs": [int(pd.Timestamp(f"{d} 16:00", tz="America/New_York").timestamp()) for d in ds],
           "fx": fx, "etf": etf, "under": under, "udv": udv}
    print(f"  미국 오래된 기록: {len(ds)}일, ETF {len(etf)}개")
    return out


def build_old_kr(yf, pd, end_old):
    syms = sorted(set(KR) | {u for u, _ in KR.values()})
    df = yf.download([s + ".KS" for s in syms], start=OLD_START.isoformat(), end=end_old.isoformat(), interval="1d",
                     auto_adjust=False, progress=False, group_by="column", threads=True)
    base = closes(df, "069500.KS")
    if base is None or len(base) < 100:
        return None
    dates = [d.date() for d in base.index if d.date() < end_old]

    def aligned(s):
        m = {d.date(): float(v) for d, v in s.items()} if s is not None else {}
        return [r6(m.get(d)) for d in dates]
    under, udv, etf = {}, {}, {}
    for u in {u for u, _ in KR.values()}:
        under[u] = trim(aligned(closes(df, u + ".KS")))
        dv = dists(df, u + ".KS", dates)
        if dv:
            udv[u] = dv
    for e in KR:
        c = aligned(closes(df, e + ".KS"))
        if sum(1 for v in c if v) >= 5:
            etf[e] = trim(c)
            dv = dists(df, e + ".KS", dates)
            if dv:
                etf[e]["dv"] = dv
    ds = [d.isoformat() for d in dates]
    print(f"  국내 오래된 기록: {len(ds)}일, ETF {len(etf)}개")
    return {"dates": ds, "closeTs": [int(pd.Timestamp(f"{d} 15:30", tz="Asia/Seoul").timestamp()) for d in ds],
            "fx": [1] * len(ds), "etf": etf, "under": under, "udv": udv}


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
    dates = drop_open_day([d.date() for d in spy.index], "America/New_York", 16, 15)
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

    under, udv = {}, {}
    for u in stocks:
        under[u] = aligned(closes(df, u))
        dv = dists(df, u, dates)                 # 배당 기록 (레버리지 ETF는 배당까지 포함한 총수익을 따라가서, 앱이 총수익으로 비교)
        if dv:
            udv[u] = dv

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
        dv = dists(df, e, dates)
        if dv:
            etf[e]["dv"] = dv
            print(f"  {e}: 분배금 {len(dv)}번 " + ", ".join(f"{d} ${v}" for d, v in dv[-3:]))
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
                splits[e] = [[d.date().isoformat(), float(r)] for d, r in sp.items() if d.date() >= OLD_START]
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
        "etf": etf, "under": under, "udv": udv, "splits": {k: v for k, v in splits.items() if v}, "check": check,
    }
    txt = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    print(f"ETF {len(etf)}개, 기초자산 {len(under)}개, {len(ds)}일, 크기 {len(txt) // 1024}KB, 마지막 날 {ds[-1]}")
    if dry:
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"저장 완료: {OUT}")
    # 오래된 기록은 거의 안 바뀌어서, 최근 기록 첫날과 이어지지 않게 될 때만(약 한 달에 한 번) 다시 만듦 → 저장소가 매일 커지지 않게
    try:
        prev = json.load(open(OUT_OLD, encoding="utf-8")) if os.path.exists(OUT_OLD) else None
        need = not prev or prev["dates"][-1] < ds[0] or (kr and (not prev.get("kr") or prev["kr"]["dates"][-1] < kr["dates"][0]))
    except Exception:                          # noqa: BLE001
        need = True
    if not need:
        print("오래된 기록은 아직 이어져 있어서 그대로 둬요.")
        return 0
    end_old = datetime.date.today() - datetime.timedelta(days=690)   # 최근 기록과 한 달쯤 겹치게 (앱이 겹친 날은 버림)
    try:
        old = build_old(yf, pd, end_old)
        if old:
            try:
                old["kr"] = build_old_kr(yf, pd, end_old) if kr else None
            except Exception as ex:            # noqa: BLE001
                print(f"국내 오래된 기록 실패: {ex}")
                old["kr"] = None
            ot = json.dumps(old, ensure_ascii=False, separators=(",", ":"))
            with open(OUT_OLD, "w", encoding="utf-8") as f:
                f.write(ot)
            print(f"오래된 기록 저장: {len(ot) // 1024}KB")
    except Exception as ex:                    # noqa: BLE001
        print(f"오래된 기록 실패 (최근 기록은 저장됨): {ex}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
