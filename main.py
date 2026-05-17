import telebot
import requests
import time
import threading
import logging
from datetime import datetime

BOT_TOKEN = "8559551745:AAFCue6bmPgvpkUdhQ_aSjR7krguHft_ACI"
ADMIN_ID = 8693522887
API_KEY = "NEUGRBMAZ9YL1O2S"

PAIRS = [
    ("EUR", "USD"),
    ("GBP", "USD"),
    ("USD", "JPY"),
    ("USD", "CHF"),
    ("AUD", "USD"),
]

CHECK_INTERVAL = 300

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

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
            return None
        time_series = data["Time Series FX (5min)"]
        closes = []
        for key in sorted(time_series.keys(), reverse=True)[:50]:
            closes.append(float(time_series[key]["4. close"]))
        return list(reversed(closes))
    except Exception as e:
        log.error("Баа алууда ката: " + str(e))
        return None

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

def calculate_tp_sl(price, direction, from_cur, to_cur):
    if to_cur == "JPY" or from_cur == "JPY":
        pip = 0.01
    else:
        pip = 0.0001
    tp_pips = 30
    sl_pips = 15
    if direction == "BUY":
        tp = round(price + (tp_pips * pip), 5)
        sl = round(price - (sl_pips * pip), 5)
    else:
        tp = round(price - (tp_pips * pip), 5)
        sl = round(price + (sl_pips * pip), 5)
    return tp, sl

def is_market_open():
    now = datetime.utcnow()
    if now.weekday() == 6:
        return False
    if now.weekday() == 5 and now.hour >= 21:
        return False
    if now.weekday() == 0 and now.hour < 22:
        return False
    return True

def analyze_pair(from_cur, to_cur):
    pair = from_cur + "/" + to_cur
    prices = get_price_data(from_cur, to_cur)
    if not prices or len(prices) < 20:
        return None
    current_price = prices[-1]
    rsi = calculate_rsi(prices)
    ma20 = calculate_ma(prices, 20)
    ma10 = calculate_ma(prices, 10)
    macd, macd_signal = calculate_macd(prices)
    signals = []
    if rsi:
        if rsi < 35: signals.append("BUY")
        elif rsi > 65: signals.append("SELL")
    if ma20 and ma10:
        if current_price > ma10 > ma20: signals.append("BUY")
        elif current_price < ma10 < ma20: signals.append("SELL")
    if macd and macd_signal:
        if macd > macd_signal: signals.append("BUY")
        elif macd < macd_signal: signals.append("SELL")
    buy_count = signals.count("BUY")
    sell_count = signals.count("SELL")
    if buy_count >= 2:
        direction = "BUY"
        strength = "Күчтүү" if buy_count == 3 else "Орточо"
    elif sell_count >= 2:
        direction = "SELL"
        strength = "Күчтүү" if sell_count == 3 else "Орточо"
    else:
        return None
    tp, sl = calculate_tp_sl(current_price, direction, from_cur, to_cur)
    return {
        "pair": pair,
        "price": current_price,
        "direction": direction,
        "strength": strength,
        "tp": tp,
        "sl": sl,
        "rsi": rsi,
        "ma10": ma10,
        "macd": macd,
        "buy": buy_count,
        "sell": sell_count,
        "time": datetime.now().strftime("%H:%M:%S")
    }

def send_signal(s):
    if s["direction"] == "BUY":
        emoji = "🟢"
        action = "BUY — Сатып АЛ"
    else:
        emoji = "🔴"
        action = "SELL — Сат"
    msg = (
        emoji + " *" + action + "* " + emoji + "\n"
        "━━━━━━━━━━━━━━━\n"
        "💱 Жуп: *" + s["pair"] + "*\n"
        "💰 Кирүү баасы:  `" + str(s["price"]) + "`\n"
        "🎯 Take Profit:  `" + str(s["tp"]) + "`\n"
        "🛑 Stop Loss:    `" + str(s["sl"]) + "`\n"
        "━━━━━━━━━━━━━━━\n"
        "💪 Күч: " + s["strength"] + "\n"
        "📊 RSI: `" + str(s["rsi"]) + "`\n"
        "📈 MA10: `" + str(s["ma10"]) + "`\n"
        "📉 MACD: `" + str(s["macd"]) + "`\n"
        "━━━━━━━━━━━━━━━\n"
        "✅ Alpha Vantage чыныгы баа\n"
        "⏰ " + s["time"] + "\n"
        "⚠️ _Соода тобокелчилиги өз мойнуңузда!_"
    )
    try:
        bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")
    except Exception as e:
        log.error("Жөнөтүүдө ката: " + str(e))

def auto_scanner():
    while True:
        if not is_market_open():
            log.info("Базар жабык. 30 мүнөттөн кийин текшерет...")
            time.sleep(1800)
            continue
        log.info("Сканерлөө...")
        for from_cur, to_cur in PAIRS:
            try:
                signal = analyze_pair(from_cur, to_cur)
                if signal:
                    send_signal(signal)
                time.sleep(15)
            except Exception as e:
                log.error(str(e))
        time.sleep(CHECK_INTERVAL)

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id,
        "📊 *Forex Сигнал Боту*\n\n"
        "✅ Alpha Vantage чыныгы баалар!\n\n"
        "/scan — азыр сканерлөө\n"
        "/status — бот абалы\n"
        "/pairs — жуптар тизмеси",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["scan"])
def manual_scan(message):
    if not is_market_open():
        bot.send_message(message.chat.id, "⛔ Базар азыр жабык! Дүйшөмбүдө ачылат.")
        return
    bot.send_message(message.chat.id, "🔍 Сканерлөө башталды... (бир аз күт)")
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
        bot.send_message(message.chat.id, "❌ Азыр так сигнал жок")

@bot.message_handler(commands=["status"])
def status(message):
    market_status = "🟢 Ачык" if is_market_open() else "🔴 Жабык"
    bot.send_message(message.chat.id,
        "✅ Бот иштеп жатат\n"
        "🌐 API: Alpha Vantage\n"
        "📊 Базар: " + market_status + "\n"
        "⏱ " + str(CHECK_INTERVAL//60) + " мүнөт сайын текшерет\n"
        "💱 Жуптар: " + str(len(PAIRS))
    )

@bot.message_handler(commands=["pairs"])
def pairs_list(message):
    text = "💱 *Жуптар:*\n" + "\n".join("• " + f + "/" + t for f, t in PAIRS)
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

if __name__ == "__main__":
    log.info("Forex Сигнал Боту башталды...")
    threading.Thread(target=auto_scanner, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            log.error("Ката: " + str(e))
            time.sleep(5)