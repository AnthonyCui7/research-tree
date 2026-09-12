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
import { ProposedRevision } from "./ProposedRevision";
import type {
  AgentActivity,
  AgentRunResult,
  AgentStep,
  BranchTreeNode,
  TreeViewModel,
} from "../../lib/types";

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
  const {
    activity,
    busy,
    conversation,
    error,
    outcome,
    pendingReview,
    result,
    steps,
    draft: message,
    setDraft: setMessage,
  } = session;

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
  }, [conversation.length, busy, pendingReview, result, outcome, steps.length]);

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

  const showIntro = conversation.length === 0 && !busy && !pendingReview && !result && !error;

  function onComposerKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    submit();
  }

  return (
    <>
      <header className="flex h-11 flex-none items-center gap-2 border-b border-[#e9e8e4] px-7">
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
          // Static 28px gutters: the panel stretches to half the screen and
          // beyond, and the chat column should not ride its edges when it does.
          "scrollbar-rt flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto px-7 pt-[18px] pb-2",
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
              {item.steps?.length ? <StepsTaken steps={item.steps} /> : null}
              <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
                {displayMarkdown(item.text)}
              </ReactMarkdown>
            </div>
          ),
        )}

        {busy ? <ActivityTrail steps={steps} activity={activity} /> : null}

        {pendingReview ? (
          <ProposedRevision
            result={pendingReview}
            tree={tree}
            busy={busy}
            onDecide={session.decide}
            restoredUserMessage={session.restoredUserMessage}
          />
        ) : null}

        {outcome === "applied" ? (
          <div className="flex items-center gap-2 rounded-lg border border-accent-border bg-accent-subtle px-[13px] py-2.5 text-xs text-accent-deep">
            <CheckIcon className="h-3 w-3 flex-none" />
            Revision applied.
          </div>
        ) : null}
        {outcome === "rerun_started" ? (
          <div className="flex items-center gap-2 rounded-lg border border-accent-border bg-accent-subtle px-[13px] py-2.5 text-xs text-accent-deep">
            <CheckIcon className="h-3 w-3 flex-none" />
            Approved. A pipeline re-run has started.
          </div>
        ) : null}
        {outcome === "rejected" ? (
          <div className="rounded-lg border border-border bg-surface-subtle px-[13px] py-2.5 text-xs text-text-secondary">
            Revision rejected.
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

      <div className="flex-none px-7 pt-2.5 pb-3.5">
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

/* ------------------------------------------------------------- activity --- */

/**
 * The turn as it runs: each step already taken, then what the assistant is
 * doing now. A turn is a minute or more of model calls and lookups; this is
 * what makes the wait legible.
 */
function ActivityTrail({ steps, activity }: { steps: AgentStep[]; activity: AgentActivity | null }) {
  const current = activity ?? { kind: "thinking" as const };
  return (
    <div className="grid gap-1.5 text-xs text-text-muted" role="status">
      {steps.map((step, index) => (
        <div className="flex items-baseline gap-2" key={`${index}:${stepKey(step)}`}>
          <span className="relative top-[-2px] h-1 w-1 flex-none rounded-full bg-border-strong" aria-hidden="true" />
          <span className="min-w-0 [overflow-wrap:anywhere]">{describe(step, "done")}</span>
        </div>
      ))}
      <div className="flex w-fit items-center gap-2 font-medium">
        <span className="flex items-center gap-[3px]" aria-hidden="true">
          <i className={thinkingDotClass} />
          <i className={cx(thinkingDotClass, "[animation-delay:140ms]")} />
          <i className={cx(thinkingDotClass, "[animation-delay:280ms]")} />
        </span>
        {describe(current, "doing")}
      </div>
    </div>
  );
}

/** What a finished reply was built from, folded to one line until opened. */
function StepsTaken({ steps }: { steps: AgentStep[] }) {
  return (
    <details className="group mb-2.5 text-xs text-text-muted">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 [&::-webkit-details-marker]:hidden">
        <ChevronDownIcon className="h-3 w-3 flex-none -rotate-90 transition-transform duration-150 group-open:rotate-0" />
        <span className="min-w-0 [overflow-wrap:anywhere]">{summarizeSteps(steps)}</span>
      </summary>
      <div className="mt-1.5 grid gap-1 pl-[18px]">
        {steps.map((step, index) => (
          <span className="[overflow-wrap:anywhere]" key={`${index}:${stepKey(step)}`}>
            {describe(step, "done")}
          </span>
        ))}
      </div>
    </details>
  );
}

type Tense = "doing" | "done";

/**
 * The copy for each thing the assistant can be doing. `link` joins the verb
 * to a quoted subject ("Searched Semantic Scholar for “…”"); a step without
 * one names its subject directly ("Read “…”"). The folded summary drops
 * subjects and counts repeats: `noun` is what got counted ("Read 3 papers"),
 * `folded` overrides the whole phrase.
 */
type ToolCopy = {
  doing: string;
  done: string;
  link?: string;
  noun?: string;
  folded?: (count: number) => string;
};

const TOOL_COPY: Record<string, ToolCopy> = {
  search_workspace: { doing: "Searching the workspace", done: "Searched the workspace", link: "for" },
  search_semantic_scholar: {
    doing: "Searching Semantic Scholar",
    done: "Searched Semantic Scholar",
    link: "for",
  },
  web_search: { doing: "Searching the web", done: "Searched the web", link: "for" },
  get_paper: { doing: "Reading", done: "Read", noun: "paper" },
  get_paper_full_text: { doing: "Reading the full text of", done: "Read the full text of", noun: "paper" },
  get_semantic_scholar_paper: { doing: "Looking up", done: "Looked up", noun: "paper" },
  get_branch: {
    doing: "Reading the branch",
    done: "Read the branch",
    folded: (count) => (count === 1 ? "Read a branch" : `Read ${count} branches`),
  },
  get_workspace_overview: { doing: "Reading the overview", done: "Read the overview" },
  list_reading_order: { doing: "Reading the reading paths", done: "Read the reading paths" },
  list_workspace_history: { doing: "Reading the history", done: "Read the history" },
};

const STAGE_COPY: Record<string, { doing: string; done: string }> = {
  reading_workspace: { doing: "Reading the workspace", done: "Read the workspace" },
  constructing: { doing: "Drafting the revision", done: "Drafted the revision" },
  validating: { doing: "Checking the revision", done: "Checked the revision" },
  skeptic: { doing: "Looking for objections", done: "Looked for objections" },
  repairing: { doing: "Repairing the revision", done: "Repaired the revision" },
  saving_review: { doing: "Saving the revision for review", done: "Saved the revision for review" },
  critiquing: { doing: "Auditing the workspace", done: "Audited the workspace" },
};

function describe(activity: AgentActivity, tense: Tense): string {
  if (activity.kind === "thinking") return "Thinking";
  if (activity.kind === "stage") {
    const copy = STAGE_COPY[activity.stage];
    return copy ? copy[tense] : humanize(activity.stage);
  }
  const copy = TOOL_COPY[activity.name];
  if (!copy) return `${tense === "doing" ? "Running" : "Ran"} ${humanize(activity.name)}`;
  if (!activity.subject) return copy[tense];
  return `${copy[tense]}${copy.link ? ` ${copy.link}` : ""} “${activity.subject}”`;
}

/** "Searched Semantic Scholar twice · Read 3 papers · Drafted the revision" */
function summarizeSteps(steps: AgentStep[]): string {
  const counts = new Map<string, { step: AgentStep; count: number }>();
  for (const step of steps) {
    const key = stepKey(step);
    const entry = counts.get(key);
    if (entry) entry.count += 1;
    else counts.set(key, { step, count: 1 });
  }
  return [...counts.values()]
    .map(({ step, count }) => {
      const copy = step.kind === "tool" ? TOOL_COPY[step.name] : undefined;
      if (copy?.folded) return copy.folded(count);
      const done = describe({ ...step, ...(step.kind === "tool" ? { subject: null } : {}) }, "done");
      if (copy?.noun) return `${done} ${count === 1 ? `a ${copy.noun}` : pluralize(count, copy.noun)}`;
      if (count === 1) return done;
      return `${done} ${count === 2 ? "twice" : `${count} times`}`;
    })
    .join(" · ");
}

function stepKey(step: AgentStep): string {
  return step.kind === "tool" ? `tool:${step.name}` : `stage:${step.stage}`;
}

function humanize(identifier: string): string {
  const words = identifier.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
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
              "The assistant could not complete that request."}
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
  // An image in a reply would make the browser fetch whatever address the
  // model wrote, from the reader's machine. The reference is kept as a link
  // the reader can choose to follow.
  img({ node: _node, src, alt }: MarkdownComponentProps<"img">) {
    const href = typeof src === "string" ? src : "";
    if (!href) return null;
    return (
      <a className="text-accent no-underline hover:text-accent-deep hover:underline" href={href} target="_blank" rel="noreferrer">
        {alt || href}
      </a>
    );
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
