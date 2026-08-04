import { useCallback, useEffect, useRef, useState } from "react";
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

/**
 * The assistant conversation outlives the panel that shows it: switching to
 * history and back must not discard a thread or a pending review. Keeping it
 * here — above the panel — is what makes that true, and keying it on the
 * workspace is what stops one workspace's thread leaking into another's.
 */
export function useAgentSession(
  workspaceId: string | null,
  onWorkspaceChanged: () => Promise<void>,
): AgentSession {
  const [conversation, setConversation] = useState<ConversationItem[]>([]);
  const [result, setResult] = useState<AgentRunResult | null>(null);
  const [outcome, setOutcome] = useState<ReviewOutcome>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState("gpt-5.6-luna");
  const [draft, setDraft] = useState("");
  const threadIdRef = useRef<string | null>(null);

  useEffect(() => {
    setConversation([]);
    setResult(null);
    setOutcome(null);
    setBusy(false);
    setError(null);
    setDraft("");
    threadIdRef.current = null;
  }, [workspaceId]);

  const send = useCallback(
    async (request: string) => {
      const trimmed = request.trim();
      if (!trimmed || !workspaceId) return;
      setBusy(true);
      setError(null);
      setOutcome(null);
      const history = conversation.slice(-MAX_HISTORY_ITEMS).map((item) => ({
        role: item.role === "agent" ? ("assistant" as const) : ("user" as const),
        text: item.text.slice(0, MAX_HISTORY_CHARACTERS),
      }));
      // A previous failure is answered by this request; a pending review is not.
      setResult((current) => (current && agentRunFailed(current.status) ? null : current));
      setConversation((items) => [...items, { role: "user", text: trimmed }]);
      try {
        const next = await repositoryWorkspaceGateway.runAgent(
          workspaceId,
          trimmed,
          model,
          history,
          threadIdRef.current,
        );
        setResult(next);
        threadIdRef.current = next.thread_id ?? threadIdRef.current;
        if (agentRunFailed(next.status)) {
          // A failed run produced no answer. Reporting one would file a failure
          // as an assistant reply and leave it in the conversation history.
          return;
        }
        const response =
          next.final_response ||
          (next.status === "pending_review"
            ? "I have prepared a structural revision for your review."
            : "Analysis complete.");
        setConversation((items) => [...items, { role: "agent", text: response }]);
      } catch (requestError) {
        setError(messageFrom(requestError));
      } finally {
        setBusy(false);
      }
    },
    [conversation, model, workspaceId],
  );

  const decide = useCallback(
    async (choice: "approve" | "reject") => {
      if (!workspaceId || !result?.review_id) return;
      setBusy(true);
      setError(null);
      try {
        if (choice === "approve") {
          await repositoryWorkspaceGateway.approveReview(workspaceId, result.review_id);
          await onWorkspaceChanged();
        } else {
          await repositoryWorkspaceGateway.rejectReview(workspaceId, result.review_id);
        }
        setOutcome(choice === "approve" ? "applied" : "rejected");
        setResult(null);
      } catch (requestError) {
        if (isVersionConflict(requestError)) {
          // The review was written against a workspace version the server has
          // already moved past; reloading is what makes the next attempt valid.
          setError(VERSION_CONFLICT_MESSAGE);
          setResult(null);
          await onWorkspaceChanged();
        } else {
          setError(messageFrom(requestError));
        }
      } finally {
        setBusy(false);
      }
    },
    [onWorkspaceChanged, result?.review_id, workspaceId],
  );

  return {
    conversation,
    result,
    outcome,
    busy,
    error,
    model,
    setModel,
    draft,
    setDraft,
    send,
    decide,
    dismissError: useCallback(() => setError(null), []),
  };
}

/** Backend failures are `failed`, `failed_validation`, `failed_guardrail`, `failed_exception`. */
export function agentRunFailed(status: string): boolean {
  return status.startsWith("failed");
}
