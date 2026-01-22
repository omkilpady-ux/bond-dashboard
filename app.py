import streamlit as st
import pandas as pd
import requests
import numpy_financial as npf
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# =====================================================
# PAGE SETUP
# =====================================================
st.set_page_config(page_title="Bond Market Monitor", layout="wide")
st.title("Composite Edge – Bond Market Monitor")
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

# =====================================================
# SESSION STATE
# =====================================================
if "watchlist" not in st.session_state:
    st.session_state.watchlist = []

if "alerts" not in st.session_state:
    st.session_state.alerts = {}

# =====================================================
# SIDEBAR CONTROLS
# =====================================================
st.sidebar.title("Controls")

series_filter = st.sidebar.multiselect(
    "Series",
    options=["GS", "SG"],
    default=["GS"]
)

if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()

# =====================================================
# INDIAN SETTLEMENT DATE (T+1, WEEKENDS)
# =====================================================
def get_settlement_date():
    today = datetime.today().date()
    wd = today.weekday()
    if wd <= 3:      # Mon–Thu
        return today + timedelta(days=1)
    elif wd == 4:    # Friday
        return today + timedelta(days=3)
    else:            # Saturday
        return today + timedelta(days=2)

SETTLEMENT = get_settlement_date()

# =====================================================
# 30/360 US DAY COUNT (MANUAL, EXCEL MATCH)
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
# LOAD MASTER DATA (STATIC)
# =====================================================
@st.cache_data(ttl=24 * 3600)
def load_master():
    df = pd.read_csv("master_debt.csv")
    df.columns = df.columns.str.strip().str.upper()

    df = df[["SYMBOL", "IP RATE", "REDEMPTION DATE"]]
    df.rename(columns={"SYMBOL": "Symbol", "IP RATE": "Coupon"}, inplace=True)

    df["REDEMPTION DATE"] = pd.to_datetime(
        df["REDEMPTION DATE"], errors="coerce", dayfirst=True
    ).dt.date

    df = df.dropna(subset=["REDEMPTION DATE"])

    df["Years to Maturity"] = (
        pd.to_datetime(df["REDEMPTION DATE"]) - pd.to_datetime(SETTLEMENT)
    ).dt.days / 365

    return df[df["Years to Maturity"] > 0]

# =====================================================
# LOAD LIVE NSE DATA (ALWAYS RETURNS DF)
# =====================================================
@st.cache_data(ttl=5)
def load_live():
    rows = []
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        })

        session.get("https://www.nseindia.com", timeout=10)

        url = "https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec"
        resp = session.get(url, timeout=10)
        data = resp.json().get("data", [])

        for d in data:
            if not isinstance(d, dict):
                continue

            last_px = d.get("lastPrice") or 0
            avg_px = d.get("averagePrice") or 0

            rows.append({
                "Symbol": d.get("symbol"),
                "Series": d.get("series"),
                "Bid": d.get("buyPrice1") or 0,
                "Ask": d.get("sellPrice1") or 0,
                "LTP": last_px,
                "Dirty Price": last_px if last_px != 0 else avg_px,
                "Volume": d.get("totalTradedVolume") or 0,
            })

    except Exception:
        pass

    return pd.DataFrame(rows)

# =====================================================
# LOAD & MERGE
# =====================================================
master = load_master()
live = load_live()

if master is None or live is None or master.empty or live.empty:
    st.warning("Live data not available right now.")
    st.stop()

df = live.merge(master, on="Symbol", how="left")
df = df[df["Series"].isin(series_filter)]
df = df.dropna(subset=["Coupon", "Dirty Price", "Years to Maturity"])

# =====================================================
# ACCRUED INTEREST (EXCEL MATCH)
# =====================================================
def last_coupon_date(redemption):
    dt = redemption
    while dt > SETTLEMENT:
        dt -= relativedelta(months=6)
    return dt

df["Last Coupon Date"] = df["REDEMPTION DATE"].apply(last_coupon_date)
df["Days Since Coupon"] = df.apply(
    lambda x: days360_us(x["Last Coupon Date"], SETTLEMENT),
    axis=1
)

df["Accrued Interest"] = df["Days Since Coupon"] * df["Coupon"] / 360
df["Clean Price"] = df["Dirty Price"] - df["Accrued Interest"]

# =====================================================
# YTM (ON CLEAN LTP)
# =====================================================
def calc_ytm(row):
    try:
        return npf.rate(
            row["Years to Maturity"] * 2,
            row["Coupon"] / 2,
            -row["Clean Price"],
            100
        ) * 2 * 100
    except:
        return None

df["YTM (%)"] = df.apply(calc_ytm, axis=1)

# =====================================================
# RELATIVE VALUE (WITHIN SERIES)
# =====================================================
df["Rel Value (bps)"] = (
    df["YTM (%)"] - df.groupby("Series")["YTM (%)"].transform("mean")
) * 100

# =====================================================
# MARKET VIEW
# =====================================================
st.subheader("Market View")

display_cols = [
    "Symbol",
    "Series",
    "Bid",
    "Ask",
    "LTP",
    "Volume",
    "Dirty Price",
    "Accrued Interest",
    "Clean Price",
    "YTM (%)",
    "Rel Value (bps)"
]

st.dataframe(
    df[display_cols].sort_values("Rel Value (bps)", ascending=False),
    use_container_width=True
)

# =====================================================
# WATCHLIST + VISUAL ALERTS
# =====================================================
st.subheader("Watchlist")

# ---- Quick add ----
all_symbols = sorted(df["Symbol"].unique())

all_symbols = sorted(df["Symbol"].unique())

quick_add = st.selectbox(
    "Quick add (type to search)",
    options=[""] + all_symbols,
    index=0
)

if quick_add and quick_add not in st.session_state.watchlist:
    st.session_state.watchlist.append(quick_add)
