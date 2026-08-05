import { useEffect, useRef, useState } from "react";
import { messageFrom } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS } from "../../lib/animation";
import { pipelineRunEventsUrl, repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { isOpenable, useBuildProgress } from "../../lib/pipelineStages";
import {
  exampleChipClass,
  ghostActionClass,
  primaryActionClass,
  secondaryActionClass,
  textInputClass,
  tintedActionClass,
} from "../../lib/controlClasses";
import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, CloseIcon } from "../ui/icons";
import type { PipelineRun, TopicReview } from "../../lib/types";

type WorkspaceCreatorProps = {
  open: boolean;
  initialTopic?: string;
  onClose: () => void;
  onCreated: (workspaceId: string, run: PipelineRun) => Promise<void>;
  onOpenExisting: (workspaceId: string) => void;
  activeRun: PipelineRun | null;
  onRunStarted: (run: PipelineRun) => void;
  onRunFinished: (runId: string) => void;
};

const EXAMPLE_TOPICS = ["Speculative decoding", "Protein language models", "Mechanistic interpretability"];

export function WorkspaceCreator({
  open,
  initialTopic = "",
  onClose,
  onCreated,
  onOpenExisting,
  activeRun,
  onRunStarted,
  onRunFinished,
}: WorkspaceCreatorProps) {
  const [topic, setTopic] = useState(initialTopic);
  /** The reader's optional steer, carried into the construction prompt. */
  const [instructions, setInstructions] = useState("");
  const [review, setReview] = useState<TopicReview | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [present, setPresent] = useState(open);
  const [closing, setClosing] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const topicInputRef = useRef<HTMLInputElement>(null);
  const readyRunIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (open) {
      setPresent(true);
      setClosing(false);
      return;
    }
    if (!present) return;
    setClosing(true);
    const timer = window.setTimeout(() => {
      dialogRef.current?.close();
      setPresent(false);
    }, DIALOG_EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [open, present]);

  useEffect(() => {
    if (present && dialogRef.current && !dialogRef.current.open) {
      dialogRef.current.showModal();
    }
  }, [present]);

  useEffect(() => {
    if (!open) return;
    setTopic((current) => (current ? current : initialTopic));
  }, [initialTopic, open]);

  useEffect(() => {
    if (!open || review || run) return;
    const frame = window.requestAnimationFrame(() => topicInputRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open, review, run]);

  useEffect(() => {
    if (activeRun && activeRun.run_id !== run?.run_id) {
      setRun(activeRun);
      readyRunIdRef.current = null;
    }
  }, [activeRun, run?.run_id]);

  useEffect(() => {
    if (!run) {
      return;
    }
    const runId = run.run_id;
    const events = new EventSource(pipelineRunEventsUrl(runId));
    events.addEventListener("pipeline_run_updated", (event) => {
      const next = parsePipelineRun((event as MessageEvent<string>).data);
      if (!next) {
        setError("A build update could not be read. The build itself continues on the server.");
        return;
      }
      if (isTerminalRunStatus(next.status)) {
        // The server stops streaming once a run leaves queued/running. Without
        // this close EventSource reconnects every few seconds and replays the
        // terminal update — and every replay re-runs the handlers below.
        events.close();
      }
      setRun(next);
      onRunStarted(next);
      if (["completed", "completed_with_warnings"].includes(next.status)) {
        void onCreated(next.workspace_id, next)
          .then(() => {
            onRunFinished(next.run_id);
            readyRunIdRef.current = null;
            setTopic("");
            setReview(null);
            setRun(null);
            setError(null);
            setCanceling(false);
          })
          .catch((requestError: unknown) => setError(messageFrom(requestError)));
        return;
      }
      if (next.status === "failed") {
        if (readyRunIdRef.current === next.run_id || isOpenable(next)) {
          void onCreated(next.workspace_id, next)
            .then(() => {
              onRunFinished(next.run_id);
              readyRunIdRef.current = null;
              setTopic("");
              setReview(null);
              setRun(null);
              setCanceling(false);
            })
            .catch((requestError: unknown) => setError(messageFrom(requestError)));
        }
        return;
      }
      if (isOpenable(next) && readyRunIdRef.current !== next.run_id) {
        readyRunIdRef.current = next.run_id;
        void onCreated(next.workspace_id, next).catch((requestError: unknown) => {
          readyRunIdRef.current = null;
          setError(messageFrom(requestError));
        });
      }
    });
    // The server says when it is done on purpose; a backend that does not send
    // this still closes on the terminal status above.
    events.addEventListener("stream_complete", () => events.close());
    // EventSource reconnects automatically after transient network failures.
    // Treating every reconnect as a failed build leaves a stale error onscreen.
    events.onerror = () => {};
    return () => events.close();
  }, [onCreated, onRunFinished, onRunStarted, run?.run_id]);

  if (!present) {
    return null;
  }

  const step = run ? 2 : review ? 1 : 0;

  async function checkTopic() {
    if (!topic.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setReview(await repositoryWorkspaceGateway.reviewTopic(topic));
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function createWorkspace() {
    if (!review?.can_create || !review.topic_review_token) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await repositoryWorkspaceGateway.createWorkspace(
        review.normalized_topic,
        review.topic_review_token,
        instructions,
      );
      readyRunIdRef.current = null;
      setRun(next);
      onRunStarted(next);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function cancelRun() {
    if (!run || !["queued", "running"].includes(run.status)) {
      return;
    }
    setCanceling(true);
    setError(null);
    try {
      const next = await repositoryWorkspaceGateway.cancelPipelineRun(run.run_id);
      setRun(null);
      onRunStarted(next);
      onRunFinished(next.run_id);
      setTopic("");
      setReview(null);
      onClose();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setCanceling(false);
    }
  }

  function startOver() {
    if (run) {
      onRunFinished(run.run_id);
    }
    readyRunIdRef.current = null;
    setRun(null);
    setReview(null);
    setError(null);
    setCanceling(false);
  }

  function close() {
    if (run && ["queued", "running"].includes(run.status)) {
      // The build keeps going on the server; the sidebar carries its progress.
      onClose();
      return;
    }
    if (run) {
      onRunFinished(run.run_id);
    }
    setTopic("");
    setInstructions("");
    setReview(null);
    setRun(null);
    setError(null);
    onClose();
  }

  return (
    <dialog
      ref={dialogRef}
      className={cx(
        "fixed inset-0 z-creator m-0 flex h-full max-h-none w-full max-w-none items-center justify-center border-0 bg-transparent p-6 [&::backdrop]:bg-[rgb(31_35_40_/_28%)]",
        closing
          ? "[&::backdrop]:animate-backdrop-exit"
          : "[&::backdrop]:animate-backdrop-enter",
      )}
      aria-labelledby="creator-title"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <section
        className={cx(
          "flex max-h-[min(640px,calc(100vh-48px))] w-[540px] max-w-full flex-col overflow-hidden rounded-[14px] bg-surface shadow-dialog",
          closing ? "animate-interface-center-exit" : "animate-interface-center-enter",
        )}
      >
        <header className="flex flex-none items-center gap-2 border-b border-hairline px-5 py-3.5">
          <h2 className="m-0 flex-1 text-[15px] font-bold tracking-[-0.01em] text-text-primary" id="creator-title">
            New workspace
          </h2>
          <button
            className="grid h-[26px] w-[26px] flex-none place-items-center rounded-[6px] border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary"
            type="button"
            onClick={close}
            aria-label="Close new workspace"
            title="Close"
          >
            <CloseIcon className="h-3 w-3" />
          </button>
        </header>

        <Stepper step={step} />

        {step === 0 ? (
          <>
            <form
              className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-5 pt-[18px] pb-5"
              id="creator-topic-form"
              onSubmit={(event) => {
                event.preventDefault();
                void checkTopic();
              }}
            >
              <h3 className="m-0 text-[22px] font-bold tracking-[-0.02em] text-text-primary">
                What field do you want to map?
              </h3>
              <label className="mt-[18px] block text-[13.5px] font-bold text-text-primary" htmlFor="creator-topic">
                Topic
              </label>
              <input
                className={cx(textInputClass, "mt-2")}
                id="creator-topic"
                ref={topicInputRef}
                type="text"
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="e.g. Speculative decoding"
                maxLength={240}
              />
              <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] text-text-muted">Try:</span>
                {EXAMPLE_TOPICS.map((example) => (
                  <button
                    className={exampleChipClass}
                    key={example}
                    type="button"
                    onClick={() => {
                      setTopic(example);
                      topicInputRef.current?.focus();
                    }}
                  >
                    {example}
                  </button>
                ))}
              </div>

              <label
                className="mt-[22px] flex items-baseline gap-1.5 text-[13.5px] font-bold text-text-primary"
                htmlFor="creator-instructions"
              >
                Instructions
                <span className="text-[12.5px] font-normal text-text-muted">optional</span>
              </label>
              <p className="mt-[3px] mb-0 text-[12.5px] leading-[1.55] text-text-secondary">
                Steer construction: what to emphasize, exclude, or anchor on.
              </p>
              <textarea
                className={cx(textInputClass, "mt-2 min-h-[92px] resize-y")}
                id="creator-instructions"
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                placeholder="e.g. Emphasize evaluation methods. Exclude hardware-specific serving papers."
                maxLength={2000}
                rows={3}
              />
              {error ? <InlineError message={error} /> : null}
            </form>
            <Footer>
              <button
                className={cx(primaryActionClass, "ml-auto")}
                type="submit"
                form="creator-topic-form"
                disabled={busy || !topic.trim()}
              >
                {busy ? "Checking…" : "Review topic"}
                {busy ? null : <ArrowRightIcon className="h-3 w-3" />}
              </button>
            </Footer>
          </>
        ) : null}

        {step === 1 && review ? (
          <>
            <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-5 pt-[18px] pb-5">
              <ReviewBody review={review} instructions={instructions} />
              {error ? <InlineError message={error} /> : null}
            </div>
            <Footer>
              <button className={secondaryActionClass} type="button" onClick={() => setReview(null)}>
                <ArrowLeftIcon className="h-3 w-3" />
                Back
              </button>
              {review.existing_workspace ? (
                <button
                  className={cx(primaryActionClass, "ml-auto")}
                  type="button"
                  onClick={() => {
                    onOpenExisting(review.existing_workspace!.workspace_id);
                    close();
                  }}
                >
                  Open it instead
                </button>
              ) : review.can_create ? (
                <button
                  className={cx(primaryActionClass, "ml-auto")}
                  type="button"
                  disabled={busy}
                  onClick={() => void createWorkspace()}
                >
                  {busy ? "Starting…" : "Build workspace"}
                </button>
              ) : null}
            </Footer>
          </>
        ) : null}

        {step === 2 && run ? (
          <>
            <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-5 pt-[22px] pb-5" aria-live="polite">
              <BuildBody run={run} />
              {error ? <InlineError message={error} /> : null}
            </div>
            <Footer>
              {["queued", "running"].includes(run.status) ? (
                <>
                  <button
                    className={cx(ghostActionClass, "hover:text-error")}
                    type="button"
                    onClick={() => void cancelRun()}
                    disabled={canceling}
                  >
                    {canceling ? "Cancelling…" : "Cancel build"}
                  </button>
                  {isOpenable(run) ? (
                    <button className={cx(tintedActionClass, "ml-auto")} type="button" onClick={close}>
                      Open now · summaries still filling in
                      <ArrowRightIcon className="h-3 w-3" />
                    </button>
                  ) : null}
                </>
              ) : (
                <button className={cx(secondaryActionClass, "ml-auto")} type="button" onClick={startOver}>
                  Start over
                </button>
              )}
            </Footer>
          </>
        ) : null}
      </section>
    </dialog>
  );
}

/* -------------------------------------------------------------- stepper --- */

const STEP_LABELS = ["Topic", "Confirm", "Build"];

/**
 * The three steps span the dialog, connectors taking whatever width is left, so
 * the run of steps reads as one bar across the top rather than a cluster on the
 * left. The first and last steps sit on the content margins.
 */
function Stepper({ step }: { step: number }) {
  return (
    <div className="flex flex-none items-center px-5 pt-4 pb-1" aria-hidden="true">
      {STEP_LABELS.map((label, index) => (
        <div className="flex min-w-0 flex-1 items-center first:flex-none" key={label}>
          {index > 0 ? (
            <span
              className={cx(
                "mx-3 h-px min-w-3 flex-1",
                step >= index ? "bg-accent-border" : "bg-hairline",
              )}
            />
          ) : null}
          <span className="flex flex-none items-center gap-2">
            <span
              className={cx(
                "grid h-[21px] w-[21px] flex-none place-items-center rounded-full border-[1.5px] text-[10.5px] font-bold",
                step > index
                  ? "border-accent-border bg-accent-subtle text-accent-deep"
                  : step === index
                    ? "border-accent bg-accent text-white"
                    : "border-border bg-surface text-text-muted",
              )}
            >
              {step > index ? <CheckIcon className="h-2.5 w-2.5" /> : index + 1}
            </span>
            <span
              className={cx(
                "text-[12.5px] font-semibold whitespace-nowrap",
                step > index ? "text-accent-deep" : step === index ? "text-text-primary" : "text-text-muted",
              )}
            >
              {label}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

function Footer({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-none items-center gap-2 border-t border-hairline bg-surface-muted px-5 py-3.5">
      {children}
    </div>
  );
}

/* --------------------------------------------------------------- review --- */

function ReviewBody({ review, instructions }: { review: TopicReview; instructions: string }) {
  const steer = instructions.trim();
  if (!review.is_research_topic) {
    return (
      <>
        <div className="flex items-center gap-2.5 rounded-lg border border-warning-border bg-warning-surface px-3.5 py-3 text-xs leading-[1.55] text-warning">
          Not recognized as a research field.
        </div>
        <h3 className="mt-3.5 mb-0 text-[21px] font-bold tracking-[-0.015em] text-text-primary [overflow-wrap:anywhere]">
          {review.submitted_topic}
        </h3>
        <p className="mt-2 mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.62] text-text-secondary">
          {review.guidance || "Try a specific field, method, benchmark, or research question."}
        </p>
      </>
    );
  }

  return (
    <>
      <div className="flex items-center gap-[9px]">
        <span className="grid h-[22px] w-[22px] flex-none place-items-center rounded-full bg-accent-subtle text-accent-deep" aria-hidden="true">
          <CheckIcon className="h-[11px] w-[11px]" />
        </span>
        <span className="text-[13px] font-semibold text-accent-deep">Recognized research field</span>
      </div>
      <h3 className="mt-3 mb-0 text-[26px] font-bold tracking-[-0.02em] text-text-primary [overflow-wrap:anywhere]">
        {review.normalized_topic}
      </h3>
      {review.guidance ? (
        <p className="mt-2 mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.62] text-text-secondary">
          {review.guidance}
        </p>
      ) : null}
      <div className="mt-5 border-t border-hairline-soft pt-4">
        <ReviewFact label="Scope">
          Core methods and their history, as branches and reading paths.
        </ReviewFact>
        {/* Only shown when a link was pasted: otherwise it is a row that always
            says nothing was found. */}
        {review.source_paper ? (
          <ReviewFact label="Linked paper">
            {review.source_paper.title} will anchor the map.
          </ReviewFact>
        ) : null}
        {steer ? <ReviewFact label="Your instructions">{steer}</ReviewFact> : null}
      </div>
      {review.existing_workspace ? (
        <div className="mt-4 rounded-lg border border-warning-border bg-warning-surface px-3.5 py-3 text-xs leading-[1.55] text-warning">
          You already have a “{review.existing_workspace.title}” workspace for this topic.
        </div>
      ) : null}
    </>
  );
}

function ReviewFact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="mt-4 first:mt-0">
      <h4 className="m-0 text-[13px] font-bold text-text-primary">{label}</h4>
      <p className="mt-1.5 mb-0 w-[min(100%,62ch)] text-[12.5px] leading-[1.62] text-text-secondary [overflow-wrap:anywhere]">
        {children}
      </p>
    </section>
  );
}

/* ---------------------------------------------------------------- build --- */

function BuildBody({ run }: { run: PipelineRun }) {
  const progress = useBuildProgress(run);
  const warnings = (run.warnings ?? []).map((warning) => warning.trim()).filter(Boolean);
  const cancelled = run.status === "cancelled";
  const failed = run.status === "failed";

  return (
    <>
      <div className="flex items-baseline gap-2.5">
        <h3 className="m-0 flex-1 text-[18px] font-bold tracking-[-0.015em] text-text-primary [overflow-wrap:anywhere]">
          Mapping {run.topic}
        </h3>
        <span className="text-[22px] font-bold tracking-[-0.02em] text-accent tabular-nums">
          {progress.percent}%
        </span>
      </div>
      <div className="mt-3.5 h-1.5 overflow-hidden rounded-[3px] bg-track">
        <div
          className="h-full rounded-[3px] bg-accent transition-[width] duration-500 ease-linear"
          style={{ width: `${progress.percent}%` }}
        />
      </div>
      <ol className="m-0 mt-[22px] flex list-none flex-col gap-[11px] p-0">
        {progress.stages.map((stage) => (
          <li className="flex items-center gap-[11px]" key={stage.id}>
            <StageMark state={stage.state} />
            <span
              className={cx(
                "text-[13px]",
                stage.state === "waiting"
                  ? "font-medium text-text-muted"
                  : stage.state === "failed"
                    ? "font-medium text-error"
                    : stage.state === "current"
                      ? "font-semibold text-text-primary"
                      : "font-medium text-text-primary",
              )}
            >
              {stage.label}
            </span>
          </li>
        ))}
      </ol>
      {warnings.length > 0 ? (
        <div className="mt-4 grid gap-1 rounded-lg border border-warning-border bg-warning-surface px-3.5 py-3 text-xs leading-[1.5] text-warning">
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
      {failed ? (
        <p className="mt-4 mb-0 rounded-lg border border-error-border bg-error-surface px-3.5 py-3 text-xs leading-[1.5] text-error">
          {run.error || "The workspace could not be built. Your existing workspaces are unchanged."}
        </p>
      ) : null}
      {cancelled ? (
        <p className="mt-4 mb-0 rounded-lg border border-border bg-surface-subtle px-3.5 py-3 text-xs leading-[1.5] text-text-secondary">
          Build cancelled. Your existing workspaces are unchanged.
        </p>
      ) : null}
    </>
  );
}

function StageMark({ state }: { state: "done" | "current" | "waiting" | "failed" }) {
  if (state === "done") {
    return (
      <span className="grid h-[18px] w-[18px] flex-none place-items-center rounded-full bg-accent-subtle text-accent-deep" aria-hidden="true">
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
      <span className="h-[18px] w-[18px] flex-none rounded-full border-2 border-error bg-error-surface" aria-hidden="true" />
    );
  }
  return <span className="h-[18px] w-[18px] flex-none rounded-full border-2 border-track" aria-hidden="true" />;
}

function InlineError({ message }: { message: string }) {
  return (
    <p className="mt-4 mb-0 rounded-lg border border-error-border bg-error-surface px-3.5 py-3 text-xs leading-[1.5] text-error" role="alert">
      {message}
    </p>
  );
}

/* -------------------------------------------------------------- helpers --- */

/** Statuses the server will send no further updates for. */
const TERMINAL_RUN_STATUSES: PipelineRun["status"][] = [
  "completed",
  "completed_with_warnings",
  "failed",
  "cancelled",
];

function isTerminalRunStatus(status: PipelineRun["status"]): boolean {
  return TERMINAL_RUN_STATUSES.includes(status);
}

/** A malformed frame is a reporting problem, not a build problem. */
function parsePipelineRun(data: string): PipelineRun | null {
  try {
    return JSON.parse(data) as PipelineRun;
  } catch {
    return null;
  }
}
