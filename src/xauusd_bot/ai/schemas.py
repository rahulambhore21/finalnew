"""Internal schema for OpenAI's raw structured-output response.

Kept separate from ``xauusd_bot.domain.models.MarketAnalysis``: this schema
is only what the model itself produces; ``MarketAnalysis`` additionally
carries metadata (analyzed_at, candle_time, symbol, model) that the analyzer
fills in after the call, not the model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class AIResponseSchema(BaseModel):
    """Raw Support/Resistance response expected from OpenAI."""

    support: float = Field(gt=0)
    resistance: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=500)
    lot_size: float = Field(
        default=0.05,
        ge=0.01,
        le=2.0,
        description="Recommended lot size based on analysis confidence and trade setup strength, usually from 0.05 to 0.10",
    )

    @model_validator(mode="after")
    def resistance_above_support(self) -> AIResponseSchema:
        if self.resistance <= self.support:
            raise ValueError("resistance must be greater than support")
        return self
