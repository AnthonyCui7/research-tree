import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, errorNotice, type ErrorNotice } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import {
  durationLabel,
  isOpenable,
  isRunActive,
  stageSpans,
  useBuildProgress,
  type BuildStage,
  type StageSpan,
} from "../../lib/pipelineStages";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import type { BuildRun } from "../../data/useBuildRun";
import {
  compactActionClass,
  errorNoticeClass,
  ghostActionClass,
  kickerClass,
  kickerTypeClass,
  plainNoticeClass,
  primaryActionClass,
  secondaryActionClass,
  warningNoticeClass,
} from "../../lib/controlClasses";
import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, CloseIcon, PlusIcon, TreeIcon, WarningIcon } from "../ui/icons";
import { BuildIllustration } from "./BuildIllustration";
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
      <div className="mx-auto flex min-h-full w-full max-w-[680px] flex-col justify-center px-6 pt-14 pb-[12vh]">
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
  const instructionsOpen = instructions !== null;

  // Runs after the instructions box's autoFocus, so returning to this step
  // lands on the topic while opening the box lands in it.
  useEffect(() => {
    topicRef.current?.focus();
  }, []);

  return (
    <div className="animate-interface-center-enter">
      <span
        className="mx-auto grid h-11 w-11 place-items-center rounded-[14px] bg-accent-subtle text-accent-deep shadow-[inset_0_0_0_1px_var(--color-accent-border)]"
        aria-hidden="true"
      >
        <TreeIcon className="h-[22px] w-[22px]" />
      </span>
      <h1 className="mt-6 mb-0 text-center text-[30px] leading-[1.2] font-semibold tracking-[-0.025em] text-text-primary">
        What field do you want to map?
      </h1>
      <p className="mx-auto mt-3 mb-0 max-w-[46ch] text-center text-[15px] leading-[1.6] text-text-secondary">
        Research Tree drafts an editable map of the literature: its branches, key papers, and
        reading paths.
      </p>

      <form
        className="mt-9 rounded-[20px] border border-border bg-surface shadow-composer transition-[border-color,box-shadow] duration-200 focus-within:border-[color-mix(in_srgb,var(--color-accent)_45%,var(--color-border))] focus-within:shadow-[0_0_0_4px_var(--color-accent-wash),var(--shadow-composer)]"
        aria-busy={busy}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <input
          ref={topicRef}
          className="block w-full border-0 bg-transparent px-5 pt-[18px] pb-2 text-[16.5px] text-text-primary outline-0 placeholder:text-text-muted"
          type="text"
          value={topic}
          onChange={(event) => onTopic(event.target.value)}
          placeholder="A research topic, e.g. speculative decoding"
          maxLength={240}
          aria-label="Research topic"
          disabled={busy}
        />
        {instructionsOpen ? (
          <div className="mx-5 animate-settle border-t border-hairline pt-3">
            <label className={kickerClass} htmlFor="build-instructions">
              Instructions
            </label>
            <textarea
              id="build-instructions"
              autoFocus
              className="mt-1.5 block min-h-[76px] w-full resize-none border-0 bg-transparent p-0 text-[14px] leading-[1.6] text-text-primary outline-0 placeholder:text-text-muted"
              value={instructions}
              onChange={(event) => onInstructions(event.target.value)}
              placeholder="e.g. emphasize evaluation methods; leave out hardware-specific serving papers."
              maxLength={2000}
              rows={3}
              disabled={busy}
            />
          </div>
        ) : null}
        <div className="flex items-center gap-2 px-3 pt-2 pb-3">
          <button
            className={cx(
              "flex h-8 items-center gap-1.5 rounded-full border px-3 text-[12.5px] font-medium transition-[background-color,border-color,color] duration-150",
              instructionsOpen
                ? "border-accent-border bg-accent-wash text-accent-deep enabled:hover:bg-accent-subtle"
                : "border-border bg-surface text-text-secondary enabled:hover:bg-surface-subtle enabled:hover:text-text-primary",
            )}
            type="button"
            onClick={() => onInstructions(instructionsOpen ? null : "")}
            aria-expanded={instructionsOpen}
            aria-label={instructionsOpen ? "Remove instructions" : "Add instructions"}
            disabled={busy}
          >
            {instructionsOpen ? <CloseIcon className="h-3 w-3" /> : <PlusIcon className="h-3.5 w-3.5" />}
            Instructions
          </button>
          <button
            className="ml-auto grid h-9 w-9 flex-none place-items-center rounded-full border-0 bg-accent p-0 text-white shadow-[0_1px_2px_rgb(23_102_71/30%)] transition-[background-color,transform] duration-150 enabled:hover:bg-accent-deep enabled:active:scale-95 disabled:cursor-not-allowed disabled:bg-border-strong disabled:shadow-none"
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

      <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
        {EXAMPLE_TOPICS.map((example) => (
          <button
            className="rounded-full border border-border bg-surface px-3.5 py-1.5 text-[12.5px] text-text-secondary transition-[background-color,border-color,color] duration-150 enabled:hover:border-border-strong enabled:hover:bg-surface-subtle enabled:hover:text-text-primary"
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
      <WarningIcon className="h-4 w-4 flex-none" />
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
        <p className="m-0 flex items-center gap-2.5 text-[13px] font-medium text-accent-deep">
          <span className="grid h-6 w-6 place-items-center rounded-full bg-accent-subtle" aria-hidden="true">
            <CheckIcon className="h-3.5 w-3.5" />
          </span>
          Recognized research field
        </p>
      ) : (
        <p className="m-0 flex items-center gap-2.5 text-[13px] font-medium text-warning">
          <span className="grid h-6 w-6 place-items-center rounded-full bg-warning-surface" aria-hidden="true">
            <WarningIcon className="h-3.5 w-3.5" />
          </span>
          Not recognized as a research field
        </p>
      )}
      <h1 className="mt-4 mb-0 text-[30px] leading-[1.2] font-semibold tracking-[-0.025em] text-text-primary [overflow-wrap:anywhere]">
        {recognized ? review.normalized_topic : review.submitted_topic}
      </h1>
      {review.guidance || !recognized ? (
        <p className="mt-3 mb-0 max-w-[60ch] text-[15px] leading-[1.6] text-text-secondary">
          {review.guidance || "Try a specific field, method, benchmark, or research question."}
        </p>
      ) : null}

      {recognized && (review.source_paper || instructions) ? (
        <dl className="mt-7 mb-0 grid gap-4 rounded-2xl border border-hairline bg-surface p-5">
          {review.source_paper ? (
            <ReviewFact label="Linked paper">
              {review.source_paper.title} will anchor the map.
            </ReviewFact>
          ) : null}
          {instructions ? <ReviewFact label="Your instructions">{instructions}</ReviewFact> : null}
        </dl>
      ) : null}

      {existing ? (
        <p className={cx(warningNoticeClass, "mt-6 mb-0 flex items-center gap-2.5")}>
          <WarningIcon className="h-4 w-4 flex-none" />
          You already have a “{existing.title}” workspace for this topic.
        </p>
      ) : null}
      {error ? <StepError error={error} onOpenApiKeys={onOpenApiKeys} /> : null}

      <div className="mt-8 flex items-center gap-2">
        <button
          className={secondaryActionClass}
          type="button"
          onClick={onBack}
          disabled={busy}
          autoFocus={!existing && !(recognized && review.can_create)}
        >
          <ArrowLeftIcon className="h-3.5 w-3.5" />
          Edit topic
        </button>
        {/* The step's one action takes focus, so a topic typed and entered
            is confirmed with a second Enter. */}
        {existing ? (
          <button
            className={cx(primaryActionClass, "ml-auto")}
            type="button"
            onClick={() => onOpenExisting(existing.workspace_id)}
            autoFocus
          >
            Open it instead
            <ArrowRightIcon className="h-3.5 w-3.5" />
          </button>
        ) : recognized && review.can_create ? (
          <button
            className={cx(primaryActionClass, "ml-auto")}
            type="button"
            disabled={busy}
            onClick={onBuild}
            autoFocus
          >
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
      <dd className="m-0 mt-1.5 text-[14px] leading-[1.6] text-text-primary [overflow-wrap:anywhere]">
        {children}
      </dd>
    </div>
  );
}

/* ---------------------------------------------------------------- build --- */

const TIME = new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" });

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
  const spans = stageSpans(run, progress.stages);
  const started = Date.parse(run.started_at ?? "");

  return (
    <div className="animate-interface-center-enter">
      {/* Announced when the stage changes; the clock and percent tick too often. */}
      <p className="sr-only" aria-live="polite">
        {progress.currentLabel ?? ""}
      </p>
      <div className="flex items-end gap-4">
        <div className="min-w-0 flex-1">
          <BuildStatus run={run} />
          <h1 className="mt-2.5 mb-0 text-[26px] leading-[1.25] font-semibold tracking-[-0.02em] text-text-primary [overflow-wrap:anywhere]">
            Mapping {run.topic}
          </h1>
          <p className="mt-1.5 mb-0 text-[13px] text-text-muted tabular-nums">
            {run.status === "queued"
              ? "Waiting for a free worker. It starts when the build ahead of it finishes."
              : Number.isNaN(started)
                ? null
                : `Started ${TIME.format(started)}${active ? ` · ${durationLabel(progress.now - started)} so far` : ""}`}
          </p>
        </div>
        <span className="flex-none text-[28px] leading-none font-semibold tracking-[-0.02em] text-accent tabular-nums">
          {progress.percent}
          <span className="text-[16px] font-medium">%</span>
        </span>
      </div>
      <div
        className="mt-5 h-1.5 overflow-hidden rounded-full bg-track"
        role="progressbar"
        aria-label="Build progress"
        aria-valuenow={progress.percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500 ease-linear"
          style={{ width: `${progress.percent}%` }}
        />
      </div>

      <div className="tree-grid-bg mt-6 overflow-hidden rounded-2xl border border-hairline px-5 py-5">
        <BuildIllustration stages={progress.stages} />
      </div>

      <ol className="m-0 mt-6 grid list-none p-0">
        {progress.stages.map((stage, index) => (
          <StageRow
            key={stage.id}
            stage={stage}
            last={index === progress.stages.length - 1}
            took={stageDuration(spans[stage.id], progress.now)}
          />
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
              className={cx(ghostActionClass, "-ml-3")}
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

/** Where the build stands, in the page's kicker. */
function BuildStatus({ run }: { run: PipelineRun }) {
  if (run.status === "failed") {
    return <span className={cx(kickerTypeClass, "text-error")}>Build failed</span>;
  }
  if (run.status === "cancelled") {
    return <span className={kickerClass}>Build cancelled</span>;
  }
  return (
    <span className={cx(kickerTypeClass, "flex items-center gap-2 text-accent-deep")}>
      <span className="relative flex h-2 w-2" aria-hidden="true">
        <span className="absolute inset-0 animate-breathe rounded-full bg-accent" />
        <span className="relative h-2 w-2 rounded-full bg-accent" />
      </span>
      {run.status === "queued" ? "Queued" : "Building"}
    </span>
  );
}

function StageRow({ stage, last, took }: { stage: BuildStage; last: boolean; took: string | null }) {
  return (
    <li className="relative flex items-center gap-3 py-[9px]">
      {last ? null : (
        <span
          className={cx(
            "absolute top-[29px] bottom-[-9px] left-[8.5px] w-px",
            stage.state === "done" ? "bg-accent-border" : "bg-hairline",
          )}
          aria-hidden="true"
        />
      )}
      <StageMark state={stage.state} />
      <span
        className={cx(
          "min-w-0 flex-1 text-[14px]",
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
      {took ? (
        <span
          className={cx(
            "flex-none text-[12.5px] tabular-nums",
            stage.state === "current" ? "text-text-secondary" : "text-text-muted",
          )}
        >
          {took}
        </span>
      ) : null}
    </li>
  );
}

function stageDuration(span: StageSpan | undefined, now: number): string | null {
  if (!span) return null;
  return durationLabel((span.end ?? now) - span.start);
}

function StageMark({ state }: { state: BuildStage["state"] }) {
  if (state === "done") {
    return (
      <span
        className="relative grid h-[18px] w-[18px] flex-none place-items-center rounded-full bg-accent text-white"
        aria-hidden="true"
      >
        <CheckIcon className="h-[11px] w-[11px]" />
      </span>
    );
  }
  if (state === "current") {
    return (
      <span
        className="relative h-[18px] w-[18px] flex-none animate-progress-spin rounded-full border-2 border-accent-subtle border-t-accent bg-surface"
        aria-hidden="true"
      />
    );
  }
  if (state === "failed") {
    return (
      <span
        className="relative grid h-[18px] w-[18px] flex-none place-items-center rounded-full bg-error text-white"
        aria-hidden="true"
      >
        <CloseIcon className="h-[10px] w-[10px]" />
      </span>
    );
  }
  return (
    <span
      className="relative h-[18px] w-[18px] flex-none rounded-full border-2 border-hairline bg-surface"
      aria-hidden="true"
    />
  );
}
