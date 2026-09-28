"""Metering: one row per model call, charged to the account that made it.

`record_llm_usage` runs after every successful OpenAI call (see `llm.py`).
It is a no-op for the local profile and for code paths with no bound
principal (the CLIs), and it never raises: a metering failure is logged, the
answer the user paid for is still returned.

A sponsored call that uses the allowance up marks the binding's spend guard,
which `llm.py` reads before every later call in the same piece of work. The
allowance is otherwise checked when the work starts, and a build or an
annotation job is hundreds of calls: without the guard, an account with a
cent left could spend a whole job on the platform key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from research_tree.billing.allowances import charge_allowance
from research_tree.billing.pricing import WEB_SEARCH_USD, cost_usd
from research_tree.db import database_url, get_engine
from research_tree.principal import current_binding

logger = logging.getLogger("uvicorn.error")

RECENT_EVENTS = 20


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


def usage_from_response(raw_response: dict[str, Any]) -> TokenUsage:
    """Reads both shapes OpenAI uses: Responses (`input_tokens`/`output_tokens`
    with detail objects) and Embeddings (`prompt_tokens`)."""

    usage = raw_response.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    return TokenUsage(
        input_tokens=_int(usage.get("input_tokens", usage.get("prompt_tokens"))),
        cached_input_tokens=_int(
            input_details.get("cached_tokens") if isinstance(input_details, dict) else 0
        ),
        output_tokens=_int(usage.get("output_tokens")),
        reasoning_tokens=_int(
            output_details.get("reasoning_tokens") if isinstance(output_details, dict) else 0
        ),
    )


def web_searches(raw_response: dict[str, Any]) -> int:
    """The searches a response ran; opening or reading a page is not charged."""

    output = raw_response.get("output")
    count = 0
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict) or item.get("type") != "web_search_call":
            continue
        action = item.get("action")
        # One that does not say what it did is charged as a search.
        if not isinstance(action, dict) or action.get("type", "search") == "search":
            count += 1
    return count


def _int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def record_llm_usage(*, model: str, raw_response: dict[str, Any], label: str) -> None:
    binding = current_binding()
    if binding is None or binding.principal.is_local:
        return
    if database_url() is None:
        return
    usage = usage_from_response(raw_response)
    cost = cost_usd(
        model,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
    ) + WEB_SEARCH_USD * web_searches(raw_response)
    source = binding.credential_source or "unknown"
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO usage_events (user_id, source, feature, label, model, "
                    "input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, "
                    "cost_usd, request_id) VALUES (CAST(:user_id AS uuid), :source, :feature, "
                    ":label, :model, :input_tokens, :cached_input_tokens, :output_tokens, "
                    ":reasoning_tokens, :cost_usd, :request_id)"
                ),
                {
                    "user_id": binding.principal.user_id,
                    "source": source,
                    "feature": binding.feature,
                    "label": label[:120],
                    "model": model[:120],
                    "input_tokens": usage.input_tokens,
                    "cached_input_tokens": usage.cached_input_tokens,
                    "output_tokens": usage.output_tokens,
                    "reasoning_tokens": usage.reasoning_tokens,
                    "cost_usd": cost,
                    "request_id": binding.request_id,
                },
            )
            if source == "sponsored" and cost > 0:
                # None means there is no allowance left to charge, which ends
                # the work as surely as spending it all does.
                if charge_allowance(conn, binding.principal.user_id, cost) is not False:
                    binding.spend.exhausted = True
    except Exception as error:  # noqa: BLE001 - metering must never fail the call it meters
        logger.warning("usage not recorded for %s (%s): %s", label, model, error)


def usage_summary(user_id: str, *, days: int = 30) -> dict[str, Any]:
    with get_engine().begin() as conn:
        totals = (
            conn.execute(
                text(
                    "SELECT source, count(*) AS calls, coalesce(sum(cost_usd), 0) AS cost_usd "
                    "FROM usage_events WHERE user_id = CAST(:user_id AS uuid) "
                    "AND created_at >= now() - make_interval(days => :days) GROUP BY source"
                ),
                {"user_id": user_id, "days": days},
            )
            .mappings()
            .all()
        )
        recent = (
            conn.execute(
                text(
                    "SELECT created_at, source, feature, label, model, cost_usd "
                    "FROM usage_events WHERE user_id = CAST(:user_id AS uuid) "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"user_id": user_id, "limit": RECENT_EVENTS},
            )
            .mappings()
            .all()
        )
    by_source = {str(row["source"]): float(Decimal(row["cost_usd"])) for row in totals}
    return {
        "days": days,
        "calls": int(sum(int(row["calls"]) for row in totals)),
        "total_usd": float(sum((Decimal(row["cost_usd"]) for row in totals), Decimal(0))),
        "byok_usd": by_source.get("byok", 0.0),
        "sponsored_usd": by_source.get("sponsored", 0.0),
        "recent": [
            {
                "created_at": row["created_at"].isoformat()
                if isinstance(row["created_at"], datetime)
                else None,
                "source": str(row["source"]),
                "feature": row["feature"],
                "label": row["label"],
                "model": str(row["model"]),
                "cost_usd": float(Decimal(row["cost_usd"])),
            }
            for row in recent
        ],
    }
