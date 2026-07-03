"""Unit tests for AIAnalyzer — mocks the OpenAI client (no real network calls)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from pydantic import ValidationError

from xauusd_bot.ai.analyzer import AIAnalyzer
from xauusd_bot.ai.errors import AIAnalysisError
from xauusd_bot.ai.schemas import AIResponseSchema
from xauusd_bot.domain.models import Candle


def _candles(count: int, start: datetime | None = None) -> list[Candle]:
    start = start or datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    return [
        Candle(
            time=start + timedelta(minutes=5 * i),
            open=1950.0,
            high=1951.0,
            low=1949.0,
            close=1950.0,
            volume=100,
        )
        for i in range(count)
    ]


def _completion(parsed=None, refusal=None, finish_reason="stop"):
    message = MagicMock(parsed=parsed, refusal=refusal)
    choice = MagicMock(message=message, finish_reason=finish_reason)
    return MagicMock(choices=[choice])


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xauusd_bot.ai.analyzer.time.sleep", lambda seconds: None)


@pytest.fixture
def openai_client_cls():
    with patch("xauusd_bot.ai.analyzer.OpenAI") as cls:
        yield cls


def _analyzer(openai_client_cls, **overrides) -> AIAnalyzer:
    kwargs: dict = dict(api_key="test-key", model="gpt-4o-mini", timeout_seconds=30, max_retries=3)
    kwargs.update(overrides)
    return AIAnalyzer(**kwargs)


class TestSuccessfulCall:
    def test_returns_correct_market_analysis(self, openai_client_cls) -> None:
        m5 = _candles(5)
        m15 = _candles(3)
        response = AIResponseSchema(support=1900.0, resistance=1950.0, confidence=0.7, reason="x", lot_size=0.08)
        client = openai_client_cls.return_value
        client.chat.completions.parse.return_value = _completion(parsed=response)

        analyzer = _analyzer(openai_client_cls)
        analysis = analyzer.analyze("XAUUSD", m5, m15)

        assert analysis.support == 1900.0
        assert analysis.resistance == 1950.0
        assert analysis.confidence == 0.7
        assert analysis.reason == "x"
        assert analysis.symbol == "XAUUSD"
        assert analysis.model == "gpt-4o-mini"
        assert analysis.candle_time == m5[-1].time
        assert analysis.lot_size == 0.08
        client.chat.completions.parse.assert_called_once()


class TestRetryThenSucceeds:
    def test_succeeds_on_second_attempt(self, openai_client_cls, caplog: pytest.LogCaptureFixture) -> None:
        response = AIResponseSchema(support=1900.0, resistance=1950.0, confidence=0.7, reason="x")
        client = openai_client_cls.return_value
        client.chat.completions.parse.side_effect = [_connection_error(), _completion(parsed=response)]

        analyzer = _analyzer(openai_client_cls)
        with caplog.at_level("WARNING"):
            analysis = analyzer.analyze("XAUUSD", _candles(3), _candles(3))

        assert analysis.support == 1900.0
        assert client.chat.completions.parse.call_count == 2
        assert any("attempt 1/4 failed" in rec.message for rec in caplog.records)


class TestAllAttemptsExhausted:
    def test_validation_failure_every_attempt_raises(self, openai_client_cls) -> None:
        client = openai_client_cls.return_value
        client.chat.completions.parse.side_effect = ValidationError.from_exception_data(
            "AIResponseSchema", []
        )

        analyzer = _analyzer(openai_client_cls, max_retries=2)
        with pytest.raises(AIAnalysisError):
            analyzer.analyze("XAUUSD", _candles(3), _candles(3))

        assert client.chat.completions.parse.call_count == 3

    def test_sdk_exception_every_attempt_raises(self, openai_client_cls) -> None:
        client = openai_client_cls.return_value
        client.chat.completions.parse.side_effect = _connection_error()

        analyzer = _analyzer(openai_client_cls, max_retries=2)
        with pytest.raises(AIAnalysisError):
            analyzer.analyze("XAUUSD", _candles(3), _candles(3))

        assert client.chat.completions.parse.call_count == 3

    def test_refusal_counts_as_failure(self, openai_client_cls) -> None:
        client = openai_client_cls.return_value
        client.chat.completions.parse.return_value = _completion(refusal="cannot help with that")

        analyzer = _analyzer(openai_client_cls, max_retries=0)
        with pytest.raises(AIAnalysisError):
            analyzer.analyze("XAUUSD", _candles(3), _candles(3))

        assert client.chat.completions.parse.call_count == 1

    def test_no_parsed_content_counts_as_failure(self, openai_client_cls) -> None:
        client = openai_client_cls.return_value
        client.chat.completions.parse.return_value = _completion(parsed=None, finish_reason="length")

        analyzer = _analyzer(openai_client_cls, max_retries=0)
        with pytest.raises(AIAnalysisError):
            analyzer.analyze("XAUUSD", _candles(3), _candles(3))


class TestPromptContent:
    def test_message_contains_correct_candle_counts(self, openai_client_cls) -> None:
        response = AIResponseSchema(support=1900.0, resistance=1950.0, confidence=0.7, reason="x")
        client = openai_client_cls.return_value
        client.chat.completions.parse.return_value = _completion(parsed=response)

        analyzer = _analyzer(openai_client_cls)
        m5 = _candles(7)
        m15 = _candles(4)
        analyzer.analyze("XAUUSD", m5, m15)

        call_kwargs = client.chat.completions.parse.call_args.kwargs
        user_message = call_kwargs["messages"][1]["content"]
        assert f"{len(m5)} bars" in user_message
        assert f"{len(m15)} bars" in user_message
        # header row + 7 data rows for M5, header row + 4 data rows for M15
        assert user_message.count("time,open,high,low,close,volume") == 2

    def test_system_prompt_contains_dynamic_lot_range(self, openai_client_cls) -> None:
        response = AIResponseSchema(support=1900.0, resistance=1950.0, confidence=0.7, reason="x", lot_size=0.08)
        client = openai_client_cls.return_value
        client.chat.completions.parse.return_value = _completion(parsed=response)

        analyzer = _analyzer(openai_client_cls, lot_min=0.06, lot_max=0.09)
        analyzer.analyze("XAUUSD", _candles(3), _candles(3))

        call_kwargs = client.chat.completions.parse.call_args.kwargs
        system_message = call_kwargs["messages"][0]["content"]
        assert "[0.06, 0.09]" in system_message


class TestConfigWiring:
    def test_client_and_call_receive_configured_settings(self, openai_client_cls) -> None:
        response = AIResponseSchema(support=1900.0, resistance=1950.0, confidence=0.7, reason="x")
        client = openai_client_cls.return_value
        client.chat.completions.parse.return_value = _completion(parsed=response)

        analyzer = _analyzer(
            openai_client_cls,
            api_key="my-key",
            model="gpt-4o-mini",
            timeout_seconds=45,
            max_retries=1,
            temperature=0.33,
        )
        analyzer.analyze("XAUUSD", _candles(3), _candles(3))

        openai_client_cls.assert_called_once_with(api_key="my-key", timeout=45, max_retries=0)
        call_kwargs = client.chat.completions.parse.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o-mini"
        assert call_kwargs["temperature"] == 0.33
        assert call_kwargs["timeout"] == 45
        assert call_kwargs["response_format"] is AIResponseSchema


class TestEmptyM5Candles:
    def test_raises_without_calling_api(self, openai_client_cls) -> None:
        analyzer = _analyzer(openai_client_cls)
        with pytest.raises(AIAnalysisError):
            analyzer.analyze("XAUUSD", [], _candles(3))

        openai_client_cls.return_value.chat.completions.parse.assert_not_called()
