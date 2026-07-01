#!/usr/bin/env python3
import csv
import datetime as dt
import os


KST = dt.timezone(dt.timedelta(hours=9), "KST")


def to_number(value):
    if value is None:
        return ""
    text = str(value).replace(",", "").strip()
    if text == "":
        return ""
    try:
        number = float(text)
    except ValueError:
        return value
    if number.is_integer():
        return int(number)
    return number


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fmt_won(value):
    if not is_number(value):
        return "-"
    return f"{value:,.0f}원"


def fmt_pct(value):
    if not is_number(value):
        return "-"
    return f"{value:+.2f}%"


def fmt_weight(value):
    if not is_number(value):
        return "-"
    return f"{value:.1f}%"


def normalize_item(raw):
    item = dict(raw)
    for key in (
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
    ):
        item[key] = to_number(item.get(key, ""))
    return item


def load_snapshot_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = [normalize_item(row) for row in csv.DictReader(f)]
    if not rows:
        return []
    latest_captured_at = max(row.get("captured_at", "") for row in rows)
    return [row for row in rows if row.get("captured_at", "") == latest_captured_at]


def classify_item(item):
    ticker = str(item.get("ticker", ""))
    name = str(item.get("name", ""))

    if "S&P500" in name:
        return "미국 S&P500"
    if "나스닥100" in name:
        return "미국 나스닥100"
    if "배당다우존스" in name or "배당" in name:
        return "미국 배당"
    if "양자" in name:
        return "테마/양자"
    if "반도체" in name and ticker.startswith(("0", "1", "2", "3", "4")):
        return "국내 반도체 ETF"
    if name in ("하나금융지주", "NH투자증권"):
        return "국내 금융"
    if name in ("NAVER", "네이버", "카카오"):
        return "국내 플랫폼"
    if name in ("기아", "현대차"):
        return "국내 자동차"
    if ticker.startswith(("0", "1", "2", "3", "4")):
        return "국내 개별주/ETF"
    return "기타"


def exposure_tags(item):
    name = str(item.get("name", ""))
    category = classify_item(item)
    tags = []

    if category.startswith("미국") or "미국" in name:
        tags.append("미국")
    else:
        tags.append("국내")

    if "나스닥" in name or "양자" in name or "반도체" in name or name in ("삼성전자", "NAVER", "네이버", "카카오"):
        tags.append("테크/AI")
    if "반도체" in name or name == "삼성전자":
        tags.append("반도체")
    if "양자" in name:
        tags.append("고변동 테마")
    if "S&P500" in name:
        tags.append("광범위 지수")
    if "배당" in name or category == "국내 금융":
        tags.append("방어/배당")

    return tags


def money_sum(items, key):
    return sum(item[key] for item in items if is_number(item.get(key)))


def add_weights(items, total_market_value):
    for item in items:
        market_value = item.get("market_value")
        if is_number(market_value) and total_market_value:
            item["weight"] = market_value / total_market_value * 100
        else:
            item["weight"] = ""
    return items


def duplicate_categories(items):
    groups = {}
    for item in items:
        category = classify_item(item)
        groups.setdefault(category, []).append(item)
    return {category: rows for category, rows in groups.items() if len(rows) >= 2}


def item_daily_contribution(item):
    quantity = item.get("quantity")
    change = item.get("change")
    if is_number(quantity) and is_number(change):
        return quantity * change
    return ""


def item_judgement(item, duplicates):
    name = str(item.get("name", ""))
    category = classify_item(item)
    weight = item.get("weight")
    pnl_pct = item.get("unrealized_pnl_pct")

    if category in duplicates and category in ("미국 S&P500", "미국 나스닥100"):
        return "통합 검토", "같은 지수 상품이 복수로 있어 관리 단순화 여지가 큼"
    if category == "미국 S&P500":
        return "핵심 보유", "장기 적립 포트폴리오의 중심 자산"
    if category == "미국 나스닥100":
        if is_number(weight) and weight >= 25:
            return "추가매수 속도 조절", "이미 단일 축 비중이 커 급등일 추격매수 부담"
        return "보유", "성장 노출은 유지하되 S&P500 대비 변동성 큼"
    if category == "테마/양자":
        return "비중 상한 관리", "수익률과 변동성이 모두 큰 테마형 자산"
    if category == "국내 반도체 ETF":
        return "추가매수 신중", "삼성전자·나스닥100과 테크 사이클이 겹침"
    if category == "미국 배당":
        return "방어 보완", "기술주 쏠림을 완화하는 보조 자산"
    if category == "국내 금융":
        return "보유", "증시·밸류업 국면에서 방어와 순환매 역할"
    if category in ("국내 플랫폼", "국내 자동차"):
        if is_number(pnl_pct) and pnl_pct <= -10:
            return "보유 논리 재점검", "손실 구간이 깊어 추가매수보다 회복 근거 확인 우선"
        return "관망", "추가매수보다 업황·주가 회복 확인 우선"
    if name == "삼성전자":
        return "보유", "반도체 핵심 노출이나 ETF와 방향성이 일부 중복"
    return "관망", "역할과 비중을 추가 점검할 필요"


def build_action_summary(items, exposure_rows, total_market_value):
    tech_weight = exposure_rows.get("테크/AI", 0)
    semiconductor_weight = exposure_rows.get("반도체", 0)
    high_theme_weight = exposure_rows.get("고변동 테마", 0)

    actions = []
    if tech_weight >= 60:
        actions.append("테크/AI 노출이 높아 급등일 추격매수보다 월 적립분 분할 투입이 적절")
    elif tech_weight >= 45:
        actions.append("테크/AI 비중이 낮지 않아 나스닥·반도체 추가매수는 속도 조절")
    else:
        actions.append("현재 비중에서는 핵심 지수 중심 적립을 우선 검토")

    if semiconductor_weight >= 15:
        actions.append("반도체 노출은 삼성전자·반도체 ETF·나스닥을 합쳐 판단")
    if high_theme_weight >= 8:
        actions.append("양자컴퓨팅 등 테마형 자산은 비중 상한을 정해 수익 변동을 관리")

    if total_market_value:
        actions.append("신규 후보 발굴보다 기존 중복 지수 상품 통합 여부를 먼저 점검")

    return actions


def make_report(captured_at, items, report_type="intraday"):
    items = [normalize_item(item) for item in items]
    total_market_value = money_sum(items, "market_value")
    total_cost_basis = money_sum(items, "cost_basis")
    total_pnl = money_sum(items, "unrealized_pnl")
    total_pnl_pct = total_pnl / total_cost_basis * 100 if total_cost_basis else ""
    add_weights(items, total_market_value)

    duplicates = duplicate_categories(items)
    exposure = {}
    for item in items:
        market_value = item.get("market_value")
        if not is_number(market_value) or not total_market_value:
            continue
        for tag in exposure_tags(item):
            exposure[tag] = exposure.get(tag, 0) + market_value / total_market_value * 100

    contribution_rows = []
    for item in items:
        contribution = item_daily_contribution(item)
        if is_number(contribution):
            contribution_rows.append((contribution, item))
    contribution_rows.sort(key=lambda row: row[0], reverse=True)

    top_items = sorted(
        [item for item in items if is_number(item.get("weight"))],
        key=lambda item: item["weight"],
        reverse=True,
    )

    actions = build_action_summary(items, exposure, total_market_value)
    summary = actions[0] if actions else "최신 스냅샷 기준으로 보유 비중과 손익을 점검"
    action_label = "관망·분할매수"
    if "추격매수" in summary or "속도 조절" in summary:
        action_label = "추격매수 자제"

    captured = captured_at or (items[0].get("captured_at") if items else "")
    title_time = captured.replace("T", " ")[:16] if captured else dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# {title_time} 포트폴리오 보고서",
        "",
        "## 한 줄 결론",
        "",
        f"{summary}.",
        "",
        "## 포트폴리오 현황",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| 총 평가금액 | {fmt_won(total_market_value)} |",
        f"| 총 원금 | {fmt_won(total_cost_basis)} |",
        f"| 평가손익 | {fmt_won(total_pnl)} |",
        f"| 평가수익률 | {fmt_pct(total_pnl_pct)} |",
        "",
        "## 오늘 기여도",
        "",
        "| 구분 | 종목 | 기여금액 | 등락률 |",
        "|---|---|---:|---:|",
    ]

    if contribution_rows:
        for label, rows in (("상위", contribution_rows[:3]), ("하위", list(reversed(contribution_rows[-3:])))):
            for contribution, item in rows:
                lines.append(
                    f"| {label} | {item.get('name', '')} | {fmt_won(contribution)} | {fmt_pct(item.get('change_pct'))} |"
                )
    else:
        lines.append("| - | 수량 또는 전일 대비 데이터 부족 | - | - |")

    lines.extend([
        "",
        "## 비중 점검",
        "",
        "| 종목 | 비중 | 평가금액 | 평가손익률 |",
        "|---|---:|---:|---:|",
    ])
    for item in top_items[:8]:
        lines.append(
            f"| {item.get('name', '')} | {fmt_weight(item.get('weight'))} | {fmt_won(item.get('market_value'))} | {fmt_pct(item.get('unrealized_pnl_pct'))} |"
        )

    lines.extend([
        "",
        "| 중복 노출 | 비중 |",
        "|---|---:|",
    ])
    for tag, weight in sorted(exposure.items(), key=lambda row: row[1], reverse=True):
        lines.append(f"| {tag} | {fmt_weight(weight)} |")

    lines.extend([
        "",
        "## 종목별 판단",
        "",
        "| 종목 | 상태 | 판단 | 이유 |",
        "|---|---|---|---|",
    ])
    for item in top_items:
        judgement, reason = item_judgement(item, duplicates)
        state = classify_item(item)
        lines.append(f"| {item.get('name', '')} | {state} | {judgement} | {reason} |")

    lines.extend([
        "",
        "## 오늘의 행동 제안",
        "",
    ])
    for action in actions:
        lines.append(f"- {action}")

    if duplicates:
        duplicate_text = ", ".join(
            f"{category}({', '.join(item.get('name', '') for item in rows)})"
            for category, rows in duplicates.items()
        )
        lines.append(f"- 중복 상품 점검 대상: {duplicate_text}")

    lines.extend([
        "",
        "## 데이터 메모",
        "",
        f"- 보고서 유형: {report_type}",
        "- 가격·수량·원금은 최신 스냅샷 CSV 기준",
        "- 중복 노출 비중은 태그 기준이라 합계가 100%를 넘을 수 있음",
    ])

    markdown = "\n".join(lines).rstrip() + "\n"

    return {
        "captured_at": captured,
        "report_type": report_type,
        "summary": summary,
        "action": action_label,
        "total_market_value": total_market_value,
        "total_cost_basis": total_cost_basis,
        "total_unrealized_pnl": total_pnl,
        "total_unrealized_pnl_pct": total_pnl_pct,
        "markdown": markdown,
    }


def write_report(report, path):
    output_dir = os.path.dirname(os.path.abspath(path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report["markdown"])


def make_report_from_csv(snapshot_csv_path, report_type="intraday"):
    items = load_snapshot_csv(snapshot_csv_path)
    if not items:
        raise RuntimeError(f"No snapshot rows found: {snapshot_csv_path}")
    captured_at = items[0].get("captured_at", "")
    return make_report(captured_at, items, report_type=report_type)


def main():
    snapshot_csv_path = os.environ.get("SNAPSHOT_CSV_PATH", "latest_snapshot.csv")
    report_path = os.environ.get("REPORT_PATH", "latest_report.md")
    report_type = os.environ.get("REPORT_TYPE", "intraday")
    report = make_report_from_csv(snapshot_csv_path, report_type=report_type)
    write_report(report, report_path)
    print(f"Saved report: {report_path}")


if __name__ == "__main__":
    main()
