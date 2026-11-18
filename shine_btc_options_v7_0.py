# ============================================================
# SHINE BTC OPTIONS V7.0 (WITH DELTA MONTHLY 5000-OTM AUTO-ORDER)
# Deribit public data + Delta Exchange India Live 1-Lot Execution
# ============================================================

import csv
import json
import math
import os
import time
from datetime import datetime, timezone
import requests

# Delta Auto-Order Module Import (from delta_test_order.py in the same folder)
try:
    from delta_test_order import execute_monthly_option_sell
    DELTA_MODULE_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING: delta_test_order.py not found in folder: {e}]")
    DELTA_MODULE_AVAILABLE = False

# ---------------- CONFIGURATION ----------------
AUTO_TRADE_DELTA = True   # True = Live 1-Lot Monthly 5000-OTM order on Delta Exchange
DELTA_LOTS = 1            # 1 Lot (0.001 BTC)

CAPITAL_INR = 1000000.0  # Capital INR 10 lakh
RISK_PERCENT = 2.0  # Max risk remains 2% of capital
RISK_BUDGET_INR = CAPITAL_INR * RISK_PERCENT / 100.0
SCAN_SECONDS = 900
MIN_DTE_DAYS = 5
MAX_DTE_DAYS = 9
SHORT_OTM_PCT = 0.03
HEDGE_OTM_PCT = 0.05
MIN_BID_BTC = 0.0001
MIN_CREDIT_BTC = 0.00005
MAX_QUOTE_SPREAD_PCT = 50.0
MIN_IV_PERCENT = 15.0
MAX_IV_PERCENT = 180.0
CSV_FILE = "shine_btc_options_v7_signals.csv"
ALERT_STATE_FILE = "shine_btc_options_v7_alert_state.json"

# ---------------- ALERT CONFIGURATION ----------------
MONITOR_POSITION = False  # Change to True AFTER you manually open a spread.
OPEN_POSITION_TYPE = "SPREAD"  # Set to "IRON_CONDOR" for a manually opened 4-leg condor
OPEN_POSITION_SIDE = "CALL"  # "CALL" for Bear Call Spread; "PUT" for Bull Put Spread
OPEN_PUT_SHORT_INSTRUMENT = ""
OPEN_PUT_HEDGE_INSTRUMENT = ""
OPEN_CALL_SHORT_INSTRUMENT = ""
OPEN_CALL_HEDGE_INSTRUMENT = ""
OPEN_SHORT_INSTRUMENT = ""  # Example: BTC-20NOV26-72000-C
OPEN_HEDGE_INSTRUMENT = ""  # Example: BTC-20NOV26-73000-C
ENTRY_CREDIT_BTC = 0.0  # Actual net credit received per 1 BTC option amount
OPEN_POSITION_SIZE_BTC = 0.10  # Actual Deribit amount, e.g. 0.10
TAKE_PROFIT_CREDIT_FRACTION = 0.75  # Alert after 75% of received credit is captured
STOP_CLOSE_COST_MULTIPLE = 2.0  # Alert if estimated close cost reaches 2x entry credit
EXIT_ALERT_DTE_DAYS = 1.0  # Alert when expiry is within this many days
MAX_SIMULTANEOUS_POSITIONS = 2
OPEN_POSITIONS_COUNT = 0  # IMPORTANT: manually update this to your actual open positions (0, 1 or 2)
# Market-change alert thresholds (compared with the previous successful scan)
BTC_CHANGE_ALERT_PCT = 1.0
OPTION_PREMIUM_CHANGE_ALERT_PCT = 10.0
OPTION_IV_CHANGE_POINTS = 5.0
OPTION_OI_CHANGE_PCT = 10.0
OPTION_VOLUME_CHANGE_PCT = 50.0
MARKET_ALERT_COOLDOWN_SCANS = 4  # 4 scans = 1 hour at 15-minute intervals
# Iron Condor strict filters (initial defaults; tune after paper-testing)
CONDOR_MIN_SHORT_IV = 25.0
CONDOR_MAX_SHORT_IV = 120.0
CONDOR_MAX_QUOTE_SPREAD_PCT = 30.0
CONDOR_MIN_OPEN_INTEREST = 10.0
CONDOR_MIN_7D_RANGE_PCT = 2.0
CONDOR_MAX_7D_RANGE_PCT = 20.0
DERIBIT_BASE = "https://www.deribit.com/api/v2/public"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD"
FX_URL = "https://open.er-api.com/v6/latest/USD"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "SHINE-BTC-Options-Signal-Agent/7.0"})


def get_json(url, params=None, timeout=20):
    r = SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data


def deribit(endpoint, params):
    data = get_json(DERIBIT_BASE + "/" + endpoint, params)
    if "result" not in data:
        raise RuntimeError("Deribit response missing result field")
    return data["result"]


def get_spot():
    return float(deribit("get_index_price", {"index_name": "btc_usd"})["index_price"])


def get_instruments():
    return deribit("get_instruments", {"currency": "BTC", "kind": "option"})


def get_summaries():
    return deribit("get_book_summary_by_currency", {"currency": "BTC", "kind": "option"})


def get_usd_inr():
    data = get_json(FX_URL)
    rate = float(data["rates"]["INR"])
    if rate <= 0:
        raise RuntimeError("Invalid USD/INR rate")
    return rate


def ema(values, period):
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def get_daily_trend():
    """Use only fully completed UTC daily candles for EMA trend filtering."""
    data = get_json(YAHOO_URL, {"range": "90d", "interval": "1d"})
    rows = data["chart"]["result"]
    if not rows:
        raise RuntimeError("Yahoo returned no BTC daily candles")
    row = rows[0]
    timestamps = row.get("timestamp") or []
    closes = row["indicators"]["quote"][0]["close"]
    completed = []
    now_ts = datetime.now(timezone.utc).timestamp()
    for ts, close in zip(timestamps, closes):
        if close is not None and float(ts) + 86400 <= now_ts:
            completed.append((float(ts), float(close)))
    values = [x[1] for x in completed]
    if len(values) < 55:
        raise RuntimeError("Not enough completed daily candles for EMA20/EMA50")
    e20 = ema(values[-80:], 20)
    e50 = ema(values[-80:], 50)
    last = values[-1]
    trend = "BULLISH" if last > e20 > e50 else "BEARISH" if last < e20 < e50 else "MIXED"
    return {"trend": trend, "close": last, "ema20": e20, "ema50": e50,
            "completed_candle_utc": datetime.fromtimestamp(completed[-1][0], timezone.utc)}


def get_completed_7d_range_pct():
    data = get_json(YAHOO_URL, {"range": "15d", "interval": "1d"})
    rows = data["chart"]["result"]
    if not rows:
        raise RuntimeError("Yahoo returned no BTC candles for range filter")
    row = rows[0]
    now_ts = datetime.now(timezone.utc).timestamp()
    highs = row["indicators"]["quote"][0].get("high", [])
    lows = row["indicators"]["quote"][0].get("low", [])
    timestamps = row.get("timestamp", [])
    ranges = [(float(h), float(l)) for ts,h,l in zip(timestamps,highs,lows)
              if h is not None and l is not None and float(ts)+86400 <= now_ts]
    if len(ranges) < 7:
        raise RuntimeError("Not enough completed candles for 7-day range filter")
    recent = ranges[-7:]
    hi=max(x[0] for x in recent); lo=min(x[1] for x in recent)
    mid=(hi+lo)/2
    return ((hi-lo)/mid*100) if mid else 0.0


def choose_expiry(instruments):
    now = datetime.now(timezone.utc)
    candidates = []
    for item in instruments:
        if item.get("option_type") not in ("call", "put") or not item.get("is_active", False):
            continue
        ts = item.get("expiration_timestamp")
        if not ts:
            continue
        exp = datetime.fromtimestamp(ts / 1000, timezone.utc)
        dte = (exp - now).total_seconds() / 86400.0
        if MIN_DTE_DAYS <= dte <= MAX_DTE_DAYS:
            candidates.append((ts, exp, dte))
    if not candidates:
        return None
    fridays = [x for x in candidates if x[1].weekday() == 4]
    pool = fridays if fridays else candidates
    return min(pool, key=lambda x: abs(x[2] - 7.0))


def build_maps(instruments, summaries, expiry_ts):
    imap = {}
    for item in instruments:
        if item.get("expiration_timestamp") == expiry_ts and item.get("is_active", False):
            name = item.get("instrument_name")
            if name:
                imap[name] = item
    smap = {x.get("instrument_name"): x for x in summaries if x.get("instrument_name") in imap}
    return imap, smap


def normalize_iv(value):
    iv = float(value or 0.0)
    return iv * 100.0 if 0 < iv <= 3 else iv


def option_rows(side, spot, imap, smap):
    result = []
    for name, inst in imap.items():
        if inst.get("option_type") != side.lower():
            continue
        strike = float(inst.get("strike") or 0)
        s = smap.get(name)
        if strike <= 0 or not s or s.get("bid_price") is None or s.get("ask_price") is None:
            continue
        bid, ask = float(s["bid_price"]), float(s["ask_price"])
        if bid < MIN_BID_BTC or ask <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2.0
        if mid <= 0:
            continue
        qspread = (ask - bid) / mid * 100.0
        if qspread > MAX_QUOTE_SPREAD_PCT:
            continue
        iv = normalize_iv(s.get("mark_iv"))
        if not MIN_IV_PERCENT <= iv <= MAX_IV_PERCENT:
            continue
        if side == "PUT" and strike >= spot:
            continue
        if side == "CALL" and strike <= spot:
            continue
        result.append({
            "name": name, "strike": strike, "bid": bid, "ask": ask,
            "iv": iv, "oi": float(s.get("open_interest") or 0),
            "volume": float(s.get("volume") or 0),
            "qspread": qspread,
            "min_amount": float(inst.get("min_trade_amount") or 0.1)
        })
    return result


def expiry_pnl_usd(settlement, side, short_k, hedge_k, credit_btc):
    s = max(float(settlement), 1.0)
    if side == "PUT":
        short_payoff = max(short_k - s, 0.0)
        hedge_payoff = max(hedge_k - s, 0.0)
    else:
        short_payoff = max(s - short_k, 0.0)
        hedge_payoff = max(s - hedge_k, 0.0)
    return credit_btc * s - short_payoff + hedge_payoff


def max_loss_usd(spread, spot):
    sk = spread["short"]["strike"]
    hk = spread["hedge"]["strike"]
    credit = spread["credit_btc"]
    prices = [1.0, max(1.0, spot * 0.001), sk, hk, spot, spot * 1.5, spot * 2, spot * 3]
    worst = min(expiry_pnl_usd(p, spread["side"], sk, hk, credit) for p in prices)
    return max(0.0, -worst)


def risk_size(spread, spot, usd_inr):
    loss_usd = max_loss_usd(spread, spot)
    loss_inr = loss_usd * usd_inr
    min_amt = max(0.1, spread["short"]["min_amount"], spread["hedge"]["min_amount"])
    if loss_inr <= 0:
        return {"qty": 0.0, "loss_usd": loss_usd, "loss_inr": loss_inr,
                "min_amt": min_amt, "min_risk": 0.0, "allowed": False}
    raw = RISK_BUDGET_INR / loss_inr
    qty = math.floor((raw + 1e-12) / min_amt) * min_amt
    qty = round(qty, 8)
    min_risk = loss_inr * min_amt
    return {"qty": qty if qty >= min_amt else 0.0, "loss_usd": loss_usd,
            "loss_inr": loss_inr, "min_amt": min_amt,
            "min_risk": min_risk, "allowed": qty >= min_amt}


def score_spread(short, credit_btc, width_usd, spot):
    credit_score = min(35.0, max(0.0, (credit_btc * spot / max(width_usd, 1.0)) * 100.0))
    iv_score = min(20.0, max(0.0, (short["iv"] - 15.0) / 4.0))
    liquidity = min(20.0, math.log1p(short["oi"]) * 3.0 + math.log1p(short["volume"]) * 2.0)
    quote = max(0.0, 25.0 - short["qspread"] * 0.5)
    return round(min(100.0, credit_score + iv_score + liquidity + quote), 1)


def find_spread(side, spot, imap, smap):
    opts = option_rows(side, spot, imap, smap)
    if len(opts) < 2:
        return None
    if side == "PUT":
        starget, htarget = spot * (1 - SHORT_OTM_PCT), spot * (1 - HEDGE_OTM_PCT)
        shorts = [x for x in opts if x["strike"] < starget]
        hedges = [x for x in opts if x["strike"] < htarget]
    else:
        starget, htarget = spot * (1 + SHORT_OTM_PCT), spot * (1 + HEDGE_OTM_PCT)
        shorts = [x for x in opts if x["strike"] > starget]
        hedges = [x for x in opts if x["strike"] > htarget]
    shorts.sort(key=lambda x: abs(x["strike"] - starget))
    hedges.sort(key=lambda x: abs(x["strike"] - htarget))
    best = None
    for short in shorts[:15]:
        for hedge in hedges[:15]:
            if side == "PUT" and hedge["strike"] >= short["strike"]:
                continue
            if side == "CALL" and hedge["strike"] <= short["strike"]:
                continue
            credit = short["bid"] - hedge["ask"]
            width = abs(short["strike"] - hedge["strike"])
            if credit < MIN_CREDIT_BTC or width <= 0:
                continue
            candidate = {"side": side, "short": short, "hedge": hedge,
                         "credit_btc": credit, "credit_usd_now": credit * spot,
                         "width_usd": width}
            candidate["score"] = score_spread(short, credit, width, spot)
            if best is None or candidate["score"] > best["score"]:
                best = candidate
    return best


def max_loss_condor_usd(condor, spot):
    return max_loss_usd(condor["put"], spot) + max_loss_usd(condor["call"], spot)


def risk_size_condor(condor, spot, usd_inr):
    loss_usd = max_loss_condor_usd(condor, spot)
    loss_inr = loss_usd * usd_inr
    min_amt = max(0.1, condor["put"]["short"]["min_amount"], condor["put"]["hedge"]["min_amount"],
                  condor["call"]["short"]["min_amount"], condor["call"]["hedge"]["min_amount"])
    raw = RISK_BUDGET_INR / loss_inr if loss_inr > 0 else 0
    qty = round(math.floor((raw + 1e-12) / min_amt) * min_amt, 8)
    return {"qty": qty if qty >= min_amt else 0.0, "loss_usd": loss_usd, "loss_inr": loss_inr,
            "min_amt": min_amt, "min_risk": loss_inr*min_amt, "allowed": qty >= min_amt}


def find_iron_condor(spot, imap, smap, range_pct):
    put = find_spread("PUT", spot, imap, smap)
    call = find_spread("CALL", spot, imap, smap)
    if not put or not call:
        return None, "Both protected wings are not available"
    short_put, short_call = put["short"], call["short"]
    if not (CONDOR_MIN_SHORT_IV <= short_put["iv"] <= CONDOR_MAX_SHORT_IV and
            CONDOR_MIN_SHORT_IV <= short_call["iv"] <= CONDOR_MAX_SHORT_IV):
        return None, "Short-leg IV filter failed"
    if max(short_put["qspread"], short_call["qspread"]) > CONDOR_MAX_QUOTE_SPREAD_PCT:
        return None, "Quote liquidity filter failed"
    if min(short_put["oi"], short_call["oi"]) < CONDOR_MIN_OPEN_INTEREST:
        return None, "Open-interest filter failed"
    if not (CONDOR_MIN_7D_RANGE_PCT <= range_pct <= CONDOR_MAX_7D_RANGE_PCT):
        return None, "7-day realized-range filter failed"
    return {"put": put, "call": call,
            "credit_btc": put["credit_btc"] + call["credit_btc"],
            "score": round((put["score"] + call["score"])/2, 1),
            "range_pct": range_pct}, "PASS"


def send_market_change_alerts(spot, smap):
    state = load_alert_state()
    prev = state.get("market_snapshot", {})
    scan_no = int(state.get("scan_no", 0)) + 1
    now_snapshot = {"spot": spot, "options": {}}
    ranked = sorted(smap.items(), key=lambda kv: float(kv[1].get("open_interest") or 0), reverse=True)[:20]
    changes = []
    for name, q in ranked:
        bid=q.get("bid_price"); ask=q.get("ask_price")
        if bid is None or ask is None:
            continue
        premium=(float(bid)+float(ask))/2
        iv=normalize_iv(q.get("mark_iv"))
        oi=float(q.get("open_interest") or 0)
        vol=float(q.get("volume") or 0)
        now_snapshot["options"][name]={"premium":premium,"iv":iv,"oi":oi,"volume":vol}
        old=prev.get("options",{}).get(name)
        if old:
            def pct_change(a,b): return abs(a-b)/abs(b)*100 if b else 0
            if pct_change(premium, old.get("premium",0)) >= OPTION_PREMIUM_CHANGE_ALERT_PCT:
                changes.append(f"{name} premium {old['premium']:.6f} -> {premium:.6f} BTC")
            if abs(iv-old.get("iv",iv)) >= OPTION_IV_CHANGE_POINTS:
                changes.append(f"{name} IV {old['iv']:.1f}% -> {iv:.1f}%")
            if pct_change(oi, old.get("oi",0)) >= OPTION_OI_CHANGE_PCT:
                changes.append(f"{name} OI {old['oi']:.2f} -> {oi:.2f}")
            if pct_change(vol, old.get("volume",0)) >= OPTION_VOLUME_CHANGE_PCT and vol > old.get("volume",0):
                changes.append(f"{name} volume {old['volume']:.2f} -> {vol:.2f}")
    old_spot=prev.get("spot")
    if old_spot and abs(spot-old_spot)/old_spot*100 >= BTC_CHANGE_ALERT_PCT:
        changes.insert(0, f"BTC index ${old_spot:,.0f} -> ${spot:,.0f} ({(spot-old_spot)/old_spot*100:+.2f}%)")
    last_alert=int(state.get("last_market_alert_scan", -999))
    if changes and scan_no-last_alert >= MARKET_ALERT_COOLDOWN_SCANS:
        alert("MARKET CHANGE UPDATE\n" + "\n".join(changes[:12]) + "\nCompared with previous successful scan. Informational only; not an entry signal.")
        state["last_market_alert_scan"]=scan_no
    elif changes:
        print(f"[Market changes detected; Telegram cooldown active: {len(changes)} changes]")
    state["market_snapshot"]=now_snapshot
    state["scan_no"]=scan_no
    save_alert_state(state)


def send_condor_entry_alert(condor, risk, expiry):
    state=load_alert_state()
    p=condor["put"]; c=condor["call"]
    key="|".join([expiry.isoformat(),p["short"]["name"],p["hedge"]["name"],c["short"]["name"],c["hedge"]["name"]])
    alerted=set(state.get("entry_alerts",[]))
    if key in alerted:
        print("[Telegram entry alert skipped: exact Iron Condor already alerted]")
        return
    msg=("ENTRY SETUP: IRON CONDOR\n"
         f"PUT: SELL {p['short']['name']} | BUY {p['hedge']['name']}\n"
         f"CALL: SELL {c['short']['name']} | BUY {c['hedge']['name']}\n"
         f"Combined credit: {condor['credit_btc']:.6f} BTC | Size: {risk['qty']:.2f} BTC\n"
         f"Estimated combined max loss: Rs. {risk['loss_inr']*risk['qty']:,.0f}\n"
         f"7-day range: {condor['range_pct']:.2f}% | Score: {condor['score']}/100 (heuristic)\n"
         "MANUAL REVIEW / EXECUTION ONLY. No order was placed.")
    alert(msg)
    alerted.add(key); state["entry_alerts"]=sorted(alerted)[-500:]; state["last_entry_key"]=key
    save_alert_state(state)


def save_signal(record):
    exists = os.path.exists(CSV_FILE) and os.path.getsize(CSV_FILE) > 0
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(record.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(record)


def load_alert_state():
    try:
        with open(ALERT_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            if not isinstance(state, dict):
                state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    state.setdefault("last_entry_key", "")
    state.setdefault("last_delta_auto_order_key", "")
    state.setdefault("entry_alerts", [])
    state.setdefault("exit_alerts", [])
    if not isinstance(state["entry_alerts"], list):
        state["entry_alerts"] = []
    if not isinstance(state["exit_alerts"], list):
        state["exit_alerts"] = []
    return state


def save_alert_state(state):
    temp = ALERT_STATE_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(temp, ALERT_STATE_FILE)


def send_telegram(message):
    """Send an alert using credentials stored in environment variables or delta_test_order.py."""
    token = os.getenv("SHINE_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("SHINE_TELEGRAM_CHAT_ID", "").strip()
    if (not token or not chat_id) and DELTA_MODULE_AVAILABLE:
        try:
            import delta_test_order as dto
            token = token or getattr(dto, "TELEGRAM_BOT_TOKEN", "").strip()
            chat_id = chat_id or str(getattr(dto, "TELEGRAM_CHAT_ID", "")).strip()
        except Exception:
            pass
    if not token or not chat_id or chat_id == "YOUR_TELEGRAM_CHAT_ID":
        print("[Telegram not configured: set SHINE_TELEGRAM_BOT_TOKEN/CHAT_ID or configure delta_test_order.py]")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": "SHINE BTC OPTIONS ALERT\n" + message},
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError("Telegram API did not confirm message delivery")
        print("[Telegram alert sent]")
        return True
    except Exception as exc:
        print(f"[Telegram send failed: {type(exc).__name__}]")
        return False


def alert(message):
    print("\n" + "!" * 66)
    print("SHINE ALERT:", message)
    print("!" * 66 + "\n")
    send_telegram(message)
    try:
        import winsound
        winsound.Beep(1200, 700)
        winsound.Beep(1500, 700)
    except Exception:
        print("[Sound alert unavailable; read the console alert]")


def trigger_delta_monthly_auto_order(signal_direction, expiry):
    """
    Executes 1-Lot Monthly (>7 DTE) 5000-OTM Direct Option Sell + Stop-Limit Auto-SL
    on Delta Exchange India, avoiding duplicate orders across 15-min scans.
    """
    if not AUTO_TRADE_DELTA or not DELTA_MODULE_AVAILABLE:
        return
    if signal_direction not in ("BEARISH", "BULLISH"):
        return

    state = load_alert_state()
    # Unique lock key per signal direction + signal expiry cycle
    order_key = f"DELTA_MONTHLY_{signal_direction}_{expiry.strftime('%Y-%m-%d')}"
    if state.get("last_delta_auto_order_key") == order_key:
        print(f"[Delta Auto-Order Skipped: {order_key} already executed for this cycle]")
        return

    print(f"\n🚀 Triggering Delta Exchange Live Monthly 5000-OTM Order for {signal_direction}...")
    try:
        execute_monthly_option_sell(signal_direction=signal_direction, lots=DELTA_LOTS)
        state["last_delta_auto_order_key"] = order_key
        save_alert_state(state)
    except Exception as exc:
        print(f"[Delta Auto-Order Error: {exc}]")


def send_entry_alert(selected, risk, expiry, trend_direction):
    state = load_alert_state()
    key = "|".join([expiry.isoformat(), selected["short"]["name"], selected["hedge"]["name"]])

    alerted = set(state.get("entry_alerts", []))
    if key in alerted:
        print("[Telegram entry alert skipped: this exact spread was already alerted]")
    else:
        message = (
            "ENTRY SETUP: " + ("BULL PUT SPREAD" if selected["side"] == "PUT" else "BEAR CALL SPREAD") +
            " | SELL " + selected["short"]["name"] +
            " | HEDGE " + selected["hedge"]["name"] +
            f" | Suggested max size {risk['qty']:.2f} BTC | Estimated risk Rs. {risk['loss_inr'] * risk['qty']:,.0f}. "
            "Delta Auto-Execution (Monthly 5000-OTM 1 Lot + Auto-SL) Enabled."
        )
        alert(message)
        alerted.add(key)
        state["entry_alerts"] = sorted(alerted)[-500:]
        state["last_entry_key"] = key
        save_alert_state(state)

    # Trigger Live Delta Monthly 5000-OTM Option Sell + Auto-SL
    trigger_delta_monthly_auto_order(trend_direction, expiry)


def monitor_open_position(summaries, instruments, usd_inr):
    if not MONITOR_POSITION:
        return
    if OPEN_POSITION_TYPE == "IRON_CONDOR":
        names = [OPEN_PUT_SHORT_INSTRUMENT, OPEN_PUT_HEDGE_INSTRUMENT,
                 OPEN_CALL_SHORT_INSTRUMENT, OPEN_CALL_HEDGE_INSTRUMENT]
        if not all(names) or ENTRY_CREDIT_BTC <= 0 or OPEN_POSITION_SIZE_BTC <= 0:
            print("EXIT MONITOR: Iron Condor configuration incomplete; no exit estimate.")
            return
        by_name = {x.get("instrument_name"): x for x in summaries}
        qs = [by_name.get(n) for n in names]
        if any(q is None for q in qs) or any(q.get(k) is None for q,k in zip(qs,["ask_price","bid_price","ask_price","bid_price"])):
            print("EXIT MONITOR: one or more Iron Condor quotes unavailable.")
            return
        close_cost = max(0.0,float(qs[0]["ask_price"])-float(qs[1]["bid_price"])) + max(0.0,float(qs[2]["ask_price"])-float(qs[3]["bid_price"]))
        pnl_btc_per_1 = ENTRY_CREDIT_BTC-close_cost
        pnl_inr = pnl_btc_per_1*get_spot()*OPEN_POSITION_SIZE_BTC*usd_inr
        captured = pnl_btc_per_1/ENTRY_CREDIT_BTC
        inst_map={x.get("instrument_name"):x for x in instruments}
        expiries=[inst_map[n].get("expiration_timestamp") for n in names if inst_map.get(n) and inst_map[n].get("expiration_timestamp")]
        dte=min([(datetime.fromtimestamp(ts/1000,timezone.utc)-datetime.now(timezone.utc)).total_seconds()/86400 for ts in expiries],default=None)
        print("\nEXIT MONITOR (manual Iron Condor):")
        print(f"Combined close cost: {close_cost:.6f} BTC | Estimated P/L: Rs. {pnl_inr:,.0f} before fees/slippage")
        state=load_alert_state(); done=set(state.get("exit_alerts",[]))
        poskey="IRON_CONDOR|"+"|".join(names)+f"|{ENTRY_CREDIT_BTC}|{OPEN_POSITION_SIZE_BTC}"
        checks=[]
        if captured>=TAKE_PROFIT_CREDIT_FRACTION: checks.append(("TAKE_PROFIT",f"IRON CONDOR TAKE-PROFIT: {captured*100:.1f}% credit captured; estimated P/L Rs. {pnl_inr:,.0f}. Review closing all four legs."))
        if close_cost>=ENTRY_CREDIT_BTC*STOP_CLOSE_COST_MULTIPLE: checks.append(("STOP",f"IRON CONDOR STOP: close cost {close_cost/ENTRY_CREDIT_BTC:.2f}x entry credit; review risk immediately."))
        if dte is not None and 0<=dte<=EXIT_ALERT_DTE_DAYS: checks.append(("EXPIRY",f"IRON CONDOR EXPIRY WARNING: {dte:.2f} days remain. Review all four legs manually."))
        for event,msg in checks:
            ek=poskey+"|"+event
            if ek not in done: alert(msg+" No automatic order is sent."); done.add(ek)
        state["exit_alerts"]=sorted(done); save_alert_state(state)
        return
    if (not OPEN_SHORT_INSTRUMENT or not OPEN_HEDGE_INSTRUMENT or
            ENTRY_CREDIT_BTC <= 0 or OPEN_POSITION_SIZE_BTC <= 0 or
            OPEN_POSITION_SIDE not in ("PUT", "CALL")):
        print("EXIT MONITOR: enabled, but position configuration is incomplete; no exit estimate.")
        return

    by_name = {x.get("instrument_name"): x for x in summaries}
    short = by_name.get(OPEN_SHORT_INSTRUMENT)
    hedge = by_name.get(OPEN_HEDGE_INSTRUMENT)
    if not short or not hedge:
        print("EXIT MONITOR: position quotes unavailable; cannot calculate close cost.")
        return
    values = [short.get("ask_price"), hedge.get("bid_price")]
    if any(v is None for v in values):
        print("EXIT MONITOR: bid/ask missing; cannot calculate close cost.")
        return

    short_ask, hedge_bid = float(values[0]), float(values[1])
    close_cost_btc = max(0.0, short_ask - hedge_bid)
    pnl_btc_per_1 = ENTRY_CREDIT_BTC - close_cost_btc
    pnl_usd = pnl_btc_per_1 * get_spot() * OPEN_POSITION_SIZE_BTC
    pnl_inr = pnl_usd * usd_inr
    captured = pnl_btc_per_1 / ENTRY_CREDIT_BTC

    instrument_map = {x.get("instrument_name"): x for x in instruments}
    short_inst = instrument_map.get(OPEN_SHORT_INSTRUMENT)
    expiry_ts = short_inst.get("expiration_timestamp") if short_inst else None
    dte = None
    if expiry_ts:
        dte = (datetime.fromtimestamp(expiry_ts / 1000, timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 86400.0

    print("\nEXIT MONITOR (manual position):")
    print(f"Close cost estimate : {close_cost_btc:.6f} BTC per 1 BTC amount")
    print(f"Unrealized P/L est. : Rs. {pnl_inr:,.0f} (before fees/slippage)")
    if dte is not None:
        print(f"Time to expiry      : {dte:.2f} days")

    state = load_alert_state()
    alerts_done = set(state.get("exit_alerts", []))
    position_key = f"{OPEN_SHORT_INSTRUMENT}|{OPEN_HEDGE_INSTRUMENT}|{ENTRY_CREDIT_BTC}|{OPEN_POSITION_SIZE_BTC}"
    checks = []
    if captured >= TAKE_PROFIT_CREDIT_FRACTION:
        checks.append(("TAKE_PROFIT", f"TAKE-PROFIT condition: approx. {captured*100:.1f}% of entry credit captured; estimated P/L Rs. {pnl_inr:,.0f}. Review closing both legs."))
    if close_cost_btc >= ENTRY_CREDIT_BTC * STOP_CLOSE_COST_MULTIPLE:
        checks.append(("STOP", f"STOP condition: spread close cost is {close_cost_btc/ENTRY_CREDIT_BTC:.2f}x entry credit; estimated P/L Rs. {pnl_inr:,.0f}. Review risk immediately."))
    if dte is not None and 0 <= dte <= EXIT_ALERT_DTE_DAYS:
        checks.append(("EXPIRY", f"EXPIRY warning: only {dte:.2f} days remain. Review/close/roll decision manually."))

    for event, message in checks:
        event_key = position_key + "|" + event
        if event_key not in alerts_done:
            alert(message + " No automatic exit/order is sent.")
            alerts_done.add(event_key)
    state["exit_alerts"] = sorted(alerts_done)
    save_alert_state(state)


def scan():
    print("\nDownloading public market data...")
    spot=get_spot(); instruments=get_instruments(); summaries=get_summaries(); usd_inr=get_usd_inr()
    monitor_open_position(summaries,instruments,usd_inr)
    trend=get_daily_trend(); range_pct=get_completed_7d_range_pct()
    expiry_data=choose_expiry(instruments)
    print("\n"+"="*64+"\n SHINE BTC OPTIONS V7.0 | DELTA AUTO-ORDER ENABLED\n"+"="*64)
    print(f"TIME UTC       : {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}")
    print(f"BTC INDEX      : ${spot:,.2f} | USD/INR: Rs. {usd_inr:.4f}")
    print(f"CAPITAL        : Rs. {CAPITAL_INR:,.0f} | RISK BUDGET: Rs. {RISK_BUDGET_INR:,.0f}")
    print(f"DAILY TREND    : {trend['trend']} | Completed candle: {trend['completed_candle_utc']:%Y-%m-%d}")
    print(f"EMA20 / EMA50  : {trend['ema20']:,.2f} / {trend['ema50']:,.2f}")
    print(f"COMPLETED 7D RANGE: {range_pct:.2f}%")
    print(f"OPEN POSITIONS : {OPEN_POSITIONS_COUNT}/{MAX_SIMULTANEOUS_POSITIONS} (manual setting)")
    if expiry_data is None:
        print("FINAL SIGNAL: NO TRADE - no eligible expiry in DTE window."); return
    expiry_ts,expiry,dte=expiry_data
    imap,smap=build_maps(instruments,summaries,expiry_ts)
    print(f"EXPIRY         : {expiry:%d-%b-%Y %H:%M UTC} ({dte:.1f} DTE) | CHAIN: {len(smap)}")
    send_market_change_alerts(spot,smap)
    put=find_spread("PUT",spot,imap,smap); call=find_spread("CALL",spot,imap,smap)
    selected=None; risk=None; strategy=""
    if trend["trend"]=="BULLISH":
        selected=put; strategy="BULL PUT SPREAD"
        if selected: risk=risk_size(selected,spot,usd_inr)
    elif trend["trend"]=="BEARISH":
        selected=call; strategy="BEAR CALL SPREAD"
        if selected: risk=risk_size(selected,spot,usd_inr)
    else:
        condor,reason=find_iron_condor(spot,imap,smap,range_pct)
        print(f"IRON CONDOR FILTER: {reason}")
        if condor:
            selected=condor; strategy="IRON CONDOR"; risk=risk_size_condor(condor,spot,usd_inr)
    print("\n"+"="*64+"\nFINAL SIGNAL\n"+"="*64)
    if OPEN_POSITIONS_COUNT>=MAX_SIMULTANEOUS_POSITIONS:
        print("NO NEW ENTRY: maximum simultaneous position count reached.")
    elif selected is None:
        print("NO TRADE: no candidate passed trend / Iron Condor filters / market data checks.")
    elif not risk or not risk["allowed"]:
        print(f"NO TRADE: minimum order exceeds per-trade risk budget. Minimum estimated risk Rs. {risk['min_risk']:,.0f}.")
    else:
        if strategy=="IRON CONDOR":
            print("STRATEGY: IRON CONDOR")
            print("PUT  SELL:",selected["put"]["short"]["name"],"| HEDGE BUY:",selected["put"]["hedge"]["name"])
            print("CALL SELL:",selected["call"]["short"]["name"],"| HEDGE BUY:",selected["call"]["hedge"]["name"])
            send_condor_entry_alert(selected,risk,expiry)
            record={"time_utc":datetime.now(timezone.utc).isoformat(),"spot_usd":spot,"usd_inr":usd_inr,
                    "capital_inr":CAPITAL_INR,"risk_budget_inr":RISK_BUDGET_INR,"trend":trend["trend"],
                    "expiry_utc":expiry.isoformat(),"strategy":strategy,
                    "put_sell":selected["put"]["short"]["name"],"put_hedge":selected["put"]["hedge"]["name"],
                    "call_sell":selected["call"]["short"]["name"],"call_hedge":selected["call"]["hedge"]["name"],
                    "combined_credit_btc":selected["credit_btc"],"estimated_max_loss_inr_per_btc":risk["loss_inr"],
                    "risk_based_size":risk["qty"],"status":"SIGNAL_ONLY"}
        else:
            print("STRATEGY:",strategy)
            print("SELL:",selected["short"]["name"],"| HEDGE BUY:",selected["hedge"]["name"])
            send_entry_alert(selected,risk,expiry,trend["trend"])
            record={"time_utc":datetime.now(timezone.utc).isoformat(),"spot_usd":spot,"usd_inr":usd_inr,
                    "capital_inr":CAPITAL_INR,"risk_budget_inr":RISK_BUDGET_INR,"trend":trend["trend"],
                    "expiry_utc":expiry.isoformat(),"strategy":strategy,"sell_instrument":selected["short"]["name"],
                    "hedge_instrument":selected["hedge"]["name"],"credit_btc":selected["credit_btc"],
                    "width_usd":selected["width_usd"],"heuristic_score_not_probability":selected["score"],
                    "max_loss_inr_per_btc_amount":risk["loss_inr"],"risk_based_size":risk["qty"],"status":"DELTA_AUTO_EXEC"}
        print(f"SUGGESTED SIZE: {risk['qty']:.2f} BTC | ESTIMATED MAX LOSS: Rs. {risk['loss_inr']*risk['qty']:,.0f}")
        save_signal(record)
    print("Signal history:",CSV_FILE)


if __name__ == "__main__":
    print("Starting SHINE BTC OPTIONS V7.0 (Delta Monthly 5000-OTM Auto-Execution)...")
    print(f"Capital Rs. {CAPITAL_INR:,.0f} | Risk limit Rs. {RISK_BUDGET_INR:,.0f} ({RISK_PERCENT:.1f}%)")
    print(f"Delta Auto-Trading: {'ENABLED (1 Lot Monthly 5000-OTM + Auto-SL)' if AUTO_TRADE_DELTA and DELTA_MODULE_AVAILABLE else 'DISABLED'}")
    
    if os.getenv("GITHUB_ACTIONS") == "true":
        # GitHub Actions par 1 scan karke clean बंद ho jayega (har 15 min mein cron khud chalayega)
        scan()
    else:
        # PC par chalane par lagatar 15 min loop mein chalega
        send_telegram("Connection test successful. SHINE BTC OPTIONS V7.0 + Delta Monthly 5000-OTM Auto-Execution is active.")
        print("Press CTRL + C to stop.")
        while True:
            try:
                scan()
            except KeyboardInterrupt:
                print("\nAgent stopped by user.")
                break
            except Exception as exc:
                print("\nSCAN ERROR:", exc)
                print("No signal generated because required data was unavailable.")
            print(f"\nNext scan in {SCAN_SECONDS // 60} minutes...")
            try:
                time.sleep(SCAN_SECONDS)
            except KeyboardInterrupt:
                print("\nAgent stopped by user.")
                break