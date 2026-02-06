import streamlit as st
import pandas as pd
import requests
import numpy_financial as npf
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import base64
import json
from pathlib import Path
import time
import random

# =====================================================
# PERSISTENCE
# =====================================================
STATE_FILE = Path("user_state.json")
HISTORY_FILE = Path("yield_history.json")
MANUAL_PRICES_FILE = Path("manual_prices.json")

def load_persistent_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"watchlist": [], "alerts": {}}

def save_persistent_state():
    with open(STATE_FILE, "w") as f:
        json.dump(
            {
                "watchlist": st.session_state.watchlist,
                "alerts": st.session_state.alerts,
            },
            f,
        )

def load_yield_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return {}

def save_yield_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

def load_manual_prices():
    if MANUAL_PRICES_FILE.exists():
        with open(MANUAL_PRICES_FILE, "r") as f:
            return json.load(f)
    return {}

def save_manual_prices(prices):
    with open(MANUAL_PRICES_FILE, "w") as f:
        json.dump(prices, f)

# =====================================================
# PAGE SETUP
# =====================================================
st.set_page_config(page_title="Bond Market Monitor", layout="wide")
st.title("Composite Edge – Bond Market Monitor")
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

# =====================================================
# SESSION STATE INIT (PERSISTENT)
# =====================================================
if "initialized" not in st.session_state:
    persisted = load_persistent_state()
    st.session_state.watchlist = persisted.get("watchlist", [])
    st.session_state.alerts = persisted.get("alerts", {})
    st.session_state.last_alert_state = {}
    st.session_state.manual_prices = load_manual_prices()
    st.session_state.initialized = True

# =====================================================
# SIDEBAR - SCANNER SETTINGS
# =====================================================
st.sidebar.header("Controls")

# Data source selector
data_source = st.sidebar.radio(
    "Data Source",
    ["Auto (Try NSE)", "Manual Entry Only"],
    help="NSE often blocks automated requests. Use 'Manual Entry' if Auto fails."
)

series_filter = st.sidebar.multiselect(
    "Series",
    ["GS", "SG"],
    default=["GS"]
)

if st.sidebar.button("🔄 Refresh prices"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("📊 Scanner Settings")

yield_threshold = st.sidebar.slider(
    "Yield change threshold (%)",
    min_value=0.05,
    max_value=0.50,
    value=0.20,
    step=0.05,
    help="Alert when yield moves by this much vs 7-day average"
)

volume_multiplier = st.sidebar.slider(
    "Volume spike multiplier",
    min_value=1.5,
    max_value=5.0,
    value=2.0,
    step=0.5,
    help="Alert when volume is this many times higher than yesterday"
)

min_volume = st.sidebar.number_input(
    "Minimum volume to show",
    min_value=0,
    value=10,
    step=5,
    help="Ignore bonds with volume below this"
)

max_opportunities = st.sidebar.selectbox(
    "Show top opportunities",
    [5, 10, 15, 20, 50],
    index=1
)

# =====================================================
# SETTLEMENT DATE (INDIA T+1)
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
# SOUND HELPERS
# =====================================================
def play_near_sound():
    beep = "UklGRigAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQQAAA=="
    st.audio(base64.b64decode(beep), format="audio/wav")

def play_hit_sound():
    beep = "UklGRlIAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YSIAAAB//38AAP//"
    st.audio(base64.b64decode(beep), format="audio/wav")

# =====================================================
# MASTER DATA
# =====================================================
@st.cache_data(ttl=24 * 3600)
def load_master():
    try:
        df = pd.read_csv("master_debt.csv")
        df.columns = df.columns.str.strip().str.upper()

        df = df[["SYMBOL", "IP RATE", "REDEMPTION DATE"]]

        df.rename(
            columns={
                "SYMBOL": "Symbol",
                "IP RATE": "Coupon"
            },
            inplace=True,
        )

        df["REDEMPTION DATE"] = pd.to_datetime(
            df["REDEMPTION DATE"],
            dayfirst=True,
            errors="coerce"
        ).dt.date

        df = df.dropna(subset=["REDEMPTION DATE"])

        df["Years"] = (
            pd.to_datetime(df["REDEMPTION DATE"]) -
            pd.to_datetime(SETTLEMENT)
        ).dt.days / 365.25

        return df[df["Years"] > 0]
    except FileNotFoundError:
        st.error("⚠️ master_debt.csv not found! Please ensure the file is in the same directory.")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Error loading master data: {str(e)}")
        return pd.DataFrame()

# =====================================================
# LIVE NSE DATA - ENHANCED WITH MULTIPLE STRATEGIES
# =====================================================
@st.cache_data(ttl=5)
def load_live_nse():
    """Try multiple strategies to fetch NSE data"""
    rows = []
    
    # Strategy 1: Standard API with rotating user agents
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    
    try:
        s = requests.Session()
        
        s.headers.update({
            "User-Agent": random.choice(user_agents),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://www.nseindia.com/market-data/bonds-traded-in-capital-market",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        })
        
        # First get cookies from homepage
        s.get("https://www.nseindia.com", timeout=10)
        time.sleep(random.uniform(1, 2))
        
        # Try main API
        url = "https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec"
        resp = s.get(url, timeout=10)
        
        if resp.status_code == 200 and resp.text.strip():
            try:
                data = resp.json().get("data", [])
                
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
                
                if rows:
                    return pd.DataFrame(rows), None
                    
            except json.JSONDecodeError:
                pass
        
        # If we get here, API failed
        error_msg = f"NSE API returned status {resp.status_code}"
        if resp.status_code == 403:
            error_msg = "NSE blocked the request (403 Forbidden). Use Manual Entry mode or try again later."
        elif resp.status_code == 429:
            error_msg = "NSE rate limit exceeded (429). Wait a few minutes before trying again."
            
        return pd.DataFrame(), error_msg
        
    except requests.exceptions.Timeout:
        return pd.DataFrame(), "NSE API timeout. Server is slow or unreachable."
    except requests.exceptions.ConnectionError:
        return pd.DataFrame(), "Connection error. Check your internet connection."
    except Exception as e:
        return pd.DataFrame(), f"Unexpected error: {str(e)}"

# =====================================================
# MANUAL PRICE ENTRY SECTION
# =====================================================
def show_manual_entry_section():
    st.sidebar.markdown("---")
    st.sidebar.subheader("📝 Manual Price Entry")
    
    with st.sidebar.expander("Enter prices manually", expanded=False):
        st.markdown("**Copy prices from NSE website and paste here:**")
        
        manual_symbol = st.text_input("Symbol (e.g., GB2025)", key="manual_sym")
        
        col1, col2 = st.columns(2)
        with col1:
            manual_bid = st.number_input("Bid", min_value=0.0, format="%.2f", key="manual_bid")
            manual_ltp = st.number_input("LTP", min_value=0.0, format="%.2f", key="manual_ltp")
        with col2:
            manual_ask = st.number_input("Ask", min_value=0.0, format="%.2f", key="manual_ask")
            manual_vol = st.number_input("Volume", min_value=0, step=1, key="manual_vol")
        
        if st.button("💾 Save Price", key="save_manual"):
            if manual_symbol:
                st.session_state.manual_prices[manual_symbol.upper()] = {
                    "Bid": manual_bid,
                    "Ask": manual_ask,
                    "LTP": manual_ltp,
                    "Volume": manual_vol,
                    "timestamp": datetime.now().isoformat()
                }
                save_manual_prices(st.session_state.manual_prices)
                st.success(f"✅ Saved prices for {manual_symbol}")
                st.rerun()
        
        if st.session_state.manual_prices:
            st.markdown("**Recently entered:**")
            for sym in list(st.session_state.manual_prices.keys())[-3:]:
                data = st.session_state.manual_prices[sym]
                st.caption(f"**{sym}**: LTP {data['LTP']}")

# Show manual entry in sidebar
show_manual_entry_section()

# =====================================================
# LOAD DATA
# =====================================================
master = load_master()

if master.empty:
    st.error("Master data file (master_debt.csv) not found or empty!")
    st.stop()

# Try to get live data based on selected source
nse_error = None
if data_source == "Auto (Try NSE)":
    live_df, nse_error = load_live_nse()
else:
    live_df = pd.DataFrame()
    nse_error = "Manual mode selected - NSE API not queried"

# Merge manual prices
manual_rows = []
for symbol, data in st.session_state.manual_prices.items():
    manual_rows.append({
        "Symbol": symbol,
        "Series": "GS",  # Default to GS
        "Bid": data["Bid"],
        "Ask": data["Ask"],
        "LTP": data["LTP"],
        "Dirty": data["LTP"] if data["LTP"] > 0 else data["Bid"],
        "Volume": data["Volume"],
    })

manual_df = pd.DataFrame(manual_rows)

# Combine live and manual data
if not live_df.empty and not manual_df.empty:
    # Prefer manual prices over API prices for same symbols
    live_df = live_df[~live_df["Symbol"].isin(manual_df["Symbol"])]
    combined_live = pd.concat([live_df, manual_df], ignore_index=True)
elif not manual_df.empty:
    combined_live = manual_df
else:
    combined_live = live_df

# Show status message
if nse_error and data_source == "Auto (Try NSE)":
    if manual_df.empty:
        st.error(f"❌ {nse_error}\n\n**Solution:** Switch to 'Manual Entry Only' mode in sidebar and enter prices manually from NSE website.")
    else:
        st.warning(f"⚠️ {nse_error}\n\nShowing manually entered prices only.")

if combined_live.empty:
    st.warning("⚠️ No live data available. Showing master data only.\n\n**To get prices:** Use 'Manual Entry' in sidebar to enter prices from NSE website.")
    df = master.copy()
    df["Series"] = ""
    df["Bid"] = 0
    df["Ask"] = 0
    df["LTP"] = 0
    df["Dirty"] = 0
    df["Volume"] = 0
else:
    df = combined_live.merge(master, on="Symbol", how="left")
    if series_filter:
        df = df[df["Series"].isin(series_filter)]
    df = df.dropna(subset=["Coupon", "Years"])

# =====================================================
# LAST INTEREST PAID + ACCRUED
# =====================================================
def last_coupon_date(redemption):
    d = redemption
    while d > SETTLEMENT:
        d -= relativedelta(months=6)
    return d

df["Last Interest Paid"] = df["REDEMPTION DATE"].apply(last_coupon_date)

df["Days Since"] = df.apply(
    lambda r: days360_us(r["Last Interest Paid"], SETTLEMENT),
    axis=1,
)

df["Accrued"] = df["Days Since"] * df["Coupon"] / 360

# =====================================================
# YTM CALCULATION HELPER
# =====================================================
def calculate_ytm(price, coupon, years):
    """Generic YTM calculator"""
    if pd.isna(price) or price is None or price <= 0:
        return None
    
    if years <= 0 or coupon <= 0:
        return None
    
    try:
        ytm = npf.rate(
            nper=years * 2,
            pmt=coupon / 2,
            pv=-price,
            fv=100,
        ) * 2 * 100
        
        if -10 < ytm < 50:
            return ytm
        else:
            return None
    except:
        return None

# =====================================================
# BID YTM (SELLING YIELD)
# =====================================================
def get_bid_ytm(r):
    """YTM based on Bid price (what you get when SELLING)"""
    if r["Bid"] > 0:
        clean_bid = r["Bid"] - r["Accrued"]
        return calculate_ytm(clean_bid, r["Coupon"], r["Years"])
    elif r["LTP"] > 0:
        clean_ltp = r["LTP"] - r["Accrued"]
        return calculate_ytm(clean_ltp, r["Coupon"], r["Years"])
    return None

df["Bid YTM"] = df.apply(get_bid_ytm, axis=1)

# =====================================================
# ASK YTM (BUYING YIELD)
# =====================================================
def get_ask_ytm(r):
    """YTM based on Ask price (what you get when BUYING)"""
    if r["Ask"] > 0:
        clean_ask = r["Ask"] - r["Accrued"]
        return calculate_ytm(clean_ask, r["Coupon"], r["Years"])
    elif r["LTP"] > 0:
        clean_ltp = r["LTP"] - r["Accrued"]
        return calculate_ytm(clean_ltp, r["Coupon"], r["Years"])
    return None

df["Ask YTM"] = df.apply(get_ask_ytm, axis=1)

# =====================================================
# BID-ASK SPREAD
# =====================================================
df["Spread"] = df["Ask"] - df["Bid"]

# =====================================================
# YIELD HISTORY TRACKING
# =====================================================
def update_yield_history(df):
    """Track 7-day yield history"""
    history = load_yield_history()
    today = datetime.now().strftime("%Y-%m-%d")
    
    if today not in history:
        history[today] = {}
    
    for _, row in df.iterrows():
        sym = row["Symbol"]
        if pd.notna(row["Bid YTM"]):
            if sym not in history[today]:
                history[today][sym] = {
                    "bid_ytm": row["Bid YTM"],
                    "ask_ytm": row["Ask YTM"] if pd.notna(row["Ask YTM"]) else None,
                    "volume": row["Volume"]
                }
    
    # Keep only last 7 days
    all_dates = sorted(history.keys())
    if len(all_dates) > 7:
        for old_date in all_dates[:-7]:
            del history[old_date]
    
    save_yield_history(history)
    return history

history = update_yield_history(df)

# =====================================================
# 7-DAY AVERAGE YIELD
# =====================================================
def get_7d_avg_yield(symbol, history):
    """Calculate 7-day average for Bid YTM and Ask YTM"""
    bid_yields = []
    ask_yields = []
    volumes = []
    
    for date_data in history.values():
        if symbol in date_data:
            if date_data[symbol]["bid_ytm"]:
                bid_yields.append(date_data[symbol]["bid_ytm"])
            if date_data[symbol]["ask_ytm"]:
                ask_yields.append(date_data[symbol]["ask_ytm"])
            if date_data[symbol]["volume"]:
                volumes.append(date_data[symbol]["volume"])
    
    return {
        "bid_avg": sum(bid_yields) / len(bid_yields) if bid_yields else None,
        "ask_avg": sum(ask_yields) / len(ask_yields) if ask_yields else None,
        "vol_avg": sum(volumes) / len(volumes) if volumes else None
    }

df["7D Avg"] = df["Symbol"].apply(lambda s: get_7d_avg_yield(s, history))

# =====================================================
# OPPORTUNITY SCANNER
# =====================================================
def generate_opportunities(df, threshold, vol_mult, min_vol):
    """Generate trading opportunities"""
    opportunities = []
    
    for _, r in df.iterrows():
        if r["Volume"] < min_vol:
            continue
        
        avg_data = r["7D Avg"]
        signals = []
        
        # High Ask YTM = BUY opportunity
        if pd.notna(r["Ask YTM"]) and avg_data["ask_avg"]:
            diff = r["Ask YTM"] - avg_data["ask_avg"]
            if diff > threshold:
                signals.append({
                    "Symbol": r["Symbol"],
                    "Bid YTM": r["Bid YTM"],
                    "Ask YTM": r["Ask YTM"],
                    "Signal": "🟢 BUY",
                    "Reason": f"Ask YTM +{diff:.2f}% vs 7D avg",
                    "Priority": diff
                })
        
        # Low Bid YTM = SELL opportunity
        if pd.notna(r["Bid YTM"]) and avg_data["bid_avg"]:
            diff = avg_data["bid_avg"] - r["Bid YTM"]
            if diff > threshold:
                signals.append({
                    "Symbol": r["Symbol"],
                    "Bid YTM": r["Bid YTM"],
                    "Ask YTM": r["Ask YTM"],
                    "Signal": "🔴 SELL",
                    "Reason": f"Bid YTM -{diff:.2f}% vs 7D avg",
                    "Priority": diff
                })
        
        # Volume spike
        if avg_data["vol_avg"] and r["Volume"] > avg_data["vol_avg"] * vol_mult:
            mult = r["Volume"] / avg_data["vol_avg"]
            signals.append({
                "Symbol": r["Symbol"],
                "Bid YTM": r["Bid YTM"],
                "Ask YTM": r["Ask YTM"],
                "Signal": "⚡ VOLUME",
                "Reason": f"Volume {mult:.1f}x avg",
                "Priority": mult
            })
        
        # Tight spread = good liquidity
        if r["Spread"] > 0 and r["Spread"] < 0.10:
            signals.append({
                "Symbol": r["Symbol"],
                "Bid YTM": r["Bid YTM"],
                "Ask YTM": r["Ask YTM"],
                "Signal": "💎 LIQUID",
                "Reason": f"Tight spread ({r['Spread']:.2f})",
                "Priority": 0.10 - r["Spread"]
            })
        
        opportunities.extend(signals)
    
    # Sort by priority and return top N
    opportunities.sort(key=lambda x: x["Priority"], reverse=True)
    return opportunities[:max_opportunities]

opportunities = generate_opportunities(df, yield_threshold, volume_multiplier, min_volume)

# =====================================================
# OPPORTUNITY SCANNER DISPLAY
# =====================================================
st.subheader("🚨 Opportunity Scanner")

if opportunities:
    opp_df = pd.DataFrame(opportunities)
    opp_df = opp_df[["Symbol", "Bid YTM", "Ask YTM", "Signal", "Reason"]]
    
    # Format YTM columns
    opp_df["Bid YTM"] = opp_df["Bid YTM"].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "—")
    opp_df["Ask YTM"] = opp_df["Ask YTM"].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "—")
    
    st.dataframe(
        opp_df,
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("No opportunities detected with current settings. Try adjusting scanner thresholds in sidebar.")

# =====================================================
# ALERT LOGIC
# =====================================================
def alert_status(r):
    a = st.session_state.alerts.get(r["Symbol"])
    if not a or a["target"] == 0:
        return "—"

    side, target, tol = a["side"], a["target"], a["tolerance"]

    if side == "SELL":
        bid = r["Bid"]
        if bid == 0:
            return "—"
        if bid >= target:
            return "HIT"
        elif (target - bid) <= tol:
            return "NEAR"
        else:
            return "FAR"

    if side == "BUY":
        ask = r["Ask"]
        if ask == 0:
            return "—"
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
    "Spread", "Accrued", "Bid YTM", "Ask YTM"
]

# Format display
display_df = df[cols].copy()
display_df["Bid YTM"] = display_df["Bid YTM"].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "—")
display_df["Ask YTM"] = display_df["Ask YTM"].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "—")

st.dataframe(display_df, use_container_width=True)

# =====================================================
# WATCHLIST
# =====================================================
st.subheader("Watchlist")

all_symbols = sorted(df["Symbol"].unique())

quick_add = st.selectbox(
    "Add bond (type to search)",
    [""] + all_symbols
)

if quick_add and quick_add not in st.session_state.watchlist:
    st.session_state.watchlist.append(quick_add)
    save_persistent_state()

paste = st.text_area("Paste from Excel (one per line)")

if st.button("➕ Add pasted"):
    items = [x.strip().upper() for x in paste.splitlines() if x.strip()]
    st.session_state.watchlist = list(
        dict.fromkeys(st.session_state.watchlist + items)
    )
    save_persistent_state()

# =====================================================
# ALERT SETUP
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
            "tolerance": tol,
        }
        save_persistent_state()

# =====================================================
# WATCHLIST TABLE + SOUND
# =====================================================
if st.session_state.watchlist:
    wdf = df[df["Symbol"].isin(st.session_state.watchlist)].copy()
    wdf["ALERT"] = wdf.apply(alert_status, axis=1)

    for _, r in wdf.iterrows():
        sym = r["Symbol"]
        new = r["ALERT"]
        old = st.session_state.last_alert_state.get(sym)

        if new != old:
            if new == "NEAR":
                play_near_sound()
            elif new == "HIT":
                play_hit_sound()

        st.session_state.last_alert_state[sym] = new

    def style(v):
        if v == "HIT":
            return "background-color:#ff4d4d;color:white;"
        if v == "NEAR":
            return "background-color:#ffa500;"
        if v == "FAR":
            return "background-color:#e0e0e0;"
        return ""

    wcols = [
        "Symbol", "Series", "Bid", "Ask", "LTP", "Volume",
        "Spread", "Bid YTM", "Ask YTM", "ALERT"
    ]

    wdf_display = wdf[wcols].copy()
    wdf_display["Bid YTM"] = wdf_display["Bid YTM"].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "—")
    wdf_display["Ask YTM"] = wdf_display["Ask YTM"].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "—")

    st.dataframe(
        wdf_display.style.applymap(style, subset=["ALERT"]),
        use_container_width=True,
    )

    remove = st.multiselect("Remove bonds", st.session_state.watchlist)

    if st.button("❌ Remove"):
        st.session_state.watchlist = [
            x for x in st.session_state.watchlist if x not in remove
        ]
        save_persistent_state()
else:
    st.info("Watchlist empty.")

# =====================================================
# HELP SECTION
# =====================================================
with st.expander("ℹ️ Help - NSE Access Issues"):
    st.markdown("""
    ### Why NSE blocks automated requests:
    NSE's website actively blocks automated scripts to prevent overload. This is normal and expected.
    
    ### Solutions:
    
    **Option 1: Manual Entry (Recommended)**
    1. Set Data Source to "Manual Entry Only" in sidebar
    2. Open NSE website manually in browser: https://www.nseindia.com/market-data/bonds-traded-in-capital-market
    3. Copy prices for bonds you care about
    4. Enter them in the "Manual Price Entry" section in sidebar
    
    **Option 2: Try Auto mode at different times**
    - NSE blocking varies by time of day
    - Try early morning or late evening
    - Wait 10-15 minutes between attempts
    
    **Option 3: Use a VPN**
    - Sometimes changing your IP helps
    - Not guaranteed to work
    
    The manual entry method is most reliable and lets you track exactly the bonds you care about!
    """)
