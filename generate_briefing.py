import json
import math
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests
import yfinance as yf
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (MorningBriefingBot/21.0; +https://github.com/)"
}

TODAY = datetime.now().strftime("%Y-%m-%d")

# ====== 可調整 ======
STOCKS = [
    {"name": "台積電", "code": "2330.TW"},
    {"name": "聯發科", "code": "2454.TW"},
    {"name": "廣達", "code": "2382.TW"},
]

NEWS_TOPICS = [
    ("ai", "AI 台灣 OR 人工智慧 台灣"),
    ("youtube", "YouTube 台灣 OR Google 台灣"),
    ("etf", "ETF 台灣 OR 美股ETF 台灣"),
]

WEATHER_SOURCES = [
    {
        "city": "桃園",
        "url": "https://www.cwa.gov.tw/V8/C/W/Town/Town.html?TID=6800100",
    },
    {
        "city": "宜蘭五結",
        "url": "https://www.cwa.gov.tw/V8/C/W/Town/Town.html?TID=1000209",
    },
]


def fetch_text(url: str, timeout: int = 15) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


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
# 🌤 天氣：中央氣象署頁面抓取
# =========================
def extract_weather_from_cwa(html: str, city_name: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # 優先抓「資料 / 溫度 / 降雨機率」
    temp = None
    rain = None
    desc = ""

    # 抓第一個「資料 ... 溫度 xx」
    m_temp = re.search(r"資料[\s\S]{0,300}?溫度\s+(\d{1,2})", text)
    if m_temp:
        temp = f"{m_temp.group(1)}°C"

    # 抓較接近現況的第一個降雨機率
    m_rain = re.search(r"降雨機率\s+(\d{1,3})%", text)
    if m_rain:
        rain = f"{m_rain.group(1)}%"

    # 抓描述
    m_desc = re.search(rf"{re.escape(city_name)}[\s\S]{{0,120}}?([^\n]*降雨機率[^\n]*)", text)
    if m_desc:
        desc = m_desc.group(1).strip()

    if not desc:
        # 從 meta / title 補
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            desc = meta_desc["content"].strip()

    return {
        "city": city_name,
        "temp": temp or "--°C",
        "rain": rain or "--%",
        "desc": desc or f"降雨 {rain or '--%'}",
        "source": "CWA",
    }


def get_weather_list():
    results = []
    for item in WEATHER_SOURCES:
        try:
            html = fetch_text(item["url"])
            results.append(extract_weather_from_cwa(html, item["city"]))
        except Exception as e:
            results.append({
                "city": item["city"],
                "temp": "--°C",
                "rain": "--%",
                "desc": f"資料取得中 ({type(e).__name__})",
                "source": "CWA",
            })
    return results


# =========================
# 📰 新聞：Google News RSS
# =========================
def clean_news_title(title: str) -> str:
    title = re.sub(r"\s*-\s*[^-]+$", "", title).strip()
    return title


def get_news(keyword: str, limit: int = 3):
    q = quote(keyword)
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)

    items = []
    for entry in feed.entries[:limit]:
        items.append({
            "title": clean_news_title(entry.title),
            "link": getattr(entry, "link", ""),
            "source": getattr(entry, "source", {}).get("title", "") if getattr(entry, "source", None) else "",
        })
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
# 📈 股票：yfinance + 技術面 AI評分
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
        df = yf.download(
            ticker,
            period="3mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )

        if df is None or df.empty or len(df) < 35:
            raise ValueError("not enough price history")

        close = df["Close"].astype(float).dropna()
        vol = df["Volume"].astype(float).fillna(0)

        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        day_change = ((last - prev) / prev) * 100 if prev else 0

        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]

        rsi14 = float(rsi(close, 14).iloc[-1])

        macd, signal, hist = calc_macd(close)
        hist_now = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2])

        vol5 = vol.rolling(5).mean().iloc[-1]
        vol20 = vol.rolling(20).mean().iloc[-1]
        vol_ratio = float(vol5 / vol20) if vol20 else 1.0

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
            "win_rate": score,   # 這是模型分數，不是假裝未來保證
            "emoji": emoji,
            "rsi14": round(rsi14, 1),
            "ma5": round(float(ma5), 2),
            "ma20": round(float(ma20), 2),
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
            "ma5": 0,
            "ma20": 0,
        }


def get_stocks():
    return [analyze_stock(s["name"], s["code"]) for s in STOCKS]


# =========================
# 🚗 國五路況：高速公路1968官方頁面
# =========================
def normalize_traffic_status(text: str) -> str:
    t = text.replace("　", " ").strip()
    if any(x in t for x in ["壅塞", "回堵", "事故", "車禍"]):
        return "壅塞"
    if any(x in t for x in ["車多", "行車量大", "旅行時間增加"]):
        return "車多"
    return "順暢"


def get_traffic():
    url = "https://1968.freeway.gov.tw/tp_future"
    try:
        html = fetch_text(url, timeout=20)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)

        notes = []
        for line in text.splitlines():
            line = line.strip()
            if "國道5號" in line or "國5" in line or "頭城" in line or "坪林" in line or "雪隧" in line:
                if len(line) >= 6:
                    notes.append(line)

        notes = list(dict.fromkeys(notes))[:8]
        joined = " | ".join(notes)

        status = normalize_traffic_status(joined)

        # 優先做成你看得懂的兩段
        return {
            "title": "國五即時路況",
            "status": status,
            "lines": [
                f"國5 / 雪隧：{status}",
                notes[0] if notes else "1968 即時資料已更新",
                notes[1] if len(notes) > 1 else "建議出發前再看一次 1968",
            ],
            "source": "高速公路1968",
        }
    except Exception as e:
        return {
            "title": "國五即時路況",
            "status": "資料取得中",
            "lines": [
                "國5 / 雪隧：資料取得中",
                f"fallback: {type(e).__name__}",
                "請改查高速公路1968",
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

    top = sorted(stocks, key=lambda x: x["win_rate"], reverse=True)[0]
    return {
        "group": group,
        "action": action,
        "focus": f"最高分：{top['name']} {top['win_rate']}分",
        "note": "勝率為技術面模型分數，非保證報酬。",
    }


# =========================
# HTML 產生
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
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; background: #f3f3f3; margin-left: 6px; }}
.small {{ color: #666; font-size: 13px; }}
</style>
</head>
<body>
<div class="card">
  <div class="section-title">🔥 早安｜AI智慧早報</div>
  <div>{TODAY}</div>
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
        <span class="task-priority {'p-high' if s["signal"] == "強勢股" else 'p-mid'}"></span>
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
    <span class="urgency-dot {'p-high' if traffic["status"] != "順暢" else 'p-mid'}"></span>
    <span class="mail-sender">{esc_html(traffic["title"])}</span>
    <span class="mail-subject">{esc_html(traffic["status"])}</span>
  </div>
  {''.join(f'<div class="traffic-row small">{esc_html(line)}</div>' for line in traffic["lines"])}
</div>

<div class="card news">
  <div class="section-title">📰 新聞速報</div>
  {''.join(
      f'''
      <div class="news-group">
        <div class="small"><strong>{esc_html(cat)}</strong></div>
        {''.join(f'<div class="news-item news-row" data-cat="{key}"><span class="news-headline">{esc_html(item["title"])}</span></div>' for item in items)}
      </div>
      '''
      for key, cat, items in [
          ("ai", "🤖 AI", news.get("ai", [])),
          ("youtube", "📺 YouTube", news.get("youtube", [])),
          ("etf", "📈 ETF", news.get("etf", [])),
      ]
  )}
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
