import streamlit as st
import pandas as pd
import requests
from datetime import datetime

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Bond Market Monitor", layout="wide")
st.title("Composite Edge – Bond Market Monitor")

# ---------------- SESSION STATE ----------------
if "watchlist" not in st.session_state:
    st.session_state.watchlist = []

if "page" not in st.session_state:
    st.session_state.page = "Market"

# ---------------- SIDEBAR NAV ----------------
st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Market", "Watchlist"],
    index=0 if st.session_state.page == "Market" else 1
)
st.session_state.page = page

# ---------------- REFRESH ----------------
if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()

st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

# ---------------- LIVE NSE DATA ----------------
@st.cache_data(ttl=10)
def fetch_live_bonds():
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

        rows = []
        for d in data:
            rows.append({
                "Symbol": d.get("symbol"),
                "Series": d.get("series"),
                "Bid": d.get("buyPrice1"),
                "Bid Qty": d.get("buyQuantity1"),
                "Ask": d.get("sellPrice1"),
                "Ask Qty": d.get("sellQuantity1"),
                "VWAP": d.get("averagePrice"),
                "Volume": d.get("totalTradedVolume"),
            })

        return pd.DataFrame(rows)

    except:
        return pd.DataFrame()

df = fetch_live_bonds()

if df.empty:
    st.warning("Live NSE data not available right now.")
    st.stop()

# ---------------- DERIVED METRICS ----------------
df["Spread"] = df["Ask"] - df["Bid"]

# Simple yield proxy (NOT full YTM – explainable & safe)
df["Yield Proxy (%)"] = ((100 - df["VWAP"]) / df["VWAP"]) * 100

# Liquidity flags
df["High Volume"] = df["Volume"] > df["Volume"].quantile(0.90)
df["Large Bid"] = df["Bid Qty"] > df["Bid Qty"].quantile(0.90)
df["Wide Spread"] = df["Spread"] > df["Spread"].quantile(0.90)

# ---------------- MARKET PAGE ----------------
if page == "Market":
    st.subheader("Market Scanner")

    # Filters
    col1, col2 = st.columns(2)
    with col1:
        series_filter = st.multiselect(
            "Series",
            options=sorted(df["Series"].dropna().unique()),
            default=["GS"]
        )
    with col2:
        min_volume = st.number_input("Minimum Volume", value=0)

    filtered = df[
        (df["Series"].isin(series_filter)) &
        (df["Volume"] >= min_volume)
    ]

    st.dataframe(
        filtered.sort_values("Volume", ascending=False),
        use_container_width=True
    )

    st.info(
        "Use this page to scan for liquidity, wide spreads, and active bonds. "
        "Execution happens on the trading terminal."
    )

# ---------------- WATCHLIST PAGE ----------------
if page == "Watchlist":
    st.subheader("Bond Watchlist")

    all_bonds = sorted(df["Symbol"].dropna().unique())

    selected = st.multiselect(
        "Select bonds to track",
        options=all_bonds,
        default=st.session_state.watchlist
    )

    st.session_state.watchlist = selected

    if not selected:
        st.info("Add bonds to build your watchlist.")
    else:
        watch_df = df[df["Symbol"].isin(selected)]

        st.dataframe(
            watch_df.sort_values("Volume", ascending=False),
            use_container_width=True
        )

        st.markdown("### Liquidity Signals")
        st.write(
            watch_df[
                ["Symbol", "High Volume", "Large Bid", "Wide Spread"]
            ]
        )
