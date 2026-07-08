import type { TreeEdgeViewModel } from "../../lib/types";

type TreeEdgeProps = {
  edge: TreeEdgeViewModel;
};

export function TreeEdge({ edge }: TreeEdgeProps) {
  const midX = edge.from.x + (edge.to.x - edge.from.x) / 2;
  const path = `M ${edge.from.x} ${edge.from.y} C ${midX} ${edge.from.y}, ${midX} ${edge.to.y}, ${edge.to.x} ${edge.to.y}`;

  return <path className="tree-edge" d={path} />;
}
