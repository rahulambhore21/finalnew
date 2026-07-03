# Project Vision

## What We Are Building

We are building an AI-powered automated Forex trading system for **XAUUSD (Gold)** using **MetaTrader 5 (MT5)**, the **MT5 MCP Server**, and the **OpenAI API**.

The objective is to automate a trading workflow that is currently performed manually by the client.

Today, the client manually:

1. Opens the XAUUSD chart in MT5.
2. Observes the current market.
3. Takes a screenshot of the chart.
4. Uploads the screenshot to ChatGPT.
5. Asks ChatGPT to analyze the market.
6. Receives support and resistance levels.
7. Waits for price to reach one of those levels.
8. Calculates the trade manually.
9. Places trades on multiple MT5 accounts.

This project automates that entire workflow.

---

# System Goal

The system should continuously monitor the XAUUSD market, automatically request AI analysis, identify trading opportunities, calculate the required risk management parameters, and execute trades without manual intervention.

The user should only start the application.

Everything else should happen automatically.

---

# Trading Instrument

Version 1 supports only:

- XAUUSD (Gold)

No other symbols will be supported.

---

# Market Analysis

The system uses the OpenAI API to analyze market data.

The AI identifies:

- Support
- Resistance
- Confidence
- Analysis reasoning

The AI is responsible only for market analysis.

The AI never executes trades.

---

# Market Monitoring

The system continuously monitors the XAUUSD market.

Analysis is performed using:

- M5 timeframe
- M15 timeframe

To reduce API costs, the AI is only called when the system is actively searching for a new trading opportunity.

No AI requests should be made while trades are open.

---

# Trade Trigger

Once the AI returns Support and Resistance levels, the system watches the live market price.

If the current market price reaches either Support or Resistance:

The system immediately prepares and executes the trade.

---

# Trading Accounts

The system connects to four MT5 accounts.

Every trading opportunity executes:

Account 1 → BUY

Account 2 → BUY

Account 3 → SELL

Account 4 → SELL

All four trades are executed simultaneously.

---

# Risk Management

The user never enters Stop Loss or Take Profit prices.

Instead the user defines:

Maximum Risk = $50

Target Profit = $150

The system automatically calculates:

- Lot Size
- Stop Loss Price
- Take Profit Price

using MT5 symbol specifications.

The goal is:

If Stop Loss is reached:

Loss ≈ $50

If Take Profit is reached:

Profit ≈ $150

---

# Trade Management

Once trades are opened:

The system does not modify them.

No:

- Trailing Stop
- Break-even
- Partial Close

Trades remain active until:

- Stop Loss
or
- Take Profit

---

# Daily Trading Rule

Only one trading opportunity is allowed each trading day.

One opportunity consists of:

- Four simultaneous trades

After execution, the system stops searching for new opportunities until the next trading day.

---

# Logging

Every important action should be recorded.

Examples:

- AI request
- AI response
- Market analysis
- Support & Resistance
- Entry
- Lot Size
- Stop Loss
- Take Profit
- Trade Result
- Errors

The goal is to make every trading decision traceable.

---

# Reliability

The system must prioritize:

- Reliability
- Maintainability
- Readability
- Safety

over adding unnecessary features.

Every module should have a single responsibility.

Business logic should remain independent from MT5 communication.

Configuration should never be hardcoded.

The architecture should allow future expansion without major redesign.

---

# Long-Term Vision

Version 1 focuses on creating a stable and reliable automated trading system for XAUUSD.

Once the core system is proven on demo accounts, future versions may introduce:

- Additional trading symbols
- Multiple AI providers
- More advanced trading strategies
- Dashboard and monitoring
- Backtesting
- Performance analytics
- Multiple trading opportunities per day

However, Version 1 should focus only on delivering a stable, production-ready automation system for XAUUSD.

---

# Client Workflow Transformation

## Current Workflow

Trader
↓

Open MT5

↓

Take Screenshot

↓

Upload to ChatGPT

↓

Receive Support & Resistance

↓

Wait for Price

↓

Calculate Risk

↓

Place Trade

↓

Monitor Trade

---

## Automated Workflow

Start Bot

↓

Monitor XAUUSD

↓

AI Analysis

↓

Watch Price

↓

Calculate Risk

↓

Execute 4 Trades

↓

Monitor Until TP/SL

↓

Log Everything

---

This gives a clear understanding that the goal isn't just to "build a trading bot" — it's to **automate the client's existing manual process** while keeping the trading logic consistent.
