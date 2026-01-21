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
# INDIAN SETTLEMENT DATE (T+1, WEEKEND ADJUSTED)
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
# 30/360 US DAY COUNT (MANUAL, NO LIBRARIES)
# =====================================================
def days360_us(start_date, end_date):
    d1 = start_date.day
    d2 = end_date.day
    m1 = start_date.month
    m2 = end_date.month
    y1 = start_date.year
    y2 = end_date.year

    if d1 == 31:
        d1 = 30
    if d2 == 31 and d1 == 30:
        d2 = 30

    return (360 * (y2 - y1)) + (30 * (m2 - m1)) + (d2 - d1)

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

    df = df[df["Years to Maturity"] > 0]

    return df

# =====================================================
# LOAD LIVE NSE DATA
# =====================================================
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

# =====================================================
# LOAD & MERGE
# =====================================================
master = load_master()
live = load_live()

if master.empty or live.empty:
    st.warning("Live data not available.")
    st.stop()

df = live.merge(master, on="Symbol", how="left")
df = df[df["Series"].isin(series_filter)]
df = df.dropna(subset=["Coupon", "Years to Maturity", "Dirty Price"])

# =====================================================
# ACCRUED INTEREST (MATCHES EXCEL EXACTLY)
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

# Coupon is annual %, price per 100
df["Accrued Interest"] = df["Days Since Coupon"] * df["Coupon"] / 360

df["Clean Price"] = df["Dirty Price"] - df["Accrued Interest"]

# =====================================================
# YTM (CLEAN PRICE)
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
# WATCHLIST (PASTE FROM EXCEL)
# =====================================================
st.subheader("Watchlist")
# ---- Quick search & add (autocomplete) ----
all_symbols = sorted(df["Symbol"].unique())

quick_add = st.selectbox(
    "Quick add (type to search)",
    options=[""] + all_symbols,
    index=0
)

if quick_add:
    if quick_add not in st.session_state.watchlist:
        st.session_state.watchlist.append(quick_add)
st.markdown("**Paste bond symbols (one per line) from Excel:**")

paste_input = st.text_area(
    "Paste symbols here",
    placeholder="754GS2036\n699GS2051\n726KA25"
)

if st.button("➕ Add to Watchlist"):
    pasted = [x.strip().upper() for x in paste_input.splitlines() if x.strip()]
    st.session_state.watchlist = list(
        set(st.session_state.watchlist + pasted)
    )

if st.session_state.watchlist:
   watch_df = df[df["Symbol"].isin(st.session_state.watchlist)].copy()

# ---- Alert inputs ----
st.markdown("### Alert Setup")

if "alerts" not in st.session_state:
    st.session_state.alerts = {}

for sym in watch_df["Symbol"]:
    if sym not in st.session_state.alerts:
        st.session_state.alerts[sym] = {
            "side": "SELL",
            "target": None,
            "tolerance": 0.02
        }

for sym in watch_df["Symbol"]:
    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        st.session_state.alerts[sym]["side"] = st.selectbox(
            f"{sym} Side",
            ["SELL", "BUY"],
            index=0 if st.session_state.alerts[sym]["side"] == "SELL" else 1,
            key=f"{sym}_side"
        )

    with col2:
        st.session_state.alerts[sym]["target"] = st.number_input(
            f"{sym} Target",
            value=st.session_state.alerts[sym]["target"] or 0.0,
            format="%.2f",
            key=f"{sym}_target"
        )

    with col3:
        st.session_state.alerts[sym]["tolerance"] = st.number_input(
            f"{sym} Tolerance",
            value=st.session_state.alerts[sym]["tolerance"],
            format="%.2f",
            key=f"{sym}_tol"
        )

# ---- Compute alert status ----
def alert_status(row):
    a = st.session_state.alerts.get(row["Symbol"])
    if not a or not a["target"]:
        return "—"

    side = a["side"]
    target = a["target"]
    tol = a["tolerance"]

    if side == "SELL":
        px = row["Bid"]
        if px >= target:
            return "HIT"
        elif target - px <= tol:
            return "NEAR"
        else:
            return "FAR"

    if side == "BUY":
        px = row["Ask"]
        if px <= target:
            return "HIT"
        elif px - target <= tol:
            return "NEAR"
        else:
            return "FAR"

watch_df["ALERT STATUS"] = watch_df.apply(alert_status, axis=1)

# ---- Styling ----
def alert_style(val):
    if val == "HIT":
        return "background-color: #ff4d4d; color: white;"
    if val == "NEAR":
        return "background-color: #ffa500; color: black;"
    if val == "FAR":
        return "background-color: #e0e0e0;"
    return ""

st.dataframe(
    watch_df[display_cols + ["ALERT STATUS"]]
    .style.applymap(alert_style, subset=["ALERT STATUS"]),
    use_container_width=True
)

    # ---- Remove bonds from watchlist ----
    st.markdown("**Remove bonds from watchlist:**")

    to_remove = st.multiselect(
        "Select bonds to remove",
        options=st.session_state.watchlist
    )

    if st.button("❌ Remove selected"):
        st.session_state.watchlist = [
            b for b in st.session_state.watchlist if b not in to_remove
        ]

else:
    st.info("Watchlist is empty. Use search or paste from Excel.")
