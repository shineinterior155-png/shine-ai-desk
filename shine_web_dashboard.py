import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

st.set_page_config(page_title="Shine AI Trading Desk v2.0", page_icon="🚀", layout="wide")

st.title("🚀 SHINE AI Institutional Trading Desk v2.0")
st.markdown("Live Multi-Factor Confluence, Option Selling Strikes & Risk Terminal (Nifty 50 & Bitcoin)")

# Sidebar Controls
st.sidebar.header("⚙️ Control Panel")
if st.sidebar.button("🔄 Refresh Live Market Data", use_container_width=True):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.info("💡 **Tip:** Click 'Refresh Live Market Data' anytime to pull the latest 15-min candles, PCR, and strike legs.")

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
    
    # .copy() fixes the Pandas 3.x read-only array error
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
        
    return ("UP" if st_dir[-1] else "DOWN"), float(st_val[-1]), float(atr.iloc[-1])

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
    st.subheader("📊 NIFTY 50 PRO ACCURACY SIGNAL")
    try:
        daily_n = yf.download("^NSEI", period="6mo", interval="1d", progress=False)
        intra_n = yf.download("^NSEI", period="5d", interval="15m", progress=False)
        bn_intra = yf.download("^NSEBANK", period="5d", interval="15m", progress=False)
        vix_df = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False)
        
        for df in [daily_n, intra_n, bn_intra, vix_df]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
        n_spot = float(intra_n['Close'].iloc[-1])
        n_vix = float(vix_df['Close'].iloc[-1])
        n_d_ema20 = float(daily_n['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
        n_d_ema50 = float(daily_n['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
        
        n_15_ema20 = float(intra_n['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
        n_rsi = float(calc_rsi(intra_n['Close']).iloc[-1])
        st_dir, st_val, _ = calc_supertrend(intra_n)
        
        bn_spot = float(bn_intra['Close'].iloc[-1])
        bn_ema20 = float(bn_intra['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
        bn_status = "BULLISH" if bn_spot > bn_ema20 else "BEARISH"
        
        pcr, sup_oi, res_oi = fetch_nifty_pcr(n_spot)
        
        bull_pts = 0
        if n_d_ema20 > n_d_ema50: bull_pts += 1
        if n_spot > n_15_ema20: bull_pts += 1
        if n_rsi > 50: bull_pts += 1
        if st_dir == "UP": bull_pts += 1
        if bn_status == "BULLISH": bull_pts += 1
        if pcr >= 0.90: bull_pts += 1
        bear_pts = 6 - bull_pts
        
        atm_n = int(round(n_spot / 50.0) * 50)
        if bull_pts >= 5:
            bias = f"STRONG BULLISH (Confluence: {bull_pts}/6)"
            strat = "BULL PUT SPREAD (Put Credit Spread)"
            sell_strike = max(sup_oi, atm_n - 350)
            buy_strike = sell_strike - 250
            leg1 = f"BUY NIFTY {buy_strike} PE (Hedge - Pehle Buy)"
            leg2 = f"SELL NIFTY {sell_strike} PE (Main Sell Leg)"
            sl = f"Spot closes below {sell_strike - 30} (15-min candle)"
            st.success(f"🔥 **{bias}** | **{strat}**")
        elif bear_pts >= 5:
            bias = f"STRONG BEARISH (Confluence: {bear_pts}/6)"
            strat = "BEAR CALL SPREAD (Call Credit Spread)"
            sell_strike = min(res_oi, atm_n + 350)
            buy_strike = sell_strike + 250
            leg1 = f"BUY NIFTY {buy_strike} CE (Hedge - Pehle Buy)"
            leg2 = f"SELL NIFTY {sell_strike} CE (Main Sell Leg)"
            sl = f"Spot closes above {sell_strike + 30} (15-min candle)"
            st.error(f"🔻 **{bias}** | **{strat}**")
        else:
            bias = f"SIDEWAYS / CHOPPY (Bull:{bull_pts} vs Bear:{bear_pts})"
            strat = "NO TRADE (Wait for Multi-Indicator Alignment)"
            leg1 = "None"
            leg2 = "None"
            sl = "Capital Safe Rakhein"
            st.warning(f"⚠️ **{bias}** | **{strat}**")

        m1, m2, m3 = st.columns(3)
        m1.metric("Nifty Spot", f"{n_spot:.2f}", f"VIX: {n_vix:.2f}")
        m2.metric("Option Chain PCR", f"{pcr}", f"RSI: {n_rsi:.1f}")
        m3.metric("OI Walls", f"{sup_oi} PE", f"Res: {res_oi} CE")

        st.markdown(f"""
        **📋 Live Institutional Signal Sheet:**
        * **Daily Trend:** `EMA20 ({n_d_ema20:.2f}) vs EMA50 ({n_d_ema50:.2f})`
        * **15-Min Status:** `EMA20 ({n_15_ema20:.2f}) | RSI (14): {n_rsi:.1f}`
        * **Pro Filters:** `SuperTrend: {st_dir} ({st_val:.2f}) | BankNifty: {bn_status}`
        * **1. HEDGE LEG:** `{leg1}`
        * **2. MAIN LEG:** `{leg2}`
        * **STOP-LOSS:** `{sl}`
        """)
        st.line_chart(intra_n['Close'].tail(60), height=220)
    except Exception as e:
        st.error(f"Nifty Data Error: {e}")

# ================= 2. BITCOIN TERMINAL =================
with col2:
    st.subheader("🪙 SHINE BTC PRO OPTION SELLING AGENT")
    try:
        daily_b = yf.download("BTC-USD", period="6mo", interval="1d", progress=False)
        intra_b = yf.download("BTC-USD", period="5d", interval="15m", progress=False)
        
        for df in [daily_b, intra_b]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
        b_spot = float(intra_b['Close'].iloc[-1])
        b_d_ema20 = float(daily_b['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
        b_d_ema50 = float(daily_b['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
        
        b_15_ema20 = float(intra_b['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
        b_rsi = float(calc_rsi(intra_b['Close']).iloc[-1])
        
        ema12 = intra_b['Close'].ewm(span=12, adjust=False).mean()
        ema26 = intra_b['Close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line = macd_line.ewm(span=9, adjust=False).mean()
        b_macd = float(macd_line.iloc[-1])
        b_sig = float(sig_line.iloc[-1])
        
        b_st_dir, b_st_val, b_atr = calc_supertrend(intra_b)
        
        b_bull = 0
        if b_d_ema20 > b_d_ema50: b_bull += 1
        if b_spot > b_15_ema20: b_bull += 1
        if b_rsi > 50: b_bull += 1
        if b_macd > b_sig: b_bull += 1
        if b_st_dir == "UP": b_bull += 1
        b_bear = 5 - b_bull
        
        atm_b = int(round(b_spot / 200.0) * 200)
        if b_bull >= 4:
            b_bias = f"STRONG BULLISH (Confluence Score: {b_bull}/5)"
            b_strat = "BULL PUT SPREAD (Credit Spread)"
            sell_p = atm_b - 600
            buy_p = sell_p - 1000
            b_leg1 = f"BUY BTC PUT ${buy_p} (Hedge)"
            b_leg2 = f"SELL BTC PUT ${sell_p} (Main Sell Leg)"
            b_sl = f"Dynamic ATR SL below ${sell_p - round(b_atr*1.5, 2)}"
            b_tgt = f"Risk-Reward 1:1.5 / 1:2 (Target Zone ~${b_spot + round(b_atr*2, 2):.2f})"
            st.success(f"🔥 **{b_bias}** | **{b_strat}**")
        elif b_bear >= 4:
            b_bias = f"STRONG BEARISH (Confluence Score: {b_bear}/5)"
            b_strat = "BEAR CALL SPREAD (Credit Spread)"
            sell_c = atm_b + 600
            buy_c = sell_c + 1000
            b_leg1 = f"BUY BTC CALL ${buy_c} (Hedge)"
            b_leg2 = f"SELL BTC CALL ${sell_c} (Main Sell Leg)"
            b_sl = f"Dynamic ATR SL above ${sell_c + round(b_atr*1.5, 2)}"
            b_tgt = f"Risk-Reward 1:1.5 / 1:2 (Target Zone ~${b_spot - round(b_atr*2, 2):.2f})"
            st.error(f"🔻 **{b_bias}** | **{b_strat}**")
        else:
            b_bias = f"SIDEWAYS / CHOPPY (Bull: {b_bull}, Bear: {b_bear})"
            b_strat = "NO TRADE (Strong Confirmation Only)"
            b_leg1 = "None"
            b_leg2 = "None"
            b_sl = "None"
            b_tgt = "None"
            st.warning(f"⚠️ **{b_bias}** | **{b_strat}**")

        bm1, bm2, bm3 = st.columns(3)
        bm1.metric("BTC Spot Price", f"${b_spot:,.2f}", f"ATR: ${b_atr:.1f}")
        bm2.metric("15m RSI & Trend", f"{b_rsi:.1f}", f"SuperTrend: {b_st_dir}")
        bm3.metric("Momentum (MACD)", f"{b_macd:.1f}", f"Sig: {b_sig:.1f}")

        st.markdown(f"""
        **📋 Live Crypto Option Signal Sheet:**
        * **Daily Trend:** `EMA20 ({b_d_ema20:.1f}) vs EMA50 ({b_d_ema50:.1f})`
        * **15-Min Status:** `EMA20 ({b_15_ema20:.1f}) | SuperTrend {b_st_dir} ({b_st_val:.1f})`
        * **1. HEDGE LEG:** `{b_leg1}`
        * **2. MAIN LEG:** `{b_leg2}`
        * **STOP-LOSS:** `{b_sl}`
        * **TARGET:** `{b_tgt}`
        """)
        st.line_chart(intra_b['Close'].tail(60), height=220)
    except Exception as e:
        st.error(f"BTC Data Error: {e}")

st.markdown("---")
st.caption(f"Shine AI Pro Desk v2.0 | Signal Only – No Auto Trading | Last Synced: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")