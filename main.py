import asyncio
import websockets
import json
import requests
import logging
import os
from datetime import datetime

# ==================== КОНФИГУРАЦИЯ ====================
DERIV_APP_ID    = os.environ.get("DERIV_APP_ID", "1089")
DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN", "pat_a8ed6a3d58be5b4d856d6e467965534d34e0a4c3d8dd3860fca899a6efcc830a")
TWELVE_API_KEY  = os.environ.get("TWELVE_API_KEY", "YOUR_TWELVE_KEY_HERE")

# MT5 конфигурациясы
MT5_LOGIN    = os.environ.get("MT5_LOGIN", "")       # MT5 аккаунт номери
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")    # MT5 сырсөз
MT5_SERVER   = os.environ.get("MT5_SERVER", "")      # MT5 сервер (мисалы: Deriv-Server)

# Соода параметрлери
TRADE_AMOUNT   = 0.01      # Lot өлчөмү (MT5 үчүн)
MAX_DAILY_LOSS = 20.0      # $20 — күнүнө максималдуу жоготуу
TIMEFRAME      = "15min"

# MT5 символдору (Deriv MT5)
PAIRS_MAP = {
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY":  "USDJPY",
    "AUD/USD": "AUDUSD",
    "USD/CAD": "USDCAD",
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
active_trades = {}

# ==================== БААСЫН АЛУу (Twelve Data) ====================
def get_price_data(pair):
    try:
        url = (
            "https://api.twelvedata.com/time_series"
            f"?symbol={pair}"
            f"&interval={TIMEFRAME}"
            "&outputsize=220"
            f"&apikey={TWELVE_API_KEY}"
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

def macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None, None
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    if ema_fast is None or ema_slow is None:
        return None, None
    macd_line = ema_fast - ema_slow
    # Жөнөкөй signal line (EMA9 of MACD)
    macd_vals = []
    for i in range(slow, len(prices)):
        ef = ema(prices[:i+1], fast)
        es = ema(prices[:i+1], slow)
        if ef and es:
            macd_vals.append(ef - es)
    if len(macd_vals) < signal:
        return macd_line, None
    signal_line = ema(macd_vals, signal)
    return macd_line, signal_line

# ==================== СИГНАЛ ====================
def get_signal(pair):
    result = get_price_data(pair)
    if not result:
        return None

    closes, highs, lows, volumes = result
    price     = closes[-1]
    ema20     = ema(closes, 20)
    ema50     = ema(closes, 50)
    ema200    = ema(closes, 200)
    rsi_val   = rsi(closes)
    fib       = fibonacci_levels(highs, lows)
    atr_val   = atr(highs, lows, closes)
    macd_line, signal_line = macd(closes)

    if not ema20 or not ema50 or not ema200 or rsi_val is None:
        return None

    buy_signals  = 0
    sell_signals = 0

    # --- Стратегия 1: EMA тренд + Fib pullback ---
    if ema20 > ema50:
        if fib and fib["0.618"] <= price <= fib["0.382"]:
            if 40 <= rsi_val <= 60:
                buy_signals += 1
    elif ema20 < ema50:
        if fib and fib["0.382"] <= price <= fib["0.618"]:
            if 40 <= rsi_val <= 60:
                sell_signals += 1

    # --- Стратегия 2: EMA50+200 + RSI ---
    if ema50 > ema200 and price > ema50 and rsi_val < 45:
        buy_signals += 1
    elif ema50 < ema200 and price < ema50 and rsi_val > 55:
        sell_signals += 1

    # --- Стратегия 3: MACD кесилиши ---
    if macd_line and signal_line:
        if macd_line > signal_line and macd_line < 0:
            buy_signals += 1
        elif macd_line < signal_line and macd_line > 0:
            sell_signals += 1

    # --- Стратегия 4: RSI ашыкча сатып алуу/сатуу ---
    if rsi_val < 35 and price > ema200:
        buy_signals += 1
    elif rsi_val > 65 and price < ema200:
        sell_signals += 1

    # Жок дегенде 2 сигнал керек
    if buy_signals >= 2:
        direction = "BUY"
        confidence = buy_signals
    elif sell_signals >= 2:
        direction = "SELL"
        confidence = sell_signals
    else:
        return None

    # TP/SL (ATR негизинде)
    pip = 0.01 if "JPY" in pair else 0.0001
    sl_dist = (atr_val * 1.0) if atr_val else (20 * pip)
    tp_dist = (atr_val * 2.5) if atr_val else (50 * pip)

    if direction == "BUY":
        tp = round(price + tp_dist, 5)
        sl = round(price - sl_dist, 5)
    else:
        tp = round(price - tp_dist, 5)
        sl = round(price + sl_dist, 5)

    return {
        "pair":       pair,
        "symbol":     PAIRS_MAP[pair],
        "direction":  direction,
        "price":      price,
        "tp":         tp,
        "sl":         sl,
        "lot":        TRADE_AMOUNT,
        "rsi":        rsi_val,
        "ema20":      ema20,
        "ema50":      ema50,
        "ema200":     ema200,
        "confidence": confidence,
    }

# ==================== DERIV MT5 WEBSOCKET ====================
class DerivMT5Bot:
    def __init__(self):
        self.ws     = None
        self.req_id = 1
        self.mt5_account_id = None

    async def connect(self):
        url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        self.ws = await websockets.connect(url, ping_interval=30)
        log.info("✅ Deriv WebSocket'ке туташты")

    async def send(self, payload):
        payload["req_id"] = self.req_id
        self.req_id += 1
        await self.ws.send(json.dumps(payload))
        resp = json.loads(await self.ws.recv())
        if "error" in resp:
            log.error(f"API катасы [{resp.get('msg_type')}]: {resp['error']['message']}")
        return resp

    async def authorize(self):
        """Deriv API token менен авторизациялоо"""
        resp = await self.send({"authorize": DERIV_API_TOKEN})
        if "error" in resp:
            return False
        info = resp["authorize"]
        log.info(f"✅ Авторизацияланды | Логин: {info.get('loginid')} | Баланс: {info.get('balance')} {info.get('currency')}")
        return True

    async def get_mt5_accounts(self):
        """MT5 аккаунттарын алуу"""
        resp = await self.send({"mt5_login_list": 1})
        if "error" in resp or "mt5_login_list" not in resp:
            log.warning("MT5 аккаунттары табылган жок")
            return []
        accounts = resp["mt5_login_list"]
        for acc in accounts:
            log.info(
                f"📊 MT5 Аккаунт: {acc.get('login')} | "
                f"Тип: {acc.get('account_type')} | "
                f"Баланс: {acc.get('balance')} {acc.get('currency')}"
            )
        return accounts

    async def mt5_new_account(self):
        """Жаңы MT5 аккаунт ачуу (эгер жок болсо)"""
        resp = await self.send({
            "mt5_new_account": 1,
            "account_type":    "financial",
            "mt5_account_type": "financial",
            "leverage":        100,
            "mainPassword":    MT5_PASSWORD or "Deriv123!",
            "name":            "Auto Trading Bot",
            "email":           "",
            "country":         "kg",
        })
        if "error" in resp:
            return None
        login = resp.get("mt5_new_account", {}).get("login")
        log.info(f"✅ Жаңы MT5 аккаунт: {login}")
        return login

    async def mt5_deposit(self, login, amount=100):
        """MT5 аккаунтка акча которуу"""
        resp = await self.send({
            "mt5_deposit": 1,
            "from_binary":  1,
            "login":        login,
            "amount":       amount,
        })
        if "error" in resp:
            return False
        log.info(f"✅ MT5'ке ${amount} которулду | Login: {login}")
        return True

    async def mt5_get_balance(self, login):
        """MT5 баланс алуу"""
        resp = await self.send({
            "mt5_get_settings": 1,
            "login": login,
        })
        if "error" in resp:
            return None
        settings = resp.get("mt5_get_settings", {})
        return float(settings.get("balance", 0))

    async def place_trade(self, signal, mt5_login):
        """MT5 аркылуу соода ачуу"""
        action = "BUY" if signal["direction"] == "BUY" else "SELL"

        resp = await self.send({
            "mt5_new_account_list": 1,
        })

        # MT5 order жиберүү
        trade_resp = await self.send({
            "mt5_trade": 1,
            "login":        mt5_login,
            "symbol":       signal["symbol"],
            "volume":       signal["lot"],
            "type":         action,
            "price":        signal["price"],
            "tp":           signal["tp"],
            "sl":           signal["sl"],
            "comment":      f"AutoBot|{signal['pair']}|RSI{signal['rsi']}",
        })

        if "error" in trade_resp:
            log.error(f"Соода ачуу катасы: {trade_resp['error']['message']}")
            return None

        order_id = trade_resp.get("mt5_trade", {}).get("order")
        if order_id:
            log.info(
                f"✅ MT5 ОРДЕР: {signal['pair']} {action} | "
                f"Лот: {signal['lot']} | "
                f"Баа: {signal['price']} | "
                f"TP: {signal['tp']} | SL: {signal['sl']} | "
                f"Order ID: {order_id}"
            )
        return order_id

    async def close_trade(self, mt5_login, order_id, symbol, direction, volume):
        """MT5 ордерди жабуу"""
        close_action = "SELL" if direction == "BUY" else "BUY"

        resp = await self.send({
            "mt5_trade": 1,
            "login":   mt5_login,
            "symbol":  symbol,
            "volume":  volume,
            "type":    close_action,
            "comment": f"Close|{order_id}",
        })

        if "error" in resp:
            log.error(f"Жабуу катасы: {resp['error']['message']}")
            return False

        log.info(f"📤 MT5 ордер жабылды | Order: {order_id}")
        return True

    async def get_open_positions(self, mt5_login):
        """Ачык позицияларды алуу"""
        resp = await self.send({
            "mt5_get_settings": 1,
            "login": mt5_login,
        })
        return resp

    async def ping(self):
        try:
            await self.send({"ping": 1})
        except Exception:
            pass

# ==================== НЕГИЗГИ БОТ ====================
async def run_bot():
    global daily_loss, trades_today

    bot = DerivMT5Bot()

    while True:
        try:
            await bot.connect()

            if not await bot.authorize():
                log.error("Авторизация болгон жок. 60с күт...")
                await asyncio.sleep(60)
                continue

            # MT5 аккаунттарын алуу
            mt5_accounts = await bot.get_mt5_accounts()

            if not mt5_accounts:
                log.warning("MT5 аккаунт жок. Жаңысын ачып жатабыз...")
                mt5_login = await bot.mt5_new_account()
                if not mt5_login:
                    log.error("MT5 аккаунт ачуу мүмкүн болгон жок")
                    await asyncio.sleep(60)
                    continue
            else:
                # Биринчи demo же financial аккаунтту алуу
                mt5_login = None
                for acc in mt5_accounts:
                    if acc.get("account_type") in ["demo", "financial"]:
                        mt5_login = acc["login"]
                        log.info(f"🎯 Колдонулуп жаткан MT5: {mt5_login} ({acc.get('account_type')})")
                        break
                if not mt5_login:
                    mt5_login = mt5_accounts[0]["login"]

            log.info(f"🚀 MT5 Бот иштеп баштады! Login: {mt5_login}")
            last_reset = datetime.now().date()

            while True:
                # Күнүнө лимит баштан башталышы
                today = datetime.now().date()
                if today != last_reset:
                    daily_loss   = 0.0
                    trades_today = 0
                    last_reset   = today
                    log.info("🔄 Күнүнө лимит баштан башталды")

                # Жоготуу лимити
                if daily_loss >= MAX_DAILY_LOSS:
                    log.warning(f"🛑 Күнүнө жоготуу лимити: ${daily_loss:.2f}. Токтоду.")
                    await asyncio.sleep(3600)
                    continue

                # MT5 баланс
                balance = await bot.mt5_get_balance(mt5_login)
                if balance is not None:
                    log.info(
                        f"💰 MT5 Баланс: ${balance:.2f} | "
                        f"Соодалар: {trades_today} | "
                        f"Жоготуу: ${daily_loss:.2f}"
                    )

                # Ар бир жуп үчүн сигнал текшерүү
                for pair in PAIRS:
                    if pair in active_trades:
                        # Ачык соода бар — убакытты текшер
                        trade = active_trades[pair]
                        elapsed = (datetime.now() - trade["open_time"]).seconds / 60
                        if elapsed >= 60:  # 1 саат өткөндөн кийин жабуу
                            success = await bot.close_trade(
                                mt5_login,
                                trade["order_id"],
                                PAIRS_MAP[pair],
                                trade["direction"],
                                TRADE_AMOUNT,
                            )
                            if success:
                                del active_trades[pair]
                        continue

                    signal = get_signal(pair)
                    if not signal:
                        log.info(f"⏳ {pair}: Сигнал жок")
                        continue

                    log.info(
                        f"📊 СИГНАЛ: {pair} {signal['direction']} | "
                        f"Ишеним: {signal['confidence']}/4 | "
                        f"RSI: {signal['rsi']} | "
                        f"Баа: {signal['price']}"
                    )

                    order_id = await bot.place_trade(signal, mt5_login)
                    if order_id:
                        active_trades[pair] = {
                            "order_id":  order_id,
                            "direction": signal["direction"],
                            "entry":     signal["price"],
                            "tp":        signal["tp"],
                            "sl":        signal["sl"],
                            "open_time": datetime.now(),
                        }
                        trades_today += 1

                    await asyncio.sleep(2)

                # Ping — байланыш сактоо
                await bot.ping()

                log.info("⏳ 15 мүнөт күтүү...")
                await asyncio.sleep(900)

        except websockets.exceptions.ConnectionClosed:
            log.warning("🔌 Байланыш үзүлдү. 10с кийин кайра туташат...")
            await asyncio.sleep(10)
        except KeyboardInterrupt:
            log.info("🛑 Бот токтотулду")
            break
        except Exception as e:
            log.error(f"Ката: {e}", exc_info=True)
            await asyncio.sleep(15)

# ==================== ИШТЕТҮҮ ====================
if __name__ == "__main__":
    log.info("=" * 55)
    log.info("   DERIV MT5 АВТО СООДА БОТ   ")
    log.info("=" * 55)
    log.info(f"Лот өлчөмү:        {TRADE_AMOUNT}")
    log.info(f"Күнүнө макс жоготуу: ${MAX_DAILY_LOSS}")
    log.info(f"Жуптар:            {', '.join(PAIRS)}")
    log.info(f"Таймфрейм:         {TIMEFRAME}")
    log.info("=" * 55)

    # Айлана-чөйрө өзгөрмөлөрүн текшерүү
    if DERIV_API_TOKEN == "YOUR_DERIV_TOKEN_HERE":
        log.warning("⚠️  DERIV_API_TOKEN коюлган жок!")
    if TWELVE_API_KEY == "YOUR_TWELVE_KEY_HERE":
        log.warning("⚠️  TWELVE_API_KEY коюлган жок!")

    asyncio.run(run_bot())
