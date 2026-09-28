"""What a model call costs, in US dollars.

List prices per million tokens. The defaults are OpenAI's published prices at
implementation time (September 2026, after the 2026-07-30 cuts); cached input
is billed at a tenth of fresh input. `RESEARCH_TREE_MODEL_PRICES` overrides or
extends the table without a deploy: a JSON object of model name to
`[input, cached_input, output]`. A model not in the table is billed at the
most expensive tier, so an unlisted model is never under-charged.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache

logger = logging.getLogger("uvicorn.error")

PRICES_ENV = "RESEARCH_TREE_MODEL_PRICES"
TOKENS_PER_UNIT = Decimal(1_000_000)
CENTS_PRECISION = Decimal("0.000001")


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input_usd: Decimal
    cached_input_usd: Decimal
    output_usd: Decimal


DEFAULT_PRICES: dict[str, tuple[str, str, str]] = {
    "gpt-5.6-luna": ("0.20", "0.02", "1.20"),
    "gpt-5.6-terra": ("2.00", "0.20", "12.00"),
    "gpt-5.6-sol": ("5.00", "0.50", "30.00"),
    "text-embedding-3-large": ("0.13", "0.13", "0"),
    "text-embedding-3-small": ("0.02", "0.02", "0"),
}
UNKNOWN_MODEL_PRICE: tuple[str, str, str] = ("5.00", "0.50", "30.00")
# The built-in web search's fee per search, for reasoning models ($10 per
# thousand). What its results add to the input is billed as input tokens.
WEB_SEARCH_USD = Decimal("0.01")


def _price(values: tuple[str, str, str] | list[object]) -> ModelPrice:
    fresh, cached, output = (Decimal(str(value)) for value in values)
    return ModelPrice(input_usd=fresh, cached_input_usd=cached, output_usd=output)


@lru_cache(maxsize=1)
def _price_table(raw_overrides: str) -> dict[str, ModelPrice]:
    table = {name: _price(values) for name, values in DEFAULT_PRICES.items()}
    if raw_overrides:
        try:
            overrides = json.loads(raw_overrides)
            if not isinstance(overrides, dict):
                raise ValueError("expected a JSON object")
            for name, values in overrides.items():
                if not isinstance(values, list) or len(values) != 3:
                    raise ValueError(f"{name}: expected [input, cached_input, output]")
                table[str(name)] = _price(values)
        except (ValueError, ArithmeticError) as error:
            logger.warning("%s ignored: %s", PRICES_ENV, error)
    return table


def price_for(model: str) -> ModelPrice:
    table = _price_table((os.environ.get(PRICES_ENV) or "").strip())
    name = model.strip()
    if name in table:
        return table[name]
    # Dated snapshots ("gpt-5.6-luna-2026-08-01") price like their base model.
    for candidate in sorted(table, key=len, reverse=True):
        if name.startswith(candidate):
            return table[candidate]
    return _price(UNKNOWN_MODEL_PRICE)


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
) -> Decimal:
    """`input_tokens` includes the cached ones, as OpenAI reports it; the
    cached share is billed at the cached rate and the rest at the fresh rate.
    Reasoning tokens are already inside `output_tokens`."""

    price = price_for(model)
    cached = max(min(cached_input_tokens, input_tokens), 0)
    fresh = max(input_tokens - cached, 0)
    total = (
        Decimal(fresh) * price.input_usd
        + Decimal(cached) * price.cached_input_usd
        + Decimal(max(output_tokens, 0)) * price.output_usd
    ) / TOKENS_PER_UNIT
    return total.quantize(CENTS_PRECISION, rounding=ROUND_HALF_UP)
