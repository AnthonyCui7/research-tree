import type {
  AgentRunResult,
  PipelineRun,
  TopicReview,
  WorkspaceDocument,
  WorkspaceSummary,
  WorkspaceVersion,
} from "../lib/types";

type WorkspacesResponse = {
  workspaces: WorkspaceSummary[];
};

type WorkspaceResponse = {
  workspace_id: string;
  workspace_version_hash: string;
  workspace: WorkspaceDocument;
};

type WorkspaceVersionsResponse = {
  workspace_id: string;
  versions: WorkspaceVersion[];
};

const API_BASE_URL = (import.meta.env.VITE_RESEARCH_TREE_API_BASE_URL ?? "/api").replace(
  /\/$/,
  "",
);

export const workspaceEventsUrl = `${API_BASE_URL}/workspaces/events/stream`;

export function pipelineRunEventsUrl(runId: string): string {
  return `${API_BASE_URL}/workspaces/pipeline-runs/${encodeURIComponent(runId)}/events`;
}

export type WorkspaceGateway = {
  listWorkspaceSummaries: () => Promise<WorkspaceSummary[]>;
  getWorkspace: (workspaceId: string) => Promise<WorkspaceResponse>;
  getWorkspaceVersions: (workspaceId: string) => Promise<WorkspaceVersion[]>;
  reviewTopic: (topic: string) => Promise<TopicReview>;
  createWorkspace: (topic: string, topicReviewToken: string) => Promise<PipelineRun>;
  getPipelineRun: (runId: string) => Promise<PipelineRun>;
  cancelPipelineRun: (runId: string) => Promise<PipelineRun>;
  restoreWorkspace: (workspaceId: string, versionHash: string, expectedHash: string) => Promise<void>;
  deleteWorkspace: (workspaceId: string, expectedHash: string) => Promise<void>;
  runAgent: (workspaceId: string, message: string, model: string) => Promise<AgentRunResult>;
  approveReview: (workspaceId: string, reviewId: string) => Promise<void>;
  rejectReview: (workspaceId: string, reviewId: string) => Promise<void>;
};

export const repositoryWorkspaceGateway: WorkspaceGateway = {
  async listWorkspaceSummaries() {
    const payload = await getJson<WorkspacesResponse>("/workspaces");
    return Array.isArray(payload.workspaces) ? payload.workspaces : [];
  },

  async getWorkspace(workspaceId) {
    const payload = await getJson<WorkspaceResponse>(
      `/workspaces/${encodeURIComponent(workspaceId)}`,
    );
    return payload;
  },

  async getWorkspaceVersions(workspaceId) {
    const payload = await getJson<WorkspaceVersionsResponse>(
      `/workspaces/${encodeURIComponent(workspaceId)}/versions`,
    );
    return Array.isArray(payload.versions) ? payload.versions : [];
  },

  async reviewTopic(topic) {
    return requestJson<TopicReview>("/workspaces/topic-review", {
      method: "POST",
      body: JSON.stringify({ topic }),
      signal: AbortSignal.timeout(12_000),
    });
  },

  async createWorkspace(topic, topicReviewToken) {
    const payload = await postJson<{ pipeline_run: PipelineRun }>("/workspaces", {
      topic,
      topic_review_token: topicReviewToken,
      model: "gpt-5.6-luna",
    });
    return payload.pipeline_run;
  },

  async getPipelineRun(runId) {
    const payload = await getJson<{ pipeline_run: PipelineRun }>(
      `/workspaces/pipeline-runs/${encodeURIComponent(runId)}`,
    );
    return payload.pipeline_run;
  },

  async cancelPipelineRun(runId) {
    const payload = await postJson<{ pipeline_run: PipelineRun }>(
      `/workspaces/pipeline-runs/${encodeURIComponent(runId)}/cancel`,
      {},
    );
    return payload.pipeline_run;
  },

  async restoreWorkspace(workspaceId, versionHash, expectedHash) {
    await postJson(
      `/workspaces/${encodeURIComponent(workspaceId)}/versions/${encodeURIComponent(versionHash)}/restore`,
      { expected_version_hash: expectedHash },
    );
  },

  async deleteWorkspace(workspaceId, expectedHash) {
    await requestJson(`/workspaces/${encodeURIComponent(workspaceId)}`, {
      method: "DELETE",
      body: JSON.stringify({ expected_version_hash: expectedHash }),
    });
  },

  async runAgent(workspaceId, message, model) {
    return postJson<AgentRunResult>(`/workspaces/${encodeURIComponent(workspaceId)}/agent`, {
      message,
      model,
      require_approval: true,
      allow_pipeline_rerun: true,
    });
  },

  async approveReview(workspaceId, reviewId) {
    await postJson(`/workspaces/${encodeURIComponent(workspaceId)}/reviews/${encodeURIComponent(reviewId)}/approve`, {});
  },

  async rejectReview(workspaceId, reviewId) {
    await postJson(`/workspaces/${encodeURIComponent(workspaceId)}/reviews/${encodeURIComponent(reviewId)}/reject`, {});
  },
};

async function getJson<T>(path: string): Promise<T> {
  return requestJson<T>(path, { method: "GET" });
}

async function postJson<T = unknown>(path: string, body: unknown): Promise<T> {
  return requestJson<T>(path, { method: "POST", body: JSON.stringify(body) });
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    throw new Error("We could not complete that request. Please try again.");
  }
  return (await response.json()) as T;
}
