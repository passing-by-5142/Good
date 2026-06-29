#!/usr/bin/env python3
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


TICKERS = [
    ("000270", "기아"),
    ("005930", "삼성전자"),
    ("005940", "NH투자증권"),
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("086790", "하나금융지주"),
    ("091160", "KODEX 반도체"),
    ("360200", "ACE 미국S&P500"),
    ("367380", "ACE 미국나스닥100"),
    ("402970", "ACE 미국배당다우존스"),
]

TOKEN_CACHE_FILE = ".kis_token.json"


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


def get_price(base_url, app_key, app_secret, access_token, ticker, name):
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
    return {
        "ticker": ticker,
        "name": name,
        "price": to_number(output.get("stck_prpr")),
        "change": to_number(output.get("prdy_vrss")),
        "change_pct": to_number(output.get("prdy_ctrt")),
        "volume": to_number(output.get("acml_vol")),
        "open": to_number(output.get("stck_oprc")),
        "high": to_number(output.get("stck_hgpr")),
        "low": to_number(output.get("stck_lwpr")),
    }


def get_price_with_retry(base_url, app_key, app_secret, access_token, ticker, name, retries=3):
    for attempt in range(retries):
        try:
            return get_price(base_url, app_key, app_secret, access_token, ticker, name)
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


def main():
    load_dotenv()
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    base_url = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    google_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL")
    snapshot_token = os.environ.get("SNAPSHOT_TOKEN")

    missing = [
        key for key, value in {
            "KIS_APP_KEY": app_key,
            "KIS_APP_SECRET": app_secret,
            "GOOGLE_APPS_SCRIPT_URL": google_url,
            "SNAPSHOT_TOKEN": snapshot_token,
        }.items()
        if not value
    ]
    if missing:
        print(f"Missing environment values: {', '.join(missing)}", file=sys.stderr)
        return 2

    captured_at = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")
    access_token = get_access_token(base_url, app_key, app_secret)
    items = []

    for ticker, name in TICKERS:
        try:
            item = get_price_with_retry(base_url, app_key, app_secret, access_token, ticker, name)
            items.append(item)
            print(f"{ticker} {name}: {item['price']} ({item['change_pct']}%)")
        except Exception as exc:
            print(f"{ticker} {name}: failed - {exc}", file=sys.stderr)
        time.sleep(1.1)

    if not items:
        print("No prices collected.", file=sys.stderr)
        return 1

    result = post_to_google(google_url, snapshot_token, captured_at, items)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
