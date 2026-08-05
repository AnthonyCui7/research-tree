import { ApiError } from "../lib/apiError";
import type {
  AgentRunResult,
  AnnotationRetrievalMode,
  ApiKeyStatus,
  BugReportResult,
  PaperAnnotation,
  PipelineRun,
  ReviewActionResponse,
  TopicReview,
  WorkspaceDocument,
  WorkspaceReview,
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

/**
 * The paper's PDF, served by this API rather than by the publisher: a viewer
 * running in the page cannot read bytes from another origin.
 */
export function paperPdfUrl(workspaceId: string, paperId: string): string {
  return (
    `${API_BASE_URL}/workspaces/${encodeURIComponent(workspaceId)}/paper-pdf` +
    `?paper_id=${encodeURIComponent(paperId)}`
  );
}

export type WorkspaceGateway = {
  listWorkspaceSummaries: () => Promise<WorkspaceSummary[]>;
  getWorkspace: (workspaceId: string) => Promise<WorkspaceResponse>;
  getWorkspaceVersions: (workspaceId: string) => Promise<WorkspaceVersion[]>;
  reviewTopic: (topic: string) => Promise<TopicReview>;
  createWorkspace: (
    topic: string,
    topicReviewToken: string,
    instructions: string,
  ) => Promise<PipelineRun>;
  cancelPipelineRun: (runId: string) => Promise<PipelineRun>;
  restoreWorkspace: (workspaceId: string, versionHash: string, expectedHash: string) => Promise<void>;
  deleteWorkspace: (workspaceId: string, expectedHash: string) => Promise<void>;
  runAgent: (
    workspaceId: string,
    message: string,
    model: string,
    conversationHistory?: Array<{ role: "user" | "assistant"; text: string }>,
    threadId?: string | null,
  ) => Promise<AgentRunResult>;
  getWorkspaceReviews: (workspaceId: string) => Promise<WorkspaceReview[]>;
  approveReview: (workspaceId: string, reviewId: string) => Promise<ReviewActionResponse>;
  rejectReview: (workspaceId: string, reviewId: string) => Promise<ReviewActionResponse>;
  getApiKeys: () => Promise<{ openai: ApiKeyStatus }>;
  reportBug: (summary: string, details: string, area: string) => Promise<BugReportResult>;
  getPaperAnnotations: (
    workspaceId: string,
    paperId: string,
    options?: PaperAnnotationOptions,
  ) => Promise<PaperAnnotationsResult>;
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

  async createWorkspace(topic, topicReviewToken, instructions) {
    const payload = await postJson<{ pipeline_run: PipelineRun }>("/workspaces", {
      topic,
      topic_review_token: topicReviewToken,
      instructions,
    });
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
    await requestJson(
      `/workspaces/${encodeURIComponent(workspaceId)}` +
        `?expected_version_hash=${encodeURIComponent(expectedHash)}`,
      { method: "DELETE" },
    );
  },

  async runAgent(workspaceId, message, model, conversationHistory = [], threadId = null) {
    return requestJson<AgentRunResult>(`/workspaces/${encodeURIComponent(workspaceId)}/agent`, {
      method: "POST",
      body: JSON.stringify({
        message,
        model,
        conversation_history: conversationHistory,
        thread_id: threadId,
        allow_pipeline_rerun: true,
      }),
      // An agent run is a tool loop over the whole workspace, and a single
      // reasoning turn in it can take half a minute on its own. The ordinary
      // read timeout would abandon answers the server goes on to finish.
      signal: AbortSignal.timeout(AGENT_REQUEST_TIMEOUT_MS),
    });
  },

  async getWorkspaceReviews(workspaceId) {
    // The server lists records by filename (a uuid), not chronologically, and
    // each record carries the full proposed workspace. Callers sort by
    // created_at and read only what they render.
    const payload = await getJson<{ reviews: WorkspaceReview[] }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/reviews`,
    );
    return Array.isArray(payload.reviews) ? payload.reviews : [];
  },

  async approveReview(workspaceId, reviewId) {
    return postJson<ReviewActionResponse>(
      `/workspaces/${encodeURIComponent(workspaceId)}/reviews/${encodeURIComponent(reviewId)}/approve`,
      {},
    );
  },

  async rejectReview(workspaceId, reviewId) {
    return postJson<ReviewActionResponse>(
      `/workspaces/${encodeURIComponent(workspaceId)}/reviews/${encodeURIComponent(reviewId)}/reject`,
      {},
    );
  },

  async getApiKeys() {
    return getJson<{ openai: ApiKeyStatus }>("/account/api-keys");
  },

  async reportBug(summary, details, area) {
    return postJson<BugReportResult>("/account/bug-reports", { summary, details, area });
  },

  async getPaperAnnotations(workspaceId, paperId, options) {
    const query = new URLSearchParams({ paper_id: paperId });
    if (options?.mode) query.set("mode", options.mode);
    if (options?.refresh) query.set("refresh", "true");
    const payload = await requestJson<PaperAnnotationsResult>(
      `/workspaces/${encodeURIComponent(workspaceId)}/paper-annotations?${query.toString()}`,
      {
        method: "GET",
        // Annotating a paper the server has not seen before is a model call per
        // passage. The ordinary read timeout would abandon work it completes.
        signal: AbortSignal.timeout(ANNOTATION_REQUEST_TIMEOUT_MS),
      },
    );
    return {
      annotations: Array.isArray(payload.annotations) ? payload.annotations : [],
      retrieval_mode: payload.retrieval_mode ?? null,
      model: payload.model ?? null,
      generated_at: payload.generated_at ?? null,
    };
  },
};

export type PaperAnnotationOptions = {
  mode?: AnnotationRetrievalMode;
  /** Regenerate from scratch, ignoring the cached result. */
  refresh?: boolean;
};

export type PaperAnnotationsResult = {
  annotations: PaperAnnotation[];
  retrieval_mode: string | null;
  model: string | null;
  generated_at: string | null;
};

async function getJson<T>(path: string): Promise<T> {
  return requestJson<T>(path, { method: "GET" });
}

async function postJson<T = unknown>(path: string, body: unknown): Promise<T> {
  return requestJson<T>(path, { method: "POST", body: JSON.stringify(body) });
}

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const AGENT_REQUEST_TIMEOUT_MS = 300_000;
const ANNOTATION_REQUEST_TIMEOUT_MS = 900_000;

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      signal: AbortSignal.timeout(DEFAULT_REQUEST_TIMEOUT_MS),
      ...init,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
    });
  } catch (error) {
    throw ApiError.fromNetworkFailure(error);
  }
  if (!response.ok) {
    throw await ApiError.fromResponse(response);
  }
  return (await response.json()) as T;
}
