"""Exceptions raised by the AI module."""

from __future__ import annotations


class AIAnalysisError(Exception):
    """Raised when the OpenAI call fails after retries, or the response fails
    schema validation."""
