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

if "page" not in st.session_state:
    st.session_state.page = "Market"

# ================= SIDEBAR =================
st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Market", "Watchlist"],
    index=0 if st.session_state.page == "Market" else 1
)
st.session_state.page = page

if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()

# ================= SETTLEMENT DATE =================
def get_settlement_date():
    today = datetime.today()
    if today.weekday() == 4:
        return today + timedelta(days=3)
    elif today.weekday() == 5:
        return today + timedelta(days=2)
    else:
        return today + timedelta(days=1)

SETTLEMENT = get_settlement_date()

# ================= LOAD MASTER DATA =================
@st.cache_data(ttl=24 * 3600)
def load_master():
    df = pd.read_csv("master_debt.csv")

    df.columns = df.columns.str.strip().str.upper()
    df = df[["SYMBOL", "IP RATE", "REDEMPTION DATE"]]
    df.rename(columns={"SYMBOL": "Symbol", "IP RATE": "Coupon"}, inplace=True)

    df["Maturity"] = pd.to_datetime(df["REDEMPTION DATE"], errors="coerce", dayfirst=True)
    df = df.dropna(subset=["Maturity"])

    df["Years to Maturity"] = (df["Maturity"] - SETTLEMENT).dt.days / 365
    df = df[df["Years to Maturity"] > 0]

    return df

# ================= LOAD LIVE NSE DATA =================
@st.cache_data(ttl=10)
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
                "VWAP": d.get("averagePrice"),
                "Volume": d.get("totalTradedVolume"),
            })

        return pd.DataFrame(rows)

    except:
        return pd.DataFrame()

# ================= LOAD & MERGE =================
master = load_master()
live = load_live()

if master.empty or live.empty:
    st.warning("Data not available right now.")
    st.stop()

df = live.merge(master, on="Symbol", how="left")
df = df[df["Series"].isin(["GS", "SG"])]
df = df.dropna(subset=["Coupon", "Years to Maturity", "VWAP"])

# ================= YTM CALC =================
def calc_ytm(row):
    try:
        return npf.rate(
            row["Years to Maturity"] * 2,
            row["Coupon"] / 2,
            -row["VWAP"],
            100
        ) * 2 * 100
    except:
        return None

df["YTM (%)"] = df.apply(calc_ytm, axis=1)

# ================= MARKET PAGE =================
if page == "Market":
    st.subheader("Market Scanner (with YTM)")

    st.dataframe(
        df.sort_values("Volume", ascending=False),
        use_container_width=True
    )

    st.info(
        "YTM is calculated using live prices and cached bond reference data. "
        "Use this page to scan relative value and liquidity."
    )

# ================= WATCHLIST PAGE =================
if page == "Watchlist":
    st.subheader("Bond Watchlist")

    all_bonds = sorted(df["Symbol"].unique())

    selected = st.multiselect(
        "Select bonds",
        options=all_bonds,
        default=st.session_state.watchlist
    )

    st.session_state.watchlist = selected

    if not selected:
        st.info("Add bonds to track.")
    else:
        watch_df = df[df["Symbol"].isin(selected)]

        st.dataframe(
            watch_df.sort_values("YTM (%)", ascending=False),
            use_container_width=True
        )
