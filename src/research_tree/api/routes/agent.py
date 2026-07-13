from __future__ import annotations

from fastapi import APIRouter, Depends

from research_tree.api.dependencies import get_workspace_agent_service
from research_tree.api.schemas import AgentRunRequest, AgentRunResponse
from research_tree.services.agent import WorkspaceAgentService


router = APIRouter(prefix="/workspaces", tags=["agent"])


@router.post("/{workspace_id}/agent", response_model=AgentRunResponse)
def run_workspace_agent(
    workspace_id: str,
    request: AgentRunRequest,
    service: WorkspaceAgentService = Depends(get_workspace_agent_service),
) -> dict[str, object]:
    return service.run_agent(
        workspace_id,
        message=request.message,
        conversation_history=request.conversation_history,
        thread_id=request.thread_id,
        allow_pipeline_rerun=request.allow_pipeline_rerun,
        require_approval=request.require_approval,
        model=request.model,
    )
