import streamlit as st
import pandas as pd
import requests
from datetime import datetime

# ---------------- PAGE SETUP ----------------
st.set_page_config(page_title="Live Bond Market", layout="wide")
st.title("Composite Edge – Live Bond Market")

# Manual refresh button
if st.button("🔄 Refresh now"):
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

        # NSE warm-up
        session.get("https://www.nseindia.com", timeout=10)

        url = "https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec"
        resp = session.get(url, timeout=10)
        data = resp.json().get("data", [])

        rows = []
        for d in data:
            rows.append({
                "Symbol": d.get("symbol"),
                "Series": d.get("series"),
                "Bid Price": d.get("buyPrice1"),
                "Bid Qty": d.get("buyQuantity1"),
                "Ask Price": d.get("sellPrice1"),
                "Ask Qty": d.get("sellQuantity1"),
                "VWAP": d.get("averagePrice"),
                "Volume": d.get("totalTradedVolume"),
            })

        return pd.DataFrame(rows)

    except:
        return pd.DataFrame()

# ---------------- LOAD DATA ----------------
df = fetch_live_bonds()

if df.empty:
    st.warning("Live NSE data not available right now. Click refresh.")
    st.stop()

# ---------------- FILTER TABS ----------------
tabs = st.tabs(["GS", "SG", "TB", "Selling"])

with tabs[0]:
    st.subheader("Government Securities (GS)")
    st.dataframe(df[df["Series"] == "GS"], use_container_width=True)

with tabs[1]:
    st.subheader("State Government Bonds (SG)")
    st.dataframe(df[df["Series"] == "SG"], use_container_width=True)

with tabs[2]:
    st.subheader("Treasury Bills (TB)")
    st.dataframe(df[df["Series"] == "TB"], use_container_width=True)

with tabs[3]:
    st.subheader("Selling – Bond Watchlist")

    bond_options = sorted(df["Symbol"].dropna().unique())

    selected_bonds = st.multiselect(
        "Select bonds to track",
        options=bond_options,
        default=[
            "754GS2036",
            "699GS2051"
        ]
    )

    if selected_bonds:
        st.dataframe(
            df[df["Symbol"].isin(selected_bonds)],
            use_container_width=True
        )
    else:
        st.info("Select bonds from the list above.")
