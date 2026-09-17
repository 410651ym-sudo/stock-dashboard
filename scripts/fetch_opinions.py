"""
抓取 15 位意見領袖的最新相關新聞（標題、連結、來源），輸出成 opinions.json。

重要設計原則：
- 這支程式只「蒐集資料」，完全不做內容解讀、不判斷立場、不生成摘要。
- 資料來源是 Google 新聞的公開 RSS 搜尋，不需要任何 API 金鑰。
- 立場標籤（看多/中立/看空）與一句話摘要，請你自己看過新聞標題後手動更新到 index.html，
  這是刻意保留的人工審核步驟，避免程式誤判真實人物的立場。

執行方式：python3 fetch_opinions.py
會在同目錄產生 opinions.json
"""
import requests
import xml.etree.ElementTree as ET
import json
import datetime
import urllib.parse

HEADERS = {"User-Agent": "Mozilla/5.0 (dashboard-bot)"}

# key 必須跟 index.html 裡每張卡片的 data-key 屬性完全對應
PEOPLE = [
    {"key": "luxingzhi", "name": "陸行之", "query": "陸行之 半導體", "lang": "zh-TW"},
    {"key": "gooaye", "name": "股癌（謝孟恭）", "query": "股癌 謝孟恭", "lang": "zh-TW"},
    {"key": "ruanmuhua", "name": "阮慕驊", "query": "阮慕驊", "lang": "zh-TW"},
    {"key": "xiejinhe", "name": "謝金河", "query": "謝金河", "lang": "zh-TW"},
    {"key": "chenweitai", "name": "陳唯泰", "query": "陳唯泰 投信", "lang": "zh-TW"},
    {"key": "gutianle", "name": "股添樂", "query": "股添樂", "lang": "zh-TW"},
    {"key": "mrmarket", "name": "市場先生", "query": "市場先生 投資理財", "lang": "zh-TW"},
    {"key": "youtinghao", "name": "游庭皓", "query": "游庭皓", "lang": "zh-TW"},
    {"key": "phoebus", "name": "菲比斯", "query": "菲比斯 股市", "lang": "zh-TW"},
    {"key": "laowang", "name": "老王（王倚隆）", "query": "王倚隆 股市", "lang": "zh-TW"},
    {"key": "chukuangren", "name": "楚狂人", "query": "楚狂人 期權", "lang": "zh-TW"},
    {"key": "quanzheng", "name": "權證小哥", "query": "權證小哥", "lang": "zh-TW"},
    {"key": "izaax", "name": "愛榭克（izaax）", "query": "愛榭克 izaax", "lang": "zh-TW"},
    {"key": "cathiewood", "name": "Cathie Wood", "query": "Cathie Wood ARK Invest", "lang": "en-US"},
    {"key": "jimcramer", "name": "Jim Cramer", "query": "Jim Cramer stocks", "lang": "en-US"},
    # 這兩位不是意見領袖，是個股本身的新聞，用同一套機制抓取，顯示在台股焦點卡片裡
    {"key": "tsmc_news", "name": "台積電", "query": "台積電 台股", "lang": "zh-TW"},
    {"key": "mtk_news", "name": "聯發科", "query": "聯發科 台股", "lang": "zh-TW"},
]


def fetch_news(query, lang="zh-TW", limit=3):
    """查詢 Google 新聞 RSS，回傳最新的幾則新聞標題、連結、來源、時間"""
    if lang == "zh-TW":
        locale_params = "hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    else:
        locale_params = "hl=en-US&gl=US&ceid=US:en"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&{locale_params}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else ""
        if title and link:
            items.append({
                "title": title,
                "link": link,
                "source": source,
                "published": pub_date,
            })
    return items


def main():
    result = {
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "people": {},
    }
    for p in PEOPLE:
        try:
            news = fetch_news(p["query"], p["lang"])
        except Exception as e:
            print(f"[警告] 抓取「{p['name']}」新聞失敗：{e}")
            news = []
        result["people"][p["key"]] = {
            "name": p["name"],
            "news": news,
        }

    with open("opinions.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("opinions.json 已產生：")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
