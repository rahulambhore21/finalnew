# XAUUSD Trading Bot

AI-powered automated Forex trading system for XAUUSD (Gold) on MetaTrader 5.

See [VISION.md](./VISION.md) for what this system does and why, and [CLAUDE.md](./CLAUDE.md) for the detailed trading rules and development principles.

## Status

Project skeleton. Config, logging, and database modules are fully implemented. The AI, MT5, Risk Engine, and Trading Engine modules are stubbed out (typed interfaces, `NotImplementedError` bodies) and will be implemented one at a time.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env   # then fill in your OpenAI key and 4 MT5 account credentials
```

Non-secret tunables (risk/reward, lot bounds, symbol, timeframes, logging, DB path) live in `config/config.toml`.

## Running

```bash
python -m xauusd_bot.main
```

This loads configuration, sets up logging, and initializes the SQLite database. It does not yet connect to MT5 or call OpenAI — those modules are still stubs.
