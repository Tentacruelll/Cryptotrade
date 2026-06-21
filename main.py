import logging
import aiohttp
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

try:
    import psycopg2
    import psycopg2.extras
    USE_PG = True
except ImportError:
    import sqlite3
    USE_PG = False

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CG_API_KEY = os.getenv("CG_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
CG_HEADERS = {"Accept": "application/json", **({"x-cg-demo-api-key": CG_API_KEY} if CG_API_KEY else {})}
STARTING_BALANCE = 10.0
MAX_TOPUP = 1000.0
CHANNEL_URL = "https://t.me/cocoonsosun"
ADMIN_ID = 7806906687

# ─── i18n ───────────────────────────────────────────────────────────────────

TRANSLATIONS = {
    "welcome_back": {"en": "👋 Welcome back, <b>{name}</b>", "ru": "👋 С возвращением, <b>{name}</b>"},
    "subtitle": {"en": "🐙 <b>OCTOtrade</b> — TON demo trading terminal", "ru": "🐙 <b>OCTOtrade</b> — демо-терминал трейдинга на TON"},
    "balance_line": {"en": "💎 Balance:  <b>{bal} GRAM</b>  <i>(≈ ${usd})</i>", "ru": "💎 Баланс:  <b>{bal} GRAM</b>  <i>(≈ ${usd})</i>"},
    "ton_price_line": {"en": "📈 TON Price:  <b>${price}</b>", "ru": "📈 Цена TON:  <b>${price}</b>"},
    "ready_to_trade": {"en": "🚀 Ready to trade?", "ru": "🚀 Готовы к трейдингу?"},
    "send_ca": {"en": "Send any TON contract address to start a demo trade.", "ru": "Отправь любой контракт-адрес TON чтобы начать демо-сделку."},
    "setbalance_hint": {"en": "⚙️ /setbalance — customize your demo balance", "ru": "⚙️ /setbalance — изменить демо-баланс"},
    "our_channel": {"en": "Our channel 📌", "ru": "Наш канал 📌"},

    "btn_portfolio": {"en": "💼 Portfolio", "ru": "💼 Портфель"},
    "btn_history": {"en": "📊 History", "ru": "📊 История"},
    "btn_performance": {"en": "📈 Performance", "ru": "📈 Статистика"},
    "btn_sell": {"en": "🔴 Sell", "ru": "🔴 Продать"},
    "btn_balance": {"en": "💎 Balance", "ru": "💎 Баланс"},
    "btn_reset": {"en": "🔄 Reset", "ru": "🔄 Сброс"},
    "btn_back": {"en": "◀️ Back", "ru": "◀️ Назад"},
    "btn_refresh": {"en": "🔄 Refresh", "ru": "🔄 Обновить"},
    "btn_language": {"en": "🌐 Language", "ru": "🌐 Язык"},

    "portfolio_title": {"en": "💼 <b>Portfolio</b>", "ru": "💼 <b>Портфель</b>"},
    "no_positions": {"en": "<i>No open positions. Drop a CA to trade.</i>", "ru": "<i>Нет открытых позиций. Отправь контракт-адрес чтобы начать торговать.</i>"},
    "total_label": {"en": "Total", "ru": "Итого"},

    "history_title": {"en": "📅 <b>History</b>", "ru": "📅 <b>История</b>"},
    "no_trades": {"en": "<i>No trades yet.</i>", "ru": "<i>Сделок пока нет.</i>"},
    "trades_word": {"en": "trades", "ru": "сделок"},
    "today": {"en": "Today", "ru": "Сегодня"},
    "yesterday": {"en": "Yesterday", "ru": "Вчера"},
    "no_trades_day": {"en": "<i>No trades.</i>", "ru": "<i>Сделок нет.</i>"},
    "buy_word": {"en": "BUY", "ru": "ПОКУПКА"},
    "sell_word": {"en": "SELL", "ru": "ПРОДАЖА"},

    "performance_title": {"en": "📈 <b>Performance</b>", "ru": "📈 <b>Статистика</b>"},
    "no_trades_perf": {"en": "<i>No trades yet. Start trading to see your stats!</i>", "ru": "<i>Сделок пока нет. Начни торговать чтобы увидеть статистику!</i>"},
    "perf_trades": {"en": "Trades", "ru": "Сделок"},
    "perf_winrate": {"en": "Win rate", "ru": "Win rate"},
    "perf_volume": {"en": "Volume", "ru": "Объём"},
    "perf_best": {"en": "Best trade", "ru": "Лучшая сделка"},
    "perf_avghold": {"en": "Avg hold", "ru": "Среднее удержание"},

    "choose_lang": {"en": "🌐 Choose your language:", "ru": "🌐 Выбери язык:"},
    "lang_updated": {"en": "✅ Language updated!", "ru": "✅ Язык изменён!"},
    "main_menu_title": {"en": "🏠 <b>Main Menu</b>", "ru": "🏠 <b>Главное меню</b>"},
    "drop_ca": {"en": "<i>Drop a CA to trade any token</i>", "ru": "<i>Отправь контракт-адрес чтобы торговать любым токеном</i>"},

    "token_not_found": {"en": "❌ <b>Token not found</b>\n\nNot listed on DeDust / STON.fi yet.", "ru": "❌ <b>Токен не найден</b>\n\nЕщё не залистен на DeDust / STON.fi."},
    "ton_price_unavailable": {"en": "❌ <b>Unable to fetch TON price</b>\n\nPlease try again in a moment.", "ru": "❌ <b>Не удалось получить цену TON</b>\n\nПопробуй ещё раз через минуту."},
    "price_label": {"en": "Price", "ru": "Цена"},
    "fdv_label": {"en": "FDV", "ru": "FDV"},
    "liquidity_label": {"en": "Liquidity", "ru": "Ликвидность"},
    "created_label": {"en": "Created", "ru": "Создан"},
    "enter_amount": {"en": "Enter amount in <b>💎 GRAM</b> or tap:", "ru": "Введи сумму в <b>💎 GRAM</b> или нажми:"},

    "insufficient_balance": {"en": "❌ Insufficient. Balance: {bal} GRAM", "ru": "❌ Недостаточно средств. Баланс: {bal} GRAM"},
    "not_enough_fees": {"en": "❌ Not enough to cover fees. Need {amt} GRAM", "ru": "❌ Недостаточно для покрытия комиссий. Нужно {amt} GRAM"},
    "enter_number": {"en": "❌ Enter a number, e.g. <code>5</code>", "ru": "❌ Введи число, например <code>5</code>"},
    "confirm_buy": {"en": "✅ <b>Confirm Buy</b>", "ru": "✅ <b>Подтверди покупку</b>"},
    "confirm_sell": {"en": "✅ <b>Confirm Sell</b>", "ru": "✅ <b>Подтверди продажу</b>"},
    "confirm_swap_btn": {"en": "✅ Confirm Swap", "ru": "✅ Подтвердить"},
    "cancel_btn": {"en": "❌ Cancel", "ru": "❌ Отмена"},
    "cost_label": {"en": "Cost", "ru": "Стоимость"},
    "receive_label": {"en": "Receive", "ru": "Получишь"},
    "dex_fee_label": {"en": "DEX fee", "ru": "Комиссия DEX"},
    "network_fee_label": {"en": "Network", "ru": "Сеть"},
    "slippage_label": {"en": "Slippage", "ru": "Слиппедж"},
    "processing_swap": {"en": "⏳ <b>Processing swap...</b>\n\n🔄 Routing through DEX", "ru": "⏳ <b>Обработка обмена...</b>\n\n🔄 Маршрутизация через DEX"},
    "swap_filled": {"en": "✅ <b>Swap Filled</b>", "ru": "✅ <b>Обмен исполнен</b>"},
    "received_label": {"en": "Received", "ru": "Получено"},
    "spent_label": {"en": "Spent", "ru": "Потрачено"},
    "entry_label": {"en": "Entry", "ru": "Вход"},
    "exit_label": {"en": "Exit", "ru": "Выход"},

    "setbalance_usage": {"en": "Usage: <code>/setbalance 500</code>", "ru": "Использование: <code>/setbalance 500</code>"},
    "setbalance_range": {"en": "❌ Enter a number between 1 and 10,000,000.", "ru": "❌ Введи число от 1 до 10,000,000."},
    "balance_updated": {"en": "✅ Balance updated — <b>{amt} 💎 GRAM</b>", "ru": "✅ Баланс обновлён — <b>{amt} 💎 GRAM</b>"},

    "no_open_positions": {"en": "📭 <b>No open positions</b>\n\nDrop a CA to open a trade.", "ru": "📭 <b>Нет открытых позиций</b>\n\nОтправь контракт-адрес чтобы открыть сделку."},
    "sell_position_title": {"en": "🔴 <b>Sell Position</b>", "ru": "🔴 <b>Продажа позиции</b>"},
    "select_label": {"en": "Select:", "ru": "Выбери:"},
    "position_not_found": {"en": "❌ Position not found.", "ru": "❌ Позиция не найдена."},
    "sell_title": {"en": "🔴 <b>Sell {symbol}</b>", "ru": "🔴 <b>Продажа {symbol}</b>"},
    "held_label": {"en": "Held", "ru": "В наличии"},
    "pnl_label": {"en": "P&L", "ru": "P&L"},
    "enter_amount_or_tap": {"en": "Enter amount or tap:", "ru": "Введи сумму или нажми:"},
    "only_have": {"en": "❌ You only have {amt} {symbol}", "ru": "❌ У тебя есть только {amt} {symbol}"},
    "enter_number_plain": {"en": "❌ Enter a number", "ru": "❌ Введи число"},
    "receive_colon": {"en": "Receive", "ru": "Получишь"},
    "processing_sell": {"en": "⏳ <b>Processing sell...</b>", "ru": "⏳ <b>Обработка продажи...</b>"},
    "position_closed": {"en": "<b>Position Closed</b>", "ru": "<b>Позиция закрыта</b>"},
    "card_failed": {"en": "❌ Failed to generate card", "ru": "❌ Не удалось создать карточку"},

    "balance_title": {"en": "💎 <b>Balance</b>", "ru": "💎 <b>Баланс</b>"},
    "live_ton_price": {"en": "(live TON price)", "ru": "(текущая цена TON)"},
    "add_gram_btn": {"en": "➕ Add GRAM (max {max})", "ru": "➕ Добавить GRAM (макс {max})"},
    "set_balance_btn": {"en": "✏️ Set balance", "ru": "✏️ Изменить баланс"},
    "set_balance_title": {"en": "✏️ <b>Set Balance</b>", "ru": "✏️ <b>Изменить баланс</b>"},
    "current_label": {"en": "Current", "ru": "Текущий"},
    "enter_new_amount": {"en": "Enter new amount:", "ru": "Введи новую сумму:"},
    "setbalance_range2": {"en": "❌ Enter a number between 1 and 10,000,000", "ru": "❌ Введи число от 1 до 10,000,000"},
    "balance_set": {"en": "✅ Balance set — <b>{amt} 💎 GRAM</b>", "ru": "✅ Баланс установлен — <b>{amt} 💎 GRAM</b>"},
    "add_gram_title": {"en": "➕ <b>Add GRAM</b>", "ru": "➕ <b>Добавить GRAM</b>"},
    "max_label": {"en": "Max", "ru": "Макс"},
    "how_much": {"en": "How much?", "ru": "Сколько?"},
    "max_topup_error": {"en": "❌ Max {max} GRAM per top-up", "ru": "❌ Максимум {max} GRAM за раз"},
    "topup_range_error": {"en": "❌ Enter a number between 1 and {max}", "ru": "❌ Введи число от 1 до {max}"},
    "added_gram": {"en": "✅ Added <b>{amt} 💎 GRAM</b>", "ru": "✅ Добавлено <b>{amt} 💎 GRAM</b>"},
    "reset_confirm_title": {"en": "⚠️ <b>Reset Account?</b>\n\nAll positions and history will be wiped.\nBalance resets to <b>{bal} 💎 GRAM</b>.", "ru": "⚠️ <b>Сбросить аккаунт?</b>\n\nВсе позиции и история будут удалены.\nБаланс сбросится до <b>{bal} 💎 GRAM</b>."},
    "reset_btn": {"en": "✅ Reset", "ru": "✅ Сбросить"},
    "account_reset": {"en": "🔄 Account reset.\n💎 Balance: <b>{bal} GRAM</b>", "ru": "🔄 Аккаунт сброшен.\n💎 Баланс: <b>{bal} GRAM</b>"},
    "drop_ca_or_menu": {"en": "🔍 Drop a TON contract address to trade.\n\nOr use the menu 👇", "ru": "🔍 Отправь контракт-адрес TON чтобы торговать.\n\nИли используй меню 👇"},

    "refreshing_price": {"en": "⏳ Refreshing price...", "ru": "⏳ Обновляю цену..."},
    "price_unavailable_resend": {"en": "❌ Price unavailable — resend CA", "ru": "❌ Цена недоступна — отправь контракт-адрес заново"},
    "insufficient_balance_alert": {"en": "❌ Insufficient balance", "ru": "❌ Недостаточно баланса"},
    "no_position_alert": {"en": "❌ No position", "ru": "❌ Нет позиции"},
    "confirm_buy_pct": {"en": "✅ <b>Confirm Buy — {pct}%</b>", "ru": "✅ <b>Подтверди покупку — {pct}%</b>"},
    "confirm_sell_pct": {"en": "✅ <b>Confirm Sell — {pct}%</b>", "ru": "✅ <b>Подтверди продажу — {pct}%</b>"},

    "btn_share_pnl": {"en": "📤 Share PnL", "ru": "📤 Поделиться PnL"},
    "btn_chart": {"en": "📊 Chart", "ru": "📊 График"},
    "your_position": {"en": "Your position", "ru": "Твоя позиция"},
    "amount_label": {"en": "Amount", "ru": "Количество"},
    "now_label": {"en": "Now", "ru": "Сейчас"},
    "pnl_gram_label": {"en": "P&L GRAM", "ru": "P&L GRAM"},
    "value_label": {"en": "Value", "ru": "Стоимость"},

    "position_not_found_alert": {"en": "❌ Position not found", "ru": "❌ Позиция не найдена"},
    "generating": {"en": "⏳ Generating...", "ru": "⏳ Создаю..."},
    "trade_result_title": {"en": "🔥 <b>Trade Result</b>", "ru": "🔥 <b>Результат сделки</b>"},
    "bought_sold_line": {"en": "<b>Bought: {start} 💎  ·  Sold: {end} 💎</b>", "ru": "<b>Куплено: {start} 💎  ·  Продано: {end} 💎</b>"},
}


def t(key, lang="en", **kwargs):
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    text = entry.get(lang, entry.get("en", key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text

TON_CA_PATTERN = re.compile(r'\b(EQ|UQ|kQ|0:)[A-Za-z0-9_\-]{46,64}\b')
DEX_FEE = 0.003
NETWORK_FEE_GRAM = 0.05

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── TON PRICE ────────────────────────────────────────────────────────────────

async def get_ton_price() -> float:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=CG_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                return float(data["the-open-network"]["usd"])
    except Exception as e:
        logger.error(f"get_ton_price error: {e}")
        return 0.0

# ─── DB ───────────────────────────────────────────────────────────────────────

def get_conn():
    if USE_PG and DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL), True
    else:
        import sqlite3 as sq
        return sq.connect("trading.db"), False

def ph(is_pg): return "%s" if is_pg else "?"

def init_db():
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    serial = "SERIAL" if is_pg else "INTEGER"
    auto = "" if is_pg else "AUTOINCREMENT"

    def safe_exec(sql):
        try:
            c.execute(sql)
            conn.commit()
        except Exception as e:
            logger.warning(f"init_db step skipped: {e}")
            if is_pg:
                conn.rollback()

    safe_exec(f"""CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY, username TEXT,
        balance REAL DEFAULT 10.0, created_at TEXT)""")
    safe_exec(f"""CREATE TABLE IF NOT EXISTS portfolio (
        id {serial} PRIMARY KEY {auto}, user_id BIGINT,
        symbol TEXT, amount REAL, avg_buy_price REAL,
        ca TEXT DEFAULT '', avg_mcap REAL DEFAULT 0)""")
    safe_exec(f"""CREATE TABLE IF NOT EXISTS trades (
        id {serial} PRIMARY KEY {auto}, user_id BIGINT,
        symbol TEXT, action TEXT, amount REAL, price REAL,
        price_buy REAL DEFAULT 0, total REAL, pnl_pct REAL DEFAULT 0,
        mcap_buy REAL DEFAULT 0, mcap_sell REAL DEFAULT 0, timestamp TEXT)""")
    safe_exec(f"""CREATE TABLE IF NOT EXISTS calls (
        id {serial} PRIMARY KEY {auto},
        chat_id BIGINT, user_id BIGINT, username TEXT,
        ca TEXT, symbol TEXT, price_at_call REAL, mcap_at_call REAL,
        timestamp TEXT)""")
    safe_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_calls_unique ON calls(chat_id, ca)")
    for col in [
        "ALTER TABLE portfolio ADD COLUMN avg_mcap REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN mcap_buy REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN mcap_sell REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN price_buy REAL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN pnl_pct REAL DEFAULT 0",
        "ALTER TABLE portfolio ADD COLUMN ca TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'en'",
    ]:
        safe_exec(col)
    safe_exec("CREATE INDEX IF NOT EXISTS idx_portfolio_ca ON portfolio(user_id, ca)")
    conn.close()

def get_user(user_id, username=""):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"SELECT * FROM users WHERE user_id={p}", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute(f"INSERT INTO users (user_id,username,balance,created_at) VALUES ({p},{p},{p},{p})",
                  (user_id, username, STARTING_BALANCE, datetime.now().isoformat()))
        conn.commit()
        c.execute(f"SELECT * FROM users WHERE user_id={p}", (user_id,))
        user = c.fetchone()
    conn.close(); return user

def get_portfolio(user_id):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"SELECT symbol,amount,avg_buy_price,ca,avg_mcap FROM portfolio WHERE user_id={p} AND amount>0.000001", (user_id,))
    rows = c.fetchall(); conn.close(); return rows

def get_trades(user_id, limit=15):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"""SELECT symbol,action,amount,price,price_buy,total,pnl_pct,mcap_buy,mcap_sell,timestamp
                 FROM trades WHERE user_id={p} ORDER BY id DESC LIMIT {p}""", (user_id, limit))
    rows = c.fetchall(); conn.close(); return rows

def get_trade_days(user_id, limit_days=14):
    """Возвращает список (дата, количество сделок) за последние дни, новые сверху"""
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    if is_pg:
        c.execute(f"""SELECT SUBSTRING(timestamp,1,5) as day, COUNT(*) as cnt
                     FROM trades WHERE user_id={p}
                     GROUP BY day ORDER BY day DESC LIMIT {p}""", (user_id, limit_days))
    else:
        c.execute(f"""SELECT SUBSTR(timestamp,1,5) as day, COUNT(*) as cnt
                     FROM trades WHERE user_id={p}
                     GROUP BY day ORDER BY day DESC LIMIT {p}""", (user_id, limit_days))
    rows = c.fetchall(); conn.close(); return rows

def get_trades_for_day(user_id, day_str, limit=50):
    """day_str в формате DD.MM (как хранится в timestamp). Возвращает сделки этого дня."""
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    like_pattern = f"{day_str}%"
    c.execute(f"""SELECT symbol,action,amount,price,price_buy,total,pnl_pct,mcap_buy,mcap_sell,timestamp
                 FROM trades WHERE user_id={p} AND timestamp LIKE {p}
                 ORDER BY id DESC LIMIT {p}""", (user_id, like_pattern, limit))
    rows = c.fetchall(); conn.close(); return rows


def get_bot_stats():
    """Общая статистика бота: пользователи, активность, трейды."""
    conn, is_pg = get_conn(); c = conn.cursor()
    today_iso = datetime.now().strftime("%Y-%m-%d")
    week_ago_iso = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM users WHERE created_at LIKE %s" if is_pg else
              "SELECT COUNT(*) FROM users WHERE created_at LIKE ?", (f"{today_iso}%",))
    new_today = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM users WHERE created_at >= %s" if is_pg else
              "SELECT COUNT(*) FROM users WHERE created_at >= ?", (week_ago_iso,))
    new_week = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT user_id) FROM trades")
    active_traders = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM trades")
    total_trades = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM trades WHERE action='BUY'")
    total_buys = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM trades WHERE action='SELL'")
    total_sells = c.fetchone()[0]

    conn.close()
    return {
        "total_users": total_users,
        "new_today": new_today,
        "new_week": new_week,
        "active_traders": active_traders,
        "total_trades": total_trades,
        "total_buys": total_buys,
        "total_sells": total_sells,
    }


def get_performance_stats(user_id):
    """Считает статистику трейдера: всего сделок, win rate, объём, лучшая сделка, среднее время удержания позиции."""
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"""SELECT id,symbol,action,amount,total,pnl_pct,timestamp
                 FROM trades WHERE user_id={p} ORDER BY id ASC""", (user_id,))
    rows = c.fetchall(); conn.close()

    if not rows:
        return None

    sell_rows = [r for r in rows if r[2] == "SELL"]
    total_trades = len(rows)
    total_sells = len(sell_rows)
    wins = sum(1 for r in sell_rows if r[5] and r[5] > 0)
    win_rate = (wins / total_sells * 100) if total_sells > 0 else 0
    volume = sum(r[4] or 0 for r in rows)
    best_trade = max((r[5] for r in sell_rows if r[5] is not None), default=0)

    # Считаем avg hold time: сопоставляем каждый BUY с следующим SELL того же symbol
    open_buys = {}  # symbol -> timestamp строки BUY
    holds_minutes = []
    for (rid, symbol, action, amount, total, pnl_pct, ts) in rows:
        try:
            day, month = int(ts[0:2]), int(ts[3:5])
            hh, mm = int(ts[6:8]), int(ts[9:11])
            minutes_abs = (month * 31 + day) * 1440 + hh * 60 + mm
        except:
            continue
        if action == "BUY":
            open_buys[symbol] = minutes_abs
        elif action == "SELL" and symbol in open_buys:
            diff = minutes_abs - open_buys.pop(symbol)
            if diff >= 0:
                holds_minutes.append(diff)

    avg_hold_min = sum(holds_minutes) / len(holds_minutes) if holds_minutes else 0

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "volume": volume,
        "best_trade": best_trade,
        "avg_hold_min": avg_hold_min,
    }

def get_all_users_pnl():
    conn, is_pg = get_conn(); c = conn.cursor()
    c.execute("""SELECT u.user_id, u.username, u.balance,
               COALESCE(SUM(CASE WHEN t.action='SELL' THEN t.total - t.amount * t.price_buy ELSE 0 END), 0) as realized_pnl
        FROM users u LEFT JOIN trades t ON u.user_id = t.user_id
        GROUP BY u.user_id, u.username, u.balance
        ORDER BY realized_pnl DESC LIMIT 20""")
    rows = c.fetchall(); conn.close(); return rows

def update_balance(user_id, delta):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"UPDATE users SET balance=balance+{p} WHERE user_id={p}", (delta, user_id))
    conn.commit(); conn.close()

def set_balance(user_id, amount):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"UPDATE users SET balance={p} WHERE user_id={p}", (amount, user_id))
    conn.commit(); conn.close()

def get_lang(user_id):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    try:
        c.execute(f"SELECT lang FROM users WHERE user_id={p}", (user_id,))
        row = c.fetchone()
        conn.close()
        return row[0] if row and row[0] else "en"
    except:
        conn.close()
        return "en"

def set_lang(user_id, lang):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"UPDATE users SET lang={p} WHERE user_id={p}", (lang, user_id))
    conn.commit(); conn.close()

def update_portfolio(user_id, symbol, amount_delta, price, ca="", mcap=0):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    if ca:
        c.execute(f"SELECT amount,avg_buy_price,avg_mcap FROM portfolio WHERE user_id={p} AND ca={p}", (user_id, ca))
    else:
        c.execute(f"SELECT amount,avg_buy_price,avg_mcap FROM portfolio WHERE user_id={p} AND symbol={p}", (user_id, symbol))
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
            if ca: c.execute(f"DELETE FROM portfolio WHERE user_id={p} AND ca={p}", (user_id, ca))
            else: c.execute(f"DELETE FROM portfolio WHERE user_id={p} AND symbol={p}", (user_id, symbol))
        else:
            if ca: c.execute(f"UPDATE portfolio SET amount={p},avg_buy_price={p},avg_mcap={p},symbol={p} WHERE user_id={p} AND ca={p}",
                          (new_amt, new_avg, new_mcap, symbol, user_id, ca))
            else: c.execute(f"UPDATE portfolio SET amount={p},avg_buy_price={p},avg_mcap={p} WHERE user_id={p} AND symbol={p}",
                          (new_amt, new_avg, new_mcap, user_id, symbol))
    else:
        if amount_delta > 0:
            try: c.execute(f"INSERT INTO portfolio (user_id,symbol,amount,avg_buy_price,ca,avg_mcap) VALUES ({p},{p},{p},{p},{p},{p})",
                          (user_id, symbol, amount_delta, price, ca, mcap))
            except Exception as e: logger.error(f"Portfolio insert: {e}")
    conn.commit(); conn.close()

def get_position(user_id, symbol):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"SELECT amount,avg_buy_price,ca,avg_mcap FROM portfolio WHERE user_id={p} AND symbol={p}", (user_id, symbol))
    row = c.fetchone(); conn.close()
    return row if row else (0, 0, "", 0)

def save_trade(user_id, symbol, action, amount, price, price_buy, total, pnl_pct, mcap_buy=0, mcap_sell=0):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"""INSERT INTO trades (user_id,symbol,action,amount,price,price_buy,total,pnl_pct,mcap_buy,mcap_sell,timestamp)
                 VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})""",
              (user_id, symbol, action, amount, price, price_buy, total, pnl_pct, mcap_buy, mcap_sell,
               datetime.now().strftime("%d.%m %H:%M")))
    conn.commit(); conn.close()

def reset_user(user_id):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"UPDATE users SET balance={p} WHERE user_id={p}", (STARTING_BALANCE, user_id))
    c.execute(f"DELETE FROM portfolio WHERE user_id={p}", (user_id,))
    c.execute(f"DELETE FROM trades WHERE user_id={p}", (user_id,))
    conn.commit(); conn.close()

def record_call(chat_id, user_id, username, ca, symbol, price, mcap):
    """Записываем первый калл ЦА в чате, возвращаем запись"""
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    try:
        if is_pg:
            c.execute(f"""INSERT INTO calls (chat_id,user_id,username,ca,symbol,price_at_call,mcap_at_call,timestamp)
                         VALUES ({p},{p},{p},{p},{p},{p},{p},{p}) ON CONFLICT DO NOTHING""",
                      (chat_id, user_id, username, ca, symbol, price, mcap, datetime.now().isoformat()))
        else:
            c.execute(f"""INSERT OR IGNORE INTO calls (chat_id,user_id,username,ca,symbol,price_at_call,mcap_at_call,timestamp)
                         VALUES ({p},{p},{p},{p},{p},{p},{p},{p})""",
                      (chat_id, user_id, username, ca, symbol, price, mcap, datetime.now().isoformat()))
        conn.commit()
        c.execute(f"SELECT user_id,username,price_at_call,mcap_at_call,timestamp FROM calls WHERE chat_id={p} AND ca={p}",
                  (chat_id, ca))
        row = c.fetchone(); conn.close(); return row
    except Exception as e:
        logger.error(f"record_call: {e}"); conn.close(); return None

def get_call(chat_id, ca):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"SELECT user_id,username,price_at_call,mcap_at_call,timestamp FROM calls WHERE chat_id={p} AND ca={p}",
              (chat_id, ca))
    row = c.fetchone(); conn.close(); return row

def get_top_callers(chat_id, limit=10):
    conn, is_pg = get_conn(); c = conn.cursor(); p = ph(is_pg)
    c.execute(f"""SELECT username,ca,symbol,price_at_call,mcap_at_call,timestamp
                 FROM calls WHERE chat_id={p} ORDER BY id ASC LIMIT {p}""", (chat_id, limit))
    rows = c.fetchall(); conn.close(); return rows


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
                headers=CG_HEADERS
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
                    headers=CG_HEADERS
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        attrs = data.get("data", {}).get("attributes", {})
                        price = float(attrs.get("price_usd") or 0)
                        if price > 0:
                            symbol = attrs.get("symbol") or ca[:6].upper()
                            # FDV берём из токена — это самое точное значение
                            fdv = float(attrs.get("fdv_usd") or 0)
                            liq = float(attrs.get("total_reserve_in_usd") or 0)
                            image = attrs.get("image_url") or ""
                            # Из пулов берём только created_at и ликвидность если нет
                            try:
                                async with s.get(
                                    f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/pools?page=1",
                                    timeout=aiohttp.ClientTimeout(total=8),
                                    headers=CG_HEADERS
                                ) as rp:
                                    if rp.status == 200:
                                        pd = await rp.json()
                                        pools = pd.get("data", [])
                                        if pools:
                                            pools.sort(key=lambda x: float(x.get("attributes", {}).get("reserve_in_usd") or 0), reverse=True)
                                            pa = pools[0].get("attributes", {})
                                            # FDV из пула только если токен не вернул
                                            if fdv <= 0:
                                                fdv = float(pa.get("fdv_usd") or 0)
                                            if liq <= 0:
                                                liq = float(pa.get("reserve_in_usd") or 0)
                                            created_at = pa.get("pool_created_at", "")[:10]
                            except Exception as e:
                                logger.warning(f"Pools extra: {e}")
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
                headers=CG_HEADERS
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

# ─── HISTORY CACHE ───────────────────────────────────────────────────────────
_history_cache = {}  # {ca_timeframe: (timestamp, history)}
HISTORY_CACHE_TTL = 300  # 5 минут

async def get_token_history(ca, timeframe="7D"):
    # Проверяем кэш
    cache_key = f"{ca}_{timeframe}"
    now = asyncio.get_event_loop().time()
    if cache_key in _history_cache:
        ts, cached = _history_cache[cache_key]
        if now - ts < HISTORY_CACHE_TTL:
            logger.info(f"History from cache: {len(cached)} candles")
            return cached

    # Правильный формат: /ohlcv/{timeframe}?aggregate={n}
    gt_tf = {
        "1H":  ("minute", "5",  12),
        "6H":  ("hour",   "1",  6),
        "24H": ("hour",   "1",  24),
        "7D":  ("hour",   "4",  42),
        "30D": ("day",    "1",  30),
        "365D": ("day",   "1",  365),
    }
    tf, aggregate, lim = gt_tf.get(timeframe, ("hour", "4", 42))

    # Шаг 1 — находим адрес пула
    pool_address = None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}/pools?page=1",
                timeout=aiohttp.ClientTimeout(total=10),
                headers=CG_HEADERS
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

    # Шаг 2 — OHLCV по адресу пула
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                url = (f"https://api.geckoterminal.com/api/v2/networks/ton/pools/"
                       f"{pool_address}/ohlcv/{tf}?aggregate={aggregate}&limit={lim}")
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                 headers=CG_HEADERS) as r:
                    logger.info(f"OHLCV status: {r.status} attempt {attempt+1}")
                    if r.status == 200:
                        raw = await r.json()
                        candles = raw.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                        logger.info(f"OHLCV candles raw: {len(candles)}")
                        if len(candles) >= 2:
                            # [timestamp_ms, open, high, low, close, volume]
                            history = [[c[0] * 1000, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]) if len(c) > 5 else 0] for c in candles]
                            logger.info(f"OHLCV OK: {len(history)} candles")
                            _history_cache[cache_key] = (now, history)
                            return history
                        else:
                            # Попробуем другой таймфрейм если нет данных
                            if tf == "hour" and aggregate == "4":
                                url2 = (f"https://api.geckoterminal.com/api/v2/networks/ton/pools/"
                                        f"{pool_address}/ohlcv/hour?aggregate=1&limit=24")
                                async with s.get(url2, timeout=aiohttp.ClientTimeout(total=15),
                                                 headers=CG_HEADERS) as r2:
                                    if r2.status == 200:
                                        raw2 = await r2.json()
                                        candles2 = raw2.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                                        if len(candles2) >= 2:
                                            history = [[c[0] * 1000, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]) if len(c) > 5 else 0] for c in candles2]
                                            logger.info(f"OHLCV fallback 1h: {len(history)} candles")
                                            return history
                    elif r.status == 429:
                        wait = 3 * (attempt + 1)
                        logger.warning(f"OHLCV rate limited, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    else:
                        body = await r.text()
                        logger.warning(f"OHLCV unexpected status {r.status}: {body[:200]}")
        except Exception as e:
            logger.warning(f"OHLCV attempt {attempt+1}: {e}")
            if attempt < 2:
                await asyncio.sleep(2)

    return []

async def edit_or_answer(message, text, reply_markup=None, parse_mode="HTML", disable_web_page_preview=True):
    """Универсальная функция — редактирует текст или caption"""
    # Обрезаем если слишком длинный
    if len(text) > 4096:
        text = text[:4090] + "..."
    try:
        if message.photo or message.document or message.video:
            # Для caption лимит 1024
            cap = text if len(text) <= 1024 else text[:1020] + "..."
            await message.edit_caption(caption=cap, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode,
                                    disable_web_page_preview=True)
    except Exception as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return  # Не ошибка — просто данные не изменились
        logger.warning(f"edit_or_answer: {e}")
        try:
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode,
                                 disable_web_page_preview=True)
        except Exception as e2:
            logger.error(f"edit_or_answer fallback failed: {e2}")

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

def format_age(date_str: str, lang: str = "en") -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        days = (datetime.utcnow() - dt).days
        if lang == "ru":
            if days < 1:
                return "сегодня"
            elif days == 1:
                return "1 день назад"
            elif days < 7:
                return f"{days} дн. назад"
            elif days < 30:
                weeks = days // 7
                return f"{weeks} нед. назад"
            elif days < 365:
                months = days // 30
                return f"{months} мес. назад"
            else:
                years = days // 365
                return f"{years} г. назад"
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
                   is_fdv=False, current_price=None, timeframe="7D", liq=None):
    if not history or len(history) < 2:
        return None
    try:
        # history: [timestamp_ms, open, high, low, close, volume]
        is_ohlc = len(history[0]) >= 5
        timestamps = [datetime.fromtimestamp(p[0]/1000) for p in history]
        if is_ohlc:
            opens  = [float(p[1]) for p in history]
            highs  = [float(p[2]) for p in history]
            lows   = [float(p[3]) for p in history]
            closes = [float(p[4]) for p in history]
            volumes = [float(p[5]) if len(p) > 5 else 0 for p in history]
        else:
            closes = [float(p[1]) for p in history]
            opens, highs, lows, volumes = closes[:], closes[:], closes[:], [0]*len(closes)

        if not any(p > 0 for p in closes): return None

        is_up = closes[-1] >= closes[0]
        accent = "#00e676" if is_up else "#ff1744"
        bg = "#0e1015"
        card_bg = "#15171f"

        fig = plt.figure(figsize=(10, 6.2))
        fig.patch.set_facecolor(bg)

        # Grid layout: header row (text only) + stats row (text only) + chart
        gs = fig.add_gridspec(3, 1, height_ratios=[1.1, 0.6, 4.3], hspace=0.05)
        ax_head = fig.add_subplot(gs[0])
        ax_head.set_facecolor(card_bg)
        ax_head.axis("off")
        ax_head.patch.set_facecolor(card_bg)
        ax_head.patch.set_visible(True)

        ax_stats = fig.add_subplot(gs[1])
        ax_stats.set_facecolor(card_bg)
        ax_stats.axis("off")
        ax_stats.patch.set_facecolor(card_bg)
        ax_stats.patch.set_visible(True)

        ax = fig.add_subplot(gs[2]); ax.set_facecolor(card_bg)

        # ── HEADER ──────────────────────────────────────────────────────
        ax_head.set_xlim(0, 10); ax_head.set_ylim(0, 1)
        if image_arr is not None:
            try:
                im = OffsetImage(image_arr, zoom=0.55)
                ab = AnnotationBbox(im, (0.45, 0.5), frameon=False)
                ax_head.add_artist(ab)
                name_x = 1.0
            except:
                name_x = 0.2
        else:
            name_x = 0.2
        ax_head.text(name_x, 0.62, symbol, color="white", fontsize=19, fontweight="bold", va="center")
        ax_head.text(name_x, 0.22, f"{symbol} / USD", color="#888888", fontsize=10, va="center")

        change = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0
        arrow = "▲" if is_up else "▼"
        ax_head.text(9.8, 0.42, f"{arrow} {change:+.2f}%", color=accent, fontsize=20,
                     fontweight="bold", ha="right", va="center")

        # ── STATS ROW ───────────────────────────────────────────────────
        ax_stats.set_xlim(0, 10); ax_stats.set_ylim(0, 1)
        mcap_label = t_label = "FDV" if is_fdv else "MCap"
        vol_sum = sum(volumes) if any(volumes) else 0
        stats = []
        if mcap and mcap > 0:
            stats.append((t_label, format_mcap_val(mcap)))
        if liq and liq > 0:
            stats.append(("LIQ", format_mcap_val(liq)))
        if vol_sum > 0:
            stats.append(("VOL", format_mcap_val(vol_sum)))
        stats.append((timeframe, f"{change:+.1f}%"))

        n = len(stats)
        for i, (label, val) in enumerate(stats):
            cx = (i + 0.5) * (10 / n)
            ax_stats.text(cx, 0.72, label, color="#777777", fontsize=8.5, ha="center", va="center")
            val_color = accent if label == timeframe else "white"
            ax_stats.text(cx, 0.28, val, color=val_color, fontsize=11.5, fontweight="bold", ha="center", va="center")

        # ── CANDLESTICK CHART ───────────────────────────────────────────
        ax.set_facecolor(card_bg)
        n_candles = len(timestamps)
        width = (timestamps[-1] - timestamps[0]).total_seconds() / n_candles * 0.6 / 86400 if n_candles > 1 else 0.02

        for i in range(n_candles):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            up = c >= o
            col = "#00e676" if up else "#ff1744"
            x = mdates.date2num(timestamps[i])
            ax.plot([x, x], [l, h], color=col, linewidth=1, zorder=2)
            body_low, body_high = min(o, c), max(o, c)
            if body_high == body_low:
                body_high = body_low * 1.0005
            rect = mpatches.Rectangle((x - width/2, body_low), width, body_high - body_low,
                                      facecolor=col, edgecolor=col, linewidth=0, zorder=3)
            ax.add_patch(rect)

        if entry_price and entry_price > 0:
            ax.axhline(y=entry_price, color="#ffd740", linewidth=1.2, linestyle="--", alpha=0.7, zorder=4)
            ax.text(timestamps[0], entry_price, "  Entry", color="#ffd740", fontsize=8, va="bottom")

        # Текущая цена — пунктир + бейдж справа
        if current_price and current_price > 0:
            ax.axhline(y=current_price, color="#555555", linewidth=0.8, linestyle=":", alpha=0.5, zorder=1)

        if timeframe in ("1H", "6H", "24H"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.yaxis.set_label_position("right")
        ax.yaxis.tick_right()
        plt.setp(ax.get_xticklabels(), rotation=0, color="#666688", fontsize=8.5, fontweight="bold")
        plt.setp(ax.get_yticklabels(), color="#888888", fontsize=8.5)
        for sp in ax.spines.values(): sp.set_visible(False)
        ax.tick_params(colors="#666688", length=0)
        ax.grid(axis='y', color="#21242e", linewidth=0.8, zorder=0)
        ax.margins(x=0.02)

        # Водяной знак
        ax.text(0.012, 0.02, "OCTOtrade", transform=ax.transAxes,
                color="#444444", fontsize=9, fontweight="bold", alpha=0.8, va="bottom", ha="left")

        plt.tight_layout(pad=0.6)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor=bg)
        buf.seek(0); plt.close()
        return buf
    except Exception as e:
        logger.warning(f"Chart error: {e}")

        plt.close('all'); return None

# ─── PNL CARD ─────────────────────────────────────────────────────────────────

def _get_random_bg(is_win: bool):
    """Берёт рандомную фотку фона из папки positive/ или negative/"""
    base = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.join(base, "pnl_backgrounds", "positive" if is_win else "negative")
    logger.info(f"PnL bg folder: {folder}")
    try:
        files = [f for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        logger.info(f"PnL bg files found: {files}")
        if files:
            path = os.path.join(folder, random.choice(files))
            img = Image.open(path).convert("RGBA").resize((1040, 585))
            return np.array(img)
    except Exception as e:
        logger.warning(f"PnL bg load: {e}")
    return None


def generate_pnl_card(symbol, pnl_pct, pnl_gram, mcap_buy, mcap_sell,
                      entry_price, exit_price, image_arr=None, ton_spent=0, ton_received=0):
    try:
        is_win = pnl_pct >= 0
        accent = "#00e676" if is_win else "#ff1744"
        bg_fallback = "#0d1117"

        # Размер карточки 16:9
        fig, ax = plt.subplots(figsize=(10, 5.625))
        ax.set_xlim(0, 16); ax.set_ylim(0, 9); ax.axis("off")

        # Фоновое изображение
        bg_arr = _get_random_bg(is_win)
        if bg_arr is not None:
            bg_resized = np.array(Image.fromarray(bg_arr[..., :3] if bg_arr.shape[2] == 4 else bg_arr).resize((1280, 720)))
            ax.imshow(bg_resized, extent=[0, 16, 0, 9], aspect="auto", zorder=0)
            fig.patch.set_facecolor(bg_fallback)
            ax.set_facecolor("none")
            # Тёмный градиент слева для читаемости текста
            grad = mpatches.FancyBboxPatch((0, 0), 8.5, 9,
                boxstyle="square,pad=0", facecolor="black", alpha=0.6, zorder=1)
            ax.add_patch(grad)
        else:
            fig.patch.set_facecolor(bg_fallback)
            ax.set_facecolor(bg_fallback)

        # ── ЛЕВАЯ ЧАСТЬ ──────────────────────────────────────────────────

        # Иконка токена + название вверху слева
        if image_arr is not None:
            try:
                icon = Image.fromarray(image_arr).resize((72, 72))
                im = OffsetImage(np.array(icon), zoom=1.0)
                ab = AnnotationBbox(im, (0.85, 7.9), frameon=False, zorder=5)
                ax.add_artist(ab)
            except: pass
            ax.text(2.0, 8.25, symbol, color="white", fontsize=16,
                    fontweight="bold", va="center", zorder=5)
            ax.text(2.0, 7.65, symbol, color="#888888", fontsize=10,
                    va="center", zorder=5)
        else:
            ax.text(0.5, 8.1, symbol, color="white", fontsize=18,
                    fontweight="bold", va="center", zorder=5)

        # Большой процент
        arrow = "▲" if is_win else "▼"
        ax.text(0.5, 6.0, f"{arrow} {pnl_pct:+.1f}%", color=accent,
                fontsize=38, fontweight="bold", va="center", zorder=5,
                fontfamily="monospace")

        # Bought / Sold
        bought_str = f"{format_gram(ton_spent)}" if ton_spent > 0 else "—"
        sold_str   = f"{format_gram(ton_received)}" if ton_received > 0 else "—"

        ax.text(0.5, 4.5, "Bought", color="#888888", fontsize=11, va="center", zorder=5)
        ax.text(3.2, 4.5, f"{bought_str} GRAM", color="white", fontsize=13,
                fontweight="bold", va="center", zorder=5)

        ax.text(0.5, 3.6, "Sold", color="#888888", fontsize=11, va="center", zorder=5)
        ax.text(3.2, 3.6, f"{sold_str} GRAM", color=accent, fontsize=13,
                fontweight="bold", va="center", zorder=5)

        # Ватермарк внизу
        ax.text(0.5, 0.4, "OCTOtrade  |  Simulator", color="#555555",
                fontsize=8, va="center", zorder=5)

        plt.tight_layout(pad=0)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor=bg_fallback)
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

def main_keyboard(lang="en"):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(t("btn_portfolio", lang), callback_data="portfolio"),
        InlineKeyboardButton(t("btn_history", lang), callback_data="history"),
        InlineKeyboardButton(t("btn_performance", lang), callback_data="performance"),
        InlineKeyboardButton(t("btn_sell", lang), callback_data="sell"),
        InlineKeyboardButton(t("btn_balance", lang), callback_data="balance_menu"),
        InlineKeyboardButton(t("btn_reset", lang), callback_data="reset_confirm"),
    )
    kb.add(InlineKeyboardButton(t("btn_language", lang), callback_data="lang_menu"))
    return kb

def amount_keyboard(action, safe_sym, lang="en"):
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(
        InlineKeyboardButton("25%", callback_data=f"q_{action}_{safe_sym}_25"),
        InlineKeyboardButton("50%", callback_data=f"q_{action}_{safe_sym}_50"),
        InlineKeyboardButton("75%", callback_data=f"q_{action}_{safe_sym}_75"),
        InlineKeyboardButton("🔥 All In", callback_data=f"q_{action}_{safe_sym}_100"),
    )
    kb.add(InlineKeyboardButton(t("btn_back", lang), callback_data="back_main"))
    return kb

def token_keyboard(safe_sym, ca, lang="en"):
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
           InlineKeyboardButton(t("btn_refresh", lang), callback_data=f"refresh_{ca}"))
    return kb

# Отслеживаем "одноразовые" графики (после свапа), которые нужно убрать при следующем действии юзера
_pending_chart_delete = {}  # user_id -> message_id

async def _clear_pending_chart(user_id):
    """Удаляет ранее отправленный после-свап график, если юзер сделал любое следующее действие."""
    msg_id = _pending_chart_delete.pop(user_id, None)
    if msg_id:
        try:
            await bot.delete_message(user_id, msg_id)
        except Exception:
            pass


def back_keyboard(target="back_main", lang="en"):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t("btn_back", lang), callback_data=target))
    return kb

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

@dp.message_handler(commands=["start"], state="*")
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    # Deep link с ЦА — сразу открываем токен
    args = message.get_args()
    if args and TON_CA_PATTERN.match(args.strip()):
        msg = await message.answer("🔍 Scanning...")
        await send_token_info(message.chat.id, args.strip(), state, edit_msg=msg)
        return

    user = get_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    lang = get_lang(message.from_user.id)
    ton_price = await get_ton_price()
    kb = main_keyboard(lang)
    await message.answer(
        f"{t('welcome_back', lang, name=message.from_user.first_name)}\n\n"
        f"{t('subtitle', lang)}\n\n"
        f"{t('balance_line', lang, bal=format_gram(user[2]), usd=f'{user[2]*ton_price:,.2f}')}\n"
        f"{t('ton_price_line', lang, price=f'{ton_price:.4f}')}\n\n"
        f"{t('ready_to_trade', lang)}\n"
        f"{t('send_ca', lang)}\n\n"
        f"{t('setbalance_hint', lang)}\n\n"
        f"<a href='{CHANNEL_URL}'>{t('our_channel', lang)}</a>",
        reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True
    )

@dp.message_handler(commands=["zx91kqlm"])
async def cmd_admin_stats(message: types.Message):
    # Скрытая команда — статистика только для владельца бота
    if message.from_user.id != ADMIN_ID:
        return  # молчим, ничего не отвечаем посторонним
    stats = get_bot_stats()
    text = (
        f"📊 <b>Bot Stats</b>\n\n"
        f"👥 Total users:  <b>{stats['total_users']}</b>\n"
        f"🆕 New today:  <b>{stats['new_today']}</b>\n"
        f"🆕 New (7d):  <b>{stats['new_week']}</b>\n"
        f"📈 Active traders:  <b>{stats['active_traders']}</b>\n\n"
        f"🔁 Total trades:  <b>{stats['total_trades']}</b>\n"
        f"🟢 Buys:  <b>{stats['total_buys']}</b>\n"
        f"🔴 Sells:  <b>{stats['total_sells']}</b>"
    )
    await message.answer(text, parse_mode="HTML")


@dp.message_handler(commands=["setbalance"])
async def cmd_setbalance(message: types.Message):
    lang = get_lang(message.from_user.id)
    args = message.text.split()
    if len(args) < 2:
        await message.answer(t("setbalance_usage", lang), parse_mode="HTML"); return
    try:
        amount = float(args[1].replace(",", ""))
        if amount < 1 or amount > 10_000_000: raise ValueError
    except:
        await message.answer(t("setbalance_range", lang)); return
    set_balance(message.from_user.id, amount)
    await message.answer(t("balance_updated", lang, amt=format_gram(amount)),
                         reply_markup=main_keyboard(lang), parse_mode="HTML")

@dp.callback_query_handler(text="back_main", state="*")
async def back_main(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await _clear_pending_chart(callback.from_user.id)
    lang = get_lang(callback.from_user.id)
    user = get_user(callback.from_user.id)
    ton_price = await get_ton_price()
    text = (
        f"{t('main_menu_title', lang)}\n\n"
        f"💎 <b>{format_gram(user[2])} GRAM</b>  <i>(≈ ${user[2]*ton_price:,.2f})</i>\n\n"
        f"{t('drop_ca', lang)}"
    )
    # Если сообщение с фото — удаляем и отправляем чистый текст
    if callback.message.photo or callback.message.document:
        try:
            await callback.message.delete()
        except: pass
        await bot.send_message(callback.from_user.id, text,
                               reply_markup=main_keyboard(lang), parse_mode="HTML")
    else:
        try:
            await callback.message.edit_text(text, reply_markup=main_keyboard(lang),
                                             parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            if "not modified" not in str(e).lower():
                await bot.send_message(callback.from_user.id, text,
                                       reply_markup=main_keyboard(lang), parse_mode="HTML")

# ─── TOKEN INFO ───────────────────────────────────────────────────────────────

async def send_token_info(chat_id, ca, state: FSMContext, edit_msg=None, timeframe="7D"):
    lang = get_lang(chat_id)
    price, symbol, mcap, is_fdv, liq, image_url, ath, created_at = await fetch_token_data(ca)

    if price == 0:
        text = t("token_not_found", lang)
        if edit_msg:
            await edit_msg.edit_text(text, reply_markup=back_keyboard(lang=lang), parse_mode="HTML")
        else:
            await bot.send_message(chat_id, text, reply_markup=back_keyboard(lang=lang), parse_mode="HTML")
        return

    ton_price = await get_ton_price()
    if ton_price <= 0:
        text = t("ton_price_unavailable", lang)
        if edit_msg:
            await edit_msg.edit_text(text, reply_markup=back_keyboard(lang=lang), parse_mode="HTML")
        else:
            await bot.send_message(chat_id, text, reply_markup=back_keyboard(lang=lang), parse_mode="HTML")
        return
    user = get_user(chat_id)
    balance = user[2]
    price_in_gram = price / ton_price
    safe_sym = re.sub(r'[^A-Za-z0-9]', '', symbol)[:12]

    await state.finish()
    await state.update_data(ca=ca, symbol=symbol, price=price, mcap=mcap,
                            is_fdv=is_fdv, safe_sym=safe_sym, image_url=image_url, liq=liq)
    await CAState.entering_ca_amount.set()

    mcap_label = t("fdv_label", lang)
    mcap_str = format_mcap_val(mcap) if mcap > 0 else "N/A"
    liq_str = format_mcap_val(liq) if liq > 0 else "N/A"
    max_buy = balance / price_in_gram if price_in_gram > 0 else 0
    balance_usd = balance * ton_price
    price_usd_str = format_price(price)
    price_gram_str = format_gram(price_in_gram)
    created_str = f"\n🗓 {t('created_label', lang)}:  <b>{format_age(created_at, lang)}</b>" if created_at else ""
    ath_fdv_str = ""  # будет заполнено позже после загрузки истории

    image_arr = load_token_image(image_url) if image_url else None

    # Параллельно тянем историю для графика и историю за год для ATH
    history, history_365 = await asyncio.gather(
        get_token_history(ca, timeframe),
        get_token_history(ca, "365D"),
        return_exceptions=True
    )
    if isinstance(history, Exception): history = []
    if isinstance(history_365, Exception): history_365 = []

    # Считаем ATH FDV из годовой истории
    ath_fdv_str = ""
    if history_365 and len(history_365) >= 2 and price > 0 and mcap > 0:
        try:
            supply = mcap / price  # total supply
            max_price = max(c[2] for c in history_365)  # high price
            ath_fdv = max_price * supply
            ath_fdv_str = f"\n🏔 ATH FDV:  <b>{format_mcap_val(ath_fdv)}</b>"
        except: pass


    # Пересобираем caption с актуальным ath_fdv_str (после загрузки истории)
    caption = (
        f"🪙 <b>{symbol}</b>\n\n"
        f"💵 {t('price_label', lang)}:  <b>{price_usd_str}</b>  ·  <b>{price_gram_str} 💎</b>\n"
        f"📊 {mcap_label}:  <b>{mcap_str}</b>\n"
        f"💧 {t('liquidity_label', lang)}:  <b>{liq_str}</b>"
        f"{ath_fdv_str}"
        f"{created_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{t('balance_line', lang, bal=format_gram(balance), usd=f'{balance_usd:,.2f}')}\n\n"
        f"<code>{ca}</code>\n"
        f"🔗 <a href='{dex_link(ca)}'>DexScreener</a>\n\n"
        f"{t('enter_amount', lang)}"
    )
    if len(caption) > 1024:
        caption = caption[:1020] + "..."
    logger.info(f"Chart history for {ca[:8]}: {len(history)} candles")

    chart_buf = None
    if history and len(history) >= 2:
        try:
            chart_buf = generate_chart(symbol, history, image_arr=image_arr,
                                       mcap=mcap, is_fdv=is_fdv,
                                       current_price=price, timeframe=timeframe, liq=liq)
            logger.info(f"chart_buf generated: {chart_buf is not None}")
        except Exception as e:
            logger.error(f"generate_chart error: {e}")
            chart_buf = None

    kb = token_keyboard(safe_sym, ca, lang)

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
        # Проверяем есть ли уже калл по этому ЦА в чате
        existing_call = get_call(message.chat.id, ca)
        if existing_call:
            # Показываем инфу с данными первого каллера
            await send_token_info_group(message.chat.id, ca, edit_msg=msg, caller_info=existing_call)
        else:
            # Новый калл — записываем кто первый
            username = message.from_user.username or message.from_user.first_name
            await send_token_info_group(message.chat.id, ca, edit_msg=msg, caller_info=None,
                caller_user_id=message.from_user.id, caller_username=username)
        return

    # В личке — проверяем есть ли позиция по этому ЦА
    user_id = message.from_user.id
    lang = get_lang(user_id)
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
            InlineKeyboardButton(t("btn_share_pnl", lang), callback_data=f"sharepnl_{ca}"),
            InlineKeyboardButton(t("btn_sell", lang), callback_data=f"sellcoin_{sym}"),
            InlineKeyboardButton(t("btn_refresh", lang), callback_data=f"refresh_{ca}"),
            InlineKeyboardButton(t("btn_chart", lang), callback_data=f"refresh_{ca}"),
        )
        text = (
            f"🪙 <b>{sym}</b>  ·  <i>{t('your_position', lang)}</i>\n\n"
            f"📦 {t('amount_label', lang)}:  <b>{format_gram(amount)}</b>\n"
            f"💵 {t('entry_label', lang)}:  <b>{format_price(avg_price)}</b>\n"
            f"💵 {t('now_label', lang)}:  <b>{format_price(price)}</b>\n"
            f"📊 {t('fdv_label', lang)}:  {format_mcap_val(avg_mcap)} → {format_mcap_val(cur_mcap)}  {mc_chg}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{'🟢' if pnl_pct>=0 else '🔴'} {t('pnl_label', lang)}:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n"
            f"💎 {t('pnl_gram_label', lang)}:  <b>{'+' if pnl_gram>=0 else ''}{format_gram(pnl_gram)}</b>  <i>(≈ ${pnl_usd:+,.2f})</i>\n"
            f"💎 {t('value_label', lang)}:  <b>{format_gram(value)} GRAM</b>  <i>(≈ ${value*ton_price:,.2f})</i>"
        )
        try: await msg.delete()
        except: pass
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    msg = await message.answer("🔍 Scanning...")
    await send_token_info(message.chat.id, ca, state, edit_msg=msg)


async def send_token_info_group(chat_id, ca, edit_msg=None, caller_info=None, caller_user_id=0, caller_username="unknown"):
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

    # Показываем калл если есть
    call_str = ""
    if caller_info:
        caller_uid, caller_name, call_price, call_mcap, call_time = caller_info
        if call_price and call_price > 0 and price > 0:
            pnl_pct = (price - call_price) / call_price * 100
            pnl_str = f"{'🟢' if pnl_pct >= 0 else '🔴'} <b>{pnl_pct:+.1f}%</b>"
            call_mcap_str = format_mcap_val(call_mcap) if call_mcap else "?"
            # Показываем @username если есть, иначе кликабельное имя через user_id
            if caller_name and caller_name.strip():
                caller_display = f"@{caller_name}"
            else:
                caller_display = f"<a href='tg://user?id={caller_uid}'>User</a>"
            call_str = (f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                       f"📣 First call by {caller_display}\n"
                       f"📊 MCap at call: <b>{call_mcap_str}</b>  →  now {pnl_str}")

    text = (
        f"🪙 <b>{symbol}</b>\n\n"
        f"💵 Price:  <b>{format_price(price)}</b>\n"
        f"📊 FDV:  <b>{mcap_str}</b>\n"
        f"💧 Liquidity:  <b>{liq_str}</b>"
        f"{created_str}"
        f"{call_str}\n\n"
        f"<code>{ca}</code>\n"
        f"🔗 <a href='{dex_link(ca)}'>DexScreener</a>\n\n"
        f"<i>Trade on @TentaTrading_Bot</i>"
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🐙 Trade on OCTOtrade", url=f"https://t.me/TentaTrading_Bot?start={ca}"))
    try: await edit_msg.delete()
    except: pass

    image_arr = load_token_image(image_url) if image_url else None
    history = await get_token_history(ca, "7D")
    chart_buf = None
    if history and len(history) >= 2:
        try:
            chart_buf = generate_chart(symbol, history, image_arr=image_arr,
                                       mcap=mcap, is_fdv=True,
                                       current_price=price, timeframe="7D", liq=liq)
        except Exception as e:
            logger.warning(f"Group chart error: {e}")

    if chart_buf:
        await bot.send_photo(chat_id, InputFile(chart_buf, filename="chart.png"),
                             caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await bot.send_message(chat_id, text, reply_markup=kb,
                               parse_mode="HTML", disable_web_page_preview=True)

    # Записываем калл (если первый раз в этом чате)
    if caller_info is None:
        record_call(chat_id, caller_user_id, caller_username, ca, symbol, price, mcap)


@dp.message_handler(commands=["cruel"], chat_type=["group", "supergroup"])
async def cmd_cruel(message: types.Message):
    """Топ каллеров в чате"""
    chat_id = message.chat.id
    rows = get_top_callers(chat_id, limit=20)
    if not rows:
        await message.reply("📊 No calls yet in this chat. Be the first to drop a CA!")
        return

    ton_price = await get_ton_price()

    # Загружаем текущие цены и считаем PNL для сортировки
    callers_data = []
    for (username, ca, symbol, price_at_call, mcap_at_call, timestamp) in rows:
        try:
            cur_price, _, cur_mcap, _, _, _, _, _ = await asyncio.wait_for(
                fetch_token_data(ca), timeout=5
            )
        except:
            cur_price = 0
            cur_mcap = 0
        if cur_price and price_at_call and price_at_call > 0:
            pnl_pct = (cur_price - price_at_call) / price_at_call * 100
        else:
            pnl_pct = None
        callers_data.append((username, ca, symbol, price_at_call, mcap_at_call, timestamp, pnl_pct))

    # Сортируем: с PNL наверху (лучшие первые), без PNL в конце
    callers_data.sort(key=lambda x: (x[6] is None, -(x[6] or 0)))

    lines = ["🏆 <b>Top Callers</b>\n"]
    for i, (username, ca, symbol, price_at_call, mcap_at_call, timestamp, pnl_pct) in enumerate(callers_data[:10], 1):
        if pnl_pct is not None:
            pnl_str = f"{'🟢' if pnl_pct >= 0 else '🔴'} {pnl_pct:+.1f}%"
        else:
            pnl_str = "❓ N/A"
        date_str = timestamp[:10] if timestamp else ""
        if username and username.strip():
            caller_display = f"@{username}"
        else:
            caller_display = "Unknown"
        lines.append(
            f"{i}. {caller_display}  ·  <b>{symbol}</b>\n"
            f"   {pnl_str}  ·  {date_str}\n"
            f"   <a href='{dex_link(ca)}'>{ca[:8]}...</a>"
        )

    await message.reply("\n".join(lines), parse_mode="HTML",
                        disable_web_page_preview=True)

@dp.callback_query_handler(lambda c: c.data.startswith("refresh_"))
async def refresh_token(callback: types.CallbackQuery, state: FSMContext):
    ca = callback.data[8:]
    await callback.answer("Refreshing...")
    try: await callback.message.delete()
    except: pass
    await send_token_info(callback.message.chat.id, ca, state)

@dp.callback_query_handler(lambda c: c.data.startswith("tf_"))
async def change_timeframe(callback: types.CallbackQuery, state: FSMContext):
    # tf_CA_7D — CA может содержать любые символы кроме последнего _TF
    data = callback.data  # "tf_EQBuNm..._7D"
    # Таймфрейм всегда последний элемент
    last_underscore = data.rfind("_")
    tf = data[last_underscore+1:]
    ca = data[3:last_underscore]  # убираем "tf_" спереди и "_TF" сзади

    if not ca or tf not in ("1H", "6H", "24H", "7D", "30D"):
        await callback.answer("❌ Error", show_alert=True)
        return

    await callback.answer(f"⏳ {tf}...")
    msg = await callback.message.answer("🔍 Loading chart...")
    try:
        await callback.message.delete()
    except: pass
    await send_token_info(callback.message.chat.id, ca, state, edit_msg=msg, timeframe=tf)

@dp.callback_query_handler(lambda c: c.data.startswith("sharepnl_"))
async def share_pnl(callback: types.CallbackQuery):
    lang = get_lang(callback.from_user.id)
    ca = callback.data[9:]
    user_id = callback.from_user.id
    holdings = get_portfolio(user_id)
    existing = next((h for h in holdings if h[3] == ca), None)
    if not existing:
        await callback.answer(t("position_not_found_alert", lang), show_alert=True); return

    await callback.answer(t("generating", lang))
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
        image_arr=image_arr,
        ton_spent=ton_start,
        ton_received=ton_end
    )
    if card:
        caption = (
            f"{t('trade_result_title', lang)}\n\n"
            f"<b>{sym}</b>\n\n"
            f"{t('entry_label', lang)}:  <b>{format_mcap_val(avg_mcap)} FDV</b>\n"
            f"{t('exit_label', lang)}:  <b>{format_mcap_val(cur_mcap or avg_mcap)} FDV</b>\n\n"
            f"{'🟢' if pnl_pct>=0 else '🔴'} <b>{pnl_pct:+.2f}%</b>\n\n"
            f"{t('bought_sold_line', lang, start=format_gram(ton_start), end=format_gram(ton_end))}\n\n"
            f"🐙 OCTOtrade"
        )
        await bot.send_photo(user_id, InputFile(card, filename="pnl.png"),
                             caption=caption, parse_mode="HTML")
    else:
        await callback.answer(t("card_failed", lang), show_alert=True)

# ─── BUY ──────────────────────────────────────────────────────────────────────

@dp.message_handler(state=CAState.entering_ca_amount)
async def ca_amount_entered(message: types.Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
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
            await message.answer(t("insufficient_balance", lang, bal=format_gram(balance))); return
        coin_raw = gram_input / price_in_gram
        coin_out, gram_spent, dex_fee, net_fee, impact, slip = apply_fees(gram_input, coin_raw, "buy")
        if gram_spent > balance:
            await message.answer(t("not_enough_fees", lang, amt=format_gram(gram_spent))); return
    except:
        await message.answer(t("enter_number", lang), parse_mode="HTML"); return

    mcap_label = t("fdv_label", lang) if is_fdv else "MCap"
    await state.update_data(gram_amount=gram_spent, coin_amount=coin_out, mcap=mcap,
                            dex_fee=dex_fee, net_fee=net_fee, slip=slip)
    await CAState.confirming_ca.set()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t("confirm_swap_btn", lang), callback_data="confirm_ca_trade"),
           InlineKeyboardButton(t("cancel_btn", lang), callback_data="back_main"))
    await message.answer(
        f"{t('confirm_buy', lang)}\n\n"
        f"🪙 {symbol}:  <b>{format_gram(coin_out)}</b>\n"
        f"💵 {t('price_label', lang)}:  {format_price(price)}\n"
        f"📊 {mcap_label}:  {format_mcap_val(mcap)}\n\n"
        f"💎 {t('cost_label', lang)}:  <b>{format_gram(gram_spent)} GRAM</b>\n"
        f"   ├ {t('dex_fee_label', lang)}:  {format_gram(dex_fee)} GRAM\n"
        f"   ├ {t('network_fee_label', lang)}:  {format_gram(net_fee)} GRAM\n"
        f"   └ {t('slippage_label', lang)}:  -{slip:.2f}%",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="confirm_ca_trade", state=CAState.confirming_ca)
async def confirm_ca_trade(callback: types.CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id)
    data = await state.get_data()
    ca=data["ca"]; symbol=data["symbol"]; price=data["price"]
    gram_amount=data.get("gram_amount", 0); coin_amount=data.get("coin_amount", 0)
    mcap=data.get("mcap", 0)
    user_id = callback.from_user.id

    await edit_or_answer(callback.message, 
        f"{t('processing_swap', lang)}\n"
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

    await edit_or_answer(callback.message, 
        f"{t('swap_filled', lang)}\n\n"
        f"🪙 {t('received_label', lang)}:  <b>{format_gram(coin_amount)} {symbol}</b>\n"
        f"💎 {t('spent_label', lang)}:  <b>{format_gram(gram_amount)} GRAM</b>\n"
        f"💵 {t('entry_label', lang)}:  {format_price(price)}\n"
        f"📊 {format_mcap(mcap, data.get('is_fdv', True))}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{t('balance_line', lang, bal=format_gram(user[2]), usd=f'{user[2]*ton_price:,.2f}')}",
        reply_markup=main_keyboard(lang), parse_mode="HTML"
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
                                       current_price=price, timeframe="7D", liq=data.get("liq", 0))
            if chart_buf:
                sent_msg = await bot.send_photo(
                    user_id, InputFile(chart_buf, filename="chart.png"),
                    caption=f"📊 <b>{symbol}</b>  ·  Entry {format_price(price)}  ·  {format_mcap(mcap, data.get('is_fdv', True))}",
                    parse_mode="HTML"
                )
                _pending_chart_delete[user_id] = sent_msg.message_id
        except Exception as e:
            logger.warning(f"Post-buy chart error: {e}")

# ─── QUICK % ─────────────────────────────────────────────────────────────────

@dp.callback_query_handler(lambda c: c.data.startswith("q_"), state="*")
async def quick_amount(callback: types.CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id)
    parts = callback.data.split("_")
    if len(parts) < 4: return
    action = parts[1]; pct = int(parts[-1])
    data = await state.get_data()
    price = data.get("price", 0)
    symbol = data.get("symbol", "")
    mcap = data.get("mcap", 0)
    is_fdv = data.get("is_fdv", True)
    ca = data.get("ca", "")

    # Если цена пропала из state — берём заново по CA
    if not price and ca:
        await callback.answer(t("refreshing_price", lang))
        try:
            price, symbol, mcap, is_fdv, _, _, _, _ = await fetch_token_data(ca)
            if price:
                await state.update_data(price=price, symbol=symbol, mcap=mcap, is_fdv=is_fdv)
                await CAState.entering_ca_amount.set()
        except Exception as e:
            logger.warning(f"Price refetch: {e}")

    if not price:
        await callback.answer(t("price_unavailable_resend", lang), show_alert=True)
        return

    ton_price = await get_ton_price()
    price_in_gram = price / ton_price
    user = get_user(callback.from_user.id); balance = user[2]
    mcap_label = t("fdv_label", lang) if is_fdv else "MCap"

    if action == "ca":
        if pct == 100:
            # ALL IN — резервируем комиссию из баланса чтобы не уйти в минус
            fee_reserve = NETWORK_FEE_GRAM + balance * DEX_FEE + balance * 0.0001
            gram_input = max(balance - fee_reserve, 0)
        else:
            gram_input = balance * pct / 100
        if gram_input <= 0: await callback.answer(t("insufficient_balance_alert", lang), show_alert=True); return
        coin_raw = gram_input / price_in_gram
        coin_out, gram_spent, dex_fee, net_fee, impact, slip = apply_fees(gram_input, coin_raw, "buy")
        # Финальная защита — не тратить больше чем есть
        if gram_spent > balance:
            gram_spent = balance
            coin_out = coin_out * (balance / gram_spent) if gram_spent > 0 else 0
        await state.update_data(gram_amount=gram_spent, coin_amount=coin_out, mcap=mcap, slip=slip)
        await CAState.confirming_ca.set()
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton(t("confirm_swap_btn", lang), callback_data="confirm_ca_trade"),
               InlineKeyboardButton(t("cancel_btn", lang), callback_data="back_main"))
        await edit_or_answer(callback.message, 
            f"{t('confirm_buy_pct', lang, pct=pct)}\n\n"
            f"🪙 {symbol}:  <b>{format_gram(coin_out)}</b>\n"
            f"💵 {t('price_label', lang)}:  {format_price(price)}\n"
            f"📊 {mcap_label}:  {format_mcap_val(mcap)}\n\n"
            f"💎 {t('cost_label', lang)}:  <b>{format_gram(gram_spent)} GRAM</b>  <i>(≈ ${gram_spent*ton_price:,.2f})</i>\n"
            f"   ├ {t('dex_fee_label', lang)}:  {format_gram(dex_fee)} GRAM\n"
            f"   ├ {t('network_fee_label', lang)}:  {format_gram(net_fee)} GRAM\n"
            f"   └ {t('slippage_label', lang)}:  -{slip:.2f}%",
            reply_markup=kb, parse_mode="HTML"
        )

    elif action == "sell":
        held, avg_price, ca, avg_mcap = get_position(callback.from_user.id, symbol)
        if held <= 0: await callback.answer(t("no_position_alert", lang), show_alert=True); return
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
        kb.add(InlineKeyboardButton(t("confirm_swap_btn", lang), callback_data="confirm_sell"),
               InlineKeyboardButton(t("cancel_btn", lang), callback_data="back_main"))
        arrow = pnl_arrow(pnl_pct)
        pnl_usd = pnl_gram * ton_price
        await edit_or_answer(callback.message, 
            f"{t('confirm_sell_pct', lang, pct=pct)}\n\n"
            f"🪙 {symbol}:  <b>{format_gram(coin_amount)}</b>\n"
            f"💵 {t('price_label', lang)}:  {format_price(price)}\n"
            f"💎 {t('receive_colon', lang)}:  <b>{format_gram(gram_received)} GRAM</b>  <i>(≈ ${gram_received*ton_price:,.2f})</i>\n\n"
            f"{'🟢' if pnl_pct>=0 else '🔴'} {t('pnl_label', lang)}:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n"
            f"{'🟢' if pnl_gram>=0 else '🔴'} {t('pnl_label', lang)} $:  <b>{'+' if pnl_gram>=0 else ''}{format_gram(pnl_gram)} GRAM</b>  <i>(≈ ${pnl_usd:+,.2f})</i>\n"
            f"📊 {t('fdv_label', lang)}:  {format_mcap_val(avg_mcap)} → {format_mcap_val(mcap or avg_mcap)}  {mc_chg}",
            reply_markup=kb, parse_mode="HTML"
        )

# ─── SELL ─────────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="sell")
async def sell_menu(callback: types.CallbackQuery):
    await _clear_pending_chart(callback.from_user.id)
    lang = get_lang(callback.from_user.id)
    await SellState.choosing_coin.set()
    holdings = get_portfolio(callback.from_user.id)
    if not holdings:
        await edit_or_answer(callback.message, 
            t("no_open_positions", lang),
            reply_markup=back_keyboard(lang=lang), parse_mode="HTML"); return
    kb = InlineKeyboardMarkup(row_width=1)
    for sym, amount, avg_price, ca, avg_mcap in holdings:
        kb.add(InlineKeyboardButton(f"🔴 {sym}  ·  {format_gram(amount)}", callback_data=f"sellcoin_{sym}"))
    kb.add(InlineKeyboardButton(t("btn_back", lang), callback_data="back_main"))
    await edit_or_answer(callback.message, f"{t('sell_position_title', lang)}\n\n{t('select_label', lang)}", reply_markup=kb, parse_mode="HTML")

@dp.callback_query_handler(lambda c: c.data.startswith("sellcoin_"), state="*")
async def sell_coin_chosen(callback: types.CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id)
    symbol = callback.data[9:]
    held, avg_price, ca, avg_mcap = get_position(callback.from_user.id, symbol)
    if held <= 0:
        await edit_or_answer(callback.message, t("position_not_found", lang), reply_markup=back_keyboard(lang=lang))
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
        mcap_line = f"\n📊 {t('fdv_label', lang)}:  {format_mcap_val(avg_mcap)} → {format_mcap_val(cur_mcap)}  {mc_chg}"

    await edit_or_answer(callback.message, 
        f"{t('sell_title', lang, symbol=symbol)}\n\n"
        f"💵 {t('price_label', lang)}:  <b>{format_price(price)}</b>\n"
        f"📦 {t('held_label', lang)}:  <b>{format_gram(held)} {symbol}</b>\n"
        f"📈 {t('entry_label', lang)}:  {format_price(avg_price)}{mcap_line}\n"
        f"{'🟢' if pnl_pct>=0 else '🔴'} {t('pnl_label', lang)}:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n\n"
        f"{t('enter_amount_or_tap', lang)}",
        reply_markup=amount_keyboard("sell", safe_sym, lang), parse_mode="HTML"
    )

@dp.message_handler(state=SellState.entering_amount)
async def sell_amount_entered(message: types.Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
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
            await message.answer(t("only_have", lang, amt=format_gram(held), symbol=symbol)); return
    except:
        await message.answer(t("enter_number_plain", lang), parse_mode="HTML"); return

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
    kb.add(InlineKeyboardButton(t("confirm_swap_btn", lang), callback_data="confirm_sell"),
           InlineKeyboardButton(t("cancel_btn", lang), callback_data="back_main"))
    await message.answer(
        f"{t('confirm_sell', lang)}\n\n"
        f"🪙 {format_gram(coin_amount)} <b>{symbol}</b>\n"
        f"💵 {t('price_label', lang)}:  {format_price(price)}\n"
        f"💎 {t('receive_colon', lang)}:  <b>{format_gram(gram_received)} GRAM</b>\n\n"
        f"{'🟢' if pnl_pct>=0 else '🔴'} {t('pnl_label', lang)}:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="confirm_sell", state=SellState.confirming)
async def confirm_sell(callback: types.CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id)
    data = await state.get_data()
    symbol=data["symbol"]; price=data["price"]; coin_amount=data["coin_amount"]
    gram_received=data["gram_received"]; pnl_pct=data["pnl_pct"]; pnl_gram=data["pnl_gram"]
    avg_price=data["avg_price"]; avg_mcap=data.get("avg_mcap",0); cur_mcap=data.get("cur_mcap",0)
    image_url=data.get("image_url","")
    user_id = callback.from_user.id
    arrow = pnl_arrow(pnl_pct)
    x_str = format_x(avg_mcap, cur_mcap)
    mc_chg = format_mcap_change(avg_mcap, cur_mcap)

    await edit_or_answer(callback.message, t("processing_sell", lang), parse_mode="HTML")
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

    await edit_or_answer(callback.message, 
        f"{'🟢' if pnl_pct>=0 else '🔴'} {t('position_closed', lang)}\n\n"
        f"🪙 {symbol}:  <b>{format_gram(coin_amount)}</b>\n\n"
        f"💎 <b>{format_gram(gram_spent)} GRAM  ——›  {format_gram(gram_received)} GRAM</b>\n\n"
        f"{'🟢' if pnl_pct>=0 else '🔴'} {t('pnl_label', lang)}:  <b>{pnl_pct:+.2f}% {arrow}</b>  {x_str}\n"
        f"{'🟢' if pnl_gram>=0 else '🔴'} {t('pnl_label', lang)}:  <b>{'+' if pnl_gram>=0 else ''}{format_gram(pnl_gram)} GRAM</b>  <i>(≈ ${pnl_usd:+,.2f})</i>\n"
        f"📊 {t('fdv_label', lang)}:  {format_mcap_val(avg_mcap)} → {format_mcap_val(cur_mcap)}  {mc_chg}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{t('balance_line', lang, bal=format_gram(user[2]), usd=f'{user[2]*ton_price:,.2f}')}",
        reply_markup=main_keyboard(lang), parse_mode="HTML"
    )

    image_arr = load_token_image(image_url) if image_url else None
    pnl_buf = generate_pnl_card(symbol, pnl_pct, pnl_gram, avg_mcap, cur_mcap, avg_price, price, image_arr, ton_spent=gram_spent, ton_received=gram_received)
    if pnl_buf:
        await bot.send_photo(user_id, InputFile(pnl_buf, filename="pnl.png"),
                             caption=f"{'🟢' if pnl_pct>=0 else '🔴'} <b>{symbol}</b>  {pnl_pct:+.2f}% {arrow}  {x_str}",
                             parse_mode="HTML")

# ─── PORTFOLIO ───────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="portfolio")
async def show_portfolio(callback: types.CallbackQuery):
    await _clear_pending_chart(callback.from_user.id)
    lang = get_lang(callback.from_user.id)
    try:
        await callback.answer("⏳ Loading...")
    except: pass
    try:
        user = get_user(callback.from_user.id)
        balance = user[2]
        holdings = get_portfolio(callback.from_user.id)
        ton_price = await get_ton_price()
        logger.info(f"Portfolio: user={callback.from_user.id} holdings={len(holdings) if holdings else 0}")
    except Exception as e:
        logger.error(f"Portfolio load error: {e}")
        await bot.send_message(callback.from_user.id, f"❌ Ошибка портфолио: {e}")
        return

    if not holdings:
        await edit_or_answer(callback.message,
            f"{t('portfolio_title', lang)}\n\n"
            f"{t('balance_line', lang, bal=format_gram(balance), usd=f'{balance*ton_price:,.2f}')}\n\n"
            f"{t('no_positions', lang)}",
            reply_markup=back_keyboard(lang=lang), parse_mode="HTML"); return

    async def fetch_one(row):
        sym, amount, avg_price, ca, avg_mcap = row
        if ca:
            try:
                price, fetched_sym, cur_mcap, is_fdv, _, _, _, _ = await asyncio.wait_for(
                    fetch_token_data(ca), timeout=8
                )
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

    total_pnl_gram = 0.0
    lines = [f"{t('portfolio_title', lang)}\n\n{t('balance_line', lang, bal=format_gram(balance), usd=f'{balance*ton_price:,.2f}')}\n"]

    for res in results:
        if isinstance(res, Exception):
            continue
        sym, amount, avg_price, ca, avg_mcap, price, cur_mcap = res
        price_in_gram = price / ton_price if ton_price > 0 else 0
        avg_price_gram = avg_price / ton_price if ton_price > 0 else 0
        value = amount * price_in_gram
        pnl_pct = (price - avg_price) / avg_price * 100 if avg_price > 0 else 0
        pnl_gram = value - amount * avg_price_gram
        total_pnl_gram += pnl_gram
        arrow = pnl_arrow(pnl_pct)
        x_str = format_x(avg_mcap, cur_mcap)
        mc_chg = format_mcap_change(avg_mcap, cur_mcap)
        dex = f"<a href='{dex_link(ca)}'>{sym}</a>" if ca else f"<b>{sym}</b>"

        invested_gram = amount * avg_price_gram
        trend = "📈" if pnl_pct >= 0 else "📉"
        lines.append(
            f"{'🟢' if pnl_pct>=0 else '🔴'} {dex}\n"
            f"   {trend} {format_mcap_val(avg_mcap)} → {format_mcap_val(cur_mcap)}  {pnl_pct:+.2f}%\n"
            f"   {format_gram(invested_gram)} 💎GRAM → {format_gram(value)}💎GRAM"
        )

    total_sign = "+" if total_pnl_gram >= 0 else ""
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━\n{t('total_label', lang)}:  <b>{total_sign}{format_gram(total_pnl_gram)} 💎GRAM</b>")

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t("btn_refresh", lang), callback_data="portfolio"))
    kb.add(InlineKeyboardButton(t("btn_back", lang), callback_data="back_main"))
    await edit_or_answer(callback.message, "\n".join(lines), reply_markup=kb,
                                     parse_mode="HTML", disable_web_page_preview=True)

# ─── LEADERBOARD ─────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="leaderboard")
async def show_leaderboard(callback: types.CallbackQuery):
    lang = get_lang(callback.from_user.id)
    rows = get_all_users_pnl()
    if not rows:
        await edit_or_answer(callback.message, "🏆 <b>Leaderboard</b>\n\n<i>No traders yet.</i>",
                                         reply_markup=back_keyboard(lang=lang), parse_mode="HTML"); return
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
    await edit_or_answer(callback.message, "\n".join(lines), reply_markup=back_keyboard(lang=lang), parse_mode="HTML")

# ─── HISTORY ─────────────────────────────────────────────────────────────────

def _day_label(day_str, lang="en"):
    """day_str = 'DD.MM' -> 'Today' / 'Yesterday' / '17 Jun'"""
    try:
        now = datetime.now()
        day, month = int(day_str[:2]), int(day_str[3:5])
        d = datetime(now.year, month, day)
        diff = (now.date() - d.date()).days
        if diff == 0: return t("today", lang)
        if diff == 1: return t("yesterday", lang)
        return d.strftime("%d %b")
    except:
        return day_str


@dp.callback_query_handler(text="history")
async def show_history(callback: types.CallbackQuery):
    await _clear_pending_chart(callback.from_user.id)
    lang = get_lang(callback.from_user.id)
    days = get_trade_days(callback.from_user.id, limit_days=14)
    if not days:
        await edit_or_answer(callback.message,
            f"{t('history_title', lang)}\n\n{t('no_trades', lang)}",
            reply_markup=back_keyboard(lang=lang), parse_mode="HTML"); return

    kb = InlineKeyboardMarkup()
    lines = [f"{t('history_title', lang)}\n"]
    for day_str, cnt in days:
        label = _day_label(day_str, lang)
        lines.append(f"<b>{label}</b>\n   {cnt} {t('trades_word', lang)}")
        kb.add(InlineKeyboardButton(f"{label}  ·  {cnt} {t('trades_word', lang)}", callback_data=f"hday_{day_str}"))
    kb.add(InlineKeyboardButton(t("btn_back", lang), callback_data="back_main"))
    await edit_or_answer(callback.message, "\n\n".join(lines), reply_markup=kb, parse_mode="HTML")


@dp.callback_query_handler(lambda c: c.data.startswith("hday_"))
async def show_history_day(callback: types.CallbackQuery):
    lang = get_lang(callback.from_user.id)
    day_str = callback.data.split("_", 1)[1]
    trades = get_trades_for_day(callback.from_user.id, day_str, limit=50)
    label = _day_label(day_str, lang)

    if not trades:
        await edit_or_answer(callback.message,
            f"📊 <b>{label}</b>\n\n{t('no_trades_day', lang)}",
            reply_markup=back_keyboard("history", lang=lang), parse_mode="HTML"); return

    lines = [f"📊 <b>{label}</b>\n"]
    for sym, action, amount, price, price_buy, total, pnl_pct, mcap_buy, mcap_sell, ts in trades:
        time_part = ts[6:] if len(ts) > 6 else ts
        if action == "BUY":
            lines.append(
                f"{time_part}  🟢 {t('buy_word', lang)}   <b>{sym}</b>\n"
                f"      {format_gram(total)} 💎  |  {format_mcap_val(mcap_buy)}"
            )
        else:
            lines.append(
                f"{time_part}  🔴 {t('sell_word', lang)}  <b>{sym}</b>\n"
                f"      {format_gram(total)} 💎  |  {format_mcap_val(mcap_sell)}"
            )
    kb = back_keyboard("history", lang=lang)
    await edit_or_answer(callback.message, "\n\n".join(lines), reply_markup=kb, parse_mode="HTML")


def _fmt_hold_time(minutes):
    if minutes <= 0:
        return "—"
    h = int(minutes // 60)
    m = int(minutes % 60)
    if h == 0:
        return f"{m}m"
    return f"{h}h {m}m"


@dp.callback_query_handler(text="performance")
async def show_performance(callback: types.CallbackQuery):
    await _clear_pending_chart(callback.from_user.id)
    lang = get_lang(callback.from_user.id)
    stats = get_performance_stats(callback.from_user.id)
    if not stats or stats["total_trades"] == 0:
        await edit_or_answer(callback.message,
            f"{t('performance_title', lang)}\n\n{t('no_trades_perf', lang)}",
            reply_markup=back_keyboard(lang=lang), parse_mode="HTML"); return

    text = (
        f"{t('performance_title', lang)}\n\n"
        f"{t('perf_trades', lang)}:  <b>{stats['total_trades']}</b>\n"
        f"{t('perf_winrate', lang)}:  <b>{stats['win_rate']:.0f}%</b>\n"
        f"{t('perf_volume', lang)}:  <b>{format_gram(stats['volume'])} 💎</b>\n"
        f"{t('perf_best', lang)}:  <b>{stats['best_trade']:+.0f}%</b>\n"
        f"{t('perf_avghold', lang)}:  <b>{_fmt_hold_time(stats['avg_hold_min'])}</b>"
    )
    await edit_or_answer(callback.message, text, reply_markup=back_keyboard(lang=lang), parse_mode="HTML")


@dp.callback_query_handler(text="lang_menu")
async def show_lang_menu(callback: types.CallbackQuery):
    lang = get_lang(callback.from_user.id)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
        InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
    )
    kb.add(InlineKeyboardButton(t("btn_back", lang), callback_data="back_main"))
    await edit_or_answer(callback.message, t("choose_lang", lang), reply_markup=kb, parse_mode="HTML")


@dp.callback_query_handler(lambda c: c.data.startswith("setlang_"))
async def set_lang_handler(callback: types.CallbackQuery):
    new_lang = callback.data.split("_", 1)[1]
    set_lang(callback.from_user.id, new_lang)
    await callback.answer(t("lang_updated", new_lang))
    await edit_or_answer(callback.message,
        f"{t('welcome_back', new_lang, name=callback.from_user.first_name)}\n\n{t('subtitle', new_lang)}",
        reply_markup=main_keyboard(new_lang), parse_mode="HTML")

# ─── BALANCE ─────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="balance_menu")
async def balance_menu(callback: types.CallbackQuery):
    await _clear_pending_chart(callback.from_user.id)
    lang = get_lang(callback.from_user.id)
    user = get_user(callback.from_user.id)
    ton_price = await get_ton_price()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t("add_gram_btn", lang, max=f"{MAX_TOPUP:,.0f}"), callback_data="topup"))
    kb.add(InlineKeyboardButton(t("set_balance_btn", lang), callback_data="setbalance_menu"))
    kb.add(InlineKeyboardButton(t("btn_back", lang), callback_data="back_main"))
    await edit_or_answer(callback.message, 
        f"{t('balance_title', lang)}\n\n"
        f"<b>{format_gram(user[2])} GRAM</b>  <i>(≈ ${user[2]*ton_price:,.2f})</i>\n\n"
        f"1 💎 GRAM  =  ${ton_price:.4f}  <i>{t('live_ton_price', lang)}</i>",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="setbalance_menu")
async def setbalance_menu(callback: types.CallbackQuery):
    lang = get_lang(callback.from_user.id)
    await SetBalanceState.entering_balance.set()
    user = get_user(callback.from_user.id)
    await edit_or_answer(callback.message, 
        f"{t('set_balance_title', lang)}\n\n{t('current_label', lang)}: <b>{format_gram(user[2])} 💎 GRAM</b>\n\n{t('enter_new_amount', lang)}",
        reply_markup=back_keyboard(lang=lang), parse_mode="HTML"
    )

@dp.message_handler(state=SetBalanceState.entering_balance)
async def setbalance_received(message: types.Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    try:
        amount = float(message.text.strip().replace(",", ""))
        if amount < 1 or amount > 10_000_000: raise ValueError
    except:
        await message.answer(t("setbalance_range2", lang)); return
    set_balance(message.from_user.id, amount)
    await state.finish()
    await message.answer(t("balance_set", lang, amt=format_gram(amount)),
                         reply_markup=main_keyboard(lang), parse_mode="HTML")

@dp.callback_query_handler(text="topup")
async def topup_start(callback: types.CallbackQuery):
    lang = get_lang(callback.from_user.id)
    await TopUpState.entering_topup.set()
    user = get_user(callback.from_user.id)
    await edit_or_answer(callback.message, 
        f"{t('add_gram_title', lang)}\n\n{t('current_label', lang)}: <b>{format_gram(user[2])} GRAM</b>\n{t('max_label', lang)}: <b>{MAX_TOPUP:,.0f} GRAM</b>\n\n{t('how_much', lang)}",
        reply_markup=back_keyboard(lang=lang), parse_mode="HTML"
    )

@dp.message_handler(state=TopUpState.entering_topup)
async def topup_received(message: types.Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    try:
        amount = float(message.text.strip())
        if amount <= 0: raise ValueError
        if amount > MAX_TOPUP:
            await message.answer(t("max_topup_error", lang, max=f"{MAX_TOPUP:,.0f}")); return
    except:
        await message.answer(t("topup_range_error", lang, max=f"{MAX_TOPUP:,.0f}")); return
    update_balance(message.from_user.id, amount)
    await state.finish()
    user = get_user(message.from_user.id)
    ton_price = await get_ton_price()
    await message.answer(
        f"{t('added_gram', lang, amt=format_gram(amount))}\n"
        f"{t('balance_line', lang, bal=format_gram(user[2]), usd=f'{user[2]*ton_price:,.2f}')}",
        reply_markup=main_keyboard(lang), parse_mode="HTML"
    )

# ─── RESET ───────────────────────────────────────────────────────────────────

@dp.callback_query_handler(text="reset_confirm")
async def reset_confirm(callback: types.CallbackQuery):
    await _clear_pending_chart(callback.from_user.id)
    lang = get_lang(callback.from_user.id)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t("reset_btn", lang), callback_data="reset_do"),
           InlineKeyboardButton(t("cancel_btn", lang), callback_data="back_main"))
    await edit_or_answer(callback.message, 
        t("reset_confirm_title", lang, bal=STARTING_BALANCE),
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query_handler(text="reset_do")
async def reset_do(callback: types.CallbackQuery):
    lang = get_lang(callback.from_user.id)
    reset_user(callback.from_user.id)
    await edit_or_answer(callback.message, 
        t("account_reset", lang, bal=STARTING_BALANCE),
        reply_markup=main_keyboard(lang), parse_mode="HTML"
    )

@dp.message_handler(content_types=types.ContentType.TEXT)
async def unknown_message(message: types.Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("drop_ca_or_menu", lang),
                         reply_markup=main_keyboard(lang))

# ─── MAIN ─────────────────────────────────────────────────────────────────────

@dp.callback_query_handler(lambda c: c.message.chat.type in ("group", "supergroup"), state="*")
async def ignore_group_callbacks(callback: types.CallbackQuery, state: FSMContext):
    """В группах игнорируем все callback кнопки"""
    await callback.answer("Open @TentaTrading_Bot in DM to trade 🐙", show_alert=False)

async def on_startup(dp):
    init_db()
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
