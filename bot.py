import os, math, requests, logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL   = "llama-3.3-70b-versatile"   # model gratis terbaik di Groq

# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def fmt(n):
    if n is None: return "-"
    return f"{int(round(n)):,}".replace(",", ".")

def fmt_vol(v):
    if not v: return "-"
    if v >= 1_000_000_000: return f"{v/1_000_000_000:.2f}M"
    if v >= 1_000_000:     return f"{v/1_000_000:.1f}Jt"
    if v >= 1_000:         return f"{v/1_000:.1f}K"
    return str(v)

def pct(a, b):
    return ((a - b) / b * 100) if b else 0

# ══════════════════════════════════════════════════════════════════
#  FETCH DATA SAHAM (Yahoo Finance - Gratis)
# ══════════════════════════════════════════════════════════════════

def get_stock_data(ticker: str) -> dict:
    symbol = ticker.upper() + ".JK"
    url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=60d"
    r      = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    res  = data["chart"]["result"]
    if not res:
        raise ValueError("Saham tidak ditemukan. Cek kode saham kamu.")
    meta  = res[0]["meta"]
    quote = res[0].get("indicators", {}).get("quote", [{}])[0]

    def clean(lst): return [x for x in (lst or []) if x is not None]

    closes  = clean(quote.get("close",  []))
    highs   = clean(quote.get("high",   []))
    lows    = clean(quote.get("low",    []))
    volumes = clean(quote.get("volume", []))

    price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    prev  = meta.get("previousClose")      or (closes[-2] if len(closes) > 1 else price)

    return {
        "symbol"  : ticker.upper(),
        "name"    : meta.get("shortName", ticker.upper()),
        "price"   : price,
        "open"    : meta.get("regularMarketOpen"),
        "high"    : meta.get("regularMarketDayHigh") or (highs[-1]   if highs   else price),
        "low"     : meta.get("regularMarketDayLow")  or (lows[-1]    if lows    else price),
        "prev"    : prev,
        "volume"  : meta.get("regularMarketVolume")  or (volumes[-1] if volumes else 0),
        "high52"  : meta.get("fiftyTwoWeekHigh"),
        "low52"   : meta.get("fiftyTwoWeekLow"),
        "closes"  : closes,
        "highs"   : highs,
        "lows"    : lows,
        "volumes" : volumes,
    }

# ══════════════════════════════════════════════════════════════════
#  INDIKATOR TEKNIKAL
# ══════════════════════════════════════════════════════════════════

def ema(data, period):
    if len(data) < period: return None
    k   = 2 / (period + 1)
    val = sum(data[:period]) / period
    for x in data[period:]:
        val = x * k + val * (1 - k)
    return val

def sma(data, period):
    if len(data) < period: return None
    return sum(data[-period:]) / period

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return 100 - (100 / (1 + ag / al))

def calc_macd(closes):
    if len(closes) < 26: return None, None, None
    macd_vals = []
    for i in range(26, len(closes) + 1):
        e12 = ema(closes[:i], 12)
        e26 = ema(closes[:i], 26)
        if e12 and e26: macd_vals.append(e12 - e26)
    if not macd_vals: return None, None, None
    macd_line = macd_vals[-1]
    if len(macd_vals) < 9: return macd_line, None, None
    signal    = ema(macd_vals, 9)
    histogram = macd_line - signal if signal else None
    return macd_line, signal, histogram

def calc_bollinger(closes, period=20, std_dev=2):
    if len(closes) < period: return None, None, None
    mid = sma(closes, period)
    if mid is None: return None, None, None
    std   = math.sqrt(sum((x - mid) ** 2 for x in closes[-period:]) / period)
    return mid + std_dev * std, mid, mid - std_dev * std

def calc_stochastic(closes, highs, lows, k_period=14):
    if len(closes) < k_period: return None, None
    low_k  = min(lows[-k_period:])
    high_k = max(highs[-k_period:])
    if high_k == low_k: return 50, 50
    k = (closes[-1] - low_k) / (high_k - low_k) * 100
    return k, k

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
           for i in range(1, len(closes))]
    if not trs: return None
    return sum(trs[-period:]) / min(len(trs), period)

def calc_volume_ratio(volumes):
    if len(volumes) < 6: return None
    avg = sum(volumes[-6:-1]) / 5
    return (volumes[-1] / avg) if avg else None

def calc_pivot_fibonacci(high, low, close):
    pivot = (high + low + close) / 3
    r     = high - low
    return {
        "pivot"   : pivot,
        "r1": 2 * pivot - low,
        "r2": pivot + r,
        "r3": high + 2 * (pivot - low),
        "s1": 2 * pivot - high,
        "s2": pivot - r,
        "s3": low - 2 * (high - pivot),
        "fib_236" : high - r * 0.236,
        "fib_382" : high - r * 0.382,
        "fib_500" : high - r * 0.500,
        "fib_618" : high - r * 0.618,
    }

def detect_candle_pattern(closes, highs, lows):
    if len(closes) < 3: return "Tidak cukup data"
    # Gunakan close[i-1] sebagai open proxy
    patterns = []
    c1, h1, l1 = closes[-1], highs[-1], lows[-1]
    o1         = closes[-2]
    c2         = closes[-2]
    o2         = closes[-3]

    body  = abs(c1 - o1)
    rng   = h1 - l1 or 0.01
    upper = h1 - max(o1, c1)
    lower = min(o1, c1) - l1

    if body / rng < 0.1:
        patterns.append("Doji ⚪ (ketidakpastian)")
    if lower > 2 * body and upper < body and c1 > o1:
        patterns.append("Hammer 🔨 (potensi reversal naik)")
    if upper > 2 * body and lower < body:
        patterns.append("Shooting Star ⭐ (potensi reversal turun)")
    if c2 < o2 and c1 > o1 and c1 > o2 and o1 < c2:
        patterns.append("Bullish Engulfing 🟢 (sinyal beli kuat)")
    if c2 > o2 and c1 < o1 and c1 < o2 and o1 > c2:
        patterns.append("Bearish Engulfing 🔴 (sinyal jual kuat)")

    return ", ".join(patterns) if patterns else "Tidak ada pola khusus"

# ══════════════════════════════════════════════════════════════════
#  SCORING MULTI-INDIKATOR (8 indikator voting)
# ══════════════════════════════════════════════════════════════════

def score_indicators(d: dict, ind: dict) -> dict:
    price  = d["price"]
    scores = {}
    notes  = {}

    # 1. RSI
    rsi = ind.get("rsi")
    if rsi is not None:
        if rsi < 35:
            scores["RSI"] = +1; notes["RSI"] = f"RSI {rsi:.1f} — Oversold 🟢"
        elif rsi > 65:
            scores["RSI"] = -1; notes["RSI"] = f"RSI {rsi:.1f} — Overbought 🔴"
        else:
            scores["RSI"] =  0; notes["RSI"] = f"RSI {rsi:.1f} — Netral ⚪"

    # 2. MACD
    macd, signal, hist = ind.get("macd", (None, None, None))
    if macd is not None and signal is not None:
        if macd > signal:
            scores["MACD"] = +1; notes["MACD"] = f"MACD {macd:.1f} > Signal — Bullish 🟢"
        else:
            scores["MACD"] = -1; notes["MACD"] = f"MACD {macd:.1f} < Signal — Bearish 🔴"

    # 3. Bollinger Bands
    bb_u, bb_m, bb_l = ind.get("bb", (None, None, None))
    if bb_u and bb_l:
        if price <= bb_l:
            scores["BB"] = +1; notes["BB"] = f"Harga di Lower Band Rp {fmt(bb_l)} 🟢"
        elif price >= bb_u:
            scores["BB"] = -1; notes["BB"] = f"Harga di Upper Band Rp {fmt(bb_u)} 🔴"
        else:
            scores["BB"] =  0; notes["BB"] = f"Di dalam BB (Mid Rp {fmt(bb_m)}) ⚪"

    # 4. Stochastic
    stoch_k, _ = ind.get("stoch", (None, None))
    if stoch_k is not None:
        if stoch_k < 20:
            scores["Stoch"] = +1; notes["Stoch"] = f"Stoch {stoch_k:.1f} — Oversold 🟢"
        elif stoch_k > 80:
            scores["Stoch"] = -1; notes["Stoch"] = f"Stoch {stoch_k:.1f} — Overbought 🔴"
        else:
            scores["Stoch"] =  0; notes["Stoch"] = f"Stoch {stoch_k:.1f} — Netral ⚪"

    # 5. EMA Trend
    ema9  = ind.get("ema9")
    ema21 = ind.get("ema21")
    if ema9 and ema21:
        if price > ema9 > ema21:
            scores["EMA"] = +1; notes["EMA"] = f"Price>EMA9({fmt(ema9)})>EMA21({fmt(ema21)}) Uptrend 🟢"
        elif price < ema9 < ema21:
            scores["EMA"] = -1; notes["EMA"] = f"Price<EMA9({fmt(ema9)})<EMA21({fmt(ema21)}) Downtrend 🔴"
        else:
            scores["EMA"] =  0; notes["EMA"] = f"EMA9={fmt(ema9)} EMA21={fmt(ema21)} Mixed ⚪"

    # 6. Volume
    vol_ratio = ind.get("vol_ratio")
    change    = pct(price, d["prev"])
    if vol_ratio is not None:
        if vol_ratio > 1.5 and change > 0:
            scores["Volume"] = +1; notes["Volume"] = f"Volume {vol_ratio:.1f}x rata2 + harga naik 🟢"
        elif vol_ratio > 1.5 and change < 0:
            scores["Volume"] = -1; notes["Volume"] = f"Volume {vol_ratio:.1f}x rata2 + harga turun 🔴"
        else:
            scores["Volume"] =  0; notes["Volume"] = f"Volume normal {vol_ratio:.1f}x rata2 ⚪"

    # 7. Pivot Point
    piv = ind.get("piv", {})
    if piv:
        if price > piv.get("r1", price):
            scores["Pivot"] = +1; notes["Pivot"] = f"Harga breakout R1 Rp {fmt(piv['r1'])} 🟢"
        elif price < piv.get("s1", price):
            scores["Pivot"] = -1; notes["Pivot"] = f"Harga breakdown S1 Rp {fmt(piv['s1'])} 🔴"
        elif price > piv.get("pivot", price):
            scores["Pivot"] = +1; notes["Pivot"] = f"Harga di atas Pivot Rp {fmt(piv['pivot'])} 🟢"
        else:
            scores["Pivot"] = -1; notes["Pivot"] = f"Harga di bawah Pivot Rp {fmt(piv['pivot'])} 🔴"

    # 8. Posisi 52-Week
    h52 = d.get("high52")
    l52 = d.get("low52")
    if h52 and l52 and (h52 - l52) > 0:
        pos = (price - l52) / (h52 - l52) * 100
        if pos < 30:
            scores["52W"] = +1; notes["52W"] = f"Di zona bawah 52W ({pos:.0f}%) — Murah 🟢"
        elif pos > 75:
            scores["52W"] = -1; notes["52W"] = f"Di zona atas 52W ({pos:.0f}%) — Mahal 🔴"
        else:
            scores["52W"] =  0; notes["52W"] = f"Di zona tengah 52W ({pos:.0f}%) ⚪"

    total   = sum(scores.values())
    bullish = sum(1 for v in scores.values() if v > 0)
    bearish = sum(1 for v in scores.values() if v < 0)
    total_v = len(scores)

    sinyal = "HOLD"
    if total >= 3:  sinyal = "BELI"
    if total <= -3: sinyal = "JUAL"

    if sinyal == "BELI":
        strength = "💪 KUAT" if bullish >= 6 else ("👌 SEDANG" if bullish >= 4 else "⚠️ LEMAH")
    elif sinyal == "JUAL":
        strength = "💪 KUAT" if bearish >= 6 else ("👌 SEDANG" if bearish >= 4 else "⚠️ LEMAH")
    else:
        strength = "⚠️ LEMAH — Tunggu konfirmasi lebih lanjut"

    return {
        "sinyal": sinyal, "strength": strength,
        "scores": scores, "notes": notes,
        "total": total, "bullish": bullish,
        "bearish": bearish, "total_v": total_v,
    }

def calc_tp_sl(d: dict, ind: dict, sinyal: str) -> dict:
    price = d["price"]
    atr   = ind.get("atr") or (d["high"] - d["low"]) or price * 0.03
    piv   = ind.get("piv", {})
    bb_u, _, bb_l = ind.get("bb", (None, None, None))

    if sinyal == "BELI":
        sl  = max(max(price - 1.5 * atr, piv.get("s1", 0) * 0.99), price * 0.93)
        tp1 = max(min(price + 1.0 * atr, piv.get("r1", price * 1.03)), price * 1.025)
        tp2 = max(min(price + 2.0 * atr, piv.get("r2", price * 1.06)), price * 1.05)
        tp3 = max(min(price + 3.0 * atr, piv.get("r3", price * 1.10)), price * 1.09)
    elif sinyal == "JUAL":
        sl  = min(price + 1.5 * atr, price * 1.07)
        tp1 = min(price - 1.0 * atr, price * 0.975)
        tp2 = min(price - 2.0 * atr, price * 0.95)
        tp3 = min(price - 3.0 * atr, price * 0.91)
    else:
        sl  = price * 0.95
        tp1 = price * 1.03
        tp2 = price * 1.06
        tp3 = price * 1.10

    return {
        "entry": round(price),
        "sl":    round(sl),
        "tp1":   round(tp1),
        "tp2":   round(tp2),
        "tp3":   round(tp3),
    }

# ══════════════════════════════════════════════════════════════════
#  GROQ AI (GRATIS)
# ══════════════════════════════════════════════════════════════════

def get_ai_summary(d: dict, ind: dict, scoring: dict, tpsl: dict) -> str:
    notes_txt = "\n".join([f"- {k}: {v}" for k, v in scoring["notes"].items()])
    piv = ind.get("piv", {})

    prompt = f"""Kamu adalah analis teknikal saham Indonesia profesional yang berpengalaman.
Berikan analisis ringkas dan akurat berdasarkan data berikut:

SAHAM: {d['symbol']} ({d['name']})
Harga : Rp {fmt(d['price'])} | Prev: Rp {fmt(d['prev'])} ({pct(d['price'],d['prev']):+.2f}%)
Volume: {fmt_vol(d['volume'])}
ATR   : Rp {fmt(ind.get('atr'))}
52W   : High Rp {fmt(d['high52'])} | Low Rp {fmt(d['low52'])}

HASIL 8 INDIKATOR ({scoring['bullish']} bullish / {scoring['bearish']} bearish):
{notes_txt}

SINYAL AKHIR: {scoring['sinyal']} ({scoring['strength']})
Entry: Rp {fmt(tpsl['entry'])} | TP1: {fmt(tpsl['tp1'])} | TP2: {fmt(tpsl['tp2'])} | TP3: {fmt(tpsl['tp3'])} | SL: {fmt(tpsl['sl'])}

Pivot: {fmt(piv.get('pivot'))} | R1: {fmt(piv.get('r1'))} | S1: {fmt(piv.get('s1'))}

Tulis analisis SINGKAT dalam format persis ini:

KESIMPULAN:
[2-3 kalimat: kondisi pasar dan alasan sinyal, sebutkan indikator paling dominan]

STRATEGI:
[1-2 kalimat: cara masuk dan kelola posisi]

WASPADAI:
[1 kalimat: kondisi yang membatalkan sinyal ini]

Gunakan bahasa Indonesia. Singkat dan padat. Langsung tanpa basa-basi."""

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type" : "application/json",
        },
        json={
            "model"      : GROQ_MODEL,
            "max_tokens" : 500,
            "temperature": 0.3,
            "messages"   : [
                {"role": "system", "content": "Kamu analis teknikal saham Indonesia profesional. Jawab singkat, padat, dan dalam bahasa Indonesia."},
                {"role": "user",   "content": prompt},
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

# ══════════════════════════════════════════════════════════════════
#  BUILD PESAN LENGKAP
# ══════════════════════════════════════════════════════════════════

def build_full_message(d, ind, scoring, tpsl, candle, ai_txt, modal):
    sinyal = scoring["sinyal"]
    change = pct(d["price"], d["prev"])
    piv    = ind.get("piv", {})
    bb_u, bb_m, bb_l = ind.get("bb", (None, None, None))
    ema9  = ind.get("ema9")
    ema21 = ind.get("ema21")
    ema50 = ind.get("ema50")

    if sinyal == "BELI":   bar = "🟢🟢 *SINYAL: BELI* 🟢🟢"
    elif sinyal == "JUAL": bar = "🔴🔴 *SINYAL: JUAL* 🔴🔴"
    else:                  bar = "🟡🟡 *SINYAL: HOLD* 🟡🟡"

    lot        = int(modal / (d["price"] * 100)) if d["price"] else 0
    modal_used = lot * 100 * d["price"]

    def pl(tp):
        return lot * 100 * (tp - d["price"]) - lot * 100 * tp * 0.002

    # Parse AI text
    kesimpulan = strategi = waspadai = ""
    mode = None
    for line in ai_txt.splitlines():
        l  = line.strip()
        up = l.upper()
        if up.startswith("KESIMPULAN:"): mode = "k"; continue
        elif up.startswith("STRATEGI:"): mode = "s"; continue
        elif up.startswith("WASPADAI:"): mode = "w"; continue
        if mode == "k" and l: kesimpulan += l + " "
        elif mode == "s" and l: strategi  += l + " "
        elif mode == "w" and l: waspadai  += l + " "

    lines = [
        f"{'📈' if change >= 0 else '📉'} *{d['symbol']}* — {d['name']}",
        f"💵 *Rp {fmt(d['price'])}*  ({change:+.2f}%)  Vol: {fmt_vol(d['volume'])}",
        f"O:{fmt(d['open'])}  H:{fmt(d['high'])}  L:{fmt(d['low'])}",
        f"52W: {fmt(d['low52'])} — {fmt(d['high52'])}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        bar,
        f"Kekuatan : {scoring['strength']}",
        f"Skor     : {scoring['bullish']}🟢 vs {scoring['bearish']}🔴 dari {scoring['total_v']} indikator",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🎯 *ENTRY     :* Rp {fmt(tpsl['entry'])}",
        "",
        "✅ *TAKE PROFIT:*",
        f"   🥇 TP1 : Rp {fmt(tpsl['tp1'])}  ({pct(tpsl['tp1'], tpsl['entry']):+.1f}%)",
        f"   🥈 TP2 : Rp {fmt(tpsl['tp2'])}  ({pct(tpsl['tp2'], tpsl['entry']):+.1f}%)",
        f"   🥉 TP3 : Rp {fmt(tpsl['tp3'])}  ({pct(tpsl['tp3'], tpsl['entry']):+.1f}%)",
        "",
        f"🛑 *STOP LOSS  :* Rp {fmt(tpsl['sl'])}  ({pct(tpsl['sl'], tpsl['entry']):+.1f}%)",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 *SUPPORT & RESISTANCE:*",
        f"   🔴 R3: {fmt(piv.get('r3'))}",
        f"   🔴 R2: {fmt(piv.get('r2'))}",
        f"   🔴 R1: {fmt(piv.get('r1'))}",
        f"   ⚪ Pivot: {fmt(piv.get('pivot'))}",
        f"   🟢 S1: {fmt(piv.get('s1'))}",
        f"   🟢 S2: {fmt(piv.get('s2'))}",
        f"   🟢 S3: {fmt(piv.get('s3'))}",
        "",
        "📐 *FIBONACCI RETRACEMENT:*",
        f"   23.6% : Rp {fmt(piv.get('fib_236'))}",
        f"   38.2% : Rp {fmt(piv.get('fib_382'))}",
        f"   50.0% : Rp {fmt(piv.get('fib_500'))}",
        f"   61.8% : Rp {fmt(piv.get('fib_618'))}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📉 *8 INDIKATOR TEKNIKAL:*",
        "",
    ]

    for key, note in scoring["notes"].items():
        lines.append(f"  {note}")

    lines += [
        "",
        f"📊 *Bollinger:* Upper {fmt(bb_u)}  Mid {fmt(bb_m)}  Lower {fmt(bb_l)}",
        f"📈 *EMA:* 9={fmt(ema9)}  21={fmt(ema21)}  50={fmt(ema50)}",
        f"📏 *ATR:* Rp {fmt(ind.get('atr'))}",
        f"🕯️ *Candlestick:* {candle}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🤖 *ANALISIS AI (Groq LLaMA):*",
        "",
    ]

    if kesimpulan: lines.append(f"📌 *Kesimpulan:* {kesimpulan.strip()}")
    if strategi:   lines.append(f"\n💡 *Strategi:* {strategi.strip()}")
    if waspadai:   lines.append(f"\n⚠️ *Waspadai:* {waspadai.strip()}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 *SIMULASI MODAL Rp {fmt(modal)}:*",
    ]

    if lot > 0:
        lines += [
            f"   Beli      : *{lot} lot* ({lot*100:,} lbr)",
            f"   Modal     : Rp {fmt(modal_used)}",
            f"   Sisa cash : Rp {fmt(modal - modal_used)}",
            "",
            f"   💵 Profit TP1 : *+Rp {fmt(pl(tpsl['tp1']))}*",
            f"   💵 Profit TP2 : *+Rp {fmt(pl(tpsl['tp2']))}*",
            f"   💵 Profit TP3 : *+Rp {fmt(pl(tpsl['tp3']))}*",
            f"   💸 Rugi SL    : *-Rp {fmt(abs(pl(tpsl['sl'])))}*",
        ]
    else:
        lines += [
            f"   ⚠️ Modal tidak cukup untuk 1 lot",
            f"   Butuh minimal Rp {fmt(d['price'] * 100)}",
        ]

    lines += [
        "",
        "📌 _TP1 → jual 50% | TP2 → jual 30% | TP3 → jual 20%_",
        "⚠️ _Disclaimer: Bukan saran investasi profesional. DYOR._",
    ]

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
#  PIPELINE UTAMA
# ══════════════════════════════════════════════════════════════════

def run_full_analysis(ticker: str, modal: int) -> str:
    d = get_stock_data(ticker)

    closes  = d["closes"]
    highs   = d["highs"]
    lows    = d["lows"]
    volumes = d["volumes"]

    ind = {
        "rsi"      : calc_rsi(closes),
        "macd"     : calc_macd(closes),
        "bb"       : calc_bollinger(closes),
        "stoch"    : calc_stochastic(closes, highs, lows),
        "atr"      : calc_atr(highs, lows, closes),
        "vol_ratio": calc_volume_ratio(volumes),
        "piv"      : calc_pivot_fibonacci(d["high"], d["low"], d["price"]),
        "ema9"     : ema(closes, 9),
        "ema21"    : ema(closes, 21),
        "ema50"    : ema(closes, 50),
    }

    try:    candle = detect_candle_pattern(closes, highs, lows)
    except: candle = "Tidak tersedia"

    scoring = score_indicators(d, ind)
    tpsl    = calc_tp_sl(d, ind, scoring["sinyal"])
    ai_txt  = get_ai_summary(d, ind, scoring, tpsl)

    return build_full_message(d, ind, scoring, tpsl, candle, ai_txt, modal)

# ══════════════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *BEI TRADING SIGNAL BOT*\n\n"
        "Analisis *8 indikator* sekaligus:\n"
        "RSI · MACD · Bollinger · Stochastic\n"
        "EMA 9/21/50 · Volume · Pivot · Fibonacci\n\n"
        "*PERINTAH:*\n"
        "📊 `/sinyal BBCA` — sinyal lengkap\n"
        "📊 `/sinyal BBCA 2000000` — modal kustom\n"
        "💰 `/average BBCA 2 9500 3 9000`\n"
        "✂️ `/cutloss BBCA 1200 2`\n"
        "📋 `/portofolio`\n"
        "❓ `/help`\n\n"
        "_Atau kirim kode saham langsung: *TLKM*_\n\n"
        "⚠️ _Bukan saran investasi profesional._",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *PANDUAN BOT*\n\n"
        "*8 Indikator yang dipakai:*\n"
        "1. RSI (14) — oversold/overbought\n"
        "2. MACD — momentum & crossover\n"
        "3. Bollinger Bands — breakout/volatilitas\n"
        "4. Stochastic — timing entry presisi\n"
        "5. EMA 9/21/50 — tren jangka pendek-menengah\n"
        "6. Volume Ratio — konfirmasi pergerakan\n"
        "7. Pivot Point — S/R harian\n"
        "8. Posisi 52W — zona murah/mahal\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *Cara Pakai TP:*\n"
        "• TP1 → Jual 50% posisi\n"
        "• TP2 → Jual 30% posisi\n"
        "• TP3 → Jual 20% sisa\n"
        "• SL  → Jual SEMUA, stop rugi\n\n"
        "💡 Prioritaskan sinyal *KUAT* (6+ indikator setuju)!",
        parse_mode="Markdown"
    )

async def cmd_sinyal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ Format: `/sinyal BBCA`", parse_mode="Markdown")
        return
    ticker = ctx.args[0].upper()
    modal  = 1_000_000
    if len(ctx.args) >= 2:
        try: modal = int(ctx.args[1])
        except: pass

    msg = await update.message.reply_text(
        f"⏳ Menganalisis *{ticker}* dengan 8 indikator...", parse_mode="Markdown"
    )
    try:
        full = run_full_analysis(ticker, modal)
        await msg.edit_text(full, parse_mode="Markdown")
    except Exception as e:
        logger.exception(e)
        await msg.edit_text(f"❌ Error: {str(e)}\nContoh kode: BBCA TLKM GOTO BBRI ANTM")

async def cmd_average(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text("❌ Format: `/average BBCA 2 9500 3 9000`", parse_mode="Markdown")
        return
    ticker = ctx.args[0].upper()
    nums   = ctx.args[1:]
    if len(nums) % 2 != 0:
        await update.message.reply_text("❌ Pasangan lot-harga harus genap.", parse_mode="Markdown")
        return
    try:
        lp = [(int(nums[i]), float(nums[i+1])) for i in range(0, len(nums), 2)]
    except:
        await update.message.reply_text("❌ Format angka salah.", parse_mode="Markdown")
        return

    msg = await update.message.reply_text(f"⏳ Mengambil harga *{ticker}*...", parse_mode="Markdown")
    try:
        d           = get_stock_data(ticker)
        current     = d["price"]
        total_lot   = sum(l for l, _ in lp)
        total_modal = sum(l * 100 * p for l, p in lp)
        avg         = total_modal / (total_lot * 100)
        pl_val      = (current - avg) * total_lot * 100
        pl_p        = pct(current, avg)
        status      = "🟢 UNTUNG" if pl_val >= 0 else "🔴 RUGI"
        sign        = "+" if pl_val >= 0 else "-"
        transaksi   = "\n".join([f"  Tx{i+1}: {l} lot @ Rp {fmt(p)}" for i, (l, p) in enumerate(lp)])

        text = (
            f"📊 *AVERAGE — {ticker}*\n\n"
            f"{transaksi}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Avg Price  : *Rp {fmt(avg)}*\n"
            f"📌 Total      : *{total_lot} lot* ({total_lot*100:,} lbr)\n"
            f"📌 Modal      : Rp {fmt(total_modal)}\n"
            f"📌 Harga Skrg : Rp {fmt(current)}\n"
            f"📌 P&L        : *{sign}Rp {fmt(abs(pl_val))} ({pl_p:+.2f}%)*\n"
            f"📌 Status     : {status}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *TARGET DARI HARGA AVG:*\n"
            f"   🥇 TP1: Rp {fmt(avg * 1.05)} (+5%)\n"
            f"   🥈 TP2: Rp {fmt(avg * 1.10)} (+10%)\n"
            f"   🥉 TP3: Rp {fmt(avg * 1.15)} (+15%)\n"
            f"   🛑 SL : Rp {fmt(avg * 0.95)} (-5%)\n"
        )
        if pl_val < 0:
            avg2 = (avg + current) / 2
            text += (
                f"\n💡 *Simulasi average down 1x di harga skrg:*\n"
                f"   Avg baru ≈ Rp {fmt(avg2)}\n"
                f"   Perlu naik {abs(pct(avg, avg2)):.1f}% untuk BEP\n"
            )
        text += "\n⚠️ _Average down hanya jika yakin fundamentalnya bagus._"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def cmd_cutloss(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "❌ Format: `/cutloss BBCA 1200` atau `/cutloss BBCA 1200 3`", parse_mode="Markdown")
        return
    ticker = ctx.args[0].upper()
    try:    buy = float(ctx.args[1])
    except: await update.message.reply_text("❌ Harga tidak valid."); return
    lot = 1
    if len(ctx.args) >= 3:
        try: lot = int(ctx.args[2])
        except: pass

    msg = await update.message.reply_text(f"⏳ Analisis cut loss *{ticker}*...", parse_mode="Markdown")
    try:
        d       = get_stock_data(ticker)
        rsi     = calc_rsi(d["closes"])
        piv     = calc_pivot_fibonacci(d["high"], d["low"], d["price"])
        current = d["price"]
        pl_val  = (current - buy) * lot * 100
        pl_p    = pct(current, buy)
        status  = "🟢 UNTUNG" if current >= buy else "🔴 RUGI"
        sign    = "+" if pl_val >= 0 else "-"
        s1      = piv.get("s1", current * 0.97)

        if current < s1:
            rek = f"⚠️ Harga di bawah S1 (Rp {fmt(s1)}). *Pertimbangkan cut loss sekarang.*"
        elif current < buy * 0.93:
            rek = f"⚠️ Sudah turun >7%. *Sangat disarankan cut loss segera.*"
        elif rsi and rsi < 35:
            rek = f"💡 RSI {rsi:.1f} oversold. Bisa tunggu sedikit, tapi pantau S1 Rp {fmt(s1)}."
        elif current < buy * 0.97:
            rek = f"💡 Turun 3-7%. Pantau ketat. Cut jika tembus Rp {fmt(buy * 0.93)}."
        else:
            rek = f"✅ Masih aman. Monitor support S1 di Rp {fmt(s1)}."

        text = (
            f"✂️ *CUT LOSS — {ticker}*\n\n"
            f"📌 Harga Beli : Rp {fmt(buy)}\n"
            f"📌 Harga Skrg : Rp {fmt(current)}\n"
            f"📌 Posisi     : {lot} lot ({lot*100} lbr)\n"
            f"📌 Status     : {status} ({pl_p:+.2f}%)\n"
            f"📌 P&L        : *{sign}Rp {fmt(abs(pl_val))}*\n"
            f"📌 RSI        : {f'{rsi:.1f}' if rsi else '-'}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 *LEVEL STOP LOSS:*\n"
            f"   Ketat  (-5%) : Rp {fmt(buy * 0.95)}\n"
            f"   Normal (-7%) : Rp {fmt(buy * 0.93)}\n"
            f"   Longgar(-10%): Rp {fmt(buy * 0.90)}\n\n"
            f"📊 *SUPPORT:*\n"
            f"   S1: Rp {fmt(piv.get('s1'))}\n"
            f"   S2: Rp {fmt(piv.get('s2'))}\n"
            f"   S3: Rp {fmt(piv.get('s3'))}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{rek}\n\n"
            f"⚠️ _Bukan saran investasi profesional._"
        )
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def cmd_portofolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *STRATEGI PORTOFOLIO MODAL Rp 1 JUTA*\n\n"
        "💡 *Alokasi:*\n"
        "   60% → 1 saham utama (Rp 600rb)\n"
        "   30% → 1 saham cadangan (Rp 300rb)\n"
        "   10% → Cash darurat (Rp 100rb)\n\n"
        "🎯 *Strategi TP Bertahap:*\n"
        "   TP1 → Jual 50% (amankan profit)\n"
        "   TP2 → Jual 30%\n"
        "   TP3 → Jual 20% sisa\n\n"
        "🛑 *Aturan SL:*\n"
        "   • Pasang SL sebelum beli\n"
        "   • Max loss per trade: -5% sd -7%\n"
        "   • Jangan average down sembarangan\n\n"
        "📌 *Prioritas Sinyal:*\n"
        "   KUAT (6+ indikator) → Masuk\n"
        "   SEDANG (4-5)        → Hati-hati, modal kecil\n"
        "   LEMAH (<4)          → Skip, tunggu sinyal lebih baik\n\n"
        "💪 *Konsistensi > Profit besar sekali!*",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper().split()[0]
    if text.isalpha() and 2 <= len(text) <= 6:
        ctx.args = [text]
        await cmd_sinyal(update, ctx)
    else:
        await update.message.reply_text(
            "❓ Ketik `/help` untuk panduan, atau kirim kode saham: *BBCA*",
            parse_mode="Markdown"
        )

# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN tidak ditemukan di environment variable!")
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY tidak ditemukan di environment variable!")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("sinyal",     cmd_sinyal))
    app.add_handler(CommandHandler("analisis",   cmd_sinyal))
    app.add_handler(CommandHandler("average",    cmd_average))
    app.add_handler(CommandHandler("cutloss",    cmd_cutloss))
    app.add_handler(CommandHandler("portofolio", cmd_portofolio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ BEI Signal Bot started dengan Groq AI!")
    app.run_polling()

if __name__ == "__main__":
    main()
