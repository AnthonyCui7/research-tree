from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from research_tree.retrieval.candidate_preparation import PipelineConfig


SAFE_ALPHA_MIN = 0.25
SAFE_ALPHA_MAX = 3.0
SAFE_MAX_NON_SURVEY_CANDIDATES = 150
SAFE_MAX_SURVEY_CANDIDATES = 20
SAFE_MAX_S2_MULTIPLIER = 100
EXPENSIVE_NON_SURVEY_THRESHOLD = 75
EXPENSIVE_S2_TARGET_THRESHOLD = 5000


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
        "k": defaults.k,
        "survey_baseline_count": defaults.survey_baseline_count,
        "s2_bulk_citation_multiplier": defaults.s2_bulk_citation_multiplier,
        "citation_age_exponent": defaults.citation_age_exponent,
    }
    warnings: list[str] = []

    if not allow_pipeline_rerun:
        return _rejected(
            "pipeline rerun is disabled for this agent run",
            prior_defaults=prior_defaults,
        )
    if not str(request.get("reason") or "").strip():
        return _rejected(
            "pipeline rerun request is missing a reason",
            prior_defaults=prior_defaults,
        )
    if _requests_algorithm_change(request):
        return _rejected(
            "pipeline rerun request attempts to change retrieval, scoring, dedupe, reranker, embedding, or schema behavior",
            prior_defaults=prior_defaults,
        )

    topic = str(request.get("topic") or defaults.topic).strip()
    if not topic:
        return _rejected("pipeline rerun topic cannot be empty", prior_defaults=prior_defaults)

    max_candidates = _optional_int(request.get("max_candidates"))
    top_k_depth = _optional_int(request.get("top_k_depth"))
    max_initial_results = _optional_int(request.get("max_initial_results"))
    k = max_candidates or top_k_depth or defaults.k
    if k <= 0:
        return _rejected("max candidate count must be positive", prior_defaults=prior_defaults)
    if k > SAFE_MAX_NON_SURVEY_CANDIDATES:
        warnings.append(
            f"max_candidates capped from {k} to {SAFE_MAX_NON_SURVEY_CANDIDATES}."
        )
        k = SAFE_MAX_NON_SURVEY_CANDIDATES

    multiplier = defaults.s2_bulk_citation_multiplier
    if max_initial_results is not None:
        multiplier = max(1, min(SAFE_MAX_S2_MULTIPLIER, int(max_initial_results)))
        if max_initial_results > SAFE_MAX_S2_MULTIPLIER:
            warnings.append(
                "max_initial_results was capped to the safe S2 multiplier limit."
            )

    alpha = request.get("alpha")
    citation_age_exponent = defaults.citation_age_exponent
    if alpha is not None:
        try:
            citation_age_exponent = float(alpha)
        except (TypeError, ValueError):
            return _rejected("alpha must be a number", prior_defaults=prior_defaults)
        if not SAFE_ALPHA_MIN <= citation_age_exponent <= SAFE_ALPHA_MAX:
            return _rejected(
                f"alpha must be between {SAFE_ALPHA_MIN:g} and {SAFE_ALPHA_MAX:g}",
                prior_defaults=prior_defaults,
            )

    query_overrides = [
        str(query).strip()
        for query in request.get("query_overrides") or []
        if str(query).strip()
    ]
    if query_overrides:
        warnings.append(
            "query_overrides are recorded for provenance; the current candidate pipeline derives S2 query variants from topic."
        )

    config = PipelineConfig(
        repo_root=repo_root,
        topic=topic,
        k=k,
        survey_baseline_count=min(defaults.survey_baseline_count, SAFE_MAX_SURVEY_CANDIDATES),
        s2_bulk_citation_multiplier=multiplier,
        request_delay_seconds=defaults.request_delay_seconds,
        request_timeout_seconds=defaults.request_timeout_seconds,
        max_academic_retries=defaults.max_academic_retries,
        refresh_cache=defaults.refresh_cache,
        cross_encoder_model=defaults.cross_encoder_model,
        citation_age_exponent=citation_age_exponent,
        verbose=defaults.verbose,
    )
    normalized_args = asdict(config)
    normalized_args["repo_root"] = str(config.repo_root)
    normalized_args["query_overrides"] = query_overrides
    normalized_args["reason"] = str(request.get("reason") or "")
    expensive = (
        k > EXPENSIVE_NON_SURVEY_THRESHOLD
        or (k * multiplier) > EXPENSIVE_S2_TARGET_THRESHOLD
    )
    new_values = {
        "topic": topic,
        "k": k,
        "s2_bulk_citation_multiplier": multiplier,
        "citation_age_exponent": citation_age_exponent,
    }
    return {
        "allowed": True,
        "normalized_args": normalized_args,
        "rejection_reason": None,
        "warnings": warnings,
        "expensive": expensive,
        "prior_defaults": prior_defaults,
        "new_values": new_values,
    }


def pipeline_config_from_normalized_args(args: Mapping[str, Any]) -> PipelineConfig:
    return PipelineConfig(
        repo_root=Path(str(args.get("repo_root") or ".")),
        topic=str(args.get("topic") or ""),
        k=int(args.get("k") or 50),
        survey_baseline_count=int(args.get("survey_baseline_count") or 5),
        s2_bulk_citation_multiplier=int(args.get("s2_bulk_citation_multiplier") or 50),
        request_delay_seconds=float(args.get("request_delay_seconds") or 1.0),
        request_timeout_seconds=float(args.get("request_timeout_seconds") or 20.0),
        max_academic_retries=int(args.get("max_academic_retries") or 2),
        refresh_cache=bool(args.get("refresh_cache", False)),
        cross_encoder_model=str(
            args.get("cross_encoder_model") or "cross-encoder/ms-marco-MiniLM-L6-v2"
        ),
        citation_age_exponent=float(args.get("citation_age_exponent") or 1.25),
        verbose=bool(args.get("verbose", True)),
    )


def _requests_algorithm_change(request: Mapping[str, Any]) -> bool:
    forbidden_keys = {
        "scoring_algorithm",
        "scoring_formula",
        "dedupe_logic",
        "dedupe_algorithm",
        "embedding_model",
        "cross_encoder_model",
        "reranker",
        "reranker_logic",
        "candidate_schema",
        "shell_command",
        "code_change",
    }
    lowered_text = " ".join(str(value).casefold() for value in request.values())
    if any(key in request for key in forbidden_keys):
        return True
    forbidden_phrases = (
        "change scoring",
        "change dedupe",
        "different embedding",
        "change embedding",
        "change reranker",
        "edit pipeline",
        "arbitrary shell",
        "unbounded",
    )
    return any(phrase in lowered_text for phrase in forbidden_phrases)


def _rejected(reason: str, *, prior_defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        "allowed": False,
        "normalized_args": {},
        "rejection_reason": reason,
        "warnings": [],
        "expensive": False,
        "prior_defaults": prior_defaults,
        "new_values": {},
    }


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

