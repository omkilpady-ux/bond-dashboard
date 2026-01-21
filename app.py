import streamlit as st
import pandas as pd
import requests
import numpy_financial as npf
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# ================= PAGE SETUP =================
st.set_page_config(page_title="Bond Market Monitor", layout="wide")
st.title("Composite Edge – Bond Market Monitor")
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

# ================= SESSION STATE =================
if "watchlist" not in st.session_state:
    st.session_state.watchlist = []

# ================= SIDEBAR =================
st.sidebar.title("Controls")

series_filter = st.sidebar.multiselect(
    "Series",
    options=["GS", "SG"],
    default=["GS"]
)

if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()

# ================= INDIAN SETTLEMENT DATE (T+1) =================
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

# ================= MASTER DATA =================
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

    df = df[df["Years to Maturity"] > 0]

    return df

# ================= LIVE NSE DATA =================
@st.cache_data(ttl=5)
def load_live():
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        })
        session.get("https://www.nseindia.com", timeout=10)

        url = "https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec"
        data = session.get(url, timeout=10).json().get("data", [])

        rows = []
        for d in data:
            rows.append({
                "Symbol": d.get("symbol"),
                "Series": d.get("series"),
                "Bid": d.get("buyPrice1"),
                "Ask": d.get("sellPrice1"),
                "Dirty Price": d.get("averagePrice"),
                "Volume": d.get("totalTradedVolume"),
            })

        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

# ================= LOAD & MERGE =================
master = load_master()
live = load_live()

if master.empty or live.empty:
    st.warning("Live data not available.")
    st.stop()

df = live.merge(master, on="Symbol", how="left")
df = df[df["Series"].isin(series_filter)]
df = df.dropna(subset=["Coupon", "Years to Maturity", "Dirty Price"])

# ================= ACCRUED INTEREST =================
def last_coupon_date(redemption):
    dt = redemption
    while dt > SETTLEMENT:
        dt -= relativedelta(months=6)
    return dt

# ================= ACCRUED INTEREST (30/360 – MATCHES EXCEL) =================

def last_coupon_date(redemption):
    dt = redemption
    while dt > SETTLEMENT:
        dt -= relativedelta(months=6)
    return dt

df["Last Coupon Date"] = df["REDEMPTION DATE"].apply(last_coupon_date)

df["Days Since Coupon"] = df.apply(
    lambda x: days360(x["Last Coupon Date"], SETTLEMENT, method="US"),
    axis=1
)

# Coupon is annual %, price per 100 face value
df["Accrued Interest"] = df["Days Since Coupon"] * df["Coupon"] / 360


df["Clean Price"] = df["Dirty Price"] - df["Accrued Interest"]

# ================= YTM =================
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

# ================= RELATIVE VALUE =================
df["Rel Value (bps)"] = (
    df["YTM (%)"] - df.groupby("Series")["YTM (%)"].transform("mean")
) * 100

# ================= MARKET VIEW =================
st.subheader("Market View")

display_cols = [
    "Symbol",
    "Series",
    "Bid",
    "Ask",
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

# ================= WATCHLIST =================
st.subheader("Watchlist")

all_bonds = sorted(df["Symbol"].unique())

selected = st.multiselect(
    "Select bonds",
    options=all_bonds,
    default=st.session_state.watchlist
)

st.session_state.watchlist = selected

if selected:
    watch_df = df[df["Symbol"].isin(selected)]
    st.dataframe(
        watch_df[display_cols].sort_values("Rel Value (bps)", ascending=False),
        use_container_width=True
    )
else:
    st.info("Add bonds to your watchlist.")
