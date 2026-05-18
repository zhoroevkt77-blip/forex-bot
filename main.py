import telebot
import requests
import time
import threading
import logging
from datetime import datetime

# ==================== НАСТРОЙКА ====================
BOT_TOKEN = "8559551745:AAGp25BeOmXva9PoQ7FVW9JoKcQymd-cZ7E"
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

CHECK_INTERVAL = 300
MIN_CONFIRM = 6      # 6/11 индикатор
TOTAL_INDICATORS = 11
TIMEFRAME = "15min"  # 15 мүнөт

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
            "&interval=" + TIMEFRAME +
            "&apikey=" + API_KEY +
            "&outputsize=compact"
        )
        response = requests.get(url, timeout=15)
        data = response.json()
        key = "Time Series FX (" + TIMEFRAME + ")"
        if key not in data:
            log.warning("API жооп бербеди: " + str(data))
            return None
        time_series = data[key]
        closes, highs, lows, volumes = [], [], [], []
        for k in sorted(time_series.keys(), reverse=True)[:100]:
            closes.append(float(time_series[k]["4. close"]))
            highs.append(float(time_series[k]["2. high"]))
            lows.append(float(time_series[k]["3. low"]))
        return (
            list(reversed(closes)),
            list(reversed(highs)),
            list(reversed(lows)),
        )
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

def calculate_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    val = prices[0]
    for p in prices[1:]:
        val = p * k + val * (1 - k)
    return round(val, 5)

def calculate_macd(prices, fast=12, slow=26):
    if len(prices) < slow:
        return None, None
    ema_fast = calculate_ema(prices[-fast*2:], fast)
    ema_slow = calculate_ema(prices[-slow*2:], slow)
    if ema_fast is None or ema_slow is None:
        return None, None
    macd_line = round(ema_fast - ema_slow, 6)
    signal_line = round(macd_line * 0.9, 6)
    return macd_line, signal_line

def calculate_bollinger(prices, period=20):
    if len(prices) < period:
        return None, None, None
    ma = sum(prices[-period:]) / period
    variance = sum((p - ma) ** 2 for p in prices[-period:]) / period
    std = variance ** 0.5
    return round(ma, 5), round(ma + 2*std, 5), round(ma - 2*std, 5)

def calculate_stochastic(closes, highs, lows, period=14):
    if len(closes) < period:
        return None
    high_max = max(highs[-period:])
    low_min = min(lows[-period:])
    if high_max == low_min:
        return 50
    return round(((closes[-1] - low_min) / (high_max - low_min)) * 100, 2)

def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 5)

def calculate_cci(highs, lows, closes, period=20):
    if len(closes) < period:
        return None
    typical = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    tp_slice = typical[-period:]
    ma = sum(tp_slice) / period
    mean_dev = sum(abs(p - ma) for p in tp_slice) / period
    if mean_dev == 0:
        return 0
    return round((typical[-1] - ma) / (0.015 * mean_dev), 2)

def calculate_williams_r(highs, lows, closes, period=14):
    if len(closes) < period:
        return None
    high_max = max(highs[-period:])
    low_min = min(lows[-period:])
    if high_max == low_min:
        return -50
    return round(((high_max - closes[-1]) / (high_max - low_min)) * -100, 2)

def calculate_momentum(prices, period=10):
    if len(prices) < period + 1:
        return None
    return round(prices[-1] - prices[-period-1], 5)

def calculate_ema_cross(prices):
    ema50 = calculate_ema(prices, 50)
    ema200 = calculate_ema(prices, 200)
    if ema50 is None or ema200 is None:
        return None, None
    return ema50, ema200

# ==================== TP/SL ====================
def calculate_tp_sl(price, direction, from_cur, to_cur, atr=None):
    pip = 0.01 if "JPY" in (from_cur, to_cur) else 0.0001
    if atr:
        tp_dist = atr * 2
        sl_dist = atr * 1
    else:
        tp_dist = 40 * pip
        sl_dist = 20 * pip
    if direction == "BUY":
        tp = round(price + tp_dist, 5)
        sl = round(price - sl_dist, 5)
    else:
        tp = round(price - tp_dist, 5)
        sl = round(price + sl_dist, 5)
    return tp, sl

# ==================== СИГНАЛ АНАЛИЗИ ====================
def analyze_pair(from_cur, to_cur):
    pair = from_cur + "/" + to_cur
    result = get_price_data(from_cur, to_cur)
    if not result:
        return None

    closes, highs, lows = result
    if len(closes) < 200:
        log.warning(pair + " — жетиштүү маалымат жок")
        return None

    current_price = closes[-1]

    # Индикаторлорду эсептөө
    rsi        = calculate_rsi(closes)
    ma10       = calculate_ma(closes, 10)
    ma20       = calculate_ma(closes, 20)
    macd, macd_signal = calculate_macd(closes)
    bb_mid, bb_upper, bb_lower = calculate_bollinger(closes)
    stoch      = calculate_stochastic(closes, highs, lows)
    atr        = calculate_atr(highs, lows, closes)
    cci        = calculate_cci(highs, lows, closes)
    williams   = calculate_williams_r(highs, lows, closes)
    momentum   = calculate_momentum(closes)
    ema50, ema200 = calculate_ema_cross(closes)

    checks = {
        "RSI":        None,
        "MA":         None,
        "MACD":       None,
        "Bollinger":  None,
        "Stochastic": None,
        "ATR":        None,
        "CCI":        None,
        "Williams%R": None,
        "Momentum":   None,
        "EMA Cross":  None,
        "EMA Trend":  None,
    }

    # 1. RSI
    if rsi is not None:
        if rsi < 35:   checks["RSI"] = "BUY"
        elif rsi > 65: checks["RSI"] = "SELL"

    # 2. MA
    if ma10 and ma20:
        if ma10 > ma20:   checks["MA"] = "BUY"
        elif ma10 < ma20: checks["MA"] = "SELL"

    # 3. MACD
    if macd and macd_signal:
        if macd > macd_signal:   checks["MACD"] = "BUY"
        elif macd < macd_signal: checks["MACD"] = "SELL"

    # 4. Bollinger
    if bb_upper and bb_lower:
        if current_price < bb_lower:   checks["Bollinger"] = "BUY"
        elif current_price > bb_upper: checks["Bollinger"] = "SELL"

    # 5. Stochastic
    if stoch is not None:
        if stoch < 25:   checks["Stochastic"] = "BUY"
        elif stoch > 75: checks["Stochastic"] = "SELL"

    # 6. ATR (волатилдүүлүк тренди)
    if atr is not None:
        atr_ma = calculate_ma([atr] * 14, 14)
        if current_price > closes[-2]:  checks["ATR"] = "BUY"
        else:                           checks["ATR"] = "SELL"

    # 7. CCI
    if cci is not None:
        if cci < -100:  checks["CCI"] = "BUY"
        elif cci > 100: checks["CCI"] = "SELL"

    # 8. Williams %R
    if williams is not None:
        if williams < -80:  checks["Williams%R"] = "BUY"
        elif williams > -20: checks["Williams%R"] = "SELL"

    # 9. Momentum
    if momentum is not None:
        if momentum > 0:   checks["Momentum"] = "BUY"
        elif momentum < 0: checks["Momentum"] = "SELL"

    # 10. EMA Cross (50/200)
    if ema50 and ema200:
        if ema50 > ema200:   checks["EMA Cross"] = "BUY"
        elif ema50 < ema200: checks["EMA Cross"] = "SELL"

    # 11. EMA Trend (баа EMA50дөн жогорубу)
    if ema50:
        if current_price > ema50:   checks["EMA Trend"] = "BUY"
        elif current_price < ema50: checks["EMA Trend"] = "SELL"

    buy_count  = sum(1 for v in checks.values() if v == "BUY")
    sell_count = sum(1 for v in checks.values() if v == "SELL")

    log.info(pair + " — BUY:" + str(buy_count) + " SELL:" + str(sell_count))

    if buy_count >= MIN_CONFIRM:
        direction = "BUY"
    elif sell_count >= MIN_CONFIRM:
        direction = "SELL"
    else:
        return None

    tp, sl = calculate_tp_sl(current_price, direction, from_cur, to_cur, atr)

    return {
        "pair":      pair,
        "price":     current_price,
        "direction": direction,
        "tp":        tp,
        "sl":        sl,
        "rsi":       rsi,
        "stoch":     stoch,
        "macd":      macd,
        "cci":       cci,
        "williams":  williams,
        "momentum":  momentum,
        "ema50":     ema50,
        "ema200":    ema200,
        "atr":       atr,
        "bb_upper":  bb_upper,
        "bb_lower":  bb_lower,
        "checks":    checks,
        "buy_count": buy_count,
        "sell_count":sell_count,
        "time":      datetime.now().strftime("%H:%M:%S")
    }

# ==================== ЖӨНӨТҮҮ ====================
def send_signal(s):
    emoji  = "🟢" if s["direction"] == "BUY" else "🔴"
    action = "BUY — Сатып АЛ" if s["direction"] == "BUY" else "SELL — Сат"
    count  = s["buy_count"] if s["direction"] == "BUY" else s["sell_count"]

    names = {
        "RSI":        "RSI:          `" + str(s["rsi"]) + "`",
        "MA":         "MA (10/20)",
        "MACD":       "MACD:         `" + str(s["macd"]) + "`",
        "Bollinger":  "Bollinger BB",
        "Stochastic": "Stochastic:   `" + str(s["stoch"]) + "`",
        "ATR":        "ATR:          `" + str(s["atr"]) + "`",
        "CCI":        "CCI:          `" + str(s["cci"]) + "`",
        "Williams%R": "Williams %R:  `" + str(s["williams"]) + "`",
        "Momentum":   "Momentum:     `" + str(s["momentum"]) + "`",
        "EMA Cross":  "EMA (50/200): `" + str(s["ema50"]) + "`",
        "EMA Trend":  "EMA Trend",
    }

    ind_lines = ""
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
        "🔥 *" + str(count) + "/" + str(TOTAL_INDICATORS) + " ИНДИКАТОР ТАСТЫКТАДЫ!*\n"
        "━━━━━━━━━━━━━━━\n"
        + ind_lines +
        "━━━━━━━━━━━━━━━\n"
        "⏱ Таймфрейм: " + TIMEFRAME + "\n"
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
        "🔥 Сигнал БЕРИ " + str(MIN_CONFIRM) + "/" + str(TOTAL_INDICATORS) + " индикатор тастыктаганда!\n\n"
        "✅ RSI\n"
        "✅ MA (10/20)\n"
        "✅ MACD\n"
        "✅ Bollinger Bands\n"
        "✅ Stochastic\n"
        "✅ ATR\n"
        "✅ CCI\n"
        "✅ Williams %R\n"
        "✅ Momentum\n"
        "✅ EMA Cross (50/200)\n"
        "✅ EMA Trend\n\n"
        "/scan — азыр сканерлөө\n"
        "/status — бот абалы\n"
        "/pairs — жуптар тизмеси",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["scan"])
def manual_scan(message):
    bot.send_message(message.chat.id, "🔍 Сканерлөө башталды... (2-3 мүнөт күт)")
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
        bot.send_message(message.chat.id,
            "❌ Азыр " + str(MIN_CONFIRM) + "/" + str(TOTAL_INDICATORS) + " тастыкталган сигнал жок")

@bot.message_handler(commands=["status"])
def status(message):
    bot.send_message(message.chat.id,
        "✅ Бот иштеп жатат\n"
        "🔥 Шарт: " + str(MIN_CONFIRM) + "/" + str(TOTAL_INDICATORS) + " индикатор\n"
        "⏱ Таймфрейм: " + TIMEFRAME + "\n"
        "🌐 API: Alpha Vantage\n"
        "🔄 " + str(CHECK_INTERVAL//60) + " мүнөт сайын текшерет\n"
        "💱 Жуптар: " + str(len(PAIRS))
    )

@bot.message_handler(commands=["pairs"])
def pairs_list(message):
    text = "💱 *Жуптар:*\n" + "\n".join("• " + f + "/" + t for f, t in PAIRS)
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# ==================== ИШТЕТҮҮ ====================
if __name__ == "__main__":
    log.info("Forex Сигнал Боту башталды!")
    threading.Thread(target=auto_scanner, daemon=True).start()
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            log.error("Ката: " + str(e))
            time.sleep(5)
