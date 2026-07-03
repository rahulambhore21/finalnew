"""OpenAI-backed Support/Resistance market analysis.

This module is the I/O boundary to OpenAI and contains no trading decision
logic. Callers decide *when* to call (once per new M5 candle, never while a
trade is open) and are responsible for persisting the resulting
MarketAnalysis via the database module.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import openai
from openai import OpenAI
from pydantic import ValidationError

from xauusd_bot.ai.errors import AIAnalysisError
from xauusd_bot.ai.schemas import AIResponseSchema
from xauusd_bot.domain.models import Candle, MarketAnalysis
from xauusd_bot.logging_setup.logger import get_logger

logger = get_logger(__name__)

_RETRY_DELAY_SECONDS = 1.0


def _format_candles(candles: list[Candle]) -> str:
    """Serialize candles as compact CSV rows: time,open,high,low,close,volume.

    Minute-precision timestamps keep each row short without losing
    information the model needs (chronological order + OHLCV values).
    """
    lines = ["time,open,high,low,close,volume"]
    for candle in candles:
        lines.append(
            f"{candle.time.strftime('%Y-%m-%dT%H:%M')},{candle.open},{candle.high},"
            f"{candle.low},{candle.close},{candle.volume}"
        )
    return "\n".join(lines)


def _build_user_message(symbol: str, m5_candles: list[Candle], m15_candles: list[Candle]) -> str:
    """Build the user message: symbol + compact CSV blocks for M5 and M15 candles."""
    return (
        f"Symbol: {symbol}\n\n"
        f"M5 candles (oldest to newest, {len(m5_candles)} bars):\n"
        f"{_format_candles(m5_candles)}\n\n"
        f"M15 candles (oldest to newest, {len(m15_candles)} bars):\n"
        f"{_format_candles(m15_candles)}"
    )


class AIAnalyzer:
    """Requests Support/Resistance analysis from OpenAI given recent candles."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: int,
        max_retries: int,
        temperature: float = 0.2,
        lot_min: float = 0.05,
        lot_max: float = 0.10,
    ) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._temperature = temperature
        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self._lot_min = lot_min
        self._lot_max = lot_max

    def _get_system_prompt(self) -> str:
        return (
            "You are a market analysis engine for an automated XAUUSD (Gold) trading "
            "system. Given recent M5 and M15 OHLCV candles, identify the single most "
            "relevant Support level and Resistance level for the current price action. "
            "Base your levels on recent swing highs/lows, price clusters, and rejection "
            f"points visible in the data. Recommend a lot size (lot_size) in the range "
            f"[{self._lot_min}, {self._lot_max}] (inclusive) based on your analysis confidence "
            "and trade setup strength. Respond only with the requested fields."
        )

    def analyze(
        self,
        symbol: str,
        m5_candles: list[Candle],
        m15_candles: list[Candle],
    ) -> MarketAnalysis:
        """Send structured OHLC candle data to OpenAI and return a validated MarketAnalysis.

        Raises:
            AIAnalysisError: on API failure after retries, empty m5_candles,
                or if the response fails schema validation after all retries.
        """
        if not m5_candles:
            raise AIAnalysisError("m5_candles is empty; cannot determine candle_time for analysis")

        parsed = self._request_with_retry(symbol, m5_candles, m15_candles)

        return MarketAnalysis(
            support=parsed.support,
            resistance=parsed.resistance,
            confidence=parsed.confidence,
            reason=parsed.reason,
            analyzed_at=datetime.now(timezone.utc),
            candle_time=m5_candles[-1].time,
            symbol=symbol,
            model=self._model,
            lot_size=parsed.lot_size,
        )

    def _request_with_retry(
        self, symbol: str, m5_candles: list[Candle], m15_candles: list[Candle]
    ) -> AIResponseSchema:
        """Call OpenAI and validate the response, retrying on any failure.

        Total attempts = max_retries + 1 (one initial attempt, then up to
        max_retries retries). Both transport/API errors and schema/business-
        rule validation failures count against the same budget, since both
        mean this attempt produced no usable analysis.

        Raises:
            AIAnalysisError: if every attempt fails.
        """
        total_attempts = self._max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, total_attempts + 1):
            logger.info(
                "AI analysis request attempt %d/%d for symbol=%s (m5=%d candles, m15=%d candles)",
                attempt,
                total_attempts,
                symbol,
                len(m5_candles),
                len(m15_candles),
            )
            try:
                completion = self._client.chat.completions.parse(
                    model=self._model,
                    temperature=self._temperature,
                    timeout=self._timeout_seconds,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt()},
                        {
                            "role": "user",
                            "content": _build_user_message(symbol, m5_candles, m15_candles),
                        },
                    ],
                    response_format=AIResponseSchema,
                )
                message = completion.choices[0].message
                if message.refusal:
                    raise AIAnalysisError(f"Model refused to answer: {message.refusal}")
                parsed = message.parsed
                if parsed is None:
                    raise AIAnalysisError(
                        "No parsed content in response "
                        f"(finish_reason={completion.choices[0].finish_reason!r})"
                    )
            except (openai.OpenAIError, AIAnalysisError, ValidationError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "AI analysis attempt %d/%d failed (%s: %s)",
                    attempt,
                    total_attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < total_attempts:
                    time.sleep(_RETRY_DELAY_SECONDS)
                continue

            logger.info(
                "AI analysis response received on attempt %d/%d: "
                "support=%s resistance=%s confidence=%s",
                attempt,
                total_attempts,
                parsed.support,
                parsed.resistance,
                parsed.confidence,
            )
            return parsed

        logger.error("AI analysis failed after %d attempt(s)", total_attempts)
        raise AIAnalysisError(f"AI analysis failed after {total_attempts} attempt(s)") from last_error
