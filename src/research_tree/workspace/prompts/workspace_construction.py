from __future__ import annotations

import json
from typing import Any, Mapping


WORKSPACE_CONSTRUCTION_PROMPT_VERSION = "workspace_construction.v11"
PROMPT_ABSTRACT_MAX_CHARS = 240


def workspace_description_rules() -> list[str]:
    """Editorial contract shared by initial construction and agent revisions."""

    return [
        "Root overview: exactly two sentences that introduce the research topic itself. Define the topic and name its central research problem, mechanism, or distinction. Do not describe the workspace, its map, its papers, its branches, or a reading route.",
        "Branch description: exactly one sentence that defines the branch as a research area from first principles. Do not describe the papers assigned to it, the branch's role in the workspace, or what comes next.",
        "Why-it-matters fields: explain the intellectual or practical consequence of the topic or branch, not why this workspace includes it.",
    ]


def workspace_importance_rules() -> list[str]:
    """Paper-card editorial contract shared by construction and agent revisions."""

    return [
        "Paper-card importance: exactly one sentence that states the field-level implication made visible by the paper's method, benchmark, or critique, then connects that implication to an essential distinction in the topic or branch.",
        "Do not restate the abstract, enumerate the paper's contribution, mention the workspace or paper card, or repeat the title followed by a dash.",
        "Begin with a concrete technical noun phrase; never begin with `It`, `This`, `They`, `The paper`, or a vague evaluative phrase.",
        "Example — weak: `Prefix-Tuning introduces prefix-based continuous conditioning as a parameter-efficient alternative to full fine-tuning.` Strong: `Continuous prompt representations establish input-side adaptation as an alternative to changing model weights, a distinction needed to separate soft-prompt methods from instruction tuning.`",
        "Example — weak: `It turns reasoning into a control problem for multi-step inference.` Strong: `Explicit intermediate computation makes decomposition, search, and verification design choices rather than opaque by-products of model generation.`",
    ]


def build_workspace_prompt(
    candidate_artifact: Mapping[str, Any],
    *,
    prompt_version: str = WORKSPACE_CONSTRUCTION_PROMPT_VERSION,
    instructions: str | None = None,
) -> str:
    if prompt_version != WORKSPACE_CONSTRUCTION_PROMPT_VERSION:
        raise ValueError(f"unsupported workspace prompt version: {prompt_version}")
    candidate_json = json.dumps(
        _candidate_artifact_for_prompt(candidate_artifact),
        separators=(",", ":"),
    )
    return _WORKSPACE_PROMPT.format(
        candidate_artifact_json=candidate_json,
        description_rules="\n".join(f"- {rule}" for rule in workspace_description_rules()),
        importance_rules="\n".join(f"- {rule}" for rule in workspace_importance_rules()),
        reader_instructions=_reader_instructions_section(instructions),
    )


def _reader_instructions_section(instructions: str | None) -> str:
    """The reader's own steer, quoted so it cannot be read as part of the contract.

    It shapes emphasis and exclusion within the rules above; it does not license
    inventing papers or breaking the output contract.
    """

    text = " ".join((instructions or "").split()).strip()
    if not text:
        return ""
    return f"""
Reader instructions

The reader asked for this workspace with one steer. Honor it in what you emphasize, exclude, or anchor on, within every rule above. It never overrides the output contract and never licenses papers outside the candidate artifact.

<reader_instructions>
{text}
</reader_instructions>
"""


def _candidate_artifact_for_prompt(
    candidate_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": candidate_artifact.get("schema_version"),
        "topic": candidate_artifact.get("topic"),
        "workspace": candidate_artifact.get("workspace"),
        "candidate_set_purpose": candidate_artifact.get("candidate_set_purpose"),
        "llm_handoff": candidate_artifact.get("llm_handoff"),
        "candidate_pool_order": candidate_artifact.get("candidate_pool_order"),
        "non_survey_papers": [
            _paper_for_prompt(paper)
            for paper in candidate_artifact.get("non_survey_papers") or []
            if isinstance(paper, Mapping)
        ],
        "survey_papers": [
            _paper_for_prompt(paper)
            for paper in candidate_artifact.get("survey_papers") or []
            if isinstance(paper, Mapping)
        ],
    }


def _paper_for_prompt(paper: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "paper_id",
        "title",
        "abstract",
        "publication_date",
        "citation_count",
        "authority_rank",
        "in_degree",
        "is_survey",
    )
    payload = {field: paper.get(field) for field in fields}
    payload["abstract"] = _truncate_text(
        str(payload.get("abstract") or ""),
        PROMPT_ABSTRACT_MAX_CHARS,
    )
    if paper.get("flagged_off_topic"):
        payload["flagged_off_topic"] = True
    if paper.get("frontier_pick"):
        payload["frontier_pick"] = True
    return payload


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rsplit(" ", 1)[0].rstrip() + "..."


_WORKSPACE_PROMPT = """You must construct Research Tree workspaces: compact, editable maps of an academic field.

The workspace gives a reader a durable model of the field: central questions, distinct lines of work, and a reading route that makes later papers intelligible. The candidate artifact is a high-recall library, not a bibliography to reproduce. Return only JSON matching the supplied schema. Do not invent papers or metadata.

Identify the field's conceptual backbone. A visible paper earns a non-interchangeable role by introducing a mechanism, establishing a benchmark, redirecting a research question, making a consequential critique, or connecting historical steps. Build the smallest teaching set that reconstructs those moves. Ten to twenty-five visible papers is an editorial calibration, not a quota: include as many papers as a coherent account needs, then stop when another paper no longer sharpens the reader's model. Do not pad sparse fields or omit necessary distinctions to meet a count.

Use citation count, publication date, and age-adjusted citation score as historical signals for establishment, momentum, and representative work; they do not replace conceptual judgment.

A paper marked `flagged_off_topic: true` entered through citation snowballing, and the retrieval system judged that the field cites the paper as general infrastructure — an optimizer, a dataset, a backbone architecture — rather than as work on the topic itself. That judgment is heuristic and can be wrong: a flagged paper may in fact be a founding paper of the topic. Decide for yourself whether each flagged paper belongs; exclude the ones that are genuinely general infrastructure, keep the ones the topic cannot be understood without.

A paper marked `frontier_pick: true` is recent work: citation authority lags the field by a few years, so these papers were selected by citation velocity and screened for topical relevance instead. Their `authority_rank` understates their importance — weigh them as current members of the field, and use them to keep the workspace's coverage from ending years before the present.

Use surveys as orientation documents. Choose a small non-overlapping set for the root and genuinely distinct branches. The root may have one survey anchor. A branch or atomic leaf may have one different survey anchor only when it provides branch-specific framing absent from the ancestor's survey. Surveys never appear in paper paths.

Build the tree from meaningful intellectual divisions: method families, benchmark traditions, debates, evaluation regimes, or historical forks. Split only when children clarify a distinction. Atomic branches become reading paths. Each paper has one primary location; secondary tags capture cross-cutting relevance.

Paths are learning sequences, not date-sorted bibliographies. Order conceptual prerequisites before refinements, with chronology breaking ties. Three to six papers is a reading-load estimate, not a cap: use the number required by intellectual dependencies without adding filler. `why_read_here` states what the reader can now understand because of that step. Use parallel or side paths only for genuinely independent lines of development.

Write as an academic editor for a research-literate reader. Name mechanisms, debates, and intellectual forks directly. Use concrete claims and short sentences.

Descriptions and significance

{description_rules}

Paper-card importance

{importance_rules}
{reader_instructions}
Output contract

- Top level: `schema_version`, `workspace_id`, `topic`, `title`, `scope`, `source_candidate_artifact`, `root`, `tree`, `paper_paths`, `paper_cards`, `provenance`.
- `scope`: `scope_label`, `scope_rationale`, `visible_paper_budget` with `target_min`, `target_max`, `hard_max_default`.
- `root`: `node_id`, `label`, `overview`, `why_it_matters`, `root_survey_type`, `survey_anchor_paper_ids`, `representative_paper_ids`, `key_terms`, `open_questions`, `suggested_reading_direction`. `overview` follows the root overview rule; `why_it_matters` explains why the topic changes the broader field. Use at most one root survey anchor.
- `tree.root_node_id` is `root`. `tree.nodes` is a flat array.
- Every branch node uses `node_id`, `parent_id`, `label`, `description`, `why_it_matters`, `is_leaf`, `child_node_ids`, `primary_paper_ids`, `secondary_paper_ids`, `survey_anchor_paper_id`, `tags`, `open_questions`.
- Set `survey_anchor_paper_id` to a survey paper ID only when the survey is a useful branch overview; otherwise use `null`.
- Every path uses `path_id`, `branch_node_id`, `path_type`, `label`, `description`, `paper_ids`, `paper_steps`, `rationale`. `paper_ids` exactly matches `paper_steps[].paper_id` order. Every step has `paper_id`, `why_read_here`.
- `root.survey_anchor_paper_ids` contains one strong topic survey when one exists, otherwise it is empty. Any anchor has a paper card. Surveys never appear in `paper_paths`.
- Every paper card is keyed by paper ID and uses only `paper_id`, `primary_tree_location`, `secondary_tags`, `importance`; `primary_tree_location` is an object with `node_id` and `path`.
- Do not output metadata already owned by the candidate artifact: titles, authors, dates, links, abstracts, TLDRs, reading status, global reading order, comparison tables, or discarded candidates. The application adds these.

Candidate artifact

<candidate_artifact_json>
{candidate_artifact_json}
</candidate_artifact_json>
"""
