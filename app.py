import streamlit as st
import pandas as pd
import requests
import numpy_financial as npf
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import base64
import json
from pathlib import Path
import os

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
        json.dump({
            "watchlist": st.session_state.watchlist,
            "alerts": st.session_state.alerts
        }, f)

# =====================================================
# PAGE SETUP
# =====================================================
st.set_page_config(page_title="Bond Market Monitor", layout="wide")
st.title("Composite Edge – Bond Market Monitor")
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

# =====================================================
# SESSION STATE INIT
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

series_filter = st.sidebar.multiselect(
    "Series", ["GS", "SG"], default=["GS"]
)

if st.sidebar.button("🔄 Refresh prices"):
    st.cache_data.clear()

# =====================================================
# SETTLEMENT DATE (T+1 INDIA)
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
# 30/360 US
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
        pd.to_datetime(df["REDEMPTION DATE"]) - pd.to_datetime(SETTLEMENT)
    ).dt.days / 365

    return df[df["Years"] > 0]

# =====================================================
# ISIN MAP (LOCAL, BULLETPROOF)
# =====================================================
@st.cache_data(ttl=24 * 3600)
def load_isin_map():
    filenames = [
        "debt_isin_map.csv",
        "DEBT.csv",
        "debt_isin_map.csv.csv"
    ]

    for f in filenames:
        if os.path.exists(f):
            df = pd.read_csv(f)
            df.columns = df.columns.str.strip().str.upper()
            if "SYMBOL" in df.columns and "ISIN" in df.columns:
                df = df[["SYMBOL", "ISIN"]]
                df.rename(columns={"SYMBOL": "Symbol"}, inplace=True)
                return df.dropna().drop_duplicates()

    return pd.DataFrame(columns=["Symbol", "ISIN"])

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
            rows.append({
                "Symbol": d.get("symbol"),
                "Series": d.get("series"),
                "Bid": d.get("buyPrice1") or 0,
                "Ask": d.get("sellPrice1") or 0,
                "LTP": d.get("lastPrice") or 0,
                "Dirty": d.get("lastPrice") or d.get("averagePrice") or 0,
                "Volume": d.get("totalTradedVolume") or 0,
            })
    except:
        pass

    return pd.DataFrame(rows)

# =====================================================
# LOAD DATA
# =====================================================
master = load_master()
live = load_live()
isin_map = load_isin_map()

df = live.merge(master, on="Symbol", how="left")
df = df.merge(isin_map, on="Symbol", how="left")
df = df[df["Series"].isin(series_filter)]
df = df.dropna(subset=["Coupon", "Dirty", "Years"])

# =====================================================
# ACCRUED INTEREST
# =====================================================
def last_coupon_date(red):
    d = red
    while d > SETTLEMENT:
        d -= relativedelta(months=6)
    return d

df["Last Coupon"] = df["REDEMPTION DATE"].apply(last_coupon_date)
df["Days Since"] = df.apply(lambda r: days360_us(r["Last Coupon"], SETTLEMENT), axis=1)
df["Accrued"] = df["Days Since"] * df["Coupon"] / 360
df["Clean"] = df["Dirty"] - df["Accrued"]

# =====================================================
# YTM
# =====================================================
df["YTM"] = df.apply(
    lambda r: npf.rate(r["Years"] * 2, r["Coupon"] / 2, -r["Clean"], 100) * 2 * 100,
    axis=1
)

df["YTM_Dirty"] = df.apply(
    lambda r: npf.rate(r["Years"] * 2, r["Coupon"] / 2, -r["Dirty"], 100) * 2 * 100,
    axis=1
)

# =====================================================
# ISIN LOOKUP
# =====================================================
isin_to_symbol = (
    df[["ISIN", "Symbol"]]
    .dropna()
    .drop_duplicates()
    .set_index("ISIN")["Symbol"]
    .to_dict()
)

# =====================================================
# MARKET VIEW
# =====================================================
st.subheader("Market View")

cols = [
    "Symbol", "ISIN", "Series", "Bid", "Ask", "LTP",
    "Dirty", "Accrued", "Clean", "YTM", "YTM_Dirty"
]

st.dataframe(df[cols], use_container_width=True)

# =====================================================
# WATCHLIST
# =====================================================
st.subheader("Watchlist")

quick_add = st.selectbox("Add bond", [""] + sorted(df["Symbol"].unique()))
if quick_add and quick_add not in st.session_state.watchlist:
    st.session_state.watchlist.append(quick_add)
    save_persistent_state()

paste_isin = st.text_area("Paste ISINs")
if st.button("➕ Add ISINs"):
    syms = [isin_to_symbol[i.strip().upper()] for i in paste_isin.splitlines()
            if i.strip().upper() in isin_to_symbol]
    st.session_state.watchlist = list(dict.fromkeys(
        st.session_state.watchlist + syms
    ))
    save_persistent_state()
