#!/usr/bin/env python3
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from portfolio_report import make_report, write_report


# -----------------------------------------------------------------------------
# Built-in fallback holdings
# -----------------------------------------------------------------------------
# Normally this script reads holdings from TICKERS_CSV_URL. This list is used only
# when TICKERS_CSV_URL is missing or cannot be parsed.
TICKERS = [
    {"ticker": "000270", "name": "기아", "quantity": 4, "cost_basis": 681200, "avg_buy_price": 170300},
    {"ticker": "005930", "name": "삼성전자", "quantity": 7, "cost_basis": 2356500, "avg_buy_price": 336643},
    {"ticker": "005940", "name": "NH투자증권", "quantity": 13, "cost_basis": 391950, "avg_buy_price": 30150},
    {"ticker": "035420", "name": "NAVER", "quantity": 3, "cost_basis": 652500, "avg_buy_price": 217500},
    {"ticker": "035720", "name": "카카오", "quantity": 11, "cost_basis": 444550, "avg_buy_price": 40414},
    {"ticker": "086790", "name": "하나금융지주", "quantity": 5, "cost_basis": 571500, "avg_buy_price": 114300},
    {"ticker": "091160", "name": "KODEX 반도체", "quantity": 8, "cost_basis": 1286700, "avg_buy_price": 160838},
    {"ticker": "360200", "name": "ACE 미국S&P500", "quantity": 157, "cost_basis": 4526260, "avg_buy_price": 28830},
    {"ticker": "360750", "name": "TIGER 미국S&P500", "quantity": 373, "cost_basis": 9012610, "avg_buy_price": 24162},
    {"ticker": "379810", "name": "KODEX 미국나스닥100", "quantity": 415, "cost_basis": 10603835, "avg_buy_price": 25551},
    {"ticker": "0023A0", "name": "SOL 미국양자컴퓨팅TOP10", "quantity": 107, "cost_basis": 3101406, "avg_buy_price": 28985},
    {"ticker": "367380", "name": "ACE 미국나스닥100", "quantity": 56, "cost_basis": 1964910, "avg_buy_price": 35088},
    {"ticker": "402970", "name": "ACE 미국배당다우존스", "quantity": 73, "cost_basis": 1157745, "avg_buy_price": 15860},
]


# -----------------------------------------------------------------------------
# Paths / constants
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TOKEN_CACHE_FILE = BASE_DIR / ".kis_token.json"
KST = dt.timezone(dt.timedelta(hours=9), "KST")

DEFAULT_SNAPSHOT_CSV = BASE_DIR / "latest_snapshot.csv"
DEFAULT_REPORT_PATH = BASE_DIR / "latest_report.md"
DEFAULT_SNAPSHOT_DIR = BASE_DIR / "snapshots"
DEFAULT_REPORT_DIR = BASE_DIR / "reports"

SNAPSHOT_FIELDNAMES = [
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


# -----------------------------------------------------------------------------
# Utility
# -----------------------------------------------------------------------------
def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "t", "yes", "y", "on")


def now_kst():
    return dt.datetime.now(KST)


def is_market_open(now=None):
    """Return True during the Korean regular session, Mon-Fri 09:00-15:30 KST."""
    if now is None:
        now = now_kst()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)

    if now.weekday() >= 5:
        return False

    return dt.time(9, 0) <= now.time() <= dt.time(15, 30)


def report_slot(now):
    """Return the scheduled report slot this snapshot is intended to feed."""
    return "1000" if now.hour < 12 else "1400"


def dated_output_paths(now):
    date_key = now.strftime("%Y%m%d")
    slot = report_slot(now)
    snapshot_path = DEFAULT_SNAPSHOT_DIR / f"snapshot_{date_key}_{slot}.csv"
    report_path = DEFAULT_REPORT_DIR / f"report_{date_key}_{slot}.md"
    return snapshot_path, report_path


def load_dotenv(path=None):
    dotenv_path = Path(path) if path else BASE_DIR / ".env"
    if not dotenv_path.exists():
        return
    with dotenv_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def to_number(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).replace(",", "").replace("%", "").strip()
    if text == "":
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer():
        return int(number)
    return number


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def normalize_ticker(value):
    text = str(value or "").strip().replace("'", "")
    if not text:
        return ""
    # Numeric tickers should remain six digits. Alphanumeric ETF tickers such as
    # 0023A0 already have six chars and are left intact.
    if text.isdigit():
        return text.zfill(6)
    return text.upper().zfill(6) if len(text) < 6 else text.upper()


def request_json(method, url, headers=None, body=None, timeout=15):
    data = None
    req_headers = headers or {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers = {**req_headers, "Content-Type": "application/json; charset=utf-8"}

    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            text = res.read().decode("utf-8")
            return json.loads(text)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {url}: {detail}") from e


def request_text(url, timeout=15):
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read().decode("utf-8-sig")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {url}: {detail}") from e


# -----------------------------------------------------------------------------
# Holdings source
# -----------------------------------------------------------------------------
def get_tickers():
    tickers_csv_url = os.environ.get("TICKERS_CSV_URL")
    if not tickers_csv_url:
        print("TICKERS_CSV_URL not set. Falling back to built-in TICKERS.", file=sys.stderr)
        return TICKERS

    try:
        text = request_text(tickers_csv_url)
        rows = csv.DictReader(text.splitlines())
        tickers = []

        for row in rows:
            enabled = str(row.get("enabled", "TRUE")).strip().lower()
            if enabled in ("false", "n", "no", "0", "미사용", "제외"):
                continue

            ticker = normalize_ticker(row.get("ticker", ""))
            name = str(row.get("name", "")).strip()
            quantity = to_number(row.get("quantity", row.get("qty", "")))
            cost_basis = to_number(row.get("cost_basis", row.get("principal", row.get("cost", ""))))
            avg_buy_price = to_number(row.get("avg_buy_price", row.get("avg_price", "")))

            if (
                not is_number(avg_buy_price)
                and is_number(cost_basis)
                and is_number(quantity)
                and quantity != 0
            ):
                avg_buy_price = cost_basis / quantity

            if not ticker or not name:
                continue

            tickers.append({
                "ticker": ticker,
                "name": name,
                "quantity": quantity,
                "cost_basis": cost_basis,
                "avg_buy_price": avg_buy_price,
            })

        if tickers:
            return tickers

        print("TICKERS_CSV_URL did not contain valid rows. Falling back to built-in TICKERS.", file=sys.stderr)
    except Exception as exc:
        print(f"Failed to load TICKERS_CSV_URL. Falling back to built-in TICKERS: {exc}", file=sys.stderr)

    return TICKERS


# -----------------------------------------------------------------------------
# KIS API
# -----------------------------------------------------------------------------
def load_cached_token():
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        with TOKEN_CACHE_FILE.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        expires_at = dt.datetime.fromisoformat(payload["expires_at"])
        now = dt.datetime.now(dt.timezone.utc)
        if expires_at > now + dt.timedelta(minutes=5):
            return payload.get("access_token")
    except Exception:
        return None
    return None


def save_cached_token(token, expires_in):
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(seconds=max(0, int(expires_in) - 60))
    payload = {
        "access_token": token,
        "expires_at": expires_at.isoformat(),
    }
    with TOKEN_CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f)


def get_access_token(base_url, app_key, app_secret):
    cached = load_cached_token()
    if cached:
        return cached

    url = f"{base_url.rstrip('/')}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }
    payload = request_json("POST", url, body=body)
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"access_token not found: {payload}")
    expires_in = int(payload.get("expires_in") or 86400)
    save_cached_token(token, expires_in)
    return token


def get_price(base_url, app_key, app_secret, access_token, item):
    ticker = item["ticker"]
    name = item["name"]
    quantity = item.get("quantity", "")
    cost_basis = item.get("cost_basis", "")
    avg_buy_price = item.get("avg_buy_price", "")

    query = urllib.parse.urlencode({
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
    })
    url = f"{base_url.rstrip('/')}/uapi/domestic-stock/v1/quotations/inquire-price?{query}"
    headers = {
        "authorization": f"Bearer {access_token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHKST01010100",
        "custtype": "P",
    }
    payload = request_json("GET", url, headers=headers)
    output = payload.get("output") or {}

    if payload.get("rt_cd") not in (None, "0"):
        raise RuntimeError(f"{ticker} KIS error: {payload}")

    price = to_number(output.get("stck_prpr"))
    market_value = ""
    if is_number(price) and is_number(quantity):
        market_value = price * quantity

    unrealized_pnl = ""
    unrealized_pnl_pct = ""
    if is_number(market_value) and is_number(cost_basis):
        unrealized_pnl = market_value - cost_basis
        if cost_basis != 0:
            unrealized_pnl_pct = unrealized_pnl / cost_basis * 100

    return {
        "ticker": ticker,
        "name": name,
        "quantity": quantity,
        "cost_basis": cost_basis,
        "avg_buy_price": avg_buy_price,
        "price": price,
        "change": to_number(output.get("prdy_vrss")),
        "change_pct": to_number(output.get("prdy_ctrt")),
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "volume": to_number(output.get("acml_vol")),
        "open": to_number(output.get("stck_oprc")),
        "high": to_number(output.get("stck_hgpr")),
        "low": to_number(output.get("stck_lwpr")),
    }


def get_price_with_retry(base_url, app_key, app_secret, access_token, item, retries=3):
    ticker = item["ticker"]
    name = item["name"]
    for attempt in range(retries):
        try:
            return get_price(base_url, app_key, app_secret, access_token, item)
        except RuntimeError as exc:
            # KIS API rate limit. Wait and retry only for this known case.
            if "EGW00201" not in str(exc) or attempt == retries - 1:
                raise
            wait_seconds = 1.5 * (attempt + 1)
            print(f"{ticker} {name}: rate limited, retrying in {wait_seconds:.1f}s", file=sys.stderr)
            time.sleep(wait_seconds)
    raise RuntimeError(f"{ticker} {name}: failed after retries")


# -----------------------------------------------------------------------------
# Output: CSV / Google Sheets / Telegram
# -----------------------------------------------------------------------------
def save_local_snapshot(captured_at, items, path):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SNAPSHOT_FIELDNAMES)
        writer.writeheader()
        for item in items:
            writer.writerow({
                "captured_at": captured_at,
                "source": "KIS_OPEN_API",
                **item,
            })


def post_to_google(url, token, captured_at, items, report=None):
    body = {
        "token": token,
        "captured_at": captured_at,
        "source": "KIS_OPEN_API",
        "items": items,
    }
    if report:
        body["report"] = report
    return request_json("POST", url, body=body)


def fmt_won(value):
    if not is_number(value):
        return "-"
    return f"{value:,.0f}원"


def fmt_pct(value):
    if not is_number(value):
        return "-"
    return f"{value:+.2f}%"


def build_telegram_text(captured_at, items, report=None):
    total_market_value = sum(item.get("market_value") for item in items if is_number(item.get("market_value")))
    total_cost_basis = sum(item.get("cost_basis") for item in items if is_number(item.get("cost_basis")))
    total_pnl = sum(item.get("unrealized_pnl") for item in items if is_number(item.get("unrealized_pnl")))
    total_pnl_pct = total_pnl / total_cost_basis * 100 if total_cost_basis else ""

    title_time = captured_at.replace("T", " ")[:16] if captured_at else now_kst().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"KIS 포트폴리오 리포트 ({title_time})",
        "",
        f"총 평가금액: {fmt_won(total_market_value)}",
        f"평가손익: {fmt_won(total_pnl)} ({fmt_pct(total_pnl_pct)})",
    ]

    if report and report.get("summary"):
        lines.extend(["", f"한 줄 결론: {report['summary']}."])

    sorted_items = sorted(
        items,
        key=lambda item: abs(item.get("market_value") if is_number(item.get("market_value")) else 0),
        reverse=True,
    )

    lines.extend(["", "[종목별]"])
    for item in sorted_items:
        name = item.get("name", "")
        ticker = item.get("ticker", "")
        price = fmt_won(item.get("price"))
        daily = fmt_pct(item.get("change_pct"))
        pnl = fmt_won(item.get("unrealized_pnl"))
        pnl_pct = fmt_pct(item.get("unrealized_pnl_pct"))
        lines.append(f"- {name}({ticker}) {price} / 당일 {daily} / 손익 {pnl} ({pnl_pct})")

    if report and report.get("action"):
        lines.extend(["", f"행동 메모: {report['action']}"])

    lines.append("")
    lines.append("※ KIS Open API 자동 집계. 매매 판단은 별도 검토.")
    return "\n".join(lines)


def send_telegram_message(bot_token, chat_id, text):
    if not bot_token or not chat_id:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Telegram sendMessage text limit is 4096 chars. Split conservatively.
    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        add_len = len(line) + 1
        if current and current_len + add_len > 3900:
            chunks.append("\n".join(current))
            current = [line]
            current_len = add_len
        else:
            current.append(line)
            current_len += add_len
    if current:
        chunks.append("\n".join(current))

    responses = []
    for chunk in chunks:
        body = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                responses.append(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram HTTP {e.code}: {detail}") from e
        time.sleep(0.5)
    return responses


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    load_dotenv()

    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    base_url = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")

    google_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL")
    snapshot_token = os.environ.get("SNAPSHOT_TOKEN")

    output_csv_path = Path(os.environ.get("SNAPSHOT_CSV_PATH", str(DEFAULT_SNAPSHOT_CSV)))
    report_path = Path(os.environ.get("REPORT_PATH", str(DEFAULT_REPORT_PATH)))
    report_type = os.environ.get("REPORT_TYPE", "intraday")

    force_run = env_bool("FORCE_RUN", False)
    require_google_sync = env_bool("REQUIRE_GOOGLE_SYNC", False)
    generate_report = env_bool("GENERATE_REPORT", False)
    send_telegram = env_bool("SEND_TELEGRAM_REPORT", False)

    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    missing = [
        key for key, value in {
            "KIS_APP_KEY": app_key,
            "KIS_APP_SECRET": app_secret,
        }.items()
        if not value
    ]
    if missing:
        print(f"Missing environment values: {', '.join(missing)}", file=sys.stderr)
        return 2

    current = now_kst()
    print(f"server_now={dt.datetime.now().isoformat(timespec='seconds')}")
    print(f"kst_now={current.isoformat(timespec='seconds')}")
    print(f"is_market_open={is_market_open(current)}")
    print(f"force_run={force_run}")
    print(f"send_telegram={send_telegram}")

    if not force_run and not is_market_open(current):
        print("Market is closed. Snapshot skipped.")
        return 0

    captured_at = current.isoformat(timespec="seconds")
    access_token = get_access_token(base_url, app_key, app_secret)

    tickers = get_tickers()
    print(f"Loaded {len(tickers)} tickers.")

    items = []
    for ticker_item in tickers:
        ticker = ticker_item["ticker"]
        name = ticker_item["name"]
        try:
            item = get_price_with_retry(base_url, app_key, app_secret, access_token, ticker_item)
            items.append(item)
            print(f"{ticker} {name}: {item['price']} ({item['change_pct']}%)")
        except Exception as exc:
            print(f"{ticker} {name}: failed - {exc}", file=sys.stderr)
        # Avoid KIS API rate limit.
        time.sleep(1.1)

    if not items:
        print("No prices collected.", file=sys.stderr)
        return 1

    save_local_snapshot(captured_at, items, output_csv_path)
    print(f"Saved local snapshot: {output_csv_path}")

    dated_snapshot_path, dated_report_path = dated_output_paths(current)
    save_local_snapshot(captured_at, items, dated_snapshot_path)
    print(f"Saved dated snapshot: {dated_snapshot_path}")

    report = None
    if generate_report or send_telegram:
        try:
            report = make_report(captured_at, items, report_type=report_type)
            write_report(report, report_path)
            print(f"Saved portfolio report: {report_path}")
            write_report(report, dated_report_path)
            print(f"Saved dated portfolio report: {dated_report_path}")
        except Exception as exc:
            print(f"Portfolio report generation failed: {exc}", file=sys.stderr)

    if google_url and snapshot_token:
        try:
            result = post_to_google(google_url, snapshot_token, captured_at, items, report=report)
            print("Posted snapshot to Google Sheets:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"Google Sheets post failed: {exc}", file=sys.stderr)
            if require_google_sync:
                return 1
    else:
        print(
            "Google Sheets post skipped. Set GOOGLE_APPS_SCRIPT_URL and SNAPSHOT_TOKEN to enable it.",
            file=sys.stderr,
        )

    if send_telegram:
        try:
            telegram_text = build_telegram_text(captured_at, items, report=report)
            send_telegram_message(telegram_bot_token, telegram_chat_id, telegram_text)
            print("Telegram report sent.")
        except Exception as exc:
            print(f"Telegram report failed: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
