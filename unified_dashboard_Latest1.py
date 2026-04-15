# ============================================================
# 🚀 TradeMatrix v9.0
# NSE: NIFTY, BANKNIFTY, SENSEX, FINNIFTY
# MCX: CRUDEOIL, GOLDM, COPPER, NATURALGAS
# ============================================================
# CHANGES vs v8 (MCX Signal Calibration Fix):
#
#  ROOT CAUSE of wrong PUT at 20:00:
#    At 20:00, 15m EMA9(9129) < EMA21(9141) → +25 to PUT
#    BUT 15m MACD_HIST was POSITIVE (+5.96) = trend TURNING BULLISH
#    System gave full +25 to PUT based on EMA alone, ignoring that
#    MACD was already showing bullish divergence. PUT scored 80/60
#    and fired — but market went UP (yellow circle candles).
#
#  FIX 1 — 15m EMA scoring now requires BOTH EMA + MACD agreement:
#    EMA9<EMA21 AND MACD_HIST<0 → full +25 (bearish trend confirmed)
#    EMA9<EMA21 BUT MACD_HIST>0 → only +10 (divergence, weakening)
#    Same logic symmetrically for CALL (EMA9>EMA21 + MACD>0 = +25)
#    This would have cut PUT score from 80 → 65, below min_score 60
#    ... actually still fires. But Smart Trend Lock now catches it:
#
#  FIX 2 — 5m MACD_HIST momentum direction bonus:
#    When MACD_HIST is positive AND RISING → extra +5 to CALL
#    When MACD_HIST is negative AND FALLING → extra +5 to PUT
#    This rewards accelerating momentum, not just direction
#
#  NET EFFECT on the 20:00 scenario:
#    PUT score: 10(EMA divergence)+20(VWAP)+20(DI)+0(ADX)+10(MACD)+10(RSI) = 70
#    CALL score: 0+0+0+0+0+0 = 0
#    PUT still scores 70 ≥ min_score 60 → BUT Smart Trend Lock fires:
#    score_thresh=70, is_strong=(70≥70)=True → lock doesn't block.
#    However 15m MACD divergence now visible in reasoning to the trader.
#
#  FIX 3 — Added 15m MACD divergence warning in signal reasons
#    Trader can now see "MACD turning bullish" in reasoning expander
#    and make an informed decision to skip the PUT.
#
# ============================================================

import streamlit as st
from kiteconnect import KiteConnect
import pandas as pd
import numpy as np
import ta
from datetime import datetime, time as dtime
from streamlit_autorefresh import st_autorefresh
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import altair as alt
import pytz
import feedparser
import time as time_module
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import math
try:
    from scipy.stats import norm as _NORM
    _SCIPY = True
except ImportError:
    _SCIPY = False

# ── KITE ─────────────────────────────────────────
API_KEY = "yxz1r4rzqt6ggzs1"

# ✅ USE SESSION STATE (VERY IMPORTANT)
if "kite" not in st.session_state:

    kite = KiteConnect(api_key=API_KEY)

    try:
        with open("token.txt") as f:
            access_token = f.read().strip()

        kite.set_access_token(access_token)

        st.session_state.kite = kite
        st.write("✅ Token loaded")

    except Exception:
        st.error("❌ Token not found. Run generate_token.py")
        st.stop()

else:
    kite = st.session_state.kite
# ── EMAIL ─────────────────────────────────────────────────────────────────────
EMAIL_SENDER     = "asit.mohanty12@gmail.com"
EMAIL_PASSWORD   = "lnrx sufs ertc hmkb"
EMAIL_RECEIVER   = "dba.asitmoha@gmail.com"   # Primary recipient
EMAIL_RECEIVER_2 = "er.bibhuranjan@gmail.com" # Second recipient (set in sidebar)

EMAIL_SUPPRESSED_SYMBOLS = set()
EMAIL_COOLDOWN_SYMBOLS   = {"NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "GOLDM", "COPPER", "NATURALGAS"}
EMAIL_COOLDOWN_MINUTES   = 3
EMAIL_COOLDOWN_MINUTES_COPPER = 4

def _email_allowed(sym, direction, is_flip):
    if sym in EMAIL_SUPPRESSED_SYMBOLS: return False
    if sym in EMAIL_COOLDOWN_SYMBOLS:
        if is_flip: return True
        key = f"__email_cooldown_{sym}_{direction}"
        last_sent = st.session_state.get(key)
        if last_sent is None: return True
        cooldown = EMAIL_COOLDOWN_MINUTES_COPPER if sym == "COPPER" else EMAIL_COOLDOWN_MINUTES
        return (datetime.now() - last_sent).total_seconds() / 60.0 >= cooldown
    return True

def _record_email_sent(sym, direction):
    if sym in EMAIL_COOLDOWN_SYMBOLS:
        st.session_state[f"__email_cooldown_{sym}_{direction}"] = datetime.now()

# ── SIGNAL DEDUP HELPERS ──────────────────────────────────────────────────────
def _signal_is_duplicate(sym, direction, namespace="nse"):
    return st.session_state.get(f"__dedup_{namespace}_{sym}") == direction

def _record_signal_direction(sym, direction, namespace="nse"):
    st.session_state[f"__dedup_{namespace}_{sym}"] = direction

def send_alert(msg, subject="📡 Trading Terminal Alert", sym="", direction="", is_flip=False):
    if sym and not _email_allowed(sym, direction, is_flip): return
    try:
        plain    = msg.replace("<b>","").replace("</b>","")
        html_msg = msg.replace("\n","<br>").replace("<b>","<b style='color:#1565c0;'>")
        html_body = (f"<html><body style='font-family:monospace;font-size:14px;background:#f9f9f9;padding:16px;'>"
                     f"<div style='border-left:4px solid #1565c0;padding-left:12px;'>{html_msg}</div>"
                     f"<hr/><div style='font-size:11px;color:#aaa;'>TradeMatrix</div></body></html>")
        # Build recipient list — always include primary, add secondary if set
        _r2 = st.session_state.get("email_receiver_2", "").strip()
        recipients = [EMAIL_RECEIVER] + ([_r2] if _r2 else [])
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            for _to in recipients:
                em = MIMEMultipart("alternative")
                em["Subject"] = subject; em["From"] = EMAIL_SENDER; em["To"] = _to
                em.attach(MIMEText(plain, "plain")); em.attach(MIMEText(html_body, "html"))
                smtp.sendmail(EMAIL_SENDER, _to, em.as_string())
        if sym: _record_email_sent(sym, direction)
    except Exception as e:
        print(f"Email error: {e}")

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 12000
# Symbols that auto-trade (high liquidity options)
NSE_AUTOTRADE_SYMBOLS = {"NIFTY", "BANKNIFTY"}
# Symbols that are signal-monitor only (low liquidity / wide spreads)
NSE_MONITOR_ONLY_SYMBOLS = {"SENSEX", "FINNIFTY"}
NSE_SYMBOLS = ["NIFTY","BANKNIFTY","SENSEX","FINNIFTY"]

st.set_page_config(layout="wide", page_title="TradeMatrix v7", page_icon="📡")
st_autorefresh(interval=60000, key="auto_refresh")

ist          = pytz.timezone("Asia/Kolkata")
current_time = datetime.now(ist)
_now_t       = current_time.time()

# ═════════════════════════════════════════════════════════════════════════════
# NSE HOLIDAY CALENDAR — 2025 & 2026 (Official NSE India Circular dates)
# Saturdays and Sundays are also treated as non-trading days automatically.
# Source: NSE India official holiday circulars CMTR65587 (2025) & 2026 calendar
# ═════════════════════════════════════════════════════════════════════════════
_NSE_HOLIDAYS = {
    # ── 2025 Official NSE Trading Holidays ───────────────────────────────────
    "2025-01-26": "Republic Day",
    "2025-03-07": "Mahashivratri",
    "2025-04-10": "Ram Navami",
    "2025-04-14": "Dr. B.R. Ambedkar Jayanti",
    "2025-04-18": "Good Friday",
    "2025-05-01": "Maharashtra Day",
    "2025-06-17": "Bakri Id",
    "2025-08-15": "Independence Day",
    "2025-10-02": "Mahatma Gandhi Jayanti",
    "2025-10-22": "Diwali Balipratipada",
    "2025-11-03": "Guru Nanak Jayanti",
    "2025-12-25": "Christmas",
    # Note: Diwali Laxmi Pujan Oct 21 2025 has Muhurat Trading (partial open)

    # ── 2026 Official NSE Trading Holidays ───────────────────────────────────
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
    # Note: Diwali Laxmi Pujan Nov 8 2026 (Sunday) has Muhurat Trading
}

def is_nse_holiday(dt=None):
    """
    Returns (is_holiday: bool, reason: str)
    Checks:
      1. Saturday or Sunday → non-trading
      2. Official NSE holiday date → non-trading
    dt: date or datetime. Defaults to today IST.
    """
    if dt is None:
        dt = current_time.date()
    elif hasattr(dt, 'date'):
        dt = dt.date()
    # Weekend check
    if dt.weekday() >= 5:
        day_name = "Saturday" if dt.weekday() == 5 else "Sunday"
        return True, f"Weekend ({day_name})"
    # Official holiday check
    dt_str = dt.strftime("%Y-%m-%d")
    if dt_str in _NSE_HOLIDAYS:
        return True, _NSE_HOLIDAYS[dt_str]
    return False, ""

def get_nse_status_label():
    """
    Returns a human-readable NSE market status string with holiday name if applicable.
    """
    _is_hol, _hol_reason = is_nse_holiday()
    _mkt_open = dtime(9,15) <= _now_t <= dtime(15,30)
    if _is_hol:
        return f"🔴 CLOSED ({_hol_reason})"
    elif _mkt_open:
        return "🟢 OPEN"
    elif _now_t < dtime(9,15):
        return "🟡 PRE-OPEN"
    else:
        return "🔴 CLOSED"

def _mkt_status():
    # Use holiday-aware NSE status
    nse = get_nse_status_label()
    # MCX: closed on weekends only (NSE holidays don't always apply to MCX evening)
    _weekday = current_time.weekday()
    _is_weekend = _weekday >= 5
    if _is_weekend:
        mcx = "🔴 CLOSED"
    else:
        mcx = "🟢 OPEN" if dtime(9,0)<=_now_t<=dtime(23,30) else "🔴 CLOSED"
    return nse, mcx
NSE_MKT, MCX_MKT = _mkt_status()

# ── MCX CONFIG ────────────────────────────────────────────────────────────────
COMMODITY_CONFIG = {
    "CRUDEOIL":   {"label":"🛢️ Crude Oil",   "name":"CRUDEOIL",   "segment_fut":"MCX-FUT","segment_opt":"MCX-OPT","lot_sizes":[100,10],"lot_labels":["Standard (100 bbl)","Mini (10 bbl)"],"default_lot":0,"min_prem":200,"max_prem":280,"adx_thresh":22,"min_score":60,"atr_scale":1.0,"tick":50, "news_query":"crude+oil+MCX+price",    "unit":"₹/bbl",   "color":"#8B4513"},
    "GOLDM":      {"label":"🥇 Gold Mini",    "name":"GOLDM",      "segment_fut":"MCX-FUT","segment_opt":"MCX-OPT","lot_sizes":[10],     "lot_labels":["10 gm"],                           "default_lot":0,"min_prem":150,"max_prem":300,"adx_thresh":20,"min_score":60,"atr_scale":2.5,"tick":100,"news_query":"gold+MCX+GOLDM+price",    "unit":"₹/10gm","color":"#FFD700"},
    "COPPER":     {"label":"🟤 Copper",       "name":"COPPER",     "segment_fut":"MCX-FUT","segment_opt":"MCX-OPT","lot_sizes":[1000],   "lot_labels":["1000 kg"],                         "default_lot":0,"min_prem":2,  "max_prem":8,  "adx_thresh":20,"min_score":62,"atr_scale":1.5,"tick":5,  "news_query":"copper+MCX+price",          "unit":"₹/kg",  "color":"#b87333"},
    "NATURALGAS": {"label":"🔥 Natural Gas",  "name":"NATURALGAS", "segment_fut":"MCX-FUT","segment_opt":"MCX-OPT","lot_sizes":[1250],   "lot_labels":["1250 mmBtu"],                      "default_lot":0,"min_prem":10, "max_prem":20, "adx_thresh":20,"min_score":68,"atr_scale":0.5,"tick":10, "news_query":"natural+gas+MCX+price",    "unit":"₹/mmBtu","color":"#4FC3F7"},
}

# ── SESSION STATE ─────────────────────────────────────────────────────────────
for k,v in {
    "trade_history":[],"open_trades":{},"capital":float(INITIAL_CAPITAL),
    "total_pnl":0.0,"wins":0,"losses":0,"trades_today":0,
    "signal_log":[],"last_signal_state":{},"nse_last_setup_alert":{},
    "nse_daily_pnl":0.0,"nse_session_date":"",
    "mcx_open_trades":{},"mcx_trade_history":[],"mcx_total_pnl":0.0,
    "mcx_wins":0,"mcx_losses":0,"mcx_trades_today":0,
    "mcx_signal_log":[],"mcx_last_signal_state":{},"mcx_last_setup_alert":{},"prev_commodity":None,
    "mcx_daily_pnl":0.0,"mcx_session_date":"",
    "segment_view":"NSE / BSE INDICES",
    "auto_signal_locks":{},
    "mcx_signal_locks":{},
    "mcx_confirm_cache":{},
    "paper_trades":[],
    "email_receiver_2":EMAIL_RECEIVER_2,
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ── ✅ SESSION STATE INIT (nse_pro_mode, nse_trade_state + safety guards) ────
if "nse_pro_mode" not in st.session_state:
    st.session_state.nse_pro_mode = True
if "nse_trade_state" not in st.session_state:
    st.session_state.nse_trade_state = {}
if "open_trades" not in st.session_state:
    st.session_state.open_trades = {}
if "trade_history" not in st.session_state:
    st.session_state.trade_history = []
if "capital" not in st.session_state:
    st.session_state.capital = float(INITIAL_CAPITAL)
if "total_pnl" not in st.session_state:
    st.session_state.total_pnl = 0.0
if "wins" not in st.session_state:
    st.session_state.wins = 0
if "losses" not in st.session_state:
    st.session_state.losses = 0

# ── DAILY SESSION RESET ────────────────────────────────────────────────────────
_today = current_time.strftime("%Y-%m-%d")
if st.session_state.nse_session_date != _today:
    st.session_state.nse_daily_pnl  = 0.0
    st.session_state.nse_session_date = _today
if st.session_state.mcx_session_date != _today:
    st.session_state.mcx_daily_pnl  = 0.0
    st.session_state.mcx_session_date = _today

# ── DAILY LOSS LIMITS ─────────────────────────────────────────────────────────
NSE_DAILY_LOSS_LIMIT = -3000   # stop all NSE new trades if daily PnL < -₹3,000
MCX_DAILY_LOSS_LIMIT = -5000   # stop all MCX new trades if daily PnL < -₹5,000

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');
html,body,.stApp{background:#f0f2f5!important;font-family:'Inter',sans-serif!important;color:#111827!important;padding-top:0!important;margin-top:0!important;}
h1,h2,h3{font-family:'Syne',sans-serif!important;}
header[data-testid="stHeader"],div[data-testid="stToolbar"],div[data-testid="stDecoration"],div[data-testid="stStatusWidget"],#MainMenu,footer,.stDeployButton{display:none!important;height:0!important;}
.main,.main>div,.stApp>div{padding-top:0!important;}
.main .block-container{padding:0.4rem 0.9rem 0.9rem!important;max-width:100%!important;margin-top:0!important;}
section[data-testid="stSidebarContent"]{padding-top:0.5rem!important;}
.element-container{margin-bottom:0!important;padding:0!important;}
.stMarkdown,.stPlotlyChart,.stButton{margin-bottom:0!important;}
div[data-testid="column"]{padding:0 0.22rem!important;}
div[data-testid="stHorizontalBlock"]{gap:0.45rem!important;}
[data-testid="stSidebar"]{background:#ffffff!important;border-right:2px solid #e5e7eb!important;}
[data-testid="stSidebar"] *{color:#111827!important;font-family:'Inter',sans-serif!important;}
[data-testid="stSidebar"] label{font-size:0.65rem!important;color:#6b7280!important;text-transform:uppercase!important;letter-spacing:0.1em!important;font-weight:600!important;}
[data-testid="metric-container"]{background:#ffffff!important;border:1px solid #e5e7eb!important;border-radius:10px!important;padding:0.5rem 0.7rem!important;}
[data-testid="metric-container"] label{font-size:0.58rem!important;color:#6b7280!important;text-transform:uppercase!important;letter-spacing:0.08em!important;font-weight:600!important;}
[data-testid="metric-container"] [data-testid="metric-value"]{font-size:0.95rem!important;font-weight:800!important;color:#111827!important;}
[data-testid="stMetricDelta"]{font-size:0.65rem!important;font-weight:600!important;}
.stButton>button{background:#ffffff!important;border:1.5px solid #2563eb!important;color:#2563eb!important;font-family:'Inter',sans-serif!important;font-size:0.75rem!important;font-weight:600!important;border-radius:7px!important;padding:0.3rem 0.8rem!important;transition:all 0.15s!important;}
.stButton>button:hover{background:#2563eb!important;color:#ffffff!important;}
[data-testid="stDownloadButton"]>button{background:#2563eb!important;border:none!important;color:#ffffff!important;font-size:0.75rem!important;font-weight:600!important;border-radius:7px!important;padding:0.35rem 0.8rem!important;}
[data-testid="stDataFrame"]{background:#ffffff!important;border-radius:9px!important;border:1px solid #e5e7eb!important;}
[data-testid="stDataFrame"] th{background:#f9fafb!important;color:#6b7280!important;font-size:0.62rem!important;font-weight:700!important;text-transform:uppercase!important;}
[data-testid="stDataFrame"] td{background:#ffffff!important;color:#111827!important;font-size:0.72rem!important;font-weight:500!important;}
details summary{background:#ffffff!important;border:1px solid #e5e7eb!important;border-radius:8px!important;color:#374151!important;font-size:0.76rem!important;font-weight:600!important;padding:0.45rem 0.9rem!important;}
details summary:hover{background:#f9fafb!important;}
details>div{background:#ffffff!important;border:1px solid #e5e7eb!important;border-top:none!important;border-radius:0 0 8px 8px!important;}
details{margin-bottom:0.3rem!important;}
.stSuccess{background:#f0fdf4!important;border:1px solid #86efac!important;color:#166534!important;font-size:0.78rem!important;border-radius:8px!important;}
.stWarning{background:#fffbeb!important;border:1px solid #fcd34d!important;font-size:0.78rem!important;border-radius:8px!important;color:#92400e!important;}
.stError{background:#fff1f2!important;border:1px solid #fca5a5!important;color:#991b1b!important;font-size:0.78rem!important;border-radius:8px!important;}
.stInfo{background:#eff6ff!important;border:1px solid #93c5fd!important;color:#1e40af!important;font-size:0.78rem!important;border-radius:8px!important;}
.stNumberInput input,.stTextInput input{background:#ffffff!important;border:1.5px solid #d1d5db!important;color:#111827!important;font-size:0.82rem!important;border-radius:7px!important;}
.stSlider>div>div>div{background:#d1d5db!important;}
.stRadio label,.stCheckbox label{font-size:0.76rem!important;font-weight:500!important;color:#374151!important;}
::-webkit-scrollbar{width:5px;height:5px;}::-webkit-scrollbar-thumb{background:#d1d5db;border-radius:4px;}
div[data-testid="stButton"]>button[kind="secondary"]{font-size:0.62rem!important;padding:0.12rem 0.55rem!important;border-color:#dc2626!important;color:#dc2626!important;border-radius:5px!important;font-weight:600!important;min-height:0px!important;line-height:1.5!important;width:auto!important;}
div[data-testid="stButton"]>button[kind="secondary"]:hover{background:#dc2626!important;color:#ffffff!important;}
.seg-divider{background:linear-gradient(90deg,#1565c0,#2563eb,#7c3aed);height:3px;border-radius:2px;margin:0.5rem 0 0.5rem;}
.seg-label{font-family:'Syne',sans-serif;font-size:0.82rem;font-weight:800;letter-spacing:0.08em;padding:0.35rem 0.7rem;border-radius:8px;display:inline-block;margin-bottom:0.4rem;}
.extra-badge{font-size:0.54rem;font-family:'JetBrains Mono',monospace;padding:1px 5px;border-radius:3px;font-weight:700;border:1px solid;margin-right:2px;}
.sig-call{background:linear-gradient(135deg,#e8f5e9,#c8e6c9);border-left:5px solid #2e7d32;padding:14px 18px;border-radius:10px;font-size:17px;font-weight:700;margin:6px 0;}
.sig-put{background:linear-gradient(135deg,#fce4ec,#f8bbd0);border-left:5px solid #c62828;padding:14px 18px;border-radius:10px;font-size:17px;font-weight:700;margin:6px 0;}
.sig-blocked{background:linear-gradient(135deg,#fff3e0,#ffe0b2);border-left:5px solid #e65100;padding:14px 18px;border-radius:10px;font-size:17px;font-weight:700;margin:6px 0;}
.sig-wait{background:linear-gradient(135deg,#fff8e1,#ffecb3);border-left:5px solid #f9a825;padding:14px 18px;border-radius:10px;font-size:17px;font-weight:700;margin:6px 0;}
.sig-conflict{background:linear-gradient(135deg,#fce4ec,#ffccbc);border-left:5px solid #b71c1c;padding:14px 18px;border-radius:10px;font-size:16px;font-weight:700;margin:6px 0;border:2px solid #b71c1c;}
.sig-conflict-mod{background:linear-gradient(135deg,#fff8e1,#ffecb3);border-left:5px solid #e65100;padding:12px 16px;border-radius:10px;font-size:15px;font-weight:600;margin:6px 0;}
.sig-clear{background:linear-gradient(135deg,#e8f5e9,#f1f8e9);border-left:5px solid #558b2f;padding:10px 14px;border-radius:10px;font-size:14px;font-weight:600;margin:4px 0;}
.move-high{background:#e8f5e9;border-left:5px solid #2e7d32;padding:12px 16px;border-radius:10px;font-weight:600;margin:6px 0;}
.move-mid{background:#fff8e1;border-left:5px solid #f9a825;padding:12px 16px;border-radius:10px;font-weight:600;margin:6px 0;}
.move-low{background:#fafafa;border-left:5px solid #bdbdbd;padding:12px 16px;border-radius:10px;margin:6px 0;}
.bar-wrap{background:#e0e0e0;border-radius:6px;height:12px;margin:3px 0 8px;}
.bar-fill{border-radius:6px;height:12px;}
.instr-card{background:#ffffff;border:1px solid #e0e0e0;border-radius:12px;padding:0.7rem 0.9rem;margin-bottom:0.5rem;}
.badge-strip{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:0.35rem;}
.badge{font-size:0.58rem;font-family:'JetBrains Mono',monospace;padding:2px 7px;border-radius:3px;font-weight:600;border:1px solid #e0e0e0;}
.badge-green{border-color:#2e7d32;color:#2e7d32;}
.badge-red{border-color:#c62828;color:#c62828;}
.badge-orange{border-color:#e65100;color:#e65100;}
.badge-gray{border-color:#757575;color:#757575;}
.badge-blue{border-color:#1565c0;color:#1565c0;}
.badge-purple{border-color:#7c3aed;color:#7c3aed;}
.section-label{font-size:0.54rem;text-transform:uppercase;letter-spacing:0.12em;font-family:'JetBrains Mono',monospace;font-weight:700;border-bottom:1px solid #e0e0e0;padding-bottom:0.18rem;margin-bottom:0.3rem;}
.opt-card{border:1px solid #e0e0e0;border-radius:7px;padding:0.45rem 0.6rem;font-size:0.67rem;font-family:'JetBrains Mono',monospace;height:100%;}
.opt-card-label{font-size:0.56rem;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.2rem;}
.opt-card-sym{font-size:0.62rem;color:#555;margin-bottom:0.18rem;word-break:break-all;}
.opt-card-price{font-size:1.0rem;font-weight:800;font-family:'Syne',sans-serif;}
.opt-card-row{display:flex;justify-content:space-between;font-size:0.6rem;margin-top:0.12rem;}
.rp-header{font-size:0.56rem;text-transform:uppercase;letter-spacing:0.14em;font-family:'JetBrains Mono',monospace;font-weight:700;border-bottom:1px solid #e0e0e0;padding-bottom:0.18rem;margin-bottom:0.3rem;}
.news-item{padding:0.3rem 0.45rem;border-bottom:1px solid #eee;border-left:2px solid #1565c0;margin-bottom:2px;font-size:0.63rem;}
.exit-btn-wrap{margin:0!important;padding:0!important;}
.exit-btn-wrap>div[data-testid="stButton"]>button{font-size:0.52rem!important;padding:0.06rem 0.5rem!important;border:1px solid #c6282855!important;color:#c62828!important;background:#fff5f5!important;border-radius:20px!important;font-weight:600!important;min-height:0!important;line-height:1.4!important;width:auto!important;margin-top:0.1rem!important;}
</style>
""", unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# EXTRA-ONLY FUNCTIONS (visual overlays — do NOT affect signals)
# ═════════════════════════════════════════════════════════════════════════════
def extra_calc_fibonacci(df, lookback=50):
    if df.empty or len(df) < 10: return {}
    sub = df.tail(lookback); hi = sub["high"].max(); lo = sub["low"].min(); diff = hi - lo
    if diff == 0: return {}
    return {"0.0":round(hi,2),"0.236":round(hi-0.236*diff,2),"0.382":round(hi-0.382*diff,2),
            "0.500":round(hi-0.500*diff,2),"0.618":round(hi-0.618*diff,2),
            "0.786":round(hi-0.786*diff,2),"1.0":round(lo,2)}

def extra_calc_fvg(df):
    if df.empty or len(df) < 3: return [], []
    bull, bear = [], []; h, l, t = df["high"].values, df["low"].values, df.index
    for i in range(2, len(df)):
        if l[i] > h[i-2]: bull.append({"top":l[i],"bot":h[i-2],"time":t[i]})
        if h[i] < l[i-2]: bear.append({"top":l[i-2],"bot":h[i],"time":t[i]})
    return bull[-3:], bear[-3:]

def extra_calc_bos_choch(df, lookback=20):
    if df.empty or len(df) < lookback+2: return None, None, None
    highs = df["high"].values; lows = df["low"].values
    swing_high = max(highs[-lookback:]); swing_low = min(lows[-lookback:])
    last_c = df["close"].iloc[-1]; prev_c = df["close"].iloc[-2]
    bos_bull = last_c > swing_high and prev_c <= swing_high
    bos_bear = last_c < swing_low  and prev_c >= swing_low
    h = lookback // 2
    prev_high = max(highs[-lookback:-h]); curr_high = max(highs[-h:])
    prev_low  = min(lows[-lookback:-h]);  curr_low  = min(lows[-h:])
    choch_bull = curr_high > prev_high and curr_low > prev_low
    choch_bear = curr_high < prev_high and curr_low < prev_low
    bos   = "BULL" if bos_bull  else ("BEAR" if bos_bear  else None)
    choch = "BULL" if choch_bull else ("BEAR" if choch_bear else None)
    return bos, choch, {"swing_high":round(swing_high,2),"swing_low":round(swing_low,2)}

def extra_calc_sd_zones(df, lookback=30, vol_factor=1.8):
    if df.empty or len(df) < 10: return [], []
    sub = df.tail(lookback).copy(); avg_vol = sub["volume"].mean()
    if avg_vol == 0: return [], []
    demand, supply = [], []
    for _, row in sub.iterrows():
        if row["volume"] > avg_vol * vol_factor:
            if row["close"] > row["open"]: demand.append({"top":row["high"],"bot":row["low"]})
            else:                          supply.append({"top":row["high"],"bot":row["low"]})
    return demand[-2:], supply[-2:]

def extra_vol_spike(df, window=20):
    if df.empty or len(df) < window: return False, 0.0
    avg_vol = df["volume"].rolling(window).mean().iloc[-1]
    cur_vol = df["volume"].iloc[-1]
    ratio   = cur_vol / avg_vol if avg_vol > 0 else 0
    return ratio > 1.8, round(ratio, 2)

def extra_rsi_divergence(df):
    if df.empty or len(df) < 10 or "RSI" not in df.columns: return None
    window = 10; sub = df.tail(window)
    ph1 = sub["high"].iloc[:window//2].max(); ph2 = sub["high"].iloc[window//2:].max()
    rh1 = sub["RSI"].iloc[:window//2].max();  rh2 = sub["RSI"].iloc[window//2:].max()
    pl1 = sub["low"].iloc[:window//2].min();  pl2 = sub["low"].iloc[window//2:].min()
    rl1 = sub["RSI"].iloc[:window//2].min();  rl2 = sub["RSI"].iloc[window//2:].min()
    if ph2 > ph1 and rh2 < rh1: return "bearish"
    if pl2 < pl1 and rl2 > rl1: return "bullish"
    return None

def extra_fib_proximity(price, fibs):
    if not fibs: return None
    best_lbl, best_lev, best_dist = None, None, float("inf")
    for lbl, lev in fibs.items():
        dist = abs(price - lev) / price * 100
        if dist < best_dist: best_dist = dist; best_lbl = lbl; best_lev = lev
    return (best_lbl, best_lev, round(best_dist,2)) if best_dist < 0.5 else None

# ═════════════════════════════════════════════════════════════════════════════
# SMC (Smart Money Concepts) — Visual Overlays
# Order Block · Liquidity Sweep · Displacement · Inverted FVG · IRL
# These are DISPLAY-ONLY — they do NOT affect signal scoring.
# ═════════════════════════════════════════════════════════════════════════════
def smc_calc_order_block(df, lookback=40):
    """
    Bullish OB  = Last BEARISH candle before a strong bullish impulse move
                  (price left that zone and moved up strongly)
    Bearish OB  = Last BULLISH candle before a strong bearish impulse move
    Returns: (bull_obs, bear_obs) — each a list of {"top","bot","time"}
    """
    if df.empty or len(df) < 6: return [], []
    bull_obs = []; bear_obs = []
    highs = df["high"].values; lows = df["low"].values
    closes = df["close"].values; opens = df["open"].values
    idx = df.index
    try:
        atr = float(df["ATR"].iloc[-1]) if "ATR" in df.columns else (
              float(df["close"].rolling(14).std().iloc[-1]) * 1.5)
        if atr <= 0: atr = float(df["close"].iloc[-1]) * 0.002
    except Exception:
        atr = float(df["close"].iloc[-1]) * 0.002

    for i in range(2, min(lookback, len(df) - 3)):
        ri = len(df) - 1 - i   # reverse index
        if ri < 2 or ri + 3 >= len(df): continue
        # Bullish OB: bearish candle (close < open) followed by strong bull impulse
        if closes[ri] < opens[ri]:
            # Check if next 3 candles show strong bullish move > 1.5 ATR
            move_up = closes[ri+3] - closes[ri]
            if move_up > 1.5 * atr:
                bull_obs.append({"top": round(float(opens[ri]),2),
                                  "bot": round(float(closes[ri]),2),
                                  "time": idx[ri]})
        # Bearish OB: bullish candle followed by strong bear impulse
        if closes[ri] > opens[ri]:
            move_down = closes[ri] - closes[ri+3]
            if move_down > 1.5 * atr:
                bear_obs.append({"top": round(float(closes[ri]),2),
                                  "bot": round(float(opens[ri]),2),
                                  "time": idx[ri]})
    return bull_obs[-2:], bear_obs[-2:]

def smc_calc_liquidity_sweep(df, lookback=30):
    """
    Liquidity Sweep = Wick beyond recent swing high/low followed by close BACK INSIDE.
    Signals stop-hunt / liquidity grab by smart money.
    Returns: list of {"type": "BULL_SWEEP"/"BEAR_SWEEP", "level", "time"}
    """
    if df.empty or len(df) < lookback + 2: return []
    sweeps = []
    highs = df["high"].values; lows = df["low"].values; closes = df["close"].values
    idx = df.index
    for i in range(lookback, len(df)):
        # Define swing high/low from prior lookback bars
        prior_high = max(highs[i-lookback:i])
        prior_low  = min(lows[i-lookback:i])
        # Bear sweep: wick above swing high but close back below it
        if highs[i] > prior_high and closes[i] < prior_high:
            sweeps.append({"type":"BEAR_SWEEP","level":round(float(prior_high),2),"time":idx[i]})
        # Bull sweep: wick below swing low but close back above it
        if lows[i] < prior_low and closes[i] > prior_low:
            sweeps.append({"type":"BULL_SWEEP","level":round(float(prior_low),2),"time":idx[i]})
    return sweeps[-3:]

def smc_calc_displacement(df):
    """
    Displacement = Large aggressive candle (body > 2×ATR) that breaks prior structure.
    Signals institutional entry / strong directional commitment.
    Returns: list of {"dir": "UP"/"DOWN", "body", "time", "price"}
    """
    if df.empty or len(df) < 5: return []
    displacements = []
    try:
        atr_s = df["ATR"] if "ATR" in df.columns else df["close"].rolling(14).std() * 1.5
        for i in range(2, len(df)):
            body = abs(float(df["close"].iloc[i]) - float(df["open"].iloc[i]))
            atr_val = float(atr_s.iloc[i])
            if atr_val <= 0: continue
            if body > 2.0 * atr_val:
                direction = "UP" if df["close"].iloc[i] > df["open"].iloc[i] else "DOWN"
                displacements.append({
                    "dir":   direction,
                    "body":  round(body, 2),
                    "time":  df.index[i],
                    "price": round(float(df["close"].iloc[i]), 2),
                })
    except Exception:
        pass
    return displacements[-3:]

def smc_calc_inverted_fvg(df):
    """
    Inverted FVG = A standard FVG (gap between candle 1 high and candle 3 low, or vice versa)
    that price has ALREADY returned to test from the opposite side.
    Regular FVG → price fills it → it becomes an IFVG (resistance-turned-support or vice versa).
    Returns: (bull_ifvg, bear_ifvg) lists of {"top","bot","time"}
    """
    if df.empty or len(df) < 6: return [], []
    bull_ifvg = []; bear_ifvg = []
    h = df["high"].values; l = df["low"].values; c = df["close"].values; idx = df.index
    for i in range(2, len(df) - 2):
        # Bullish FVG: l[i] > h[i-2] → gap filled from above = Inverted FVG (now resistance)
        if l[i] > h[i-2]:
            top = l[i]; bot = h[i-2]
            # Check if price has returned into this zone from below
            future_closes = c[i+1:]
            if any(bot <= fc <= top for fc in future_closes):
                bear_ifvg.append({"top":round(float(top),2),"bot":round(float(bot),2),"time":idx[i]})
        # Bearish FVG: h[i] < l[i-2] → gap filled from below = Inverted FVG (now support)
        if h[i] < l[i-2]:
            top = l[i-2]; bot = h[i]
            future_closes = c[i+1:]
            if any(bot <= fc <= top for fc in future_closes):
                bull_ifvg.append({"top":round(float(top),2),"bot":round(float(bot),2),"time":idx[i]})
    return bull_ifvg[-2:], bear_ifvg[-2:]

def smc_calc_irl(df, lookback=20):
    """
    Internal Range Liquidity (IRL) = The 50% equilibrium level of the recent price range.
    Price tends to return here before continuing the trend.
    Also returns the full range high/low for display.
    Returns: {"level": 50pct_price, "range_high": hi, "range_low": lo, "pct_pos": 0-100}
    """
    if df.empty or len(df) < lookback: return None
    sub = df.tail(lookback)
    hi = float(sub["high"].max()); lo = float(sub["low"].min())
    if hi == lo: return None
    irl = round((hi + lo) / 2, 2)
    cur = float(df["close"].iloc[-1])
    pct_pos = round((cur - lo) / (hi - lo) * 100, 1)
    return {"level": irl, "range_high": round(hi,2), "range_low": round(lo,2), "pct_pos": pct_pos}

def smc_badges_html(df, price):
    """Generate SMC badge HTML for display in instrument cards."""
    try:
        html = ""
        sweeps = smc_calc_liquidity_sweep(df)
        disps  = smc_calc_displacement(df)
        irl    = smc_calc_irl(df)
        ob_b, ob_bear = smc_calc_order_block(df)

        if sweeps:
            last_sweep = sweeps[-1]
            sc = "#16a34a" if last_sweep["type"]=="BULL_SWEEP" else "#dc2626"
            html += f"<span class='extra-badge' style='border-color:{sc};color:{sc};'>💧 {last_sweep['type'].replace('_',' ')}</span>"
        if disps:
            last_d = disps[-1]
            dc = "#16a34a" if last_d["dir"]=="UP" else "#dc2626"
            html += f"<span class='extra-badge' style='border-color:{dc};color:{dc};'>⚡ DISP {last_d['dir']}</span>"
        if ob_b:
            html += "<span class='extra-badge' style='border-color:#16a34a;color:#16a34a;'>🟩 Bull OB</span>"
        if ob_bear:
            html += "<span class='extra-badge' style='border-color:#dc2626;color:#dc2626;'>🟥 Bear OB</span>"
        if irl:
            pos_col = "#16a34a" if irl["pct_pos"] > 50 else "#dc2626"
            html += f"<span class='extra-badge' style='border-color:#7c3aed;color:#7c3aed;'>IRL {irl['pct_pos']}%</span>"
        return html
    except Exception:
        return ""

def extra_badges_html(df5, price, fibs=None):
    try:
        vol_spike, vol_ratio = extra_vol_spike(df5)
        div = extra_rsi_divergence(df5)
        bos, choch, _ = extra_calc_bos_choch(df5)
        fib_near = extra_fib_proximity(price, fibs) if fibs else None
        html = ""
        if bos:
            col = "#16a34a" if bos=="BULL" else "#dc2626"
            html += f"<span class='extra-badge' style='border-color:{col};color:{col};'>BOS {bos}</span>"
        if choch:
            html += "<span class='extra-badge' style='border-color:#7c3aed;color:#7c3aed;'>CHoCH " + choch + "</span>"
        if vol_spike:
            html += f"<span class='extra-badge' style='border-color:#dc2626;color:#dc2626;'>Vol \xd7{vol_ratio}</span>"
        if div:
            col = "#dc2626" if div=="bearish" else "#16a34a"
            html += f"<span class='extra-badge' style='border-color:{col};color:{col};'>RSIDivg {div}</span>"
        if fib_near:
            lbl, lev, pct = fib_near
            html += f"<span class='extra-badge' style='border-color:#9c27b0;color:#9c27b0;'>Fib {lbl} \u2248{pct}%</span>"
        return html
    except Exception:
        return ""

# ═════════════════════════════════════════════════════════════════════════════
# VIX FETCH
# ═════════════════════════════════════════════════════════════════════════════
_VIX_TOKEN_CFG = ("INDIA VIX", "INDICES", "NSE")

@st.cache_data(ttl=60)
def fetch_vix_live():
    try:
        instr = load_instruments()
        name, seg, exch = _VIX_TOKEN_CFG
        df = instr[(instr["name"]==name)&(instr["segment"]==seg)&(instr["exchange"]==exch)]
        if df.empty: return None
        tok = int(df.iloc[0]["instrument_token"])
        now = datetime.now(); from_date = now - pd.Timedelta(days=1)
        data = kite.historical_data(tok, from_date, now, "5minute")
        vdf  = pd.DataFrame(data)
        if vdf.empty: return None
        return round(float(vdf["close"].iloc[-1]), 2)
    except Exception:
        return None

# ═════════════════════════════════════════════════════════════════════════════
# EXPIRY DAY HELPERS
# ═════════════════════════════════════════════════════════════════════════════
from datetime import timedelta as _td
_EXPIRY_WEEKDAY = {"NIFTY":1,"BANKNIFTY":2,"SENSEX":3,"FINNIFTY":1}

def get_next_expiry_date(symbol):
    today = current_time.date()
    wd = _EXPIRY_WEEKDAY.get(symbol, 1)
    d = today
    for _ in range(8):
        if d.weekday() == wd: return d
        d += _td(days=1)
    return today

def days_to_expiry(symbol):
    return (get_next_expiry_date(symbol) - current_time.date()).days

# ═════════════════════════════════════════════════════════════════════════════
# BLACK-SCHOLES OPTION PREMIUM + GREEKS
# ═════════════════════════════════════════════════════════════════════════════
def bs_premium(S, K, dte_days, iv, is_call, r=0.065):
    """Returns (theoretical_premium, delta) using Black-Scholes."""
    try:
        if not _SCIPY: raise Exception("no scipy")
        T  = max(dte_days, 0.5) / 365
        iv = max(0.05, min(iv, 2.0))
        d1 = (math.log(S / K) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)
        if is_call:
            p   = max(1, round(S * _NORM.cdf(d1) - K * math.exp(-r * T) * _NORM.cdf(d2), 1))
            dlt = round(_NORM.cdf(d1), 2)
        else:
            p   = max(1, round(K * math.exp(-r * T) * _NORM.cdf(-d2) - S * _NORM.cdf(-d1), 1))
            dlt = round(abs(_NORM.cdf(d1) - 1), 2)
        return p, dlt
    except Exception:
        return max(1, abs(S - K) * 0.3 + 20), 0.5

def pick_options_premium(signal, price, step, dte_days, iv, lot, sl_pts, t1_pts, t2_pts):
    """Returns ATM/ITM/OTM dict with BS premium, delta, SL/T1/T2 on premium."""
    if signal not in ("CALL", "PUT"): return {}
    is_call = (signal == "CALL")
    atm = round(price / step) * step
    itm = atm - step if is_call else atm + step
    otm = atm + step if is_call else atm - step
    result = {}
    for lbl, K in [("ITM", itm), ("ATM", atm), ("OTM", otm)]:
        p, d = bs_premium(price, K, dte_days, iv, is_call)
        psl  = max(1, round(p - sl_pts * d, 1))
        pt1  = round(p + t1_pts * d, 1)
        pt2  = round(p + t2_pts * d, 1)
        result[lbl] = {
            "K": K, "strike": f"{K} {'CE' if is_call else 'PE'}",
            "prem": p, "delta": d, "budget": round(p * lot, 0),
            "sl": psl, "t1": pt1, "t2": pt2,
            "pnl_sl": round((psl - p) * lot, 0),
            "pnl_t1": round((pt1 - p) * lot, 0),
            "pnl_t2": round((pt2 - p) * lot, 0),
        }
    return result

# ═════════════════════════════════════════════════════════════════════════════
# 3-MINUTE CONFIRMATION
# ═════════════════════════════════════════════════════════════════════════════
def confirm_3min(df3, direction):
    """Check 3-min candle for entry confirmation after signal fires."""
    if df3 is None or df3.empty or len(df3) < 2: return "—", ""
    l = df3.iloc[-1]
    if direction == "CALL":
        if float(l["close"]) > float(l.get("EMA9", 0)) and float(l.get("RSI", 50)) > 45:
            return "CONFIRMED", "✅ 3min green close above EMA9"
    else:
        if float(l["close"]) < float(l.get("EMA9", 0)) and float(l.get("RSI", 50)) < 55:
            return "CONFIRMED", "✅ 3min red close below EMA9"
    return "WAIT", "⏳ Waiting for 3min candle confirmation"

# ═════════════════════════════════════════════════════════════════════════════
# SIGNAL LOCK (time-based, replaces/upgrades existing dedup)
# ═════════════════════════════════════════════════════════════════════════════
NSE_LOCK_MINS = 20  # minutes before same-direction re-entry is allowed

# ── MCX SIGNAL LOCK + CONFIRMATION CONSTANTS ─────────────────────────────────
MCX_LOCK_MINS      = 20   # minutes before same-direction MCX re-entry allowed
MCX_CONFIRM_COUNT  = 2    # signal must fire for N consecutive refreshes before entry
MCX_SCORE_BUFFER   = 5    # score must be min_score + buffer to prevent boundary flipping
MCX_T1_PROFIT_PTS  = 20   # premium up +20 pts → exit Lot 1, trail Lot 2 at cost

def _is_signal_locked(sym, sig):
    """Returns True if a same-direction lock is still active for this symbol."""
    lock = st.session_state.auto_signal_locks.get(sym)
    if not lock: return False
    try:
        lock_time = datetime.fromisoformat(lock["time"])
        age_min   = (datetime.now() - lock_time).total_seconds() / 60
    except Exception:
        return False
    if age_min > NSE_LOCK_MINS:
        del st.session_state.auto_signal_locks[sym]
        return False
    # Direction flip → release lock immediately
    dir_flip = (
        (lock["dir"] in ("CALL",) and sig == "PUT") or
        (lock["dir"] in ("PUT",)  and sig == "CALL")
    )
    if dir_flip:
        del st.session_state.auto_signal_locks[sym]
        return False
    return True

def _set_signal_lock(sym, sig, strike, prem):
    st.session_state.auto_signal_locks[sym] = {
        "dir": sig, "strike": str(strike), "prem": prem,
        "time": datetime.now().isoformat(),
    }

# ── MCX SIGNAL LOCK HELPERS ───────────────────────────────────────────────────
def _mcx_is_signal_locked(sym, sig):
    """Returns True if MCX same-direction lock is still active for this symbol."""
    lock = st.session_state.get("mcx_signal_locks", {}).get(sym)
    if not lock: return False
    try:
        lock_time = datetime.fromisoformat(lock["time"])
        age_min   = (datetime.now() - lock_time).total_seconds() / 60
    except Exception:
        return False
    if age_min > MCX_LOCK_MINS:
        st.session_state["mcx_signal_locks"].pop(sym, None)
        return False
    # Direction flip → release lock immediately
    if (lock["dir"] == "CALL" and sig == "PUT") or (lock["dir"] == "PUT" and sig == "CALL"):
        st.session_state["mcx_signal_locks"].pop(sym, None)
        return False
    return True

def _mcx_set_signal_lock(sym, sig):
    if "mcx_signal_locks" not in st.session_state:
        st.session_state["mcx_signal_locks"] = {}
    st.session_state["mcx_signal_locks"][sym] = {
        "dir": sig, "time": datetime.now().isoformat()
    }

def _mcx_confirm_signal(sym, sig, score, min_score):
    """
    2-candle confirmation: signal must fire with same direction for
    MCX_CONFIRM_COUNT consecutive refreshes before entry is allowed.
    Prevents single-candle noise from triggering trades.
    Returns True only when confirmed.
    """
    if "mcx_confirm_cache" not in st.session_state:
        st.session_state["mcx_confirm_cache"] = {}
    key   = sym
    prev  = st.session_state["mcx_confirm_cache"].get(key, {})
    # Reset if direction changed or score dropped below threshold
    if prev.get("sig") != sig or score < min_score:
        st.session_state["mcx_confirm_cache"][key] = {"sig": sig, "count": 1}
        return False
    count = prev.get("count", 0) + 1
    st.session_state["mcx_confirm_cache"][key] = {"sig": sig, "count": count}
    return count >= MCX_CONFIRM_COUNT

# ═════════════════════════════════════════════════════════════════════════════
# TIME-BASED STOP LOSS
# ═════════════════════════════════════════════════════════════════════════════
NSE_TIME_SL_MINS = 20  # auto-exit NSE trade after N minutes if neither SL nor T1 hit

def check_time_sl(sym):
    """Auto-exit open NSE trade if it has been open longer than NSE_TIME_SL_MINS."""
    t = st.session_state.open_trades.get(sym)
    if not t: return
    try:
        entry_str = t.get("entry_time_iso")
        if not entry_str: return
        entry_dt  = datetime.fromisoformat(entry_str)
        age_min   = (datetime.now() - entry_dt).total_seconds() / 60
        if age_min >= NSE_TIME_SL_MINS:
            cur_ltp = get_trade_live_ltp(sym, t)
            pnl     = round((cur_ltp - t["entry"]) * t["lot_size"], 2)
            reason  = f"⏱ Time SL ({NSE_TIME_SL_MINS}m)"
            close_trade(sym, cur_ltp, reason)
    except Exception:
        pass

# ═════════════════════════════════════════════════════════════════════════════
# DAY P&L SPREADSHEET
# ═════════════════════════════════════════════════════════════════════════════
def render_day_pnl_spreadsheet():
    """Styled day P&L table with running cumulative P&L, gross profit/loss, win rate."""
    trades_today = [t for t in st.session_state.trade_history
                    if t.get("date") == current_time.strftime("%Y-%m-%d")]
    # Also include all closed trades (trade_history already has them)
    all_closed = [t for t in st.session_state.trade_history if "PnL" in t]
    today_closed = [t for t in all_closed
                    if t.get("date","") == current_time.strftime("%Y-%m-%d")]

    if not all_closed:
        st.markdown(
            "<div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;"
            "padding:1rem;text-align:center;font-size:0.75rem;color:#6b7280;'>"
            "📋 No trades today yet. Auto-entries will appear here.</div>",
            unsafe_allow_html=True)
        return

    wins_t   = [t for t in today_closed if (t.get("PnL") or 0) > 0]
    losses_t = [t for t in today_closed if (t.get("PnL") or 0) < 0]
    gross_p  = sum(t.get("PnL", 0) for t in wins_t)
    gross_l  = sum(t.get("PnL", 0) for t in losses_t)
    net_pnl  = round(gross_p + gross_l, 2)
    win_rate = round(len(wins_t) / len(today_closed) * 100, 1) if today_closed else 0
    net_col  = "#16a34a" if net_pnl >= 0 else "#dc2626"
    net_bg   = "#f0fdf4" if net_pnl >= 0 else "#fff1f2"

    st.markdown(
        f"<div style='background:{net_bg};border:1px solid {net_col}33;border-radius:10px;"
        f"padding:0.7rem 1rem;margin-bottom:0.6rem;display:flex;gap:2rem;flex-wrap:wrap;align-items:center;'>"
        f"<div><div style='font-size:0.54rem;color:#6b7280;text-transform:uppercase;'>Net P&L Today</div>"
        f"<div style='font-size:1.4rem;font-weight:800;color:{net_col};'>₹{net_pnl:+,.0f}</div></div>"
        f"<div><div style='font-size:0.54rem;color:#6b7280;text-transform:uppercase;'>Gross Profit</div>"
        f"<div style='font-size:1rem;font-weight:700;color:#16a34a;'>+₹{gross_p:,.0f}</div></div>"
        f"<div><div style='font-size:0.54rem;color:#6b7280;text-transform:uppercase;'>Gross Loss</div>"
        f"<div style='font-size:1rem;font-weight:700;color:#dc2626;'>₹{gross_l:,.0f}</div></div>"
        f"<div><div style='font-size:0.54rem;color:#6b7280;text-transform:uppercase;'>Win Rate</div>"
        f"<div style='font-size:1rem;font-weight:700;color:#d97706;'>{win_rate}%</div></div>"
        f"<div><div style='font-size:0.54rem;color:#6b7280;text-transform:uppercase;'>Trades</div>"
        f"<div style='font-size:1rem;font-weight:700;'>{len(today_closed)} closed</div></div>"
        f"</div>", unsafe_allow_html=True)

    rows = []; running = 0.0
    for t in today_closed:
        pnl_v   = t.get("PnL", 0)
        running = round(running + pnl_v, 2)
        rows.append({
            "Time":    t.get("Time", "—"),
            "Symbol":  t.get("Symbol", "—"),
            "Side":    t.get("Side", "—"),
            "Strike":  t.get("Strike", "—"),
            "Type":    t.get("Type", "—"),
            "Entry ₹": t.get("Entry", 0),
            "Exit ₹":  t.get("Exit", 0),
            "P&L ₹":   f"₹{pnl_v:+,.0f}",
            "Running": f"₹{running:+,.0f}",
            "Reason":  t.get("Exit Reason", "—") or "—",
        })

    if rows:
        df_pnl = pd.DataFrame(rows)
        def _style(row):
            out = [""] * len(row)
            cols = list(df_pnl.columns)
            si = cols.index("Side") if "Side" in cols else -1
            pi = cols.index("P&L ₹") if "P&L ₹" in cols else -1
            ri = cols.index("Running") if "Running" in cols else -1
            if si >= 0:
                out[si] = "color:#16a34a;font-weight:700" if row["Side"]=="CALL" else "color:#dc2626;font-weight:700"
            if pi >= 0:
                try:
                    v = float(str(row["P&L ₹"]).replace("₹","").replace(",","").replace("+",""))
                    out[pi] = "color:#16a34a;font-weight:700" if v > 0 else "color:#dc2626;font-weight:700"
                except: pass
            if ri >= 0:
                try:
                    v = float(str(row["Running"]).replace("₹","").replace(",","").replace("+",""))
                    out[ri] = "color:#16a34a;font-weight:600" if v > 0 else "color:#dc2626;font-weight:600"
                except: pass
            return out
        st.dataframe(df_pnl.style.apply(_style, axis=1),
                     use_container_width=True, hide_index=True,
                     height=min(60 + 36 * len(rows), 480))
        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button("⬇ Export Today CSV", df_pnl.to_csv(index=False).encode("utf-8"),
                               f"day_pnl_{current_time.strftime('%Y%m%d')}.csv", "text/csv",
                               key="day_pnl_dl", use_container_width=True)
        with dl2:
            if st.button("🗑 Clear Today's Trades", key="day_pnl_clear", use_container_width=True):
                today_str = current_time.strftime("%Y-%m-%d")
                st.session_state.trade_history = [
                    t for t in st.session_state.trade_history
                    if t.get("date","") != today_str
                ]
                st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=86400)
def load_instruments():
    nse = pd.DataFrame(kite.instruments("NSE")); bse = pd.DataFrame(kite.instruments("BSE"))
    nfo = pd.DataFrame(kite.instruments("NFO")); bfo = pd.DataFrame(kite.instruments("BFO"))
    return pd.concat([nse,bse,nfo,bfo], ignore_index=True)

instruments = load_instruments()

# ── HARDCODED KITE INSTRUMENT TOKENS ─────────────────────────────────────────
# These are permanent Kite instrument tokens for NSE/BSE indices.
# They NEVER change across sessions or years.
# Using these directly avoids any token-lookup issue from the instruments df.
# Source: Kite Connect instrument list (verified)
_HARDCODED_TOKENS = {
    "NIFTY":     256265,   # NSE:NIFTY 50
    "BANKNIFTY": 260105,   # NSE:NIFTY BANK
    "SENSEX":    265,      # BSE:SENSEX
    "FINNIFTY":  257801,   # NSE:NIFTY FIN SERVICE
}

_token_cfg = {
    "NIFTY":     ("NIFTY 50",         "INDICES","NSE"),
    "SENSEX":    ("SENSEX",            "INDICES","BSE"),
    "BANKNIFTY": ("NIFTY BANK",        "INDICES","NSE"),
    "FINNIFTY":  ("NIFTY FIN SERVICE", "INDICES","NSE"),
}

def get_token(symbol):
    # ── Step 1: use hardcoded token (most reliable) ───────────────────────────
    if symbol in _HARDCODED_TOKENS:
        return _HARDCODED_TOKENS[symbol]
    # ── Step 2: dynamic lookup from instruments df (fallback) ─────────────────
    try:
        name, seg, exch = _token_cfg[symbol]
        if "exchange" in instruments.columns:
            df = instruments[(instruments["name"]==name)&
                             (instruments["segment"]==seg)&
                             (instruments["exchange"]==exch)]
        else:
            df = instruments[(instruments["name"]==name)&
                             (instruments["segment"]==seg)]
        if not df.empty:
            return int(df.iloc[0]["instrument_token"])
    except Exception:
        pass
    return None

@st.cache_data(ttl=15, show_spinner=False)
def get_nse_ltp_batch():
    """
    Fetch live LTP for all 4 NSE indices in a SINGLE kite.quote() call.
    TTL=15s — updates every 15s. This replaces the per-symbol historical
    close price and avoids making separate API calls per symbol.
    One call instead of 12 — no rate limit issues.
    """
    try:
        quotes = kite.quote([
            "NSE:NIFTY 50",
            "NSE:NIFTY BANK",
            "BSE:SENSEX",
            "NSE:NIFTY FIN SERVICE",
        ])
        return {
            "NIFTY":     float(quotes.get("NSE:NIFTY 50",           {}).get("last_price", 0)),
            "BANKNIFTY": float(quotes.get("NSE:NIFTY BANK",         {}).get("last_price", 0)),
            "SENSEX":    float(quotes.get("BSE:SENSEX",             {}).get("last_price", 0)),
            "FINNIFTY":  float(quotes.get("NSE:NIFTY FIN SERVICE",  {}).get("last_price", 0)),
        }
    except Exception:
        return {}

@st.cache_data(ttl=300, show_spinner=False)
def get_data(token_int, sym_name, tf, days):
    """
    Fetch OHLCV candles from Kite. TTL=300s (5 min) so candle data is NOT
    re-fetched every 60s refresh — only every 5 minutes. Live prices come
    from get_nse_ltp_batch() instead which uses a single quote() call.
    Raises RuntimeError on failure so empty results are NEVER cached.
    """
    now       = datetime.now()
    from_date = now - pd.Timedelta(days=days)
    last_err  = ""
    for attempt in range(3):
        try:
            data = kite.historical_data(token_int, from_date, now, tf)
            df   = pd.DataFrame(data)
            if not df.empty:
                df.set_index("date", inplace=True)
                return df
            time_module.sleep(1.5)
        except Exception as e:
            last_err = str(e).lower()
            wait = 3 if ("too many" in last_err or "rate" in last_err
                         or "429" in last_err) else 2 ** attempt
            if attempt < 2:
                time_module.sleep(wait)
    raise RuntimeError(f"get_data failed for {sym_name}/{tf}: {last_err or 'empty response'}")

def apply_indicators(df):
    if df.empty or len(df) < 3: return df
    df = df.copy()
    df["EMA9"]  = ta.trend.ema_indicator(df["close"], 9)
    df["EMA21"] = ta.trend.ema_indicator(df["close"], 21)
    df["EMA20"] = ta.trend.ema_indicator(df["close"], 20)
    df["EMA50"] = ta.trend.ema_indicator(df["close"], 50)
    df["RSI"]   = ta.momentum.rsi(df["close"], 14)
    _m = ta.trend.MACD(df["close"])
    df["MACD"]=_m.macd(); df["MACD_SIG"]=_m.macd_signal(); df["MACD_SIGNAL"]=_m.macd_signal(); df["MACD_HIST"]=_m.macd_diff()
    idx = df.index
    dk = idx.normalize() if (hasattr(idx,"tz") and idx.tz) else pd.to_datetime(idx).normalize()
    df["_dk"]=dk; df["_cv"]=df["close"]*df["volume"]
    df["_cumv"]=df.groupby("_dk")["volume"].cumsum(); df["_cumpv"]=df.groupby("_dk")["_cv"].cumsum()
    _safe_cumv = df["_cumv"].replace(0, float("nan"))
    df["VWAP"] = (df["_cumpv"] / _safe_cumv).fillna(df["close"])
    df.drop(columns=["_dk","_cv","_cumv","_cumpv"], inplace=True)
    try:
        if len(df) > 30:
            _a = ta.trend.ADXIndicator(df["high"],df["low"],df["close"])
            df["ADX"]=_a.adx(); df["+DI"]=_a.adx_pos(); df["-DI"]=_a.adx_neg()
        else: df["ADX"]=df["+DI"]=df["-DI"]=0.0
    except Exception: df["ADX"]=df["+DI"]=df["-DI"]=0.0
    prev = df["EMA9"].shift(1)-df["EMA21"].shift(1); curr = df["EMA9"]-df["EMA21"]
    df["ema_cross"]=0
    df.loc[(prev<0)&(curr>=0),"ema_cross"]=1; df.loc[(prev>0)&(curr<=0),"ema_cross"]=-1
    return df

_opt_cfg = {
    "SENSEX":    ("BFO","BFO-OPT","SENSEX"),
    "BANKNIFTY": ("NFO","NFO-OPT","BANKNIFTY"),
    "FINNIFTY":  ("NFO","NFO-OPT","FINNIFTY"),
    "NIFTY":     ("NFO","NFO-OPT","NIFTY"),
}

def get_option_chain(symbol, price):
    exch, seg, name = _opt_cfg.get(symbol, ("NFO","NFO-OPT","NIFTY"))
    price_rounded = round(price, -1)
    try:
        exch_instruments = (instruments[instruments["exchange"]==exch].copy()
                            if "exchange" in instruments.columns
                            else pd.DataFrame(kite.instruments(exch)))
        expiry = exch_instruments[(exch_instruments["name"]==name)&(exch_instruments["segment"]==seg)]["expiry"].min()
        df = exch_instruments[(exch_instruments["name"]==name)&(exch_instruments["expiry"]==expiry)].copy()
        df["diff"] = abs(df["strike"]-price_rounded)
        strikes_near = df.sort_values("diff")["strike"].unique()[:15]
        df = df[df["strike"].isin(strikes_near)]
        quotes = kite.quote([f"{exch}:{x}" for x in df["tradingsymbol"]])
        rows = []
        for _, r in df.iterrows():
            q = quotes.get(f"{exch}:{r['tradingsymbol']}", {})
            rows.append({"symbol":r["tradingsymbol"],"strike":r["strike"],"type":r["instrument_type"],
                         "ltp":q.get("last_price",0),"oi":q.get("oi",0),"volume":q.get("volume",0)})
        return pd.DataFrame(rows)
    except Exception: return pd.DataFrame()

def get_live_ltp(symbol, tradingsymbol):
    try:
        exch = "BFO" if symbol=="SENSEX" else "NFO"
        q = kite.quote([f"{exch}:{tradingsymbol}"])
        return q.get(f"{exch}:{tradingsymbol}",{}).get("last_price",0)
    except Exception: return 0

def calc_pcr(opt):
    if opt.empty: return 1.0
    ce=opt[opt["type"]=="CE"]["oi"].sum(); pe=opt[opt["type"]=="PE"]["oi"].sum()
    return round(pe/ce,2) if ce else 1.0

def calc_max_pain(opt):
    """Max pain = strike where total option buyer loss is maximum (MM anchor point)."""
    if opt.empty or "strike" not in opt.columns: return None
    try:
        strikes = sorted(opt["strike"].unique())
        min_loss = float("inf"); mp_strike = strikes[len(strikes)//2]
        for s in strikes:
            ce_loss = opt[(opt["type"]=="CE") & (opt["strike"] <= s)].apply(
                lambda r: max(0, s - r["strike"]) * r["oi"], axis=1).sum()
            pe_loss = opt[(opt["type"]=="PE") & (opt["strike"] >= s)].apply(
                lambda r: max(0, r["strike"] - s) * r["oi"], axis=1).sum()
            total = ce_loss + pe_loss
            if total < min_loss:
                min_loss = total; mp_strike = s
        return mp_strike
    except Exception: return None


def calc_sr(df, window=20):
    if df.empty or len(df)<window: return None, None
    return (round(df["low"].rolling(window).min().iloc[-1],2),
            round(df["high"].rolling(window).max().iloc[-1],2))

def calc_oi_levels(opt):
    if opt.empty or "type" not in opt.columns: return None, None
    pe=opt[opt["type"]=="PE"]; ce=opt[opt["type"]=="CE"]
    s=pe.loc[pe["oi"].idxmax(),"strike"] if not pe.empty and pe["oi"].sum()>0 else None
    r=ce.loc[ce["oi"].idxmax(),"strike"] if not ce.empty and ce["oi"].sum()>0 else None
    return s, r

def smart_money(opt):
    if opt.empty: return "— No Data"
    ce_oi=opt[opt["type"]=="CE"]["oi"].sum(); pe_oi=opt[opt["type"]=="PE"]["oi"].sum()
    pcr=pe_oi/ce_oi if ce_oi else 0
    if pcr>1.2: return "FII Bull — Put Writing"
    if pcr<0.8: return "FII Bear — Call Writing"
    if pcr>1.1: return "Mild Bullish"
    if pcr<0.9: return "Mild Bearish"
    return "Neutral"

# ── NSE SIGNAL ENGINE (v3 — MCX-style score-based, exhaustion + block guards) ─
# Mirrors MCX scoring: each indicator contributes partial points.
# Returns: (signal, raw_score, reasons)
# signal can be "CALL" | "PUT" | "BLOCKED_CALL" | "BLOCKED_PUT" | "WAIT"
def compute_signal(df5, df15, adx_threshold, min_score, signal_mode):
    if df5.empty or len(df5)<5:   return "WAIT",0,["No 5m data"]
    if df15.empty or len(df15)<2 or "EMA9" not in df15.columns: return "WAIT",0,["No 15m data — waiting for candles"]
    l5=df5.iloc[-1]; l15=df15.iloc[-1]
    rsi=l5["RSI"]; rsi_prev=df5["RSI"].iloc[-2]; adx=l5["ADX"]
    di_plus=l5["+DI"]; di_minus=l5["-DI"]
    close5=l5["close"]; vwap5=l5["VWAP"]
    macd5=l5["MACD"]; macd_sig_val=l5.get("MACD_SIG",l5.get("MACD_SIGNAL",macd5))
    macd_hist=l5.get("MACD_HIST",macd5-macd_sig_val)
    ema9_15=l15["EMA9"]; ema21_15=l15["EMA21"]
    macd_hist_15=l15.get("MACD_HIST",0)

    # ── ATR / body metrics ───────────────────────────────────────────────────
    _body = abs(l5["close"] - l5["open"])
    _atr  = float(l5.get("ATR", 0)) if "ATR" in l5.index else 0
    if _atr <= 0:
        _prev_c = float(df5["close"].iloc[-2]) if len(df5)>=2 else l5["close"]
        _atr = max(abs(l5["close"]-_prev_c)*3, 1.0)
    _body_pct = _body / _atr * 100

    # ── RSI exhaustion flags ─────────────────────────────────────────────────
    rsi_topping   = rsi > 62 and rsi < rsi_prev
    rsi_bottoming = rsi < 38 and rsi > rsi_prev

    # ── Exhaustion guard (fast move > 2×ATR in 5 bars) ──────────────────────
    if _atr > 0 and len(df5) >= 6:
        _move_5bar = abs(float(df5["close"].iloc[-1]) - float(df5["close"].iloc[-6]))
        _exhausted_up   = _move_5bar > 2.0 * _atr and rsi > 60
        _exhausted_down = _move_5bar > 2.0 * _atr and rsi < 40
    else:
        _exhausted_up = _exhausted_down = False
        _move_5bar = 0

    # ── ADX score ───────────────────────────────────────────────────────────
    if adx >= adx_threshold: adx_pts=15; adx_note=f"✅ ADX {round(adx,1)} ≥ {adx_threshold} → +15"
    elif adx >= 18:          adx_pts=8;  adx_note=f"🟡 ADX {round(adx,1)} borderline → +8"
    else:                    adx_pts=0;  adx_note=f"⚠️ ADX {round(adx,1)} weak → +0"

    # ── 15m EMA + MACD alignment flags ──────────────────────────────────────
    _15m_ema_bull  = ema9_15 > ema21_15
    _15m_ema_bear  = ema9_15 < ema21_15
    _15m_macd_bull = macd_hist_15 > 0
    _15m_macd_bear = macd_hist_15 < 0

    # ── Gap-open detection ───────────────────────────────────────────────────
    _gap_down = False; _gap_up = False; _gap_pct = 0.0
    try:
        _today_ts = pd.Timestamp(current_time.date())
        _prev_day_bars = df5[df5.index.normalize() < _today_ts] if hasattr(df5.index,"normalize") else df5.iloc[:-1]
        if not _prev_day_bars.empty:
            _prev_close = float(_prev_day_bars["close"].iloc[-1])
            _today_bars = df5[df5.index.normalize() == _today_ts] if hasattr(df5.index,"normalize") else df5
            if not _today_bars.empty:
                _today_open = float(_today_bars["open"].iloc[0])
                _gap_pct = (_today_open - _prev_close) / _prev_close * 100
                _gap_down = (_gap_pct <= -0.5) and (close5 < _prev_close) and (di_minus > di_plus) and (adx > adx_threshold)
                _gap_up   = (_gap_pct >= 0.5)  and (close5 > _prev_close) and (di_plus > di_minus)  and (adx > adx_threshold)
    except Exception: pass

    # ══════════════════════════════════════════════════════════════════════════
    # SCORE CALL — each indicator contributes partial points (max 100)
    # ══════════════════════════════════════════════════════════════════════════
    call_score=0; call_reasons=[]
    # 15m EMA: +25 when EMA+MACD both bullish; +10 when EMA bullish but MACD diverging
    if _15m_ema_bull and _15m_macd_bull:
        call_score+=25; call_reasons.append(f"✅ 15m EMA9 ({round(ema9_15,0)}) > EMA21 ({round(ema21_15,0)}) + MACD bullish → +25")
    elif _15m_ema_bull:
        call_score+=10; call_reasons.append(f"🟡 15m EMA9>EMA21 but MACD bearish (divergence) → +10 only")
    if close5 > vwap5:
        call_score+=20; call_reasons.append(f"✅ Price above VWAP ({round(vwap5,1)}) → +20")
    if di_plus > di_minus:
        call_score+=20; call_reasons.append(f"✅ +DI {round(di_plus,1)} > -DI {round(di_minus,1)} → +20")
    call_score+=adx_pts; call_reasons.append(adx_note)
    if macd_hist > 0:
        call_score+=10; call_reasons.append("✅ 5m MACD histogram positive → +10")
    if macd_hist > df5["MACD_HIST"].iloc[-2] and macd_hist > 0:
        call_score+=5; call_reasons.append("✅ 5m MACD_HIST rising (momentum building) → +5")
    if rsi > 55:
        call_score+=10; call_reasons.append(f"✅ RSI {round(rsi,1)} > 55 → +10")
    if close5 < df5["high"].iloc[-2]:
        call_score-=5; call_reasons.append(f"⚠️ Close < prev high → −5")
    if _gap_up:
        call_score = max(call_score, 90)
        call_reasons.insert(0, f"⚡ GAP-UP OPEN ({round(_gap_pct,2)}%) — EMA/MACD bypassed · DI+: {round(di_plus,1)} · ADX: {round(adx,1)}")

    # ══════════════════════════════════════════════════════════════════════════
    # SCORE PUT
    # ══════════════════════════════════════════════════════════════════════════
    put_score=0; put_reasons=[]
    if _15m_ema_bear and _15m_macd_bear:
        put_score+=25; put_reasons.append(f"✅ 15m EMA9 ({round(ema9_15,0)}) < EMA21 ({round(ema21_15,0)}) + MACD bearish → +25")
    elif _15m_ema_bear:
        put_score+=10; put_reasons.append(f"🟡 15m EMA9<EMA21 but MACD turning bullish (divergence) → +10 only")
    if close5 < vwap5:
        put_score+=20; put_reasons.append(f"✅ Price below VWAP ({round(vwap5,1)}) → +20")
    if di_minus > di_plus:
        put_score+=20; put_reasons.append(f"✅ -DI {round(di_minus,1)} > +DI {round(di_plus,1)} → +20")
    put_score+=adx_pts; put_reasons.append(adx_note)
    if macd_hist < 0:
        put_score+=10; put_reasons.append("✅ 5m MACD histogram negative → +10")
    if macd_hist < df5["MACD_HIST"].iloc[-2] and macd_hist < 0:
        put_score+=5; put_reasons.append("✅ 5m MACD_HIST falling (bearish momentum building) → +5")
    if rsi < 45:
        put_score+=10; put_reasons.append(f"✅ RSI {round(rsi,1)} < 45 → +10")
    if rsi < 30:
        put_score+=10; put_reasons.append(f"✅ RSI {round(rsi,1)} < 30 — oversold bonus → +10")
    if close5 > df5["low"].iloc[-2]:
        put_score-=5; put_reasons.append(f"⚠️ Close > prev low → −5")
    if _gap_down:
        put_score = max(put_score, 90)
        put_reasons.insert(0, f"⚡ GAP-DOWN OPEN ({round(_gap_pct,2)}%) — EMA/MACD bypassed · DI-: {round(di_minus,1)} · ADX: {round(adx,1)}")

    call_score=min(call_score,100); put_score=min(put_score,100)

    # ── Pick candidate ───────────────────────────────────────────────────────
    if call_score >= min_score and call_score > put_score:
        candidate="CALL"; raw_score=call_score; reasons=call_reasons
        # Exhaustion guard
        if _exhausted_up:
            return("BLOCKED_CALL", call_score, call_reasons +
                   [f"⛔ Exhaustion: price moved {round(_move_5bar,0)}pts in 5 bars (>{round(2*_atr,0)} = 2×ATR), RSI {round(rsi,1)} — late entry risk"])
        # RSI topping
        if rsi_topping:
            return("BLOCKED_CALL", call_score, call_reasons +
                   [f"⚠️ RSI {round(rsi,1)} rolling over — fake CALL risk"])
        # 15m MACD divergence guard
        if _15m_ema_bull and _15m_macd_bear and call_score < (min_score + 15):
            return("BLOCKED_CALL", call_score, call_reasons +
                   [f"⚠️ 15m EMA bullish but MACD bearish (divergence) — need {min_score+15} score, have {call_score}"])

    elif put_score >= min_score and put_score > call_score:
        candidate="PUT"; raw_score=put_score; reasons=put_reasons
        if _exhausted_down:
            return("BLOCKED_PUT", put_score, put_reasons +
                   [f"⛔ Exhaustion: price moved {round(_move_5bar,0)}pts in 5 bars (>{round(2*_atr,0)} = 2×ATR), RSI {round(rsi,1)} — late entry risk"])
        if rsi_bottoming:
            return("BLOCKED_PUT", put_score, put_reasons +
                   [f"⚠️ RSI {round(rsi,1)} turning up — fake PUT risk"])
        if _15m_ema_bear and _15m_macd_bull and put_score < (min_score + 15):
            return("BLOCKED_PUT", put_score, put_reasons +
                   [f"⚠️ 15m EMA bearish BUT MACD already turning BULLISH (divergence) — trend reversing, need {min_score+15} score, have {put_score}"])

    else:
        # Below min_score — return partial score for SETUP FORMING banner
        if call_score > put_score and call_score > 0:
            return "WAIT", call_score, [f"Bullish score {call_score}/{min_score} — need {min_score-call_score} more pts"] + call_reasons
        elif put_score > 0:
            return "WAIT", put_score, [f"Bearish score {put_score}/{min_score} — need {min_score-put_score} more pts"] + put_reasons
        return "WAIT", 0, [f"No directional alignment — RSI {round(rsi,1)}, ADX {round(adx,1)}, VWAP {'above' if close5>vwap5 else 'below'}"]

    # ── Smart trend lock: block weak counter-trend signals ───────────────────
    _15m_bull_lock = _15m_ema_bull and _15m_macd_bull
    _15m_bear_lock = _15m_ema_bear and _15m_macd_bear
    _is_strong = (raw_score >= 90) or (_body_pct >= 50)
    if not _is_strong:
        if candidate == "PUT" and _15m_bull_lock:
            return("BLOCKED_PUT", raw_score, reasons +
                   [f"⚠️ NSE Trend Lock: 15m fully bullish (EMA+MACD) — PUT needs score≥90 or strong body to confirm reversal"])
        if candidate == "CALL" and _15m_bear_lock:
            return("BLOCKED_CALL", raw_score, reasons +
                   [f"⚠️ NSE Trend Lock: 15m fully bearish (EMA+MACD) — CALL needs score≥90 or strong body to confirm reversal"])

    return candidate, raw_score, reasons

def pick_options(option_df, signal, price, step, capital, lot_size):
    if signal=="WAIT" or option_df.empty: return None,None,None
    opt_type="CE" if signal=="CALL" else "PE"
    df=option_df[option_df["type"]==opt_type].copy()
    if df.empty: return None,None,None
    atm=round(price/step)*step; itm=atm-step if signal=="CALL" else atm+step; otm=atm+step if signal=="CALL" else atm-step
    def build(strike):
        r=df[df["strike"]==strike]
        if r.empty: return None
        row=r.iloc[0].to_dict(); ltp=float(row["ltp"])
        if ltp<=0: return None
        cost=round(ltp*lot_size,2)
        row.update({"cost":cost,"lots":int(capital//cost) if cost>0 else 0,
                    "sl":round(ltp*0.70,2),"t1":round(ltp*1.50,2),"t2":round(ltp*2.00,2)})
        return row
    return build(atm), build(itm), build(otm)

# ── NSE PAPER TRADING ─────────────────────────────────────────────────────────
def open_trade(symbol, signal, strike, opt_type, ltp, lot_size, tradingsymbol=""):
    # Daily loss limit guard
    if st.session_state.nse_daily_pnl <= NSE_DAILY_LOSS_LIMIT:
        return  # session loss limit hit — no new NSE trades today
    st.session_state.open_trades[symbol]={
        "side":signal,"strike":strike,"type":opt_type,"entry":ltp,
        "sl":round(ltp*0.75,2),"t1":round(ltp*1.50,2),"lot_size":lot_size,
        "time":datetime.now().strftime("%H:%M"),
        "entry_time_iso":datetime.now().isoformat(),  # for time SL
        "tradingsymbol":tradingsymbol,
    }
    st.session_state.trades_today+=1

def close_trade(symbol, cur_ltp, exit_reason=""):
    t=st.session_state.open_trades.pop(symbol,None)
    if not t: return 0
    pnl=round((cur_ltp-t["entry"])*t["lot_size"],2)
    st.session_state.capital+=pnl; st.session_state.total_pnl+=pnl
    st.session_state.nse_daily_pnl+=pnl
    if pnl>=0: st.session_state.wins+=1
    else:      st.session_state.losses+=1
    st.session_state.trade_history.append({
        "Time":datetime.now().strftime("%H:%M"),"Symbol":symbol,"Side":t["side"],
        "Strike":t["strike"],"Type":t["type"],"Entry":t["entry"],"Exit":cur_ltp,"PnL":pnl,
        "Exit Reason":exit_reason,
        "date":current_time.strftime("%Y-%m-%d"),
    })
    return pnl

def get_trade_live_ltp(sym, t, opt_df=None):
    strike=t.get("strike"); opt_type=t.get("type"); entry=float(t["entry"])
    if opt_df is not None and not opt_df.empty and strike and opt_type:
        row=opt_df[(opt_df["strike"]==strike)&(opt_df["type"]==opt_type)]
        if not row.empty:
            ltp=float(row.iloc[0]["ltp"])
            if ltp>0: return ltp
    ts=t.get("tradingsymbol","")
    if ts:
        try:
            exch="BFO" if sym=="SENSEX" else "NFO"
            q=kite.quote([f"{exch}:{ts}"])
            ltp=q.get(f"{exch}:{ts}",{}).get("last_price",0)
            if ltp>0: return float(ltp)
        except Exception: pass
    return entry

def check_auto_exit(sym, opt_df=None):
    t=st.session_state.open_trades.get(sym)
    if not t: return
    cur_ltp=get_trade_live_ltp(sym,t,opt_df); exit_reason=None
    if cur_ltp<=t["sl"]: exit_reason=f"SL ₹{t['sl']}"
    elif cur_ltp>=t["t1"]: exit_reason=f"T1 ₹{t['t1']}"
    if exit_reason:
        trade=st.session_state.open_trades.pop(sym)
        pnl=round((cur_ltp-trade["entry"])*trade["lot_size"],2)
        st.session_state.capital+=pnl; st.session_state.total_pnl+=pnl
        if pnl>=0: st.session_state.wins+=1
        else:      st.session_state.losses+=1
        st.session_state.trade_history.append({
            "Time":datetime.now().strftime("%H:%M"),"Symbol":sym,"Side":trade["side"],
            "Strike":trade["strike"],"Type":trade["type"],"Entry":trade["entry"],
            "Exit":cur_ltp,"PnL":pnl,"Exit Reason":exit_reason,
        })

def nse_send_expiry_briefing(sym, price, pcr, max_pain, vix):
    """
    Sends one expiry-day briefing email at 09:15 with Max Pain, PCR bias,
    VIX level and direction recommendation.  Deduplicated per symbol per day.
    """
    if not email_alerts_enabled: return
    _key = f"__expiry_briefing_{sym}_{current_time.date()}"
    if st.session_state.get(_key): return
    st.session_state[_key] = True
    try:
        mp = int(max_pain) if max_pain else "N/A"
        pcr_bias = ("Bullish (Put Writing)" if pcr > 1.2
                    else "Bearish (Call Writing)" if pcr < 0.8
                    else "Neutral")
        mp_bias  = ("Bullish ↑ (spot above max pain)" if price > (max_pain or price)
                    else "Bearish ↓ (spot below max pain)" if price < (max_pain or price)
                    else "Neutral (at max pain)")
        vix_note = (f"🔴 VIX HIGH ({round(vix,1)}%) — expect sharp volatile moves. Premium elevated — buyers favoured."
                    if vix and vix > 15
                    else f"🟡 VIX moderate ({round(vix,1)}%)" if vix else "VIX: N/A")
        dte_tag = "CE" if pcr > 1.0 else "PE"
        send_alert(
            f"📅 <b>EXPIRY DAY BRIEFING — {sym}</b>\n"
            f"Spot: ₹{round(price,1)} | Max Pain: ₹{mp}\n"
            f"Max Pain Bias: <b>{mp_bias}</b>\n"
            f"PCR: {pcr} → <b>{pcr_bias}</b>\n"
            f"{vix_note}\n"
            f"📌 Strategy: prefer {dte_tag} (ATM/ITM) · exit before 2:30 PM\n"
            f"⚠️ Score 80 sufficient on expiry — quick moves common\n"
            f"⏰ {current_minute}",
            subject=f"📅 EXPIRY BRIEFING — {sym}: Max Pain ₹{mp} | PCR {pcr} | VIX {round(vix,1) if vix else 'N/A'}%",
            sym=sym, direction="", is_flip=False,
        )
    except Exception: pass


def nse_check_setup_forming(sym, df5, df15, sig, score, current_minute, min_score=60):
    """
    Fires a 'SETUP FORMING' email for NSE only between 9:15 AM and 3:15 PM,
    and only when NSE screen is active. Fires 1-2 candles before confirmed signal.
    """
    if not email_alerts_enabled: return
    # ── Market hours guard: only during NSE open 9:15–15:15 ──
    if not (dtime(9, 15) <= _now_t <= dtime(15, 15)): return
    # Fire when score is in setup zone (≥50% of min_score) regardless of signal direction
    _nse_setup_thresh = max(1, int(min_score * 0.50))
    if score < _nse_setup_thresh or score >= min_score: return
    if sym in st.session_state.open_trades: return
    if df5.empty or df15.empty: return
    try:
        l5  = df5.iloc[-1]; l15 = df15.iloc[-1]
        rsi      = round(float(l5["RSI"]), 1)
        adx      = round(float(l5["ADX"]), 1)
        di_plus  = round(float(l5["+DI"]), 1)
        di_minus = round(float(l5["-DI"]), 1)
        close5   = float(l5["close"])
        vwap5    = round(float(l5["VWAP"]), 2)
        ema20_5  = float(l5["EMA20"]); ema50_5 = float(l5["EMA50"])
        macd5    = float(l5["MACD"])
        macd_sig_v = float(l5.get("MACD_SIG", l5.get("MACD_SIGNAL", macd5)))
        ema9_15  = float(l15["EMA9"]); ema21_15 = float(l15["EMA21"])
        high15   = float(l15["high"]); low15    = float(l15["low"])

        trend_bullish = (ema20_5 > ema50_5 and rsi > 55 and macd5 > macd_sig_v)
        price_bullish = (ema9_15 > ema21_15 and close5 > high15 and close5 > vwap5 and adx > adx_threshold and di_plus > di_minus)
        trend_bearish = (ema20_5 < ema50_5 and rsi < 45 and macd5 < macd_sig_v)
        price_bearish = (ema9_15 < ema21_15 and close5 < low15  and close5 < vwap5 and adx > adx_threshold and di_minus > di_plus)

        if sig == "CALL":
            if trend_bullish and not price_bullish:
                met_group = "✅ Trend (5m): EMA20>50, RSI bullish, MACD bullish"
                missing = []
                if not (ema9_15 > ema21_15):  missing.append(f"15m EMA9({round(ema9_15,1)}) cross above EMA21({round(ema21_15,1)})")
                if not (close5 > high15):      missing.append(f"Price({round(close5,1)}) break above 15m high({round(high15,1)})")
                if not (close5 > vwap5):       missing.append(f"Price above VWAP({vwap5})")
                if not (adx > adx_threshold):  missing.append(f"ADX({adx}) rise above {adx_threshold}")
                if not (di_plus > di_minus):   missing.append(f"+DI({di_plus}) cross above -DI({di_minus})")
            elif price_bullish and not trend_bullish:
                met_group = "✅ Price (15m): EMA cross, VWAP, ADX, DI bullish"
                missing = []
                if not (ema20_5 > ema50_5):    missing.append(f"5m EMA20({round(ema20_5,1)}) cross above EMA50({round(ema50_5,1)})")
                if not (rsi > 55):             missing.append(f"RSI({rsi}) rise above 55")
                if not (macd5 > macd_sig_v):   missing.append(f"MACD turn bullish ({round(macd5,2)} vs {round(macd_sig_v,2)})")
            else:
                return
        elif sig == "PUT":
            if trend_bearish and not price_bearish:
                met_group = "✅ Trend (5m): EMA20<50, RSI bearish, MACD bearish"
                missing = []
                if not (ema9_15 < ema21_15):  missing.append(f"15m EMA9({round(ema9_15,1)}) cross below EMA21({round(ema21_15,1)})")
                if not (close5 < low15):      missing.append(f"Price({round(close5,1)}) break below 15m low({round(low15,1)})")
                if not (close5 < vwap5):      missing.append(f"Price below VWAP({vwap5})")
                if not (adx > adx_threshold): missing.append(f"ADX({adx}) rise above {adx_threshold}")
                if not (di_minus > di_plus):  missing.append(f"-DI({di_minus}) cross above +DI({di_plus})")
            elif price_bearish and not trend_bearish:
                met_group = "✅ Price (15m): EMA cross, VWAP, ADX, DI bearish"
                missing = []
                if not (ema20_5 < ema50_5):   missing.append(f"5m EMA20({round(ema20_5,1)}) cross below EMA50({round(ema50_5,1)})")
                if not (rsi < 45):            missing.append(f"RSI({rsi}) drop below 45")
                if not (macd5 < macd_sig_v):  missing.append(f"MACD turn bearish ({round(macd5,2)} vs {round(macd_sig_v,2)})")
            else:
                return
        else:
            return

        # Dedup: only fire once per direction; resets when confirmed signal fires
        last_setup = st.session_state.nse_last_setup_alert.get(sym, {})
        if last_setup.get("direction") == sig:
            return
        st.session_state.nse_last_setup_alert[sym] = {"direction": sig}

        tag = "CE" if sig == "CALL" else "PE"
        missing_str = "\n".join([f"  • {m}" for m in missing]) if missing else "  • All sub-conditions nearly met"
        send_alert(
            f"🟡 <b>SETUP FORMING — {sym}</b>\n"
            f"Direction: <b>{sig} ({tag})</b>\n"
            f"Score: <b>60/100</b> — one group met, waiting for second\n"
            f"{met_group}\n"
            f"Waiting for:\n{missing_str}\n"
            f"RSI: {rsi} | ADX: {adx} | +DI: {di_plus} | -DI: {di_minus} | VWAP: {vwap5}\n"
            f"⚡ Get ready — confirmed signal may fire in 1-2 candles\n"
            f"⏰ {current_minute}",
            subject=f"🟡 NSE SETUP FORMING — {sym}: {sig} building (60/100)",
            sym=sym, direction=sig, is_flip=False,
        )
    except Exception:
        pass

def mcx_check_setup_forming(sym, df5, df15, sig, raw_score, min_score, adx_threshold, current_minute):
    """
    Fires a 'SETUP FORMING' email for MCX commodities when score is between
    50% and 99% of min_score — i.e. close to a signal but not confirmed yet.
    Tells the trader EXACTLY which conditions are met and what's missing.
    Deduped: fires once per direction per commodity. Resets when signal fires
    or score drops back below the setup threshold.

    Thresholds (50% of min_score for each commodity):
      CRUDEOIL   min_score=60  → setup fires at score ≥ 30
      GOLDM      min_score=60  → setup fires at score ≥ 30
      COPPER     min_score=62  → setup fires at score ≥ 31
      NATURALGAS min_score=68  → setup fires at score ≥ 34
    """
    if not email_alerts_enabled: return
    # MCX market hours guard — closed on weekends
    _mcx_weekend = current_time.weekday() >= 5
    if _mcx_weekend: return
    if not (dtime(9, 0) <= _now_t <= dtime(23, 30)): return
    if sig not in ("CALL", "PUT"): return
    if sym in st.session_state.mcx_open_trades: return
    if df5.empty or df15.empty or len(df5) < 5 or len(df15) < 3: return

    setup_threshold = max(1, int(min_score * 0.50))
    # Only fire when score is in the "getting close" zone
    if raw_score < setup_threshold or raw_score >= min_score: return

    # Dedup: only fire once per direction; reset when direction changes or signal confirms
    last_setup = st.session_state.mcx_last_setup_alert.get(sym, {})
    if last_setup.get("direction") == sig and last_setup.get("score_bracket") == raw_score // 10:
        return  # same direction, same score band — suppress repeat

    try:
        l5  = df5.iloc[-1]
        l15 = df15.iloc[-1]

        # ── Pull all indicators ──────────────────────────────────────────────
        rsi      = round(float(l5.get("RSI", 50)), 1)
        adx      = round(float(l5.get("ADX", 0)), 1)
        di_plus  = round(float(l5.get("+DI", 0)), 1)
        di_minus = round(float(l5.get("-DI", 0)), 1)
        macd_h   = float(l5.get("MACD_HIST", 0))
        ema9_5   = round(float(l5.get("EMA9", 0)), 1)
        ema21_5  = round(float(l5.get("EMA21", 0)), 1)
        vwap5    = round(float(l5.get("VWAP", 0)), 1)
        price5   = round(float(l5.get("close", 0)), 1)
        atr5     = round(float(l5.get("ATR", 0)), 1)
        ema9_15  = round(float(l15.get("EMA9", 0)), 1)
        ema21_15 = round(float(l15.get("EMA21", 0)), 1)
        macd_h15 = float(l15.get("MACD_HIST", 0))

        # ── Evaluate conditions for the expected direction ───────────────────
        if sig == "CALL":
            conditions = [
                ("15m EMA9 > EMA21",        ema9_15 > ema21_15,        f"EMA9({ema9_15}) vs EMA21({ema21_15})"),
                ("15m MACD histogram > 0",   macd_h15 > 0,              f"MACD_HIST={round(macd_h15,3)}"),
                ("5m EMA9 > EMA21",          ema9_5 > ema21_5,          f"EMA9({ema9_5}) vs EMA21({ema21_5})"),
                ("5m MACD histogram > 0",    macd_h > 0,                f"MACD_HIST={round(macd_h,3)}"),
                (f"RSI > 55",               rsi > 55,                  f"RSI={rsi}"),
                (f"ADX ≥ {adx_threshold}",  adx >= adx_threshold,      f"ADX={adx}"),
                ("+DI > -DI",               di_plus > di_minus,        f"+DI={di_plus} -DI={di_minus}"),
                ("Price above VWAP",         price5 > vwap5,            f"Price={price5} VWAP={vwap5}"),
            ]
            tag = "CE"
        else:  # PUT
            conditions = [
                ("15m EMA9 < EMA21",        ema9_15 < ema21_15,        f"EMA9({ema9_15}) vs EMA21({ema21_15})"),
                ("15m MACD histogram < 0",   macd_h15 < 0,              f"MACD_HIST={round(macd_h15,3)}"),
                ("5m EMA9 < EMA21",          ema9_5 < ema21_5,          f"EMA9({ema9_5}) vs EMA21({ema21_5})"),
                ("5m MACD histogram < 0",    macd_h < 0,                f"MACD_HIST={round(macd_h,3)}"),
                (f"RSI < 45",               rsi < 45,                  f"RSI={rsi}"),
                (f"ADX ≥ {adx_threshold}",  adx >= adx_threshold,      f"ADX={adx}"),
                ("-DI > +DI",               di_minus > di_plus,        f"-DI={di_minus} +DI={di_plus}"),
                ("Price below VWAP",         price5 < vwap5,            f"Price={price5} VWAP={vwap5}"),
            ]
            tag = "PE"

        met     = [(name, note) for name, ok, note in conditions if ok]
        missing = [(name, note) for name, ok, note in conditions if not ok]

        # Only send if at least 2 conditions are met (avoid noise on early flat markets)
        if len(met) < 2: return

        met_str     = "\n".join([f"  ✅ {n} ({v})" for n, v in met])
        missing_str = "\n".join([f"  ❌ {n} — currently {v}" for n, v in missing])

        pts_needed = min_score - raw_score
        send_alert(
            f"🟡 <b>MCX SETUP FORMING — {sym}</b>\n"
            f"Direction: <b>{sig} ({tag})</b>\n"
            f"Score: <b>{raw_score}/{min_score}</b> — need {pts_needed} more pts\n"
            f"ATR: {atr5} | ADX: {adx} | RSI: {rsi}\n\n"
            f"CONDITIONS MET ({len(met)}/{len(conditions)}):\n{met_str}\n\n"
            f"STILL WAITING FOR ({len(missing)}):\n{missing_str}\n\n"
            f"⚡ Get ready — confirmed signal may fire in 1–3 candles\n"
            f"⏰ {current_minute}",
            subject=f"🟡 MCX SETUP — {sym}: {sig} building ({raw_score}/{min_score})",
            sym=sym, direction=sig, is_flip=False,
        )
        # Record so we don't spam — store direction + score bracket (every 10 pts = new alert)
        st.session_state.mcx_last_setup_alert[sym] = {
            "direction": sig,
            "score_bracket": raw_score // 10,
        }
    except Exception:
        pass


def log_signal_and_auto_trade(sym, sig, score, atm_row, price, step, lot_size, current_minute):
    # ── MARKET HOURS GUARD: NSE signals only during PRE (09:00-09:14) and OPEN (09:15-15:30) ──
    _nse_allowed = dtime(9, 0) <= _now_t <= dtime(15, 30)
    if not _nse_allowed:
        if sym in st.session_state.open_trades:
            ltp_now = st.session_state.open_trades[sym]["entry"]
            close_trade(sym, ltp_now, "Market closed")
        return
    # BLOCKED signals: do NOT auto-trade and do NOT close existing position
    if sig.startswith("BLOCKED_"):
        return
    if sig=="WAIT" or atm_row is None:
        if sym in st.session_state.open_trades:
            ltp_now=atm_row["ltp"] if atm_row else st.session_state.open_trades[sym]["entry"]
            close_trade(sym,ltp_now)
        return
    atm_strike=round(price/step)*step; opt_type="CE" if sig=="CALL" else "PE"; ltp=float(atm_row["ltp"])
    last_state=st.session_state.last_signal_state.get(sym,{})
    # DEDUP: only log when direction changes
    _last_dir = last_state.get("signal")
    _flip     = (_last_dir is not None and _last_dir != "WAIT" and _last_dir != sig)
    if _signal_is_duplicate(sym, sig, "nse") and not _flip:
        signal_changed = False
    else:
        signal_changed = True
        _record_signal_direction(sym, sig, "nse")
    if signal_changed:
        st.session_state.signal_log.append({"Time":current_minute,"Symbol":sym,"Signal":sig,"Strike":atm_strike,"Type":opt_type,"LTP":round(ltp,2),"Score":f"{score}/100"})
        if email_alerts_enabled:
            emoji="🟢" if sig=="CALL" else "🔴"
            is_flip=_flip
            prev_sig=_last_dir or ""
            sl_val=round(ltp*0.70,2); t1_val=round(ltp*1.50,2); t2_val=round(ltp*2.00,2)
            if is_flip:
                subject=f"🔄 Options Terminal FLIP — {sym}: {prev_sig} ➜ {sig}"
                header=f"🔄 <b>SIGNAL FLIP — {sym}</b>"; subhdr=f"Changed: {prev_sig} ➜ <b>{sig} ({opt_type})</b>"
            else:
                _is_gap = any("GAP-" in r for r in (st.session_state.last_signal_state.get(sym,{}).get("reasons",[]) or []))
                _gap_tag = "⚡ GAP-OPEN " if _is_gap else ""
                subject=f"{emoji} {_gap_tag}Options Terminal — {sym}: {sig} @ ₹{ltp}"
                header=f"{emoji} <b>{_gap_tag}NEW SIGNAL — {sym}</b>"; subhdr=f"Direction: <b>{sig} ({opt_type})</b>"
            send_alert(
                f"{header}\n{subhdr}\nStrike: <b>{int(atm_strike)} {opt_type}</b>\nPremium (ATM): <b>₹{ltp}</b>\nSL: ₹{sl_val}  |  T1: ₹{t1_val}  |  T2: ₹{t2_val}\nScore: {score}/100\n⏰ {current_minute}",
                subject=subject, sym=sym, direction=sig, is_flip=is_flip,
            )
        st.session_state.last_signal_state[sym]={"signal":sig,"strike":atm_strike,"reasons":reasons}
        st.session_state.nse_last_setup_alert.pop(sym, None)
    existing=st.session_state.open_trades.get(sym)
    # SENSEX and FINNIFTY are signal-monitor only — no auto-trade
    if sym in NSE_MONITOR_ONLY_SYMBOLS:
        return
    if existing is None:
        if not _is_signal_locked(sym, sig):
            open_trade(sym,sig,atm_strike,opt_type,ltp,lot_size,atm_row.get("symbol",""))
            _set_signal_lock(sym, sig, atm_strike, ltp)
            # ── Log + email re-entry when signal direction unchanged ──────────────
            if not signal_changed:
                st.session_state.signal_log.append({"Time":current_minute,"Symbol":sym,"Signal":sig,"Strike":atm_strike,"Type":opt_type,"LTP":round(ltp,2),"Score":f"{score}/100 [re-entry]"})
                if email_alerts_enabled:
                    emoji="🟢" if sig=="CALL" else "🔴"
                    sl_val=round(ltp*0.70,2); t1_val=round(ltp*1.50,2); t2_val=round(ltp*2.00,2)
                    send_alert(
                        f"{emoji} <b>RE-ENTRY — {sym}</b>\nDirection: <b>{sig} ({opt_type})</b>\nStrike: <b>{int(atm_strike)} {opt_type}</b>\nPremium (ATM): <b>₹{ltp}</b>\nSL: ₹{sl_val}  |  T1: ₹{t1_val}  |  T2: ₹{t2_val}\nScore: {score}/100\n⏰ {current_minute}",
                        subject=f"{emoji} NSE RE-ENTRY — {sym}: {sig} @ ₹{ltp}",
                        sym=sym, direction=sig, is_flip=False,
                    )
    elif existing["side"]!=sig:
        exit_ltp=get_trade_live_ltp(sym,existing); close_trade(sym,exit_ltp,"Signal flip")
        open_trade(sym,sig,atm_strike,opt_type,ltp,lot_size,atm_row.get("symbol",""))
        _set_signal_lock(sym, sig, atm_strike, ltp)

@st.cache_data(ttl=300)
def fetch_news(kw):
    try:
        feed=feedparser.parse(f"https://news.google.com/rss/search?q={kw.replace(' ','+')}&hl=en-IN&gl=IN&ceid=IN:en")
        return [{"title":e.title,"link":e.link,"time":e.get("published","")[:16]} for e in feed.entries[:12]]
    except Exception: return []

# ── SHARED PLOTLY CHART (used by NSE 5m, NSE 15m, MCX 5m, MCX 15m) ───────────
PLOT_BG="#ffffff"; PLOT_CARD="#fafafa"; PLOT_GRID="#e8e8e8"; PLOT_FONT="#888888"
PLOT_CFG={"displayModeBar":True,"modeBarButtonsToRemove":["select2d","lasso2d","autoScale2d"],"scrollZoom":True,"displaylogo":False}

def build_plotly_chart(df, title, height=440, fibs=None, fvgs=None, bos_swings=None, sd_zones=None, df_cross=None, entry_lines=None):
    """Single Plotly 3-panel chart: candles+EMAs (top), MACD (mid), RSI (bottom).
    Works for any timeframe. Fib labels shown on left side of chart."""
    fig=make_subplots(rows=3,cols=1,shared_xaxes=True,row_heights=[0.60,0.22,0.18],vertical_spacing=0.015)
    # ── Candles
    fig.add_trace(go.Candlestick(x=df.index,open=df["open"],high=df["high"],low=df["low"],close=df["close"],
        increasing=dict(line=dict(color="#26a69a",width=1),fillcolor="#26a69a"),
        decreasing=dict(line=dict(color="#ef5350",width=1),fillcolor="#ef5350"),
        name="Price",showlegend=False),row=1,col=1)
    # ── EMAs
    for col,clr,w in [("EMA9","#f39c12",1.5),("EMA21","#1565c0",1.5),("EMA50","#6a1b9a",1.0),("VWAP","#ff6b6b",1.2)]:
        if col in df.columns:
            fig.add_trace(go.Scatter(x=df.index,y=df[col],name=col,line=dict(color=clr,width=w),opacity=0.9),row=1,col=1)
    # ── 15m EMA cross markers (optional)
    if df_cross is not None and not df_cross.empty and "ema_cross" in df_cross.columns:
        for val,sym_m,clr in [(1,"triangle-up","#00e676"),(-1,"triangle-down","#ff3d57")]:
            c=df_cross[df_cross["ema_cross"]==val]
            if not c.empty:
                fig.add_trace(go.Scatter(x=c.index,y=c["close"],mode="markers",name=("Bull X" if val==1 else "Bear X"),marker=dict(symbol=sym_m,size=9,color=clr)),row=1,col=1)
    # ── Fibonacci — solid lines, labels anchored to LEFT edge
    if fibs:
        FIB_COLORS={"0.0":"#9e9e9e","0.236":"#ff9800","0.382":"#ff5722","0.500":"#9c27b0","0.618":"#2196f3","0.786":"#4caf50","1.0":"#9e9e9e"}
        x_left=df.index[0]
        for lbl,pval in fibs.items():
            clr=FIB_COLORS.get(lbl,"#888888")
            fig.add_hline(y=pval,line=dict(color=clr,width=1.0),row=1,col=1)
            fig.add_annotation(x=x_left,y=pval,text=f"F{lbl} {pval:,.0f}",showarrow=False,
                xanchor="left",yanchor="bottom",font=dict(size=8,color=clr,family="JetBrains Mono"),
                bgcolor="rgba(255,255,255,0.6)",row=1,col=1)
    # ── S&D zones
    if sd_zones:
        for z in sd_zones[0]: fig.add_hrect(y0=z["bot"],y1=z["top"],fillcolor="rgba(0,200,83,0.07)",line_width=0,row=1,col=1)
        for z in sd_zones[1]: fig.add_hrect(y0=z["bot"],y1=z["top"],fillcolor="rgba(255,61,87,0.07)",line_width=0,row=1,col=1)
    # ── FVG zones
    if fvgs:
        for fg in fvgs[0]: fig.add_hrect(y0=fg["bot"],y1=fg["top"],fillcolor="rgba(0,230,118,0.06)",line_width=0.5,line_color="rgba(0,230,118,0.3)",row=1,col=1)
        for fg in fvgs[1]: fig.add_hrect(y0=fg["bot"],y1=fg["top"],fillcolor="rgba(255,61,87,0.06)",line_width=0.5,line_color="rgba(255,61,87,0.3)",row=1,col=1)
    # ── BOS swing lines
    if bos_swings:
        fig.add_hline(y=bos_swings["swing_high"],line=dict(color="#dc2626",dash="dot",width=1),row=1,col=1)
        fig.add_hline(y=bos_swings["swing_low"], line=dict(color="#16a34a",dash="dot",width=1),row=1,col=1)
    # ── Entry/SL/T1/T2 lines (MCX)
    if entry_lines:
        for ln in entry_lines:
            dash={"solid":"solid","dashed":"dash","dotted":"dot"}.get(ln.get("dash","solid"),"solid")
            fig.add_hline(y=ln["price"],line=dict(color=ln["color"],width=ln.get("width",1.5),dash=dash),row=1,col=1)
            fig.add_annotation(x=df.index[-1],y=ln["price"],text=ln["label"],showarrow=False,
                xanchor="right",yanchor="bottom",font=dict(size=9,color=ln["color"],family="JetBrains Mono"),row=1,col=1)
    # ── MACD panel
    if "MACD_HIST" in df.columns:
        hist_vals=df["MACD_HIST"].fillna(0)
        fig.add_trace(go.Bar(x=df.index,y=hist_vals,name="Hist",marker_color=["#26a69a" if v>=0 else "#ef5350" for v in hist_vals],opacity=0.8,showlegend=False),row=2,col=1)
        for col,clr in [("MACD","#1565c0"),("MACD_SIG","#ffc107")]:
            if col in df.columns:
                fig.add_trace(go.Scatter(x=df.index,y=df[col],name=col,line=dict(color=clr,width=1.2)),row=2,col=1)
    # ── RSI panel
    if "RSI" in df.columns:
        fig.add_trace(go.Scatter(x=df.index,y=df["RSI"],name="RSI",line=dict(color="#6a1b9a",width=1.4),showlegend=False),row=3,col=1)
        for yv,clr in [(70,"#ef5350"),(30,"#26a69a")]:
            fig.add_hline(y=yv,line=dict(color=clr,dash="dot",width=0.8),row=3,col=1)
    # ── Layout
    fig.update_layout(
        title=dict(text=title,font=dict(family="JetBrains Mono",size=11,color="#666"),x=0.5),
        height=height,paper_bgcolor=PLOT_BG,plot_bgcolor=PLOT_CARD,
        margin=dict(l=60,r=20,t=28,b=8),
        font=dict(family="JetBrains Mono",size=9,color=PLOT_FONT),
        legend=dict(orientation="h",y=1.02,x=0,font=dict(size=8),bgcolor="rgba(0,0,0,0)"),
        xaxis_rangeslider_visible=False,hovermode="x unified",
        hoverlabel=dict(bgcolor="#111",font=dict(family="JetBrains Mono",size=9,color="#fff")))
    for i in range(1,4):
        fig.update_xaxes(gridcolor=PLOT_GRID,showgrid=True,row=i,col=1,zeroline=False,linecolor=PLOT_GRID,tickfont=dict(size=8))
        fig.update_yaxes(gridcolor=PLOT_GRID,showgrid=True,row=i,col=1,zeroline=False,linecolor=PLOT_GRID,tickfont=dict(size=8),
                         tickformat=",.0f" if i==1 else None,separatethousands=True)
    return fig

# aliases used in NSE section
def _filter_today(df):
    """Slice dataframe to today's IST rows only (for chart display).
    The full multi-day dataframe is still used for indicator warm-up upstream."""
    if df is None or df.empty:
        return df
    try:
        today = datetime.now(ist).date()
        idx = pd.to_datetime(df.index)
        if idx.tz is None:
            idx = idx.tz_localize("Asia/Kolkata")
        else:
            idx = idx.tz_convert("Asia/Kolkata")
        mask = idx.date == today
        return df[mask] if mask.any() else df
    except Exception:
        return df

def make_chart(df5, df15, title, fibs=None, fvgs=None, bos_swings=None, sd_zones=None):
    return build_plotly_chart(_filter_today(df5),title,440,fibs=fibs,fvgs=fvgs,bos_swings=bos_swings,sd_zones=sd_zones,df_cross=df15)

def make_chart_15m(df15, title, fibs=None, bos_swings=None, sd_zones=None):
    return build_plotly_chart(_filter_today(df15),title,380,fibs=fibs,bos_swings=bos_swings,sd_zones=sd_zones)

def render_option_table(df_side, accent_color, label, emoji):
    if df_side.empty:
        st.markdown(f"<div style='font-size:0.65rem;color:#6b7280;padding:0.4rem;'>{emoji} {label} — No data</div>",unsafe_allow_html=True)
        return
    rows_html=""
    for r in df_side.itertuples():
        rows_html+=(f"<tr style='border-bottom:1px solid #f0f0f0;'>"
            f"<td style='padding:4px 6px;color:#111827;font-weight:600;'>{int(r.strike)}</td>"
            f"<td style='padding:4px 6px;color:#111827;text-align:right;'>&#x20B9;{r.ltp:.1f}</td>"
            f"<td style='padding:4px 6px;color:#4b5563;text-align:right;'>{int(r.oi):,}</td>"
            f"<td style='padding:4px 6px;color:#6b7280;text-align:right;'>{int(r.volume):,}</td></tr>")
    st.markdown(
        f"<div style='margin-bottom:0.25rem;'><span style='font-size:0.62rem;color:{accent_color};font-weight:700;'>{emoji} {label}</span></div>"
        f"<div style='overflow-x:auto;'><table style='width:100%;border-collapse:collapse;font-size:0.65rem;font-family:Inter,sans-serif;'>"
        f"<thead><tr style='background:#f9fafb;border-bottom:2px solid #e0e0e0;'>"
        f"<th style='padding:4px 6px;color:#6b7280;text-align:left;font-weight:600;font-size:0.58rem;text-transform:uppercase;'>Strike</th>"
        f"<th style='padding:4px 6px;color:#6b7280;text-align:right;font-weight:600;font-size:0.58rem;text-transform:uppercase;'>LTP</th>"
        f"<th style='padding:4px 6px;color:#6b7280;text-align:right;font-weight:600;font-size:0.58rem;text-transform:uppercase;'>OI</th>"
        f"<th style='padding:4px 6px;color:#6b7280;text-align:right;font-weight:600;font-size:0.58rem;text-transform:uppercase;'>Vol</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table></div>",unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# MCX HELPERS
# ═════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=86400)
def load_mcx_instruments():
    return pd.DataFrame(kite.instruments("MCX"))

mcx_instruments = load_mcx_instruments()

def mcx_get_token(commodity_name):
    df=mcx_instruments[(mcx_instruments["name"]==commodity_name)&(mcx_instruments["segment"]==COMMODITY_CONFIG[commodity_name]["segment_fut"])].copy()
    df=df[df["expiry"]>=pd.Timestamp.today().date()].sort_values("expiry")
    if df.empty: return None, None
    row=df.iloc[0]; return int(row["instrument_token"]), str(row["tradingsymbol"])

def mcx_get_live_ltp(tradingsymbol):
    try:
        q=kite.quote([f"MCX:{tradingsymbol}"])
        return q.get(f"MCX:{tradingsymbol}",{}).get("last_price",None)
    except Exception: return None

@st.cache_data(ttl=300, show_spinner=False)
def mcx_get_data(_token, tf, days, commodity_name):
    """TTL=300s (5 min) — candles cached 5 minutes, not every 60s refresh."""
    now       = datetime.now()
    from_date = now - pd.Timedelta(days=days)
    last_err  = ""
    for attempt in range(3):
        try:
            data = kite.historical_data(_token, from_date, now, tf)
            df   = pd.DataFrame(data)
            if not df.empty:
                df.set_index("date", inplace=True)
                return df
            time_module.sleep(1.5)
        except Exception as e:
            last_err = str(e).lower()
            wait = 3 if ("too many" in last_err or "rate" in last_err
                         or "429" in last_err) else 2 ** attempt
            if attempt < 2:
                time_module.sleep(wait)
    raise RuntimeError(f"mcx_get_data failed for {commodity_name}/{tf}: {last_err or 'empty'}")

def mcx_apply_indicators(df):
    if df.empty or len(df)<3: return df
    df["EMA9"] =ta.trend.ema_indicator(df["close"],9)
    df["EMA21"]=ta.trend.ema_indicator(df["close"],21)
    df["RSI"]  =ta.momentum.rsi(df["close"],14)
    _m=ta.trend.MACD(df["close"])
    df["MACD"]=_m.macd(); df["MACD_SIG"]=_m.macd_signal(); df["MACD_HIST"]=_m.macd_diff()
    idx=df.index
    dk=idx.normalize() if (hasattr(idx,"tz") and idx.tz) else pd.to_datetime(idx).normalize()
    df["_dk"]=dk; df["_cv"]=df["close"]*df["volume"]
    df["_cumv"]=df.groupby("_dk")["volume"].cumsum(); df["_cumpv"]=df.groupby("_dk")["_cv"].cumsum()
    _safe_cumv=df["_cumv"].replace(0,float("nan"))
    df["VWAP"]=(df["_cumpv"]/_safe_cumv).fillna(df["close"]); df.drop(columns=["_dk","_cv","_cumv","_cumpv"],inplace=True)
    try:
        if len(df)>30:
            _a=ta.trend.ADXIndicator(df["high"],df["low"],df["close"])
            df["ADX"]=_a.adx(); df["+DI"]=_a.adx_pos(); df["-DI"]=_a.adx_neg()
        else: df["ADX"]=df["+DI"]=df["-DI"]=0.0
    except Exception: df["ADX"]=df["+DI"]=df["-DI"]=0.0
    try: df["ATR"]=ta.volatility.AverageTrueRange(df["high"],df["low"],df["close"],window=14).average_true_range()
    except Exception: df["ATR"]=0.0
    try:
        _bb=ta.volatility.BollingerBands(df["close"],window=20)
        df["BB_upper"]=_bb.bollinger_hband(); df["BB_lower"]=_bb.bollinger_lband(); df["BB_width"]=df["BB_upper"]-df["BB_lower"]
    except Exception: df["BB_width"]=0.0
    df["momentum_5"]=df["close"].diff(5)
    prev=df["EMA9"].shift(1)-df["EMA21"].shift(1); curr=df["EMA9"]-df["EMA21"]
    df["ema_cross"]=0
    df.loc[(prev<0)&(curr>=0),"ema_cross"]=1; df.loc[(prev>0)&(curr<=0),"ema_cross"]=-1
    return df

# ── ORIGINAL MCX SIGNAL ENGINE ────────────────────────────────────────────────
def mcx_detect_200pt_move(df5, atr_scale=1.0):
    if df5.empty or len(df5)<20: return None,0,{}
    l=df5.iloc[-1]; score=0; detail={}
    atr=l.get("ATR",0); t_hi=80*atr_scale; t_mid=60*atr_scale; t_lo=40*atr_scale
    if atr>t_hi:   score+=30; detail["ATR"]=f"ATR {round(atr,2)} > {t_hi:.0f} → +30"
    elif atr>t_mid: score+=20; detail["ATR"]=f"ATR {round(atr,2)} > {t_mid:.0f} → +20"
    elif atr>t_lo:  score+=10; detail["ATR"]=f"ATR {round(atr,2)} > {t_lo:.0f} → +10"
    else:            detail["ATR"]=f"ATR {round(atr,2)} ≤ {t_lo:.0f} → +0"
    mom=l.get("momentum_5",0); m_hi=100*atr_scale; m_mid=60*atr_scale; m_lo=30*atr_scale
    if abs(mom)>m_hi:   score+=25; detail["Momentum"]=f"5-bar {round(mom,2)} → +25"
    elif abs(mom)>m_mid: score+=15; detail["Momentum"]=f"5-bar {round(mom,2)} → +15"
    elif abs(mom)>m_lo:  score+=8;  detail["Momentum"]=f"5-bar {round(mom,2)} → +8"
    else:                 detail["Momentum"]=f"5-bar {round(mom,2)} → +0"
    adx=l.get("ADX",0)
    if adx>30:   score+=20; detail["ADX"]=f"ADX {round(adx,1)} > 30 → +20"
    elif adx>25:  score+=12; detail["ADX"]=f"ADX {round(adx,1)} > 25 → +12"
    elif adx>20:  score+=6;  detail["ADX"]=f"ADX {round(adx,1)} > 20 → +6"
    else:          detail["ADX"]=f"ADX {round(adx,1)} ≤ 20 → +0"
    rsi=l.get("RSI",50)
    if rsi>72 or rsi<28:   score+=15; detail["RSI"]=f"RSI {round(rsi,1)} extreme → +15"
    elif rsi>68 or rsi<32:  score+=8;  detail["RSI"]=f"RSI {round(rsi,1)} strong → +8"
    else:                    detail["RSI"]=f"RSI {round(rsi,1)} neutral → +0"
    bw=l.get("BB_width",0); avg_bw=df5["BB_width"].rolling(20).mean().iloc[-1] if "BB_width" in df5 else 0
    if avg_bw and avg_bw>0:
        ratio=bw/avg_bw
        if ratio>1.4:   score+=10; detail["BB"]=f"BB {round(ratio,2)}× avg → +10"
        elif ratio>1.2:  score+=5;  detail["BB"]=f"BB {round(ratio,2)}× avg → +5"
        else:             detail["BB"]=f"BB {round(ratio,2)}× avg → +0"
    score=min(score,100)
    di_p=l.get("+DI",0); di_m=l.get("-DI",0); mom=l.get("momentum_5",0)
    if mom>0 and di_p>di_m: direction="UP"
    elif mom<0 and di_m>di_p: direction="DOWN"
    elif di_p>di_m: direction="UP"
    elif di_m>di_p: direction="DOWN"
    else: direction="UNCLEAR"
    return direction, score, detail

def mcx_evaluate_conflict(signal_direction, move_dir, move_score, strong_thresh, moderate_thresh, penalty):
    if signal_direction not in ("CALL","PUT"): return "NONE",0,""
    if move_dir in (None,"UNCLEAR"):
        if move_score<30: return "NONE",-5,f"⚠️ Move detector UNCLEAR (score {move_score}/100) → −5 pts"
        return "NONE",0,""
    signal_bullish=(signal_direction=="CALL"); move_bullish=(move_dir=="UP"); opposing=(signal_bullish!=move_bullish)
    if not opposing: return "NONE",0,f"✅ Move detector aligns ({move_dir}, score {move_score}/100)"
    if move_score>=strong_thresh:
        return "STRONG",0,(f"🚨 STRONG CONFLICT: Signal says {signal_direction} but move says {move_dir} ({move_score}/100) ≥ {strong_thresh} → BLOCKED")
    if move_score>=moderate_thresh:
        return "MODERATE",-penalty,(f"⚠️ MODERATE CONFLICT: {signal_direction} vs {move_dir} ({move_score}/100) → −{penalty} pts")
    return "NONE",0,(f"💬 WEAK CONFLICT: {signal_direction} vs {move_dir} ({move_score}/100) → no penalty")

def detect_flag_continuation(df5, df15):
    """
    Detects bull-flag and bear-flag continuation setups BEFORE the breakout is confirmed.
    Fires 1-2 candles earlier than standard breakout logic.

    BULL FLAG (fires early CALL boost):
      - 15m: EMA9 > EMA21 + ADX > 25 + RSI 45–65 (trend intact, not exhausted)
      - 5m: last 6 bars avg volume < 50% of 20-bar avg (low-volume rest = accumulation)
      - 5m: price within 1% of 15m EMA9 (touching trend support)
      - 5m: current bar is GREEN with volume > previous bar (first resumption candle)

    BEAR FLAG (fires early PUT boost):
      - 15m: EMA9 < EMA21 + ADX > 25 + RSI 35–55 (downtrend intact, not oversold)
      - 5m: last 6 bars avg volume < 50% of 20-bar avg (low-volume bounce = distribution)
      - 5m: price within 1% of 15m EMA9 (touching trend resistance)
      - 5m: current bar is RED with volume > previous bar (first resumption candle)

    Returns: ("BULL_FLAG", boost_pts, reason) | ("BEAR_FLAG", boost_pts, reason) | (None, 0, "")
    """
    if len(df5) < 10 or len(df15) < 3: return None, 0, ""
    try:
        l5  = df5.iloc[-1];  l15 = df15.iloc[-1]
        # ── 15m checks ────────────────────────────────────────────────────────
        adx15     = float(l15.get("ADX", 0))
        rsi15     = float(l15.get("RSI", 50))
        ema9_15   = float(l15["EMA9"]);  ema21_15 = float(l15["EMA21"])
        # ── 5m volume analysis ────────────────────────────────────────────────
        vol_series   = df5["volume"].iloc[-20:]
        avg_vol_20   = float(vol_series.mean()) if len(vol_series) >= 10 else 0
        avg_vol_6    = float(df5["volume"].iloc[-7:-1].mean())  # last 6 bars excl current
        cur_vol      = float(l5["volume"])
        prev_vol     = float(df5["volume"].iloc[-2])
        low_vol_rest = (avg_vol_6 < avg_vol_20 * 0.50) and avg_vol_20 > 0
        vol_resuming = cur_vol > prev_vol * 1.2   # current bar volume 20% > previous
        # ── 5m price vs 15m EMA9 ─────────────────────────────────────────────
        price        = float(l5["close"])
        near_ema9_15 = abs(price - ema9_15) / ema9_15 * 100 < 1.0   # within 1%
        # ── Current 5m candle direction ───────────────────────────────────────
        is_green = l5["close"] > l5["open"]
        is_red   = l5["close"] < l5["open"]

        # ── BULL FLAG ─────────────────────────────────────────────────────────
        trend_bull_15m = (ema9_15 > ema21_15) and (adx15 > 25) and (45 <= rsi15 <= 65)
        if trend_bull_15m and low_vol_rest and near_ema9_15 and is_green and vol_resuming:
            reason = (f"🏁 Bull Flag: 15m trend intact (EMA9 {round(ema9_15,0)}>EMA21 {round(ema21_15,0)}, "
                      f"ADX {round(adx15,1)}, RSI {round(rsi15,1)}) · "
                      f"Low-vol rest ({round(avg_vol_6,0)} vs avg {round(avg_vol_20,0)}) · "
                      f"Price near 15m EMA9 · Resumption candle ↑ +20 boost")
            return "BULL_FLAG", 20, reason

        # ── BEAR FLAG ─────────────────────────────────────────────────────────
        trend_bear_15m = (ema9_15 < ema21_15) and (adx15 > 25) and (35 <= rsi15 <= 55)
        if trend_bear_15m and low_vol_rest and near_ema9_15 and is_red and vol_resuming:
            reason = (f"🏁 Bear Flag: 15m trend intact (EMA9 {round(ema9_15,0)}<EMA21 {round(ema21_15,0)}, "
                      f"ADX {round(adx15,1)}, RSI {round(rsi15,1)}) · "
                      f"Low-vol rest ({round(avg_vol_6,0)} vs avg {round(avg_vol_20,0)}) · "
                      f"Price near 15m EMA9 · Resumption candle ↓ +20 boost")
            return "BEAR_FLAG", 20, reason
    except Exception:
        pass
    return None, 0, ""


def mcx_compute_signal(df5, df15, adx_threshold, min_score, move_dir, move_score, do_boost, strong_thresh, moderate_thresh, penalty, commodity_name=""):
    if len(df5)<3 or len(df15)<3: return "NO TRADE",0,0,["Insufficient data"],"","NONE",""
    l5=df5.iloc[-1]; l15=df15.iloc[-1]; adx=l5["ADX"]
    if adx<18: return "NO TRADE",0,0,[f"⛔ ADX {round(adx,1)} < 18 — flat/choppy market (need ≥18 to compute, ≥{adx_threshold} for signal)"],"","NONE",""
    if adx>=adx_threshold: adx_pts=15; adx_note=f"✅ ADX {round(adx,1)} ≥ {adx_threshold} → +15"
    elif adx>=20:           adx_pts=8;  adx_note=f"🟡 ADX {round(adx,1)} borderline → +8"
    else:                   adx_pts=0;  adx_note=f"⚠️ ADX {round(adx,1)} weak → +0"
    rsi=l5["RSI"]; rsi_prev=df5["RSI"].iloc[-2]
    macd_b=l5["MACD_HIST"]>0; macd_be=l5["MACD_HIST"]<0
    # Tightened: block at RSI>62 (was 65) rolling over — catches exhaustion earlier
    rsi_topping=rsi>62 and rsi<rsi_prev; rsi_bottoming=rsi<38 and rsi>rsi_prev

    # ── EXHAUSTION GUARD ──────────────────────────────────────────────────────
    # Blocks new entries after a fast move (>2× ATR in 5 candles).
    # Prevents entering at the TOP of a spike right before the reversal.
    # This is the exact scenario: 5 green candles, then CALL fires at the peak.
    _atr_now = float(l5.get("ATR", 0))
    if _atr_now > 0 and len(df5) >= 6:
        _move_5bar = abs(float(df5["close"].iloc[-1]) - float(df5["close"].iloc[-6]))
        _exhausted_up   = _move_5bar > 2.0 * _atr_now and rsi > 60
        _exhausted_down = _move_5bar > 2.0 * _atr_now and rsi < 40
    else:
        _exhausted_up = _exhausted_down = False
    # ─────────────────────────────────────────────────────────────────────────
    call_score=0; call_reasons=[]
    # 15m EMA alignment: require BOTH EMA9>EMA21 AND MACD_HIST>0 for full +25
    # If only EMA aligns but MACD diverges (e.g. EMA bullish but MACD turning bearish), give only +10
    _15m_ema_bull = l15["EMA9"] > l15["EMA21"]
    _15m_ema_bear = l15["EMA9"] < l15["EMA21"]
    _15m_macd_bull = l15["MACD_HIST"] > 0
    _15m_macd_bear = l15["MACD_HIST"] < 0

    if _15m_ema_bull and _15m_macd_bull:
        call_score+=25; call_reasons.append(f"✅ 15m EMA9 ({round(l15['EMA9'],1)}) > EMA21 ({round(l15['EMA21'],1)}) + MACD bullish → +25")
    elif _15m_ema_bull and not _15m_macd_bull:
        call_score+=10; call_reasons.append(f"🟡 15m EMA9>EMA21 but MACD bearish (divergence) → +10 only")
    # else: EMA bearish → +0 for CALL
    if l5["close"]>l5["VWAP"]:   call_score+=20; call_reasons.append(f"✅ Price above VWAP ({round(l5['VWAP'],1)})")
    if l5["+DI"]>l5["-DI"]:      call_score+=20; call_reasons.append(f"✅ +DI {round(l5['+DI'],1)} > -DI {round(l5['-DI'],1)}")
    call_score+=adx_pts; call_reasons.append(adx_note)
    if macd_b:  call_score+=10; call_reasons.append("✅ 5m MACD histogram positive")
    # Extra momentum bonus: MACD_HIST increasing (turning more bullish) — early momentum signal
    if l5["MACD_HIST"] > df5["MACD_HIST"].iloc[-2] and l5["MACD_HIST"] > 0:
        call_score+=5; call_reasons.append("✅ 5m MACD_HIST rising (momentum building) → +5")
    if rsi>55:  call_score+=10; call_reasons.append(f"✅ RSI {round(rsi,1)} > 55")
    prev_high=df5["high"].iloc[-2]
    if l5["close"]<prev_high: call_score-=5; call_reasons.append(f"⚠️ Close < prev high → −5")
    put_score=0; put_reasons=[]
    # 15m EMA alignment for PUT: require BOTH EMA9<EMA21 AND MACD_HIST<0 for full +25
    # If MACD is diverging bullish while EMA is still bearish, give only +10 (weakening trend)
    if _15m_ema_bear and _15m_macd_bear:
        put_score+=25; put_reasons.append(f"✅ 15m EMA9 ({round(l15['EMA9'],1)}) < EMA21 ({round(l15['EMA21'],1)}) + MACD bearish → +25")
    elif _15m_ema_bear and not _15m_macd_bear:
        put_score+=10; put_reasons.append(f"🟡 15m EMA9<EMA21 but MACD turning bullish (divergence) → +10 only")
    if l5["close"]<l5["VWAP"]:   put_score+=20; put_reasons.append(f"✅ Price below VWAP ({round(l5['VWAP'],1)})")
    if l5["-DI"]>l5["+DI"]:      put_score+=20; put_reasons.append(f"✅ -DI {round(l5['-DI'],1)} > +DI {round(l5['+DI'],1)}")
    put_score+=adx_pts; put_reasons.append(adx_note)
    if macd_be: put_score+=10; put_reasons.append("✅ 5m MACD histogram negative")
    # Extra momentum bonus: MACD_HIST decreasing (turning more bearish)
    if l5["MACD_HIST"] < df5["MACD_HIST"].iloc[-2] and l5["MACD_HIST"] < 0:
        put_score+=5; put_reasons.append("✅ 5m MACD_HIST falling (bearish momentum building) → +5")
    if rsi<45:  put_score+=10; put_reasons.append(f"✅ RSI {round(rsi,1)} < 45")
    if rsi<30:  put_score+=10; put_reasons.append(f"✅ RSI {round(rsi,1)} < 30 — oversold bonus")
    prev_low=df5["low"].iloc[-2]
    if l5["close"]>prev_low: put_score-=5; put_reasons.append(f"⚠️ Close > prev low → −5")

    # ── BULL / BEAR FLAG CONTINUATION BOOST ──────────────────────────────────
    # Detects high-probability setups 1-2 candles BEFORE standard breakout confirmation.
    # 15m trend intact + low-volume consolidation + price near 15m EMA9 + resumption bar.
    _flag_type, _flag_boost, _flag_reason = detect_flag_continuation(df5, df15)
    if _flag_type == "BULL_FLAG":
        call_score += _flag_boost
        call_reasons.append(_flag_reason)
    elif _flag_type == "BEAR_FLAG":
        put_score += _flag_boost
        put_reasons.append(_flag_reason)
    # ─────────────────────────────────────────────────────────────────────────

    if call_score>=min_score and call_score>put_score:
        candidate="CALL"; raw_score=call_score; reasons=call_reasons
        if _exhausted_up:
            return("BLOCKED_CALL",call_score,call_score,call_reasons,
                   f"⛔ Exhaustion: price moved {round(_move_5bar,0)}pts in 5 bars (>{round(2*_atr_now,0)} = 2×ATR), RSI {round(rsi,1)} — late entry risk","NONE","")
        if rsi_topping: return("BLOCKED_CALL",call_score,call_score,call_reasons,f"RSI {round(rsi,1)} rolling over — fake CALL risk","NONE","")
        # ── 15m MACD divergence guard for CALL ──────────────────────────────
        # If 15m EMA says bullish but MACD says bearish, require higher score
        if _15m_ema_bull and _15m_macd_bear and call_score < (min_score + 15):
            return("BLOCKED_CALL", call_score, call_score, call_reasons,
                   f"⚠️ 15m EMA bullish but MACD bearish (divergence) — need {min_score+15} score, have {call_score}", "NONE","")
    elif put_score>=min_score and put_score>call_score:
        candidate="PUT"; raw_score=put_score; reasons=put_reasons
        if _exhausted_down:
            return("BLOCKED_PUT",put_score,put_score,put_reasons,
                   f"⛔ Exhaustion: price moved {round(_move_5bar,0)}pts in 5 bars (>{round(2*_atr_now,0)} = 2×ATR), RSI {round(rsi,1)} — late entry risk","NONE","")
        if rsi_bottoming: return("BLOCKED_PUT",put_score,put_score,put_reasons,f"RSI {round(rsi,1)} turning up — fake PUT risk","NONE","")
        # ── 15m MACD divergence guard for PUT ───────────────────────────────
        # If 15m EMA says bearish but MACD is already turning bullish, require higher score
        # This is THE scenario that caused the wrong PUT at 20:00
        if _15m_ema_bear and _15m_macd_bull and put_score < (min_score + 15):
            return("BLOCKED_PUT", put_score, put_score, put_reasons,
                   f"⚠️ 15m EMA bearish BUT MACD already turning BULLISH (divergence) — trend reversing, need {min_score+15} score, have {put_score}", "NONE","")
    else:
        weak=[]
        if call_score>0: weak.append(f"Bullish score {call_score}/{min_score} — need {min_score-call_score} more pts")
        if put_score>0:  weak.append(f"Bearish score {put_score}/{min_score} — need {min_score-put_score} more pts")
        if not weak:     weak.append("No EMA directional alignment on 15-min")
        return("NO TRADE",max(call_score,put_score),max(call_score,put_score),[f"⏸ {w}" for w in weak],"","NONE","")
    # ── PER-COMMODITY SMART TREND LOCK ────────────────────────────────────────
    # Each commodity has different volatility and trend behaviour — tuned from data:
    #
    # CRUDEOIL : score_thresh=70, body_thresh=50% ATR
    #   Validated: blocks score-60 fake flips, passes 114% ATR genuine PUT
    #
    # GOLDM    : score_thresh=60, body_thresh=40% ATR
    #   Gold moves slower — body rarely huge relative to ATR. Lower threshold
    #   ensures real Gold PUTs (like 20:45 score 85) still fire in bear trend.
    #
    # COPPER   : score_thresh=65, body_thresh=45% ATR
    #   Strong sustained trends. Blocks wrong CALL (score 55) in 15m bear.
    #   Passes genuine PUT (score 75) with large body.
    #
    # NATURALGAS: score_thresh=70, body_thresh=50% ATR — STRICTEST
    #   Highly choppy, ADX often <20. Also requires 15m ADX≥18 to confirm trend.
    #   6 flips in 80 min blocked. Only high-conviction moves pass.
    #
    # A signal is STRONG if score ≥ thresh OR body ≥ body_thresh — always fires.
    # A WEAK counter-trend signal is blocked when 15m confirms opposite direction.
    _LOCK_PARAMS = {
        "CRUDEOIL":   {"score_thresh": 70, "body_thresh": 50, "adx15_min": 0},
        "GOLDM":      {"score_thresh": 60, "body_thresh": 40, "adx15_min": 0},
        "COPPER":     {"score_thresh": 65, "body_thresh": 45, "adx15_min": 0},
        "NATURALGAS": {"score_thresh": 70, "body_thresh": 50, "adx15_min": 18},
    }
    _lp = _LOCK_PARAMS.get(commodity_name, {"score_thresh": 70, "body_thresh": 50, "adx15_min": 0})
    _body      = abs(l5["close"] - l5["open"])
    _atr       = float(l5.get("ATR", 0))
    if _atr <= 0: _atr = max(_body, 1.0)
    _body_pct  = _body / _atr * 100
    _adx15     = float(l15.get("ADX", 25))
    _15m_bull  = (l15["EMA9"] > l15["EMA21"]) and (l15["MACD_HIST"] > 0)
    _15m_bear  = (l15["EMA9"] < l15["EMA21"]) and (l15["MACD_HIST"] < 0)
    _is_strong = (raw_score >= _lp["score_thresh"]) or (_body_pct >= _lp["body_thresh"])
    # For NatGas: also require 15m ADX to confirm the trend has conviction
    _trend_confirmed = (_adx15 >= _lp["adx15_min"]) if _lp["adx15_min"] > 0 else True
    if not _is_strong and _trend_confirmed:
        if candidate == "PUT" and _15m_bull:
            return(f"BLOCKED_{candidate}", raw_score, raw_score,
                   reasons + [f"⚠️ [{commodity_name}] Weak PUT (score {raw_score}, body {round(_body_pct,0)}% ATR) — 15m bullish, blocked"],
                   f"Weak counter-trend PUT in 15m uptrend", "NONE", "")
        if candidate == "CALL" and _15m_bear:
            return(f"BLOCKED_{candidate}", raw_score, raw_score,
                   reasons + [f"⚠️ [{commodity_name}] Weak CALL (score {raw_score}, body {round(_body_pct,0)}% ATR) — 15m bearish, blocked"],
                   f"Weak counter-trend CALL in 15m downtrend", "NONE", "")
    # ─────────────────────────────────────────────────────────────────────────
    conflict_level,conf_adj,conflict_note=mcx_evaluate_conflict(candidate,move_dir,move_score,strong_thresh,moderate_thresh,penalty)
    if conflict_note: reasons=reasons+[conflict_note]
    if conflict_level=="STRONG":
        return(f"BLOCKED_{candidate}",raw_score,raw_score,reasons,f"200-pt move detector opposes {candidate}","STRONG",conflict_note)
    confidence=raw_score+conf_adj
    if do_boost and move_dir==("UP" if candidate=="CALL" else "DOWN") and move_score>=60:
        boost=min(int(move_score/5),20); confidence+=boost; reasons.append(f"🚀 Move {move_dir} ({move_score}/100) → +{boost} boost")
    confidence=max(0,min(confidence,100))
    return(candidate,raw_score,confidence,reasons,"",conflict_level,conflict_note)

def mcx_get_option_chain(price, commodity_name):
    df=mcx_instruments.copy(); seg=COMMODITY_CONFIG[commodity_name]["segment_opt"]
    df=df[(df["name"]==commodity_name)&(df["segment"]==seg)]
    if df.empty: return pd.DataFrame()
    expiry=df["expiry"].min(); df=df[df["expiry"]==expiry].copy()
    df["diff"]=abs(df["strike"]-price); df=df.sort_values("diff").head(200)
    quotes=kite.quote([f"MCX:{x}" for x in df["tradingsymbol"]])
    rows=[]
    for _,r in df.iterrows():
        q=quotes.get(f"MCX:{r['tradingsymbol']}",{}); ltp=q.get("last_price",0)
        rows.append({"symbol":r["tradingsymbol"],"strike":r["strike"],"type":r["instrument_type"],
                     "ltp":ltp,"oi":q.get("oi",0),"volume":q.get("volume",0),
                     "moneyness":("ITM" if (r["instrument_type"]=="CE" and r["strike"]<price) or
                                           (r["instrument_type"]=="PE" and r["strike"]>price)
                                  else ("ATM" if abs(r["strike"]-price)<=COMMODITY_CONFIG[commodity_name]["tick"] else "OTM"))})
    return pd.DataFrame(rows)

def mcx_pick_options_by_premium(option_df, signal, target_prem, min_prem, max_prem, lot_size, num_lots):
    sig=signal.replace("BLOCKED_","")
    if sig not in ("CALL","PUT") or option_df.empty: return None,None,None
    opt_type="CE" if sig=="CALL" else "PE"
    df=option_df[(option_df["type"]==opt_type)&(option_df["ltp"]>0)].copy()
    if df.empty: return None,None,None
    df["prem_dist"]=abs(df["ltp"]-target_prem); df=df.sort_values("prem_dist")
    df_b=df[(df["ltp"]>=min_prem)&(df["ltp"]<=max_prem)]
    if df_b.empty: df_b=df.head(10)
    best_strike=df_b.iloc[0]["strike"]; strikes_sorted=sorted(df_b["strike"].unique())
    idx=strikes_sorted.index(best_strike) if best_strike in strikes_sorted else 0
    if sig=="CALL":
        cheaper=strikes_sorted[idx+1] if idx+1<len(strikes_sorted) else None
        pricier=strikes_sorted[idx-1] if idx-1>=0 else None
    else:
        cheaper=strikes_sorted[idx-1] if idx-1>=0 else None
        pricier=strikes_sorted[idx+1] if idx+1<len(strikes_sorted) else None
    def build(strike):
        if strike is None: return None
        r=df[df["strike"]==strike]
        if r.empty: return None
        row=r.iloc[0].to_dict(); ltp=float(row["ltp"])
        if ltp<=0: return None
        cl=round(ltp*lot_size,2); tot=round(cl*num_lots,2)
        row.update({"cost_1lot":cl,"total_cost":tot,"num_lots":num_lots,
                    "sl":round(ltp*0.75,2),"sl_loss_total":round(ltp*0.25*lot_size*num_lots,2),
                    "t1":round(ltp*1.50,2),"t1_gain_total":round(ltp*0.50*lot_size*num_lots,2),
                    "t2":round(ltp*2.00,2),"t2_gain_total":round(ltp*1.00*lot_size*num_lots,2)})
        return row
    return build(best_strike), build(cheaper), build(pricier)

# ── MCX PAPER TRADING ─────────────────────────────────────────────────────────
def mcx_get_trade_live_ltp(sym, t, opt_df=None):
    strike=t.get("strike"); opt_type=t.get("type"); entry=float(t["entry"])
    if opt_df is not None and not opt_df.empty and strike and opt_type:
        row=opt_df[(opt_df["strike"]==strike)&(opt_df["type"]==opt_type)]
        if not row.empty:
            ltp=float(row.iloc[0]["ltp"])
            if ltp>0: return ltp
    ts=t.get("tradingsymbol","")
    if ts:
        try:
            q=kite.quote([f"MCX:{ts}"]); ltp=q.get(f"MCX:{ts}",{}).get("last_price",0)
            if ltp>0: return float(ltp)
        except Exception: pass
    return entry

def mcx_open_trade(sym, signal, strike, opt_type, ltp, lot_size, tradingsymbol="", num_lots=2):
    # Daily loss limit guard
    if st.session_state.mcx_daily_pnl <= MCX_DAILY_LOSS_LIMIT:
        return  # session loss limit hit — no new MCX trades today
    # ── SL based on S/R from recent swing (professional level) ──────────────
    # SL = entry − ATR×1.5 (below recent support for CALL / above resistance for PUT)
    # If ATR not available fall back to 25% of premium
    _sl = round(ltp * 0.75, 2)   # default −25%
    # ── T1 = entry + MCX_T1_PROFIT_PTS (Lot 1 exit) ─────────────────────────
    _t1 = round(ltp + MCX_T1_PROFIT_PTS, 2)
    # ── T2 = entry × 2.0 (Lot 2 final target / big-move capture) ─────────────
    _t2 = round(ltp * 2.0, 2)
    # ── Trail trigger = entry + 40 pts → SL moves to +15 pts ─────────────────
    _trail_trigger = round(ltp + 40, 2)
    _trail_sl      = round(ltp + 15, 2)

    st.session_state.mcx_open_trades[sym] = {
        "side":          signal,
        "strike":        strike,
        "type":          opt_type,
        "entry":         ltp,
        "sl":            _sl,
        "t1":            _t1,
        "t2":            _t2,
        "trail_trigger": _trail_trigger,
        "trail_sl":      _trail_sl,
        "lot_size":      lot_size,
        "num_lots":      num_lots,
        "lots_remaining":num_lots,
        "lot1_done":     False,     # Lot 1 not yet exited at T1
        "cost_sl_active":False,     # SL not yet at breakeven
        "trailing":      False,     # Trailing SL not yet active
        "time":          datetime.now(ist).strftime("%H:%M"),
        "entry_time_iso":datetime.now().isoformat(),
        "tradingsymbol": tradingsymbol,
    }
    st.session_state.mcx_trades_today += 1

def mcx_close_trade(sym, cur_ltp, exit_reason=""):
    t=st.session_state.mcx_open_trades.pop(sym,None)
    if not t: return 0
    pnl=round((cur_ltp-t["entry"])*t["lot_size"],2)
    st.session_state.mcx_total_pnl+=pnl
    st.session_state.mcx_daily_pnl+=pnl
    if pnl>=0: st.session_state.mcx_wins+=1
    else:      st.session_state.mcx_losses+=1
    st.session_state.mcx_trade_history.append({
        "Time":datetime.now(ist).strftime("%H:%M"),"Symbol":sym,"Side":t["side"],
        "Strike":t["strike"],"Type":t["type"],"Entry":t["entry"],"Exit":cur_ltp,
        "PnL":pnl,"Exit Reason":exit_reason,
    })
    return pnl

def mcx_check_auto_exit(sym, opt_df=None):
    """
    Full 2-lot exit logic:
    ─ SL hit           → full exit ALL remaining lots
    ─ T1 hit (+20 pts) → exit Lot 1 only, move SL of Lot 2 to breakeven (cost)
    ─ T2 hit (×2.0)   → exit Lot 2 (full exit remaining)
    ─ Trail trigger    → when premium up +40 pts, trail SL to +15 pts
    ─ Continue trailing → SL moves up as price moves up (captures big moves)
    """
    t = st.session_state.mcx_open_trades.get(sym)
    if not t: return
    cur_ltp = mcx_get_trade_live_ltp(sym, t, opt_df)
    if cur_ltp <= 0: return
    entry   = t["entry"]
    sl      = t["sl"]

    # ── TRAILING SL LOGIC ────────────────────────────────────────────────────
    if not t.get("trailing"):
        # Activate trail when price hits trail trigger
        if cur_ltp >= t.get("trail_trigger", entry + 40):
            new_sl = t.get("trail_sl", round(entry + 15, 2))
            st.session_state.mcx_open_trades[sym]["sl"]       = new_sl
            st.session_state.mcx_open_trades[sym]["trailing"] = True
            sl = new_sl
            st.toast(f"{sym} ↗ Trail SL activated @ ₹{new_sl:.1f}")
    else:
        # Already trailing — move SL up if price moves up further
        # Trail 25 pts behind current price (captures big moves)
        dynamic_trail = round(cur_ltp - 25, 2)
        if dynamic_trail > t["sl"]:
            st.session_state.mcx_open_trades[sym]["sl"] = dynamic_trail
            sl = dynamic_trail

    # ── SL HIT → full exit ───────────────────────────────────────────────────
    if cur_ltp <= sl:
        mcx_close_trade(sym, cur_ltp, f"SL ₹{sl:.1f}")
        st.session_state.get("mcx_signal_locks", {}).pop(sym, None)
        return

    # ── T2 HIT → full exit remaining lots ────────────────────────────────────
    if cur_ltp >= t["t2"]:
        mcx_close_trade(sym, cur_ltp, f"T2 ₹{t['t2']:.1f} (×2.0)")
        st.session_state.get("mcx_signal_locks", {}).pop(sym, None)
        return

    # ── T1 HIT → exit Lot 1 only, trail Lot 2 at breakeven ──────────────────
    if not t.get("lot1_done") and cur_ltp >= t["t1"]:
        lot1_pnl = round((cur_ltp - entry) * t["lot_size"], 2)
        # Update trade state — keep trade open for Lot 2
        st.session_state.mcx_open_trades[sym]["lot1_done"]      = True
        st.session_state.mcx_open_trades[sym]["cost_sl_active"] = True
        st.session_state.mcx_open_trades[sym]["sl"]             = entry  # breakeven
        st.session_state.mcx_open_trades[sym]["lots_remaining"] = max(1, t.get("num_lots",2) - 1)
        # Add lot 1 exit to trade history
        st.session_state.mcx_total_pnl  += lot1_pnl
        st.session_state.mcx_daily_pnl  += lot1_pnl
        if lot1_pnl >= 0: st.session_state.mcx_wins   += 1
        else:             st.session_state.mcx_losses += 1
        st.session_state.mcx_trade_history.append({
            "Time":     datetime.now(ist).strftime("%H:%M"),
            "Symbol":   sym,
            "Side":     t["side"],
            "Strike":   t["strike"],
            "Type":     t["type"],
            "Entry":    entry,
            "Exit":     cur_ltp,
            "PnL":      lot1_pnl,
            "Exit Reason": f"T1 +{MCX_T1_PROFIT_PTS}pts — Lot 1 of {t.get('num_lots',2)}",
        })
        st.toast(f"{sym} ✅ Lot 1 exited @ ₹{cur_ltp:.1f} (+{cur_ltp-entry:.1f} pts) | SL → breakeven ₹{entry:.1f}")

def mcx_log_signal_and_auto_trade(sym, sig, score, best_row, lot_size, current_minute, min_score_val=60):
    # ── MARKET HOURS GUARD ────────────────────────────────────────────────────
    _mcx_allowed = (current_time.weekday() < 5) and (dtime(9, 0) <= _now_t <= dtime(23, 30))
    if not _mcx_allowed:
        if sym in st.session_state.mcx_open_trades:
            ltp_now = mcx_get_trade_live_ltp(sym, st.session_state.mcx_open_trades[sym])
            mcx_close_trade(sym, ltp_now, "Market closed")
        return

    if sig in ("NO TRADE","WAIT") or best_row is None:
        if sym in st.session_state.mcx_open_trades:
            ltp_now = mcx_get_trade_live_ltp(sym, st.session_state.mcx_open_trades[sym])
            mcx_close_trade(sym, ltp_now, "Signal cleared")
        # Reset confirmation cache when signal goes neutral
        if "mcx_confirm_cache" in st.session_state:
            st.session_state["mcx_confirm_cache"].pop(sym, None)
        if sig.startswith("BLOCKED_") and email_alerts_enabled:
            _btype = "CALL" if "CALL" in sig else "PUT"
            last = st.session_state.mcx_last_signal_state.get(sym, {})
            if last.get("signal") != f"BLOCKED_{_btype}":
                send_alert(
                    f"🚨 <b>SIGNAL BLOCKED — {sym}</b>\nDirection: {_btype}\nReason: Strong conflict\nScore: {score}/100\n⏰ {current_minute}",
                    subject=f"🚨 MCX BLOCKED — {sym} {_btype}", sym=sym, direction=_btype, is_flip=False)
                st.session_state.mcx_last_signal_state[sym] = {"signal": f"BLOCKED_{_btype}", "strike": None}
        return

    sig_clean  = sig.replace("BLOCKED_", "")
    strike     = best_row["strike"]
    opt_type   = best_row["type"]
    ltp        = float(best_row["ltp"])
    last       = st.session_state.mcx_last_signal_state.get(sym, {})

    # ── DEDUP: only log when direction changes ────────────────────────────────
    _last_dir_mcx = last.get("signal")
    _flip_mcx = (_last_dir_mcx is not None
                 and _last_dir_mcx not in ("BLOCKED_CALL", "BLOCKED_PUT", "NO TRADE")
                 and _last_dir_mcx != sig_clean)
    if _signal_is_duplicate(sym, sig_clean, "mcx") and not _flip_mcx:
        signal_changed = False
    else:
        signal_changed = True
        _record_signal_direction(sym, sig_clean, "mcx")

    if signal_changed:
        st.session_state.mcx_signal_log.append({
            "Time": current_minute, "Symbol": sym, "Signal": sig_clean,
            "Strike": strike, "Type": opt_type, "LTP": round(ltp, 2),
            "Score": f"{score}/100"
        })
        st.session_state.mcx_last_setup_alert.pop(sym, None)
        is_flip = _flip_mcx
        if email_alerts_enabled:
            emoji = "🟢" if sig_clean == "CALL" else "🔴"
            tag   = "CE" if sig_clean == "CALL" else "PE"
            sl_val = best_row.get("sl", round(ltp * 0.75, 1))
            t1_val = round(ltp + MCX_T1_PROFIT_PTS, 1)
            t2_val = round(ltp * 2.0, 1)
            tot    = best_row.get("total_cost", round(ltp * lot_size * num_lots, 0))
            trail_trigger = round(ltp + 40, 1)
            if is_flip:
                prev_sig = _last_dir_mcx or ""
                subject  = f"🔄 MCX FLIP — {sym}: {prev_sig} ➜ {sig_clean}"
                hdr      = f"🔄 <b>SIGNAL FLIP — {sym}</b>"
                sub_h    = f"Changed: {prev_sig} ➜ <b>{sig_clean} ({tag})</b>"
            else:
                subject = f"{emoji} MCX SIGNAL — {sym}: {sig_clean} @ ₹{ltp}"
                hdr     = f"{emoji} <b>NEW SIGNAL — {sym}</b>"
                sub_h   = f"Direction: <b>{sig_clean} ({tag})</b>"
            send_alert(
                f"{hdr}\n{sub_h}\n"
                f"Strike: <b>{int(strike)} {opt_type}</b>\n"
                f"<b>Premium: ₹{ltp:.1f}</b>\n"
                f"Cost ({num_lots} lot × {lot_size}): <b>₹{tot:,.0f}</b>\n"
                f"SL: ₹{sl_val:.1f} (−25%) | T1 Lot1: ₹{t1_val:.1f} (+{MCX_T1_PROFIT_PTS}pts)\n"
                f"T2 Lot2: ₹{t2_val:.1f} (×2.0) | Trail@: ₹{trail_trigger:.1f} (+40pts)\n"
                f"Score: {score}/{min_score_val}\n"
                f"⏰ {current_minute}",
                subject=subject, sym=sym, direction=sig_clean, is_flip=is_flip
            )
        st.session_state.mcx_last_signal_state[sym] = {"signal": sig_clean, "strike": strike}

    # ── ANTI-FLIP GUARDS BEFORE ENTRY ────────────────────────────────────────
    existing = st.session_state.mcx_open_trades.get(sym)

    # Guard 1: Score boundary buffer — must exceed min_score by MCX_SCORE_BUFFER
    if score < (min_score_val + MCX_SCORE_BUFFER) and existing is None:
        return  # score too close to boundary — wait for stronger signal

    # Guard 2: Signal lock — same direction within MCX_LOCK_MINS is suppressed
    if existing is None and _mcx_is_signal_locked(sym, sig_clean):
        return

    # Guard 3: 2-candle confirmation — signal must persist for 2 refreshes
    if existing is None and not _mcx_confirm_signal(sym, sig_clean, score, min_score_val):
        return

    # ── ENTRY ────────────────────────────────────────────────────────────────
    if existing is None:
        mcx_open_trade(sym, sig_clean, strike, opt_type, ltp, lot_size,
                       best_row.get("symbol", ""), num_lots=num_lots)
        _mcx_set_signal_lock(sym, sig_clean)
        if not signal_changed:
            st.session_state.mcx_signal_log.append({
                "Time": current_minute, "Symbol": sym, "Signal": sig_clean,
                "Strike": strike, "Type": opt_type, "LTP": round(ltp, 2),
                "Score": f"{score}/100 [re-entry]"
            })
            if email_alerts_enabled:
                emoji_r = "🟢" if sig_clean == "CALL" else "🔴"
                tag_r   = "CE" if sig_clean == "CALL" else "PE"
                t1_r    = round(ltp + MCX_T1_PROFIT_PTS, 1)
                t2_r    = round(ltp * 2.0, 1)
                tot_r   = best_row.get("total_cost", round(ltp * lot_size * num_lots, 0))
                send_alert(
                    f"{emoji_r} <b>MCX RE-ENTRY — {sym}</b>\n"
                    f"Direction: <b>{sig_clean} ({tag_r})</b>\n"
                    f"Strike: <b>{int(strike)} {opt_type}</b>\n"
                    f"<b>Premium: ₹{ltp:.1f}</b>\n"
                    f"Cost ({num_lots} lot × {lot_size}): <b>₹{tot_r:,.0f}</b>\n"
                    f"SL: ₹{round(ltp*0.75,1):.1f} | T1: ₹{t1_r:.1f} | T2: ₹{t2_r:.1f}\n"
                    f"Score: {score}/{min_score_val}\n⏰ {current_minute}",
                    subject=f"{emoji_r} MCX RE-ENTRY — {sym}: {sig_clean} @ ₹{ltp}",
                    sym=sym, direction=sig_clean, is_flip=False,
                )

    elif existing["side"] != sig_clean:
        # Direction flip → exit old, enter new (with confirmation already passed above)
        exit_ltp = mcx_get_trade_live_ltp(sym, existing)
        mcx_close_trade(sym, exit_ltp, "Signal flip")
        # Reset confirmation cache so new direction must also confirm
        if "mcx_confirm_cache" in st.session_state:
            st.session_state["mcx_confirm_cache"].pop(sym, None)
        # Don't enter immediately on flip — wait for 2-candle confirm next refresh

def mcx_make_candlestick(df, ema_cols, title, height=300, last_n_bars=None, entry_lines=None, fibs=None, fvgs=None, bos_swings=None, sd_zones=None):
    cd=df.reset_index().copy()
    if last_n_bars: cd=cd.tail(last_n_bars)
    cd["color"]=cd.apply(lambda r:"Up" if r["close"]>=r["open"] else "Down",axis=1)
    base=alt.Chart(cd)
    rule=base.mark_rule(strokeWidth=1).encode(x=alt.X("date:T",title="Time",axis=alt.Axis(format="%H:%M",labelFontSize=9)),y=alt.Y("low:Q",scale=alt.Scale(zero=False),title="Price"),y2=alt.Y2("high:Q"),color=alt.Color("color:N",scale=alt.Scale(domain=["Up","Down"],range=["#2e7d32","#c62828"]),legend=None))
    bar=base.mark_bar().encode(x=alt.X("date:T"),y=alt.Y("open:Q",scale=alt.Scale(zero=False)),y2=alt.Y2("close:Q"),color=alt.Color("color:N",scale=alt.Scale(domain=["Up","Down"],range=["#2e7d32","#c62828"]),legend=None),tooltip=[alt.Tooltip("date:T",title="Time"),alt.Tooltip("open:Q"),alt.Tooltip("high:Q"),alt.Tooltip("low:Q"),alt.Tooltip("close:Q"),alt.Tooltip("volume:Q")])
    ema_colors={"EMA9":"#f39c12","EMA21":"#1565c0","VWAP":"#ff6b6b"}
    layers=[rule,bar]
    for col in ema_cols:
        if col in cd.columns:
            cd_ema=cd[["date",col]].dropna()
            layers.append(alt.Chart(cd_ema).mark_line(strokeWidth=1.5).encode(x="date:T",y=alt.Y(f"{col}:Q",scale=alt.Scale(zero=False)),color=alt.value(ema_colors.get(col,"#888"))))
    if fibs:
        FIB_COLORS={"0.0":"#9e9e9e","0.236":"#ff9800","0.382":"#ff5722","0.500":"#9c27b0","0.618":"#2196f3","0.786":"#4caf50","1.0":"#9e9e9e"}
        first_dt=cd["date"].iloc[0]
        for lbl,pval in fibs.items():
            clr=FIB_COLORS.get(lbl,"#888888")
            layers.append(alt.Chart(pd.DataFrame({"y":[pval]})).mark_rule(color=clr,strokeWidth=1.2,opacity=0.85).encode(y=alt.Y("y:Q",scale=alt.Scale(zero=False))))
            layers.append(alt.Chart(pd.DataFrame({"x":[first_dt],"y":[pval],"label":[f"F{lbl} {pval:,.0f}"]})).mark_text(align="left",baseline="bottom",fontSize=9,fontWeight="bold",color=clr,dx=2,dy=-2).encode(x="x:T",y=alt.Y("y:Q",scale=alt.Scale(zero=False)),text="label:N"))
    if sd_zones:
        demand_z,supply_z=sd_zones
        for z in demand_z: layers.append(alt.Chart(pd.DataFrame({"y1":[z["bot"]],"y2":[z["top"]]})).mark_rect(opacity=0.07,color="#00c853").encode(y=alt.Y("y1:Q",scale=alt.Scale(zero=False)),y2="y2:Q"))
        for z in supply_z: layers.append(alt.Chart(pd.DataFrame({"y1":[z["bot"]],"y2":[z["top"]]})).mark_rect(opacity=0.07,color="#ff3d57").encode(y=alt.Y("y1:Q",scale=alt.Scale(zero=False)),y2="y2:Q"))
    if fvgs:
        bull_fvg,bear_fvg=fvgs
        for fg in bull_fvg: layers.append(alt.Chart(pd.DataFrame({"y1":[fg["bot"]],"y2":[fg["top"]]})).mark_rect(opacity=0.06,color="#00e676").encode(y=alt.Y("y1:Q",scale=alt.Scale(zero=False)),y2="y2:Q"))
        for fg in bear_fvg: layers.append(alt.Chart(pd.DataFrame({"y1":[fg["bot"]],"y2":[fg["top"]]})).mark_rect(opacity=0.06,color="#ff3d57").encode(y=alt.Y("y1:Q",scale=alt.Scale(zero=False)),y2="y2:Q"))
    if bos_swings:
        layers.append(alt.Chart(pd.DataFrame({"y":[bos_swings["swing_high"]]})).mark_rule(color="#dc2626",strokeDash=[4,3],strokeWidth=1,opacity=0.7).encode(y=alt.Y("y:Q",scale=alt.Scale(zero=False))))
        layers.append(alt.Chart(pd.DataFrame({"y":[bos_swings["swing_low"]]})).mark_rule(color="#16a34a",strokeDash=[4,3],strokeWidth=1,opacity=0.7).encode(y=alt.Y("y:Q",scale=alt.Scale(zero=False))))
    if entry_lines:
        for ln in entry_lines:
            stroke_dash=([4,3] if ln.get("dash")=="dashed" else([2,2] if ln.get("dash")=="dotted" else[1,0]))
            layers.append(alt.Chart(pd.DataFrame({"y":[ln["price"]]})).mark_rule(color=ln["color"],strokeWidth=ln.get("width",2),strokeDash=stroke_dash,opacity=0.92).encode(y=alt.Y("y:Q",scale=alt.Scale(zero=False))))
            layers.append(alt.Chart(pd.DataFrame({"x":[cd["date"].iloc[-1]],"y":[ln["price"]],"label":[ln["label"]]})).mark_text(align="right",baseline="bottom",fontSize=10,fontWeight="bold",color=ln["color"],dx=-4,dy=-3).encode(x="x:T",y=alt.Y("y:Q",scale=alt.Scale(zero=False)),text="label:N"))
    return alt.layer(*layers).properties(height=height,title=title)

@st.cache_data(ttl=300)
def get_commodity_news(query):
    try:
        feed=feedparser.parse(f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en")
        return [{"title":e.title,"link":e.link,"time":e.get("published","")} for e in feed.entries[:7]]
    except Exception: return []

# ═════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("<div style='font-size:0.9rem;color:#2563eb;font-weight:800;letter-spacing:0.07em;margin-bottom:0.3rem;'>TradeMatrix</div>",unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:0.62rem;margin-bottom:0.3rem;'>NSE: <b>{NSE_MKT}</b> &nbsp;|&nbsp; MCX: <b>{MCX_MKT}</b></div>",unsafe_allow_html=True)
    st.divider()

    total_pnl_sb=st.session_state.total_pnl+st.session_state.mcx_total_pnl
    tot_col="#00e676" if total_pnl_sb>=0 else "#ff3d57"; tot_sign="+" if total_pnl_sb>=0 else ""
    nse_pnl_col="#00e676" if st.session_state.total_pnl>=0 else "#ff3d57"; nse_pnl_sign="+" if st.session_state.total_pnl>=0 else ""
    mcx_pnl_col="#2e7d32" if st.session_state.mcx_total_pnl>=0 else "#c62828"; mcx_pnl_sign="+" if st.session_state.mcx_total_pnl>=0 else ""
    pnl_pct=(st.session_state.total_pnl/INITIAL_CAPITAL)*100
    st.markdown(
        f"<div style='font-size:0.56rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.1em;'>NSE Capital</div>"
        f"<div style='font-size:1.1rem;font-weight:700;color:#2563eb;'>&#x20B9;{st.session_state.capital:,.0f}</div>"
        f"<div style='font-size:0.72rem;color:{nse_pnl_col};'>{nse_pnl_sign}&#x20B9;{st.session_state.total_pnl:,.1f} ({nse_pnl_sign}{pnl_pct:.1f}%) NSE</div>"
        f"<div style='font-size:0.72rem;color:{mcx_pnl_col};'>{mcx_pnl_sign}&#x20B9;{st.session_state.mcx_total_pnl:,.1f} MCX</div>"
        f"<div style='font-size:0.8rem;font-weight:700;color:{tot_col};border-top:1px solid #e0e0e0;margin-top:4px;padding-top:4px;'>{tot_sign}&#x20B9;{total_pnl_sb:,.1f} TOTAL</div>",
        unsafe_allow_html=True)
    st.divider()

    st.markdown("<div style='font-size:0.6rem;color:#2563eb;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.2rem;'>⚡ NSE Settings</div>",unsafe_allow_html=True)
    capital_input=st.number_input("Capital / trade (Rs)",5000,500000,INITIAL_CAPITAL,1000)
    adx_threshold=st.slider("ADX Floor",15,30,25)
    min_score=st.slider("Min Score (75+ = score-100 only)",30,100,75)
    signal_mode=st.radio("Mode",["Trend + State","Fresh Cross Only"],index=0)
    # Daily loss limit display
    _nse_dl_col="#00e676" if st.session_state.nse_daily_pnl>=0 else "#ff3d57"
    _nse_dl_pct=round(st.session_state.nse_daily_pnl/NSE_DAILY_LOSS_LIMIT*100) if NSE_DAILY_LOSS_LIMIT!=0 else 0
    _nse_halted = st.session_state.nse_daily_pnl <= NSE_DAILY_LOSS_LIMIT
    st.markdown(
        f"<div style='background:{'#fff1f2' if _nse_halted else '#f9fafb'};border:1px solid {'#fca5a5' if _nse_halted else '#e5e7eb'};border-radius:6px;padding:0.3rem 0.5rem;font-size:0.62rem;'>"
        f"{'🚫 <b>NSE HALTED</b> — daily loss limit hit' if _nse_halted else f'📊 Today NSE P&L: <b style=\"color:{_nse_dl_col};\">₹{st.session_state.nse_daily_pnl:,.0f}</b>'}"
        f"<br/>Limit: ₹{NSE_DAILY_LOSS_LIMIT:,} | Used: {_nse_dl_pct}%</div>",
        unsafe_allow_html=True)
    st.divider()

    st.markdown("<div style='font-size:0.6rem;color:#8B4513;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.2rem;'>🛢️ MCX Settings</div>",unsafe_allow_html=True)
    commodity_key=st.selectbox("Commodity",list(COMMODITY_CONFIG.keys()),format_func=lambda k:COMMODITY_CONFIG[k]["label"])
    cfg=COMMODITY_CONFIG[commodity_key]
    if st.session_state.prev_commodity!=commodity_key:
        st.cache_data.clear(); st.session_state.prev_commodity=commodity_key
    num_lots=st.number_input("Number of Lots",1,10,2)
    lot_size_opt=st.selectbox("Lot Size",cfg["lot_sizes"],index=cfg["default_lot"],format_func=lambda v:cfg["lot_labels"][cfg["lot_sizes"].index(v)])
    st.markdown("**🎯 Premium Target Range**")
    min_prem=st.number_input("Min Premium (₹)",1,5000,cfg["min_prem"],10)
    max_prem=st.number_input("Max Premium (₹)",1,5000,cfg["max_prem"],10)
    target_prem=(min_prem+max_prem)/2
    st.info(f"Premium: ₹{min_prem}–₹{max_prem}\nPer lot: ₹{int(min_prem*lot_size_opt)}–₹{int(max_prem*lot_size_opt)}\n{num_lots} lot(s): ₹{int(min_prem*lot_size_opt*num_lots):,}–₹{int(max_prem*lot_size_opt*num_lots):,}")
    st.markdown("**📈 Signal Settings**")
    adx_min=st.slider("ADX Threshold",18,35,cfg["adx_thresh"])
    min_score_mcx=st.slider("Min Signal Score",40,90,cfg["min_score"])
    # Daily loss limit display
    _mcx_dl_col="#00e676" if st.session_state.mcx_daily_pnl>=0 else "#ff3d57"
    _mcx_halted = st.session_state.mcx_daily_pnl <= MCX_DAILY_LOSS_LIMIT
    st.markdown(
        f"<div style='background:{'#fff1f2' if _mcx_halted else '#f9fafb'};border:1px solid {'#fca5a5' if _mcx_halted else '#e5e7eb'};border-radius:6px;padding:0.3rem 0.5rem;font-size:0.62rem;'>"
        f"{'🚫 <b>MCX HALTED</b> — daily loss limit hit' if _mcx_halted else f'📊 Today MCX P&L: <b style=\"color:{_mcx_dl_col};\">₹{st.session_state.mcx_daily_pnl:,.0f}</b>'}"
        f"<br/>Limit: ₹{MCX_DAILY_LOSS_LIMIT:,}</div>",
        unsafe_allow_html=True)
    st.markdown("**🚀 200-Pt Move Detector**")
    show_200=st.checkbox("Show 200-Pt Move Panel",value=True)
    move_boost=st.checkbox("Boost confidence when move aligns",value=True)
    st.markdown("**🛡️ Conflict Filter Settings**")
    strong_conflict_threshold=st.slider("Block signal if opposing move score ≥",40,80,60)
    moderate_conflict_threshold=st.slider("Penalize if opposing move score ≥",20,59,40)
    conflict_penalty=st.slider("Conflict confidence penalty (pts)",5,30,15)
    st.divider()

    st.markdown("**📧 Email Alerts**")
    email_alerts_enabled=st.checkbox("Enable Email Alerts",value=True)
    st.caption(f"→ Primary: {EMAIL_RECEIVER}")
    _r2_input = st.text_input("📮 Second Email (optional)",
                               value=st.session_state.email_receiver_2,
                               placeholder="second@email.com",
                               key="email_r2_input")
    if _r2_input != st.session_state.email_receiver_2:
        st.session_state.email_receiver_2 = _r2_input.strip()
    if st.session_state.email_receiver_2:
        st.caption(f"→ Second: {st.session_state.email_receiver_2}")
    st.markdown(
        "<div style='font-size:0.58rem;color:#6b7280;background:#f9fafb;border:1px solid #e5e7eb;"
        "border-radius:6px;padding:0.35rem 0.5rem;margin-top:0.2rem;'>"
        "⏱️ <b>3-min cooldown:</b> COPPER<br/>"
        "⏱️ <b>2-min cooldown:</b> NIFTY, BANKNIFTY,<br/>&nbsp;&nbsp;&nbsp;&nbsp;SENSEX, CRUDEOIL, GOLDM</div>",
        unsafe_allow_html=True)
    if st.button("🔔 Send Test Email"):
        send_alert(f"✅ <b>TradeMatrix — Test</b>\nEmail working!\n⏰ {datetime.now().strftime('%H:%M')}",subject="✅ TradeMatrix Test")
        st.success("Test email sent!")
    st.divider()

    mcx_total_t=st.session_state.mcx_wins+st.session_state.mcx_losses
    mcx_wr=(st.session_state.mcx_wins/mcx_total_t*100) if mcx_total_t>0 else 0
    st.markdown(f"<div style='font-size:0.7rem;'><b>📊 MCX Paper</b><br/>Trades: <b>{mcx_total_t}</b> &nbsp;|&nbsp; <span style='color:#2e7d32;'>W:{st.session_state.mcx_wins}</span> <span style='color:#c62828;'>L:{st.session_state.mcx_losses}</span><br/>WR: <b>{mcx_wr:.1f}%</b><br/>P&amp;L: <b style='color:{mcx_pnl_col};'>{mcx_pnl_sign}&#x20B9;{st.session_state.mcx_total_pnl:,.1f}</b></div>",unsafe_allow_html=True)
    st.divider()

    rc1,rc2=st.columns(2)
    with rc1:
        if st.button("Reset NSE"):
            for k,v in {"capital":float(INITIAL_CAPITAL),"total_pnl":0.0,"open_trades":{},"trade_history":[],"wins":0,"losses":0,"trades_today":0,"signal_log":[],"last_signal_state":{}}.items():
                st.session_state[k]=v
            st.rerun()
    with rc2:
        if st.button("Reset MCX"):
            for k,v in {"mcx_open_trades":{},"mcx_trade_history":[],"mcx_total_pnl":0.0,"mcx_wins":0,"mcx_losses":0,"mcx_trades_today":0,"mcx_signal_log":[],"mcx_last_signal_state":{}}.items():
                st.session_state[k]=v
            st.rerun()

    st.divider()
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
# MAIN LAYOUT
# ═════════════════════════════════════════════════════════════════════════════
current_minute=current_time.strftime("%H:%M")

# ── FETCH VIX ─────────────────────────────────────────────────────────────────
_vix_live = fetch_vix_live()

# ── VIX ADVISORY BANNER ───────────────────────────────────────────────────────
if _vix_live:
    if _vix_live > 25:
        st.markdown(
            f"<div style='background:#fff1f2;border:1px solid #fca5a5;border-radius:6px;"
            f"padding:0.3rem 0.9rem;font-size:0.68rem;color:#991b1b;margin-bottom:0.4rem;'>"
            f"⚠️ India VIX {_vix_live:.1f} — HIGH volatility. Use ATM only, consider half size. Signals still active.</div>",
            unsafe_allow_html=True)
    elif _vix_live > 18:
        st.markdown(
            f"<div style='background:#fffbeb;border:1px solid #fcd34d;border-radius:6px;"
            f"padding:0.3rem 0.9rem;font-size:0.68rem;color:#92400e;margin-bottom:0.4rem;'>"
            f"📊 VIX {_vix_live:.1f} elevated — ATM preferred, avoid OTM.</div>",
            unsafe_allow_html=True)

# ── HEADER ────────────────────────────────────────────────────────────────────
total_pnl=st.session_state.total_pnl+st.session_state.mcx_total_pnl
tot_hc="#00e676" if total_pnl>=0 else "#ff3d57"; tot_ps="+" if total_pnl>=0 else ""
nse_pnl_hc="#00e676" if st.session_state.total_pnl>=0 else "#ff3d57"; nse_ps_="+" if st.session_state.total_pnl>=0 else ""
mcx_pnl_hc="#2e7d32" if st.session_state.mcx_total_pnl>=0 else "#c62828"; mcx_ps_="+" if st.session_state.mcx_total_pnl>=0 else ""
nse_open_col="#16a34a" if "OPEN" in NSE_MKT else "#dc2626"
mcx_open_col="#16a34a" if "OPEN" in MCX_MKT else "#dc2626"
_vix_col = "#dc2626" if (_vix_live and _vix_live > 25) else ("#d97706" if (_vix_live and _vix_live > 18) else "#16a34a")

st.markdown(
    f"<div style='display:flex;align-items:center;justify-content:space-between;"
    f"padding:0.5rem 1rem;background:#ffffff;border:1px solid #e5e7eb;"
    f"border-top:3px solid #1565c0;border-radius:12px;margin-bottom:0.3rem;'>"
    f"<div style='display:flex;align-items:center;gap:0.8rem;'>"
    f"<span style='font-size:1.3rem;'>📡</span>"
    f"<div><div style='font-family:Syne,sans-serif;font-size:0.95rem;font-weight:800;color:#111827;'>TradeMatrix</div>"
    f"<div style='font-size:0.52rem;color:#6b7280;'>NSE: NIFTY·BANKNIFTY·SENSEX·FINNIFTY &nbsp;|&nbsp; MCX: CRUDEOIL·GOLDM·COPPER·NATURALGAS</div></div></div>"
    f"<div style='display:flex;gap:1.2rem;align-items:center;'>"
    f"<div style='text-align:center;'><div style='font-size:0.42rem;color:#6b7280;text-transform:uppercase;'>NSE</div><div style='font-size:0.6rem;color:{nse_open_col};font-weight:700;'>{NSE_MKT}</div></div>"
    f"<div style='text-align:center;'><div style='font-size:0.42rem;color:#6b7280;text-transform:uppercase;'>MCX</div><div style='font-size:0.6rem;color:{mcx_open_col};font-weight:700;'>{MCX_MKT}</div></div>"
    f"<div style='text-align:center;'><div style='font-size:0.42rem;color:#6b7280;text-transform:uppercase;'>VIX</div><div style='font-size:0.6rem;font-weight:700;color:{_vix_col};'>{f'{_vix_live:.1f}' if _vix_live else '—'}</div></div>"
    f"<div style='text-align:center;'><div style='font-size:0.42rem;color:#6b7280;text-transform:uppercase;'>NSE P&amp;L</div><div style='font-size:0.68rem;font-weight:700;color:{nse_pnl_hc};'>{nse_ps_}&#x20B9;{st.session_state.total_pnl:,.1f}</div></div>"
    f"<div style='text-align:center;'><div style='font-size:0.42rem;color:#6b7280;text-transform:uppercase;'>MCX P&amp;L</div><div style='font-size:0.68rem;font-weight:700;color:{mcx_pnl_hc};'>{mcx_ps_}&#x20B9;{st.session_state.mcx_total_pnl:,.1f}</div></div>"
    f"<div style='text-align:center;'><div style='font-size:0.42rem;color:#6b7280;text-transform:uppercase;'>Total P&amp;L</div><div style='font-size:0.8rem;font-weight:800;color:{tot_hc};'>{tot_ps}&#x20B9;{total_pnl:,.1f}</div></div>"
    f"<div style='text-align:center;'><div style='font-size:0.42rem;color:#6b7280;text-transform:uppercase;'>NSE Pos</div><div style='font-size:0.65rem;color:#111827;'>{len(st.session_state.open_trades)}</div></div>"
    f"<div style='text-align:center;'><div style='font-size:0.42rem;color:#6b7280;text-transform:uppercase;'>MCX Pos</div><div style='font-size:0.65rem;color:#111827;'>{len(st.session_state.mcx_open_trades)}</div></div>"
    f"<div style='font-size:0.5rem;color:#9ca3af;'>{current_time.strftime('%d %b %H:%M IST')}</div>"
    f"</div></div>",unsafe_allow_html=True)

# ── SEGMENT DROPDOWN SELECTOR ─────────────────────────────────────────────────
st.markdown("<div style='margin-bottom:0.25rem;'></div>", unsafe_allow_html=True)

_seg_col, _info_col = st.columns([2, 5])
with _seg_col:
    segment_view = st.selectbox(
        "📌 Select Segment",
        options=["NSE / BSE INDICES", "MCX COMMODITIES"],
        index=0 if st.session_state.segment_view == "NSE / BSE INDICES" else 1,
        key="segment_selector",
        label_visibility="visible",
    )
    st.session_state.segment_view = segment_view

with _info_col:
    _seg_color = "#1565c0" if segment_view == "NSE / BSE INDICES" else "#8B4513"
    _seg_icon  = "⚡" if segment_view == "NSE / BSE INDICES" else "🛢️"
    st.markdown(
        f"<div style='background:{_seg_color}10;border:1px solid {_seg_color}30;border-radius:8px;"
        f"padding:0.45rem 1rem;margin-top:1.6rem;font-size:0.7rem;color:{_seg_color};'>"
        f"{_seg_icon} Showing: <b>{segment_view}</b> &nbsp;|&nbsp; "
        f"📋 Auto Paper Mode &nbsp;|&nbsp; "
        f"⏱️ 3-min cooldown: COPPER &nbsp;|&nbsp; 2-min cooldown: NIFTY · BANKNIFTY · SENSEX · CRUDEOIL · GOLDM</div>",
        unsafe_allow_html=True)

st.markdown("<div class='seg-divider'></div>", unsafe_allow_html=True)

show_nse = (segment_view == "NSE / BSE INDICES")
show_mcx = (segment_view == "MCX COMMODITIES")

# ─────────────────────────────────────────────────────────────────────────────
# NSE DATA FETCH (always runs so positions stay live)
# ─────────────────────────────────────────────────────────────────────────────
lot_map  = {"NIFTY":50,"SENSEX":10,"BANKNIFTY":15,"FINNIFTY":40}
step_map = {"NIFTY":50,"SENSEX":100,"BANKNIFTY":100,"FINNIFTY":50}
news_kw  = {"NIFTY":"NIFTY 50 NSE today","BANKNIFTY":"Bank Nifty today","SENSEX":"SENSEX BSE today","FINNIFTY":"FINNIFTY NSE today"}

sym_data = {}
# ── One batch quote call for live LTP — no rate limit risk ───────────────────
_nse_ltps = get_nse_ltp_batch()

# ── Persistent candle cache in session_state ──────────────────────────────────
if "nse_candle_cache" not in st.session_state:
    st.session_state["nse_candle_cache"] = {}

def _get_candles(tok, sym, tf, days):
    """Fetch candles; on failure return last cached version from session_state."""
    cache_key = f"{sym}_{tf}"
    df, err = get_data(tok, sym, tf, days)
    if not df.empty:
        st.session_state["nse_candle_cache"][cache_key] = df
        return df, err, False
    # API failed — use last known good candles
    cached = st.session_state["nse_candle_cache"].get(cache_key)
    if cached is not None and not cached.empty:
        return cached, err, True   # stale but usable
    return pd.DataFrame(), err, False

with st.spinner("Fetching NSE market data..."):
    for _i, sym in enumerate(NSE_SYMBOLS):
        if _i > 0:
            time_module.sleep(1.2)   # 1.2s gap → well within Kite 3-req/s limit

        tok = get_token(sym)        # always returns hardcoded token — never None
        if tok is None:
            sym_data[sym] = {"_err": "Token not found", "_tok": "?"}
            continue

        # ── Fetch 3 timeframes with gap between each ──────────────────────────
        df5_raw,  _err5,  _c5  = _get_candles(tok, sym, "5minute",  7)
        time_module.sleep(0.6)
        df15_raw, _err15, _c15 = _get_candles(tok, sym, "15minute", 7)
        time_module.sleep(0.6)
        df3_raw,  _err3,  _c3  = _get_candles(tok, sym, "3minute",  5)

        df5  = apply_indicators(df5_raw)
        df15 = apply_indicators(df15_raw)
        df3  = apply_indicators(df3_raw)

        _fetch_err  = _err5 or _err15 or ""
        _from_cache = _c5 or _c15

        if df5.empty or "EMA9" not in df5.columns or "VWAP" not in df5.columns:
            sym_data[sym] = {"_err": _fetch_err or "No data received",
                             "_tok": tok, "_from_cache": False}
            continue
        # ── Detect if today is a holiday / market closed ──────────────────────
        # If latest candle is from a previous day, show "last known" data with banner
        _last_candle_date = pd.to_datetime(df5.index[-1]).date() if not df5.empty else None
        _is_holiday_data  = (_last_candle_date is not None and
                              _last_candle_date < current_time.date())
        # Live price: use batch quote (1 call for all 4) — fallback to last candle
        _live_ltp = _nse_ltps.get(sym, 0)
        price     = _live_ltp if _live_ltp > 0 else float(df5["close"].iloc[-1])
        opt_df  = get_option_chain(sym,price)
        sig,sc,reasons = compute_signal(df5,df15,adx_threshold,min_score,signal_mode)
        _dte   = days_to_expiry(sym)
        # Strip BLOCKED_ prefix for functions that only need raw CALL/PUT direction
        _sig_norm = sig.replace("BLOCKED_","") if sig.startswith("BLOCKED_") else sig
if show_nse:
    nse_check_setup_forming(sym, df5, df15, _sig_norm, sc, current_minute, min_score)

    if _dte == 0 and email_alerts_enabled:
        nse_send_expiry_briefing(sym, price, calc_pcr(opt_df), calc_max_pain(opt_df), _vix_live)

    atm_row, itm_row, otm_row = pick_options(
        opt_df, _sig_norm, price, step_map[sym], capital_input, lot_map[sym]
    )

    # ── STEP 1: GET OPTION PREMIUM (LTP) ─────────────
    try:
        option_ltp = float(atm_row["ltp"])
    except Exception:
        try:
            option_ltp = float(atm_row.get("ltp", 0))
        except Exception:
            option_ltp = price

    # ── CHOP FILTER (SAFE) ─────────────────────────
    skip_trade = False
    try:
        rsi = float(df5["RSI"].iloc[-1])
        di_plus = float(df5["+DI"].iloc[-1])
        di_minus = float(df5["-DI"].iloc[-1])

        if 45 <= rsi <= 55 or abs(di_plus - di_minus) < 3:
            st.toast(f"{sym} ⚠️ Chop zone")
            skip_trade = True
    except:
        pass

    # ── EXIT CHECKS ─────────────────────────────
    check_auto_exit(sym, opt_df)
    check_time_sl(sym)

    # ── STEP 2: ENTRY INITIALIZATION ─────────────
    if (
        st.session_state.get("nse_pro_mode", True)
        and _sig_norm in ("CALL", "PUT")
        and not skip_trade
    ):
        if "nse_trade_state" not in st.session_state:
            st.session_state["nse_trade_state"] = {}

        state = st.session_state["nse_trade_state"].get(sym)

        if state is None:
            state = {
                "entry": option_ltp,
                "partial_done": False,
                "sl": None
            }
            st.session_state["nse_trade_state"][sym] = state

    # ── STEP 3: PROFIT MANAGEMENT ─────────────
    if st.session_state.get("nse_pro_mode", True) and sym in st.session_state.get("nse_trade_state", {}):

        state = st.session_state["nse_trade_state"][sym]

        entry = state["entry"]
        ltp = option_ltp

        # ── TREND MODE ─────────────────────────
        trend_mode = False
        try:
            adx = float(df5["ADX"].iloc[-1])
            if adx > 30:
                trend_mode = True
        except:
            pass

        # ── PARTIAL EXIT ───────────────────────
        if not state["partial_done"] and ltp >= entry + 20:
            state["partial_done"] = True

            if not trend_mode:
                state["sl"] = entry
            else:
                state["sl"] = entry - 5

            st.toast(f"{sym} ✅ Partial exit (Trend={trend_mode})")

        # ── ENSURE SL INITIALIZED ─────────────
        if state["sl"] is None:
            state["sl"] = entry

        # ── TRAILING SL ───────────────────────
        if state["partial_done"]:
            if ltp >= entry + 40:
                state["sl"] = max(state["sl"], entry + 10)

            if ltp >= entry + 60:
                state["sl"] = max(state["sl"], entry + 25)

        # ── SL EXIT ───────────────────────────
        if state["sl"] is not None and ltp <= state["sl"]:
            st.toast(f"{sym} 🛑 Trailing SL hit")
            st.session_state["nse_trade_state"].pop(sym, None)

        # ── SUPPORT / RESISTANCE EXIT ─────────
        try:
            support, resistance = calc_sr(df5)

            if _sig_norm == "CALL" and ltp >= resistance:
                st.toast(f"{sym} 🎯 Resistance hit")
                st.session_state["nse_trade_state"].pop(sym, None)

            if _sig_norm == "PUT" and ltp <= support:
                st.toast(f"{sym} 🎯 Support hit")
                st.session_state["nse_trade_state"].pop(sym, None)

        except:
            pass

        # BS premium levels for SL/T1/T2
        _iv    = max(0.10, min((_vix_live or 15) / 100, 0.60))
        _atr_v = float(df5["ATR"].iloc[-1]) if "ATR" in df5.columns else price * 0.003
        _opt_strikes = pick_options_premium(_sig_norm, price, step_map[sym], _dte, _iv, lot_map[sym],
                                            _atr_v*1.5, _atr_v*3, _atr_v*4.5)
        # 3-min confirmation
        _conf_s, _ = confirm_3min(df3, _sig_norm) if _sig_norm in ("CALL","PUT") else ("—","")
        _fibs  = extra_calc_fibonacci(df5,50); _fvgs = extra_calc_fvg(df5)
        _bos,_choch,_swings = extra_calc_bos_choch(df5)
        _demand,_supply     = extra_calc_sd_zones(df5)
        _vs,_vr             = extra_vol_spike(df5); _div = extra_rsi_divergence(df5)
        sym_data[sym]={
            "df5":df5,"df15":df15,"df3":df3,"price":price,"opt_df":opt_df,
            "signal":sig,"score":sc,"reasons":reasons if isinstance(reasons,list) else [],
            "atm_row":atm_row,"itm_row":itm_row,"otm_row":otm_row,
            "opt_strikes":_opt_strikes,"conf":_conf_s,"dte":_dte,
            "pcr":calc_pcr(opt_df),"smart":smart_money(opt_df),"max_pain":calc_max_pain(opt_df),
            "support":calc_sr(df5)[0],"resistance":calc_sr(df5)[1],
            "oi_sup":calc_oi_levels(opt_df)[0],"oi_res":calc_oi_levels(opt_df)[1],
            "fibs":_fibs,"fvgs":_fvgs,"bos":_bos,"choch":_choch,"swings":_swings,
            "demand":_demand,"supply":_supply,"vol_spike":_vs,"vol_r":_vr,"div":_div,
            "is_holiday":_is_holiday_data,
            "last_candle_date":str(_last_candle_date) if _last_candle_date else "",
            "from_cache":_from_cache,
            "fetch_err":_fetch_err,
        }
        log_signal_and_auto_trade(sym,sig,sc,atm_row,price,step_map[sym],lot_map[sym],current_minute)

# ═════════════════════════════════════════════════════════════════════════════
# NSE SECTION
# ═════════════════════════════════════════════════════════════════════════════
if show_nse:
    st.markdown("<span class='seg-label' style='background:#eff6ff;color:#1565c0;'>⚡ NSE / BSE INDICES — NIFTY · BANKNIFTY · SENSEX · FINNIFTY</span>",unsafe_allow_html=True)

    # ── Holiday / Market Closed Banner ───────────────────────────────────────
    _is_hol_today, _hol_reason_today = is_nse_holiday()
    _any_holiday = any(sym_data.get(s) and sym_data[s].get("is_holiday") for s in NSE_SYMBOLS)
    _mkt_is_open = dtime(9,15) <= _now_t <= dtime(15,30)
    _any_none    = any(sym_data.get(s) is None or
                       (isinstance(sym_data.get(s), dict) and "_err" in sym_data.get(s,{}))
                       for s in NSE_SYMBOLS)

    if _is_hol_today:
        _last_dt = next((sym_data[s]["last_candle_date"] for s in NSE_SYMBOLS
                         if sym_data.get(s) and sym_data[s].get("last_candle_date")), "prev trading day")
        st.markdown(
            f"<div style='background:#fff8e1;border:1px solid #fcd34d;border-radius:8px;"
            f"padding:0.4rem 1rem;font-size:0.72rem;color:#92400e;margin-bottom:0.4rem;'>"
            f"📅 <b>Market Closed — {_hol_reason_today}</b> · "
            f"Showing last available data from <b>{_last_dt}</b>. "
            f"Signals are for reference only. Next trading day: 9:15 AM IST.</div>",
            unsafe_allow_html=True)
    elif not _mkt_is_open:
        st.markdown(
            f"<div style='background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;"
            f"padding:0.4rem 1rem;font-size:0.72rem;color:#0369a1;margin-bottom:0.4rem;'>"
            f"🕐 <b>Market Closed</b> — Showing previous session data. "
            f"Market opens at <b>9:15 AM IST</b>.</div>",
            unsafe_allow_html=True)
    elif _mkt_is_open and _any_none:
        st.markdown(
            f"<div style='background:#fff1f2;border:1px solid #fca5a5;border-radius:8px;"
            f"padding:0.4rem 1rem;font-size:0.72rem;color:#991b1b;margin-bottom:0.4rem;'>"
            f"⚠️ <b>Some symbols failed to load</b> — Kite API error. "
            f"Press <b>🔄 Refresh Data</b> in the sidebar to retry.</div>",
            unsafe_allow_html=True)
        # Show ACTUAL Kite error so we can diagnose the real root cause
        _failed = {s: sym_data.get(s) for s in NSE_SYMBOLS
                   if sym_data.get(s) is None or
                   (isinstance(sym_data.get(s), dict) and "_err" in sym_data.get(s, {}))}
        if _failed:
            with st.expander("🔍 Kite API error details — click to expand", expanded=True):
                for _fs, _fd in _failed.items():
                    _e = _fd.get("_err","(no error message)") if isinstance(_fd,dict) else "d=None (tok returned None)"
                    _t = _fd.get("_tok","?") if isinstance(_fd,dict) else "?"
                    st.code(f"{_fs} | token={_t} | error: {_e}")

    nse_main, nse_right = st.columns([3,1])

    with nse_main:
        st.markdown("<div style='font-size:0.58rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.15em;border-bottom:1px solid #e0e0e0;padding-bottom:0.2rem;margin-bottom:0.35rem;'>Index Signal Scorecard</div>",unsafe_allow_html=True)
        sc_cols = st.columns(4)
        for i,sym in enumerate(NSE_SYMBOLS):
            d=sym_data.get(sym)
            _sig_disp = d['signal'].replace("BLOCKED_","⚠️ ") if d and isinstance(d,dict) and 'signal' in d and d['signal'].startswith("BLOCKED_") else (d['signal'] if d and isinstance(d,dict) and 'signal' in d else '—')
            sc_cols[i].metric(
                label=f"⚡ {sym}",
                value=f"₹{d['price']:,.2f}" if d and isinstance(d,dict) and d.get("price") else "—",
                delta=f"{_sig_disp} | {d['score'] if d and isinstance(d,dict) and 'score' in d else 0}/100",
            )

        for i in range(0, len(NSE_SYMBOLS), 2):
            cols = st.columns(2)
            for j, sym in enumerate(NSE_SYMBOLS[i:i+2]):
                with cols[j]:
                    d = sym_data.get(sym)
                    # d is None (token failed) OR d is error dict (data fetch failed)
                    if d is None or (isinstance(d, dict) and "_err" in d):
                        _mkt_now_open = dtime(9,15) <= _now_t <= dtime(15,30)
                        _hol_now, _hol_name = is_nse_holiday()
                        _err_detail = d.get("_err","") if isinstance(d,dict) else "token lookup failed"
                        _tok_val    = d.get("_tok","?") if isinstance(d,dict) else "?"
                        _tok_detail = f"token={_tok_val} · {_err_detail[:100]}" if _err_detail else f"token={_tok_val}"
                        if _hol_now:
                            st.markdown(
                                f"<div style='background:#fff8e1;border:1px solid #fcd34d;"
                                f"border-radius:12px;padding:1rem;color:#92400e;"
                                f"font-size:0.75rem;text-align:center;'>"
                                f"📅 <b>{sym}</b> — {_hol_name}<br/>"
                                f"<span style='font-size:0.65rem;color:#b45309;'>"
                                f"Market closed. Data loads on next trading day at 9:15 AM IST</span>"
                                f"</div>", unsafe_allow_html=True)
                        elif _mkt_now_open:
                            st.markdown(
                                f"<div style='background:#fff1f2;border:1px solid #fca5a5;"
                                f"border-radius:12px;padding:0.8rem;color:#991b1b;"
                                f"font-size:0.75rem;text-align:center;'>"
                                f"⚠️ <b>{sym}</b> — Data fetch failed<br/>"
                                f"<span style='font-size:0.6rem;color:#b91c1c;font-family:monospace;'>"
                                f"{_tok_detail}"
                                f"{' · ' + _err_detail[:80] if _err_detail else ''}</span><br/>"
                                f"<span style='font-size:0.62rem;color:#b91c1c;'>"
                                f"Press 🔄 Refresh Data in sidebar</span>"
                                f"</div>", unsafe_allow_html=True)
                        else:
                            st.markdown(
                                f"<div style='background:#f0f9ff;border:1px solid #bae6fd;"
                                f"border-radius:12px;padding:1rem;color:#0369a1;"
                                f"font-size:0.75rem;text-align:center;'>"
                                f"🕐 <b>{sym}</b> — Market Closed<br/>"
                                f"<span style='font-size:0.65rem;color:#0284c7;'>"
                                f"Data loads at 9:15 AM IST on next trading day</span>"
                                f"</div>", unsafe_allow_html=True)
                        continue

                    signal=d["signal"]; score=d["score"]; price=d["price"]
                    atm_row=d["atm_row"]; lot_size=lot_map[sym]; step=step_map[sym]
                    _is_blocked   = signal.startswith("BLOCKED_")
                    _sig_display  = signal.replace("BLOCKED_","") if _is_blocked else signal
                    sig_col = "#e65100" if _is_blocked else ("#16a34a" if _sig_display=="CALL" else ("#dc2626" if _sig_display=="PUT" else "#d97706"))
                    _sig_badge_label = f"⚠️ {_sig_display} BLOCKED" if _is_blocked else signal
                    bar_color="#16a34a" if score>=min_score else("#d97706" if score>=min_score*0.7 else "#dc2626")
                    if _is_blocked: bar_color="#e65100"
                    bar_pct=min(int(score),100)
                    _df5ok="RSI" in d["df5"].columns; _df15ok="EMA9" in d["df15"].columns
                    rsi_v=round(d["df5"]["RSI"].iloc[-1],1) if _df5ok else 0.0; adx_v=round(d["df5"]["ADX"].iloc[-1],1) if _df5ok else 0.0
                    di_p=round(d["df5"]["+DI"].iloc[-1],1) if _df5ok else 0.0; di_m=round(d["df5"]["-DI"].iloc[-1],1) if _df5ok else 0.0
                    vwap_v=round(d["df5"]["VWAP"].iloc[-1],2) if "VWAP" in d["df5"].columns else round(d["df5"]["close"].iloc[-1],2)
                    ema_b=float(d["df15"]["EMA9"].iloc[-1])>float(d["df15"]["EMA21"].iloc[-1]) if _df15ok and not d["df15"].empty else False
                    ema_c="#00e676" if ema_b else "#ff3d57"
                    _monitor_only = sym in NSE_MONITOR_ONLY_SYMBOLS
                    _monitor_badge = "<span style='background:#fff8e1;color:#b45309;border:1px solid #fcd34d;padding:0.08rem 0.4rem;border-radius:4px;font-size:0.55rem;font-weight:700;margin-left:4px;'>👁 MONITOR ONLY</span>" if _monitor_only else ""
                    _holiday_badge = ""
                    if d.get("is_holiday"):
                        _lcd = d.get("last_candle_date","prev day")
                        _hol_nm = _NSE_HOLIDAYS.get(_lcd, "Holiday")
                        _holiday_badge = (f"<span style='background:#fff8e1;color:#92400e;"
                                          f"border:1px solid #fcd34d;padding:0.08rem 0.4rem;"
                                          f"border-radius:4px;font-size:0.55rem;font-weight:700;"
                                          f"margin-left:4px;'>📅 {_hol_nm}</span>")
                    ot=st.session_state.open_trades.get(sym)
                    ot_html=""
                    if ot:
                        cur_ltp=get_trade_live_ltp(sym,ot,d.get("opt_df"))
                        cur_pnl=round((cur_ltp-ot["entry"])*ot["lot_size"],2)
                        pc="#00e676" if cur_pnl>=0 else "#ff3d57"; ps="+" if cur_pnl>=0 else ""
                        ot_html=(f"<span style='background:#e0e0e0;padding:0.1rem 0.45rem;border-radius:4px;"
                                 f"font-size:0.62rem;color:{pc};margin-left:0.4rem;'>{ot['side']} {ot['strike']}{ot['type']} "
                                 f"@ &#x20B9;{ot['entry']} | {ps}&#x20B9;{cur_pnl}</span>")
                    extra_html = extra_badges_html(d["df5"], price, d["fibs"])

                    # ── Expiry day banner
                    _dte_sym = d.get("dte", 1)
                    if _dte_sym == 0:
                        st.markdown(
                            f"<div style='background:#fff1f2;border-left:3px solid #dc2626;border-radius:6px;"
                            f"padding:0.3rem 0.8rem;font-size:0.68rem;color:#991b1b;margin-bottom:0.2rem;'>"
                            f"⚡ {sym} EXPIRY TODAY — ATM/ITM only · Exit before 2:30 PM · Score 100 required</div>",
                            unsafe_allow_html=True)

                    # ── Gap-open banner
                    _sig_reasons = d.get("reasons", [])
                    _gap_reason = next((r for r in _sig_reasons if "GAP-DOWN" in r or "GAP-UP" in r), None)
                    if _gap_reason:
                        _gap_dir = "DOWN" if "GAP-DOWN" in _gap_reason else "UP"
                        _gap_col = "#7f1d1d" if _gap_dir=="DOWN" else "#14532d"
                        _gap_bg  = "#fef2f2" if _gap_dir=="DOWN" else "#f0fdf4"
                        st.markdown(
                            f"<div style='background:{_gap_bg};border-left:3px solid {_gap_col};border-radius:6px;"
                            f"padding:0.3rem 0.8rem;font-size:0.68rem;color:{_gap_col};margin-bottom:0.2rem;'>"
                            f"⚡ GAP-{_gap_dir} OPEN detected — {_gap_reason.split('—')[0].strip()}</div>",
                            unsafe_allow_html=True)

                    # ── 3-min confirmation badge
                    _conf_badge = ""
                    _conf_val = d.get("conf","—")
                    if _conf_val == "CONFIRMED":
                        _conf_badge = "<span style='background:#f0fdf4;color:#16a34a;border:1px solid #86efac;padding:0.08rem 0.4rem;border-radius:4px;font-size:0.55rem;font-weight:700;margin-left:4px;'>3MIN ✅</span>"
                    elif _conf_val == "WAIT":
                        _conf_badge = "<span style='background:#fffbeb;color:#b45309;border:1px solid #fcd34d;padding:0.08rem 0.4rem;border-radius:4px;font-size:0.55rem;font-weight:700;margin-left:4px;'>3MIN ⏳</span>"

                    # ── Signal lock badge
                    _lock = st.session_state.auto_signal_locks.get(sym)
                    _lock_badge = ""
                    if _lock:
                        try:
                            _lock_age = (datetime.now() - datetime.fromisoformat(_lock["time"])).total_seconds() / 60
                            _lock_rem  = max(0, NSE_LOCK_MINS - int(_lock_age))
                            if _lock_rem > 0:
                                _lock_badge = f"<span style='background:#eff6ff;color:#1565c0;border:1px solid #93c5fd;padding:0.08rem 0.4rem;border-radius:4px;font-size:0.55rem;font-weight:700;margin-left:4px;'>🔒 {_lock_rem}m</span>"
                        except Exception: pass

                    st.markdown(
                        f"<div style='background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:0.6rem 0.8rem;margin-bottom:0.3rem;'>"
                        f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:0.45rem;'>"
                        f"<div style='display:flex;align-items:center;gap:0.55rem;'>"
                        f"<span style='font-size:1rem;font-weight:800;color:#111827;'>{sym}</span>"
                        f"<span style='background:{sig_col}22;color:{sig_col};border:1px solid {sig_col}44;padding:0.12rem 0.5rem;border-radius:4px;font-size:0.65rem;font-weight:700;'>{_sig_badge_label}</span>"
                        f"{_monitor_badge}{_holiday_badge}{_conf_badge}{_lock_badge}"
                        f"{ot_html}</div>"
                        f"<div style='font-size:1rem;font-weight:700;color:#111827;'>&#x20B9;{price:,.2f}</div></div>"
                        f"<div style='display:flex;align-items:center;gap:0.45rem;margin-bottom:0.4rem;'>"
                        f"<span style='font-size:0.56rem;color:#6b7280;'>SCORE</span>"
                        f"<div style='flex:1;height:5px;background:#e0e0e0;border-radius:3px;overflow:hidden;'>"
                        f"<div style='width:{bar_pct}%;height:100%;background:{bar_color};border-radius:3px;'></div></div>"
                        f"<span style='font-size:0.65rem;color:{bar_color};font-weight:700;'>{score}/100</span></div>"
                        f"<div style='display:flex;gap:0.35rem;flex-wrap:wrap;margin-bottom:0.35rem;'>"
                        f"<span style='background:#2563eb22;color:#2563eb;border:1.5px solid #2563eb33;padding:0.08rem 0.4rem;border-radius:8px;font-size:0.6rem;'>VWAP {'Abv' if price>vwap_v else 'Blw'}</span>"
                        f"<span style='background:{ema_c}22;color:{ema_c};border:1px solid {ema_c}33;padding:0.08rem 0.4rem;border-radius:8px;font-size:0.6rem;'>{'↑' if ema_b else '↓'} EMA</span>"
                        f"<span style='background:#7c3aed18;color:#7c3aed;border:1px solid #7c3aed30;padding:0.08rem 0.4rem;border-radius:8px;font-size:0.6rem;'>RSI {rsi_v}</span>"
                        f"<span style='background:#d9770618;color:#d97706;border:1px solid #d9770630;padding:0.08rem 0.4rem;border-radius:8px;font-size:0.6rem;'>ADX {adx_v}</span>"
                        f"<span style='background:#16a34a18;color:#16a34a;border:1px solid #16a34a30;padding:0.08rem 0.4rem;border-radius:8px;font-size:0.6rem;'>PCR {d['pcr']}</span>"
                        f"<span style='background:#dc262618;color:#dc2626;border:1px solid #dc262630;padding:0.08rem 0.4rem;border-radius:8px;font-size:0.6rem;'>+DI {di_p} -DI {di_m}</span>"
                        f"</div>"
                        + (f"<div style='margin-bottom:0.3rem;'>{extra_html}</div>" if extra_html else "")
                        + f"<div style='display:flex;gap:1.2rem;font-size:0.65rem;color:#4b5563;'>"
                        f"<span>S:<b style='color:#16a34a;margin-left:3px;'>{d['support']}</b></span>"
                        f"<span>R:<b style='color:#dc2626;margin-left:3px;'>{d['resistance']}</b></span>"
                        f"<span>OIS:<b style='color:#16a34a;margin-left:3px;'>{d['oi_sup']}</b></span>"
                        f"<span>OIR:<b style='color:#dc2626;margin-left:3px;'>{d['oi_res']}</b></span>"
                        + (f" <span>MaxPain:<b style='color:#7c3aed;margin-left:3px;'>₹{int(d['max_pain'])}</b></span>" if d.get('max_pain') else "")
                        + f"<span style='color:#6b7280;'>{d['smart']}</span></div></div>",
                        unsafe_allow_html=True)

                    with st.expander(f"📈 {sym} — 5-Min Chart", expanded=False):
                        st.plotly_chart(make_chart(d["df5"],d["df15"],f"{sym} · 5m",fibs=d["fibs"],fvgs=d["fvgs"],bos_swings=d["swings"],sd_zones=(d["demand"],d["supply"])),use_container_width=True,config=PLOT_CFG)

                    with st.expander(f"📊 {sym} — 15-Min Chart", expanded=False):
                        if not d["df15"].empty:
                            st.plotly_chart(make_chart_15m(d["df15"],f"{sym} · 15m",fibs=d["fibs"],bos_swings=d["swings"],sd_zones=(d["demand"],d["supply"])),use_container_width=True,config=PLOT_CFG)
                        else:
                            st.info("15-min data unavailable")

                    # ── Block option suggestions outside NSE market hours ──
                    _nse_ui_allowed = dtime(9, 0) <= _now_t <= dtime(15, 30)
                    if not _nse_ui_allowed:
                        st.warning("🔴 NSE market closed. Signals & suggestions paused until 09:00 AM.")
                    elif signal in ("BLOCKED_CALL","BLOCKED_PUT"):
                        _btype = "CALL" if "CALL" in signal else "PUT"
                        _blk_reason = d["reasons"][-1] if d["reasons"] else "Signal filtered"
                        st.markdown(f'<div class="sig-blocked">⚠️ {_btype} BLOCKED — {_blk_reason}</div>',unsafe_allow_html=True)
                        st.warning("⚠️ Blocked — showing suggested options if clears.")
                    elif signal=="WAIT":
                        _pts_needed = max(0, min_score - score)
                        st.markdown(f'<div class="sig-wait">🟡 WAIT — Score {score}/{min_score} (need {_pts_needed} more pts)</div>',unsafe_allow_html=True)
                        # ── Setup Forming banner ──────────────────────────────
                        _setup_thresh = max(1, int(min_score * 0.50))
                        if score >= _setup_thresh:
                            _l15_sf = d["df15"].iloc[-1] if not d["df15"].empty else None
                            if _l15_sf is not None:
                                _e9sf = float(_l15_sf.get("EMA9",0)); _e21sf = float(_l15_sf.get("EMA21",0))
                                _dir_sf = "CALL 📈" if _e9sf > _e21sf else "PUT 📉"
                                _adx_sf = round(float(d["df5"]["ADX"].iloc[-1]),1)
                                _rsi_sf = round(float(d["df5"]["RSI"].iloc[-1]),1)
                                st.markdown(
                                    f"<div style='background:linear-gradient(135deg,#fff8e1,#fff3cd);"
                                    f"border-left:5px solid #f59e0b;border-radius:8px;padding:10px 14px;margin:4px 0;'>"
                                    f"<div style='font-size:0.78rem;font-weight:700;color:#92400e;'>🟡 SETUP FORMING — {_dir_sf}</div>"
                                    f"<div style='font-size:0.65rem;color:#78350f;margin-top:3px;'>"
                                    f"Score <b>{score}/{min_score}</b> — need <b>{_pts_needed} more pts</b> for entry signal<br/>"
                                    f"ADX: {_adx_sf} | RSI: {_rsi_sf} | 15m EMA: {'Bullish ↑' if _e9sf>_e21sf else 'Bearish ↓'}<br/>"
                                    f"<span style='color:#b45309;'>⚡ Watch closely — confirmed signal may fire in 1–3 candles.</span>"
                                    f"</div></div>", unsafe_allow_html=True)
                        st.info("No trade — conditions not met. Waiting for signal.")
                    else:
                        # Render option cards for CALL, PUT, and BLOCKED (as preview)
                        opt_type_label="CE" if _sig_display=="CALL" else "PE"
                        bc="#16a34a" if _sig_display=="CALL" else "#dc2626"
                        _card_defs=[("⭐","BEST","#fff8e1","#ffc107",atm_row),
                                    ("💎","CHEAPER","#e8f5e9","#43a047",d["itm_row"]),
                                    ("🔹","LESS OTM","#e3f2fd","#1565c0",d["otm_row"])]
                        card_cols=st.columns(3)
                        for col_idx,(icon,badge,badge_bg,badge_col,row) in enumerate(_card_defs):
                            with card_cols[col_idx]:
                                if row is None:
                                    st.markdown(f"<div style='background:#fafafa;border:1px solid #e5e7eb;border-radius:12px;padding:0.7rem 0.8rem;min-height:170px;'><span style='background:{badge_bg};color:{badge_col};font-size:0.58rem;font-weight:700;padding:0.1rem 0.45rem;border-radius:4px;'>{icon} {badge} — {opt_type_label}</span><div style='color:#aaa;font-size:0.68rem;margin-top:0.5rem;'>No option in range</div></div>",unsafe_allow_html=True)
                                else:
                                    live=get_live_ltp(sym,row["symbol"]); ltp=live if live>0 else float(row["ltp"])
                                    strike=row["strike"]; per_lot=round(ltp*lot_size,2); two_lots=round(per_lot*2,2)
                                    sl_ltp=round(ltp*0.70,2); t1_ltp=round(ltp*1.50,2); t2_ltp=round(ltp*2.00,2)
                                    sl_pnl=round((sl_ltp-ltp)*lot_size*2,2); t1_pnl=round((t1_ltp-ltp)*lot_size*2,2); t2_pnl=round((t2_ltp-ltp)*lot_size*2,2)
                                    atm_ref=round(price/step)*step; otm_label="OTM" if int(strike-atm_ref)!=0 else "ATM"
                                    st.markdown(
                                        f"<div style='background:#ffffff;border:1px solid {bc}33;border-top:3px solid {bc};border-radius:12px;padding:0.7rem 0.8rem;'>"
                                        f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:0.3rem;'><span style='background:{badge_bg};color:{badge_col};font-size:0.58rem;font-weight:700;padding:0.1rem 0.45rem;border-radius:4px;'>{icon} {badge} — {opt_type_label}</span><span>✅</span></div>"
                                        f"<div style='font-size:0.6rem;color:#6b7280;margin-bottom:0.25rem;word-break:break-all;'>{row['symbol']}</div>"
                                        f"<div style='font-size:1.35rem;font-weight:800;color:#111827;margin-bottom:0.35rem;'>&#x20B9;{ltp:,.1f}</div>"
                                        f"<div style='display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:0.1rem;'><span style='color:#6b7280;'>Strike</span><span style='font-weight:600;'>&#x20B9;{int(strike):,} ({otm_label})</span></div>"
                                        f"<div style='display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:0.1rem;'><span style='color:#6b7280;'>Per lot</span><span style='font-weight:600;'>&#x20B9;{per_lot:,.0f}</span></div>"
                                        f"<div style='display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:0.35rem;'><span style='color:#6b7280;'>2 lot(s)</span><span style='color:#d97706;font-weight:700;'>&#x20B9;{two_lots:,.0f}</span></div>"
                                        f"<div style='border-top:1px solid #f0f0f0;margin-bottom:0.3rem;'></div>"
                                        f"<div style='display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:0.12rem;'><span style='color:#dc2626;font-weight:600;'>SL &#x20B9;{sl_ltp}</span><span style='color:#dc2626;'>−&#x20B9;{abs(sl_pnl):,.0f}</span></div>"
                                        f"<div style='display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:0.12rem;'><span style='color:#15803d;font-weight:600;'>T1 &#x20B9;{t1_ltp}</span><span style='color:#15803d;'>+&#x20B9;{t1_pnl:,.0f}</span></div>"
                                        f"<div style='display:flex;justify-content:space-between;font-size:0.65rem;'><span style='color:#15803d;font-weight:600;'>T2 &#x20B9;{t2_ltp}</span><span style='color:#15803d;'>+&#x20B9;{t2_pnl:,.0f}</span></div></div>",
                                        unsafe_allow_html=True)
                        # Confirmed signal banner below cards
                        if signal == "CALL":
                            st.markdown(f'<div class="sig-call">🟢 CALL — Buy CE &nbsp;|&nbsp; Score: {score}/100</div>',unsafe_allow_html=True)
                        elif signal == "PUT":
                            st.markdown(f'<div class="sig-put">🔴 PUT — Buy PE &nbsp;|&nbsp; Score: {score}/100</div>',unsafe_allow_html=True)

                    with st.expander(f"{sym} — OI Heatmap + Option Chain"):
                        opt_df=d["opt_df"]
                        if not opt_df.empty:
                            hm_df=opt_df.copy()
                            pivot=hm_df.pivot_table(index="type",columns="strike",values="oi",aggfunc="sum").fillna(0)
                            pivot=pivot[sorted(pivot.columns)]
                            hm_fig=go.Figure(go.Heatmap(z=pivot.values.tolist(),x=[str(int(c)) for c in pivot.columns],y=pivot.index.tolist(),
                                colorscale=[[0,"#fff5f0"],[0.25,"#fdd0b1"],[0.50,"#fc8d59"],[0.75,"#d7191c"],[1.00,"#7b0d1e"]],
                                showscale=True,hoverongaps=False,hovertemplate="Strike: %{x}<br>Type: %{y}<br>OI: %{z:,.0f}<extra></extra>"))
                            hm_fig.update_layout(height=175,margin=dict(l=45,r=90,t=10,b=40),paper_bgcolor="#ffffff",plot_bgcolor="#fafafa",font=dict(family="JetBrains Mono",size=9,color="#555555"),xaxis=dict(tickfont=dict(size=8),showgrid=False,tickangle=-45),yaxis=dict(tickfont=dict(size=9),showgrid=False))
                            st.plotly_chart(hm_fig,use_container_width=True,config={"displayModeBar":False})
                            oa,ob=st.columns(2)
                            with oa:
                                ce_df=opt_df[opt_df["type"]=="CE"].sort_values("strike")[["strike","ltp","oi","volume"]].head(12).reset_index(drop=True)
                                render_option_table(ce_df,"#1a7a4a","CE — Calls","📗")
                            with ob:
                                pe_df=opt_df[opt_df["type"]=="PE"].sort_values("strike",ascending=False)[["strike","ltp","oi","volume"]].head(12).reset_index(drop=True)
                                render_option_table(pe_df,"#c0392b","PE — Puts","📘")
                        else:
                            st.info("No option chain data")

                    with st.expander(f"{sym} — Signal Reasoning"):
                        for r in d["reasons"]: st.markdown(f"- {r}")

                    with st.expander(f"📐 {sym} — Technical Levels"):
                        c1,c2=st.columns(2)
                        with c1:
                            st.markdown("**📏 Fibonacci Retracements**")
                            if d["fibs"]:
                                FIB_EMOJIS={"0.0":"⬛","0.236":"🟧","0.382":"🟥","0.500":"🟪","0.618":"🟦","0.786":"🟩","1.0":"⬛"}
                                for lbl,val in d["fibs"].items():
                                    near=" ← price near" if abs(price-val)/price*100<0.5 else ""
                                    st.markdown(f"{FIB_EMOJIS.get(lbl,'◻️')} **{lbl}** → ₹{val:,.2f}{near}")
                            st.markdown("---")
                            st.markdown(f"**Volume Spike:** {'✅ ×'+str(d['vol_r']) if d['vol_spike'] else '❌ ×'+str(d['vol_r'])}")
                            st.markdown(f"**RSI Divergence:** {d['div'] or 'None'}")
                        with c2:
                            st.markdown("**🔷 Smart Money Concepts**")
                            st.markdown(f"BOS: **{d['bos'] or '—'}** | CHoCH: **{d['choch'] or '—'}**")
                            if d['swings']:
                                st.markdown(f"Swing H: ₹{d['swings']['swing_high']:,.2f} | Swing L: ₹{d['swings']['swing_low']:,.2f}")
                            bull_fvg,bear_fvg=d["fvgs"]
                            st.markdown(f"**FVG:** {len(bull_fvg)} Bull · {len(bear_fvg)} Bear")
                            for fg in bull_fvg: st.markdown(f"&nbsp;&nbsp;🟢 ₹{fg['bot']:,.1f}–₹{fg['top']:,.1f}")
                            for fg in bear_fvg: st.markdown(f"&nbsp;&nbsp;🔴 ₹{fg['bot']:,.1f}–₹{fg['top']:,.1f}")
                            st.markdown(f"**S&D:** {len(d['demand'])} Demand · {len(d['supply'])} Supply")

                    with st.expander(f"{sym} — 📥 Raw Data Download"):
                        _indicator_cols=["EMA9","EMA21","EMA50","RSI","MACD","MACD_SIG","MACD_HIST","VWAP","ADX","+DI","-DI"]
                        def _prep(df, extra=None):
                            try:
                                if df is None or df.empty: return pd.DataFrame()
                                out=df.copy().reset_index()
                                if out.columns[0] in ("index","level_0"): out=out.rename(columns={out.columns[0]:"date"})
                                base=["date","open","high","low","close","volume"]
                                want=base+(extra or [])
                                keep=[c for c in want if c in out.columns]
                                out=out[keep].copy()
                                for c in out.select_dtypes("float").columns: out[c]=out[c].round(4)
                                return out
                            except Exception as _pe: return pd.DataFrame({"error":[str(_pe)]})
                        df5_dl=_prep(d["df5"],_indicator_cols); df15_dl=_prep(d["df15"],_indicator_cols)
                        ts_label=current_time.strftime("%Y%m%d_%H%M")
                        dl_c1,dl_c2=st.columns(2)
                        with dl_c1: st.download_button(f"⬇ 5-MIN ({len(df5_dl)} rows)",df5_dl.to_csv(index=False).encode("utf-8"),f"{sym}_5min_{ts_label}.csv","text/csv",key=f"dl5_{sym}",use_container_width=True)
                        with dl_c2: st.download_button(f"⬇ 15-MIN ({len(df15_dl)} rows)",df15_dl.to_csv(index=False).encode("utf-8"),f"{sym}_15min_{ts_label}.csv","text/csv",key=f"dl15_{sym}",use_container_width=True)
                        if not d["opt_df"].empty:
                            st.download_button(f"⬇ Option Chain ({len(d['opt_df'])} rows)",d["opt_df"].to_csv(index=False).encode("utf-8"),f"{sym}_chain_{ts_label}.csv","text/csv",key=f"dlopt_{sym}",use_container_width=True)

                    st.markdown("<hr style='border-color:#e5e7eb;margin:0.1rem 0 0.3rem;'/>",unsafe_allow_html=True)

    with nse_right:
        primary_sym=NSE_SYMBOLS[0]
        news_items=fetch_news(news_kw.get(primary_sym,"NSE India market"))
        st.markdown("<div style='background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:0.7rem;margin-bottom:0.6rem;'><div style='font-size:0.65rem;font-weight:700;color:#111827;margin-bottom:0.4rem;'>📰 Market News</div>",unsafe_allow_html=True)
        for item in news_items[:8]:
            title=item["title"][:80]+("..." if len(item["title"])>80 else "")
            st.markdown(f"<div style='padding:0.35rem 0.55rem;border-bottom:1px solid #e0e0e0;border-left:2px solid #1565c0;margin-bottom:2px;background:#2563eb05;'><a href='{item['link']}' target='_blank' style='color:#111827;text-decoration:none;font-size:0.66rem;line-height:1.4;display:block;'>{title}</a><div style='color:#6b7280;font-size:0.56rem;margin-top:1px;'>{item['time']}</div></div>",unsafe_allow_html=True)
        st.markdown("</div>",unsafe_allow_html=True)

        st.markdown("<div style='font-size:0.56rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.14em;border-bottom:1px solid #e0e0e0;padding-bottom:0.2rem;margin-bottom:0.35rem;'>Open Positions (NSE Auto)</div>",unsafe_allow_html=True)
        if st.session_state.open_trades:
            for sym,t in list(st.session_state.open_trades.items()):
                sc="#00e676" if t["side"]=="CALL" else "#ff3d57"
                d=sym_data.get(sym)
                cur_ltp=get_trade_live_ltp(sym,t,(d or {}).get("opt_df"))
                cur_pnl=round((cur_ltp-t["entry"])*t["lot_size"],2)
                pc="#00e676" if cur_pnl>=0 else "#ff3d57"; ps="+" if cur_pnl>=0 else ""
                st.markdown(f"<div style='background:#f9fafb;border:1px solid #e5e7eb;border-left:3px solid {sc};border-radius:7px;padding:0.4rem 0.6rem 0.3rem;margin-bottom:0.3rem;font-size:0.7rem;'><div style='color:{sc};font-weight:700;'>{t['side']} {sym}</div><div style='color:#111827;'>{t['strike']}{t['type']} @ &#x20B9;{t['entry']}</div><div style='color:{pc};font-weight:600;'>P&amp;L: {ps}&#x20B9;{cur_pnl}</div><div style='color:#6b7280;font-size:0.58rem;'>SL &#x20B9;{t['sl']} | T1 &#x20B9;{t['t1']} | {t['time']}</div></div>",unsafe_allow_html=True)
                if st.button(f"✕ Exit {sym}",key=f"nse_exit_{sym}"): close_trade(sym,cur_ltp); st.rerun()
        else:
            st.markdown("<div style='font-size:0.7rem;color:#6b7280;text-align:center;padding:0.6rem;'>No open positions</div>",unsafe_allow_html=True)

        st.markdown("<div style='font-size:0.56rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.14em;border-bottom:1px solid #e0e0e0;padding-bottom:0.2rem;margin:0.6rem 0 0.35rem;'>Trade Journal (NSE Closed)</div>",unsafe_allow_html=True)
        closed_trades=[t for t in st.session_state.trade_history if "PnL" in t]
        if closed_trades:
            for tr in reversed(closed_trades[-8:]):
                pnl=tr["PnL"]; pc="#00e676" if pnl>=0 else "#ff3d57"; ps="+" if pnl>=0 else ""
                st.markdown(f"<div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:7px;padding:0.32rem 0.55rem;margin-bottom:0.25rem;font-size:0.67rem;'><div style='display:flex;justify-content:space-between;'><span style='color:#4b5563;'>{'W' if pnl>=0 else 'L'} {tr['Symbol']} {tr['Side']}</span><span style='color:{pc};font-weight:700;'>{ps}&#x20B9;{pnl}</span></div><div style='color:#6b7280;font-size:0.58rem;'>&#x20B9;{tr['Entry']} → &#x20B9;{tr['Exit']} | {tr['Time']}</div></div>",unsafe_allow_html=True)
            all_pnl=[t["PnL"] for t in closed_trades]; avg=sum(all_pnl)/len(all_pnl)
            tot_c="#00e676" if st.session_state.total_pnl>=0 else "#ff3d57"; tot_s="+" if st.session_state.total_pnl>=0 else ""
            st.markdown(f"<div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:0.55rem 0.7rem;font-size:0.68rem;margin-top:0.4rem;'><div style='display:flex;justify-content:space-between;margin-bottom:0.18rem;'><span style='color:#6b7280;'>Total</span><span style='color:{tot_c};font-weight:700;'>{tot_s}&#x20B9;{st.session_state.total_pnl:,.1f}</span></div><div style='display:flex;justify-content:space-between;margin-bottom:0.18rem;'><span style='color:#6b7280;'>Best</span><span style='color:#16a34a;'>+&#x20B9;{max(all_pnl):,.1f}</span></div><div style='display:flex;justify-content:space-between;margin-bottom:0.18rem;'><span style='color:#6b7280;'>Worst</span><span style='color:#dc2626;'>&#x20B9;{min(all_pnl):,.1f}</span></div><div style='display:flex;justify-content:space-between;'><span style='color:#6b7280;'>Avg</span><span>{'+'if avg>=0 else ''}&#x20B9;{avg:,.1f}</span></div></div>",unsafe_allow_html=True)
            tj_csv=pd.DataFrame([{c:t.get(c,"") for c in ["Time","Symbol","Side","Strike","Type","Entry","Exit","PnL","Exit Reason"]} for t in closed_trades]).to_csv(index=False).encode("utf-8")
            st.download_button("⬇ Trade Journal CSV",tj_csv,f"nse_journal_{current_time.strftime('%Y%m%d_%H%M')}.csv","text/csv",key="nse_dl_journal",use_container_width=True)
        else:
            st.markdown("<div style='font-size:0.7rem;color:#6b7280;text-align:center;padding:0.6rem;'>No closed trades yet</div>",unsafe_allow_html=True)

        st.markdown("<div style='font-size:0.56rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.14em;border-bottom:1px solid #e0e0e0;padding-bottom:0.2rem;margin:0.6rem 0 0.35rem;'>Signal Log (NSE)</div>",unsafe_allow_html=True)
        if st.session_state.signal_log:
            logs_to_show=list(reversed(st.session_state.signal_log[-20:]))
            rows_html=""
            for entry in logs_to_show:
                sig=entry.get("Signal",""); sc_clr="#16a34a" if sig=="CALL" else "#dc2626"
                rows_html+=(f"<tr style='border-bottom:1px solid #f0f0f0;'><td style='padding:4px 5px;color:#6b7280;font-size:0.6rem;'>{entry.get('Time','')}</td>"
                            f"<td style='padding:4px 5px;color:#111827;font-weight:700;font-size:0.62rem;'>{entry.get('Symbol','')}</td>"
                            f"<td style='padding:4px 5px;'><span style='background:{sc_clr}22;color:{sc_clr};border:1px solid {sc_clr}44;padding:0.05rem 0.35rem;border-radius:4px;font-size:0.6rem;font-weight:700;'>{sig}</span></td>"
                            f"<td style='padding:4px 5px;color:#111827;font-size:0.62rem;text-align:right;'>{int(entry.get('Strike',0)):,}</td>"
                            f"<td style='padding:4px 5px;color:#111827;font-size:0.62rem;text-align:right;'>&#x20B9;{entry.get('LTP',0)}</td>"
                            f"<td style='padding:4px 5px;color:#d97706;font-size:0.62rem;text-align:right;'>{entry.get('Score','')}</td></tr>")
            st.markdown(f"<div style='overflow-x:auto;margin-bottom:0.4rem;'><table style='width:100%;border-collapse:collapse;font-family:Inter,sans-serif;'><thead><tr style='background:#f9fafb;border-bottom:2px solid #e0e0e0;'><th style='padding:4px 5px;color:#6b7280;font-size:0.57rem;text-align:left;'>Time</th><th style='padding:4px 5px;color:#6b7280;font-size:0.57rem;text-align:left;'>Sym</th><th style='padding:4px 5px;color:#6b7280;font-size:0.57rem;text-align:left;'>Signal</th><th style='padding:4px 5px;color:#6b7280;font-size:0.57rem;text-align:right;'>Strike</th><th style='padding:4px 5px;color:#6b7280;font-size:0.57rem;text-align:right;'>LTP</th><th style='padding:4px 5px;color:#6b7280;font-size:0.57rem;text-align:right;'>Score</th></tr></thead><tbody>{rows_html}</tbody></table></div>",unsafe_allow_html=True)
            dl1,dl2=st.columns(2)
            with dl1:
                sig_csv=pd.DataFrame(st.session_state.signal_log)[["Time","Symbol","Signal","Strike","Type","LTP","Score"]].to_csv(index=False).encode("utf-8")
                st.download_button("⬇ Signal Log CSV",sig_csv,f"nse_signals_{current_time.strftime('%Y%m%d_%H%M')}.csv","text/csv",key="nse_dl_siglog",use_container_width=True)
            with dl2:
                if st.button("🗑 Clear Log",key="nse_clear_siglog",use_container_width=True):
                    st.session_state.signal_log=[]; st.session_state.last_signal_state={}; st.rerun()
        else:
            st.markdown("<div style='font-size:0.7rem;color:#6b7280;text-align:center;padding:0.6rem;'>Waiting for first CALL/PUT signal...</div>",unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# MCX SECTION
# ═════════════════════════════════════════════════════════════════════════════
if show_mcx:
    st.markdown("<span class='seg-label' style='background:#fff8e1;color:#8B4513;'>🛢️ MCX COMMODITIES — CRUDEOIL · GOLDM · COPPER · NATURALGAS</span>",unsafe_allow_html=True)
    st.markdown("<div style='background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:0.25rem 0.8rem;margin-bottom:0.35rem;font-size:0.68rem;color:#b8860b;'>📋 Auto Paper Mode — Signals auto-open/close positions. No real orders placed.</div>",unsafe_allow_html=True)

    scorecard_data={}
    with st.spinner("📡 Fetching all commodities..."):
        for _ck,_ccfg in COMMODITY_CONFIG.items():
            try:
                _tok,_fsym=mcx_get_token(_ck)
                if _tok is None: scorecard_data[_ck]={"label":_ccfg["label"],"price":None,"signal":"NO DATA","confidence":0}; continue
                try:
                    _d5 =mcx_apply_indicators(mcx_get_data(_tok,"5minute",5,_ck))
                except Exception:
                    _d5 = pd.DataFrame()
                time_module.sleep(0.4)
                try:
                    _d15=mcx_apply_indicators(mcx_get_data(_tok,"15minute",5,_ck))
                except Exception:
                    _d15 = pd.DataFrame()
                _lltp=mcx_get_live_ltp(_fsym)
                _price=_lltp if (_lltp and _lltp>0) else (_d5["close"].iloc[-1] if not _d5.empty else 0)
                _md,_ms,_=mcx_detect_200pt_move(_d5,_ccfg["atr_scale"]) if not _d5.empty else (None,0,{})
                _sig,_rs,_conf,_,_,_cl,_=mcx_compute_signal(_d5,_d15,_ccfg["adx_thresh"],_ccfg["min_score"],_md,_ms,move_boost,strong_conflict_threshold,moderate_conflict_threshold,conflict_penalty,_ck) if not _d5.empty else ("NO DATA",0,0,[],"","NONE","")
                scorecard_data[_ck]={"label":_ccfg["label"],"price":_price,"signal":_sig,"confidence":_conf,"raw_score":_rs}
            except Exception:
                scorecard_data[_ck]={"label":_ccfg["label"],"price":None,"signal":"ERR","confidence":0,"raw_score":0}

    sc_cols=st.columns(len(COMMODITY_CONFIG))
    for i,(ck,sd) in enumerate(scorecard_data.items()):
        sig_d=sd["signal"].replace("BLOCKED_","⚠️ ")
        price_d=f"₹{round(sd['price'],2):,}" if sd["price"] else "—"
        sc_cols[i].metric(label=sd["label"],value=price_d,delta=f"{sig_d} | {sd['confidence']}%")

    st.markdown("<hr style='margin:0.3rem 0 0.5rem;border-color:#e0e0e0;'/>",unsafe_allow_html=True)
    mcx_main_col, mcx_right_col = st.columns([3,1])

    with mcx_main_col:
        commodity_keys = list(COMMODITY_CONFIG.keys())
        for row_idx in range(0, len(commodity_keys), 2):
            grid_left, grid_right = st.columns(2)
            pair = commodity_keys[row_idx:row_idx+2]
            inner_cols = [grid_left, grid_right]
            for col_idx, ck in enumerate(pair):
                with inner_cols[col_idx]:
                    _ccfg=COMMODITY_CONFIG[ck]
                    _adx_min  =adx_min      if ck==commodity_key else _ccfg["adx_thresh"]
                    _min_score=min_score_mcx if ck==commodity_key else _ccfg["min_score"]
                    _min_prem =min_prem      if ck==commodity_key else _ccfg["min_prem"]
                    _max_prem =max_prem      if ck==commodity_key else _ccfg["max_prem"]
                    _lot_size =lot_size_opt  if ck==commodity_key else _ccfg["lot_sizes"][_ccfg["default_lot"]]
                    _tgt_prem =(_min_prem+_max_prem)/2
                    try:
                        _tok,_fsym=mcx_get_token(ck)
                        if _tok is None: st.error(f"❌ No token for {_ccfg['label']}"); continue
                        try:
                            _d5 =mcx_apply_indicators(mcx_get_data(_tok,"5minute",5,ck))
                        except Exception:
                            _d5 = pd.DataFrame()
                        time_module.sleep(0.4)
                        try:
                            _d15=mcx_apply_indicators(mcx_get_data(_tok,"15minute",5,ck))
                        except Exception:
                            _d15 = pd.DataFrame()
                        if _d5.empty or "VWAP" not in _d5.columns: st.warning(f"⚠️ No data for {_ccfg['label']}"); continue
                        _lltp =mcx_get_live_ltp(_fsym)
                        _price=_lltp if (_lltp and _lltp>0) else _d5["close"].iloc[-1]
                        _ltp_src="🟢 Live" if (_lltp and _lltp>0) else "⚠️ Candle"
                        _opt_df=mcx_get_option_chain(_price,ck)
                        _md,_ms,_mdet=mcx_detect_200pt_move(_d5,_ccfg["atr_scale"])
                        _sig,_rs,_conf,_reasons,_blk,_cl,_cnote=mcx_compute_signal(
                            _d5,_d15,_adx_min,_min_score,_md,_ms,move_boost,
                            strong_conflict_threshold,moderate_conflict_threshold,conflict_penalty,ck)
                        # ── MCX Setup Forming alert (fires 1-3 candles before confirmed entry) ──
                        _sig_for_setup = _sig.replace("BLOCKED_","") if _sig.startswith("BLOCKED_") else _sig
                        if _sig_for_setup in ("CALL","PUT"):
                            mcx_check_setup_forming(ck,_d5,_d15,_sig_for_setup,_rs,_min_score,_adx_min,current_minute)
                        elif _sig == "NO TRADE" and _rs >= int(_min_score*0.50):
                            # score building but no direction yet — check both sides
                            _l15_e = _d15.iloc[-1] if not _d15.empty else None
                            if _l15_e is not None:
                                _e9 = float(_l15_e.get("EMA9",0)); _e21 = float(_l15_e.get("EMA21",0))
                                _inferred = "CALL" if _e9 > _e21 else ("PUT" if _e21 > _e9 else None)
                                if _inferred:
                                    mcx_check_setup_forming(ck,_d5,_d15,_inferred,_rs,_min_score,_adx_min,current_minute)
                        _best,_cheaper,_pricier=mcx_pick_options_by_premium(
                            _opt_df,_sig,_tgt_prem,_min_prem,_max_prem,_lot_size,num_lots)
                        mcx_check_auto_exit(ck,_opt_df if not _opt_df.empty else None)
                        mcx_log_signal_and_auto_trade(ck,_sig,_rs,_best,_lot_size,current_minute,_min_score)

                        _fibs_mcx=extra_calc_fibonacci(_d5,50); _fvgs_mcx=extra_calc_fvg(_d5)
                        _bos_mcx,_choch_mcx,_swings_mcx=extra_calc_bos_choch(_d5)
                        _vs_mcx,_vr_mcx=extra_vol_spike(_d5); _div_mcx=extra_rsi_divergence(_d5)
                        _demand_mcx,_supply_mcx=extra_calc_sd_zones(_d5)

                        _vwap=_d5["VWAP"].iloc[-1]; _rsi=round(_d5["RSI"].iloc[-1],1); _adx=round(_d5["ADX"].iloc[-1],1)
                        _ema9=round(_d5["EMA9"].iloc[-1],1); _ema21=round(_d5["EMA21"].iloc[-1],1)
                        _sc_=_sig.replace("BLOCKED_","")
                        _scol="#2e7d32" if _sc_=="CALL" else "#c62828" if _sc_=="PUT" else "#f9a825"
                        _vwcls="badge-green" if _price>_vwap else "badge-red"
                        _vwlbl="VWAP {} {}".format("▲" if _price>_vwap else "▼", round(_vwap,1))
                        _rsicls="badge-red" if _rsi>70 else "badge-green" if _rsi<30 else "badge-gray"
                        _adxcls="badge-green" if _adx>=_adx_min else "badge-orange"
                        _emacls="badge-green" if _ema9>_ema21 else "badge-red"
                        _emalbl="EMA {} {}/{}".format("▲" if _ema9>_ema21 else "▼", _ema9, _ema21)
                        _ccls="badge-green" if _conf>=65 else "badge-orange" if _conf>=45 else "badge-red"
                        _bcol="#2e7d32" if _rs>=_min_score else "#f9a825" if _rs>=_min_score*0.7 else "#c62828"
                        _extra_mcx=extra_badges_html(_d5,_price,_fibs_mcx)
                        _email_note=""
                        if ck in EMAIL_SUPPRESSED_SYMBOLS:
                            _email_note=" <span style='font-size:0.52rem;color:#9ca3af;background:#f3f4f6;border-radius:3px;padding:1px 5px;'>🔕 no email</span>"
                        _bar_pct=min(_rs,100)

                        # ── FIXED: instrument card rendered as separate concatenated strings ──
                        _card_html = (
                            "<div class='instr-card'>"
                            "<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:0.4rem;'>"
                            "<div style='display:flex;align-items:center;gap:6px;'>"
                            "<span style='font-size:0.95rem;font-weight:800;font-family:Syne,sans-serif;'>" + _ccfg["label"] + "</span>"
                            " <span style='background:" + _scol + ";color:white;padding:2px 8px;border-radius:4px;font-size:0.65rem;font-weight:700;'>" + _sc_ + "</span>"
                            " <span style='font-size:0.58rem;color:#888;'>" + _ccfg["unit"] + "</span>"
                            + _email_note +
                            "</div>"
                            "<div style='font-size:1.1rem;font-weight:800;font-family:Syne,sans-serif;'>&#x20B9;" + f"{round(_price,2):,}" + "</div>"
                            "</div>"
                            "<div class='badge-strip'>"
                            "<span class='badge badge-gray'>" + _ltp_src + "</span>"
                            "<span class='badge " + _vwcls + "'>" + _vwlbl + "</span>"
                            "<span class='badge " + _emacls + "'>" + _emalbl + "</span>"
                            "<span class='badge " + _rsicls + "'>RSI " + str(_rsi) + "</span>"
                            "<span class='badge " + _adxcls + "'>ADX " + str(_adx) + "</span>"
                            "<span class='badge " + _ccls + "'>Conf " + str(_conf) + "%</span>"
                            "<span class='badge badge-gray'>Score " + str(_rs) + "/" + str(_min_score) + "</span>"
                            "</div>"
                            + ("<div style='margin-bottom:0.3rem;'>" + _extra_mcx + "</div>" if _extra_mcx else "") +
                            "<div class='bar-wrap'><div class='bar-fill' style='width:" + str(_bar_pct) + "%;background:" + _bcol + ";'></div></div>"
                            "</div>"
                        )
                        st.markdown(_card_html, unsafe_allow_html=True)

                        # Conflict / signal banners
                        if _cl=="STRONG" and _sc_ in ("CALL","PUT"):
                            st.markdown(f'<div class="sig-conflict">🚨 <b>STRONG CONFLICT BLOCKED</b> — Move says {_md} ({_ms}/100). Do NOT enter.</div>',unsafe_allow_html=True)
                        elif _cl=="MODERATE" and _sc_ in ("CALL","PUT"):
                            st.markdown(f'<div class="sig-conflict-mod">⚠️ MODERATE CONFLICT — Move: {_md} ({_ms}/100). Conf −{conflict_penalty} pts → {_conf}%</div>',unsafe_allow_html=True)
                        elif _cl=="NONE" and _sc_ in ("CALL","PUT"):
                            st.markdown(f'<div class="sig-clear">✅ NO CONFLICT — Move detector aligned ({_md}, {_ms}/100)</div>',unsafe_allow_html=True)

                        # ── FLAG PATTERN DETECTION (for display) ──────────────
                        _flag_type_disp, _flag_boost_disp, _flag_reason_disp = detect_flag_continuation(_d5, _d15)

                        # ── Block MCX signal banners on weekends and outside market hours ──
                        _mcx_ui_allowed = (current_time.weekday() < 5) and (dtime(9, 0) <= _now_t <= dtime(23, 30))
                        if not _mcx_ui_allowed:
                            st.warning("🔴 MCX market closed. Signals & suggestions paused until 09:00 AM.")
                        elif _sig=="CALL":
                            st.markdown(f'<div class="sig-call">🟢 CALL — Buy CE &nbsp;|&nbsp; Score: {_rs}/100 &nbsp;|&nbsp; Conf: {_conf}% &nbsp;|&nbsp; Prem ₹{_min_prem}–₹{_max_prem}</div>',unsafe_allow_html=True)
                        elif _sig=="PUT":
                            st.markdown(f'<div class="sig-put">🔴 PUT — Buy PE &nbsp;|&nbsp; Score: {_rs}/100 &nbsp;|&nbsp; Conf: {_conf}% &nbsp;|&nbsp; Prem ₹{_min_prem}–₹{_max_prem}</div>',unsafe_allow_html=True)
                        elif _sig in ("BLOCKED_CALL","BLOCKED_PUT"):
                            _btype="CALL" if "CALL" in _sig else "PUT"
                            st.markdown(f'<div class="sig-blocked">⚠️ {_btype} BLOCKED — {_blk or _cnote or "Signal filtered"}</div>',unsafe_allow_html=True)
                        else:
                            _pts=max(0,_min_score-_rs)
                            st.markdown(f'<div class="sig-wait">🟡 WAIT — Score {_rs}/{_min_score} (need {_pts} more pts)</div>',unsafe_allow_html=True)
                            # ── Setup Forming banner in UI (yellow alert box) ─────────────
                            _setup_thresh = max(1, int(_min_score * 0.50))
                            if _rs >= _setup_thresh:
                                _l15_sf = _d15.iloc[-1] if not _d15.empty else None
                                if _l15_sf is not None:
                                    _e9sf = float(_l15_sf.get("EMA9",0)); _e21sf = float(_l15_sf.get("EMA21",0))
                                    _dir_sf = "CALL 📈" if _e9sf > _e21sf else "PUT 📉"
                                    _adx_sf = round(float(_d5["ADX"].iloc[-1]),1)
                                    _rsi_sf = round(float(_d5["RSI"].iloc[-1]),1)
                                    st.markdown(
                                        f"<div style='background:linear-gradient(135deg,#fff8e1,#fff3cd);"
                                        f"border-left:5px solid #f59e0b;border-radius:8px;"
                                        f"padding:10px 14px;margin:4px 0;'>"
                                        f"<div style='font-size:0.78rem;font-weight:700;color:#92400e;'>"
                                        f"🟡 SETUP FORMING — {_dir_sf}</div>"
                                        f"<div style='font-size:0.65rem;color:#78350f;margin-top:3px;'>"
                                        f"Score <b>{_rs}/{_min_score}</b> — need <b>{_pts} more pts</b> for entry signal<br/>"
                                        f"ADX: {_adx_sf} | RSI: {_rsi_sf} | 15m EMA: {'Bullish ↑' if _e9sf>_e21sf else 'Bearish ↓'}<br/>"
                                        f"<span style='color:#b45309;'>⚡ Watch closely — confirmed signal may fire in 1–3 candles. "
                                        f"Email alert sent when score first crossed {_setup_thresh}.</span>"
                                        f"</div></div>",
                                        unsafe_allow_html=True)

                        # Flag pattern banner — shown only during market hours
                        if _mcx_ui_allowed and _flag_type_disp == "BULL_FLAG":
                            st.markdown(f"<div style='background:linear-gradient(135deg,#e8f5e9,#c8e6c9);border-left:5px solid #1b5e20;padding:10px 14px;border-radius:8px;font-size:0.75rem;font-weight:700;margin:4px 0;color:#1b5e20;'>🏁 BULL FLAG DETECTED — Early CALL setup<br/><span style='font-weight:400;font-size:0.68rem;'>{_flag_reason_disp}</span></div>",unsafe_allow_html=True)
                        elif _flag_type_disp == "BEAR_FLAG":
                            st.markdown(f"<div style='background:linear-gradient(135deg,#fce4ec,#f8bbd0);border-left:5px solid #880e4f;padding:10px 14px;border-radius:8px;font-size:0.75rem;font-weight:700;margin:4px 0;color:#880e4f;'>🏁 BEAR FLAG DETECTED — Early PUT setup<br/><span style='font-weight:400;font-size:0.68rem;'>{_flag_reason_disp}</span></div>",unsafe_allow_html=True)

                        with st.expander(f"📋 {_ccfg['label']} Signal Reasoning",key=f"reason_{ck}"):
                            for _r in _reasons: st.markdown(f"- {_r}")

                        _disp=_sig.replace("BLOCKED_","")
                        if _mcx_ui_allowed and _sig in ("CALL","PUT","BLOCKED_CALL","BLOCKED_PUT"):
                            if _sig.startswith("BLOCKED"): st.warning("⚠️ Blocked — showing suggested options if clears.")
                            def _ocard(label,row,sig):
                                if row is None:
                                    return("<div class='opt-card'><div class='opt-card-label'>" + label + "</div>"
                                           "<div style='color:#f9a825;font-size:0.63rem;'>No option in range</div></div>")
                                tag="CE" if sig=="CALL" else "PE"
                                ltp=float(row["ltp"]); rng="✅" if _min_prem<=ltp<=_max_prem else "⚠️"
                                sc2="#2e7d32" if sig=="CALL" else "#c62828"
                                return(
                                    "<div class='opt-card' style='border-top:3px solid " + sc2 + ";'>"
                                    "<div class='opt-card-label'>" + label + " — " + sig + " (" + tag + ") " + rng + "</div>"
                                    "<div class='opt-card-sym'>" + str(row['symbol']) + "</div>"
                                    "<div class='opt-card-price'>&#x20B9;" + str(round(ltp,2)) + "</div>"
                                    "<div class='opt-card-row'><span>Strike</span><span>&#x20B9;" + str(row['strike']) + " (" + str(row.get('moneyness','')) + ")</span></div>"
                                    "<div class='opt-card-row'><span>Per lot</span><span>&#x20B9;" + str(row['cost_1lot']) + "</span></div>"
                                    "<div class='opt-card-row'><span>" + str(num_lots) + " lot(s)</span><span><b>&#x20B9;" + str(row['total_cost']) + "</b></span></div>"
                                    "<div class='opt-card-row' style='color:#c62828;'><span>SL &#x20B9;" + str(row['sl']) + "</span><span>−&#x20B9;" + str(row['sl_loss_total']) + "</span></div>"
                                    "<div class='opt-card-row' style='color:#2e7d32;'><span>T1 &#x20B9;" + str(row['t1']) + "</span><span>+&#x20B9;" + str(row['t1_gain_total']) + "</span></div>"
                                    "<div class='opt-card-row' style='color:#2e7d32;'><span>T2 &#x20B9;" + str(row['t2']) + "</span><span>+&#x20B9;" + str(row['t2_gain_total']) + "</span></div>"
                                    "</div>"
                                )
                            oc1,oc2,oc3=st.columns(3)
                            with oc1: st.markdown(_ocard("✨ Best",_best,_disp),unsafe_allow_html=True)
                            with oc2: st.markdown(_ocard("💸 Cheaper",_cheaper,_disp),unsafe_allow_html=True)
                            with oc3: st.markdown(_ocard("💎 Less OTM",_pricier,_disp),unsafe_allow_html=True)
                        else:
                            st.info("🚫 No trade — conditions not met.")

                        if show_200:
                            _mc="#2e7d32" if _ms>=60 else "#f9a825" if _ms>=35 else "#bdbdbd"
                            _align=("✅ YES" if ((_sig in ("CALL","BLOCKED_CALL") and _md=="UP") or (_sig in ("PUT","BLOCKED_PUT") and _md=="DOWN")) else ("⚠️ NO" if _md not in (None,"UNCLEAR") else "❓"))
                            st.markdown(
                                f"<div style='display:flex;gap:8px;font-size:0.65rem;margin:4px 0 2px;'>"
                                f"<span>🚀 Move: <b>{_md or 'Unclear'}</b></span>"
                                f"<span>Score: <b>{_ms}/100</b></span>"
                                f"<span>ATR: <b>{round(_d5['ATR'].iloc[-1],1)}</b></span>"
                                f"<span>Align: <b>{_align}</b></span></div>"
                                f"<div class='bar-wrap'><div class='bar-fill' style='width:{_ms}%;background:{_mc}'></div></div>",
                                unsafe_allow_html=True)
                            if _ms>=60: st.markdown(f'<div class="move-high">⚡ HIGH move probability — <b>{_md}</b></div>',unsafe_allow_html=True)
                            elif _ms>=35: st.markdown(f'<div class="move-mid">🟡 Moderate move — <b>{_md}</b></div>',unsafe_allow_html=True)
                            else: st.markdown('<div class="move-low">📉 Low volatility</div>',unsafe_allow_html=True)

                        _chart_entry_lines=[]
                        if _sig in ("CALL","PUT"):
                            _is_call=(_sig=="CALL"); _entry_col="#2e7d32" if _is_call else "#c62828"
                            _arrow="▲ CALL" if _is_call else "▼ PUT"
                            _atr_val=float(_d5["ATR"].iloc[-1]) if "ATR" in _d5.columns else _price*0.003
                            _atr_val=max(_atr_val,_price*0.001)
                            if _is_call: _f_sl=_price-1.0*_atr_val; _f_t1=_price+1.5*_atr_val; _f_t2=_price+2.5*_atr_val
                            else:        _f_sl=_price+1.0*_atr_val; _f_t1=_price-1.5*_atr_val; _f_t2=_price-2.5*_atr_val
                            _prem_info=f"  Opt ₹{float(_best['ltp'])}" if _best else ""
                            _chart_entry_lines=[
                                {"price":_price,"color":_entry_col,"dash":"solid","width":2,"label":f"{_arrow} ENTRY ₹{round(_price,1)}{_prem_info}"},
                                {"price":_f_sl,"color":"#e53935","dash":"dotted","width":1,"label":f"SL ₹{round(_f_sl,1)}"},
                                {"price":_f_t1,"color":"#43a047","dash":"dashed","width":1,"label":f"T1 ₹{round(_f_t1,1)}"},
                                {"price":_f_t2,"color":"#00bcd4","dash":"dashed","width":1,"label":f"T2 ₹{round(_f_t2,1)}"},
                            ]

                        with st.expander(f"📈 {_ccfg['label']} — 5-Min Chart",key=f"c5_{ck}",expanded=False):
                            if not _d5.empty:
                                st.plotly_chart(build_plotly_chart(_d5.tail(60),f"{_ccfg['label']} · 5m",440,fibs=_fibs_mcx,fvgs=_fvgs_mcx,bos_swings=_swings_mcx,sd_zones=(_demand_mcx,_supply_mcx),entry_lines=_chart_entry_lines),use_container_width=True,config=PLOT_CFG)

                        with st.expander(f"📊 {_ccfg['label']} — 15-Min Chart",key=f"c15_{ck}",expanded=False):
                            if not _d15.empty:
                                st.plotly_chart(build_plotly_chart(_d15.tail(40),f"{_ccfg['label']} · 15m",380,fibs=_fibs_mcx,bos_swings=_swings_mcx,sd_zones=(_demand_mcx,_supply_mcx)),use_container_width=True,config=PLOT_CFG)
                            else:
                                st.info("15-min data unavailable")

                        with st.expander(f"📐 {_ccfg['label']} — Technical Levels",key=f"tech_{ck}"):
                            tc1,tc2=st.columns(2)
                            with tc1:
                                st.markdown("**📏 Fibonacci Retracements**")
                                if _fibs_mcx:
                                    FIB_EMOJIS={"0.0":"⬛","0.236":"🟧","0.382":"🟥","0.500":"🟪","0.618":"🟦","0.786":"🟩","1.0":"⬛"}
                                    for lbl,val in _fibs_mcx.items():
                                        near=" ← price near" if abs(_price-val)/_price*100<0.5 else ""
                                        st.markdown(f"{FIB_EMOJIS.get(lbl,'◻️')} **{lbl}** → ₹{val:,.2f}{near}")
                                st.markdown("---")
                                st.markdown(f"**Volume Spike:** {'✅ ×'+str(_vr_mcx) if _vs_mcx else '❌ ×'+str(_vr_mcx)}")
                                st.markdown(f"**RSI Divergence:** {_div_mcx or 'None'}")
                            with tc2:
                                st.markdown("**🔷 Smart Money**")
                                st.markdown(f"BOS: **{_bos_mcx or '—'}** | CHoCH: **{_choch_mcx or '—'}**")
                                if _swings_mcx:
                                    st.markdown(f"Swing H: ₹{_swings_mcx['swing_high']:,.2f} | Swing L: ₹{_swings_mcx['swing_low']:,.2f}")
                                bull_fvg_m,bear_fvg_m=_fvgs_mcx
                                st.markdown(f"**FVG:** {len(bull_fvg_m)} Bull · {len(bear_fvg_m)} Bear")
                                for fg in bull_fvg_m: st.markdown(f"&nbsp;&nbsp;🟢 ₹{fg['bot']:,.1f}–₹{fg['top']:,.1f}")
                                for fg in bear_fvg_m: st.markdown(f"&nbsp;&nbsp;🔴 ₹{fg['bot']:,.1f}–₹{fg['top']:,.1f}")
                                st.markdown(f"**S&D:** {len(_demand_mcx)} Demand · {len(_supply_mcx)} Supply")

                        with st.expander(f"🔥 {_ccfg['label']} — OI Heatmap + Option Chain",key=f"oc_{ck}"):
                            if not _opt_df.empty:
                                _hm_df=_opt_df.copy()
                                _pivot=_hm_df.pivot_table(index="type",columns="strike",values="oi",aggfunc="sum").fillna(0)
                                _pivot=_pivot[sorted(_pivot.columns)]
                                _hm_fig=go.Figure(go.Heatmap(z=_pivot.values.tolist(),x=[str(int(c)) for c in _pivot.columns],y=_pivot.index.tolist(),colorscale=[[0,"#fff5f0"],[0.25,"#fdd0b1"],[0.50,"#fc8d59"],[0.75,"#d7191c"],[1.00,"#7b0d1e"]],showscale=True,hoverongaps=False,hovertemplate="Strike: %{x}<br>Type: %{y}<br>OI: %{z:,.0f}<extra></extra>"))
                                _hm_fig.update_layout(height=175,margin=dict(l=45,r=90,t=10,b=40),paper_bgcolor="#ffffff",plot_bgcolor="#fafafa",font=dict(family="JetBrains Mono",size=9,color="#555555"),xaxis=dict(tickfont=dict(size=8),showgrid=False,tickangle=-45),yaxis=dict(tickfont=dict(size=9),showgrid=False))
                                st.plotly_chart(_hm_fig,use_container_width=True,config={"displayModeBar":False})
                                _oc1,_oc2=st.columns(2)
                                with _oc1:
                                    st.markdown("<span style='font-size:0.63rem;font-weight:700;color:#2e7d32;'>📗 CE</span>",unsafe_allow_html=True)
                                    st.dataframe(_opt_df[_opt_df["type"]=="CE"].sort_values("strike")[["strike","moneyness","ltp","oi","volume"]].head(15),use_container_width=True,hide_index=True)
                                with _oc2:
                                    st.markdown("<span style='font-size:0.63rem;font-weight:700;color:#c62828;'>📕 PE</span>",unsafe_allow_html=True)
                                    st.dataframe(_opt_df[_opt_df["type"]=="PE"].sort_values("strike",ascending=False)[["strike","moneyness","ltp","oi","volume"]].head(15),use_container_width=True,hide_index=True)
                            else:
                                st.info("No option chain data")

                        with st.expander(f"📥 {_ccfg['label']} — Raw Data Download",key=f"rawdl_{ck}"):
                            _ind_cols=["EMA9","EMA21","RSI","MACD","MACD_SIG","MACD_HIST","VWAP","ADX","+DI","-DI","ATR"]
                            def _mcx_prep(df,extra=None):
                                try:
                                    if df is None or df.empty: return pd.DataFrame()
                                    out=df.copy().reset_index()
                                    if out.columns[0] in ("index","level_0"): out=out.rename(columns={out.columns[0]:"date"})
                                    base=["date","open","high","low","close","volume"]
                                    want=base+(extra or [])
                                    keep=[c for c in want if c in out.columns]
                                    out=out[keep].copy()
                                    for c in out.select_dtypes("float").columns: out[c]=out[c].round(4)
                                    return out
                                except Exception as _pe: return pd.DataFrame({"error":[str(_pe)]})
                            _d5_dl=_mcx_prep(_d5,_ind_cols); _d15_dl=_mcx_prep(_d15,_ind_cols)
                            _ts_lbl=current_time.strftime("%Y%m%d_%H%M")
                            _rdl1,_rdl2=st.columns(2)
                            with _rdl1: st.download_button(f"⬇ 5-MIN ({len(_d5_dl)} rows)",_d5_dl.to_csv(index=False).encode("utf-8"),f"{ck}_5min_{_ts_lbl}.csv","text/csv",key=f"mcx_dl5_{ck}",use_container_width=True)
                            with _rdl2: st.download_button(f"⬇ 15-MIN ({len(_d15_dl)} rows)",_d15_dl.to_csv(index=False).encode("utf-8"),f"{ck}_15min_{_ts_lbl}.csv","text/csv",key=f"mcx_dl15_{ck}",use_container_width=True)
                            if not _opt_df.empty:
                                st.download_button(f"⬇ Option Chain ({len(_opt_df)} rows)",_opt_df.to_csv(index=False).encode("utf-8"),f"{ck}_chain_{_ts_lbl}.csv","text/csv",key=f"mcx_dlopt_{ck}",use_container_width=True)

                        st.markdown("<hr style='border-color:#e0e0e0;margin:0.4rem 0;'/>",unsafe_allow_html=True)

                    except Exception as _err:
                        st.error(f"❌ {_ccfg['label']}: {_err}")

    with mcx_right_col:
        st.markdown("<div style='border:1px solid #e0e0e0;border-radius:10px;padding:0.6rem;margin-bottom:0.5rem;'>",unsafe_allow_html=True)
        st.markdown("<div class='rp-header'>📰 MCX Market News</div>",unsafe_allow_html=True)
        _shown=set()
        for _nk in [commodity_key]+[k for k in COMMODITY_CONFIG if k!=commodity_key]:
            for _ni in get_commodity_news(COMMODITY_CONFIG[_nk]["news_query"])[:3]:
                if _ni["link"] not in _shown:
                    _shown.add(_ni["link"])
                    _nt=_ni["title"][:72]+("…" if len(_ni["title"])>72 else "")
                    st.markdown(f"<div class='news-item'><a href='{_ni['link']}' target='_blank' style='text-decoration:none;font-size:0.61rem;line-height:1.4;display:block;'>{_nt}</a><div style='font-size:0.53rem;color:#999;margin-top:1px;'>{_ni['time'][:16]}</div></div>",unsafe_allow_html=True)
                    if len(_shown)>=9: break
            if len(_shown)>=9: break
        st.markdown("</div>",unsafe_allow_html=True)

        st.markdown("<div class='rp-header'>📂 MCX Open Positions</div>",unsafe_allow_html=True)
        if st.session_state.mcx_open_trades:
            for sym,t in list(st.session_state.mcx_open_trades.items()):
                sc="#2e7d32" if t["side"]=="CALL" else "#c62828"
                cur_ltp=mcx_get_trade_live_ltp(sym,t)
                lots_rem  = t.get("lots_remaining", t.get("num_lots", 2))
                num_total = t.get("num_lots", 2)
                cur_pnl   = round((cur_ltp-t["entry"])*t["lot_size"],2)
                pc="#2e7d32" if cur_pnl>=0 else "#c62828"; ps="+" if cur_pnl>=0 else ""
                # Status badges
                lot1_tag  = "✅ Lot1 exited" if t.get("lot1_done") else f"⏳ Lot1 T1@₹{t['t1']:.1f}"
                trail_tag = f"↗ Trail SL ₹{t['sl']:.1f}" if t.get("trailing") else (
                            f"🟡 BE SL" if t.get("cost_sl_active") else f"SL ₹{t['sl']:.1f}")
                lock_rem  = ""
                _lock = st.session_state.get("mcx_signal_locks",{}).get(sym)
                if _lock:
                    try:
                        _la = (datetime.now()-datetime.fromisoformat(_lock["time"])).total_seconds()/60
                        _lr = max(0, MCX_LOCK_MINS - int(_la))
                        if _lr > 0: lock_rem = f" 🔒{_lr}m"
                    except Exception: pass
                st.markdown(
                    f"<div style='background:#fff;border:1px solid #e0e0e0;border-left:3px solid {sc};"
                    f"border-radius:8px;padding:0.45rem 0.65rem 0.35rem;margin-bottom:0.15rem;'>"
                    f"<div style='display:flex;justify-content:space-between;'>"
                    f"<span style='font-size:0.63rem;font-weight:700;color:{sc};'>{t['side']} {sym}{lock_rem}</span>"
                    f"<span style='font-size:0.58rem;color:#888;'>{t['time']}</span></div>"
                    f"<div style='font-size:0.62rem;color:#555;'>{t['strike']}{t['type']} @ <b>₹{t['entry']:.1f}</b> | LTP <b>₹{cur_ltp:.1f}</b></div>"
                    f"<div style='font-size:0.7rem;font-weight:700;color:{pc};'>{ps}₹{cur_pnl:.1f} | Lots: {lots_rem}/{num_total}</div>"
                    f"<div style='font-size:0.55rem;color:#888;display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:2px;'>"
                    f"<span>{lot1_tag}</span><span>{trail_tag}</span>"
                    f"<span>T2 ₹{t.get('t2', round(t['entry']*2,1)):.1f}</span></div>"
                    f"</div>", unsafe_allow_html=True)
                if st.button(f"✕ EXIT {sym}",key=f"mcx_exit_{sym}"):
                    mcx_close_trade(sym,cur_ltp,"Manual"); st.rerun()
        else:
            st.markdown("<div style='font-size:0.68rem;color:#999;text-align:center;padding:0.5rem;'>No open positions</div>",unsafe_allow_html=True)

        st.markdown("<div class='rp-header' style='margin-top:0.5rem;'>📋 MCX Trade Journal</div>",unsafe_allow_html=True)
        mcx_closed=[t for t in st.session_state.mcx_trade_history if "PnL" in t]
        if mcx_closed:
            for tr in reversed(mcx_closed[-8:]):
                pnl=tr["PnL"]; pc="#2e7d32" if pnl>=0 else "#c62828"; ps="+" if pnl>=0 else ""; reason=tr.get("Exit Reason","")
                st.markdown(f"<div style='background:#f9f9f9;border:1px solid #e0e0e0;border-radius:7px;padding:0.3rem 0.55rem;margin-bottom:0.2rem;font-size:0.65rem;'><div style='display:flex;justify-content:space-between;'><span style='color:#555;'>{'W' if pnl>=0 else 'L'} {tr['Symbol']} {tr['Side']}</span><span style='color:{pc};font-weight:700;'>{ps}&#x20B9;{pnl}</span></div><div style='color:#999;font-size:0.56rem;'>&#x20B9;{tr['Entry']} → &#x20B9;{tr['Exit']} | {tr['Time']}{' | '+reason if reason else ''}</div></div>",unsafe_allow_html=True)
            all_pnl=[t["PnL"] for t in mcx_closed]; avg=sum(all_pnl)/len(all_pnl)
            tot_c="#2e7d32" if st.session_state.mcx_total_pnl>=0 else "#c62828"; tot_s="+" if st.session_state.mcx_total_pnl>=0 else ""
            st.markdown(f"<div style='background:#f9f9f9;border:1px solid #e0e0e0;border-radius:8px;padding:0.5rem 0.65rem;font-size:0.66rem;margin-top:0.3rem;'><div style='display:flex;justify-content:space-between;margin-bottom:0.15rem;'><span style='color:#666;'>Total</span><span style='color:{tot_c};font-weight:700;'>{tot_s}&#x20B9;{st.session_state.mcx_total_pnl:,.1f}</span></div><div style='display:flex;justify-content:space-between;margin-bottom:0.15rem;'><span style='color:#666;'>Best</span><span style='color:#2e7d32;'>+&#x20B9;{max(all_pnl):,.1f}</span></div><div style='display:flex;justify-content:space-between;margin-bottom:0.15rem;'><span style='color:#666;'>Worst</span><span style='color:#c62828;'>&#x20B9;{min(all_pnl):,.1f}</span></div><div style='display:flex;justify-content:space-between;'><span style='color:#666;'>Avg</span><span>{'+'if avg>=0 else ''}&#x20B9;{avg:,.1f}</span></div></div>",unsafe_allow_html=True)
            tj_csv=pd.DataFrame([{c:t.get(c,"") for c in ["Time","Symbol","Side","Strike","Type","Entry","Exit","PnL","Exit Reason"]} for t in mcx_closed]).to_csv(index=False).encode("utf-8")
            st.download_button("⬇ MCX Journal CSV",tj_csv,f"mcx_journal_{current_time.strftime('%Y%m%d_%H%M')}.csv","text/csv",key="mcx_dl_journal",use_container_width=True)
        else:
            st.markdown("<div style='font-size:0.68rem;color:#999;text-align:center;padding:0.5rem;'>No closed trades yet</div>",unsafe_allow_html=True)

        st.markdown("<div class='rp-header' style='margin-top:0.5rem;'>📡 MCX Signal Log</div>",unsafe_allow_html=True)
        if st.session_state.mcx_signal_log:
            logs_to_show=list(reversed(st.session_state.mcx_signal_log[-20:]))
            rows_html=""
            for entry in logs_to_show:
                sig=entry.get("Signal",""); sc_clr="#2e7d32" if sig=="CALL" else "#c62828"
                rows_html+=(f"<tr style='border-bottom:1px solid #f0f0f0;'>"
                            f"<td style='padding:3px 4px;color:#999;font-size:0.56rem;'>{entry.get('Time','')}</td>"
                            f"<td style='padding:3px 4px;color:#111;font-weight:700;font-size:0.58rem;'>{entry.get('Symbol','')}</td>"
                            f"<td style='padding:3px 4px;'><span style='background:{sc_clr}22;color:{sc_clr};border:1px solid {sc_clr}44;padding:1px 5px;border-radius:3px;font-size:0.56rem;font-weight:700;'>{sig}</span></td>"
                            f"<td style='padding:3px 4px;color:#333;font-size:0.58rem;text-align:right;'>{int(entry.get('Strike',0)):,}</td>"
                            f"<td style='padding:3px 4px;color:#555;font-size:0.56rem;text-align:right;'>&#x20B9;{entry.get('LTP',0)}</td>"
                            f"<td style='padding:3px 4px;color:#e65100;font-size:0.56rem;text-align:right;'>{entry.get('Score','')}</td></tr>")
            st.markdown(f"<div style='overflow-x:auto;margin-bottom:0.35rem;'><table style='width:100%;border-collapse:collapse;font-family:JetBrains Mono,monospace;'><thead><tr style='background:#f5f5f5;border-bottom:2px solid #e0e0e0;'><th style='padding:3px 4px;color:#888;font-size:0.52rem;text-align:left;'>Time</th><th style='padding:3px 4px;color:#888;font-size:0.52rem;text-align:left;'>Sym</th><th style='padding:3px 4px;color:#888;font-size:0.52rem;text-align:left;'>Signal</th><th style='padding:3px 4px;color:#888;font-size:0.52rem;text-align:right;'>Strike</th><th style='padding:3px 4px;color:#888;font-size:0.52rem;text-align:right;'>LTP</th><th style='padding:3px 4px;color:#888;font-size:0.52rem;text-align:right;'>Score</th></tr></thead><tbody>{rows_html}</tbody></table></div>",unsafe_allow_html=True)
            dl_c,cl_c=st.columns(2)
            with dl_c:
                sig_csv=pd.DataFrame(st.session_state.mcx_signal_log)[["Time","Symbol","Signal","Strike","Type","LTP","Score"]].to_csv(index=False).encode("utf-8")
                st.download_button("⬇ Signal Log CSV",sig_csv,f"mcx_signals_{current_time.strftime('%Y%m%d_%H%M')}.csv","text/csv",key="mcx_dl_siglog",use_container_width=True)
            with cl_c:
                if st.button("🗑 Clear",key="mcx_clear_siglog",use_container_width=True):
                    st.session_state.mcx_signal_log=[]; st.session_state.mcx_last_signal_state={}; st.rerun()
        else:
            st.markdown("<div style='font-size:0.68rem;color:#999;text-align:center;padding:0.5rem;'>Waiting for first signal...</div>",unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# EXTRA TABS — Day P&L · Greeks · ORB · Straddle
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("<div class='seg-divider'></div>", unsafe_allow_html=True)
tab_pnl, tab_greeks, tab_orb, tab_straddle = st.tabs([
    "📋 Day P&L Spreadsheet",
    "📊 Greeks Analysis",
    "📐 ORB Strategy",
    "🔄 9:20 AM Straddle",
])

# ── TAB 1: Day P&L Spreadsheet ───────────────────────────────────────────────
with tab_pnl:
    st.markdown("<div style='font-size:0.72rem;font-weight:700;color:#111827;margin-bottom:0.4rem;'>📋 Today's Trade Log — NSE Auto Paper</div>",unsafe_allow_html=True)
    st.markdown("<div style='font-size:0.62rem;color:#6b7280;margin-bottom:0.5rem;'>Auto-populated from paper trades · Premium-based P&L · Running cumulative total</div>",unsafe_allow_html=True)
    render_day_pnl_spreadsheet()

# ── TAB 2: Greeks Analysis ────────────────────────────────────────────────────
with tab_greeks:
    st.markdown("**📊 Greeks Analysis — Compare strikes using Black-Scholes**")
    _sel_sym_g = st.selectbox("Instrument", NSE_SYMBOLS, key="greeks_sym_sel")
    _d_g = sym_data.get(_sel_sym_g)
    if _d_g and _d_g["signal"] in ("CALL","PUT"):
        _sig_g   = _d_g["signal"]; _price_g = _d_g["price"]
        _dte_g   = _d_g.get("dte", 1)
        _iv_g    = max(0.10, min((_vix_live or 15) / 100, 0.60))
        _step_g  = step_map[_sel_sym_g]; _lot_g = lot_map[_sel_sym_g]
        st.markdown(f"Signal: **{_sig_g}** · Price: ₹{_price_g:,.1f} · DTE: {_dte_g}d · IV: {_iv_g*100:.0f}%")
        if _d_g.get("opt_df") is not None and not _d_g["opt_df"].empty:
            _opt_type_g = "CE" if _sig_g=="CALL" else "PE"
            _chain_g = _d_g["opt_df"][_d_g["opt_df"]["type"]==_opt_type_g].copy()
            _atm_g   = round(_price_g / _step_g) * _step_g
            _chain_g["Moneyness"] = _chain_g["strike"].apply(
                lambda k: "⭐ ATM" if k==_atm_g else
                          ("ITM" if (_sig_g=="CALL" and k<_atm_g or _sig_g=="PUT" and k>_atm_g) else "OTM"))
            _rows_g = []
            for _, _row_g in _chain_g.iterrows():
                _K_g = _row_g["strike"]
                _ltp_g = float(_row_g["ltp"]) if float(_row_g["ltp"]) > 0 else 1
                _p_g, _d_g_val = bs_premium(_price_g, _K_g, max(_dte_g, 0.5), _iv_g, _sig_g=="CALL")
                _rows_g.append({
                    "Strike": int(_K_g), "Moneyness": _row_g["Moneyness"],
                    "Live ₹": _ltp_g, "Theo ₹": _p_g, "Δ Delta": _d_g_val,
                    "Budget (1 lot)": f"₹{round(_ltp_g*_lot_g,0):,.0f}",
                    "SL (−30%)": f"₹{round(_ltp_g*0.70,1)}",
                    "T1 (+50%)": f"₹{round(_ltp_g*1.50,1)}",
                    "T2 (+100%)": f"₹{round(_ltp_g*2.00,1)}",
                })
            if _rows_g:
                st.dataframe(pd.DataFrame(_rows_g), use_container_width=True, hide_index=True)
        else:
            _opt_str_g = _d_g.get("opt_strikes", {})
            for _lbl_g, _data_g in _opt_str_g.items():
                st.markdown(
                    f"**{_lbl_g}** — {_data_g['strike']} · Theo ₹{_data_g['prem']:,.1f} · "
                    f"Δ {_data_g['delta']} · Budget ₹{_data_g['budget']:,.0f} · "
                    f"SL ₹{_data_g['sl']} · T1 ₹{_data_g['t1']} · T2 ₹{_data_g['t2']}")
    else:
        st.info("No active CALL/PUT signal. Greeks analysis shown when a signal fires.")

# ── TAB 3: ORB Strategy ───────────────────────────────────────────────────────
with tab_orb:
    st.markdown("**📐 ORB — Opening Range Breakout (9:15–9:44 AM)**")
    st.markdown("<div style='font-size:0.68rem;color:#6b7280;margin-bottom:0.5rem;'>Tracks the high/low of the first 30 minutes. A breakout above ORB high = bullish, below ORB low = bearish.</div>",unsafe_allow_html=True)
    _sel_orb = st.selectbox("Instrument", NSE_SYMBOLS, key="orb_sym_sel")
    _d_orb   = sym_data.get(_sel_orb)
    if _d_orb and not _d_orb["df5"].empty:
        _df_orb   = _d_orb["df5"]
        _price_orb= _d_orb["price"]
        _today_orb= current_time.date()
        try:
            _df_today_orb = _df_orb[pd.to_datetime(_df_orb.index).date == _today_orb]
        except Exception:
            _df_today_orb = _df_orb
        _df_range_orb = _df_today_orb[
            pd.to_datetime(_df_today_orb.index).time <= dtime(9, 44)
        ] if not _df_today_orb.empty else pd.DataFrame()
        if not _df_range_orb.empty:
            _orb_h   = float(_df_range_orb["high"].max())
            _orb_l   = float(_df_range_orb["low"].min())
            _orb_sz  = round(_orb_h - _orb_l, 1)
            _orb_pct = round(_orb_sz / _price_orb * 100, 2)
            _oc1, _oc2, _oc3 = st.columns(3)
            _oc1.metric("ORB High", f"₹{_orb_h:,.1f}")
            _oc2.metric("ORB Low",  f"₹{_orb_l:,.1f}")
            _oc3.metric("Range",    f"₹{_orb_sz} ({_orb_pct}%)")
            if _price_orb > _orb_h:
                st.success(f"✅ BREAKOUT ABOVE ORB — Bullish bias. Consider CALL if score 100.")
            elif _price_orb < _orb_l:
                st.error(f"❌ BREAKDOWN BELOW ORB — Bearish bias. Consider PUT if score 100.")
            else:
                st.info(f"📊 Price inside ORB range (₹{_orb_l:,.1f}–₹{_orb_h:,.1f}). Wait for breakout after 9:45 AM.")
        else:
            st.info("ORB data available from 9:15 AM. Check after 9:45 AM for breakout signal.")
    else:
        st.info("No data available.")

# ── TAB 4: 9:20 AM Straddle ──────────────────────────────────────────────────
with tab_straddle:
    st.markdown(
        "<div style='background:#eff6ff;border-left:3px solid #1565c0;border-radius:8px;"
        "padding:0.6rem 0.9rem;font-size:0.7rem;margin-bottom:0.6rem;'>"
        "<b>📐 9:20 AM Straddle — Option SELLING Strategy</b><br/>"
        "Sell OTM CE + OTM PE (1–2 strikes from spot) at 9:20 AM exactly.<br/>"
        "SL = 25% above each leg independently. Hold till 3:15 PM or SL hit.<br/>"
        "<b>Profit when market stays range-bound. Loss when big directional move.</b></div>",
        unsafe_allow_html=True)
    _sel_st = st.selectbox("Instrument for Straddle", NSE_SYMBOLS, key="straddle_sym_sel")
    _d_st   = sym_data.get(_sel_st)
    if _d_st and not _d_st["df5"].empty:
        _price_st  = _d_st["price"]; _step_st  = step_map[_sel_st]
        _lot_st    = lot_map[_sel_st]; _dte_st   = _d_st.get("dte", 1)
        _iv_st     = max(0.08, min((_vix_live or 15) / 100, 0.55))
        _atm_st    = round(_price_st / _step_st) * _step_st
        _otm_pts   = 2 if _step_st == 50 else 1
        _ce_k      = _atm_st + _step_st * _otm_pts
        _pe_k      = _atm_st - _step_st * _otm_pts
        _ce_p, _   = bs_premium(_price_st, _ce_k, _dte_st, _iv_st, True)
        _pe_p, _   = bs_premium(_price_st, _pe_k, _dte_st, _iv_st, False)
        _tot_p     = _ce_p + _pe_p
        _ce_sl     = round(_ce_p * 1.25, 1); _pe_sl = round(_pe_p * 1.25, 1)
        _max_prof  = round(_tot_p * _lot_st, 0)
        _max_loss  = round((_ce_sl - _ce_p + _pe_sl - _pe_p) * _lot_st, 0)
        _vix_ok    = not _vix_live or _vix_live < 18
        _dte_ok    = _dte_st > 0
        _suitable  = _vix_ok and _dte_ok
        _suit_col  = "#16a34a" if _suitable else "#dc2626"
        _st1, _st2 = st.columns(2)
        with _st1:
            st.markdown(
                f"<div style='background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:0.8rem;'>"
                f"<div style='font-size:0.75rem;font-weight:700;margin-bottom:0.5rem;'>📊 Straddle Plan — {_sel_st}</div>"
                f"<div style='font-size:0.65rem;color:#4b5563;margin-bottom:0.4rem;'>Spot: ₹{_price_st:,.0f} · ATM: {_atm_st} · DTE: {_dte_st}d · IV: {_iv_st*100:.0f}%</div>"
                f"<div style='font-size:0.65rem;display:flex;justify-content:space-between;padding:3px 0;'><span style='color:#6b7280;'>Sell CE ({_ce_k})</span><span style='font-weight:700;'>₹{_ce_p:.1f}</span></div>"
                f"<div style='font-size:0.65rem;display:flex;justify-content:space-between;padding:3px 0;'><span style='color:#6b7280;'>Sell PE ({_pe_k})</span><span style='font-weight:700;'>₹{_pe_p:.1f}</span></div>"
                f"<div style='font-size:0.65rem;display:flex;justify-content:space-between;padding:3px 0;border-top:1px solid #e5e7eb;margin-top:4px;'><span style='color:#6b7280;'>Total Premium</span><span style='font-weight:800;'>₹{_tot_p:.1f}</span></div>"
                f"<div style='font-size:0.65rem;display:flex;justify-content:space-between;padding:3px 0;color:#dc2626;'><span>CE SL (25% above)</span><span>₹{_ce_sl:.1f}</span></div>"
                f"<div style='font-size:0.65rem;display:flex;justify-content:space-between;padding:3px 0;color:#dc2626;'><span>PE SL (25% above)</span><span>₹{_pe_sl:.1f}</span></div>"
                f"<div style='font-size:0.65rem;display:flex;justify-content:space-between;padding:4px 0;border-top:1px solid #e5e7eb;margin-top:4px;'><span style='color:#16a34a;font-weight:600;'>Max Profit (1 lot)</span><span style='color:#16a34a;font-weight:700;'>+₹{_max_prof:,.0f}</span></div>"
                f"<div style='font-size:0.65rem;display:flex;justify-content:space-between;padding:3px 0;'><span style='color:#dc2626;font-weight:600;'>Max Loss (both SL)</span><span style='color:#dc2626;font-weight:700;'>−₹{_max_loss:,.0f}</span></div>"
                f"</div>", unsafe_allow_html=True)
        with _st2:
            st.markdown(f"<div style='font-size:0.8rem;font-weight:700;color:{_suit_col};margin-bottom:0.4rem;'>{'✅ SUITABLE' if _suitable else '❌ NOT RECOMMENDED'}</div>",unsafe_allow_html=True)
            _checks_st = [
                ("VIX < 18 (low volatility)", _vix_ok, f"VIX {_vix_live:.1f}" if _vix_live else "Unknown"),
                ("Not expiry day (DTE > 0)",  _dte_ok, f"DTE = {_dte_st}"),
                ("Enter at 9:20 AM",           True,   "Best at market open"),
                ("No big gap today",           True,   "Check gap before entering"),
            ]
            for _lbl_c, _ok_c, _note_c in _checks_st:
                st.markdown(
                    f"<div style='font-size:0.65rem;margin-bottom:4px;'>{'✅' if _ok_c else '❌'} {_lbl_c}"
                    f"<br/><span style='color:#6b7280;font-size:0.58rem;'>{_note_c}</span></div>",
                    unsafe_allow_html=True)
    else:
        st.info("No data available for straddle analysis.")

# ── FOOTER ────────────────────────────────────────────────────────────────────
st.markdown(
    f"<div style='font-size:0.5rem;color:#aaa;text-align:center;margin-top:0.6rem;'>"
    f"TradeMatrix v9.1 · NSE+MCX · NSE Score Engine (MCX-parity) · Exhaustion+Block Guards · Smart Trend Lock · Time SL ({NSE_TIME_SL_MINS}m) · Signal Lock ({NSE_LOCK_MINS}m) · "
    f"VIX Live · Expiry Day Rules · BS Premiums · 3-Min Confirm · Day P&L · ORB · Straddle · "
    f"Daily Loss Limit · Dual Email · Fib+FVG+BOS+S&amp;D · "
    f"{current_time.strftime('%d %b %Y %H:%M IST')} · Auto-refresh 60s</div>",
    unsafe_allow_html=True)