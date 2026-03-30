import os
from datetime import datetime
from pathlib import Path

def generate_html():
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"morning-briefing-{today}.html"

    html = f"""
    <html>
    <head>
        <title>{today} 早報</title>
        <meta charset="UTF-8">
    </head>
    <body>

    <div class="weather">
        <span class="weather-icon">🌤</span>
        <div class="weather-info">
            <span class="city">台北</span>
            <span class="temp">26°C</span>
            <span class="desc">晴天</span>
        </div>
    </div>

    <div class="tasks">
        <div class="task-item">
            <span class="task-priority p-high"></span>
            <span class="task-name">完成報表</span>
        </div>
        <div class="task-item">
            <span class="task-priority p-mid"></span>
            <span class="task-name">回覆客戶</span>
        </div>
    </div>

    <div class="mails">
        <div class="mail-item">
            <span class="urgency-dot p-urgent"></span>
            <span class="mail-sender">老闆</span>
            <span class="mail-subject">今天會議</span>
        </div>
    </div>

    <div class="news">
        <div class="news-item" data-cat="ai">
            <span class="news-headline">AI 市場持續成長</span>
        </div>
        <div class="news-item" data-cat="etf">
            <span class="news-headline">ETF 資金流入創新高</span>
        </div>
    </div>

    </body>
    </html>
    """

    Path(filename).write_text(html, encoding="utf-8")
    print(f"✅ 已生成 {filename}")

if __name__ == "__main__":
    generate_html()
