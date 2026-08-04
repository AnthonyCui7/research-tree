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

export function buildProgress(run: PipelineRun): BuildProgress {
  const requested = STAGE_LABELS.filter(
    ([id]) => run.requested_stages.includes(id) || run.stages[id] !== undefined,
  );
  const stageList = requested.length > 0 ? requested : STAGE_LABELS;

  let seenUnfinished = false;
  const stages: BuildStage[] = stageList.map(([id, label]) => {
    const status = run.stages[id]?.status ?? "";
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
  const running = stages.some((stage) => stage.state === "current") ? 0.5 : 0;
  const finished = ["completed", "completed_with_warnings"].includes(run.status);
  const percent = finished
    ? 100
    : Math.min(97, Math.round(((done + running) / stages.length) * 100));

  return {
    stages,
    percent,
    openable: isOpenable(run),
    currentLabel: stages.find((stage) => stage.state === "current")?.label ?? null,
  };
}

/** Structure plus paper details have landed; later stages only enrich it. */
export function isOpenable(run: PipelineRun): boolean {
  const status = run.stages.hydrate?.status ?? "";
  return status.startsWith("completed");
}

export function isRunActive(run: PipelineRun | null | undefined): run is PipelineRun {
  return run?.status === "queued" || run?.status === "running";
}
