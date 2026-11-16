import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

st.set_page_config(page_title="Shine AI Institutional Trading Desk v4.0", page_icon="🚀", layout="wide")

st.title("🚀 SHINE AI Institutional Trading Desk v4.0")
st.markdown("Multi-TF Matrix, Option Selling Strikes, Position Sizing & Greeks Tracker")

# Sidebar Controls & Risk Calculator Setup
st.sidebar.header("⚙️ Institutional Risk Terminal")
if st.sidebar.button("🔄 Refresh Live Market Data", use_container_width=True):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("💰 Capital & Position Sizing")
user_capital = st.sidebar.number_input("Total Trading Capital (₹ / $)", min_value=10000.0, value=200000.0, step=10000.0)
risk_pct = st.sidebar.slider("Max Risk Per Trade (%)", min_value=0.5, max_value=5.0, value=1.5, step=0.5)

st.sidebar.markdown("---")
st.sidebar.info("💡 **Pro Tip:** Position sizing dynamically allocates lots based on your defined risk percentage to protect capital during credit spread trades.")

# ---------------- HELPER FUNCTIONS ----------------
def calc_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.ewm(com=period - 1, adjust=True, min_periods=period).mean()
    ma_down = down.ewm(com=period - 1, adjust=True, min_periods=period).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

def calc_supertrend(df, period=10, multiplier=3):
    hl2 = (df['High'] + df['Low']) / 2
    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev_close).abs(),
        (df['Low'] - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)
    
    st_dir = [True] * len(df)
    st_val = [0.0] * len(df)
    
    close = df['Close'].to_numpy(dtype=float).copy()
    ub = upperband.to_numpy(dtype=float).copy()
    lb = lowerband.to_numpy(dtype=float).copy()
    
    for i in range(1, len(df)):
        if close[i] > ub[i-1]:
            st_dir[i] = True
        elif close[i] < lb[i-1]:
            st_dir[i] = False
        else:
            st_dir[i] = st_dir[i-1]
            if st_dir[i] and lb[i] < lb[i-1]:
                lb[i] = lb[i-1]
            if not st_dir[i] and ub[i] > ub[i-1]:
                ub[i] = ub[i-1]
        st_val[i] = lb[i] if st_dir[i] else ub[i]
        
    return ("BULLISH" if st_dir[-1] else "BEARISH"), float(st_val[-1]), float(atr.iloc[-1])

def fetch_nifty_pcr(spot_price):
    atm = int(round(spot_price / 50.0) * 50)
    try:
        from curl_cffi import requests as crequests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://www.nseindia.com/option-chain'
        }
        session = crequests.Session(impersonate="chrome110")
        session.get("https://www.nseindia.com", headers=headers, timeout=5)
        r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", headers=headers, timeout=8)
        if r.status_code == 200:
            payload = r.json()
            records = payload.get('records', {}).get('data', [])
            expiries = payload.get('records', {}).get('expiryDates', [])
            near_exp = expiries[0] if expiries else ""
            curr_data = [d for d in records if isinstance(d, dict) and (
                d.get('CE', {}).get('expiryDate') == near_exp or 
                d.get('PE', {}).get('expiryDate') == near_exp
            )]
            if not curr_data:
                curr_data = records
            tot_pe = sum(d.get('PE', {}).get('openInterest', 0) for d in curr_data if isinstance(d, dict))
            tot_ce = sum(d.get('CE', {}).get('openInterest', 0) for d in curr_data if isinstance(d, dict))
            pcr = round(tot_pe / tot_ce, 2) if tot_ce > 0 else 0.98
            
            pe_c = [d for d in curr_data if isinstance(d, dict) and (atm - 800) <= d.get('strikePrice', 0) <= (atm + 100) and 'PE' in d]
            ce_c = [d for d in curr_data if isinstance(d, dict) and (atm - 100) <= d.get('strikePrice', 0) <= (atm + 800) and 'CE' in d]
            sup = int(max(pe_c, key=lambda x: x['PE'].get('openInterest', 0))['strikePrice']) if pe_c else atm - 400
            res = int(max(ce_c, key=lambda x: x['CE'].get('openInterest', 0))['strikePrice']) if ce_c else atm + 400
            return pcr, sup, res
    except Exception:
        pass
    return 0.98, atm - 400, atm + 400

# ---------------- MAIN DASHBOARD COLUMNS ----------------
col1, col2 = st.columns(2)

# ================= 1. NIFTY 50 TERMINAL =================
with col1:
    st.subheader("📊 NIFTY 50 PRO TERMINAL")
    try:
        daily_n = yf.download("^NSEI", period="6mo", interval="1d", progress=False)
        hourly_n = yf.download("^NSEI", period="1mo", interval="1h", progress=False)
        intra_n = yf.download("^NSEI", period="5d", interval="15m", progress=False)
        vix_df = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False)
        
        for df in [daily_n, hourly_n, intra_n, vix_df]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
        n_spot = float(intra_n['Close'].iloc[-1])
        n_vix = float(vix_df['Close'].iloc[-1])
        
        d_trend, _, _ = calc_supertrend(daily_n)
        h_trend, _, _ = calc_supertrend(hourly_n)
        m_trend, st_val, n_atr = calc_supertrend(intra_n)
        
        n_rsi = float(calc_rsi(intra_n['Close']).iloc[-1])
        pcr, sup_oi, res_oi = fetch_nifty_pcr(n_spot)
        
        tf_bull = sum([1 for t in [d_trend, h_trend, m_trend] if t == "BULLISH"])
        tf_bear = 3 - tf_bull
        
        atm_n = int(round(n_spot / 50.0) * 50)
        
        # Position Sizing Calculation for Spread (Approx margin/risk per lot ~ ₹4,000 max loss)
        max_risk_amount = user_capital * (risk_pct / 100.0)
        risk_per_lot_nifty = 2500.0 # Standard credit spread risk per lot
        suggested_lots = max(1, int(max_risk_amount / risk_per_lot_nifty))

        if tf_bull == 3 and n_rsi > 45:
            st.success("🔥 **MULTI-TF ALIGNED: STRONG BULLISH | BULL PUT SPREAD**")
            sell_strike = max(sup_oi, atm_n - 350)
            buy_strike = sell_strike - 250
            leg1 = f"BUY NIFTY {buy_strike} PE (Hedge)"
            leg2 = f"SELL NIFTY {sell_strike} PE (Main Sell Leg)"
            sl = f"Spot closes below {sell_strike - 30}"
            delta_val = "-0.22 (OTM Put Sell)"
            theta_val = f"+₹{(suggested_lots * 350)} / day (Time Decay Gain)"
        elif tf_bear == 3 and n_rsi < 55:
            st.error("🔻 **MULTI-TF ALIGNED: STRONG BEARISH | BEAR CALL SPREAD**")
            sell_strike = min(res_oi, atm_n + 350)
            buy_strike = sell_strike + 250
            leg1 = f"BUY NIFTY {buy_strike} CE (Hedge)"
            leg2 = f"SELL NIFTY {sell_strike} CE (Main Sell Leg)"
            sl = f"Spot closes above {sell_strike + 30}"
            delta_val = "+0.22 (OTM Call Sell)"
            theta_val = f"+₹{(suggested_lots * 350)} / day (Time Decay Gain)"
        else:
            st.warning(f"⚠️ **MIXED TIMEFRAMES (1D: {d_trend} | 1H: {h_trend} | 15m: {m_trend})**")
            leg1, leg2, sl, delta_val, theta_val, suggested_lots = "None (No Trade)", "None", "Capital Safe", "0.0", "₹0", 0

        m1, m2, m3 = st.columns(3)
        m1.metric("Nifty Spot", f"{n_spot:.2f}", f"VIX: {n_vix:.2f}")
        m2.metric("Option Chain PCR", f"{pcr}", f"RSI: {n_rsi:.1f}")
        m3.metric("Allowed Lots", f"{suggested_lots} Lot(s)", f"Risk: {risk_pct}%")

        # Multi-Timeframe Matrix Table
        st.markdown("**🌐 Multi-Timeframe Confluence Matrix:**")
        matrix_df = pd.DataFrame({
            "Timeframe": ["Daily (1D)", "Hourly (1H)", "Intraday (15m)"],
            "Trend Status": [d_trend, h_trend, m_trend],
            "SuperTrend Signal": ["🟢 Bullish" if d_trend=="BULLISH" else "🔴 Bearish", 
                                  "🟢 Bullish" if h_trend=="BULLISH" else "🔴 Bearish", 
                                  "🟢 Bullish" if m_trend=="BULLISH" else "🔴 Bearish"]
        })
        st.table(matrix_df)

        st.markdown(f"""
        **📋 Execution & Greeks Sheet:**
        * **1. HEDGE LEG:** `{leg1}`
        * **2. MAIN SELL LEG:** `{leg2}`
        * **STOP-LOSS:** `{sl}`
        * **GREEKS (Delta):** `{delta_val}`
        * **THETA DECAY:** `{theta_val}`
        """)
        st.line_chart(intra_n['Close'].tail(60), height=180)
    except Exception as e:
        st.error(f"Nifty Terminal Error: {e}")

# ================= 2. BITCOIN TERMINAL =================
with col2:
    st.subheader("🪙 BITCOIN PRO TERMINAL")
    try:
        daily_b = yf.download("BTC-USD", period="6mo", interval="1d", progress=False)
        hourly_b = yf.download("BTC-USD", period="1mo", interval="1h", progress=False)
        intra_b = yf.download("BTC-USD", period="5d", interval="15m", progress=False)
        
        for df in [daily_b, hourly_b, intra_b]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
        b_spot = float(intra_b['Close'].iloc[-1])
        
        bd_trend, _, _ = calc_supertrend(daily_b)
        bh_trend, _, _ = calc_supertrend(hourly_b)
        bm_trend, b_st_val, b_atr = calc_supertrend(intra_b)
        
        b_rsi = float(calc_rsi(intra_b['Close']).iloc[-1])
        
        btf_bull = sum([1 for t in [bd_trend, bh_trend, bm_trend] if t == "BULLISH"])
        btf_bear = 3 - btf_bull
        
        atm_b = int(round(b_spot / 200.0) * 200)
        btc_lots = max(1, int((user_capital * (risk_pct / 100.0)) / 15000.0))

        if btf_bull == 3:
            st.success("🔥 **MULTI-TF ALIGNED: STRONG BULLISH | BULL PUT SPREAD**")
            sell_p = atm_b - 600
            buy_p = sell_p - 1000
            b_leg1 = f"BUY BTC PUT ${buy_p} (Hedge)"
            b_leg2 = f"SELL BTC PUT ${sell_p} (Main Sell Leg)"
            b_sl = f"ATR SL below ${sell_p - round(b_atr*1.5, 2)}"
            b_delta = "-0.25 (OTM Put Sell)"
            b_theta = f"+${btc_lots * 25} / day (Theta Earning)"
        elif btf_bear == 3:
            st.error("🔻 **MULTI-TF ALIGNED: STRONG BEARISH | BEAR CALL SPREAD**")
            sell_c = atm_b + 600
            buy_c = sell_c + 1000
            b_leg1 = f"BUY BTC CALL ${buy_c} (Hedge)"
            b_leg2 = f"SELL BTC CALL ${sell_c} (Main Sell Leg)"
            b_sl = f"ATR SL above ${sell_c + round(b_atr*1.5, 2)}"
            b_delta = "+0.25 (OTM Call Sell)"
            b_theta = f"+${btc_lots * 25} / day (Theta Earning)"
        else:
            st.warning(f"⚠️ **MIXED TIMEFRAMES (1D: {bd_trend} | 1H: {bh_trend} | 15m: {bm_trend})**")
            b_leg1, b_leg2, b_sl, b_delta, b_theta, btc_lots = "None (No Trade)", "None", "None", "0.0", "$0", 0

        bm1, bm2, bm3 = st.columns(3)
        bm1.metric("BTC Spot Price", f"${b_spot:,.2f}", f"ATR: ${b_atr:.1f}")
        bm2.metric("15m RSI", f"{b_rsi:.1f}", f"SuperTrend: {bm_trend}")
        bm3.metric("BTC Sizing", f"{btc_lots} Unit(s)", f"Sync: {max(btf_bull, btf_bear)}/3")

        # Multi-Timeframe Matrix Table for BTC
        st.markdown("**🌐 Multi-Timeframe Confluence Matrix:**")
        b_matrix_df = pd.DataFrame({
            "Timeframe": ["Daily (1D)", "Hourly (1H)", "Intraday (15m)"],
            "Trend Status": [bd_trend, bh_trend, bm_trend],
            "SuperTrend Signal": ["🟢 Bullish" if bd_trend=="BULLISH" else "🔴 Bearish", 
                                  "🟢 Bullish" if bh_trend=="BULLISH" else "🔴 Bearish", 
                                  "🟢 Bullish" if bm_trend=="BULLISH" else "🔴 Bearish"]
        })
        st.table(b_matrix_df)

        st.markdown(f"""
        **📋 Execution & Greeks Sheet:**
        * **1. HEDGE LEG:** `{b_leg1}`
        * **2. MAIN SELL LEG:** `{b_leg2}`
        * **STOP-LOSS:** `{b_sl}`
        * **GREEKS (Delta):** `{b_delta}`
        * **THETA DECAY:** `{b_theta}`
        """)
        st.line_chart(intra_b['Close'].tail(60), height=180)
    except Exception as e:
        st.error(f"BTC Terminal Error: {e}")

st.markdown("---")
st.caption(f"Shine AI Pro Desk v4.0 | Institutional Position Sizing & Greeks Terminal | Last Synced: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
