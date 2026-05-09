"""
Telegram Stock Analysis & Trading Journal Bot
Simple version - all in one file
"""

import os
import logging
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import yfinance as yf
import pandas as pd
import numpy as np

# ============================================
# CONFIG - Set Environment Variables di Railway
# ============================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
JK_SUFFIX = ".JK"

# Database path
DATABASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_journal.db")

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# DATABASE
# ============================================
def init_db():
    """Init SQLite database."""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, ticker TEXT, type TEXT, lot INTEGER, price REAL, time TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, ticker TEXT UNIQUE, lot INTEGER, avg_price REAL
    )""")
    conn.commit()
    conn.close()

def ensure_user(user_id):
    """Dummy function - user tracking optional."""
    pass

def record_transaction(user_id, ticker, tx_type, lot, price):
    """Record buy/sell transaction."""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO transactions (user_id, ticker, type, lot, price, time) VALUES (?, ?, ?, ?, ?, ?)",
              (user_id, ticker, tx_type, lot, price, datetime.now().strftime("%Y-%m-%d %H:%M")))

    if tx_type == 'BUY':
        c.execute("SELECT lot, avg_price FROM positions WHERE user_id=? AND ticker=?", (user_id, ticker))
        row = c.fetchone()
        if row:
            new_lot = row[0] + lot
            new_avg = ((row[0] * row[1]) + (lot * price)) / new_lot
            c.execute("UPDATE positions SET lot=?, avg_price=? WHERE user_id=? AND ticker=?",
                     (new_lot, new_avg, user_id, ticker))
        else:
            c.execute("INSERT INTO positions (user_id, ticker, lot, avg_price) VALUES (?, ?, ?, ?)",
                     (user_id, ticker, lot, price))
    elif tx_type == 'SELL':
        c.execute("SELECT lot FROM positions WHERE user_id=? AND ticker=?", (user_id, ticker))
        row = c.fetchone()
        if row:
            new_lot = row[0] - lot
            if new_lot <= 0:
                c.execute("DELETE FROM positions WHERE user_id=? AND ticker=?", (user_id, ticker))
            else:
                c.execute("UPDATE positions SET lot=? WHERE user_id=? AND ticker=?", (new_lot, user_id, ticker))
    conn.commit()
    conn.close()

def get_positions(user_id):
    """Get open positions."""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("SELECT ticker, lot, avg_price FROM positions WHERE user_id=?", (user_id,))
    return c.fetchall()

def get_history(user_id):
    """Get transaction history."""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("SELECT ticker, type, lot, price, time FROM transactions WHERE user_id=? ORDER BY time DESC LIMIT 20", (user_id,))
    return c.fetchall()

# ============================================
# TECHNICAL ANALYSIS
# ============================================
def get_data(ticker):
    """Fetch stock data from Yahoo Finance."""
    try:
        full = f"{ticker.upper().replace('.JK', '')}{JK_SUFFIX}"
        stock = yf.Ticker(full)
        df = stock.history(period="3mo")
        return df if not df.empty else None
    except:
        return None

def calc_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(prices):
    ema12 = prices.ewm(span=12, adjust=False).mean()
    ema26 = prices.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal

def analyze(ticker):
    """Complete technical analysis."""
    df = get_data(ticker)
    if df is None:
        return None

    close = df['Close']
    high = df['High']
    low = df['Low']

    # Indicators
    rsi = calc_rsi(close)
    macd, signal = calc_macd(close)
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    # Bollinger Bands
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + (2 * bb_std)
    bb_lower = bb_mid - (2 * bb_std)

    # Support/Resistance
    support = low.tail(20).min()
    resistance = high.tail(20).max()

    # ATR
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    atr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()

    # Current values
    cp = close.iloc[-1]
    crsi = rsi.iloc[-1]
    cmacd = macd.iloc[-1]
    csig = signal.iloc[-1]
    cma20 = ma20.iloc[-1]
    cma50 = ma50.iloc[-1]
    cma200 = ma200.iloc[-1]
    catr = atr.iloc[-1]

    # Trend
    if pd.isna(cma200):
        trend = "INSUFFICIENT_DATA"
    elif cp > cma20 > cma50 > cma200:
        trend = "STRONG_UPTREND"
    elif cp > cma20 > cma50:
        trend = "UPTREND"
    elif cp < cma20 < cma50 < cma200:
        trend = "STRONG_DOWNTREND"
    elif cp < cma20 < cma50:
        trend = "DOWNTREND"
    else:
        trend = "SIDEWAYS"

    # Signal
    score = 0
    if crsi < 30: score += 2
    elif crsi > 70: score -= 2
    if cmacd > csig: score += 1
    else: score -= 1
    if cp > cma20: score += 1
    else: score -= 1
    if cp > cma50: score += 1
    else: score -= 1
    if "UP" in trend: score += 2
    elif "DOWN" in trend: score -= 2

    if score >= 3: sig = "BUY"
    elif score <= -3: sig = "SELL"
    else: sig = "HOLD"

    # TP/SL
    tp = resistance
    sl = support if support > 0 else cp - (catr * 2)
    risk = cp - sl
    reward = tp - cp
    rr = reward / risk if risk > 0 else 0
    tp_valid = rr >= 1.5

    return {
        'ticker': ticker.upper(),
        'price': cp,
        'change': ((cp - close.iloc[-2]) / close.iloc[-2]) * 100,
        'signal': sig,
        'trend': trend,
        'rsi': crsi,
        'macd': cmacd,
        'macd_sig': csig,
        'ma20': cma20,
        'ma50': cma50,
        'ma200': cma200,
        'bb_upper': bb_upper.iloc[-1],
        'bb_lower': bb_lower.iloc[-1],
        'bb_mid': bb_mid.iloc[-1],
        'stoch_k': 50,  # Simplified
        'atr': catr,
        'volume': df['Volume'].iloc[-1],
        'support': support,
        'resistance': resistance,
        'tp': tp,
        'sl': sl,
        'rr': rr,
        'tp_valid': tp_valid
    }

def get_groq_insight(ticker, data):
    """Get AI insight from Groq."""
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        prompt = f"""Analisa saham {ticker} dengan data teknikal:
- Harga: Rp {data['price']:,.0f}
- RSI: {data['rsi']:.1f}
- MACD: {data['macd']:.2f}
- Signal: {data['signal']}
- Trend: {data['trend']}
- Support: Rp {data['support']:,.0f}
- Resistance: Rp {data['resistance']:,.0f}
- R/R Ratio: {data['rr']:.2f}x

Berikan analisis singkat dalam Bahasa Indonesia (maksimal 150 kata)."""

        response = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=500
        )
        return response.choices[0].message.content
    except:
        return None

# ============================================
# BOT COMMANDS
# ============================================
async def start(update: Update, ctx):
    await update.message.reply_text("""🤖 *Stock Analysis Bot*

📊 `/sinyal <ticker>` - Analisis teknikal
📝 `/beli <ticker> <lot> <harga>` - Catat beli
📝 `/jual <ticker> <lot> <harga>` - Catat jual
📖 `/jurnal` - History transaksi
📊 `/posisi` - Posisi terbuka
🧮 `/average <lot1> <harga1> <lot2> <harga2>` - Rata-rata
🔴 `/cutloss <entry> <current> <lot>` - Cut loss""", parse_mode='Markdown')

async def sinyal(update: Update, ctx):
    if not ctx.args:
        await update.message.reply_text("Format: /sinyal BBCA")
        return

    ticker = ctx.args[0].upper()
    await update.message.reply_text(f"🔍 Analisis {ticker}...")

    data = analyze(ticker)
    if not data:
        await update.message.reply_text(f"❌ Data tidak ditemukan untuk {ticker}")
        return

    # Signal emoji
    emoji = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '🟡'}
    trend_emoji = {'STRONG_UPTREND': '📈💪', 'UPTREND': '📈', 'DOWNTREND': '📉',
                   'STRONG_DOWNTREND': '📉😰', 'SIDEWAYS': '➡️', 'INSUFFICIENT_DATA': '❓'}

    change_sign = "+" if data['change'] > 0 else ""

    msg = f"""📊 *ANALISIS {ticker}*

💰 Harga: Rp {data['price']:,.0f} ({change_sign}{data['change']:.2f}%)
🎯 Signal: {emoji.get(data['signal'])} {data['signal']}
📐 Trend: {trend_emoji.get(data['trend'])} {data['trend']}

━━━━━━━━━━━━━━━━━━━━

📊 *Indikator:*
• RSI: {data['rsi']:.1f} {'(Oversold)' if data['rsi'] < 30 else '(Overbought)' if data['rsi'] > 70 else ''}
• MACD: {data['macd']:.2f} {'(Bullish)' if data['macd'] > data['macd_sig'] else '(Bearish)'}
• MA20: Rp {data['ma20']:,.0f}
• MA50: Rp {data['ma50']:,.0f}
• MA200: Rp {data['ma200']:,.0f}

━━━━━━━━━━━━━━━━━━━━

📍 Support: Rp {data['support']:,.0f}
🔴 Resistance: Rp {data['resistance']:,.0f}

🎯 TP: Rp {data['tp']:,.0f}
🛡️ SL: Rp {data['sl']:,.0f}
📐 R/R: {data['rr']:.2f}x"""

    if data['signal'] == 'BUY' and not data['tp_valid']:
        msg += "\n\n⚠️ *R/R < 1.5x - Perhatian!*"

    await update.message.reply_text(msg, parse_mode='Markdown')

    # Groq insight
    if GROQ_API_KEY:
        insight = get_groq_insight(ticker, data)
        if insight:
            await update.message.reply_text(f"💡 *Insight:*\n{insight}", parse_mode='Markdown')

async def screener(update: Update, ctx):
    await update.message.reply_text("🔍 Scanning LQ45...")
    # Simplified - just return a message
    await update.message.reply_text("📊 Fitur screener dalam pengembangan.\n\nCoba `/sinyal BBCA` untuk analisis individual.")

async def beli(update: Update, ctx):
    if len(ctx.args) < 3:
        await update.message.reply_text("Format: /beli BBCA 10 8500")
        return
    try:
        ticker, lot, price = ctx.args[0].upper(), int(ctx.args[1]), float(ctx.args[2])
        record_transaction(update.effective_user.id, ticker, 'BUY', lot, price)
        total = lot * price * 100
        await update.message.reply_text(f"✅ BUY recorded\n\n📌 {ticker}\n📊 Lot: {lot}\n💵 @ Rp {price:,.0f}\n💰 Total: Rp {total:,.0f}")
    except:
        await update.message.reply_text("❌ Format salah. Contoh: /beli BBCA 10 8500")

async def jual(update: Update, ctx):
    if len(ctx.args) < 3:
        await update.message.reply_text("Format: /jual BBCA 5 9000")
        return
    try:
        ticker, lot, price = ctx.args[0].upper(), int(ctx.args[1]), float(ctx.args[2])
        record_transaction(update.effective_user.id, ticker, 'SELL', lot, price)
        total = lot * price * 100
        await update.message.reply_text(f"✅ SELL recorded\n\n📌 {ticker}\n📊 Lot: {lot}\n💵 @ Rp {price:,.0f}\n💰 Total: Rp {total:,.0f}")
    except:
        await update.message.reply_text("❌ Format salah. Contoh: /jual BBCA 5 9000")

async def jurnal(update: Update, ctx):
    history = get_history(update.effective_user.id)
    if not history:
        await update.message.reply_text("📭 Belum ada transaksi")
        return

    msg = "📖 *JURNAL*\n\n"
    for h in history[:10]:
        emoji = "🟢" if h[1] == 'BUY' else "🔴"
        msg += f"{emoji} {h[1]} {h[0]} Lot:{h[2]} @ Rp {h[3]:,.0f}\n"
        msg += f"   🕐 {h[4]}\n"

    await update.message.reply_text(msg, parse_mode='Markdown')

async def posisi(update: Update, ctx):
    positions = get_positions(update.effective_user.id)
    if not positions:
        await update.message.reply_text("📭 Tidak ada posisi terbuka")
        return

    msg = "📊 *POSISI TERBUKA*\n\n"
    for p in positions:
        ticker, lot, avg = p
        # Get current price
        data = get_data(ticker)
        if data:
            cur = data['Close'].iloc[-1]
            pnl = (cur - avg) * lot * 100
            pnl_pct = ((cur - avg) / avg) * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            sign = "+" if pnl >= 0 else ""
            msg += f"📌 *{ticker}*\n"
            msg += f"   Lot: {lot} | Avg: Rp {avg:,.0f}\n"
            msg += f"   Current: Rp {cur:,.0f}\n"
            msg += f"   {emoji} P/L: {sign}Rp {pnl:,.0f} ({sign}{pnl_pct:.1f}%)\n\n"
        else:
            msg += f"📌 {ticker} | Lot: {lot} | Avg: Rp {avg:,.0f}\n"

    await update.message.reply_text(msg, parse_mode='Markdown')

async def average(update: Update, ctx):
    if len(ctx.args) < 4:
        await update.message.reply_text("Format: /average 10 8500 5 8700")
        return
    try:
        lot1, price1, lot2, price2 = float(ctx.args[0]), float(ctx.args[1]), float(ctx.args[2]), float(ctx.args[3])
        total_lot = lot1 + lot2
        avg = ((lot1 * price1) + (lot2 * price2)) / total_lot
        await update.message.reply_text(f"""🧮 *AVERAGE PRICE*

Lot1: {int(lot1)} @ Rp {price1:,.0f}
Lot2: {int(lot2)} @ Rp {price2:,.0f}

✅ Total Lot: {int(total_lot)}
💵 Average: *Rp {avg:,.0f}*""", parse_mode='Markdown')
    except:
        await update.message.reply_text("❌ Format salah. Contoh: /average 10 8500 5 8700")

async def cutloss(update: Update, ctx):
    if len(ctx.args) < 3:
        await update.message.reply_text("Format: /cutloss 8500 8000 10")
        return
    try:
        entry, current, lot = float(ctx.args[0]), float(ctx.args[1]), int(ctx.args[2])
        loss_pct = ((current - entry) / entry) * 100
        loss_total = (entry - current) * lot * 100
        cut_price = entry * 0.95
        await update.message.reply_text(f"""🔴 *CUT LOSS ANALYSIS*

Entry: Rp {entry:,.0f}
Current: Rp {current:,.0f}
Lot: {lot}

📉 Loss: {loss_pct:.2f}%
💰 Total Loss: Rp {loss_total:,.0f}

🛡️ Cut @ Rp {cut_price:,.0f} (-5%)""", parse_mode='Markdown')
    except:
        await update.message.reply_text("❌ Format salah. Contoh: /cutloss 8500 8000 10")

async def backtest(update: Update, ctx):
    await update.message.reply_text("📊 Fitur backtest dalam pengembangan.\n\nGunakan `/sinyal <ticker>` untuk analisis.")

# ============================================
# MAIN
# ============================================
def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set!")
        return

    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("sinyal", sinyal))
    app.add_handler(CommandHandler("screener", screener))
    app.add_handler(CommandHandler("backtest", backtest))
    app.add_handler(CommandHandler("beli", beli))
    app.add_handler(CommandHandler("jual", jual))
    app.add_handler(CommandHandler("jurnal", jurnal))
    app.add_handler(CommandHandler("posisi", posisi))
    app.add_handler(CommandHandler("average", average))
    app.add_handler(CommandHandler("cutloss", cutloss))

    print("🤖 Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
