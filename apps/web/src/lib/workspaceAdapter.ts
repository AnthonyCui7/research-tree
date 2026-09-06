import type {
  BranchNode,
  BranchTreeNode,
  PaperAnalysisEntry,
  PaperCard,
  PaperContentSummary,
  PaperDetails,
  PaperPath,
  PaperReference,
  PaperStep,
  PaperTreeNode,
  PathLabelViewModel,
  Point,
  ReadingOrderEntry,
  RootTreeNode,
  TreeEdgeViewModel,
  TreeNodeId,
  TreeNodeViewModel,
  TreeViewModel,
  WorkspaceDocument,
  WorkspaceScopeSummary,
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
/** Room over a timeline row for its caption: one kicker line and a gap. */
const PATH_LABEL_HEIGHT = 20;

/** Real card heights measured from the DOM, keyed by tree node id. */
export type MeasuredNodeHeights = Record<string, number>;

type LayoutState = {
  nextY: number;
  nodes: TreeNodeViewModel[];
  edges: TreeEdgeViewModel[];
  pathLabels: PathLabelViewModel[];
  pathStarts: { branchId: string; paperNodeId: string }[];
  maxRight: number;
};

type PlacedPath = {
  path: PaperPath;
  centerY: number;
  paperNodeIds: TreeNodeId[];
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
  // "Branches" means the atomic ones: a parent branch is a grouping of its
  // children, and only leaves carry reading paths. Counting both reports the
  // same work twice.
  const atomicBranchCount = branches.filter((branch) => branch.is_leaf).length;
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
    pathLabels: [],
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

  // A root card taller than the whole tree would otherwise centre itself above
  // the canvas origin and lose its first lines to the scroll container.
  const rootCenterY = Math.max(
    TOP_PADDING + rootSize.height / 2,
    average(childCenters, TOP_PADDING + rootSize.height / 2),
  );
  const rootNode: RootTreeNode = {
    id: rootNodeViewId,
    kind: "root",
    title: workspace.root.label || workspace.title,
    overview: workspace.root.overview,
    whyItMatters: workspace.root.why_it_matters || workspace.root.overview,
    suggestedReadingDirection: workspace.root.suggested_reading_direction,
    keyTerms: workspace.root.key_terms,
    openQuestions: workspace.root.open_questions,
    branchCount: atomicBranchCount,
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
  const nodesById = Object.fromEntries(state.nodes.map((node) => [node.id, node]));
  // A paper on several paths has several cards; references and the reading
  // order point at the first one laid out.
  const nodeIdByPaperId = new Map<string, TreeNodeId>();
  for (const node of state.nodes) {
    if (node.kind === "paper" && !nodeIdByPaperId.has(node.paperId)) {
      nodeIdByPaperId.set(node.paperId, node.id);
    }
  }
  for (const node of state.nodes) {
    if (node.kind === "paper") {
      const card = workspace.paper_cards[node.paperId];
      node.readBefore = paperReferences(workspace, card?.read_before, nodeIdByPaperId);
      node.readAfter = paperReferences(workspace, card?.read_after, nodeIdByPaperId);
    }
  }

  return {
    workspaceId: workspace.workspace_id,
    title: workspace.title,
    paperCount: Object.keys(workspace.paper_cards).length,
    branchCount: atomicBranchCount,
    currentVersionHash: currentVersion,
    canvas: {
      width: Math.max(1040, state.maxRight + 48),
      height: Math.max(
        520,
        state.nextY + BOTTOM_PADDING,
        rootNode.position.y + rootSize.height + BOTTOM_PADDING,
      ),
    },
    root: rootNode,
    nodes: state.nodes,
    nodesById,
    edges: state.edges,
    scope: scopeSummary(workspace),
    readingOrder: readingOrderEntries(workspace, nodesById, nodeIdByPaperId),
    pathLabels: state.pathLabels,
  };
}

function scopeSummary(workspace: WorkspaceDocument): WorkspaceScopeSummary | null {
  const scope = workspace.scope;
  const label = typeof scope?.scope_label === "string" ? scope.scope_label.trim() : "";
  const rationale = typeof scope?.scope_rationale === "string" ? scope.scope_rationale.trim() : "";
  if (!label && !rationale) {
    return null;
  }
  return { label, rationale };
}

function readingOrderEntries(
  workspace: WorkspaceDocument,
  nodesById: Record<TreeNodeId, TreeNodeViewModel>,
  nodeIdByPaperId: Map<string, TreeNodeId>,
): ReadingOrderEntry[] {
  const items = Array.isArray(workspace.reading_order) ? workspace.reading_order : [];
  return items
    .flatMap((item): ReadingOrderEntry[] => {
      const card = workspace.paper_cards[item.paper_id];
      if (!card) {
        return [];
      }
      const nodeId = nodeIdByPaperId.get(item.paper_id) ?? null;
      const node = nodeId ? nodesById[nodeId] : undefined;
      return [
        {
          order: Number(item.order) || 0,
          paperId: item.paper_id,
          nodeId,
          title: card.title,
          authors: Array.isArray(card.authors) ? card.authors : [],
          year: card.year ?? null,
          publicationDate: card.publication_date ?? null,
          branchTitle: node?.kind === "paper" ? node.branchTitle : "",
        },
      ];
    })
    .sort((left, right) => left.order - right.order);
}

/** Ids a card names, resolved to titles; ids without a card are dropped. */
function paperReferences(
  workspace: WorkspaceDocument,
  paperIds: unknown,
  nodeIdByPaperId: Map<string, TreeNodeId>,
): PaperReference[] {
  if (!Array.isArray(paperIds)) {
    return [];
  }
  return paperIds.flatMap((paperId): PaperReference[] => {
    const card = typeof paperId === "string" ? workspace.paper_cards[paperId] : undefined;
    if (!card || typeof paperId !== "string") {
      return [];
    }
    return [{ paperId, title: card.title, nodeId: nodeIdByPaperId.get(paperId) ?? null }];
  });
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
  // Everything the subtree places lands after these marks, so the whole group
  // can be nudged down as one once the branch card's own height is known.
  const subtreeNodeStart = state.nodes.length;
  const subtreeEdgeStart = state.edges.length;
  const subtreeLabelStart = state.pathLabels.length;
  const spanTop = state.nextY;
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
  const placedPaths = (pathsByBranch.get(branch.node_id) ?? []).flatMap((path) => {
    const placed = layoutPath({ workspace, branch, branchX, path, familyByBranch, measuredHeights, state });
    return placed ? [placed] : [];
  });
  const childOrPathCenters = [...childCenters, ...placedPaths.map((placed) => placed.centerY)];
  const branchAnchor = anchorPaper(
    workspace,
    branch.survey_anchor_paper_id ? [branch.survey_anchor_paper_id] : [],
  );
  const branchSize = branchNodeSize(branch, branchAnchor, measuredHeights?.[branch.node_id]);
  // The branch's papers were pushed by layoutPath above, so they can be counted
  // here rather than threaded back out of every path.
  const branchPaperCount = state.nodes.reduce(
    (total, node) => total + (node.kind === "paper" && node.branchId === branch.node_id ? 1 : 0),
    0,
  );
  const centerY = reserveBranchRow({
    state,
    branchHeight: branchSize.height,
    childOrPathCenters,
    spanTop,
    subtreeNodeStart,
    subtreeEdgeStart,
    subtreeLabelStart,
  });
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
    paperCount: branchPaperCount,
    paths: placedPaths.map(({ path, paperNodeIds }) => ({
      pathId: path.path_id,
      label: path.label?.trim() ?? "",
      description: path.description?.trim() ?? "",
      rationale: path.rationale?.trim() ?? "",
      paperNodeIds,
    })),
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

/**
 * Places a branch card on its subtree and reserves the vertical room it needs.
 *
 * A branch is centred on the papers it introduces, but it is not necessarily
 * shorter than them: a branch carrying a survey block can stand taller than its
 * single-paper row. Reserving only the papers' height let such a card spill into
 * the row below and sit flush against the next branch. The card's own height is
 * therefore folded into the reservation — pushing the subtree down when the card
 * overhangs the top, and extending `nextY` when it overhangs the bottom.
 */
function reserveBranchRow({
  state,
  branchHeight,
  childOrPathCenters,
  spanTop,
  subtreeNodeStart,
  subtreeEdgeStart,
  subtreeLabelStart,
}: {
  state: LayoutState;
  branchHeight: number;
  childOrPathCenters: number[];
  spanTop: number;
  subtreeNodeStart: number;
  subtreeEdgeStart: number;
  subtreeLabelStart: number;
}): number {
  if (childOrPathCenters.length === 0) {
    state.nextY = spanTop + branchHeight + ROW_GAP;
    return spanTop + branchHeight / 2;
  }
  const spanBottom = state.nextY - ROW_GAP;
  const rawCenterY = average(childOrPathCenters, spanTop + branchHeight / 2);
  const overhangAbove = Math.max(0, spanTop - (rawCenterY - branchHeight / 2));
  if (overhangAbove > 0) {
    shiftPlacedSubtree(state, subtreeNodeStart, subtreeEdgeStart, subtreeLabelStart, overhangAbove);
  }
  const centerY = rawCenterY + overhangAbove;
  state.nextY =
    Math.max(spanBottom + overhangAbove, centerY + branchHeight / 2) + ROW_GAP;
  return centerY;
}

/**
 * Moves everything a subtree has already placed. Timeline edges and path
 * captions carry resolved points rather than node references, so they have to
 * travel with their nodes; tree edges are derived from final positions once
 * layout is done.
 */
function shiftPlacedSubtree(
  state: LayoutState,
  nodeStart: number,
  edgeStart: number,
  labelStart: number,
  distance: number,
): void {
  for (let index = nodeStart; index < state.nodes.length; index += 1) {
    state.nodes[index]!.position.y += distance;
  }
  for (let index = edgeStart; index < state.edges.length; index += 1) {
    const edge = state.edges[index]!;
    edge.from.y += distance;
    edge.to.y += distance;
  }
  for (let index = labelStart; index < state.pathLabels.length; index += 1) {
    state.pathLabels[index]!.position.y += distance;
  }
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
}): PlacedPath | null {
  const whyReadHereById = new Map(
    (Array.isArray(path.paper_steps) ? path.paper_steps : []).map((step) => [
      step.paper_id,
      step.why_read_here ?? "",
    ]),
  );
  const stepPapers = pathPaperIds(path).flatMap((paperId) => {
    const paper = workspace.paper_cards[paperId];
    return paper && !isSurveyPaper(paper) ? [paper] : [];
  });
  const [firstPaperNode] = stepPapers;
  if (firstPaperNode === undefined) {
    return null;
  }

  // The caption sits over the row, so the row starts below it.
  const caption = path.label?.trim() ?? "";
  const captionHeight = caption ? PATH_LABEL_HEIGHT : 0;
  const paperY = state.nextY + captionHeight;
  const paperX = branchX + BRANCH_WIDTH + COLUMN_GAP;
  const family = familyByBranch.get(branch.node_id) ?? null;
  const paperNodes = stepPapers.map((paper, index) =>
    paperNodeViewModel({
      paper,
      path,
      pathLabel: caption,
      branch,
      index,
      readingLength: stepPapers.length,
      whyReadHere: whyReadHereById.get(paper.paper_id) ?? "",
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

  if (caption) {
    state.pathLabels.push({
      id: `path-label:${path.path_id}`,
      pathId: path.path_id,
      text: caption,
      position: { x: paperX, y: state.nextY },
      width: lastNode.position.x + lastNode.size.width - paperX,
    });
  }

  state.maxRight = Math.max(state.maxRight, lastNode.position.x + lastNode.size.width);
  state.nextY += captionHeight + pathHeight + ROW_GAP;
  return { path, centerY: timelineCenterY, paperNodeIds: paperNodes.map((node) => node.id) };
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
  pathLabel,
  branch,
  index,
  readingLength,
  whyReadHere,
  family,
  measuredHeights,
  position,
}: {
  paper: PaperCard;
  path: PaperPath;
  pathLabel: string;
  branch: BranchNode;
  index: number;
  readingLength: number;
  whyReadHere: string;
  family: number | null;
  measuredHeights?: MeasuredNodeHeights;
  position: Point;
}): PaperTreeNode {
  const id = `paper:${path.path_id}:${index + 1}:${paper.paper_id}`;
  return {
    id,
    kind: "paper",
    family,
    branchId: branch.node_id,
    branchTitle: branch.label,
    readingIndex: index + 1,
    readingLength,
    whyReadHere: whyReadHere.trim(),
    pathId: path.path_id,
    pathLabel,
    // Resolved once every card is placed, so a reference can point at its card.
    readBefore: [],
    readAfter: [],
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
    paperId: paper.paper_id,
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
    secondaryTags: Array.isArray(paper.secondary_tags)
      ? paper.secondary_tags.filter((tag): tag is string => typeof tag === "string" && tag.trim() !== "")
      : [],
    analysis: paperAnalysis(paper),
    similarPapers: Array.isArray(paper.similar_papers) ? paper.similar_papers : [],
    content: paperContentSummary(paper),
  };
}

const ANALYSIS_FIELDS: readonly (readonly [keyof PaperCard, string])[] = [
  ["problem", "Problem"],
  ["core_idea", "Core idea"],
  ["method", "Method"],
  ["assumptions", "Assumptions"],
  ["datasets_or_benchmarks", "Datasets and benchmarks"],
  ["results", "Results"],
  ["limitations", "Limitations"],
];

/** The card's analysis fields that hold text, in the order they are read. */
function paperAnalysis(paper: PaperCard): PaperAnalysisEntry[] {
  return ANALYSIS_FIELDS.flatMap(([field, label]) => {
    const value = paper[field];
    const body = typeof value === "string" ? value.trim() : "";
    return body ? [{ label, body }] : [];
  });
}

/**
 * Enrichment records the open-access PDF it managed to reach, if any. A card
 * with no record — or one whose retrieval failed — has no PDF to offer, which
 * is what disables the download and the reader for that paper.
 */
function paperContentSummary(paper: PaperCard): PaperContentSummary | null {
  const content = paper.paper_content;
  if (!content) {
    return null;
  }
  const sourceUrl = content.source_url?.trim() || null;
  const status = content.status?.trim() || "unavailable";
  if (!status.startsWith("available") && !sourceUrl) {
    return null;
  }
  return {
    status,
    sourceUrl,
    pageCount: typeof content.page_count === "number" ? content.page_count : null,
    truncated: Boolean(content.truncated),
  };
}

function arxivLink(arxivId: string | null): string | null {
  return arxivId ? `https://arxiv.org/abs/${arxivId}` : null;
}

function isSurveyPaper(paper: PaperCard): boolean {
  return paper.paper_role.toLowerCase().includes("survey");
}

/**
 * A branch with papers but no path still gets a row. The row has nothing to say
 * for itself, so it carries no caption and the branch panel titles it generically
 * rather than repeating the branch's own description under a second heading.
 */
function fallbackPath(branch: BranchNode): PaperPath {
  return {
    path_id: `fallback-${branch.node_id}`,
    branch_node_id: branch.node_id,
    path_type: "primary_timeline",
    label: "",
    description: "",
    paper_ids: branch.primary_paper_ids,
    rationale: "",
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
    estimatedTextHeight("Research topic", 13.65, 31),
    estimatedTextHeight(root.label, 23.52, 31),
    estimatedTextHeight(root.overview, 18, 54),
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
    estimatedTextHeight("Research branch", 13.65, 34),
    estimatedTextHeight(branch.label, 19.1, 34),
    Math.min(3, fallbackLineCount(branch.description, 43, 0)) * 18,
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
    estimatedTextHeight(paper.title, 19.1, 39),
    estimatedTextHeight(`${compactAuthorLine(paper.authors)} · Date`, 14.85, 42),
    // Card body copy is clamped to three lines; see TreeNode.
    Math.min(3, fallbackLineCount(paper.tldr || "Unavailable", 46, 6)) * 18,
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
  const labelAndYearHeight = 13.65 + 14.85;
  const anchorGaps = 3 * 2;
  // Top margin, rule padding and the rule itself, less the stacking gap already
  // counted by stackedNodeHeight.
  const anchorTopRule = 10 + 9 + 1 - 6;
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
