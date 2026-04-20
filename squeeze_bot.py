"""
TTM Squeeze Trading Bot
Detects TTM Squeeze signals and places paper trades via Tradier sandbox.

SIM_MODE=true  → runs without Tradier; generates synthetic OHLCV data and
                  forces a squeeze-fire signal so the Claude pipeline can be
                  tested immediately.
"""

import os
import time
import random
import logging
import datetime
import requests
import numpy as np
from dotenv import load_dotenv
import anthropic
from supabase import create_client, Client as SupabaseClient

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TRADIER_SANDBOX_TOKEN = os.getenv("TRADIER_SANDBOX_TOKEN", "")
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
TRADIER_ACCOUNT_ID    = os.getenv("TRADIER_ACCOUNT_ID", "")
SUPABASE_URL          = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY     = os.getenv("SUPABASE_ANON_KEY", "")

# Set SIM_MODE=true in .env (or env) to run without Tradier credentials
SIM_MODE = os.getenv("SIM_MODE", "false").lower() == "true" or not TRADIER_SANDBOX_TOKEN

TRADIER_BASE_URL = "https://sandbox.tradier.com/v1"

WATCHLIST = [
    "NVDA", "AMD", "TSM", "PLTR", "CRWD", "MSTR", "AAPL", "MSFT",
    "GOOGL", "META", "AMZN", "TSLA", "COIN", "SHOP", "SQ", "UBER",
    "NET", "SNOW", "SPY", "QQQ", "IWM", "TQQQ", "RBLX", "HOOD",
]
SCAN_INTERVAL_SECONDS = 300   # 5 minutes

# TTM Squeeze parameters (John Carter defaults)
BB_LENGTH       = 20
BB_MULT         = 2.0
KC_LENGTH       = 20
KC_MULT         = 1.5
MOMENTUM_LENGTH = 12

DEFAULT_SHARES = 1

MARKET_OPEN  = datetime.time(9, 30)
MARKET_CLOSE = datetime.time(16, 0)

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── SUPABASE CLIENT ──────────────────────────────────────────────────────────

def _make_supabase() -> SupabaseClient | None:
    if SUPABASE_URL and SUPABASE_ANON_KEY:
        return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    log.warning("SUPABASE_URL / SUPABASE_ANON_KEY not set — signals won't be persisted.")
    return None

supabase: SupabaseClient | None = _make_supabase()


def _db_upsert_status(symbol: str, signal: dict):
    if not supabase:
        return
    try:
        direction = "BULLISH" if signal["fired_bullish"] else ("BEARISH" if signal["fired_bearish"] else None)
        supabase.table("squeeze_status").upsert({
            "symbol":     symbol,
            "squeeze_on": signal["squeeze_on"],
            "momentum":   signal["momentum"],
            "close":      signal["close"],
            "direction":  direction,
            "updated_at": datetime.datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        log.error(f"Supabase status upsert failed: {e}")


def _db_insert_signal(symbol: str, signal: dict, analysis: str):
    if not supabase:
        return
    try:
        direction = "BULLISH" if signal["fired_bullish"] else "BEARISH"
        supabase.table("signals").insert({
            "symbol":          symbol,
            "direction":       direction,
            "close":           signal["close"],
            "momentum":        signal["momentum"],
            "momentum_prev":   signal["momentum_prev"],
            "bb_upper":        signal["bb_upper"],
            "bb_lower":        signal["bb_lower"],
            "kc_upper":        signal["kc_upper"],
            "kc_lower":        signal["kc_lower"],
            "claude_analysis": analysis,
            "sim_mode":        SIM_MODE,
        }).execute()
    except Exception as e:
        log.error(f"Supabase signal insert failed: {e}")


def _db_insert_trade(symbol: str, side: str, price: float, order_id: str = ""):
    if not supabase:
        return
    try:
        supabase.table("trades").insert({
            "symbol":   symbol,
            "side":     side,
            "quantity": DEFAULT_SHARES,
            "price":    price,
            "order_id": order_id,
            "sim_mode": SIM_MODE,
        }).execute()
    except Exception as e:
        log.error(f"Supabase trade insert failed: {e}")

# ─── SIMULATION DATA ──────────────────────────────────────────────────────────

# Approximate base prices for realistic simulation output
_SIM_PRICES = {
    "AAPL": 213.0, "TSLA": 175.0, "NVDA": 875.0,
    "SPY":  530.0, "QQQ":  450.0, "MSFT": 415.0,
    "AMD":  155.0, "META": 490.0,
}

def _generate_sim_bars(symbol: str, n: int = 80, force_fire: bool = True) -> list[dict]:
    """
    Generate synthetic OHLCV bars that mimic a realistic price series.
    When force_fire=True the last two bars are crafted to produce a squeeze fire.
    """
    rng   = random.Random(symbol)          # deterministic per symbol
    base  = _SIM_PRICES.get(symbol, 200.0)
    bars  = []
    price = base

    for i in range(n):
        daily_ret = rng.gauss(0.0003, 0.015)
        close = max(price * (1 + daily_ret), 1.0)
        high  = close * (1 + abs(rng.gauss(0, 0.005)))
        low   = close * (1 - abs(rng.gauss(0, 0.005)))
        open_ = price
        vol   = int(rng.gauss(50_000_000, 10_000_000))
        bars.append({"open": open_, "high": high, "low": low,
                     "close": close, "volume": max(vol, 1_000_000)})
        price = close

    if force_fire:
        # Second-to-last bar: squeeze ON (tight range, BB inside KC guaranteed by
        # keeping stddev very low relative to ATR).
        last_close = bars[-2]["close"]
        bars[-2].update({
            "high":  last_close * 1.002,
            "low":   last_close * 0.998,
            "close": last_close,
        })
        # Last bar: price breakout upward → squeeze fires bullish
        breakout = last_close * 1.018
        bars[-1].update({
            "open":  last_close,
            "high":  breakout * 1.005,
            "low":   last_close * 0.999,
            "close": breakout,
        })

    return bars


def _sim_quote(symbol: str, bars: list[dict]) -> dict:
    last = bars[-1]["close"]
    spread = last * 0.001
    return {
        "symbol":    symbol,
        "last":      round(last, 2),
        "bid":       round(last - spread / 2, 2),
        "ask":       round(last + spread / 2, 2),
        "volume":    bars[-1]["volume"],
        "change":    round(last - bars[-2]["close"], 2),
        "change_pct": round((last / bars[-2]["close"] - 1) * 100, 2),
    }


# ─── TRADIER CLIENT ───────────────────────────────────────────────────────────
class TradierClient:
    def __init__(self, token: str, base_url: str = TRADIER_BASE_URL):
        self.base_url = base_url
        self.session  = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    def _get(self, path: str, params: dict = None):
        url  = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: dict = None):
        url  = f"{self.base_url}{path}"
        resp = self.session.post(url, data=data, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_quote(self, symbol: str) -> dict:
        data   = self._get("/markets/quotes", {"symbols": symbol, "greeks": "false"})
        quotes = data.get("quotes", {}).get("quote", {})
        return quotes if isinstance(quotes, dict) else {}

    def get_history(self, symbol: str, interval: str = "daily", lookback: int = 80) -> list[dict]:
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=max(lookback * 2, 120))
        data  = self._get("/markets/history", {
            "symbol":   symbol,
            "interval": interval,
            "start":    start.isoformat(),
            "end":      end.isoformat(),
        })
        history = data.get("history", {})
        if not history or history == "null":
            return []
        days = history.get("day", [])
        return days if isinstance(days, list) else [days]

    def place_order(self, account_id: str, symbol: str, side: str, quantity: int,
                    order_type: str = "market", duration: str = "day") -> dict:
        return self._post(f"/accounts/{account_id}/orders", {
            "class":    "equity",
            "symbol":   symbol,
            "side":     side,
            "quantity": quantity,
            "type":     order_type,
            "duration": duration,
        })

    def get_positions(self, account_id: str) -> list[dict]:
        data      = self._get(f"/accounts/{account_id}/positions")
        pos       = data.get("positions", {})
        if not pos or pos == "null":
            return []
        positions = pos.get("position", [])
        return positions if isinstance(positions, list) else [positions]


# ─── TTM SQUEEZE CALCULATIONS ─────────────────────────────────────────────────

def _sma(values: np.ndarray, n: int) -> np.ndarray:
    result = np.full_like(values, np.nan)
    for i in range(n - 1, len(values)):
        result[i] = values[i - n + 1: i + 1].mean()
    return result

def _stdev(values: np.ndarray, n: int) -> np.ndarray:
    result = np.full_like(values, np.nan)
    for i in range(n - 1, len(values)):
        result[i] = values[i - n + 1: i + 1].std(ddof=0)
    return result

def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.concatenate([[np.nan], close[:-1]])
    return np.maximum(high - low,
           np.maximum(np.abs(high - prev_close),
                      np.abs(low  - prev_close)))

def _linreg_value(values: np.ndarray, length: int) -> np.ndarray:
    result = np.full_like(values, np.nan)
    x = np.arange(length, dtype=float)
    for i in range(length - 1, len(values)):
        y = values[i - length + 1: i + 1]
        m, b = np.polyfit(x, y, 1)
        result[i] = m * (length - 1) + b
    return result

def compute_ttm_squeeze(bars: list[dict]) -> dict | None:
    if len(bars) < BB_LENGTH + MOMENTUM_LENGTH + 5:
        return None

    closes = np.array([float(b["close"]) for b in bars], dtype=float)
    highs  = np.array([float(b["high"])  for b in bars], dtype=float)
    lows   = np.array([float(b["low"])   for b in bars], dtype=float)

    # Bollinger Bands
    bb_basis = _sma(closes, BB_LENGTH)
    bb_dev   = _stdev(closes, BB_LENGTH)
    bb_upper = bb_basis + BB_MULT * bb_dev
    bb_lower = bb_basis - BB_MULT * bb_dev

    # Keltner Channels
    kc_basis = _sma(closes, KC_LENGTH)
    atr      = _sma(_true_range(highs, lows, closes), KC_LENGTH)
    kc_upper = kc_basis + KC_MULT * atr
    kc_lower = kc_basis - KC_MULT * atr

    squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)

    # Momentum
    mid      = (highs + lows) / 2.0
    delta    = closes - _sma(mid + _sma(closes, KC_LENGTH), KC_LENGTH)
    momentum = _linreg_value(delta, MOMENTUM_LENGTH)

    if np.isnan(momentum[-1]) or np.isnan(momentum[-2]):
        return None

    fired_bullish = (not squeeze_on[-1]) and squeeze_on[-2] and momentum[-1] > 0
    fired_bearish = (not squeeze_on[-1]) and squeeze_on[-2] and momentum[-1] < 0

    return {
        "squeeze_on":     bool(squeeze_on[-1]),
        "fired_bullish":  fired_bullish,
        "fired_bearish":  fired_bearish,
        "momentum":       float(momentum[-1]),
        "momentum_prev":  float(momentum[-2]),
        "mom_cross_up":   bool(momentum[-1] > 0 and momentum[-2] <= 0),
        "mom_cross_down": bool(momentum[-1] < 0 and momentum[-2] >= 0),
        "close":          float(closes[-1]),
        "bb_upper":       float(bb_upper[-1]),
        "bb_lower":       float(bb_lower[-1]),
        "kc_upper":       float(kc_upper[-1]),
        "kc_lower":       float(kc_lower[-1]),
    }


# ─── CLAUDE ANALYSIS ──────────────────────────────────────────────────────────

def analyze_with_claude(symbol: str, signal: dict, quote: dict) -> str:
    client    = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    direction = "BULLISH" if signal["fired_bullish"] else "BEARISH"

    prompt = f"""You are John Carter, the trading expert and author of "Mastering the Trade" who created the TTM Squeeze indicator.
Analyze the following TTM Squeeze signal and give a concise trade recommendation (3–5 sentences max).

Symbol: {symbol}
Signal Direction: {direction}
Current Price: ${quote.get('last', 'N/A')}
Bid/Ask: ${quote.get('bid', 'N/A')} / ${quote.get('ask', 'N/A')}
Volume: {quote.get('volume', 'N/A')}

TTM Squeeze Data:
- Squeeze just fired: YES – momentum released!
- Momentum value: {signal['momentum']:.4f}
- Previous momentum: {signal['momentum_prev']:.4f}
- Momentum trend: {"accelerating" if abs(signal['momentum']) > abs(signal['momentum_prev']) else "decelerating"}
- Bollinger Band upper/lower: {signal['bb_upper']:.2f} / {signal['bb_lower']:.2f}
- Keltner Channel upper/lower: {signal['kc_upper']:.2f} / {signal['kc_lower']:.2f}

Provide: trade direction, entry price suggestion, stop-loss placement, and confidence level (low/med/high).
Speak in John Carter's direct, practical trading style."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ─── TRADE EXECUTION ──────────────────────────────────────────────────────────

def execute_trade(tradier: TradierClient | None, symbol: str, signal: dict):
    if signal["fired_bullish"]:
        side, action = "buy", "BUY"
    elif signal["fired_bearish"]:
        side, action = "sell_short", "SELL SHORT"
    else:
        return

    if SIM_MODE or not tradier or not TRADIER_ACCOUNT_ID:
        log.info(f"  [SIM] Would place: {action} {DEFAULT_SHARES} {symbol} @ market")
        _db_insert_trade(symbol, side, signal["close"])
        return

    try:
        result   = tradier.place_order(TRADIER_ACCOUNT_ID, symbol, side, DEFAULT_SHARES)
        order_id = result.get("order", {}).get("id", "unknown")
        log.info(f"  ORDER PLACED  {action} {DEFAULT_SHARES} {symbol}  →  order_id={order_id}")
        _db_insert_trade(symbol, side, signal["close"], order_id)
    except requests.HTTPError as e:
        log.error(f"  Order failed for {symbol}: {e.response.text}")


# ─── MARKET HOURS CHECK ───────────────────────────────────────────────────────

def is_market_open() -> bool:
    if SIM_MODE:
        return True   # always scan in simulation mode
    now_et = datetime.datetime.now(datetime.timezone.utc).astimezone(
        datetime.timezone(datetime.timedelta(hours=-4))   # EDT (switch to -5 in EST)
    )
    if now_et.weekday() >= 5:
        return False
    return MARKET_OPEN <= now_et.time() <= MARKET_CLOSE


# ─── SCAN ─────────────────────────────────────────────────────────────────────

def scan(tradier: TradierClient | None):
    mode_label = "[SIM MODE]" if SIM_MODE else "[LIVE]"
    log.info(f"── Scanning {len(WATCHLIST)} symbols {mode_label} ──────────────")

    for symbol in WATCHLIST:
        try:
            if SIM_MODE:
                bars  = _generate_sim_bars(symbol, n=80, force_fire=True)
                quote = _sim_quote(symbol, bars)
            else:
                bars  = tradier.get_history(symbol, interval="daily", lookback=80)
                quote = tradier.get_quote(symbol)

            if not bars:
                log.warning(f"  {symbol}: no history returned")
                continue

            signal = compute_ttm_squeeze(bars)
            if signal is None:
                log.info(f"  {symbol}: insufficient data for TTM Squeeze")
                continue

            _db_upsert_status(symbol, signal)

            status = "SQUEEZE ON" if signal["squeeze_on"] else "no squeeze"
            log.info(
                f"  {symbol:6s}  close=${signal['close']:.2f}  "
                f"mom={signal['momentum']:+.4f}  [{status}]"
            )

            if signal["fired_bullish"] or signal["fired_bearish"]:
                direction = "BULLISH" if signal["fired_bullish"] else "BEARISH"
                log.info(f"  *** {symbol} SQUEEZE FIRED – {direction} ***")

                analysis = analyze_with_claude(symbol, signal, quote)
                log.info(
                    f"\n{'─'*60}\n"
                    f"Claude / John Carter Analysis — {symbol} ({direction}):\n"
                    f"{analysis}\n"
                    f"{'─'*60}\n"
                )

                _db_insert_signal(symbol, signal, analysis)
                execute_trade(tradier, symbol, signal)

            elif signal["mom_cross_up"] or signal["mom_cross_down"]:
                cross_dir = "UP" if signal["mom_cross_up"] else "DOWN"
                log.info(
                    f"  {symbol}: momentum zero-line cross {cross_dir} "
                    f"(squeeze {'ON' if signal['squeeze_on'] else 'OFF'})"
                )

        except requests.HTTPError as e:
            log.error(f"  {symbol}: HTTP error – {e}")
        except Exception as e:
            log.exception(f"  {symbol}: unexpected error – {e}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if SIM_MODE:
        log.info("=" * 60)
        log.info("  SIMULATION MODE – no Tradier token detected")
        log.info("  All market data is synthetic. Orders are printed only.")
        log.info("  Add TRADIER_SANDBOX_TOKEN to .env to go live.")
        log.info("=" * 60)
    else:
        log.info("TTM Squeeze Bot starting in LIVE (sandbox) mode…")

    log.info(f"Watchlist: {', '.join(WATCHLIST)}")
    log.info(f"Scan interval: {SCAN_INTERVAL_SECONDS}s")

    tradier = TradierClient(token=TRADIER_SANDBOX_TOKEN) if not SIM_MODE else None

    while True:
        if is_market_open():
            scan(tradier)
        else:
            log.info("Market closed – waiting…")

        log.info(f"Next scan in {SCAN_INTERVAL_SECONDS // 60} minutes.")
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
