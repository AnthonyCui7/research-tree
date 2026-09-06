import { useCallback, useEffect, useRef, useState } from "react";
import { repositoryWorkspaceGateway } from "./workspaceApi";
import { isVersionConflict, messageFrom, VERSION_CONFLICT_MESSAGE } from "../lib/apiError";
import type { AgentActivity, AgentRunResult, AgentStep, WorkspaceReview } from "../lib/types";

export type ConversationItem = {
  role: "user" | "agent";
  text: string;
  /** What the assistant did before this reply, in order; only on replies. */
  steps?: AgentStep[];
};

export type ReviewOutcome = "applied" | "rejected" | "rerun_started" | null;

// One turn is a user message plus the assistant's reply.
const MAX_HISTORY_TURNS = 12;
const MAX_HISTORY_ITEMS = MAX_HISTORY_TURNS * 2;
const MAX_HISTORY_CHARACTERS = 4_000;

export type AgentSession = {
  conversation: ConversationItem[];
  result: AgentRunResult | null;
  outcome: ReviewOutcome;
  busy: boolean;
  /** What the running turn is doing right now; null when idle. */
  activity: AgentActivity | null;
  /** What the running turn has done so far. */
  steps: AgentStep[];
  error: string | null;
  model: string;
  setModel: (model: string) => void;
  /** The unsent message, kept here so closing the panel does not discard it. */
  draft: string;
  setDraft: (draft: string) => void;
  /** Set when the pending review was restored from storage after a reload. */
  restoredUserMessage: string | null;
  send: (request: string) => Promise<void>;
  decide: (choice: "approve" | "reject") => Promise<void>;
  dismissError: () => void;
};

/** Everything one workspace's conversation is made of. */
type SessionState = {
  conversation: ConversationItem[];
  result: AgentRunResult | null;
  outcome: ReviewOutcome;
  busy: boolean;
  activity: AgentActivity | null;
  steps: AgentStep[];
  error: string | null;
  model: string;
  draft: string;
  threadId: string | null;
  restoredUserMessage: string | null;
};

const NEW_SESSION: SessionState = {
  conversation: [],
  result: null,
  outcome: null,
  busy: false,
  activity: null,
  steps: [],
  error: null,
  model: "gpt-5.6-luna",
  draft: "",
  threadId: null,
  restoredUserMessage: null,
};

/**
 * The assistant conversation outlives the panel that shows it: switching to
 * history and back, or to another workspace and back, must not discard a thread
 * or a pending review. Keeping one entry per workspace is what makes both true —
 * and it is also what files a reply that lands after a switch under the
 * workspace it was asked of rather than the one now on screen.
 */
export function useAgentSession(
  workspaceId: string | null,
  onWorkspaceChanged: () => Promise<void>,
): AgentSession {
  const [sessions, setSessions] = useState<Record<string, SessionState>>({});
  // Chat state is in-memory, so a reload orphans any review still pending on
  // the server. Probed once per workspace; a ref, not state, so the effect
  // cannot loop on its own writes.
  const restoredWorkspaceIds = useRef<Set<string>>(new Set());
  const session = (workspaceId ? sessions[workspaceId] : null) ?? NEW_SESSION;

  const update = useCallback(
    (id: string, change: (state: SessionState) => SessionState) => {
      setSessions((all) => ({ ...all, [id]: change(all[id] ?? NEW_SESSION) }));
    },
    [],
  );

  useEffect(() => {
    if (!workspaceId || restoredWorkspaceIds.current.has(workspaceId)) return;
    restoredWorkspaceIds.current.add(workspaceId);
    const id = workspaceId;
    void repositoryWorkspaceGateway
      .getWorkspaceReviews(id)
      .then((reviews) => {
        const pending = newestPendingPatchReview(reviews);
        if (!pending) return;
        update(id, (state) => {
          // A live session owns the panel; restoration only fills silence.
          if (state.result || state.busy || state.conversation.length > 0) return state;
          return {
            ...state,
            result: restoredResult(id, pending),
            outcome: null,
            restoredUserMessage: pending.user_message?.trim() || null,
          };
        });
      })
      // Restoration is best-effort; an error strip about a background probe
      // would be noise.
      .catch(() => undefined);
  }, [update, workspaceId]);

  const send = useCallback(
    async (request: string) => {
      const trimmed = request.trim();
      if (!trimmed || !workspaceId) return;
      const id = workspaceId;
      const current = sessions[id] ?? NEW_SESSION;
      const history = current.conversation.slice(-MAX_HISTORY_ITEMS).map((item) => ({
        role: item.role === "agent" ? ("assistant" as const) : ("user" as const),
        text: item.text.slice(0, MAX_HISTORY_CHARACTERS),
      }));
      update(id, (state) => ({
        ...state,
        busy: true,
        activity: { kind: "thinking" },
        steps: [],
        error: null,
        outcome: null,
        // A previous failure is answered by this request; a pending review is not.
        result: state.result && agentRunFailed(state.result.status) ? null : state.result,
        conversation: [...state.conversation, { role: "user", text: trimmed }],
      }));
      try {
        const next = await repositoryWorkspaceGateway.runAgent(
          id,
          trimmed,
          current.model,
          history,
          current.threadId,
          (activity) =>
            update(id, (state) => ({
              ...state,
              activity,
              // A model turn is the gap between steps, not a step itself.
              steps: activity.kind === "thinking" ? state.steps : [...state.steps, activity],
            })),
        );
        const response =
          meaningfulResponse(next.final_response) ||
          (next.status === "pending_review"
            ? "I have prepared a structural revision for your review."
            : "Analysis complete.");
        update(id, (state) => ({
          ...state,
          result: next,
          threadId: next.thread_id ?? state.threadId,
          restoredUserMessage: null,
          // A failed run produced no answer. Reporting one would file a failure
          // as an assistant reply and leave it in the conversation history.
          conversation: agentRunFailed(next.status)
            ? state.conversation
            : [
                ...state.conversation,
                {
                  role: "agent",
                  text: response,
                  ...(state.steps.length > 0 ? { steps: state.steps } : {}),
                },
              ],
        }));
      } catch (requestError) {
        update(id, (state) => ({ ...state, error: messageFrom(requestError) }));
      } finally {
        update(id, (state) => ({ ...state, busy: false, activity: null, steps: [] }));
      }
    },
    [sessions, update, workspaceId],
  );

  const decide = useCallback(
    async (choice: "approve" | "reject") => {
      const reviewId = workspaceId ? sessions[workspaceId]?.result?.review_id : null;
      if (!workspaceId || !reviewId) return;
      const id = workspaceId;
      update(id, (state) => ({ ...state, busy: true, error: null }));
      try {
        let outcome: ReviewOutcome;
        if (choice === "approve") {
          const action = await repositoryWorkspaceGateway.approveReview(id, reviewId);
          // Approving a rerun review starts a pipeline stage instead of
          // applying a patch; the strip should say which happened.
          outcome = action.pipeline_run ? "rerun_started" : "applied";
          await onWorkspaceChanged();
        } else {
          await repositoryWorkspaceGateway.rejectReview(id, reviewId);
          outcome = "rejected";
        }
        update(id, (state) => ({
          ...state,
          outcome,
          result: null,
          restoredUserMessage: null,
        }));
      } catch (requestError) {
        if (isVersionConflict(requestError)) {
          // The review was written against a workspace version the server has
          // already moved past; reloading is what makes the next attempt valid.
          update(id, (state) => ({
            ...state,
            error: VERSION_CONFLICT_MESSAGE,
            result: null,
            restoredUserMessage: null,
          }));
          await onWorkspaceChanged();
        } else {
          update(id, (state) => ({ ...state, error: messageFrom(requestError) }));
        }
      } finally {
        update(id, (state) => ({ ...state, busy: false }));
      }
    },
    [onWorkspaceChanged, sessions, update, workspaceId],
  );

  const change = useCallback(
    (key: "model" | "draft", value: string) => {
      if (workspaceId) update(workspaceId, (state) => ({ ...state, [key]: value }));
    },
    [update, workspaceId],
  );

  return {
    conversation: session.conversation,
    result: session.result,
    outcome: session.outcome,
    busy: session.busy,
    activity: session.activity,
    steps: session.steps,
    error: session.error,
    model: session.model,
    setModel: useCallback((model: string) => change("model", model), [change]),
    draft: session.draft,
    setDraft: useCallback((draft: string) => change("draft", draft), [change]),
    restoredUserMessage: session.restoredUserMessage,
    send,
    decide,
    dismissError: useCallback(() => {
      if (workspaceId) update(workspaceId, (state) => ({ ...state, error: null }));
    }, [update, workspaceId]),
  };
}

/** Backend failures are `failed`, `failed_validation`, `failed_guardrail`, `failed_exception`. */
export function agentRunFailed(status: string): boolean {
  return status.startsWith("failed");
}

/** Treats the backend's old placeholder the same as no reply at all. */
function meaningfulResponse(finalResponse: string | null): string | null {
  const trimmed = finalResponse?.trim() ?? "";
  if (!trimmed || trimmed === "Assistant completed.") return null;
  return trimmed;
}

function newestPendingPatchReview(reviews: WorkspaceReview[]): WorkspaceReview | null {
  // A restored rerun approval would silently start a build this panel is not
  // narrating, so only workspace patches are resurrected.
  const pending = reviews.filter(
    (review) =>
      review.status === "pending" && (review.review_type ?? "workspace_patch") === "workspace_patch",
  );
  pending.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  return pending[0] ?? null;
}

/** A stored review, reshaped into the run result the review card renders. */
function restoredResult(workspaceId: string, review: WorkspaceReview): AgentRunResult {
  const interruptPayload =
    review.interrupt_payload ??
    (review.proposed_operations ? { proposed_operations: review.proposed_operations } : null);
  return {
    workspace_id: workspaceId,
    status: "pending_review",
    thread_id: null,
    agent_run_id: review.agent_run_id ?? null,
    review_id: review.review_id,
    interrupt_payload: interruptPayload,
    final_response: null,
    diff_summary: review.diff_summary ?? review.interrupt_payload?.diff_summary ?? null,
    validation_summary: review.validation_summary ?? null,
    warnings: [],
    errors: [],
  };
}
