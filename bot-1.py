import logging
import asyncio
import aiohttp
import sqlite3
import os
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
STARTING_BALANCE = 10000.0

# TON CA pattern (EQ... или UQ... адреса)
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 10000.0,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            amount REAL,
            avg_buy_price REAL,
            ca TEXT DEFAULT '',
            UNIQUE(user_id, symbol)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            action TEXT,
            amount REAL,
            price REAL,
            price_buy REAL DEFAULT 0,
            total REAL,
            pnl_pct REAL DEFAULT 0,
            timestamp TEXT
        )
    """)
    for col in [
        "ALTER TABLE trades ADD COLUMN price_buy REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN pnl_pct REAL DEFAULT 0",
        "ALTER TABLE portfolio ADD COLUMN ca TEXT DEFAULT ''",
    ]:
        try:
            c.execute(col)
        except Exception:
            pass
    conn.commit()
    conn.close()

def get_user(user_id: int, username: str = ""):
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute(
            "INSERT INTO users (user_id, username, balance, created_at) VALUES (?, ?, ?, ?)",
            (user_id, username, STARTING_BALANCE, datetime.now().isoformat())
        )
        conn.commit()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
    conn.close()
    return user

def get_portfolio(user_id: int):
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("SELECT symbol, amount, avg_buy_price, ca FROM portfolio WHERE user_id = ? AND amount > 0.000001", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_trades(user_id: int, limit=10):
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute(
        "SELECT symbol, action, amount, price, price_buy, total, pnl_pct, timestamp FROM trades WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def update_balance(user_id: int, delta: float):
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

def set_balance(user_id: int, amount: float):
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def update_portfolio(user_id: int, symbol: str, amount_delta: float, price: float, ca: str = ""):
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("SELECT amount, avg_buy_price FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    row = c.fetchone()
    if row:
        old_amount, old_avg = row
        new_amount = old_amount + amount_delta
        if amount_delta > 0:
            new_avg = (old_amount * old_avg + amount_delta * price) / new_amount if new_amount > 0 else price
        else:
            new_avg = old_avg
        if new_amount <= 0.000001:
            c.execute("DELETE FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol))
        else:
            c.execute(
                "UPDATE portfolio SET amount = ?, avg_buy_price = ? WHERE user_id = ? AND symbol = ?",
                (new_amount, new_avg, user_id, symbol)
            )
    else:
        if amount_delta > 0:
            c.execute(
                "INSERT INTO portfolio (user_id, symbol, amount, avg_buy_price, ca) VALUES (?, ?, ?, ?, ?)",
                (user_id, symbol, amount_delta, price, ca)
            )
    conn.commit()
    conn.close()

def get_avg_buy_price(user_id: int, symbol: str) -> float:
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("SELECT avg_buy_price FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0.0

def save_trade(user_id: int, symbol: str, action: str, amount: float, price: float,
               price_buy: float, total: float, pnl_pct: float):
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO trades (user_id, symbol, action, amount, price, price_buy, total, pnl_pct, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, symbol, action, amount, price, price_buy, total, pnl_pct,
         datetime.now().strftime("%d.%m %H:%M"))
    )
    conn.commit()
    conn.close()

def reset_user(user_id: int):
    conn = sqlite3.connect("trading.db")
    c = conn.cursor()
    c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (STARTING_BALANCE, user_id))
    c.execute("DELETE FROM portfolio WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM trades WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ─── ЦЕНА ТОКЕНА ПО CA ────────────────────────────────────────────────────────

async def get_ton_token_price_by_ca(ca: str) -> tuple[float, str]:
    """GeckoTerminal (все DEX) → STON.fi → DeDust"""
    # 1. GeckoTerminal — агрегирует все DEX включая DeDust и STON.fi
    url = f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                   headers={"Accept": "application/json"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    attrs = data.get("data", {}).get("attributes", {})
                    price = float(attrs.get("price_usd") or 0)
                    symbol = attrs.get("symbol") or ca[:6].upper()
                    if price > 0:
                        return price, symbol
    except Exception as e:
        logger.warning(f"GeckoTerminal: {e}")

    # 2. STON.fi REST API
    url2 = f"https://api.ston.fi/v1/assets/{ca}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url2, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    asset = data.get("asset", {})
                    price = float(asset.get("dex_price_usd") or 0)
                    symbol = asset.get("symbol") or ca[:6].upper()
                    if price > 0:
                        return price, symbol
    except Exception as e:
        logger.warning(f"STON.fi: {e}")

    # 3. DeDust через GeckoTerminal (пулы конкретно с DeDust)
    url3 = f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/pools?dex=dedust"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url3, timeout=aiohttp.ClientTimeout(total=10),
                                   headers={"Accept": "application/json"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pools = data.get("data", [])
                    if pools:
                        attrs = pools[0].get("attributes", {})
                        # Берём цену base токена из пула
                        price_str = attrs.get("base_token_price_usd") or attrs.get("quote_token_price_usd")
                        price = float(price_str or 0)
                        # Определяем символ
                        rels = pools[0].get("relationships", {})
                        symbol = ca[:8].upper()
                        if price > 0:
                            return price, symbol
    except Exception as e:
        logger.warning(f"DeDust: {e}")

    # 4. DeDust напрямую через tonapi.io
    url4 = f"https://tonapi.io/v2/jettons/{ca}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url4, timeout=aiohttp.ClientTimeout(total=10),
                                   headers={"Accept": "application/json"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    symbol = data.get("metadata", {}).get("symbol") or ca[:8].upper()
                    # tonapi даёт цену в USD если есть
                    price = float(data.get("dex_usd_price") or 0)
                    if price > 0:
                        return price, symbol
    except Exception as e:
        logger.warning(f"TonAPI: {e}")

    return 0.0, ca[:8].upper()


async def get_ton_token_history_by_ca(ca: str, days: int = 7) -> list:
    url = f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/ohlcv/day?limit={days}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                   headers={"Accept": "application/json"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ohlcv = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                    return [[item[0] * 1000, item[4]] for item in reversed(ohlcv)]
    except Exception as e:
        logger.warning(f"GeckoTerminal history: {e}")
    return []

# ─── API CoinGecko ─────────────────────────────────────────────────────────────

async def get_prices(symbols: list[str]) -> dict:
    known = [s for s in symbols if s in MEMECOINS]
    if not known:
        return {}
    ids = [MEMECOINS[s] for s in known]
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(ids)}&vs_currencies=usd"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        return {sym: data[MEMECOINS[sym]]["usd"] for sym in known if MEMECOINS[sym] in data}
    except Exception as e:
        logger.error(f"CoinGecko: {e}")
        return {}

async def get_price_history(symbol: str, days: int = 7) -> list:
    cg_id = MEMECOINS.get(symbol)
    if not cg_id:
        return []
    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart?vs_currency=usd&days={days}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        return data.get("prices", [])
    except Exception as e:
        logger.error(f"CoinGecko history: {e}")
        return []

def format_price(p: float) -> str:
    if p == 0:
        return "$0"
    if p < 0.000001:
        return f"${p:.10f}"
    if p < 0.001:
        return f"${p:.8f}"
    if p < 1:
        return f"${p:.6f}"
    return f"${p:.4f}"

def pnl_bar(pnl_pct: float) -> str:
    """Визуальный бар PnL"""
    filled = min(int(abs(pnl_pct) / 5), 10)
    empty = 10 - filled
    bar = ("🟩" if pnl_pct >= 0 else "🟥") * filled + "⬜" * empty
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
dp = Dispatcher(storage=MemoryStorage())

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💼 Портфель", callback_data="portfolio"),
            InlineKeyboardButton(text="📊 Рынок", callback_data="market"),
        ],
        [
            InlineKeyboardButton(text="🟢 Купить", callback_data="buy"),
            InlineKeyboardButton(text="🔴 Продать", callback_data="sell"),
        ],
        [
            InlineKeyboardButton(text="🔍 Купить по CA", callback_data="buy_ca"),
            InlineKeyboardButton(text="📈 График", callback_data="chart_menu"),
        ],
        [
            InlineKeyboardButton(text="📜 История", callback_data="history"),
            InlineKeyboardButton(text="💎 Баланс", callback_data="balance_menu"),
        ],
        [
            InlineKeyboardButton(text="🔄 Сбросить аккаунт", callback_data="reset_confirm"),
        ],
    ])

def amount_keyboard(action: str, symbol: str):
    """Кнопки 25/50/75/ALL для быстрого ввода суммы"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="25%", callback_data=f"quick_{action}_{symbol}_25"),
            InlineKeyboardButton(text="50%", callback_data=f"quick_{action}_{symbol}_50"),
            InlineKeyboardButton(text="75%", callback_data=f"quick_{action}_{symbol}_75"),
            InlineKeyboardButton(text="ALL", callback_data=f"quick_{action}_{symbol}_100"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])

def coins_keyboard(action: str):
    buttons = []
    row = []
    for sym in MEMECOINS:
        row.append(InlineKeyboardButton(text=sym, callback_data=f"{action}_{sym}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main")]
    ])

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    text = (
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        f"🎮 <b>MemeTrader</b> — симулятор торговли мемкоинами TON\n\n"
        f"💎 Стартовый баланс: <b>{user[2]:,.2f} vTON</b>\n"
        f"🔍 Просто кинь CA токена — бот сразу его найдёт\n"
        f"📊 Реальные цены + P&L по каждой сделке\n\n"
        f"<b>Команды:</b>\n"
        f"/setbalance — установить свой баланс\n"
        f"/help — помощь\n\n"
        f"Выбери действие:"
    )
    await message.answer(text, reply_markup=main_keyboard(), parse_mode="HTML")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "📖 <b>Помощь</b>\n\n"
        "<b>Как торговать:</b>\n"
        "1. Кинь CA токена прямо в чат — бот покажет цену\n"
        "2. Или нажми «Купить по CA» в меню\n"
        "3. Для стандартных монет — кнопка «Купить»\n\n"
        "<b>Команды:</b>\n"
        "/setbalance &lt;сумма&gt; — поставить баланс (пример: /setbalance 50000)\n"
        "/start — главное меню\n\n"
        "<b>P&L</b> — разница между ценой покупки и продажи в процентах\n"
        "Зелёный = плюс, Красный = минус 🟢🔴"
    )
    await message.answer(text, reply_markup=main_keyboard(), parse_mode="HTML")

@dp.message(Command("setbalance"))
async def cmd_setbalance(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "💎 <b>Установить баланс</b>\n\nПример: <code>/setbalance 50000</code>\n\nОт 100 до 10,000,000 vTON",
            parse_mode="HTML"
        )
        return
    try:
        amount = float(args[1].replace(",", ""))
        if amount < 100 or amount > 10_000_000:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи число от 100 до 10,000,000\nПример: <code>/setbalance 50000</code>", parse_mode="HTML")
        return

    set_balance(message.from_user.id, amount)
    await message.answer(
        f"✅ Баланс установлен!\n💰 <b>{amount:,.2f} vTON</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_main")
async def back_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"💼 Главное меню\n💰 Баланс: <b>{user[2]:,.2f} vTON</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

# ─── АВТООПРЕДЕЛЕНИЕ CA ──────────────────────────────────────────────────────

@dp.message(F.text)
async def auto_detect_ca(message: types.Message, state: FSMContext):
    """Если пользователь кидает CA прямо в чат — автоматически ищем токен"""
    current_state = await state.get_state()

    text = message.text.strip()
    match = TON_CA_PATTERN.search(text)

    # Если нет CA в тексте и нет активного состояния — игнорируем
    if not match:
        if current_state is None:
            await message.answer(
                "❓ Не понял команду.\n\nКинь CA токена или используй меню /start",
                reply_markup=main_keyboard()
            )
        return

    # Если уже в каком-то состоянии FSM — не перехватываем
    if current_state is not None:
        return

    ca = match.group(0)
    await message.answer("⏳ Ищу токен по CA...")
    price, symbol = await get_ton_token_price_by_ca(ca)

    if price == 0:
        await message.answer(
            f"❌ Токен не найден.\n\n"
            f"CA: <code>{ca[:24]}...</code>\n\n"
            f"Убедись что токен торгуется на DeDust/STON.fi",
            reply_markup=back_keyboard(),
            parse_mode="HTML"
        )
        return

    user = get_user(message.from_user.id)
    balance = user[2]
    max_coins = balance / price

    # Сохраняем в state и предлагаем купить
    await state.update_data(ca=ca, symbol=symbol, price=price)
    await state.set_state(CAState.entering_ca_amount)

    text_out = (
        f"✅ Найден: <b>{symbol}</b>\n\n"
        f"💵 Цена: <b>{format_price(price)}</b>\n"
        f"💰 Твой баланс: <b>{balance:,.2f} vTON</b>\n"
        f"📦 Максимум: <b>{max_coins:,.4f} {symbol}</b>\n\n"
        f"Введи сумму в <b>vTON</b> или нажми кнопку:"
    )
    await message.answer(text_out, reply_markup=amount_keyboard("ca", symbol), parse_mode="HTML")

# ─── ПОРТФЕЛЬ ─────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "portfolio")
async def show_portfolio(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    balance = user[2]
    holdings = get_portfolio(callback.from_user.id)

    if not holdings:
        await callback.message.edit_text(
            f"💼 <b>Портфель</b>\n\n💰 Свободный баланс: <b>{balance:,.2f} vTON</b>\n\n📭 Портфель пуст!",
            reply_markup=back_keyboard(), parse_mode="HTML"
        )
        return

    known_syms = [h[0] for h in holdings if h[0] in MEMECOINS]
    prices = await get_prices(known_syms) if known_syms else {}

    for sym, amount, avg_price, ca in holdings:
        if sym not in MEMECOINS and ca:
            price, _ = await get_ton_token_price_by_ca(ca)
            if price:
                prices[sym] = price

    total_invested = 0.0
    total_value = balance
    lines = [f"💼 <b>Портфель</b>\n\n💰 Свободно: <b>{balance:,.2f} vTON</b>\n"]

    for sym, amount, avg_price, ca in holdings:
        cur_price = prices.get(sym, avg_price)
        value = amount * cur_price
        invested = amount * avg_price
        pnl = (cur_price - avg_price) / avg_price * 100 if avg_price > 0 else 0
        pnl_abs = value - invested
        total_value += value
        total_invested += invested
        emoji = "🟢" if pnl >= 0 else "🔴"
        sign = "+" if pnl_abs >= 0 else ""
        lines.append(
            f"{emoji} <b>{sym}</b>\n"
            f"   📦 {amount:,.4f} шт. | вход: {format_price(avg_price)}\n"
            f"   💵 Сейчас: {format_price(cur_price)}\n"
            f"   {pnl_bar(pnl)} {pnl:+.1f}% ({sign}{pnl_abs:,.2f} vTON)\n"
            f"   💎 Стоимость: {value:,.2f} vTON"
        )

    pnl_total = (total_value - STARTING_BALANCE) / STARTING_BALANCE * 100
    pnl_abs_total = total_value - STARTING_BALANCE
    sign_total = "+" if pnl_abs_total >= 0 else ""
    lines.append(
        f"\n{'─' * 20}\n"
        f"📊 <b>Итого: {total_value:,.2f} vTON</b>\n"
        f"{'🟢' if pnl_total >= 0 else '🔴'} Общий P&L: <b>{pnl_total:+.1f}%</b> "
        f"({sign_total}{pnl_abs_total:,.2f} vTON)\n"
        f"{pnl_bar(pnl_total)}"
    )

    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── РЫНОК ────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "market")
async def show_market(callback: types.CallbackQuery):
    await callback.message.edit_text("⏳ Загружаю цены...")
    prices = await get_prices(list(MEMECOINS.keys()))

    if not prices:
        await callback.message.edit_text("❌ Не удалось получить цены.", reply_markup=back_keyboard())
        return

    lines = ["📊 <b>Рынок мемкоинов</b>\n"]
    for sym in MEMECOINS:
        if sym in prices:
            lines.append(f"• <b>{sym}</b>: {format_price(prices[sym])}")
    lines.append("\n💡 Кинь CA любого токена прямо в чат!")
    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── БАЛАНС МЕНЮ ─────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "balance_menu")
async def balance_menu(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Пополнить", callback_data="topup")],
        [InlineKeyboardButton(text="✏️ Установить вручную", callback_data="setbalance_menu")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])
    await callback.message.edit_text(
        f"💎 <b>Управление балансом</b>\n\n"
        f"💰 Текущий баланс: <b>{user[2]:,.2f} vTON</b>\n\n"
        f"Пополнить — добавить к текущему\n"
        f"Установить вручную — поставить любую сумму",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "setbalance_menu")
async def setbalance_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SetBalanceState.entering_balance)
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"✏️ <b>Установить баланс</b>\n\n"
        f"Текущий: <b>{user[2]:,.2f} vTON</b>\n\n"
        f"Введи новый баланс (от 100 до 10,000,000):\n"
        f"Например: <code>50000</code>",
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )

@dp.message(SetBalanceState.entering_balance)
async def setbalance_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", ""))
        if amount < 100 or amount > 10_000_000:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи число от 100 до 10,000,000")
        return

    set_balance(message.from_user.id, amount)
    await state.clear()
    await message.answer(
        f"✅ Баланс установлен!\n💰 <b>{amount:,.2f} vTON</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

# ─── ПОПОЛНЕНИЕ ──────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "topup")
async def topup_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TopUpState.entering_topup)
    user = get_user(callback.from_user.id)
    text = (
        f"💎 <b>Пополнить баланс</b>\n\n"
        f"Текущий баланс: <b>{user[2]:,.2f} vTON</b>\n\n"
        f"Введи сколько vTON добавить:\n"
        f"Например: <code>5000</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.message(TopUpState.entering_topup)
async def topup_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0 or amount > 1_000_000:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи число от 1 до 1,000,000")
        return

    update_balance(message.from_user.id, amount)
    await state.clear()
    user = get_user(message.from_user.id)
    await message.answer(
        f"✅ Добавлено <b>{amount:,.2f} vTON</b>\n💰 Баланс: <b>{user[2]:,.2f} vTON</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

# ─── ПОКУПКА / ПРОДАЖА (стандарт) ────────────────────────────────────────────

@dp.callback_query(F.data == "buy")
async def buy_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(action="buy")
    await state.set_state(TradeState.choosing_coin)
    await callback.message.edit_text(
        "🟢 <b>Купить монету</b>\n\nВыбери:", reply_markup=coins_keyboard("buy"), parse_mode="HTML"
    )

@dp.callback_query(F.data == "sell")
async def sell_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TradeState.choosing_coin)
    holdings = get_portfolio(callback.from_user.id)
    if not holdings:
        await callback.message.edit_text("📭 Нет монет для продажи!", reply_markup=back_keyboard())
        return

    buttons = []
    row = []
    for sym, amount, avg_price, ca in holdings:
        row.append(InlineKeyboardButton(text=f"{sym} ({amount:,.2f})", callback_data=f"sell_{sym}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")])
    await callback.message.edit_text(
        "🔴 <b>Продать монету</b>\n\nВыбери:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )

@dp.callback_query(TradeState.choosing_coin)
async def coin_chosen(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 1)
    if len(parts) != 2:
        return
    action, symbol = parts

    holdings_map = {h[0]: (h[1], h[2], h[3]) for h in get_portfolio(callback.from_user.id)}

    if symbol in MEMECOINS:
        prices = await get_prices([symbol])
        price = prices.get(symbol, 0)
    else:
        ca = holdings_map.get(symbol, ("", "", ""))[2]
        price, _ = await get_ton_token_price_by_ca(ca) if ca else (0, symbol)

    if not price:
        await callback.message.edit_text("❌ Не удалось получить цену.", reply_markup=back_keyboard())
        await state.clear()
        return

    await state.update_data(symbol=symbol, action=action, price=price)
    await state.set_state(TradeState.entering_amount)

    user = get_user(callback.from_user.id)
    balance = user[2]

    if action == "buy":
        max_coins = balance / price
        text = (
            f"🟢 <b>Купить {symbol}</b>\n\n"
            f"💵 Цена: {format_price(price)}\n"
            f"💰 Баланс: {balance:,.2f} vTON\n"
            f"📦 Макс: {max_coins:,.4f} {symbol}\n\n"
            f"Введи сумму в <b>vTON</b> или нажми кнопку:"
        )
    else:
        held, avg, _ = holdings_map.get(symbol, (0, 0, ""))
        pnl = (price - avg) / avg * 100 if avg > 0 else 0
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        text = (
            f"🔴 <b>Продать {symbol}</b>\n\n"
            f"💵 Цена: {format_price(price)}\n"
            f"📦 У тебя: {held:,.4f} {symbol}\n"
            f"{pnl_emoji} Текущий P&L: <b>{pnl:+.1f}%</b>\n"
            f"{pnl_bar(pnl)}\n\n"
            f"Введи кол-во или нажми кнопку:"
        )

    await callback.message.edit_text(text, reply_markup=amount_keyboard(action, symbol), parse_mode="HTML")

# ─── БЫСТРЫЕ КНОПКИ % ─────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("quick_"))
async def quick_amount(callback: types.CallbackQuery, state: FSMContext):
    """Обработка кнопок 25/50/75/ALL"""
    # quick_buy_NOT_50 или quick_ca_NOT_50
    parts = callback.data.split("_")
    # parts = ["quick", action, symbol, pct]
    if len(parts) < 4:
        return

    action = parts[1]  # buy / sell / ca
    pct = int(parts[-1])
    symbol = "_".join(parts[2:-1])  # на случай символов с _

    data = await state.get_data()
    price = data.get("price", 0)
    if not price:
        await callback.answer("❌ Цена недоступна", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    balance = user[2]

    if action in ("buy", "ca"):
        ton_amount = balance * pct / 100
        coin_amount = ton_amount / price
        total_cost = ton_amount
        avg_buy = price
        pnl_pct = 0.0
    else:  # sell
        holdings_map = {h[0]: h[1] for h in get_portfolio(callback.from_user.id)}
        held = holdings_map.get(symbol, 0)
        coin_amount = held * pct / 100
        total_cost = coin_amount * price
        avg_buy = get_avg_buy_price(callback.from_user.id, symbol)
        pnl_pct = (price - avg_buy) / avg_buy * 100 if avg_buy > 0 else 0.0
        ton_amount = total_cost

    if ton_amount <= 0 or coin_amount <= 0:
        await callback.answer("❌ Недостаточно средств", show_alert=True)
        return

    await state.update_data(
        coin_amount=coin_amount, total_cost=total_cost,
        pnl_pct=pnl_pct, avg_buy=avg_buy,
        ton_amount=ton_amount
    )

    if action in ("buy", "ca"):
        await state.set_state(CAState.confirming_ca if action == "ca" else TradeState.confirming)
        text = (
            f"✅ <b>Подтверди покупку ({pct}%)</b>\n\n"
            f"🪙 {symbol}: {coin_amount:,.4f} шт.\n"
            f"💵 Цена: {format_price(price)}\n"
            f"💸 Спишется: {ton_amount:,.2f} vTON"
        )
    else:
        await state.set_state(TradeState.confirming)
        pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
        text = (
            f"✅ <b>Подтверди продажу ({pct}%)</b>\n\n"
            f"🪙 {symbol}: {coin_amount:,.4f} шт.\n"
            f"💵 Цена: {format_price(price)}\n"
            f"💰 Получишь: {total_cost:,.2f} vTON\n"
            f"{pnl_emoji} P&L: <b>{pnl_pct:+.1f}%</b>\n"
            f"{pnl_bar(pnl_pct)}"
        )

    confirm_cb = "confirm_ca_trade" if action == "ca" else "confirm_trade"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=confirm_cb),
            InlineKeyboardButton(text="❌ Отмена", callback_data="back_main"),
        ]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.message(TradeState.entering_amount)
async def amount_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    action = data["action"]
    symbol = data["symbol"]
    price = data["price"]
    user = get_user(message.from_user.id, message.from_user.username or "")
    balance = user[2]
    text_input = message.text.strip().lower()

    try:
        if action == "buy":
            ton_amount = balance if text_input == "all" else float(text_input)
            if ton_amount <= 0:
                raise ValueError
            if ton_amount > balance:
                await message.answer(f"❌ Недостаточно vTON. У тебя {balance:,.2f}")
                return
            coin_amount = ton_amount / price
            total_cost = ton_amount
            pnl_pct = 0.0
            avg_buy = price
        else:
            holdings_map = {h[0]: h[1] for h in get_portfolio(message.from_user.id)}
            held = holdings_map.get(symbol, 0)
            coin_amount = held if text_input == "all" else float(text_input)
            if coin_amount <= 0:
                raise ValueError
            if coin_amount > held + 0.000001:
                await message.answer(f"❌ У тебя только {held:,.4f} {symbol}")
                return
            total_cost = coin_amount * price
            avg_buy = get_avg_buy_price(message.from_user.id, symbol)
            pnl_pct = (price - avg_buy) / avg_buy * 100 if avg_buy > 0 else 0.0
            ton_amount = total_cost
    except (ValueError, ZeroDivisionError):
        await message.answer("❌ Введи число, например: <code>100</code>", parse_mode="HTML")
        return

    await state.update_data(coin_amount=coin_amount, total_cost=total_cost, pnl_pct=pnl_pct, avg_buy=avg_buy, ton_amount=ton_amount)
    await state.set_state(TradeState.confirming)

    if action == "buy":
        text = (
            f"✅ <b>Подтверди покупку</b>\n\n"
            f"🪙 {symbol}: {coin_amount:,.4f} шт.\n"
            f"💵 Цена: {format_price(price)}\n"
            f"💸 Спишется: {total_cost:,.2f} vTON"
        )
    else:
        pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
        text = (
            f"✅ <b>Подтверди продажу</b>\n\n"
            f"🪙 {symbol}: {coin_amount:,.4f} шт.\n"
            f"💵 Цена: {format_price(price)}\n"
            f"💰 Получишь: {total_cost:,.2f} vTON\n"
            f"{pnl_emoji} P&L по сделке: <b>{pnl_pct:+.1f}%</b>\n"
            f"{pnl_bar(pnl_pct)}"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_trade"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="back_main"),
        ]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(TradeState.confirming, F.data == "confirm_trade")
async def confirm_trade(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    action = data["action"]
    symbol = data["symbol"]
    coin_amount = data["coin_amount"]
    price = data["price"]
    total_cost = data["total_cost"]
    pnl_pct = data.get("pnl_pct", 0.0)
    avg_buy = data.get("avg_buy", price)
    user_id = callback.from_user.id

    if action == "buy":
        update_balance(user_id, -total_cost)
        update_portfolio(user_id, symbol, coin_amount, price)
        save_trade(user_id, symbol, "BUY", coin_amount, price, price, total_cost, 0.0)
        text = (
            f"🟢 Куплено <b>{coin_amount:,.4f} {symbol}</b>\n"
            f"💸 Потрачено: {total_cost:,.2f} vTON\n"
            f"💵 Цена входа: {format_price(price)}"
        )
    else:
        update_balance(user_id, total_cost)
        update_portfolio(user_id, symbol, -coin_amount, price)
        save_trade(user_id, symbol, "SELL", coin_amount, price, avg_buy, total_cost, pnl_pct)
        pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
        pnl_abs = coin_amount * (price - avg_buy)
        sign = "+" if pnl_abs >= 0 else ""
        text = (
            f"🔴 Продано <b>{coin_amount:,.4f} {symbol}</b>\n"
            f"💰 Получено: {total_cost:,.2f} vTON\n"
            f"{pnl_emoji} P&L: <b>{pnl_pct:+.1f}%</b> ({sign}{pnl_abs:,.2f} vTON)\n"
            f"{pnl_bar(pnl_pct)}"
        )

    await state.clear()
    user = get_user(user_id)
    text += f"\n\n💰 Баланс: <b>{user[2]:,.2f} vTON</b>"
    await callback.message.edit_text(text, reply_markup=main_keyboard(), parse_mode="HTML")

# ─── ПОКУПКА ПО CA (вручную из меню) ─────────────────────────────────────────

@dp.callback_query(F.data == "buy_ca")
async def buy_ca_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(CAState.entering_ca)
    text = (
        "🔍 <b>Купить токен по CA</b>\n\n"
        "Отправь контрактный адрес токена TON.\n\n"
        "📌 CA можно найти на:\n"
        "• <b>GeckoTerminal</b> (geckoterminal.com)\n"
        "• <b>STON.fi</b> или <b>DeDust</b>\n\n"
        "💡 Или просто кинь CA прямо в чат из любого места!"
    )
    await callback.message.edit_text(text, reply_markup=back_keyboard(), parse_mode="HTML")

@dp.message(CAState.entering_ca)
async def ca_received(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    if len(ca) < 10:
        await message.answer("❌ Слишком короткий адрес. Проверь и попробуй снова.")
        return

    await message.answer("⏳ Ищу токен...")
    price, symbol = await get_ton_token_price_by_ca(ca)

    if price == 0:
        await message.answer(
            "❌ Токен не найден.\n\nУбедись что адрес правильный и токен торгуется на DeDust/STON.fi.",
            reply_markup=back_keyboard()
        )
        return

    await state.update_data(ca=ca, symbol=symbol, price=price)
    await state.set_state(CAState.entering_ca_amount)

    user = get_user(message.from_user.id)
    balance = user[2]
    max_coins = balance / price

    text = (
        f"✅ Найден: <b>{symbol}</b>\n\n"
        f"💵 Цена: {format_price(price)}\n"
        f"💰 Баланс: {balance:,.2f} vTON\n"
        f"📦 Макс: {max_coins:,.4f} {symbol}\n\n"
        f"Введи сумму в <b>vTON</b> или нажми кнопку:"
    )
    await message.answer(text, reply_markup=amount_keyboard("ca", symbol), parse_mode="HTML")

@dp.message(CAState.entering_ca_amount)
async def ca_amount_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ca = data["ca"]
    symbol = data["symbol"]
    price = data["price"]
    user = get_user(message.from_user.id)
    balance = user[2]
    text_input = message.text.strip().lower()

    try:
        ton_amount = balance if text_input == "all" else float(text_input)
        if ton_amount <= 0:
            raise ValueError
        if ton_amount > balance:
            await message.answer(f"❌ Недостаточно vTON. У тебя {balance:,.2f}")
            return
        coin_amount = ton_amount / price
    except ValueError:
        await message.answer("❌ Введи число, например: <code>500</code>", parse_mode="HTML")
        return

    await state.update_data(ton_amount=ton_amount, coin_amount=coin_amount)
    await state.set_state(CAState.confirming_ca)

    text = (
        f"✅ <b>Подтверди покупку</b>\n\n"
        f"🪙 {symbol}: {coin_amount:,.4f} шт.\n"
        f"💵 Цена: {format_price(price)}\n"
        f"💸 Спишется: {ton_amount:,.2f} vTON\n"
        f"📋 CA: <code>{ca[:24]}...</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Купить", callback_data="confirm_ca_trade"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="back_main"),
        ]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(CAState.confirming_ca, F.data == "confirm_ca_trade")
async def confirm_ca_trade(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ca = data["ca"]
    symbol = data["symbol"]
    price = data["price"]
    ton_amount = data.get("ton_amount") or data.get("total_cost", 0)
    coin_amount = data.get("coin_amount", 0)
    user_id = callback.from_user.id

    update_balance(user_id, -ton_amount)
    update_portfolio(user_id, symbol, coin_amount, price, ca)
    save_trade(user_id, symbol, "BUY", coin_amount, price, price, ton_amount, 0.0)

    await state.clear()
    user = get_user(user_id)
    text = (
        f"🟢 Куплено <b>{coin_amount:,.4f} {symbol}</b>\n"
        f"💸 Потрачено: {ton_amount:,.2f} vTON\n"
        f"💵 Цена входа: {format_price(price)}\n\n"
        f"💰 Баланс: <b>{user[2]:,.2f} vTON</b>"
    )
    await callback.message.edit_text(text, reply_markup=main_keyboard(), parse_mode="HTML")

# ─── ИСТОРИЯ ──────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "history")
async def show_history(callback: types.CallbackQuery):
    trades = get_trades(callback.from_user.id, 10)
    if not trades:
        await callback.message.edit_text("📜 История пуста!", reply_markup=back_keyboard())
        return

    lines = ["📜 <b>Последние сделки</b>\n"]
    for sym, action, amount, price, price_buy, total, pnl_pct, ts in trades:
        if action == "BUY":
            lines.append(
                f"🟢 {ts} | <b>{sym}</b> BUY\n"
                f"   {amount:,.4f} шт. @ {format_price(price)} = {total:,.2f} vTON"
            )
        else:
            pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
            lines.append(
                f"🔴 {ts} | <b>{sym}</b> SELL\n"
                f"   {amount:,.4f} шт. @ {format_price(price)} = {total:,.2f} vTON\n"
                f"   {pnl_emoji} P&L: <b>{pnl_pct:+.1f}%</b> (вход: {format_price(price_buy)})\n"
                f"   {pnl_bar(pnl_pct)}"
            )

    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── ГРАФИК ───────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "chart_menu")
async def chart_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📈 <b>График цены</b>\n\nВыбери монету:",
        reply_markup=coins_keyboard("chart"),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("chart_"))
async def show_chart(callback: types.CallbackQuery):
    symbol = callback.data[6:]
    await callback.message.edit_text(f"⏳ Загружаю график {symbol}...")

    holdings_map = {h[0]: h[3] for h in get_portfolio(callback.from_user.id)}
    ca = holdings_map.get(symbol, "")

    if symbol in MEMECOINS:
        history = await get_price_history(symbol, days=7)
    elif ca:
        history = await get_ton_token_history_by_ca(ca, days=7)
    else:
        await callback.message.edit_text("❌ Нет данных.", reply_markup=back_keyboard())
        return

    if not history:
        await callback.message.edit_text("❌ Не удалось загрузить данные.", reply_markup=back_keyboard())
        return

    timestamps = [datetime.fromtimestamp(p[0] / 1000) for p in history]
    prices_hist = [p[1] for p in history]

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')
    color = '#00ff88' if prices_hist[-1] >= prices_hist[0] else '#ff4444'
    ax.plot(timestamps, prices_hist, color=color, linewidth=2)
    ax.fill_between(timestamps, prices_hist, alpha=0.15, color=color)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    plt.xticks(rotation=45, color='#888888')
    plt.yticks(color='#888888')
    for spine in ['bottom', 'left']:
        ax.spines[spine].set_color('#333333')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(colors='#888888')
    change = (prices_hist[-1] - prices_hist[0]) / prices_hist[0] * 100
    ax.set_title(f'{symbol} — 7 дней ({change:+.1f}%)', color='white', fontsize=14, pad=15)
    ax.set_ylabel('USD', color='#888888')
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, bbox_inches='tight')
    buf.seek(0)
    plt.close()

    await callback.message.delete()
    await bot.send_photo(
        callback.from_user.id,
        photo=BufferedInputFile(buf.read(), filename="chart.png"),
        caption=f"📈 <b>{symbol}</b> | {format_price(prices_hist[-1])} | 7д: {change:+.1f}%",
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )

# ─── СБРОС ────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "reset_confirm")
async def reset_confirm(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Сбросить", callback_data="reset_do"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="back_main"),
        ]
    ])
    await callback.message.edit_text(
        "⚠️ Сбросить всё и начать заново?\nБаланс вернётся к 10,000 vTON.",
        reply_markup=kb
    )

@dp.callback_query(F.data == "reset_do")
async def reset_do(callback: types.CallbackQuery):
    reset_user(callback.from_user.id)
    await callback.message.edit_text(
        "🔄 Аккаунт сброшен!\n💰 Баланс: 10,000 vTON", reply_markup=main_keyboard()
    )

# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    init_db()
    logger.info("MemeTrader bot started!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
