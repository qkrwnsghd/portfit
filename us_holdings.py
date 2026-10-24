"""포트핏 미국 ETF 전체 구성종목 수집기.

한국거래소는 국내 상장 미국 ETF의 구성종목을 '종목명 + 계약수(주식 수)'로만 주고 비중은 0으로 줘요.
그래서 미국 주가(야후 파이낸스)를 곱해서 비중을 직접 계산해요.
  비중 = (계약수 × 주가) ÷ 전체 합

- 종목명 → 미국 티커: 위키백과 S&P500·나스닥100 목록과 이름을 맞추고, 못 찾으면 야후 검색
- 업종: 위키백과의 GICS 분류 (반도체는 '반도체'로)
- 찾은 결과는 us_symbols.json에 저장해서 다음부터는 다시 찾지 않음
- 결과는 holdings.json의 해당 ETF 자리에 넣음 (주가를 찾은 종목이 90% 이상일 때만)

GitHub Actions: python us_holdings.py
코랩 시험:     python us_holdings.py --dry   (파일을 바꾸지 않고 결과만 출력)
"""
import json
import os
import re
import sys
import time

US_ETFS = {
    "360750": "TIGER 미국S&P500",
    "379800": "KODEX 미국S&P500",
    "360200": "ACE 미국S&P500",
    "379780": "RISE 미국S&P500",
    "379810": "KODEX 미국나스닥100",
    "367380": "ACE 미국나스닥100",
    "381170": "TIGER 미국테크TOP10",
    "465580": "ACE 미국빅테크TOP7 Plus",
    "381180": "TIGER 미국필라델피아반도체나스닥",
    "390390": "KODEX 미국반도체",
    "458730": "TIGER 미국배당다우존스",
    "446720": "SOL 미국배당다우존스",
    "489250": "KODEX 미국배당다우존스",
}
HERE = os.path.dirname(os.path.abspath(__file__))
HOLDINGS = os.path.join(HERE, "holdings.json")
CACHE = os.path.join(HERE, "us_symbols.json")
MIN_COVER = 0.9

# 포트핏 화면에서 쓰는 한글 이름 (다른 ETF 데이터와 이름을 맞추기 위해)
KO = {
    "NVDA": "엔비디아", "AAPL": "애플", "MSFT": "마이크로소프트", "GOOGL": "알파벳", "GOOG": "알파벳",
    "AMZN": "아마존", "AVGO": "브로드컴", "META": "메타", "LLY": "일라이릴리", "TSLA": "테슬라",
    "AMD": "AMD", "MU": "마이크론", "QCOM": "퀄컴", "TXN": "텍사스인스트루먼트", "ADI": "아날로그디바이스",
    "MRVL": "마벨", "INTC": "인텔", "TSM": "TSMC", "ASML": "ASML", "KLAC": "KLA", "PG": "P&G",
    "KO": "코카콜라", "HD": "홈디포", "UNH": "유나이티드헬스", "AMGN": "암젠", "MRK": "머크",
    "COP": "코노코필립스", "CVX": "셰브론", "NFLX": "넷플릭스", "COST": "코스트코", "JPM": "JP모건",
    "V": "비자", "MA": "마스터카드", "WMT": "월마트", "XOM": "엑슨모빌", "ORCL": "오라클",
    "PLTR": "팔란티어", "AMAT": "어플라이드머티리얼즈", "LRCX": "램리서치", "PEP": "펩시코",
    "ABBV": "애브비", "CSCO": "시스코", "ADBE": "어도비", "CRM": "세일즈포스", "BRK.B": "버크셔해서웨이",
}
GICS_KO = {
    "Information Technology": "IT", "Communication Services": "인터넷·플랫폼", "Consumer Discretionary": "소비재·유통",
    "Consumer Staples": "필수소비재", "Health Care": "헬스케어", "Financials": "금융", "Industrials": "산업재",
    "Energy": "에너지", "Utilities": "유틸리티", "Real Estate": "부동산", "Materials": "소재",
}
YF_SECTOR = {
    "Technology": "IT", "Communication Services": "인터넷·플랫폼", "Consumer Cyclical": "소비재·유통",
    "Consumer Defensive": "필수소비재", "Healthcare": "헬스케어", "Financial Services": "금융", "Industrials": "산업재",
    "Energy": "에너지", "Utilities": "유틸리티", "Real Estate": "부동산", "Basic Materials": "소재",
}
SUFFIX = r"\b(INCORPORATED|INC|CORPORATION|CORP|COMPANY|COMPANIES|COS|CO|LTD|LIMITED|PLC|HOLDINGS?|HLDGS|GROUP|THE|NV|N V|SA|AG|SE|CLASS [A-C]|CL [A-C]|SHS|ADR|ADS|SP|SPON|SPONSORED|NY|REG|ORD|NEW|REIT|LP)\b"
# 위키백과(S&P500·나스닥100)에 없는 해외 상장 종목 등 직접 지정 {정규화 이름: 티커}
ALIAS = {
    "TAIWAN SEMICONDUCTOR": "TSM", "TAIWAN SEMICONDUCTOR MANUFACTURING": "TSM", "ASML": "ASML", "ARM": "ARM",
    "STMICROELECTRONICS": "STM", "FERROVIAL": "FER", "NEBIUS": "NBIS", "LINDE": "LIN", "BERKSHIRE HATHWAY": "BRK.B",
    "BERKSHIRE HATHAWAY": "BRK.B", "BLACKSTONE": "BX", "BOSTON PROPERTIES": "BXP", "CH ROBINSON WORLDWIDE": "CHRW",
    "FNF": "FNF", "SHOPIFY": "SHOP", "PDD": "PDD", "ASTRAZENECA": "AZN", "MERCADOLIBRE": "MELI",
    "COCA COLA EUROPACIFIC PARTNERS": "CCEP", "INTERNATIONAL BUSINESS MACHINES": "IBM", "HUNT JB TRANSPRT SVCS": "JBHT",
    "EXPEDITORS INTERNATIONAL WASH": "EXPD", "INTERNATIONAL FLAVORS AND FRAGRANCES": "IFF", "JM SMUCKER": "SJM",
    "DAVITA HEALTHCARE PARTNERS I": "DVA", "DELL TECHNOLOGY C": "DELL", "DELL TECHNOLOGY": "DELL", "GENERAL AEROSPACE": "GE",
    "ROCKET LAB USA": "RKLB", "MOELIS AND": "MC", "PREFERRED BANK": "PFBC", "PREFERRED BANK ANGELES": "PFBC", "PREFERRED BANK ANGELES CA": "PFBC", "WENDY S": "WEN", "WENDY": "WEN",
    "FEDERAL AGRIC MTG": "AGM", "INDEPENDENT BANK MICH": "IBCP", "ORRSTOWN FINL SERVICES": "ORRF", "GLOBALFOUNDRIES": "GFS", "NXP SEMICONDUCTORS": "NXPI", "UNITED MICROELECTRONICS": "UMC",
}


def norm(name):
    s = str(name).upper().replace("&", " AND ")
    s = re.sub(r"-\s*CL(ASS)?\s*[A-C]\b", " ", s)
    s = re.sub(r"\s*-\s*[A-C]\s*$", " ", s)            # 'CBRE GROUP INC - A', 'COGNIZANT ...-A'
    s = re.sub(r"/[A-Z]{1,4}\b", " ", s)                 # 'CDW CORP/DE', 'BUCKLE INC/THE', '/OH'
    s = re.sub(r"\bTECH\b", "TECHNOLOGY", s)
    s = re.sub(r"\bINTL\b", "INTERNATIONAL", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(SUFFIX, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sector_of(sub, sector):
    if sub and "semiconductor" in sub.lower():
        return "반도체"
    return GICS_KO.get(sector, YF_SECTOR.get(sector, sector or ""))


def wiki_universe():
    """위키백과 S&P500·나스닥100 표 → {정규화 이름: (티커, 업종)}"""
    import pandas as pd
    out = {}
    pages = [
        ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol", "Security"),
        ("https://en.wikipedia.org/wiki/Nasdaq-100", "Ticker", "Company"),
    ]
    for url, tcol, ncol in pages:
        try:
            for t in pd.read_html(url, storage_options={"User-Agent": "Mozilla/5.0 portfit"}):
                if tcol in t.columns and ncol in t.columns:
                    scol = next((c for c in t.columns if "Sector" in str(c)), None)
                    bcol = next((c for c in t.columns if "Sub-Industry" in str(c)), None)
                    for _, r in t.iterrows():
                        sym = str(r[tcol]).strip()
                        key = norm(r[ncol])
                        if key and key not in out:
                            out[key] = (sym, sector_of(str(r[bcol]) if bcol else "", str(r[scol]) if scol else ""))
                    break
        except Exception as e:                          # noqa: BLE001
            print(f"  위키백과 목록 실패 ({url}): {e}")
    print(f"위키백과 목록 {len(out)}개")
    return out


def yahoo_search(name):
    import yfinance as yf
    try:
        q = yf.Search(name, max_results=5).quotes
    except Exception:                                   # noqa: BLE001
        return None
    for it in q or []:
        if it.get("quoteType") == "EQUITY" and it.get("exchange") in ("NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS", "NAS", "NYS"):
            return it.get("symbol"), YF_SECTOR.get(it.get("sector", ""), it.get("sector", "")), it.get("industry", "")
    return None


def main(dry):
    if not os.environ.get("KRX_ID") or not os.environ.get("KRX_PW"):
        print("KRX_ID, KRX_PW 환경 변수가 없어요.")
        return 1
    from pykrx import stock
    import yfinance as yf

    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    raw = {}
    for code, name in US_ETFS.items():
        try:
            df = stock.get_etf_portfolio_deposit_file(code)
            rows = []
            for _, r in df.iterrows():
                nm, sh = str(r["구성종목명"]).strip(), float(r["계약수"] or 0)
                if nm and sh > 0:
                    rows.append((nm, sh))
            raw[code] = rows
            print(f"받음 {name}: {len(rows)}종목")
        except Exception as e:                          # noqa: BLE001
            print(f"실패 {name}: {e}")
        time.sleep(0.5)

    names = {nm for rows in raw.values() for nm, _ in rows}
    todo = [n for n in names if not cache.get(norm(n), {}).get("t")]   # 처음 보거나 지난번에 못 찾은 이름
    if todo:
        wiki = wiki_universe()
        wiki_sector = {t: sec for t, sec in wiki.values()}
        wiki_sector.update({"TSM": "반도체", "ASML": "반도체", "ARM": "반도체", "STM": "반도체", "GFS": "반도체", "NXPI": "반도체", "UMC": "반도체"})
        for n in todo:
            k = norm(n)
            hit = (ALIAS[k], wiki_sector.get(ALIAS[k], "")) if k in ALIAS else wiki.get(k)
            if not hit:                                 # 앞 두 단어만으로 다시 맞춰 보기
                k2 = " ".join(k.split()[:2])
                cand = [v for kk, v in wiki.items() if kk.startswith(k2)] if len(k2) >= 4 else []
                hit = cand[0] if len(cand) == 1 else None
            if hit:
                cache[k] = {"t": hit[0], "s": hit[1]}
            else:
                y = yahoo_search(k.title()) or yahoo_search(n)
                cache[k] = {"t": y[0], "s": sector_of(y[2], y[1])} if y else {"t": None, "s": ""}
                time.sleep(0.3)
        print(f"새로 찾은 이름 {len(todo)}개 (못 찾음 {sum(1 for n in todo if not cache[norm(n)]['t'])}개)")

    syms = sorted({cache[norm(n)]["t"] for n in names if cache.get(norm(n), {}).get("t")})
    yf_syms = [s.replace(".", "-") for s in syms]           # BRK.B → BRK-B
    px = {}
    for i in range(0, len(yf_syms), 200):
        part = yf_syms[i:i + 200]
        try:
            close = yf.download(part, period="10d", progress=False, auto_adjust=False, threads=True)["Close"]
            last = close.ffill().iloc[-1]
            for s in part:
                v = float(last.get(s, float("nan"))) if hasattr(last, "get") else float(last)
                if v == v and v > 0:
                    px[s.replace("-", ".")] = v
        except Exception as e:                          # noqa: BLE001
            print(f"  주가 받기 실패: {e}")
    print(f"주가 {len(px)}/{len(syms)}개")

    data = json.load(open(HOLDINGS, encoding="utf-8")) if os.path.exists(HOLDINGS) else {"etfs": {}}
    done = 0
    for code, rows in raw.items():
        vals, miss = {}, []
        for nm, sh in rows:
            c = cache.get(norm(nm), {})
            t = c.get("t")
            if t and t in px:
                disp = KO.get(t, nm.title())
                v = vals.setdefault(disp, [t, 0.0, c.get("s", "")])
                v[1] += sh * px[t]
            else:
                miss.append(nm)
        cover = 1 - len(miss) / max(1, len(rows))
        total = sum(v[1] for v in vals.values())
        out = sorted(([t, disp, round(val / total * 100, 4), sec] for disp, (t, val, sec) in vals.items()), key=lambda r: -r[2])
        top = ", ".join(f"{r[1]} {r[2]:.1f}%" for r in out[:5])
        print(f"{US_ETFS[code]}: 주가 찾음 {cover:.0%} ({len(rows) - len(miss)}/{len(rows)}) | 상위: {top}")
        if miss[:5]:
            print(f"    못 찾은 예: {miss[:5]}")
        if cover >= MIN_COVER and total > 0:
            data["etfs"][code] = {"name": US_ETFS[code], "holdings": out}
            done += 1
    print(f"반영할 ETF {done}/{len(raw)}개")

    if dry:
        print("(--dry: 파일은 바꾸지 않았어요)")
        return 0
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0, sort_keys=True)
    if done:
        data["us_asof"] = time.strftime("%Y-%m-%d")
        with open(HOLDINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print("holdings.json 저장 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry" in sys.argv))
