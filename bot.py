cat > /mnt/user-data/outputs/bot.py << 'BOTEOF'
import logging
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
STARTING_BALANCE = 10.0
MAX_TOPUP = 1000.0
TON_CA_PATTERN = re.compile(r'\b(EQ|UQ)[A-Za-z0-9_\-]{46}\b')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# ─── PRICES ───────────────────────────────────────────────────────────────────

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
                    fdv = float(attrs.get("fdv_usd") or 0)
                    if price > 0: return price, symbol, fdv
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
                    if price > 0: return price, symbol, 0.0
    except Exception as e: logger.warning(f"STON.fi: {e}")
    # 3. DeDust via GeckoTerminal
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
                        if price > 0: return price, ca[:8].upper(), 0.0
    except Exception as e: logger.warning(f"DeDust: {e}")
    # 4. TonAPI
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://tonapi.io/v2/jettons/{ca}", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    symbol = data.get("metadata",{}).get("symbol") or ca[:8].upper()
                    price = float(data.get("dex_usd_price") or 0)
                    if price > 0: return price, symbol, 0.0
    except Exception as e: logger.warning(f"TonAPI: {e}")
    return 0.0, ca[:8].upper(), 0.0

async def get_ton_token_history_by_ca(ca, days=7):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/ohlcv/day?limit={days}",
                             timeout=aiohttp.ClientTimeout(total=10), headers={"Accept":"application/json"}) as r:
                if r.status == 200:
                    data = await r.json()
                    ohlcv = data.get("data",{}).get("attributes",{}).get("ohlcv_list",[])
                    return [[item[0]*1000, item[4]] for item in reversed(ohlcv)]
    except Exception as e: logger.warning(f"GT history: {e}")
    return []

def format_price(p):
    if p == 0: return "$0"
    if p < 0.000001: return f"${p:.10f}"
    if p < 0.001: return f"${p:.8f}"
    if p < 1: return f"${p:.6f}"
    return f"${p:.4f}"

def format_mcap(fdv):
    if fdv <= 0: return "N/A"
    if fdv >= 1_000_000_000: return f"${fdv/1_000_000_000:.2f}B"
    if fdv >= 1_000_000: return f"${fdv/1_000_000:.2f}M"
    if fdv >= 1_000: return f"${fdv/1_000:.1f}K"
    return f"${fdv:.0f}"

def pnl_bar(pnl_pct):
    filled = min(int(abs(pnl_pct)/5), 10)
    return ("🟩" if pnl_pct >= 0 else "🟥") * filled + "⬜" * (10-filled)

def dexscreener_link(ca):
    return f"https://dexscreener.com/ton/{ca}"

# ─── FSM ──────────────────────────────────────────────────────────────────────

class CAState(StatesGroup):
    entering_ca = State()
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
        InlineKeyboardButton("📊 My Trades", callback_data="history"),
        InlineKeyboardButton("🔴 Sell", callback_data="sell"),
        InlineKeyboardButton("💎 Balance", callback_data="balance_menu"),
    )
    kb.add(InlineKeyboardButton("🔄 Reset Account", callback_data="reset_confirm"))
    return kb

def amount_keyboard(action, safe_sym):
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(
        InlineKeyboardButton("25%", callback_data=f"q_{action}_{safe_sym}_25"),
        InlineKeyboardButton("50%", callback_data=f"q_{action}_{safe_sym}_50"),
        InlineKeyboardButton("75%", callback_data=f"q_{action}_{safe_sym}_75"),
        InlineKeyboardButton("ALL IN 🔥", callback_data=f"q_{action}_{safe_sym}_100"),
    )
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    return kb

def back_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("◀️ Main Menu", callback_data="back_main"))
    return kb

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    await message.answer(
        f"👋 Welcome, <b>{message.from_user.first_name}</b>!\n\n"
        f"🎮 <b>MemeTrader</b> — TON meme coin paper trading\n\n"
        f"💎 Balance: <b>{user[2]:,.4f} 💎 GRAM</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔍 <b>Drop any TON token CA in chat</b>\n"
        f"Bot finds it instantly — price, mcap, chart link\n\n"
        f"📊 Real prices · P&L tracking · Paper trades\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<i>/setbalance — set custom balance</i>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(commands=["setbalance"])
async def cmd_setbalance(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("📝 Example: <code>/setbalance 500</code>", parse_mode="HTML"); return
    try:
        amount = float(args[1].replace(",",""))
        if amount < 1 or amount > 10_000_000: raise ValueError
    except:
        await message.answer("❌ Enter a number between 1 and 10,000,000"); return
    set_balance(message.from_user.id, amount)
    await message.answer(f"✅ Balance updated!\n💎 <b>{amount:,.4f} GRAM</b>", reply_markup=main_keyboard(), parse_mode="HTML")

@dp.callback_query_handler(text="back_main", state="*")
async def back_main(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"🏠 <b>Main Menu</b>\n💎 Balance: <b>{user[2]:,.4f} GRAM</b>\n\n🔍 Drop a CA in chat to trade any token!",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── AUTO CA DETECT ──────────────────────────────────────────────────────────

@dp.message_handler(lambda m: bool(TON_CA_PATTERN.search(m.text or "")), state="*")
async def auto_detect_ca(message: types.Message, state: FSMContext):
    await state.finish()
    ca = TON_CA_PATTERN.search(message.text).group(0)
    msg = await message.answer("🔍 Scanning token...")
    price, symbol, fdv = await get_ton_token_price_by_ca(ca)
    if price == 0:
        await msg.edit_text(
            "❌ <b>Token not found</b>\n\n"
            "Not listed on DeDust / STON.fi yet\n"
            "Try again when it has liquidity 💧",
            reply_markup=back_keyboard(), parse_mode="HTML"
        ); return
    user = get_user(message.from_user.id)
    balance = user[2]
    safe_sym = re.sub(r'[^A-Za-z0-9]', '', symbol)[:12]
    await state.update_data(ca=ca, symbol=symbol, price=price, safe_sym=safe_sym)
    await CAState.entering_ca_amount.set()
    dex_link = dexscreener_link(ca)
    mcap_str = format_mcap(fdv)
    await msg.edit_text(
        f"🚀 <b>{symbol}</b>\n\n"
        f"💵 Price:  <b>{format_price(price)}</b>\n"
        f"📊 MCap:  <b>{mcap_str}</b>\n"
        f"💎 Balance:  <b>{balance:,.4f} GRAM</b>\n"
        f"📦 Max buy:  <b>{balance/price:,.2f} {symbol}</b>\n\n"
        f"🔗 <a href='{dex_link}'>View on DexScreener</a>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Enter amount in <b>💎 GRAM</b> or tap:",
        reply_markup=amount_keyboard("ca", safe_sym),
        parse_mode="HTML", disable_web_page_preview=True
    )

# ─── PORTFOLIO ───────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="portfolio")
async def show_portfolio(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    balance = user[2]
    holdings = get_portfolio(callback.from_user.id)
    if not holdings:
        await callback.message.edit_text(
            f"💼 <b>Portfolio</b>\n\n"
            f"💎 Free balance: <b>{balance:,.4f} GRAM</b>\n\n"
            f"📭 No open positions\n\n"
            f"<i>Drop a CA in chat to start trading!</i>",
            reply_markup=back_keyboard(), parse_mode="HTML"); return

    prices = {}
    for sym, amount, avg_price, ca in holdings:
        if ca:
            p, _, _ = await get_ton_token_price_by_ca(ca)
            if p: prices[sym] = p

    total_value = balance
    lines = [f"💼 <b>Portfolio</b>\n\n💎 Free: <b>{balance:,.4f} GRAM</b>\n"]
    for sym, amount, avg_price, ca in holdings:
        cur = prices.get(sym, avg_price)
        value = amount * cur
        pnl = (cur - avg_price) / avg_price * 100 if avg_price > 0 else 0
        pnl_abs = value - amount * avg_price
        total_value += value
        sign = "+" if pnl_abs >= 0 else ""
        dex = f"<a href='{dexscreener_link(ca)}'>{sym}</a>" if ca else f"<b>{sym}</b>"
        lines.append(
            f"{'🟢' if pnl>=0 else '🔴'} {dex}\n"
            f"   📦 {amount:,.4f} | entry: {format_price(avg_price)}\n"
            f"   💵 Now: {format_price(cur)}\n"
            f"   {pnl_bar(pnl)} {pnl:+.1f}%\n"
            f"   💎 {value:,.4f} GRAM ({sign}{pnl_abs:,.4f})"
        )
    pnl_total = (total_value - STARTING_BALANCE) / STARTING_BALANCE * 100
    pnl_abs_total = total_value - STARTING_BALANCE
    sign = "+" if pnl_abs_total >= 0 else ""
    lines.append(
        f"\n━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Total: {total_value:,.4f} 💎 GRAM</b>\n"
        f"{'🟢' if pnl_total>=0 else '🔴'} Overall P&L: <b>{pnl_total:+.1f}%</b> ({sign}{pnl_abs_total:,.4f} 💎)\n"
        f"{pnl_bar(pnl_total)}"
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML", disable_web_page_preview=True)

# ─── SELL ─────────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="sell")
async def sell_menu(callback: types.CallbackQuery):
    await SellState.choosing_coin.set()
    holdings = get_portfolio(callback.from_user.id)
    if not holdings:
        await callback.message.edit_text("📭 <b>Nothing to sell!</b>\n\nDrop a CA to buy something first.", reply_markup=back_keyboard(), parse_mode="HTML"); return
    kb = InlineKeyboardMarkup(row_width=1)
    for sym, amount, avg_price, ca in holdings:
        kb.add(InlineKeyboardButton(f"🔴 {sym} — {amount:,.4f}", callback_data=f"sellcoin_{sym}"))
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    await callback.message.edit_text("🔴 <b>Sell</b>\n\nChoose a position:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query_handler(lambda c: c.data.startswith("sellcoin_"), state=SellState.choosing_coin)
async def sell_coin_chosen(callback: types.CallbackQuery, state: FSMContext):
    symbol = callback.data[9:]
    holdings_map = {h[0]: (h[1], h[2], h[3]) for h in get_portfolio(callback.from_user.id)}
    if symbol not in holdings_map:
        await callback.message.edit_text("❌ Position not found.", reply_markup=back_keyboard())
        await state.finish(); return
    held, avg, ca = holdings_map[symbol]
    price, _, _ = await get_ton_token_price_by_ca(ca) if ca else (0, symbol, 0)
    if not price: price = avg
    safe_sym = re.sub(r'[^A-Za-z0-9]', '', symbol)[:12]
    pnl = (price-avg)/avg*100 if avg > 0 else 0
    await state.update_data(symbol=symbol, price=price, ca=ca, safe_sym=safe_sym, held=held, avg=avg)
    await SellState.entering_amount.set()
    await callback.message.edit_text(
        f"🔴 <b>Sell {symbol}</b>\n\n"
        f"💵 Current price: <b>{format_price(price)}</b>\n"
        f"📦 You hold: <b>{held:,.4f} {symbol}</b>\n"
        f"📈 Entry: <b>{format_price(avg)}</b>\n"
        f"{'🟢' if pnl>=0 else '🔴'} P&L: <b>{pnl:+.1f}%</b>\n"
        f"{pnl_bar(pnl)}\n\n"
        f"Enter amount or tap:",
        reply_markup=amount_keyboard("sell", safe_sym), parse_mode="HTML"
    )

@dp.message_handler(state=SellState.entering_amount)
async def sell_amount_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    symbol=data["symbol"]; price=data["price"]; held=data["held"]; avg=data["avg"]
    text_input = message.text.strip().lower()
    try:
        coin_amount = held if text_input == "all" else float(text_input)
        if coin_amount <= 0: raise ValueError
        if coin_amount > held+0.000001:
            await message.answer(f"❌ You only have {held:,.4f} {symbol}"); return
    except:
        await message.answer("❌ Enter a number, e.g. <code>100</code>", parse_mode="HTML"); return
    total = coin_amount * price
    pnl_pct = (price-avg)/avg*100 if avg > 0 else 0
    pnl_abs = coin_amount*(price-avg)
    await state.update_data(coin_amount=coin_amount, total=total, pnl_pct=pnl_pct, pnl_abs=pnl_abs)
    await SellState.confirming.set()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Confirm Sell", callback_data="confirm_sell"),
           InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
    await message.answer(
        f"✅ <b>Confirm Sell</b>\n\n"
        f"🪙 {coin_amount:,.4f} <b>{symbol}</b>\n"
        f"💵 Price: {format_price(price)}\n"
        f"💎 You receive: <b>{total:,.4f} GRAM</b>\n"
        f"{'🟢' if pnl_pct>=0 else '🔴'} P&L: <b>{pnl_pct:+.1f}%</b> ({'+' if pnl_abs>=0 else ''}{pnl_abs:,.4f} 💎)\n"
        f"{pnl_bar(pnl_pct)}",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="confirm_sell", state=SellState.confirming)
async def confirm_sell(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    symbol=data["symbol"]; price=data["price"]; coin_amount=data["coin_amount"]
    total=data["total"]; pnl_pct=data["pnl_pct"]; pnl_abs=data["pnl_abs"]; avg=data["avg"]
    user_id = callback.from_user.id
    update_balance(user_id, total)
    update_portfolio(user_id, symbol, -coin_amount, price)
    save_trade(user_id, symbol, "SELL", coin_amount, price, avg, total, pnl_pct)
    await state.finish()
    user = get_user(user_id)
    await callback.message.edit_text(
        f"🔴 Sold <b>{coin_amount:,.4f} {symbol}</b>\n"
        f"💎 Received: <b>{total:,.4f} GRAM</b>\n"
        f"{'🟢' if pnl_pct>=0 else '🔴'} P&L: <b>{pnl_pct:+.1f}%</b> ({'+' if pnl_abs>=0 else ''}{pnl_abs:,.4f} 💎)\n"
        f"{pnl_bar(pnl_pct)}\n\n"
        f"💎 Balance: <b>{user[2]:,.4f} GRAM</b>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── QUICK % BUTTONS ─────────────────────────────────────────────────────────

@dp.callback_query_handler(lambda c: c.data.startswith("q_"), state="*")
async def quick_amount(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 4: return
    action = parts[1]; pct = int(parts[-1])
    data = await state.get_data()
    price = data.get("price", 0)
    symbol = data.get("symbol", "")
    ca = data.get("ca", "")
    avg = data.get("avg", price)
    if not price: await callback.answer("❌ Price unavailable", show_alert=True); return
    user = get_user(callback.from_user.id); balance = user[2]
    if action in ("buy", "ca"):
        ton_amount = balance * pct / 100
        if ton_amount <= 0: await callback.answer("❌ Insufficient balance", show_alert=True); return
        coin_amount = ton_amount / price
        total = ton_amount; pnl_pct = 0.0; pnl_abs = 0.0
    else:
        held = data.get("held", 0)
        if not held:
            holdings_map = {h[0]: h[1] for h in get_portfolio(callback.from_user.id)}
            held = holdings_map.get(symbol, 0)
        coin_amount = held * pct / 100
        if coin_amount <= 0: await callback.answer("❌ Nothing to sell", show_alert=True); return
        total = coin_amount * price
        pnl_pct = (price-avg)/avg*100 if avg > 0 else 0.0
        pnl_abs = coin_amount*(price-avg)
        ton_amount = total
    await state.update_data(coin_amount=coin_amount, total=total, total_cost=total,
                            pnl_pct=pnl_pct, pnl_abs=pnl_abs, avg_buy=avg, ton_amount=ton_amount)
    if action == "ca":
        await CAState.confirming_ca.set()
        confirm_cb = "confirm_ca_trade"
    elif action == "sell":
        await SellState.confirming.set()
        confirm_cb = "confirm_sell"
    else:
        await CAState.confirming_ca.set()
        confirm_cb = "confirm_ca_trade"

    if action in ("buy", "ca"):
        text = (f"✅ <b>Confirm Buy ({pct}%)</b>\n\n"
                f"🪙 {coin_amount:,.4f} <b>{symbol}</b>\n"
                f"💵 Price: {format_price(price)}\n"
                f"💎 Cost: <b>{ton_amount:,.4f} GRAM</b>")
    else:
        text = (f"✅ <b>Confirm Sell ({pct}%)</b>\n\n"
                f"🪙 {coin_amount:,.4f} <b>{symbol}</b>\n"
                f"💵 Price: {format_price(price)}\n"
                f"💎 You get: <b>{total:,.4f} GRAM</b>\n"
                f"{'🟢' if pnl_pct>=0 else '🔴'} P&L: <b>{pnl_pct:+.1f}%</b>\n{pnl_bar(pnl_pct)}")
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Confirm", callback_data=confirm_cb),
           InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

# ─── CA BUY CONFIRM ──────────────────────────────────────────────────────────

@dp.message_handler(state=CAState.entering_ca_amount)
async def ca_amount_entered(message: types.Message, state: FSMContext):
    data = await state.get_data()
    symbol=data["symbol"]; price=data["price"]; ca=data["ca"]
    safe_sym=data.get("safe_sym", re.sub(r'[^A-Za-z0-9]','',symbol)[:12])
    user = get_user(message.from_user.id); balance = user[2]
    text_input = message.text.strip().lower()
    try:
        ton_amount = balance if text_input == "all" else float(text_input)
        if ton_amount <= 0: raise ValueError
        if ton_amount > balance: await message.answer(f"❌ Not enough. Balance: {balance:,.4f} GRAM"); return
        coin_amount = ton_amount/price
    except:
        await message.answer("❌ Enter a number, e.g. <code>5</code>", parse_mode="HTML"); return
    await state.update_data(ton_amount=ton_amount, coin_amount=coin_amount)
    await CAState.confirming_ca.set()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Buy", callback_data="confirm_ca_trade"),
           InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
    await message.answer(
        f"✅ <b>Confirm Buy</b>\n\n"
        f"🪙 {coin_amount:,.4f} <b>{symbol}</b>\n"
        f"💵 Price: {format_price(price)}\n"
        f"💎 Cost: <b>{ton_amount:,.4f} GRAM</b>",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="confirm_ca_trade", state=CAState.confirming_ca)
async def confirm_ca_trade(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ca=data["ca"]; symbol=data["symbol"]; price=data["price"]
    ton_amount=data.get("ton_amount") or data.get("total_cost", data.get("total",0))
    coin_amount=data.get("coin_amount",0)
    user_id = callback.from_user.id
    update_balance(user_id, -ton_amount)
    update_portfolio(user_id, symbol, coin_amount, price, ca)
    save_trade(user_id, symbol, "BUY", coin_amount, price, price, ton_amount, 0.0)
    await state.finish()
    user = get_user(user_id)
    await callback.message.edit_text(
        f"🟢 Bought <b>{coin_amount:,.4f} {symbol}</b>\n"
        f"💎 Spent: <b>{ton_amount:,.4f} GRAM</b>\n"
        f"💵 Entry: {format_price(price)}\n\n"
        f"💎 Balance: <b>{user[2]:,.4f} GRAM</b>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── BALANCE ─────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="balance_menu")
async def balance_menu(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"➕ Add GRAM (max {MAX_TOPUP:,.0f})", callback_data="topup"))
    kb.add(InlineKeyboardButton("✏️ Set manually", callback_data="setbalance_menu"))
    kb.add(InlineKeyboardButton("◀️ Back", callback_data="back_main"))
    await callback.message.edit_text(
        f"💎 <b>Balance</b>\n\nCurrent: <b>{user[2]:,.4f} 💎 GRAM</b>",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="setbalance_menu")
async def setbalance_menu(callback: types.CallbackQuery):
    await SetBalanceState.entering_balance.set()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"✏️ <b>Set Balance</b>\n\nCurrent: <b>{user[2]:,.4f} 💎 GRAM</b>\n\nEnter new balance:",
        reply_markup=back_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(state=SetBalanceState.entering_balance)
async def setbalance_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",",""))
        if amount < 1 or amount > 10_000_000: raise ValueError
    except:
        await message.answer("❌ Enter a number between 1 and 10,000,000"); return
    set_balance(message.from_user.id, amount)
    await state.finish()
    await message.answer(f"✅ Balance set!\n💎 <b>{amount:,.4f} GRAM</b>", reply_markup=main_keyboard(), parse_mode="HTML")

@dp.callback_query_handler(text="topup")
async def topup_start(callback: types.CallbackQuery):
    await TopUpState.entering_topup.set()
    user = get_user(callback.from_user.id)
    await callback.message.edit_text(
        f"➕ <b>Add GRAM</b>\n\n"
        f"Current: <b>{user[2]:,.4f} GRAM</b>\n"
        f"Max per top-up: <b>{MAX_TOPUP:,.0f} GRAM</b>\n\n"
        f"How much to add?",
        reply_markup=back_keyboard(), parse_mode="HTML"
    )

@dp.message_handler(state=TopUpState.entering_topup)
async def topup_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0: raise ValueError
        if amount > MAX_TOPUP:
            await message.answer(f"❌ Max top-up is {MAX_TOPUP:,.0f} GRAM at a time"); return
    except:
        await message.answer(f"❌ Enter a number between 1 and {MAX_TOPUP:,.0f}"); return
    update_balance(message.from_user.id, amount)
    await state.finish()
    user = get_user(message.from_user.id)
    await message.answer(
        f"✅ Added <b>{amount:,.4f} 💎 GRAM</b>\n💎 New balance: <b>{user[2]:,.4f} GRAM</b>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── HISTORY ─────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="history")
async def show_history(callback: types.CallbackQuery):
    trades = get_trades(callback.from_user.id, 15)
    if not trades:
        await callback.message.edit_text(
            "📊 <b>Trade History</b>\n\n📭 No trades yet!\n\n<i>Drop a CA in chat to start</i>",
            reply_markup=back_keyboard(), parse_mode="HTML"); return
    lines = ["📊 <b>Trade History</b>\n"]
    for sym, action, amount, price, price_buy, total, pnl_pct, ts in trades:
        if action == "BUY":
            lines.append(f"🟢 {ts} · <b>{sym}</b>\n   +{amount:,.4f} @ {format_price(price)} · {total:,.4f} 💎")
        else:
            lines.append(
                f"🔴 {ts} · <b>{sym}</b>\n"
                f"   -{amount:,.4f} @ {format_price(price)} · {total:,.4f} 💎\n"
                f"   {'🟢' if pnl_pct>=0 else '🔴'} P&L: <b>{pnl_pct:+.1f}%</b>  {pnl_bar(pnl_pct)}"
            )
    await callback.message.edit_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode="HTML")

# ─── RESET ───────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="reset_confirm")
async def reset_confirm(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Yes, reset", callback_data="reset_do"),
           InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
    await callback.message.edit_text(
        f"⚠️ <b>Reset Account?</b>\n\nAll positions and history will be cleared.\nBalance returns to <b>{STARTING_BALANCE} 💎 GRAM</b>.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="reset_do")
async def reset_do(callback: types.CallbackQuery):
    reset_user(callback.from_user.id)
    await callback.message.edit_text(
        f"🔄 Account reset!\n💎 Balance: <b>{STARTING_BALANCE} GRAM</b>",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── UNKNOWN TEXT ────────────────────────────────────────────────────────────

@dp.message_handler(content_types=types.ContentType.TEXT)
async def unknown_message(message: types.Message):
    await message.answer(
        "🔍 <b>Drop a TON CA to trade</b>\n\nOr use the menu below 👇",
        reply_markup=main_keyboard(), parse_mode="HTML"
    )

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    logger.info("MemeTrader bot started!")
    executor.start_polling(dp, skip_updates=True)
BOTEOF
