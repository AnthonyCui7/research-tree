import type { CSSProperties } from "react";
import { cx } from "../../lib/cx";
import { FAMILY_TONES } from "../../lib/familyTint";
import type { BuildStage } from "../../lib/pipelineStages";

type StageState = BuildStage["state"];

/**
 * A miniature of the canvas, assembled the way a build assembles the real
 * one: the literature is read (a field of papers), the structure is drawn
 * (topic, branches, reading paths), the papers are written up (cards fill
 * in), and similar papers are linked. Each layer follows its stage, so the
 * picture is the progress rather than decoration next to it.
 */
export function BuildIllustration({ stages }: { stages: BuildStage[] }) {
  const state = (id: string): StageState =>
    stages.find((stage) => stage.id === id)?.state ?? "waiting";
  const reading = state("candidates");
  const drawing = state("construct");
  const writing = state("hydrate");
  const linking = state("related");
  // A stage that failed drew nothing, so it shows as not yet reached.
  const ran = (stage: StageState) => stage === "current" || stage === "done";
  const structureShown = ran(drawing);

  return (
    <svg className="block h-auto w-full" viewBox="0 0 560 196" aria-hidden="true">
      {/* The field being read: the pool of papers the build ranks and chooses
          from. It gives way to the structure drawn from it, and stays when a
          build stops before any was. */}
      <g
        className="transition-opacity duration-700"
        style={{ opacity: structureShown ? 0 : 1 }}
      >
        {POOL.map(([x, y], index) => (
          <rect
            key={index}
            x={x}
            y={y}
            width={9}
            height={12}
            rx={2}
            className={cx(
              "fill-surface stroke-edge-line",
              reading === "current" ? "animate-breathe" : "opacity-40",
            )}
            style={{ animationDelay: `${(index * 173) % 1800}ms` }}
          />
        ))}
      </g>

      {structureShown ? (
        <g>
          {/* Topic to branches, and each branch into its reading path. */}
          {BRANCHES.map((branch, index) => (
            <g key={branch.y}>
              <path
                d={`M 108 98 C 138 98, 138 ${branch.cy}, 168 ${branch.cy}`}
                pathLength={1}
                className={edgeClass(drawing)}
                style={drawDelay(index * 120)}
              />
              <path
                d={`M 260 ${branch.cy} H 296 M 372 ${branch.cy} H 384 M 460 ${branch.cy} H 472`}
                pathLength={1}
                className={edgeClass(drawing)}
                style={drawDelay(360 + index * 120)}
              />
            </g>
          ))}

          <g className="animate-settle">
            <rect x={4} y={74} width={104} height={48} rx={8} className="fill-accent-wash stroke-accent-border" />
            <Bar x={14} y={84} width={34} height={3} />
            <Bar x={14} y={92} width={62} height={6} strong />
            <Bar x={14} y={104} width={82} height={3} />
            <Bar x={14} y={111} width={64} height={3} />
          </g>

          {BRANCHES.map((branch, index) => {
            const tone = FAMILY_TONES[index] ?? FAMILY_TONES[0]!;
            return (
              <g className="animate-settle" key={branch.y} style={{ animationDelay: `${120 + index * 90}ms` }}>
                <rect x={168} y={branch.y} width={92} height={34} rx={7} fill={tone.branch} stroke={tone.border} />
                <Bar x={176} y={branch.y + 8} width={28} height={2.5} />
                <Bar x={176} y={branch.y + 15} width={58} height={4.5} strong />
                <Bar x={176} y={branch.y + 24} width={68} height={2.5} />
              </g>
            );
          })}

          {/* The papers on each path: outlined once placed, written up as the
              details land. */}
          {BRANCHES.map((branch, branchIndex) =>
            PAPER_XS.map((x, paperIndex) => {
              const order = branchIndex * PAPER_XS.length + paperIndex;
              const filled = ran(writing);
              return (
                <g key={`${branch.y}:${x}`}>
                  <rect
                    x={x}
                    y={branch.y + 4}
                    width={76}
                    height={26}
                    rx={6}
                    className="fill-none stroke-node-border [stroke-dasharray:3_3]"
                  />
                  {filled ? (
                    <g
                      className="animate-settle"
                      style={{ animationDelay: writing === "current" ? `${order * 260}ms` : "0ms" }}
                    >
                      <rect x={x} y={branch.y + 4} width={76} height={26} rx={6} className="fill-surface stroke-node-border" />
                      <Bar x={x + 7} y={branch.y + 11} width={48} height={4} strong />
                      <Bar x={x + 7} y={branch.y + 20} width={58} height={2.5} />
                    </g>
                  ) : null}
                </g>
              );
            }),
          )}
        </g>
      ) : null}

      {/* Similar papers, linked across branches. */}
      {ran(linking) ? (
        <g className="animate-settle">
          {LINKS.map((d) => (
            <path
              key={d}
              d={d}
              className={cx(
                "fill-none [stroke-dasharray:2_4] [stroke-linecap:round]",
                linking === "current" ? "animate-march stroke-accent" : "stroke-edge-line",
              )}
              strokeWidth={1.25}
            />
          ))}
        </g>
      ) : null}
    </svg>
  );
}

/** A line of text inside a card, at the card's scale. */
function Bar({
  x,
  y,
  width,
  height,
  strong = false,
}: {
  x: number;
  y: number;
  width: number;
  height: number;
  strong?: boolean;
}) {
  return (
    <rect
      x={x}
      y={y}
      width={width}
      height={height}
      rx={height / 2}
      className={strong ? "fill-text-secondary/45" : "fill-text-muted/25"}
    />
  );
}

/** Edges draw once when their stage starts, in the accent while it runs. */
function edgeClass(stage: StageState): string {
  return cx(
    "fill-none [stroke-dasharray:1] [stroke-width:1.75] [stroke-linecap:round] transition-[stroke] duration-500",
    stage === "current" ? "animate-draw stroke-accent" : "stroke-edge-line",
  );
}

function drawDelay(ms: number): CSSProperties {
  return { animationDelay: `${ms}ms` };
}

const BRANCHES = [6, 56, 106, 156].map((y) => ({ y, cy: y + 17 }));
const PAPER_XS = [296, 384, 472];

/** Similar-paper links arc between papers on neighbouring paths. */
const LINKS = [
  "M 422 36 C 440 46, 440 58, 422 66",
  "M 334 86 C 352 96, 352 108, 334 116",
  "M 510 136 C 528 146, 528 158, 510 166",
  "M 510 36 C 540 70, 540 100, 510 116",
];

/** The pool's papers, on a loose grid over the whole field. */
const POOL: ReadonlyArray<readonly [number, number]> = [
  [8, 14], [94, 6], [129, 11], [266, 7], [350, 13], [392, 6], [420, 7], [512, 8], [17, 48],
  [47, 44], [136, 47], [177, 47], [223, 46], [260, 55], [297, 57], [388, 45], [416, 52],
  [466, 55], [510, 51], [13, 93], [55, 90], [140, 95], [174, 86], [210, 87], [376, 84],
  [431, 82], [466, 93], [513, 85], [11, 131], [49, 121], [180, 123], [258, 127], [304, 126],
  [345, 120], [388, 131], [422, 125], [498, 120], [88, 157], [210, 169], [254, 161],
  [299, 159], [351, 164], [376, 158], [420, 169], [540, 150], [470, 172], [548, 30],
];
