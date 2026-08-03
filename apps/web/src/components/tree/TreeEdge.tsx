import type { TreeEdgeViewModel } from "../../lib/types";

type TreeEdgeProps = {
  edge: TreeEdgeViewModel;
};

export function TreeEdge({ edge }: TreeEdgeProps) {
  if (edge.kind === "timeline") {
    return (
      <line
        className="fill-none stroke-edge-line stroke-[2.25] [stroke-linecap:round]"
        x1={edge.from.x}
        y1={edge.from.y}
        x2={edge.to.x}
        y2={edge.to.y}
      />
    );
  }

  const midX = edge.from.x + (edge.to.x - edge.from.x) / 2;
  const path = `M ${edge.from.x} ${edge.from.y} C ${midX} ${edge.from.y}, ${midX} ${edge.to.y}, ${edge.to.x} ${edge.to.y}`;

  return <path className="fill-none stroke-edge-line stroke-[2.25] [stroke-linecap:round]" d={path} />;
}
