import requests
from datetime import datetime
from pathlib import Path
import random

# =========================
# 🌤 天氣（使用 open-meteo）
# =========================
def get_weather(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,precipitation_probability"
    data = requests.get(url).json()
    current = data.get("current", {})
    return {
        "temp": f"{current.get('temperature_2m', '--')}°C",
        "rain": f"{current.get('precipitation_probability', '--')}%"
    }

# =========================
# 📰 新聞（RSS）
# =========================
def get_news(keyword):
    url = f"https://news.google.com/rss/search?q={keyword}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    import feedparser
    feed = feedparser.parse(url)
    return [entry.title for entry in feed.entries[:3]]

# =========================
# 📈 台股（簡化版）
# =========================
def get_stocks():
    # 這裡先用假資料（你之後可接 FinMind）
    stocks = [
        ("台積電", "+2.3%"),
        ("聯發科", "+1.8%"),
        ("廣達", "+3.5%"),
    ]
    return stocks

# =========================
# 🚗 國五交通（簡化）
# =========================
def get_traffic():
    status = random.choice(["順暢", "車多", "壅塞"])
    return status

# =========================
# 主產生 HTML
# =========================
def generate_html():
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"morning-briefing-{today}.html"

    # 天氣
    taoyuan = get_weather(24.9936, 121.3009)
    yilan   = get_weather(24.684, 121.799)

    # 新聞
    ai_news  = get_news("AI")
    yt_news  = get_news("YouTube")
    etf_news = get_news("ETF")

    # 股票
    stocks = get_stocks()

    # 交通
    traffic = get_traffic()

    html = f"""
    <html><head><meta charset="UTF-8">
    <title>{today} 早報</title></head><body>

    <div class="weather">
        <span class="weather-icon">🌤</span>
        <div class="weather-info">
            <span class="city">桃園</span>
            <span class="temp">{taoyuan['temp']}</span>
            <span class="desc">降雨 {taoyuan['rain']}</span>
        </div>
        <div class="weather-info">
            <span class="city">宜蘭五結</span>
            <span class="temp">{yilan['temp']}</span>
            <span class="desc">降雨 {yilan['rain']}</span>
        </div>
    </div>

    <div class="news">
        {''.join([f'<div class="news-item" data-cat="ai"><span class="news-headline">{n}</span></div>' for n in ai_news])}
        {''.join([f'<div class="news-item" data-cat="youtube"><span class="news-headline">{n}</span></div>' for n in yt_news])}
        {''.join([f'<div class="news-item" data-cat="etf"><span class="news-headline">{n}</span></div>' for n in etf_news])}
    </div>

    <div class="tasks">
        {''.join([f'<div class="task-item"><span class="task-priority p-high"></span><span class="task-name">{s[0]} {s[1]}</span></div>' for s in stocks])}
    </div>

    <div class="mails">
        <div class="mail-item">
            <span class="urgency-dot p-high"></span>
            <span class="mail-sender">國五交通</span>
            <span class="mail-subject">{traffic}</span>
        </div>
    </div>

    </body></html>
    """

    Path(filename).write_text(html, encoding="utf-8")
    print(f"✅ 已生成 {filename}")

if __name__ == "__main__":
    generate_html()
