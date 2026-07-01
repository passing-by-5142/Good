[README.md](https://github.com/user-attachments/files/29533342/README.md)
# KIS Price Snapshot

한국투자증권 Open API로 국내주식·국내상장 ETF 현재가를 조회해 구글시트 `snapshot` 탭에 저장하고, 선택적으로 포트폴리오 보고서를 `reports` 탭과 로컬 마크다운 파일에 저장하는 구성입니다.

## 1. 구글시트 Apps Script 설정

1. 구글시트에서 `확장 프로그램 > Apps Script`를 엽니다.
2. `apps_script.gs` 내용을 붙여넣습니다.
3. `CONFIG.TOKEN` 값을 긴 임의 문자열로 바꿉니다.
4. `배포 > 새 배포 > 웹 앱`을 선택합니다.
5. 실행 권한은 본인, 접근 권한은 `링크가 있는 모든 사용자` 또는 `Anyone`으로 둡니다.
6. 배포 URL을 복사합니다.
7. 웹앱 URL을 브라우저에서 열었을 때 `KIS snapshot web app is reachable` 문구가 보이면 접근 권한 설정이 된 것입니다.

## 2. 로컬 환경 설정

```bash
cd kis_snapshot
cp .env.example .env
```

`.env`에 아래 값을 넣습니다.

```text
KIS_APP_KEY=한국투자증권_APP_KEY
KIS_APP_SECRET=한국투자증권_APP_SECRET
GOOGLE_APPS_SCRIPT_URL=구글_웹앱_배포_URL
SNAPSHOT_TOKEN=Apps_Script_CONFIG_TOKEN과_같은_값
TICKERS_CSV_URL=종목목록_tickers_탭_CSV_게시_URL

GENERATE_REPORT=true
REPORT_PATH=latest_report.md
REPORT_TYPE=intraday
```

API 키와 계좌번호는 채팅에 올리지 마세요.

## 3. 종목 목록을 구글시트에서 관리하기

구글시트에 `tickers` 탭을 만들고 아래처럼 입력합니다.

| ticker | name | enabled | quantity | cost_basis | avg_buy_price |
|---|---|---|---:|---:|---:|
| 000270 | 기아 | TRUE | 4 | 681200 | 170300 |
| 005930 | 삼성전자 | TRUE | 7 | 2356500 | 336643 |
| 005940 | NH투자증권 | TRUE | 13 | 391950 | 30150 |
| 035420 | NAVER | TRUE | 3 | 652500 | 217500 |
| 035720 | 카카오 | TRUE | 11 | 444550 | 40414 |
| 086790 | 하나금융지주 | TRUE | 5 | 571500 | 114300 |
| 091160 | KODEX 반도체 | TRUE | 8 | 1286700 | 160838 |
| 360200 | ACE 미국S&P500 | TRUE | 157 | 4526260 | 28830 |
| 360750 | TIGER 미국S&P500 | TRUE | 373 | 9012610 | 24162 |
| 379810 | KODEX 미국나스닥100 | TRUE | 415 | 10603835 | 25551 |
| 0023A0 | SOL 미국양자컴퓨팅TOP10 | TRUE | 107 | 3101406 | 28985 |
| 367380 | ACE 미국나스닥100 | TRUE | 56 | 1964910 | 35088 |
| 402970 | ACE 미국배당다우존스 | TRUE | 73 | 1157745 | 15860 |

`tickers` 탭을 CSV로 게시한 뒤, 해당 URL을 `.env`의 `TICKERS_CSV_URL` 또는 GitHub Actions secret에 넣으면 됩니다.

종목을 빼고 싶으면 행을 삭제하거나 `enabled`를 `FALSE`로 바꾸면 됩니다.
종목을 추가하려면 새 행을 추가합니다.
`cost_basis`는 종목별 총 매입금액입니다. `cost_basis`가 비어 있고 `quantity`, `avg_buy_price`가 있으면 스크립트가 총 매입금액을 자동 계산합니다.

## 4. 실행

```bash
python3 kis_price_snapshot.py
```

정상 실행되면 구글시트 `snapshot` 탭에 아래 컬럼이 쌓입니다.

| column | meaning |
|---|---|
| captured_at | 스냅샷 저장 시각, KST |
| source | KIS_OPEN_API |
| ticker | 종목코드 |
| name | 종목명 |
| price | 현재가 |
| change | 전일 대비 |
| change_pct | 전일 대비 등락률 |
| volume | 누적 거래량 |
| open | 시가 |
| high | 고가 |
| low | 저가 |

`GENERATE_REPORT=true`이면 같은 실행에서 `latest_report.md`도 생성되고, Apps Script가 최신 코드로 배포돼 있으면 구글시트 `reports` 탭에도 아래 컬럼이 쌓입니다.

| column | meaning |
|---|---|
| created_at | 구글시트 저장 시각 |
| captured_at | 가격 스냅샷 기준 시각 |
| report_type | 보고서 유형, 예: `intraday`, `close`, `weekly` |
| summary | 한 줄 결론 |
| action | 행동 판단 |
| total_market_value | 총 평가금액 |
| total_cost_basis | 총 원금 |
| total_unrealized_pnl | 총 평가손익 |
| total_unrealized_pnl_pct | 총 평가수익률 |
| markdown | 전체 보고서 본문 |

보고서는 아래 항목을 자동 계산합니다.

| section | content |
|---|---|
| 한 줄 결론 | 추격매수·분할매수·관망 등 당일 판단 |
| 포트폴리오 현황 | 총 평가금액, 총 원금, 평가손익, 평가수익률 |
| 오늘 기여도 | 수량과 전일 대비를 이용한 종목별 당일 기여도 |
| 비중 점검 | 종목별 비중과 미국·테크/AI·반도체·방어/배당 등 중복 노출 |
| 종목별 판단 | 핵심 보유, 통합 검토, 추가매수 신중, 비중 상한 관리 등 |
| 행동 제안 | 신규매수보다 중복 노출 관리가 우선인지 여부 |

## 5. 자동 실행

윈도우 작업 스케줄러에서 평일 10:00, 14:00에 아래 명령을 실행하도록 설정합니다.

```text
python3 C:\path\to\kis_snapshot\kis_price_snapshot.py
```

작업 시작 위치는 `kis_snapshot` 폴더로 지정해야 `.env`를 읽을 수 있습니다.

GitHub Actions의 `schedule` 호출이 불안정하면 `cron-job.org` 같은 외부 cron에서 GitHub workflow dispatch URL이나 별도 실행 서버를 호출해도 됩니다. 중요한 점은 실행 환경에 아래 값이 들어가 있어야 한다는 것입니다.

```text
GENERATE_REPORT=true
REPORT_TYPE=intraday
```

오전 10시와 오후 2시를 구분하고 싶으면 외부 cron 작업을 둘로 나누고, 각각 `REPORT_TYPE=morning`, `REPORT_TYPE=afternoon`처럼 다르게 넘기면 됩니다.

## 6. 보고서만 다시 생성

이미 `latest_snapshot.csv`가 있는 상태에서 보고서만 다시 만들려면 아래 명령을 실행합니다.

```bash
python3 portfolio_report.py
```

이 경우 KIS API를 다시 호출하지 않고 최신 CSV만 읽어 `latest_report.md`를 다시 만듭니다.
