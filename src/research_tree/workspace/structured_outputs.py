from __future__ import annotations

from typing import Any


WORKSPACE_RESPONSE_FORMAT_NAME = "research_tree_workspace"


def workspace_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": WORKSPACE_RESPONSE_FORMAT_NAME,
        "description": "A backend Research Tree workspace JSON document.",
        "strict": False,
        "schema": _workspace_json_schema(),
    }


def _workspace_json_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    visible_paper_budget = {
        "type": "object",
        "properties": {
            "target_min": {"type": "integer"},
            "target_max": {"type": "integer"},
            "hard_max_default": {"type": "integer"},
        },
        "required": ["target_min", "target_max", "hard_max_default"],
        "additionalProperties": False,
    }
    tree_location = {
        "type": "object",
        "properties": {
            "node_id": {"type": "string"},
            "path": string_array,
        },
        "required": ["node_id", "path"],
        "additionalProperties": False,
    }
    branch_node = {
        "type": "object",
        "properties": {
            "node_id": {"type": "string"},
            "parent_id": {"type": "string"},
            "label": {"type": "string"},
            "description": {"type": "string"},
            "why_it_matters": {"type": "string"},
            "is_leaf": {"type": "boolean"},
            "child_node_ids": string_array,
            "primary_paper_ids": string_array,
            "secondary_paper_ids": string_array,
            "survey_anchor_paper_id": {"type": ["string", "null"]},
            "tags": string_array,
            "open_questions": string_array,
        },
        "required": [
            "node_id",
            "parent_id",
            "label",
            "description",
            "why_it_matters",
            "is_leaf",
            "child_node_ids",
            "primary_paper_ids",
            "secondary_paper_ids",
            "survey_anchor_paper_id",
            "tags",
            "open_questions",
        ],
        "additionalProperties": False,
    }
    paper_step = {
        "type": "object",
        "properties": {
            "paper_id": {"type": "string"},
            "why_read_here": {"type": "string"},
        },
        "required": [
            "paper_id",
            "why_read_here",
        ],
        "additionalProperties": False,
    }
    paper_path = {
        "type": "object",
        "properties": {
            "path_id": {"type": "string"},
            "branch_node_id": {"type": "string"},
            "path_type": {"type": "string"},
            "label": {"type": "string"},
            "description": {"type": "string"},
            "paper_ids": string_array,
            "paper_steps": {"type": "array", "items": paper_step},
            "rationale": {"type": "string"},
        },
        "required": [
            "path_id",
            "branch_node_id",
            "path_type",
            "label",
            "description",
            "paper_ids",
            "paper_steps",
            "rationale",
        ],
        "additionalProperties": False,
    }
    paper_card = {
        "type": "object",
        "properties": {
            "paper_id": {"type": "string"},
            "title": {"type": "string"},
            "authors": string_array,
            "year": {"type": ["integer", "null"]},
            "venue": {"type": "string"},
            "primary_link": {"type": ["string", "null"]},
            "doi": {"type": ["string", "null"]},
            "arxiv_id": {"type": ["string", "null"]},
            "abstract": {"type": "string"},
            "primary_tree_location": tree_location,
            "secondary_tags": string_array,
            "reading_status": {"type": "string"},
            "paper_role": {
                "type": "string",
                "enum": [
                    "foundational",
                    "survey",
                    "method",
                    "benchmark",
                    "evaluation",
                    "critique",
                    "application",
                    "other",
                ],
            },
            "importance": {"type": "string"},
            "problem": {"type": "string"},
            "core_idea": {"type": "string"},
            "method": {"type": "string"},
            "assumptions": {"type": "string"},
            "datasets_or_benchmarks": {"type": "string"},
            "results": {"type": "string"},
            "limitations": {"type": "string"},
            "read_before": string_array,
            "read_after": string_array,
            "user_notes": {"type": "string"},
            "similar_papers": {"type": "array", "items": {"type": "object"}},
        },
        "required": [
            "paper_id",
            "title",
            "authors",
            "year",
            "venue",
            "primary_link",
            "doi",
            "arxiv_id",
            "abstract",
            "primary_tree_location",
            "secondary_tags",
            "reading_status",
            "paper_role",
            "importance",
            "problem",
            "core_idea",
            "method",
            "assumptions",
            "datasets_or_benchmarks",
            "results",
            "limitations",
            "read_before",
            "read_after",
            "user_notes",
            "similar_papers",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "workspace_id": {"type": "string"},
            "topic": {"type": "string"},
            "title": {"type": "string"},
            "scope": {
                "type": "object",
                "properties": {
                    "scope_label": {"type": "string"},
                    "scope_rationale": {"type": "string"},
                    "visible_paper_budget": visible_paper_budget,
                },
                "required": [
                    "scope_label",
                    "scope_rationale",
                    "visible_paper_budget",
                ],
                "additionalProperties": False,
            },
            "source_candidate_artifact": {"type": "object"},
            "root": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "label": {"type": "string"},
                    "overview": {"type": "string"},
                    "root_survey_type": {"type": "string"},
                    "survey_anchor_paper_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 1,
                    },
                    "representative_paper_ids": string_array,
                    "key_terms": string_array,
                    "open_questions": string_array,
                    "suggested_reading_direction": {"type": "string"},
                },
                "required": [
                    "node_id",
                    "label",
                    "overview",
                    "root_survey_type",
                    "survey_anchor_paper_ids",
                    "representative_paper_ids",
                    "key_terms",
                    "open_questions",
                    "suggested_reading_direction",
                ],
                "additionalProperties": False,
            },
            "tree": {
                "type": "object",
                "properties": {
                    "root_node_id": {"type": "string"},
                    "nodes": {"type": "array", "items": branch_node},
                },
                "required": ["root_node_id", "nodes"],
                "additionalProperties": False,
            },
            "paper_paths": {"type": "array", "items": paper_path},
            "paper_cards": {
                "type": "object",
                "additionalProperties": paper_card,
            },
            "reading_order": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "order": {"type": "integer"},
                        "paper_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["order", "paper_id", "reason"],
                    "additionalProperties": False,
                },
            },
            "comparison_tables": {"type": "array", "items": {"type": "object"}},
            "discarded_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "paper_id": {"type": "string"},
                        "title": {"type": "string"},
                        "discard_reason": {"type": "string"},
                        "possible_future_use": {"type": "string"},
                    },
                    "required": [
                        "paper_id",
                        "title",
                        "discard_reason",
                        "possible_future_use",
                    ],
                    "additionalProperties": False,
                },
            },
            "provenance": {"type": "object"},
        },
        "required": [
            "schema_version",
            "workspace_id",
            "topic",
            "title",
            "scope",
            "source_candidate_artifact",
            "root",
            "tree",
            "paper_paths",
            "paper_cards",
            "reading_order",
            "comparison_tables",
            "discarded_candidates",
            "provenance",
        ],
        "additionalProperties": False,
    }
