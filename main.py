import asyncio
import websockets
import json
import requests
import logging
import os
from datetime import datetime

# ==================== НАСТРОЙКА ====================
DERIV_APP_ID    = "1089"                # Deriv app_id (тест үчүн 1089)
DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN", "YOUR_DERIV_TOKEN_HERE")
TWELVE_API_KEY  = os.environ.get("TWELVE_API_KEY", "YOUR_TWELVE_KEY_HERE")

# Соода параметрлери
TRADE_AMOUNT    = 1.0      # $1 — баштапкы (өзгөртүүгө болот)
MAX_DAILY_LOSS  = 20.0     # $20 — күнүнө максималдуу жоготуу
TIMEFRAME       = "15min"

# Deriv synthetic индекстери (forex эмес — 24/7 иштейт)
# Чыныгы forex Deriv'де спред контракт катары иштейт
PAIRS_MAP = {
    "EUR/USD": "frxEURUSD",
    "GBP/USD": "frxGBPUSD",
    "USD/JPY":  "frxUSDJPY",
    "AUD/USD": "frxAUDUSD",
    "USD/CAD": "frxUSDCAD",
}

PAIRS = list(PAIRS_MAP.keys())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ==================== ГЛОБАЛ КүЙ ====================
daily_loss    = 0.0
trades_today  = 0
active_trades = {}   # {pair: contract_id}

# ==================== БААСЫН АЛУу ====================
def get_price_data(pair):
    try:
        url = (
            "https://api.twelvedata.com/time_series"
            "?symbol=" + pair
            + "&interval=" + TIMEFRAME
            + "&outputsize=220"
            + "&apikey=" + TWELVE_API_KEY
        )
        r = requests.get(url, timeout=15)
        data = r.json()
        if "values" not in data:
            log.warning(f"{pair} API жооп бербеди: {data}")
            return None
        closes, highs, lows, volumes = [], [], [], []
        for v in reversed(data["values"]):
            closes.append(float(v["close"]))
            highs.append(float(v["high"]))
            lows.append(float(v["low"]))
            volumes.append(float(v.get("volume", 0)))
        if len(closes) < 50:
            return None
        return closes, highs, lows, volumes
    except Exception as e:
        log.error(f"Баа алуу катасы: {e}")
        return None

# ==================== ИНДИКАТОРЛОР ====================
def ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return round(val, 5)

def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
    gains  = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag*(period-1) + gains[i]) / period
        al = (al*(period-1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag/al)), 2)

def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i],
                 abs(highs[i]-closes[i-1]),
                 abs(lows[i]-closes[i-1]))
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 5)

def fibonacci_levels(highs, lows, lookback=50):
    if len(highs) < lookback:
        return None
    swing_high = max(highs[-lookback:])
    swing_low  = min(lows[-lookback:])
    diff = swing_high - swing_low
    return {
        "0.382": round(swing_high - diff * 0.382, 5),
        "0.5":   round(swing_high - diff * 0.5,   5),
        "0.618": round(swing_high - diff * 0.618, 5),
        "swing_high": swing_high,
        "swing_low":  swing_low,
    }

# ==================== СИГНАЛ ====================
def get_signal(pair):
    result = get_price_data(pair)
    if not result:
        return None

    closes, highs, lows, volumes = result
    price    = closes[-1]
    ema20    = ema(closes, 20)
    ema50    = ema(closes, 50)
    ema200   = ema(closes, 200)
    rsi_val  = rsi(closes)
    fib      = fibonacci_levels(highs, lows)
    atr_val  = atr(highs, lows, closes)

    if not ema20 or not ema50 or not ema200 or rsi_val is None:
        return None

    signals = []

    # --- Стратегия 1: EMA тренд + Fib pullback ---
    if ema20 > ema50:
        if fib and fib["0.618"] <= price <= fib["0.382"]:
            if 40 <= rsi_val <= 60:
                signals.append("BUY")
    elif ema20 < ema50:
        if fib and fib["0.382"] <= price <= fib["0.618"]:
            if 40 <= rsi_val <= 60:
                signals.append("SELL")

    # --- Стратегия 2: EMA50+200 + RSI ---
    if ema50 > ema200 and price > ema50 and rsi_val < 45:
        signals.append("BUY")
    elif ema50 < ema200 and price < ema50 and rsi_val > 55:
        signals.append("SELL")

    if signals.count("BUY") >= 2:
        direction = "BUY"
    elif signals.count("SELL") >= 2:
        direction = "SELL"
    else:
        return None

    # TP/SL (pip боюнча)
    pip = 0.01 if "JPY" in pair else 0.0001
    sl_dist = (atr_val * 1.0) if atr_val else (20 * pip)
    tp_dist = (atr_val * 3.0) if atr_val else (60 * pip)

    if direction == "BUY":
        tp = round(price + tp_dist, 5)
        sl = round(price - sl_dist, 5)
    else:
        tp = round(price - tp_dist, 5)
        sl = round(price + sl_dist, 5)

    # Duration: 15мин таймфрейм → 15 мүнөттүк контракт
    duration = 15

    return {
        "pair":      pair,
        "symbol":    PAIRS_MAP[pair],
        "direction": direction,
        "price":     price,
        "tp":        tp,
        "sl":        sl,
        "duration":  duration,
        "rsi":       rsi_val,
        "ema20":     ema20,
        "ema50":     ema50,
    }

# ==================== DERIV WEBSOCKET ====================
class DerivTrader:
    def __init__(self):
        self.ws  = None
        self.req_id = 1

    async def connect(self):
        url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        self.ws = await websockets.connect(url)
        log.info("✅ Deriv'ге туташты")

    async def send(self, payload):
        payload["req_id"] = self.req_id
        self.req_id += 1
        await self.ws.send(json.dumps(payload))
        resp = json.loads(await self.ws.recv())
        return resp

    async def authorize(self):
        resp = await self.send({"authorize": DERIV_API_TOKEN})
        if "error" in resp:
            log.error(f"Авторизация катасы: {resp['error']['message']}")
            return False
        balance = resp["authorize"]["balance"]
        currency = resp["authorize"]["currency"]
        log.info(f"✅ Авторизацияланды | Баланс: {balance} {currency}")
        return True

    async def get_balance(self):
        resp = await self.send({"balance": 1, "account": "current"})
        if "balance" in resp:
            return float(resp["balance"]["balance"])
        return None

    async def buy_contract(self, signal):
        """Контракт сатып алуу"""
        contract_type = "CALL" if signal["direction"] == "BUY" else "PUT"

        # 1. Proposal алуу
        proposal_resp = await self.send({
            "proposal":       1,
            "amount":         TRADE_AMOUNT,
            "basis":          "stake",
            "contract_type":  contract_type,
            "currency":       "USD",
            "duration":       signal["duration"],
            "duration_unit":  "m",
            "symbol":         signal["symbol"],
        })

        if "error" in proposal_resp:
            log.error(f"Proposal катасы: {proposal_resp['error']['message']}")
            return None

        proposal_id = proposal_resp["proposal"]["id"]
        ask_price   = proposal_resp["proposal"]["ask_price"]
        log.info(f"📋 Proposal: {signal['pair']} {contract_type} | Баа: {ask_price}")

        # 2. Контракт сатып алуу
        buy_resp = await self.send({
            "buy":   proposal_id,
            "price": ask_price,
        })

        if "error" in buy_resp:
            log.error(f"Buy катасы: {buy_resp['error']['message']}")
            return None

        contract_id = buy_resp["buy"]["contract_id"]
        log.info(
            f"✅ Контракт ачылды | {signal['pair']} {signal['direction']} | "
            f"ID: {contract_id} | Сумма: ${TRADE_AMOUNT}"
        )
        return contract_id

    async def sell_contract(self, contract_id):
        """Контрактты мөөнөтүнөн мурда жабуу"""
        resp = await self.send({"sell": contract_id, "price": 0})
        if "error" in resp:
            log.error(f"Sell катасы: {resp['error']['message']}")
            return None
        sold_for = resp["sell"].get("sold_for", 0)
        log.info(f"📤 Контракт жабылды | ID: {contract_id} | Алынды: ${sold_for}")
        return sold_for

    async def ping(self):
        await self.send({"ping": 1})

# ==================== НЕГИЗГИ БОТ ====================
async def run_bot():
    global daily_loss, trades_today

    trader = DerivTrader()

    while True:
        try:
            await trader.connect()

            if not await trader.authorize():
                log.error("Авторизация болгон жок. 60с күт...")
                await asyncio.sleep(60)
                continue

            log.info("🚀 Бот иштеп баштады!")
            last_reset = datetime.now().date()

            while True:
                # Күнүнө жоготууну баштан баштоо
                today = datetime.now().date()
                if today != last_reset:
                    daily_loss   = 0.0
                    trades_today = 0
                    last_reset   = today
                    log.info("🔄 Күнүнө лимит баштан башталды")

                # Күнүнө жоготуу лимити
                if daily_loss >= MAX_DAILY_LOSS:
                    log.warning(f"🛑 Күнүнө жоготуу лимити жетти: ${daily_loss:.2f}. Бот токтоду.")
                    await asyncio.sleep(3600)
                    continue

                # Баланс текшерүү
                balance = await trader.get_balance()
                if balance is not None:
                    log.info(f"💰 Баланс: ${balance:.2f} | Соодалар: {trades_today} | Жоготуу: ${daily_loss:.2f}")

                # Ар бир жуп үчүн сигнал текшерүү
                for pair in PAIRS:
                    if pair in active_trades:
                        continue  # Ачык контракт бар

                    signal = get_signal(pair)
                    if not signal:
                        log.info(f"⏳ {pair}: Сигнал жок")
                        continue

                    log.info(
                        f"📊 СИГНАЛ: {pair} {signal['direction']} | "
                        f"RSI: {signal['rsi']} | EMA20: {signal['ema20']}"
                    )

                    contract_id = await trader.buy_contract(signal)
                    if contract_id:
                        active_trades[pair] = {
                            "contract_id": contract_id,
                            "direction":   signal["direction"],
                            "entry":       signal["price"],
                            "tp":          signal["tp"],
                            "sl":          signal["sl"],
                            "open_time":   datetime.now(),
                            "duration":    signal["duration"],
                        }
                        trades_today += 1

                    await asyncio.sleep(2)

                # Ачык контракттарды текшерүү (duration өткөндөн кийин)
                closed_pairs = []
                for pair, trade in active_trades.items():
                    elapsed = (datetime.now() - trade["open_time"]).seconds / 60
                    if elapsed >= trade["duration"]:
                        sold_for = await trader.sell_contract(trade["contract_id"])
                        if sold_for is not None:
                            profit = sold_for - TRADE_AMOUNT
                            if profit < 0:
                                daily_loss += abs(profit)
                            log.info(
                                f"{'✅ Пайда' if profit > 0 else '❌ Жоготуу'}: "
                                f"{pair} | ${profit:+.2f}"
                            )
                        closed_pairs.append(pair)

                for pair in closed_pairs:
                    del active_trades[pair]

                # Ping (байланыш сактоо)
                await trader.ping()

                # 15 мүнөт күтүү
                log.info("⏳ 15 мүнөт күтүү...")
                await asyncio.sleep(900)

        except websockets.exceptions.ConnectionClosed:
            log.warning("🔌 Байланыш үзүлдү. 10с кийин кайра туташат...")
            await asyncio.sleep(10)
        except Exception as e:
            log.error(f"Ката: {e}")
            await asyncio.sleep(15)

# ==================== ИШТЕТҮҮ ====================
if __name__ == "__main__":
    log.info("=" * 50)
    log.info("  DERIV АВТО СООДА БОТ  ")
    log.info("=" * 50)
    log.info(f"Сумма/соода: ${TRADE_AMOUNT}")
    log.info(f"Күнүнө макс жоготуу: ${MAX_DAILY_LOSS}")
    log.info(f"Жуптар: {', '.join(PAIRS)}")
    log.info("=" * 50)
    asyncio.run(run_bot())
