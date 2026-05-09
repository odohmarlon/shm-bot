import os, math, requests, logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL   = "llama-3.3-70b-versatile"

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
#  FETCH DATA (60 hari untuk indikator akurat)
# ══════════════════════════════════════════════════════════════════

def get_stock_data(ticker: str) -> dict:
    symbol = ticker.upper() + ".JK"
    url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=60d"
    r      = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    res  = data["chart"]["result"]
    if not res:
        raise ValueError("Saham tidak ditemukan.")
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
    std = math.sqrt(sum((x - mid) ** 2 for x in closes[-period:]) / period)
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

# ══════════════════════════════════════════════════════════════════
#  SUPPORT & RESISTANCE MULTI-SUMBER
#  Gabungan: Pivot Classic + Swing High/Low historis + Fibonacci
# ══════════════════════════════════════════════════════════════════

def calc_sr_levels(d: dict) -> dict:
    """
    Hitung S/R dari 3 sumber berbeda:
    1. Pivot Point classic (harian)
    2. Swing High/Low dari 30 hari terakhir
    3. Fibonacci retracement dari swing tertinggi-terendah
    Kemudian bersihkan level yang terlalu berdekatan.
    """
    price  = d["price"]
    high   = d["high"]
    low    = d["low"]
    closes = d["closes"]
    highs  = d["highs"]
    lows   = d["lows"]

    # ── 1. Pivot Point Classic ─────────────────────────────────────
    pivot = (high + low + d["prev"]) / 3
    r     = high - low
    piv_r1 = 2 * pivot - low
    piv_r2 = pivot + r
    piv_r3 = high + 2 * (pivot - low)
    piv_s1 = 2 * pivot - high
    piv_s2 = pivot - r
    piv_s3 = low - 2 * (high - pivot)

    # ── 2. Swing High/Low dari 30 hari ────────────────────────────
    window = 5   # candle kiri-kanan untuk konfirmasi swing
    swing_highs = []
    swing_lows  = []
    h_data = highs[-30:] if len(highs) >= 30 else highs
    l_data = lows[-30:]  if len(lows)  >= 30 else lows

    for i in range(window, len(h_data) - window):
        if all(h_data[i] >= h_data[i-j] for j in range(1, window+1)) and \
           all(h_data[i] >= h_data[i+j] for j in range(1, window+1)):
            swing_highs.append(h_data[i])
        if all(l_data[i] <= l_data[i-j] for j in range(1, window+1)) and \
           all(l_data[i] <= l_data[i+j] for j in range(1, window+1)):
            swing_lows.append(l_data[i])

    # ── 3. Fibonacci dari 52W high-low ────────────────────────────
    h52 = d.get("high52") or max(highs) if highs else price * 1.2
    l52 = d.get("low52")  or min(lows)  if lows  else price * 0.8
    fib_rng = h52 - l52

    fib_levels = {
        "fib_236": h52 - fib_rng * 0.236,
        "fib_382": h52 - fib_rng * 0.382,
        "fib_500": h52 - fib_rng * 0.500,
        "fib_618": h52 - fib_rng * 0.618,
        "fib_786": h52 - fib_rng * 0.786,
    }

    # ── Kumpulkan semua level resistance & support ─────────────────
    raw_resistance = [piv_r1, piv_r2, piv_r3] + swing_highs + \
                     [v for v in fib_levels.values() if v > price]
    raw_support    = [piv_s1, piv_s2, piv_s3] + swing_lows + \
                     [v for v in fib_levels.values() if v < price]

    # ── Bersihkan: ambil yang valid dan tidak terlalu berdekatan ──
    min_gap = price * 0.005   # min gap 0.5% antar level

    def clean_levels(levels, above_price: bool):
        # Filter: harus di atas/bawah harga
        filtered = [l for l in levels if (l > price if above_price else l < price)]
        if not filtered: return []
        # Sort
        filtered = sorted(set([round(l) for l in filtered]),
                          reverse=(not above_price))
        # Hapus level terlalu berdekatan (cluster → ambil yang paling sering muncul)
        cleaned = []
        for lvl in filtered:
            if not cleaned or abs(lvl - cleaned[-1]) >= min_gap:
                cleaned.append(lvl)
        return cleaned

    resistances = clean_levels(raw_resistance, above_price=True)[:4]
    supports    = clean_levels(raw_support,    above_price=False)[:4]

    return {
        "pivot"      : round(pivot),
        "resistances": resistances,   # list terurut, terdekat ke harga duluan
        "supports"   : supports,      # list terurut, terdekat ke harga duluan
        "fib"        : {k: round(v) for k, v in fib_levels.items()},
        # Untuk kompatibilitas
        "r1": resistances[0] if len(resistances) > 0 else round(piv_r1),
        "r2": resistances[1] if len(resistances) > 1 else round(piv_r2),
        "r3": resistances[2] if len(resistances) > 2 else round(piv_r3),
        "s1": supports[0]    if len(supports)    > 0 else round(piv_s1),
        "s2": supports[1]    if len(supports)    > 1 else round(piv_s2),
        "s3": supports[2]    if len(supports)    > 2 else round(piv_s3),
    }

def detect_candle_pattern(closes, highs, lows):
    if len(closes) < 3: return "Tidak cukup data"
    patterns = []
    c1, h1, l1 = closes[-1], highs[-1], lows[-1]
    o1 = closes[-2]
    c2 = closes[-2]
    o2 = closes[-3]

    body  = abs(c1 - o1)
    rng   = (h1 - l1) or 0.01
    upper = h1 - max(o1, c1)
    lower = min(o1, c1) - l1

    if body / rng < 0.1:
        patterns.append("Doji ⚪")
    if lower > 2 * body and upper < body and c1 > o1:
        patterns.append("Hammer 🔨 (reversal naik)")
    if upper > 2 * body and lower < body:
        patterns.append("Shooting Star ⭐ (reversal turun)")
    if c2 < o2 and c1 > o1 and c1 > o2 and o1 < c2:
        patterns.append("Bullish Engulfing 🟢")
    if c2 > o2 and c1 < o1 and c1 < o2 and o1 > c2:
        patterns.append("Bearish Engulfing 🔴")

    return ", ".join(patterns) if patterns else "Tidak ada pola khusus"

# ══════════════════════════════════════════════════════════════════
#  SCORING 8 INDIKATOR
# ══════════════════════════════════════════════════════════════════

def score_indicators(d: dict, ind: dict) -> dict:
    price  = d["price"]
    scores = {}
    notes  = {}

    rsi = ind.get("rsi")
    if rsi is not None:
        if rsi < 35:   scores["RSI"] = +1; notes["RSI"] = f"RSI {rsi:.1f} — Oversold 🟢"
        elif rsi > 65: scores["RSI"] = -1; notes["RSI"] = f"RSI {rsi:.1f} — Overbought 🔴"
        else:          scores["RSI"] =  0; notes["RSI"] = f"RSI {rsi:.1f} — Netral ⚪"

    macd, signal, hist = ind.get("macd", (None, None, None))
    if macd is not None and signal is not None:
        if macd > signal:  scores["MACD"] = +1; notes["MACD"] = f"MACD bullish crossover 🟢"
        else:              scores["MACD"] = -1; notes["MACD"] = f"MACD bearish crossover 🔴"

    bb_u, bb_m, bb_l = ind.get("bb", (None, None, None))
    if bb_u and bb_l:
        if price <= bb_l:   scores["BB"] = +1; notes["BB"] = f"Harga di Lower Band Rp {fmt(bb_l)} 🟢"
        elif price >= bb_u: scores["BB"] = -1; notes["BB"] = f"Harga di Upper Band Rp {fmt(bb_u)} 🔴"
        else:               scores["BB"] =  0; notes["BB"] = f"Di dalam BB (Mid Rp {fmt(bb_m)}) ⚪"

    stoch_k, _ = ind.get("stoch", (None, None))
    if stoch_k is not None:
        if stoch_k < 20:   scores["Stoch"] = +1; notes["Stoch"] = f"Stoch {stoch_k:.1f} — Oversold 🟢"
        elif stoch_k > 80: scores["Stoch"] = -1; notes["Stoch"] = f"Stoch {stoch_k:.1f} — Overbought 🔴"
        else:              scores["Stoch"] =  0; notes["Stoch"] = f"Stoch {stoch_k:.1f} — Netral ⚪"

    ema9  = ind.get("ema9")
    ema21 = ind.get("ema21")
    if ema9 and ema21:
        if price > ema9 > ema21:   scores["EMA"] = +1; notes["EMA"] = f"Price>EMA9({fmt(ema9)})>EMA21({fmt(ema21)}) 🟢"
        elif price < ema9 < ema21: scores["EMA"] = -1; notes["EMA"] = f"Price<EMA9({fmt(ema9)})<EMA21({fmt(ema21)}) 🔴"
        else:                      scores["EMA"] =  0; notes["EMA"] = f"EMA Mixed ⚪"

    vol_ratio = ind.get("vol_ratio")
    change    = pct(price, d["prev"])
    if vol_ratio is not None:
        if vol_ratio > 1.5 and change > 0:   scores["Volume"] = +1; notes["Volume"] = f"Volume {vol_ratio:.1f}x + naik 🟢"
        elif vol_ratio > 1.5 and change < 0: scores["Volume"] = -1; notes["Volume"] = f"Volume {vol_ratio:.1f}x + turun 🔴"
        else:                                scores["Volume"] =  0; notes["Volume"] = f"Volume normal {vol_ratio:.1f}x ⚪"

    sr  = ind.get("sr", {})
    piv = sr.get("pivot", price)
    if price > sr.get("r1", price * 1.1):  scores["Pivot"] = +1; notes["Pivot"] = f"Breakout R1 Rp {fmt(sr.get('r1'))} 🟢"
    elif price < sr.get("s1", price * 0.9):scores["Pivot"] = -1; notes["Pivot"] = f"Breakdown S1 Rp {fmt(sr.get('s1'))} 🔴"
    elif price > piv:                       scores["Pivot"] = +1; notes["Pivot"] = f"Di atas Pivot Rp {fmt(piv)} 🟢"
    else:                                   scores["Pivot"] = -1; notes["Pivot"] = f"Di bawah Pivot Rp {fmt(piv)} 🔴"

    h52 = d.get("high52")
    l52 = d.get("low52")
    if h52 and l52 and (h52 - l52) > 0:
        pos = (price - l52) / (h52 - l52) * 100
        if pos < 30:    scores["52W"] = +1; notes["52W"] = f"Zona bawah 52W ({pos:.0f}%) — Murah 🟢"
        elif pos > 75:  scores["52W"] = -1; notes["52W"] = f"Zona atas 52W ({pos:.0f}%) — Mahal 🔴"
        else:           scores["52W"] =  0; notes["52W"] = f"Zona tengah 52W ({pos:.0f}%) ⚪"

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
        strength = "⏳ Tunggu konfirmasi"

    return {
        "sinyal": sinyal, "strength": strength,
        "scores": scores, "notes": notes,
        "total": total, "bullish": bullish,
        "bearish": bearish, "total_v": total_v,
    }

# ══════════════════════════════════════════════════════════════════
#  TP/SL BERBASIS LEVEL S/R NYATA
#  Prinsip:
#  - TP1 = Resistance terdekat di atas harga
#  - TP2 = Resistance berikutnya
#  - TP3 = Resistance paling jauh (atau 52W High)
#  - SL  = Di bawah Support terdekat (dengan buffer)
#  - Hanya valid jika Risk/Reward >= 1.5
# ══════════════════════════════════════════════════════════════════

def calc_tp_sl_smart(d: dict, sr: dict, scoring: dict, atr: float) -> dict:
    price    = d["price"]
    sinyal   = scoring["sinyal"]
    resistances = sr.get("resistances", [])
    supports    = sr.get("supports", [])

    # ATR fallback
    if not atr or atr <= 0:
        atr = price * 0.02

    result = {
        "sinyal"   : sinyal,
        "entry"    : round(price),
        "sl"       : None,
        "tp1"      : None,
        "tp2"      : None,
        "tp3"      : None,
        "rr1"      : None,   # Risk/Reward ke TP1
        "rr2"      : None,
        "rr3"      : None,
        "valid"    : False,
        "invalid_reason": "",
    }

    if sinyal == "BELI":
        # ── SL: tepat di bawah S1 dengan buffer 0.3% ──────────────
        if supports:
            sl = supports[0] * 0.997          # sedikit di bawah S1
        else:
            sl = price - 1.5 * atr             # fallback ATR

        # Pastikan SL tidak terlalu jauh (max 8%)
        sl = max(sl, price * 0.92)
        risk = price - sl

        if risk <= 0:
            result["invalid_reason"] = "SL tidak valid (di atas harga)"
            return result

        # ── TP: gunakan level Resistance yang ada ─────────────────
        # Filter resistance yang memberikan RR >= 1.5
        valid_tps = [r for r in resistances if (r - price) / risk >= 1.5]

        if not valid_tps:
            result["invalid_reason"] = (
                f"⚠️ Tidak ada ruang TP yang cukup.\n"
                f"Resistance terdekat Rp {fmt(resistances[0]) if resistances else '-'} "
                f"terlalu dekat dari entry.\n"
                f"Risk/Reward < 1.5 — *Tidak disarankan masuk sekarang.*"
            )
            return result

        # Assign TP1, TP2, TP3
        tp1 = valid_tps[0]
        tp2 = valid_tps[1] if len(valid_tps) > 1 else round(tp1 + 1.0 * atr)
        tp3 = valid_tps[2] if len(valid_tps) > 2 else round(tp1 + 2.0 * atr)

        # Hitung R/R
        rr1 = (tp1 - price) / risk
        rr2 = (tp2 - price) / risk
        rr3 = (tp3 - price) / risk

        result.update({
            "sl"   : round(sl),
            "tp1"  : round(tp1),
            "tp2"  : round(tp2),
            "tp3"  : round(tp3),
            "rr1"  : rr1,
            "rr2"  : rr2,
            "rr3"  : rr3,
            "risk" : round(risk),
            "valid": True,
        })

    elif sinyal == "JUAL":
        # ── SL: tepat di atas R1 ──────────────────────────────────
        if resistances:
            sl = resistances[0] * 1.003
        else:
            sl = price + 1.5 * atr

        sl   = min(sl, price * 1.08)
        risk = sl - price

        if risk <= 0:
            result["invalid_reason"] = "SL tidak valid"
            return result

        valid_tps = [s for s in supports if (price - s) / risk >= 1.5]

        if not valid_tps:
            result["invalid_reason"] = (
                f"⚠️ Tidak ada ruang TP yang cukup.\n"
                f"Support terdekat terlalu dekat dari entry.\n"
                f"Risk/Reward < 1.5 — *Tidak disarankan masuk sekarang.*"
            )
            return result

        tp1 = valid_tps[0]
        tp2 = valid_tps[1] if len(valid_tps) > 1 else round(tp1 - 1.0 * atr)
        tp3 = valid_tps[2] if len(valid_tps) > 2 else round(tp1 - 2.0 * atr)

        rr1 = (price - tp1) / risk
        rr2 = (price - tp2) / risk
        rr3 = (price - tp3) / risk

        result.update({
            "sl"   : round(sl),
            "tp1"  : round(tp1),
            "tp2"  : round(tp2),
            "tp3"  : round(tp3),
            "rr1"  : rr1,
            "rr2"  : rr2,
            "rr3"  : rr3,
            "risk" : round(risk),
            "valid": True,
        })

    else:
        # HOLD — tidak ada TP/SL
        result["invalid_reason"] = "Sinyal HOLD — Tunggu konfirmasi lebih lanjut sebelum masuk posisi."

    return result

# ══════════════════════════════════════════════════════════════════
#  GROQ AI SUMMARY
# ══════════════════════════════════════════════════════════════════

def get_ai_summary(d: dict, ind: dict, scoring: dict, tpsl: dict) -> str:
    sr  = ind.get("sr", {})
    notes_txt = "\n".join([f"- {k}: {v}" for k, v in scoring["notes"].items()])

    tp_info = ""
    if tpsl["valid"]:
        tp_info = (
            f"Entry: Rp {fmt(tpsl['entry'])} | SL: Rp {fmt(tpsl['sl'])} (risk Rp {fmt(tpsl.get('risk'))})\n"
            f"TP1: Rp {fmt(tpsl['tp1'])} (RR {tpsl['rr1']:.1f}x) | "
            f"TP2: Rp {fmt(tpsl['tp2'])} (RR {tpsl['rr2']:.1f}x) | "
            f"TP3: Rp {fmt(tpsl['tp3'])} (RR {tpsl['rr3']:.1f}x)"
        )
    else:
        tp_info = f"Sinyal tidak actionable: {tpsl.get('invalid_reason', '')}"

    prompt = f"""Kamu adalah analis teknikal saham Indonesia profesional.
Buat ringkasan analisis SINGKAT berdasarkan data ini:

SAHAM: {d['symbol']} ({d['name']})
Harga : Rp {fmt(d['price'])} ({pct(d['price'],d['prev']):+.2f}%)
Volume: {fmt_vol(d['volume'])}
52W   : Low {fmt(d['low52'])} | High {fmt(d['high52'])}

HASIL 8 INDIKATOR ({scoring['bullish']} bullish / {scoring['bearish']} bearish dari {scoring['total_v']}):
{notes_txt}

SINYAL: {scoring['sinyal']} — {scoring['strength']}

{tp_info}

Support terdekat: Rp {fmt(sr.get('s1'))} | Resistance terdekat: Rp {fmt(sr.get('r1'))}

Tulis dalam format PERSIS ini (jangan tambah apapun di luar format):

KESIMPULAN:
[2-3 kalimat ringkas tentang kondisi teknikal dan mengapa sinyal ini muncul]

STRATEGI:
[1-2 kalimat cara masuk dan kelola posisi secara konkret]

WASPADAI:
[1 kalimat kondisi spesifik yang membatalkan sinyal ini]

Bahasa Indonesia, singkat, langsung ke poin."""

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type" : "application/json",
        },
        json={
            "model"      : GROQ_MODEL,
            "max_tokens" : 400,
            "temperature": 0.2,
            "messages"   : [
                {"role": "system", "content": "Kamu analis teknikal saham Indonesia. Jawab singkat dan langsung ke poin dalam bahasa Indonesia."},
                {"role": "user",   "content": prompt},
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

# ══════════════════════════════════════════════════════════════════
#  BUILD PESAN TELEGRAM
# ══════════════════════════════════════════════════════════════════

def build_message(d, ind, scoring, tpsl, candle, ai_txt, modal):
    sinyal = scoring["sinyal"]
    change = pct(d["price"], d["prev"])
    sr     = ind.get("sr", {})
    bb_u, bb_m, bb_l = ind.get("bb", (None, None, None))
    ema9  = ind.get("ema9")
    ema21 = ind.get("ema21")
    ema50 = ind.get("ema50")
    price = d["price"]

    if sinyal == "BELI":   bar = "🟢🟢 *SINYAL: BELI* 🟢🟢"
    elif sinyal == "JUAL": bar = "🔴🔴 *SINYAL: JUAL* 🔴🔴"
    else:                  bar = "🟡🟡 *SINYAL: HOLD* 🟡🟡"

    # Parse AI
    kesimpulan = strategi = waspadai = ""
    mode = None
    for line in ai_txt.splitlines():
        l  = line.strip()
        up = l.upper()
        if up.startswith("KESIMPULAN:"): mode = "k"; continue
        elif up.startswith("STRATEGI:"): mode = "s"; continue
        elif up.startswith("WASPADAI:"): mode = "w"; continue
        if   mode == "k" and l: kesimpulan += l + " "
        elif mode == "s" and l: strategi   += l + " "
        elif mode == "w" and l: waspadai   += l + " "

    lines = [
        f"{'📈' if change >= 0 else '📉'} *{d['symbol']}* — {d['name']}",
        f"💵 *Rp {fmt(price)}*  ({change:+.2f}%)  Vol: {fmt_vol(d['volume'])}",
        f"O:{fmt(d['open'])}  H:{fmt(d['high'])}  L:{fmt(d['low'])}",
        f"52W: Rp {fmt(d['low52'])} — Rp {fmt(d['high52'])}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        bar,
        f"Kekuatan : {scoring['strength']}",
        f"Voting   : {scoring['bullish']}🟢 Bullish | {scoring['bearish']}🔴 Bearish | dari {scoring['total_v']} indikator",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # ── TP/SL Section ─────────────────────────────────────────────
    if tpsl["valid"]:
        lot        = int(modal / (price * 100)) if price else 0
        modal_used = lot * 100 * price

        def pl(tp):
            arah = 1 if sinyal == "BELI" else -1
            return arah * lot * 100 * abs(tp - price) - lot * 100 * max(tp, price) * 0.002

        def pl_sl():
            return -(price - tpsl["sl"]) * lot * 100 if sinyal == "BELI" else -(tpsl["sl"] - price) * lot * 100

        lines += [
            f"🎯 *ENTRY     :* Rp {fmt(tpsl['entry'])}",
            "",
            f"✅ *TAKE PROFIT:*",
            f"   🥇 TP1 : Rp {fmt(tpsl['tp1'])}  ({pct(tpsl['tp1'], price):+.1f}%)  RR {tpsl['rr1']:.1f}x",
            f"   🥈 TP2 : Rp {fmt(tpsl['tp2'])}  ({pct(tpsl['tp2'], price):+.1f}%)  RR {tpsl['rr2']:.1f}x",
            f"   🥉 TP3 : Rp {fmt(tpsl['tp3'])}  ({pct(tpsl['tp3'], price):+.1f}%)  RR {tpsl['rr3']:.1f}x",
            "",
            f"🛑 *STOP LOSS  :* Rp {fmt(tpsl['sl'])}  ({pct(tpsl['sl'], price):+.1f}%)",
            f"   Risk per lot: Rp {fmt(tpsl.get('risk', 0) * 100)}",
            "",
        ]

        if lot > 0:
            lines += [
                f"💰 *SIMULASI MODAL Rp {fmt(modal)}:*",
                f"   Beli      : *{lot} lot* ({lot*100:,} lbr)",
                f"   Modal     : Rp {fmt(modal_used)}",
                f"   Sisa      : Rp {fmt(modal - modal_used)}",
                "",
                f"   💵 Profit TP1 : *+Rp {fmt(pl(tpsl['tp1']))}*",
                f"   💵 Profit TP2 : *+Rp {fmt(pl(tpsl['tp2']))}*",
                f"   💵 Profit TP3 : *+Rp {fmt(pl(tpsl['tp3']))}*",
                f"   💸 Rugi SL    : *-Rp {fmt(abs(pl_sl()))}*",
                "",
                "   📌 _TP1→jual 50% | TP2→jual 30% | TP3→jual 20%_",
            ]
        else:
            lines += [
                f"💰 *MODAL Rp {fmt(modal)}:*",
                f"   ⚠️ Butuh min Rp {fmt(price * 100)} untuk 1 lot",
            ]
    else:
        # Sinyal tidak valid / HOLD
        lines += [
            "━━━━━━━━━━━━━━━━━━━━",
            f"⚠️ *STATUS ENTRY:*",
            f"   {tpsl.get('invalid_reason', 'Tidak ada sinyal actionable saat ini.')}",
        ]

    # ── S/R Levels ────────────────────────────────────────────────
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 *SUPPORT & RESISTANCE:*",
    ]
    resistances = sr.get("resistances", [])
    supports    = sr.get("supports",    [])
    for i, r in enumerate(resistances[:3], 1):
        gap = pct(r, price)
        lines.append(f"   🔴 R{i}: Rp {fmt(r)}  ({gap:+.1f}%)")
    lines.append(f"   ⚪ Pivot: Rp {fmt(sr.get('pivot'))}")
    for i, s in enumerate(supports[:3], 1):
        gap = pct(s, price)
        lines.append(f"   🟢 S{i}: Rp {fmt(s)}  ({gap:+.1f}%)")

    fib = sr.get("fib", {})
    if fib:
        lines += [
            "",
            "📐 *FIBONACCI (52W):*",
            f"   23.6%: Rp {fmt(fib.get('fib_236'))}",
            f"   38.2%: Rp {fmt(fib.get('fib_382'))}",
            f"   50.0%: Rp {fmt(fib.get('fib_500'))}",
            f"   61.8%: Rp {fmt(fib.get('fib_618'))}",
        ]

    # ── 8 Indikator ───────────────────────────────────────────────
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📉 *8 INDIKATOR TEKNIKAL:*",
        "",
    ]
    for key, note in scoring["notes"].items():
        lines.append(f"  {note}")

    lines += [
        "",
        f"📊 *Bollinger:* U:{fmt(bb_u)}  M:{fmt(bb_m)}  L:{fmt(bb_l)}",
        f"📈 *EMA:* 9={fmt(ema9)}  21={fmt(ema21)}  50={fmt(ema50)}",
        f"📏 *ATR:* Rp {fmt(ind.get('atr'))}",
        f"🕯️ *Candlestick:* {candle}",
    ]

    # ── AI Summary ────────────────────────────────────────────────
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🤖 *ANALISIS AI:*",
        "",
    ]
    if kesimpulan: lines.append(f"📌 {kesimpulan.strip()}")
    if strategi:   lines.append(f"\n💡 {strategi.strip()}")
    if waspadai:   lines.append(f"\n⚠️ {waspadai.strip()}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
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

    # Hitung semua indikator
    atr_val = calc_atr(highs, lows, closes)
    sr      = calc_sr_levels(d)

    ind = {
        "rsi"      : calc_rsi(closes),
        "macd"     : calc_macd(closes),
        "bb"       : calc_bollinger(closes),
        "stoch"    : calc_stochastic(closes, highs, lows),
        "atr"      : atr_val,
        "vol_ratio": calc_volume_ratio(volumes),
        "sr"       : sr,
        "ema9"     : ema(closes, 9),
        "ema21"    : ema(closes, 21),
        "ema50"    : ema(closes, 50),
    }

    try:    candle = detect_candle_pattern(closes, highs, lows)
    except: candle = "Tidak tersedia"

    scoring = score_indicators(d, ind)
    tpsl    = calc_tp_sl_smart(d, sr, scoring, atr_val)
    ai_txt  = get_ai_summary(d, ind, scoring, tpsl)

    return build_message(d, ind, scoring, tpsl, candle, ai_txt, modal)

# ══════════════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *BEI TRADING SIGNAL BOT v3*\n\n"
        "TP/SL sekarang berdasarkan *level S/R nyata*\n"
        "bukan kalkulasi matematika kaku.\n\n"
        "*PERINTAH:*\n"
        "📊 `/sinyal BBCA` — sinyal + TP/SL\n"
        "📊 `/sinyal BBCA 2000000` — modal kustom\n"
        "💰 `/average BBCA 2 9500 3 9000`\n"
        "✂️ `/cutloss BBCA 1200 2`\n"
        "📋 `/portofolio`\n"
        "❓ `/help`\n\n"
        "_Kirim kode saham langsung: *TLKM*_\n\n"
        "⚠️ _Bukan saran investasi profesional._",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *PANDUAN v3*\n\n"
        "*Cara kerja TP/SL baru:*\n"
        "• TP1/TP2/TP3 diambil dari level *Resistance nyata*\n"
        "  (Pivot + Swing High historis + Fibonacci)\n"
        "• SL diletakkan tepat *di bawah Support nyata*\n"
        "• Sinyal hanya actionable jika *Risk/Reward ≥ 1.5x*\n"
        "• Jika tidak ada ruang yang cukup → bot bilang *JANGAN MASUK*\n\n"
        "*8 Indikator:*\n"
        "RSI · MACD · Bollinger · Stochastic\n"
        "EMA 9/21/50 · Volume · Pivot · Posisi 52W\n\n"
        "📌 *Cara pakai TP:*\n"
        "• TP1 → Jual 50%\n"
        "• TP2 → Jual 30%\n"
        "• TP3 → Jual 20% sisa\n"
        "• SL  → Jual semua, stop rugi",
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
        f"⏳ Menganalisis *{ticker}*...", parse_mode="Markdown")
    try:
        full = run_full_analysis(ticker, modal)
        await msg.edit_text(full, parse_mode="Markdown")
    except Exception as e:
        logger.exception(e)
        await msg.edit_text(f"❌ Error: {str(e)}\nContoh: BBCA TLKM GOTO BBRI ANTM")

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

        sr = calc_sr_levels(d)
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
            f"   🥇 TP1: Rp {fmt(sr['r1'])} ({pct(sr['r1'], avg):+.1f}%) — R1\n"
            f"   🥈 TP2: Rp {fmt(sr['r2'])} ({pct(sr['r2'], avg):+.1f}%) — R2\n"
            f"   🥉 TP3: Rp {fmt(sr['r3'])} ({pct(sr['r3'], avg):+.1f}%) — R3\n"
            f"   🛑 SL : Rp {fmt(sr['s1'])} ({pct(sr['s1'], avg):+.1f}%) — S1\n"
        )
        if pl_val < 0:
            avg2 = (avg + current) / 2
            text += (
                f"\n💡 *Simulasi average down 1x:*\n"
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
        sr      = calc_sr_levels(d)
        current = d["price"]
        pl_val  = (current - buy) * lot * 100
        pl_p    = pct(current, buy)
        status  = "🟢 UNTUNG" if current >= buy else "🔴 RUGI"
        sign    = "+" if pl_val >= 0 else "-"
        s1      = sr.get("s1", current * 0.97)

        if current < s1:
            rek = f"⚠️ Harga di bawah S1 (Rp {fmt(s1)}). *Pertimbangkan cut loss sekarang.*"
        elif current < buy * 0.93:
            rek = f"⚠️ Sudah turun >7%. *Sangat disarankan cut loss segera.*"
        elif rsi and rsi < 35:
            rek = f"💡 RSI {rsi:.1f} oversold — bisa tunggu, tapi monitor S1 Rp {fmt(s1)}."
        elif current < buy * 0.97:
            rek = f"💡 Turun 3-7%. Pantau ketat. Cut jika tembus S1 Rp {fmt(s1)}."
        else:
            rek = f"✅ Masih aman. Monitor S1 di Rp {fmt(s1)}."

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
            f"📊 *SUPPORT (dari chart):*\n"
            f"   S1: Rp {fmt(sr.get('s1'))}  ({pct(sr.get('s1', current), current):+.1f}%)\n"
            f"   S2: Rp {fmt(sr.get('s2'))}  ({pct(sr.get('s2', current), current):+.1f}%)\n"
            f"   S3: Rp {fmt(sr.get('s3'))}  ({pct(sr.get('s3', current), current):+.1f}%)\n\n"
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
        "   • SL selalu di bawah level Support nyata\n"
        "   • Jangan masuk jika RR < 1.5x\n"
        "   • Jangan average down sembarangan\n\n"
        "📌 *Prioritas Sinyal:*\n"
        "   KUAT (6+ indikator) + RR ≥ 2x → *Masuk*\n"
        "   SEDANG (4-5) + RR ≥ 1.5x     → *Hati-hati*\n"
        "   LEMAH (<4) atau RR < 1.5x    → *Skip*\n\n"
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
        raise ValueError("BOT_TOKEN tidak ada!")
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY tidak ada!")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("sinyal",     cmd_sinyal))
    app.add_handler(CommandHandler("analisis",   cmd_sinyal))
    app.add_handler(CommandHandler("average",    cmd_average))
    app.add_handler(CommandHandler("cutloss",    cmd_cutloss))
    app.add_handler(CommandHandler("portofolio", cmd_portofolio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ BEI Signal Bot v3 started!")
    app.run_polling()

if __name__ == "__main__":
    main()
