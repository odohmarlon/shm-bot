"""
BEI Trading Signal Bot v4
Fitur: Sinyal TP/SL, Screener LQ45, Backtest, Jurnal Trading
"""
import os, math, time, json, sqlite3, requests, logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL   = "llama-3.3-70b-versatile"
DB_PATH      = "trading_journal.db"

# ══════════════════════════════════════════════════════════════════
#  DATABASE — Jurnal Trading (SQLite)
# ══════════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            ticker      TEXT    NOT NULL,
            action      TEXT    NOT NULL,  -- BELI / JUAL
            lot         INTEGER NOT NULL,
            price       REAL    NOT NULL,
            total       REAL    NOT NULL,
            notes       TEXT,
            created_at  TEXT    NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            ticker      TEXT    NOT NULL,
            lot         INTEGER NOT NULL,
            avg_price   REAL    NOT NULL,
            tp1         REAL,
            tp2         REAL,
            tp3         REAL,
            sl          REAL,
            opened_at   TEXT    NOT NULL,
            UNIQUE(user_id, ticker)
        )
    """)
    conn.commit()
    conn.close()

def db_add_trade(user_id, ticker, action, lot, price, notes=""):
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    total = lot * 100 * price
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute(
        "INSERT INTO trades (user_id,ticker,action,lot,price,total,notes,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (user_id, ticker.upper(), action, lot, price, total, notes, now)
    )
    # Update posisi
    if action == "BELI":
        c.execute("SELECT lot, avg_price FROM positions WHERE user_id=? AND ticker=?",
                  (user_id, ticker.upper()))
        existing = c.fetchone()
        if existing:
            old_lot, old_avg = existing
            new_lot   = old_lot + lot
            new_avg   = (old_lot * old_avg + lot * price) / new_lot
            c.execute("UPDATE positions SET lot=?, avg_price=?, opened_at=? WHERE user_id=? AND ticker=?",
                      (new_lot, new_avg, now, user_id, ticker.upper()))
        else:
            c.execute(
                "INSERT INTO positions (user_id,ticker,lot,avg_price,opened_at) VALUES (?,?,?,?,?)",
                (user_id, ticker.upper(), lot, price, now)
            )
    elif action == "JUAL":
        c.execute("SELECT lot FROM positions WHERE user_id=? AND ticker=?",
                  (user_id, ticker.upper()))
        existing = c.fetchone()
        if existing:
            new_lot = existing[0] - lot
            if new_lot <= 0:
                c.execute("DELETE FROM positions WHERE user_id=? AND ticker=?",
                          (user_id, ticker.upper()))
            else:
                c.execute("UPDATE positions SET lot=? WHERE user_id=? AND ticker=?",
                          (new_lot, user_id, ticker.upper()))
    conn.commit()
    conn.close()

def db_get_trades(user_id, limit=10):
    conn   = sqlite3.connect(DB_PATH)
    c      = conn.cursor()
    c.execute(
        "SELECT ticker,action,lot,price,total,notes,created_at FROM trades WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def db_get_positions(user_id):
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute(
        "SELECT ticker,lot,avg_price,tp1,tp2,tp3,sl,opened_at FROM positions WHERE user_id=?",
        (user_id,)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def db_get_stats(user_id):
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    # Pasangan beli-jual per ticker
    c.execute(
        "SELECT ticker, action, lot, price, created_at FROM trades WHERE user_id=? ORDER BY id",
        (user_id,)
    )
    rows  = c.fetchall()
    conn.close()

    buys  = {}   # ticker -> [(lot, price)]
    wins  = 0
    losses= 0
    total_pl = 0.0
    trades_closed = 0

    for ticker, action, lot, price, _ in rows:
        if action == "BELI":
            buys.setdefault(ticker, []).append((lot, price))
        elif action == "JUAL" and ticker in buys and buys[ticker]:
            b_lot, b_price = buys[ticker].pop(0)
            pl = (price - b_price) * min(lot, b_lot) * 100
            total_pl += pl
            trades_closed += 1
            if pl > 0: wins += 1
            else:      losses += 1

    win_rate = (wins / trades_closed * 100) if trades_closed else 0
    return {
        "total_trades"  : trades_closed,
        "wins"          : wins,
        "losses"        : losses,
        "win_rate"      : win_rate,
        "total_pl"      : total_pl,
    }

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
#  FETCH DATA
# ══════════════════════════════════════════════════════════════════

def get_stock_data(ticker: str, days: int = 60) -> dict:
    symbol = ticker.upper() + ".JK"
    range_ = f"{days}d" if days <= 60 else "3mo"
    url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={range_}"
    r      = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    res  = data["chart"]["result"]
    if not res:
        raise ValueError(f"Saham {ticker} tidak ditemukan.")
    meta  = res[0]["meta"]
    quote = res[0].get("indicators", {}).get("quote", [{}])[0]
    ts    = res[0].get("timestamp", [])

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
        "timestamps": ts,
    }

# ══════════════════════════════════════════════════════════════════
#  INDIKATOR TEKNIKAL
# ══════════════════════════════════════════════════════════════════

def ema(data, period):
    if len(data) < period: return None
    k   = 2 / (period + 1)
    val = sum(data[:period]) / period
    for x in data[period:]: val = x * k + val * (1 - k)
    return val

def sma(data, period):
    if len(data) < period: return None
    return sum(data[-period:]) / period

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses= [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return 100 - (100 / (1 + ag / al))

def calc_macd(closes):
    if len(closes) < 26: return None, None, None
    macd_vals = []
    for i in range(26, len(closes)+1):
        e12 = ema(closes[:i], 12)
        e26 = ema(closes[:i], 26)
        if e12 and e26: macd_vals.append(e12 - e26)
    if not macd_vals: return None, None, None
    ml = macd_vals[-1]
    if len(macd_vals) < 9: return ml, None, None
    sig = ema(macd_vals, 9)
    return ml, sig, (ml - sig if sig else None)

def calc_bollinger(closes, period=20, std_dev=2):
    if len(closes) < period: return None, None, None
    mid = sma(closes, period)
    if not mid: return None, None, None
    std = math.sqrt(sum((x-mid)**2 for x in closes[-period:]) / period)
    return mid + std_dev*std, mid, mid - std_dev*std

def calc_stochastic(closes, highs, lows, k=14):
    if len(closes) < k: return None, None
    lo = min(lows[-k:]);  hi = max(highs[-k:])
    if hi == lo: return 50, 50
    return (closes[-1]-lo)/(hi-lo)*100, (closes[-1]-lo)/(hi-lo)*100

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return None
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    return sum(trs[-period:]) / min(len(trs), period) if trs else None

def calc_volume_ratio(volumes):
    if len(volumes) < 6: return None
    avg = sum(volumes[-6:-1]) / 5
    return (volumes[-1]/avg) if avg else None

def calc_sr_levels(d: dict) -> dict:
    price = d["price"]; high = d["high"]; low = d["low"]
    closes= d["closes"]; highs= d["highs"]; lows= d["lows"]

    pivot   = (high + low + d["prev"]) / 3
    r       = high - low
    piv_r1  = 2*pivot - low;  piv_r2 = pivot + r; piv_r3 = high + 2*(pivot-low)
    piv_s1  = 2*pivot - high; piv_s2 = pivot - r; piv_s3 = low - 2*(high-pivot)

    # Swing High/Low
    window = 5
    h_data = highs[-30:] if len(highs) >= 30 else highs
    l_data = lows[-30:]  if len(lows)  >= 30 else lows
    swing_h, swing_l = [], []
    for i in range(window, len(h_data)-window):
        if all(h_data[i] >= h_data[i-j] for j in range(1,window+1)) and \
           all(h_data[i] >= h_data[i+j] for j in range(1,window+1)):
            swing_h.append(h_data[i])
        if all(l_data[i] <= l_data[i-j] for j in range(1,window+1)) and \
           all(l_data[i] <= l_data[i+j] for j in range(1,window+1)):
            swing_l.append(l_data[i])

    h52 = d.get("high52") or (max(highs) if highs else price*1.2)
    l52 = d.get("low52")  or (min(lows)  if lows  else price*0.8)
    rng = h52 - l52
    fibs = {k: h52 - rng*v for k,v in
            [("fib_236",0.236),("fib_382",0.382),("fib_500",0.5),("fib_618",0.618),("fib_786",0.786)]}

    raw_r = [piv_r1,piv_r2,piv_r3] + swing_h + [v for v in fibs.values() if v > price]
    raw_s = [piv_s1,piv_s2,piv_s3] + swing_l + [v for v in fibs.values() if v < price]
    min_gap = price * 0.005

    def clean_levels(levels, above):
        f = sorted(set([round(l) for l in levels if (l>price if above else l<price)]),
                   reverse=not above)
        out = []
        for lvl in f:
            if not out or abs(lvl-out[-1]) >= min_gap: out.append(lvl)
        return out

    res = clean_levels(raw_r, True)[:4]
    sup = clean_levels(raw_s, False)[:4]

    return {
        "pivot"      : round(pivot),
        "resistances": res,
        "supports"   : sup,
        "fib"        : {k: round(v) for k,v in fibs.items()},
        "r1": res[0] if res else round(piv_r1),
        "r2": res[1] if len(res)>1 else round(piv_r2),
        "r3": res[2] if len(res)>2 else round(piv_r3),
        "s1": sup[0] if sup else round(piv_s1),
        "s2": sup[1] if len(sup)>1 else round(piv_s2),
        "s3": sup[2] if len(sup)>2 else round(piv_s3),
    }

def detect_candle(closes, highs, lows):
    if len(closes) < 3: return "—"
    c1,h1,l1 = closes[-1],highs[-1],lows[-1]
    o1 = closes[-2]; c2 = closes[-2]; o2 = closes[-3]
    body = abs(c1-o1); rng = (h1-l1) or 0.01
    upper= h1-max(o1,c1); lower=min(o1,c1)-l1
    p = []
    if body/rng < 0.1:                              p.append("Doji ⚪")
    if lower>2*body and upper<body and c1>o1:       p.append("Hammer 🔨")
    if upper>2*body and lower<body:                 p.append("Shooting Star ⭐")
    if c2<o2 and c1>o1 and c1>o2 and o1<c2:        p.append("Bullish Engulfing 🟢")
    if c2>o2 and c1<o1 and c1<o2 and o1>c2:        p.append("Bearish Engulfing 🔴")
    return ", ".join(p) if p else "—"

def score_indicators(d, ind):
    price = d["price"]; scores = {}; notes = {}

    rsi = ind.get("rsi")
    if rsi is not None:
        if rsi < 35:   scores["RSI"]=+1; notes["RSI"]=f"RSI {rsi:.1f} Oversold 🟢"
        elif rsi > 65: scores["RSI"]=-1; notes["RSI"]=f"RSI {rsi:.1f} Overbought 🔴"
        else:          scores["RSI"]= 0; notes["RSI"]=f"RSI {rsi:.1f} Netral ⚪"

    ml,sig,hist = ind.get("macd",(None,None,None))
    if ml is not None and sig is not None:
        if ml>sig: scores["MACD"]=+1; notes["MACD"]="MACD Bullish crossover 🟢"
        else:      scores["MACD"]=-1; notes["MACD"]="MACD Bearish crossover 🔴"

    bu,bm,bl = ind.get("bb",(None,None,None))
    if bu and bl:
        if price<=bl:   scores["BB"]=+1; notes["BB"]=f"Lower Band Rp {fmt(bl)} 🟢"
        elif price>=bu: scores["BB"]=-1; notes["BB"]=f"Upper Band Rp {fmt(bu)} 🔴"
        else:           scores["BB"]= 0; notes["BB"]=f"Mid Band Rp {fmt(bm)} ⚪"

    sk,_ = ind.get("stoch",(None,None))
    if sk is not None:
        if sk<20:   scores["Stoch"]=+1; notes["Stoch"]=f"Stoch {sk:.1f} Oversold 🟢"
        elif sk>80: scores["Stoch"]=-1; notes["Stoch"]=f"Stoch {sk:.1f} Overbought 🔴"
        else:       scores["Stoch"]= 0; notes["Stoch"]=f"Stoch {sk:.1f} Netral ⚪"

    e9 = ind.get("ema9"); e21 = ind.get("ema21")
    if e9 and e21:
        if price>e9>e21:   scores["EMA"]=+1; notes["EMA"]=f"Price>EMA9({fmt(e9)})>EMA21({fmt(e21)}) 🟢"
        elif price<e9<e21: scores["EMA"]=-1; notes["EMA"]=f"Price<EMA9({fmt(e9)})<EMA21({fmt(e21)}) 🔴"
        else:              scores["EMA"]= 0; notes["EMA"]=f"EMA Mixed ⚪"

    vr = ind.get("vol_ratio"); ch = pct(price, d["prev"])
    if vr is not None:
        if vr>1.5 and ch>0:   scores["Vol"]=+1; notes["Vol"]=f"Vol {vr:.1f}x + naik 🟢"
        elif vr>1.5 and ch<0: scores["Vol"]=-1; notes["Vol"]=f"Vol {vr:.1f}x + turun 🔴"
        else:                 scores["Vol"]= 0; notes["Vol"]=f"Vol normal {vr:.1f}x ⚪"

    sr = ind.get("sr",{}); pv = sr.get("pivot",price)
    if price > sr.get("r1", price*1.1):  scores["Pivot"]=+1; notes["Pivot"]=f"Breakout R1 Rp {fmt(sr.get('r1'))} 🟢"
    elif price < sr.get("s1", price*.9): scores["Pivot"]=-1; notes["Pivot"]=f"Breakdown S1 Rp {fmt(sr.get('s1'))} 🔴"
    elif price > pv:                     scores["Pivot"]=+1; notes["Pivot"]=f"Di atas Pivot Rp {fmt(pv)} 🟢"
    else:                                scores["Pivot"]=-1; notes["Pivot"]=f"Di bawah Pivot Rp {fmt(pv)} 🔴"

    h52=d.get("high52"); l52=d.get("low52")
    if h52 and l52 and (h52-l52)>0:
        pos=(price-l52)/(h52-l52)*100
        if pos<30:   scores["52W"]=+1; notes["52W"]=f"Zona bawah 52W ({pos:.0f}%) 🟢"
        elif pos>75: scores["52W"]=-1; notes["52W"]=f"Zona atas 52W ({pos:.0f}%) 🔴"
        else:        scores["52W"]= 0; notes["52W"]=f"Zona tengah 52W ({pos:.0f}%) ⚪"

    total=sum(scores.values())
    bullish=sum(1 for v in scores.values() if v>0)
    bearish=sum(1 for v in scores.values() if v<0)
    sinyal = "BELI" if total>=3 else ("JUAL" if total<=-3 else "HOLD")
    strength = ("💪 KUAT" if (bullish if sinyal=="BELI" else bearish)>=6
                else "👌 SEDANG" if (bullish if sinyal=="BELI" else bearish)>=4
                else "⚠️ LEMAH") if sinyal!="HOLD" else "⏳ Tunggu"
    return {"sinyal":sinyal,"strength":strength,"scores":scores,"notes":notes,
            "total":total,"bullish":bullish,"bearish":bearish,"total_v":len(scores)}

def calc_tp_sl_smart(d, sr, scoring, atr):
    price=d["price"]; sinyal=scoring["sinyal"]
    res=sr.get("resistances",[]); sup=sr.get("supports",[])
    if not atr or atr<=0: atr=price*0.02
    out={"sinyal":sinyal,"entry":round(price),"sl":None,"tp1":None,"tp2":None,"tp3":None,
         "rr1":None,"rr2":None,"rr3":None,"valid":False,"invalid_reason":""}

    if sinyal=="BELI":
        sl   = max((sup[0]*0.997 if sup else price-1.5*atr), price*0.92)
        risk = price-sl
        if risk<=0: out["invalid_reason"]="SL tidak valid"; return out
        valid_tps=[r for r in res if (r-price)/risk>=1.5]
        if not valid_tps:
            out["invalid_reason"]=(f"⚠️ Risk/Reward < 1.5x\nResistance terdekat Rp {fmt(res[0]) if res else '-'} "
                                   f"terlalu dekat.\n*Jangan masuk sekarang — tunggu setup lebih baik.*")
            return out
        tp1=valid_tps[0]
        tp2=valid_tps[1] if len(valid_tps)>1 else round(tp1+atr)
        tp3=valid_tps[2] if len(valid_tps)>2 else round(tp1+2*atr)
        out.update({"sl":round(sl),"tp1":round(tp1),"tp2":round(tp2),"tp3":round(tp3),
                    "rr1":(tp1-price)/risk,"rr2":(tp2-price)/risk,"rr3":(tp3-price)/risk,
                    "risk":round(risk),"valid":True})

    elif sinyal=="JUAL":
        sl   = min((res[0]*1.003 if res else price+1.5*atr), price*1.08)
        risk = sl-price
        if risk<=0: out["invalid_reason"]="SL tidak valid"; return out
        valid_tps=[s for s in sup if (price-s)/risk>=1.5]
        if not valid_tps:
            out["invalid_reason"]=(f"⚠️ Risk/Reward < 1.5x\n*Jangan masuk sekarang.*")
            return out
        tp1=valid_tps[0]
        tp2=valid_tps[1] if len(valid_tps)>1 else round(tp1-atr)
        tp3=valid_tps[2] if len(valid_tps)>2 else round(tp1-2*atr)
        out.update({"sl":round(sl),"tp1":round(tp1),"tp2":round(tp2),"tp3":round(tp3),
                    "rr1":(price-tp1)/risk,"rr2":(price-tp2)/risk,"rr3":(price-tp3)/risk,
                    "risk":round(risk),"valid":True})
    else:
        out["invalid_reason"]="Sinyal HOLD — Tunggu konfirmasi lebih lanjut."
    return out

# ══════════════════════════════════════════════════════════════════
#  GROQ AI
# ══════════════════════════════════════════════════════════════════

def groq_chat(prompt: str, max_tokens=400) -> str:
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},
            json={"model":GROQ_MODEL,"max_tokens":max_tokens,"temperature":0.2,
                  "messages":[
                      {"role":"system","content":"Kamu analis teknikal saham Indonesia profesional. Jawab singkat dan langsung ke poin dalam bahasa Indonesia."},
                      {"role":"user","content":prompt}
                  ]},
            timeout=30
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # Pastikan selalu return string
        return str(content) if content is not None else "Analisis tidak tersedia."
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return "Analisis tidak tersedia saat ini."

def get_ai_summary(d, ind, scoring, tpsl):
    sr = ind.get("sr",{})
    notes_txt = "\n".join([f"- {k}: {v}" for k,v in scoring["notes"].items()])
    def safe_rr(v): return f"{v:.1f}" if v is not None else "-"
    tp_info = (f"Entry:{fmt(tpsl['entry'])} SL:{fmt(tpsl['sl'])} "
               f"TP1:{fmt(tpsl['tp1'])}(RR{safe_rr(tpsl['rr1'])}x) "
               f"TP2:{fmt(tpsl['tp2'])}(RR{safe_rr(tpsl['rr2'])}x) "
               f"TP3:{fmt(tpsl['tp3'])}(RR{safe_rr(tpsl['rr3'])}x)"
               if tpsl["valid"] else f"Tidak actionable: {tpsl.get('invalid_reason','')}")
    prompt = f"""Saham: {d['symbol']} ({d['name']}) | Harga: Rp {fmt(d['price'])} ({pct(d['price'],d['prev']):+.2f}%)
Indikator ({scoring['bullish']} bullish/{scoring['bearish']} bearish): {notes_txt}
Sinyal: {scoring['sinyal']} {scoring['strength']} | {tp_info}
S1:{fmt(sr.get('s1'))} R1:{fmt(sr.get('r1'))}

Format PERSIS:
KESIMPULAN:
[2-3 kalimat kondisi teknikal & alasan sinyal]

STRATEGI:
[1-2 kalimat cara masuk & kelola posisi]

WASPADAI:
[1 kalimat risiko utama]"""
    return groq_chat(prompt, 400)

# ══════════════════════════════════════════════════════════════════
#  PIPELINE ANALISIS
# ══════════════════════════════════════════════════════════════════

def run_analysis(ticker: str) -> tuple:
    d = get_stock_data(ticker)
    closes=d["closes"]; highs=d["highs"]; lows=d["lows"]; volumes=d["volumes"]
    atr_val = calc_atr(highs,lows,closes)
    sr      = calc_sr_levels(d)
    ind = {
        "rsi":calc_rsi(closes),"macd":calc_macd(closes),
        "bb":calc_bollinger(closes),"stoch":calc_stochastic(closes,highs,lows),
        "atr":atr_val,"vol_ratio":calc_volume_ratio(volumes),
        "sr":sr,"ema9":ema(closes,9),"ema21":ema(closes,21),"ema50":ema(closes,50),
    }
    try:    candle = detect_candle(closes,highs,lows)
    except: candle = "—"
    scoring = score_indicators(d,ind)
    tpsl    = calc_tp_sl_smart(d,sr,scoring,atr_val)
    ai_txt  = get_ai_summary(d,ind,scoring,tpsl)
    return d, ind, scoring, tpsl, candle, ai_txt

def build_signal_msg(d, ind, scoring, tpsl, candle, ai_txt, modal):
    sinyal=scoring["sinyal"]; price=d["price"]; change=pct(price,d["prev"])
    sr=ind.get("sr",{}); bb_u,bb_m,bb_l=ind.get("bb",(None,None,None))
    e9=ind.get("ema9"); e21=ind.get("ema21"); e50=ind.get("ema50")

    bar = ("🟢🟢 *SINYAL: BELI* 🟢🟢" if sinyal=="BELI" else
           "🔴🔴 *SINYAL: JUAL* 🔴🔴" if sinyal=="JUAL" else
           "🟡🟡 *SINYAL: HOLD* 🟡🟡")

    # Pastikan ai_txt selalu string
    ai_txt = str(ai_txt) if ai_txt is not None else ""

    # Gunakan nama variabel yang tidak bentrok dengan loop variable (i,s) di bawah
    ai_kesimpulan = ""
    ai_strategi   = ""
    ai_waspadai   = ""
    mode=None
    for line in ai_txt.splitlines():
        ln=str(line).strip(); up=ln.upper()
        if up.startswith("KESIMPULAN:"): mode="k"; continue
        elif up.startswith("STRATEGI:"): mode="s"; continue
        elif up.startswith("WASPADAI:"): mode="w"; continue
        if   mode=="k" and ln: ai_kesimpulan += ln+" "
        elif mode=="s" and ln: ai_strategi   += ln+" "
        elif mode=="w" and ln: ai_waspadai   += ln+" "

    lot=int(modal/(price*100)) if price else 0
    modal_used=lot*100*price

    def pl(tp): return lot*100*(tp-price)-lot*100*max(tp,price)*0.002
    def pl_sl(): return -(price-tpsl["sl"])*lot*100

    lines=[
        f"{'📈'if change>=0 else'📉'} *{d['symbol']}* — {d['name']}",
        f"💵 *Rp {fmt(price)}*  ({change:+.2f}%)  Vol:{fmt_vol(d['volume'])}",
        f"O:{fmt(d['open'])} H:{fmt(d['high'])} L:{fmt(d['low'])}",
        f"52W: Rp {fmt(d['low52'])} — Rp {fmt(d['high52'])}","",
        "━━━━━━━━━━━━━━━━━━━━",bar,
        f"Kekuatan : {scoring['strength']}",
        f"Voting   : {scoring['bullish']}🟢 | {scoring['bearish']}🔴 | dari {scoring['total_v']} indikator",
        "━━━━━━━━━━━━━━━━━━━━","",
    ]

    def safe_rr(v): return f"{v:.1f}" if v is not None else "-"

    if tpsl["valid"]:
        lines+=[
            f"🎯 *ENTRY     :* Rp {fmt(tpsl['entry'])}","",
            "✅ *TAKE PROFIT:*",
            f"   🥇 TP1: Rp {fmt(tpsl['tp1'])}  ({pct(tpsl['tp1'],price):+.1f}%)  RR {safe_rr(tpsl['rr1'])}x",
            f"   🥈 TP2: Rp {fmt(tpsl['tp2'])}  ({pct(tpsl['tp2'],price):+.1f}%)  RR {safe_rr(tpsl['rr2'])}x",
            f"   🥉 TP3: Rp {fmt(tpsl['tp3'])}  ({pct(tpsl['tp3'],price):+.1f}%)  RR {safe_rr(tpsl['rr3'])}x","",
            f"🛑 *STOP LOSS  :* Rp {fmt(tpsl['sl'])}  ({pct(tpsl['sl'],price):+.1f}%)",
            f"   Risk per lot: Rp {fmt(tpsl.get('risk',0)*100)}","",
        ]
        if lot>0:
            lines+=[
                f"💰 *SIMULASI MODAL Rp {fmt(modal)}:*",
                f"   Beli: *{lot} lot* ({lot*100:,} lbr) | Modal: Rp {fmt(modal_used)}",
                f"   💵 TP1: *+Rp {fmt(pl(tpsl['tp1']))}*",
                f"   💵 TP2: *+Rp {fmt(pl(tpsl['tp2']))}*",
                f"   💵 TP3: *+Rp {fmt(pl(tpsl['tp3']))}*",
                f"   💸 SL : *-Rp {fmt(abs(pl_sl()))}*","",
                "   📌 _TP1→50% | TP2→30% | TP3→20%_",
            ]
        else:
            lines.append(f"   ⚠️ Butuh min Rp {fmt(price*100)} untuk 1 lot")
    else:
        lines+=["━━━━━━━━━━━━━━━━━━━━",
                f"⚠️ *STATUS:* {tpsl.get('invalid_reason','')}"]

    lines+=["","━━━━━━━━━━━━━━━━━━━━","📊 *SUPPORT & RESISTANCE:*"]
    for i,r in enumerate(sr.get("resistances",[])[:3],1):
        lines.append(f"   🔴 R{i}: Rp {fmt(r)}  ({pct(r,price):+.1f}%)")
    lines.append(f"   ⚪ Pivot: Rp {fmt(sr.get('pivot'))}")
    for i,s in enumerate(sr.get("supports",[])[:3],1):
        lines.append(f"   🟢 S{i}: Rp {fmt(s)}  ({pct(s,price):+.1f}%)")

    fib=sr.get("fib",{})
    if fib:
        lines+=["","📐 *FIBONACCI:*",
                f"   23.6%:{fmt(fib.get('fib_236'))} 38.2%:{fmt(fib.get('fib_382'))}",
                f"   50.0%:{fmt(fib.get('fib_500'))} 61.8%:{fmt(fib.get('fib_618'))}"]

    lines+=["","━━━━━━━━━━━━━━━━━━━━","📉 *8 INDIKATOR:*",""]
    for _,note in scoring["notes"].items(): lines.append(f"  {note}")
    lines+=[f"","📊 BB: U:{fmt(bb_u)} M:{fmt(bb_m)} L:{fmt(bb_l)}",
            f"📈 EMA: 9={fmt(e9)} 21={fmt(e21)} 50={fmt(e50)}",
            f"📏 ATR: Rp {fmt(ind.get('atr'))} | 🕯️ {candle}"]

    lines+=["","━━━━━━━━━━━━━━━━━━━━","🤖 *ANALISIS AI:*",""]
    if ai_kesimpulan: lines.append(f"📌 {ai_kesimpulan.strip()}")
    if ai_strategi:   lines.append(f"\n💡 {ai_strategi.strip()}")
    if ai_waspadai:   lines.append(f"\n⚠️ {ai_waspadai.strip()}")
    lines+=["","━━━━━━━━━━━━━━━━━━━━",
            "⚠️ _Disclaimer: Bukan saran investasi profesional. DYOR._"]
    # Pastikan semua elemen adalah string sebelum join
    return "\n".join(str(x) for x in lines)

# ══════════════════════════════════════════════════════════════════
#  SCREENER LQ45
# ══════════════════════════════════════════════════════════════════

LQ45 = [
    "AALI","ADRO","AKRA","AMRT","ANTM","ASII","BBCA","BBNI","BBRI","BBTN",
    "BMRI","BRIS","BRPT","BUKA","CPIN","EMTK","EXCL","GOTO","HMSP","ICBP",
    "INCO","INDF","INKP","INTP","ISAT","ITMG","JPFA","JSMR","KLBF","MAPI",
    "MBMA","MDKA","MEDC","MIKA","MNCN","PGAS","PTBA","PTPP","SIDO","SMGR",
    "SMRA","TINS","TLKM","TOWR","TPIA","UNTR","UNVR","WIKA","WSKT","ACES"
]

def quick_score(ticker: str) -> dict | None:
    """Scoring cepat tanpa AI untuk screener."""
    try:
        d = get_stock_data(ticker, days=30)
        closes=d["closes"]; highs=d["highs"]; lows=d["lows"]; volumes=d["volumes"]
        if not closes or len(closes) < 15: return None
        sr  = calc_sr_levels(d)
        ind = {
            "rsi":calc_rsi(closes),"macd":calc_macd(closes),
            "bb":calc_bollinger(closes),"stoch":calc_stochastic(closes,highs,lows),
            "atr":calc_atr(highs,lows,closes),"vol_ratio":calc_volume_ratio(volumes),
            "sr":sr,"ema9":ema(closes,9),"ema21":ema(closes,21),"ema50":ema(closes,50),
        }
        scoring = score_indicators(d,ind)
        change  = pct(d["price"],d["prev"])
        return {
            "ticker" : ticker,
            "price"  : d["price"],
            "change" : change,
            "sinyal" : scoring["sinyal"],
            "bullish": scoring["bullish"],
            "bearish": scoring["bearish"],
            "total_v": scoring["total_v"],
            "strength":scoring["strength"],
        }
    except:
        return None

# ══════════════════════════════════════════════════════════════════
#  BACKTEST
# ══════════════════════════════════════════════════════════════════

def run_backtest(ticker: str, days: int = 60) -> dict:
    """
    Simulasi sinyal harian selama N hari ke belakang.
    Setiap hari: hitung scoring dari data sebelum hari itu,
    lalu cek apakah harga naik/turun di hari berikutnya.
    """
    d = get_stock_data(ticker, days=days+10)
    closes=d["closes"]; highs=d["highs"]; lows=d["lows"]; volumes=d["volumes"]
    if len(closes) < 20:
        raise ValueError("Data tidak cukup untuk backtest.")

    results = []
    # Mulai dari index 20 (butuh data historis cukup)
    for i in range(20, len(closes)-1):
        sub_c = closes[:i+1]; sub_h = highs[:i+1]
        sub_l = lows[:i+1];   sub_v = volumes[:i+1] if i<len(volumes) else volumes

        price_today = sub_c[-1]
        price_next  = closes[i+1]     # harga besok (untuk evaluasi)

        # Hitung indikator
        fake_d = {**d, "price":price_today, "prev":sub_c[-2] if len(sub_c)>1 else price_today,
                  "high":sub_h[-1],"low":sub_l[-1],
                  "closes":sub_c,"highs":sub_h,"lows":sub_l,"volumes":sub_v}
        try:
            sr  = calc_sr_levels(fake_d)
            ind = {
                "rsi":calc_rsi(sub_c),"macd":calc_macd(sub_c),
                "bb":calc_bollinger(sub_c),"stoch":calc_stochastic(sub_c,sub_h,sub_l),
                "atr":calc_atr(sub_h,sub_l,sub_c),"vol_ratio":calc_volume_ratio(sub_v),
                "sr":sr,"ema9":ema(sub_c,9),"ema21":ema(sub_c,21),"ema50":ema(sub_c,50),
            }
            scoring = score_indicators(fake_d,ind)
            sinyal  = scoring["sinyal"]

            if sinyal in ("BELI","JUAL"):
                actual_up = price_next > price_today
                correct   = (sinyal=="BELI" and actual_up) or (sinyal=="JUAL" and not actual_up)
                ret       = pct(price_next, price_today)
                results.append({
                    "sinyal" : sinyal,
                    "correct": correct,
                    "return" : ret,
                    "bullish": scoring["bullish"],
                    "bearish": scoring["bearish"],
                })
        except:
            continue

    if not results:
        raise ValueError("Tidak ada sinyal yang terbentuk dalam periode backtest.")

    total  = len(results)
    wins   = sum(1 for r in results if r["correct"])
    beli   = [r for r in results if r["sinyal"]=="BELI"]
    jual   = [r for r in results if r["sinyal"]=="JUAL"]
    beli_w = sum(1 for r in beli if r["correct"])
    jual_w = sum(1 for r in jual if r["correct"])

    # Akurasi berdasarkan kekuatan sinyal (bullish >= 5)
    strong = [r for r in results if r["bullish"]>=5 or r["bearish"]>=5]
    strong_w = sum(1 for r in strong if r["correct"])

    avg_ret_win  = sum(abs(r["return"]) for r in results if r["correct"])  / wins  if wins  else 0
    avg_ret_loss = sum(abs(r["return"]) for r in results if not r["correct"]) / (total-wins) if (total-wins) else 0

    return {
        "ticker"       : ticker,
        "days"         : days,
        "total_signals": total,
        "win_rate"     : wins/total*100,
        "wins"         : wins,
        "losses"       : total-wins,
        "beli_total"   : len(beli),
        "beli_win_rate": beli_w/len(beli)*100 if beli else 0,
        "jual_total"   : len(jual),
        "jual_win_rate": jual_w/len(jual)*100 if jual else 0,
        "strong_total" : len(strong),
        "strong_wr"    : strong_w/len(strong)*100 if strong else 0,
        "avg_ret_win"  : avg_ret_win,
        "avg_ret_loss" : avg_ret_loss,
    }

# ══════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *BEI TRADING BOT v4 — PRO EDITION*\n\n"
        "*📊 ANALISIS:*\n"
        "  `/sinyal BBCA` — sinyal + TP/SL berbasis S/R nyata\n"
        "  `/sinyal BBCA 2000000` — modal kustom\n\n"
        "*🔍 SCREENER:*\n"
        "  `/screener` — scan LQ45 cari yang bullish\n"
        "  `/screener 5` — top 5 saham terkuat\n\n"
        "*📈 BACKTEST:*\n"
        "  `/backtest BBCA` — uji akurasi sinyal 60 hari\n"
        "  `/backtest BBCA 30` — 30 hari\n\n"
        "*📋 JURNAL TRADING:*\n"
        "  `/beli BBCA 2 9500` — catat beli 2 lot @9500\n"
        "  `/jual BBCA 1 9800` — catat jual 1 lot @9800\n"
        "  `/jurnal` — lihat riwayat transaksi\n"
        "  `/posisi` — posisi aktif\n"
        "  `/statistik` — win rate & total P&L\n\n"
        "*🛠 TOOLS:*\n"
        "  `/average BBCA 2 9500 3 9000`\n"
        "  `/cutloss BBCA 1200 2`\n"
        "  `/portofolio`\n\n"
        "_Ketik kode saham langsung: *TLKM*_\n\n"
        "⚠️ _Bukan saran investasi profesional._",
        parse_mode="Markdown"
    )

async def cmd_sinyal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ Format: `/sinyal BBCA`", parse_mode="Markdown"); return
    ticker = ctx.args[0].upper()
    modal  = 1_000_000
    if len(ctx.args)>=2:
        try: modal=int(ctx.args[1])
        except: pass
    msg = await update.message.reply_text(f"⏳ Menganalisis *{ticker}*...", parse_mode="Markdown")
    try:
        d,ind,scoring,tpsl,candle,ai_txt = run_analysis(ticker)
        text = build_signal_msg(d,ind,scoring,tpsl,candle,ai_txt,modal)
        # Pastikan text adalah string bersih sebelum kirim
        if not isinstance(text, str):
            text = str(text)
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Error sinyal {ticker}:\n{tb}")
        # Tampilkan error detail untuk debug
        short_err = str(e)[:200]
        await msg.edit_text(f"❌ Error: {short_err}\n\nCoba lagi atau hubungi admin.")

# ── SCREENER ──────────────────────────────────────────────────────

async def cmd_screener(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    top_n = 5
    if ctx.args:
        try: top_n = int(ctx.args[0])
        except: pass
    top_n = min(max(top_n, 3), 10)

    msg = await update.message.reply_text(
        f"🔍 *Scanning {len(LQ45)} saham LQ45...*\nIni butuh 1-2 menit, sabar ya 😊",
        parse_mode="Markdown"
    )

    bullish_list = []
    done = 0
    for ticker in LQ45:
        result = quick_score(ticker)
        if result and result["sinyal"] == "BELI":
            bullish_list.append(result)
        done += 1
        # Update progress tiap 10 saham
        if done % 10 == 0:
            try:
                await msg.edit_text(
                    f"🔍 Scanning... {done}/{len(LQ45)} saham\n"
                    f"Ditemukan: {len(bullish_list)} bullish sejauh ini",
                    parse_mode="Markdown"
                )
            except: pass
        time.sleep(0.3)  # Rate limit

    # Sort by jumlah bullish indikator
    bullish_list.sort(key=lambda x: x["bullish"], reverse=True)
    top = bullish_list[:top_n]

    if not top:
        await msg.edit_text(
            "😐 *Tidak ada saham LQ45 dengan sinyal BELI saat ini.*\n"
            "Pasar sedang bearish atau sideways. Lebih baik tunggu dulu.",
            parse_mode="Markdown"
        )
        return

    lines = [
        f"🔍 *SCREENER LQ45 — TOP {len(top)} BULLISH*",
        f"📅 Scan: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Ditemukan {len(bullish_list)} dari {len(LQ45)} saham bullish",
        "━━━━━━━━━━━━━━━━━━━━","",
    ]

    for i, r in enumerate(top, 1):
        medal = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"][i-1]
        strength = ("💪 KUAT" if r["bullish"]>=6 else
                    "👌 SEDANG" if r["bullish"]>=4 else "⚠️ LEMAH")
        lines += [
            f"{medal} *{r['ticker']}*  Rp {fmt(r['price'])}  ({r['change']:+.1f}%)",
            f"   🟢 {r['bullish']}/{r['total_v']} indikator bullish — {strength}",
            "",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "💡 Analisis detail: `/sinyal BBCA`",
        "⚠️ _Screener bukan rekomendasi beli. Selalu analisis lebih lanjut._"
    ]
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")

# ── BACKTEST ──────────────────────────────────────────────────────

async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ Format: `/backtest BBCA` atau `/backtest BBCA 30`",
                                         parse_mode="Markdown"); return
    ticker = ctx.args[0].upper()
    days   = 60
    if len(ctx.args)>=2:
        try: days = int(ctx.args[1])
        except: pass
    days = min(max(days, 20), 90)

    msg = await update.message.reply_text(
        f"⏳ Backtest *{ticker}* selama {days} hari...", parse_mode="Markdown")
    try:
        bt = run_backtest(ticker, days)

        # Rating akurasi
        wr = bt["win_rate"]
        if wr >= 70:   rating = "🌟🌟🌟 SANGAT AKURAT"
        elif wr >= 60: rating = "🌟🌟 CUKUP AKURAT"
        elif wr >= 50: rating = "🌟 LUMAYAN"
        else:          rating = "⚠️ KURANG AKURAT"

        swr = bt["strong_wr"]
        text = (
            f"📈 *BACKTEST — {bt['ticker']}*\n"
            f"📅 Periode: {bt['days']} hari ke belakang\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *HASIL KESELURUHAN:*\n"
            f"   Total sinyal  : {bt['total_signals']} sinyal\n"
            f"   Win           : {bt['wins']} ✅\n"
            f"   Loss          : {bt['losses']} ❌\n"
            f"   *Win Rate     : {wr:.1f}%*\n"
            f"   Rating        : {rating}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 *SINYAL BELI:*\n"
            f"   Total: {bt['beli_total']} sinyal\n"
            f"   Akurasi: *{bt['beli_win_rate']:.1f}%*\n\n"
            f"🔴 *SINYAL JUAL:*\n"
            f"   Total: {bt['jual_total']} sinyal\n"
            f"   Akurasi: *{bt['jual_win_rate']:.1f}%*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💪 *SINYAL KUAT (≥5 indikator):*\n"
            f"   Total: {bt['strong_total']} sinyal\n"
            f"   Akurasi: *{swr:.1f}%*\n"
            f"   {'✅ Lebih akurat dari sinyal biasa!' if swr > wr else '⚪ Tidak berbeda signifikan'}\n\n"
            f"📊 *RATA-RATA RETURN:*\n"
            f"   Saat benar  : +{bt['avg_ret_win']:.2f}%\n"
            f"   Saat salah  : -{bt['avg_ret_loss']:.2f}%\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *Kesimpulan:*\n"
        )

        if wr >= 60 and swr >= 65:
            text += f"Bot cukup akurat untuk {ticker}. Prioritaskan sinyal KUAT (≥5 indikator)."
        elif wr >= 50:
            text += f"Akurasi sedang. Gunakan sebagai konfirmasi, bukan satu-satunya acuan."
        else:
            text += f"Akurasi rendah untuk {ticker}. Tambahkan analisis manual sebelum masuk."

        text += "\n\n⚠️ _Backtest ≠ jaminan hasil masa depan. Past performance is not indicative of future results._"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.exception(e)
        await msg.edit_text(f"❌ Error backtest: {str(e)}")

# ── JURNAL TRADING ────────────────────────────────────────────────

async def cmd_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # /beli BBCA 2 9500 [catatan opsional]
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text(
            "❌ Format: `/beli BBCA 2 9500` atau `/beli BBCA 2 9500 bottom fishing`",
            parse_mode="Markdown"); return
    ticker = ctx.args[0].upper()
    try:
        lot   = int(ctx.args[1])
        price = float(ctx.args[2])
    except:
        await update.message.reply_text("❌ Format angka salah."); return
    notes = " ".join(ctx.args[3:]) if len(ctx.args)>3 else ""
    user_id = update.effective_user.id
    db_add_trade(user_id, ticker, "BELI", lot, price, notes)
    total = lot * 100 * price
    await update.message.reply_text(
        f"✅ *Transaksi BELI dicatat!*\n\n"
        f"📌 Saham : {ticker}\n"
        f"📌 Lot   : {lot} lot ({lot*100:,} lbr)\n"
        f"📌 Harga : Rp {fmt(price)}\n"
        f"📌 Total : Rp {fmt(total)}\n"
        f"📌 Waktu : {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
        f"{'📌 Catatan: '+notes if notes else ''}\n\n"
        f"Lihat posisi: /posisi | Jurnal: /jurnal",
        parse_mode="Markdown"
    )

async def cmd_jual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text(
            "❌ Format: `/jual BBCA 1 9800`", parse_mode="Markdown"); return
    ticker = ctx.args[0].upper()
    try:
        lot   = int(ctx.args[1])
        price = float(ctx.args[2])
    except:
        await update.message.reply_text("❌ Format angka salah."); return
    notes   = " ".join(ctx.args[3:]) if len(ctx.args)>3 else ""
    user_id = update.effective_user.id

    # Cek posisi
    positions = db_get_positions(user_id)
    pos = next((p for p in positions if p[0]==ticker), None)

    db_add_trade(user_id, ticker, "JUAL", lot, price, notes)
    total = lot * 100 * price

    pl_txt = ""
    if pos:
        avg   = pos[2]
        pl    = (price - avg) * lot * 100
        pl_p  = pct(price, avg)
        sign  = "+" if pl>=0 else "-"
        emoji = "🟢" if pl>=0 else "🔴"
        pl_txt= f"\n{emoji} *P&L  : {sign}Rp {fmt(abs(pl))} ({pl_p:+.2f}%)*"

    await update.message.reply_text(
        f"✅ *Transaksi JUAL dicatat!*\n\n"
        f"📌 Saham : {ticker}\n"
        f"📌 Lot   : {lot} lot ({lot*100:,} lbr)\n"
        f"📌 Harga : Rp {fmt(price)}\n"
        f"📌 Total : Rp {fmt(total)}"
        f"{pl_txt}\n"
        f"📌 Waktu : {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Lihat statistik: /statistik",
        parse_mode="Markdown"
    )

async def cmd_jurnal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    trades  = db_get_trades(user_id, limit=15)
    if not trades:
        await update.message.reply_text(
            "📋 Jurnal masih kosong.\nMulai catat: `/beli BBCA 2 9500`",
            parse_mode="Markdown"); return

    lines = ["📋 *JURNAL TRADING — 15 Terakhir*","━━━━━━━━━━━━━━━━━━━━",""]
    for ticker,action,lot,price,total,notes,created_at in trades:
        emoji = "🟢" if action=="BELI" else "🔴"
        lines.append(f"{emoji} *{action}* {ticker}  {lot}lot @Rp{fmt(price)}")
        lines.append(f"   Total: Rp {fmt(total)} | {created_at}")
        if notes: lines.append(f"   📝 {notes}")
        lines.append("")

    lines += ["━━━━━━━━━━━━━━━━━━━━",
              "📊 Statistik: /statistik | Posisi: /posisi"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_posisi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id   = update.effective_user.id
    positions = db_get_positions(user_id)
    if not positions:
        await update.message.reply_text(
            "📊 Belum ada posisi aktif.\nCatat beli: `/beli BBCA 2 9500`",
            parse_mode="Markdown"); return

    lines = ["📊 *POSISI AKTIF*","━━━━━━━━━━━━━━━━━━━━",""]
    total_modal = 0
    total_pl    = 0

    for ticker,lot,avg_price,tp1,tp2,tp3,sl,opened_at in positions:
        try:
            d       = get_stock_data(ticker, days=5)
            current = d["price"]
            pl      = (current - avg_price) * lot * 100
            pl_p    = pct(current, avg_price)
            total_modal += avg_price * lot * 100
            total_pl    += pl
            emoji   = "🟢" if pl>=0 else "🔴"
            sign    = "+" if pl>=0 else "-"
            lines += [
                f"*{ticker}*  {lot} lot  avg Rp {fmt(avg_price)}",
                f"   Skrg: Rp {fmt(current)}  {emoji} {sign}Rp {fmt(abs(pl))} ({pl_p:+.2f}%)",
                f"   Masuk: {opened_at}",
                "",
            ]
        except:
            lines += [f"*{ticker}*  {lot} lot  avg Rp {fmt(avg_price)}  _(gagal ambil harga)_",""]

    sign = "+" if total_pl>=0 else "-"
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"💼 Total Modal Aktif : Rp {fmt(total_modal)}",
        f"📊 Total Unrealized P&L: *{sign}Rp {fmt(abs(total_pl))}*",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_statistik(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats   = db_get_stats(user_id)
    if stats["total_trades"] == 0:
        await update.message.reply_text(
            "📊 Belum ada transaksi selesai (beli+jual).\nMulai catat: `/beli BBCA 2 9500`",
            parse_mode="Markdown"); return

    wr    = stats["win_rate"]
    pl    = stats["total_pl"]
    sign  = "+" if pl>=0 else "-"
    emoji = "🟢" if pl>=0 else "🔴"

    rating = ("🌟🌟🌟 Excellent!" if wr>=70 else
              "🌟🌟 Good"        if wr>=55 else
              "🌟 Perlu improve" if wr>=45 else
              "⚠️ Perlu evaluasi")

    text = (
        f"📊 *STATISTIK TRADING KAMU*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Total Trade Selesai : {stats['total_trades']}\n"
        f"✅ Win                 : {stats['wins']}\n"
        f"❌ Loss                : {stats['losses']}\n"
        f"🎯 *Win Rate           : {wr:.1f}%*\n"
        f"   {rating}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} *Total Realized P&L : {sign}Rp {fmt(abs(pl))}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Tips:*\n"
    )
    if wr >= 60:
        text += "Win rate bagus! Pertahankan disiplin SL dan strategi TP bertahap."
    elif wr >= 45:
        text += "Win rate sedang. Review trade yang loss — apakah sudah pasang SL?"
    else:
        text += "Win rate perlu diperbaiki. Coba hanya masuk saat sinyal KUAT (6+ indikator)."

    text += "\n\nLihat posisi: /posisi | Jurnal: /jurnal"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_average(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text("❌ Format: `/average BBCA 2 9500 3 9000`", parse_mode="Markdown"); return
    ticker = ctx.args[0].upper(); nums = ctx.args[1:]
    if len(nums)%2!=0:
        await update.message.reply_text("❌ Pasangan lot-harga harus genap."); return
    try: lp = [(int(nums[i]),float(nums[i+1])) for i in range(0,len(nums),2)]
    except: await update.message.reply_text("❌ Format angka salah."); return

    msg = await update.message.reply_text(f"⏳ Mengambil harga *{ticker}*...", parse_mode="Markdown")
    try:
        d = get_stock_data(ticker)
        current=d["price"]; total_lot=sum(l for l,_ in lp)
        total_modal=sum(l*100*p for l,p in lp); avg=total_modal/(total_lot*100)
        pl_val=(current-avg)*total_lot*100; pl_p=pct(current,avg)
        status="🟢 UNTUNG" if pl_val>=0 else "🔴 RUGI"; sign="+" if pl_val>=0 else "-"
        transaksi="\n".join([f"  Tx{i+1}: {l} lot @ Rp {fmt(p)}" for i,(l,p) in enumerate(lp)])
        sr=calc_sr_levels(d)
        text=(f"📊 *AVERAGE — {ticker}*\n\n{transaksi}\n\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"📌 Avg Price  : *Rp {fmt(avg)}*\n"
              f"📌 Total      : *{total_lot} lot* ({total_lot*100:,} lbr)\n"
              f"📌 Modal      : Rp {fmt(total_modal)}\n"
              f"📌 Harga Skrg : Rp {fmt(current)}\n"
              f"📌 P&L        : *{sign}Rp {fmt(abs(pl_val))} ({pl_p:+.2f}%)*\n"
              f"📌 Status     : {status}\n\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"🎯 *TARGET DARI AVG:*\n"
              f"   🥇 TP1: Rp {fmt(sr['r1'])} ({pct(sr['r1'],avg):+.1f}%) — R1\n"
              f"   🥈 TP2: Rp {fmt(sr['r2'])} ({pct(sr['r2'],avg):+.1f}%) — R2\n"
              f"   🥉 TP3: Rp {fmt(sr['r3'])} ({pct(sr['r3'],avg):+.1f}%) — R3\n"
              f"   🛑 SL : Rp {fmt(sr['s1'])} ({pct(sr['s1'],avg):+.1f}%) — S1\n")
        if pl_val<0:
            avg2=(avg+current)/2
            text+=(f"\n💡 *Simulasi average down 1x:*\n"
                   f"   Avg baru ≈ Rp {fmt(avg2)}\n"
                   f"   Perlu naik {abs(pct(avg,avg2)):.1f}% untuk BEP\n")
        text+="\n⚠️ _Average down hanya jika yakin fundamentalnya bagus._"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e: await msg.edit_text(f"❌ {str(e)}")

async def cmd_cutloss(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args)<2:
        await update.message.reply_text("❌ Format: `/cutloss BBCA 1200 2`", parse_mode="Markdown"); return
    ticker=ctx.args[0].upper()
    try: buy=float(ctx.args[1])
    except: await update.message.reply_text("❌ Harga tidak valid."); return
    lot=1
    if len(ctx.args)>=3:
        try: lot=int(ctx.args[2])
        except: pass
    msg = await update.message.reply_text(f"⏳ Analisis cut loss *{ticker}*...", parse_mode="Markdown")
    try:
        d=get_stock_data(ticker); rsi=calc_rsi(d["closes"]); sr=calc_sr_levels(d)
        current=d["price"]; pl_val=(current-buy)*lot*100; pl_p=pct(current,buy)
        status="🟢 UNTUNG" if current>=buy else "🔴 RUGI"; sign="+" if pl_val>=0 else "-"
        s1=sr.get("s1",current*0.97)
        if current<s1:       rek=f"⚠️ Di bawah S1 (Rp {fmt(s1)}). *Cut loss sekarang.*"
        elif current<buy*.93:rek=f"⚠️ Turun >7%. *Sangat disarankan cut loss.*"
        elif rsi and rsi<35: rek=f"💡 RSI {rsi:.1f} oversold — bisa tunggu, pantau S1 Rp {fmt(s1)}."
        elif current<buy*.97:rek=f"💡 Turun 3-7%. Cut jika tembus S1 Rp {fmt(s1)}."
        else:                rek=f"✅ Masih aman. Monitor S1 di Rp {fmt(s1)}."
        text=(f"✂️ *CUT LOSS — {ticker}*\n\n"
              f"📌 Harga Beli : Rp {fmt(buy)}\n"
              f"📌 Harga Skrg : Rp {fmt(current)}\n"
              f"📌 Posisi     : {lot} lot\n"
              f"📌 Status     : {status} ({pl_p:+.2f}%)\n"
              f"📌 P&L        : *{sign}Rp {fmt(abs(pl_val))}*\n"
              f"📌 RSI        : {f'{rsi:.1f}' if rsi else '-'}\n\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"🛑 *LEVEL SL:*\n"
              f"   -5%  : Rp {fmt(buy*.95)}\n"
              f"   -7%  : Rp {fmt(buy*.93)}\n"
              f"   -10% : Rp {fmt(buy*.90)}\n\n"
              f"📊 *SUPPORT (chart):*\n"
              f"   S1: Rp {fmt(sr.get('s1'))}  S2: Rp {fmt(sr.get('s2'))}  S3: Rp {fmt(sr.get('s3'))}\n\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"{rek}\n\n⚠️ _Bukan saran investasi profesional._")
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e: await msg.edit_text(f"❌ {str(e)}")

async def cmd_portofolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *STRATEGI PORTOFOLIO MODAL Rp 1 JUTA*\n\n"
        "💡 *Alokasi:*\n"
        "   60% → 1 saham utama\n   30% → 1 saham cadangan\n   10% → Cash\n\n"
        "🎯 *Strategi TP:*\n"
        "   TP1→50% | TP2→30% | TP3→20%\n\n"
        "🛑 *Aturan SL:*\n"
        "   • SL di bawah Support nyata\n"
        "   • Jangan masuk jika RR < 1.5x\n\n"
        "📌 *Prioritas Sinyal:*\n"
        "   6+ indikator + RR≥2x → Masuk\n"
        "   4-5 indikator + RR≥1.5x → Hati-hati\n"
        "   <4 indikator atau RR<1.5x → Skip\n\n"
        "💪 *Konsistensi > Profit besar sekali!*",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *PANDUAN LENGKAP*\n\n"
        "*/sinyal BBCA* — analisis 8 indikator + TP/SL dari S/R nyata\n"
        "*/screener* — scan LQ45, cari saham bullish\n"
        "*/backtest BBCA 60* — uji akurasi sinyal historis\n"
        "*/beli BBCA 2 9500* — catat transaksi beli\n"
        "*/jual BBCA 1 9800* — catat transaksi jual\n"
        "*/jurnal* — riwayat transaksi\n"
        "*/posisi* — posisi aktif + unrealized P&L\n"
        "*/statistik* — win rate & total realized P&L\n"
        "*/average BBCA 2 9500 3 9000* — hitung average\n"
        "*/cutloss BBCA 1200 2* — analisis cut loss\n\n"
        "📌 TP valid hanya jika Risk/Reward ≥ 1.5x",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper().split()[0]
    if text.isalpha() and 2<=len(text)<=6:
        ctx.args=[text]; await cmd_sinyal(update,ctx)
    else:
        await update.message.reply_text(
            "❓ Ketik `/help` untuk panduan, atau kirim kode saham: *BBCA*",
            parse_mode="Markdown")

# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:    raise ValueError("BOT_TOKEN tidak ada!")
    if not GROQ_API_KEY: raise ValueError("GROQ_API_KEY tidak ada!")
    init_db()
    logger.info("✅ Database jurnal trading siap.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("sinyal",     cmd_sinyal))
    app.add_handler(CommandHandler("analisis",   cmd_sinyal))
    app.add_handler(CommandHandler("screener",   cmd_screener))
    app.add_handler(CommandHandler("backtest",   cmd_backtest))
    app.add_handler(CommandHandler("beli",       cmd_beli))
    app.add_handler(CommandHandler("jual",       cmd_jual))
    app.add_handler(CommandHandler("jurnal",     cmd_jurnal))
    app.add_handler(CommandHandler("posisi",     cmd_posisi))
    app.add_handler(CommandHandler("statistik",  cmd_statistik))
    app.add_handler(CommandHandler("average",    cmd_average))
    app.add_handler(CommandHandler("cutloss",    cmd_cutloss))
    app.add_handler(CommandHandler("portofolio", cmd_portofolio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 BEI Trading Bot v4 PRO started!")
    app.run_polling()

if __name__ == "__main__":
    main()
