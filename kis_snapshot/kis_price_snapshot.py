#!/usr/bin/env python3
import datetime as dt
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


TICKERS = [
    {"ticker": "000270", "name": "기아", "quantity": ""},
    {"ticker": "005930", "name": "삼성전자", "quantity": ""},
    {"ticker": "005940", "name": "NH투자증권", "quantity": ""},
    {"ticker": "035420", "name": "NAVER", "quantity": ""},
    {"ticker": "035720", "name": "카카오", "quantity": ""},
    {"ticker": "086790", "name": "하나금융지주", "quantity": ""},
    {"ticker": "091160", "name": "KODEX 반도체", "quantity": ""},
    {"ticker": "360200", "name": "ACE 미국S&P500", "quantity": ""},
    {"ticker": "367380", "name": "ACE 미국나스닥100", "quantity": ""},
    {"ticker": "402970", "name": "ACE 미국배당다우존스", "quantity": ""},
]

TOKEN_CACHE_FILE = ".kis_token.json"
KST = dt.timezone(dt.timedelta(hours=9), "KST")
DEFAULT_SNAPSHOT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_snapshot.csv")


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


def load_dotenv(path=".env"):
    if not os.path.exists(path):
      return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request_json(method, url, headers=None, body=None, timeout=15):
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {**(headers or {}), "Content-Type": "application/json; charset=utf-8"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
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


def get_tickers():
    tickers_csv_url = os.environ.get("TICKERS_CSV_URL")
    if not tickers_csv_url:
        return TICKERS

    try:
        text = request_text(tickers_csv_url)
        rows = csv.DictReader(text.splitlines())
        tickers = []
        for row in rows:
            enabled = str(row.get("enabled", "TRUE")).strip().lower()
            if enabled in ("false", "n", "no", "0", "미사용", "제외"):
                continue
            ticker = str(row.get("ticker", "")).strip().zfill(6)
            name = str(row.get("name", "")).strip()
            quantity = to_number(row.get("quantity", row.get("qty", "")))
            cost_basis = to_number(row.get("cost_basis", row.get("principal", row.get("cost", ""))))
            avg_buy_price = to_number(row.get("avg_buy_price", row.get("avg_price", "")))
            if (
                not isinstance(avg_buy_price, (int, float))
                and isinstance(cost_basis, (int, float))
                and isinstance(quantity, (int, float))
                and quantity != 0
            ):
                avg_buy_price = cost_basis / quantity
            if ticker and name:
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


def get_access_token(base_url, app_key, app_secret, retries=4, base_wait=5):
    cached = load_cached_token()
    if cached:
        return cached

    url = f"{base_url.rstrip('/')}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }

    last_exc = None
    for attempt in range(retries):
        try:
            payload = request_json("POST", url, body=body, timeout=30)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            wait_seconds = base_wait * (attempt + 1)
            print(
                f"Token request timed out/failed (attempt {attempt + 1}/{retries}): {exc}. "
                f"Retrying in {wait_seconds}s",
                file=sys.stderr,
            )
            if attempt < retries - 1:
                time.sleep(wait_seconds)
    else:
        raise RuntimeError(f"Failed to reach KIS token endpoint after {retries} attempts: {last_exc}")

    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"access_token not found: {payload}")
    expires_in = int(payload.get("expires_in") or 86400)
    save_cached_token(token, expires_in)
    return token


def load_cached_token():
    if not os.path.exists(TOKEN_CACHE_FILE):
        return None
    try:
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
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
    expires_at = now + dt.timedelta(seconds=max(0, expires_in - 60))
    payload = {
        "access_token": token,
        "expires_at": expires_at.isoformat(),
    }
    with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)


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
    if isinstance(price, (int, float)) and isinstance(quantity, (int, float)):
        market_value = price * quantity
    unrealized_pnl = ""
    unrealized_pnl_pct = ""
    if isinstance(market_value, (int, float)) and isinstance(cost_basis, (int, float)):
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
            if "EGW00201" not in str(exc) or attempt == retries - 1:
                raise
            wait_seconds = 1.5 * (attempt + 1)
            print(f"{ticker} {name}: rate limited, retrying in {wait_seconds:.1f}s", file=sys.stderr)
            time.sleep(wait_seconds)
    raise RuntimeError(f"{ticker} {name}: failed after retries")


def to_number(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text == "":
        return ""
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def post_to_google(url, token, captured_at, items):
    body = {
        "token": token,
        "captured_at": captured_at,
        "source": "KIS_OPEN_API",
        "items": items,
    }
    return request_json("POST", url, body=body)


def save_local_snapshot(captured_at, items, path="latest_snapshot.csv"):
    fieldnames = [
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
    output_dir = os.path.dirname(os.path.abspath(path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow({
                "captured_at": captured_at,
                "source": "KIS_OPEN_API",
                **item,
            })


def main():
    load_dotenv()
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    base_url = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    google_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL")
    snapshot_token = os.environ.get("SNAPSHOT_TOKEN")
    output_csv_path = os.environ.get("SNAPSHOT_CSV_PATH", DEFAULT_SNAPSHOT_CSV)
    force_run = env_bool("FORCE_RUN", False)
    require_google_sync = env_bool("REQUIRE_GOOGLE_SYNC", False)

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

    if not force_run and not is_market_open(current):
        print("Market is closed. Snapshot skipped.")
        return 0

    captured_at = current.isoformat(timespec="seconds")
    access_token = get_access_token(base_url, app_key, app_secret)
    items = []

    tickers = get_tickers()
    print(f"Loaded {len(tickers)} tickers.")

    for ticker_item in tickers:
        ticker = ticker_item["ticker"]
        name = ticker_item["name"]
        try:
            item = get_price_with_retry(base_url, app_key, app_secret, access_token, ticker_item)
            items.append(item)
            print(f"{ticker} {name}: {item['price']} ({item['change_pct']}%)")
        except Exception as exc:
            print(f"{ticker} {name}: failed - {exc}", file=sys.stderr)
        time.sleep(1.1)

    if not items:
        print("No prices collected.", file=sys.stderr)
        return 1

    save_local_snapshot(captured_at, items, output_csv_path)
    print(f"Saved local snapshot: {output_csv_path}")

    if google_url and snapshot_token:
        try:
            result = post_to_google(google_url, snapshot_token, captured_at, items)
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
