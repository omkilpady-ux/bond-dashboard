import streamlit as st
import pandas as pd
import requests
import time
from datetime import datetime

# ---------------- PAGE SETUP ----------------
st.set_page_config(page_title="Live Bond Market", layout="wide")
st.title("Composite Edge – Live Bond Market")
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")
time.sleep(5)
st.experimental_rerun()

# ---------------- LIVE NSE DATA ----------------
@st.cache_data(ttl=5)
def fetch_live_bonds():
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        })

        # Warm-up call (NSE requirement)
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

    except Exception as e:
        return pd.DataFrame()

# ---------------- LOAD DATA ----------------
live = fetch_live_bonds()

if not isinstance(live, pd.DataFrame) or live.empty:
    st.warning("Live NSE data not available right now. Retrying automatically.")
    st.stop()

# ---------------- FILTER TABS ----------------
tabs = st.tabs(["GS", "SG", "TB", "Selling"])

with tabs[0]:
    st.subheader("Government Securities (GS)")
    st.dataframe(
        live[live["Series"] == "GS"],
        use_container_width=True
    )

with tabs[1]:
    st.subheader("State Government Bonds (SG)")
    st.dataframe(
        live[live["Series"] == "SG"],
        use_container_width=True
    )

with tabs[2]:
    st.subheader("Treasury Bills (TB)")
    st.dataframe(
        live[live["Series"] == "TB"],
        use_container_width=True
    )

with tabs[3]:
    st.subheader("Selling – Liquidity Check")

    sell_list = [
        "754GS2036",
        "699GS2051",
        "726KA25",
        "774GA32"
    ]

    st.dataframe(
        live[live["Symbol"].isin(sell_list)],
        use_container_width=True
    )
