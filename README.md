# TTM Squeeze Trading Bot

A Python bot that scans a watchlist every 5 minutes during market hours, detects TTM Squeeze signals, asks Claude (in John Carter's voice) to analyze them, and places paper trades automatically via Tradier's sandbox API.

## What it does

1. **TTM Squeeze detection** — compares Bollinger Bands against Keltner Channels. A "squeeze" is when BB is inside KC (low volatility coiling). When the squeeze fires (BB expands outside KC), momentum direction determines the trade.
2. **Momentum histogram** — linear regression on the mid-point displacement gives the direction and strength of the move.
3. **Claude AI analysis** — each signal is sent to Claude with a prompt modeled on John Carter's trading style for a plain-English read.
4. **Paper trade execution** — buys on bullish fires, sells short on bearish fires via the Tradier sandbox (no real money).

## Setup

### 1. Clone / copy the project

```bash
cd ~/squeeze-bot
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure credentials

```bash
cp .env.template .env
```

Edit `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `TRADIER_SANDBOX_TOKEN` | [sandbox.tradier.com](https://sandbox.tradier.com) → API Access |
| `TRADIER_ACCOUNT_ID` | Shown in your Tradier sandbox dashboard |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys |

### 5. Run the bot

```bash
python squeeze_bot.py
```

The bot will:
- Wait until market hours (9:30–16:00 ET, weekdays)
- Scan AAPL, TSLA, NVDA, SPY, QQQ, MSFT, AMD, META every 5 minutes
- Print signals and Claude's analysis to the console
- Place paper orders in your Tradier sandbox account

## Configuration (top of squeeze_bot.py)

| Variable | Default | Description |
|---|---|---|
| `BB_LENGTH` | 20 | Bollinger Band period |
| `BB_MULT` | 2.0 | Bollinger Band std-dev multiplier |
| `KC_LENGTH` | 20 | Keltner Channel period |
| `KC_MULT` | 1.5 | Keltner Channel ATR multiplier |
| `MOMENTUM_LENGTH` | 12 | Linear regression period for momentum |
| `SCAN_INTERVAL_SECONDS` | 300 | Seconds between scans |
| `DEFAULT_SHARES` | 1 | Shares per paper trade |

## TTM Squeeze Logic

```
Squeeze ON  → BB upper < KC upper AND BB lower > KC lower
Squeeze FIRE → squeeze was ON last bar, is OFF this bar
Bullish fire → momentum > 0 when squeeze fires
Bearish fire → momentum < 0 when squeeze fires
```

## Signals detected

- **Squeeze FIRE (bullish)** → places a `buy` market order
- **Squeeze FIRE (bearish)** → places a `sell_short` market order
- **Momentum zero-line cross** → logged only (no trade; use as confirmation)

## Disclaimer

This bot is for educational and paper-trading purposes only. It uses Tradier's **sandbox** environment — no real money is involved. Always do your own due diligence before trading with real capital.
