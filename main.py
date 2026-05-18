import telebot
import requests
import time
import threading
import logging
from datetime import datetime
import os
# ==================== НАСТРОЙКА ====================
BOT_TOKEN = os.environ.get('8559551745:AAG6Oyoqp6adwd-6kLbrhAdblYOcRTjPGC0')
ADMIN_ID = 8693522887
API_KEY = "NEUGRBMAZ9YL1O2S"

PAIRS = [
    ("EUR", "USD"),
    ("GBP", "USD"),
    ("USD", "JPY"),
    ("USD", "CHF"),
    ("AUD", "USD"),
    ("USD", "CAD"),
    ("NZD", "USD"),
]

CHECK_INTERVAL = 300  # 5 мүнөт
MIN_CONFIRM = 5  # 5/5 индикатор дал келсе гана сигнал

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

# ==================== БААСЫН АЛУу ====================
def get_price_data(from_cur, to_cur):
    try:
        url = (
            "https://www.alphavantage.co/query"
            "?function=FX_INTRADAY"
            "&from_symbol=" + from_cur +
            "&to_symbol=" + to_cur +
            "&interval=5min"
            "&apikey=" + API_KEY +
            "&outputsize=compact"
        )
        response = requests.get(url, timeout=15)
        data = response.json()
        if "Time Series FX (5min)" not in data:
            log.warning("API жооп бербеди: " + str(data))
            return None
        time_series = data["Time Series FX (5min)"]
        closes, highs, lows = [], [], []
        for key in sorted(time_series.keys(), reverse=True)[:50]:
            closes.append(float(time_series[key]["4. close"]))
            highs.append(float(time_series[key]["2. high"]))
            lows.append(float(time_series[key]["3. low"]))
        return list(reversed(closes)), list(reversed(highs)), list(reversed(lows))
    except Exception as e:
        log.error("Баа алууда ката: " + str(e))
        return None

# ==================== ИНДИКАТОРЛОР ====================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_ma(prices, period=20):
    if len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 5)

def calculate_macd(prices, fast=12, slow=26):
    if len(prices) < slow:
        return None, None
    def ema(data, period):
        k = 2 / (period + 1)
        val = data[0]
        for p in data[1:]:
            val = p * k + val * (1 - k)
        return val
    ema_fast = ema(prices[-fast*2:], fast)
    ema_slow = ema(prices[-slow*2:], slow)
    macd_line = round(ema_fast - ema_slow, 6)
    signal_line = round(macd_line * 0.9, 6)
    return macd_line, signal_line

def calculate_bollinger(prices, period=20):
    if len(prices) < period:
        return None, None, None
    ma = sum(prices[-period:]) / period
    variance = sum((p - ma) ** 2 for p in prices[-period:]) / period
    std = variance ** 0.5
    upper = round(ma + 2 * std, 5)
    lower = round(ma - 2 * std, 5)
    return round(ma, 5), upper, lower

def calculate_stochastic(closes, highs, lows, period=14):
    if len(closes) < period:
        return None
    high_max = max(highs[-period:])
    low_min = min(lows[-period:])
    if high_max == low_min:
        return 50
    k = ((closes[-1] - low_min) / (high_max - low_min)) * 100
    return round(k, 2)

# ==================== TP/SL ====================
def calculate_tp_sl(price, direction, from_cur, to_cur):
    pip = 0.01 if "JPY" in (from_cur, to_cur) else 0.0001
    tp_pips = 40
    sl_pips = 20
    if direction == "BUY":
        tp = round(price + (tp_pips * pip), 5)
        sl = round(price - (sl_pips * pip), 5)
    else:
        tp = round(price - (tp_pips * pip), 5)
        sl = round(price + (sl_pips * pip), 5)
    return tp, sl

# ==================== СИГНАЛ АНАЛИЗИ ====================
def analyze_pair(from_cur, to_cur):
    pair = from_cur + "/" + to_cur
    result = get_price_data(from_cur, to_cur)
    if not result:
        return None

    closes, highs, lows = result
    if len(closes) < 26:
        return None

    current_price = closes[-1]

    rsi = calculate_rsi(closes)
    ma10 = calculate_ma(closes, 10)
    ma20 = calculate_ma(closes, 20)
    macd, macd_signal = calculate_macd(closes)
    bb_mid, bb_upper, bb_lower = calculate_bollinger(closes)
    stoch = calculate_stochastic(closes, highs, lows)

    # Ар бир индикатор текшерүү
    checks = {
        "RSI":        None,
        "MA":         None,
        "MACD":       None,
        "Bollinger":  None,
        "Stochastic": None,
    }

    if rsi is not None:
        if rsi < 30:   checks["RSI"] = "BUY"
        elif rsi > 70: checks["RSI"] = "SELL"

    if ma10 and ma20:
        if ma10 > ma20:   checks["MA"] = "BUY"
        elif ma10 < ma20: checks["MA"] = "SELL"

    if macd and macd_signal:
        if macd > macd_signal:   checks["MACD"] = "BUY"
        elif macd < macd_signal: checks["MACD"] = "SELL"

    if bb_upper and bb_lower:
        if current_price < bb_lower:   checks["Bollinger"] = "BUY"
        elif current_price > bb_upper: checks["Bollinger"] = "SELL"

    if stoch is not None:
        if stoch < 20:   checks["Stochastic"] = "BUY"
        elif stoch > 80: checks["Stochastic"] = "SELL"

    # 5/5 тастыктоо
    buy_count  = sum(1 for v in checks.values() if v == "BUY")
    sell_count = sum(1 for v in checks.values() if v == "SELL")

    log.info(pair + " — BUY:" + str(buy_count) + " SELL:" + str(sell_count))

    if buy_count == MIN_CONFIRM:
        direction = "BUY"
    elif sell_count == MIN_CONFIRM:
        direction = "SELL"
    else:
        return None  # 5/5 эмес — сигнал жок

    tp, sl = calculate_tp_sl(current_price, direction, from_cur, to_cur)

    return {
        "pair": pair,
        "price": current_price,
        "direction": direction,
        "tp": tp,
        "sl": sl,
        "rsi": rsi,
        "stoch": stoch,
        "macd": macd,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "checks": checks,
        "time": datetime.now().strftime("%H:%M:%S")
    }

# ==================== ЖӨНӨТҮҮ ====================
def send_signal(s):
    emoji = "🟢" if s["direction"] == "BUY" else "🔴"
    action = "BUY — Сатып АЛ" if s["direction"] == "BUY" else "SELL — Сат"

    # Ар бир индикатордун жыйынтыгы
    ind_lines = ""
    icons = {"BUY": "✅", "SELL": "✅", None: "⬜"}
    names = {
        "RSI": "RSI:         `" + str(s["rsi"]) + "`",
        "MA": "MA(10/20)",
        "MACD": "MACD:        `" + str(s["macd"]) + "`",
        "Bollinger": "Bollinger BB",
        "Stochastic": "Stochastic:  `" + str(s["stoch"]) + "`",
    }
    for k, v in s["checks"].items():
        mark = "✅" if v == s["direction"] else "❌"
        ind_lines += mark + " " + names[k] + "\n"

    msg = (
        emoji + " *" + action + "* " + emoji + "\n"
        "━━━━━━━━━━━━━━━\n"
        "💱 Жуп: *" + s["pair"] + "*\n"
        "💰 Кирүү баасы:  `" + str(s["price"]) + "`\n"
        "🎯 Take Profit:  `" + str(s["tp"]) + "`\n"
        "🛑 Stop Loss:    `" + str(s["sl"]) + "`\n"
        "━━━━━━━━━━━━━━━\n"
        "🔥 *5/5 ИНДИКАТОР ТАСТЫКТАДЫ!*\n"
        "━━━━━━━━━━━━━━━\n"
        + ind_lines +
        "━━━━━━━━━━━━━━━\n"
        "🌐 Alpha Vantage чыныгы баа\n"
        "⏰ " + s["time"] + "\n"
        "⚠️ _Соода тобокелчилиги өз мойнуңузда!_"
    )
    try:
        bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")
    except Exception as e:
        log.error("Жөнөтүүдө ката: " + str(e))

# ==================== АВТО СКАНЕР ====================
def auto_scanner():
    while True:
        log.info("Сканерлөө башталды...")
        for from_cur, to_cur in PAIRS:
            try:
                signal = analyze_pair(from_cur, to_cur)
                if signal:
                    send_signal(signal)
                time.sleep(15)
            except Exception as e:
                log.error(str(e))
        time.sleep(CHECK_INTERVAL)

# ==================== БОТ КОМАНДЫ ====================
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id,
        "📊 *Forex Сигнал Боту* 🚀\n\n"
        "🔥 Сигнал БЕРИ 5/5 индикатор тастыктаганда!\n\n"
        "✅ RSI\n"
        "✅ MA (10/20)\n"
        "✅ MACD\n"
        "✅ Bollinger Bands\n"
        "✅ Stochastic\n\n"
        "/scan — азыр сканерлөө\n"
        "/status — бот абалы\n"
        "/pairs — жуптар тизмеси",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["scan"])
def manual_scan(message):
    bot.send_message(message.chat.id, "🔍 Сканерлөө башталды... (1-2 мүнөт күт)")
    found = 0
    for from_cur, to_cur in PAIRS:
        signal = analyze_pair(from_cur, to_cur)
        if signal:
            send_signal(signal)
            found += 1
        time.sleep(15)
    if found:
        bot.send_message(message.chat.id, "✅ " + str(found) + " сигнал табылды!")
    else:
        bot.send_message(message.chat.id, "❌ Азыр 5/5 тастыкталган сигнал жок")

@bot.message_handler(commands=["status"])
def status(message):
    bot.send_message(message.chat.id,
        "✅ Бот иштеп жатат\n"
        "🔥 Шарт: 5/5 индикатор дал келсе гана сигнал\n"
        "🌐 API: Alpha Vantage\n"
        "⏱ " + str(CHECK_INTERVAL//60) + " мүнөт сайын текшерет\n"
        "💱 Жуптар: " + str(len(PAIRS))
    )

@bot.message_handler(commands=["pairs"])
def pairs_list(message):
    text = "💱 *Жуптар:*\n" + "\n".join("• " + f + "/" + t for f, t in PAIRS)
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# ==================== ИШТЕТҮҮ ====================
if __name__ == "__main__":
    log.info("Forex Сигнал Боту башталды — 5/5 режими!")
    threading.Thread(target=auto_scanner, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            log.error("Ката: " + str(e))
            time.sleep(5)
