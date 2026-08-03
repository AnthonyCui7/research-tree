"""Guardrails for agent-requested pipeline reruns.

The agent may retune retrieval; it may not redesign it. One table below names
every knob the agent can move and the range each is allowed to move within, so
adding a tunable means adding a row rather than editing two mappings that can
drift apart.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Mapping

from research_tree.retrieval.candidate_preparation import PipelineConfig


@dataclass(frozen=True)
class TunableField:
    """One agent-visible knob, and the bounds it is clamped to."""

    config_field: str
    minimum: float
    maximum: float
    expensive_above: float | None = None


# Request key -> config field. Request keys are the agent's vocabulary and do not
# always match the config field they drive.
AGENT_TUNABLE_FIELDS: dict[str, TunableField] = {
    "max_candidates": TunableField("k", 1, 150, expensive_above=75),
    "top_k_depth": TunableField("k", 1, 150, expensive_above=75),
    "survey_count": TunableField("survey_baseline_count", 0, 20),
    "max_initial_results": TunableField("pool_target", 100, 20_000, expensive_above=10_000),
    "root_set_size": TunableField("root_set_size", 25, 400, expensive_above=350),
    "alpha": TunableField("citation_age_exponent", 0.25, 3.0),
}

MAX_QUERY_OVERRIDES = 8

FORBIDDEN_REQUEST_KEYS = {
    "scoring_algorithm",
    "scoring_formula",
    "dedupe_logic",
    "dedupe_algorithm",
    "embedding_model",
    "cross_encoder_model",
    "reranker",
    "reranker_logic",
    "candidate_schema",
    "hits_max_iterations",
    "shell_command",
    "code_change",
}

FORBIDDEN_REQUEST_PHRASES = (
    "change scoring",
    "change dedupe",
    "different embedding",
    "change embedding",
    "change reranker",
    "edit pipeline",
    "arbitrary shell",
    "unbounded",
)


def validate_pipeline_rerun_request(
    request: Mapping[str, Any],
    *,
    repo_root: Path,
    allow_pipeline_rerun: bool,
    prior_config: PipelineConfig | None = None,
) -> dict[str, Any]:
    defaults = prior_config or PipelineConfig(repo_root=repo_root)
    prior_defaults = {
        "topic": defaults.topic,
        **{
            request_key: getattr(defaults, tunable.config_field)
            for request_key, tunable in AGENT_TUNABLE_FIELDS.items()
        },
    }

    if not allow_pipeline_rerun:
        return _rejected("pipeline rerun is disabled for this agent run", prior_defaults)
    if not str(request.get("reason") or "").strip():
        return _rejected("pipeline rerun request is missing a reason", prior_defaults)
    if _requests_algorithm_change(request):
        return _rejected(
            "pipeline rerun request attempts to change retrieval, scoring, dedupe, "
            "reranker, embedding, or schema behavior",
            prior_defaults,
        )

    topic = str(request.get("topic") or defaults.topic).strip()
    if not topic:
        return _rejected("pipeline rerun topic cannot be empty", prior_defaults)

    warnings: list[str] = []
    overrides: dict[str, Any] = {}
    expensive = False
    for request_key, tunable in AGENT_TUNABLE_FIELDS.items():
        raw_value = request.get(request_key)
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return _rejected(f"{request_key} must be a number", prior_defaults)
        clamped = min(max(value, tunable.minimum), tunable.maximum)
        if clamped != value:
            warnings.append(
                f"{request_key} was clamped from {value:g} to {clamped:g}."
            )
        if tunable.expensive_above is not None and clamped > tunable.expensive_above:
            expensive = True
        current = getattr(defaults, tunable.config_field)
        overrides[tunable.config_field] = (
            int(clamped) if isinstance(current, int) else clamped
        )

    query_overrides = [
        str(query).strip()
        for query in request.get("query_overrides") or []
        if str(query).strip()
    ][:MAX_QUERY_OVERRIDES]

    config = replace(
        defaults,
        repo_root=repo_root,
        topic=topic,
        query_overrides=tuple(query_overrides),
        **overrides,
    )
    normalized_args = asdict(config)
    normalized_args["repo_root"] = str(config.repo_root)
    normalized_args["query_overrides"] = query_overrides
    normalized_args["reason"] = str(request.get("reason") or "")
    return {
        "allowed": True,
        "normalized_args": normalized_args,
        "rejection_reason": None,
        "warnings": warnings,
        "expensive": expensive,
        "prior_defaults": prior_defaults,
        "new_values": {"topic": topic, **overrides},
    }


def pipeline_config_from_normalized_args(args: Mapping[str, Any]) -> PipelineConfig:
    """Rebuild a config from a serialized one, ignoring unknown keys."""

    defaults = PipelineConfig(repo_root=Path(str(args.get("repo_root") or ".")))
    known = {field.name for field in fields(PipelineConfig)} - {"repo_root"}
    overrides: dict[str, Any] = {}
    for name in known:
        if name not in args or args[name] is None:
            continue
        current = getattr(defaults, name)
        value = args[name]
        if name == "query_overrides":
            overrides[name] = tuple(str(item) for item in value or [])
        elif isinstance(current, bool):
            overrides[name] = bool(value)
        elif isinstance(current, int):
            overrides[name] = int(value)
        elif isinstance(current, float):
            overrides[name] = float(value)
        else:
            overrides[name] = str(value)
    return replace(defaults, **overrides)


def _requests_algorithm_change(request: Mapping[str, Any]) -> bool:
    if any(key in request for key in FORBIDDEN_REQUEST_KEYS):
        return True
    lowered_text = " ".join(str(value).casefold() for value in request.values())
    return any(phrase in lowered_text for phrase in FORBIDDEN_REQUEST_PHRASES)


def _rejected(reason: str, prior_defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        "allowed": False,
        "normalized_args": {},
        "rejection_reason": reason,
        "warnings": [],
        "expensive": False,
        "prior_defaults": prior_defaults,
        "new_values": {},
    }
