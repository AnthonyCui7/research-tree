import type {
  BranchNode,
  BranchTreeNode,
  PaperCard,
  PaperDetails,
  PaperPath,
  PaperStep,
  PaperTreeNode,
  Point,
  RootTreeNode,
  TreeEdgeViewModel,
  TreeNodeViewModel,
  TreeViewModel,
  WorkspaceDocument,
} from "./types";

const ROOT_POSITION_X = 32;
const ROOT_WIDTH = 408;
const BRANCH_WIDTH = 292;
const PAPER_WIDTH = 324;
const COLUMN_GAP = 72;
const ROW_GAP = 32;
const TOP_PADDING = 36;
const BOTTOM_PADDING = 48;
const BRANCH_FAMILY_COUNT = 8;
const NODE_PADDING = 14;
const NODE_BORDER = 2;
const NODE_GAP = 6;
const NODE_VERTICAL_GUARD = 12;

/** Real card heights measured from the DOM, keyed by tree node id. */
export type MeasuredNodeHeights = Record<string, number>;

type LayoutState = {
  nextY: number;
  nodes: TreeNodeViewModel[];
  edges: TreeEdgeViewModel[];
  pathStarts: { branchId: string; paperNodeId: string }[];
  maxRight: number;
};

/**
 * Builds the tree view model. Without `measuredHeights`, node heights are
 * character-count estimates — good enough for an unpainted first pass. The
 * canvas re-runs this with real DOM-measured card heights (see TreeCanvas),
 * which is the layout that actually paints.
 */
export function normalizeWorkspaceForTree(
  workspace: WorkspaceDocument,
  measuredHeights?: MeasuredNodeHeights,
): TreeViewModel {
  const branches = workspace.tree.nodes.filter((node) => Boolean(node.node_id));
  const branchesById = new Map(branches.map((branch) => [branch.node_id, branch]));
  const childrenByParent = childBranches(branches, workspace.tree.root_node_id);
  const pathsByBranch = pathsGroupedByBranch(workspace, branches, childrenByParent);
  const familyByBranch = new Map(
    branches
      .filter((branch) => branch.is_leaf)
      .map((branch, index) => [branch.node_id, index % BRANCH_FAMILY_COUNT]),
  );
  const rootId = workspace.tree.root_node_id || workspace.root.node_id || "root";
  const rootNodeViewId = workspace.root.node_id || rootId;
  const rootSize = rootNodeSize(
    workspace.root,
    anchorPaper(workspace, workspace.root.survey_anchor_paper_ids),
    measuredHeights?.[rootNodeViewId],
  );
  const state: LayoutState = {
    nextY: TOP_PADDING,
    nodes: [],
    edges: [],
    pathStarts: [],
    maxRight: ROOT_POSITION_X + rootSize.width,
  };

  const rootChildren = childrenByParent.get(rootId) ?? [];
  const childCenters = rootChildren
    .map((branchId) => layoutBranch({
      workspace,
      branchId,
      depth: 1,
      branchesById,
      childrenByParent,
      pathsByBranch,
      familyByBranch,
      measuredHeights,
      state,
    }))
    .filter((center): center is number => center !== null);

  const rootCenterY = average(childCenters, TOP_PADDING + rootSize.height / 2);
  const rootNode: RootTreeNode = {
    id: rootNodeViewId,
    kind: "root",
    title: workspace.root.label || workspace.title,
    overview: workspace.root.overview,
    whyItMatters: workspace.root.why_it_matters || workspace.root.overview,
    suggestedReadingDirection: workspace.root.suggested_reading_direction,
    keyTerms: workspace.root.key_terms,
    openQuestions: workspace.root.open_questions,
    branchCount: branches.length,
    anchorPaper: anchorPaper(workspace, workspace.root.survey_anchor_paper_ids),
    position: {
      x: ROOT_POSITION_X,
      y: rootCenterY - rootSize.height / 2,
    },
    size: rootSize,
  };
  state.nodes.unshift(rootNode);

  for (const branch of branches) {
    const branchNode = state.nodes.find(
      (node): node is BranchTreeNode => node.kind === "branch" && node.branchNodeId === branch.node_id,
    );
    if (!branchNode) {
      continue;
    }
    const parentNode =
      branch.parent_id === rootId || branch.parent_id === "root"
        ? rootNode
        : state.nodes.find(
            (node): node is BranchTreeNode =>
              node.kind === "branch" && node.branchNodeId === branch.parent_id,
          );
    if (parentNode) {
      state.edges.push(edgeBetween(parentNode, branchNode));
    }
  }
  for (const pathStart of state.pathStarts) {
    const branchNode = state.nodes.find(
      (node): node is BranchTreeNode =>
        node.kind === "branch" && node.branchNodeId === pathStart.branchId,
    );
    const paperNode = state.nodes.find(
      (node): node is PaperTreeNode => node.kind === "paper" && node.id === pathStart.paperNodeId,
    );
    if (branchNode && paperNode) {
      state.edges.push(edgeBetween(branchNode, paperNode, "timeline"));
    }
  }

  const currentVersion = workspace.current_workspace_version_hash ?? null;

  return {
    workspaceId: workspace.workspace_id,
    title: workspace.title,
    paperCount: Object.keys(workspace.paper_cards).length,
    branchCount: branches.length,
    currentVersionHash: currentVersion,
    canvas: {
      width: Math.max(1040, state.maxRight + 48),
      height: Math.max(520, state.nextY + BOTTOM_PADDING),
    },
    root: rootNode,
    nodes: state.nodes,
    nodesById: Object.fromEntries(state.nodes.map((node) => [node.id, node])),
    edges: state.edges,
  };
}

function layoutBranch({
  workspace,
  branchId,
  depth,
  branchesById,
  childrenByParent,
  pathsByBranch,
  familyByBranch,
  measuredHeights,
  state,
}: {
  workspace: WorkspaceDocument;
  branchId: string;
  depth: number;
  branchesById: Map<string, BranchNode>;
  childrenByParent: Map<string, string[]>;
  pathsByBranch: Map<string, PaperPath[]>;
  familyByBranch: Map<string, number>;
  measuredHeights?: MeasuredNodeHeights;
  state: LayoutState;
}): number | null {
  const branch = branchesById.get(branchId);
  if (!branch) {
    return null;
  }

  const branchX = branchPositionX(depth);
  const childCenters = (childrenByParent.get(branch.node_id) ?? [])
    .map((childId) =>
      layoutBranch({
        workspace,
        branchId: childId,
        depth: depth + 1,
        branchesById,
        childrenByParent,
        pathsByBranch,
        familyByBranch,
        measuredHeights,
        state,
      }),
    )
    .filter((center): center is number => center !== null);
  const paths = pathsByBranch.get(branch.node_id) ?? [];
  const pathCenters = paths
    .map((path) => layoutPath({ workspace, branch, branchX, path, familyByBranch, measuredHeights, state }))
    .filter((center): center is number => center !== null);
  const childOrPathCenters = [...childCenters, ...pathCenters];
  const branchAnchor = anchorPaper(
    workspace,
    branch.survey_anchor_paper_id ? [branch.survey_anchor_paper_id] : [],
  );
  const branchSize = branchNodeSize(branch, branchAnchor, measuredHeights?.[branch.node_id]);
  const fallbackCenterY = state.nextY + branchSize.height / 2;
  if (childOrPathCenters.length === 0) {
    state.nextY += branchSize.height + ROW_GAP;
  }
  const centerY = average(childOrPathCenters, fallbackCenterY);
  const branchNode: BranchTreeNode = {
    id: branch.node_id,
    kind: "branch",
    family: branch.is_leaf
      ? familyByBranch.get(branch.node_id) ?? null
      : (childrenByParent.get(branch.node_id)?.length ?? 0) > 0
        ? "group"
        : null,
    branchNodeId: branch.node_id,
    title: branch.label,
    description: branch.description,
    whyItMatters: branch.why_it_matters,
    tags: branch.tags,
    openQuestions: branch.open_questions,
    anchorPaper: branchAnchor,
    position: {
      x: branchX,
      y: centerY - branchSize.height / 2,
    },
    size: branchSize,
  };
  state.nodes.push(branchNode);
  state.maxRight = Math.max(state.maxRight, branchX + branchSize.width);
  return centerY;
}

function layoutPath({
  workspace,
  branch,
  branchX,
  path,
  familyByBranch,
  measuredHeights,
  state,
}: {
  workspace: WorkspaceDocument;
  branch: BranchNode;
  branchX: number;
  path: PaperPath;
  familyByBranch: Map<string, number>;
  measuredHeights?: MeasuredNodeHeights;
  state: LayoutState;
}): number | null {
  const stepPapers = pathPaperIds(path).flatMap((paperId) => {
    const paper = workspace.paper_cards[paperId];
    return paper && !isSurveyPaper(paper) ? [paper] : [];
  });
  const [firstPaperNode] = stepPapers;
  if (firstPaperNode === undefined) {
    return null;
  }

  const paperY = state.nextY;
  const paperX = branchX + BRANCH_WIDTH + COLUMN_GAP;
  const family = familyByBranch.get(branch.node_id) ?? null;
  const paperNodes = stepPapers.map((paper, index) =>
    paperNodeViewModel({
      paper,
      path,
      index,
      family,
      measuredHeights,
      position: {
        x: paperX + index * (PAPER_WIDTH + COLUMN_GAP),
        y: paperY,
      },
    }),
  );
  const pathHeight = Math.max(...paperNodes.map((node) => node.size.height));
  const timelineCenterY = paperY + pathHeight / 2;
  for (const paperNode of paperNodes) {
    paperNode.position.y = timelineCenterY - paperNode.size.height / 2;
  }

  state.nodes.push(...paperNodes);
  // paperNodes mirrors stepPapers, which the guard above proved non-empty.
  const firstNode = paperNodes[0]!;
  const lastNode = paperNodes[paperNodes.length - 1]!;
  state.pathStarts.push({ branchId: branch.node_id, paperNodeId: firstNode.id });
  for (let index = 1; index < paperNodes.length; index += 1) {
    state.edges.push(edgeBetween(paperNodes[index - 1]!, paperNodes[index]!, "timeline"));
  }

  state.maxRight = Math.max(state.maxRight, lastNode.position.x + lastNode.size.width);
  state.nextY += pathHeight + ROW_GAP;
  return timelineCenterY;
}

function childBranches(
  branches: BranchNode[],
  rootId: string,
): Map<string, string[]> {
  const children = new Map<string, string[]>();
  for (const branch of branches) {
    const parentId = branch.parent_id || rootId;
    const childIds = children.get(parentId) ?? [];
    childIds.push(branch.node_id);
    children.set(parentId, childIds);
  }
  return children;
}

function pathsGroupedByBranch(
  workspace: WorkspaceDocument,
  branches: BranchNode[],
  childrenByParent: Map<string, string[]>,
): Map<string, PaperPath[]> {
  const paths = new Map<string, PaperPath[]>();
  for (const path of workspace.paper_paths) {
    const branchPaths = paths.get(path.branch_node_id) ?? [];
    branchPaths.push(path);
    paths.set(path.branch_node_id, branchPaths);
  }
  for (const branch of branches) {
    if (paths.has(branch.node_id) || (childrenByParent.get(branch.node_id)?.length ?? 0) > 0) {
      continue;
    }
    if (branch.primary_paper_ids.length > 0) {
      paths.set(branch.node_id, [fallbackPath(branch)]);
    }
  }
  return paths;
}

/** A path's papers in reading order; explicit steps win over the flat id list. */
function pathPaperIds(path: PaperPath): string[] {
  const explicitSteps = Array.isArray(path.paper_steps) ? [...path.paper_steps] : [];
  if (explicitSteps.length > 0) {
    return explicitSteps.sort(legacyStepOrder).map((step) => step.paper_id);
  }
  return path.paper_ids;
}

function legacyStepOrder(left: PaperStep, right: PaperStep): number {
  const leftIndex = Number((left as PaperStep & { step_index?: unknown }).step_index);
  const rightIndex = Number((right as PaperStep & { step_index?: unknown }).step_index);
  if (Number.isFinite(leftIndex) && Number.isFinite(rightIndex)) {
    return leftIndex - rightIndex;
  }
  return 0;
}

function paperNodeViewModel({
  paper,
  path,
  index,
  family,
  measuredHeights,
  position,
}: {
  paper: PaperCard;
  path: PaperPath;
  index: number;
  family: number | null;
  measuredHeights?: MeasuredNodeHeights;
  position: Point;
}): PaperTreeNode {
  const id = `paper:${path.path_id}:${index + 1}:${paper.paper_id}`;
  return {
    id,
    kind: "paper",
    family,
    ...paperDetails(paper),
    position,
    size: paperNodeSize(paper, measuredHeights?.[id]),
  };
}

function anchorPaper(workspace: WorkspaceDocument, paperIds: string[]): PaperDetails | null {
  for (const paperId of paperIds) {
    const paper = workspace.paper_cards[paperId];
    if (paper) {
      return paperDetails(paper);
    }
  }
  return null;
}

function paperDetails(paper: PaperCard): PaperDetails {
  return {
    title: paper.title,
    authors: Array.isArray(paper.authors) ? paper.authors : [],
    year: paper.year ?? null,
    publicationDate: paper.publication_date ?? null,
    venue: paper.venue || "",
    arxivLink: paper.arxiv_link ?? arxivLink(paper.arxiv_id),
    semanticScholarLink: paper.s2_link ?? null,
    citationCount: paper.citation_count ?? null,
    tldr: paper.tldr?.trim() || null,
    importance: paper.importance?.trim() || "",
    abstract: paper.abstract || "",
    similarPapers: Array.isArray(paper.similar_papers) ? paper.similar_papers : [],
  };
}

function arxivLink(arxivId: string | null): string | null {
  return arxivId ? `https://arxiv.org/abs/${arxivId}` : null;
}

function isSurveyPaper(paper: PaperCard): boolean {
  return paper.paper_role.toLowerCase().includes("survey");
}

function fallbackPath(branch: BranchNode): PaperPath {
  return {
    path_id: `fallback-${branch.node_id}`,
    branch_node_id: branch.node_id,
    path_type: "primary_timeline",
    label: "Reading sequence",
    description: branch.description,
    paper_ids: branch.primary_paper_ids,
    rationale: branch.why_it_matters,
  };
}

function branchPositionX(depth: number): number {
  return ROOT_POSITION_X + ROOT_WIDTH + COLUMN_GAP + (depth - 1) * (BRANCH_WIDTH + COLUMN_GAP);
}

function rootNodeSize(
  root: WorkspaceDocument["root"],
  anchor: PaperDetails | null,
  measuredHeight?: number,
) {
  if (measuredHeight) {
    return { width: ROOT_WIDTH, height: measuredHeight };
  }
  const contentHeights = [
    estimatedTextHeight("Research topic", 14.3, 31),
    estimatedTextHeight(root.label, 23.52, 31),
    estimatedTextHeight(root.overview, 17.4, 54),
    ...(anchor ? [anchorSummaryEstimate(anchor.title, 46)] : []),
  ];
  return {
    width: ROOT_WIDTH,
    height: stackedNodeHeight(contentHeights),
  };
}

function branchNodeSize(
  branch: BranchNode,
  anchor: PaperDetails | null,
  measuredHeight?: number,
) {
  if (measuredHeight) {
    return { width: BRANCH_WIDTH, height: measuredHeight };
  }
  const contentHeights = [
    estimatedTextHeight("Research branch", 14.3, 34),
    estimatedTextHeight(branch.label, 18.2, 34),
    estimatedTextHeight(branch.description, 17.4, 43),
    ...(anchor ? [anchorSummaryEstimate(anchor.title, 40)] : []),
  ];
  return {
    width: BRANCH_WIDTH,
    height: stackedNodeHeight(contentHeights),
  };
}

function paperNodeSize(paper: PaperCard, measuredHeight?: number) {
  if (measuredHeight) {
    return { width: PAPER_WIDTH, height: measuredHeight };
  }
  const contentHeights = [
    estimatedTextHeight(paper.title, 18.2, 39),
    estimatedTextHeight(compactAuthorLine(paper.authors), 16.2, 42),
    estimatedTextHeight("Date", 14.3, 42),
    estimatedTextHeight(paper.tldr || "Unavailable", 17.04, 46, 6),
  ];
  return {
    width: PAPER_WIDTH,
    height: stackedNodeHeight(contentHeights),
  };
}

function stackedNodeHeight(contentHeights: number[]): number {
  const contentHeight = contentHeights.reduce((total, height) => total + height, 0);
  const gapHeight = Math.max(0, contentHeights.length - 1) * NODE_GAP;
  return Math.ceil(NODE_BORDER + NODE_PADDING * 2 + contentHeight + gapHeight + NODE_VERTICAL_GUARD);
}

function anchorSummaryEstimate(title: string, fallbackCharactersPerLine: number): number {
  const labelAndYearHeight = 13.5 * 2;
  const anchorGaps = 3 * 2;
  const anchorTopRule = 8 + 1;
  return anchorTopRule + labelAndYearHeight + anchorGaps + estimatedTextHeight(
    title,
    14.85,
    fallbackCharactersPerLine,
  );
}

function estimatedTextHeight(
  value: string,
  lineHeight: number,
  charactersPerLine: number,
  firstLinePrefixCharacters = 0,
): number {
  if (!value) {
    return 0;
  }
  return fallbackLineCount(value, charactersPerLine, firstLinePrefixCharacters) * lineHeight;
}

function fallbackLineCount(value: string, charactersPerLine: number, firstLinePrefixCharacters: number): number {
  return value.split(/\r?\n/).reduce((total, line) => {
    const firstLineCharacters = Math.max(1, charactersPerLine - firstLinePrefixCharacters);
    if (!line) {
      return total + 1;
    }
    if (line.length <= firstLineCharacters) {
      return total + 1;
    }
    return total + 1 + Math.ceil((line.length - firstLineCharacters) / charactersPerLine);
  }, 0);
}

function compactAuthorLine(authors: string[]): string {
  const [first, second] = authors;
  if (first === undefined) {
    return "Authors unavailable";
  }
  if (second === undefined) {
    return first;
  }
  if (authors.length === 2) {
    return `${first} & ${second}`;
  }
  return `${first} et al.`;
}

function edgeBetween(
  from: Pick<RootTreeNode | BranchTreeNode | PaperTreeNode, "position" | "size">,
  to: Pick<RootTreeNode | BranchTreeNode | PaperTreeNode, "position" | "size">,
  kind: TreeEdgeViewModel["kind"] = "tree",
): TreeEdgeViewModel {
  return {
    from: nodeRightCenter(from),
    to: nodeLeftCenter(to),
    kind,
  };
}

function nodeRightCenter(
  node: Pick<RootTreeNode | BranchTreeNode | PaperTreeNode, "position" | "size">,
): Point {
  return {
    x: node.position.x + node.size.width,
    y: node.position.y + node.size.height / 2,
  };
}

function nodeLeftCenter(
  node: Pick<RootTreeNode | BranchTreeNode | PaperTreeNode, "position" | "size">,
): Point {
  return {
    x: node.position.x,
    y: node.position.y + node.size.height / 2,
  };
}

function average(values: number[], fallback: number): number {
  if (values.length === 0) {
    return fallback;
  }
  return values.reduce((total, value) => total + value, 0) / values.length;
}
