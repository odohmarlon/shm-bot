"""
Technical Analysis module for Indonesian Stocks
Implements 8 standard indicators and signal generation
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional, List
from config import JK_SUFFIX, DEFAULT_RSI_PERIOD, DEFAULT_MACD_FAST, DEFAULT_MACD_SLOW, DEFAULT_MACD_SIGNAL
from config import DEFAULT_MA_SHORT, DEFAULT_MA_LONG, DEFAULT_MA_TREND, DEFAULT_BB_PERIOD, DEFAULT_BB_STD


def get_stock_data(ticker: str, period: str = "3mo") -> Optional[pd.DataFrame]:
    """
    Fetch stock data from Yahoo Finance for Indonesian stocks.
    Appends .JK suffix for Indonesian Exchange.
    """
    try:
        full_ticker = f"{ticker.upper().replace('.JK', '')}{JK_SUFFIX}"
        stock = yf.Ticker(full_ticker)
        df = stock.history(period=period)
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        return None


def calculate_rsi(prices: pd.Series, period: int = DEFAULT_RSI_PERIOD) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI).
    RSI > 70 = Overbought (potential sell signal)
    RSI < 30 = Oversold (potential buy signal)
    """
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(prices: pd.Series, fast: int = DEFAULT_MACD_FAST,
                   slow: int = DEFAULT_MACD_SLOW, signal: int = DEFAULT_MACD_SIGNAL
                   ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate MACD (Moving Average Convergence Divergence).
    Returns: (MACD line, Signal line, Histogram)
    Buy signal: MACD crosses above signal line
    Sell signal: MACD crosses below signal line
    """
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_moving_averages(prices: pd.Series) -> Dict[str, pd.Series]:
    """
    Calculate Simple Moving Averages (SMA).
    MA20, MA50, MA200 for trend determination.
    """
    return {
        "ma20": prices.rolling(window=DEFAULT_MA_SHORT).mean(),
        "ma50": prices.rolling(window=DEFAULT_MA_LONG).mean(),
        "ma200": prices.rolling(window=DEFAULT_MA_TREND).mean()
    }


def calculate_bollinger_bands(prices: pd.Series, period: int = DEFAULT_BB_PERIOD,
                              std_dev: int = DEFAULT_BB_STD
                              ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate Bollinger Bands.
    Middle = 20-day SMA
    Upper = Middle + (2 * std dev)
    Lower = Middle - (2 * std dev)
    Price near lower band = potential buy (oversold)
    Price near upper band = potential sell (overbought)
    """
    middle = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    return upper, middle, lower


def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                         k_period: int = 14, d_period: int = 3
                         ) -> Tuple[pd.Series, pd.Series]:
    """
    Calculate Stochastic Oscillator.
    %K line and %D line.
    %K > 80 = Overbought
    %K < 20 = Oversold
    """
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k_line = 100 * (close - lowest_low) / (highest_high - lowest_low)
    d_line = k_line.rolling(window=d_period).mean()
    return k_line, d_line


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                 period: int = 14) -> pd.Series:
    """
    Calculate Average True Range (ATR).
    Used for volatility measurement and stop-loss calculation.
    """
    high_low = high - low
    high_close = np.abs(high - close.shift())
    low_close = np.abs(low - close.shift())
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr


def calculate_support_resistance(prices: pd.Series, lookback: int = 20
                                 ) -> Tuple[float, float]:
    """
    Calculate support and resistance levels using recent price action.
    Uses pivot point concept: S1, R1 levels.
    """
    recent_prices = prices.tail(lookback)
    max_price = recent_prices.max()
    min_price = recent_prices.min()
    current_price = prices.iloc[-1]

    # Simple support/resistance based on min/max with buffer
    resistance = max_price
    support = min_price

    # Refine: Find recent swing highs and lows
    if len(recent_prices) >= 5:
        # Look for local maxima/minima
        for i in range(2, len(recent_prices) - 2):
            if recent_prices.iloc[i] > recent_prices.iloc[i-1] and \
               recent_prices.iloc[i] > recent_prices.iloc[i+1]:
                resistance = min(resistance, recent_prices.iloc[i])
            if recent_prices.iloc[i] < recent_prices.iloc[i-1] and \
               recent_prices.iloc[i] < recent_prices.iloc[i+1]:
                support = max(support, recent_prices.iloc[i])

    return support, resistance


def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """
    Calculate On-Balance Volume (OBV).
    Cumulative volume indicator showing buying/selling pressure.
    """
    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    return obv


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return prices.ewm(span=period, adjust=False).mean()


def get_trend(prices: pd.Series, ma_short: pd.Series, ma_long: pd.Series,
              ma_trend: pd.Series) -> str:
    """
    Determine overall trend based on moving averages.
    Uptrend: Price > MA20 > MA50 > MA200
    Downtrend: Price < MA20 < MA50 < MA200
    Sideways: Mixed signals
    """
    current_price = prices.iloc[-1]
    ma20_val = ma_short.iloc[-1]
    ma50_val = ma_long.iloc[-1]
    ma200_val = ma_trend.iloc[-1]

    if pd.isna(ma200_val):
        return "INSUFFICIENT_DATA"

    if current_price > ma20_val > ma50_val > ma200_val:
        return "STRONG_UPTREND"
    elif current_price > ma20_val > ma50_val:
        return "UPTREND"
    elif current_price < ma20_val < ma50_val < ma200_val:
        return "STRONG_DOWNTREND"
    elif current_price < ma20_val < ma50_val:
        return "DOWNTREND"
    else:
        return "SIDEWAYS"


def generate_signal(indicators: Dict) -> Tuple[str, str]:
    """
    Generate buy/sell/hold signal based on multiple indicators.
    Returns: (signal, confidence)
    """
    score = 0
    reasons = []

    current_price = indicators['price']
    rsi = indicators['rsi']
    macd_hist = indicators['macd_histogram']
    ma20 = indicators['ma20']
    ma50 = indicators['ma50']
    bb_lower = indicators['bb_lower']
    bb_upper = indicators['bb_upper']
    stoch_k = indicators['stoch_k']
    trend = indicators['trend']

    # RSI Analysis
    if rsi < 30:
        score += 2
        reasons.append("RSI Oversold")
    elif rsi > 70:
        score -= 2
        reasons.append("RSI Overbought")
    elif rsi < 50:
        score += 1
    else:
        score -= 1

    # MACD Analysis
    if macd_hist > 0:
        score += 1
        reasons.append("MACD Bullish")
    else:
        score -= 1
        reasons.append("MACD Bearish")

    # Price vs Moving Averages
    if current_price > ma20:
        score += 1
    else:
        score -= 1

    if current_price > ma50:
        score += 1
    else:
        score -= 1

    # Bollinger Bands
    if current_price <= bb_lower * 1.02:
        score += 2
        reasons.append("Near Lower BB (Oversold)")
    elif current_price >= bb_upper * 0.98:
        score -= 2
        reasons.append("Near Upper BB (Overbought)")

    # Stochastic
    if stoch_k < 20:
        score += 1
    elif stoch_k > 80:
        score -= 1

    # Trend
    if "UPTREND" in trend:
        score += 2
        reasons.append(f"Trend: {trend}")
    elif "DOWNTREND" in trend:
        score -= 2
        reasons.append(f"Trend: {trend}")

    # Determine signal
    if score >= 4:
        signal = "BUY"
        confidence = "HIGH" if score >= 6 else "MEDIUM"
    elif score <= -4:
        signal = "SELL"
        confidence = "HIGH" if score <= -6 else "MEDIUM"
    else:
        signal = "HOLD"
        confidence = "LOW"

    return signal, confidence


def analyze_stock(ticker: str, period: str = "3mo") -> Optional[Dict]:
    """
    Perform complete technical analysis on a stock.
    Returns dictionary with all indicators and signals.
    """
    df = get_stock_data(ticker, period)
    if df is None or df.empty:
        return None

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']

    # Calculate all indicators
    rsi = calculate_rsi(close)
    macd_line, signal_line, histogram = calculate_macd(close)
    ma_dict = calculate_moving_averages(close)
    bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(close)
    stoch_k, stoch_d = calculate_stochastic(high, low, close)
    atr = calculate_atr(high, low, close)
    support, resistance = calculate_support_resistance(close)
    obv = calculate_obv(close, volume)

    # Get current values
    current_price = close.iloc[-1]
    current_rsi = rsi.iloc[-1]
    current_macd = macd_line.iloc[-1]
    current_signal = signal_line.iloc[-1]
    current_hist = histogram.iloc[-1]
    current_ma20 = ma_dict['ma20'].iloc[-1]
    current_ma50 = ma_dict['ma50'].iloc[-1]
    current_ma200 = ma_dict['ma200'].iloc[-1]
    current_bb_upper = bb_upper.iloc[-1]
    current_bb_lower = bb_lower.iloc[-1]
    current_bb_middle = bb_middle.iloc[-1]
    current_stoch_k = stoch_k.iloc[-1]
    current_stoch_d = stoch_d.iloc[-1]
    current_atr = atr.iloc[-1]

    # Get trend
    trend = get_trend(close, ma_dict['ma20'], ma_dict['ma50'], ma_dict['ma200'])

    # Prepare indicators dict for signal generation
    indicators = {
        'price': current_price,
        'rsi': current_rsi,
        'macd_line': current_macd,
        'macd_signal': current_signal,
        'macd_histogram': current_hist,
        'ma20': current_ma20,
        'ma50': current_ma50,
        'ma200': current_ma200,
        'bb_upper': current_bb_upper,
        'bb_lower': current_bb_lower,
        'bb_middle': current_bb_middle,
        'stoch_k': current_stoch_k,
        'stoch_d': current_stoch_d,
        'atr': current_atr,
        'trend': trend,
        'volume': volume.iloc[-1],
        'obv': obv.iloc[-1],
        'support': support,
        'resistance': resistance
    }

    # Generate signal
    signal, confidence = generate_signal(indicators)

    # Calculate Take Profit and Stop Loss
    # TP based on resistance or ATR multiple
    # SL based on support or ATR multiple
    tp1 = resistance
    tp2 = current_price + (current_atr * 2)
    sl = support if support > 0 else current_price - (current_atr * 2)

    # Calculate Risk/Reward ratio
    risk = current_price - sl
    reward = tp1 - current_price
    rr_ratio = reward / risk if risk > 0 else 0

    # CRITICAL: Validate TP only if R/R >= 1.5
    tp_valid = rr_ratio >= 1.5

    return {
        'ticker': ticker.upper(),
        'price': current_price,
        'signal': signal,
        'confidence': confidence,
        'trend': trend,
        'indicators': indicators,
        'support': support,
        'resistance': resistance,
        'tp': tp1,
        'tp2': tp2,
        'sl': sl,
        'rr_ratio': rr_ratio,
        'tp_valid': tp_valid,
        'rsi': current_rsi,
        'macd': current_macd,
        'macd_signal': current_signal,
        'macd_histogram': current_hist,
        'ma20': current_ma20,
        'ma50': current_ma50,
        'ma200': current_ma200,
        'bb_upper': current_bb_upper,
        'bb_lower': current_bb_lower,
        'stoch_k': current_stoch_k,
        'stoch_d': current_stoch_d,
        'atr': current_atr,
        'volume': volume.iloc[-1],
        'prev_close': close.iloc[-2],
        'change': ((current_price - close.iloc[-2]) / close.iloc[-2]) * 100,
        'date': df.index[-1].strftime('%Y-%m-%d')
    }


def is_bullish(trend: str) -> bool:
    """Check if a trend is considered bullish."""
    return "UPTREND" in trend or trend == "SIDEWAYS"


def screener_lq45() -> List[Dict]:
    """
    Screen LQ45 stocks for bullish signals.
    Returns list of stocks in uptrend with buy signals.
    """
    results = []
    bullish_stocks = []

    # For efficiency, check a subset of major stocks
    major_stocks = [
        "BBCA", "BBRI", "BMRI", "BBTN", "BNI", "BTPS",
        "ASII", "GOTO", "TLKM", "EXCL", "FREN",
        "UNVR", "HMSP", "ICBP", "INDF", "CPIN",
        "ADRO", "PTBA", "TINS", "MDKA", "NUSA",
        "AMRT", "MAPA", "MAPI", "ACES", "RALS"
    ]

    for ticker in major_stocks:
        try:
            analysis = analyze_stock(ticker, period="1mo")
            if analysis and is_bullish(analysis['trend']):
                bullish_stocks.append({
                    'ticker': ticker,
                    'price': analysis['price'],
                    'signal': analysis['signal'],
                    'trend': analysis['trend'],
                    'rsi': analysis['rsi'],
                    'change': analysis['change']
                })
        except Exception as e:
            print(f"Error screening {ticker}: {e}")
            continue

    return bullish_stocks


def backtest_signals(ticker: str, days: int = 60) -> Dict:
    """
    Backtest historical accuracy of buy/sell signals.
    Compares signal prices with subsequent price movements.
    """
    df = get_stock_data(ticker, period=f"{days + 30}d")  # Extra days for lookback
    if df is None or len(df) < days:
        return None

    close = df['Close']
    high = df['High']
    low = df['Low']

    # Calculate indicators for historical data
    rsi = calculate_rsi(close)
    macd_line, signal_line, histogram = calculate_macd(close)
    ma_dict = calculate_moving_averages(close)
    bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(close)
    stoch_k, stoch_d = calculate_stochastic(high, low, close)

    signals_generated = []
    wins = 0
    losses = 0
    total_profit = 0.0
    total_loss = 0.0

    # Generate signals and evaluate
    for i in range(50, len(df) - 5):  # Start after enough data, leave buffer for evaluation
        current_price = close.iloc[i]
        rsi_val = rsi.iloc[i]
        macd_hist = histogram.iloc[i]
        ma20_val = ma_dict['ma20'].iloc[i]
        ma50_val = ma_dict['ma50'].iloc[i]
        ma200_val = ma_dict['ma200'].iloc[i]
        bb_lower_val = bb_lower.iloc[i]
        bb_upper_val = bb_upper.iloc[i]
        stoch_k_val = stoch_k.iloc[i]

        trend = get_trend(close.iloc[:i+1], ma_dict['ma20'].iloc[:i+1],
                         ma_dict['ma50'].iloc[:i+1], ma_dict['ma200'].iloc[:i+1])

        indicators = {
            'price': current_price,
            'rsi': rsi_val,
            'macd_histogram': macd_hist,
            'ma20': ma20_val,
            'ma50': ma50_val,
            'bb_upper': bb_upper_val,
            'bb_lower': bb_lower_val,
            'stoch_k': stoch_k_val,
            'trend': trend
        }

        signal, confidence = generate_signal(indicators)

        if signal in ['BUY', 'SELL']:
            # Check price after 5 days
            future_price = close.iloc[i + 5]
            price_change = ((future_price - current_price) / current_price) * 100

            if signal == 'BUY' and price_change > 0:
                wins += 1
                total_profit += price_change
            elif signal == 'BUY' and price_change < 0:
                losses += 1
                total_loss += abs(price_change)
            elif signal == 'SELL' and price_change < 0:
                wins += 1
                total_profit += abs(price_change)
            elif signal == 'SELL' and price_change > 0:
                losses += 1
                total_loss += price_change

            signals_generated.append({
                'day': i,
                'signal': signal,
                'price': current_price,
                'future_price': future_price,
                'change': price_change,
                'correct': (signal == 'BUY' and price_change > 0) or (signal == 'SELL' and price_change < 0)
            })

    total_signals = wins + losses
    win_rate = (wins / total_signals * 100) if total_signals > 0 else 0
    avg_win = total_profit / wins if wins > 0 else 0
    avg_loss = total_loss / losses if losses > 0 else 0

    return {
        'ticker': ticker,
        'period_days': days,
        'total_signals': total_signals,
        'wins': wins,
        'losses': losses,
        'win_rate': win_rate,
        'avg_win_pct': avg_win,
        'avg_loss_pct': avg_loss,
        'signals': signals_generated[-10:]  # Last 10 signals for reference
    }


# Average Price Calculator
def calculate_average_price(lot1: int, price1: float, lot2: int, price2: float) -> Dict:
    """Calculate weighted average price from two buy orders."""
    total_lot = lot1 + lot2
    total_value = (lot1 * price1) + (lot2 * price2)
    average_price = total_value / total_lot

    return {
        'total_lot': total_lot,
        'average_price': average_price,
        'total_value': total_value,
        'lot1': lot1,
        'price1': price1,
        'lot2': lot2,
        'price2': price2
    }


# Cut Loss Calculator
def calculate_cut_loss(current_price: float, entry_price: float,
                       lot: int, target_loss_pct: float = 5.0) -> Dict:
    """
    Calculate cut loss parameters.
    Determines how much loss at current price and what price to cut.
    """
    loss_per_share = current_price - entry_price
    total_loss = loss_per_share * lot * 100  # 100 shares per lot

    # Calculate loss percentage
    loss_pct = ((current_price - entry_price) / entry_price) * 100

    # Calculate price at which to cut based on target loss
    target_price = entry_price * (1 - target_loss_pct / 100)
    price_to_cut = entry_price * 0.95  # Default 5% cut loss

    return {
        'entry_price': entry_price,
        'current_price': current_price,
        'loss_per_share': loss_per_share,
        'loss_percentage': loss_pct,
        'total_loss': total_loss,
        'price_to_cut': price_to_cut,
        'target_loss_pct': target_loss_pct,
        'lot': lot,
        'is_in_loss': current_price < entry_price
    }


# ============================================
# GROQ AI INTEGRATION
# ============================================

def get_ai_insight(ticker: str, analysis_result: Dict) -> str:
    """
    Gunakan Groq AI untuk memberikan insight natural language
    berdasarkan data technical analysis.

    Args:
        ticker: Kode saham (contoh: BBCA)
        analysis_result: Dictionary dari fungsi analyze_stock()

    Returns:
        String berisi insight dari AI dalam Bahasa Indonesia
    """
    try:
        from groq import Groq
        from config import GROQ_API_KEY, GROQ_MODEL

        # Skip jika API key belum di-set
        if not GROQ_API_KEY or GROQ_API_KEY == "YOUR_GROQ_API_KEY_HERE":
            return None

        client = Groq(api_key=GROQ_API_KEY)

        # Format data untuk prompt
        indicators_text = f"""
- Harga Saat Ini: Rp {analysis_result['price']:,.0f}
- Perubahan: {analysis_result['change']:+.2f}%
- Signal: {analysis_result['signal']} (Confidence: {analysis_result['confidence']})
- Trend: {analysis_result['trend']}

Indikator Teknikal:
- RSI (14): {analysis_result['rsi']:.1f}
- MACD: {analysis_result['macd']:.2f} (Histogram: {analysis_result['macd_histogram']:.2f})
- MA20: Rp {analysis_result['ma20']:,.0f}
- MA50: Rp {analysis_result['ma50']:,.0f}
- MA200: Rp {analysis_result['ma200']:,.0f}
- Bollinger Lower: Rp {analysis_result['bb_lower']:,.0f}
- Bollinger Upper: Rp {analysis_result['bb_upper']:,.0f}
- Stochastic %K: {analysis_result['stoch_k']:.1f}
- ATR: {analysis_result['atr']:.2f}

Support & Resistance:
- Support: Rp {analysis_result['support']:,.0f}
- Resistance: Rp {analysis_result['resistance']:,.0f}

Manajemen Risiko:
- Take Profit: Rp {analysis_result['tp']:,.0f}
- Stop Loss: Rp {analysis_result['sl']:,.0f}
- Risk/Reward Ratio: {analysis_result['rr_ratio']:.2f}x
- TP Valid (R/R >= 1.5x): {'Ya' if analysis_result['tp_valid'] else 'Tidak'}
"""

        prompt = f"""Anda adalah analis saham profesional yang fokus di pasar modal Indonesia (IDX).

Berdasarkan data teknikal berikut untuk saham {ticker}, berikan analisis singkat dalam Bahasa Indonesia (maksimal 300 kata):

{indicators_text}

Buat analisis yang mencakup:
1. Interpretasi sinyal ({analysis_result['signal']})
2. Kondisi indikator utama (RSI, MACD, dll)
3. Level support dan resistance yang penting
4. Rekomendasi manajemen risiko (TP, SL)
5. Peringatan jika R/R ratio kurang dari 1.5x

CATATAN PENTING:
- JANGAN memberikan saran buy/sell langsung
- Fokus pada edukasi dan informasi teknikal
- Selalu ingatkan bahwa ini BUKAN nasihat finansial
- Cantumkan disclaimer bahwa analisis teknikal memiliki keterbatasan
"""

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Kamu adalah analis saham profesional yang membantu trader Indonesia dengan analisis teknikal yang informatif dan edukatif."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.7,
            max_tokens=800
        )

        return response.choices[0].message.content

    except ImportError:
        print("Groq library not installed. Run: pip install groq")
        return None
    except Exception as e:
        print(f"Groq API Error: {e}")
        return None


def is_groq_configured() -> bool:
    """Cek apakah Groq sudah dikonfigurasi dengan benar."""
    from config import GROQ_API_KEY
    return bool(GROQ_API_KEY and GROQ_API_KEY != "YOUR_GROQ_API_KEY_HERE")
