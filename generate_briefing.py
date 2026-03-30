import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests
from bs4 import BeautifulSoup

TODAY = datetime.now().strftime("%Y-%m-%d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (MorningBriefingBot/21.3; +https://github.com/)",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

STOCKS = [
    {"name": "台積電", "code": "2330"},
    {"name": "聯發科", "code": "2454"},
    {"name": "廣達", "code": "2382"},
]

NEWS_TOPICS = [
    ("ai", "AI 台灣 OR 人工智慧 台灣"),
    ("youtube", "YouTube 台灣 OR Google 台灣"),
    ("etf", "ETF 台灣 OR 美股ETF 台灣"),
]

WEATHER_POINTS = [
    {"city": "桃園", "lat": 24.9936, "lon": 121.3009},
    {"city": "宜蘭五結", "lat": 24.6840, "lon": 121.7990},
]


def fetch_json(url: str, timeout: int = 20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_text(url: str, timeout: int = 20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def clamp(n, low, high):
    return max(low, min(high, n))


def first_non_empty(d: dict, keys, default=""):
    for k in keys:
        if k in d:
            v = str(d.get(k, "")).strip()
            if v and v != "None" and v != "--":
                return v
    return default


def to_float(v, default=0.0):
    try:
        s = str(v).replace(",", "").replace("%", "").strip()
        if s in {"", "--", "X", "除權息"}:
            return default
        return float(s)
    except Exception:
        return default


# =========================
# 🌤 天氣
# =========================
def get_weather(lat: float, lon: float) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,precipitation_probability"
        "&timezone=Asia%2FTaipei"
    )
    data = fetch_json(url, timeout=20)
    current = data.get("current", {})
    temp = current.get("temperature_2m")
    rain = current.get("precipitation_probability")

    return {
        "temp": f"{temp:.1f}°C" if isinstance(temp, (int, float)) else "--°C",
        "rain": f"{int(round(rain))}%" if isinstance(rain, (int, float)) else "--%",
        "source": "Open-Meteo",
    }


def get_weather_list():
    result = []
    for item in WEATHER_POINTS:
        try:
            w = get_weather(item["lat"], item["lon"])
            result.append({
                "city": item["city"],
                "temp": w["temp"],
                "rain": w["rain"],
                "desc": f"降雨 {w['rain']}",
                "source": w["source"],
            })
        except Exception as e:
            result.append({
                "city": item["city"],
                "temp": "--°C",
                "rain": "--%",
                "desc": f"資料取得中 ({type(e).__name__})",
                "source": "Open-Meteo",
            })
    return result


# =========================
# 📰 新聞：更乾淨去重
# =========================
def normalize_news_title(title: str) -> str:
    title = re.sub(r"\s*-\s*[^-]+$", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    title = title.replace("｜", "|")
    title = title.replace("（", "(").replace("）", ")")
    return title


def news_fingerprint(title: str) -> str:
    t = normalize_news_title(title).lower()
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    return t


def get_news(keyword: str, limit: int = 3):
    q = quote(keyword)
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)

    items = []
    seen_fp = set()

    for entry in feed.entries:
        title = normalize_news_title(getattr(entry, "title", "").strip())
        if not title:
            continue

        fp = news_fingerprint(title)
        if fp in seen_fp:
            continue

        seen_fp.add(fp)
        items.append({
            "title": title,
            "link": getattr(entry, "link", ""),
        })
        if len(items) >= limit:
            break

    return items


def get_all_news():
    result = {}
    for cat, keyword in NEWS_TOPICS:
        try:
            result[cat] = get_news(keyword, limit=3)
        except Exception:
            result[cat] = []
    return result


# =========================
# 📈 股票：改接 TWSE 官方 OpenAPI
# =========================
def fetch_twse_stock_day_all():
    return fetch_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=25)


def fetch_twse_bwibbu_all():
    return fetch_json("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=25)


def build_index_by_code(rows):
    out = {}
    for row in rows:
        code = first_non_empty(row, ["Code", "股票代號", "證券代號"])
        if code:
            out[code] = row
    return out


def analyze_stock_with_twse(code: str, name: str, price_row: dict, val_row: dict):
    close_price = to_float(first_non_empty(price_row, ["ClosingPrice", "收盤價"]))
    open_price = to_float(first_non_empty(price_row, ["OpeningPrice", "開盤價"]))
    high_price = to_float(first_non_empty(price_row, ["HighestPrice", "最高價"]))
    low_price = to_float(first_non_empty(price_row, ["LowestPrice", "最低價"]))
    change = to_float(first_non_empty(price_row, ["Change", "漲跌價差"]))
    direction = first_non_empty(price_row, ["Dir", "漲跌(+/-)"], "")
    volume = to_float(first_non_empty(price_row, ["TradeVolume", "成交股數"]), 0.0)

    if direction == "-":
        change = -abs(change)
    elif direction == "+":
        change = abs(change)

    prev_close = close_price - change if close_price and change is not None else 0.0
    change_pct = (change / prev_close * 100) if prev_close else 0.0

    pe = to_float(first_non_empty(val_row, ["PEratio", "本益比"]), 0.0)
    pb = to_float(first_non_empty(val_row, ["PBratio", "股價淨值比"]), 0.0)
    yield_pct = to_float(first_non_empty(val_row, ["DividendYield", "殖利率(%)"]), 0.0)

    intraday_range_pct = ((high_price - low_price) / open_price * 100) if open_price else 0.0
    close_near_high = (close_price >= (high_price - (high_price - low_price) * 0.25)) if high_price and low_price else False

    score = 50

    if change_pct >= 3:
        score += 16
    elif change_pct >= 1.5:
        score += 10
    elif change_pct <= -2:
        score -= 10

    if close_near_high:
        score += 8

    if intraday_range_pct >= 4:
        score += 6

    if volume >= 30_000_000:
        score += 8
    elif volume >= 10_000_000:
        score += 4

    if pe > 0:
        score += 3
    if 0 < pb <= 8:
        score += 3
    if yield_pct >= 2:
        score += 2

    score = int(clamp(round(score), 35, 92))

    if change_pct >= 2 and close_near_high:
        signal_text = "強勢股"
        reason = "收盤偏強 / 當日動能強"
        emoji = "🔴"
    elif change_pct > 0 and intraday_range_pct >= 3:
        signal_text = "轉折點"
        reason = "波動放大 / 轉強觀察"
        emoji = "🟡"
    elif change_pct <= -2:
        signal_text = "整理觀察"
        reason = "短線拉回 / 等待止穩"
        emoji = "⚪"
    else:
        signal_text = "整理觀察"
        reason = "量價中性 / 觀察續航"
        emoji = "⚪"

    return {
        "code": code,
        "name": name,
        "price": close_price if close_price else "--",
        "change_pct": round(change_pct, 2),
        "signal": signal_text,
        "reason": reason,
        "win_rate": score,
        "emoji": emoji,
        "volume": int(volume) if volume else 0,
        "pe": pe,
        "pb": pb,
        "yield": yield_pct,
    }


def get_stocks():
    try:
        price_rows = fetch_twse_stock_day_all()
        val_rows = fetch_twse_bwibbu_all()

        price_index = build_index_by_code(price_rows)
        val_index = build_index_by_code(val_rows)

        result = []
        for s in STOCKS:
            code = s["code"]
            name = s["name"]
            price_row = price_index.get(code, {})
            val_row = val_index.get(code, {})

            if not price_row:
                result.append({
                    "code": code,
                    "name": name,
                    "price": "--",
                    "change_pct": 0.0,
                    "signal": "資料取得中",
                    "reason": "TWSE無當日資料",
                    "win_rate": 50,
                    "emoji": "⚪",
                    "volume": 0,
                    "pe": 0,
                    "pb": 0,
                    "yield": 0,
                })
                continue

            result.append(analyze_stock_with_twse(code, name, price_row, val_row))

        return result

    except Exception as e:
        return [{
            "code": s["code"],
            "name": s["name"],
            "price": "--",
            "change_pct": 0.0,
            "signal": "資料取得中",
            "reason": type(e).__name__,
            "win_rate": 50,
            "emoji": "⚪",
            "volume": 0,
            "pe": 0,
            "pb": 0,
            "yield": 0,
        } for s in STOCKS]


# =========================
# 🚗 國五：南下 / 北上分開顯示
# =========================
def normalize_traffic_status(text: str) -> str:
    t = text.replace("　", " ").strip()
    if any(x in t for x in ["壅塞", "回堵", "事故", "車禍", "封閉"]):
        return "壅塞"
    if any(x in t for x in ["車多", "行車量大", "旅行時間增加", "施工", "塞車"]):
        return "車多"
    return "順暢"


def parse_n5_lines(text: str):
    results = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if any(k in line for k in [
            "國道5", "國5", "雪隧", "頭城", "坪林", "石碇", "南港系統", "蘇澳", "宜蘭", "羅東", "南下", "北上"
        ]):
            if len(line) >= 5:
                results.append(line)
    return list(dict.fromkeys(results))


def shorten_line(s: str, max_len: int = 46) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= max_len else s[:max_len] + "…"


def _safe_title_text(soup):
    parts = []
    if soup.title and soup.title.text:
        parts.append(soup.title.text.strip())

    for meta in soup.find_all("meta"):
        content = meta.get("content")
        if content and any(k in content for k in ["國道5", "國5", "雪隧", "頭城", "坪林", "蘇澳", "宜蘭", "南下", "北上"]):
            parts.append(content.strip())

    return "\n".join(parts)


def split_direction_lines(lines):
    south = []
    north = []

    for line in lines:
        is_south = any(k in line for k in ["南下", "南港系統", "石碇", "坪林", "頭城", "宜蘭", "羅東", "蘇澳"])
        is_north = any(k in line for k in ["北上", "蘇澳", "羅東", "宜蘭", "頭城", "坪林", "石碇", "南港系統"])

        if "南下" in line and line not in south:
            south.append(line)
        elif "北上" in line and line not in north:
            north.append(line)
        else:
            # 沒明寫方向時，先盡量依內容放兩邊都可理解的摘要
            if is_south and line not in south:
                south.append(line)
            if is_north and line not in north:
                north.append(line)

    if not south and lines:
        south = lines[:2]
    if not north and lines:
        north = lines[:2]

    return south[:3], north[:3]


def get_traffic():
    sources = [
        ("官方1968", "https://1968.freeway.gov.tw/"),
        ("官方1968-英文頁", "https://1968.freeway.gov.tw/?lang=en"),
        ("備援-國5影像頁", "https://www.1968services.tw/freeway/5"),
        ("備援-國5塞車頁", "https://www.1968services.tw/jam/n5"),
        ("備援-即時路況地圖", "https://www.1968services.tw/map"),
    ]

    collected = []
    hit_source = ""

    for source_name, url in sources:
        try:
            html = fetch_text(url, timeout=15)
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text("\n", strip=True)
            lines = parse_n5_lines(text)

            title_text = _safe_title_text(soup)
            if title_text:
                lines.extend(parse_n5_lines(title_text))

            lines = list(dict.fromkeys(lines))
            if lines:
                collected = lines[:10]
                hit_source = source_name
                break
        except Exception:
            continue

    if collected:
        south_lines_raw, north_lines_raw = split_direction_lines(collected)
        south_joined = " | ".join(south_lines_raw)
        north_joined = " | ".join(north_lines_raw)

        south_status = normalize_traffic_status(south_joined) if south_joined else "資料取得中"
        north_status = normalize_traffic_status(north_joined) if north_joined else "資料取得中"

        south_lines = [shorten_line(x) for x in south_lines_raw[:2]] or [f"南下：{south_status}"]
        north_lines = [shorten_line(x) for x in north_lines_raw[:2]] or [f"北上：{north_status}"]

        return {
            "title": "國五即時路況",
            "south_status": south_status,
            "north_status": north_status,
            "south_lines": south_lines,
            "north_lines": north_lines,
            "source": hit_source or "高速公路資料",
        }

    return {
        "title": "國五即時路況",
        "south_status": "資料取得中",
        "north_status": "資料取得中",
        "south_lines": [
            "南下：資料取得中",
            "官方與備援站暫時無法連線",
        ],
        "north_lines": [
            "北上：資料取得中",
            "官方與備援站暫時無法連線",
        ],
        "source": "fallback",
    }


# =========================
# 💡 AI 總結
# =========================
def build_ai_summary(stocks):
    strong = [s for s in stocks if s["signal"] == "強勢股"]
    turning = [s for s in stocks if s["signal"] == "轉折點"]
    valid_scores = [s for s in stocks if isinstance(s["win_rate"], int)]

    if strong:
        group = "強勢股續航偏強"
        action = "優先觀察動能股"
    elif turning:
        group = "市場有轉強跡象"
        action = "量價確認後再加碼"
    else:
        group = "盤勢中性整理"
        action = "控倉等待突破"

    top = max(valid_scores, key=lambda x: x["win_rate"]) if valid_scores else {"name": "無資料", "win_rate": 50}

    return {
        "group": group,
        "action": action,
        "focus": f"最高分：{top['name']} {top['win_rate']}分",
        "note": "勝率為官方日資料快照模型分數，非保證報酬。",
    }


# =========================
# HTML
# =========================
def esc_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_html():
    weather_list = get_weather_list()
    news = get_all_news()
    stocks = get_stocks()
    traffic = get_traffic()
    ai_summary = build_ai_summary(stocks)

    html = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>{TODAY} 早報</title>
<style>
body {{ font-family: Arial, "Noto Sans TC", sans-serif; padding: 24px; color: #222; }}
.card {{ border: 1px solid #ddd; border-radius: 16px; padding: 16px; margin-bottom: 16px; }}
.section-title {{ font-size: 18px; font-weight: 700; margin-bottom: 10px; }}
.weather-row, .stock-row, .news-row, .traffic-row {{ margin: 8px 0; }}
.small {{ color: #666; font-size: 13px; display:block; margin-top:4px; }}
.traffic-block {{ margin-top: 10px; padding-top: 10px; border-top: 1px dashed #ddd; }}
</style>
</head>
<body>

<div class="card">
  <div class="section-title">🔥 早安｜AI智慧早報</div>
  <div>{TODAY} 早報</div>
</div>

<div class="card weather">
  <div class="section-title">🌤 天氣觀測</div>
  {''.join(
      f'''
      <div class="weather-info weather-row">
        <span class="city">{esc_html(w["city"])}</span>｜
        <span class="temp">{esc_html(w["temp"])}</span>｜
        <span class="desc">降雨 {esc_html(w["rain"])}</span>
      </div>
      '''
      for w in weather_list
  )}
</div>

<div class="card tasks">
  <div class="section-title">📈 AI股票洞察</div>
  {''.join(
      f'''
      <div class="task-item stock-row">
        <span class="task-name">{esc_html(s["emoji"])} {esc_html(s["name"])} {s["change_pct"]:+.2f}%｜{esc_html(s["signal"])}｜勝率{s["win_rate"]}%</span>
        <span class="task-meta small">{esc_html(s["reason"])} / PER {esc_html(s["pe"])} / PB {esc_html(s["pb"])} / 殖利率 {esc_html(s["yield"])}%</span>
      </div>
      '''
      for s in stocks
  )}
</div>

<div class="card traffic">
  <div class="section-title">🚗 國五即時路況</div>

  <div class="traffic-block southbound">
    <div class="traffic-title" data-dir="south">南下｜{esc_html(traffic["south_status"])}</div>
    {''.join(f'<div class="traffic-row small south-line">{esc_html(line)}</div>' for line in traffic["south_lines"])}
  </div>

  <div class="traffic-block northbound">
    <div class="traffic-title" data-dir="north">北上｜{esc_html(traffic["north_status"])}</div>
    {''.join(f'<div class="traffic-row small north-line">{esc_html(line)}</div>' for line in traffic["north_lines"])}
  </div>

  <div class="traffic-row small">來源：{esc_html(traffic.get("source", ""))}</div>
</div>

<div class="card news">
  <div class="section-title">📰 新聞速報</div>

  <div class="news-group">
    <div><strong>🤖 AI</strong></div>
    {''.join(f'<div class="news-item news-row" data-cat="ai"><span class="news-headline">{esc_html(item["title"])}</span></div>' for item in news.get("ai", []))}
  </div>

  <div class="news-group">
    <div><strong>📺 YouTube</strong></div>
    {''.join(f'<div class="news-item news-row" data-cat="youtube"><span class="news-headline">{esc_html(item["title"])}</span></div>' for item in news.get("youtube", []))}
  </div>

  <div class="news-group">
    <div><strong>📈 ETF</strong></div>
    {''.join(f'<div class="news-item news-row" data-cat="etf"><span class="news-headline">{esc_html(item["title"])}</span></div>' for item in news.get("etf", []))}
  </div>
</div>

<div class="card ai-summary">
  <div class="section-title">💡 AI 今日判斷</div>
  <div class="summary-line">{esc_html(ai_summary["group"])}</div>
  <div class="summary-line">{esc_html(ai_summary["action"])}</div>
  <div class="summary-line">{esc_html(ai_summary["focus"])}</div>
  <div class="summary-line small">{esc_html(ai_summary["note"])}</div>
</div>

</body>
</html>
"""
    filename = f"morning-briefing-{TODAY}.html"
    Path(filename).write_text(html, encoding="utf-8")
    print(f"✅ 已生成 {filename}")


if __name__ == "__main__":
    generate_html()
