import streamlit as st
import pandas as pd
import requests
from io import StringIO
from datetime import datetime, timedelta

st.set_page_config(page_title="Bond Dashboard", layout="wide")
st.title("Composite Edge – Live Bond Market")

@st.cache_data(ttl=10)
def fetch_master_debt():
    url = "https://nsearchives.nseindia.com/content/equities/DEBT.csv"
    df = pd.read_csv(StringIO(requests.get(url).text))
    df = df[["SYMBOL", " IP RATE", " REDEMPTION DATE"]]
    df.rename(columns={"SYMBOL": "Symbol"}, inplace=True)
    return df

@st.cache_data(ttl=5)
def fetch_live_bonds():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    })
    session.get("https://www.nseindia.com")

    url = "https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec"
    data = session.get(url).json()["data"]

    rows = []
    for d in data:
        rows.append({
            "Symbol": d["symbol"],
            "Series": d["series"],
            "Bid Price": d["buyPrice1"],
            "Bid Qty": d["buyQuantity1"],
            "Ask Price": d["sellPrice1"],
            "Ask Qty": d["sellQuantity1"],
            "Volume": d["totalTradedVolume"],
            "VWAP": d["averagePrice"]
        })

    return pd.DataFrame(rows)

master = fetch_master_debt()
live = fetch_live_bonds()

df = live.merge(master, on="Symbol", how="left")
df = df[df["Volume"] > 0]

st.subheader("Live Traded Bonds (Top of Book)")
st.dataframe(df, use_container_width=True)

