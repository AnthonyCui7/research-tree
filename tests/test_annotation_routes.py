"""Route coverage for serving a paper's PDF and its annotations.

The service takes its downloader and its annotator as arguments, so these
exercise caching, headers, and every error path without a network or a key.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from research_tree.annotation.models import BoundingBox, PaperAnnotation
from research_tree.api.dependencies import get_paper_annotation_service
from research_tree.services.annotations import PaperAnnotationService
from research_tree.workspace.repository import LocalJsonWorkspaceRepository


PAPER_ID = "10.48550/arxiv.2207.05221"
PDF_BYTES = b"%PDF-1.7 first"
OTHER_PDF_BYTES = b"%PDF-1.7 second"


class Annotator:
    """Stands in for the pipeline, counting how often it is asked to run."""

    def __init__(self) -> None:
        self.calls = 0
        self.modes: list[str] = []

    def __call__(
        self,
        pdf_bytes: bytes,
        *,
        title: str,
        abstract: str = "",
        mode: str | None = None,
    ) -> list[PaperAnnotation]:
        self.calls += 1
        self.modes.append(mode or "fast")
        return [
            PaperAnnotation(
                type="highlight",
                text_ref="fixed compute budget",
                note="Holding compute equal is what makes the comparison meaningful.",
                importance=3,
                page_number=1,
                bbox=BoundingBox(x=0.1, y=0.2, width=0.4, height=0.02),
            )
        ]


@pytest.fixture
def annotator() -> Annotator:
    return Annotator()


@pytest.fixture
def downloads() -> dict[str, bytes]:
    """The bytes the stubbed downloader returns, mutable within a test."""

    return {"pdf": PDF_BYTES}


@pytest.fixture
def annotated_client(
    client: TestClient,
    repository: LocalJsonWorkspaceRepository,
    annotator: Annotator,
    downloads: dict[str, bytes],
) -> TestClient:
    seed_paper_workspace(repository)
    client.app.dependency_overrides[get_paper_annotation_service] = lambda: PaperAnnotationService(
        repository,
        download_pdf=lambda url, **_: downloads["pdf"],
        generate_annotations=annotator,
    )
    return client


def seed_paper_workspace(
    repository: LocalJsonWorkspaceRepository,
    *,
    workspace_id: str = "sampling",
    card: dict[str, Any] | None = None,
) -> None:
    repository.save_workspace_version(
        workspace_id,
        {
            "schema_version": "research_tree_workspace.v1",
            "workspace_id": workspace_id,
            "topic": "Sampling",
            "title": "Sampling",
            "root": {"node_id": "root", "label": "Sampling", "overview": "Overview."},
            "tree": {"root_node_id": "root", "nodes": []},
            "paper_paths": [],
            "paper_cards": {
                PAPER_ID: card
                if card is not None
                else {
                    "paper_id": PAPER_ID,
                    "title": "Language Models (Mostly) Know What They Know",
                    "abstract": "We study self-evaluation.",
                    "paper_content": {"source_url": "http://arxiv.org/pdf/2207.05221"},
                }
            },
        },
        actor="system",
        parent_version_hash=None,
        reason="seeded for tests",
    )


def annotations_url(workspace_id: str = "sampling", paper_id: str = PAPER_ID) -> str:
    return f"/workspaces/{workspace_id}/paper-annotations?paper_id={paper_id}"


class TestPaperAnnotations:
    def test_annotations_come_back_and_are_cached(
        self,
        annotated_client: TestClient,
        repository: LocalJsonWorkspaceRepository,
        annotator: Annotator,
    ) -> None:
        response = annotated_client.get(annotations_url())

        assert response.status_code == 200
        payload = response.json()
        assert payload["paper_id"] == PAPER_ID
        assert [item["text_ref"] for item in payload["annotations"]] == ["fixed compute budget"]
        assert payload["annotations"][0]["bbox"]["width"] == pytest.approx(0.4)
        assert annotator.calls == 1

        stored = repository.get_paper_annotations("sampling", PAPER_ID)
        assert stored["schema_version"] == "research_tree.paper_annotations.v1"
        assert stored["pdf_sha256"]

    def test_a_different_mode_regenerates_and_is_reported(
        self, annotated_client: TestClient, annotator: Annotator
    ) -> None:
        first = annotated_client.get(annotations_url() + "&mode=fast")
        again = annotated_client.get(annotations_url() + "&mode=fast")
        dense = annotated_client.get(annotations_url() + "&mode=dense")

        assert first.json()["retrieval_mode"] == "fast"
        assert again.json()["retrieval_mode"] == "fast"
        assert dense.json()["retrieval_mode"] == "dense"
        assert annotator.calls == 2
        assert annotator.modes == ["fast", "dense"]

    def test_refresh_regenerates_despite_the_cache(
        self, annotated_client: TestClient, annotator: Annotator
    ) -> None:
        annotated_client.get(annotations_url())
        refreshed = annotated_client.get(annotations_url() + "&refresh=true")

        assert refreshed.status_code == 200
        assert annotator.calls == 2

    def test_an_unknown_mode_is_a_bad_request(self, annotated_client: TestClient) -> None:
        response = annotated_client.get(annotations_url() + "&mode=exhaustive")

        assert response.status_code == 400

    def test_a_second_request_reuses_the_cache(
        self, annotated_client: TestClient, annotator: Annotator
    ) -> None:
        first = annotated_client.get(annotations_url())
        second = annotated_client.get(annotations_url())

        assert first.status_code == second.status_code == 200
        assert first.json()["annotations"] == second.json()["annotations"]
        assert annotator.calls == 1

    def test_a_changed_pdf_is_annotated_again(
        self,
        annotated_client: TestClient,
        annotator: Annotator,
        downloads: dict[str, bytes],
    ) -> None:
        annotated_client.get(annotations_url())
        downloads["pdf"] = OTHER_PDF_BYTES
        assert annotated_client.get(annotations_url()).status_code == 200
        assert annotator.calls == 2

    def test_a_failing_annotator_reports_the_paper_as_unavailable(
        self, client: TestClient, repository: LocalJsonWorkspaceRepository
    ) -> None:
        seed_paper_workspace(repository)

        def explode(*_args: object, **_kwargs: object) -> list[PaperAnnotation]:
            raise RuntimeError("model unavailable")

        client.app.dependency_overrides[get_paper_annotation_service] = lambda: PaperAnnotationService(
            repository,
            download_pdf=lambda url, **_: PDF_BYTES,
            generate_annotations=explode,
        )

        response = client.get(annotations_url())
        assert response.status_code == 502
        assert response.json()["error_code"] == "paper_unavailable"

    def test_a_failing_download_reports_the_paper_as_unavailable(
        self, client: TestClient, repository: LocalJsonWorkspaceRepository
    ) -> None:
        seed_paper_workspace(repository)

        def refuse(url: str, **_kwargs: object) -> bytes:
            raise ValueError("Paper PDF URL must be a public HTTPS URL.")

        client.app.dependency_overrides[get_paper_annotation_service] = lambda: PaperAnnotationService(
            repository, download_pdf=refuse, generate_annotations=Annotator()
        )

        response = client.get(annotations_url())
        assert response.status_code == 502
        assert response.json()["error_code"] == "paper_unavailable"

    def test_a_paper_without_a_pdf_is_a_bad_request(
        self, client: TestClient, repository: LocalJsonWorkspaceRepository
    ) -> None:
        seed_paper_workspace(
            repository,
            card={"paper_id": PAPER_ID, "title": "No PDF anywhere", "abstract": ""},
        )
        client.app.dependency_overrides[get_paper_annotation_service] = lambda: PaperAnnotationService(
            repository,
            download_pdf=lambda url, **_: PDF_BYTES,
            generate_annotations=Annotator(),
        )

        response = client.get(annotations_url())
        assert response.status_code == 400
        assert response.json()["error_code"] == "invalid_payload"

    def test_unknown_workspaces_and_papers_are_not_found(
        self, annotated_client: TestClient
    ) -> None:
        assert annotated_client.get(annotations_url(workspace_id="missing")).status_code == 404
        assert annotated_client.get(annotations_url(paper_id="not-a-paper")).status_code == 404

    def test_a_blank_paper_id_is_rejected(self, annotated_client: TestClient) -> None:
        response = annotated_client.get(annotations_url(paper_id=""))
        assert response.status_code == 400
        assert response.json()["error_code"] == "invalid_resource_id"


class TestPaperPdf:
    def test_the_pdf_is_served_with_its_filename(self, annotated_client: TestClient) -> None:
        response = annotated_client.get(f"/workspaces/sampling/paper-pdf?paper_id={PAPER_ID}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "Language-Models-Mostly-Know-What-They-Know.pdf" in response.headers[
            "content-disposition"
        ]
        assert response.content == PDF_BYTES

    def test_an_unknown_paper_is_not_found(self, annotated_client: TestClient) -> None:
        response = annotated_client.get("/workspaces/sampling/paper-pdf?paper_id=nope")
        assert response.status_code == 404
