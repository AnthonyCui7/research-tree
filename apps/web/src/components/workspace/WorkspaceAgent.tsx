import { messageFrom } from "../../lib/apiError";
import { iconButtonClass, primaryActionClass, secondaryActionClass } from "../../lib/controlClasses";
import { useEffect, useRef, useState, type ComponentProps, type CSSProperties, type JSX, type KeyboardEvent as ReactKeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { cx } from "../../lib/cx";
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
  { id: "gpt-5.6-luna", label: "GPT-5.6 Luna", detail: "Recommended" },
  { id: "gpt-5.6-terra", label: "GPT-5.6 Terra", detail: "Smarter" },
  { id: "gpt-5.6-sol", label: "GPT-5.6 Sol", detail: "Smartest" },
] as const;
const DESKTOP_SIDEBAR_WIDTH = 252;
const AGENT_PANEL_MIN_WIDTH = 480;
// One turn is a user message plus the assistant's reply.
const MAX_CONVERSATION_HISTORY_TURNS = 12;
const MAX_CONVERSATION_HISTORY_ITEMS = MAX_CONVERSATION_HISTORY_TURNS * 2;
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
      className="fixed top-16 right-0 bottom-0 z-panel flex h-auto w-[min(var(--agent-panel-width,560px),var(--agent-panel-max-width,calc(100%_-_80px)))] animate-interface-right-enter flex-col border-l border-border bg-agent-canvas shadow-panel-left data-[state=closing]:pointer-events-none data-[state=closing]:animate-interface-right-exit max-[980px]:top-auto max-[980px]:left-[68px] max-[980px]:h-[min(72vh,680px)] max-[980px]:w-auto max-[980px]:border-l-0 max-[980px]:border-t max-[720px]:left-0 max-[720px]:h-[min(80vh,720px)]"
      data-state={closing ? "closing" : "open"}
      aria-label="Workspace Assistant"
      style={{
        "--agent-panel-width": `${clampResizablePanelWidth(panelWidth, AGENT_PANEL_MIN_WIDTH, resizablePanelMaxWidth(sidebarCollapsed))}px`,
        "--agent-panel-max-width": `${resizablePanelMaxWidth(sidebarCollapsed)}px`,
      } as CSSProperties}
    >
      <button className="group absolute top-1/2 -left-1 z-[1] grid h-12 w-2 -translate-y-1/2 cursor-ew-resize touch-none place-items-center rounded-full border border-border bg-surface p-0 shadow-control transition-[background-color,border-color,box-shadow,transform] duration-150 hover:border-accent hover:shadow-[0_8px_18px_rgb(31_35_40_/_14%)] focus-visible:border-accent focus-visible:shadow-[0_8px_18px_rgb(31_35_40_/_14%)] max-[980px]:hidden" type="button" onPointerDown={beginResize} aria-label="Resize Assistant panel"><span className="relative block h-[42px] w-1.5 rounded-full bg-[color-mix(in_srgb,var(--color-surface-subtle)_60%,var(--color-surface))] transition-[background-color,transform] duration-150 before:absolute before:top-2.5 before:bottom-2.5 before:left-1/2 before:block before:w-px before:-translate-x-1/2 before:bg-text-secondary before:opacity-80 before:content-[''] group-hover:bg-accent-subtle group-focus-visible:bg-accent-subtle" aria-hidden="true" /></button>
      <header className="flex h-10 min-h-10 items-center justify-between border-b border-border bg-surface px-3 py-1">
        <div className="flex items-center gap-[7px]">
          <h2 className="m-0 text-base font-semibold tracking-normal text-text-primary">Assistant</h2>
          <button className="grid h-[18px] w-[18px] place-items-center rounded-full border border-border-strong bg-[#f8f9f9] p-0 text-xs font-bold leading-none text-text-secondary transition-[background-color,border-color,color] duration-150 hover:border-accent hover:bg-accent-subtle hover:text-accent-deep" type="button" onClick={() => setHelpOpen(true)} aria-label="Learn about the assistant" title="Assistant capabilities"><span className="block translate-y-[-0.25px] font-[Arial,sans-serif] not-italic leading-none">?</span></button>
        </div>
        <div className="flex items-center gap-[3px]">
          <button className={iconButtonClass} type="button" onClick={onClose} aria-label="Close assistant" title="Close">
            <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
          </button>
        </div>
      </header>
      <div className="scrollbar-rt grid min-h-0 flex-1 content-start gap-[30px] overflow-y-auto px-[clamp(24px,5%,34px)] py-[34px] max-[720px]:px-[18px] max-[720px]:py-[22px]" ref={conversationRef} aria-live="polite">
        {conversation.length === 0 ? (
          <div className="grid gap-[9px] border-0">
            <button className={suggestionClass} type="button" onClick={() => setMessage("Explain the research landscape represented by this workspace, including how its branches relate.")}>
              <span className="text-[13px] font-semibold">Explain the research landscape</span><small className="text-xs leading-[1.45] text-text-secondary">Clarify the field’s main questions, methods, and relationships.</small>
            </button>
            <button className={suggestionClass} type="button" onClick={() => setMessage("Identify important conceptual or bibliographic gaps in this workspace and explain their significance.")}>
              <span className="text-[13px] font-semibold">Identify material gaps</span><small className="text-xs leading-[1.45] text-text-secondary">Assess where the current tree may be incomplete or unbalanced.</small>
            </button>
            <button className={suggestionClass} type="button" onClick={() => setMessage("Recommend a focused next reading path for understanding this workspace.")}>
              <span className="text-[13px] font-semibold">Plan the next reading path</span><small className="text-xs leading-[1.45] text-text-secondary">Choose a sequence suited to the structure already present.</small>
            </button>
          </div>
        ) : conversation.map((item, index) => (
          <div
            className={cx("grid w-[min(100%,78ch)] justify-self-center", item.role === "user" && "w-[min(100%,78ch)]")}
            data-pending={busy && item.role === "user" && index === conversation.length - 1}
            key={`${item.role}-${index}`}
          >
            {item.role === "user" ? <p className="m-0 max-w-[min(76%,46ch)] justify-self-end rounded-[18px_18px_0_18px] border border-agent-user-border bg-agent-user-surface px-3.5 py-2.5 text-sm leading-[1.62] text-text-primary [overflow-wrap:anywhere]">{item.text}</p> : (
              <div className="m-0 text-sm leading-[1.62] text-text-primary">
                <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>{displayMarkdown(item.text)}</ReactMarkdown>
              </div>
            )}
          </div>
        ))}
        {busy ? (
          <div className="flex w-fit items-center gap-2 text-xs font-medium text-text-secondary" role="status">
            <span className="flex items-center gap-[3px]" aria-hidden="true"><i className={thinkingDotClass} /><i className={cx(thinkingDotClass, "[animation-delay:140ms]")} /><i className={cx(thinkingDotClass, "[animation-delay:280ms]")} /></span>
            <span>Analyzing</span>
          </div>
        ) : null}
      </div>
      {result?.status === "pending_review" && result.review_id ? (
        <div className="mx-[18px] mb-[18px] grid gap-[7px] rounded-md border border-border bg-surface-subtle p-[13px]">
          <strong className="text-[13px]">Review proposed change</strong>
          <p className="m-0 text-xs text-text-secondary">{diffLabel(result.diff_summary)}</p>
          <div className="flex justify-end gap-2">
            <button className={secondaryActionClass} type="button" disabled={busy} onClick={() => void decide("reject")}>Reject</button>
            <button className={primaryActionClass} type="button" disabled={busy} onClick={() => void decide("approve")}>Approve change</button>
          </div>
        </div>
      ) : null}
      {error ? <p className="mx-6 mt-0 mb-5 rounded-sm bg-[color-mix(in_srgb,var(--color-error)_9%,var(--color-surface))] px-3 py-2.5 text-xs leading-[1.45] text-error" role="alert">{error}</p> : null}
      <form className="mx-[18px] mb-[18px] flex-none max-[720px]:mx-3.5" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <div className="grid gap-[5px] rounded-[10px] border border-border-strong bg-surface pt-[7px] pr-2 pb-[5px] pl-[5px] transition-[border-color,box-shadow] duration-150 focus-within:border-accent focus-within:shadow-[0_0_0_2px_var(--color-accent-subtle)]">
          <textarea
            className="max-h-[168px] min-h-[38px] w-full resize-none border-0 bg-transparent px-2 pt-[5px] pb-[7px] text-sm leading-normal text-text-primary outline-0"
            ref={composerRef}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder="Ask about this workspace…"
            rows={1}
            aria-label="Message the Assistant"
          />
          <div className="flex items-center justify-between gap-2">
            <div className="relative" ref={modelPickerRef}>
              <button
                className="flex min-h-[34px] items-center gap-2 rounded-[7px] border-0 bg-transparent py-1.5 pr-2 pl-2.5 text-[11px] font-semibold text-text-secondary hover:bg-surface-subtle hover:text-text-primary aria-expanded:bg-surface-subtle aria-expanded:text-text-primary max-[720px]:min-h-10"
                type="button"
                aria-haspopup="listbox"
                aria-expanded={modelMenuOpen}
                onClick={() => setModelMenuOpen((open) => !open)}
              >
                <span>{activeModel.label}</span>
                <svg className={cx("h-3.5 w-3.5 transition-transform duration-150", modelMenuOpen && "rotate-180")} aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="m4 6 4 4 4-4" />
                </svg>
              </button>
              {modelMenuOpen ? (
                <div className="absolute bottom-[calc(100%_+_7px)] left-0 z-dropdown grid w-56 rounded-[10px] border border-border bg-surface p-1 shadow-popover" role="listbox" aria-label="Assistant model">
                  {models.map((option) => (
                    <button
                      className="grid gap-0.5 rounded-[5px] border-0 bg-transparent px-2.5 py-[9px] text-left text-text-primary hover:bg-surface-subtle aria-selected:bg-surface-subtle"
                      key={option.id}
                      type="button"
                      role="option"
                      aria-selected={model === option.id}
                      onClick={() => {
                        setModel(option.id);
                        setModelMenuOpen(false);
                      }}
                    >
                      <span className="text-xs font-semibold">{option.label}</span>
                      <small className="text-[11px] leading-[1.35] text-text-secondary">{option.detail}</small>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            <button className="grid h-[31px] w-[31px] flex-none place-items-center rounded-md border-0 bg-accent p-0 text-surface transition-[background-color,transform] duration-150 enabled:hover:-translate-y-px enabled:hover:bg-accent-deep disabled:cursor-not-allowed disabled:bg-border-strong [&_svg]:h-[15px] [&_svg]:w-[15px] max-[720px]:h-10 max-[720px]:w-10" type="submit" disabled={!message.trim() || busy} aria-label="Send message" title="Send message">
              <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M8 13V3m0 0 4 4M8 3 4 7" /></svg>
            </button>
          </div>
        </div>
      </form>
    </aside>
    {helpOpen ? (
      <div className="fixed inset-0 z-backdrop grid place-items-center bg-[rgb(31_35_40_/_35%)] p-6" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeHelp(); }}>
        <section className="w-full max-w-[520px] animate-interface-center-enter rounded-[10px] border border-border bg-surface shadow-dialog data-[state=closing]:pointer-events-none data-[state=closing]:animate-interface-center-exit" data-state={helpClosing ? "closing" : "open"} role="dialog" aria-modal="true" aria-labelledby="agent-help-title">
          <header className="flex items-center justify-between gap-4 border-b border-border px-5 pt-[18px] pb-[15px]">
            <div className="grid gap-1"><span className="text-[11px] font-semibold text-text-secondary">Research Tree assistant</span><h2 className="m-0 text-[17px] tracking-normal" id="agent-help-title">How the assistant can help</h2></div>
            <button className={iconButtonClass} type="button" onClick={closeHelp} aria-label="Close assistant capabilities" title="Close"><svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg></button>
          </header>
          <div className="grid gap-3 p-5">
            <p className="m-0 text-[13px] leading-[1.55] text-text-secondary">Use the assistant to examine individual papers, compare methods and evidence, and clarify the relationships among branches, reading paths, and research questions in the current workspace.</p>
            <p className="m-0 text-[13px] leading-[1.55] text-text-secondary">Structural requests can produce a proposed revision: adding papers, expanding coverage, merging or splitting branches, refining branch structure, or adjusting similar-paper recommendations. Every proposed revision remains subject to review before any workspace change is made.</p>
            <p className="m-0 text-[13px] leading-[1.55] text-text-secondary">Responses draw on workspace metadata and available paper text. Original papers remain the appropriate source for claims that inform research decisions.</p>
          </div>
        </section>
      </div>
    ) : null}
    </>
  );
}

type MarkdownComponentProps<T extends keyof JSX.IntrinsicElements> = ComponentProps<T> & {
  node?: unknown;
};

const markdownComponents = {
  p({ node: _node, className, ...props }: MarkdownComponentProps<"p">) {
    return <p className={cx("mb-3 whitespace-pre-wrap last:mb-0", className)} {...props} />;
  },
  h1({ node: _node, className, ...props }: MarkdownComponentProps<"h1">) {
    return <h1 className={cx("mt-[22px] mb-2.5 text-lg font-bold leading-[1.3] text-text-primary first:mt-0", className)} {...props} />;
  },
  h2({ node: _node, className, ...props }: MarkdownComponentProps<"h2">) {
    return <h2 className={cx("mt-[22px] mb-2.5 text-base font-bold leading-[1.3] text-text-primary first:mt-0", className)} {...props} />;
  },
  h3({ node: _node, className, ...props }: MarkdownComponentProps<"h3">) {
    return <h3 className={cx("mt-[22px] mb-2.5 text-sm font-bold leading-[1.3] text-text-primary first:mt-0", className)} {...props} />;
  },
  h4({ node: _node, className, ...props }: MarkdownComponentProps<"h4">) {
    return <h4 className={cx("mt-[22px] mb-2.5 text-sm font-bold leading-[1.3] text-text-primary first:mt-0", className)} {...props} />;
  },
  ul({ node: _node, className, ...props }: MarkdownComponentProps<"ul">) {
    return <ul className={cx("mt-[-2px] mb-3 pl-5 last:mb-0", className)} {...props} />;
  },
  ol({ node: _node, className, ...props }: MarkdownComponentProps<"ol">) {
    return <ol className={cx("mt-[-2px] mb-3 pl-5 last:mb-0", className)} {...props} />;
  },
  li({ node: _node, className, ...props }: MarkdownComponentProps<"li">) {
    return <li className={cx("mb-[7px] pl-0.5 last:mb-0", className)} {...props} />;
  },
  blockquote({ node: _node, className, ...props }: MarkdownComponentProps<"blockquote">) {
    return <blockquote className={cx("mb-3 border-l-2 border-border-strong pl-2.5 text-text-secondary last:mb-0", className)} {...props} />;
  },
  pre({ node: _node, className, ...props }: MarkdownComponentProps<"pre">) {
    return <pre className={cx("mb-3 overflow-x-auto rounded-sm bg-surface-subtle p-2 last:mb-0", className)} {...props} />;
  },
  code({ node: _node, className, ...props }: MarkdownComponentProps<"code">) {
    return <code className={cx("rounded bg-surface-subtle px-1 py-px font-mono text-[0.92em]", className)} {...props} />;
  },
};

const suggestionClass = "grid min-h-[86px] gap-1 rounded-sm border border-border bg-surface p-3.5 text-left text-text-primary transition-[background-color,border-color,color,transform] duration-150 hover:translate-x-[3px] hover:border-accent hover:bg-accent-subtle hover:text-accent-deep";
const thinkingDotClass = "h-1 w-1 animate-agent-thinking-pulse rounded-full bg-text-secondary";

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
  return conversation.slice(-MAX_CONVERSATION_HISTORY_ITEMS).map((item) => ({
    role: item.role === "agent" ? "assistant" : "user",
    text: item.text.slice(0, MAX_CONVERSATION_HISTORY_CHARACTERS),
  }));
}
