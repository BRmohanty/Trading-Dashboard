# ============================================================
# 🚀 BB Breakout Scanner v2.0
# Bollinger Band Upper Breakout — NSE F&O Stocks
# 100% Kite-powered — NO external website dependency
# ============================================================
#
# v2.0 ADDITIONS vs v1.0:
#   ✅ T1 → partial exit 50% qty + SL auto-trailed to breakeven
#   ✅ T2 → full exit of remaining qty
#   ✅ Auto trailing SL: after +35% move → SL moves to +15%
#   ✅ GTT OCO order on Kite exchange (SL survives browser close)
#   ✅ Paper Trade daily journal table (per-day, cumulative P&L)
#   ✅ Day Profit summary card — today's gross/net/win-rate
#   ✅ All SL/T1/T2 checked every 60s auto-refresh
#
# ROUTING:
#   CE budget ≤ ₹20k  → Buy CE option (bullish BB breakout)
#   CE budget  > ₹30k  → Buy equity stock (same signal)
#   Between 20–30k     → Show both, trader/auto picks
#
# SL/TARGET (Options on premium):
#   SL  = −25%  |  T1 = +50% (partial 50% exit + trail SL to BE)
#   T2  = +100% (full exit of remaining)
#   Auto-trail: if premium up +35% → SL moves to +15%
#
# SL/TARGET (Equity on LTP):
#   SL  = −2%   |  T1 = +3% (partial 50% exit + trail SL to BE)
#   T2  = +5%   (full exit of remaining)
#   Auto-trail: if LTP up +3.5% → SL moves to +1.5%
# ============================================================

import streamlit as st
from kiteconnect import KiteConnect
import pandas as pd
import numpy as np
import math, pytz, time as time_module
from datetime import datetime, time as dtime, timedelta
from streamlit_autorefresh import st_autorefresh
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from scipy.stats import norm as _NORM
    _SCIPY = True
except ImportError:
    _SCIPY = False

# ── KITE ──────────────────────────────────────────────────────────────────────
API_KEY      = "yxz1r4rzqt6ggzs1"
ACCESS_TOKEN = "3ttZ718NBg1RSnPIimiGUZzC9QcO09Zn"
kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# ── EMAIL ─────────────────────────────────────────────────────────────────────
EMAIL_SENDER   = "asit.mohanty12@gmail.com"
EMAIL_PASSWORD = "lnrx sufs ertc hmkb"
EMAIL_RECEIVER = "dba.asitmoha@gmail.com"

def send_email(subject, body, enabled=True):
    if not enabled: return
    try:
        html = (f"<html><body style='font-family:monospace;font-size:14px;"
                f"background:#f9f9f9;padding:16px;'>"
                f"<div style='border-left:4px solid #1565c0;padding-left:12px;'>"
                f"{body.replace(chr(10),'<br>')}</div>"
                f"<hr/><small style='color:#aaa;'>BB Breakout Scanner v2.0</small>"
                f"</body></html>")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            m = MIMEMultipart("alternative")
            m["Subject"] = subject
            m["From"]    = EMAIL_SENDER
            m["To"]      = EMAIL_RECEIVER
            m.attach(MIMEText(body, "plain"))
            m.attach(MIMEText(html,  "html"))
            s.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, m.as_string())
    except Exception as e:
        print(f"Email error: {e}")

# ── TIMEZONE ──────────────────────────────────────────────────────────────────
ist          = pytz.timezone("Asia/Kolkata")
current_time = datetime.now(ist)
_now_t       = current_time.time()
_today_str   = current_time.strftime("%Y-%m-%d")

# ── BB PARAMETERS ─────────────────────────────────────────────────────────────
BB_PERIOD    = 20
BB_STD       = 2
CANDLE_TF    = "5minute"
CANDLE_DAYS  = 5

# ── ROUTING THRESHOLDS ────────────────────────────────────────────────────────
OPT_BUDGET_MAX   = 20_000
OPT_BUDGET_LIMIT = 30_000
EQUITY_CAPITAL   = 50_000
EQUITY_MAX_QTY   = 1_000

# ── OPTIONS SL / TARGET (on premium) ─────────────────────────────────────────
SL_OPT          = 0.25   # −25%  → full exit
T1_OPT          = 0.50   # +50%  → partial exit 50% qty, trail SL to breakeven
T2_OPT          = 1.00   # +100% → full exit remaining qty
TRAIL_TRIGGER_OPT = 0.35  # if premium up +35% → trail SL to +15%
TRAIL_SL_OPT      = 0.15  # trailed SL level

# ── EQUITY SL / TARGET (on LTP) ───────────────────────────────────────────────
SL_EQ           = 0.02   # −2%
T1_EQ           = 0.03   # +3%   → partial exit 50%, trail SL to breakeven
T2_EQ           = 0.05   # +5%   → full exit remaining
TRAIL_TRIGGER_EQ  = 0.035
TRAIL_SL_EQ       = 0.015

# ── MARKET HOURS ─────────────────────────────────────────────────────────────
NSE_OPEN   = dtime(9, 15)
NSE_CLOSE  = dtime(15, 30)
NSE_CUTOFF = dtime(15,  0)

# ── SCAN LIMITS ───────────────────────────────────────────────────────────────
MAX_FO_STOCKS = 200
BATCH_SIZE    = 10
BATCH_SLEEP   = 0.35

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="BB Breakout Scanner v2.0", page_icon="📊")
st_autorefresh(interval=60000, key="bb_v2_refresh")

# ═══════════════════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════════════════
_ss = {
    "open_trades":     {},      # sym → trade dict
    "trade_history":   [],      # all closed trades (paper + live)
    "paper_journal":   {},      # date → [paper trade rows]
    "total_pnl":       0.0,
    "daily_pnl":       0.0,
    "paper_daily_pnl": 0.0,     # paper-only daily P&L
    "session_date":    "",
    "wins":            0,
    "losses":          0,
    "trades_today":    0,
    "paper_mode":      True,
    "auto_trade":      False,
    "email_on":        True,
}
for _k, _v in _ss.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

if st.session_state.session_date != _today_str:
    st.session_state.daily_pnl       = 0.0
    st.session_state.paper_daily_pnl = 0.0
    st.session_state.session_date    = _today_str

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');
html,body,.stApp{background:#f0f2f5!important;font-family:'Inter',sans-serif!important;color:#111827!important;}
h1,h2,h3{font-family:'Syne',sans-serif!important;}
header[data-testid="stHeader"],div[data-testid="stToolbar"],div[data-testid="stDecoration"],#MainMenu,footer{display:none!important;}
.main .block-container{padding:0.4rem 0.9rem 0.9rem!important;max-width:100%!important;}
[data-testid="metric-container"]{background:#fff!important;border:1px solid #e5e7eb!important;border-radius:10px!important;padding:0.5rem 0.7rem!important;}
[data-testid="metric-container"] label{font-size:0.58rem!important;color:#6b7280!important;text-transform:uppercase!important;font-weight:600!important;}
[data-testid="metric-container"] [data-testid="metric-value"]{font-size:0.95rem!important;font-weight:800!important;}
.stButton>button{background:#fff!important;border:1.5px solid #2563eb!important;color:#2563eb!important;font-size:0.75rem!important;font-weight:600!important;border-radius:7px!important;padding:0.3rem 0.8rem!important;transition:all 0.15s!important;}
.stButton>button:hover{background:#2563eb!important;color:#fff!important;}
.day-card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:0.8rem 1rem;margin-bottom:0.5rem;}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# INSTRUMENTS
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=86400, show_spinner=False)
def load_instruments():
    nse = pd.DataFrame(kite.instruments("NSE"))
    nfo = pd.DataFrame(kite.instruments("NFO"))
    return nse, nfo

try:
    nse_df, nfo_df = load_instruments()
    _INSTR_OK = True
except Exception as _ie:
    nse_df = nfo_df = pd.DataFrame()
    _INSTR_OK = False

@st.cache_data(ttl=86400, show_spinner=False)
def build_fo_stock_list():
    if nfo_df.empty or nse_df.empty:
        return []
    EXCLUDE = {"NIFTY","BANKNIFTY","SENSEX","FINNIFTY","MIDCPNIFTY",
               "NIFTY 50","NIFTY BANK","INDIA VIX"}
    fo_names = (nfo_df[nfo_df["segment"]=="NFO-OPT"]["name"]
                .dropna().unique().tolist())
    fo_names = sorted([n for n in fo_names if n not in EXCLUDE and "&" not in n])[:MAX_FO_STOCKS]
    nse_eq   = nse_df[nse_df["segment"]=="NSE"].copy() if "segment" in nse_df.columns else nse_df.copy()
    result   = []
    for name in fo_names:
        row = nse_eq[nse_eq["tradingsymbol"]==name]
        if row.empty: continue
        result.append({"symbol": name, "instrument_token": int(row.iloc[0]["instrument_token"])})
    return result

fo_stocks = build_fo_stock_list()

# ═══════════════════════════════════════════════════════════════════════════════
# CANDLES + BOLLINGER BANDS
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=55, show_spinner=False)
def fetch_candles(token: int, symbol: str) -> pd.DataFrame:
    try:
        now       = datetime.now()
        from_date = now - timedelta(days=CANDLE_DAYS)
        data      = kite.historical_data(token, from_date, now, CANDLE_TF)
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        df.set_index("date", inplace=True)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return pd.DataFrame()

def calc_bb(df: pd.DataFrame, period=BB_PERIOD, std=BB_STD) -> pd.DataFrame:
    if df.empty or len(df) < period: return df
    df = df.copy()
    df["BB_mid"]   = df["close"].rolling(period).mean()
    df["BB_std"]   = df["close"].rolling(period).std()
    df["BB_upper"] = df["BB_mid"] + std * df["BB_std"]
    df["BB_lower"] = df["BB_mid"] - std * df["BB_std"]
    df["BB_width"] = df["BB_upper"] - df["BB_lower"]
    return df

def detect_bb_breakout(df: pd.DataFrame) -> dict:
    if df.empty or "BB_upper" not in df.columns or len(df) < BB_PERIOD + 2:
        return {"signal": False}
    today = datetime.now(ist).date()
    try:
        mask   = pd.to_datetime(df.index).tz_localize(None).normalize() == pd.Timestamp(today)
        df_sig = df[mask] if mask.sum() >= 2 else df
    except Exception:
        df_sig = df
    if len(df_sig) < 2: return {"signal": False}

    latest = df_sig.iloc[-1]; prev = df_sig.iloc[-2]
    cn  = float(latest["close"]);  cp  = float(prev["close"])
    bun = float(latest["BB_upper"]) if not pd.isna(latest["BB_upper"]) else 0
    bup = float(prev["BB_upper"])   if not pd.isna(prev["BB_upper"])   else 0
    bm  = float(latest["BB_mid"])   if not pd.isna(latest["BB_mid"])   else 0
    bl  = float(latest["BB_lower"]) if not pd.isna(latest["BB_lower"]) else 0
    bw  = float(latest["BB_width"]) if not pd.isna(latest["BB_width"]) else 0

    return {
        "signal":      (cn > bun > 0) and (cp <= bup),
        "above_band":  (cn > bun > 0),
        "close":       round(cn, 2),
        "bb_upper":    round(bun, 2),
        "bb_mid":      round(bm, 2),
        "bb_lower":    round(bl, 2),
        "bb_width":    round(bw, 2),
        "candle_time": str(latest.name)[:16],
        "pct_above":   round((cn - bun) / bun * 100, 2) if bun > 0 else 0,
        "vol":         int(latest.get("volume", 0)),
    }

# ═══════════════════════════════════════════════════════════════════════════════
# EXPIRY / LOT / STEP
# ═══════════════════════════════════════════════════════════════════════════════
def get_expiry_info(symbol: str):
    if nfo_df.empty: return None, None, None
    try:
        today = pd.Timestamp(current_time.date())
        df    = nfo_df[(nfo_df["name"]==symbol) &
                       (nfo_df["segment"]=="NFO-OPT") &
                       (nfo_df["expiry"]>=today)]
        if df.empty: return None, None, None
        expiry   = df["expiry"].min()
        df_e     = df[df["expiry"]==expiry]
        lot_size = int(df_e.iloc[0]["lot_size"])
        strikes  = sorted(df_e["strike"].unique())
        step     = int(strikes[1]-strikes[0]) if len(strikes)>=2 else 10
        return expiry, lot_size, step
    except Exception:
        return None, None, None

def dte(expiry) -> int:
    if expiry is None: return 0
    return max((pd.Timestamp(expiry).date() - current_time.date()).days, 0)

# ═══════════════════════════════════════════════════════════════════════════════
# BLACK-SCHOLES
# ═══════════════════════════════════════════════════════════════════════════════
def bs_premium(S, K, dte_days, iv, is_call, r=0.065):
    try:
        if not _SCIPY: raise ValueError("no scipy")
        T  = max(dte_days, 0.5) / 365
        iv = max(0.05, min(iv, 2.0))
        d1 = (math.log(S/K) + (r + 0.5*iv**2)*T) / (iv*math.sqrt(T))
        d2 = d1 - iv*math.sqrt(T)
        if is_call:
            p   = max(1.0, round(S*_NORM.cdf(d1) - K*math.exp(-r*T)*_NORM.cdf(d2), 1))
            dlt = round(_NORM.cdf(d1), 2)
        else:
            p   = max(1.0, round(K*math.exp(-r*T)*_NORM.cdf(-d2) - S*_NORM.cdf(-d1), 1))
            dlt = round(abs(_NORM.cdf(d1)-1), 2)
        return p, dlt
    except Exception:
        return max(1.0, round(max(0, S-K)*0.3 + S*0.005, 1)), 0.5

# ═══════════════════════════════════════════════════════════════════════════════
# LIVE LTP
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=20, show_spinner=False)
def get_ltp_eq(symbol: str) -> float:
    try:
        q = kite.quote([f"NSE:{symbol}"])
        return float(q.get(f"NSE:{symbol}", {}).get("last_price", 0))
    except Exception:
        return 0.0

def get_ltp_opt(tradingsymbol: str) -> float:
    try:
        q = kite.quote([f"NFO:{tradingsymbol}"])
        return float(q.get(f"NFO:{tradingsymbol}", {}).get("last_price", 0))
    except Exception:
        return 0.0

# ═══════════════════════════════════════════════════════════════════════════════
# STOCK ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
def analyse(symbol: str, token: int, vix: float=15.0) -> dict:
    base = {
        "symbol":"","ltp":0.0,"bb":{},"expiry_str":"—","dte":0,
        "lot_size":None,"step":None,"atm":None,"opt_sym":None,
        "live_prem":0.0,"bs_prem":0.0,"prem":0.0,"delta":0.5,
        "budget_opt":0,"sl_prem":0.0,"t1_prem":0.0,"t2_prem":0.0,
        "trail_trigger_prem":0.0,"trail_sl_prem":0.0,
        "eq_qty":0,"budget_eq":0,"sl_eq":0.0,"t1_eq":0.0,"t2_eq":0.0,
        "trail_trigger_eq":0.0,"trail_sl_eq":0.0,
        "route":"NO_SIGNAL","route_reason":"","error":None,
    }
    base["symbol"] = symbol

    df = fetch_candles(token, symbol)
    if df.empty:
        base["error"] = "No candle data"; return base
    df  = calc_bb(df)
    bb  = detect_bb_breakout(df)
    base["bb"] = bb
    if not bb.get("signal"):
        base["route"] = "NO_SIGNAL"
        base["ltp"]   = bb.get("close", 0.0)
        return base

    ltp = get_ltp_eq(symbol)
    if ltp <= 0: ltp = bb.get("close", 0.0)
    base["ltp"] = ltp
    if ltp <= 0: return base

    eq_qty = max(1, min(EQUITY_MAX_QTY, int(EQUITY_CAPITAL/ltp)))
    base.update({
        "eq_qty":          eq_qty,
        "budget_eq":       int(round(ltp*eq_qty, 0)),
        "sl_eq":           round(ltp*(1-SL_EQ), 2),
        "t1_eq":           round(ltp*(1+T1_EQ), 2),
        "t2_eq":           round(ltp*(1+T2_EQ), 2),
        "trail_trigger_eq":round(ltp*(1+TRAIL_TRIGGER_EQ), 2),
        "trail_sl_eq":     round(ltp*(1+TRAIL_SL_EQ), 2),
    })

    expiry, lot_size, step = get_expiry_info(symbol)
    if expiry is None:
        base["route"]        = "EQUITY"
        base["route_reason"] = "Not in F&O → equity trade"
        return base

    _dte = dte(expiry)
    iv   = max(0.08, min(vix/100, 0.55))
    atm  = int(round(ltp/step)*step)
    base.update({"expiry_str":str(expiry)[:10],"dte":_dte,"lot_size":lot_size,"step":step,"atm":atm})

    try:
        today = pd.Timestamp(current_time.date())
        df_ce = nfo_df[(nfo_df["name"]==symbol) & (nfo_df["segment"]=="NFO-OPT") &
                       (nfo_df["instrument_type"]=="CE") & (nfo_df["expiry"]==expiry)].copy()
        if df_ce.empty:
            base["error"] = "No CE chain found"
        else:
            df_ce["diff"] = abs(df_ce["strike"]-atm)
            best   = df_ce.sort_values("diff").iloc[0]
            opt_sym= best["tradingsymbol"]
            atm_k  = int(best["strike"])
            base["opt_sym"] = opt_sym; base["atm"] = atm_k
            live_p = get_ltp_opt(opt_sym)
            bs_p, delta = bs_premium(ltp, atm_k, max(_dte,0.5), iv, True)
            prem   = live_p if live_p > 0 else bs_p
            budget_opt = int(round(prem*lot_size, 0))
            base.update({
                "live_prem":         live_p,
                "bs_prem":           bs_p,
                "prem":              prem,
                "delta":             delta,
                "budget_opt":        budget_opt,
                "sl_prem":           round(prem*(1-SL_OPT), 1),
                "t1_prem":           round(prem*(1+T1_OPT), 1),
                "t2_prem":           round(prem*(1+T2_OPT), 1),
                "trail_trigger_prem":round(prem*(1+TRAIL_TRIGGER_OPT), 1),
                "trail_sl_prem":     round(prem*(1+TRAIL_SL_OPT), 1),
            })
    except Exception as e:
        base["error"] = str(e)

    b = base["budget_opt"]
    if b <= 0:
        base["route"] = "EQUITY"; base["route_reason"] = "Premium unavailable → equity"
    elif b <= OPT_BUDGET_MAX:
        base["route"] = "OPTIONS"
        base["route_reason"] = f"CE budget ₹{b:,} ≤ ₹{OPT_BUDGET_MAX:,} → Buy CE"
    elif b > OPT_BUDGET_LIMIT:
        base["route"] = "EQUITY"
        base["route_reason"] = f"CE budget ₹{b:,} > ₹{OPT_BUDGET_LIMIT:,} → Buy equity"
    else:
        base["route"] = "OPTIONS_MARGINAL"
        base["route_reason"] = f"CE budget ₹{b:,} — marginal zone"
    return base

# ═══════════════════════════════════════════════════════════════════════════════
# FULL SCAN
# ═══════════════════════════════════════════════════════════════════════════════
def run_scan(vix: float, prog, status) -> list:
    signals = []; total = len(fo_stocks); done = 0
    for i in range(0, total, BATCH_SIZE):
        for stock in fo_stocks[i:i+BATCH_SIZE]:
            res = analyse(stock["symbol"], stock["instrument_token"], vix)
            if res["route"] in ("OPTIONS","OPTIONS_MARGINAL","EQUITY"):
                signals.append(res)
            done += 1
            prog.progress(done/total, text=f"Scanning {stock['symbol']} ({done}/{total})")
        if i+BATCH_SIZE < total:
            time_module.sleep(BATCH_SLEEP)
    status.markdown(
        f"<div style='font-size:0.7rem;color:#6b7280;'>✅ {total} stocks scanned · "
        f"{len(signals)} BB breakout signals · {current_time.strftime('%H:%M IST')}</div>",
        unsafe_allow_html=True)
    return signals

# ═══════════════════════════════════════════════════════════════════════════════
# KITE ORDER HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def _buy_opt(sym, qty, paper):
    if paper: return {"status":"PAPER","order_id":f"PAPER-{int(time_module.time())}"}
    try:
        oid = kite.place_order(variety=kite.VARIETY_REGULAR,exchange=kite.EXCHANGE_NFO,
            tradingsymbol=sym,transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,product=kite.PRODUCT_MIS,order_type=kite.ORDER_TYPE_MARKET)
        return {"status":"PLACED","order_id":oid}
    except Exception as e:
        return {"status":"ERROR","error":str(e)}

def _sell_opt(sym, qty, paper):
    if paper: return {"status":"PAPER","order_id":f"PAPER-{int(time_module.time())}"}
    try:
        oid = kite.place_order(variety=kite.VARIETY_REGULAR,exchange=kite.EXCHANGE_NFO,
            tradingsymbol=sym,transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,product=kite.PRODUCT_MIS,order_type=kite.ORDER_TYPE_MARKET)
        return {"status":"PLACED","order_id":oid}
    except Exception as e:
        return {"status":"ERROR","error":str(e)}

def _buy_eq(sym, qty, paper):
    if paper: return {"status":"PAPER","order_id":f"PAPER-{int(time_module.time())}"}
    try:
        oid = kite.place_order(variety=kite.VARIETY_REGULAR,exchange=kite.EXCHANGE_NSE,
            tradingsymbol=sym,transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,product=kite.PRODUCT_MIS,order_type=kite.ORDER_TYPE_MARKET)
        return {"status":"PLACED","order_id":oid}
    except Exception as e:
        return {"status":"ERROR","error":str(e)}

def _sell_eq(sym, qty, paper):
    if paper: return {"status":"PAPER","order_id":f"PAPER-{int(time_module.time())}"}
    try:
        oid = kite.place_order(variety=kite.VARIETY_REGULAR,exchange=kite.EXCHANGE_NSE,
            tradingsymbol=sym,transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,product=kite.PRODUCT_MIS,order_type=kite.ORDER_TYPE_MARKET)
        return {"status":"PLACED","order_id":oid}
    except Exception as e:
        return {"status":"ERROR","error":str(e)}

# ── GTT OCO (exchange-level SL+T1 — survives browser close) ──────────────────
def place_gtt(sym, exchange, qty, entry, sl, t1, paper):
    """
    Places a GTT OCO order on Kite — fires even if app is closed.
    OCO = One-Cancels-Other: whichever of SL or T1 triggers first,
    the other is cancelled automatically.
    """
    if paper: return None   # GTT not needed for paper trades
    try:
        gtt_id = kite.place_gtt(
            trigger_type=kite.GTT_TYPE_OCO,
            tradingsymbol=sym,
            exchange=exchange,
            trigger_values=[round(sl, 1), round(t1, 1)],
            last_price=entry,
            orders=[
                {"transaction_type": kite.TRANSACTION_TYPE_SELL,
                 "quantity": qty, "price": round(sl*0.99, 1),
                 "order_type": kite.ORDER_TYPE_LIMIT,
                 "product": kite.PRODUCT_MIS},
                {"transaction_type": kite.TRANSACTION_TYPE_SELL,
                 "quantity": qty, "price": round(t1*1.01, 1),
                 "order_type": kite.ORDER_TYPE_LIMIT,
                 "product": kite.PRODUCT_MIS},
            ]
        )
        return gtt_id
    except Exception as e:
        print(f"GTT error for {sym}: {e}")
        return None

def cancel_gtt(gtt_id):
    if gtt_id is None: return
    try: kite.delete_gtt(gtt_id)
    except Exception: pass

# ═══════════════════════════════════════════════════════════════════════════════
# PAPER TRADE JOURNAL HELPER
# ═══════════════════════════════════════════════════════════════════════════════
def _log_paper_trade(row: dict):
    """
    Add a closed paper trade to the daily paper journal.
    row must have: symbol, type, entry, exit_px, qty, pnl, reason, time, strike, expiry
    """
    date = _today_str
    if date not in st.session_state.paper_journal:
        st.session_state.paper_journal[date] = []

    running = sum(r.get("P&L ₹", 0) for r in st.session_state.paper_journal[date])
    running += row.get("P&L ₹", 0)

    st.session_state.paper_journal[date].append({
        "#":          len(st.session_state.paper_journal[date]) + 1,
        "Time":       row.get("time", "—"),
        "Symbol":     row.get("symbol", "—"),
        "Type":       row.get("type", "—"),
        "Strike":     row.get("strike", "—"),
        "Expiry":     row.get("expiry", "—"),
        "Entry ₹":    row.get("entry", 0),
        "Exit ₹":     row.get("exit_px", 0),
        "Qty":        row.get("qty", 0),
        "P&L ₹":      row.get("P&L ₹", 0),
        "Running ₹":  round(running, 2),
        "Reason":     row.get("reason", "—"),
        "Partial":    "Yes" if row.get("partial") else "No",
    })
    st.session_state.paper_daily_pnl += row.get("P&L ₹", 0)

# ═══════════════════════════════════════════════════════════════════════════════
# TRADE ENTRY
# ═══════════════════════════════════════════════════════════════════════════════
def enter_trade(sym: str, res: dict, force_route: str="", paper: bool=True):
    if sym in st.session_state.open_trades: return
    route = force_route or res["route"]
    if route not in ("OPTIONS","OPTIONS_MARGINAL","EQUITY"): return

    if route in ("OPTIONS","OPTIONS_MARGINAL") and res.get("opt_sym"):
        entry_px = res["prem"]; qty = res["lot_size"]
        resp     = _buy_opt(res["opt_sym"], qty, paper)
        gtt_id   = place_gtt(res["opt_sym"],"NFO",qty,entry_px,res["sl_prem"],res["t1_prem"],paper)
        trade = {
            "type":"CE_OPTION","symbol":sym,"opt_sym":res["opt_sym"],
            "entry":entry_px,
            "sl":res["sl_prem"],"t1":res["t1_prem"],"t2":res["t2_prem"],
            "trail_trigger":res["trail_trigger_prem"],
            "trail_sl":     res["trail_sl_prem"],
            "qty":qty,"qty_remaining":qty,"budget":res["budget_opt"],
            "strike":res["atm"],"expiry":res["expiry_str"],
            "t1_done":False,"trailed":False,
            "gtt_id":gtt_id,"order":resp,
            "entry_time":current_time.strftime("%H:%M"),
            "entry_iso":datetime.now().isoformat(),
            "date":_today_str,"mode":"📄 Paper" if paper else "⚡ Live",
        }
    else:
        entry_px = res["ltp"]; qty = res["eq_qty"]
        resp     = _buy_eq(sym, qty, paper)
        gtt_id   = place_gtt(sym,"NSE",qty,entry_px,res["sl_eq"],res["t1_eq"],paper)
        trade = {
            "type":"EQUITY","symbol":sym,"opt_sym":None,
            "entry":entry_px,
            "sl":res["sl_eq"],"t1":res["t1_eq"],"t2":res["t2_eq"],
            "trail_trigger":res["trail_trigger_eq"],
            "trail_sl":     res["trail_sl_eq"],
            "qty":qty,"qty_remaining":qty,"budget":res["budget_eq"],
            "strike":"—","expiry":"—",
            "t1_done":False,"trailed":False,
            "gtt_id":gtt_id,"order":resp,
            "entry_time":current_time.strftime("%H:%M"),
            "entry_iso":datetime.now().isoformat(),
            "date":_today_str,"mode":"📄 Paper" if paper else "⚡ Live",
        }

    st.session_state.open_trades[sym] = trade
    st.session_state.trades_today    += 1

    if paper:
        _log_paper_trade({
            "time":trade["entry_time"],"symbol":sym,"type":trade["type"],
            "strike":trade["strike"],"expiry":trade["expiry"],
            "entry":entry_px,"exit_px":0,"qty":qty,"P&L ₹":0,
            "reason":"ENTRY","partial":False,
        })

    send_email(
        f"📈 ENTRY {sym} ({trade['type']}) ₹{entry_px:.1f}",
        f"Symbol:{sym} | Route:{trade['type']} | Entry:₹{entry_px:.1f}\n"
        f"Qty:{qty} | Budget:₹{trade['budget']:,}\n"
        f"SL:₹{trade['sl']:.1f} | T1:₹{trade['t1']:.1f} | T2:₹{trade['t2']:.1f}\n"
        f"GTT:{'placed' if gtt_id else 'paper/failed'} | "
        f"{'PAPER' if paper else 'LIVE'} | {current_time.strftime('%H:%M IST')}",
        enabled=st.session_state.email_on
    )

# ═══════════════════════════════════════════════════════════════════════════════
# PARTIAL EXIT (at T1 — 50% qty)
# ═══════════════════════════════════════════════════════════════════════════════
def partial_exit(sym: str, cur_price: float, reason: str, paper: bool):
    """
    Exit 50% of remaining qty at T1.
    Trail SL to breakeven after partial exit.
    Cancel existing GTT and place new one with updated SL=entry.
    """
    t = st.session_state.open_trades.get(sym)
    if not t or t["t1_done"]: return

    half_qty = max(1, t["qty_remaining"] // 2)

    if t["type"] == "CE_OPTION" and t.get("opt_sym"):
        _sell_opt(t["opt_sym"], half_qty, paper)
    else:
        _sell_eq(sym, half_qty, paper)

    pnl = round((cur_price - t["entry"]) * half_qty, 2)

    # Update trade state
    st.session_state.open_trades[sym]["qty_remaining"]  -= half_qty
    st.session_state.open_trades[sym]["t1_done"]         = True
    st.session_state.open_trades[sym]["sl"]              = t["entry"]   # trail to BE

    # Cancel old GTT, place new one for remaining qty with SL=entry
    cancel_gtt(t.get("gtt_id"))
    new_gtt = place_gtt(
        t.get("opt_sym") or sym,
        "NFO" if t["type"]=="CE_OPTION" else "NSE",
        t["qty_remaining"] - half_qty,
        t["entry"], t["entry"], t["t2"], paper
    )
    st.session_state.open_trades[sym]["gtt_id"] = new_gtt

    # Update P&L
    st.session_state.total_pnl  += pnl
    st.session_state.daily_pnl  += pnl
    if pnl >= 0: st.session_state.wins   += 1
    else:        st.session_state.losses += 1

    # Log to trade history & paper journal
    _record_closed(t, sym, cur_price, half_qty, reason, partial=True, paper=paper)

    send_email(
        f"⚡ PARTIAL EXIT {sym} (+50%) P&L ₹{pnl:+,.0f}",
        f"Symbol:{sym} | Partial exit {half_qty} qty @ ₹{cur_price:.1f}\n"
        f"P&L:₹{pnl:+,.0f} | SL trailed to breakeven ₹{t['entry']:.1f}\n"
        f"Remaining qty:{t['qty_remaining']-half_qty} | Reason:{reason}",
        enabled=st.session_state.email_on
    )

# ═══════════════════════════════════════════════════════════════════════════════
# FULL EXIT
# ═══════════════════════════════════════════════════════════════════════════════
def full_exit(sym: str, cur_price: float, reason: str, paper: bool):
    """Exit all remaining qty. Cancel GTT."""
    t = st.session_state.open_trades.pop(sym, None)
    if not t: return 0

    qty = t["qty_remaining"]
    if t["type"] == "CE_OPTION" and t.get("opt_sym"):
        if cur_price <= 0: cur_price = get_ltp_opt(t["opt_sym"])
        if cur_price <= 0: cur_price = t["entry"]
        _sell_opt(t["opt_sym"], qty, paper)
    else:
        if cur_price <= 0: cur_price = get_ltp_eq(sym)
        if cur_price <= 0: cur_price = t["entry"]
        _sell_eq(sym, qty, paper)

    cancel_gtt(t.get("gtt_id"))

    pnl = round((cur_price - t["entry"]) * qty, 2)
    st.session_state.total_pnl  += pnl
    st.session_state.daily_pnl  += pnl
    if pnl >= 0: st.session_state.wins   += 1
    else:        st.session_state.losses += 1

    _record_closed(t, sym, cur_price, qty, reason, partial=False, paper=paper)

    send_email(
        f"🚪 EXIT {sym} P&L ₹{pnl:+,.0f} [{reason}]",
        f"Symbol:{sym} | Exit {qty} qty @ ₹{cur_price:.1f}\n"
        f"P&L:₹{pnl:+,.0f} | Entry:₹{t['entry']:.1f} | Reason:{reason}",
        enabled=st.session_state.email_on
    )
    return pnl

def _record_closed(t, sym, exit_px, qty, reason, partial, paper):
    """Write closed trade row to trade_history and paper_journal."""
    pnl = round((exit_px - t["entry"]) * qty, 2)
    row = {
        "Time":     t["entry_time"],
        "Symbol":   sym,
        "Type":     t["type"],
        "Strike":   t["strike"],
        "Expiry":   t["expiry"],
        "Entry ₹":  t["entry"],
        "Exit ₹":   exit_px,
        "Qty":      qty,
        "P&L ₹":    pnl,
        "Partial":  "Yes" if partial else "No",
        "Reason":   reason,
        "Mode":     t["mode"],
        "Date":     _today_str,
    }
    st.session_state.trade_history.append(row)
    if paper:
        _log_paper_trade({
            "time":t["entry_time"],"symbol":sym,"type":t["type"],
            "strike":t["strike"],"expiry":t["expiry"],
            "entry":t["entry"],"exit_px":exit_px,"qty":qty,
            "P&L ₹":pnl,"reason":reason,"partial":partial,
        })

# ═══════════════════════════════════════════════════════════════════════════════
# SL / T1 / T2 / TRAIL MONITOR  (runs every 60s)
# ═══════════════════════════════════════════════════════════════════════════════
def monitor_trade(sym: str, paper: bool):
    """
    Full SL/T1/T2/Trail logic for one open position.

    Flow:
      1. Get current price
      2. Auto trailing SL check (if not already trailed)
      3. SL check → full exit
      4. T2 check → full exit remaining
      5. T1 check (only if T1 not already done) → partial exit 50%
    """
    t = st.session_state.open_trades.get(sym)
    if not t: return

    # ── Get current price ────────────────────────────────────────────────────
    cur = (get_ltp_opt(t["opt_sym"])
           if t["type"]=="CE_OPTION" and t.get("opt_sym")
           else get_ltp_eq(sym))
    if cur <= 0: return

    entry = t["entry"]
    sl    = t["sl"]
    t1    = t["t1"]
    t2    = t["t2"]

    # ── 1. Auto trailing SL ──────────────────────────────────────────────────
    # If price has moved above trail_trigger and SL not yet trailed:
    #   Options:  if premium up +35% → move SL to +15%
    #   Equity:   if LTP up +3.5%   → move SL to +1.5%
    if not t["trailed"] and cur >= t["trail_trigger"]:
        new_sl = t["trail_sl"]
        st.session_state.open_trades[sym]["sl"]      = new_sl
        st.session_state.open_trades[sym]["trailed"] = True
        # Re-place GTT with new SL
        cancel_gtt(t.get("gtt_id"))
        new_gtt = place_gtt(
            t.get("opt_sym") or sym,
            "NFO" if t["type"]=="CE_OPTION" else "NSE",
            t["qty_remaining"], entry, new_sl, t2, paper
        )
        st.session_state.open_trades[sym]["gtt_id"] = new_gtt
        send_email(
            f"↗ TRAIL SL {sym} → ₹{new_sl:.1f}",
            f"Price ₹{cur:.1f} hit trail trigger ₹{t['trail_trigger']:.1f}\n"
            f"SL moved: ₹{sl:.1f} → ₹{new_sl:.1f}",
            enabled=st.session_state.email_on
        )
        sl = new_sl   # use updated SL for rest of checks

    # ── 2. SL hit → full exit ────────────────────────────────────────────────
    if cur <= sl:
        full_exit(sym, cur, f"SL hit ₹{sl:.1f}", paper)
        return

    # ── 3. T2 hit → full exit remaining ─────────────────────────────────────
    if cur >= t2:
        full_exit(sym, cur, f"T2 hit ₹{t2:.1f}", paper)
        return

    # ── 4. T1 hit (first time) → partial exit 50% + trail SL to BE ──────────
    if cur >= t1 and not t["t1_done"]:
        partial_exit(sym, cur, f"T1 hit ₹{t1:.1f}", paper)
        return

# ── manual exit wrapper ───────────────────────────────────────────────────────
def manual_exit(sym: str, paper: bool):
    t = st.session_state.open_trades.get(sym)
    if not t: return
    cur = (get_ltp_opt(t["opt_sym"])
           if t["type"]=="CE_OPTION" and t.get("opt_sym")
           else get_ltp_eq(sym))
    full_exit(sym, cur, "Manual Exit", paper)

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(
        "<div style='font-family:Syne,sans-serif;font-size:1.05rem;"
        "font-weight:800;margin-bottom:0.5rem;'>⚙️ Settings</div>",
        unsafe_allow_html=True
    )
    paper_mode = st.toggle("📄 Paper Trade Mode", value=st.session_state.paper_mode)
    st.session_state.paper_mode = paper_mode
    if paper_mode: st.success("Paper mode ON — no real orders")
    else:          st.error("⚡ LIVE — real Kite orders!")

    st.markdown("---")
    auto_trade = st.toggle("🤖 Auto-Trade on Signal", value=st.session_state.auto_trade)
    st.session_state.auto_trade = auto_trade
    max_open         = st.slider("Max simultaneous trades", 1, 20, 5)
    daily_loss_limit = st.number_input("Daily Loss Limit (₹)", value=-5000, step=500, max_value=0)

    st.markdown("---")
    st.markdown("**BB Parameters**")
    bb_period_inp = st.number_input("BB Period",  value=BB_PERIOD, min_value=5,  max_value=50, step=1)
    bb_std_inp    = st.number_input("BB Std Dev", value=float(BB_STD), min_value=1.0, max_value=3.0, step=0.1)
    vix_val       = st.number_input("India VIX",  value=15.0, min_value=5.0, max_value=80.0, step=0.5)

    st.markdown("---")
    st.markdown("**Budget (₹)**")
    opt_max   = st.number_input("Options Max",    value=OPT_BUDGET_MAX,   step=1000, min_value=5000)
    opt_limit = st.number_input("Equity Trigger", value=OPT_BUDGET_LIMIT, step=1000, min_value=opt_max+1000)

    st.markdown("---")
    mkt = "🟢 OPEN" if NSE_OPEN<=_now_t<=NSE_CLOSE else "🔴 CLOSED"
    st.markdown(f"**Market:** {mkt}")
    st.markdown(f"**F&O Stocks:** {len(fo_stocks)}")
    st.markdown(f"**Time:** `{current_time.strftime('%H:%M:%S IST')}`")

    st.markdown("---")
    email_on = st.toggle("📧 Email Alerts", value=True)
    st.session_state.email_on = email_on

    if st.button("🔄 Force Re-scan"):
        fetch_candles.clear(); get_ltp_eq.clear(); st.rerun()

    if st.button("🗑 Clear Today History"):
        st.session_state.trade_history = [
            t for t in st.session_state.trade_history if t.get("Date","")!=_today_str
        ]
        if _today_str in st.session_state.paper_journal:
            del st.session_state.paper_journal[_today_str]
        st.session_state.paper_daily_pnl = 0.0
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# HEADER + METRICS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown(
    "<div style='background:linear-gradient(90deg,#1565c0,#2563eb,#7c3aed);"
    "padding:0.65rem 1rem;border-radius:10px;margin-bottom:0.5rem;'>"
    "<span style='font-family:Syne,sans-serif;font-size:1.2rem;font-weight:800;color:#fff;'>"
    "📊 BB Breakout Scanner v2.0</span>"
    "<span style='font-size:0.65rem;color:#c7d2fe;margin-left:1rem;'>"
    "Bollinger Band (20,2) · NSE F&O · CE ≤₹20k · Equity >₹30k · "
    "T1 Partial+Trail · T2 Full Exit · GTT · Paper Journal · 100% Kite"
    "</span></div>",
    unsafe_allow_html=True
)

mc1,mc2,mc3,mc4,mc5,mc6,mc7 = st.columns(7)
mc1.metric("Daily P&L",       f"₹{st.session_state.daily_pnl:+,.0f}")
mc2.metric("Paper P&L Today", f"₹{st.session_state.paper_daily_pnl:+,.0f}")
mc3.metric("Total P&L",       f"₹{st.session_state.total_pnl:+,.0f}")
mc4.metric("Trades Today",    st.session_state.trades_today)
mc5.metric("Open Positions",  len(st.session_state.open_trades))
_wr = round(st.session_state.wins/max(st.session_state.wins+st.session_state.losses,1)*100,1)
mc6.metric("Win Rate",        f"{_wr}%")
mc7.metric("Mode",            "📄 Paper" if paper_mode else "⚡ Live")

_dl_blocked = st.session_state.daily_pnl <= daily_loss_limit
if _dl_blocked:
    st.error(f"🛑 Daily Loss Limit ₹{daily_loss_limit:,} hit — no new trades today")

# ── Run SL/T1/T2/Trail monitor for every open position ────────────────────────
for _sym in list(st.session_state.open_trades.keys()):
    monitor_trade(_sym, paper_mode)

# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════
tab_scan, tab_open, tab_paper, tab_hist = st.tabs([
    "🔍 BB Breakout Scan",
    f"📂 Open Positions ({len(st.session_state.open_trades)})",
    "📄 Paper Trade Journal",
    "📋 Full Trade History",
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — SCAN
# ─────────────────────────────────────────────────────────────────────────────
with tab_scan:
    st.markdown(
        "<div style='background:#eff6ff;border-left:3px solid #1565c0;"
        "border-radius:8px;padding:0.55rem 0.9rem;font-size:0.68rem;margin-bottom:0.5rem;'>"
        "<b>📐 Bollinger Band Upper Crossover — NSE F&O · 5-min Kite candles</b><br/>"
        "Signal: Latest 5m Close > BB Upper (20,2) AND Previous 5m Close ≤ BB Upper "
        "(fresh crossover today only)<br/>"
        "CE ≤₹20k · Equity >₹30k · "
        "T1=+50% partial 50%+trail to BE · T2=+100% full exit · "
        "SL=−25% full exit · Auto-trail: +35%→SL to +15%"
        "</div>",
        unsafe_allow_html=True
    )

    if not _INSTR_OK or not fo_stocks:
        st.error("Instruments not loaded. Check Kite API key/token.")
        st.stop()

    prog      = st.progress(0, text="Starting scan…")
    status_ph = st.empty()
    with st.spinner(""):
        results = run_scan(vix_val, prog, status_ph)
    prog.empty()

    if not results:
        st.info("No BB upper crossovers right now. Signals appear when 5m close breaks above BB upper band.")
        st.stop()

    _ord = {"OPTIONS":0,"EQUITY":1,"OPTIONS_MARGINAL":2}
    results.sort(key=lambda x: _ord.get(x["route"],9))

    n_opt  = sum(1 for r in results if r["route"]=="OPTIONS")
    n_eq   = sum(1 for r in results if r["route"]=="EQUITY")
    n_marg = sum(1 for r in results if r["route"]=="OPTIONS_MARGINAL")
    st.markdown(
        f"<div style='display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.4rem;'>"
        f"<span style='background:#e8f5e9;border:1px solid #2e7d32;color:#2e7d32;"
        f"border-radius:20px;padding:2px 10px;font-size:0.63rem;font-weight:700;'>🟢 CE Options: {n_opt}</span>"
        f"<span style='background:#e3f2fd;border:1px solid #1565c0;color:#1565c0;"
        f"border-radius:20px;padding:2px 10px;font-size:0.63rem;font-weight:700;'>🔵 Equity: {n_eq}</span>"
        f"<span style='background:#fff8e1;border:1px solid #f9a825;color:#b45309;"
        f"border-radius:20px;padding:2px 10px;font-size:0.63rem;font-weight:700;'>🟡 Marginal: {n_marg}</span>"
        f"<span style='background:#f9fafb;border:1px solid #9ca3af;color:#374151;"
        f"border-radius:20px;padding:2px 10px;font-size:0.63rem;font-weight:700;'>📊 Total: {len(results)}</span>"
        f"</div>",
        unsafe_allow_html=True
    )

    if auto_trade and not _dl_blocked:
        for res in results:
            sym = res["symbol"]
            if (len(st.session_state.open_trades) < max_open and
                    sym not in st.session_state.open_trades and
                    NSE_OPEN<=_now_t<=NSE_CUTOFF):
                enter_trade(sym, res, paper=paper_mode)

    for res in results:
        sym      = res["symbol"]
        ltp      = res["ltp"]
        route    = res["route"]
        bb       = res.get("bb", {})
        in_trade = sym in st.session_state.open_trades

        tag = {"OPTIONS":"🟢 CE OPTION","EQUITY":"🔵 EQUITY BUY","OPTIONS_MARGINAL":"🟡 MARGINAL"}.get(route,"⚪")
        trade_badge = "  ✅ IN TRADE" if in_trade else ""

        with st.expander(
            f"{tag}  •  **{sym}**  —  ₹{ltp:,.2f}"
            f"  |  BB upper: ₹{bb.get('bb_upper',0):,.2f}"
            f"  |  {bb.get('pct_above',0):+.2f}% above"
            f"{trade_badge}",
            expanded=(not in_trade)
        ):
            c1, c2, c3 = st.columns([1.2, 2.2, 1])

            with c1:
                st.markdown("**📌 BB Signal**")
                st.markdown(f"LTP: **₹{ltp:,.2f}**")
                st.markdown(f"BB Upper: ₹{bb.get('bb_upper',0):,.2f}")
                st.markdown(f"BB Mid:   ₹{bb.get('bb_mid',0):,.2f}")
                st.markdown(f"BB Lower: ₹{bb.get('bb_lower',0):,.2f}")
                st.markdown(f"Width:    ₹{bb.get('bb_width',0):,.1f}")
                st.markdown(f"Above:    **{bb.get('pct_above',0):+.2f}%**")
                st.markdown(f"Candle:   `{bb.get('candle_time','—')}`")
                st.markdown(f"Volume:   {bb.get('vol',0):,}")
                if res["expiry_str"] != "—":
                    st.markdown(f"Expiry:   `{res['expiry_str']}` · {res['dte']}d")

            with c2:
                if route in ("OPTIONS","OPTIONS_MARGINAL") and res.get("opt_sym"):
                    st.markdown("**📈 CE Plan**")
                    ps = "live" if res["live_prem"]>0 else "BS est"
                    st.markdown(f"Strike: **{res['atm']} CE** · `{res['opt_sym']}`")
                    st.markdown(f"Premium: **₹{res['prem']:.1f}** ({ps}) · Δ {res['delta']} · Lot: {res['lot_size']}")
                    st.markdown(f"**1-Lot Budget: ₹{res['budget_opt']:,}**")
                    p1,p2,p3,p4 = st.columns(4)
                    p1.markdown(f"<div style='background:#fff1f2;border-radius:6px;padding:4px 6px;font-size:0.6rem;text-align:center;'><b>SL −25%</b><br/>₹{res['sl_prem']:.1f}</div>",unsafe_allow_html=True)
                    p2.markdown(f"<div style='background:#fff8e1;border-radius:6px;padding:4px 6px;font-size:0.6rem;text-align:center;'><b>T1 +50%</b><br/>₹{res['t1_prem']:.1f}<br/><span style='font-size:0.52rem;'>50% exit+trail</span></div>",unsafe_allow_html=True)
                    p3.markdown(f"<div style='background:#f0fdf4;border-radius:6px;padding:4px 6px;font-size:0.6rem;text-align:center;'><b>T2 +100%</b><br/>₹{res['t2_prem']:.1f}<br/><span style='font-size:0.52rem;'>full exit</span></div>",unsafe_allow_html=True)
                    p4.markdown(f"<div style='background:#eff6ff;border-radius:6px;padding:4px 6px;font-size:0.6rem;text-align:center;'><b>Trail +35%</b><br/>₹{res['trail_trigger_prem']:.1f}<br/><span style='font-size:0.52rem;'>SL→+15%</span></div>",unsafe_allow_html=True)
                    if route=="OPTIONS_MARGINAL":
                        st.markdown("---")

                if route in ("EQUITY","OPTIONS_MARGINAL"):
                    st.markdown("**📦 Equity Plan**")
                    st.markdown(f"Qty: **{res['eq_qty']}** @ ₹{ltp:,.2f} · Budget: **₹{res['budget_eq']:,}**")
                    e1,e2,e3,e4 = st.columns(4)
                    e1.markdown(f"<div style='background:#fff1f2;border-radius:6px;padding:4px 6px;font-size:0.6rem;text-align:center;'><b>SL −2%</b><br/>₹{res['sl_eq']:.2f}</div>",unsafe_allow_html=True)
                    e2.markdown(f"<div style='background:#fff8e1;border-radius:6px;padding:4px 6px;font-size:0.6rem;text-align:center;'><b>T1 +3%</b><br/>₹{res['t1_eq']:.2f}<br/><span style='font-size:0.52rem;'>50%+trail</span></div>",unsafe_allow_html=True)
                    e3.markdown(f"<div style='background:#f0fdf4;border-radius:6px;padding:4px 6px;font-size:0.6rem;text-align:center;'><b>T2 +5%</b><br/>₹{res['t2_eq']:.2f}<br/><span style='font-size:0.52rem;'>full exit</span></div>",unsafe_allow_html=True)
                    e4.markdown(f"<div style='background:#eff6ff;border-radius:6px;padding:4px 6px;font-size:0.6rem;text-align:center;'><b>Trail +3.5%</b><br/>₹{res['trail_trigger_eq']:.2f}<br/><span style='font-size:0.52rem;'>SL→+1.5%</span></div>",unsafe_allow_html=True)

                if res.get("error"): st.caption(f"ℹ️ {res['error']}")
                st.caption(res.get("route_reason",""))

            with c3:
                st.markdown("**🎯 Action**")
                if in_trade:
                    t     = st.session_state.open_trades[sym]
                    cur_p = (get_ltp_opt(t["opt_sym"]) if t["type"]=="CE_OPTION" and t.get("opt_sym") else get_ltp_eq(sym))
                    if cur_p > 0:
                        live_pnl = round((cur_p-t["entry"])*t["qty_remaining"], 2)
                        pc = "#16a34a" if live_pnl>=0 else "#dc2626"
                        st.markdown(f"<div style='font-weight:800;font-size:0.95rem;color:{pc};'>₹{live_pnl:+,.0f}</div>",unsafe_allow_html=True)
                    st.caption(f"Entry ₹{t['entry']:.1f} · Qty rem: {t['qty_remaining']}")
                    if t["t1_done"]: st.caption("✅ T1 done · SL at BE")
                    if t["trailed"]: st.caption(f"↗ SL trailed to ₹{t['sl']:.1f}")
                    if st.button(f"🚪 Exit {sym}", key=f"exit_s_{sym}"):
                        manual_exit(sym, paper_mode); st.rerun()

                elif not _dl_blocked and not auto_trade:
                    if len(st.session_state.open_trades)<max_open and NSE_OPEN<=_now_t<=NSE_CUTOFF:
                        mi = "📄" if paper_mode else "⚡"
                        if route in ("OPTIONS","OPTIONS_MARGINAL") and res.get("opt_sym"):
                            if st.button(f"{mi} Buy CE", key=f"bce_{sym}"):
                                enter_trade(sym,res,force_route="OPTIONS",paper=paper_mode); st.rerun()
                        if route in ("EQUITY","OPTIONS_MARGINAL"):
                            if st.button(f"{mi} Buy Stock", key=f"beq_{sym}"):
                                enter_trade(sym,res,force_route="EQUITY",paper=paper_mode); st.rerun()
                    else:
                        st.warning("Max open trades" if len(st.session_state.open_trades)>=max_open else "Mkt closed/cutoff")
                elif auto_trade and not in_trade:
                    st.success("🤖 Auto-traded")
                elif _dl_blocked:
                    st.error("🛑 Loss limit")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — OPEN POSITIONS
# ─────────────────────────────────────────────────────────────────────────────
with tab_open:
    if not st.session_state.open_trades:
        st.info("No open positions.")
    else:
        for sym, t in list(st.session_state.open_trades.items()):
            cur_p = (get_ltp_opt(t["opt_sym"]) if t["type"]=="CE_OPTION" and t.get("opt_sym") else get_ltp_eq(sym))
            if cur_p<=0: cur_p=t["entry"]
            pnl     = round((cur_p-t["entry"])*t["qty_remaining"], 2)
            pnl_pct = round((cur_p-t["entry"])/t["entry"]*100,1) if t["entry"] else 0
            pc = "#16a34a" if pnl>=0 else "#dc2626"
            bg = "#f0fdf4" if pnl>=0 else "#fff1f2"

            # Status badges
            badges = ""
            if t["trailed"]:  badges += "<span style='background:#eff6ff;border:1px solid #2563eb;color:#1d4ed8;border-radius:4px;font-size:0.55rem;padding:1px 6px;margin-right:4px;'>↗ SL TRAILED</span>"
            if t["t1_done"]:  badges += "<span style='background:#f0fdf4;border:1px solid #16a34a;color:#166534;border-radius:4px;font-size:0.55rem;padding:1px 6px;margin-right:4px;'>✅ T1 DONE</span>"
            gtt_badge = "<span style='background:#fef9c3;border:1px solid #ca8a04;color:#854d0e;border-radius:4px;font-size:0.55rem;padding:1px 6px;'>🔔 GTT ON EXCHANGE</span>" if t.get("gtt_id") else ""

            st.markdown(
                f"<div style='background:{bg};border:1px solid {pc}33;"
                f"border-radius:10px;padding:0.75rem 1rem;margin-bottom:0.4rem;'>"
                f"<div style='display:flex;gap:1.2rem;flex-wrap:wrap;align-items:center;margin-bottom:4px;'>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>SYMBOL</div><div style='font-size:1rem;font-weight:800;'>{sym}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>TYPE</div><div style='font-size:0.8rem;font-weight:700;'>{t['type']}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>STRIKE</div><div style='font-size:0.8rem;font-weight:700;'>{t['strike']}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>ENTRY</div><div style='font-size:0.8rem;font-weight:700;'>₹{t['entry']:.1f}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>LTP</div><div style='font-size:0.8rem;font-weight:700;'>₹{cur_p:.1f}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>QTY REM</div><div style='font-size:0.8rem;font-weight:700;'>{t['qty_remaining']}/{t['qty']}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>LIVE P&L</div>"
                f"<div style='font-size:1.1rem;font-weight:800;color:{pc};'>₹{pnl:+,.0f} ({pnl_pct:+.1f}%)</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>SL</div><div style='font-size:0.8rem;color:#dc2626;font-weight:700;'>₹{t['sl']:.1f}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>T1</div><div style='font-size:0.8rem;color:#d97706;font-weight:700;'>₹{t['t1']:.1f}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>T2</div><div style='font-size:0.8rem;color:#16a34a;font-weight:700;'>₹{t['t2']:.1f}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>TRAIL@</div><div style='font-size:0.75rem;'>₹{t['trail_trigger']:.1f}</div></div>"
                f"<div><div style='font-size:0.53rem;color:#6b7280;'>MODE</div><div style='font-size:0.72rem;font-weight:700;'>{t['mode']}</div></div>"
                f"</div>"
                f"<div>{badges}{gtt_badge}</div>"
                f"</div>",
                unsafe_allow_html=True
            )
            oc1, oc2, oc3, _ = st.columns([1,1,1,3])
            with oc1:
                if st.button(f"🚪 Exit {sym}", key=f"exit_op_{sym}"):
                    manual_exit(sym, paper_mode); st.rerun()
            with oc2:
                if st.button(f"↗ Trail to BE", key=f"trail_{sym}"):
                    st.session_state.open_trades[sym]["sl"] = t["entry"]
                    st.session_state.open_trades[sym]["trailed"] = True
                    st.rerun()
            with oc3:
                if not t["t1_done"]:
                    if st.button(f"½ Partial T1 {sym}", key=f"pt1_{sym}"):
                        cur_now = (get_ltp_opt(t["opt_sym"]) if t["type"]=="CE_OPTION" and t.get("opt_sym") else get_ltp_eq(sym))
                        partial_exit(sym, cur_now, "Manual Partial", paper_mode)
                        st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — PAPER TRADE JOURNAL  ← NEW
# ─────────────────────────────────────────────────────────────────────────────
with tab_paper:
    st.markdown(
        "<div style='font-family:Syne,sans-serif;font-size:1rem;"
        "font-weight:800;margin-bottom:0.5rem;'>📄 Paper Trade Journal</div>",
        unsafe_allow_html=True
    )

    journal = st.session_state.paper_journal
    if not journal:
        st.info("No paper trades recorded yet. Enable Paper Mode and start trading.")
    else:
        # ── Day selector ──────────────────────────────────────────────────────
        all_dates = sorted(journal.keys(), reverse=True)
        sel_date  = st.selectbox("Select Date", all_dates, index=0, key="journal_date")

        day_trades = journal.get(sel_date, [])
        if not day_trades:
            st.info("No trades for this date.")
        else:
            # ── Day summary card ──────────────────────────────────────────────
            closed = [r for r in day_trades if r.get("Reason","") != "ENTRY"]
            entries = [r for r in day_trades if r.get("Reason","") == "ENTRY"]
            net_pnl  = sum(r.get("P&L ₹", 0) for r in closed)
            gross_p  = sum(r.get("P&L ₹", 0) for r in closed if r.get("P&L ₹", 0) > 0)
            gross_l  = sum(r.get("P&L ₹", 0) for r in closed if r.get("P&L ₹", 0) < 0)
            wins_d   = sum(1 for r in closed if r.get("P&L ₹", 0) > 0 and r.get("Partial") == "No")
            losses_d = sum(1 for r in closed if r.get("P&L ₹", 0) < 0 and r.get("Partial") == "No")
            total_full = wins_d + losses_d
            wr_d     = round(wins_d/max(total_full,1)*100, 1)
            nc       = "#16a34a" if net_pnl >= 0 else "#dc2626"
            nb       = "#f0fdf4" if net_pnl >= 0 else "#fff1f2"

            st.markdown(
                f"<div class='day-card' style='background:{nb};border-color:{nc}55;'>"
                f"<div style='font-size:0.62rem;color:#6b7280;font-weight:700;text-transform:uppercase;"
                f"letter-spacing:0.08em;margin-bottom:0.4rem;'>📅 {sel_date} — Day Summary (Paper)</div>"
                f"<div style='display:flex;gap:2rem;flex-wrap:wrap;'>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>NET P&L</div>"
                f"<div style='font-size:1.5rem;font-weight:800;color:{nc};'>₹{net_pnl:+,.0f}</div></div>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>GROSS PROFIT</div>"
                f"<div style='font-size:1.1rem;font-weight:700;color:#16a34a;'>+₹{gross_p:,.0f}</div></div>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>GROSS LOSS</div>"
                f"<div style='font-size:1.1rem;font-weight:700;color:#dc2626;'>₹{gross_l:,.0f}</div></div>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>WIN RATE</div>"
                f"<div style='font-size:1.1rem;font-weight:700;color:#d97706;'>"
                f"{wr_d}% ({wins_d}W/{losses_d}L)</div></div>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>TOTAL TRADES</div>"
                f"<div style='font-size:1.1rem;font-weight:700;'>{len(entries)} entries</div></div>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>CLOSED LEGS</div>"
                f"<div style='font-size:1.1rem;font-weight:700;'>{len(closed)}</div></div>"
                f"</div></div>",
                unsafe_allow_html=True
            )

            # ── Trade table ───────────────────────────────────────────────────
            df_journal = pd.DataFrame(day_trades)

            # Colour P&L column
            def _style_journal(row):
                out = [""] * len(row)
                cols = list(df_journal.columns)
                pi = cols.index("P&L ₹") if "P&L ₹" in cols else -1
                ri = cols.index("Running ₹") if "Running ₹" in cols else -1
                if pi >= 0:
                    v = row.get("P&L ₹", 0)
                    out[pi] = "color:#16a34a;font-weight:700" if v > 0 else (
                              "color:#dc2626;font-weight:700" if v < 0 else "")
                if ri >= 0:
                    v = row.get("Running ₹", 0)
                    out[ri] = "color:#16a34a;font-weight:600" if v > 0 else (
                              "color:#dc2626;font-weight:600" if v < 0 else "")
                return out

            st.dataframe(
                df_journal.style.apply(_style_journal, axis=1),
                use_container_width=True,
                hide_index=True,
                height=min(60 + 36*len(day_trades), 520)
            )

            # ── Export ────────────────────────────────────────────────────────
            jc1, jc2 = st.columns(2)
            with jc1:
                st.download_button(
                    f"⬇ Export {sel_date} CSV",
                    df_journal.to_csv(index=False).encode(),
                    f"paper_journal_{sel_date}.csv","text/csv",
                    key=f"dl_journal_{sel_date}"
                )
            with jc2:
                if len(all_dates) > 1:
                    all_rows = []
                    for d in all_dates:
                        for r in journal.get(d, []):
                            all_rows.append({**r, "Date": d})
                    if all_rows:
                        st.download_button(
                            "⬇ Export All Days CSV",
                            pd.DataFrame(all_rows).to_csv(index=False).encode(),
                            "paper_journal_all.csv","text/csv",
                            key="dl_journal_all"
                        )

        # ── Multi-day P&L summary bar ─────────────────────────────────────────
        if len(all_dates) > 1:
            st.markdown("---")
            st.markdown(
                "<div style='font-size:0.75rem;font-weight:700;margin-bottom:0.4rem;'>"
                "📆 All-Day Paper Performance</div>",
                unsafe_allow_html=True
            )
            summary_rows = []
            for d in sorted(all_dates):
                day_closed = [r for r in journal.get(d,[]) if r.get("Reason","")!="ENTRY"]
                d_net  = sum(r.get("P&L ₹",0) for r in day_closed)
                d_wins = sum(1 for r in day_closed if r.get("P&L ₹",0)>0 and r.get("Partial")=="No")
                d_loss = sum(1 for r in day_closed if r.get("P&L ₹",0)<0 and r.get("Partial")=="No")
                summary_rows.append({
                    "Date": d,
                    "Trades": len([r for r in journal.get(d,[]) if r.get("Reason","")=="ENTRY"]),
                    "Closed Legs": len(day_closed),
                    "Wins": d_wins, "Losses": d_loss,
                    "Win Rate %": round(d_wins/max(d_wins+d_loss,1)*100,1),
                    "Net P&L ₹": d_net,
                })
            df_summary = pd.DataFrame(summary_rows)

            def _style_summary(row):
                out = [""] * len(row)
                cols = list(df_summary.columns)
                pi = cols.index("Net P&L ₹") if "Net P&L ₹" in cols else -1
                if pi >= 0:
                    v = row.get("Net P&L ₹", 0)
                    out[pi] = "color:#16a34a;font-weight:800" if v > 0 else (
                              "color:#dc2626;font-weight:800" if v < 0 else "")
                return out

            st.dataframe(
                df_summary.style.apply(_style_summary, axis=1),
                use_container_width=True,
                hide_index=True
            )
            total_all = sum(r["Net P&L ₹"] for r in summary_rows)
            tc = "#16a34a" if total_all >= 0 else "#dc2626"
            st.markdown(
                f"<div style='font-size:1rem;font-weight:800;color:{tc};"
                f"margin-top:0.4rem;'>All-Time Paper P&L: ₹{total_all:+,.0f}</div>",
                unsafe_allow_html=True
            )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — FULL TRADE HISTORY (paper + live)
# ─────────────────────────────────────────────────────────────────────────────
with tab_hist:
    today_trades = [t for t in st.session_state.trade_history if t.get("Date")==_today_str]
    h1, h2 = st.tabs(["📅 Today", "📆 All Time"])

    with h1:
        if not today_trades:
            st.info("No closed trades today.")
        else:
            net  = sum(t.get("P&L ₹",0) for t in today_trades)
            gp   = sum(t.get("P&L ₹",0) for t in today_trades if t.get("P&L ₹",0)>0)
            gl   = sum(t.get("P&L ₹",0) for t in today_trades if t.get("P&L ₹",0)<0)
            wins = sum(1 for t in today_trades if t.get("P&L ₹",0)>0 and t.get("Partial","No")=="No")
            nc   = "#16a34a" if net>=0 else "#dc2626"; nb = "#f0fdf4" if net>=0 else "#fff1f2"
            st.markdown(
                f"<div style='background:{nb};border:1px solid {nc}33;border-radius:10px;"
                f"padding:0.7rem 1rem;margin-bottom:0.5rem;display:flex;gap:2rem;flex-wrap:wrap;'>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>NET P&L</div>"
                f"<div style='font-size:1.4rem;font-weight:800;color:{nc};'>₹{net:+,.0f}</div></div>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>GROSS PROFIT</div>"
                f"<div style='font-size:1rem;font-weight:700;color:#16a34a;'>+₹{gp:,.0f}</div></div>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>GROSS LOSS</div>"
                f"<div style='font-size:1rem;font-weight:700;color:#dc2626;'>₹{gl:,.0f}</div></div>"
                f"<div><div style='font-size:0.52rem;color:#6b7280;'>WIN RATE</div>"
                f"<div style='font-size:1rem;font-weight:700;color:#d97706;'>"
                f"{round(wins/max(len([t for t in today_trades if t.get('Partial','No')=='No']),1)*100,1)}%</div></div>"
                f"</div>",
                unsafe_allow_html=True
            )
            df_t = pd.DataFrame(today_trades)
            st.dataframe(df_t, use_container_width=True, hide_index=True,
                         height=min(60+36*len(today_trades), 480))
            st.download_button("⬇ Export Today CSV",
                               df_t.to_csv(index=False).encode(),
                               f"bb_trades_{_today_str}.csv","text/csv",key="dl_t")

    with h2:
        if not st.session_state.trade_history:
            st.info("No trades yet.")
        else:
            df_a = pd.DataFrame(st.session_state.trade_history)
            all_net = sum(t.get("P&L ₹",0) for t in st.session_state.trade_history)
            ac = "#16a34a" if all_net>=0 else "#dc2626"
            st.markdown(f"<div style='font-size:1rem;font-weight:800;color:{ac};margin-bottom:0.4rem;'>All-Time P&L: ₹{all_net:+,.0f}</div>",unsafe_allow_html=True)
            st.dataframe(df_a, use_container_width=True, hide_index=True)
            st.download_button("⬇ Export All CSV",
                               df_a.to_csv(index=False).encode(),
                               "bb_all_trades.csv","text/csv",key="dl_all")

# ── FOOTER ────────────────────────────────────────────────────────────────────
st.markdown(
    f"<div style='font-size:0.5rem;color:#aaa;text-align:center;margin-top:0.6rem;'>"
    f"BB Breakout Scanner v2.0 · BB(20,2) · NSE F&O · CE ≤₹{opt_max//1000}k · "
    f"Equity >₹{opt_limit//1000}k · T1 Partial+Trail · T2 Full · GTT OCO · "
    f"Paper Journal · 100% Kite · Auto-refresh 60s · "
    f"{current_time.strftime('%d %b %Y %H:%M IST')}</div>",
    unsafe_allow_html=True
)
