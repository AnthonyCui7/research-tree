import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, errorNotice, type ErrorNotice } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import { isOpenable, isRunActive, useBuildProgress } from "../../lib/pipelineStages";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import type { BuildRun } from "../../data/useBuildRun";
import {
  chipClass,
  compactActionClass,
  errorNoticeClass,
  ghostActionClass,
  kickerClass,
  plainNoticeClass,
  primaryActionClass,
  secondaryActionClass,
  warningNoticeClass,
} from "../../lib/controlClasses";
import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, WarningIcon } from "../ui/icons";
import type { PipelineRun, TopicReview } from "../../lib/types";

type NewWorkspacePageProps = {
  build: BuildRun;
  onOpenWorkspace: (workspaceId: string) => void;
  /** Opens the API keys screen, offered when a step was refused for want of a key. */
  onOpenApiKeys: () => void;
};

const EXAMPLE_TOPICS = ["Speculative decoding", "Protein language models", "Mechanistic interpretability"];

/**
 * Starting a workspace, in three steps on one page: name a topic, confirm how
 * it was understood, then watch the build. While a build is being watched the
 * page shows it, since only one runs at a time from here.
 */
export function NewWorkspacePage({ build, onOpenWorkspace, onOpenApiKeys }: NewWorkspacePageProps) {
  const [topic, setTopic] = useState("");
  /** The reader's optional steer, carried into the construction prompt. */
  const [instructions, setInstructions] = useState<string | null>(null);
  const [review, setReview] = useState<TopicReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorNotice | null>(null);

  async function checkTopic() {
    if (!topic.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setReview(await repositoryWorkspaceGateway.reviewTopic(topic));
    } catch (requestError) {
      setError(errorNotice(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function startBuild(approved: TopicReview) {
    if (!approved.can_create || !approved.topic_review_token || busy) return;
    setBusy(true);
    setError(null);
    try {
      await build.start(approved.normalized_topic, approved.topic_review_token, instructions ?? "");
      setReview(null);
      setTopic("");
      setInstructions(null);
    } catch (requestError) {
      setError(errorNotice(requestError));
      // The approval lasts fifteen minutes. Past that, the only way forward
      // is to review the topic again, so the page goes back to that step
      // with the reason rather than leaving a dead "Build workspace" button.
      if (requestError instanceof ApiError && requestError.code === "topic_review_expired") {
        setReview(null);
      }
    } finally {
      setBusy(false);
    }
  }

  let content: ReactNode;
  if (build.run) {
    const run = build.run;
    content = (
      <BuildProgress
        run={run}
        error={build.error}
        onCancel={build.cancel}
        onOpen={() => onOpenWorkspace(run.workspace_id)}
        onStartOver={() => {
          setTopic(run.topic);
          build.dismiss();
        }}
      />
    );
  } else if (review) {
    content = (
      <ReviewStep
        review={review}
        instructions={instructions?.trim() ?? ""}
        busy={busy}
        error={error}
        onOpenApiKeys={onOpenApiKeys}
        onBack={() => {
          setError(null);
          setReview(null);
        }}
        onBuild={() => void startBuild(review)}
        onOpenExisting={onOpenWorkspace}
      />
    );
  } else {
    content = (
      <TopicStep
        topic={topic}
        onTopic={setTopic}
        instructions={instructions}
        onInstructions={setInstructions}
        busy={busy}
        error={error}
        onOpenApiKeys={onOpenApiKeys}
        onSubmit={() => void checkTopic()}
      />
    );
  }

  return (
    <div className="scrollbar-rt h-full overflow-y-auto bg-surface">
      <div className="mx-auto flex min-h-full w-full max-w-[660px] flex-col justify-center px-6 pt-12 pb-[14vh]">
        {content}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- topic --- */

function TopicStep({
  topic,
  onTopic,
  instructions,
  onInstructions,
  busy,
  error,
  onOpenApiKeys,
  onSubmit,
}: {
  topic: string;
  onTopic: (topic: string) => void;
  instructions: string | null;
  onInstructions: (instructions: string | null) => void;
  busy: boolean;
  error: ErrorNotice | null;
  onOpenApiKeys: () => void;
  onSubmit: () => void;
}) {
  const topicRef = useRef<HTMLInputElement>(null);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);
  const instructionsOpen = instructions !== null;

  useEffect(() => {
    topicRef.current?.focus();
  }, []);

  useEffect(() => {
    if (instructionsOpen) instructionsRef.current?.focus();
  }, [instructionsOpen]);

  return (
    <div className="animate-interface-center-enter">
      <h1 className="m-0 text-center text-[28px] leading-[1.25] font-semibold tracking-[-0.02em] text-text-primary">
        What field do you want to map?
      </h1>
      <p className="mx-auto mt-2.5 mb-0 max-w-[50ch] text-center text-[14px] leading-[1.6] text-text-secondary">
        Research Tree drafts an editable map of the literature: its branches, key papers, and
        reading paths.
      </p>

      <form
        className="mt-8 rounded-2xl border border-border bg-surface shadow-composer transition-[border-color] duration-150 focus-within:border-border-strong"
        aria-busy={busy}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <input
          ref={topicRef}
          className="block w-full border-0 bg-transparent px-4 pt-4 pb-2 text-[16px] text-text-primary outline-0 placeholder:text-text-muted"
          type="text"
          value={topic}
          onChange={(event) => onTopic(event.target.value)}
          placeholder="A research topic, e.g. speculative decoding"
          maxLength={240}
          aria-label="Research topic"
          disabled={busy}
        />
        {instructionsOpen ? (
          <textarea
            ref={instructionsRef}
            className="block min-h-[72px] w-full resize-none border-0 bg-transparent px-4 pt-1 pb-2 text-[14px] leading-[1.55] text-text-primary outline-0 placeholder:text-text-muted"
            value={instructions}
            onChange={(event) => onInstructions(event.target.value)}
            placeholder="Instructions for the build, e.g. emphasize evaluation methods; leave out hardware-specific serving papers."
            maxLength={2000}
            rows={3}
            aria-label="Instructions for the build"
            disabled={busy}
          />
        ) : null}
        <div className="flex items-center gap-2 px-3 pt-1 pb-3">
          <button
            className="flex h-8 items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-[12.5px] font-medium text-text-secondary transition-[background-color,color] duration-150 enabled:hover:bg-surface-subtle enabled:hover:text-text-primary"
            type="button"
            onClick={() => onInstructions(instructionsOpen ? null : "")}
            aria-expanded={instructionsOpen}
            disabled={busy}
          >
            {instructionsOpen ? "Remove instructions" : "Add instructions"}
          </button>
          <button
            className="ml-auto grid h-9 w-9 flex-none place-items-center rounded-full border-0 bg-accent p-0 text-white transition-[background-color] duration-150 enabled:hover:bg-accent-deep disabled:cursor-not-allowed disabled:bg-border-strong"
            type="submit"
            disabled={busy || !topic.trim()}
            aria-label="Review topic"
            title="Review topic"
          >
            {busy ? (
              <span
                className="h-4 w-4 animate-progress-spin rounded-full border-2 border-white/40 border-t-white"
                aria-hidden="true"
              />
            ) : (
              <ArrowRightIcon className="h-4 w-4" />
            )}
          </button>
        </div>
      </form>

      <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
        {EXAMPLE_TOPICS.map((example) => (
          <button
            className={cx(
              chipClass,
              "border border-transparent py-1 transition-[background-color,color,border-color] duration-150 hover:border-border hover:bg-surface hover:text-text-primary",
            )}
            key={example}
            type="button"
            onClick={() => {
              onTopic(example);
              topicRef.current?.focus();
            }}
            disabled={busy}
          >
            {example}
          </button>
        ))}
      </div>

      {error ? <StepError error={error} onOpenApiKeys={onOpenApiKeys} /> : null}
    </div>
  );
}

/** A refused step, with the way past it when that is an API key. */
function StepError({ error, onOpenApiKeys }: { error: ErrorNotice; onOpenApiKeys: () => void }) {
  return (
    <div className={cx(errorNoticeClass, "mt-6 flex items-center gap-3")} role="alert">
      <span className="min-w-0 flex-1">{error.message}</span>
      {error.needsKey ? (
        <button className={cx(compactActionClass, "flex-none")} type="button" onClick={onOpenApiKeys}>
          Add API key
        </button>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------- review --- */

function ReviewStep({
  review,
  instructions,
  busy,
  error,
  onOpenApiKeys,
  onBack,
  onBuild,
  onOpenExisting,
}: {
  review: TopicReview;
  instructions: string;
  busy: boolean;
  error: ErrorNotice | null;
  onOpenApiKeys: () => void;
  onBack: () => void;
  onBuild: () => void;
  onOpenExisting: (workspaceId: string) => void;
}) {
  const recognized = review.is_research_topic;
  const existing = recognized ? review.existing_workspace : null;
  return (
    <div className="animate-interface-center-enter">
      {recognized ? (
        <p className="m-0 flex items-center gap-2 text-[13px] font-medium text-accent-deep">
          <span
            className="grid h-5 w-5 place-items-center rounded-full bg-accent-subtle"
            aria-hidden="true"
          >
            <CheckIcon className="h-2.5 w-2.5" />
          </span>
          Recognized research field
        </p>
      ) : (
        <p className="m-0 flex items-center gap-2 text-[13px] font-medium text-warning">
          <WarningIcon className="h-4 w-4" />
          Not recognized as a research field
        </p>
      )}
      <h1 className="mt-3 mb-0 text-[28px] leading-[1.25] font-semibold tracking-[-0.02em] text-text-primary [overflow-wrap:anywhere]">
        {recognized ? review.normalized_topic : review.submitted_topic}
      </h1>
      {review.guidance || !recognized ? (
        <p className="mt-2.5 mb-0 max-w-[62ch] text-[14px] leading-[1.6] text-text-secondary">
          {review.guidance || "Try a specific field, method, benchmark, or research question."}
        </p>
      ) : null}

      {recognized && (review.source_paper || instructions) ? (
        <dl className="mt-7 mb-0 grid gap-4 border-t border-hairline pt-5">
          {review.source_paper ? (
            <ReviewFact label="Linked paper">
              {review.source_paper.title} will anchor the map.
            </ReviewFact>
          ) : null}
          {instructions ? <ReviewFact label="Your instructions">{instructions}</ReviewFact> : null}
        </dl>
      ) : null}

      {existing ? (
        <p className={cx(warningNoticeClass, "mt-6 mb-0")}>
          You already have a “{existing.title}” workspace for this topic.
        </p>
      ) : null}
      {error ? <StepError error={error} onOpenApiKeys={onOpenApiKeys} /> : null}

      <div className="mt-8 flex items-center gap-2">
        <button className={secondaryActionClass} type="button" onClick={onBack} disabled={busy}>
          <ArrowLeftIcon className="h-3.5 w-3.5" />
          Edit topic
        </button>
        {existing ? (
          <button
            className={cx(primaryActionClass, "ml-auto")}
            type="button"
            onClick={() => onOpenExisting(existing.workspace_id)}
          >
            Open it instead
          </button>
        ) : recognized && review.can_create ? (
          <button className={cx(primaryActionClass, "ml-auto")} type="button" disabled={busy} onClick={onBuild}>
            {busy ? "Starting…" : "Build workspace"}
            {busy ? null : <ArrowRightIcon className="h-3.5 w-3.5" />}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function ReviewFact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className={kickerClass}>{label}</dt>
      <dd className="m-0 mt-1.5 max-w-[62ch] text-[14px] leading-[1.6] text-text-primary [overflow-wrap:anywhere]">
        {children}
      </dd>
    </div>
  );
}

/* ---------------------------------------------------------------- build --- */

function BuildProgress({
  run,
  error,
  onCancel,
  onOpen,
  onStartOver,
}: {
  run: PipelineRun;
  error: string | null;
  onCancel: () => Promise<void>;
  onOpen: () => void;
  onStartOver: () => void;
}) {
  const progress = useBuildProgress(run);
  const [canceling, setCanceling] = useState(false);
  const warnings = (run.warnings ?? []).map((warning) => warning.trim()).filter(Boolean);
  const active = isRunActive(run);

  return (
    <div className="animate-interface-center-enter" aria-live="polite">
      <div className="flex items-baseline gap-4">
        <h1 className="m-0 min-w-0 flex-1 text-[24px] leading-[1.3] font-semibold tracking-[-0.02em] text-text-primary [overflow-wrap:anywhere]">
          Mapping {run.topic}
        </h1>
        <span className="flex-none text-[15px] font-semibold text-accent tabular-nums">
          {progress.percent}%
        </span>
      </div>
      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-track" aria-hidden="true">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500 ease-linear"
          style={{ width: `${progress.percent}%` }}
        />
      </div>
      {run.status === "queued" ? (
        <p className="mt-3 mb-0 text-[13px] leading-[1.5] text-text-secondary">
          Waiting for a free worker. It starts when the build ahead of it finishes.
        </p>
      ) : null}

      <ol className="m-0 mt-7 grid list-none gap-3.5 p-0">
        {progress.stages.map((stage) => (
          <li className="flex items-center gap-3" key={stage.id}>
            <StageMark state={stage.state} />
            <span
              className={cx(
                "text-[14px]",
                stage.state === "waiting"
                  ? "text-text-muted"
                  : stage.state === "failed"
                    ? "text-error"
                    : stage.state === "current"
                      ? "font-medium text-text-primary"
                      : "text-text-secondary",
              )}
            >
              {stage.label}
            </span>
          </li>
        ))}
      </ol>

      {warnings.length > 0 ? (
        <div className={cx(warningNoticeClass, "mt-6 grid gap-1")}>
          <strong className="font-semibold">{warnings.length === 1 ? "Warning" : "Warnings"}</strong>
          {warnings.map((warning, index) => (
            <span className="[overflow-wrap:anywhere]" key={`${index}:${warning}`}>
              {warning}
            </span>
          ))}
        </div>
      ) : null}
      {run.status === "failed" ? (
        <p className={cx(errorNoticeClass, "mt-6 mb-0")} role="alert">
          {run.error || "The workspace could not be built."}
        </p>
      ) : null}
      {run.status === "cancelled" ? (
        <p className={cx(plainNoticeClass, "mt-6 mb-0")}>Build cancelled.</p>
      ) : null}
      {error ? (
        <p className={cx(errorNoticeClass, "mt-6 mb-0")} role="alert">
          {error}
        </p>
      ) : null}

      <div className="mt-8 flex items-center gap-2">
        {active ? (
          <>
            <button
              className={cx(ghostActionClass, "-ml-3 enabled:hover:text-error")}
              type="button"
              disabled={canceling}
              onClick={() => {
                setCanceling(true);
                void onCancel().finally(() => setCanceling(false));
              }}
            >
              {canceling ? "Cancelling…" : "Cancel build"}
            </button>
            {isOpenable(run) ? (
              <button className={cx(primaryActionClass, "ml-auto")} type="button" onClick={onOpen}>
                Open workspace
                <ArrowRightIcon className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </>
        ) : (
          <button className={secondaryActionClass} type="button" onClick={onStartOver}>
            Start over
          </button>
        )}
      </div>
    </div>
  );
}

function StageMark({ state }: { state: "done" | "current" | "waiting" | "failed" }) {
  if (state === "done") {
    return (
      <span
        className="grid h-[18px] w-[18px] flex-none place-items-center rounded-full bg-accent-subtle text-accent-deep"
        aria-hidden="true"
      >
        <CheckIcon className="h-[9px] w-[9px]" />
      </span>
    );
  }
  if (state === "current") {
    return (
      <span
        className="h-[18px] w-[18px] flex-none animate-progress-spin rounded-full border-2 border-track border-t-accent"
        aria-hidden="true"
      />
    );
  }
  if (state === "failed") {
    return (
      <span
        className="h-[18px] w-[18px] flex-none rounded-full border-2 border-error bg-error-surface"
        aria-hidden="true"
      />
    );
  }
  return <span className="h-[18px] w-[18px] flex-none rounded-full border-2 border-track" aria-hidden="true" />;
}
