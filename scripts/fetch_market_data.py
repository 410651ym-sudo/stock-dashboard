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


def fetch_yahoo_history(symbol, rng="6mo"):
    """從 Yahoo Finance 抓歷史日線資料，回傳 [{Open, High, Low, Close}, ...]（由舊到新排序）
    這個介面同時可以查台股個股(2330.TW)、美股指數(^DJI)、匯率(TWD=X)，格式統一。
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": rng, "interval": "1d"}
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    payload = r.json()
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    rows = []
    for i in range(len(timestamps)):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        if None in (o, h, l, c):
            continue
        rows.append({"Open": o, "High": h, "Low": l, "Close": c})
    return rows


def fetch_fred_latest(series_id):
    """從 FRED 抓某個總經數列的最新一筆有效數字"""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rows = [row for row in reader if row.get(series_id) not in (None, ".", "")]
    return float(rows[-1][series_id]) if rows else None


def fetch_taifex_futures_daily():
    """期交所官方 OpenAPI：期貨每日交易行情，包含日盤與夜盤(盤後)資料"""
    url = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_num(s):
    """把 API 回傳的字串數字（可能含逗號、%、+/-）轉成浮點數"""
    if s is None:
        return None
    s = str(s).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_tx_night_session(rows):
    """從全部期貨資料中，篩選出「臺股期貨(TX)」的盤後(夜盤)那一筆
    如果同時有多個到期月份，取成交量最大的（通常是近月主力合約）
    """
    candidates = [
        row for row in rows
        if row.get("Contract", "").strip() == "TX"
        and "盤後" in (row.get("TradingSession") or "")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: parse_num(row.get("Volume")) or 0, reverse=True)
    return candidates[0]


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


def pct_change(rows):
    if len(rows) < 2:
        return None
    prev, cur = rows[-2]["Close"], rows[-1]["Close"]
    return round((cur - prev) / prev * 100, 2)


def safe(fn, default=None, label=""):
    """任何一個資料來源失敗都不要讓整支程式掛掉，改回傳 None 並印出警告"""
    try:
        return fn()
    except Exception as e:
        print(f"[警告] 抓取「{label}」失敗：{e}")
        return default


def main():
    twii = safe(lambda: fetch_yahoo_history("^TWII", "6mo"), [], "加權指數歷史資料")
    closes = [r["Close"] for r in twii] if twii else []
    highs = [r["High"] for r in twii] if twii else []
    lows = [r["Low"] for r in twii] if twii else []

    rsi = compute_rsi(closes) if closes else None
    k, d = compute_kd(highs, lows, closes) if closes else (None, None)
    bias = compute_bias(closes) if closes else None

    dji = safe(lambda: fetch_yahoo_history("^DJI", "5d"), [], "道瓊指數")
    ndq = safe(lambda: fetch_yahoo_history("^IXIC", "5d"), [], "那斯達克指數")
    usdtwd_series = safe(lambda: fetch_yahoo_history("TWD=X", "5d"), [], "美元兌台幣")
    tsmc_series = safe(lambda: fetch_yahoo_history("2330.TW", "5d"), [], "台積電收盤價")
    mtk_series = safe(lambda: fetch_yahoo_history("2454.TW", "5d"), [], "聯發科收盤價")

    inst = safe(fetch_twse_institutional, {}, "三大法人買賣超")

    night_session = safe(
        lambda: extract_tx_night_session(fetch_taifex_futures_daily()),
        None,
        "台指期夜盤",
    )
    if night_session is None:
        print("[警告] 找不到台指期(TX)盤後資料，可能是收盤時段還沒有夜盤資料，或欄位名稱有異動")

    y10 = safe(lambda: fetch_fred_latest("DGS10"), None, "10年期美債殖利率")
    wti = safe(lambda: fetch_fred_latest("DCOILWTICO"), None, "WTI原油")

    data = {
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "overnight": {
            "dow_pct": pct_change(dji),
            "nasdaq_pct": pct_change(ndq),
            "usdtwd": round(usdtwd_series[-1]["Close"], 2) if usdtwd_series else None,
        },
        "taiwan_focus": {
            "tsmc_close": round(tsmc_series[-1]["Close"], 1) if tsmc_series else None,
            "mtk_close": round(mtk_series[-1]["Close"], 1) if mtk_series else None,
            "taiex_close": round(closes[-1], 0) if closes else None,
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
        "taifex_night": {
            "last": parse_num(night_session.get("Last")) if night_session else None,
            "change_pct": parse_num(night_session.get("%")) if night_session else None,
        },
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("data.json 已產生：")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
