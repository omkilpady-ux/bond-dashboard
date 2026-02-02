import streamlit as st
import pandas as pd
import requests
import numpy_financial as npf
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import base64
import json
from pathlib import Path

# =====================================================
# PERSISTENCE
# =====================================================
STATE_FILE = Path("user_state.json")

def load_persistent_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"watchlist": [], "alerts": {}}

def save_persistent_state():
    with open(STATE_FILE, "w") as f:
        json.dump(
            {
                "watchlist": st.session_state.watchlist,
                "alerts": st.session_state.alerts,
            },
            f,
        )

# =====================================================
# PAGE SETUP
# =====================================================
st.set_page_config(page_title="Bond Market Monitor", layout="wide")
st.title("Composite Edge – Bond Market Monitor")
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

# =====================================================
# SESSION STATE INIT (PERSISTENT)
# =====================================================
if "initialized" not in st.session_state:
    persisted = load_persistent_state()
    st.session_state.watchlist = persisted.get("watchlist", [])
    st.session_state.alerts = persisted.get("alerts", {})
    st.session_state.last_alert_state = {}
    st.session_state.initialized = True

# =====================================================
# SIDEBAR
# =====================================================
st.sidebar.header("Controls")
series_filter = st.sidebar.multiselect("Series", ["GS", "SG"], default=["GS"])

if st.sidebar.button("🔄 Refresh prices"):
    st.cache_data.clear()

# =====================================================
# CUSTOM PRICE INPUT
# =====================================================
custom_price = st.sidebar.number_input(
    "Custom Dirty Price (for yield calc)", value=0.0, format="%.2f"
)

# =====================================================
# SETTLEMENT DATE (INDIA T+1)
# =====================================================
def get_settlement_date():
    today = datetime.today().date()
    wd = today.weekday()
    if wd <= 3:
        return today + timedelta(days=1)
    elif wd == 4:
        return today + timedelta(days=3)
    else:
        return today + timedelta(days=2)

SETTLEMENT = get_settlement_date()

# =====================================================
# 30/360 US (EXCEL MATCH)
# =====================================================
def days360_us(start, end):
    d1, d2 = start.day, end.day
    m1, m2 = start.month, end.month
    y1, y2 = start.year, end.year

    if d1 == 31:
        d1 = 30
    if d2 == 31 and d1 == 30:
        d2 = 30

    return 360 * (y2 - y1) + 30 * (m2 - m1) + (d2 - d1)

# =====================================================
# SOUND HELPERS
# =====================================================
def play_near_sound():
    beep = "UklGRigAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQQAAA=="
    st.audio(base64.b64decode(beep), format="audio/wav")

def play_hit_sound():
    beep = "UklGRlIAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YVIAAAB//38AAP//"
    st.audio(base64.b64decode(beep), format="audio/wav")

# =====================================================
# MASTER DATA
# =====================================================
@st.cache_data(ttl=24 * 3600)
def load_master():
    df = pd.read_csv("master_debt.csv")
    df.columns = df.columns.str.strip().str.upper()

    df = df[["SYMBOL", "IP RATE", "REDEMPTION DATE"]]
    df.rename(columns={"SYMBOL": "Symbol", "IP RATE": "Coupon"}, inplace=True)

    df["REDEMPTION DATE"] = pd.to_datetime(
        df["REDEMPTION DATE"], dayfirst=True, errors="coerce"
    ).dt.date

    df = df.dropna(subset=["REDEMPTION DATE"])

    df["Years"] = (
        pd.to_datetime(df["REDEMPTION DATE"])
        - pd.to_datetime(SETTLEMENT)
    ).dt.days / 365

    return df[df["Years"] > 0]

# =====================================================
# LIVE NSE DATA
# =====================================================
@st.cache_data(ttl=5)
def load_live():
    rows = []
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        s.get("https://www.nseindia.com", timeout=10)

        url = "https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec"
        data = s.get(url, timeout=10).json().get("data", [])

        for d in data:
            if not isinstance(d, dict):
                continue

            last_px = d.get("lastPrice") or 0
            avg_px = d.get("averagePrice") or 0

            rows.append(
                {
                    "Symbol": d.get("symbol"),
                    "Series": d.get("series"),
                    "Bid": d.get("buyPrice1") or 0,
                    "Ask": d.get("sellPrice1") or 0,
                    "LTP": last_px,
                    "Dirty": last_px if last_px != 0 else avg_px,
                    "Volume": d.get("totalTradedVolume") or 0,
                }
            )
    except:
        pass

    return pd.DataFrame(rows)

# =====================================================
# LOAD DATA
# =====================================================
master = load_master()
live = load_live()

if master.empty or live.empty:
    st.warning("Live data unavailable. Try refresh.")
    st.stop()

df = live.merge(master, on="Symbol", how="left")
df = df[df["Series"].isin(series_filter)]
df = df.dropna(subset=["Coupon", "Dirty", "Years"])

# =====================================================
# LAST INTEREST PAID + ACCRUED
# =====================================================
def last_coupon_date(redemption):
    d = redemption
    while d > SETTLEMENT:
        d -= relativedelta(months=6)
    return d

df["Last Interest Paid"] = df["REDEMPTION DATE"].apply(last_coupon_date)

df["Days Since"] = df.apply(
    lambda r: days360_us(r["Last Interest Paid"], SETTLEMENT), axis=1
)

df["Accrued"] = df["Days Since"] * df["Coupon"] / 360
df["Clean"] = df["Dirty"] - df["Accrued"]

# =====================================================
# YTM CALCS
# =====================================================
def calc_ytm_clean(r):
    try:
        return npf.rate(
            r["Years"] * 2, r["Coupon"] / 2, -r["Clean"], 100
        ) * 2 * 100
    except:
        return None

def calc_ytm_dirty(r):
    try:
        return npf.rate(
            r["Years"] * 2, r["Coupon"] / 2, -r["Dirty"], 100
        ) * 2 * 100
    except:
        return None

def calc_ytm_bid_dirty(r):
    try:
        if r["Bid"] <= 0:
            return None
        bid_dirty = r["Bid"] + r["Accrued"]
        return npf.rate(
            r["Years"] * 2, r["Coupon"] / 2, -bid_dirty, 100
        ) * 2 * 100
    except:
        return None

def calc_ytm_ask_dirty(r):
    try:
        if r["Ask"] <= 0:
            return None
        ask_dirty = r["Ask"] + r["Accrued"]
        return npf.rate(
            r["Years"] * 2, r["Coupon"] / 2, -ask_dirty, 100
        ) * 2 * 100
    except:
        return None

def calc_ytm_custom_dirty(r):
    try:
        if custom_price <= 0:
            return None
        return npf.rate(
            r["Years"] * 2, r["Coupon"] / 2, -custom_price, 100
        ) * 2 * 100
    except:
        return None

df["YTM"] = df.apply(calc_ytm_clean, axis=1)
df["YTM_Dirty"] = df.apply(calc_ytm_dirty, axis=1)
df["YTM_Bid_Dirty"] = df.apply(calc_ytm_bid_dirty, axis=1)
df["YTM_Ask_Dirty"] = df.apply(calc_ytm_ask_dirty, axis=1)
df["YTM_Custom_Dirty"] = df.apply(calc_ytm_custom_dirty, axis=1)

# =====================================================
# MARKET VIEW
# =====================================================
st.subheader("Market View")

cols = [
    "Symbol",
    "Series",
    "Bid",
    "Ask",
    "LTP",
    "Volume",
    "Dirty",
    "Accrued",
    "Clean",
    "Last Interest Paid",
    "YTM",
    "YTM_Dirty",
    "YTM_Bid_Dirty",
    "YTM_Ask_Dirty",
    "YTM_Custom_Dirty",
]

st.dataframe(df[cols], use_container_width=True)
