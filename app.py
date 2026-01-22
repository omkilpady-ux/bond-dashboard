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
# SIDEBAR
# =====================================================
st.sidebar.header("Controls")

series_filter = st.sidebar.multiselect(
    "Series",
    ["GS", "SG"],
    default=["GS"]
)

if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()

# =====================================================
# INDIAN SETTLEMENT DATE (T+1)
# =====================================================
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

# =====================================================
# 30/360 US (EXCEL MATCH)
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
# MASTER DATA
# =====================================================
@st.cache_data(ttl=24 * 3600)
def load_master():
    df = pd.read_csv("master_debt.csv")
    df.columns = df.columns.str.strip().str.upper()
    df = df[["SYMBOL", "IP RATE", "REDEMPTION DATE"]]
    df.rename(columns={"SYMBOL": "Symbol", "IP RATE": "Coupon"}, inplace=True)

    df["REDEMPTION DATE"] = pd.to_datetime(
        df["REDEMPTION DATE"], dayfirst=True, errors="coerce"
    ).dt.date

    df = df.dropna(subset=["REDEMPTION DATE"])
    df["Years"] = (
        pd.to_datetime(df["REDEMPTION DATE"]) - pd.to_datetime(SETTLEMENT)
    ).dt.days / 365

    return df[df["Years"] > 0]

# =====================================================
# LIVE NSE DATA
# =====================================================
@st.cache_data(ttl=5)
def load_live():
    rows = []
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        s.get("https://www.nseindia.com", timeout=10)

        url = "https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec"
        data = s.get(url, timeout=10).json().get("data", [])

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
                "Dirty": last_px if last_px != 0 else avg_px,
                "Volume": d.get("totalTradedVolume") or 0,
            })
    except:
        pass

    return pd.DataFrame(rows)

# =====================================================
# LOAD & MERGE
# =====================================================
master = load_master()
live = load_live()

if master.empty or live.empty:
    st.warning("Live data unavailable. Refresh shortly.")
    st.stop()

df = live.merge(master, on="Symbol", how="left")
df = df[df["Series"].isin(series_filter)]
df = df.dropna(subset=["Coupon", "Dirty", "Years"])

# =====================================================
# ACCRUED INTEREST
# =====================================================
def last_coupon_date(red):
    d = red
    while d > SETTLEMENT:
        d -= relativedelta(months=6)
    return d

df["Last Coupon"] = df["REDEMPTION DATE"].apply(last_coupon_date)
df["Days Since"] = df.apply(
    lambda r: days360_us(r["Last Coupon"], SETTLEMENT),
    axis=1
)
df["Accrued"] = df["Days Since"] * df["Coupon"] / 360
df["Clean"] = df["Dirty"] - df["Accrued"]

# =====================================================
# YTM (CLEAN LTP)
# =====================================================
def calc_ytm(r):
    try:
        return npf.rate(
            r["Years"] * 2,
            r["Coupon"] / 2,
            -r["Clean"],
            100
        ) * 2 * 100
    except:
        return None

df["YTM"] = df.apply(calc_ytm, axis=1)

df["RelVal(bps)"] = (
    df["YTM"] - df.groupby("Series")["YTM"].transform("mean")
) * 100

# =====================================================
# ALERT LOGIC (TOP-LEVEL, SAFE)
# =====================================================
def alert_status(r):
    a = st.session_state.alerts.get(r["Symbol"])
    if not a or a["target"] == 0:
        return "—"

    side = a["side"]
    target = a["target"]
    tol = a["tolerance"]

    if side == "SELL":
        bid = r["Bid"]
        if bid >= target:
            return "HIT"
        elif (target - bid) <= tol:
            return "NEAR"
        else:
            return "FAR"

    if side == "BUY":
        ask = r["Ask"]
        if ask <= target:
            return "HIT"
        elif (ask - target) <= tol:
            return "NEAR"
        else:
            return "FAR"

    return "—"

# =====================================================
# MARKET VIEW
# =====================================================
st.subheader("Market View")

cols = [
    "Symbol", "Series", "Bid", "Ask", "LTP", "Volume",
    "Dirty", "Accrued", "Clean", "YTM", "RelVal(bps)"
]

st.dataframe(
    df[cols].sort_values("RelVal(bps)", ascending=False),
    use_container_width=True
)

# =====================================================
# WATCHLIST
# =====================================================
st.subheader("Watchlist")

all_symbols = sorted(df["Symbol"].unique())

quick_add = st.selectbox("Add bond", [""] + all_symbols)
if quick_add and quick_add not in st.session_state.watchlist:
    st.session_state.watchlist.append(quick_add)

paste = st.text_area(
    "Paste from Excel (one per line)",
    placeholder="754GS2036\n709GS2054"
)

if st.button("➕ Add pasted"):
    items = [x.strip().upper() for x in paste.splitlines() if x.strip()]
    st.session_state.watchlist = list(dict.fromkeys(
        st.session_state.watchlist + items
    ))

# =====================================================
# ALERT SETUP (CLEAN)
# =====================================================
st.markdown("### 🎯 Alert Setup")

alert_sym = st.selectbox(
    "Bond",
    [""] + st.session_state.watchlist
)

if alert_sym:
    c1, c2, c3 = st.columns(3)
    with c1:
        side = st.selectbox("Side", ["BUY", "SELL"])
    with c2:
        target = st.number_input("Target", format="%.2f")
    with c3:
        tol = st.number_input("Tolerance", value=0.02, format="%.2f")

    if st.button("💾 Save Alert"):
        st.session_state.alerts[alert_sym] = {
            "side": side,
            "target": target,
            "tolerance": tol
        }

# =====================================================
# WATCHLIST TABLE
# =====================================================
if st.session_state.watchlist:
    wdf = df[df["Symbol"].isin(st.session_state.watchlist)].copy()
    wdf["ALERT"] = wdf.apply(alert_status, axis=1)

    def style(v):
        if v == "HIT":
            return "background-color:#ff4d4d;color:white;"
        if v == "NEAR":
            return "background-color:#ffa500;"
        if v == "FAR":
            return "background-color:#e0e0e0;"
        return ""

    st.dataframe(
        wdf[cols + ["ALERT"]]
        .style.applymap(style, subset=["ALERT"]),
        use_container_width=True
    )

    remove = st.multiselect("Remove bonds", st.session_state.watchlist)
    if st.button("❌ Remove"):
        st.session_state.watchlist = [
            x for x in st.session_state.watchlist if x not in remove
        ]
else:
    st.info("Watchlist empty.")
