import { messageFrom } from "../../lib/apiError";
import { primaryActionClass, secondaryActionClass } from "../../lib/controlClasses";
import { useEffect, useRef, useState } from "react";
import { pipelineRunEventsUrl, repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { cx } from "../../lib/cx";
import type { PipelineRun, TopicReview } from "../../lib/types";

type WorkspaceCreatorProps = {
  open: boolean;
  onClose: () => void;
  onCreated: (workspaceId: string, run: PipelineRun) => Promise<void>;
  onOpenExisting: (workspaceId: string) => void;
  activeRun: PipelineRun | null;
  onRunStarted: (run: PipelineRun) => void;
  onRunFinished: (runId: string) => void;
};

export function WorkspaceCreator({
  open,
  onClose,
  onCreated,
  onOpenExisting,
  activeRun,
  onRunStarted,
  onRunFinished,
}: WorkspaceCreatorProps) {
  const [topic, setTopic] = useState("");
  const [review, setReview] = useState<TopicReview | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [present, setPresent] = useState(open);
  const [closing, setClosing] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const topicInputRef = useRef<HTMLTextAreaElement>(null);
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
    }, 200);
    return () => window.clearTimeout(timer);
  }, [open, present]);

  useEffect(() => {
    if (present && dialogRef.current && !dialogRef.current.open) {
      dialogRef.current.showModal();
    }
  }, [present]);

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
      try {
        const next = JSON.parse((event as MessageEvent<string>).data) as PipelineRun;
        setRun(next);
        onRunStarted(next);
        if (["completed", "completed_with_warnings"].includes(next.status)) {
          void onCreated(next.workspace_id, next).then(() => {
            onRunFinished(next.run_id);
            readyRunIdRef.current = null;
            setTopic("");
            setReview(null);
            setRun(null);
            setError(null);
            setCanceling(false);
          }).catch((requestError: unknown) => setError(messageFrom(requestError)));
          return;
        }
        if (next.status === "failed") {
          if (readyRunIdRef.current === next.run_id || workspaceIsReadyForUse(next)) {
            void onCreated(next.workspace_id, next).then(() => {
              onRunFinished(next.run_id);
              readyRunIdRef.current = null;
              setTopic("");
              setReview(null);
              setRun(null);
              setCanceling(false);
            }).catch((requestError: unknown) => setError(messageFrom(requestError)));
          }
          return;
        }
        if (workspaceIsReadyForUse(next) && readyRunIdRef.current !== next.run_id) {
          readyRunIdRef.current = next.run_id;
          void onCreated(next.workspace_id, next).catch((requestError: unknown) => {
            readyRunIdRef.current = null;
            setError(messageFrom(requestError));
          });
        }
      } catch (requestError) {
        setError(messageFrom(requestError));
      }
    });
    // EventSource reconnects automatically after transient network failures.
    // Treating every reconnect as a failed build leaves a stale error onscreen.
    events.onerror = () => {};
    return () => events.close();
  }, [onCreated, onRunFinished, onRunStarted, run?.run_id]);

  if (!present) {
    return null;
  }

  async function checkTopic() {
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
      onClose();
      return;
    }
    if (run) {
      onRunFinished(run.run_id);
    }
    setTopic("");
    setReview(null);
    setRun(null);
    setError(null);
    onClose();
  }

  return (
    <dialog
      ref={dialogRef}
      className="fixed inset-0 z-backdrop grid h-full max-h-none w-full max-w-none place-items-center border-0 bg-transparent p-6 max-[720px]:items-end max-[720px]:p-0"
      aria-labelledby="creation-title"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onMouseDown={(event) => {
      if (event.target === event.currentTarget) close();
    }}>
      <section className="max-h-[min(760px,calc(100vh_-_48px))] w-full max-w-[540px] animate-interface-center-enter overflow-y-auto rounded-[10px] border border-border bg-surface shadow-dialog data-[state=closing]:pointer-events-none data-[state=closing]:animate-interface-center-exit max-[720px]:max-h-[88vh] max-[720px]:w-full max-[720px]:rounded-t-md max-[720px]:rounded-b-none" data-state={closing ? "closing" : "open"}>
        <header className="flex min-w-0 items-center justify-between gap-[18px] border-b border-border px-6 pt-[21px] pb-[18px] max-[720px]:p-[18px]">
          <div className="grid min-w-0 gap-[5px]">
            <span className="text-[11px] font-semibold text-text-secondary">{run ? "Workspace build" : "New workspace"}</span>
            <h2 className="m-0 text-balance text-base font-bold leading-tight tracking-normal text-text-primary [overflow-wrap:anywhere]" id="creation-title">
              {run ? run.topic : review ? "Review research focus" : "Start with a research focus"}
            </h2>
          </div>
          <button className="grid h-8 w-8 flex-none place-items-center rounded-md border-0 bg-transparent p-0 text-text-secondary transition-[background-color,border-color,color,transform] duration-200 ease-research enabled:hover:bg-surface-subtle enabled:hover:text-text-primary enabled:active:scale-[0.94] disabled:cursor-not-allowed disabled:text-text-muted [&_svg]:h-[18px] [&_svg]:w-[18px] max-[720px]:h-10 max-[720px]:w-10" type="button" onClick={close} aria-label="Close new workspace" title="Close">
            <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
          </button>
        </header>

        {!review && !run ? (
          <form className="grid gap-[18px] p-6 max-[720px]:p-5 max-[720px]:px-[18px]" onSubmit={(event) => { event.preventDefault(); void checkTopic(); }}>
            <p className="m-0 text-[13px] leading-[1.55] text-text-secondary">Enter a field, research question, method, benchmark, or survey.</p>
            <label className="grid gap-2 text-xs font-semibold text-text-primary">
              Research focus
              <textarea
                className="min-h-[116px] w-full resize-y rounded-sm border border-border-strong bg-surface px-3.5 py-[13px] text-sm leading-normal text-text-primary outline-0 transition-[border-color,box-shadow] duration-150 placeholder:text-text-secondary focus:border-accent focus:shadow-[0_0_0_2px_var(--color-accent-subtle)]"
                ref={topicInputRef}
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="A field, research question, method, benchmark, or survey"
                maxLength={240}
              />
            </label>
            <button className={primaryActionClass} type="submit" disabled={busy || !topic.trim()}>
              {busy ? "Checking…" : "Review focus"}
            </button>
          </form>
        ) : null}

        {review && !run ? (
          <div className="grid gap-[18px] p-6 max-[720px]:p-5 max-[720px]:px-[18px]">
            {review.existing_workspace ? (
              <>
                <p className="m-0 text-[13px] leading-[1.55] text-text-secondary">A workspace already exists for this research focus. Enter a different focus or open the existing workspace.</p>
                <button className={primaryActionClass} type="button" onClick={() => {
                  onOpenExisting(review.existing_workspace!.workspace_id);
                  close();
                }}>
                  Open {review.existing_workspace.title}
                </button>
              </>
            ) : review.is_research_topic ? (
              <>
                <dl className="m-0 grid border-t border-border">
                  <div className="grid grid-cols-[116px_minmax(0,1fr)] gap-4 border-b border-border py-[13px] max-[520px]:grid-cols-1 max-[520px]:gap-[5px]"><dt className="m-0 text-[13px] text-text-secondary">Extracted focus</dt><dd className="m-0 text-[13px] font-medium leading-[1.45] text-text-primary [overflow-wrap:anywhere]">{review.normalized_topic}</dd></div>
                  {review.source_paper ? <div className="grid grid-cols-[116px_minmax(0,1fr)] gap-4 border-b border-border py-[13px] max-[520px]:grid-cols-1 max-[520px]:gap-[5px]"><dt className="m-0 text-[13px] text-text-secondary">Linked paper</dt><dd className="m-0 text-[13px] font-medium leading-[1.45] text-text-primary [overflow-wrap:anywhere]">{review.source_paper.title}</dd></div> : null}
                  <div className="grid grid-cols-[116px_minmax(0,1fr)] gap-4 border-b border-border py-[13px] max-[520px]:grid-cols-1 max-[520px]:gap-[5px]"><dt className="m-0 text-[13px] text-text-secondary">Workspace</dt><dd className="m-0 text-[13px] font-medium leading-[1.45] text-text-primary [overflow-wrap:anywhere]">Branches, reading paths, and paper notes</dd></div>
                </dl>
                <div className="flex justify-end gap-2 max-[520px]:flex-col-reverse">
                  <button className={cx(secondaryActionClass, "max-[520px]:w-full")} type="button" onClick={() => setReview(null)}>Edit topic</button>
                  <button className={primaryActionClass} type="button" disabled={busy} onClick={() => void createWorkspace()}>
                    {busy ? "Starting…" : "Build workspace"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="m-0 text-[13px] leading-[1.55] text-text-secondary">{review.guidance || "This doesn't appear to be a research topic. Try a specific field or question."}</p>
                <button className={cx(secondaryActionClass, "max-[520px]:w-full")} type="button" onClick={() => setReview(null)}>Try another topic</button>
              </>
            )}
          </div>
        ) : null}

        {run ? (
          <PipelineProgress
            run={run}
            canceling={canceling}
            onCancel={() => void cancelRun()}
            onStartOver={startOver}
          />
        ) : null}
        {error ? <p className={inlineErrorClass} role="alert">{error}</p> : null}
      </section>
    </dialog>
  );
}

function workspaceIsReadyForUse(run: PipelineRun): boolean {
  const hydrateStatus = run.stages.hydrate?.status;
  return hydrateStatus === "completed" || hydrateStatus === "completed_with_warnings";
}

function PipelineProgress({
  run,
  canceling,
  onCancel,
  onStartOver,
}: {
  run: PipelineRun;
  canceling: boolean;
  onCancel: () => void;
  onStartOver: () => void;
}) {
  const stages = [
    ["candidates", "Searching papers"],
    ["construct", "Building structure"],
    ["hydrate", "Loading details"],
    ["related", "Finding related work"],
  ] as const;
  return (
    <div className="grid gap-[18px] p-6 max-[720px]:p-5 max-[720px]:px-[18px]" aria-live="polite">
      <ol className="m-0 grid list-none p-0">
        {stages.map(([stage, label]) => {
          const status = run.stages[stage]?.status || (run.requested_stages.includes(stage) ? "waiting" : "reused");
          return <li className="grid grid-cols-[18px_minmax(0,1fr)] gap-2.5 border-b border-border py-[13px]" key={stage}><span className={stageDotClass(status)} aria-hidden="true" /> <div className="grid gap-[3px]"><strong className="text-[13px]">{label}</strong><small className="text-[11px] text-text-secondary">{stageStatus(status)}</small></div></li>;
        })}
      </ol>
      {["queued", "running"].includes(run.status) ? (
        <button className="mt-0.5 min-w-28 justify-self-center rounded-sm border border-[color-mix(in_srgb,var(--color-text-muted)_46%,transparent)] bg-surface px-3.5 py-2 text-xs font-semibold text-text-secondary transition-[background-color,border-color,color] duration-200 ease-research enabled:hover:border-error enabled:hover:bg-[color-mix(in_srgb,var(--color-error)_7%,var(--color-surface))] enabled:hover:text-error disabled:cursor-not-allowed disabled:text-text-muted" type="button" onClick={onCancel} disabled={canceling}>
          {canceling ? "Cancelling…" : "Cancel"}
        </button>
      ) : null}
      {run.status === "failed" ? (
        <div className="grid gap-2.5">
          <p className="m-0 rounded-sm bg-[color-mix(in_srgb,var(--color-error)_9%,var(--color-surface))] px-3 py-2.5 text-xs leading-[1.45] text-error">
            {run.error || "The workspace could not be built. Your existing workspaces are unchanged."}
          </p>
          <button className={cx(secondaryActionClass, "justify-self-center")} type="button" onClick={onStartOver}>Start over</button>
        </div>
      ) : null}
    </div>
  );
}

const inlineErrorClass = "mx-6 mt-0 mb-5 rounded-sm bg-[color-mix(in_srgb,var(--color-error)_9%,var(--color-surface))] px-3 py-2.5 text-xs leading-[1.45] text-error";

function stageDotClass(status: string): string {
  return cx(
    "mt-1 h-2.5 w-2.5 rounded-full border-2 border-border-strong",
    status === "running" && "animate-progress-spin border-accent border-t-transparent",
    status.startsWith("completed") && "border-accent bg-accent",
    status === "failed" && "border-error bg-[color-mix(in_srgb,var(--color-error)_12%,var(--color-surface))]",
  );
}

function stageStatus(status: string) {
  if (status === "running") return "In progress";
  if (status === "failed") return "Failed";
  if (status === "cancelled") return "Cancelled";
  if (status === "completed") return "Complete";
  if (status === "completed_with_warnings") return "Complete";
  if (status === "reused") return "Reused from the prior run";
  return "Waiting";
}
