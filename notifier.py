import streamlit as st
import pandas as pd
import requests
import numpy_financial as npf
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import json
from pathlib import Path

# ===================== FILE SAVE =====================
STATE_FILE = Path("user_state.json")

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"watchlist": [], "alerts": {}}

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump({
            "watchlist": st.session_state.watchlist,
            "alerts": st.session_state.alerts
        }, f)

# ===================== PAGE =====================
st.set_page_config(layout="wide")
st.title("Bond Monitor")

# ===================== STATE =====================
if "init" not in st.session_state:
    s = load_state()
    st.session_state.watchlist = s["watchlist"]
    st.session_state.alerts = s["alerts"]
    st.session_state.init = True

# ===================== SETTLEMENT =====================
def settlement():
    d = datetime.today().date()
    if d.weekday() <= 3:
        return d + timedelta(days=1)
    if d.weekday() == 4:
        return d + timedelta(days=3)
    return d + timedelta(days=2)

SETTLE = settlement()

# ===================== DAY COUNT =====================
def days360(start, end):
    d1, d2 = start.day, end.day
    m1, m2 = start.month, end.month
    y1, y2 = start.year, end.year
    if d1 == 31: d1 = 30
    if d2 == 31 and d1 == 30: d2 = 30
    return 360*(y2-y1) + 30*(m2-m1) + (d2-d1)

# ===================== MASTER =====================
@st.cache_data
def load_master():
    df = pd.read_csv("master_debt.csv")
    df.columns = df.columns.str.strip().str.upper()
    df = df[["SYMBOL","IP RATE","REDEMPTION DATE"]]
    df.rename(columns={"SYMBOL":"Symbol","IP RATE":"Coupon"}, inplace=True)
    df["REDEMPTION DATE"] = pd.to_datetime(df["REDEMPTION DATE"], dayfirst=True).dt.date
    df["Years"] = (pd.to_datetime(df["REDEMPTION DATE"]) - pd.to_datetime(SETTLE)).dt.days/365
    return df[df["Years"]>0]

# ===================== LIVE =====================
@st.cache_data(ttl=5)
def load_live():
    rows=[]
    try:
        s=requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0"})
        s.get("https://www.nseindia.com")
        data=s.get("https://www.nseindia.com/api/liveBonds-traded-on-cm?type=gsec").json()["data"]
        for d in data:
            rows.append({
                "Symbol":d["symbol"],
                "Bid":d["buyPrice1"] or 0,
                "Ask":d["sellPrice1"] or 0,
                "LTP":d["lastPrice"] or 0
            })
    except:
        pass
    return pd.DataFrame(rows)

master = load_master()
live = load_live()
df = live.merge(master,on="Symbol",how="left").dropna()

# ===================== PRICING =====================
def last_coupon(m):
    d=m
    while d>SETTLE:
        d-=relativedelta(months=6)
    return d

df["LastCoupon"]=df["REDEMPTION DATE"].apply(last_coupon)
df["Days"]=df.apply(lambda r: days360(r["LastCoupon"],SETTLE),axis=1)
df["Accrued"]=df["Days"]*df["Coupon"]/360
df["Clean"]=df["LTP"]-df["Accrued"]

def ytm(r):
    try:
        return npf.rate(r["Years"]*2,r["Coupon"]/2,-r["Clean"],100)*2*100
    except:
        return None

df["YTM"]=df.apply(ytm,axis=1)

# ===================== UI =====================
st.subheader("Market")
st.dataframe(df[["Symbol","Bid","Ask","LTP","Clean","YTM"]])

st.subheader("Watchlist")

add = st.selectbox("Add bond", [""]+sorted(df["Symbol"].unique()))
if add and add not in st.session_state.watchlist:
    st.session_state.watchlist.append(add)
    save_state()

paste = st.text_area("Paste bonds (one per line)")
if st.button("Add pasted"):
    for x in paste.splitlines():
        x=x.strip()
        if x and x not in st.session_state.watchlist:
            st.session_state.watchlist.append(x)
    save_state()

if st.session_state.watchlist:
    w=df[df["Symbol"].isin(st.session_state.watchlist)]
    st.dataframe(w[["Symbol","Bid","Ask","YTM"]])

st.subheader("Alerts")

sym = st.selectbox("Bond", [""]+st.session_state.watchlist)
if sym:
    side = st.selectbox("Side",["BUY","SELL"])
    target = st.number_input("Target",format="%.2f")
    tol = st.number_input("Tolerance",value=0.02)
    if st.button("Save Alert"):
        st.session_state.alerts[sym]={
            "side":side,
            "target":target,
            "tolerance":tol,
            "last_status":"FAR"
        }
        save_state()
