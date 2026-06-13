import logging
import aiohttp
import sqlite3
import os
import re
import asyncio
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.dates as mdates
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image
import numpy as np
import io
import urllib.request

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
STARTING_BALANCE = 10.0
MAX_TOPUP = 1000.0
CHANNEL_URL = "https://t.me/cocoonsosun"
TON_CA_PATTERN = re.compile(r'\b(EQ|UQ|kQ|0:)[A-Za-z0-9_\-]{46,64}\b')
DEX_FEE = 0.003        # 0.3% DEX fee
NETWORK_FEE_GRAM = 0.05  # ~0.05 GRAM network fee

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── TON PRICE CACHE ─────────────────────────────────────────────────────────
_ton_cache = {"price": 0.0, "updated": 0}

async def get_ton_price():
    now = asyncio.get_event_loop().time()
    if now - _ton_cache["updated"] < 60 and _ton_cache["price"] > 0:
        return _ton_cache["price"]

    # 1. CoinGecko
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd",
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    p = float(data.get("the-open-network", {}).get("usd", 0))
                    if p > 0:
                        _ton_cache["price"] = p
                        _ton_cache["updated"] = now
                        return p
    except Exception as e:
        logger.warning(f"TON price CoinGecko: {e}")

    # 2. CoinPaprika (no rate limits)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.coinpaprika.com/v1/tickers/ton-the-open-network",
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    p = float(data.get("quotes", {}).get("USD", {}).get("price", 0))
                    if p > 0:
                        _ton_cache["price"] = p
                        _ton_cache["updated"] = now
                        logger.info(f"TON price from CoinPaprika: {p}")
                        return p
    except Exception as e:
        logger.warning(f"TON price CoinPaprika: {e}")

    # 3. Binance public API
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT",
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    p = float(data.get("price", 0))
                    if p > 0:
                        _ton_cache["price"] = p
                        _ton_cache["updated"] = now
                        logger.info(f"TON price from Binance: {p}")
                        return p
    except Exception as e:
        logger.warning(f"TON price Binance: {e}")

    # Return last known price if available, otherwise fail loudly
    if _ton_cache["price"] > 0:
        logger.warning("TON price: all APIs failed, using cached value")
        return _ton_cache["price"]

    logger.error("TON price: all APIs failed, no cached price available!")
    return 0.0

# ─── DB ───────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT,
        balance REAL DEFAULT 10.0, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        symbol TEXT, amount REAL, avg_buy_price REAL, ca TEXT DEFAULT '',
        avg_mcap REAL DEFAULT 0, UNIQUE(user_id, symbol))""")
    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        symbol TEXT, action TEXT, amount REAL, price REAL,
        price_buy REAL DEFAULT 0, total REAL, pnl_pct REAL DEFAULT 0,
        mcap_buy REAL DEFAULT 0, mcap_sell REAL DEFAULT 0, timestamp TEXT)""")
    for col in [
        "ALTER TABLE portfolio ADD COLUMN avg_mcap REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN mcap_buy REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN mcap_sell REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN price_buy REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN pnl_pct REAL DEFAULT 0",
        "ALTER TABLE portfolio ADD COLUMN ca TEXT DEFAULT ''",
    ]:
        try: c.execute(col)
        except: pass
    conn.commit(); conn.close()

def get_user(user_id, username=""):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (user_id,username,balance,created_at) VALUES (?,?,?,?)",
                  (user_id, username, STARTING_BALANCE, datetime.now().isoformat()))
        conn.commit()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = c.fetchone()
    conn.close(); return user

def get_portfolio(user_id):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("SELECT symbol,amount,avg_buy_price,ca,avg_mcap FROM portfolio WHERE user_id=? AND amount>0.000001", (user_id,))
    rows = c.fetchall(); conn.close(); return rows

def get_trades(user_id, limit=15):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("""SELECT symbol,action,amount,price,price_buy,total,pnl_pct,mcap_buy,mcap_sell,timestamp
                 FROM trades WHERE user_id=? ORDER BY id DESC LIMIT ?""", (user_id, limit))
    rows = c.fetchall(); conn.close(); return rows

def get_all_users_pnl():
    """Для лидерборда — считаем PnL каждого юзера из trades"""
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("""
        SELECT u.user_id, u.username, u.balance,
               COALESCE(SUM(CASE WHEN t.action='SELL' THEN t.total - t.amount * t.price_buy ELSE 0 END), 0) as realized_pnl
        FROM users u
        LEFT JOIN trades t ON u.user_id = t.user_id
        GROUP BY u.user_id
        ORDER BY realized_pnl DESC
        LIMIT 20
    """)
    rows = c.fetchall(); conn.close(); return rows

def update_balance(user_id, delta):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, user_id))
    conn.commit(); conn.close()

def set_balance(user_id, amount):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user_id))
    conn.commit(); conn.close()

def update_portfolio(user_id, symbol, amount_delta, price, ca="", mcap=0):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("SELECT amount,avg_buy_price,avg_mcap FROM portfolio WHERE user_id=? AND symbol=?", (user_id, symbol))
    row = c.fetchone()
    if row:
        old_amt, old_avg, old_mcap = row
        new_amt = old_amt + amount_delta
        if amount_delta > 0 and new_amt > 0:
            new_avg = (old_amt*old_avg + amount_delta*price) / new_amt
            new_mcap = (old_amt*old_mcap + amount_delta*mcap) / new_amt if mcap > 0 else old_mcap
        else:
            new_avg = old_avg; new_mcap = old_mcap
        if new_amt <= 0.000001:
            c.execute("DELETE FROM portfolio WHERE user_id=? AND symbol=?", (user_id, symbol))
        else:
            c.execute("UPDATE portfolio SET amount=?,avg_buy_price=?,avg_mcap=? WHERE user_id=? AND symbol=?",
                      (new_amt, new_avg, new_mcap, user_id, symbol))
    else:
        if amount_delta > 0:
            c.execute("INSERT INTO portfolio (user_id,symbol,amount,avg_buy_price,ca,avg_mcap) VALUES (?,?,?,?,?,?)",
                      (user_id, symbol, amount_delta, price, ca, mcap))
    conn.commit(); conn.close()

def get_position(user_id, symbol):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("SELECT amount,avg_buy_price,ca,avg_mcap FROM portfolio WHERE user_id=? AND symbol=?", (user_id, symbol))
    row = c.fetchone(); conn.close()
    return row if row else (0, 0, "", 0)

def save_trade(user_id, symbol, action, amount, price, price_buy, total, pnl_pct, mcap_buy=0, mcap_sell=0):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("""INSERT INTO trades (user_id,symbol,action,amount,price,price_buy,total,pnl_pct,mcap_buy,mcap_sell,timestamp)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (user_id, symbol, action, amount, price, price_buy, total, pnl_pct, mcap_buy, mcap_sell,
               datetime.now().strftime("%d.%m %H:%M")))
    conn.commit(); conn.close()

def reset_user(user_id):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("UPDATE users SET balance=? WHERE user_id=?", (STARTING_BALANCE, user_id))
    c.execute("DELETE FROM portfolio WHERE user_id=?", (user_id,))
    c.execute("DELETE FROM trades WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

# ─── TOKEN DATA ───────────────────────────────────────────────────────────────

async def _fetch_mcap_from_cmc(symbol: str) -> tuple:
    """CoinMarketCap — ищем по символу, возвращаем (mcap, fdv, is_fdv)"""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/search/quick?query={symbol}&limit=3",
                timeout=aiohttp.ClientTimeout(total=8),
                headers={"User-Agent": "Mozilla/5.0"}
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    coins = data.get("data", {}).get("cryptoCurrencyList", [])
                    for coin in coins:
                        # Ищем точное совпадение символа
                        if coin.get("symbol", "").upper() == symbol.upper():
                            stats = coin.get("quotes", [{}])[0].get("price", 0)
                            mc = float(coin.get("quotes", [{}])[0].get("marketCap") or 0)
                            fdv = float(coin.get("quotes", [{}])[0].get("fullyDilutedMarketCap") or 0)
                            if mc > 0:
                                return mc, fdv if fdv > 0 else mc, False
                            if fdv > 0:
                                return fdv, fdv, True
    except Exception as e:
        logger.warning(f"CMC: {e}")
    return 0.0, 0.0, True

async def _fetch_mcap_from_pools(ca: str) -> tuple:
    """Возвращает (mcap, is_fdv) из пулов"""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/pools?page=1",
                timeout=aiohttp.ClientTimeout(total=8),
                headers={"Accept": "application/json"}
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    pools = data.get("data", [])
                    if pools:
                        attrs = pools[0].get("attributes", {})
                        mc = float(attrs.get("market_cap_usd") or 0)
                        fdv = float(attrs.get("fully_diluted_valuation") or 0)
                        if mc > 0:
                            return mc, False   # реальный mcap
                        if fdv > 0:
                            return fdv, True   # FDV
    except:
        pass
    return 0.0, True

async def fetch_token_data(ca: str):
    """Returns (price_usd, symbol, fdv, is_fdv, liquidity, image_url, ath, created_at)"""
    ath = 0.0
    created_at = ""

    # 1. GeckoTerminal tokens endpoint
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}",
                    timeout=aiohttp.ClientTimeout(total=12),
                    headers={"Accept": "application/json"}
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        attrs = data.get("data", {}).get("attributes", {})
                        price = float(attrs.get("price_usd") or 0)
                        if price > 0:
                            symbol = attrs.get("symbol") or ca[:6].upper()
                            fdv = float(attrs.get("fdv_usd") or 0)
                            liq = float(attrs.get("total_reserve_in_usd") or 0)
                            image = attrs.get("image_url") or ""
                            # Всегда берём FDV из пулов для точности
                            try:
                                async with s.get(
                                    f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/pools?page=1",
                                    timeout=aiohttp.ClientTimeout(total=8),
                                    headers={"Accept": "application/json"}
                                ) as rp:
                                    if rp.status == 200:
                                        pd = await rp.json()
                                        pools = pd.get("data", [])
                                        if pools:
                                            pools.sort(key=lambda x: float(x.get("attributes", {}).get("reserve_in_usd") or 0), reverse=True)
                                            pa = pools[0].get("attributes", {})
                                            fdv = float(pa.get("fdv_usd") or 0) or fdv
                                            if liq == 0:
                                                liq = float(pa.get("reserve_in_usd") or 0)
                                            created_at = pa.get("pool_created_at", "")[:10]
                                            # ATH из 24h high * supply estimate
                                            ath_price = float(pa.get("price_change_percentage", {}).get("h24") or 0)
                            except Exception as e:
                                logger.warning(f"Pools extra: {e}")
                            if fdv <= 0:
                                mc2, is_fdv2 = await _fetch_mcap_from_pools(ca)
                                fdv = mc2
                            return price, symbol, fdv, True, liq, image, ath, created_at
                    elif r.status == 429:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    else:
                        break
        except Exception as e:
            logger.warning(f"GT attempt {attempt+1}: {e}")
            if attempt < 2: await asyncio.sleep(1.5)

    # 2. STON.fi
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.ston.fi/v1/assets/{ca}",
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json()
                        asset = data.get("asset", {})
                        price = float(asset.get("dex_price_usd") or 0)
                        if price > 0:
                            return price, asset.get("symbol") or ca[:6].upper(), 0.0, True, 0.0, "", 0.0, ""
        except Exception as e:
            logger.warning(f"STON {attempt+1}: {e}")
            if attempt < 1: await asyncio.sleep(1)

    # 3. DeDust pools
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/pools?dex=dedust",
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"Accept": "application/json"}
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    pools = data.get("data", [])
                    if pools:
                        attrs = pools[0].get("attributes", {})
                        price = float(attrs.get("base_token_price_usd") or attrs.get("quote_token_price_usd") or 0)
                        fdv = float(attrs.get("fdv_usd") or attrs.get("fully_diluted_valuation") or 0)
                        created = attrs.get("pool_created_at", "")[:10]
                        if price > 0:
                            return price, ca[:8].upper(), fdv, True, 0.0, "", 0.0, created
    except Exception as e:
        logger.warning(f"DeDust: {e}")

    # 4. TonAPI
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://tonapi.io/v2/jettons/{ca}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    symbol = data.get("metadata", {}).get("symbol") or ca[:8].upper()
                    price = float(data.get("dex_usd_price") or 0)
                    if price > 0:
                        return price, symbol, 0.0, True, 0.0, "", 0.0, ""
    except Exception as e:
        logger.warning(f"TonAPI: {e}")

    return 0.0, ca[:8].upper(), 0.0, True, 0.0, "", 0.0, ""

async def get_token_history(ca, timeframe="7D"):
    # Правильный формат: /ohlcv/{timeframe}?aggregate={n}
    gt_tf = {
        "1H":  ("minute", "5",  12),
        "6H":  ("hour",   "1",  6),
        "24H": ("hour",   "1",  24),
        "7D":  ("hour",   "4",  42),
        "30D": ("day",    "1",  30),
    }
    tf, aggregate, lim = gt_tf.get(timeframe, ("hour", "4", 42))

    # Шаг 1 — находим адрес пула
    pool_address = None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/pools?page=1",
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"Accept": "application/json"}
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    pools = data.get("data", [])
                    if pools:
                        pools.sort(
                            key=lambda x: float(x.get("attributes", {}).get("reserve_in_usd") or 0),
                            reverse=True
                        )
                        pool_address = pools[0].get("attributes", {}).get("address")
                        logger.info(f"Pool for {ca[:8]}: {pool_address}")
    except Exception as e:
        logger.warning(f"Pool lookup: {e}")

    if not pool_address:
        return []

    # Шаг 2 — OHLCV по адресу пула с правильным форматом URL
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as s:
                url = (f"https://api.geckoterminal.com/api/v2/networks/ton/pools/"
                       f"{pool_address}/ohlcv/{tf}?aggregate={aggregate}&limit={lim}")
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                 headers={"Accept": "application/json"}) as r:
                    if r.status == 200:
                        raw = await r.json()
                        candles = raw.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                        if len(candles) >= 2:
                            history = [[c[0] * 1000, float(c[4])] for c in candles]
                            logger.info(f"OHLCV OK: {len(history)} candles")
                            return history
                    elif r.status == 429:
                        await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"OHLCV attempt {attempt+1}: {e}")
            if attempt < 1:
                await asyncio.sleep(1)

    return []

# ─── FORMATTERS ───────────────────────────────────────────────────────────────

def format_price(p):
    if not p or p == 0: return "$0"
    if p < 0.000001: return f"${p:.10f}"
    if p < 0.001: return f"${p:.8f}"
    if p < 1: return f"${p:.6f}"
    return f"${p:.4f}"

def format_gram(v):
    if v is None or v == 0: return "0"
    neg = v < 0
    v = abs(v)
    if v >= 1:
        s = f"{v:.2f}".rstrip('0').rstrip('.')
    else:
        s = f"{v:.6f}".rstrip('0').rstrip('.')
    return f"-{s}" if neg else s

def format_mcap(v, is_fdv=False):
    label = "FDV" if is_fdv else "MCap"
    if not v or v <= 0: return f"{label}: N/A"
    if v >= 1_000_000_000: return f"{label}: ${v/1_000_000_000:.2f}B"
    if v >= 1_000_000: return f"{label}: ${v/1_000_000:.2f}M"
    if v >= 1_000: return f"{label}: ${v/1_000:.1f}K"
    return f"{label}: ${v:.0f}"

def format_mcap_val(v):
    if not v or v <= 0: return "N/A"
    if v >= 1_000_000_000: return f"${v/1_000_000_000:.2f}B"
    if v >= 1_000_000: return f"${v/1_000_000:.2f}M"
    if v >= 1_000: return f"${v/1_000:.1f}K"
    return f"${v:.0f}"

def format_x(buy_mcap, cur_mcap):
    if not buy_mcap or buy_mcap <= 0 or not cur_mcap or cur_mcap <= 0:
        return ""
    x = cur_mcap / buy_mcap
    if x >= 1:
        return f"{x:.2f}x 🚀" if x >= 2 else f"{x:.2f}x"
    else:
        return f"{x:.2f}x 📉"

def format_mcap_change(buy_mcap, cur_mcap):
    if not buy_mcap or buy_mcap <= 0 or not cur_mcap or cur_mcap <= 0:
        return ""
    pct = (cur_mcap - buy_mcap) / buy_mcap * 100
    if pct >= 0:
        return f"+{pct:.0f}% 🚀"
    return f"{pct:.0f}% 📉"

def pnl_arrow(pnl_pct):
    return "↑" if pnl_pct >= 0 else "↓"

def format_age(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        days = (datetime.utcnow() - dt).days
        if days < 1:
            return "today"
        elif days == 1:
            return "1 day ago"
        elif days < 7:
            return f"{days} days ago"
        elif days < 30:
            weeks = days // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        elif days < 365:
            months = days // 30
            return f"{months} month{'s' if months > 1 else ''} ago"
        else:
            years = days // 365
            return f"{years} year{'s' if years > 1 else ''} ago"
    except:
        return date_str

def dex_link(ca):
    return f"https://dexscreener.com/ton/{ca}"

def apply_fees(gram_amount, coin_amount, action="buy"):
    """DEX fee 0.3% + network fee + price impact"""
    dex = gram_amount * DEX_FEE
    network = NETWORK_FEE_GRAM
    # Price impact — зависит от размера сделки (упрощённо)
    impact_pct = min(gram_amount * 0.0001, 0.02)  # max 2%
    impact = gram_amount * impact_pct
    total_fee = dex + network + impact
    slip = random.uniform(0.003, 0.015)
    if action == "buy":
        coin_out = coin_amount * (1 - slip)
        gram_spent = gram_amount + total_fee
        return coin_out, gram_spent, dex, network, impact, slip * 100
    else:
        gram_out = gram_amount - total_fee
        return coin_amount, max(gram_out, 0), dex, network, impact, slip * 100

def load_token_image(url):
    if not url: return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            img = Image.open(io.BytesIO(r.read())).convert("RGBA").resize((48, 48))
            return np.array(img)
    except:
        return None

# ─── CHART ────────────────────────────────────────────────────────────────────

def generate_chart(symbol, history, image_arr=None, entry_price=None, mcap=None,
                   is_fdv=False, current_price=None, timeframe="7D"):
    if not history or len(history) < 2:
        return None
    try:
        timestamps = [datetime.fromtimestamp(p[0]/1000) for p in history]
        prices = [float(p[1]) for p in history]
        if not any(p > 0 for p in prices): return None
        is_up = prices[-1] >= prices[0]
        color = "#00e676" if is_up else "#ff1744"
        bg = "#0a0e1a"
        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor(bg); ax.set_facecolor(bg)
        ax.plot(timestamps, prices, color=color, linewidth=2.2, zorder=3)
        ax.fill_between(timestamps, prices, min(prices)*0.98, alpha=0.18, color=color, zorder=2)
        if entry_price and entry_price > 0:
            ax.axhline(y=entry_price, color="#ffd740", linewidth=1.2, linestyle="--", alpha=0.7, zorder=4)
            ax.text(timestamps[0], entry_price, "  Entry", color="#ffd740", fontsize=8, va="bottom")
        if timeframe in ("1H", "6H", "24H"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.xticks(rotation=30, color="#666688", fontsize=8)
        plt.yticks(color="#666688", fontsize=8)
        for sp in ax.spines.values(): sp.set_visible(False)
        ax.tick_params(colors="#666688", length=0)
        ax.grid(axis='y', color="#1a1f35", linewidth=0.8, zorder=1)
        change = (prices[-1] - prices[0]) / prices[0] * 100
        arrow = "▲" if is_up else "▼"
        title = f"{symbol}  {arrow} {change:+.1f}%  [{timeframe}]"
        if mcap and mcap > 0:
            label = "FDV" if is_fdv else "MCap"
            title += f"   {label} {format_mcap_val(mcap)}"
        ax.set_title(title, color=color, fontsize=12, fontweight="bold", pad=14)
        if image_arr is not None:
            try:
                im = OffsetImage(image_arr, zoom=0.6)
                ab = AnnotationBbox(im, (0.04, 0.90), xycoords='axes fraction',
                                    frameon=False, box_alignment=(0, 1))
                ax.add_artist(ab)
            except: pass
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor=bg)
        buf.seek(0); plt.close()
        return buf
    except Exception as e:
        logger.warning(f"Chart error: {e}")
        plt.close('all'); return None

# ─── PNL CARD ─────────────────────────────────────────────────────────────────

def generate_pnl_card(symbol, pnl_pct, pnl_gram, mcap_buy, mcap_sell,
                      entry_price, exit_price, image_arr=None):
    try:
        is_win = pnl_pct >= 0
        bg = "#0a1a10" if is_win else "#1a0a0a"
        accent = "#00e676" if is_win else "#ff1744"
        fig, ax = plt.subplots(figsize=(8, 4.5))
        fig.patch.set_facecolor(bg); ax.set_facecolor(bg)
        ax.set_xlim(0, 10); ax.set_ylim(0, 10); ax.axis("off")
        for lw, alpha in [(18, 0.04), (10, 0.08), (4, 0.18)]:
            rect = mpatches.FancyBboxPatch((0.1, 0.1), 9.8, 9.8,
                boxstyle="round,pad=0.1", linewidth=lw,
                edgecolor=accent, facecolor="none", alpha=alpha)
            ax.add_patch(rect)
        if image_arr is not None:
            try:
                im = OffsetImage(image_arr, zoom=0.9)
                ab = AnnotationBbox(im, (1.1, 8.2), frameon=False)
                ax.add_artist(ab)
            except: pass
        ax.text(2.2, 8.3, symbol, color="white", fontsize=20, fontweight="bold", va="center")
        arrow = "▲" if is_win else "▼"
        ax.text(5, 6.2, f"{arrow}  {pnl_pct:+.2f}%", color=accent, fontsize=34,
                fontweight="bold", ha="center", va="center")
        # X множитель
        if mcap_buy > 0 and mcap_sell > 0:
            x_mult = mcap_sell / mcap_buy
            ax.text(5, 5.0, f"{x_mult:.2f}x", color=accent, fontsize=18,
                    fontweight="bold", ha="center", alpha=0.85)
        gram_sign = "+" if pnl_gram >= 0 else ""
        ax.text(5, 3.9, f"{gram_sign}{format_gram(pnl_gram)} 💎 GRAM",
                color="#cccccc", fontsize=12, ha="center")
        if mcap_buy > 0 and mcap_sell > 0:
            mc_pct = (mcap_sell - mcap_buy) / mcap_buy * 100
            ax.text(5, 2.9,
                    f"MCap  {format_mcap_val(mcap_buy)}  →  {format_mcap_val(mcap_sell)}  ({mc_pct:+.0f}%)",
                    color="#888899", fontsize=9, ha="center")
        ax.text(3.0, 1.9, f"Entry: {format_price(entry_price)}", color="#666688", fontsize=9, ha="center")
        ax.text(7.0, 1.9, f"Exit: {format_price(exit_price)}", color="#666688", fontsize=9, ha="center")
        ax.text(5, 0.8, "🐙 OCTOtrade  ·  Simulator", color="#333355", fontsize=8, ha="center")
        plt.tight_layout(pad=0)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor=bg)
        buf.seek(0); plt.close()
        return buf
    except Exception as e:
        logger.warning(f"PnL card: {e}")
        plt.close('all'); return None

# ─── FSM ──────────────────────────────────────────────────────────────────────

class CAState(StatesGroup):
    entering_ca_amount = State()
    confirming_ca = State()

class SellState(StatesGroup):
    choosing_coin = State()
    entering_amount = State()
    confirming = State()

class TopUpState(StatesGroup):
    entering_topup = State()

class SetBalanceState(StatesGroup):
    entering_balance = State()

# ─── BOT ──────────────────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

def main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💼 Portfolio", callback_data="portfolio"),
        InlineKeyboardButton("📊 History", callback_data="history"),
        InlineKeyboardButton("🔴 Sell", callback_data="sell"),
        InlineKeyboardButton("💎 Balance", callback_data="balance_menu"),
        InlineKeyboardButton("🔄 Reset", callback_data="reset_confirm"),
    )
    return kb

def amount_keyboard(action, safe_sym):
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(
        InlineKeyboardButton("25%", callback_data=f"q_{action}_{safe_sym}_25"),
        InlineKeyboardButton("50%", callback_data=f"q_{action}_{safe_sym}_50"),
        InlineKeyboardButton("75%", callback_data=f"q_{action}_{safe_sym}_75"),
        InlineKeyboardButton("🔥 All In", callback_data=f"q_{action}_{safe_sym}_100"),
    )
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    return kb

def token_keyboard(safe_sym, ca):
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(
        InlineKeyboardButton("25%", callback_data=f"q_ca_{safe_sym}_25"),
        InlineKeyboardButton("50%", callback_data=f"q_ca_{safe_sym}_50"),
        InlineKeyboardButton("75%", callback_data=f"q_ca_{safe_sym}_75"),
        InlineKeyboardButton("🔥 All In", callback_data=f"q_ca_{safe_sym}_100"),
    )
    kb.add(
        InlineKeyboardButton("1H", callback_data=f"tf_{ca}_1H"),
        InlineKeyboardButton("6H", callback_data=f"tf_{ca}_6H"),
        InlineKeyboardButton("24H", callback_data=f"tf_{ca}_24H"),
        InlineKeyboardButton("7D", callback_data=f"tf_{ca}_7D"),
    )
    kb.add(InlineKeyboardButton("30D", callback_data=f"tf_{ca}_30D"),
           InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{ca}"))
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    return kb

def back_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    return kb

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    ton_price = await get_ton_price()
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💼 Portfolio", callback_data="portfolio"),
        InlineKeyboardButton("📊 History", callback_data="history"),
        InlineKeyboardButton("🔴 Sell", callback_data="sell"),
        InlineKeyboardButton("💎 Balance", callback_data="balance_menu"),
        InlineKeyboardButton("🔄 Reset", callback_data="reset_confirm"),
    )
    kb.add(InlineKeyboardButton("📢 Channel", url=CHANNEL_URL))
    await message.answer(
        f"👋 Welcome back, <b>{message.from_user.first_name}</b>\n\n"
        f"<b>OCTOtrade</b> — TON demo trading terminal\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Balance:  <b>{format_gram(user[2])} GRAM</b>  <i>(≈ ${user[2]*ton_price:,.2f})</i>\n"
        f"📈 TON:  <b>${ton_price:.4f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Drop any TON contract address in chat to trade.\n\n"
        f"<i>/setbalance — set a custom balance</i>",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.message_handler(commands=["setbalance"])
async def cmd_setbalance(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Usage: <code>/setbalance 500</code>", parse_mode="HTML"); return
    try:
        amount = float(args[1].replace(",", ""))
        if amount < 1 or amount > 10_000_000: raise ValueError
    except:
        await message.answer("❌ Enter a number between 1 and 10,000,000."); return
    set_balance(message.from_user.id, amount)
    await message.answer(f"✅ Balance updated — <b>{format_gram(amount)} 💎 GRAM</b>",
                         reply_markup=main_keyboard(), parse_mode="HTML")

@dp.callback_query_handler(text="back_main", state="*")
async def back_main(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    user = get_user(callback.from_user.id)
    ton_price = await get_ton_price()
    await callback.message.edit_text(
        f"🏠 <b>Main Menu</b>\n\n"
        f"💎 <b>{format_gram(user[2])} GRAM</b>  <i>(≈ ${user[2]*ton_price:,.2f})</i>\n\n"
        f"<i>Drop a CA to trade any token</i>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── TOKEN INFO ───────────────────────────────────────────────────────────────

async def send_token_info(chat_id, ca, state: FSMContext, edit_msg=None, timeframe="7D"):
    price, symbol, mcap, is_fdv, liq, image_url, ath, created_at = await fetch_token_data(ca)

    if price == 0:
        text = "❌ <b>Token not found</b>\n\nNot listed on DeDust / STON.fi yet."
        if edit_msg:
            await edit_msg.edit_text(text, reply_markup=back_keyboard(), parse_mode="HTML")
        else:
            await bot.send_message(chat_id, text, reply_markup=back_keyboard(), parse_mode="HTML")
        return

    ton_price = await get_ton_price()
    if ton_price <= 0:
        text = "❌ <b>Unable to fetch TON price</b>\n\nPlease try again in a moment."
        if edit_msg:
            await edit_msg.edit_text(text, reply_markup=back_keyboard(), parse_mode="HTML")
        else:
            await bot.send_message(chat_id, text, reply_markup=back_keyboard(), parse_mode="HTML")
        return
    user = get_user(chat_id)
    balance = user[2]
    price_in_gram = price / ton_price
    safe_sym = re.sub(r'[^A-Za-z0-9]', '', symbol)[:12]

    await state.finish()
    await state.update_data(ca=ca, symbol=symbol, price=price, mcap=mcap,
                            is_fdv=is_fdv, safe_sym=safe_sym, image_url=image_url, liq=liq)
    await CAState.entering_ca_amount.set()

    mcap_label = "FDV"
    mcap_str = format_mcap_val(mcap) if mcap > 0 else "N/A"
    liq_str = format_mcap_val(liq) if liq > 0 else "N/A"
    max_buy = balance / price_in_gram if price_in_gram > 0 else 0
    balance_usd = balance * ton_price
    price_usd_str = format_price(price)
    price_gram_str = format_gram(price_in_gram)
    created_str = f"\n🗓 Created:  <b>{format_age(created_at)}</b>" if created_at else ""

    caption = (
        f"🪙 <b>{symbol}</b>\n\n"
        f"💵 Price:  <b>{price_usd_str}</b>  ·  <b>{price_gram_str} 💎</b>\n"
        f"📊 FDV:  <b>{mcap_str}</b>\n"
        f"💧 Liquidity:  <b>{liq_str}</b>"
        f"{created_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Balance:  <b>{format_gram(balance)} GRAM</b>  <i>(≈ ${balance_usd:,.2f})</i>\n"
        f"📦 Max buy:  <b>{format_gram(max_buy)} {symbol}</b>\n\n"
        f"<code>{ca}</code>\n"
        f"🔗 <a href='{dex_link(ca)}'>DexScreener</a>\n\n"
        f"Enter amount in <b>💎 GRAM</b> or tap:"
    )
    # Telegram caption limit = 1024 chars
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    image_arr = load_token_image(image_url) if image_url else None
    history = await get_token_history(ca, timeframe)
    logger.info(f"Chart history for {ca[:8]}: {len(history)} candles")

    chart_buf = None
    if history and len(history) >= 2:
        try:
            chart_buf = generate_chart(symbol, history, image_arr=image_arr,
                                       mcap=mcap, is_fdv=is_fdv,
                                       current_price=price, timeframe=timeframe)
            logger.info(f"chart_buf generated: {chart_buf is not None}")
        except Exception as e:
            logger.error(f"generate_chart error: {e}")
            chart_buf = None

    kb = token_keyboard(safe_sym, ca)

    # Сначала отправляем контент, потом удаляем "Scanning..."
    try:
        if chart_buf:
            await bot.send_photo(chat_id, InputFile(chart_buf, filename="chart.png"),
                                 caption=caption, reply_markup=kb,
                                 parse_mode="HTML")
        else:
            await bot.send_message(chat_id, caption, reply_markup=kb,
                                   parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"send_token_info send error: {e}")
        await bot.send_message(chat_id, caption, reply_markup=kb,
                               parse_mode="HTML", disable_web_page_preview=True)
    finally:
        if edit_msg:
            try: await edit_msg.delete()
            except: pass

@dp.message_handler(lambda m: bool(TON_CA_PATTERN.search(m.text or "")), state="*")
async def auto_detect_ca(message: types.Message, state: FSMContext):
    ca = TON_CA_PATTERN.search(message.text).group(0)

    # В группах — только инфо без кнопок покупки
    if message.chat.type in ("group", "supergroup"):
        msg = await message.answer("🔍 Scanning...")
        await send_token_info_group(message.chat.id, ca, edit_msg=msg)
        return

    # В личке — проверяем есть ли позиция по этому ЦА
    user_id = message.from_user.id
    holdings = get_portfolio(user_id)
    existing = next((h for h in holdings if h[3] == ca), None)

    if existing:
        # Показываем позицию юзера по этому токену
        sym, amount, avg_price, ca_, avg_mcap = existing
        msg = await message.answer("🔍 Scanning...")
        ton_price = await get_ton_price()
        price, _, cur_mcap, is_fdv, _, _, _, _ = await fetch_token_data(ca)
        if not price:
            price = avg_price
        price_in_gram = price / ton_price if ton_price > 0 else 0
        avg_price_gram = avg_price / ton_price if ton_price > 0 else 0
        value = amount * price_in_gram
        pnl_pct = (price - avg_price) / avg_price * 100 if avg_price > 0 else 0
        pnl_gram = value - amount * avg_price_gram
        pnl_usd = pnl_gram * ton_price
        arrow = pnl_arrow(pnl_pct)
        x_str = format_x(avg_mcap, cur_mcap)
        mc_chg = format_mcap_change(avg_mcap, cur_mcap)
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("📤 Share PnL", callback_data=f"sharepnl_{ca}"),
            InlineKeyboardButton("🔴 Sell", callback_data=f"sellcoin_{sym}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{ca}"),
            InlineKeyboardButton("📊 Chart", callback_data=f"refresh_{ca}"),
        )
        text = (
            f"🪙 <b>{sym}</b>  ·  <i>Your position</i>\n\n"
            f"📦 Amount:  <b>{format_gram(amount)}</b>\n"
            f"💵 Entry:  <b>{format_price(avg_price)}</b>\n"
            f"💵 Now:  <b>{format_price(price)}</b>\n"
            f"📊 FDV:  {format_mcap_val(avg_mcap)} → {format_mcap_val(cur_mcap)}  {mc_chg}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{'🟢' if pnl_pct>=0 else '🔴'} P&L:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n"
            f"💎 P&L GRAM:  <b>{'+' if pnl_gram>=0 else ''}{format_gram(pnl_gram)}</b>  <i>(≈ ${pnl_usd:+,.2f})</i>\n"
            f"💎 Value:  <b>{format_gram(value)} GRAM</b>  <i>(≈ ${value*ton_price:,.2f})</i>"
        )
        try: await msg.delete()
        except: pass
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    msg = await message.answer("🔍 Scanning...")
    await send_token_info(message.chat.id, ca, state, edit_msg=msg)


async def send_token_info_group(chat_id, ca, edit_msg=None):
    """Упрощённая карточка токена для групповых чатов — без кнопок покупки"""
    price, symbol, mcap, is_fdv, liq, image_url, ath, created_at = await fetch_token_data(ca)
    if not price:
        try: await edit_msg.delete()
        except: pass
        return
    ton_price = await get_ton_price()
    mcap_str = format_mcap_val(mcap) if mcap > 0 else "N/A"
    liq_str = format_mcap_val(liq) if liq > 0 else "N/A"
    created_str = f"\n🗓 Created:  <b>{format_age(created_at)}</b>" if created_at else ""
    text = (
        f"🪙 <b>{symbol}</b>\n\n"
        f"💵 Price:  <b>{format_price(price)}</b>\n"
        f"📊 FDV:  <b>{mcap_str}</b>\n"
        f"💧 Liquidity:  <b>{liq_str}</b>"
        f"{created_str}\n\n"
        f"<code>{ca}</code>\n"
        f"🔗 <a href='{dex_link(ca)}'>DexScreener</a>\n\n"
        f"<i>Trade on @TentaTrading_Bot</i>"
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🐙 Open in OCTOtrade", url=f"https://t.me/TentaTrading_Bot?start={ca}"))
    try: await edit_msg.delete()
    except: pass
    await bot.send_message(chat_id, text, reply_markup=kb,
                           parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query_handler(lambda c: c.data.startswith("refresh_"))
async def refresh_token(callback: types.CallbackQuery, state: FSMContext):
    ca = callback.data[8:]
    await callback.answer("Refreshing...")
    try: await callback.message.delete()
    except: pass
    await send_token_info(callback.message.chat.id, ca, state)

@dp.callback_query_handler(lambda c: c.data.startswith("tf_"))
async def change_timeframe(callback: types.CallbackQuery, state: FSMContext):
    # tf_CA_7D
    parts = callback.data.split("_", 2)
    if len(parts) < 3: return
    ca = parts[1]; tf = parts[2]
    await callback.answer(f"Loading {tf}...")
    try: await callback.message.delete()
    except: pass
    await send_token_info(callback.message.chat.id, ca, state, timeframe=tf)

@dp.callback_query_handler(lambda c: c.data.startswith("sharepnl_"))
async def share_pnl(callback: types.CallbackQuery):
    ca = callback.data[9:]
    user_id = callback.from_user.id
    holdings = get_portfolio(user_id)
    existing = next((h for h in holdings if h[3] == ca), None)
    if not existing:
        await callback.answer("❌ Position not found", show_alert=True); return

    await callback.answer("⏳ Generating...")
    sym, amount, avg_price, ca_, avg_mcap = existing
    ton_price = await get_ton_price()
    price, _, cur_mcap, _, _, image_url, _, _ = await fetch_token_data(ca)
    if not price:
        price = avg_price

    price_in_gram = price / ton_price if ton_price > 0 else 0
    avg_price_gram = avg_price / ton_price if ton_price > 0 else 0
    value = amount * price_in_gram
    pnl_pct = (price - avg_price) / avg_price * 100 if avg_price > 0 else 0
    pnl_gram = value - amount * avg_price_gram
    ton_start = amount * avg_price_gram
    ton_end = value

    image_arr = load_token_image(image_url) if image_url else None
    card = generate_pnl_card(
        symbol=sym,
        pnl_pct=pnl_pct,
        pnl_gram=pnl_gram,
        entry_price=avg_price,
        exit_price=price,
        mcap_buy=avg_mcap,
        mcap_sell=cur_mcap or avg_mcap,
        image_arr=image_arr
    )
    if card:
        caption = (
            f"🔥 <b>Trade Result</b>\n\n"
            f"<b>{sym}</b>\n\n"
            f"Entry:  <b>{format_mcap_val(avg_mcap)} FDV</b>\n"
            f"Exit:  <b>{format_mcap_val(cur_mcap or avg_mcap)} FDV</b>\n\n"
            f"{'🟢' if pnl_pct>=0 else '🔴'} <b>{pnl_pct:+.2f}%</b>\n\n"
            f"<b>{format_gram(ton_start)} TON → {format_gram(ton_end)} TON</b>\n\n"
            f"🐙 OCTOtrade"
        )
        await bot.send_photo(user_id, InputFile(card, filename="pnl.png"),
                             caption=caption, parse_mode="HTML")
    else:
        await callback.answer("❌ Failed to generate card", show_alert=True)

# ─── BUY ──────────────────────────────────────────────────────────────────────

@dp.message_handler(state=CAState.entering_ca_amount)
async def ca_amount_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    symbol=data["symbol"]; price=data["price"]; mcap=data.get("mcap", 0)
    is_fdv=data.get("is_fdv", True)
    user = get_user(message.from_user.id); balance = user[2]
    ton_price = await get_ton_price()
    price_in_gram = price / ton_price
    text_input = message.text.strip().lower()
    try:
        gram_input = balance if text_input == "all" else float(text_input)
        if gram_input <= 0: raise ValueError
        if gram_input > balance:
            await message.answer(f"❌ Insufficient. Balance: {format_gram(balance)} GRAM"); return
        coin_raw = gram_input / price_in_gram
        coin_out, gram_spent, dex_fee, net_fee, impact, slip = apply_fees(gram_input, coin_raw, "buy")
        if gram_spent > balance:
            await message.answer(f"❌ Not enough to cover fees. Need {format_gram(gram_spent)} GRAM"); return
    except:
        await message.answer("❌ Enter a number, e.g. <code>5</code>", parse_mode="HTML"); return

    mcap_label = "FDV" if is_fdv else "MCap"
    await state.update_data(gram_amount=gram_spent, coin_amount=coin_out, mcap=mcap,
                            dex_fee=dex_fee, net_fee=net_fee, slip=slip)
    await CAState.confirming_ca.set()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Confirm Swap", callback_data="confirm_ca_trade"),
           InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
    await message.answer(
        f"✅ <b>Confirm Buy</b>\n\n"
        f"🪙 {symbol}:  <b>{format_gram(coin_out)}</b>\n"
        f"💵 Price:  {format_price(price)}\n"
        f"📊 {mcap_label}:  {format_mcap_val(mcap)}\n\n"
        f"💎 Cost:  <b>{format_gram(gram_spent)} GRAM</b>\n"
        f"   ├ DEX fee:  {format_gram(dex_fee)} GRAM\n"
        f"   ├ Network:  {format_gram(net_fee)} GRAM\n"
        f"   └ Slippage:  -{slip:.2f}%",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="confirm_ca_trade", state=CAState.confirming_ca)
async def confirm_ca_trade(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ca=data["ca"]; symbol=data["symbol"]; price=data["price"]
    gram_amount=data.get("gram_amount", 0); coin_amount=data.get("coin_amount", 0)
    mcap=data.get("mcap", 0)
    user_id = callback.from_user.id

    await callback.message.edit_text(
        f"⏳ <b>Processing swap...</b>\n\n"
        f"🔄 Routing through DEX\n"
        f"💎 {format_gram(gram_amount)} GRAM  →  {symbol}",
        parse_mode="HTML"
    )
    await asyncio.sleep(2)

    update_balance(user_id, -gram_amount)
    update_portfolio(user_id, symbol, coin_amount, price, ca, mcap)
    save_trade(user_id, symbol, "BUY", coin_amount, price, price, gram_amount, 0.0, mcap, 0)
    await state.finish()
    user = get_user(user_id)
    ton_price = await get_ton_price()

    await callback.message.edit_text(
        f"✅ <b>Swap Filled</b>\n\n"
        f"🪙 Received:  <b>{format_gram(coin_amount)} {symbol}</b>\n"
        f"💎 Spent:  <b>{format_gram(gram_amount)} GRAM</b>\n"
        f"💵 Entry:  {format_price(price)}\n"
        f"📊 {format_mcap(mcap, data.get('is_fdv', True))}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Balance:  <b>{format_gram(user[2])} GRAM</b>  <i>(≈ ${user[2]*ton_price:,.2f})</i>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

    # Send chart after buy confirmation
    ca = data.get("ca", "")
    image_url = data.get("image_url", "")
    if ca:
        try:
            image_arr = load_token_image(image_url) if image_url else None
            history = await get_token_history(ca, "7D")
            chart_buf = generate_chart(symbol, history, image_arr=image_arr,
                                       entry_price=price, mcap=mcap,
                                       is_fdv=data.get("is_fdv", True),
                                       current_price=price, timeframe="7D")
            if chart_buf:
                await bot.send_photo(
                    user_id, InputFile(chart_buf, filename="chart.png"),
                    caption=f"📊 <b>{symbol}</b>  ·  Entry {format_price(price)}  ·  {format_mcap(mcap, data.get('is_fdv', True))}",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"Post-buy chart error: {e}")

# ─── QUICK % ─────────────────────────────────────────────────────────────────

@dp.callback_query_handler(lambda c: c.data.startswith("q_"), state="*")
async def quick_amount(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 4: return
    action = parts[1]; pct = int(parts[-1])
    data = await state.get_data()
    price = data.get("price", 0)
    symbol = data.get("symbol", "")
    mcap = data.get("mcap", 0)
    is_fdv = data.get("is_fdv", True)
    if not price: await callback.answer("❌ Price unavailable", show_alert=True); return

    ton_price = await get_ton_price()
    price_in_gram = price / ton_price
    user = get_user(callback.from_user.id); balance = user[2]
    mcap_label = "FDV" if is_fdv else "MCap"

    if action == "ca":
        if pct == 100:
            # ALL IN — резервируем комиссию из баланса чтобы не уйти в минус
            fee_reserve = NETWORK_FEE_GRAM + balance * DEX_FEE + balance * 0.0001
            gram_input = max(balance - fee_reserve, 0)
        else:
            gram_input = balance * pct / 100
        if gram_input <= 0: await callback.answer("❌ Insufficient balance", show_alert=True); return
        coin_raw = gram_input / price_in_gram
        coin_out, gram_spent, dex_fee, net_fee, impact, slip = apply_fees(gram_input, coin_raw, "buy")
        # Финальная защита — не тратить больше чем есть
        if gram_spent > balance:
            gram_spent = balance
            coin_out = coin_out * (balance / gram_spent) if gram_spent > 0 else 0
        await state.update_data(gram_amount=gram_spent, coin_amount=coin_out, mcap=mcap, slip=slip)
        await CAState.confirming_ca.set()
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ Confirm Swap", callback_data="confirm_ca_trade"),
               InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
        await callback.message.edit_text(
            f"✅ <b>Confirm Buy — {pct}%</b>\n\n"
            f"🪙 {symbol}:  <b>{format_gram(coin_out)}</b>\n"
            f"💵 Price:  {format_price(price)}\n"
            f"📊 {mcap_label}:  {format_mcap_val(mcap)}\n\n"
            f"💎 Cost:  <b>{format_gram(gram_spent)} GRAM</b>  <i>(≈ ${gram_spent*ton_price:,.2f})</i>\n"
            f"   ├ DEX fee:  {format_gram(dex_fee)} GRAM\n"
            f"   ├ Network:  {format_gram(net_fee)} GRAM\n"
            f"   └ Slippage:  -{slip:.2f}%",
            reply_markup=kb, parse_mode="HTML"
        )

    elif action == "sell":
        held, avg_price, ca, avg_mcap = get_position(callback.from_user.id, symbol)
        if held <= 0: await callback.answer("❌ No position", show_alert=True); return
        coin_amount = held * pct / 100
        gram_raw = coin_amount * price_in_gram
        avg_price_gram = avg_price / ton_price
        _, gram_received, dex_fee, net_fee, impact, slip = apply_fees(gram_raw, coin_amount, "sell")
        pnl_pct = (price - avg_price) / avg_price * 100 if avg_price > 0 else 0
        pnl_gram = gram_received - coin_amount * avg_price_gram
        x_str = format_x(avg_mcap, mcap or avg_mcap)
        mc_chg = format_mcap_change(avg_mcap, mcap or avg_mcap)
        await state.update_data(coin_amount=coin_amount, gram_received=gram_received,
                                pnl_pct=pnl_pct, pnl_gram=pnl_gram,
                                avg_price=avg_price, avg_mcap=avg_mcap,
                                cur_mcap=mcap or avg_mcap, ca=ca)
        await SellState.confirming.set()
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ Confirm Sell", callback_data="confirm_sell"),
               InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
        arrow = pnl_arrow(pnl_pct)
        pnl_usd = pnl_gram * ton_price
        await callback.message.edit_text(
            f"✅ <b>Confirm Sell — {pct}%</b>\n\n"
            f"🪙 {symbol}:  <b>{format_gram(coin_amount)}</b>\n"
            f"💵 Price:  {format_price(price)}\n"
            f"💎 Receive:  <b>{format_gram(gram_received)} GRAM</b>  <i>(≈ ${gram_received*ton_price:,.2f})</i>\n\n"
            f"{'🟢' if pnl_pct>=0 else '🔴'} P&L:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n"
            f"{'🟢' if pnl_gram>=0 else '🔴'} P&L $:  <b>{'+' if pnl_gram>=0 else ''}{format_gram(pnl_gram)} GRAM</b>  <i>(≈ ${pnl_usd:+,.2f})</i>\n"
            f"📊 MCap:  {format_mcap_val(avg_mcap)} → {format_mcap_val(mcap or avg_mcap)}  {mc_chg}",
            reply_markup=kb, parse_mode="HTML"
        )

# ─── SELL ─────────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="sell")
async def sell_menu(callback: types.CallbackQuery):
    await SellState.choosing_coin.set()
    holdings = get_portfolio(callback.from_user.id)
    if not holdings:
        await callback.message.edit_text(
            "📭 <b>No open positions</b>\n\nDrop a CA to open a trade.",
            reply_markup=back_keyboard(), parse_mode="HTML"); return
    kb = InlineKeyboardMarkup(row_width=1)
    for sym, amount, avg_price, ca, avg_mcap in holdings:
        kb.add(InlineKeyboardButton(f"🔴 {sym}  ·  {format_gram(amount)}", callback_data=f"sellcoin_{sym}"))
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    await callback.message.edit_text("🔴 <b>Sell Position</b>\n\nSelect:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query_handler(lambda c: c.data.startswith("sellcoin_"), state=SellState.choosing_coin)
async def sell_coin_chosen(callback: types.CallbackQuery, state: FSMContext):
    symbol = callback.data[9:]
    held, avg_price, ca, avg_mcap = get_position(callback.from_user.id, symbol)
    if held <= 0:
        await callback.message.edit_text("❌ Position not found.", reply_markup=back_keyboard())
        await state.finish(); return

    if ca:
        price, _, cur_mcap, is_fdv, _, image_url, _, _ = await fetch_token_data(ca)
    else:
        price, cur_mcap, is_fdv, image_url = avg_price, 0, True, ""
    if not price:
        price = avg_price
    ton_price = await get_ton_price()
    price_in_gram = price / ton_price
    avg_price_gram = avg_price / ton_price
    pnl_pct = (price - avg_price) / avg_price * 100 if avg_price > 0 else 0
    safe_sym = re.sub(r'[^A-Za-z0-9]', '', symbol)[:12]
    arrow = pnl_arrow(pnl_pct)
    x_str = format_x(avg_mcap, cur_mcap)
    mc_chg = format_mcap_change(avg_mcap, cur_mcap)

    await state.update_data(symbol=symbol, price=price, ca=ca, safe_sym=safe_sym,
                            held=held, avg_price=avg_price, avg_mcap=avg_mcap,
                            cur_mcap=cur_mcap, image_url=image_url, mcap=cur_mcap, is_fdv=is_fdv)
    await SellState.entering_amount.set()

    mcap_line = ""
    if avg_mcap > 0:
        mcap_line = f"\n📊 MCap:  {format_mcap_val(avg_mcap)} → {format_mcap_val(cur_mcap)}  {mc_chg}"

    await callback.message.edit_text(
        f"🔴 <b>Sell {symbol}</b>\n\n"
        f"💵 Price:  <b>{format_price(price)}</b>\n"
        f"📦 Held:  <b>{format_gram(held)} {symbol}</b>\n"
        f"📈 Entry:  {format_price(avg_price)}{mcap_line}\n"
        f"{'🟢' if pnl_pct>=0 else '🔴'} P&L:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n\n"
        f"Enter amount or tap:",
        reply_markup=amount_keyboard("sell", safe_sym), parse_mode="HTML"
    )

@dp.message_handler(state=SellState.entering_amount)
async def sell_amount_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    symbol=data["symbol"]; price=data["price"]; held=data["held"]
    avg_price=data["avg_price"]; avg_mcap=data.get("avg_mcap",0); cur_mcap=data.get("cur_mcap",0)
    ton_price = await get_ton_price()
    price_in_gram = price / ton_price
    avg_price_gram = avg_price / ton_price
    text_input = message.text.strip().lower()
    try:
        coin_amount = held if text_input == "all" else float(text_input)
        if coin_amount <= 0: raise ValueError
        if coin_amount > held + 0.000001:
            await message.answer(f"❌ You only have {format_gram(held)} {symbol}"); return
    except:
        await message.answer("❌ Enter a number", parse_mode="HTML"); return

    gram_raw = coin_amount * price_in_gram
    _, gram_received, dex_fee, net_fee, impact, slip = apply_fees(gram_raw, coin_amount, "sell")
    pnl_pct = (price - avg_price) / avg_price * 100 if avg_price > 0 else 0
    pnl_gram = gram_received - coin_amount * avg_price_gram
    arrow = pnl_arrow(pnl_pct)
    x_str = format_x(avg_mcap, cur_mcap)

    await state.update_data(coin_amount=coin_amount, gram_received=gram_received,
                            pnl_pct=pnl_pct, pnl_gram=pnl_gram)
    await SellState.confirming.set()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Confirm Sell", callback_data="confirm_sell"),
           InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
    await message.answer(
        f"✅ <b>Confirm Sell</b>\n\n"
        f"🪙 {format_gram(coin_amount)} <b>{symbol}</b>\n"
        f"💵 Price:  {format_price(price)}\n"
        f"💎 Receive:  <b>{format_gram(gram_received)} GRAM</b>\n\n"
        f"{'🟢' if pnl_pct>=0 else '🔴'} P&L:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="confirm_sell", state=SellState.confirming)
async def confirm_sell(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    symbol=data["symbol"]; price=data["price"]; coin_amount=data["coin_amount"]
    gram_received=data["gram_received"]; pnl_pct=data["pnl_pct"]; pnl_gram=data["pnl_gram"]
    avg_price=data["avg_price"]; avg_mcap=data.get("avg_mcap",0); cur_mcap=data.get("cur_mcap",0)
    image_url=data.get("image_url","")
    user_id = callback.from_user.id
    arrow = pnl_arrow(pnl_pct)
    x_str = format_x(avg_mcap, cur_mcap)
    mc_chg = format_mcap_change(avg_mcap, cur_mcap)

    await callback.message.edit_text("⏳ <b>Processing sell...</b>", parse_mode="HTML")
    await asyncio.sleep(2)

    update_balance(user_id, gram_received)
    update_portfolio(user_id, symbol, -coin_amount, price)
    save_trade(user_id, symbol, "SELL", coin_amount, price, avg_price,
               gram_received, pnl_pct, avg_mcap, cur_mcap)
    await state.finish()

    user = get_user(user_id)
    ton_price = await get_ton_price()
    gram_spent = coin_amount * (avg_price / ton_price) if ton_price > 0 else 0
    pnl_usd = pnl_gram * ton_price

    await callback.message.edit_text(
        f"{'🟢' if pnl_pct>=0 else '🔴'} <b>Position Closed</b>\n\n"
        f"🪙 {symbol}:  <b>{format_gram(coin_amount)}</b>\n\n"
        f"💎 <b>{format_gram(gram_spent)} GRAM  ——›  {format_gram(gram_received)} GRAM</b>\n\n"
        f"{'🟢' if pnl_pct>=0 else '🔴'} P&L:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n"
        f"{'🟢' if pnl_gram>=0 else '🔴'} P&L:  <b>{'+' if pnl_gram>=0 else ''}{format_gram(pnl_gram)} GRAM</b>  <i>(≈ ${pnl_usd:+,.2f})</i>\n"
        f"📊 FDV:  {format_mcap_val(avg_mcap)} → {format_mcap_val(cur_mcap)}  {mc_chg}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Balance:  <b>{format_gram(user[2])} GRAM</b>  <i>(≈ ${user[2]*ton_price:,.2f})</i>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

    image_arr = load_token_image(image_url) if image_url else None
    pnl_buf = generate_pnl_card(symbol, pnl_pct, pnl_gram, avg_mcap, cur_mcap, avg_price, price, image_arr)
    if pnl_buf:
        await bot.send_photo(user_id, InputFile(pnl_buf, filename="pnl.png"),
                             caption=f"{'🟢' if pnl_pct>=0 else '🔴'} <b>{symbol}</b>  {pnl_pct:+.2f}% {arrow}  {x_str}",
                             parse_mode="HTML")

# ─── PORTFOLIO ───────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="portfolio")
async def show_portfolio(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    balance = user[2]
    holdings = get_portfolio(callback.from_user.id)
    ton_price = await get_ton_price()

    if not holdings:
        await callback.message.edit_text(
            f"💼 <b>Portfolio</b>\n\n"
            f"💎 Available:  <b>{format_gram(balance)} GRAM</b>  <i>(≈ ${balance*ton_price:,.2f})</i>\n\n"
            f"<i>No open positions. Drop a CA to trade.</i>",
            reply_markup=back_keyboard(), parse_mode="HTML"); return

    # Параллельно тянем цены всех токенов
    async def fetch_one(row):
        sym, amount, avg_price, ca, avg_mcap = row
        if ca:
            try:
                price, fetched_sym, cur_mcap, is_fdv, _, _, _, _ = await fetch_token_data(ca)
                # Если в БД кусок ЦА а не символ — берём свежий
                if fetched_sym and len(fetched_sym) <= 12 and not re.match(r'^[A-Z0-9]{8,}$', fetched_sym):
                    sym = fetched_sym
                elif fetched_sym and fetched_sym != ca[:8].upper():
                    sym = fetched_sym
                if not price:
                    price = avg_price
            except:
                price, cur_mcap, is_fdv = avg_price, avg_mcap, True
        else:
            price, cur_mcap, is_fdv = avg_price, avg_mcap, True
        return sym, amount, avg_price, ca, avg_mcap, price, cur_mcap

    results = await asyncio.gather(*[fetch_one(row) for row in holdings], return_exceptions=True)

    total_value = balance
    lines = [f"💼 <b>Portfolio</b>\n\n💎 Balance:  <b>{format_gram(balance)} GRAM</b>  <i>(≈ ${balance*ton_price:,.2f})</i>\n"]

    for res in results:
        if isinstance(res, Exception):
            continue
        sym, amount, avg_price, ca, avg_mcap, price, cur_mcap = res
        price_in_gram = price / ton_price if ton_price > 0 else 0
        avg_price_gram = avg_price / ton_price if ton_price > 0 else 0
        value = amount * price_in_gram
        pnl_pct = (price - avg_price) / avg_price * 100 if avg_price > 0 else 0
        pnl_gram = value - amount * avg_price_gram
        total_value += value
        arrow = pnl_arrow(pnl_pct)
        x_str = format_x(avg_mcap, cur_mcap)
        mc_chg = format_mcap_change(avg_mcap, cur_mcap)
        dex = f"<a href='{dex_link(ca)}'>{sym}</a>" if ca else f"<b>{sym}</b>"

        lines.append(
            f"{'🟢' if pnl_pct>=0 else '🔴'} {dex}\n"
            f"   📦 {format_gram(amount)}  ·  entry {format_price(avg_price)}\n"
            f"   📊 {format_mcap_val(avg_mcap)} → {format_mcap_val(cur_mcap)}  {mc_chg}\n"
            f"   {'🟢' if pnl_pct>=0 else '🔴'} <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n"
            f"   💎 {format_gram(value)} GRAM  <i>(≈ ${value*ton_price:,.2f})</i>  ({'+' if pnl_gram>=0 else ''}{format_gram(pnl_gram)})"
        )

    lines.append(
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Total value:  <b>{format_gram(total_value)} 💎 GRAM</b>  <i>(≈ ${total_value*ton_price:,.2f})</i>"
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔄 Refresh", callback_data="portfolio"))
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    await callback.message.edit_text("\n".join(lines), reply_markup=kb,
                                     parse_mode="HTML", disable_web_page_preview=True)

# ─── LEADERBOARD ─────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="leaderboard")
async def show_leaderboard(callback: types.CallbackQuery):
    rows = get_all_users_pnl()
    if not rows:
        await callback.message.edit_text("🏆 <b>Leaderboard</b>\n\n<i>No traders yet.</i>",
                                         reply_markup=back_keyboard(), parse_mode="HTML"); return
    ton_price = await get_ton_price()
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Leaderboard — Top Traders</b>\n"]
    for i, (uid, uname, balance, realized_pnl) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = f"@{uname}" if uname else f"User{uid}"
        sign = "+" if realized_pnl >= 0 else ""
        lines.append(
            f"{medal} <b>{name}</b>\n"
            f"   💎 {format_gram(balance)} GRAM  ·  P&L: {sign}{format_gram(realized_pnl)} 💎"
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── HISTORY ─────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="history")
async def show_history(callback: types.CallbackQuery):
    trades = get_trades(callback.from_user.id, 15)
    if not trades:
        await callback.message.edit_text(
            "📊 <b>Trade History</b>\n\n<i>No trades yet.</i>",
            reply_markup=back_keyboard(), parse_mode="HTML"); return
    lines = ["📊 <b>Trade History</b>\n"]
    for sym, action, amount, price, price_buy, total, pnl_pct, mcap_buy, mcap_sell, ts in trades:
        if action == "BUY":
            lines.append(
                f"🟢 {ts}  ·  <b>{sym}</b>\n"
                f"   +{format_gram(amount)}  @  {format_price(price)}\n"
                f"   💎 {format_gram(total)}  ·  {format_mcap(mcap_buy, False)}"
            )
        else:
            arrow = pnl_arrow(pnl_pct)
            x_str = format_x(mcap_buy, mcap_sell)
            mc_chg = format_mcap_change(mcap_buy, mcap_sell)
            lines.append(
                f"🔴 {ts}  ·  <b>{sym}</b>\n"
                f"   -{format_gram(amount)}  @  {format_price(price)}\n"
                f"   💎 {format_gram(total)}\n"
                f"   {'🟢' if pnl_pct>=0 else '🔴'} <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}  {mc_chg}"
            )
    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── BALANCE ─────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="balance_menu")
async def balance_menu(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    ton_price = await get_ton_price()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"➕ Add GRAM (max {MAX_TOPUP:,.0f})", callback_data="topup"))
    kb.add(InlineKeyboardButton("✏️ Set balance", callback_data="setbalance_menu"))
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    await callback.message.edit_text(
        f"💎 <b>Balance</b>\n\n"
        f"<b>{format_gram(user[2])} GRAM</b>  <i>(≈ ${user[2]*ton_price:,.2f})</i>\n\n"
        f"1 💎 GRAM  =  ${ton_price:.4f}  <i>(live TON price)</i>",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="setbalance_menu")
async def setbalance_menu(callback: types.CallbackQuery):
    await SetBalanceState.entering_balance.set()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"✏️ <b>Set Balance</b>\n\nCurrent: <b>{format_gram(user[2])} 💎 GRAM</b>\n\nEnter new amount:",
        reply_markup=back_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(state=SetBalanceState.entering_balance)
async def setbalance_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", ""))
        if amount < 1 or amount > 10_000_000: raise ValueError
    except:
        await message.answer("❌ Enter a number between 1 and 10,000,000"); return
    set_balance(message.from_user.id, amount)
    await state.finish()
    await message.answer(f"✅ Balance set — <b>{format_gram(amount)} 💎 GRAM</b>",
                         reply_markup=main_keyboard(), parse_mode="HTML")

@dp.callback_query_handler(text="topup")
async def topup_start(callback: types.CallbackQuery):
    await TopUpState.entering_topup.set()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"➕ <b>Add GRAM</b>\n\nCurrent: <b>{format_gram(user[2])} GRAM</b>\nMax: <b>{MAX_TOPUP:,.0f} GRAM</b>\n\nHow much?",
        reply_markup=back_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(state=TopUpState.entering_topup)
async def topup_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0: raise ValueError
        if amount > MAX_TOPUP:
            await message.answer(f"❌ Max {MAX_TOPUP:,.0f} GRAM per top-up"); return
    except:
        await message.answer(f"❌ Enter a number between 1 and {MAX_TOPUP:,.0f}"); return
    update_balance(message.from_user.id, amount)
    await state.finish()
    user = get_user(message.from_user.id)
    ton_price = await get_ton_price()
    await message.answer(
        f"✅ Added <b>{format_gram(amount)} 💎 GRAM</b>\n"
        f"💎 Balance: <b>{format_gram(user[2])} GRAM</b>  <i>(≈ ${user[2]*ton_price:,.2f})</i>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── RESET ───────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="reset_confirm")
async def reset_confirm(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Reset", callback_data="reset_do"),
           InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
    await callback.message.edit_text(
        f"⚠️ <b>Reset Account?</b>\n\nAll positions and history will be wiped.\n"
        f"Balance resets to <b>{STARTING_BALANCE} 💎 GRAM</b>.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="reset_do")
async def reset_do(callback: types.CallbackQuery):
    reset_user(callback.from_user.id)
    await callback.message.edit_text(
        f"🔄 Account reset.\n💎 Balance: <b>{STARTING_BALANCE} GRAM</b>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(content_types=types.ContentType.TEXT)
async def unknown_message(message: types.Message):
    await message.answer("🔍 Drop a TON contract address to trade.\n\nOr use the menu 👇",
                         reply_markup=main_keyboard())

# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def on_startup(dp):
    init_db()
    # Регистрируем команды — появится кнопка /start внизу слева
    await bot.set_my_commands([
        types.BotCommand("start", "🏠 Main menu"),
        types.BotCommand("setbalance", "💎 Set balance"),
    ])
    logger.info("Warming up TON price...")
    price = await get_ton_price()
    if price > 0:
        logger.info(f"TON price ready: ${price:.4f}")
    else:
        logger.error("TON price warmup failed — will retry on first request")

if __name__ == "__main__":
    logger.info("OCTOtrade started.")
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
