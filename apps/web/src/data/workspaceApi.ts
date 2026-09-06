import { ApiError } from "../lib/apiError";
import type {
  AgentActivity,
  AgentRunResult,
  AnnotationRetrievalMode,
  ApiKeysResult,
  BugReportResult,
  RemoveApiKeyResult,
  SaveApiKeyResult,
  UsageSummary,
  PaperAnnotation,
  PipelineRun,
  ReviewActionResponse,
  TopicReview,
  WorkspaceDocument,
  WorkspaceEditOperation,
  WorkspaceEditResult,
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

export const API_BASE_URL = (import.meta.env.VITE_RESEARCH_TREE_API_BASE_URL ?? "/api").replace(
  /\/$/,
  "",
);

/**
 * Told once whenever any request comes back 401. The session store listens and
 * drops back to the sign-in screen, so no caller has to handle an expired
 * session itself.
 */
let unauthenticatedListener: (() => void) | null = null;

export function onUnauthenticated(listener: (() => void) | null): void {
  unauthenticatedListener = listener;
}

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
  editWorkspace: (
    workspaceId: string,
    operations: WorkspaceEditOperation[],
    expectedHash: string,
  ) => Promise<WorkspaceEditResult>;
  deleteWorkspace: (workspaceId: string, expectedHash: string) => Promise<void>;
  runAgent: (
    workspaceId: string,
    message: string,
    model: string,
    conversationHistory?: Array<{ role: "user" | "assistant"; text: string }>,
    threadId?: string | null,
    onActivity?: (activity: AgentActivity) => void,
  ) => Promise<AgentRunResult>;
  getWorkspaceReviews: (workspaceId: string) => Promise<WorkspaceReview[]>;
  approveReview: (workspaceId: string, reviewId: string) => Promise<ReviewActionResponse>;
  rejectReview: (workspaceId: string, reviewId: string) => Promise<ReviewActionResponse>;
  getApiKeys: () => Promise<ApiKeysResult>;
  saveApiKey: (apiKey: string) => Promise<SaveApiKeyResult>;
  removeApiKey: () => Promise<RemoveApiKeyResult>;
  getUsage: () => Promise<UsageSummary>;
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

  async editWorkspace(workspaceId, operations, expectedHash) {
    return postJson<WorkspaceEditResult>(
      `/workspaces/${encodeURIComponent(workspaceId)}/edits`,
      { expected_version_hash: expectedHash, operations },
    );
  },

  async deleteWorkspace(workspaceId, expectedHash) {
    await requestJson(
      `/workspaces/${encodeURIComponent(workspaceId)}` +
        `?expected_version_hash=${encodeURIComponent(expectedHash)}`,
      { method: "DELETE" },
    );
  },

  async runAgent(
    workspaceId,
    message,
    model,
    conversationHistory = [],
    threadId = null,
    onActivity,
  ) {
    // An agent run is a tool loop over the whole workspace, and a single
    // reasoning turn in it can take half a minute on its own. The server
    // narrates the turn as `progress` events, streams keepalive comments in
    // the silences, and sends the answer as one `result` event, so neither
    // the proxy nor this client gives up early.
    const response = await requestRaw(`/workspaces/${encodeURIComponent(workspaceId)}/agent`, {
      method: "POST",
      headers: { Accept: "text/event-stream" },
      body: JSON.stringify({
        message,
        model,
        conversation_history: conversationHistory,
        thread_id: threadId,
        allow_pipeline_rerun: true,
      }),
      signal: AbortSignal.timeout(AGENT_REQUEST_TIMEOUT_MS),
    });
    return readEventResult<AgentRunResult>(response, (payload) => {
      if (onActivity && isAgentActivity(payload)) onActivity(payload);
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
    return getJson<ApiKeysResult>("/account/api-keys");
  },

  async saveApiKey(apiKey) {
    return requestJson<SaveApiKeyResult>("/account/api-keys", {
      method: "PUT",
      body: JSON.stringify({ provider: "openai", api_key: apiKey }),
    });
  },

  async removeApiKey() {
    return requestJson<RemoveApiKeyResult>("/account/api-keys", { method: "DELETE" });
  },

  async getUsage() {
    return getJson<UsageSummary>("/account/usage");
  },

  async reportBug(summary, details, area) {
    return postJson<BugReportResult>("/account/bug-reports", { summary, details, area });
  },

  async getPaperAnnotations(workspaceId, paperId, options) {
    const query = new URLSearchParams({ paper_id: paperId });
    if (options?.mode) query.set("mode", options.mode);
    if (options?.refresh) query.set("refresh", "true");
    const path =
      `/workspaces/${encodeURIComponent(workspaceId)}/paper-annotations?${query.toString()}`;
    // Annotating a paper the server has not seen before is a model call per
    // passage. A server with a worker answers 202 with a job to poll; one
    // without does the work inside this request, so the timeout stays long.
    const deadline = Date.now() + ANNOTATION_REQUEST_TIMEOUT_MS;
    let response = await requestRaw(path, {
      method: "GET",
      signal: AbortSignal.timeout(ANNOTATION_REQUEST_TIMEOUT_MS),
    });
    if (response.status === 202) {
      const job = (await response.json()) as AnnotationJob;
      await waitForAnnotationJob(workspaceId, job.job_id, deadline);
      // The job left its result in the cache; a plain read now serves it.
      query.delete("refresh");
      response = await requestRaw(
        `/workspaces/${encodeURIComponent(workspaceId)}/paper-annotations?${query.toString()}`,
        { method: "GET" },
      );
      if (response.status === 202) {
        throw new ApiError("Annotations are still being prepared. Try again shortly.", 202);
      }
    }
    const payload = (await response.json()) as PaperAnnotationsResult;
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

type AnnotationJob = {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  error_code?: string | null;
  error_status?: number | null;
  detail?: string | null;
};

const ANNOTATION_JOB_POLL_MS = 2_000;

async function waitForAnnotationJob(
  workspaceId: string,
  jobId: string,
  deadline: number,
): Promise<void> {
  const path =
    `/workspaces/${encodeURIComponent(workspaceId)}/paper-annotations/jobs/` +
    encodeURIComponent(jobId);
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, ANNOTATION_JOB_POLL_MS));
    const job = await requestJson<AnnotationJob>(path, { method: "GET" });
    if (job.status === "completed") return;
    if (job.status === "failed") {
      throw new ApiError(
        job.detail || "We could not annotate that paper. Please try again.",
        job.error_status ?? 502,
        job.detail ?? "",
        job.error_code ?? "",
      );
    }
  }
  throw new ApiError("Annotating this paper is taking too long. Try again later.", 0);
}

function isAgentActivity(payload: unknown): payload is AgentActivity {
  if (!payload || typeof payload !== "object") return false;
  const { kind } = payload as { kind?: unknown };
  if (kind === "thinking") return true;
  if (kind === "tool") return typeof (payload as { name?: unknown }).name === "string";
  if (kind === "stage") return typeof (payload as { stage?: unknown }).stage === "string";
  return false;
}

/**
 * Reads a one-shot event stream: keepalive comments and `progress` events
 * (handed to `onProgress`), then either a `result` event carrying the JSON
 * answer or an `error` event carrying an API error.
 */
async function readEventResult<T>(
  response: Response,
  onProgress?: (payload: unknown) => void,
): Promise<T> {
  if (!response.headers.get("content-type")?.includes("text/event-stream")) {
    return (await response.json()) as T;
  }
  if (!response.body) throw new ApiError("The server sent an empty response.", 0);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
      const parsed = parseEventFrame(frame);
      if (!parsed) continue;
      if (parsed.event === "progress") {
        onProgress?.(JSON.parse(parsed.data));
        continue;
      }
      if (parsed.event === "result") return JSON.parse(parsed.data) as T;
      if (parsed.event === "error") {
        const payload = JSON.parse(parsed.data) as {
          status?: number;
          detail?: string;
          error_code?: string;
        };
        const status = payload.status ?? 500;
        if (status === 401) unauthenticatedListener?.();
        throw new ApiError(
          payload.detail || "We could not complete that request. Please try again.",
          status,
          payload.detail ?? "",
          payload.error_code ?? "",
        );
      }
    }
  }
  throw new ApiError("The connection closed before the assistant answered.", 0);
}

function parseEventFrame(frame: string): { event: string; data: string } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":") || !line.trim()) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return data.length ? { event, data: data.join("\n") } : null;
}

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
// Keepalives make a long turn safe from the proxy; this is the client's own
// patience for one answer.
const AGENT_REQUEST_TIMEOUT_MS = 600_000;
const ANNOTATION_REQUEST_TIMEOUT_MS = 900_000;

export async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await requestRaw(path, init);
  return (await response.json()) as T;
}

/** A request whose body the caller reads itself (or ignores, for a 204). */
export async function requestRaw(path: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      signal: AbortSignal.timeout(DEFAULT_REQUEST_TIMEOUT_MS),
      credentials: "same-origin",
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body instanceof URLSearchParams
          ? { "Content-Type": "application/x-www-form-urlencoded" }
          : { "Content-Type": "application/json" }),
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    throw ApiError.fromNetworkFailure(error);
  }
  if (!response.ok) {
    if (response.status === 401) unauthenticatedListener?.();
    throw await ApiError.fromResponse(response);
  }
  return response;
}
