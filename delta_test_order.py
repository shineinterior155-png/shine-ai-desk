import os
import hmac
import hashlib
import time
import json
import calendar
from datetime import datetime, timezone
import requests

# --- 1. CONFIGURATION (Reads from GitHub Secrets or Local Env) ---
API_KEY = os.getenv("DELTA_API_KEY", "YOUR_DELTA_API_KEY").strip()
API_SECRET = os.getenv("DELTA_API_SECRET", "YOUR_DELTA_API_SECRET").strip()
BASE_URL = "https://api.india.delta.exchange"

TELEGRAM_BOT_TOKEN = os.getenv("SHINE_TELEGRAM_BOT_TOKEN", "9032117445:AAEerBt_kqWXr-A8j1xmaQXmfnyp-O8i1-A").strip()
TELEGRAM_CHAT_ID = os.getenv("SHINE_TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID").strip()

OTM_DISTANCE = 5000       # Spot price se 5000 points dur
MIN_DTE = 8               # 7 DTE hote hi next monthly expiry
SL_MULTIPLIER = 1.5       # Entry premium ka 1.5x Stop-Loss

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"Telegram Error: {e}")

def generate_signature(secret, method, path, query_string="", payload=""):
    message = method + path + query_string + payload
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def has_open_btc_option_position():
    """
    Delta Exchange par check karta hai ki pehle se koi BTC Call/Put position open toh nahi hai.
    Isse GitHub Actions har 15 min mein duplicate order nahi marega.
    """
    path = "/v2/positions/margined"
    url = BASE_URL + path
    timestamp = str(int(time.time()))
    signature = generate_signature(API_SECRET, "GET", timestamp + path, "", "")
    headers = {
        "api-key": API_KEY,
        "signature": signature,
        "timestamp": timestamp,
        "Content-Type": "application/json",
        "User-Agent": "python-rest-client"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            positions = response.json().get("result", [])
            for p in positions:
                sym = p.get("product_symbol", "")
                size = int(p.get("size") or 0)
                if size != 0 and (sym.startswith("C-BTC-") or sym.startswith("P-BTC-")):
                    print(f"ℹ️ Active Delta BTC Option Position Found: {sym} (Size: {size}). Skipping new order.")
                    return True
    except Exception as e:
        print(f"Position check error: {e}")
    return False

def send_delta_request(payload):
    path = "/v2/orders"
    url = BASE_URL + path
    timestamp = str(int(time.time()))
    payload_str = json.dumps(payload, separators=(',', ':'))
    signature = generate_signature(API_SECRET, "POST", timestamp + path, "", payload_str)
    
    headers = {
        "api-key": API_KEY,
        "signature": signature,
        "timestamp": timestamp,
        "Content-Type": "application/json",
        "User-Agent": "python-rest-client"
    }
    response = requests.post(url, headers=headers, data=payload_str, timeout=15)
    return response.status_code, response.json()

def is_last_friday_of_month(dt):
    if dt.weekday() != 4:
        return False
    _, last_day = calendar.monthrange(dt.year, dt.month)
    return (last_day - dt.day) < 7

def find_monthly_5000_otm_option(signal_direction):
    url = f"{BASE_URL}/v2/tickers"
    response = requests.get(url, timeout=15)
    if response.status_code != 200:
        return None

    tickers = response.json().get("result", [])
    spot_price = 0
    for t in tickers:
        if t.get("symbol") == "BTCUSD":
            spot_price = float(t.get("spot_price") or t.get("mark_price") or 0)
            break
    if spot_price == 0:
        for t in tickers:
            if t.get("spot_price") and "BTC" in t.get("symbol", ""):
                spot_price = float(t.get("spot_price"))
                break
    if spot_price == 0:
        return None

    if signal_direction == "BEARISH":
        opt_code = "C"
        c_type_match = "call_options"
        target_strike = spot_price + OTM_DISTANCE
    else:
        opt_code = "P"
        c_type_match = "put_options"
        target_strike = spot_price - OTM_DISTANCE

    prefix = f"{opt_code}-BTC-"
    today = datetime.now(timezone.utc).date()
    monthly_candidates = []
    all_valid = []

    for t in tickers:
        symbol = t.get("symbol", "")
        if symbol.startswith(prefix) and t.get("contract_type") == c_type_match:
            parts = symbol.split("-")
            if len(parts) == 4:
                try:
                    strike = float(parts[2])
                    exp_date = datetime.strptime(parts[3], "%d%m%y").date()
                except ValueError:
                    continue
                dte = (exp_date - today).days
                if dte < MIN_DTE:
                    continue
                mark_price = float(t.get("mark_price") or 0)
                quotes = t.get("quotes") or {}
                best_bid = float(quotes.get("best_bid") or 0)
                item = {
                    "product_id": t.get("product_id"),
                    "symbol": symbol,
                    "strike": strike,
                    "dte": dte,
                    "exp_date": exp_date,
                    "mark_price": mark_price,
                    "best_bid": best_bid,
                    "spot_price": spot_price,
                    "strike_diff": abs(strike - target_strike)
                }
                all_valid.append(item)
                if is_last_friday_of_month(exp_date):
                    monthly_candidates.append(item)

    pool = monthly_candidates if monthly_candidates else [c for c in all_valid if c["dte"] >= 20]
    if not pool:
        pool = all_valid
    if not pool:
        return None

    nearest_monthly_exp = min(c["exp_date"] for c in pool)
    same_expiry_pool = [c for c in pool if c["exp_date"] == nearest_monthly_exp]
    same_expiry_pool.sort(key=lambda x: x["strike_diff"])
    return same_expiry_pool[0]

def place_limit_sell_order(product_id, limit_price, size=1):
    payload = {
        "product_id": product_id,
        "size": size,
        "side": "sell",
        "order_type": "limit_order",
        "limit_price": str(round(limit_price, 1)),
        "time_in_force": "gtc"
    }
    return send_delta_request(payload)

def place_auto_sl_order(product_id, sl_trigger_price, size=1):
    trigger_val = round(sl_trigger_price, 1)
    limit_val = round(trigger_val * 1.03, 1)
    payload = {
        "product_id": product_id,
        "size": size,
        "side": "buy",
        "order_type": "limit_order",
        "limit_price": str(limit_val),
        "stop_order_type": "stop_loss_order",
        "stop_price": str(trigger_val),
        "stop_trigger_method": "mark_price",
        "reduce_only": True
    }
    return send_delta_request(payload)

def execute_monthly_option_sell(signal_direction="BEARISH", lots=1):
    # Sabse pehle check karein ki koi live BTC option position pehle se open toh nahi hai
    if has_open_btc_option_position():
        return

    opt_name = "CALL" if signal_direction == "BEARISH" else "PUT"
    contract = find_monthly_5000_otm_option(signal_direction)
    if not contract:
        print("❌ Matching Monthly Option contract nahi mila!")
        return

    prod_id = contract["product_id"]
    sym = contract["symbol"]
    spot = contract["spot_price"]
    dte = contract["dte"]
    mark_price = contract["mark_price"]
    best_bid = contract["best_bid"]
    entry_limit = best_bid if best_bid > 0 else round(mark_price * 0.99, 1)

    status, res = place_limit_sell_order(prod_id, entry_limit, size=lots)
    if status == 200 and res.get("success"):
        sl_trigger = max(round(entry_limit * SL_MULTIPLIER, 1), round(entry_limit + 5.0, 1))
        time.sleep(1)
        sl_status, sl_res = place_auto_sl_order(prod_id, sl_trigger_price=sl_trigger, size=lots)
        if sl_status == 200 and sl_res.get("success"):
            sl_limit = round(sl_trigger * 1.03, 1)
            sl_text = f"✅ Active (Trigger: `${sl_trigger}` | Limit: `${sl_limit}`)"
        else:
            sl_text = f"⚠️ Auto-SL Pending (Manual `${sl_trigger}` lagayein)"

        msg = (
            f"🎯 *SHINE AI — 24/7 CLOUD MONTHLY SELL EXECUTED*\n"
            f"• *Signal:* `BTC {signal_direction} ({opt_name} SELL)`\n"
            f"• *BTC Spot:* `${spot:,.0f}`\n"
            f"• *Contract:* `{sym}` (`{dte} DTE Monthly`)\n"
            f"• *Sell Price (Limit):* `${entry_limit:.1f}` ({lots} Lot)\n"
            f"• *Auto Stop-Loss:* {sl_text}\n"
            f"• *Take-Profit:* `Manual`"
        )
        print("\n" + msg)
        send_telegram_alert(msg)
    else:
        send_telegram_alert(f"❌ *Monthly Option SELL Failed* (`{sym}`):\n`{res}`")