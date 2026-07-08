import type {
  BranchNode,
  PaperCard,
  Point,
  TreeEdgeViewModel,
  TreeNodeViewModel,
  TreeViewModel,
  WorkspaceDocument,
} from "./types";

const ROOT_X = 48;
const BRANCH_X = 292;
const PAPER_X = 536;
const TOP_PADDING = 72;
const PAPER_ROW_HEIGHT = 88;
const BRANCH_GAP = 42;

export function normalizeWorkspaceForTree(workspace: WorkspaceDocument): TreeViewModel {
  const branches = workspace.tree.nodes.filter((node) => node.parent_id === workspace.tree.root_node_id);
  const branchPaperIds = new Map<string, string[]>();

  for (const branch of branches) {
    branchPaperIds.set(branch.node_id, paperIdsForBranch(workspace, branch));
  }

  const branchLayouts = layoutBranches(branches, branchPaperIds);
  const rootNode = {
    id: workspace.root.node_id,
    kind: "root" as const,
    title: workspace.root.label || workspace.title,
    overview: workspace.root.overview,
    surveyType: workspace.root.root_survey_type,
    suggestedReadingDirection: workspace.root.suggested_reading_direction,
    keyTerms: workspace.root.key_terms,
    openQuestions: workspace.root.open_questions,
    paperCount: workspace.root.representative_paper_ids.length,
    position: { x: ROOT_X, y: TOP_PADDING },
  };

  const nodes: TreeNodeViewModel[] = [rootNode];
  const edges: TreeEdgeViewModel[] = [];

  for (const layout of branchLayouts) {
    const branch = layout.branch;
    const paperIds = branchPaperIds.get(branch.node_id) ?? [];
    const branchNode = {
      id: branch.node_id,
      kind: "branch" as const,
      title: branch.label,
      description: branch.description,
      whyItMatters: branch.why_it_matters,
      tags: branch.tags,
      openQuestions: branch.open_questions,
      paperCount: paperIds.length,
      position: layout.branchPosition,
    };
    nodes.push(branchNode);
    edges.push(edgeBetween(rootNode.position, branchNode.position, "root", "branch"));

    paperIds.forEach((paperId, paperIndex) => {
      const paper = workspace.paper_cards[paperId];
      if (!paper) {
        return;
      }
      const paperNode = paperNodeViewModel(paper, layout.paperPositions[paperIndex]);
      nodes.push(paperNode);
      edges.push(edgeBetween(branchNode.position, paperNode.position, "branch", "paper"));
    });
  }

  return {
    workspaceId: workspace.workspace_id,
    title: workspace.title,
    paperCount: Object.keys(workspace.paper_cards).length,
    canvas: {
      width: 900,
      height: Math.max(560, lastLayoutBottom(branchLayouts) + TOP_PADDING),
    },
    nodes,
    nodesById: Object.fromEntries(nodes.map((node) => [node.id, node])),
    edges,
  };
}

function paperIdsForBranch(workspace: WorkspaceDocument, branch: BranchNode): string[] {
  const ids = new Set<string>();
  for (const paperId of branch.primary_paper_ids) {
    ids.add(paperId);
  }
  for (const path of workspace.paper_paths) {
    if (path.branch_node_id === branch.node_id) {
      for (const paperId of path.paper_ids) {
        ids.add(paperId);
      }
    }
  }
  for (const [paperId, card] of Object.entries(workspace.paper_cards)) {
    if (card.primary_tree_location?.node_id === branch.node_id) {
      ids.add(paperId);
    }
  }
  return Array.from(ids);
}

function layoutBranches(
  branches: BranchNode[],
  branchPaperIds: Map<string, string[]>,
): {
  branch: BranchNode;
  branchPosition: Point;
  paperPositions: Point[];
}[] {
  let cursorY = TOP_PADDING;

  return branches.map((branch) => {
    const paperCount = Math.max(1, branchPaperIds.get(branch.node_id)?.length ?? 0);
    const paperPositions = Array.from({ length: paperCount }, (_, paperIndex) => ({
      x: PAPER_X,
      y: cursorY + paperIndex * PAPER_ROW_HEIGHT,
    }));
    const branchPosition = {
      x: BRANCH_X,
      y: average(
        paperPositions.map((point) => point.y),
        cursorY,
      ),
    };

    cursorY += paperCount * PAPER_ROW_HEIGHT + BRANCH_GAP;

    return {
      branch,
      branchPosition,
      paperPositions,
    };
  });
}

function paperNodeViewModel(paper: PaperCard, position: Point): TreeNodeViewModel {
  return {
    id: paper.paper_id,
    kind: "paper",
    title: paper.title,
    year: paper.year,
    venue: paper.venue,
    role: paper.paper_role,
    readingStatus: paper.reading_status,
    contribution: paper.one_sentence_contribution,
    abstractPreview: previewText(paper.abstract),
    similarPaperCount: paper.similar_papers.length,
    position,
  };
}

function previewText(text: string): string {
  if (text.length <= 220) {
    return text;
  }
  return `${text.slice(0, 217).trim()}...`;
}

function average(values: number[], fallback: number): number {
  if (values.length === 0) {
    return fallback;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function lastLayoutBottom(
  layouts: {
    paperPositions: Point[];
    branchPosition: Point;
  }[],
): number {
  return layouts.reduce((bottom, layout) => {
    const paperBottom = Math.max(...layout.paperPositions.map((point) => point.y), layout.branchPosition.y);
    return Math.max(bottom, paperBottom);
  }, TOP_PADDING);
}

function edgeBetween(
  from: Point,
  to: Point,
  fromKind: "root" | "branch",
  toKind: "branch" | "paper",
): TreeEdgeViewModel {
  const fromWidth = fromKind === "root" ? 178 : 216;
  const toHeight = toKind === "branch" ? 86 : 76;
  return {
    from: {
      x: from.x + fromWidth,
      y: from.y + toHeight / 2,
    },
    to: {
      x: to.x,
      y: to.y + toHeight / 2,
    },
  };
}
