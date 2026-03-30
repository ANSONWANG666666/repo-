"""
send_morning_briefing_telegram.py
GitHub Actions 版 — Token 從環境變數讀取
"""

import os
import requests
from datetime import date
from pathlib import Path
from bs4 import BeautifulSoup

# ─── 設定（從 GitHub Secrets 注入）────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

# ─── 找 HTML（已升級：三層 fallback）──────────────────────────
def find_briefing_html() -> Path:
    today = date.today().strftime("%Y-%m-%d")

    # 1️⃣ 找今天檔
    today_path = Path(f"morning-briefing-{today}.html")
    if today_path.exists():
        print(f"✅ 使用今日早報：{today_path}")
        return today_path

    # 2️⃣ 找固定檔（你目前 repo 有的）
    default_path = Path("morning-briefing.html")
    if default_path.exists():
        print("⚠️ 使用固定檔：morning-briefing.html")
        return default_path

    # 3️⃣ 找歷史最新檔
    files = sorted(Path(".").glob("morning-briefing-*.html"), reverse=True)
    if files:
        print(f"⚠️ 使用最新檔：{files[0]}")
        return files[0]

    raise FileNotFoundError("❌ 找不到任何早報 HTML")

# ─── 工具函數 ────────────────────────────────────────────────
def _text(el) -> str:
    return el.text.strip() if el else ""

# ─── 解析 HTML ──────────────────────────────────────────────
def parse_briefing(html_path: Path) -> dict:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    result = {}

    result["date"] = _text(soup.find("title")) or date.today().strftime("%Y/%m/%d")

    result["weather"] = {
        "icon": _text(soup.select_one(".weather-icon")) or "🌤",
        "city": _text(soup.select_one(".weather-info .city")) or "",
        "temp": _text(soup.select_one(".weather-info .temp")) or "",
        "desc": _text(soup.select_one(".weather-info .desc")) or "",
    }

    # 任務
    tasks = []
    for level in ("p-high", "p-mid"):
        for item in soup.select(".task-item"):
            p_el = item.select_one(".task-priority")
            name = item.select_one(".task-name")
            if p_el and level in p_el.get("class", []):
                tasks.append((level, _text(name) or "未知任務"))
        if tasks:
            break
    result["tasks"] = tasks[:5]

    # 郵件
    mails = []
    for item in soup.select(".mail-item"):
        dot = item.select_one(".urgency-dot")
        if dot and ("p-urgent" in dot.get("class", []) or "p-high" in dot.get("class", [])):
            mails.append({
                "sender": _text(item.select_one(".mail-sender")) or "未知",
                "subject": _text(item.select_one(".mail-subject")) or "（無主旨）"
            })
    result["mails"] = mails[:3]

    # 新聞
    news = {}
    for item in soup.select(".news-item"):
        cat = item.get("data-cat", "")
        headline = item.select_one(".news-headline")
        if headline and cat not in news:
            news[cat] = _text(headline)
    result["news"] = news

    return result

# ─── Telegram MarkdownV2 escape ────────────────────────────
def esc(text: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text

# ─── 建立訊息 ──────────────────────────────────────────────
def build_message(data: dict) -> str:
    w = data.get("weather", {})
    lines = [
        "*☀️ 早安\\！早報摘要*",
        f"📅 {esc(data.get('date',''))}",
        ""
    ]

    if w.get("temp"):
        lines += [
            f"{esc(w['icon'])} {esc(w['city'])} {esc(w['temp'])} {esc(w['desc'])}",
            ""
        ]

    # 任務
    if data.get("tasks"):
        lines.append("*─── 今日任務 ───*")
        for level, name in data["tasks"]:
            icon = "🔴" if "high" in level else "🟡"
            lines.append(f"{icon} {esc(name)}")
        lines.append("")

    # 郵件
    if data.get("mails"):
        lines.append("*─── 重要郵件 ───*")
        for m in data["mails"]:
            lines.append(f"📧 {esc(m['sender'])}｜{esc(m['subject'])}")
        lines.append("")

    # 新聞
    if data.get("news"):
        lines.append("*─── 新聞速報 ───*")
        for cat, headline in data["news"].items():
            prefix = {
                "ai": "🤖 AI",
                "etf": "📈 ETF",
                "youtube": "📺 YT"
            }.get(cat, cat.upper())
            lines.append(f"*{esc(prefix)}* {esc(headline)}")
        lines.append("")

    lines += [
        esc("─────────────────"),
        "Have a great day\\! 🚀"
    ]

    return "\n".join(lines)

# ─── 發送 Telegram ────────────────────────────────────────
def send_telegram(message: str):
    if not BOT_TOKEN or not CHAT_ID:
        raise ValueError("❌ BOT_TOKEN 或 CHAT_ID 未設定")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    resp = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "MarkdownV2"
        },
        timeout=10
    )

    resp.raise_for_status()
    print("✅ Telegram 發送成功")

# ─── 主程式 ───────────────────────────────────────────────
def main():
    html_path = find_briefing_html()
    print(f"📄 使用檔案：{html_path}")

    data = parse_briefing(html_path)
    message = build_message(data)

    print("📨 預覽訊息：\n", message)
    send_telegram(message)

if __name__ == "__main__":
    main()
