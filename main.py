import telebot
import requests
import time
import threading
import logging
import os
from datetime import datetime

# ==================== НАСТРОЙКА ====================
BOT_TOKEN = "8325546419:AAG6SVUOYzL7v98NltuewOtnhtR3gbVlptg"
ADMIN_ID = 8693522887
TWELVE_API_KEY = os.environ.get("TWELVE_API_KEY", "8bb0a93e7742495da70ccbd53f2bbb7c")

PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "AUD/USD",
    "USD/CAD",
    "NZD/USD",
]

CHECK_INTERVAL = 900
MIN_CONFIRM = 5       # 5/6 индикатор
TOTAL_INDICATORS = 6
TIMEFRAME = "15min"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

# ==================== БААСЫН АЛУу ====================
def get_price_data(pair):
    try:
        url = (
            "https://api.twelvedata.com/time_series"
            "?symbol=" + pair +
            "&interval=" + TIMEFRAME +
            "&outputsize=220" +
            "&apikey=" + TWELVE_API_KEY
        )
        response = requests.get(url, timeout=15)
        data = response.json()

        if "values" not in data:
            log.warning(pair + " API жооп бербеди: " + str(data))
            return None

        values = data["values"]
        closes, highs, lows, opens = [], [], [], []
        for v in reversed(values):
            closes.append(float(v["close"]))
            highs.append(float(v["high"]))
            lows.append(float(v["low"]))
            opens.append(float(v["open"]))

        if len(closes) < 50:
            log.warning(pair + " — маалымат жетишсиз")
            return None

        return closes, highs, lows, opens

    except Exception as e:
        log.error("Баа алууда ката: " + str(e))
        return None

# ==================== 1. RSI ====================
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

# ==================== 2. EMA TREND ====================
def calculate_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return round(val, 5)

# ==================== 3. ORDER BLOCK ====================
def detect_order_block(closes, highs, lows, opens, lookback=20):
    """
    Order Block — институционалдык зона.
    Bullish OB: чоң ылдый свеча, андан кийин жогору кеткен
    Bearish OB: чоң жогору свеча, андан кийин ылдый кеткен
    """
    if len(closes) < lookback + 3:
        return None, None

    current = closes[-1]
    ob_bull = None
    ob_bear = None

    for i in range(-lookback, -2):
        candle_size = abs(closes[i] - opens[i])
        avg_size = sum(abs(closes[j] - opens[j]) for j in range(-lookback, -1)) / lookback

        # Bullish Order Block
        if (opens[i] > closes[i] and          # ылдый свеча
            candle_size > avg_size * 1.5 and   # чоң свеча
            closes[i+1] > opens[i+1] and       # андан кийин жогору
            closes[i+2] > closes[i+1]):        # тренд жогору
            ob_bull = (lows[i], highs[i])      # OB зонасы

        # Bearish Order Block
        if (closes[i] > opens[i] and           # жогору свеча
            candle_size > avg_size * 1.5 and   # чоң свеча
            closes[i+1] < opens[i+1] and       # андан кийин ылдый
            closes[i+2] < closes[i+1]):        # тренд ылдый
            ob_bear = (lows[i], highs[i])      # OB зонасы

    return ob_bull, ob_bear

# ==================== 4. BREAK OF STRUCTURE ====================
def detect_bos(closes, highs, lows, lookback=20):
    """
    BOS — тренд өзгөрүүсү.
    Bullish BOS: акыркы жогорку чокуну сындырды
    Bearish BOS: акыркы төмөнкү түпкүрдү сындырды
    """
    if len(closes) < lookback + 1:
        return None

    recent_highs = highs[-lookback:-1]
    recent_lows = lows[-lookback:-1]
    current = closes[-1]

    prev_high = max(recent_highs)
    prev_low = min(recent_lows)

    if current > prev_high:
        return "BUY"   # Bullish BOS
    elif current < prev_low:
        return "SELL"  # Bearish BOS
    return None

# ==================== 5. FAIR VALUE GAP ====================
def detect_fvg(closes, highs, lows, lookback=10):
    """
    FVG — баа боштугу (3 свеча арасында).
    Bullish FVG: свеча[i] high < свеча[i+2] low
    Bearish FVG: свеча[i] low > свеча[i+2] high
    """
    if len(closes) < lookback + 3:
        return None

    current = closes[-1]

    for i in range(-lookback, -2):
        # Bullish FVG
        if highs[i] < lows[i+2]:
            fvg_low = highs[i]
            fvg_high = lows[i+2]
            if fvg_low <= current <= fvg_high:
                return "BUY"

        # Bearish FVG
        if lows[i] > highs[i+2]:
            fvg_high = lows[i]
            fvg_low = highs[i+2]
            if fvg_low <= current <= fvg_high:
                return "SELL"

    return None

# ==================== 6. ATR ====================
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

# ==================== TP/SL ====================
def calculate_tp_sl(price, direction, pair, atr):
    pip = 0.01 if "JPY" in pair else 0.0001
    if atr:
        tp_dist = atr * 3    # RR 1:3
        sl_dist = atr * 1
    else:
        tp_dist = 60 * pip
        sl_dist = 20 * pip
    if direction == "BUY":
        tp = round(price + tp_dist, 5)
        sl = round(price - sl_dist, 5)
    else:
        tp = round(price - tp_dist, 5)
        sl = round(price + sl_dist, 5)
    return tp, sl

# ==================== СИГНАЛ АНАЛИЗИ ====================
def analyze_pair(pair):
    result = get_price_data(pair)
    if not result:
        return None

    closes, highs, lows, opens = result
    current_price = closes[-1]

    # Индикаторлорду эсептөө
    rsi      = calculate_rsi(closes)
    ema50    = calculate_ema(closes, 50)
    ema200   = calculate_ema(closes, 200)
    atr      = calculate_atr(highs, lows, closes)
    ob_bull, ob_bear = detect_order_block(closes, highs, lows, opens)
    bos      = detect_bos(closes, highs, lows)
    fvg      = detect_fvg(closes, highs, lows)

    checks = {
        "RSI":          None,
        "EMA Trend":    None,
        "Order Block":  None,
        "BOS":          None,
        "FVG":          None,
        "ATR Filter":   None,
    }

    # 1. RSI
    if rsi is not None:
        if rsi < 45:   checks["RSI"] = "BUY"
        elif rsi > 55: checks["RSI"] = "SELL"

    # 2. EMA Trend (50/200)
    if ema50 and ema200:
        if ema50 > ema200 and current_price > ema50:
            checks["EMA Trend"] = "BUY"
        elif ema50 < ema200 and current_price < ema50:
            checks["EMA Trend"] = "SELL"

    # 3. Order Block
    if ob_bull:
        ob_low, ob_high = ob_bull
        if ob_low <= current_price <= ob_high:
            checks["Order Block"] = "BUY"
    if ob_bear:
        ob_low, ob_high = ob_bear
        if ob_low <= current_price <= ob_high:
            checks["Order Block"] = "SELL"

    # 4. BOS
    if bos:
        checks["BOS"] = bos

    # 5. FVG
    if fvg:
        checks["FVG"] = fvg

    # 6. ATR Filter — волатилдүүлүк жетиштүүбү
    if atr is not None:
        pip = 0.01 if "JPY" in pair else 0.0001
        min_atr = 5 * pip   # минимум 5 pip волатилдүүлүк
        if atr >= min_atr:
            # Тренд багытын аныктоо
            if closes[-1] > closes[-5]:
                checks["ATR Filter"] = "BUY"
            else:
                checks["ATR Filter"] = "SELL"

    buy_count  = sum(1 for v in checks.values() if v == "BUY")
    sell_count = sum(1 for v in checks.values() if v == "SELL")

    log.info(pair + " — BUY:" + str(buy_count) + " SELL:" + str(sell_count) +
             " RSI:" + str(rsi) + " BOS:" + str(bos) + " FVG:" + str(fvg))

    if buy_count >= MIN_CONFIRM:
        direction = "BUY"
    elif sell_count >= MIN_CONFIRM:
        direction = "SELL"
    else:
        return None

    tp, sl = calculate_tp_sl(current_price, direction, pair, atr)
    rr = round((tp - current_price) / (current_price - sl), 1) if direction == "BUY" else round((current_price - tp) / (sl - current_price), 1)

    return {
        "pair":      pair,
        "price":     current_price,
        "direction": direction,
        "tp":        tp,
        "sl":        sl,
        "rr":        rr,
        "rsi":       rsi,
        "ema50":     ema50,
        "ema200":    ema200,
        "atr":       atr,
        "bos":       bos,
        "fvg":       fvg,
        "ob_bull":   ob_bull,
        "ob_bear":   ob_bear,
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
        "RSI":         "RSI:         `" + str(s["rsi"]) + "`",
        "EMA Trend":   "EMA (50/200): `" + str(s["ema50"]) + "`",
        "Order Block": "Order Block  (Smart Money)",
        "BOS":         "Break of Structure",
        "FVG":         "Fair Value Gap",
        "ATR Filter":  "ATR Filter:  `" + str(s["atr"]) + "`",
    }

    ind_lines = ""
    for k, v in s["checks"].items():
        mark = "✅" if v == s["direction"] else "❌"
        ind_lines += mark + " " + names[k] + "\n"

    msg = (
        emoji + " *" + action + "* " + emoji + "\n"
        "━━━━━━━━━━━━━━━\n"
        "💱 Жуп: *" + s["pair"] + "*\n"
        "💰 Кирүү:       `" + str(s["price"]) + "`\n"
        "🎯 Take Profit: `" + str(s["tp"]) + "`\n"
        "🛑 Stop Loss:   `" + str(s["sl"]) + "`\n"
        "📊 Risk/Reward: `1:" + str(s["rr"]) + "`\n"
        "━━━━━━━━━━━━━━━\n"
        "🔥 *" + str(count) + "/" + str(TOTAL_INDICATORS) + " ИНДИКАТОР ТАСТЫКТАДЫ!*\n"
        "━━━━━━━━━━━━━━━\n"
        + ind_lines +
        "━━━━━━━━━━━━━━━\n"
        "⏱ Таймфрейм: " + TIMEFRAME + "\n"
        "🌐 Twelve Data чыныгы баа\n"
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
        for pair in PAIRS:
            try:
                signal = analyze_pair(pair)
                if signal:
                    send_signal(signal)
                time.sleep(10)
            except Exception as e:
                log.error(str(e))
        time.sleep(CHECK_INTERVAL)

# ==================== БОТ КОМАНДЫ ====================
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id,
        "📊 *Forex Smart Money Боту* 🚀\n\n"
        "🔥 Сигнал БЕРИ " + str(MIN_CONFIRM) + "/" + str(TOTAL_INDICATORS) + " тастыктаганда!\n\n"
        "✅ RSI (45/55)\n"
        "✅ EMA Trend (50/200)\n"
        "✅ Order Block (Smart Money)\n"
        "✅ Break of Structure\n"
        "✅ Fair Value Gap\n"
        "✅ ATR Filter\n\n"
        "/scan — азыр сканерлөө\n"
        "/status — бот абалы\n"
        "/pairs — жуптар тизмеси",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["scan"])
def manual_scan(message):
    bot.send_message(message.chat.id, "🔍 Сканерлөө башталды... (бир аз күт)")
    found = 0
    for pair in PAIRS:
        signal = analyze_pair(pair)
        if signal:
            send_signal(signal)
            found += 1
        time.sleep(10)
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
        "🌐 API: Twelve Data\n"
        "🔄 " + str(CHECK_INTERVAL//60) + " мүнөт сайын текшерет\n"
        "💱 Жуптар: " + str(len(PAIRS))
    )

@bot.message_handler(commands=["pairs"])
def pairs_list(message):
    text = "💱 *Жуптар:*\n" + "\n".join("• " + p for p in PAIRS)
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# ==================== ИШТЕТҮҮ ====================
if __name__ == "__main__":
    log.info("Forex Smart Money Боту башталды!")
    threading.Thread(target=auto_scanner, daemon=True).start()
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            log.error("Ката: " + str(e))
            time.sleep(5)
