import { useEffect, useState } from "react";
import type { PipelineRun } from "./types";

/**
 * The build is described to the reader in plain language, not pipeline stage
 * names. Order matters: progress is read off the first stage that is not done.
 */
const STAGE_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["candidates", "Reading the field"],
  ["construct", "Drawing branches and reading paths"],
  ["hydrate", "Writing overviews and summaries"],
  ["related", "Finding similar papers"],
];

export type BuildStage = {
  id: string;
  label: string;
  state: "done" | "current" | "waiting" | "failed";
};

export type BuildProgress = {
  stages: BuildStage[];
  /** 0–100, held below 100 until the run itself reports it finished. */
  percent: number;
  /** The workspace is openable: structure and details have landed. */
  openable: boolean;
  currentLabel: string | null;
};

/**
 * How much of the bar the clock alone can fill, and how quickly it gets there.
 *
 * A build has four stages but no way to know how far into one it is, so a bar
 * driven by stage transitions alone sits still for minutes and then jumps. The
 * clock fills the gaps: `1 - e^(-t/τ)` moves quickly at the start and keeps
 * decelerating, so it never has to guess a finish time it cannot know. τ is set
 * near a typical run — the curve reaches ~63% of its ceiling at τ, ~86% at 2τ.
 */
const CLOCK_CEILING = 0.9;
const CLOCK_TIME_CONSTANT_MS = 180_000;
/** Real progress is honest but coarse; nothing pretends the run is over. */
const STAGE_CEILING = 0.97;

/**
 * `elapsedMs` is how long the run has been going. Pass 0 for a run whose
 * progress should be read off its stages alone.
 */
export function buildProgress(run: PipelineRun, elapsedMs = 0): BuildProgress {
  // Read defensively: this runs inside the sidebar's render, above every
  // error boundary, so a record missing a field must not blank the page.
  const requestedStages = Array.isArray(run.requested_stages) ? run.requested_stages : [];
  const stageStates = run.stages && typeof run.stages === "object" ? run.stages : {};
  const requested = STAGE_LABELS.filter(
    ([id]) => requestedStages.includes(id) || stageStates[id] !== undefined,
  );
  const stageList = requested.length > 0 ? requested : STAGE_LABELS;

  let seenUnfinished = false;
  const stages: BuildStage[] = stageList.map(([id, label]) => {
    const status = stageStates[id]?.status ?? "";
    if (status === "failed") {
      seenUnfinished = true;
      return { id, label, state: "failed" };
    }
    if (status.startsWith("completed") || status === "reused") {
      return { id, label, state: "done" };
    }
    if (seenUnfinished) {
      return { id, label, state: "waiting" };
    }
    seenUnfinished = true;
    return { id, label, state: status === "running" ? "current" : "waiting" };
  });

  const done = stages.filter((stage) => stage.state === "done").length;

  return {
    stages,
    percent: percentComplete(run, done / stages.length, elapsedMs),
    openable: isOpenable(run),
    currentLabel: stages.find((stage) => stage.state === "current")?.label ?? null,
  };
}

/**
 * The bar is whichever is further along: the stages actually finished, or the
 * clock. Both only ever rise, so their maximum never steps backwards.
 */
function percentComplete(run: PipelineRun, stagesDone: number, elapsedMs: number): number {
  if (["completed", "completed_with_warnings"].includes(run.status)) {
    return 100;
  }
  const settled = run.status === "failed" || run.status === "cancelled";
  // A stopped build's bar should stay where the work stopped rather than drift
  // on toward a finish that is not coming.
  const clock = settled
    ? 0
    : CLOCK_CEILING * (1 - Math.exp(-Math.max(0, elapsedMs) / CLOCK_TIME_CONSTANT_MS));
  return Math.round(Math.min(STAGE_CEILING, Math.max(stagesDone * STAGE_CEILING, clock)) * 100);
}

/**
 * Progress that advances between stage transitions, for the components that
 * show a bar. The tick is what animates the clock; it stops with the run.
 */
export function useBuildProgress(run: PipelineRun): BuildProgress {
  // The clock runs from when a worker picked the build up. A build waiting in
  // the queue has not started, and a bar that filled while it waited had
  // nothing left to show once the work began.
  const startedAt = run.status === "queued" ? Number.NaN : Date.parse(run.started_at ?? run.created_at);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!isRunActive(run)) return;
    const timer = window.setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => window.clearInterval(timer);
  }, [run]);

  return buildProgress(run, Number.isNaN(startedAt) ? 0 : now - startedAt);
}

/** Matches the bar's width transition, so the fill reads as continuous. */
const CLOCK_TICK_MS = 500;

/** Structure plus paper details have landed; later stages only enrich it. */
export function isOpenable(run: PipelineRun): boolean {
  const status = run.stages?.hydrate?.status ?? "";
  return status.startsWith("completed");
}

export function isRunActive(
  run: PipelineRun | null | undefined,
): run is PipelineRun & { status: "queued" | "running" } {
  return run?.status === "queued" || run?.status === "running";
}
