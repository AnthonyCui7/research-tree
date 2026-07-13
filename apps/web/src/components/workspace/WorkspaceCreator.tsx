import { useEffect, useRef, useState } from "react";
import { pipelineRunEventsUrl, repositoryWorkspaceGateway } from "../../data/workspaceApi";
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
    if (!run || !["queued", "running"].includes(run.status)) {
      return;
    }
    const events = new EventSource(pipelineRunEventsUrl(run.run_id));
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
  }, [onCreated, onRunFinished, onRunStarted, run?.run_id, run?.status]);

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
      className="creation-backdrop"
      aria-labelledby="creation-title"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onMouseDown={(event) => {
      if (event.target === event.currentTarget) close();
    }}>
      <section className="creation-panel" data-state={closing ? "closing" : "open"}>
        <header className="creation-heading">
          <div>
            <span>{run ? "Workspace build" : "New workspace"}</span>
            <h2 id="creation-title">
              {run ? run.topic : review ? "Review research focus" : "Start with a research focus"}
            </h2>
          </div>
          <button className="icon-button" type="button" onClick={close} aria-label="Close new workspace" title="Close">
            <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
          </button>
        </header>

        {!review && !run ? (
          <form className="topic-entry" onSubmit={(event) => { event.preventDefault(); void checkTopic(); }}>
            <p className="creation-intro">Enter a field, research question, method, benchmark, or survey.</p>
            <label>
              Research focus
              <textarea
                ref={topicInputRef}
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="A field, research question, method, benchmark, or survey"
                maxLength={240}
              />
            </label>
            <button className="primary-action" type="submit" disabled={busy || !topic.trim()}>
              {busy ? "Checking…" : "Review focus"}
            </button>
          </form>
        ) : null}

        {review && !run ? (
          <div className="topic-confirmation">
            {review.existing_workspace ? (
              <>
                <p>A workspace already exists for this research focus. Enter a different focus or open the existing workspace.</p>
                <button className="primary-action" type="button" onClick={() => {
                  onOpenExisting(review.existing_workspace!.workspace_id);
                  close();
                }}>
                  Open {review.existing_workspace.title}
                </button>
              </>
            ) : review.is_research_topic ? (
              <>
                <dl>
                  <div><dt>Extracted focus</dt><dd>{review.normalized_topic}</dd></div>
                  {review.source_paper ? <div><dt>Linked paper</dt><dd>{review.source_paper.title}</dd></div> : null}
                  <div><dt>Workspace</dt><dd>Branches, reading paths, and paper notes</dd></div>
                </dl>
                <div className="creation-actions">
                  <button type="button" onClick={() => setReview(null)}>Edit topic</button>
                  <button className="primary-action" type="button" disabled={busy} onClick={() => void createWorkspace()}>
                    {busy ? "Starting…" : "Build workspace"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p>{review.guidance || "This doesn't appear to be a research topic. Try a specific field or question."}</p>
                <button type="button" onClick={() => setReview(null)}>Try another topic</button>
              </>
            )}
          </div>
        ) : null}

        {run ? (
          <PipelineProgress
            run={run}
            canceling={canceling}
            onCancel={() => void cancelRun()}
          />
        ) : null}
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
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
}: {
  run: PipelineRun;
  canceling: boolean;
  onCancel: () => void;
}) {
  const stages = [
    ["candidates", "Searching papers"],
    ["construct", "Building structure"],
    ["hydrate", "Loading details"],
    ["related", "Finding related work"],
  ] as const;
  return (
    <div className="pipeline-progress" aria-live="polite">
      {run.status === "failed" ? <p>Workspace construction failed.</p> : null}
      <ol>
        {stages.map(([stage, label]) => {
          const status = run.stages[stage]?.status || (run.requested_stages.includes(stage) ? "waiting" : "reused");
          return <li key={stage} data-status={status}><span aria-hidden="true" /> <div><strong>{label}</strong><small>{stageStatus(status)}</small></div></li>;
        })}
      </ol>
      {["queued", "running"].includes(run.status) ? (
        <button className="pipeline-cancel-button" type="button" onClick={onCancel} disabled={canceling}>
          {canceling ? "Cancelling…" : "Cancel"}
        </button>
      ) : null}
      {run.status === "failed" ? <p className="inline-error">The workspace could not be built. Your existing workspaces are unchanged.</p> : null}
    </div>
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

function messageFrom(error: unknown): string {
  return "We could not complete that request. Please try again.";
}
