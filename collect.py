"""포트핏 ETF 전체 구성종목 자동 수집기.

GitHub Actions가 평일 장 마감 후 이 파일을 실행해 holdings.json을 갱신합니다.
한국거래소(KRX) 정보데이터시스템 일반 회원 로그인이 필요하며,
아이디·비밀번호는 저장소 Secrets(KRX_ID, KRX_PW)에서 환경 변수로 들어옵니다.

직접 실행: KRX_ID=아이디 KRX_PW=비밀번호 python collect.py
"""
import datetime
import json
import os
import sys
import time

# 계산기에서 '전체 구성종목'으로 계산하는 국내 ETF
# (미국 ETF는 거래소가 종목별 비중을 0으로 줘서 수집하지 않고, 계산기의 상위 10개 데이터를 씀)
ETFS = {
    "069500": "KODEX 200",
    "102110": "TIGER 200",
    "229200": "KODEX 코스닥150",
    "396500": "TIGER 반도체TOP10",
    "091160": "KODEX 반도체",
    "305720": "KODEX 2차전지산업",
    "364980": "TIGER 2차전지TOP10",
}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "holdings.json")
MIN_ETFS = 5          # 이보다 적게 성공하면 기존 파일을 지키기 위해 저장하지 않음


def get_holdings(stock, code):
    df = stock.get_etf_portfolio_deposit_file(code)
    if df is None or len(df) == 0:
        raise ValueError("빈 결과")
    w = df["비중"].astype(float)
    if w.sum() < 50:                                   # 비중이 비어 있으면 금액으로 계산
        a = df["금액"].astype(float)
        if a.sum() <= 0:
            raise ValueError("비중과 금액이 모두 비어 있음")
        w = a / a.sum() * 100
    rows = []
    for ticker, weight in w.items():
        if weight > 0:
            name = str(df.loc[ticker, "구성종목명"]).strip() or str(ticker)
            rows.append([str(ticker).strip(), name, round(float(weight), 4)])
    rows.sort(key=lambda r: -r[2])
    return rows


def get_sectors(stock):
    """최근 영업일 기준 국내 종목 업종 분류 {티커: 업종명}. 실패하면 빈 사전."""
    today = datetime.date.today()
    for back in range(0, 8):
        day = (today - datetime.timedelta(days=back)).strftime("%Y%m%d")
        sectors = {}
        try:
            for mkt in ("KOSPI", "KOSDAQ"):
                s = stock.get_market_sector_classifications(day, mkt)
                if s is not None and len(s):
                    sectors.update(s["업종명"].astype(str).to_dict())
        except Exception as e:                          # noqa: BLE001
            print(f"  업종 분류 {day} 실패: {e}")
            continue
        if sectors:
            print(f"업종 분류 {day} 기준 {len(sectors)}개")
            return sectors
    print("업종 분류를 받지 못했어요. 업종 칸은 비워 둡니다.")
    return {}


def main():
    if not os.environ.get("KRX_ID") or not os.environ.get("KRX_PW"):
        print("KRX_ID, KRX_PW 환경 변수가 없어요. 저장소 Settings → Secrets에 등록했는지 확인하세요.")
        return 1

    from pykrx import stock                             # 로그인 정보를 넣은 뒤에 불러오기

    result, failed = {}, {}
    for code, name in ETFS.items():
        try:
            rows = get_holdings(stock, code)
            result[code] = {"name": name, "holdings": rows}
            print(f"OK   {name}: {len(rows)}종목, 비중 합 {sum(r[2] for r in rows):.1f}%")
        except Exception as e:                          # noqa: BLE001
            failed[code] = str(e)
            print(f"실패 {name}: {e}")
        time.sleep(0.5)

    if len(result) < MIN_ETFS:
        print(f"성공 {len(result)}개뿐이라 저장하지 않아요 (기존 holdings.json 유지).")
        return 1

    sectors = get_sectors(stock)
    for v in result.values():
        for r in v["holdings"]:
            r.append(sectors.get(r[0], ""))

    # 실패한 ETF는 기존 파일의 데이터를 그대로 이어 씀
    if failed and os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8")).get("etfs", {})
            for code in failed:
                if code in old:
                    result[code] = old[code]
                    print(f"     {ETFS[code]}: 이전 데이터 유지")
        except Exception:                               # noqa: BLE001
            pass

    data = {
        "asof": datetime.date.today().isoformat(),
        "source": "한국거래소 ETF 납부자산구성내역 (pykrx)",
        "etfs": result,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"저장 완료: {OUT} ({len(result)}개 ETF)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
