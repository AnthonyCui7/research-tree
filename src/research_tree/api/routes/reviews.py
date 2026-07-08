from __future__ import annotations

from fastapi import APIRouter, Depends

from research_tree.api.dependencies import get_workspace_review_service
from research_tree.api.schemas import (
    ReviewActionResponse,
    ReviewDecisionRequest,
    ReviewEditRequest,
    ReviewEditResponse,
    WorkspaceReviewResponse,
)
from research_tree.services.reviews import WorkspaceReviewService


router = APIRouter(prefix="/workspaces", tags=["reviews"])


@router.get("/{workspace_id}/reviews/{review_id}", response_model=WorkspaceReviewResponse)
def get_workspace_review(
    workspace_id: str,
    review_id: str,
    service: WorkspaceReviewService = Depends(get_workspace_review_service),
) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "review_id": review_id,
        "review": service.get_review(workspace_id, review_id),
    }


@router.post(
    "/{workspace_id}/reviews/{review_id}/approve",
    response_model=ReviewActionResponse,
)
def approve_workspace_review(
    workspace_id: str,
    review_id: str,
    request: ReviewDecisionRequest | None = None,
    service: WorkspaceReviewService = Depends(get_workspace_review_service),
) -> dict[str, object]:
    decision = request or ReviewDecisionRequest()
    return service.approve_review(
        workspace_id,
        review_id,
        reason=decision.reason,
        approval_decision=decision.approval_decision,
    )


@router.post(
    "/{workspace_id}/reviews/{review_id}/reject",
    response_model=ReviewActionResponse,
)
def reject_workspace_review(
    workspace_id: str,
    review_id: str,
    request: ReviewDecisionRequest | None = None,
    service: WorkspaceReviewService = Depends(get_workspace_review_service),
) -> dict[str, object]:
    decision = request or ReviewDecisionRequest()
    return service.reject_review(
        workspace_id,
        review_id,
        reason=decision.reason,
        approval_decision=decision.approval_decision,
    )


@router.post(
    "/{workspace_id}/reviews/{review_id}/edit",
    response_model=ReviewEditResponse,
)
def edit_workspace_review(
    workspace_id: str,
    review_id: str,
    request: ReviewEditRequest,
    service: WorkspaceReviewService = Depends(get_workspace_review_service),
) -> dict[str, object]:
    return service.edit_review(
        workspace_id,
        review_id,
        proposed_workspace=request.proposed_workspace,
        approval_decision=request.approval_decision,
    )

