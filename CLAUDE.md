# CLAUDE.md

> See [VISION.md](./VISION.md) for the "why" behind this project — the manual client workflow being automated and the long-term goal.

# Forex Gold Trading Automation System

## Project Overview

This project is a production-grade AI-powered Forex trading automation system for MetaTrader 5 (MT5).

The bot trades only **XAUUSD (Gold)** and automates the complete workflow from market analysis to trade execution.

The project must be modular, maintainable, scalable, and production-ready.

---

# Primary Objective

Build an automated trading system that:

- Monitors XAUUSD continuously.
- Uses OpenAI to identify Support and Resistance.
- Executes trades automatically on four MT5 accounts.
- Uses fixed dollar-based risk management.
- Minimizes OpenAI API usage.
- Produces detailed logs for every trading decision.

---

# Trading Rules

## Instrument

- XAUUSD only

No other symbols are supported in Version 1.

---

## Timeframes

Market analysis is based on:

- M5
- M15

---

## Trading Accounts

The system manages four MT5 accounts.

Execution strategy:

- Account 1 → BUY
- Account 2 → BUY
- Account 3 → SELL
- Account 4 → SELL

All four trades must be executed simultaneously.

---

## Daily Limit

Only one trading opportunity is allowed per trading day.

One opportunity consists of four simultaneous trades.

---

# AI Responsibilities

The AI is responsible only for market analysis.

It should return:

- Support Level
- Resistance Level
- Confidence Score
- Reason

AI must never execute trades or manage account state.

---

# OpenAI Usage

OpenAI API is a paid service.

Optimization rules:

- Never call OpenAI while trades are open.
- Only request analysis when searching for a new trade.
- Perform analysis once per new M5 candle.
- Reuse the latest Support and Resistance until the next analysis.

---

# Trade Trigger

When the live XAUUSD price reaches either the latest Support or Resistance level:

- Execute Market Orders immediately.

Do not use pending orders.

---

# Risk Management

User configuration:

- Risk = $50
- Reward = $150

The system must calculate:

- Lot Size
- Stop Loss Price
- Take Profit Price

Never calculate TP/SL using fixed price distances.

Always use MT5 symbol specifications.

---

# Lot Size

Allowed range:

- Minimum = 0.05
- Maximum = 0.10

All four accounts must use the same lot size.

---

# Trade Management

After execution:

- No Trailing Stop
- No Break-even
- No Partial Close

Wait until TP or SL.

---

# Logging

Every important action must be logged.

Including:

- AI requests
- AI responses
- Market analysis
- Trade execution
- Errors
- Retries
- Trade results

---

# Development Principles

- Build production-quality code.
- Prefer readability over cleverness.
- Keep modules independent.
- Follow Single Responsibility Principle.
- Never duplicate business logic.
- Validate every external response.
- Handle failures gracefully.
- Use configuration files instead of hardcoded values.
- Strong typing throughout the project.
- Every public function should have a docstring.

---

# Project Structure

The project should remain modular.

Major modules include:

- AI
- MT5
- Trading Engine
- Risk Engine
- Logger
- Configuration
- Database
- Utilities

Keep business logic separate from MT5 communication.

---

# Technology Stack

- Python
- MT5 MCP Server
- OpenAI API
- SQLite
- Environment Variables
- Modular Architecture

---

# Important Rules

1. Never hardcode configuration values.

2. Never duplicate logic.

3. Never trust AI responses without validation.

4. Never trust MT5 responses without validation.

5. Every external call must include proper error handling.

6. Every exception must be logged.

7. Keep functions small and reusable.

8. Build one feature completely before moving to the next.

9. Ask before changing existing architecture.

10. Prioritize reliability over adding new features.

---

# Goal

The objective is not simply to place trades.

The objective is to build a reliable, maintainable, and production-ready automated trading platform that can be extended with additional strategies and features in the future.