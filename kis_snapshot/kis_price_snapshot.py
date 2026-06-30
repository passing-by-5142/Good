import os
import json
from pathlib import Path
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import requests
import gspread
from google.oauth2.service_account import Credentials


# =========================
# 기본 설정
# =========================

KST = ZoneInfo("Asia/Seoul")

KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
KIS_APP_KEY = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
PORTFOLIO_SHEET_NAME = os.getenv("PORTFOLIO_SHEET_NAME", "portfolio")
SNAPSHOT_SHEET_NAME = os.getenv("SNAPSHOT_SHEET_NAME", "snapshot")

# 장 마감 후 강제 실행하고 싶을 때 FORCE_RUN=true
FORCE_RUN = os.getenv("FORCE_RUN", "false").lower() == "true"

TOKEN_CACHE_PATH = Path(os.getenv("KIS_TOKEN_CACHE_PATH", "/tmp/kis_token.json"))

HEADERS = [
    "captured_at",
    "source",
    "ticker",
    "name",
    "quantity",
    "cost_basis",
    "avg_buy_price",
    "price",
    "change",
    "change_pct",
    "market_value",
    "unrealized_pnl",
    "unrealized_pnl_pct",
    "volume",
    "open",
    "high",
    "low",
]


# =========================
# 시간 / 장 운영 여부
# =========================

def now_kst():
    return datetime.now(KST)


def is_market_open(now=None):
    """현재 시간이 한국 정규장 운영 시간(평일 09:00~15:30 KST)인지 확인"""
    if now is None:
        now = now_kst()
    elif now.tzinfo is None:
        # naive datetime이 들어오면 KST로 간주
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)

    # 토요일=5, 일요일=6
    if now.weekday() >= 5:
        return False

    return dt_time(9, 0) <= now.time() <= dt_time(15, 30)


# =========================
# 숫자 처리
# =========================

def to_float(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).replace(",", "").replace("%", "").strip()

    if value == "":
        return None

    try:
        return float(value)
    except ValueError:
        return None


def to_int(value):
    num = to_float(value)
    if num is None:
        return None
    return int(num)


def normalize_ticker(value):
    value = str(value).strip().replace("'", "")
    if value == "":
        return ""
    return value.zfill(6)


# =========================
# Google Sheets 연결
# =========================

def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if service_account_json:
        info = json.loads(service_account_json)
        credentials = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        # GOOGLE_APPLICATION_CREDENTIALS 환경변수에 json 파일 경로 지정
        credentials = Credentials.from_service_account_file(
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
            scopes=scopes,
        )

    return gspread.authorize(credentials)


def get_or_create_worksheet(spreadsheet, title, rows=1000, cols=30):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def ensure_snapshot_header(ws):
    existing = ws.row_values(1)
    if existing != HEADERS:
        ws.update("A1", [HEADERS], value_input_option="RAW")


def read_portfolio(ws):
    """
    portfolio 시트 필요 컬럼:
    - ticker: 필수
    - name: 선택
    - quantity: 선택
    - avg_buy_price: 선택
    - cost_basis: 선택

    예시:
    ticker | name | quantity | avg_buy_price | cost_basis
    005930 | 삼성전자 | 7 | 336642 | 2356494
    """

    values = ws.get_all_values()

    if not values:
        raise ValueError("portfolio 시트가 비어 있습니다.")

    headers = [h.strip() for h in values[0]]
    rows = values[1:]

    required = ["ticker"]
    for col in required:
        if col not in headers:
            raise ValueError(f"portfolio 시트에 필수 컬럼이 없습니다: {col}")

    result = []

    for row in rows:
        item = {}

        for idx, header in enumerate(headers):
            item[header] = row[idx].strip() if idx < len(row) else ""

        ticker = normalize_ticker(item.get("ticker"))

        if not ticker:
            continue

        quantity = to_float(item.get("quantity"))
        avg_buy_price = to_float(item.get("avg_buy_price"))
        cost_basis = to_float(item.get("cost_basis"))

        if cost_basis is None and quantity is not None and avg_buy_price is not None:
            cost_basis = quantity * avg_buy_price

        result.append({
            "ticker": ticker,
            "name": item.get("name", ""),
            "quantity": quantity,
            "avg_buy_price": avg_buy_price,
            "cost_basis": cost_basis,
        })

    return result


# =========================
# KIS API
# =========================

def validate_env():
    missing = []

    for key, value in {
        "KIS_APP_KEY": KIS_APP_KEY,
        "KIS_APP_SECRET": KIS_APP_SECRET,
        "GOOGLE_SHEET_ID": GOOGLE_SHEET_ID,
    }.items():
        if not value:
            missing.append(key)

    if not os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        missing.append("GOOGLE_SERVICE_ACCOUNT_JSON 또는 GOOGLE_APPLICATION_CREDENTIALS")

    if missing:
        raise EnvironmentError("필수 환경변수가 없습니다: " + ", ".join(missing))


def load_cached_token():
    if not TOKEN_CACHE_PATH.exists():
        return None

    try:
        data = json.loads(TOKEN_CACHE_PATH.read_text())
        expires_at = datetime.fromisoformat(data["expires_at"])

        if datetime.now(KST) < expires_at:
            return data["access_token"]

    except Exception:
        return None

    return None


def save_cached_token(access_token, expires_in):
    # 만료 직전 오류 방지를 위해 10분 먼저 만료 처리
    expires_at = now_kst() + timedelta(seconds=int(expires_in) - 600)

    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_PATH.write_text(json.dumps({
        "access_token": access_token,
        "expires_at": expires_at.isoformat(),
    }))


def get_kis_access_token():
    cached = load_cached_token()
    if cached:
        return cached

    url = f"{KIS_BASE_URL}/oauth2/tokenP"

    payload = {
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
    }

    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()

    data = response.json()

    access_token = data.get("access_token")
    expires_in = data.get("expires_in", 86400)

    if not access_token:
        raise RuntimeError(f"KIS 토큰 발급 실패: {data}")

    save_cached_token(access_token, expires_in)

    return access_token


def get_kis_price(ticker, access_token):
    """
    국내 주식 현재가 조회
    KIS TR ID: FHKST01010100
    """

    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010100",
        "custtype": "P",
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
    }

    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()

    data = response.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"KIS 가격 조회 실패 {ticker}: {data}")

    output = data.get("output", {})

    price = to_float(output.get("stck_prpr"))
    change = to_float(output.get("prdy_vrss"))
    change_pct = to_float(output.get("prdy_ctrt"))
    volume = to_float(output.get("acml_vol"))
    open_price = to_float(output.get("stck_oprc"))
    high = to_float(output.get("stck_hgpr"))
    low = to_float(output.get("stck_lwpr"))

    return {
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "volume": volume,
        "open": open_price,
        "high": high,
        "low": low,
    }


# =========================
# 스냅샷 생성
# =========================

def build_snapshot_row(captured_at, holding, price_data):
    ticker = holding["ticker"]
    name = holding.get("name", "")
    quantity = holding.get("quantity")
    avg_buy_price = holding.get("avg_buy_price")
    cost_basis = holding.get("cost_basis")

    price = price_data.get("price")

    market_value = None
    unrealized_pnl = None
    unrealized_pnl_pct = None

    if quantity is not None and price is not None:
        market_value = quantity * price

    if market_value is not None and cost_basis is not None:
        unrealized_pnl = market_value - cost_basis

        if cost_basis != 0:
            unrealized_pnl_pct = unrealized_pnl / cost_basis * 100

    return [
        captured_at,
        "KIS",
        ticker,
        name,
        quantity if quantity is not None else "",
        round(cost_basis, 2) if cost_basis is not None else "",
        round(avg_buy_price, 2) if avg_buy_price is not None else "",
        price if price is not None else "",
        price_data.get("change", ""),
        price_data.get("change_pct", ""),
        round(market_value, 2) if market_value is not None else "",
        round(unrealized_pnl, 2) if unrealized_pnl is not None else "",
        round(unrealized_pnl_pct, 2) if unrealized_pnl_pct is not None else "",
        price_data.get("volume", ""),
        price_data.get("open", ""),
        price_data.get("high", ""),
        price_data.get("low", ""),
    ]


def run_snapshot():
    validate_env()

    current = now_kst()

    print("server_now:", datetime.now())
    print("kst_now:", current.isoformat(timespec="seconds"))
    print("is_market_open:", is_market_open(current))
    print("force_run:", FORCE_RUN)

    if not FORCE_RUN and not is_market_open(current):
        print("정규장 시간이 아니므로 스냅샷 생성을 건너뜁니다.")
        return

    gc = get_gspread_client()
    spreadsheet = gc.open_by_key(GOOGLE_SHEET_ID)

    portfolio_ws = spreadsheet.worksheet(PORTFOLIO_SHEET_NAME)
    snapshot_ws = get_or_create_worksheet(spreadsheet, SNAPSHOT_SHEET_NAME)

    ensure_snapshot_header(snapshot_ws)

    portfolio = read_portfolio(portfolio_ws)

    if not portfolio:
        raise ValueError("portfolio 시트에서 읽은 종목이 없습니다.")

    access_token = get_kis_access_token()

    captured_at = current.isoformat(timespec="seconds")
    rows_to_append = []

    for holding in portfolio:
        ticker = holding["ticker"]

        try:
            price_data = get_kis_price(ticker, access_token)
            row = build_snapshot_row(captured_at, holding, price_data)
            rows_to_append.append(row)

            print(
                f"{ticker} {holding.get('name', '')} "
                f"price={price_data.get('price')} "
                f"change_pct={price_data.get('change_pct')}"
            )

        except Exception as e:
            print(f"[ERROR] {ticker} 조회 실패: {e}")

    if not rows_to_append:
        raise RuntimeError("추가할 스냅샷 행이 없습니다.")

    snapshot_ws.append_rows(rows_to_append, value_input_option="RAW")

    print(f"스냅샷 저장 완료: {captured_at}, {len(rows_to_append)}개 종목")


if __name__ == "__main__":
    run_snapshot()
