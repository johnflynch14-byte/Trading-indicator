"""
TTM Squeeze Trading Bot — Multi-Timeframe (Daily + 15-min)

Strategy (John Carter style):
  1. Daily scan  → find symbols with squeeze ON (coiling) or fired within last 3 bars
  2. 15-min scan → for those symbols only, watch for squeeze fire in the same direction
  3. Alert       → daily context + 15-min trigger = high-conviction entry signal

SIM_MODE=true  → runs without Tradier using synthetic OHLCV data.
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

# How many daily bars back a fired squeeze is still considered "active context"
DAILY_FIRE_LOOKBACK = 3

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

# ─── SUPABASE ─────────────────────────────────────────────────────────────────

def _make_supabase() -> SupabaseClient | None:
    if SUPABASE_URL and SUPABASE_ANON_KEY:
        return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    log.warning("Supabase not configured — signals won't be persisted.")
    return None

supabase: SupabaseClient | None = _make_supabase()


def _db_upsert_status(symbol: str, signal: dict, timeframe: str = "daily"):
    if not supabase:
        return
    try:
        direction = "BULLISH" if signal["fired_bullish"] else ("BEARISH" if signal["fired_bearish"] else None)
        key = symbol if timeframe == "daily" else f"{symbol}_15m"
        supabase.table("squeeze_status").upsert({
            "symbol":     key,
            "squeeze_on": signal["squeeze_on"],
            "momentum":   signal["momentum"],
            "close":      signal["close"],
            "direction":  direction,
            "updated_at": datetime.datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        log.error(f"Supabase status upsert failed: {e}")


def _db_insert_signal(symbol: str, signal: dict, analysis: str, timeframe: str,
                      daily_direction: str | None = None):
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
            "timeframe":       timeframe,
            "daily_direction": daily_direction,
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

_SIM_PRICES = {
    "NVDA": 875.0, "AMD": 155.0,  "TSM":  175.0, "PLTR":  25.0,
    "CRWD": 380.0, "MSTR": 520.0, "AAPL": 213.0, "MSFT":  415.0,
    "GOOGL":175.0, "META": 490.0, "AMZN": 190.0, "TSLA":  175.0,
    "COIN": 230.0, "SHOP":  90.0, "SQ":    75.0, "UBER":   80.0,
    "NET":   95.0, "SNOW":  155.0,"SPY":  530.0, "QQQ":   450.0,
    "IWM":  205.0, "TQQQ":  55.0, "RBLX":  45.0, "HOOD":   20.0,
}

def _generate_sim_bars(symbol: str, n: int = 80, force_fire: bool = False,
                       force_squeeze_on: bool = False, seed_offset: int = 0) -> list[dict]:
    rng   = random.Random(symbol + str(seed_offset))
    base  = _SIM_PRICES.get(symbol, 100.0)
    bars  = []
    price = base

    for _ in range(n):
        ret   = rng.gauss(0.0003, 0.012)
        close = max(price * (1 + ret), 1.0)
        high  = close * (1 + abs(rng.gauss(0, 0.005)))
        low   = close * (1 - abs(rng.gauss(0, 0.005)))
        vol   = int(rng.gauss(30_000_000, 8_000_000))
        bars.append({"open": price, "high": high, "low": low,
                     "close": close, "volume": max(vol, 500_000)})
        price = close

    if force_squeeze_on:
        # Keep last 5 bars in a tight range → BB stays inside KC
        for i in range(-5, 0):
            c = bars[i]["close"]
            bars[i].update({"high": c * 1.001, "low": c * 0.999})

    if force_fire:
        c = bars[-2]["close"]
        bars[-2].update({"high": c * 1.001, "low": c * 0.999, "close": c})
        breakout = c * 1.018
        bars[-1].update({"open": c, "high": breakout * 1.005,
                         "low": c * 0.999, "close": breakout})
    return bars


def _sim_quote(symbol: str, bars: list[dict]) -> dict:
    last   = bars[-1]["close"]
    spread = last * 0.001
    return {
        "symbol": symbol, "last": round(last, 2),
        "bid": round(last - spread / 2, 2), "ask": round(last + spread / 2, 2),
        "volume": bars[-1]["volume"],
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
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: dict = None):
        resp = self.session.post(f"{self.base_url}{path}", data=data, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_quote(self, symbol: str) -> dict:
        data   = self._get("/markets/quotes", {"symbols": symbol, "greeks": "false"})
        quotes = data.get("quotes", {}).get("quote", {})
        return quotes if isinstance(quotes, dict) else {}

    def get_daily_bars(self, symbol: str, lookback: int = 80) -> list[dict]:
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=max(lookback * 2, 120))
        data  = self._get("/markets/history", {
            "symbol": symbol, "interval": "daily",
            "start": start.isoformat(), "end": end.isoformat(),
        })
        history = data.get("history", {})
        if not history or history == "null":
            return []
        days = history.get("day", [])
        return days if isinstance(days, list) else [days]

    def get_intraday_bars(self, symbol: str, interval: str = "15min") -> list[dict]:
        """Fetch intraday bars for today + yesterday (enough for 15-min squeeze)."""
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=5)   # back 5 days covers weekends
        data  = self._get("/markets/timesales", {
            "symbol": symbol, "interval": interval,
            "start": f"{start} 09:30", "end": f"{end} 16:00",
            "session_filter": "open",
        })
        series = data.get("series", {})
        if not series or series == "null":
            return []
        candles = series.get("data", [])
        if isinstance(candles, dict):
            candles = [candles]
        # Normalize field names to match daily bar format
        bars = []
        for c in candles:
            bars.append({
                "open":   float(c.get("open",  c.get("o", 0))),
                "high":   float(c.get("high",  c.get("h", 0))),
                "low":    float(c.get("low",   c.get("l", 0))),
                "close":  float(c.get("close", c.get("c", 0))),
                "volume": int(c.get("volume",  c.get("v", 0))),
            })
        return bars

    def place_order(self, account_id: str, symbol: str, side: str, quantity: int,
                    order_type: str = "market", duration: str = "day") -> dict:
        return self._post(f"/accounts/{account_id}/orders", {
            "class": "equity", "symbol": symbol, "side": side,
            "quantity": quantity, "type": order_type, "duration": duration,
        })


# ─── TTM SQUEEZE CALCULATIONS ─────────────────────────────────────────────────

def _sma(v: np.ndarray, n: int) -> np.ndarray:
    r = np.full_like(v, np.nan)
    for i in range(n - 1, len(v)):
        r[i] = v[i - n + 1: i + 1].mean()
    return r

def _stdev(v: np.ndarray, n: int) -> np.ndarray:
    r = np.full_like(v, np.nan)
    for i in range(n - 1, len(v)):
        r[i] = v[i - n + 1: i + 1].std(ddof=0)
    return r

def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    pc = np.concatenate([[np.nan], close[:-1]])
    return np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))

def _linreg(v: np.ndarray, n: int) -> np.ndarray:
    r = np.full_like(v, np.nan)
    x = np.arange(n, dtype=float)
    for i in range(n - 1, len(v)):
        y = v[i - n + 1: i + 1]
        m, b = np.polyfit(x, y, 1)
        r[i] = m * (n - 1) + b
    return r

def compute_ttm_squeeze(bars: list[dict]) -> dict | None:
    if len(bars) < BB_LENGTH + MOMENTUM_LENGTH + 5:
        return None

    closes = np.array([float(b["close"]) for b in bars], dtype=float)
    highs  = np.array([float(b["high"])  for b in bars], dtype=float)
    lows   = np.array([float(b["low"])   for b in bars], dtype=float)

    bb_basis = _sma(closes, BB_LENGTH)
    bb_dev   = _stdev(closes, BB_LENGTH)
    bb_upper = bb_basis + BB_MULT * bb_dev
    bb_lower = bb_basis - BB_MULT * bb_dev

    kc_basis = _sma(closes, KC_LENGTH)
    atr      = _sma(_true_range(highs, lows, closes), KC_LENGTH)
    kc_upper = kc_basis + KC_MULT * atr
    kc_lower = kc_basis - KC_MULT * atr

    squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)

    mid      = (highs + lows) / 2.0
    delta    = closes - _sma(mid + _sma(closes, KC_LENGTH), KC_LENGTH)
    momentum = _linreg(delta, MOMENTUM_LENGTH)

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
        "squeeze_history": [bool(x) for x in squeeze_on[-DAILY_FIRE_LOOKBACK:]],
    }


def daily_is_actionable(daily: dict) -> tuple[bool, str | None]:
    """
    Returns (actionable, direction).
    Actionable if:
      - Squeeze is currently ON (coiling), OR
      - Squeeze fired within the last DAILY_FIRE_LOOKBACK bars
    Direction is the daily momentum direction.
    """
    direction = None
    if daily["momentum"] > 0:
        direction = "BULLISH"
    elif daily["momentum"] < 0:
        direction = "BEARISH"

    if daily["squeeze_on"]:
        return True, direction

    # Fired recently if any bar in lookback was ON and then turned OFF
    history = daily.get("squeeze_history", [])
    if any(history):
        return True, direction

    return False, None


# ─── CLAUDE ANALYSIS ──────────────────────────────────────────────────────────

def analyze_with_claude(symbol: str, intraday: dict, quote: dict,
                        daily: dict, daily_direction: str) -> str:
    client        = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    m15_direction = "BULLISH" if intraday["fired_bullish"] else "BEARISH"
    aligned       = m15_direction == daily_direction

    prompt = f"""You are John Carter, trading expert and creator of the TTM Squeeze indicator.

A multi-timeframe TTM Squeeze signal just triggered. Give a sharp, actionable trade recommendation in 4–6 sentences.

Symbol: {symbol}
Price: ${quote.get('last', 'N/A')}  |  Bid/Ask: ${quote.get('bid', 'N/A')} / ${quote.get('ask', 'N/A')}  |  Volume: {quote.get('volume', 'N/A')}

DAILY TIMEFRAME:
- Squeeze: {"ON (still coiling)" if daily["squeeze_on"] else "recently fired"}
- Momentum: {daily['momentum']:+.4f} → {daily_direction}
- Daily close: ${daily['close']:.2f}

15-MINUTE TIMEFRAME:
- Squeeze just FIRED: {m15_direction}
- Momentum: {intraday['momentum']:+.4f} (prev: {intraday['momentum_prev']:+.4f})
- Momentum {"accelerating" if abs(intraday['momentum']) > abs(intraday['momentum_prev']) else "decelerating"}
- BB: {intraday['bb_lower']:.2f} – {intraday['bb_upper']:.2f}
- KC: {intraday['kc_lower']:.2f} – {intraday['kc_upper']:.2f}

Timeframe alignment: {"✅ ALIGNED — daily and 15-min agree" if aligned else "⚠️ DIVERGING — daily and 15-min disagree"}

Provide: trade direction, entry, stop-loss, and confidence (low/med/high).
If diverging, explain the risk clearly. Speak like John Carter — direct, no fluff."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
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


# ─── MARKET HOURS ─────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    if SIM_MODE:
        return True
    now_et = datetime.datetime.now(datetime.timezone.utc).astimezone(
        datetime.timezone(datetime.timedelta(hours=-4))
    )
    if now_et.weekday() >= 5:
        return False
    return MARKET_OPEN <= now_et.time() <= MARKET_CLOSE


# ─── MAIN SCAN ────────────────────────────────────────────────────────────────

def scan(tradier: TradierClient | None):
    mode_label = "[SIM]" if SIM_MODE else "[LIVE]"
    log.info(f"{'═'*60}")
    log.info(f"  PASS 1 — Daily squeeze scan {mode_label}")
    log.info(f"{'═'*60}")

    # ── Pass 1: daily ────────────────────────────────────────────────────────
    qualified: list[tuple[str, dict, str]] = []   # (symbol, daily_signal, direction)

    for symbol in WATCHLIST:
        try:
            if SIM_MODE:
                # Randomise: ~40% coiling, ~20% fired, ~40% nothing
                rng = random.Random(symbol + str(int(time.time() // 3600)))
                roll = rng.random()
                if roll < 0.4:
                    daily_bars = _generate_sim_bars(symbol, n=80, force_squeeze_on=True)
                elif roll < 0.6:
                    daily_bars = _generate_sim_bars(symbol, n=80, force_fire=True)
                else:
                    daily_bars = _generate_sim_bars(symbol, n=80)
            else:
                daily_bars = tradier.get_daily_bars(symbol)

            if not daily_bars:
                continue

            daily = compute_ttm_squeeze(daily_bars)
            if daily is None:
                continue

            _db_upsert_status(symbol, daily, timeframe="daily")

            actionable, direction = daily_is_actionable(daily)
            status = (
                "🔴 COILING"  if daily["squeeze_on"] else
                "🟢 FIRED"    if daily["fired_bullish"] or daily["fired_bearish"] else
                "   no squeeze"
            )
            log.info(
                f"  {symbol:6s}  ${daily['close']:>8.2f}  "
                f"mom={daily['momentum']:+.5f}  {status}"
                + (f"  → ON RADAR ({direction})" if actionable else "")
            )

            if actionable and direction:
                qualified.append((symbol, daily, direction))

        except Exception as e:
            log.exception(f"  {symbol} daily scan error: {e}")

    log.info(f"\n  {len(qualified)} symbol(s) on radar for 15-min check: "
             f"{[s for s,_,_ in qualified]}\n")

    if not qualified:
        return

    # ── Pass 2: 15-min ───────────────────────────────────────────────────────
    log.info(f"{'═'*60}")
    log.info(f"  PASS 2 — 15-min squeeze scan {mode_label}")
    log.info(f"{'═'*60}")

    for symbol, daily, daily_direction in qualified:
        try:
            if SIM_MODE:
                rng  = random.Random(symbol + "15m" + str(int(time.time() // 300)))
                fire = rng.random() < 0.35   # ~35% chance of 15-min fire in sim
                m15_bars = _generate_sim_bars(symbol, n=60, force_fire=fire, seed_offset=999)
            else:
                m15_bars = tradier.get_intraday_bars(symbol, interval="15min")

            if not m15_bars:
                log.info(f"  {symbol}: no 15-min data")
                continue

            m15 = compute_ttm_squeeze(m15_bars)
            if m15 is None:
                log.info(f"  {symbol}: insufficient 15-min bars")
                continue

            _db_upsert_status(symbol, m15, timeframe="15min")

            m15_fired = m15["fired_bullish"] or m15["fired_bearish"]
            m15_dir   = "BULLISH" if m15["fired_bullish"] else ("BEARISH" if m15["fired_bearish"] else None)
            aligned   = m15_dir == daily_direction if m15_dir else False

            if not m15_fired:
                squeeze_state = "SQUEEZE ON" if m15["squeeze_on"] else "no squeeze"
                log.info(f"  {symbol:6s}  15m: mom={m15['momentum']:+.5f}  [{squeeze_state}]  — waiting for fire")
                continue

            # ── SIGNAL ───────────────────────────────────────────────────────
            alignment_tag = "ALIGNED ✅" if aligned else "DIVERGING ⚠️"
            alert_bar = "█" * 60
            log.info(f"\n  {alert_bar}")
            log.info(f"  🚨 ENTRY SIGNAL: {symbol}  |  15-min {m15_dir}  |  Daily {daily_direction}  |  {alignment_tag}")
            log.info(f"  {alert_bar}\n")

            if SIM_MODE:
                quote = _sim_quote(symbol, m15_bars)
            else:
                quote = tradier.get_quote(symbol)

            analysis = analyze_with_claude(symbol, m15, quote, daily, daily_direction)
            log.info(
                f"\n{'─'*60}\n"
                f"  Claude / John Carter — {symbol} ({m15_dir} 15m | {daily_direction} Daily):\n\n"
                f"{analysis}\n"
                f"{'─'*60}\n"
            )

            _db_insert_signal(symbol, m15, analysis, timeframe="15min",
                              daily_direction=daily_direction)

            # Only auto-trade when daily and 15-min are aligned
            if aligned:
                execute_trade(tradier, symbol, m15)
            else:
                log.info(f"  {symbol}: skipping trade — daily/15m diverge")

        except Exception as e:
            log.exception(f"  {symbol} 15-min scan error: {e}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if SIM_MODE:
        log.info("=" * 60)
        log.info("  SIMULATION MODE")
        log.info("  Add TRADIER_SANDBOX_TOKEN to .env to go live.")
        log.info("=" * 60)
    else:
        log.info("TTM Squeeze Bot — LIVE sandbox mode")

    log.info(f"Watchlist ({len(WATCHLIST)}): {', '.join(WATCHLIST)}")
    log.info(f"Scan interval: {SCAN_INTERVAL_SECONDS // 60} min  |  "
             f"Daily fire lookback: {DAILY_FIRE_LOOKBACK} bars")

    tradier = TradierClient(token=TRADIER_SANDBOX_TOKEN) if not SIM_MODE else None

    while True:
        if is_market_open():
            scan(tradier)
        else:
            log.info("Market closed — waiting…")
        log.info(f"Next scan in {SCAN_INTERVAL_SECONDS // 60} minutes.\n")
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
