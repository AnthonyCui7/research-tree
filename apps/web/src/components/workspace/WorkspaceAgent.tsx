import {
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type JSX,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cx } from "../../lib/cx";
import { pluralize } from "../../lib/format";
import {
  compactActionClass,
  errorNoticeClass,
  inlineIconButtonClass,
  warningNoticeClass,
} from "../../lib/controlClasses";
import { agentRunFailed, type AgentSession } from "../../data/useAgentSession";
import { PanelHeader } from "../panel/RightPanel";
import { MenuItem, MenuSection, PopoverMenu, anchorFromEvent, type MenuAnchor } from "../ui/PopoverMenu";
import {
  ArrowRightIcon,
  ChatIcon,
  CheckIcon,
  ChevronDownIcon,
  ClockIcon,
  CloseIcon,
  GlobeIcon,
  PaperIcon,
  PlusIcon,
  SearchIcon,
  SendIcon,
  StepIcon,
  TreeIcon,
} from "../ui/icons";
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
  /** Opens the API keys screen, offered when a turn was refused for want of a key. */
  onOpenApiKeys: () => void;
};

const MODELS = [
  { id: "gpt-5.6-luna", detail: "Recommended" },
  { id: "gpt-5.6-terra", detail: "Smarter" },
  { id: "gpt-5.6-sol", detail: "Smartest" },
] as const;

/** Tallest the composer grows before it scrolls: eight 24px lines under its 14px top padding. */
const COMPOSER_MAX_HEIGHT = 206;

/** The thread's own top padding, kept above a question brought to the top. */
const THREAD_TOP_INSET = 24;

export function WorkspaceAgent({ session, tree, onClose, onOpenApiKeys }: WorkspaceAgentProps) {
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

  const latestQuestionRef = useRef<HTMLDivElement>(null);
  const latestQuestionIndex = conversation.map((item) => item.role).lastIndexOf("user");
  const itemsShown = useRef(conversation.length);

  // The thread follows what is added at its foot, except an answer. It lands
  // whole, so one taller than the view would open on its last lines; the
  // question it answers is brought to the top instead, and the answer is read
  // from its start.
  useEffect(() => {
    const element = conversationRef.current;
    if (!element) return;
    const answered =
      conversation.length > itemsShown.current && conversation.at(-1)?.role === "agent";
    itemsShown.current = conversation.length;
    const bottom = element.scrollHeight - element.clientHeight;
    const question = latestQuestionRef.current;
    const top =
      answered && question
        ? Math.min(
            bottom,
            element.scrollTop +
              question.getBoundingClientRect().top -
              element.getBoundingClientRect().top -
              THREAD_TOP_INSET,
          )
        : bottom;
    element.scrollTo({ top, behavior: "smooth" });
  }, [conversation, busy, pendingReview, result, outcome, steps.length]);

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
              <div
                className="flex justify-end pl-10"
                key={`user-${index}`}
                ref={index === latestQuestionIndex ? latestQuestionRef : undefined}
              >
                <p className="m-0 rounded-3xl bg-surface-subtle px-4 py-2 text-14 leading-6 whitespace-pre-wrap text-text-primary [overflow-wrap:anywhere]">
                  {item.text}
                </p>
              </div>
            ) : (
              <div className="text-14 leading-6 text-text-primary" key={`agent-${index}`}>
                {item.steps?.length ? <StepsTaken steps={item.steps} /> : null}
                {/* A box of its own, so a reply that opens with a heading sets it
                    flush under the steps rather than a heading's margin below. */}
                <div>
                  <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
                    {displayMarkdown(item.text)}
                  </ReactMarkdown>
                </div>
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
            // A button at the foot of the strip is a hard edge, so it sits as
            // far from the bottom as the message's capitals do from the top.
            <div className={cx(errorNoticeClass, "flex items-start gap-3", error.needsKey && "pb-4")} role="alert">
              <span className="min-w-0 flex-1">
                {error.message}
                {error.needsKey ? (
                  <span className="mt-2 block">
                    <button className={compactActionClass} type="button" onClick={onOpenApiKeys}>
                      Add API key
                    </button>
                  </span>
                ) : null}
              </span>
              <button
                className={cx(inlineIconButtonClass, "-mr-1.5 hover:bg-error-border/40")}
                type="button"
                onClick={session.dismissError}
                aria-label="Dismiss assistant error"
              >
                <CloseIcon className="size-3" />
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/* The fade over the conversation's last lines says there is more
          below while it is scrolled up. */}
      <div className="relative flex-none px-6 pt-1 pb-6 before:pointer-events-none before:absolute before:inset-x-0 before:-top-6 before:h-6 before:bg-linear-to-b before:from-transparent before:to-surface">
        <form
          className="mx-auto w-full max-w-[760px] rounded-3xl border border-border bg-surface shadow-composer transition-[border-color] duration-150 focus-within:border-border-strong"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <textarea
            className="block w-full resize-none border-0 bg-transparent px-5 pt-3.5 text-14 leading-6 text-text-primary outline-0 placeholder:text-text-muted"
            ref={composerRef}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={onComposerKeyDown}
            placeholder={tree ? `Ask about ${tree.title}` : "Ask about this workspace"}
            rows={1}
            aria-label="Message the assistant"
          />
          {/* The buttons sit 8px inside the box, which is why it is rounded 24px:
              their own 16px radius plus that gap. The picker's label lines up with
              the text above it, and its chevron, drawn 4px inside its box, ends
              as far from the pill's edge as the label starts. */}
          <div className="flex items-center gap-2 p-2">
            <button
              className="flex h-8 items-center gap-1 rounded-full border-0 bg-transparent pr-2 pl-3 text-13 font-medium text-text-secondary transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary aria-expanded:bg-surface-subtle aria-expanded:text-text-primary"
              type="button"
              aria-haspopup="menu"
              aria-expanded={modelAnchor !== null}
              onClick={(event) => setModelAnchor(anchorFromEvent(event.currentTarget, "left", true))}
              title="Switch model"
            >
              {session.model}
              <ChevronDownIcon className="size-4" />
            </button>
            <button
              className="ml-auto grid h-8 w-8 flex-none place-items-center rounded-full border-0 bg-accent p-0 text-white transition-[background-color] duration-150 enabled:hover:bg-accent-deep disabled:cursor-not-allowed disabled:bg-border-strong"
              type="submit"
              disabled={!message.trim() || busy}
              aria-label="Send message"
              title="Send"
            >
              <SendIcon className="size-4" />
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
    <section className="my-auto">
      <span
        className="mx-auto mb-4 grid h-10 w-10 place-items-center rounded-lg bg-accent-subtle text-accent-deep shadow-[inset_0_0_0_1px_var(--color-accent-border)]"
        aria-hidden="true"
      >
        <ChatIcon className="size-5" />
      </span>
      <h3 className="m-0 text-center text-24 font-semibold text-text-primary text-trim [overflow-wrap:anywhere]">
        {tree ? `Ask about ${tree.title}` : "Ask about this workspace"}
      </h3>
      {tree ? (
        <p className="mt-4 mb-0 text-center text-13 text-text-muted text-trim">
          {pluralize(tree.branchCount, "branch", "branches")} · {pluralize(tree.paperCount, "paper")}
        </p>
      ) : null}
      <div className="mx-auto mt-8 grid max-w-[520px] gap-2">
        {openers.map((opener) => (
          <button
            className="group flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3 text-left transition-[background-color,border-color] duration-150 hover:border-border-strong hover:bg-surface-subtle"
            key={opener.title}
            type="button"
            onClick={() => onUse(opener.prompt)}
          >
            <span
              className="grid h-8 w-8 flex-none place-items-center rounded-md bg-surface-subtle text-text-secondary transition-[background-color,color] duration-150 group-hover:bg-accent-subtle group-hover:text-accent-deep"
              aria-hidden="true"
            >
              {opener.icon}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-14 font-medium text-text-primary">{opener.title}</span>
              <span className="mt-0.5 block text-13 text-text-muted">
                {opener.detail}
              </span>
            </span>
            <ArrowRightIcon className="size-4 flex-none text-text-muted opacity-0 transition-[opacity,transform] duration-150 group-hover:translate-x-0.5 group-hover:opacity-100" />
          </button>
        ))}
      </div>
    </section>
  );
}

type IntroPrompt = { title: string; detail: string; prompt: string; icon: ReactNode };

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
      icon: <SearchIcon className="size-4" />,
    },
    {
      title: "Plan a reading path",
      icon: <StepIcon className="size-4" />,
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
      icon: <TreeIcon className="size-4" />,
      detail: `${first.title} vs. ${second.title}, and where they meet`,
      prompt: `Compare the “${first.title}” and “${second.title}” branches of this workspace, and explain where they meet.`,
    });
  } else {
    openers.push({
      title: "What's missing?",
      icon: <PlusIcon className="size-4" />,
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
  // A step is recorded as it starts, so the newest one is still under way
  // until the next event arrives: it shows once, as what is happening now.
  const done = steps[steps.length - 1] === activity ? steps.slice(0, -1) : steps;
  return (
    <div className="grid gap-2" role="status">
      {done.map((step, index) => (
        <div className="flex items-start gap-2.5 text-13 text-text-muted" key={`${index}:${stepKey(step)}`}>
          <StepGlyph step={step} />
          <span className="min-w-0 [overflow-wrap:anywhere]">{describe(step, "done")}</span>
        </div>
      ))}
      <div className="flex items-start gap-2.5">
        <span
          className="mt-0.5 size-4 flex-none animate-progress-spin rounded-full border-[1.5px] border-accent-subtle border-t-accent"
          aria-hidden="true"
        />
        <span className="min-w-0 text-13 font-medium text-shimmer [overflow-wrap:anywhere]">
          {describe(current, "doing")}
        </span>
      </div>
    </div>
  );
}

/**
 * What a finished reply was built from, folded to one line until opened.
 * Folded, the line is the reply's own header and sits close above it; opened,
 * the steps are a block of their own, a paragraph's space from the reply.
 */
function StepsTaken({ steps }: { steps: AgentStep[] }) {
  return (
    <details className="group mb-1 open:mb-4">
      <summary className="flex w-fit max-w-full cursor-pointer list-none items-center gap-1 text-13 text-text-muted transition-[color] duration-150 hover:text-text-secondary [&::-webkit-details-marker]:hidden">
        <span className="min-w-0 truncate">{summarizeSteps(steps)}</span>
        <ChevronDownIcon className="size-4 flex-none -rotate-90 transition-transform duration-150 group-open:rotate-0" />
      </summary>
      <ol className="m-0 mt-2 grid list-none gap-2 p-0">
        {steps.map((step, index) => (
          <li
            className="flex items-start gap-2.5 text-13 text-text-muted"
            key={`${index}:${stepKey(step)}`}
          >
            <StepGlyph step={step} />
            <span className="min-w-0 [overflow-wrap:anywhere]">{describe(step, "done")}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

/** What kind of step it was, at a glance: a search, a paper, a branch, the web. */
function StepGlyph({ step }: { step: AgentStep }) {
  const glyph = step.kind === "tool" ? TOOL_GLYPH[step.name] : null;
  return (
    <span className="mt-0.5 flex size-4 flex-none items-center justify-center text-text-muted/80" aria-hidden="true">
      {glyph ?? <StepIcon className="size-4" />}
    </span>
  );
}

const TOOL_GLYPH: Record<string, ReactNode> = {
  search_workspace: <SearchIcon className="size-4" />,
  search_semantic_scholar: <SearchIcon className="size-4" />,
  web_search: <GlobeIcon className="size-4" />,
  get_paper: <PaperIcon className="size-4" />,
  get_paper_full_text: <PaperIcon className="size-4" />,
  get_semantic_scholar_paper: <PaperIcon className="size-4" />,
  get_branch: <TreeIcon className="size-4" />,
  get_workspace_overview: <TreeIcon className="size-4" />,
  list_reading_order: <StepIcon className="size-4" />,
  list_workspace_history: <ClockIcon className="size-4" />,
};

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
    return <p className="m-0 text-13 text-text-muted">Revision rejected.</p>;
  }
  return (
    <p className="m-0 flex items-center gap-2 text-13 font-medium text-accent-deep" role="status">
      <CheckIcon className="size-4 flex-none" />
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
            <span className="text-12 opacity-80 [overflow-wrap:anywhere]" key={`${index}:${detail}`}>
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
    return <p className={cx("mb-4 whitespace-pre-wrap last:mb-0", className)} {...props} />;
  },
  h1({ node: _node, className, ...props }: MarkdownComponentProps<"h1">) {
    return <h1 className={cx(markdownHeadingClass, "text-17", className)} {...props} />;
  },
  h2({ node: _node, className, ...props }: MarkdownComponentProps<"h2">) {
    return <h2 className={cx(markdownHeadingClass, "text-15", className)} {...props} />;
  },
  h3({ node: _node, className, ...props }: MarkdownComponentProps<"h3">) {
    return <h3 className={cx(markdownHeadingClass, "text-15", className)} {...props} />;
  },
  h4({ node: _node, className, ...props }: MarkdownComponentProps<"h4">) {
    return <h4 className={cx(markdownHeadingClass, "text-14", className)} {...props} />;
  },
  ul({ node: _node, className, ...props }: MarkdownComponentProps<"ul">) {
    return <ul className={cx("mb-4 list-disc pl-5 last:mb-0", className)} {...props} />;
  },
  ol({ node: _node, className, ...props }: MarkdownComponentProps<"ol">) {
    return <ol className={cx("mb-4 list-decimal pl-5 last:mb-0", className)} {...props} />;
  },
  li({ node: _node, className, ...props }: MarkdownComponentProps<"li">) {
    return <li className={cx("mb-2 pl-1 last:mb-0 marker:text-text-muted", className)} {...props} />;
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
        className={cx("mb-4 border-l-2 border-border pl-3 text-text-secondary last:mb-0", className)}
        {...props}
      />
    );
  },
  hr({ node: _node, className, ...props }: MarkdownComponentProps<"hr">) {
    return <hr className={cx("my-6 border-0 border-t border-hairline", className)} {...props} />;
  },
  // A code block's edge is drawn, and text 24px from an edge looks as far
  // away as the next paragraph's letters do 16px below a line.
  pre({ node: _node, className, ...props }: MarkdownComponentProps<"pre">) {
    return (
      <pre
        className={cx(
          "my-6 overflow-x-auto rounded-md border border-hairline bg-surface-subtle p-4 text-13 first:mt-0 last:mb-0 [&_code]:bg-transparent [&_code]:p-0",
          className,
        )}
        {...props}
      />
    );
  },
  code({ node: _node, className, ...props }: MarkdownComponentProps<"code">) {
    return (
      <code className={cx("rounded-xs bg-surface-subtle px-1.5 py-0.5 font-mono text-[0.9em]", className)} {...props} />
    );
  },
  // Replies compare papers and branches in tables often, so a table gets
  // rules and cell padding to be read as one. Its header starts where a
  // paragraph would, and the rule under its last row is an edge, spaced as
  // a code block's is.
  table({ node: _node, className, ...props }: MarkdownComponentProps<"table">) {
    return (
      <div className="mb-6 overflow-x-auto last:mb-0">
        <table className={cx("w-full border-collapse text-13", className)} {...props} />
      </div>
    );
  },
  th({ node: _node, className, ...props }: MarkdownComponentProps<"th">) {
    return (
      <th
        className={cx("border-b border-border pr-4 pb-2 text-left align-bottom font-semibold", className)}
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

const markdownHeadingClass = "mt-6 mb-2 font-semibold text-text-primary first:mt-0";

function displayMarkdown(text: string): string {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  // Code is shown exactly as written, so nothing inside a fence is rewritten.
  let fenced = false;
  return lines
    .map((line, index) => {
      const trimmed = line.trim();
      if (/^(```|~~~)/.test(trimmed)) {
        fenced = !fenced;
        return line;
      }
      if (fenced || !trimmed) return line;
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
  // Already markdown: a heading, a list item, a quote, a fence, or a table row.
  if (/^(#{1,6}|\d+\.|[-*+]\s|>|```|\|)/.test(line)) return false;
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
