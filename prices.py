"""포트핏 국내 종목·ETF 종가 자동 수집기.

GitHub Actions가 평일 장 마감 후 실행해 prices.json을 만듭니다.
포트핏 '평가손익'에서 아직 들고 있는 국내 종목의 현재가를 자동으로 채울 때 써요.
형식: {"asof": "2026-10-23", "prices": {"삼성전자": 90000, "KODEX 200": 41000, ...}}

직접 실행: KRX_ID=아이디 KRX_PW=비밀번호 python prices.py
"""
import datetime
import json
import os
import sys

KST = datetime.timezone(datetime.timedelta(hours=9))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.json")
MIN_COUNT = 1000      # 이보다 적게 받으면 이상한 날이라 저장하지 않음


def close_col(df):
    for c in ("종가", "Close", "close"):
        if c in df.columns:
            return c
    raise ValueError(f"종가 칸을 찾지 못함: {list(df.columns)}")


def collect(stock, day):
    prices = {}
    # 주식 (코스피·코스닥·코넥스)
    df = stock.get_market_ohlcv_by_ticker(day, market="ALL")
    if df is not None and len(df):
        col = close_col(df)
        for ticker, close in df[col].items():
            close = float(close)
            if close > 0:
                name = stock.get_market_ticker_name(ticker)
                if isinstance(name, str) and name:
                    prices[name.strip()] = int(close)
    # ETF
    try:
        ef = stock.get_etf_ohlcv_by_ticker(day)
        if ef is not None and len(ef):
            col = close_col(ef)
            for ticker, close in ef[col].items():
                close = float(close)
                if close > 0:
                    name = stock.get_etf_ticker_name(ticker)
                    if isinstance(name, str) and name:
                        prices[name.strip()] = int(close)
    except Exception as e:                              # noqa: BLE001
        print(f"  ETF 종가 실패: {e}")
    return prices


def main():
    if not os.environ.get("KRX_ID") or not os.environ.get("KRX_PW"):
        print("KRX_ID, KRX_PW 환경 변수가 없어요.")
        return 1
    from pykrx import stock

    today = datetime.datetime.now(KST).date()
    for back in range(0, 8):                            # 휴장일이면 하루씩 앞으로
        d = today - datetime.timedelta(days=back)
        day = d.strftime("%Y%m%d")
        try:
            prices = collect(stock, day)
        except Exception as e:                          # noqa: BLE001
            print(f"{day} 실패: {e}")
            continue
        print(f"{day}: {len(prices)}개 종목")
        if len(prices) >= MIN_COUNT:
            data = {"asof": d.isoformat(), "source": "한국거래소 종가 (pykrx)", "prices": prices}
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            print(f"저장 완료: {OUT}")
            return 0
    print("종가를 받지 못해 저장하지 않았어요 (기존 prices.json 유지).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
