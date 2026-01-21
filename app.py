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

# ================= INDIAN SETTLEMENT DATE (T+1) =================
def get_settlement_date():
    today = datetime.today().date()
    wd = today.weekday()

    if wd <= 3:       # Mon–Thu
        return today + timedelta(days=1)
    elif wd == 4:     # Friday
        return today + timedelta(days=3)
    else:             # Saturday
        return today + timedelta(days=2)

SETTLEMENT = get_settlement_date()

# ================= LOAD MASTER DATA =================
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

# ================= ACCRUED INTEREST =================
def last_coupon_date(redemption):
    dt = redemption
    while dt > SETTLEMENT:
        dt -= relativedelta(months=6)
    return dt

def next_coupon_date(last_coupon):
    return last_coupon + relativedelta(months=6)

df["Last Coupon Date"] = df["REDEMPTION DATE"].apply(last_coupon_date)
df["Next Coupon Date"] = df["Last Coupon Date"].apply(next_coupon_date)

df["Days Since Coupon"] = (SETTLEMENT - df["Last Coupon Date"]).apply(lambda x: x.days)
df["Days Between Coupons"] = (
    df["Next Coupon Date"] - df["Last Coupon Date"]
).apply(lambda x: x.days)

df["Accrued Interest"] = (
    df["Coupon"] * df["Days Since Coupon"] / df["Days Between Coupons"]
)

df["Dirty Price"] = df["VWAP"]
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

# ================= MATURITY BUCKETS =================
def maturity_bucket(y):
    if y < 3:
        return "0–3Y"
    elif y < 5:
        return "3–5Y"
    elif y < 7:
        return "5–7Y"
    elif y < 10:
        return "7–10Y"
    else:
        return "10Y+"

df["Bucket"] = df["Years to Maturity"].apply(maturity_bucket)

bucket_avg = (
    df.groupby("Bucket")["YTM (%)"]
    .mean()
    .reset_index()
    .rename(columns={"YTM (%)": "Bucket Avg YTM"})
)

df = df.merge(bucket_avg, on="Bucket", how="left")
df["Rel Value (bps)"] = (df["YTM (%)"] - df["Bucket Avg YTM"]) * 100

# ================= MARKET PAGE =================
if page == "Market":
    st.subheader("Market Scanner")

    st.dataframe(
        df.sort_values("Rel Value (bps)", ascending=False),
        use_container_width=True
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
            watch_df.sort_values("Rel Value (bps)", ascending=False),
            use_container_width=True
        )
