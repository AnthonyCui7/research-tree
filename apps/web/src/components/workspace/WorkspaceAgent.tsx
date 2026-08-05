import {
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type JSX,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cx } from "../../lib/cx";
import { DROPDOWN_EXIT_MS, useExitAnimation } from "../../lib/animation";
import { pluralize } from "../../lib/format";
import { agentRunFailed, type AgentSession } from "../../data/useAgentSession";
import { ArrowRightIcon, ChevronDownIcon, CheckIcon, CloseIcon, SendIcon } from "../ui/icons";
import type { AgentRunResult, BranchTreeNode, TreeViewModel } from "../../lib/types";

type WorkspaceAgentProps = {
  session: AgentSession;
  /** Null only while a workspace is still loading; the intro reads from it. */
  tree: TreeViewModel | null;
  onClose: () => void;
};

const MODELS = [
  { id: "gpt-5.6-luna", label: "gpt-5.6-luna", detail: "Recommended" },
  { id: "gpt-5.6-terra", label: "gpt-5.6-terra", detail: "Smarter" },
  { id: "gpt-5.6-sol", label: "gpt-5.6-sol", detail: "Smartest" },
] as const;

export function WorkspaceAgent({ session, tree, onClose }: WorkspaceAgentProps) {
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const modelMenu = useExitAnimation(modelMenuOpen, DROPDOWN_EXIT_MS);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const activeModel = MODELS.find((option) => option.id === session.model) ?? MODELS[0];
  const { busy, conversation, error, outcome, result, draft: message, setDraft: setMessage } = session;

  useEffect(() => {
    function closeModelMenu(event: MouseEvent) {
      if (!modelPickerRef.current?.contains(event.target as Node)) {
        setModelMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", closeModelMenu);
    return () => document.removeEventListener("mousedown", closeModelMenu);
  }, []);

  useEffect(() => {
    const element = conversationRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, [conversation.length, busy, result, outcome]);

  useEffect(() => {
    const composer = composerRef.current;
    if (!composer) return;
    composer.style.height = "auto";
    composer.style.height = `${Math.min(composer.scrollHeight, 168)}px`;
  }, [message]);

  function submit() {
    const request = message.trim();
    if (!request || busy) return;
    setMessage("");
    void session.send(request);
  }

  // An opener is a starting point, not a question already asked: it lands in the
  // composer so it can be edited or added to before it is sent.
  function fillComposer(prompt: string) {
    setMessage(prompt);
    composerRef.current?.focus();
  }

  const showIntro = conversation.length === 0 && !busy && !result && !error;

  function onComposerKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    submit();
  }

  return (
    <>
      <header className="flex h-11 flex-none items-center gap-2 border-b border-[#e9e8e4] px-4">
        <h2 className="m-0 flex-1 text-[13.5px] font-bold text-text-primary">Assistant</h2>
        <button
          className="grid h-[26px] w-[26px] flex-none place-items-center rounded-[6px] border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary"
          type="button"
          onClick={onClose}
          aria-label="Close assistant"
          title="Close"
        >
          <CloseIcon className="h-3 w-3" />
        </button>
      </header>

      <div
        className={cx(
          "scrollbar-rt flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto px-4 pt-[18px] pb-2",
          // With nothing said yet the panel is mostly empty space, so the
          // opening prompts sit in the middle of it rather than clinging to the
          // top of a tall blank column.
          showIntro && "justify-center",
        )}
        ref={conversationRef}
        aria-live="polite"
      >
        {showIntro ? <AgentIntro tree={tree} onUse={fillComposer} /> : null}

        {conversation.map((item, index) =>
          item.role === "user" ? (
            <div className="flex justify-end" key={`user-${index}`}>
              <p className="m-0 max-w-[46ch] rounded-[14px_14px_4px_14px] border border-agent-user-border bg-agent-user-surface px-[13px] py-[9px] text-[13px] leading-[1.62] text-text-primary [overflow-wrap:anywhere]">
                {item.text}
              </p>
            </div>
          ) : (
            <div className="text-[13px] leading-[1.62] text-text-primary" key={`agent-${index}`}>
              <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
                {displayMarkdown(item.text)}
              </ReactMarkdown>
            </div>
          ),
        )}

        {busy ? (
          <div className="flex w-fit items-center gap-2 text-xs font-medium text-text-muted" role="status">
            <span className="flex items-center gap-[3px]" aria-hidden="true">
              <i className={thinkingDotClass} />
              <i className={cx(thinkingDotClass, "[animation-delay:140ms]")} />
              <i className={cx(thinkingDotClass, "[animation-delay:280ms]")} />
            </span>
            Analyzing
          </div>
        ) : null}

        {result?.status === "pending_review" && result.review_id ? (
          <ProposedRevision result={result} busy={busy} onDecide={session.decide} />
        ) : null}

        {outcome === "applied" ? (
          <div className="flex items-center gap-2 rounded-lg border border-accent-border bg-accent-subtle px-[13px] py-2.5 text-xs text-accent-deep">
            <CheckIcon className="h-3 w-3 flex-none" />
            Revision applied. It is the current version — see History.
          </div>
        ) : null}
        {outcome === "rejected" ? (
          <div className="rounded-lg border border-border bg-surface-subtle px-[13px] py-2.5 text-xs text-text-secondary">
            Revision rejected. The workspace is unchanged.
          </div>
        ) : null}

        {result ? <AgentRunNotices result={result} /> : null}
        {error ? (
          <div className="flex items-start gap-2 rounded-lg border border-error-border bg-error-surface px-[13px] py-2.5 text-xs leading-[1.5] text-error" role="alert">
            <span className="min-w-0 flex-1">{error}</span>
            <button
              className="-mr-1 grid h-5 w-5 flex-none place-items-center rounded-[5px] border-0 bg-transparent p-0 hover:bg-[#f0dcdc]"
              type="button"
              onClick={session.dismissError}
              aria-label="Dismiss assistant error"
            >
              <CloseIcon className="h-2.5 w-2.5" />
            </button>
          </div>
        ) : null}
      </div>

      <div className="flex-none px-4 pt-2.5 pb-3.5">
        <form
          className="rounded-xl border border-border bg-surface pt-2.5 pr-2.5 pb-2 pl-3.5 transition-[border-color,box-shadow] duration-150 focus-within:border-accent focus-within:shadow-[0_0_0_2px_var(--color-accent-subtle)]"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <textarea
            className="max-h-[168px] min-h-[20px] w-full resize-none border-0 bg-transparent p-0 text-[13px] leading-[1.5] text-text-primary outline-0 placeholder:text-text-muted"
            ref={composerRef}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={onComposerKeyDown}
            placeholder="Ask about this workspace…"
            rows={1}
            aria-label="Message the assistant"
          />
          <div className="mt-2 flex items-center">
            <div className="relative" ref={modelPickerRef}>
              <button
                className="flex items-center gap-1 rounded-[6px] border-0 bg-transparent px-2 py-[3px] text-[11px] text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary aria-expanded:bg-surface-subtle aria-expanded:text-text-primary"
                type="button"
                aria-haspopup="listbox"
                aria-expanded={modelMenuOpen}
                onClick={() => setModelMenuOpen((open) => !open)}
                title="Switch model"
              >
                {activeModel.label}
                <ChevronDownIcon className={cx("h-3 w-3 transition-transform duration-150", modelMenuOpen && "rotate-180")} />
              </button>
              {modelMenu.present ? (
                <div
                  className={cx(
                    "absolute bottom-[calc(100%+7px)] left-0 z-dropdown grid w-56 origin-bottom-left rounded-lg border border-border bg-surface p-1 shadow-popover",
                    modelMenu.closing ? "animate-dropdown-exit" : "animate-dropdown-enter",
                  )}
                  role="listbox"
                  aria-label="Assistant model"
                >
                  {MODELS.map((option) => (
                    <button
                      className="grid gap-0.5 rounded-[5px] border-0 bg-transparent px-2.5 py-2 text-left transition-[background-color] duration-150 hover:bg-surface-subtle aria-selected:bg-surface-subtle"
                      key={option.id}
                      type="button"
                      role="option"
                      aria-selected={session.model === option.id}
                      onClick={() => {
                        session.setModel(option.id);
                        setModelMenuOpen(false);
                      }}
                    >
                      <span className="text-xs font-semibold text-text-primary">{option.label}</span>
                      <span className="text-[11px] text-text-muted">{option.detail}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            <button
              className="ml-auto grid h-8 w-8 flex-none place-items-center rounded-lg border-0 bg-accent p-0 text-white transition-[background-color,transform] duration-150 enabled:hover:bg-accent-deep enabled:active:scale-95 disabled:cursor-not-allowed disabled:bg-border-strong"
              type="submit"
              disabled={!message.trim() || busy}
              aria-label="Send message"
              title="Send"
            >
              <SendIcon className="h-3.5 w-3.5" />
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

/**
 * The opening screen: what the assistant is looking at, and three things worth
 * asking it about that tree. Choosing one writes it into the composer, where it
 * can be sharpened before it is sent.
 */
function AgentIntro({
  tree,
  onUse,
}: {
  tree: TreeViewModel | null;
  onUse: (prompt: string) => void;
}) {
  const openers = introPrompts(tree);
  return (
    <section className="px-0.5">
      {tree ? (
        <p className="m-0 text-[13px] leading-[1.5] text-text-muted">
          {tree.title} · {pluralize(tree.branchCount, "branch", "branches")} ·{" "}
          {pluralize(tree.paperCount, "paper")}
        </p>
      ) : null}
      <h3 className="mt-[3px] mb-0 text-[19px] font-bold tracking-[-0.015em] text-text-primary">
        Ask about this tree
      </h3>
      <div className="mt-3.5 border-t border-hairline">
        {openers.map((opener) => (
          <button
            className="group flex w-full items-center gap-3 rounded-[7px] border-0 border-b border-hairline bg-transparent px-1.5 py-[13px] text-left transition-[background-color] duration-150 hover:bg-surface-subtle"
            key={opener.title}
            type="button"
            onClick={() => onUse(opener.prompt)}
          >
            <span className="min-w-0 flex-1">
              <span className="block text-[13.5px] font-bold text-text-primary">
                {opener.title}
              </span>
              <span className="mt-[3px] block text-[12.5px] leading-[1.5] text-text-secondary">
                {opener.detail}
              </span>
            </span>
            <ArrowRightIcon
              className="h-3.5 w-3.5 flex-none text-text-muted transition-[color,transform] duration-150 group-hover:translate-x-0.5 group-hover:text-accent-deep"
            />
          </button>
        ))}
      </div>
    </section>
  );
}

type IntroPrompt = { title: string; detail: string; prompt: string };

/**
 * The openers describe the tree in front of the reader rather than research in
 * general, so the counts and branch names come from the workspace itself.
 */
function introPrompts(tree: TreeViewModel | null): IntroPrompt[] {
  const papers = tree?.paperCount ?? 0;
  const branches = (tree?.nodes ?? []).filter(
    (node): node is BranchTreeNode => node.kind === "branch",
  );
  const openers: IntroPrompt[] = [
    {
      title: "Critique coverage",
      detail: "Where this tree is thin and what would fill it",
      prompt: "Critique the coverage of this workspace. Where is it thin, and what would fill it?",
    },
    {
      title: "Plan a reading path",
      detail: papers
        ? `All ${papers} papers in one order, tuned to your background`
        : "Every paper in one order, tuned to your background",
      prompt:
        "Plan a single reading path through every paper in this workspace, ordered for someone new to the field.",
    },
  ];
  const [first, second] = branches;
  if (first && second) {
    openers.push({
      title: "Compare two branches",
      detail: `${first.title} vs. ${second.title}, and where they meet`,
      prompt: `Compare the “${first.title}” and “${second.title}” branches of this workspace, and explain where they meet.`,
    });
  } else {
    openers.push({
      title: "What's missing?",
      detail: "Recent work this tree does not account for",
      prompt: "What important recent work is missing from this workspace?",
    });
  }
  return openers;
}

/* ---------------------------------------------------------- review card --- */

function ProposedRevision({
  result,
  busy,
  onDecide,
}: {
  result: AgentRunResult;
  busy: boolean;
  onDecide: (choice: "approve" | "reject") => Promise<void>;
}) {
  const operations = describeOperations(result.diff_summary);
  const count = operationCount(result.diff_summary);
  const paperDelta = paperCountDelta(result.diff_summary);

  return (
    <div className="overflow-hidden rounded-[11px] border border-border bg-surface">
      <div className="flex items-center gap-[7px] border-b border-hairline-soft px-[13px] py-2.5">
        <span className="h-[7px] w-[7px] flex-none rounded-full bg-accent" aria-hidden="true" />
        <span className="flex-1 text-xs font-semibold text-text-primary">Proposed revision</span>
        <span className="text-[10.5px] text-text-muted">
          {count === null ? "needs your approval" : `${count} operation${count === 1 ? "" : "s"} · needs your approval`}
        </span>
      </div>
      <div className="flex flex-col gap-2 px-[13px] py-[11px]">
        {operations.length > 0 ? (
          operations.map((operation) => (
            <div className="flex gap-2 text-xs leading-[1.5]" key={operation.type}>
              <span className={cx("mt-px flex-none rounded-[5px] px-[7px] text-[10px] font-semibold", badgeTone(operation.badge))}>
                {operation.badge}
              </span>
              <span className="min-w-0 text-text-primary">{operation.label}</span>
            </div>
          ))
        ) : (
          <p className="m-0 text-xs leading-[1.5] text-text-secondary">
            A structural revision is ready for your review.
          </p>
        )}
        {paperDelta ? (
          <p className="m-0 border-t border-dashed border-hairline pt-2 text-[11.5px] leading-[1.55] text-text-muted">
            Visible papers: {paperDelta.before} → {paperDelta.after}
          </p>
        ) : null}
      </div>
      <div className="flex items-center gap-2 border-t border-hairline-soft bg-surface-muted px-[13px] py-2.5">
        <button
          className="rounded-[7px] border-0 bg-accent px-3.5 py-1.5 text-xs font-semibold text-white transition-[background-color] duration-150 enabled:hover:bg-accent-deep disabled:cursor-not-allowed disabled:bg-border-strong"
          type="button"
          disabled={busy}
          onClick={() => void onDecide("approve")}
        >
          Approve &amp; apply
        </button>
        <button
          className="rounded-[7px] border border-border bg-surface px-3.5 py-1.5 text-xs font-semibold text-text-secondary transition-[border-color,color] duration-150 enabled:hover:border-border-strong enabled:hover:text-text-primary disabled:cursor-not-allowed disabled:text-text-muted"
          type="button"
          disabled={busy}
          onClick={() => void onDecide("reject")}
        >
          Reject
        </button>
      </div>
    </div>
  );
}

type OperationDescription = { type: string; badge: string; label: string };

const OPERATION_LABELS: Record<string, { badge: string; label: string }> = {
  promote_candidate_paper: { badge: "ADD", label: "Add papers to the workspace" },
  create_paper_path: { badge: "ADD", label: "Add a reading path" },
  split_branch: { badge: "ADD", label: "Add research branches" },
  demote_visible_paper: { badge: "REMOVE", label: "Remove papers from the workspace" },
  merge_branches: { badge: "MERGE", label: "Merge research branches" },
  move_paper: { badge: "MOVE", label: "Move papers between branches" },
  update_reading_order: { badge: "MOVE", label: "Reorder the reading order" },
  rename_branch: { badge: "EDIT", label: "Rename a research branch" },
  update_paper_card: { badge: "EDIT", label: "Update paper details" },
  refresh_similar_papers: { badge: "EDIT", label: "Refresh similar-paper suggestions" },
  update_root_overview: { badge: "EDIT", label: "Update the topic overview" },
  update_workspace_subtree: { badge: "EDIT", label: "Restructure part of the tree" },
};

function describeOperations(diff: Record<string, unknown> | null): OperationDescription[] {
  const types = diff?.operation_types;
  if (!Array.isArray(types)) {
    return [];
  }
  return types.flatMap((value) => {
    const type = String(value);
    const known = OPERATION_LABELS[type];
    return [
      known
        ? { type, badge: known.badge, label: known.label }
        : { type, badge: "EDIT", label: humanizeOperation(type) },
    ];
  });
}

function humanizeOperation(type: string): string {
  const words = type.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function badgeTone(badge: string): string {
  if (badge === "ADD") return "bg-accent-subtle text-accent-deep";
  if (badge === "REMOVE") return "bg-error-surface text-error";
  return "bg-surface-subtle text-text-secondary";
}

function operationCount(diff: Record<string, unknown> | null): number | null {
  const value = Number(diff?.operation_count ?? Number.NaN);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function paperCountDelta(
  diff: Record<string, unknown> | null,
): { before: number; after: number } | null {
  const before = Number(diff?.visible_paper_count_before ?? Number.NaN);
  const after = Number(diff?.visible_paper_count_after ?? Number.NaN);
  if (!Number.isFinite(before) || !Number.isFinite(after) || before === after) {
    return null;
  }
  return { before, after };
}

/* -------------------------------------------------------------- notices --- */

/**
 * A run that failed, or finished with warnings, states so. The backend reports
 * both in `errors`/`warnings`, and a failed run's `final_response` is a failure
 * notice rather than an answer — neither belongs in the conversation.
 */
function AgentRunNotices({ result }: { result: AgentRunResult }) {
  const failed = agentRunFailed(result.status);
  const errors = noticeLines(result.errors);
  const warnings = noticeLines(result.warnings);
  if (!failed && warnings.length === 0) {
    return null;
  }
  return (
    <div className="grid gap-2">
      {failed ? (
        <div className="grid gap-1 rounded-lg border border-error-border bg-error-surface px-[13px] py-2.5 text-xs leading-[1.5] text-error" role="alert">
          <span>
            {result.final_response?.trim() ||
              "The assistant could not complete that request. Your workspace is unchanged."}
          </span>
          {errors.map((detail, index) => (
            <span className="text-[11px] leading-[1.45] opacity-80 [overflow-wrap:anywhere]" key={`${index}:${detail}`}>
              {detail}
            </span>
          ))}
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <div className="grid gap-1 rounded-lg border border-warning-border bg-warning-surface px-[13px] py-2.5 text-xs leading-[1.5] text-warning" role="status">
          <strong className="text-[11px] font-semibold">
            {warnings.length === 1 ? "Warning" : "Warnings"}
          </strong>
          {warnings.map((warning, index) => (
            <span className="[overflow-wrap:anywhere]" key={`${index}:${warning}`}>
              {warning}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function noticeLines(values: string[] | undefined): string[] {
  return (values ?? []).map((value) => value.trim()).filter(Boolean);
}

/* ------------------------------------------------------------- markdown --- */

type MarkdownComponentProps<T extends keyof JSX.IntrinsicElements> = ComponentProps<T> & {
  node?: unknown;
};

const markdownComponents = {
  p({ node: _node, className, ...props }: MarkdownComponentProps<"p">) {
    return <p className={cx("mb-3 whitespace-pre-wrap last:mb-0", className)} {...props} />;
  },
  h1({ node: _node, className, ...props }: MarkdownComponentProps<"h1">) {
    return <h1 className={cx(markdownHeadingClass, "text-[15px]", className)} {...props} />;
  },
  h2({ node: _node, className, ...props }: MarkdownComponentProps<"h2">) {
    return <h2 className={cx(markdownHeadingClass, "text-[14px]", className)} {...props} />;
  },
  h3({ node: _node, className, ...props }: MarkdownComponentProps<"h3">) {
    return <h3 className={cx(markdownHeadingClass, "text-[13px]", className)} {...props} />;
  },
  h4({ node: _node, className, ...props }: MarkdownComponentProps<"h4">) {
    return <h4 className={cx(markdownHeadingClass, "text-[13px]", className)} {...props} />;
  },
  ul({ node: _node, className, ...props }: MarkdownComponentProps<"ul">) {
    return <ul className={cx("mt-[-2px] mb-3 list-disc pl-5 last:mb-0", className)} {...props} />;
  },
  ol({ node: _node, className, ...props }: MarkdownComponentProps<"ol">) {
    return <ol className={cx("mt-[-2px] mb-3 list-decimal pl-5 last:mb-0", className)} {...props} />;
  },
  li({ node: _node, className, ...props }: MarkdownComponentProps<"li">) {
    return <li className={cx("mb-[7px] pl-0.5 last:mb-0", className)} {...props} />;
  },
  a({ node: _node, className, ...props }: MarkdownComponentProps<"a">) {
    return (
      <a
        className={cx("text-accent no-underline hover:text-accent-deep hover:underline", className)}
        target="_blank"
        rel="noreferrer"
        {...props}
      />
    );
  },
  blockquote({ node: _node, className, ...props }: MarkdownComponentProps<"blockquote">) {
    return (
      <blockquote
        className={cx("mb-3 border-l-2 border-border pl-2.5 text-text-secondary last:mb-0", className)}
        {...props}
      />
    );
  },
  pre({ node: _node, className, ...props }: MarkdownComponentProps<"pre">) {
    return <pre className={cx("mb-3 overflow-x-auto rounded-md bg-surface-subtle p-2.5 last:mb-0", className)} {...props} />;
  },
  code({ node: _node, className, ...props }: MarkdownComponentProps<"code">) {
    return <code className={cx("rounded-sm bg-surface-subtle px-1 py-px font-mono text-[0.92em]", className)} {...props} />;
  },
};

const markdownHeadingClass = "mt-5 mb-2 font-bold leading-[1.35] text-text-primary first:mt-0";
const thinkingDotClass = "h-1 w-1 animate-agent-thinking-pulse rounded-full bg-text-muted";

function displayMarkdown(text: string): string {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  return lines
    .map((line, index) => {
      const trimmed = line.trim();
      if (!trimmed) return line;
      if (isPlainAssistantHeading(lines, index)) {
        return `## ${trimmed}`;
      }
      if (isBracketedTexLine(trimmed)) {
        return texLineToReadableText(trimmed);
      }
      return line;
    })
    .join("\n");
}

function isPlainAssistantHeading(lines: string[], index: number): boolean {
  const line = lines[index]?.trim() ?? "";
  if (line.length > 72 || /[.!?:;,]$/.test(line)) return false;
  if (/^(#{1,6}|\d+\.|[-*+]\s|>|```)/.test(line)) return false;
  if (!/[A-Za-z]/.test(line)) return false;
  const previous = lines[index - 1]?.trim();
  const next = lines[index + 1]?.trim();
  if (index > 0 && previous) return false;
  if (!next) return false;
  return line.split(/\s+/).length <= 8;
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
