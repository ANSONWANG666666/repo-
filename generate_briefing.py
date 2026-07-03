import email
import imaplib
import re
from datetime import datetime
from email.header import decode_header
from pathlib import Path
from urllib.parse import quote
import html as html_lib

import feedparser
import requests
from bs4 import BeautifulSoup

TODAY = datetime.now().strftime("%Y-%m-%d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (MorningBriefingBot/21.4; +https://github.com/)",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# AI 供應鏈觀察清單（依產業分組；上市/上櫃自動判別資料源）
STOCK_GROUPS = [
    ("AI 晶圓代工", [
        ("台積電", "2330", "🥇龍頭"),
        ("聯電", "2303", "第二梯隊"),
        ("世界先進", "5347", "第三梯隊"),
    ]),
    ("AI ASIC／IC設計", [
        ("世芯-KY", "3661", "🥇ASIC龍頭"),
        ("創意", "3443", "ASIC"),
        ("智原", "3035", "ASIC"),
        ("聯發科", "2454", "AI SoC"),
    ]),
    ("AI 封裝／測試", [
        ("日月光投控", "3711", "先進封裝"),
        ("京元電子", "2449", "AI晶片測試"),
    ]),
    ("AI 伺服器／ODM", [
        ("廣達", "2382", "🥇伺服器"),
        ("緯穎", "6669", "🥈伺服器"),
        ("鴻海", "2317", "AI伺服器／機器人"),
    ]),
    ("AI 機殼", [
        ("勤誠", "8210", "機殼龍頭"),
    ]),
    ("AI 散熱", [
        ("奇鋐", "3017", "🥇散熱龍頭"),
        ("雙鴻", "3324", "散熱"),
        ("建準", "2421", "散熱風扇"),
    ]),
    ("AI PCB／CCL／ABF載板", [
        ("台光電", "2383", "高速CCL"),
        ("金像電", "2368", "AI PCB"),
        ("欣興", "3037", "PCB／ABF"),
        ("南電", "8046", "ABF載板"),
        ("景碩", "3189", "IC載板"),
    ]),
    ("AI 光通訊／CPO", [
        ("上詮", "3363", "CPO"),
        ("光聖", "6442", "光模組"),
        ("聯鈞", "3450", "光通訊"),
    ]),
    ("AI 網通／交換器", [
        ("智邦", "2345", "🥇交換器"),
        ("啟碁", "6285", "網通"),
        ("中磊", "5388", "網通"),
    ]),
    ("AI 電源／BBU", [
        ("台達電", "2308", "電源龍頭"),
        ("群電", "6412", "電源"),
        ("光寶科", "2301", "電源"),
        ("AES-KY", "6781", "BBU備援電池"),
        ("順達", "3211", "BBU／電池"),
    ]),
    ("AI 機器人／自動化", [
        ("上銀", "2049", "精密傳動"),
        ("所羅門", "2359", "AI視覺／機器人"),
        ("盟立", "2464", "自動化設備"),
    ]),
]

STOCKS = [
    {"name": n, "code": c, "industry": g, "position": p}
    for g, items in STOCK_GROUPS
    for n, c, p in items
]

INDICES = [
    {"name": "韓股KOSPI", "symbol": "^KS11"},
    {"name": "日股日經225", "symbol": "^N225"},
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

EMAIL_ACCOUNT = "wjia.tw@gmail.com"
EMAIL_APP_PASSWORD = None  # 從環境變數讀，不要寫死


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
# 📈 股票：TWSE 官方 OpenAPI
# =========================
def fetch_tpex_price_index():
    """上櫃每日收盤行情（TPEx OpenAPI），轉成 TWSE 相容欄位的 {code: row}。

    TPEx 憑證缺 Subject Key Identifier，新版 Python 驗證會失敗，
    對公開行情資料改用 verify=False 並加重試。
    """
    import time as _time
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    last_err = None
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=40, verify=False)
            r.raise_for_status()
            out = {}
            for row in r.json():
                code = (row.get("SecuritiesCompanyCode") or "").strip()
                if not code:
                    continue
                out[code] = {
                    "ClosingPrice": row.get("Close", ""),
                    "OpeningPrice": row.get("Open", ""),
                    "HighestPrice": row.get("High", ""),
                    "LowestPrice": row.get("Low", ""),
                    "Change": row.get("Change", ""),  # 已含正負號
                    "TradeVolume": row.get("TradingShares", ""),
                }
            return out
        except Exception as e:
            last_err = e
            _time.sleep(3)
    raise last_err


def fetch_tpex_valuation_index():
    """上櫃個股本益比/殖利率/股價淨值比，轉成 TWSE 相容欄位的 {code: row}（best-effort）。"""
    import time as _time
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=40, verify=False)
            r.raise_for_status()
            out = {}
            for row in r.json():
                code = (row.get("SecuritiesCompanyCode") or "").strip()
                if not code:
                    continue
                out[code] = {
                    "PEratio": first_non_empty(
                        row, ["PriceEarningRatio", "PERatio", "本益比"], "0"
                    ),
                    "PBratio": first_non_empty(
                        row, ["PriceBookRatio", "PBRatio", "股價淨值比"], "0"
                    ),
                    "DividendYield": first_non_empty(
                        row, ["YieldRatio", "DividendYield", "殖利率(%)"], "0"
                    ),
                }
            return out
        except Exception:
            _time.sleep(3)
    return {}


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


def _get_trend_map(code_suffix: dict):
    """以 yfinance 批量抓近 3 個月日 K，計算每檔多日趨勢指標。

    回傳 {code: {above_ma20, ma20_rising, up_days, ret20}}；失敗的檔略過。
    """
    import yfinance as yf
    out = {}
    if not code_suffix:
        return out
    tickers = [f"{c}{sfx}" for c, sfx in code_suffix.items()]
    df = yf.download(
        " ".join(tickers), period="3mo", interval="1d",
        progress=False, auto_adjust=False, group_by="ticker", threads=True,
    )
    for c, sfx in code_suffix.items():
        t = f"{c}{sfx}"
        try:
            closes = df[t]["Close"].dropna()
            if len(closes) < 21:
                continue
            ma20 = closes.rolling(20).mean()
            up_days = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes.iloc[i] > closes.iloc[i - 1]:
                    up_days += 1
                else:
                    break
            out[c] = {
                "above_ma20": bool(closes.iloc[-1] > ma20.iloc[-1]),
                "ma20_rising": bool(ma20.iloc[-1] > ma20.iloc[-6]),
                "up_days": up_days,
                "ret20": float((closes.iloc[-1] / closes.iloc[-21] - 1) * 100),
            }
        except Exception:
            continue
    return out


def _apply_trend(row, tr):
    """把多日趨勢納入訊號與勝率。

    - 紅燈(強勢股)需站上月線(MA20)確認，否則降為🟡轉折點
    - 勝率依月線位置/月線方向/連漲天數加減分
    - 產出趨勢說明文字附在明細列
    """
    if not tr:
        return row
    above, rising = tr["above_ma20"], tr["ma20_rising"]

    score = row["win_rate"]
    score += 5 if above else -5
    score += 3 if rising else -3
    if tr["up_days"] >= 3:
        score += 3
    row["win_rate"] = int(clamp(round(score), 35, 92))

    if row["signal"] == "強勢股" and not above:
        row["signal"] = "轉折點"
        row["emoji"] = "🟡"
        row["reason"] = "動能強但未站上月線"

    parts = [f"月線{'上' if above else '下'}({'升' if rising else '降'})"]
    if tr["up_days"] >= 2:
        parts.append(f"連漲{tr['up_days']}日")
    parts.append(f"20日{tr['ret20']:+.1f}%")
    row["trend"] = "／".join(parts)
    return row


def get_stocks():
    import sys
    try:
        price_rows = fetch_twse_stock_day_all()
        val_rows = fetch_twse_bwibbu_all()

        price_index = build_index_by_code(price_rows)
        val_index = build_index_by_code(val_rows)

        # 上櫃股票補充資料源（清單中若有上市查不到的代號才需要）
        tpex_price = {}
        tpex_val = {}
        missing = [s["code"] for s in STOCKS if s["code"] not in price_index]
        if missing:
            try:
                tpex_price = fetch_tpex_price_index()
                tpex_val = fetch_tpex_valuation_index()
            except Exception as e:
                print(f"⚠️ TPEx 上櫃資料取得失敗: {type(e).__name__}", file=sys.stderr)

        # 多日趨勢（yfinance 批量日 K；上市 .TW／上櫃 .TWO）
        trend_map = {}
        try:
            code_suffix = {
                s["code"]: (".TWO" if s["code"] not in price_index and s["code"] in tpex_price else ".TW")
                for s in STOCKS
            }
            trend_map = _get_trend_map(code_suffix)
        except Exception as e:
            print(f"⚠️ 趨勢資料取得失敗（僅用當日訊號）: {type(e).__name__}", file=sys.stderr)

        result = []
        for s in STOCKS:
            code = s["code"]
            name = s["name"]
            price_row = price_index.get(code) or tpex_price.get(code) or {}
            val_row = val_index.get(code) or tpex_val.get(code) or {}

            if not price_row:
                row = {
                    "code": code,
                    "name": name,
                    "price": "--",
                    "change_pct": 0.0,
                    "signal": "資料取得中",
                    "reason": "無當日資料",
                    "win_rate": 50,
                    "emoji": "⚪",
                    "volume": 0,
                    "pe": 0,
                    "pb": 0,
                    "yield": 0,
                }
            else:
                row = analyze_stock_with_twse(code, name, price_row, val_row)
                row = _apply_trend(row, trend_map.get(code))

            row["industry"] = s.get("industry", "")
            row["position"] = s.get("position", "")
            result.append(row)

        return result

    except Exception as e:
        return [{
            "code": s["code"],
            "name": s["name"],
            "industry": s.get("industry", ""),
            "position": s.get("position", ""),
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
# 🌏 國際大盤指數
# =========================
def get_indices():
    import sys
    result = []
    try:
        import yfinance as yf
        for idx in INDICES:
            try:
                ticker = yf.Ticker(idx['symbol'])
                info = ticker.info if hasattr(ticker, 'info') else {}

                current_price = to_float(info.get("currentPrice") or info.get("regularMarketPrice"), 0)
                change = to_float(info.get("regularMarketChange", 0), 0)
                change_pct = to_float(info.get("regularMarketChangePercent", 0), 0)

                emoji = "🔴" if change >= 0 else "🔻"

                result.append({
                    "name": idx["name"],
                    "symbol": idx["symbol"],
                    "price": f"{current_price:,.0f}" if current_price else "--",
                    "change": f"{change:+.0f}" if change else "--",
                    "change_pct": round(change_pct, 2),
                    "emoji": emoji,
                })
            except Exception as e:
                print(f"⚠️ 無法取得 {idx['name']} 數據: {type(e).__name__}", file=sys.stderr)
                result.append({
                    "name": idx["name"],
                    "symbol": idx["symbol"],
                    "price": "--",
                    "change": "--",
                    "change_pct": 0.0,
                    "emoji": "⚪",
                })
    except ImportError:
        print("⚠️ yfinance 模組未安裝，使用預設值", file=sys.stderr)
        for idx in INDICES:
            result.append({
                "name": idx["name"],
                "symbol": idx["symbol"],
                "price": "--",
                "change": "--",
                "change_pct": 0.0,
                "emoji": "⚪",
            })

    return result


# =========================
# 😱 CNN 恐懼與貪婪指數 (Fear & Greed Index)
# =========================
def _fng_label(rating: str, score: float):
    """將 CNN 英文 rating 轉成中文標籤與對應 emoji。"""
    rating = (rating or "").strip().lower()
    mapping = {
        "extreme fear": ("極度恐懼", "🟥"),
        "fear": ("恐懼", "🟧"),
        "neutral": ("中性", "🟨"),
        "greed": ("貪婪", "🟩"),
        "extreme greed": ("極度貪婪", "🟦"),
    }
    if rating in mapping:
        return mapping[rating]

    # 沒有 rating 時依分數判斷
    if score <= 25:
        return ("極度恐懼", "🟥")
    if score <= 45:
        return ("恐懼", "🟧")
    if score <= 55:
        return ("中性", "🟨")
    if score <= 75:
        return ("貪婪", "🟩")
    return ("極度貪婪", "🟦")


def get_fear_greed():
    """取得 CNN Fear & Greed Index（美股市場情緒指標）。"""
    import sys
    fng_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://edition.cnn.com/markets/fear-and-greed",
        "Origin": "https://edition.cnn.com",
    }
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=fng_headers,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        fng = data.get("fear_and_greed", {})

        score = to_float(fng.get("score"), 0.0)
        rating = fng.get("rating", "")
        label, emoji = _fng_label(rating, score)

        prev_close = to_float(fng.get("previous_close"), 0.0)
        prev_week = to_float(fng.get("previous_1_week"), 0.0)
        prev_month = to_float(fng.get("previous_1_month"), 0.0)

        return {
            "ok": True,
            "name": "CNN 恐懼與貪婪指數",
            "score": int(round(score)),
            "label": label,
            "emoji": emoji,
            "prev_close": int(round(prev_close)) if prev_close else None,
            "prev_week": int(round(prev_week)) if prev_week else None,
            "prev_month": int(round(prev_month)) if prev_month else None,
        }
    except Exception as e:
        print(f"⚠️ 無法取得 CNN 恐懼與貪婪指數: {type(e).__name__}", file=sys.stderr)
        return {
            "ok": False,
            "name": "CNN 恐懼與貪婪指數",
            "score": "--",
            "label": "資料取得中",
            "emoji": "⚪",
            "prev_close": None,
            "prev_week": None,
            "prev_month": None,
        }


# =========================
# 🇹🇼 台股恐懼與貪婪指數（校準版，貼近 MacroMicro MM 指數）
# =========================
# 說明：MacroMicro 官方 MM 指數在雲端 IP 會被 Cloudflare 擋下，無法直接取得。
# 因此以加權指數（^TWII，yfinance 免費資料）建構特徵，並以「線性回歸」對
# MacroMicro 台灣 MM 恐懼與貪婪指數（近 3 年、726 個交易日）做一次性校準，
# 得到下列係數（標準化線性模型，R²≈0.80、平均絕對誤差≈6.4）。
# 雲端只需 yfinance 即可套用，方向與量級貼近 MM；屬估計值，非 MM 官方數字。
TW_FNG_FEATURES = ["r1", "r5", "r10", "r20", "rsi", "ma60", "vol20", "volratio"]
TW_FNG_INTERCEPT = 53.631665
TW_FNG_COEF = [0.225492, 3.129087, 2.878829, 1.633002, 7.349767, 5.290639, -3.656927, -0.248778]
TW_FNG_MU = [0.00141363, 0.00729884, 0.01454393, 0.02880505, 58.84206389, 0.03915552, 0.01218958, 0.94874548]
TW_FNG_SD = [0.01387929, 0.02955986, 0.04106744, 0.0588246, 16.23967955, 0.0578041, 0.00638906, 0.47446619]


def _twii_features(close):
    """由 ^TWII 收盤序列建構與校準時相同的特徵 DataFrame。"""
    import pandas as pd
    ret = close.pct_change()
    f = pd.DataFrame(index=close.index)
    f["r1"] = ret
    f["r5"] = close.pct_change(5)
    f["r10"] = close.pct_change(10)
    f["r20"] = close.pct_change(20)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    f["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
    f["ma60"] = close / close.rolling(60).mean() - 1
    f["vol20"] = ret.rolling(20).std()
    f["volratio"] = ret.rolling(5).std() / ret.rolling(60).std().replace(0, 1e-9)
    return f


def get_tw_fear_greed():
    """計算貼近 MacroMicro 的台股恐懼與貪婪指數，並取加權指數最新收盤與漲跌。

    以 ^TWII 特徵套用已對 MM 校準的線性模型，輸出 0–100 分數。
    完全使用 yfinance，GitHub Actions 可穩定自動運行。
    """
    import sys
    fail = {
        "ok": False,
        "name": "台股恐懼與貪婪指數",
        "score": "--",
        "label": "資料取得中",
        "emoji": "⚪",
        "prev": None,
        "prev_label": "",
        "date": "",
        "taiex": "--",
        "taiex_change": "--",
        "taiex_pct": 0.0,
        "taiex_emoji": "⚪",
    }
    try:
        import yfinance as yf

        df = yf.Ticker("^TWII").history(period="2y", auto_adjust=False)
        if df is None or df.empty or "Close" not in df:
            raise ValueError("無 ^TWII 歷史資料")

        close = df["Close"].dropna()
        if len(close) < 70:
            raise ValueError("^TWII 歷史資料不足")

        feat = _twii_features(close)

        # 套用標準化線性模型：score = intercept + Σ coef * (x - mu) / sd
        score = feat[TW_FNG_FEATURES].copy()
        for i, col in enumerate(TW_FNG_FEATURES):
            score[col] = (score[col] - TW_FNG_MU[i]) / TW_FNG_SD[i] * TW_FNG_COEF[i]
        score_series = (TW_FNG_INTERCEPT + score.sum(axis=1)).clip(0, 100)
        score_series = score_series.dropna()
        if score_series.empty:
            raise ValueError("指標計算資料不足")

        fng_val = float(score_series.iloc[-1])
        fng_prev_val = float(score_series.iloc[-2]) if len(score_series) >= 2 else None

        label, emoji = _fng_label("", fng_val)
        prev_label = _fng_label("", fng_prev_val)[0] if fng_prev_val is not None else ""

        taiex_now = float(close.iloc[-1])
        taiex_prev = float(close.iloc[-2]) if len(close) >= 2 else taiex_now
        taiex_change = taiex_now - taiex_prev
        taiex_pct = (taiex_change / taiex_prev * 100) if taiex_prev else 0.0
        fng_date = close.index[-1].strftime("%Y-%m-%d")

        return {
            "ok": True,
            "name": "台股恐懼與貪婪指數",
            "score": int(round(fng_val)),
            "label": label,
            "emoji": emoji,
            "prev": int(round(fng_prev_val)) if fng_prev_val is not None else None,
            "prev_label": prev_label,
            "date": fng_date,
            "taiex": f"{taiex_now:,.0f}" if taiex_now else "--",
            "taiex_change": f"{taiex_change:+,.0f}" if taiex_change else "0",
            "taiex_pct": round(taiex_pct, 2),
            "taiex_emoji": "🔴" if taiex_change >= 0 else "🔻",
        }
    except Exception as e:
        print(f"⚠️ 無法計算台股恐懼與貪婪指數: {type(e).__name__}: {e}", file=sys.stderr)
        return fail




# =========================
# 🇺🇸 美國消費者物價指數 CPI（資料來源：FRED / 美國勞工統計局）
# =========================
_FRED_CACHE = {}


def _fred_observations(series_id: str, tries: int = 4):
    """從 FRED 取得完整時間序列，回傳 [(YYYY-MM-DD, value), ...]（升冪、免 API key）。

    含記憶體快取與重試，降低 FRED 偶發逾時造成的失敗。
    """
    import io
    import csv as _csv
    import time as _time

    if series_id in _FRED_CACHE:
        return _FRED_CACHE[series_id]

    # 只抓近 5 年資料（用 cosd 起始日）：避免 T10Y2Y/VIXCLS 等超長日序列
    # 整包 CSV 過大導致下載逾時；5 年足夠計算年增率與趨勢。
    from datetime import datetime as _dt
    cosd = f"{_dt.now().year - 5}-01-01"
    url = (
        f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={cosd}"
    )
    last_err = None
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
            r.raise_for_status()
            data = []
            rows = list(_csv.reader(io.StringIO(r.text)))
            for row in rows[1:]:
                if len(row) < 2:
                    continue
                date, val = row[0].strip(), row[-1].strip()
                if val in (".", "", "NA"):
                    continue
                try:
                    data.append((date, float(val)))
                except ValueError:
                    continue
            _FRED_CACHE[series_id] = data
            return data
        except Exception as e:
            last_err = e
            _time.sleep(2)
    raise last_err


def _fred_series(series_id: str):
    """回傳 {YYYY-MM: value} 的月度 dict（取每月最後一筆）。"""
    out = {}
    for date, val in _fred_observations(series_id):
        out[date[:7]] = val
    return out


def _fred_latest(series_id: str):
    """回傳最新一筆 (date, value)；無資料則 (None, None)。"""
    obs = _fred_observations(series_id)
    return obs[-1] if obs else (None, None)


def _yoy(series: dict, ym: str):
    """計算某月（YYYY-MM）相對 12 個月前的年增率 %。"""
    y, m = ym.split("-")
    prev_key = f"{int(y) - 1}-{m}"
    if ym in series and prev_key in series and series[prev_key]:
        return (series[ym] / series[prev_key] - 1) * 100
    return None


def get_us_cpi():
    """取得最新一期美國 CPI 年增率（實際值 vs 前值）與核心 CPI、月增率。"""
    import sys
    fail = {
        "ok": False,
        "month_label": "",
        "headline_actual": None,
        "headline_prev": None,
        "headline_mom": None,
        "headline_mom_prev": None,
        "core_actual": None,
        "core_prev": None,
        "rising": None,
        "source": "FRED",
    }
    try:
        head = _fred_series("CPIAUCNS")    # 全項 CPI（未季調，用於年增率）
        if not head:
            raise ValueError("無 CPI 資料")
        months = sorted(head.keys())
        last_m = months[-1]
        prev_m = months[-2]

        headline_actual = _yoy(head, last_m)
        headline_prev = _yoy(head, prev_m)
        headline_mom = (head[last_m] / head[prev_m] - 1) * 100 if head.get(prev_m) else None
        prev2_m = months[-3] if len(months) >= 3 else None
        headline_mom_prev = (
            (head[prev_m] / head[prev2_m] - 1) * 100
            if prev2_m and head.get(prev2_m) else None
        )

        # 核心 CPI（未季調）
        core_actual = core_prev = None
        try:
            core = _fred_series("CPILFENS")
            if core:
                core_actual = _yoy(core, last_m)
                core_prev = _yoy(core, prev_m)
        except Exception:
            pass

        y, m = last_m.split("-")
        rising = (
            headline_actual is not None and headline_prev is not None
            and headline_actual > headline_prev
        )

        return {
            "ok": True,
            "month_label": f"{y}年{int(m)}月",
            "headline_actual": round(headline_actual, 2) if headline_actual is not None else None,
            "headline_prev": round(headline_prev, 2) if headline_prev is not None else None,
            "headline_mom": round(headline_mom, 2) if headline_mom is not None else None,
            "headline_mom_prev": round(headline_mom_prev, 2) if headline_mom_prev is not None else None,
            "core_actual": round(core_actual, 2) if core_actual is not None else None,
            "core_prev": round(core_prev, 2) if core_prev is not None else None,
            "rising": rising,
            "source": "FRED",
        }
    except Exception as e:
        print(f"⚠️ 無法取得美國 CPI: {type(e).__name__}: {e}", file=sys.stderr)
        return fail


# =========================
# 🏦 美國 Fed 利率決策 / PCE / 非農就業與失業率 / 10年期公債
# 資料來源：FRED（DFEDTARU/DFEDTARL / PCEPI/PCEPILFE / PAYEMS/UNRATE / DGS10）
# =========================
def _fred_last_change(obs):
    """在 (日期, 值) 序列中找最後一次數值變動，回傳 (現值, 前值, 變動日 YYYY-MM-DD)。"""
    if not obs:
        return None, None, None
    cur = obs[-1][1]
    for i in range(len(obs) - 1, 0, -1):
        if obs[i][1] != cur:
            return cur, obs[i][1], obs[i + 1][0]
    return cur, cur, obs[0][0]


def get_us_econ():
    """取得 Fed 利率決策、PCE、非農就業與失業率、10年期公債殖利率。

    每項獨立抓取，單一失敗只略過該項，不影響其他項與整體輸出。
    """
    import sys
    e = {"ok": False}

    # Fed 目標利率區間 + 最近一次決策
    try:
        up = _fred_observations("DFEDTARU")
        lo = _fred_observations("DFEDTARL")
        if up and lo:
            e["fed_upper"] = up[-1][1]
            e["fed_lower"] = lo[-1][1]
            _, prev_up, chg_date = _fred_last_change(up)
            e["fed_prev_upper"] = prev_up
            e["fed_change_date"] = chg_date
            e["fed_delta_yards"] = round((up[-1][1] - prev_up) / 0.25) if prev_up is not None else 0
    except Exception as ex:
        print(f"⚠️ Fed利率取得失敗: {type(ex).__name__}: {ex}", file=sys.stderr)

    # PCE 年增率（總項 + 核心）
    try:
        pce = {d[:7]: v for d, v in _fred_observations("PCEPI")}
        pm = sorted(pce)
        e["pce_actual"], e["pce_prev"] = _yoy(pce, pm[-1]), _yoy(pce, pm[-2])
        e["pce_month"] = pm[-1]
    except Exception as ex:
        print(f"⚠️ PCE取得失敗: {type(ex).__name__}: {ex}", file=sys.stderr)
    try:
        cpce = {d[:7]: v for d, v in _fred_observations("PCEPILFE")}
        cm = sorted(cpce)
        e["core_pce_actual"], e["core_pce_prev"] = _yoy(cpce, cm[-1]), _yoy(cpce, cm[-2])
    except Exception as ex:
        print(f"⚠️ 核心PCE取得失敗: {type(ex).__name__}: {ex}", file=sys.stderr)

    # 非農就業（月增，千人）
    try:
        pay = _fred_observations("PAYEMS")
        if len(pay) >= 3:
            e["nfp_actual"] = pay[-1][1] - pay[-2][1]
            e["nfp_prev"] = pay[-2][1] - pay[-3][1]
            e["nfp_month"] = pay[-1][0][:7]
    except Exception as ex:
        print(f"⚠️ 非農就業取得失敗: {type(ex).__name__}: {ex}", file=sys.stderr)

    # 失業率
    try:
        ur = _fred_observations("UNRATE")
        if len(ur) >= 2:
            e["ur_actual"], e["ur_prev"] = ur[-1][1], ur[-2][1]
    except Exception as ex:
        print(f"⚠️ 失業率取得失敗: {type(ex).__name__}: {ex}", file=sys.stderr)

    # 10 年期公債殖利率
    try:
        t10 = _fred_observations("DGS10")
        if len(t10) >= 2:
            e["t10"], e["t10_prev"] = t10[-1][1], t10[-2][1]
            e["t10_chg_bp"] = (t10[-1][1] - t10[-2][1]) * 100
    except Exception as ex:
        print(f"⚠️ 10年期公債取得失敗: {type(ex).__name__}: {ex}", file=sys.stderr)

    e["ok"] = any(k in e for k in ("fed_upper", "pce_actual", "nfp_actual", "ur_actual", "t10"))
    return e


def build_us_econ_lines(e):
    """組裝【美國 Fed 與經濟數據】輸出文字。"""
    if not e.get("ok"):
        return ["資料取得中"]
    lines = []

    if e.get("fed_upper") is not None:
        chg = ""
        yards = e.get("fed_delta_yards", 0)
        if e.get("fed_prev_upper") is not None and yards:
            arrow = "↓" if yards < 0 else "↑"
            pu = e["fed_prev_upper"]
            d = e.get("fed_change_date", "")
            dstr = f"{d[5:7]}/{d[8:10]}" if len(d) >= 10 else d
            chg = f"（前次{pu - 0.25:.2f}%~{pu:.2f}%，{arrow}{abs(yards)}碼 {dstr}）"
        lines.append(f"Fed利率目標：{e['fed_lower']:.2f}%~{e['fed_upper']:.2f}%{chg}")

    if e.get("pce_actual") is not None and e.get("pce_prev") is not None:
        lines.append(f"PCE年增率：實際 {e['pce_actual']:.2f}%｜前值 {e['pce_prev']:.2f}%")
    if e.get("core_pce_actual") is not None and e.get("core_pce_prev") is not None:
        lines.append(f"核心PCE年增率：實際 {e['core_pce_actual']:.2f}%｜前值 {e['core_pce_prev']:.2f}%")

    if e.get("nfp_actual") is not None:
        lines.append(f"非農就業(新增)：實際 {e['nfp_actual']:+,.0f}千人｜前值 {e['nfp_prev']:+,.0f}千人")
    if e.get("ur_actual") is not None:
        lines.append(f"失業率：實際 {e['ur_actual']:.1f}%｜前值 {e['ur_prev']:.1f}%")

    if e.get("t10") is not None:
        lines.append(f"10年期公債殖利率：{e['t10']:.2f}%（較前日 {e['t10_chg_bp']:+.1f}bp）")

    return lines if lines else ["資料取得中"]


# =========================
# 🌐 美國景氣風險指標（失業率-CPI + 五大總經風險儀表板）
# 資料來源：FRED（UNRATE / CPIAUCNS / T10Y2Y / USALOLITOAASTSAM / BAMLH0A0HYM2 / VIXCLS）
# =========================
_RISK_COLORS = ["🟢", "🟡", "🟠", "🔴"]
_RISK_NAMES = ["安全", "注意", "警戒", "高風險"]


def _consecutive_declines(values):
    """從序列尾端往前算連續下降的期數。"""
    dec = 0
    for i in range(len(values) - 1, 0, -1):
        if values[i] < values[i - 1]:
            dec += 1
        else:
            break
    return dec


def get_macro_risk():
    """計算美國景氣與五大總經風險指標，回傳各指標數值、風險等級與綜合分數。

    每個指標獨立抓取，單一資料源失敗只會讓該指標顯示「暫無資料」，
    不影響其他指標與整體輸出。
    """
    import sys
    m = {
        "ok": False,
        "unrate": None, "cpi_yoy": None, "spread": None, "l1": None,
        "curve": None, "l2": None,
        "lei_yoy": None, "lei_dec": None, "l3": None,
        "hy": None, "l4": None,
        "vix": None, "l5": None,
        "total": 0, "max_total": 0, "zone": 0, "n_avail": 0,
    }

    # 指標1：失業率 − CPI 年增率
    try:
        unrate = _fred_latest("UNRATE")[1]
        cpi = _fred_series("CPIAUCNS")
        cpi_yoy = _yoy(cpi, sorted(cpi)[-1])
        if unrate is not None and cpi_yoy is not None:
            spread = unrate - cpi_yoy
            m["unrate"], m["cpi_yoy"], m["spread"] = unrate, cpi_yoy, spread
            if spread > 2:
                m["l1"] = 0
            elif spread >= 0.5:
                m["l1"] = 1
            elif spread >= 0:
                m["l1"] = 2
            else:
                m["l1"] = 3
    except Exception as e:
        print(f"⚠️ 失業率-CPI 取得失敗: {type(e).__name__}: {e}", file=sys.stderr)

    # 指標2：10Y−2Y 殖利率曲線
    try:
        curve = _fred_latest("T10Y2Y")[1]
        if curve is not None:
            m["curve"] = curve
            m["l2"] = 0 if curve > 1 else (1 if curve >= 0 else 3)
    except Exception as e:
        print(f"⚠️ 10Y-2Y 取得失敗: {type(e).__name__}: {e}", file=sys.stderr)

    # 指標3：領先指標（OECD 美國綜合領先指標 CLI，振幅調整，代理 Conference Board LEI）
    try:
        lei_obs = _fred_observations("USALOLITOAASTSAM")
        if lei_obs:
            lei_vals = [v for _, v in lei_obs]
            lei_dict = {d[:7]: v for d, v in lei_obs}
            lei_yoy = _yoy(lei_dict, sorted(lei_dict)[-1])
            lei_dec = _consecutive_declines(lei_vals)
            m["lei_yoy"], m["lei_dec"] = lei_yoy, lei_dec
            if lei_yoy is not None and lei_yoy < -4:
                m["l3"] = 3
            elif lei_dec >= 6:
                m["l3"] = 2
            elif lei_dec >= 3:
                m["l3"] = 1
            else:
                m["l3"] = 0
    except Exception as e:
        print(f"⚠️ 領先指標取得失敗: {type(e).__name__}: {e}", file=sys.stderr)

    # 指標4：高收益債利差
    try:
        hy = _fred_latest("BAMLH0A0HYM2")[1]
        if hy is not None:
            m["hy"] = hy
            m["l4"] = 0 if hy < 4 else (1 if hy < 6 else (2 if hy < 8 else 3))
    except Exception as e:
        print(f"⚠️ 高收益債利差取得失敗: {type(e).__name__}: {e}", file=sys.stderr)

    # 指標5：VIX 恐慌指數
    try:
        vix = _fred_latest("VIXCLS")[1]
        if vix is not None:
            m["vix"] = vix
            m["l5"] = 0 if vix < 20 else (1 if vix < 30 else (2 if vix < 40 else 3))
    except Exception as e:
        print(f"⚠️ VIX 取得失敗: {type(e).__name__}: {e}", file=sys.stderr)

    # 綜合評分（只計可用指標；全部可用時用使用者指定的 0-15 分級）
    levels = [m[k] for k in ("l1", "l2", "l3", "l4", "l5") if m[k] is not None]
    n = len(levels)
    m["n_avail"] = n
    if n:
        m["ok"] = True
        total = sum(levels)
        m["total"], m["max_total"] = total, 3 * n
        if n == 5:
            m["zone"] = 0 if total <= 3 else (1 if total <= 6 else (2 if total <= 10 else 3))
        else:
            ratio = total / (3 * n)
            m["zone"] = 0 if ratio <= 0.2 else (1 if ratio <= 0.4 else (2 if ratio <= 0.67 else 3))
    return m


def build_business_cycle_lines(m):
    """組裝【失業率－CPI景氣指標】輸出文字。"""
    if m.get("l1") is None or m.get("spread") is None:
        return ["資料取得中"]
    l1, s = m["l1"], m["spread"]
    hist = {
        0: "差值偏高，經濟偏冷、通膨受控，聯準會具降息空間，對股市偏正面。",
        1: "成熟擴張期，開始留意過熱風險，對股市中性偏多。",
        2: "差值接近0，歷史上常見於景氣循環後段，需提高警覺、留意聯準會轉向。",
        3: "通膨高於失業率，歷史上曾見於1968/1973/1990/2000/2007/2021等重大修正或衰退前夕，高風險警訊。",
    }[l1]
    us = {
        0: "標普500/NASDAQ/AI股：流動性偏寬，偏多。",
        1: "標普500/NASDAQ/AI股：中性偏多，留意評價。",
        2: "標普500/NASDAQ/AI股：審慎，防禦類股相對抗跌。",
        3: "標普500/NASDAQ/AI股：高波動風險，宜降低部位。",
    }[l1]
    tw = {
        0: "加權/台積電/AI供應鏈：資金行情有利，偏多。",
        1: "加權/台積電/AI供應鏈：中性偏多，分批操作。",
        2: "加權/台積電/AI供應鏈：留意外資動向，控管部位。",
        3: "加權/台積電/AI供應鏈：景氣後段，提高警覺。",
    }[l1]
    concl = {
        0: f"差值{s:.2f}%，高於警戒區，未見高風險訊號，可偏積極。",
        1: f"差值{s:.2f}%，成熟擴張，中性偏多。",
        2: f"差值{s:.2f}%，接近警戒區，宜觀望、提高警覺。",
        3: f"差值{s:.2f}%，已翻負，歷史高風險訊號，宜防守減碼。",
    }[l1]
    return [
        f"最新失業率：{m['unrate']:.1f}%",
        f"最新CPI年增率：{m['cpi_yoy']:.2f}%",
        f"差值：{s:.2f}%",
        f"風險等級：{_RISK_COLORS[l1]} {_RISK_NAMES[l1]}",
        f"歷史位置：{hist}",
        f"對美股：{us}",
        f"對台股：{tw}",
        f"一句話結論：{concl}",
    ]


def build_risk_dashboard_lines(m):
    """組裝【全球股市風險儀表板】輸出文字。"""
    if not m.get("ok"):
        return ["資料取得中"]

    def row(label, valtext, level):
        if level is None:
            return f"{label}：暫無資料 ⚪"
        return f"{label}：{valtext} {_RISK_COLORS[level]} {_RISK_NAMES[level]}"

    if m["lei_yoy"] is not None:
        lei_yoy_txt = f"YoY {m['lei_yoy']:+.1f}%"
        if m["lei_dec"] and m["lei_dec"] >= 3:
            lei_yoy_txt += f"／連跌{m['lei_dec']}月"
    else:
        lei_yoy_txt = "—"

    lines = [
        row("失業率-CPI", f"{m['spread']:.2f}%" if m["spread"] is not None else "—", m["l1"]),
        row("10Y-2Y利差", f"{m['curve']:.2f}%" if m["curve"] is not None else "—", m["l2"]),
        row("領先指標(OECD CLI)", lei_yoy_txt, m["l3"]),
        row("高收益債利差", f"{m['hy']:.2f}%" if m["hy"] is not None else "—", m["l4"]),
        row("VIX恐慌指數", f"{m['vix']:.1f}" if m["vix"] is not None else "—", m["l5"]),
    ]
    zone = m["zone"]
    zone_name = ["牛市區", "正常區", "警戒區", "高風險區"][zone]
    avail_note = "" if m["n_avail"] == 5 else f"（{m['n_avail']}/5項）"
    lines.append(
        f"風險分數：{m['total']}/{m['max_total']}{avail_note}　燈號：{_RISK_COLORS[zone]} {zone_name}"
    )
    us = {
        0: "美股：SP500/NASDAQ/費半/AI偏多，可進攻。",
        1: "美股：SP500/NASDAQ/費半/AI中性偏多。",
        2: "美股：SP500/NASDAQ/費半/AI審慎，留意回檔。",
        3: "美股：SP500/NASDAQ/費半/AI高風險，宜防守。",
    }[zone]
    tw = {
        0: "台股：加權/台積電/AI供應鏈/高股息ETF偏多。",
        1: "台股：加權/台積電/AI供應鏈中性偏多，高股息ETF防禦。",
        2: "台股：加權審慎，側重高股息ETF防禦。",
        3: "台股：加權高風險，提高現金、側重高股息ETF。",
    }[zone]
    concl = {0: "進攻", 1: "正常持股", 2: "觀望", 3: "防守減碼"}[zone]
    lines.append(us)
    lines.append(tw)
    lines.append(f"一句話結論：風險分數{m['total']}/{m['max_total']}，建議「{concl}」。")
    return lines


# =========================
# 🎯 即將出關處置股（主力/三大法人買超）— TWSE 官方資料
# =========================
# 「即將出關」定義：最後一筆處置迄日落在今日起算 N 天內
DISPOSITION_RELEASE_WINDOW_DAYS = 3


def _roc_to_date(s: str):
    """民國日期字串（如 115/06/26）轉成西元 date。"""
    from datetime import date as _date
    parts = (s or "").strip().split("/")
    if len(parts) != 3:
        return None
    try:
        return _date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
    except Exception:
        return None


def _get_latest_t86():
    """取最近一個交易日的三大法人買賣超，回傳 ({code: 淨買賣超股數}, 日期字串)。"""
    from datetime import datetime as _dt, timedelta as _td
    today = _dt.now().date()
    for i in range(7):
        ymd = (today - _td(days=i)).strftime("%Y%m%d")
        url = (
            f"https://www.twse.com.tw/rwd/zh/fund/T86"
            f"?date={ymd}&selectType=ALL&response=json"
        )
        try:
            j = fetch_json(url, timeout=25)
            if j.get("stat") == "OK" and j.get("data"):
                out = {}
                for r in j["data"]:
                    code = (r[0] or "").strip()
                    out[code] = to_float(r[-1], 0.0)  # 末欄＝三大法人買賣超股數
                return out, f"{ymd[4:6]}/{ymd[6:8]}"
        except Exception:
            continue
    return {}, ""


def get_disposition_watch():
    """即將出關且主力（三大法人）買超的上市處置股。"""
    import sys
    from datetime import datetime as _dt
    try:
        punish = fetch_json(
            "https://openapi.twse.com.tw/v1/announcement/punish", timeout=25
        )
    except Exception as e:
        print(f"⚠️ 無法取得處置股清單: {type(e).__name__}: {e}", file=sys.stderr)
        return {"ok": False, "items": [], "note": "資料取得中"}

    today = _dt.now().date()
    # 同一檔可能有多筆處置公告，取最晚迄日（真正出關日）
    ends = {}
    for row in punish:
        code = (row.get("Code") or "").strip()
        if not (len(code) == 4 and code.isdigit()):  # 僅上市股票，排除權證
            continue
        period = row.get("DispositionPeriod", "")
        end_s = re.split(r"[～~]", period)[-1].strip() if period else ""
        end_d = _roc_to_date(end_s)
        if not end_d:
            continue
        name = (row.get("Name") or "").strip()
        if code not in ends or end_d > ends[code][1]:
            ends[code] = (name, end_d)

    upcoming = []
    for code, (name, end_d) in ends.items():
        days = (end_d - today).days
        if 0 <= days <= DISPOSITION_RELEASE_WINDOW_DAYS:
            upcoming.append({"code": code, "name": name, "end": end_d, "days": days})

    if not upcoming:
        return {"ok": True, "items": [], "note": "今日無即將出關的處置股"}

    t86, t86_date = _get_latest_t86()
    if not t86:
        return {
            "ok": True, "items": [], "upcoming_count": len(upcoming),
            "note": "即將出關 %d 檔，惟主力(法人)資料暫缺" % len(upcoming),
        }

    items = []
    for u in upcoming:
        net = t86.get(u["code"])
        if net is not None and net > 0:  # 主力(法人)買大於賣
            u["net_lots"] = net / 1000.0
            items.append(u)
    items.sort(key=lambda x: (x["days"], -x["net_lots"]))

    return {
        "ok": True,
        "items": items[:8],
        "upcoming_count": len(upcoming),
        "t86_date": t86_date,
    }


def build_disposition_lines(d):
    """組裝【即將出關處置股】輸出文字。"""
    if not d.get("ok"):
        return ["資料取得中"]
    items = d.get("items", [])
    if not items:
        return [d.get("note", "今日無即將出關的處置股")]
    lines = []
    for it in items:
        mmdd = it["end"].strftime("%m/%d")
        lines.append(
            f"{it['code']} {it['name']}：處置至 {mmdd}｜主力(法人)買超 +{it['net_lots']:,.0f}張"
        )
    if d.get("t86_date"):
        lines.append(f"（法人資料：{d['t86_date']}；隔日為出關首日）")
    return lines


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
# 📧 個人重要郵件（Gmail IMAP）
# =========================
def decode_mime_words(s):
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="ignore"))
            except Exception:
                out.append(text.decode("utf-8", errors="ignore"))
        else:
            out.append(text)
    return "".join(out).strip()


def extract_email_address(from_text: str) -> str:
    m = re.search(r"<([^>]+)>", from_text or "")
    if m:
        return m.group(1).strip().lower()
    return (from_text or "").strip().lower()


def is_promo_mail(sender: str, subject: str) -> bool:
    text = f"{sender} {subject}".lower()

    promo_keywords = [
        "sale", "promo", "promotion", "newsletter", "discount", "coupon",
        "優惠", "促銷", "折扣", "限時", "特價", "廣告", "電子報",
        "雙11", "雙 11", "免運", "搶購", "週年慶", "black friday",
        "cyber monday", "618", "購物節", "momo", "pchome", "蝦皮"
    ]
    promo_senders = [
        "noreply", "no-reply", "newsletter", "mailer-daemon",
        "marketing", "edm", "notification", "news@", "promo@"
    ]

    if any(k in text for k in promo_keywords):
        return True
    if any(k in text for k in promo_senders):
        return True
    return False


def get_personal_emails(limit: int = 3):
    import os

    email_account = os.environ.get("EMAIL_ACCOUNT", EMAIL_ACCOUNT)
    app_password = os.environ.get("EMAIL_APP_PASSWORD", "")

    if not email_account or not app_password:
        return {
            "enabled": False,
            "items": [],
            "error": "EMAIL_ACCOUNT 或 EMAIL_APP_PASSWORD 未設定",
        }

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_account, app_password)

        # Gmail IMAP 支援 X-GM-RAW，可直接用 Gmail 搜尋語法
        raw_query = (
            'in:inbox newer_than:2d '
            '-category:promotions -category:social -in:spam -in:trash '
            '-from:noreply -from:no-reply -from:newsletter -from:mailer-daemon'
        )

        status, data = mail.uid("SEARCH", "X-GM-RAW", f'"{raw_query}"')
        if status != "OK":
            mail.logout()
            return {
                "enabled": True,
                "items": [],
                "error": "搜尋失敗",
            }

        uids = data[0].split()
        uids = uids[-20:]  # 最近 20 封中挑重要的

        selected = []
        seen = set()

        for uid in reversed(uids):
            status, msg_data = mail.uid("FETCH", uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = decode_mime_words(msg.get("Subject", "（無主旨）"))
            sender = decode_mime_words(msg.get("From", "未知寄件者"))
            sender_email = extract_email_address(sender)

            if is_promo_mail(sender, subject):
                continue

            fp = f"{sender_email}|{subject}".lower()
            if fp in seen:
                continue
            seen.add(fp)

            selected.append({
                "sender": sender,
                "subject": subject or "（無主旨）",
            })

            if len(selected) >= limit:
                break

        mail.logout()

        return {
            "enabled": True,
            "items": selected,
            "error": "",
        }

    except Exception as e:
        return {
            "enabled": True,
            "items": [],
            "error": type(e).__name__,
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
        "note": "勝率＝當日量價＋20日趨勢模型分數，非保證報酬。",
    }


# =========================
# HTML
# =========================
def esc_html(text: str) -> str:
    return html_lib.escape(str(text))


def generate_html():
    weather_list = get_weather_list()
    news = get_all_news()
    stocks = get_stocks()
    indices = get_indices()
    fear_greed = get_fear_greed()
    tw_fear_greed = get_tw_fear_greed()
    us_cpi = get_us_cpi()
    us_econ = get_us_econ()
    econ_lines = build_us_econ_lines(us_econ)
    macro_risk = get_macro_risk()
    bc_lines = build_business_cycle_lines(macro_risk)
    rd_lines = build_risk_dashboard_lines(macro_risk)
    disposition = get_disposition_watch()
    disp_lines = build_disposition_lines(disposition)
    traffic = get_traffic()
    personal_emails = get_personal_emails(limit=3)
    ai_summary = build_ai_summary(stocks)

    fng_parts = []
    if fear_greed.get("prev_close") is not None:
        fng_parts.append(f"昨日 {fear_greed['prev_close']}")
    if fear_greed.get("prev_week") is not None:
        fng_parts.append(f"上週 {fear_greed['prev_week']}")
    if fear_greed.get("prev_month") is not None:
        fng_parts.append(f"上月 {fear_greed['prev_month']}")
    fng_sub = " / ".join(fng_parts) if fng_parts else "0=極度恐懼 100=極度貪婪"

    # 台股 MM 指數副標：前次數值 + 加權指數
    tw_parts = []
    if tw_fear_greed.get("prev") is not None:
        prev_lbl = f"（{tw_fear_greed['prev_label']}）" if tw_fear_greed.get("prev_label") else ""
        tw_parts.append(f"前次 {tw_fear_greed['prev']}{prev_lbl}")
    if tw_fear_greed.get("taiex") not in (None, "--"):
        tw_parts.append(
            f"加權指數 {tw_fear_greed['taiex']} "
            f"{tw_fear_greed['taiex_change']} {tw_fear_greed['taiex_pct']:+.2f}%"
        )
    tw_sub = " ｜ ".join(tw_parts) if tw_parts else "0=極度恐懼 100=極度貪婪"

    # AI 股票洞察：依產業分組輸出
    stock_parts = []
    _cur_industry = None
    for s in stocks:
        industry = s.get("industry", "")
        if industry and industry != _cur_industry:
            _cur_industry = industry
            stock_parts.append(
                f'<div class="task-group"><strong>▍{esc_html(industry)}</strong></div>'
            )
        pos = f"｜{esc_html(s['position'])}" if s.get("position") else ""
        stock_parts.append(
            f'''
      <div class="task-item stock-row">
        <span class="task-name">{esc_html(s["emoji"])} {esc_html(s["name"])}{pos} {s["change_pct"]:+.2f}%｜{esc_html(s["signal"])}｜勝率{s["win_rate"]}%</span>
        <span class="task-meta small">{esc_html(s["reason"])} / PER {esc_html(s["pe"])} / PB {esc_html(s["pb"])} / 殖利率 {esc_html(s["yield"])}%{(" / " + esc_html(s["trend"])) if s.get("trend") else ""}</span>
      </div>
      '''
        )
    stocks_html = "".join(stock_parts)

    # 美國 CPI 顯示文字
    def _pct(v):
        return f"{v:.2f}%" if isinstance(v, (int, float)) else "--"

    if us_cpi.get("ok"):
        cpi_title = f"🇺🇸 美國 CPI（{us_cpi['month_label']}）"
        cpi_arrow = "🔴 較前值上升" if us_cpi.get("rising") else "🟢 較前值回落"
        cpi_lines = [
            f"年增率(YoY)：實際 {_pct(us_cpi['headline_actual'])}｜前值 {_pct(us_cpi['headline_prev'])} {cpi_arrow}",
            f"月增率(MoM)：實際 {_pct(us_cpi['headline_mom'])}｜前值 {_pct(us_cpi['headline_mom_prev'])}",
        ]
        if us_cpi.get("core_actual") is not None:
            cpi_lines.append(
                f"核心年增率：實際 {_pct(us_cpi['core_actual'])}｜前值 {_pct(us_cpi['core_prev'])}"
            )
    else:
        cpi_title = "🇺🇸 美國 CPI"
        cpi_lines = ["資料取得中"]

    html = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>{TODAY} 早報</title>
<style>
body {{ font-family: Arial, "Noto Sans TC", sans-serif; padding: 24px; color: #222; }}
.card {{ border: 1px solid #ddd; border-radius: 16px; padding: 16px; margin-bottom: 16px; }}
.section-title {{ font-size: 18px; font-weight: 700; margin-bottom: 10px; }}
.weather-row, .stock-row, .news-row, .traffic-row, .mail-row {{ margin: 8px 0; }}
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
  {stocks_html}
</div>

<div class="card indices">
  <div class="section-title">🌏 國際大盤指數</div>
  {''.join(
      f'''
      <div class="index-row stock-row">
        <span class="index-name">{esc_html(idx["emoji"])} {esc_html(idx["name"])}：{esc_html(idx["price"])} {idx["change"]} {idx["change_pct"]:+.2f}%</span>
      </div>
      '''
      for idx in indices
  )}
</div>

<div class="card fear-greed">
  <div class="section-title">😱 市場情緒指標</div>
  <div class="fng-row stock-row">
    <span class="fng-name">{esc_html(fear_greed["emoji"])} {esc_html(fear_greed["name"])}：{esc_html(fear_greed["score"])}（{esc_html(fear_greed["label"])}）</span>
    <span class="fng-meta small">{esc_html(fng_sub)}</span>
  </div>
</div>

<div class="card tw-fear-greed">
  <div class="section-title">🇹🇼 台股情緒指標</div>
  <div class="twfng-row stock-row">
    <span class="twfng-name">{esc_html(tw_fear_greed["emoji"])} {esc_html(tw_fear_greed["name"])}：{esc_html(tw_fear_greed["score"])}（{esc_html(tw_fear_greed["label"])}）</span>
    <span class="twfng-meta small">{esc_html(tw_sub)}</span>
  </div>
</div>

<div class="card us-cpi">
  <div class="section-title">{esc_html(cpi_title)}</div>
  {''.join(f'<div class="cpi-row stock-row"><span class="cpi-line">{esc_html(line)}</span></div>' for line in cpi_lines)}
  <div class="cpi-row small">來源：{esc_html(us_cpi.get("source", "FRED"))}</div>
</div>

<div class="card us-econ">
  <div class="section-title">🏦 美國Fed與經濟數據</div>
  {''.join(f'<div class="econ-row stock-row"><span class="econ-line">{esc_html(line)}</span></div>' for line in econ_lines)}
  <div class="econ-row small">來源：FRED</div>
</div>

<div class="card biz-cycle">
  <div class="section-title">📉 失業率－CPI景氣指標</div>
  {''.join(f'<div class="bc-row stock-row"><span class="bc-line">{esc_html(line)}</span></div>' for line in bc_lines)}
</div>

<div class="card risk-dashboard">
  <div class="section-title">🌐 全球股市風險儀表板</div>
  {''.join(f'<div class="rd-row stock-row"><span class="rd-line">{esc_html(line)}</span></div>' for line in rd_lines)}
</div>

<div class="card disposition">
  <div class="section-title">🎯 即將出關處置股（主力買超）</div>
  {''.join(f'<div class="disp-row stock-row"><span class="disp-line">{esc_html(line)}</span></div>' for line in disp_lines)}
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

<div class="card personal-mails">
  <div class="section-title">📧 個人重要郵件</div>
  {
      ''.join(
          f'''
          <div class="mail-row mail-item">
            <span class="mail-sender">{esc_html(m["sender"])}</span>｜
            <span class="mail-subject">{esc_html(m["subject"])}</span>
          </div>
          '''
          for m in personal_emails.get("items", [])
      )
      if personal_emails.get("items") else
      f'<div class="mail-row small">{esc_html(personal_emails.get("error", "目前沒有重要郵件"))}</div>'
  }
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
