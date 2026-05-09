import os

# Configuration - Ambil dari Environment Variables (Railway/VPS)
# Set di Railway Dashboard: Settings > Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Groq AI Configuration (untuk natural language analysis)
# Set di Railway Dashboard: Settings > Variables
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama3-70b-8192")

# Database
# Railway: Gunakan /data (persistent) atau fallback ke /var/data
# Jika running di local, akan menggunakan folder project
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "trading_journal.db"))

# Indonesian stock suffix for Yahoo Finance
JK_SUFFIX = ".JK"

# LQ45 stocks (Indonesian index)
LQ45_STOCKS = [
    "ACES", "ADHI", "ADMR", "ADRO", "AKPI", "AKRO", "AMMN", "AMRT", "ANTM",
    "ARTO", "ASII", "AUTO", "BBCA", "BBNI", "BBRI", "BBTN", "BBTN", "BDMN",
    "BFIN", "BIPI", "BKSL", "BOGA", "BRIS", "BRPT", "BSDE", "BTPS", "BULL",
    "CPIN", "CPRO", "CTRA", "DGGI", "DMAS", "DOID", "ELSA", "EMTK", "ENRG",
    "ERAA", "ESSA", "EXCL", "FILM", "FLMC", "GAMA", "GEMS", "GIAA", "GOTO",
    "HRUM", "HRUM", "IBFN", "ICBP", "INCO", "INDF", "INKP", "INTP", "ITMG",
    "JBSS", "JPRIME", "KAEF", "KBAG", "KLBF", "LINK", "LPGI", "LPPF", "LTLS",
    "MAPI", "MBMA", "MDKA", "MEDC", "MIKA", "MINT", "MITI", "MLBI", "MPPA",
    "MTDL", "MYOR", "NCKL", "NISP", "NRCA", "OILS", "PGAS", "PGEO", "PII",
    "PJAA", "PKPK", "PMMP", "PNBN", "PNBS", "PNIN", "POLL", "PPGL", "PPRE",
    "PSMM", "PTBA", "PTDU", "PTPP", "PTRO", "PTSN", "PUDP", "RAJA", "RDTX",
    "RISE", "RUIS", "SAME", "SCRC", "SCYA", "SFAN", "SIDO", "SILK", "SILO",
    "SMGR", "SMRA", "SMSM", "SNSA", "SOHO", "SPTO", "SRTG", "SSIA", "SSTM",
    "STAR", "STTP", "SURE", "TAXI", "TAMB", "TAPG", "TELE", "TFAS", "TFCO",
    "TGHI", "TINS", "TKIM", "TLKM", "TMAS", "TNCA", "TOBA", "TOPS", "TPIA",
    "TRIM", "TRIS", "TRUG", "TSPC", "TUFI", "UANG", "UBHD", "ULOG", "UMUM",
    "UNIC", "UNPP", "UNTR", "UNVR", "UPST", "VICI", "VICO", "VINS", "VISI",
    "VIVA", "VODE", "WIFI", "WIIM", "WIKA", "WIRG", "WMII", "WMPP", "WSBP",
    "WTON", "XIXI", "YPAS", "YELO", "ZETA"
]

# Technical Analysis defaults
DEFAULT_RSI_PERIOD = 14
DEFAULT_MACD_FAST = 12
DEFAULT_MACD_SLOW = 26
DEFAULT_MACD_SIGNAL = 9
DEFAULT_MA_SHORT = 20
DEFAULT_MA_LONG = 50
DEFAULT_MA_TREND = 200
DEFAULT_BB_PERIOD = 20
DEFAULT_BB_STD = 2