import logging
import asyncio
import aiohttp
import sqlite3
import os
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
STARTING_BALANCE = 10000.0
TON_CA_PATTERN = re.compile(r'\b(EQ|UQ)[A-Za-z0-9_\-]{46}\b')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MEMECOINS = {
    "NOT": "notcoin",
    "DOGS": "dogs-2",
    "HMSTR": "hamster-kombat",
    "CATI": "catizen",
    "PEPE": "pepe",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
    "FLOKI": "floki",
    "SHIB": "shiba-inu",
}

# ─── БД ───────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT,
        balance REAL DEFAULT 10000.0, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        symbol TEXT, amount REAL, avg_buy_price REAL, ca TEXT DEFAULT '',
        UNIQUE(user_id, symbol))""")
    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        symbol TEXT, action TEXT, amount REAL, price REAL,
        price_buy REAL DEFAULT 0, total REAL, pnl_pct REAL DEFAULT 0, timestamp TEXT)""")
    for col in [
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
    c.execute("SELECT symbol,amount,avg_buy_price,ca FROM portfolio WHERE user_id=? AND amount>0.000001", (user_id,))
    rows = c.fetchall(); conn.close(); return rows

def get_trades(user_id, limit=10):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("SELECT symbol,action,amount,price,price_buy,total,pnl_pct,timestamp FROM trades WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = c.fetchall(); conn.close(); return rows

def update_balance(user_id, delta):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, user_id))
    conn.commit(); conn.close()

def set_balance(user_id, amount):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user_id))
    conn.commit(); conn.close()

def update_portfolio(user_id, symbol, amount_delta, price, ca=""):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("SELECT amount,avg_buy_price FROM portfolio WHERE user_id=? AND symbol=?", (user_id, symbol))
    row = c.fetchone()
    if row:
        old_amount, old_avg = row
        new_amount = old_amount + amount_delta
        new_avg = (old_amount*old_avg + amount_delta*price)/new_amount if amount_delta > 0 and new_amount > 0 else old_avg
        if new_amount <= 0.000001:
            c.execute("DELETE FROM portfolio WHERE user_id=? AND symbol=?", (user_id, symbol))
        else:
            c.execute("UPDATE portfolio SET amount=?,avg_buy_price=? WHERE user_id=? AND symbol=?", (new_amount, new_avg, user_id, symbol))
    else:
        if amount_delta > 0:
            c.execute("INSERT INTO portfolio (user_id,symbol,amount,avg_buy_price,ca) VALUES (?,?,?,?,?)", (user_id, symbol, amount_delta, price, ca))
    conn.commit(); conn.close()

def get_avg_buy_price(user_id, symbol):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("SELECT avg_buy_price FROM portfolio WHERE user_id=? AND symbol=?", (user_id, symbol))
    row = c.fetchone(); conn.close(); return row[0] if row else 0.0

def save_trade(user_id, symbol, action, amount, price, price_buy, total, pnl_pct):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("INSERT INTO trades (user_id,symbol,action,amount,price,price_buy,total,pnl_pct,timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
              (user_id, symbol, action, amount, price, price_buy, total, pnl_pct, datetime.now().strftime("%d.%m %H:%M")))
    conn.commit(); conn.close()

def reset_user(user_id):
    conn = sqlite3.connect("trading.db"); c = conn.cursor()
    c.execute("UPDATE users SET balance=? WHERE user_id=?", (STARTING_BALANCE, user_id))
    c.execute("DELETE FROM portfolio WHERE user_id=?", (user_id,))
    c.execute("DELETE FROM trades WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

# ─── ЦЕНЫ ─────────────────────────────────────────────────────────────────────

async def get_ton_token_price_by_ca(ca):
    # 1. GeckoTerminal
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}",
                             timeout=aiohttp.ClientTimeout(total=10), headers={"Accept":"application/json"}) as r:
                if r.status == 200:
                    data = await r.json()
                    attrs = data.get("data",{}).get("attributes",{})
                    price = float(attrs.get("price_usd") or 0)
                    symbol = attrs.get("symbol") or ca[:6].upper()
                    if price > 0: return price, symbol
    except Exception as e: logger.warning(f"GeckoTerminal: {e}")

    # 2. STON.fi
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.ston.fi/v1/assets/{ca}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    asset = data.get("asset",{})
                    price = float(asset.get("dex_price_usd") or 0)
                    symbol = asset.get("symbol") or ca[:6].upper()
                    if price > 0: return price, symbol
    except Exception as e: logger.warning(f"STON.fi: {e}")

    # 3. DeDust через GeckoTerminal
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/pools?dex=dedust",
                             timeout=aiohttp.ClientTimeout(total=10), headers={"Accept":"application/json"}) as r:
                if r.status == 200:
                    data = await r.json()
                    pools = data.get("data",[])
                    if pools:
                        attrs = pools[0].get("attributes",{})
                        price = float(attrs.get("base_token_price_usd") or attrs.get("quote_token_price_usd") or 0)
                        if price > 0: return price, ca[:8].upper()
    except Exception as e: logger.warning(f"DeDust: {e}")

    # 4. TonAPI
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://tonapi.io/v2/jettons/{ca}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    symbol = data.get("metadata",{}).get("symbol") or ca[:8].upper()
                    price = float(data.get("dex_usd_price") or 0)
                    if price > 0: return price, symbol
    except Exception as e: logger.warning(f"TonAPI: {e}")

    return 0.0, ca[:8].upper()

async def get_ton_token_history_by_ca(ca, days=7):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/ohlcv/day?limit={days}",
                             timeout=aiohttp.ClientTimeout(total=10), headers={"Accept":"application/json"}) as r:
                if r.status == 200:
                    data = await r.json()
                    ohlcv = data.get("data",{}).get("attributes",{}).get("ohlcv_list",[])
                    return [[item[0]*1000, item[4]] for item in reversed(ohlcv)]
    except Exception as e: logger.warning(f"GeckoTerminal history: {e}")
    return []

async def get_prices(symbols):
    known = [s for s in symbols if s in MEMECOINS]
    if not known: return {}
    ids = [MEMECOINS[s] for s in known]
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(ids)}&vs_currencies=usd",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
        return {sym: data[MEMECOINS[sym]]["usd"] for sym in known if MEMECOINS[sym] in data}
    except Exception as e: logger.error(f"CoinGecko: {e}"); return {}

async def get_price_history(symbol, days=7):
    cg_id = MEMECOINS.get(symbol)
    if not cg_id: return []
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart?vs_currency=usd&days={days}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
        return data.get("prices",[])
    except Exception as e: logger.error(f"CoinGecko history: {e}"); return []

def format_price(p):
    if p == 0: return "$0"
    if p < 0.000001: return f"${p:.10f}"
    if p < 0.001: return f"${p:.8f}"
    if p < 1: return f"${p:.6f}"
    return f"${p:.4f}"

def pnl_bar(pnl_pct):
    filled = min(int(abs(pnl_pct)/5), 10)
    bar = ("🟩" if pnl_pct >= 0 else "🟥") * filled + "⬜" * (10-filled)
    return bar

# ─── FSM ──────────────────────────────────────────────────────────────────────

class TradeState(StatesGroup):
    choosing_coin = State()
    entering_amount = State()
    confirming = State()

class CAState(StatesGroup):
    entering_ca = State()
    entering_ca_amount = State()
    confirming_ca = State()

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
        InlineKeyboardButton("💼 Портфель", callback_data="portfolio"),
        InlineKeyboardButton("📊 Рынок", callback_data="market"),
        InlineKeyboardButton("🟢 Купить", callback_data="buy"),
        InlineKeyboardButton("🔴 Продать", callback_data="sell"),
        InlineKeyboardButton("🔍 Купить по CA", callback_data="buy_ca"),
        InlineKeyboardButton("📈 График", callback_data="chart_menu"),
        InlineKeyboardButton("📜 История", callback_data="history"),
        InlineKeyboardButton("💎 Баланс", callback_data="balance_menu"),
    )
    kb.add(InlineKeyboardButton("🔄 Сбросить аккаунт", callback_data="reset_confirm"))
    return kb

def amount_keyboard(action, symbol):
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(
        InlineKeyboardButton("25%", callback_data=f"quick_{action}_{symbol}_25"),
        InlineKeyboardButton("50%", callback_data=f"quick_{action}_{symbol}_50"),
        InlineKeyboardButton("75%", callback_data=f"quick_{action}_{symbol}_75"),
        InlineKeyboardButton("ALL", callback_data=f"quick_{action}_{symbol}_100"),
    )
    kb.add(InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    return kb

def coins_keyboard(action):
    kb = InlineKeyboardMarkup(row_width=3)
    for sym in MEMECOINS:
        kb.insert(InlineKeyboardButton(sym, callback_data=f"{action}_{sym}"))
    kb.add(InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    return kb

def back_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("◀️ Главное меню", callback_data="back_main"))
    return kb

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    await message.answer(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        f"🎮 <b>MemeTrader</b> — симулятор торговли мемкоинами TON\n\n"
        f"💎 Стартовый баланс: <b>{user[2]:,.2f} vTON</b>\n"
        f"🔍 Кинь CA токена прямо в чат — бот сразу найдёт\n"
        f"📊 Реальные цены + P&L по каждой сделке\n\n"
        f"/setbalance — установить свой баланс\n\n"
        f"Выбери действие:",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(commands=["help"])
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 <b>Помощь</b>\n\n"
        "Кинь CA токена прямо в чат — бот найдёт и предложит купить\n"
        "/setbalance 50000 — поставить баланс\n"
        "/start — главное меню\n\n"
        "P&L — разница между ценой покупки и продажи 🟢🔴",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(commands=["setbalance"])
async def cmd_setbalance(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Пример: <code>/setbalance 50000</code>", parse_mode="HTML"); return
    try:
        amount = float(args[1].replace(",",""))
        if amount < 100 or amount > 10_000_000: raise ValueError
    except:
        await message.answer("❌ Введи число от 100 до 10,000,000"); return
    set_balance(message.from_user.id, amount)
    await message.answer(f"✅ Баланс установлен!\n💰 <b>{amount:,.2f} vTON</b>", reply_markup=main_keyboard(), parse_mode="HTML")

@dp.callback_query_handler(text="back_main", state="*")
async def back_main(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"💼 Главное меню\n💰 Баланс: <b>{user[2]:,.2f} vTON</b>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── АВТОДЕТЕКТ CA ───────────────────────────────────────────────────────────

@dp.message_handler(lambda m: bool(TON_CA_PATTERN.search(m.text or "")), state="*")
async def auto_detect_ca(message: types.Message, state: FSMContext):
    current = await state.get_state()
    if current is not None: return
    ca = TON_CA_PATTERN.search(message.text).group(0)
    await message.answer("⏳ Ищу токен...")
    price, symbol = await get_ton_token_price_by_ca(ca)
    if price == 0:
        await message.answer("❌ Токен не найден. Убедись что он торгуется на DeDust/STON.fi.", reply_markup=back_keyboard()); return
    user = get_user(message.from_user.id)
    balance = user[2]
    await state.update_data(ca=ca, symbol=symbol, price=price)
    await CAState.entering_ca_amount.set()
    await message.answer(
        f"✅ Найден: <b>{symbol}</b>\n\n"
        f"💵 Цена: <b>{format_price(price)}</b>\n"
        f"💰 Баланс: <b>{balance:,.2f} vTON</b>\n"
        f"📦 Макс: <b>{balance/price:,.4f} {symbol}</b>\n\n"
        f"Введи сумму в <b>vTON</b> или нажми кнопку:",
        reply_markup=amount_keyboard("ca", symbol), parse_mode="HTML"
    )

# ─── ПОРТФЕЛЬ ────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="portfolio")
async def show_portfolio(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    balance = user[2]
    holdings = get_portfolio(callback.from_user.id)
    if not holdings:
        await callback.message.edit_text(
            f"💼 <b>Портфель</b>\n\n💰 Свободно: <b>{balance:,.2f} vTON</b>\n\n📭 Пусто!",
            reply_markup=back_keyboard(), parse_mode="HTML"); return
    known_syms = [h[0] for h in holdings if h[0] in MEMECOINS]
    prices = await get_prices(known_syms) if known_syms else {}
    for sym, amount, avg_price, ca in holdings:
        if sym not in MEMECOINS and ca:
            p, _ = await get_ton_token_price_by_ca(ca)
            if p: prices[sym] = p
    total_value = balance
    lines = [f"💼 <b>Портфель</b>\n\n💰 Свободно: <b>{balance:,.2f} vTON</b>\n"]
    for sym, amount, avg_price, ca in holdings:
        cur = prices.get(sym, avg_price)
        value = amount * cur
        pnl = (cur - avg_price) / avg_price * 100 if avg_price > 0 else 0
        pnl_abs = value - amount * avg_price
        total_value += value
        sign = "+" if pnl_abs >= 0 else ""
        lines.append(
            f"{'🟢' if pnl>=0 else '🔴'} <b>{sym}</b>\n"
            f"   📦 {amount:,.4f} шт. | вход: {format_price(avg_price)}\n"
            f"   💵 Сейчас: {format_price(cur)}\n"
            f"   {pnl_bar(pnl)} {pnl:+.1f}% ({sign}{pnl_abs:,.2f} vTON)\n"
            f"   💎 {value:,.2f} vTON"
        )
    pnl_total = (total_value - STARTING_BALANCE) / STARTING_BALANCE * 100
    pnl_abs_total = total_value - STARTING_BALANCE
    sign = "+" if pnl_abs_total >= 0 else ""
    lines.append(f"\n{'─'*20}\n📊 <b>Итого: {total_value:,.2f} vTON</b>\n{'🟢' if pnl_total>=0 else '🔴'} P&L: <b>{pnl_total:+.1f}%</b> ({sign}{pnl_abs_total:,.2f} vTON)\n{pnl_bar(pnl_total)}")
    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── РЫНОК ───────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="market")
async def show_market(callback: types.CallbackQuery):
    await callback.message.edit_text("⏳ Загружаю цены...")
    prices = await get_prices(list(MEMECOINS.keys()))
    if not prices:
        await callback.message.edit_text("❌ Не удалось получить цены.", reply_markup=back_keyboard()); return
    lines = ["📊 <b>Рынок мемкоинов</b>\n"]
    for sym in MEMECOINS:
        if sym in prices: lines.append(f"• <b>{sym}</b>: {format_price(prices[sym])}")
    lines.append("\n💡 Кинь CA любого токена прямо в чат!")
    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── БАЛАНС МЕНЮ ─────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="balance_menu")
async def balance_menu(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➕ Пополнить", callback_data="topup"))
    kb.add(InlineKeyboardButton("✏️ Установить вручную", callback_data="setbalance_menu"))
    kb.add(InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    await callback.message.edit_text(
        f"💎 <b>Управление балансом</b>\n\n💰 Текущий: <b>{user[2]:,.2f} vTON</b>",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="setbalance_menu")
async def setbalance_menu(callback: types.CallbackQuery):
    await SetBalanceState.entering_balance.set()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"✏️ Введи новый баланс (100 — 10,000,000):\nТекущий: <b>{user[2]:,.2f} vTON</b>",
        reply_markup=back_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(state=SetBalanceState.entering_balance)
async def setbalance_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",",""))
        if amount < 100 or amount > 10_000_000: raise ValueError
    except:
        await message.answer("❌ Введи число от 100 до 10,000,000"); return
    set_balance(message.from_user.id, amount)
    await state.finish()
    await message.answer(f"✅ Баланс установлен!\n💰 <b>{amount:,.2f} vTON</b>", reply_markup=main_keyboard(), parse_mode="HTML")

@dp.callback_query_handler(text="topup")
async def topup_start(callback: types.CallbackQuery):
    await TopUpState.entering_topup.set()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"💎 <b>Пополнить баланс</b>\n\nТекущий: <b>{user[2]:,.2f} vTON</b>\n\nВведи сколько добавить:",
        reply_markup=back_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(state=TopUpState.entering_topup)
async def topup_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0 or amount > 1_000_000: raise ValueError
    except:
        await message.answer("❌ Введи число от 1 до 1,000,000"); return
    update_balance(message.from_user.id, amount)
    await state.finish()
    user = get_user(message.from_user.id)
    await message.answer(f"✅ Добавлено <b>{amount:,.2f} vTON</b>\n💰 Баланс: <b>{user[2]:,.2f} vTON</b>", reply_markup=main_keyboard(), parse_mode="HTML")

# ─── ПОКУПКА / ПРОДАЖА ───────────────────────────────────────────────────────

@dp.callback_query_handler(text="buy")
async def buy_menu(callback: types.CallbackQuery):
    await TradeState.choosing_coin.set()
    await callback.message.edit_text("🟢 <b>Купить монету</b>\n\nВыбери:", reply_markup=coins_keyboard("buy"), parse_mode="HTML")

@dp.callback_query_handler(text="sell")
async def sell_menu(callback: types.CallbackQuery):
    await TradeState.choosing_coin.set()
    holdings = get_portfolio(callback.from_user.id)
    if not holdings:
        await callback.message.edit_text("📭 Нет монет для продажи!", reply_markup=back_keyboard()); return
    kb = InlineKeyboardMarkup(row_width=2)
    for sym, amount, avg_price, ca in holdings:
        kb.insert(InlineKeyboardButton(f"{sym} ({amount:,.2f})", callback_data=f"sell_{sym}"))
    kb.add(InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    await callback.message.edit_text("🔴 <b>Продать монету</b>\n\nВыбери:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query_handler(state=TradeState.choosing_coin)
async def coin_chosen(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 1)
    if len(parts) != 2: return
    action, symbol = parts
    holdings_map = {h[0]: (h[1], h[2], h[3]) for h in get_portfolio(callback.from_user.id)}
    if symbol in MEMECOINS:
        prices = await get_prices([symbol]); price = prices.get(symbol, 0)
    else:
        ca = holdings_map.get(symbol, ("","",""))[2]
        price, _ = await get_ton_token_price_by_ca(ca) if ca else (0, symbol)
    if not price:
        await callback.message.edit_text("❌ Не удалось получить цену.", reply_markup=back_keyboard())
        await state.finish(); return
    await state.update_data(symbol=symbol, action=action, price=price)
    await TradeState.entering_amount.set()
    user = get_user(callback.from_user.id); balance = user[2]
    if action == "buy":
        text = (f"🟢 <b>Купить {symbol}</b>\n\n💵 Цена: {format_price(price)}\n"
                f"💰 Баланс: {balance:,.2f} vTON\n📦 Макс: {balance/price:,.4f} {symbol}\n\nВведи сумму в vTON или нажми кнопку:")
    else:
        held, avg, _ = holdings_map.get(symbol, (0, 0, ""))
        pnl = (price-avg)/avg*100 if avg > 0 else 0
        text = (f"🔴 <b>Продать {symbol}</b>\n\n💵 Цена: {format_price(price)}\n"
                f"📦 У тебя: {held:,.4f} {symbol}\n{'🟢' if pnl>=0 else '🔴'} P&L: <b>{pnl:+.1f}%</b>\n{pnl_bar(pnl)}\n\nВведи кол-во или нажми кнопку:")
    await callback.message.edit_text(text, reply_markup=amount_keyboard(action, symbol), parse_mode="HTML")

@dp.callback_query_handler(lambda c: c.data.startswith("quick_"), state="*")
async def quick_amount(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 4: return
    action = parts[1]; pct = int(parts[-1]); symbol = "_".join(parts[2:-1])
    data = await state.get_data(); price = data.get("price", 0)
    if not price: await callback.answer("❌ Цена недоступна", show_alert=True); return
    user = get_user(callback.from_user.id); balance = user[2]
    if action in ("buy", "ca"):
        ton_amount = balance * pct / 100
        coin_amount = ton_amount / price
        total_cost = ton_amount; avg_buy = price; pnl_pct = 0.0
    else:
        holdings_map = {h[0]: h[1] for h in get_portfolio(callback.from_user.id)}
        held = holdings_map.get(symbol, 0)
        coin_amount = held * pct / 100
        total_cost = coin_amount * price
        avg_buy = get_avg_buy_price(callback.from_user.id, symbol)
        pnl_pct = (price-avg_buy)/avg_buy*100 if avg_buy > 0 else 0.0
        ton_amount = total_cost
    if ton_amount <= 0 or coin_amount <= 0:
        await callback.answer("❌ Недостаточно средств", show_alert=True); return
    await state.update_data(coin_amount=coin_amount, total_cost=total_cost, pnl_pct=pnl_pct, avg_buy=avg_buy, ton_amount=ton_amount)
    confirm_cb = "confirm_ca_trade" if action == "ca" else "confirm_trade"
    if action in ("buy", "ca"):
        await CAState.confirming_ca.set() if action == "ca" else await TradeState.confirming.set()
        text = (f"✅ <b>Подтверди покупку ({pct}%)</b>\n\n🪙 {symbol}: {coin_amount:,.4f} шт.\n"
                f"💵 Цена: {format_price(price)}\n💸 Спишется: {ton_amount:,.2f} vTON")
    else:
        await TradeState.confirming.set()
        text = (f"✅ <b>Подтверди продажу ({pct}%)</b>\n\n🪙 {symbol}: {coin_amount:,.4f} шт.\n"
                f"💵 Цена: {format_price(price)}\n💰 Получишь: {total_cost:,.2f} vTON\n"
                f"{'🟢' if pnl_pct>=0 else '🔴'} P&L: <b>{pnl_pct:+.1f}%</b>\n{pnl_bar(pnl_pct)}")
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Подтвердить", callback_data=confirm_cb),
           InlineKeyboardButton("❌ Отмена", callback_data="back_main"))
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.message_handler(state=TradeState.entering_amount)
async def amount_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    action = data["action"]; symbol = data["symbol"]; price = data["price"]
    user = get_user(message.from_user.id); balance = user[2]
    text_input = message.text.strip().lower()
    try:
        if action == "buy":
            ton_amount = balance if text_input == "all" else float(text_input)
            if ton_amount <= 0: raise ValueError
            if ton_amount > balance: await message.answer(f"❌ Недостаточно. У тебя {balance:,.2f}"); return
            coin_amount = ton_amount / price; total_cost = ton_amount; pnl_pct = 0.0; avg_buy = price
        else:
            holdings_map = {h[0]: h[1] for h in get_portfolio(message.from_user.id)}
            held = holdings_map.get(symbol, 0)
            coin_amount = held if text_input == "all" else float(text_input)
            if coin_amount <= 0: raise ValueError
            if coin_amount > held + 0.000001: await message.answer(f"❌ У тебя только {held:,.4f} {symbol}"); return
            total_cost = coin_amount * price
            avg_buy = get_avg_buy_price(message.from_user.id, symbol)
            pnl_pct = (price-avg_buy)/avg_buy*100 if avg_buy > 0 else 0.0
            ton_amount = total_cost
    except: await message.answer("❌ Введи число, например: <code>100</code>", parse_mode="HTML"); return
    await state.update_data(coin_amount=coin_amount, total_cost=total_cost, pnl_pct=pnl_pct, avg_buy=avg_buy, ton_amount=ton_amount)
    await TradeState.confirming.set()
    if action == "buy":
        text = f"✅ <b>Подтверди покупку</b>\n\n🪙 {symbol}: {coin_amount:,.4f} шт.\n💵 {format_price(price)}\n💸 {total_cost:,.2f} vTON"
    else:
        text = (f"✅ <b>Подтверди продажу</b>\n\n🪙 {symbol}: {coin_amount:,.4f} шт.\n💵 {format_price(price)}\n"
                f"💰 {total_cost:,.2f} vTON\n{'🟢' if pnl_pct>=0 else '🔴'} P&L: <b>{pnl_pct:+.1f}%</b>\n{pnl_bar(pnl_pct)}")
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_trade"),
           InlineKeyboardButton("❌ Отмена", callback_data="back_main"))
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query_handler(text="confirm_trade", state=TradeState.confirming)
async def confirm_trade(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    action=data["action"]; symbol=data["symbol"]; coin_amount=data["coin_amount"]
    price=data["price"]; total_cost=data["total_cost"]
    pnl_pct=data.get("pnl_pct",0.0); avg_buy=data.get("avg_buy",price)
    user_id = callback.from_user.id
    if action == "buy":
        update_balance(user_id, -total_cost)
        update_portfolio(user_id, symbol, coin_amount, price)
        save_trade(user_id, symbol, "BUY", coin_amount, price, price, total_cost, 0.0)
        text = f"🟢 Куплено <b>{coin_amount:,.4f} {symbol}</b>\n💸 {total_cost:,.2f} vTON\n💵 Вход: {format_price(price)}"
    else:
        update_balance(user_id, total_cost)
        update_portfolio(user_id, symbol, -coin_amount, price)
        save_trade(user_id, symbol, "SELL", coin_amount, price, avg_buy, total_cost, pnl_pct)
        pnl_abs = coin_amount*(price-avg_buy)
        text = (f"🔴 Продано <b>{coin_amount:,.4f} {symbol}</b>\n💰 {total_cost:,.2f} vTON\n"
                f"{'🟢' if pnl_pct>=0 else '🔴'} P&L: <b>{pnl_pct:+.1f}%</b> ({'+' if pnl_abs>=0 else ''}{pnl_abs:,.2f} vTON)\n{pnl_bar(pnl_pct)}")
    await state.finish()
    user = get_user(user_id)
    await callback.message.edit_text(text + f"\n\n💰 Баланс: <b>{user[2]:,.2f} vTON</b>", reply_markup=main_keyboard(), parse_mode="HTML")

# ─── ПОКУПКА ПО CA ───────────────────────────────────────────────────────────

@dp.callback_query_handler(text="buy_ca")
async def buy_ca_start(callback: types.CallbackQuery):
    await CAState.entering_ca.set()
    await callback.message.edit_text(
        "🔍 <b>Купить токен по CA</b>\n\nОтправь контрактный адрес токена TON.\n\n"
        "💡 Или просто кинь CA прямо в чат!",
        reply_markup=back_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(state=CAState.entering_ca)
async def ca_received(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    if len(ca) < 10: await message.answer("❌ Слишком короткий адрес."); return
    await message.answer("⏳ Ищу токен...")
    price, symbol = await get_ton_token_price_by_ca(ca)
    if price == 0:
        await message.answer("❌ Токен не найден.", reply_markup=back_keyboard()); return
    await state.update_data(ca=ca, symbol=symbol, price=price)
    await CAState.entering_ca_amount.set()
    user = get_user(message.from_user.id); balance = user[2]
    await message.answer(
        f"✅ Найден: <b>{symbol}</b>\n\n💵 {format_price(price)}\n💰 {balance:,.2f} vTON\n📦 Макс: {balance/price:,.4f} {symbol}\n\nВведи сумму или нажми кнопку:",
        reply_markup=amount_keyboard("ca", symbol), parse_mode="HTML"
    )

@dp.message_handler(state=CAState.entering_ca_amount)
async def ca_amount_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    symbol=data["symbol"]; price=data["price"]
    user = get_user(message.from_user.id); balance = user[2]
    text_input = message.text.strip().lower()
    try:
        ton_amount = balance if text_input == "all" else float(text_input)
        if ton_amount <= 0: raise ValueError
        if ton_amount > balance: await message.answer(f"❌ Недостаточно. У тебя {balance:,.2f}"); return
        coin_amount = ton_amount / price
    except: await message.answer("❌ Введи число, например: <code>500</code>", parse_mode="HTML"); return
    await state.update_data(ton_amount=ton_amount, coin_amount=coin_amount)
    await CAState.confirming_ca.set()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Купить", callback_data="confirm_ca_trade"),
           InlineKeyboardButton("❌ Отмена", callback_data="back_main"))
    await message.answer(
        f"✅ <b>Подтверди покупку</b>\n\n🪙 {symbol}: {coin_amount:,.4f} шт.\n💵 {format_price(price)}\n💸 {ton_amount:,.2f} vTON",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="confirm_ca_trade", state=CAState.confirming_ca)
async def confirm_ca_trade(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ca=data["ca"]; symbol=data["symbol"]; price=data["price"]
    ton_amount=data.get("ton_amount") or data.get("total_cost",0)
    coin_amount=data.get("coin_amount",0)
    user_id = callback.from_user.id
    update_balance(user_id, -ton_amount)
    update_portfolio(user_id, symbol, coin_amount, price, ca)
    save_trade(user_id, symbol, "BUY", coin_amount, price, price, ton_amount, 0.0)
    await state.finish()
    user = get_user(user_id)
    await callback.message.edit_text(
        f"🟢 Куплено <b>{coin_amount:,.4f} {symbol}</b>\n💸 {ton_amount:,.2f} vTON\n💵 Вход: {format_price(price)}\n\n💰 Баланс: <b>{user[2]:,.2f} vTON</b>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── ИСТОРИЯ ─────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="history")
async def show_history(callback: types.CallbackQuery):
    trades = get_trades(callback.from_user.id, 10)
    if not trades: await callback.message.edit_text("📜 История пуста!", reply_markup=back_keyboard()); return
    lines = ["📜 <b>Последние сделки</b>\n"]
    for sym, action, amount, price, price_buy, total, pnl_pct, ts in trades:
        if action == "BUY":
            lines.append(f"🟢 {ts} | <b>{sym}</b> BUY\n   {amount:,.4f} шт. @ {format_price(price)} = {total:,.2f} vTON")
        else:
            lines.append(f"🔴 {ts} | <b>{sym}</b> SELL\n   {amount:,.4f} @ {format_price(price)} = {total:,.2f} vTON\n   {'🟢' if pnl_pct>=0 else '🔴'} P&L: <b>{pnl_pct:+.1f}%</b>\n   {pnl_bar(pnl_pct)}")
    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── ГРАФИК ──────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="chart_menu")
async def chart_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("📈 <b>График цены</b>\n\nВыбери монету:", reply_markup=coins_keyboard("chart"), parse_mode="HTML")

@dp.callback_query_handler(lambda c: c.data.startswith("chart_"))
async def show_chart(callback: types.CallbackQuery):
    symbol = callback.data[6:]
    await callback.message.edit_text(f"⏳ Загружаю график {symbol}...")
    holdings_map = {h[0]: h[3] for h in get_portfolio(callback.from_user.id)}
    ca = holdings_map.get(symbol, "")
    history = await get_price_history(symbol, days=7) if symbol in MEMECOINS else (await get_ton_token_history_by_ca(ca, days=7) if ca else [])
    if not history: await callback.message.edit_text("❌ Нет данных.", reply_markup=back_keyboard()); return
    timestamps = [datetime.fromtimestamp(p[0]/1000) for p in history]
    prices_hist = [p[1] for p in history]
    fig, ax = plt.subplots(figsize=(10,5))
    fig.patch.set_facecolor('#0d1117'); ax.set_facecolor('#0d1117')
    color = '#00ff88' if prices_hist[-1] >= prices_hist[0] else '#ff4444'
    ax.plot(timestamps, prices_hist, color=color, linewidth=2)
    ax.fill_between(timestamps, prices_hist, alpha=0.15, color=color)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    plt.xticks(rotation=45, color='#888888'); plt.yticks(color='#888888')
    for spine in ['bottom','left']: ax.spines[spine].set_color('#333333')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    change = (prices_hist[-1]-prices_hist[0])/prices_hist[0]*100
    ax.set_title(f'{symbol} — 7 дней ({change:+.1f}%)', color='white', fontsize=14, pad=15)
    ax.set_ylabel('USD', color='#888888'); plt.tight_layout()
    buf = io.BytesIO(); plt.savefig(buf, format='png', dpi=130, bbox_inches='tight'); buf.seek(0); plt.close()
    await callback.message.delete()
    await bot.send_photo(callback.from_user.id, InputFile(buf, filename="chart.png"),
                         caption=f"📈 <b>{symbol}</b> | {format_price(prices_hist[-1])} | 7д: {change:+.1f}%",
                         reply_markup=back_keyboard(), parse_mode="HTML")

# ─── СБРОС ───────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="reset_confirm")
async def reset_confirm(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Сбросить", callback_data="reset_do"),
           InlineKeyboardButton("❌ Отмена", callback_data="back_main"))
    await callback.message.edit_text("⚠️ Сбросить всё и начать заново?\nБаланс вернётся к 10,000 vTON.", reply_markup=kb)

@dp.callback_query_handler(text="reset_do")
async def reset_do(callback: types.CallbackQuery):
    reset_user(callback.from_user.id)
    await callback.message.edit_text("🔄 Аккаунт сброшен!\n💰 Баланс: 10,000 vTON", reply_markup=main_keyboard())

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    logger.info("MemeTrader bot started!")
    executor.start_polling(dp, skip_updates=True)
