"""
Telegram 發送器 v21.3
"""

import os
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")


def find_briefing_html() -> Path:
    today = date.today().strftime("%Y-%m-%d")

    today_path = Path(f"morning-briefing-{today}.html")
    if today_path.exists():
        print(f"✅ 使用今日早報：{today_path}")
        return today_path

    default_path = Path("morning-briefing.html")
    if default_path.exists():
        print("⚠️ 使用固定檔：morning-briefing.html")
        return default_path

    files = sorted(Path(".").glob("morning-briefing-*.html"), reverse=True)
    if files:
        print(f"⚠️ 使用最新檔：{files[0]}")
        return files[0]

    raise FileNotFoundError("❌ 找不到任何早報 HTML")


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def esc(text: str) -> str:
    text = str(text)
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def parse_briefing(html_path: Path) -> dict:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    result = {
        "date": _text(soup.find("title")) or date.today().strftime("%Y-%m-%d"),
        "weather_list": [],
        "stocks": [],
        "traffic": {
            "south_status": "",
            "north_status": "",
            "south_lines": [],
            "north_lines": [],
            "source": "",
        },
        "news": {"ai": [], "youtube": [], "etf": []},
        "ai_summary": [],
    }

    for w in soup.select(".weather .weather-info"):
        result["weather_list"].append({
            "city": _text(w.select_one(".city")),
            "temp": _text(w.select_one(".temp")),
            "desc": _text(w.select_one(".desc")),
        })

    for item in soup.select(".tasks .task-item"):
        task_name = _text(item.select_one(".task-name"))
        task_meta = _text(item.select_one(".task-meta"))
        if task_name:
            result["stocks"].append({
                "line": task_name,
                "meta": task_meta,
            })

    south_title = soup.select_one('.traffic .southbound .traffic-title[data-dir="south"]')
    north_title = soup.select_one('.traffic .northbound .traffic-title[data-dir="north"]')

    if south_title:
        text = _text(south_title)
        result["traffic"]["south_status"] = text.split("｜", 1)[1].strip() if "｜" in text else text

    if north_title:
        text = _text(north_title)
        result["traffic"]["north_status"] = text.split("｜", 1)[1].strip() if "｜" in text else text

    for row in soup.select(".traffic .southbound .south-line"):
        txt = _text(row)
        if txt:
            result["traffic"]["south_lines"].append(txt)

    for row in soup.select(".traffic .northbound .north-line"):
        txt = _text(row)
        if txt:
            result["traffic"]["north_lines"].append(txt)

    for row in soup.select(".traffic .traffic-row"):
        txt = _text(row)
        if txt.startswith("來源："):
            result["traffic"]["source"] = txt.replace("來源：", "").strip()

    for item in soup.select(".news .news-item"):
        cat = item.get("data-cat", "").strip()
        headline = _text(item.select_one(".news-headline"))
        if cat in result["news"] and headline:
            result["news"][cat].append(headline)

    for k in result["news"]:
        result["news"][k] = result["news"][k][:3]

    for row in soup.select(".ai-summary .summary-line"):
        txt = _text(row)
        if txt:
            result["ai_summary"].append(txt)

    return result


def build_message(data: dict) -> str:
    lines = [
        "🔥 *早安｜AI智慧早報*",
        f"📅 *{esc(data.get('date', ''))}*",
        "",
    ]

    if data.get("weather_list"):
        lines += ["╭─ 🌤 *天氣觀測*"]
        for w in data["weather_list"]:
            lines.append(f"│ 📍 *{esc(w['city'])}*｜{esc(w['temp'])}｜{esc(w['desc'])}")
        lines += ["╰────────────────", ""]

    if data.get("stocks"):
        lines += ["╭─ 📈 *AI股票洞察*"]
        for s in data["stocks"]:
            lines.append(f"│ {esc(s['line'])}")
            if s.get("meta"):
                lines.append(f"│ └─ {esc(s['meta'])}")
        lines += ["╰────────────────", ""]

    traffic = data.get("traffic", {})
    if traffic:
        lines += [
            "╭─ 🚗 *國五即時路況*",
            f"│ *南下*｜{esc(traffic.get('south_status', '資料取得中'))}",
        ]
        for t in traffic.get("south_lines", [])[:2]:
            lines.append(f"│ └─ {esc(t)}")

        lines.append(f"│ *北上*｜{esc(traffic.get('north_status', '資料取得中'))}")
        for t in traffic.get("north_lines", [])[:2]:
            lines.append(f"│ └─ {esc(t)}")

        if traffic.get("source"):
            lines.append(f"│ 來源：{esc(traffic.get('source'))}")
        lines += ["╰────────────────", ""]

    news = data.get("news", {})
    if any(news.values()):
        lines += ["╭─ 📰 *新聞速報*"]

        section_names = {
            "ai": "🤖 AI",
            "youtube": "📺 YouTube",
            "etf": "📈 ETF",
        }

        for key in ("ai", "youtube", "etf"):
            items = news.get(key, [])
            if not items:
                continue
            lines.append(f"│ *{esc(section_names[key])}*")
            for i, title in enumerate(items[:3], 1):
                lines.append(f"│ {i}\\. {esc(title)}")

        lines += ["╰────────────────", ""]

    if data.get("ai_summary"):
        lines += ["╭─ 💡 *AI 今日判斷*"]
        for row in data["ai_summary"][:4]:
            lines.append(f"│ {esc(row)}")
        lines += ["╰────────────────", ""]

    lines += [
        "━━━━━━━━━━━━━━━━━━",
        "🚀 *Have a great day\\!*"
    ]
    return "\n".join(lines)


def send_telegram(message: str):
    if not BOT_TOKEN or not CHAT_ID:
        raise ValueError("❌ BOT_TOKEN 或 CHAT_ID 未設定")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        },
        timeout=20
    )
    resp.raise_for_status()
    print("✅ Telegram 發送成功")


def main():
    html_path = find_briefing_html()
    print(f"📄 使用檔案：{html_path}")

    data = parse_briefing(html_path)
    message = build_message(data)

    print("📨 預覽訊息：\n")
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
