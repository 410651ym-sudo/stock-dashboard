"""
抓取台股儀表板所需的市場數據，輸出成 data.json。

資料來源（全部免金鑰、免申請）：
- 台積電(2330)/聯發科(2454)收盤價：證交所 TWSE 公開資訊觀測站
- 三大法人買賣超：證交所 TWSE 公開資訊觀測站
- 加權指數(用來算技術指標)、道瓊、那斯達克、美元兌台幣：stooq.com 免費歷史資料
- 10年期美債殖利率、WTI原油：FRED（美國聖路易聯準銀行）

執行方式：python3 fetch_market_data.py
會在同目錄產生 data.json
"""
import requests
import csv
import io
import json
import datetime
import statistics

HEADERS = {"User-Agent": "Mozilla/5.0 (dashboard-bot)"}


def fetch_stooq_series(symbol, days=60):
    """從 stooq 抓每日歷史資料，回傳最近 N 天的 [{Date, Open, High, Low, Close}, ...]"""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = [row for row in reader if row.get("Close")]
    return rows[-days:]


def fetch_fred_latest(series_id):
    """從 FRED 抓某個總經數列的最新一筆有效數字"""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = [row for row in reader if row.get(series_id) not in (None, ".", "")]
    return float(rows[-1][series_id]) if rows else None


def fetch_twse_stock_day_all():
    """證交所：全部上市股票當日收盤資訊。回傳 {股票代號: {欄位名: 值}}"""
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    payload = r.json()
    fields = payload.get("fields", [])
    result = {}
    for row in payload.get("data", []):
        record = dict(zip(fields, row))
        code = record.get("證券代號") or record.get("Code")
        if code:
            result[code.strip()] = record
    return result


def fetch_twse_institutional():
    """證交所：三大法人買賣超彙總表。回傳 {法人名稱: 買賣超金額(億元)}"""
    url = "https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    payload = r.json()
    result = {}
    for row in payload.get("data", []):
        try:
            name = row[0]
            net = float(str(row[3]).replace(",", ""))
            result[name] = round(net / 1e8, 2)  # 轉換成「億元」
        except (ValueError, IndexError):
            continue
    return result


def compute_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return None
    avg_gain = statistics.mean(gains[-period:])
    avg_loss = statistics.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def compute_kd(highs, lows, closes, period=9):
    if len(closes) < period:
        return None, None
    k_values, d_values = [], []
    for i in range(period - 1, len(closes)):
        h = max(highs[i - period + 1: i + 1])
        l = min(lows[i - period + 1: i + 1])
        rsv = 0 if h == l else (closes[i] - l) / (h - l) * 100
        prev_k = k_values[-1] if k_values else 50
        k = prev_k * 2 / 3 + rsv * 1 / 3
        k_values.append(k)
        prev_d = d_values[-1] if d_values else 50
        d_values.append(prev_d * 2 / 3 + k * 1 / 3)
    return round(k_values[-1], 1), round(d_values[-1], 1)


def compute_bias(closes, period=20):
    if len(closes) < period:
        return None
    ma = statistics.mean(closes[-period:])
    return round((closes[-1] - ma) / ma * 100, 2)


def pct_change(series):
    if len(series) < 2:
        return None
    prev, cur = float(series[-2]["Close"]), float(series[-1]["Close"])
    return round((cur - prev) / prev * 100, 2)


def safe(fn, default=None, label=""):
    """任何一個資料來源失敗都不要讓整支程式掛掉，改回傳 None 並印出警告"""
    try:
        return fn()
    except Exception as e:
        print(f"[警告] 抓取「{label}」失敗：{e}")
        return default


def main():
    twii = safe(lambda: fetch_stooq_series("^twii", days=60), [], "加權指數歷史資料")
    closes = [float(r["Close"]) for r in twii] if twii else []
    highs = [float(r["High"]) for r in twii] if twii else []
    lows = [float(r["Low"]) for r in twii] if twii else []

    rsi = compute_rsi(closes) if closes else None
    k, d = compute_kd(highs, lows, closes) if closes else (None, None)
    bias = compute_bias(closes) if closes else None

    dji = safe(lambda: fetch_stooq_series("^dji", days=3), [], "道瓊指數")
    ndq = safe(lambda: fetch_stooq_series("^ndq", days=3), [], "那斯達克指數")
    usdtwd_series = safe(lambda: fetch_stooq_series("usdtwd", days=3), [], "美元兌台幣")

    twse_all = safe(fetch_twse_stock_day_all, {}, "證交所全市場收盤價")
    tsmc = twse_all.get("2330", {})
    mtk = twse_all.get("2454", {})

    inst = safe(fetch_twse_institutional, {}, "三大法人買賣超")

    y10 = safe(lambda: fetch_fred_latest("DGS10"), None, "10年期美債殖利率")
    wti = safe(lambda: fetch_fred_latest("DCOILWTICO"), None, "WTI原油")

    def to_float(record, *keys):
        for key in keys:
            if record.get(key):
                try:
                    return float(str(record[key]).replace(",", ""))
                except ValueError:
                    continue
        return None

    data = {
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "overnight": {
            "dow_pct": pct_change(dji),
            "nasdaq_pct": pct_change(ndq),
            "usdtwd": float(usdtwd_series[-1]["Close"]) if usdtwd_series else None,
        },
        "taiwan_focus": {
            "tsmc_close": to_float(tsmc, "收盤價", "ClosingPrice"),
            "mtk_close": to_float(mtk, "收盤價", "ClosingPrice"),
            "taiex_close": closes[-1] if closes else None,
        },
        "institutional_flow": {
            "foreign": inst.get("外資及陸資(不含外資自營商)") or inst.get("外資及陸資"),
            "trust": inst.get("投信"),
            "dealer": inst.get("自營商(自行買賣)") or inst.get("自營商"),
        },
        "technical": {
            "rsi14": rsi,
            "k": k,
            "d": d,
            "bias20": bias,
        },
        "macro": {
            "us10y_yield": y10,
            "wti_price": wti,
        },
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("data.json 已產生：")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
