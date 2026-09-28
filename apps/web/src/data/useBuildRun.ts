import { useCallback, useEffect, useRef, useState } from "react";
import { pipelineRunEventsUrl, repositoryWorkspaceGateway } from "./workspaceApi";
import { messageFrom } from "../lib/apiError";
import { isOpenable, isRunActive } from "../lib/pipelineStages";
import type { PipelineRun } from "../lib/types";

export type BuildRun = {
  /**
   * The build being watched: queued or running, or one that stopped before
   * its workspace could be opened. Null when there is nothing to report.
   */
  run: PipelineRun | null;
  /** A problem watching or cancelling the build, not the build's own failure. */
  error: string | null;
  /** Starts a build from an approved topic; a refusal is thrown to the caller. */
  start: (topic: string, topicReviewToken: string, instructions: string) => Promise<void>;
  /** Follows a build started elsewhere, such as an approved rebuild. */
  watch: (run: PipelineRun) => void;
  cancel: () => Promise<void>;
  /** Forgets a build that failed or was cancelled. */
  dismiss: () => void;
};

/** How often a run is read once its stream has been given up on. */
const RUN_POLL_MS = 3_000;

/**
 * The one build the app watches. A build lives on the server; the page only
 * follows it, over the run's event stream, and falls back to polling when the
 * stream gives up. `onReady` is told once per build, as soon as its workspace
 * can be opened (structure and paper details have landed) or, failing that,
 * when it finishes.
 */
export function useBuildRun(onReady: (workspaceId: string) => void): BuildRun {
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const readyRunIdRef = useRef<string | null>(null);
  // The stream handler outlives renders; it reads the newest callback here.
  const onReadyRef = useRef(onReady);
  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  // A page reloaded mid-build has no other way to find the build again: the
  // workspace is not listed until its first version lands. Asked once.
  useEffect(() => {
    let cancelled = false;
    repositoryWorkspaceGateway
      .listActivePipelineRuns()
      .then((runs) => {
        const newest = runs.find(isRunActive);
        if (!cancelled && newest) setRun((current) => current ?? newest);
      })
      // A build that cannot be recovered shows up in the sidebar once it
      // lands; nothing else is lost.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const runId = run?.run_id ?? null;
  const active = isRunActive(run);

  useEffect(() => {
    if (!runId || !active) return;
    let finished = false;
    let poll: number | null = null;

    function handle(next: PipelineRun) {
      if (finished || next.run_id !== runId) return;
      const done = next.status === "completed" || next.status === "completed_with_warnings";
      if (!isRunActive(next)) {
        // The server stops streaming once a run leaves queued/running.
        // Without this close EventSource reconnects and replays the update.
        finished = true;
        events.close();
        if (poll !== null) window.clearInterval(poll);
      }
      const openable = done || isOpenable(next);
      if (openable && readyRunIdRef.current !== next.run_id) {
        readyRunIdRef.current = next.run_id;
        onReadyRef.current(next.workspace_id);
      }
      // A finished build, or a failed one whose workspace opened anyway, has
      // nothing left to report; the workspace itself is the result.
      setRun(done || (next.status === "failed" && openable) ? null : next);
    }

    const events = new EventSource(pipelineRunEventsUrl(runId));
    events.addEventListener("pipeline_run_updated", (event) => {
      const next = parseRun((event as MessageEvent<string>).data);
      if (next) handle(next);
      else setError("A build update could not be read. The build itself continues on the server.");
    });
    // The server says when it is done on purpose; a backend that does not
    // send this still closes on the terminal status above.
    events.addEventListener("stream_complete", () => events.close());
    events.onerror = () => {
      // EventSource reconnects on its own after a dropped connection. A
      // stream it gives up on (a 5xx during a rollout, a 401 once the
      // session ends) would leave the build looking stuck, so the run is
      // polled instead; an ended session answers with a 401 the session
      // store hears.
      if (events.readyState !== EventSource.CLOSED || finished || poll !== null) return;
      poll = window.setInterval(() => {
        repositoryWorkspaceGateway
          .getPipelineRun(runId)
          .then(handle)
          .catch(() => undefined);
      }, RUN_POLL_MS);
    };
    return () => {
      finished = true;
      events.close();
      if (poll !== null) window.clearInterval(poll);
    };
  }, [runId, active]);

  const start = useCallback(
    async (topic: string, topicReviewToken: string, instructions: string) => {
      const next = await repositoryWorkspaceGateway.createWorkspace(
        topic,
        topicReviewToken,
        instructions,
      );
      setError(null);
      setRun(next);
    },
    [],
  );

  const watch = useCallback((next: PipelineRun) => {
    setError(null);
    setRun(next);
  }, []);

  const cancel = useCallback(async () => {
    if (!runId || !active) return;
    setError(null);
    try {
      await repositoryWorkspaceGateway.cancelPipelineRun(runId);
      setRun(null);
    } catch (requestError) {
      setError(messageFrom(requestError));
    }
  }, [active, runId]);

  const dismiss = useCallback(() => {
    setError(null);
    setRun(null);
  }, []);

  return { run, error, start, watch, cancel, dismiss };
}

/** A malformed frame is a reporting problem, not a build problem. */
function parseRun(data: string): PipelineRun | null {
  try {
    const parsed = JSON.parse(data) as PipelineRun;
    return parsed && typeof parsed.run_id === "string" ? parsed : null;
  } catch {
    return null;
  }
}
