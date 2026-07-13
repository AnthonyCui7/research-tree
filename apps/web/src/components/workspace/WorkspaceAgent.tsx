import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import type { AgentRunResult } from "../../lib/types";

type WorkspaceAgentProps = {
  open: boolean;
  workspaceId: string;
  sidebarCollapsed: boolean;
  onClose: () => void;
  onWorkspaceChanged: () => Promise<void>;
};

type ConversationItem = {
  role: "user" | "agent";
  text: string;
};

const models = [
  { id: "gpt-5.6-luna", label: "GPT-5.6 Luna", detail: "Fast" },
] as const;
const DESKTOP_SIDEBAR_WIDTH = 252;
const AGENT_PANEL_MIN_WIDTH = 480;
const MAX_CONVERSATION_HISTORY_TURNS = 12;
const MAX_CONVERSATION_HISTORY_CHARACTERS = 4_000;

export function WorkspaceAgent({
  open,
  workspaceId,
  sidebarCollapsed,
  onClose,
  onWorkspaceChanged,
}: WorkspaceAgentProps) {
  const [message, setMessage] = useState("");
  const [model, setModel] = useState("gpt-5.6-luna");
  const [conversation, setConversation] = useState<ConversationItem[]>([]);
  const [result, setResult] = useState<AgentRunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [panelWidth, setPanelWidth] = useState(560);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [present, setPresent] = useState(open);
  const [closing, setClosing] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpClosing, setHelpClosing] = useState(false);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const activeModel = models.find((option) => option.id === model) ?? models[0];

  useEffect(() => {
    if (open) {
      setPresent(true);
      setClosing(false);
      return;
    }
    if (!present) return;
    setClosing(true);
    const timer = window.setTimeout(() => setPresent(false), 200);
    return () => window.clearTimeout(timer);
  }, [open, present]);

  useEffect(() => {
    function closeModelMenu(event: MouseEvent) {
      if (!modelPickerRef.current?.contains(event.target as Node)) {
        setModelMenuOpen(false);
      }
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (helpOpen) {
          closeHelp();
        } else if (modelMenuOpen) {
          setModelMenuOpen(false);
        } else {
          onClose();
        }
      }
    }
    document.addEventListener("mousedown", closeModelMenu);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeModelMenu);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [helpOpen, modelMenuOpen, onClose]);

  useEffect(() => {
    if (!busy) return;
    const conversationElement = conversationRef.current;
    if (!conversationElement) return;
    conversationElement.scrollTo({ top: conversationElement.scrollHeight, behavior: "smooth" });
  }, [busy]);

  useEffect(() => {
    const composer = composerRef.current;
    if (!composer) return;
    composer.style.height = "auto";
    composer.style.height = `${Math.min(composer.scrollHeight, 168)}px`;
  }, [message]);

  if (!present) return null;

  function closeHelp() {
    setHelpClosing(true);
    window.setTimeout(() => {
      setHelpOpen(false);
      setHelpClosing(false);
    }, 180);
  }

  async function send() {
    const request = message.trim();
    if (!request || busy) return;
    setMessage("");
    setBusy(true);
    setError(null);
    const history = conversationHistoryForRequest(conversation);
    setConversation((items) => [...items, { role: "user", text: request }]);
    try {
      const next = await repositoryWorkspaceGateway.runAgent(workspaceId, request, model, history, threadId);
      setResult(next);
      setThreadId(next.thread_id ?? threadId);
      const response = next.final_response || (next.status === "pending_review"
        ? "I have prepared a structural revision for your review."
        : "Analysis complete.");
      setConversation((items) => [...items, { role: "agent", text: response }]);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function decide(choice: "approve" | "reject") {
    if (!result?.review_id) return;
    setBusy(true);
    setError(null);
    try {
      if (choice === "approve") {
        await repositoryWorkspaceGateway.approveReview(workspaceId, result.review_id);
        await onWorkspaceChanged();
      } else {
        await repositoryWorkspaceGateway.rejectReview(workspaceId, result.review_id);
      }
      setConversation((items) => [...items, {
        role: "agent",
        text: choice === "approve" ? "Change applied." : "Change discarded. Your workspace was not modified.",
      }]);
      setResult(null);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy(false);
    }
  }

  function beginResize(event: React.PointerEvent<HTMLButtonElement>) {
    if (window.innerWidth <= 980) return;
    event.preventDefault();
    const startX = event.clientX;
    const maxWidth = resizablePanelMaxWidth(sidebarCollapsed);
    const startWidth = clampResizablePanelWidth(panelWidth, AGENT_PANEL_MIN_WIDTH, maxWidth);
    document.body.style.cursor = "ew-resize";
    document.body.style.userSelect = "none";
    const onPointerMove = (moveEvent: PointerEvent) => {
      setPanelWidth(clampResizablePanelWidth(startWidth + startX - moveEvent.clientX, AGENT_PANEL_MIN_WIDTH, maxWidth));
    };
    const onPointerUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
  }

  function handleComposerKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void send();
  }

  return (
    <>
    <aside
      className="utility-panel agent-panel"
      data-state={closing ? "closing" : "open"}
      aria-label="Workspace Assistant"
      style={{
        "--agent-panel-width": `${clampResizablePanelWidth(panelWidth, AGENT_PANEL_MIN_WIDTH, resizablePanelMaxWidth(sidebarCollapsed))}px`,
        "--agent-panel-max-width": `${resizablePanelMaxWidth(sidebarCollapsed)}px`,
      } as CSSProperties}
    >
      <button className="agent-resize-handle" type="button" onPointerDown={beginResize} aria-label="Resize Assistant panel"><span className="drag-pill" aria-hidden="true" /></button>
      <header className="agent-header">
        <div className="agent-header-title">
          <h2>Assistant</h2>
          <button className="agent-help-button" type="button" onClick={() => setHelpOpen(true)} aria-label="Learn about the assistant" title="Assistant capabilities"><span>?</span></button>
        </div>
        <div className="agent-header-actions">
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close assistant" title="Close">
            <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
          </button>
        </div>
      </header>
      <div className="agent-conversation" ref={conversationRef} aria-live="polite">
        {conversation.length === 0 ? (
          <div className="agent-suggestions">
            <button type="button" onClick={() => setMessage("Explain the research landscape represented by this workspace, including how its branches relate.")}>
              <span>Explain the research landscape</span><small>Clarify the field’s main questions, methods, and relationships.</small>
            </button>
            <button type="button" onClick={() => setMessage("Identify important conceptual or bibliographic gaps in this workspace and explain their significance.")}>
              <span>Identify material gaps</span><small>Assess where the current tree may be incomplete or unbalanced.</small>
            </button>
            <button type="button" onClick={() => setMessage("Recommend a focused next reading path for understanding this workspace.")}>
              <span>Plan the next reading path</span><small>Choose a sequence suited to the structure already present.</small>
            </button>
          </div>
        ) : conversation.map((item, index) => (
          <div
            className={`agent-message agent-message-${item.role}`}
            data-pending={busy && item.role === "user" && index === conversation.length - 1}
            key={`${item.role}-${index}`}
          >
            {item.role === "user" ? <p>{item.text}</p> : (
              <div className="agent-message-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayMarkdown(item.text)}</ReactMarkdown>
              </div>
            )}
          </div>
        ))}
        {busy ? (
          <div className="agent-thinking" role="status">
            <span className="agent-thinking-mark" aria-hidden="true"><i /><i /><i /></span>
            <span>Analyzing</span>
          </div>
        ) : null}
      </div>
      {result?.status === "pending_review" && result.review_id ? (
        <div className="agent-review">
          <strong>Review proposed change</strong>
          <p>{diffLabel(result.diff_summary)}</p>
          <div>
            <button type="button" disabled={busy} onClick={() => void decide("reject")}>Reject</button>
            <button className="primary-action" type="button" disabled={busy} onClick={() => void decide("approve")}>Approve change</button>
          </div>
        </div>
      ) : null}
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      <form className="agent-composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <div className="agent-composer-wrapper">
          <textarea
            ref={composerRef}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder="Ask about this workspace…"
            rows={1}
            aria-label="Message the Assistant"
          />
          <div className="agent-composer-footer">
            <div className="agent-model-picker" ref={modelPickerRef}>
              <button
                className="agent-model-trigger"
                type="button"
                aria-haspopup="listbox"
                aria-expanded={modelMenuOpen}
                onClick={() => setModelMenuOpen((open) => !open)}
              >
                <span>{activeModel.label}</span>
                <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="m4 6 4 4 4-4" />
                </svg>
              </button>
              {modelMenuOpen ? (
                <div className="agent-model-menu" role="listbox" aria-label="Assistant model">
                  {models.map((option) => (
                    <button
                      key={option.id}
                      type="button"
                      role="option"
                      aria-selected={model === option.id}
                      onClick={() => {
                        setModel(option.id);
                        setModelMenuOpen(false);
                      }}
                    >
                      <span>{option.label}</span>
                      <small>{option.detail}</small>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            <button className="agent-send-button" type="submit" disabled={!message.trim() || busy} aria-label="Send message" title="Send message">
              <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M8 13V3m0 0 4 4M8 3 4 7" /></svg>
            </button>
          </div>
        </div>
      </form>
    </aside>
    {helpOpen ? (
      <div className="agent-help-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeHelp(); }}>
        <section className="agent-help-panel" data-state={helpClosing ? "closing" : "open"} role="dialog" aria-modal="true" aria-labelledby="agent-help-title">
          <header>
            <div><span>Research Tree assistant</span><h2 id="agent-help-title">How the assistant can help</h2></div>
            <button className="icon-button" type="button" onClick={closeHelp} aria-label="Close assistant capabilities" title="Close"><svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg></button>
          </header>
          <div>
            <p>Use the assistant to examine individual papers, compare methods and evidence, and clarify the relationships among branches, reading paths, and research questions in the current workspace.</p>
            <p>Structural requests can produce a proposed revision: adding papers, expanding coverage, merging or splitting branches, refining branch structure, or adjusting similar-paper recommendations. Every proposed revision remains subject to review before any workspace change is made.</p>
            <p>Responses draw on workspace metadata and available paper text. Original papers remain the appropriate source for claims that inform research decisions.</p>
          </div>
        </section>
      </div>
    ) : null}
    </>
  );
}

function diffLabel(diff: Record<string, unknown> | null): string {
  if (!diff) return "Structural revision ready for review.";
  const count = Number(diff.operation_count ?? diff.changed_item_count ?? 0);
  return count > 0 ? `${count} structural change${count === 1 ? "" : "s"} prepared.` : "Structural revision ready for review.";
}

function resizablePanelMaxWidth(sidebarCollapsed: boolean): number {
  return Math.max(0, window.innerWidth - (sidebarCollapsed ? 0 : DESKTOP_SIDEBAR_WIDTH));
}

function clampResizablePanelWidth(width: number, minWidth: number, maxWidth: number): number {
  const effectiveMinWidth = Math.min(minWidth, maxWidth);
  return Math.min(maxWidth, Math.max(effectiveMinWidth, width));
}

function displayMarkdown(text: string): string {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  return lines.map((line, index) => {
    const trimmed = line.trim();
    if (!trimmed) return line;
    if (isPlainAssistantHeading(lines, index)) {
      return `## ${trimmed}`;
    }
    if (isBracketedTexLine(trimmed)) {
      return texLineToReadableText(trimmed);
    }
    return line;
  }).join("\n");
}

function isPlainAssistantHeading(lines: string[], index: number): boolean {
  const line = lines[index].trim();
  if (line.length > 72 || /[.!?:;,]$/.test(line)) return false;
  if (/^(#{1,6}|\d+\.|[-*+]\s|>|```)/.test(line)) return false;
  if (!/[A-Za-z]/.test(line)) return false;
  const previous = lines[index - 1]?.trim();
  const next = lines[index + 1]?.trim();
  if (index > 0 && previous) return false;
  if (!next) return false;
  const words = line.split(/\s+/);
  return words.length <= 8;
}

function isBracketedTexLine(line: string): boolean {
  return line.startsWith("[") && line.endsWith("]") && /\\(text|rightarrow)/.test(line);
}

function texLineToReadableText(line: string): string {
  return line
    .replace(/^\[\s*/, "")
    .replace(/\s*\]$/, "")
    .replace(/\\text\{([^}]+)\}/g, "$1")
    .replace(/\\rightarrow/g, "→")
    .replace(/\\+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function conversationHistoryForRequest(
  conversation: ConversationItem[],
): Array<{ role: "user" | "assistant"; text: string }> {
  return conversation.slice(-MAX_CONVERSATION_HISTORY_TURNS).map((item) => ({
    role: item.role === "agent" ? "assistant" : "user",
    text: item.text.slice(0, MAX_CONVERSATION_HISTORY_CHARACTERS),
  }));
}

function messageFrom(error: unknown): string {
  return "We could not complete that request. Please try again.";
}
