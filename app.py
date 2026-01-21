import streamlit as st
import pandas as pd
import requests
import numpy_financial as npf
from io import StringIO
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from days360 import days360

# ---------------- UI SETUP ----------------
st.set_page_config(page_title="Bond Dashboard", layout="wide")
st.title("Composite Edge – Live Bond Market")
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

# ---------------- SETTLEMENT DATE ----------------
def get_settlement_date():
    today = datetime.today()
    wd = today.weekday()
    if wd == 4:      # Friday
        return today + timedelta(days=3)
    elif wd == 5:    # Saturday
        return today + timedelta(days=2)
    else:
        return today + timedelta(days=1)

# ---------------- MASTER DEBT (SAFE) ----------------
@st.cache_data(ttl=3600)
def fetch_master_debt():
    url = "https://nsearchives.nseindia.com/content/equities/DEBT.csv"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text))
    except:
        st.warning("Using cached master debt file")
return pd.read_csv("master_debt.csv")

    df = df[["SYMBOL", " IP RATE", " REDEMPTION DATE"]]
    df.rename(columns={"SYMBOL": "Symbol"}, inplace=True)

    settlement = get_settlement_date()

    def last_coupon_date(redemption):
        rd = datetime.strptime(redemption, "%d-%b-%Y")
        while rd > settlement:
            rd -= relativedelta(months=6)
        return rd

    df["Last Coupon"] = df[" REDEMPTION DATE"].apply(last_coupon_date)
    df["Days Accrued"] = df["Last Coupon"].apply(
        lambda x: days360(x, settlement, method="US")
    )
    df["Accrued Interest"] = (df[" IP RATE"] / 360) * df["Days Accrued"]
    df["Years"] = (
        pd.to_datetime(df[" REDEMPTION DATE"]) - settlement
    ).dt.days / 365

    return df

# ---------------- LIVE NSE DATA ----------------
@st.cache_data(ttl=5)
def fetch_live_bonds():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    })

    # warm-up call
    session.get("https://www.nseindia.com")

    url = "https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec"
    response = session.get(url, timeout=10)
    data = response.json()["data"]

    rows = []
    for d in data:
        rows.append({
            "Symbol": d["symbol"],
            "Series": d["series"],
            "Bid": d["buyPrice1"],
            "Bid Qty": d["buyQuantity1"],
            "Ask": d["sellPrice1"],
            "Ask Qty": d["sellQuantity1"],
            "VWAP": d["averagePrice"],
            "Volume": d["totalTradedVolume"],
        })

    return pd.DataFrame(rows)

# ---------------- YIELD LOGIC ----------------
def calc_yield(row, price):
    if price <= 0 or row["Years"] <= 0:
        return None

    # Coupon bond
    if row[" IP RATE"] > 0:
        return npf.rate(
            row["Years"] * 2,
            row[" IP RATE"] / 2,
            -price,
            100
        ) * 2 * 100

    # T-Bill
    return (100 - price) / price * (365 / (row["Years"] * 365)) * 100

# ---------------- LOAD DATA ----------------
master = fetch_master_debt()
live = fetch_live_bonds()

if master.empty or live.empty:
    st.stop()

df = live.merge(master, on="Symbol", how="left")
df = df[df["Volume"] > 0]

df["Clean Bid"] = df["Bid"] - df["Accrued Interest"]
df["Clean Ask"] = df["Ask"] - df["Accrued Interest"]

df["Bid Yield %"] = df.apply(lambda x: calc_yield(x, x["Clean Bid"]), axis=1)
df["Ask Yield %"] = df.apply(lambda x: calc_yield(x, x["Clean Ask"]), axis=1)

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
    st.subheader("Selling – Liquidity Check")
    sell_list = [
        "754GS2036",
        "699GS2051",
        "726KA25",
        "774GA32"
    ]
    st.dataframe(df[df["Symbol"].isin(sell_list)], use_container_width=True)

