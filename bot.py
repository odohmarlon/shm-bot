"""
Telegram Stock Analysis and Trading Journal Bot
Main bot file with all command handlers
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from config import TELEGRAM_BOT_TOKEN
from database import (
    ensure_user, record_transaction, get_positions,
    get_transaction_history, calculate_statistics, save_signal
)
from analysis import (
    analyze_stock, screener_lq45, backtest_signals,
    calculate_average_price, calculate_cut_loss, get_stock_data,
    get_ai_insight, is_groq_configured
)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============================================
# HELPER FUNCTIONS
# ============================================

def format_currency(amount: float) -> str:
    """Format number as Indonesian Rupiah."""
    return f"Rp {amount:,.0f}"


def format_percentage(pct: float) -> str:
    """Format percentage with sign."""
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def format_stock_change(change: float) -> str:
    """Format stock change with emoji."""
    emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
    return f"{emoji} {format_percentage(change)}"


# ============================================
# COMMAND HANDLERS
# ============================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    ensure_user(user.id, user.username)

    welcome_message = f"""
🤖 *Selamat datang di Stock Analysis Bot!*

Bot ini menyediakan analisis teknis untuk saham Indonesia (IDX) dan journal trading.

*📊 Perintah Analisis:*
• `/sinyal <ticker>` - Analisis teknikal lengkap
• `/screener` - Scan saham LQ45 bullish
• `/backtest <ticker> <hari>` - Test akurasi sinyal

*📝 Perintah Journal:*
• `/beli <ticker> <lot> <harga>` - Catat pembelian
• `/jual <ticker> <lot> <harga>` - Catat penjualan
• `/jurnal` - Lihat history transaksi
• `/posisi` - Lihat posisi terbuka
• `/statistik` - Statistik trading

*🧮 Perintah Kalkulator:*
• `/average <ticker> <lot1> <harga1> <lot2> <harga2>` - Hitung rata-rata
• `/cutloss <ticker> <harga> <lot>` - Analisis cut loss

*Contoh:* `/sinyal BBCA`
"""
    await update.message.reply_text(welcome_message, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await start_command(update, context)


async def sinyal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /sinyal <ticker> command.
    Performs complete technical analysis on the given ticker.
    """
    if not context.args:
        await update.message.reply_text(
            "❌ *Format salah!* Gunakan: `/sinyal <ticker>`\n"
            "Contoh: `/sinyal BBCA`",
            parse_mode='Markdown'
        )
        return

    ticker = context.args[0].upper().replace('.JK', '')
    await update.message.reply_text(f"🔍 *Menganalisis {ticker}...*", parse_mode='Markdown')

    try:
        analysis = analyze_stock(ticker)

        if analysis is None:
            await update.message.reply_text(
                f"❌ Tidak dapat mengambil data untuk *{ticker}*. "
                "Pastikan ticker benar dan coba lagi.",
                parse_mode='Markdown'
            )
            return

        # Signal emoji and color
        signal_emoji = {
            'BUY': '🟢 BUY',
            'SELL': '🔴 SELL',
            'HOLD': '🟡 HOLD'
        }

        signal_text = signal_emoji.get(analysis['signal'], analysis['signal'])
        confidence_emoji = {
            'HIGH': '⭐⭐⭐',
            'MEDIUM': '⭐⭐',
            'LOW': '⭐'
        }

        # Trend emoji
        trend_emoji = {
            'STRONG_UPTREND': '📈💪',
            'UPTREND': '📈',
            'SIDEWAYS': '➡️',
            'DOWNTREND': '📉',
            'STRONG_DOWNTREND': '📉😰',
            'INSUFFICIENT_DATA': '❓'
        }

        # Build message
        message = f"""
📊 *ANALISIS TEKNIS - {ticker}*

💰 *Harga Saat Ini:* Rp {analysis['price']:,.0f}
{format_stock_change(analysis['change'])}
📅 *Tanggal:* {analysis['date']}

━━━━━━━━━━━━━━━━━━━━

🎯 *SINAL:* {signal_text} {confidence_emoji.get(analysis['confidence'], '')}
📐 *Trend:* {trend_emoji.get(analysis['trend'], '')} {analysis['trend']}
📈 *Confidence:* {analysis['confidence']}

━━━━━━━━━━━━━━━━━━━━

📊 *INDIKATOR TEKNIS:*

*RSI (14):* {analysis['rsi']:.1f}
   └ {'🟢 Oversold' if analysis['rsi'] < 30 else '🔴 Overbought' if analysis['rsi'] > 70 else '🟡 Netral'}

*MACD:*
   └ Line: {analysis['macd']:.2f}
   └ Signal: {analysis['macd_signal']:.2f}
   └ Histogram: {analysis['macd_histogram']:.2f}
   └ {'🟢 Bullish' if analysis['macd_histogram'] > 0 else '🔴 Bearish'}

*Moving Averages:*
   └ MA20: Rp {analysis['ma20']:,.0f}
   └ MA50: Rp {analysis['ma50']:,.0f}
   └ MA200: Rp {analysis['ma200']:,.0f}

*Bollinger Bands:*
   └ Upper: Rp {analysis['bb_upper']:,.0f}
   └ Middle: Rp {analysis['bb_middle']:,.0f}
   └ Lower: Rp {analysis['bb_lower']:,.0f}

*Stochastic:*
   └ %K: {analysis['stoch_k']:.1f}
   └ %D: {analysis['stoch_d']:.1f}

*ATR:* {analysis['atr']:.2f}
*Volume:* {analysis['volume']:,.0f}

━━━━━━━━━━━━━━━━━━━━

📍 *SUPPORT & RESISTANCE:*

   🟢 Support: Rp {analysis['support']:,.0f}
   🔴 Resistance: Rp {analysis['resistance']:,.0f}

━━━━━━━━━━━━━━━━━━━━

📋 *MANAJEMEN RISIKO:*

   🎯 Take Profit (TP): Rp {analysis['tp']:,.0f}
   🛡️ Stop Loss (SL): Rp {analysis['sl']:,.0f}
   📐 Risk/Reward: {analysis['rr_ratio']:.2f}x
"""

        # CRITICAL: Add TP validation warning
        if analysis['signal'] == 'BUY':
            if analysis['tp_valid']:
                message += f"""
✅ *TP VALID* - R/R ratio ({analysis['rr_ratio']:.2f}x) ≥ 1.5x
   TP ini memenuhi kriteria Risk/Reward minimum.
"""
            else:
                message += f"""
⚠️ *⚠️ PERHATIAN: TP TIDAK VALID ⚠️*
   R/R ratio ({analysis['rr_ratio']:.2f}x) < 1.5x
   ❌ TP ini TIDAK memenuhi kriteria Risk/Reward minimum!
   Disarankan untuk TIDAK ENTRY pada kondisi ini
   atau tunggu harga pullback ke support.
"""

        # ============================================
        # GROQ AI INSIGHT
        # ============================================
        if is_groq_configured():
            message += "\n━━━━━━━━━━━━━━━━━━━━\n"
            message += "\n🤖 *AI INSIGHT:*\n"
            message += "_Sedang generate insight..._"

            await update.message.reply_text(message, parse_mode='Markdown')

            # Get AI insight
            ai_insight = get_ai_insight(ticker, analysis)

            if ai_insight:
                # Send AI insight as separate message
                insight_message = f"💡 *Insight untuk {ticker}:*\n\n{ai_insight}\n\n_🔧 Generated by Groq AI_"
                await update.message.reply_text(insight_message, parse_mode='Markdown')
            else:
                # Fallback if Groq fails
                await update.message.reply_text(
                    "⚠️ Gagal mendapatkan AI insight. Coba lagi nanti.",
                    parse_mode='Markdown'
                )
        else:
            # No Groq configured - just send technical analysis
            message += "\n\n_💡 Hint: Tambahkan GROQ_API_KEY di config.py untuk AI insight!_"
            await update.message.reply_text(message, parse_mode='Markdown')

        # Save signal to database (always save regardless of Groq)
        user = update.effective_user
        ensure_user(user.id, user.username)
        save_signal(user.id, ticker, analysis['signal'], analysis['price'],
                   analysis['rsi'], analysis['trend'])

    except Exception as e:
        logger.error(f"Error in sinyal_command: {e}")
        await update.message.reply_text(
            f"❌ Terjadi kesalahan: {str(e)}",
            parse_mode='Markdown'
        )


async def screener_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /screener command.
    Scans LQ45 stocks for bullish trends.
    """
    await update.message.reply_text(
        "🔍 *Scanning saham LQ45 bullish...*\n"
        "Ini mungkin memakan waktu beberapa saat...",
        parse_mode='Markdown'
    )

    try:
        results = screener_lq45()

        if not results:
            await update.message.reply_text(
                "❌ Tidak ada saham bullish ditemukan.",
                parse_mode='Markdown'
            )
            return

        message = "📊 *SCREENER SAHAM BULLISH*\n\n"
        message += f"Ditemukan {len(results)} saham dengan trend bullish:\n\n"

        for stock in results:
            signal_emoji = "🟢" if stock['signal'] == 'BUY' else "🟡"
            message += f"{signal_emoji} *{stock['ticker']}*\n"
            message += f"   💰 Rp {stock['price']:,.0f} {format_stock_change(stock['change'])}\n"
            message += f"   📐 {stock['trend']}\n"
            message += f"   📊 RSI: {stock['rsi']:.1f}\n\n"

        message += "\n_Analisis dilakukan pada timeframe 1 bulan_"

        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in screener_command: {e}")
        await update.message.reply_text(
            f"❌ Terjadi kesalahan: {str(e)}",
            parse_mode='Markdown'
        )


async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /backtest <ticker> <period> command.
    Tests historical accuracy of buy/sell signals.
    """
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Gunakan: `/backtest <ticker> <jumlah_hari>`\n"
            "Contoh: `/backtest BBCA 60`",
            parse_mode='Markdown'
        )
        return

    ticker = context.args[0].upper().replace('.JK', '')

    try:
        days = int(context.args[1])
        if days < 30:
            days = 30
        elif days > 365:
            days = 365
    except ValueError:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Jumlah hari harus angka.\n"
            "Contoh: `/backtest BBCA 60`",
            parse_mode='Markdown'
        )
        return

    await update.message.reply_text(
        f"🔬 *Backtest {ticker} untuk {days} hari...*",
        parse_mode='Markdown'
    )

    try:
        result = backtest_signals(ticker, days)

        if result is None:
            await update.message.reply_text(
                f"❌ Data tidak cukup untuk backtest *{ticker}*.",
                parse_mode='Markdown'
            )
            return

        win_emoji = "🟢" if result['win_rate'] >= 50 else "🔴"

        message = f"""
📊 *BACKTEST RESULTS - {ticker}*

📅 *Periode:* {result['period_days']} hari
📈 *Total Sinyal:* {result['total_signals']}

━━━━━━━━━━━━━━━━━━━━

🎯 *WIN RATE:* {win_emoji} {result['win_rate']:.1f}%

   ✅ Wins: {result['wins']}
   ❌ Losses: {result['losses']}

📊 *Rata-rata:* +{result['avg_win_pct']:.2f}% / -{result['avg_loss_pct']:.2f}%
"""

        if result['signals']:
            message += "\n📋 *Sample Sinyal Terakhir:*\n\n"
            for sig in result['signals'][-5:]:
                status = "✅" if sig['correct'] else "❌"
                sig_type = "🟢" if sig['signal'] == 'BUY' else "🔴"
                message += f"{status} {sig_type} {sig['signal']} @ Rp {sig['price']:,.0f}\n"
                message += f"   ➡️ Setelah 5 hari: Rp {sig['future_price']:,.0f} ({sig['change']:+.2f}%)\n\n"

        message += "_*Catatan: Backtest berdasarkan data historis dan tidak menjamin hasil di masa depan_"

        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in backtest_command: {e}")
        await update.message.reply_text(
            f"❌ Terjadi kesalahan: {str(e)}",
            parse_mode='Markdown'
        )


async def beli_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /beli <ticker> <lot> <price> command.
    Records a BUY transaction.
    """
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Gunakan: `/beli <ticker> <lot> <harga>`\n"
            "Contoh: `/beli BBCA 10 8500`",
            parse_mode='Markdown'
        )
        return

    ticker = context.args[0].upper().replace('.JK', '')

    try:
        lot = int(context.args[1])
        price = float(context.args[2])

        if lot <= 0 or price <= 0:
            raise ValueError()

    except ValueError:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Lot dan harga harus angka positif.\n"
            "Contoh: `/beli BBCA 10 8500`",
            parse_mode='Markdown'
        )
        return

    user = update.effective_user
    ensure_user(user.id, user.username)

    success = record_transaction(user.id, ticker, 'BUY', lot, price)

    if success:
        total_value = lot * price * 100  # 100 shares per lot
        message = f"""
✅ *TRANSAKSI BERHASIL DICATAT*

🟢 *BUY ORDER*

📌 *Ticker:* {ticker}
📊 *Lot:* {lot}
💵 *Harga:* Rp {price:,.0f}
💰 *Total:* Rp {total_value:,.0f}
"""
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        await update.message.reply_text(
            "❌ *Gagal mencatat transaksi.*\n"
            "Silakan coba lagi.",
            parse_mode='Markdown'
        )


async def jual_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /jual <ticker> <lot> <price> command.
    Records a SELL transaction.
    """
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Gunakan: `/jual <ticker> <lot> <harga>`\n"
            "Contoh: `/jual BBCA 5 9000`",
            parse_mode='Markdown'
        )
        return

    ticker = context.args[0].upper().replace('.JK', '')

    try:
        lot = int(context.args[1])
        price = float(context.args[2])

        if lot <= 0 or price <= 0:
            raise ValueError()

    except ValueError:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Lot dan harga harus angka positif.\n"
            "Contoh: `/jual BBCA 5 9000`",
            parse_mode='Markdown'
        )
        return

    user = update.effective_user
    ensure_user(user.id, user.username)

    success = record_transaction(user.id, ticker, 'SELL', lot, price)

    if success:
        total_value = lot * price * 100
        message = f"""
✅ *TRANSAKSI BERHASIL DICATAT*

🔴 *SELL ORDER*

📌 *Ticker:* {ticker}
📊 *Lot:* {lot}
💵 *Harga:* Rp {price:,.0f}
💰 *Total:* Rp {total_value:,.0f}
"""
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        await update.message.reply_text(
            "❌ *Gagal mencatat transaksi.*\n"
            "Pastikan Anda memiliki posisi yang cukup.",
            parse_mode='Markdown'
        )


async def jurnal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /jurnal command.
    Displays complete transaction history.
    """
    user = update.effective_user
    ensure_user(user.id, user.username)

    transactions = get_transaction_history(user.id, limit=20)

    if not transactions:
        await update.message.reply_text(
            "📭 *Belum ada transaksi.*\n"
            "Gunakan `/beli` atau `/jual` untuk mencatat transaksi.",
            parse_mode='Markdown'
        )
        return

    message = "📖 *JURNAL TRANSAKSI*\n\n"

    for i, tx in enumerate(transactions, 1):
        type_emoji = "🟢" if tx['type'] == 'BUY' else "🔴"
        total = tx['lot'] * tx['price'] * 100

        # Format timestamp
        try:
            ts = datetime.strptime(tx['timestamp'], '%Y-%m-%d %H:%M:%S')
            date_str = ts.strftime('%d/%m %H:%M')
        except:
            date_str = tx['timestamp']

        message += f"{i}. {type_emoji} *{tx['type']}* {tx['ticker']}\n"
        message += f"   📊 Lot: {tx['lot']} @ Rp {tx['price']:,.0f}\n"
        message += f"   💰 Total: Rp {total:,.0f}\n"
        message += f"   🕐 {date_str}\n\n"

    message += f"_Menampilkan {len(transactions)} transaksi terakhir_"

    await update.message.reply_text(message, parse_mode='Markdown')


async def posisi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /posisi command.
    Displays open positions with unrealized P&L.
    """
    user = update.effective_user
    ensure_user(user.id, user.username)

    positions = get_positions(user.id)

    if not positions:
        await update.message.reply_text(
            "📭 *Tidak ada posisi terbuka.*\n"
            "Gunakan `/beli` untuk membuka posisi.",
            parse_mode='Markdown'
        )
        return

    message = "📊 *POSISI TERBUKA*\n\n"

    total_unrealized_pnl = 0

    for pos in positions:
        ticker = pos['ticker']

        # Get current price
        df = get_stock_data(ticker, period="5d")
        if df is not None and not df.empty:
            current_price = df['Close'].iloc[-1]
        else:
            current_price = pos['average_price']  # Fallback

        avg_price = pos['average_price']
        lot = pos['total_lot']

        # Calculate unrealized P&L
        pnl_per_share = current_price - avg_price
        total_pnl = pnl_per_share * lot * 100
        pnl_pct = ((current_price - avg_price) / avg_price) * 100

        total_unrealized_pnl += total_pnl

        # Emoji based on P&L
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        pnl_sign = "+" if total_pnl >= 0 else ""

        message += f"📌 *{ticker}*\n"
        message += f"   📊 Lot: {lot}\n"
        message += f"   💵 Avg Price: Rp {avg_price:,.0f}\n"
        message += f"   💰 Current: Rp {current_price:,.0f}\n"
        message += f"   {pnl_emoji} Unrealized P&L: {pnl_sign}Rp {total_pnl:,.0f} ({pnl_sign}{pnl_pct:.2f}%)\n\n"

    # Total portfolio P&L
    total_pnl_emoji = "🟢" if total_unrealized_pnl >= 0 else "🔴"
    total_pnl_sign = "+" if total_unrealized_pnl >= 0 else ""
    message += f"━━━━━━━━━━━━━━━━━━━━\n"
    message += f"{total_pnl_emoji} *Total Unrealized P&L:* {total_pnl_sign}Rp {total_unrealized_pnl:,.0f}"
    message += f"\n\n_⚠️ P&L berdasarkan harga terakhir dari Yahoo Finance_"

    await update.message.reply_text(message, parse_mode='Markdown')


async def statistik_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /statistik command.
    Calculates and displays overall trading statistics.
    """
    user = update.effective_user
    ensure_user(user.id, user.username)

    stats = calculate_statistics(user.id)

    message = f"""
📊 *STATISTIK TRADING*

━━━━━━━━━━━━━━━━━━━━

📈 *Total Transaksi:* {stats['total_transactions']}
📉 *Total Sell Trades:* {stats['total_sell_trades']}

━━━━━━━━━━━━━━━━━━━━

🎯 *WIN RATE:* {stats['win_rate']:.1f}%

   ✅ Winning Trades: {stats['winning_trades']}
   ❌ Losing Trades: {stats['total_sell_trades'] - stats['winning_trades']}

━━━━━━━━━━━━━━━━━━━━

💰 *Total Realized P&L:* {'+' if stats['total_realized_pnl'] >= 0 else ''}Rp {stats['total_realized_pnl']:,.0f}

━━━━━━━━━━━━━━━━━━━━

_⚠️ Statistik berdasarkan transaksi yang sudah ditutup_
"""

    await update.message.reply_text(message, parse_mode='Markdown')


async def average_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /average <ticker> <lot1> <price1> <lot2> <price2> command.
    Calculates new average price after multiple buys.
    """
    if len(context.args) < 5:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Gunakan: `/average <ticker> <lot1> <harga1> <lot2> <harga2>`\n"
            "Contoh: `/average BBCA 10 8500 5 8700`",
            parse_mode='Markdown'
        )
        return

    ticker = context.args[0].upper().replace('.JK', '')

    try:
        lot1 = int(context.args[1])
        price1 = float(context.args[2])
        lot2 = int(context.args[3])
        price2 = float(context.args[4])

        if lot1 <= 0 or lot2 <= 0 or price1 <= 0 or price2 <= 0:
            raise ValueError()

    except ValueError:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Lot dan harga harus angka positif.",
            parse_mode='Markdown'
        )
        return

    result = calculate_average_price(lot1, price1, lot2, price2)

    message = f"""
🧮 *AVERAGE PRICE CALCULATOR*

📌 *Ticker:* {ticker}

━━━━━━━━━━━━━━━━━━━━

*Pembelian 1:*
   📊 Lot: {result['lot1']}
   💵 Harga: Rp {result['price1']:,.0f}

*Pembelian 2:*
   📊 Lot: {result['lot2']}
   💵 Harga: Rp {result['price2']:,.0f}

━━━━━━━━━━━━━━━━━━━━

✅ *HASIL:*

   📊 Total Lot: {result['total_lot']}
   💵 Average Price: *Rp {result['average_price']:,.0f}*
   💰 Total Value: Rp {result['total_value']:,.0f}

━━━━━━━━━━━━━━━━━━━━

💡 *Rata-rata harga beli Anda adalah Rp {result['average_price']:,.0f}*
"""

    await update.message.reply_text(message, parse_mode='Markdown')


async def cutloss_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /cutloss <ticker> <price> <lot> command.
    Provides cut loss analysis based on input parameters.
    """
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Gunakan: `/cutloss <ticker> <harga_saat_ini> <lot>`\n"
            "Contoh: `/cutloss BBCA 8000 10`",
            parse_mode='Markdown'
        )
        return

    ticker = context.args[0].upper().replace('.JK', '')

    try:
        current_price = float(context.args[1])
        lot = int(context.args[2])

        if current_price <= 0 or lot <= 0:
            raise ValueError()

    except ValueError:
        await update.message.reply_text(
            "❌ *Format salah!*\n"
            "Harga dan lot harus angka positif.",
            parse_mode='Markdown'
        )
        return

    # Get entry price from database or ask user
    user = update.effective_user
    positions = get_positions(user.id)

    # Try to find position for this ticker
    position = None
    for pos in positions:
        if pos['ticker'] == ticker:
            position = pos
            break

    if not position:
        await update.message.reply_text(
            f"📌 *POSISI TIDAK DITEMUKAN*\n\n"
            f"Tidak ada posisi terbuka untuk *{ticker}*.\n"
            f"Gunakan `/cutloss {ticker} <harga_entry> <lot>` untuk analisis manual.",
            parse_mode='Markdown'
        )
        return

    entry_price = position['average_price']
    result = calculate_cut_loss(current_price, entry_price, lot)

    loss_emoji = "🟢" if result['total_loss'] >= 0 else "🔴"
    loss_sign = "+" if result['total_loss'] >= 0 else ""

    message = f"""
🔴 *CUT LOSS ANALYSIS*

📌 *Ticker:* {ticker}

━━━━━━━━━━━━━━━━━━━━

📊 *Posisi Saat Ini:*
   📊 Lot: {result['lot']}
   💵 Entry Price: Rp {result['entry_price']:,.0f}
   💰 Current Price: Rp {result['current_price']:,.0f}

━━━━━━━━━━━━━━━━━━━━

📉 *ANALISIS KERUGIAN:*

   💸 Loss per share: {loss_sign}Rp {result['loss_per_share']:,.0f}
   📐 Loss: {result['loss_percentage']:+.2f}%
   💰 Total Loss: {loss_emoji} {loss_sign}Rp {abs(result['total_loss']):,.0f}

━━━━━━━━━━━━━━━━━━━━

🛡️ *REKOMENDASI:*

   ⚠️ Cut loss di: *Rp {result['price_to_cut']:,.0f}*
   📐 Target loss: {result['target_loss_pct']}%

━━━━━━━━━━━━━━━━━━━━

💡 *Saran:*
   • Jika loss sudah {result['target_loss_pct']}%, pertimbangkan untuk cut loss
   • Cut loss melindungi modal dari kerugian lebih besar
   • Evaluasi apakah ada sinyal reversal sebelum cut

_⚠️*Ini adalah analisis, bukan saran keuangan*_
"""

    await update.message.reply_text(message, parse_mode='Markdown')


# ============================================
# ERROR HANDLER
# ============================================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")


# ============================================
# MAIN FUNCTION
# ============================================

def main():
    """Run the bot."""
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("sinyal", sinyal_command))
    application.add_handler(CommandHandler("screener", screener_command))
    application.add_handler(CommandHandler("backtest", backtest_command))
    application.add_handler(CommandHandler("beli", beli_command))
    application.add_handler(CommandHandler("jual", jual_command))
    application.add_handler(CommandHandler("jurnal", jurnal_command))
    application.add_handler(CommandHandler("posisi", posisi_command))
    application.add_handler(CommandHandler("statistik", statistik_command))
    application.add_handler(CommandHandler("average", average_command))
    application.add_handler(CommandHandler("cutloss", cutloss_command))

    # Add error handler
    application.add_error_handler(error_handler)

    # Run the bot
    print("🤖 Bot started! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
