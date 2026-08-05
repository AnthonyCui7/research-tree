import { useCallback, useState } from "react";
import { repositoryWorkspaceGateway } from "./workspaceApi";
import { isVersionConflict, messageFrom, VERSION_CONFLICT_MESSAGE } from "../lib/apiError";
import type { AgentRunResult } from "../lib/types";

export type ConversationItem = {
  role: "user" | "agent";
  text: string;
};

export type ReviewOutcome = "applied" | "rejected" | null;

// One turn is a user message plus the assistant's reply.
const MAX_HISTORY_TURNS = 12;
const MAX_HISTORY_ITEMS = MAX_HISTORY_TURNS * 2;
const MAX_HISTORY_CHARACTERS = 4_000;

export type AgentSession = {
  conversation: ConversationItem[];
  result: AgentRunResult | null;
  outcome: ReviewOutcome;
  busy: boolean;
  error: string | null;
  model: string;
  setModel: (model: string) => void;
  /** The unsent message, kept here so closing the panel does not discard it. */
  draft: string;
  setDraft: (draft: string) => void;
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
  error: string | null;
  model: string;
  draft: string;
  threadId: string | null;
};

const NEW_SESSION: SessionState = {
  conversation: [],
  result: null,
  outcome: null,
  busy: false,
  error: null,
  model: "gpt-5.6-luna",
  draft: "",
  threadId: null,
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
  const session = (workspaceId ? sessions[workspaceId] : null) ?? NEW_SESSION;

  const update = useCallback(
    (id: string, change: (state: SessionState) => SessionState) => {
      setSessions((all) => ({ ...all, [id]: change(all[id] ?? NEW_SESSION) }));
    },
    [],
  );

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
        );
        const response =
          next.final_response ||
          (next.status === "pending_review"
            ? "I have prepared a structural revision for your review."
            : "Analysis complete.");
        update(id, (state) => ({
          ...state,
          result: next,
          threadId: next.thread_id ?? state.threadId,
          // A failed run produced no answer. Reporting one would file a failure
          // as an assistant reply and leave it in the conversation history.
          conversation: agentRunFailed(next.status)
            ? state.conversation
            : [...state.conversation, { role: "agent", text: response }],
        }));
      } catch (requestError) {
        update(id, (state) => ({ ...state, error: messageFrom(requestError) }));
      } finally {
        update(id, (state) => ({ ...state, busy: false }));
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
        if (choice === "approve") {
          await repositoryWorkspaceGateway.approveReview(id, reviewId);
          await onWorkspaceChanged();
        } else {
          await repositoryWorkspaceGateway.rejectReview(id, reviewId);
        }
        update(id, (state) => ({
          ...state,
          outcome: choice === "approve" ? "applied" : "rejected",
          result: null,
        }));
      } catch (requestError) {
        if (isVersionConflict(requestError)) {
          // The review was written against a workspace version the server has
          // already moved past; reloading is what makes the next attempt valid.
          update(id, (state) => ({ ...state, error: VERSION_CONFLICT_MESSAGE, result: null }));
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
    error: session.error,
    model: session.model,
    setModel: useCallback((model: string) => change("model", model), [change]),
    draft: session.draft,
    setDraft: useCallback((draft: string) => change("draft", draft), [change]),
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
