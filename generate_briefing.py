import math
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests
import yfinance as yf
from bs4 import BeautifulSoup

TODAY = datetime.now().strftime("%Y-%m-%d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (MorningBriefingBot/21.1; +https://github.com/)",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

STOCKS = [
    {"name": "台積電", "ticker": "2330.TW"},
    {"name": "聯發科", "ticker": "2454.TW"},
    {"name": "廣達", "ticker": "2382.TW"},
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


def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        if isinstance(v, str):
            v = v.replace(",", "").replace("%", "").strip()
        return float(v)
    except Exception:
        return default


def clamp(n, low, high):
    return max(low, min(high, n))


# =========================
# 🌤 天氣（穩定版）
# =========================
def get_weather(lat: float, lon: float) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,precipitation_probability,weather_code"
        "&timezone=Asia%2FTaipei"
    )
    data = fetch_json(url, timeout=20)
    current = data.get("current", {})
    temp = current.get("temperature_2m")
    rain = current.get("precipitation_probability")

    return {
        "temp": f"{temp:.1f}°C" if isinstance(temp, (int, float)) else "--°C",
        "rain": f"{int(round(rain))}%" if isinstance(rain, (int, float)) else "--%",
        "raw_temp": temp if isinstance(temp, (int, float)) else None,
        "raw_rain": rain if isinstance(rain, (int, float)) else None,
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
# 📰 新聞
# =========================
def clean_news_title(title: str) -> str:
    title = re.sub(r"\s*-\s*[^-]+$", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    return title


def get_news(keyword: str, limit: int = 3):
    q = quote(keyword)
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)

    items = []
    seen = set()
    for entry in feed.entries:
        title = clean_news_title(getattr(entry, "title", "").strip())
        if not title or title in seen:
            continue
        seen.add(title)
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
# 📈 股票 AI 分析
# =========================
def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, math.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def calc_macd(close):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return macd, signal, hist


def analyze_stock(name: str, ticker: str):
    try:
        # 改用 Ticker().history()，避免 download() 常見 MultiIndex / TypeError 問題
        hist = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=False)

        if hist is None or hist.empty or len(hist) < 35:
            raise ValueError("not enough price history")

        close = hist["Close"].astype(float).dropna()
        vol = hist["Volume"].astype(float).fillna(0)

        if len(close) < 35:
            raise ValueError("not enough close series")

        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        day_change = ((last - prev) / prev) * 100 if prev else 0.0

        ma5 = float(close.rolling(5).mean().iloc[-1])
        ma10 = float(close.rolling(10).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])

        rsi14 = float(rsi(close, 14).iloc[-1])

        macd, signal, histv = calc_macd(close)
        hist_now = float(histv.iloc[-1])
        hist_prev = float(histv.iloc[-2])

        vol5 = float(vol.rolling(5).mean().iloc[-1])
        vol20 = float(vol.rolling(20).mean().iloc[-1]) if float(vol.rolling(20).mean().iloc[-1]) != 0 else 1.0
        vol_ratio = vol5 / vol20

        price_above_ma20 = last > ma20
        price_above_ma5 = last > ma5
        trend_up = ma5 > ma10 > ma20
        macd_turn_up = hist_now > hist_prev and hist_now > -0.05
        volume_expand = vol_ratio >= 1.05

        score = 50
        if price_above_ma20:
            score += 8
        if price_above_ma5:
            score += 5
        if trend_up:
            score += 12
        if macd_turn_up:
            score += 8
        if 52 <= rsi14 <= 72:
            score += 10
        elif 72 < rsi14 <= 80:
            score += 3
        elif rsi14 < 40:
            score -= 8
        if volume_expand:
            score += 6
        if day_change > 2:
            score += 5
        elif day_change < -2:
            score -= 8

        score = int(clamp(round(score), 35, 92))

        if trend_up and rsi14 >= 55 and day_change >= 1:
            signal_text = "強勢股"
            reason = "均線多頭 / 動能延續"
            emoji = "🔴"
        elif macd_turn_up and last >= ma20 * 0.98:
            signal_text = "轉折點"
            reason = "MACD翻揚 / 轉強觀察"
            emoji = "🟡"
        elif rsi14 > 75:
            signal_text = "高檔震盪"
            reason = "短線過熱 / 留意震盪"
            emoji = "🟠"
        else:
            signal_text = "整理觀察"
            reason = "型態整理 / 等待突破"
            emoji = "⚪"

        return {
            "name": name,
            "ticker": ticker,
            "price": round(last, 2),
            "change_pct": round(day_change, 2),
            "signal": signal_text,
            "reason": reason,
            "win_rate": score,
            "emoji": emoji,
            "rsi14": round(rsi14, 1),
        }

    except Exception as e:
        return {
            "name": name,
            "ticker": ticker,
            "price": "--",
            "change_pct": 0.0,
            "signal": "資料取得中",
            "reason": type(e).__name__,
            "win_rate": 50,
            "emoji": "⚪",
            "rsi14": 0,
        }


def get_stocks():
    return [analyze_stock(s["name"], s["ticker"]) for s in STOCKS]


# =========================
# 🚗 國五路況（多端點 fallback）
# =========================
def normalize_traffic_status(text: str) -> str:
    t = text.replace("　", " ").strip()
    if any(x in t for x in ["壅塞", "回堵", "事故", "車禍", "封閉"]):
        return "壅塞"
    if any(x in t for x in ["車多", "行車量大", "旅行時間增加", "施工"]):
        return "車多"
    return "順暢"


def parse_n5_lines(text: str):
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if any(k in line for k in ["國道5", "國5", "雪隧", "頭城", "坪林", "石碇", "南港系統"]):
            if len(line) >= 6:
                lines.append(line)
    return list(dict.fromkeys(lines))


def get_traffic():
    urls = [
        "https://1968.freeway.gov.tw/roadinfo?freeway=5",
        "https://1968.freeway.gov.tw/n_notify",
        "https://1968.freeway.gov.tw/",
    ]

    collected = []

    for url in urls:
        try:
            html = fetch_text(url, timeout=15)
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text("\n", strip=True)
            lines = parse_n5_lines(text)
            collected.extend(lines)
            if collected:
                break
        except Exception:
            continue

    collected = list(dict.fromkeys(collected))[:5]

    if collected:
        joined = " | ".join(collected)
        status = normalize_traffic_status(joined)
        pretty = []
        for x in collected[:3]:
            if len(x) > 42:
                x = x[:42] + "…"
            pretty.append(x)

        return {
            "title": "國五即時路況",
            "status": status,
            "lines": pretty if pretty else [f"國5 / 雪隧：{status}"],
            "source": "高速公路1968",
        }

    return {
        "title": "國五即時路況",
        "status": "資料取得中",
        "lines": [
            "國5 / 雪隧：資料取得中",
            "1968 查詢暫時失敗",
            "稍後自動恢復",
        ],
        "source": "高速公路1968",
    }


# =========================
# 💡 AI 總結
# =========================
def build_ai_summary(stocks):
    strong = [s for s in stocks if s["signal"] == "強勢股"]
    turning = [s for s in stocks if s["signal"] == "轉折點"]
    hot = [s for s in stocks if s["signal"] == "高檔震盪"]

    valid_scores = [s for s in stocks if isinstance(s["win_rate"], int)]

    if strong:
        group = "AI / 高算力族群偏強"
        action = "續抱強勢、弱留強"
    elif turning:
        group = "市場進入轉折觀察"
        action = "等量價確認再加碼"
    elif hot:
        group = "短線偏熱"
        action = "高檔不追價，等拉回"
    else:
        group = "盤勢中性整理"
        action = "控倉等待突破"

    top = max(valid_scores, key=lambda x: x["win_rate"]) if valid_scores else {"name": "無資料", "win_rate": 50}

    return {
        "group": group,
        "action": action,
        "focus": f"最高分：{top['name']} {top['win_rate']}分",
        "note": "勝率為技術面模型分數，非保證報酬。",
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
        <span class="task-meta small">{esc_html(s["reason"])} / RSI {esc_html(s["rsi14"])}</span>
      </div>
      '''
      for s in stocks
  )}
</div>

<div class="card mails">
  <div class="section-title">🚗 國五即時路況</div>
  <div class="mail-item">
    <span class="mail-sender">{esc_html(traffic["title"])}</span>｜
    <span class="mail-subject">{esc_html(traffic["status"])}</span>
  </div>
  {''.join(f'<div class="traffic-row small">{esc_html(line)}</div>' for line in traffic["lines"])}
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
