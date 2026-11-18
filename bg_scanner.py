import os
import sys
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime, timedelta, timezone

TG_TOKEN = "9032117445:AAEerBt_kqWXr-A8j1xmaQXmfnyp-O8i1-A"
TG_CHAT_ID = "8085621728"

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
    return ("BULLISH" if st_dir[-1] else "BEARISH"), float(atr.iloc[-1])

def run_scan():
    # 1. NIFTY SCAN
    daily_n = yf.download("^NSEI", period="6mo", interval="1d", progress=False)
    hourly_n = yf.download("^NSEI", period="1mo", interval="1h", progress=False)
    intra_n = yf.download("^NSEI", period="5d", interval="15m", progress=False)
    for df in [daily_n, hourly_n, intra_n]:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
    n_spot = float(intra_n['Close'].iloc[-1])
    d_trend, _ = calc_supertrend(daily_n)
    h_trend, _ = calc_supertrend(hourly_n)
    m_trend, n_atr = calc_supertrend(intra_n)
    n_rsi = float(calc_rsi(intra_n['Close']).iloc[-1])
    
    recent = intra_n.tail(24)
    bull_p = float((recent[recent['Close'] >= recent['Open']]['High'] - recent[recent['Close'] >= recent['Open']]['Low']).sum()) + 1e-5
    bear_p = float((recent[recent['Close'] < recent['Open']]['High'] - recent[recent['Close'] < recent['Open']]['Low']).sum()) + 1e-5
    pcr = round(float(np.clip((bull_p / bear_p) * 0.55 + (n_rsi / 50.0) * 0.45, 0.55, 1.65)), 2)
    
    atm_n = int(round(n_spot / 50.0) * 50)
    step_dist = max(200, int(round((n_atr * 3.5) / 50.0) * 50))
    sup_oi, res_oi = atm_n - step_dist, atm_n + step_dist

    # Strict Multi-TF Confluence (1H aur 15m dono match hone par hi trade)
    if h_trend == "BULLISH" and m_trend == "BULLISH":
        n_status = "BULLISH (1H+15m Confirmed)"
        sell_s = max(sup_oi, atm_n - 300)
        leg1, leg2, sl = f"BUY NIFTY {sell_s - 250} PE", f"SELL NIFTY {sell_s} PE", f"Below {sell_s - 30}"
    elif h_trend == "BEARISH" and m_trend == "BEARISH":
        n_status = "BEARISH (1H+15m Confirmed)"
        sell_s = min(res_oi, atm_n + 300)
        leg1, leg2, sl = f"BUY NIFTY {sell_s + 250} CE", f"SELL NIFTY {sell_s} CE", f"Above {sell_s + 30}"
    else:
        n_status, leg1, leg2, sl = "MIXED / NO TRADE", "None", "None", "Capital Safe"

    # 2. BTC SCAN
    daily_b = yf.download("BTC-USD", period="6mo", interval="1d", progress=False)
    hourly_b = yf.download("BTC-USD", period="1mo", interval="1h", progress=False)
    intra_b = yf.download("BTC-USD", period="5d", interval="15m", progress=False)
    for df in [daily_b, hourly_b, intra_b]:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
    b_spot = float(intra_b['Close'].iloc[-1])
    bd_trend, _ = calc_supertrend(daily_b)
    bh_trend, _ = calc_supertrend(hourly_b)
    bm_trend, b_atr = calc_supertrend(intra_b)
    b_rsi = float(calc_rsi(intra_b['Close']).iloc[-1])
    atm_b = int(round(b_spot / 200.0) * 200)

    # Strict Multi-TF Confluence for BTC
    if bh_trend == "BULLISH" and bm_trend == "BULLISH":
        b_status = "BULLISH (1H+15m Confirmed)"
        b_leg1, b_leg2 = f"BUY BTC PUT ${atm_b - 1600}", f"SELL BTC PUT ${atm_b - 600}"
    elif bh_trend == "BEARISH" and bm_trend == "BEARISH":
        b_status = "BEARISH (1H+15m Confirmed)"
        b_leg1, b_leg2 = f"BUY BTC CALL ${atm_b + 1600}", f"SELL BTC CALL ${atm_b + 600}"
    else:
        b_status, b_leg1, b_leg2 = "MIXED / NO TRADE", "None", "None"

    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %H:%M IST')
    
    msg = (
        f"⚡ SHINE AI — 24/7 BACKGROUND AUTO-ALERT\n\n"
        f"📊 NIFTY 50: {n_status}\n"
        f"• Matrix: 1D:{d_trend} | 1H:{h_trend} | 15m:{m_trend}\n"
        f"• Spot: {n_spot:.2f} | PCR: {pcr} | RSI: {n_rsi:.1f}\n"
        f"• OI Walls: Support {sup_oi} PE | Res {res_oi} CE\n"
        f"• Hedge Leg: {leg1}\n"
        f"• Main Leg: {leg2}\n"
        f"• Stop-Loss: {sl}\n\n"
        f"🪙 BITCOIN: {b_status}\n"
        f"• Matrix: 1D:{bd_trend} | 1H:{bh_trend} | 15m:{bm_trend}\n"
        f"• Spot: ${b_spot:,.2f} | RSI: {b_rsi:.1f}\n"
        f"• Hedge Leg: {b_leg1}\n"
        f"• Main Leg: {b_leg2}\n\n"
        f"⏱️ Time: {ist_now}"
    )
    
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT_ID, "text": msg},
        timeout=10
    )
    print("Telegram Response:", r.status_code, r.text)

if __name__ == "__main__":
    run_scan()
