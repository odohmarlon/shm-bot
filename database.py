"""
Database module for Trading Journal Bot
Handles SQLite database operations for trading journal
"""

import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from config import DATABASE_PATH


def init_database():
    """Initialize the SQLite database with required tables."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Users table - stores user information
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Transactions table - stores all buy/sell transactions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            transaction_type TEXT NOT NULL CHECK(transaction_type IN ('BUY', 'SELL')),
            lot INTEGER NOT NULL,
            price REAL NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # Positions table - stores current open positions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL UNIQUE,
            total_lot INTEGER NOT NULL,
            average_price REAL NOT NULL,
            opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # Signals table - stores historical buy/sell signals for backtesting
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            signal_type TEXT NOT NULL CHECK(signal_type IN ('BUY', 'SELL')),
            price REAL NOT NULL,
            rsi REAL,
            macd_signal TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    conn.commit()
    conn.close()


def ensure_user(user_id: int, username: str = None) -> None:
    """Ensure user exists in database."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
        (user_id, username)
    )
    conn.commit()
    conn.close()


def record_transaction(user_id: int, ticker: str, transaction_type: str,
                       lot: int, price: float) -> bool:
    """
    Record a buy or sell transaction.
    Updates positions table accordingly.
    """
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    try:
        # Insert transaction record
        cursor.execute(
            "INSERT INTO transactions (user_id, ticker, transaction_type, lot, price) VALUES (?, ?, ?, ?, ?)",
            (user_id, ticker.upper(), transaction_type.upper(), lot, price)
        )

        # Update positions based on transaction type
        if transaction_type.upper() == 'BUY':
            # Calculate new average price for BUY
            cursor.execute(
                "SELECT total_lot, average_price FROM positions WHERE user_id = ? AND ticker = ?",
                (user_id, ticker.upper())
            )
            result = cursor.fetchone()

            if result:
                old_lot, old_avg = result
                new_lot = old_lot + lot
                # New average price = ((old_lot * old_avg) + (new_lot * price)) / new_lot
                new_avg = ((old_lot * old_avg) + (lot * price)) / new_lot
                cursor.execute(
                    "UPDATE positions SET total_lot = ?, average_price = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND ticker = ?",
                    (new_lot, new_avg, user_id, ticker.upper())
                )
            else:
                # New position
                cursor.execute(
                    "INSERT INTO positions (user_id, ticker, total_lot, average_price) VALUES (?, ?, ?, ?)",
                    (user_id, ticker.upper(), lot, price)
                )

        elif transaction_type.upper() == 'SELL':
            cursor.execute(
                "SELECT total_lot, average_price FROM positions WHERE user_id = ? AND ticker = ?",
                (user_id, ticker.upper())
            )
            result = cursor.fetchone()

            if result:
                old_lot, avg_price = result
                new_lot = old_lot - lot

                if new_lot <= 0:
                    # Close entire position
                    cursor.execute(
                        "DELETE FROM positions WHERE user_id = ? AND ticker = ?",
                        (user_id, ticker.upper())
                    )
                else:
                    # Update remaining position (average price stays the same for sells)
                    cursor.execute(
                        "UPDATE positions SET total_lot = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND ticker = ?",
                        (new_lot, user_id, ticker.upper())
                    )

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        conn.rollback()
        conn.close()
        return False


def get_positions(user_id: int) -> List[Dict]:
    """Get all open positions for a user."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT ticker, total_lot, average_price, opened_at FROM positions WHERE user_id = ? ORDER BY ticker",
        (user_id,)
    )
    positions = [
        {
            "ticker": row[0],
            "total_lot": row[1],
            "average_price": row[2],
            "opened_at": row[3]
        }
        for row in cursor.fetchall()
    ]
    conn.close()
    return positions


def get_transaction_history(user_id: int, limit: int = 50) -> List[Dict]:
    """Get transaction history for a user."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT ticker, transaction_type, lot, price, timestamp
           FROM transactions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?""",
        (user_id, limit)
    )
    transactions = [
        {
            "ticker": row[0],
            "type": row[1],
            "lot": row[2],
            "price": row[3],
            "timestamp": row[4]
        }
        for row in cursor.fetchall()
    ]
    conn.close()
    return transactions


def calculate_statistics(user_id: int) -> Dict:
    """Calculate trading statistics for a user."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Get all sell transactions (realized P&L)
    cursor.execute(
        """SELECT ticker, lot, price, timestamp FROM transactions
           WHERE user_id = ? AND transaction_type = 'SELL' ORDER BY timestamp""",
        (user_id,)
    )
    sells = cursor.fetchall()

    # Get all buy transactions
    cursor.execute(
        """SELECT ticker, lot, price, timestamp FROM transactions
           WHERE user_id = ? AND transaction_type = 'BUY' ORDER BY timestamp""",
        (user_id,)
    )
    buys = cursor.fetchall()

    # Calculate total realized P&L and win rate
    total_trades = len(sells)
    winning_trades = 0
    total_realized_pnl = 0.0

    for sell in sells:
        ticker, lot, sell_price, _ = sell
        # Find matching buy price for this ticker
        # Simplified: just use average buy price for the same ticker
        cursor.execute(
            """SELECT average_price FROM positions
               WHERE user_id = ? AND ticker = ?""",
            (user_id, ticker)
        )
        # For now, calculate based on all buys of this ticker
        cursor.execute(
            """SELECT AVG(price) FROM transactions
               WHERE user_id = ? AND ticker = ? AND transaction_type = 'BUY'""",
            (user_id, ticker)
        )
        avg_buy_result = cursor.fetchone()

        if avg_buy_result and avg_buy_result[0]:
            avg_buy = avg_buy_result[0]
            pnl = (sell_price - avg_buy) * lot * 100  # 100 shares per lot
            total_realized_pnl += pnl
            if pnl > 0:
                winning_trades += 1

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

    # Get total trades count
    cursor.execute(
        "SELECT COUNT(*) FROM transactions WHERE user_id = ?",
        (user_id,)
    )
    total_transactions = cursor.fetchone()[0]

    conn.close()

    return {
        "total_transactions": total_transactions,
        "total_sell_trades": total_trades,
        "winning_trades": winning_trades,
        "win_rate": win_rate,
        "total_realized_pnl": total_realized_pnl
    }


def save_signal(user_id: int, ticker: str, signal_type: str, price: float,
                rsi: float = None, macd_signal: str = None) -> None:
    """Save a trading signal for backtesting purposes."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO signals (user_id, ticker, signal_type, price, rsi, macd_signal)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, ticker.upper(), signal_type.upper(), price, rsi, macd_signal)
    )
    conn.commit()
    conn.close()


def get_signals(user_id: int, ticker: str, days: int = 60) -> List[Dict]:
    """Get historical signals for backtesting."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT signal_type, price, rsi, macd_signal, timestamp
           FROM signals WHERE user_id = ? AND ticker = ?
           AND timestamp >= datetime('now', '-' || ? || ' days')
           ORDER BY timestamp""",
        (user_id, ticker.upper(), days)
    )
    signals = [
        {
            "type": row[0],
            "price": row[1],
            "rsi": row[2],
            "macd_signal": row[3],
            "timestamp": row[4]
        }
        for row in cursor.fetchall()
    ]
    conn.close()
    return signals


# Initialize database on module import
init_database()
