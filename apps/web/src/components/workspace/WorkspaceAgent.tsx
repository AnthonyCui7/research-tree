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
import { pluralize } from "../../lib/format";
import { errorNoticeClass, warningNoticeClass } from "../../lib/controlClasses";
import { agentRunFailed, type AgentSession } from "../../data/useAgentSession";
import { PanelHeader } from "../panel/RightPanel";
import { MenuItem, MenuSection, PopoverMenu, anchorFromEvent, type MenuAnchor } from "../ui/PopoverMenu";
import { CheckIcon, ChevronDownIcon, CloseIcon, SendIcon } from "../ui/icons";
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
  { id: "gpt-5.6-luna", detail: "Recommended" },
  { id: "gpt-5.6-terra", detail: "Smarter" },
  { id: "gpt-5.6-sol", detail: "Smartest" },
] as const;

/** Tallest the composer grows before it scrolls. */
const COMPOSER_MAX_HEIGHT = 200;

export function WorkspaceAgent({ session, tree, onClose }: WorkspaceAgentProps) {
  const [modelAnchor, setModelAnchor] = useState<MenuAnchor | null>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
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
    const element = conversationRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, [conversation.length, busy, pendingReview, result, outcome, steps.length]);

  useEffect(() => {
    const composer = composerRef.current;
    if (!composer) return;
    composer.style.height = "auto";
    composer.style.height = `${Math.min(composer.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
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
      <PanelHeader title="Assistant" onClose={onClose} closeLabel="Close assistant" />

      <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto" ref={conversationRef} aria-live="polite">
        <div className="mx-auto flex min-h-full w-full max-w-[760px] flex-col gap-6 px-6 pt-6 pb-4">
          {showIntro ? <AgentIntro tree={tree} onUse={fillComposer} /> : null}

          {conversation.map((item, index) =>
            item.role === "user" ? (
              <div className="flex justify-end pl-10" key={`user-${index}`}>
                <p className="m-0 rounded-[20px] bg-surface-subtle px-4 py-2.5 text-[14px] leading-[1.6] whitespace-pre-wrap text-text-primary [overflow-wrap:anywhere]">
                  {item.text}
                </p>
              </div>
            ) : (
              <div className="text-[14px] leading-[1.7] text-text-primary" key={`agent-${index}`}>
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

          {outcome ? <ReviewOutcomeLine outcome={outcome} /> : null}
          {result ? <AgentRunNotices result={result} /> : null}
          {error ? (
            <div className={cx(errorNoticeClass, "flex items-start gap-2")} role="alert">
              <span className="min-w-0 flex-1">{error}</span>
              <button
                className="-mr-1 grid h-5 w-5 flex-none place-items-center rounded-[5px] border-0 bg-transparent p-0 hover:bg-error-border/40"
                type="button"
                onClick={session.dismissError}
                aria-label="Dismiss assistant error"
              >
                <CloseIcon className="h-2.5 w-2.5" />
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <div className="flex-none px-6 pt-1 pb-5">
        <form
          className="mx-auto w-full max-w-[760px] rounded-[20px] border border-border bg-surface shadow-composer transition-[border-color] duration-150 focus-within:border-border-strong"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <textarea
            className="block max-h-[200px] min-h-[24px] w-full resize-none border-0 bg-transparent px-4 pt-3.5 pb-1 text-[14px] leading-[1.55] text-text-primary outline-0 placeholder:text-text-muted"
            ref={composerRef}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={onComposerKeyDown}
            placeholder={tree ? `Ask about ${tree.title}` : "Ask about this workspace"}
            rows={1}
            aria-label="Message the assistant"
          />
          <div className="flex items-center gap-2 px-2.5 pt-1 pb-2.5">
            <button
              className="flex h-8 items-center gap-1 rounded-full border-0 bg-transparent px-2.5 text-[12.5px] font-medium text-text-secondary transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary aria-expanded:bg-surface-subtle aria-expanded:text-text-primary"
              type="button"
              aria-haspopup="menu"
              aria-expanded={modelAnchor !== null}
              onClick={(event) =>
                setModelAnchor((current) =>
                  current ? null : anchorFromEvent(event.currentTarget, "left", true),
                )
              }
              title="Switch model"
            >
              {session.model}
              <ChevronDownIcon className="h-3.5 w-3.5" />
            </button>
            <button
              className="ml-auto grid h-8 w-8 flex-none place-items-center rounded-full border-0 bg-accent p-0 text-white transition-[background-color] duration-150 enabled:hover:bg-accent-deep disabled:cursor-not-allowed disabled:bg-border-strong"
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

      {modelAnchor ? (
        <PopoverMenu
          anchor={modelAnchor}
          onClose={() => setModelAnchor(null)}
          label="Assistant model"
          width={220}
        >
          <MenuSection>
            {MODELS.map((option) => (
              <MenuItem
                key={option.id}
                checked={session.model === option.id}
                description={option.detail}
                onClick={() => session.setModel(option.id)}
              >
                {option.id}
              </MenuItem>
            ))}
          </MenuSection>
        </PopoverMenu>
      ) : null}
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
    <section className="my-auto py-6">
      <h3 className="m-0 text-center text-[22px] leading-[1.3] font-semibold tracking-[-0.02em] text-text-primary [overflow-wrap:anywhere]">
        {tree ? `Ask about ${tree.title}` : "Ask about this workspace"}
      </h3>
      {tree ? (
        <p className="mt-1.5 mb-0 text-center text-[13px] text-text-muted">
          {pluralize(tree.branchCount, "branch", "branches")} · {pluralize(tree.paperCount, "paper")}
        </p>
      ) : null}
      <div className="mx-auto mt-7 grid max-w-[520px] gap-2">
        {openers.map((opener) => (
          <button
            className="rounded-xl border border-border bg-surface px-4 py-3 text-left transition-[background-color,border-color] duration-150 hover:border-border-strong hover:bg-surface-subtle"
            key={opener.title}
            type="button"
            onClick={() => onUse(opener.prompt)}
          >
            <span className="block text-[13.5px] font-medium text-text-primary">{opener.title}</span>
            <span className="mt-0.5 block text-[12.5px] leading-[1.5] text-text-muted">
              {opener.detail}
            </span>
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
    <div className="grid gap-1.5" role="status">
      {steps.map((step, index) => (
        <div className="flex items-baseline gap-2 text-[13px] text-text-muted" key={`${index}:${stepKey(step)}`}>
          <CheckIcon className="relative top-[1px] h-3 w-3 flex-none" />
          <span className="min-w-0 [overflow-wrap:anywhere]">{describe(step, "done")}</span>
        </div>
      ))}
      <span className="w-fit text-[13.5px] font-medium text-shimmer [overflow-wrap:anywhere]">
        {describe(current, "doing")}
      </span>
    </div>
  );
}

/** What a finished reply was built from, folded to one line until opened. */
function StepsTaken({ steps }: { steps: AgentStep[] }) {
  return (
    <details className="group mb-3">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-[13px] text-text-muted transition-[color] duration-150 hover:text-text-secondary [&::-webkit-details-marker]:hidden">
        <span className="[overflow-wrap:anywhere]">{summarizeSteps(steps)}</span>
        <ChevronDownIcon className="h-3.5 w-3.5 flex-none -rotate-90 transition-transform duration-150 group-open:rotate-0" />
      </summary>
      <ol className="m-0 mt-2 ml-1 grid list-none gap-1 border-l border-hairline p-0 pl-3">
        {steps.map((step, index) => (
          <li className="text-[12.5px] leading-[1.5] text-text-muted [overflow-wrap:anywhere]" key={`${index}:${stepKey(step)}`}>
            {describe(step, "done")}
          </li>
        ))}
      </ol>
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

/** How the reader's decision on the last proposal landed. */
function ReviewOutcomeLine({ outcome }: { outcome: NonNullable<AgentSession["outcome"]> }) {
  if (outcome === "rejected") {
    return <p className="m-0 text-[13px] text-text-muted">Revision rejected.</p>;
  }
  return (
    <p className="m-0 flex items-center gap-2 text-[13px] font-medium text-accent-deep" role="status">
      <CheckIcon className="h-3.5 w-3.5 flex-none" />
      {outcome === "applied" ? "Revision applied." : "Approved. The workspace is being rebuilt."}
    </p>
  );
}

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
        <div className={cx(errorNoticeClass, "grid gap-1")} role="alert">
          <span>{result.final_response?.trim() || "The assistant could not complete that request."}</span>
          {errors.map((detail, index) => (
            <span className="text-[12px] opacity-80 [overflow-wrap:anywhere]" key={`${index}:${detail}`}>
              {detail}
            </span>
          ))}
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <div className={cx(warningNoticeClass, "grid gap-1")} role="status">
          <strong className="font-semibold">{warnings.length === 1 ? "Warning" : "Warnings"}</strong>
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
    return <p className={cx("mb-3.5 whitespace-pre-wrap last:mb-0", className)} {...props} />;
  },
  h1({ node: _node, className, ...props }: MarkdownComponentProps<"h1">) {
    return <h1 className={cx(markdownHeadingClass, "text-[17px]", className)} {...props} />;
  },
  h2({ node: _node, className, ...props }: MarkdownComponentProps<"h2">) {
    return <h2 className={cx(markdownHeadingClass, "text-[15.5px]", className)} {...props} />;
  },
  h3({ node: _node, className, ...props }: MarkdownComponentProps<"h3">) {
    return <h3 className={cx(markdownHeadingClass, "text-[14.5px]", className)} {...props} />;
  },
  h4({ node: _node, className, ...props }: MarkdownComponentProps<"h4">) {
    return <h4 className={cx(markdownHeadingClass, "text-[14px]", className)} {...props} />;
  },
  ul({ node: _node, className, ...props }: MarkdownComponentProps<"ul">) {
    return <ul className={cx("mb-3.5 list-disc pl-5 last:mb-0", className)} {...props} />;
  },
  ol({ node: _node, className, ...props }: MarkdownComponentProps<"ol">) {
    return <ol className={cx("mb-3.5 list-decimal pl-5 last:mb-0", className)} {...props} />;
  },
  li({ node: _node, className, ...props }: MarkdownComponentProps<"li">) {
    return <li className={cx("mb-1.5 pl-1 last:mb-0 marker:text-text-muted", className)} {...props} />;
  },
  a({ node: _node, className, ...props }: MarkdownComponentProps<"a">) {
    return (
      <a
        className={cx("text-accent-deep underline decoration-accent-border underline-offset-2 hover:decoration-accent-deep", className)}
        target="_blank"
        rel="noreferrer"
        {...props}
      />
    );
  },
  blockquote({ node: _node, className, ...props }: MarkdownComponentProps<"blockquote">) {
    return (
      <blockquote
        className={cx("mb-3.5 border-l-2 border-border pl-3 text-text-secondary last:mb-0", className)}
        {...props}
      />
    );
  },
  hr({ node: _node, className, ...props }: MarkdownComponentProps<"hr">) {
    return <hr className={cx("my-5 border-0 border-t border-hairline", className)} {...props} />;
  },
  pre({ node: _node, className, ...props }: MarkdownComponentProps<"pre">) {
    return (
      <pre
        className={cx(
          "mb-3.5 overflow-x-auto rounded-lg border border-hairline bg-surface-subtle p-3.5 text-[13px] leading-[1.55] last:mb-0 [&_code]:bg-transparent [&_code]:p-0",
          className,
        )}
        {...props}
      />
    );
  },
  code({ node: _node, className, ...props }: MarkdownComponentProps<"code">) {
    return (
      <code className={cx("rounded-[5px] bg-surface-subtle px-1.5 py-0.5 font-mono text-[0.9em]", className)} {...props} />
    );
  },
  // Replies compare papers and branches in tables often enough that a bare
  // browser table, with no rules or padding, was unreadable.
  table({ node: _node, className, ...props }: MarkdownComponentProps<"table">) {
    return (
      <div className="mb-3.5 overflow-x-auto last:mb-0">
        <table className={cx("w-full border-collapse text-[13px] leading-[1.5]", className)} {...props} />
      </div>
    );
  },
  th({ node: _node, className, ...props }: MarkdownComponentProps<"th">) {
    return (
      <th
        className={cx("border-b border-border py-2 pr-4 text-left align-bottom font-semibold", className)}
        {...props}
      />
    );
  },
  td({ node: _node, className, ...props }: MarkdownComponentProps<"td">) {
    return <td className={cx("border-b border-hairline py-2 pr-4 align-top", className)} {...props} />;
  },
  // An image in a reply would make the browser fetch whatever address the
  // model wrote, from the reader's machine. The reference is kept as a link
  // the reader can choose to follow.
  img({ node: _node, src, alt }: MarkdownComponentProps<"img">) {
    const href = typeof src === "string" ? src : "";
    if (!href) return null;
    return (
      <a className="text-accent-deep underline underline-offset-2" href={href} target="_blank" rel="noreferrer">
        {alt || href}
      </a>
    );
  },
};

const markdownHeadingClass = "mt-6 mb-2 font-semibold leading-[1.35] text-text-primary first:mt-0";

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
