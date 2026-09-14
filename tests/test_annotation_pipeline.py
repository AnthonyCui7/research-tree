"""Unit coverage for the annotation pipeline's offline stages.

Everything here runs against a PDF built in the test, so extraction, chunking,
quote handling, and placement are checked without a network or a key.
"""

from __future__ import annotations

import fitz
import numpy as np
import pytest

from research_tree.annotation.anchoring import resolve_annotation_geometry, resolve_bounding_box
from research_tree.annotation.chunking import chunk_blocks, split_with_offsets
from research_tree.annotation.embedding import cosine_top_k
from research_tree.annotation.extraction import (
    build_page_sources,
    extract_blocks,
    infer_section_hint,
    should_skip_block,
    should_skip_chunk,
)
from research_tree.annotation.generation import (
    anchor_within_chunk,
    build_annotations,
    build_paper_brief,
    parse_annotation_json,
)
from research_tree.annotation.models import BoundingBox, PaperAnnotation
from research_tree.annotation.quotes import find_occurrences, quote_key, shorten_quote
from research_tree.annotation.retrieval import group_into_units
from research_tree.annotation.validation import dedupe_annotations, enforce_quote_rules


FIRST_PAGE_TEXT = (
    "Introduction "
    "We introduce a retrieval-augmented training objective that improves generalization "
    "across long-form translation benchmarks. Under a fixed compute budget the approach "
    "outperforms every prior baseline that we evaluated, and the resulting models remain "
    "cheaper to serve than the strongest published alternative."
)
SECOND_PAGE_TEXT = (
    "Results "
    "The retrieval-augmented training objective reduces inference latency by 43 percent "
    "compared with prior approaches while maintaining downstream accuracy on every "
    "benchmark. We attribute the remaining gap to the retrieval-augmented training "
    "objective rather than to additional parameters or longer training schedules."
)


@pytest.fixture(scope="module")
def paper_pdf() -> bytes:
    """A two-page paper whose second page repeats one phrase twice."""

    document = fitz.open()
    for text in (FIRST_PAGE_TEXT, SECOND_PAGE_TEXT):
        page = document.new_page()
        # A narrow column, so a quoted phrase wraps the way it does in a paper.
        page.insert_textbox(fitz.Rect(60, 60, 300, 700), text, fontsize=11)
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


@pytest.fixture(scope="module")
def blocks(paper_pdf: bytes) -> list[dict]:
    document = fitz.open(stream=paper_pdf, filetype="pdf")
    try:
        return extract_blocks(document)
    finally:
        document.close()


class TestExtraction:
    def test_blocks_carry_page_numbers_and_normalized_geometry(self, blocks: list[dict]) -> None:
        assert {block["page_number"] for block in blocks} == {1, 2}
        for block in blocks:
            box = block["bbox"]
            assert 0.0 <= box["x"] <= 1.0
            assert 0.0 <= box["y"] <= 1.0
            assert 0.0 < box["width"] <= 1.0
            assert 0.0 < box["height"] <= 1.0

    def test_block_offsets_slice_their_page_source(self, blocks: list[dict]) -> None:
        page_sources = build_page_sources(blocks)
        for block in blocks:
            page_text = page_sources[block["page_number"]]
            assert page_text[block["page_text_start"] : block["page_text_end"]] == block["text"]

    def test_boilerplate_and_contact_lines_are_dropped(self) -> None:
        assert should_skip_block("arXiv:2401.00001v2 [cs.CL] 4 Jan 2024")
        assert should_skip_block("Correspondence to alex@example.edu")
        assert not should_skip_block("We introduce a retrieval-augmented training objective.")

    def test_passages_without_prose_are_skipped(self) -> None:
        assert should_skip_chunk("x = 3")
        assert should_skip_chunk("Figure 2: 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8")
        assert not should_skip_chunk(FIRST_PAGE_TEXT)

    def test_headings_are_recognized(self) -> None:
        assert infer_section_hint("Introduction") == "Introduction"
        assert infer_section_hint("3 Model Architecture") == "Model Architecture"
        assert infer_section_hint("We introduce a new objective that works well.") is None


class TestChunking:
    def test_pieces_slice_back_out_of_the_source(self) -> None:
        text = " ".join(f"Sentence number {index} carries enough words to matter." for index in range(200))
        pieces = split_with_offsets(text)
        assert len(pieces) > 1
        for start, piece in pieces:
            assert text[start : start + len(piece)] == piece

    def test_pieces_cover_the_whole_source(self) -> None:
        text = " ".join(f"Sentence number {index} carries enough words to matter." for index in range(200))
        pieces = split_with_offsets(text)
        assert pieces[0][0] == 0
        assert pieces[-1][0] + len(pieces[-1][1]) == len(text)
        for (start, piece), (next_start, _) in zip(pieces, pieces[1:]):
            # Consecutive pieces overlap rather than leaving a gap.
            assert next_start <= start + len(piece)

    def test_chunks_keep_offsets_into_the_page(self, blocks: list[dict]) -> None:
        page_sources = build_page_sources(blocks)
        chunks = chunk_blocks(blocks)
        assert chunks
        for chunk in chunks:
            page_text = page_sources[chunk["page_number"]]
            assert page_text[chunk["page_text_start"] : chunk["page_text_end"]] == chunk["text"]


class TestQuotes:
    def test_every_occurrence_is_found_in_reading_order(self) -> None:
        spans = find_occurrences(SECOND_PAGE_TEXT, "retrieval-augmented training objective")
        assert len(spans) == 2
        assert spans[0][0] < spans[1][0]

    def test_an_over_long_quote_shrinks_to_something_on_the_page(self) -> None:
        shortened = shorten_quote(
            "The retrieval-augmented training objective reduces inference latency by 43 percent "
            "compared with prior approaches",
            "highlight",
            SECOND_PAGE_TEXT,
        )
        assert shortened is not None
        assert shortened in SECOND_PAGE_TEXT
        assert len(shortened.split()) < 15

    def test_a_quote_absent_from_the_page_cannot_be_shortened(self) -> None:
        assert shorten_quote("a phrase " * 20, "note", SECOND_PAGE_TEXT) is None

    def test_keys_ignore_case_and_edge_punctuation(self) -> None:
        assert quote_key("Fixed Compute Budget.") == quote_key("fixed compute budget")


class TestPlacement:
    def test_a_quote_is_located_on_its_page(self, paper_pdf: bytes) -> None:
        document = fitz.open(stream=paper_pdf, filetype="pdf")
        try:
            page_sources = build_page_sources(extract_blocks(document))
            annotation = _annotation("reduces inference latency by 43 percent", page_number=2)
            box = resolve_bounding_box(annotation, document, page_sources[2])
        finally:
            document.close()

        assert box is not None
        assert box.fragments
        assert 0.0 < box.y < 1.0
        assert 0.0 < box.width < 1.0

    def test_a_quote_wrapping_a_line_keeps_every_line(self, paper_pdf: bytes) -> None:
        document = fitz.open(stream=paper_pdf, filetype="pdf")
        try:
            page_sources = build_page_sources(extract_blocks(document))
            # This phrase wraps in the rendered page but appears once in its text.
            annotation = _annotation(
                "compared with prior approaches while maintaining downstream accuracy",
                page_number=2,
            )
            box = resolve_bounding_box(annotation, document, page_sources[2])
        finally:
            document.close()

        assert box is not None
        assert len(box.fragments) > 1
        # The covering box spans every line the quote occupies.
        assert box.height >= max(fragment.height for fragment in box.fragments)

    def test_a_repeated_quote_uses_the_occurrence_its_anchor_names(self, paper_pdf: bytes) -> None:
        document = fitz.open(stream=paper_pdf, filetype="pdf")
        try:
            page_sources = build_page_sources(extract_blocks(document))
            page_text = page_sources[2]
            quote = "retrieval-augmented training objective"
            spans = find_occurrences(page_text, quote)

            first = _annotation(quote, page_number=2)
            first.anchor = anchor_within_chunk(page_text, quote, spans[0][0], spans[0][1])
            second = _annotation(quote, page_number=2)
            second.anchor = anchor_within_chunk(page_text, quote, spans[1][0], spans[1][1])

            resolve_annotation_geometry([first, second], document, page_sources)
        finally:
            document.close()

        assert first.anchor is not None and second.anchor is not None
        assert first.anchor.occurrence_index == 0
        assert second.anchor.occurrence_index == 1
        assert first.bbox.y < second.bbox.y


class TestGeneration:
    def test_json_survives_fences_and_surrounding_prose(self) -> None:
        assert parse_annotation_json('Here you go:\n```json\n[{"type": "note"}]\n```') == [
            {"type": "note"}
        ]
        assert parse_annotation_json("no json at all") is None
        assert parse_annotation_json("") is None

    def test_malformed_items_are_dropped(self) -> None:
        chunk = {
            "page_number": 1,
            "text": FIRST_PAGE_TEXT,
            "bbox": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.2},
            "page_text_start": 0,
            "page_text_end": len(FIRST_PAGE_TEXT),
        }
        built = build_annotations(
            [
                {"type": "highlight", "text_ref": "fixed compute budget", "note": "Matters.", "importance": 3},
                {"type": "shout", "text_ref": "x", "note": "y", "importance": 1},
                {"type": "note", "text_ref": "", "note": "y", "importance": 1},
                {"type": "note", "text_ref": "x", "note": "y", "importance": 9},
                {"type": "highlight", "text_ref": "Fixed compute budget", "note": "Repeat.", "importance": 1},
                "not a dict",
            ],
            chunk,
            {1: FIRST_PAGE_TEXT},
        )
        assert [annotation.text_ref for annotation in built] == ["fixed compute budget"]

    def test_the_brief_samples_across_the_paper(self) -> None:
        chunks = [
            {"page_number": 1, "text": f"Passage {index} of the paper.", "section_hint": None}
            for index in range(9)
        ]
        brief = build_paper_brief("A Title", "An abstract.", chunks)
        assert "- Paper: A Title" in brief
        assert "Passage 0" in brief and "Passage 8" in brief


class TestValidation:
    def test_the_strongest_annotation_wins_a_shared_quote(self) -> None:
        weak = _annotation("fixed compute budget", importance=1)
        strong = _annotation("Fixed compute budget", importance=3)
        assert dedupe_annotations([weak, strong]) == [strong]

    def test_quotes_off_the_page_or_over_the_limit_are_dropped(self) -> None:
        kept = _annotation("fixed compute budget")
        absent = _annotation("a claim the page never makes")
        unshortenable = _annotation(" ".join(f"word{index}" for index in range(30)))
        survivors = enforce_quote_rules(
            [kept, absent, unshortenable], {1: FIRST_PAGE_TEXT}
        )
        assert [annotation.text_ref for annotation in survivors] == ["fixed compute budget"]


class TestRetrieval:
    def test_units_follow_sections_and_cover_every_chunk(self) -> None:
        chunks = [
            {"page_number": 1 + index // 4, "text": "prose " * 600, "section_hint": section}
            for index, section in enumerate(["Introduction"] * 4 + ["Results"] * 4)
        ]
        units = group_into_units(chunks)
        assert sorted(index for unit in units for index in unit.chunk_indices) == list(range(8))
        assert [unit.section_hint for unit in units][:1] == ["Introduction"]

    def test_ranking_returns_the_nearest_rows_and_skips_its_own(self) -> None:
        matrix = np.array([[1.0, 0.0], [0.9, 0.44], [0.0, 1.0]], dtype=np.float32)
        ranked = cosine_top_k(matrix[0], matrix, 2, skip_index=0)
        assert [index for index, _ in ranked] == [1, 2]


def _annotation(text_ref: str, *, page_number: int = 1, importance: int = 2) -> PaperAnnotation:
    return PaperAnnotation(
        type="highlight",
        text_ref=text_ref,
        note="Why this matters.",
        importance=importance,
        page_number=page_number,
        bbox=BoundingBox(x=0.1, y=0.1, width=0.5, height=0.2),
    )


class TestOccurrenceGeometry:
    def test_each_occurrence_keeps_its_own_lines_when_one_wraps(self) -> None:
        """The viewer returns one rectangle per line and nothing between hits,
        so the second occurrence's two lines used to be read as the second and
        third hits; the annotation for it got one line and the wrong one."""

        document = fitz.open()
        page = document.new_page(width=300, height=200)
        page.insert_textbox(
            fitz.Rect(20, 20, 200, 180),
            "alpha beta gamma delta epsilon zeta eta theta alpha beta gamma delta epsilon",
            fontsize=11,
        )
        try:
            page_sources = build_page_sources(extract_blocks(document))
            page_text = page_sources[1]
            quote = "gamma delta epsilon"
            spans = find_occurrences(page_text, quote)
            assert len(spans) == 2
            first = _annotation(quote, page_number=1)
            first.anchor = anchor_within_chunk(page_text, quote, spans[0][0], spans[0][1])
            second = _annotation(quote, page_number=1)
            second.anchor = anchor_within_chunk(page_text, quote, spans[1][0], spans[1][1])

            resolve_annotation_geometry([first, second], document, page_sources)
        finally:
            document.close()

        assert len(first.bbox.fragments) == 1
        assert len(second.bbox.fragments) == 2
        assert first.bbox.y < second.bbox.y


class _NoIndex:
    def related_passages(self, chunk_index: int) -> list[str]:
        return []


def _chunks(*texts: str) -> list[dict]:
    return [
        {"text": text, "page_number": 1, "section_hint": None, "start": 0, "end": len(text)}
        for text in texts
    ]


class TestRefusalsEndThePaper:
    """A refusal this codebase wrote ends the paper; an emptied page stays empty."""

    def test_an_exhausted_allowance_ends_annotation_rather_than_caching_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from research_tree.annotation import generation
        from research_tree.services.errors import AllowanceExhaustedError

        def refuse(*args: object, **kwargs: object) -> dict:
            raise AllowanceExhaustedError("used up")

        monkeypatch.setattr(generation, "call_responses_api", refuse)
        with pytest.raises(AllowanceExhaustedError):
            generation.annotate_chunks(
                _chunks("Some prose.", "More prose."),
                page_sources={1: "Some prose. More prose."},
                paper_brief="",
                index=_NoIndex(),
                api_key="sk-test",
            )

    def test_a_paper_no_passage_of_which_could_be_annotated_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from research_tree.annotation import generation

        def fail(*args: object, **kwargs: object) -> dict:
            raise RuntimeError("model unavailable")

        monkeypatch.setattr(generation, "call_responses_api", fail)
        with pytest.raises(RuntimeError, match="no passage could be annotated"):
            generation.annotate_chunks(
                _chunks("Some prose.", "More prose."),
                page_sources={1: "Some prose. More prose."},
                paper_brief="",
                index=_NoIndex(),
                api_key="sk-test",
            )

    def test_a_review_that_keeps_nothing_on_a_page_is_taken_at_its_word(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from research_tree.annotation import validation

        annotation = _annotation("Some prose")
        monkeypatch.setattr(validation, "_request_review", lambda *args, **kwargs: [])
        assert validation.review_annotations_by_page([annotation], {1: "Some prose"}, api_key="k") == []
        # A review none of whose items can be read back is unusable, not a verdict.
        monkeypatch.setattr(validation, "_request_review", lambda *args, **kwargs: [{"bogus": 1}])
        assert validation.review_annotations_by_page([annotation], {1: "Some prose"}, api_key="k") == [
            annotation
        ]

    def test_a_refusal_during_review_ends_the_paper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from research_tree.annotation import validation
        from research_tree.services.errors import AllowanceExhaustedError

        def refuse(*args: object, **kwargs: object) -> list:
            raise AllowanceExhaustedError("used up")

        monkeypatch.setattr(validation, "_request_review", refuse)
        with pytest.raises(AllowanceExhaustedError):
            validation.review_annotations_by_page([_annotation("Some prose")], {1: "Some prose"}, api_key="k")

    def test_a_paper_with_too_many_passages_is_refused_before_any_call(
        self, paper_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from research_tree.annotation import pipeline

        monkeypatch.setattr(pipeline, "MAX_ANNOTATION_PASSAGES", 1)
        with pytest.raises(pipeline.PaperTooLongError, match="passages"):
            pipeline.generate_paper_annotations(paper_pdf, title="Paper", api_key="sk-test")
