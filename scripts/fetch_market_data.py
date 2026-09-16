"""
產生財經行事曆，輸出成 calendar.json。

資料組成（四種不同的可靠方式）：
1. 非農就業／CPI／PPI：美國勞工部(BLS)官方年度行事曆訂閱檔(.ics)，
   官方直接公布全年確切日期，不需要自己用規則猜測。
2. PCE物價指數：美國經濟分析局(BEA)官方行事曆訂閱檔(.ics)，
   對應項目叫「Personal Income and Outlays」。
3. FOMC 會議日期：官方一次公布全年，變動機率極低，用清單維護，
   每年年初更新一次即可（見下方 FOMC_MEETINGS_2026）。
4. 台指期／選擇權結算日：固定規則（每月第三個星期三），直接用程式計算。
5. 台積電／聯發科除息日：證交所公開資料，即時查詢。

美股財報季、CES、WWDC 這類展會目前沒有加進來——這些日期沒有一個公開、
機器可讀、免金鑰的官方時程表（財報季精確日期因公司而異，展會日期由主辦方
每年臨時公布），要嘛需要付費資料源，要嘛需要人工每年查證後手動維護。
"""
import requests
import json
import datetime
import calendar
import re

HEADERS = {"User-Agent": "Mozilla/5.0 (dashboard-bot)"}

BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_ICS_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"

# 只挑這幾個我們關心的 BLS 發布項目（英文名稱需與 ICS 裡的 SUMMARY 完全一致）
BLS_WATCHED = {
    "Employment Situation": ("非農就業報告", "紅"),
    "Consumer Price Index": ("CPI 消費者物價指數", "紅"),
    "Producer Price Index": ("PPI 生產者物價指數", "黃"),
}

# 2026 年 FOMC 決策公布日（美東時間第二天下午2點公布）
# 資料來源：federalreserve.gov/monetarypolicy/fomccalendars.htm
# 維護方式：每年年初查一次官網公布的隔年全年日期，更新這個清單即可
FOMC_MEETINGS_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]

WATCHED_STOCKS = {"2330": "台積電", "2454": "聯發科"}


def safe(fn, default, label):
    try:
        return fn()
    except Exception as e:
        print(f"[警告] 抓取「{label}」失敗：{e}")
        return default


def parse_ics_events(ics_text):
    """簡易 ICS 解析器：抓出每個 VEVENT 的 SUMMARY 跟 DTSTART 日期(YYYY-MM-DD)
    ICS 格式規定：一行太長會被「折行」，下一行以空白開頭代表接續上一行，
    這裡先把折行還原，再逐一解析 VEVENT 區塊。
    """
    unfolded_lines = []
    for line in ics_text.splitlines():
        if line.startswith((" ", "\t")) and unfolded_lines:
            unfolded_lines[-1] += line[1:]
        else:
            unfolded_lines.append(line)

    events = []
    current = {}
    in_event = False
    for line in unfolded_lines:
        if line.startswith("BEGIN:VEVENT"):
            in_event = True
            current = {}
        elif line.startswith("END:VEVENT"):
            if "SUMMARY" in current and "DTSTART" in current:
                events.append(current)
            in_event = False
        elif in_event and line.startswith("SUMMARY"):
            current["SUMMARY"] = line.split(":", 1)[1].strip()
        elif in_event and line.startswith("DTSTART"):
            # 可能是 DTSTART;TZID=US-Eastern:20260916T083000 或 DTSTART:20260826T123000Z
            m = re.search(r"(\d{8})T", line)
            if m:
                d = m.group(1)
                current["DTSTART"] = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return events


def fetch_bls_events():
    r = requests.get(BLS_ICS_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    events = parse_ics_events(r.text)
    result = []
    for ev in events:
        if ev["SUMMARY"] in BLS_WATCHED:
            name, level_word = BLS_WATCHED[ev["SUMMARY"]]
            result.append({"date": ev["DTSTART"], "title": name, "source": "BLS"})
    return result


def fetch_bea_pce_events():
    r = requests.get(BEA_ICS_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    events = parse_ics_events(r.text)
    result = []
    for ev in events:
        if ev["SUMMARY"].startswith("Personal Income and Outlays"):
            result.append({"date": ev["DTSTART"], "title": "PCE 物價指數（聯準會最重視的通膨指標）", "source": "BEA"})
    return result


def third_wednesday(year, month):
    c = calendar.Calendar()
    wednesdays = [
        d for d in c.itermonthdates(year, month)
        if d.month == month and d.weekday() == 2
    ]
    return wednesdays[2]


def upcoming_settlement_dates(count=2):
    today = datetime.date.today()
    dates = []
    y, m = today.year, today.month
    while len(dates) < count:
        d = third_wednesday(y, m)
        if d >= today:
            dates.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return dates


def upcoming_fomc_dates(count=2):
    today = datetime.date.today()
    dates = [datetime.date.fromisoformat(s) for s in FOMC_MEETINGS_2026]
    return sorted(d for d in dates if d >= today)[:count]


def fetch_twse_ex_dividend():
    url = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U?response=json"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    payload = r.json()
    fields = payload.get("fields", [])
    events = []
    for row in payload.get("data", []):
        record = dict(zip(fields, row))
        code = (record.get("股票代號") or record.get("Code") or "").strip()
        if code in WATCHED_STOCKS:
            date_str = (
                record.get("除权除息日期")
                or record.get("除權除息日期")
                or record.get("資料日期")
            )
            if date_str:
                events.append({"code": code, "name": WATCHED_STOCKS[code], "date_raw": date_str})
    return events


def normalize_roc_date(date_str):
    try:
        parts = str(date_str).replace("-", "/").split("/")
        if len(parts) == 3:
            y = int(parts[0])
            if y < 1911:
                y += 1911
            return datetime.date(y, int(parts[1]), int(parts[2])).isoformat()
    except (ValueError, IndexError):
        pass
    return None


def main():
    events = []

    for d in upcoming_fomc_dates():
        events.append({
            "date": d.isoformat(),
            "title": "FOMC 利率決策公布",
            "level": "red",
            "note": "美國央行決策會議，是市場最大變數之一，公布後半小時內波動通常最大。",
        })

    for ev in safe(fetch_bls_events, [], "BLS 非農/CPI/PPI 行事曆"):
        note_map = {
            "非農就業報告": "美國勞動市場溫度計，數字強弱直接影響聯準會升降息判斷。",
            "CPI 消費者物價指數": "評估通膨最核心的指標，市場對這個數字最敏感。",
            "PPI 生產者物價指數": "反映廠商端的成本壓力，通常被視為 CPI 的領先訊號。",
        }
        events.append({
            "date": ev["date"],
            "title": ev["title"],
            "level": "red" if "CPI" in ev["title"] or "非農" in ev["title"] else "yellow",
            "note": note_map.get(ev["title"], ""),
        })

    for ev in safe(fetch_bea_pce_events, [], "BEA PCE 行事曆"):
        events.append({
            "date": ev["date"],
            "title": ev["title"],
            "level": "yellow",
            "note": "聯準會制定利率政策時最看重的通膨指標，比 CPI 更貼近央行實際決策依據。",
        })

    for d in upcoming_settlement_dates():
        events.append({
            "date": d.isoformat(),
            "title": "台指期／選擇權結算日",
            "level": "yellow",
            "note": "每月第三個星期三為結算日，前一天到當天盤中容易出現不尋常的拉高或壓低。",
        })

    ex_div_raw = safe(fetch_twse_ex_dividend, [], "除權除息預告")
    for e in ex_div_raw:
        iso_date = normalize_roc_date(e["date_raw"])
        if iso_date:
            events.append({
                "date": iso_date,
                "title": f"{e['name']}（{e['code']}）除息交易日",
                "level": "yellow",
                "note": "除息當天股價會先扣掉配發的股息，看起來變低是正常的，要看之後能不能填息。",
            })
        else:
            print(f"[警告] 「{e['name']}」除息日期格式無法解析：{e['date_raw']}")

    today_str = datetime.date.today().isoformat()
    events = [e for e in events if e["date"] >= today_str]
    # 去重（同一天同標題只保留一筆）
    seen = set()
    deduped = []
    for e in events:
        key = (e["date"], e["title"])
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    deduped.sort(key=lambda x: x["date"])

    result = {
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "events": deduped[:8],
    }

    with open("calendar.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
