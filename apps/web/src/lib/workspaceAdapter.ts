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
  TreePathLabelViewModel,
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
const NODE_FONT_FAMILY = "Inter, sans-serif";
let cachedTextMeasureContext: CanvasRenderingContext2D | null | undefined;

type LayoutState = {
  nextY: number;
  nodes: TreeNodeViewModel[];
  edges: TreeEdgeViewModel[];
  pathLabels: TreePathLabelViewModel[];
  pathStarts: { branchId: string; paperNodeId: string }[];
  paperCountByBranch: Map<string, number>;
  maxRight: number;
};

export function normalizeWorkspaceForTree(workspace: WorkspaceDocument): TreeViewModel {
  const branches = workspace.tree.nodes.filter((node) => Boolean(node.node_id));
  const branchesById = new Map(branches.map((branch) => [branch.node_id, branch]));
  const childrenByParent = childBranches(branches, workspace.tree.root_node_id);
  const pathsByBranch = pathsGroupedByBranch(workspace, branches, childrenByParent);
  const familyByBranch = new Map(
    branches
      .filter((branch) => branch.is_leaf)
      .map((branch, index) => [branch.node_id, index % BRANCH_FAMILY_COUNT]),
  );
  const rootSize = rootNodeSize(workspace.root, anchorPaper(workspace, workspace.root.survey_anchor_paper_ids));
  const state: LayoutState = {
    nextY: TOP_PADDING,
    nodes: [],
    edges: [],
    pathLabels: [],
    pathStarts: [],
    paperCountByBranch: new Map(),
    maxRight: ROOT_POSITION_X + rootSize.width,
  };

  const rootId = workspace.tree.root_node_id || workspace.root.node_id || "root";
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
      state,
    }))
    .filter((center): center is number => center !== null);

  const rootCenterY = average(childCenters, TOP_PADDING + rootSize.height / 2);
  const rootNode: RootTreeNode = {
    id: workspace.root.node_id || rootId,
    kind: "root",
    title: workspace.root.label || workspace.title,
    overview: workspace.root.overview,
    whyItMatters: workspace.root.why_it_matters || workspace.root.overview,
    surveyType: workspace.root.root_survey_type,
    suggestedReadingDirection: workspace.root.suggested_reading_direction,
    keyTerms: workspace.root.key_terms,
    openQuestions: workspace.root.open_questions,
    paperCount: workspace.root.representative_paper_ids.length,
    branchCount: branches.length,
    pathCount: workspace.paper_paths.length,
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

  const currentVersion =
    workspace.current_workspace_version_hash ??
    workspace.workspace_versions?.find((version) => version.is_current)?.version_hash ??
    null;

  return {
    workspaceId: workspace.workspace_id,
    title: workspace.title,
    paperCount: Object.keys(workspace.paper_cards).length,
    branchCount: branches.length,
    pathCount: workspace.paper_paths.length,
    currentVersionHash: currentVersion,
    versionCount: workspace.workspace_versions?.length ?? 0,
    canvas: {
      width: Math.max(1040, state.maxRight + 48),
      height: Math.max(520, state.nextY + BOTTOM_PADDING),
    },
    root: rootNode,
    nodes: state.nodes,
    nodesById: Object.fromEntries(state.nodes.map((node) => [node.id, node])),
    edges: state.edges,
    pathLabels: state.pathLabels,
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
  state,
}: {
  workspace: WorkspaceDocument;
  branchId: string;
  depth: number;
  branchesById: Map<string, BranchNode>;
  childrenByParent: Map<string, string[]>;
  pathsByBranch: Map<string, PaperPath[]>;
  familyByBranch: Map<string, number>;
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
        state,
      }),
    )
    .filter((center): center is number => center !== null);
  const paths = pathsByBranch.get(branch.node_id) ?? [];
  const pathCenters = paths
    .map((path) => layoutPath({ workspace, branch, branchX, path, familyByBranch, state }))
    .filter((center): center is number => center !== null);
  const childOrPathCenters = [...childCenters, ...pathCenters];
  const branchAnchor = anchorPaper(
    workspace,
    branch.survey_anchor_paper_id ? [branch.survey_anchor_paper_id] : [],
  );
  const branchSize = branchNodeSize(branch, branchAnchor);
  const fallbackCenterY = state.nextY + branchSize.height / 2;
  if (childOrPathCenters.length === 0) {
    state.nextY += branchSize.height + ROW_GAP;
  }
  const centerY = average(childOrPathCenters, fallbackCenterY);
  const paperCount = state.paperCountByBranch.get(branch.node_id) ?? 0;
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
    breadcrumb: branchBreadcrumb(branch, branchesById, workspace.root.label || workspace.title),
    tags: branch.tags,
    openQuestions: branch.open_questions,
    paperCount,
    pathCount: paths.length,
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
  state,
}: {
  workspace: WorkspaceDocument;
  branch: BranchNode;
  branchX: number;
  path: PaperPath;
  familyByBranch: Map<string, number>;
  state: LayoutState;
}): number | null {
  const steps = paperSteps(workspace, path).filter((step) => {
    const paper = workspace.paper_cards[step.paper_id];
    return paper && !isSurveyPaper(paper);
  });
  if (steps.length === 0) {
    return null;
  }

  const paperY = state.nextY;
  const paperX = branchX + BRANCH_WIDTH + COLUMN_GAP;
  const family = familyByBranch.get(branch.node_id) ?? null;
  const paperNodes = steps.map((step, index) =>
    paperNodeViewModel({
      paper: workspace.paper_cards[step.paper_id],
      path,
      step,
      index,
      family,
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
  state.paperCountByBranch.set(
    branch.node_id,
    (state.paperCountByBranch.get(branch.node_id) ?? 0) + paperNodes.length,
  );
  state.pathLabels.push({
    id: `path:${path.path_id}`,
    label: path.label,
    description: path.description,
    position: { x: paperX, y: Math.max(10, paperY - 20) },
  });

  state.pathStarts.push({ branchId: branch.node_id, paperNodeId: paperNodes[0].id });
  for (let index = 1; index < paperNodes.length; index += 1) {
    state.edges.push(edgeBetween(paperNodes[index - 1], paperNodes[index], "timeline"));
  }

  state.maxRight = Math.max(
    state.maxRight,
    paperNodes[paperNodes.length - 1].position.x + paperNodes[paperNodes.length - 1].size.width,
  );
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

function paperSteps(workspace: WorkspaceDocument, path: PaperPath): PaperStep[] {
  const explicitSteps = Array.isArray(path.paper_steps) ? [...path.paper_steps] : [];
  if (explicitSteps.length > 0) {
    return explicitSteps.sort(legacyStepOrder);
  }
  return path.paper_ids.map((paperId) => ({
    paper_id: paperId,
    why_read_here:
      workspace.paper_cards[paperId]?.importance ||
      "Part of this saved reading sequence.",
  }));
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
  step,
  index,
  family,
  position,
}: {
  paper: PaperCard;
  path: PaperPath;
  step: PaperStep;
  index: number;
  family: number | null;
  position: Point;
}): PaperTreeNode {
  return {
    id: `paper:${path.path_id}:${index + 1}:${paper.paper_id}`,
    kind: "paper",
    family,
    ...paperDetails(paper),
    whyReadHere: step.why_read_here,
    pathId: path.path_id,
    position,
    size: paperNodeSize(paper),
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
    primaryLink: paper.primary_link ?? null,
    doi: paper.doi ?? null,
    arxivId: paper.arxiv_id ?? null,
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

function branchBreadcrumb(
  branch: BranchNode,
  branchesById: Map<string, BranchNode>,
  rootLabel: string,
): string[] {
  const labels = [branch.label];
  let parentId = branch.parent_id;
  while (parentId && parentId !== "root") {
    const parent = branchesById.get(parentId);
    if (!parent) {
      break;
    }
    labels.unshift(parent.label);
    parentId = parent.parent_id;
  }
  return [rootLabel, ...labels];
}

function branchPositionX(depth: number): number {
  return ROOT_POSITION_X + ROOT_WIDTH + COLUMN_GAP + (depth - 1) * (BRANCH_WIDTH + COLUMN_GAP);
}

function rootNodeSize(root: WorkspaceDocument["root"], anchor: PaperDetails | null) {
  const contentWidth = nodeContentWidth(ROOT_WIDTH);
  const contentHeights = [
    textHeight("Research topic", contentWidth, 14.3, `600 11px ${NODE_FONT_FAMILY}`, 31),
    textHeight(root.label, contentWidth, 23.52, `700 21px ${NODE_FONT_FAMILY}`, 31),
    textHeight(root.overview, contentWidth, 17.4, `400 12px ${NODE_FONT_FAMILY}`, 54),
    ...(anchor ? [anchorSummaryHeight(anchor.title, contentWidth, 46)] : []),
  ];
  return {
    width: ROOT_WIDTH,
    height: stackedNodeHeight(contentHeights),
  };
}

function branchNodeSize(branch: BranchNode, anchor: PaperDetails | null) {
  const contentWidth = nodeContentWidth(BRANCH_WIDTH);
  const contentHeights = [
    textHeight("Research branch", contentWidth, 14.3, `600 11px ${NODE_FONT_FAMILY}`, 34),
    textHeight(branch.label, contentWidth, 18.2, `700 14px ${NODE_FONT_FAMILY}`, 34),
    textHeight(branch.description, contentWidth, 17.4, `400 12px ${NODE_FONT_FAMILY}`, 43),
    ...(anchor ? [anchorSummaryHeight(anchor.title, contentWidth, 40)] : []),
  ];
  return {
    width: BRANCH_WIDTH,
    height: stackedNodeHeight(contentHeights),
  };
}

function paperNodeSize(paper: PaperCard) {
  const contentWidth = nodeContentWidth(PAPER_WIDTH);
  const tldrPrefixWidth = textWidth("TLDR", `700 11px ${NODE_FONT_FAMILY}`) + 4;
  const contentHeights = [
    textHeight(paper.title, contentWidth, 18.2, `700 14px ${NODE_FONT_FAMILY}`, 39),
    textHeight(compactAuthorLine(paper.authors), contentWidth, 16.2, `400 12px ${NODE_FONT_FAMILY}`, 42),
    textHeight("Date", contentWidth, 14.3, `400 11px ${NODE_FONT_FAMILY}`, 42),
    textHeight(
      paper.tldr || "Unavailable",
      contentWidth,
      17.04,
      `400 12px ${NODE_FONT_FAMILY}`,
      46,
      tldrPrefixWidth,
      6,
    ),
  ];
  return {
    width: PAPER_WIDTH,
    height: stackedNodeHeight(contentHeights),
  };
}

function stackedNodeHeight(contentHeights: number[]): number {
  const contentHeight = contentHeights.reduce((total, height) => total + height, 0);
  const gapHeight = Math.max(0, contentHeights.length - 1) * NODE_GAP;
  return Math.ceil(NODE_BORDER + NODE_PADDING * 2 + contentHeight + gapHeight);
}

function nodeContentWidth(width: number): number {
  return width - NODE_PADDING * 2 - NODE_BORDER;
}

function anchorSummaryHeight(title: string, width: number, fallbackCharactersPerLine: number): number {
  const labelAndYearHeight = 13.5 * 2;
  const anchorGaps = 3 * 2;
  const anchorTopRule = 8 + 1;
  return anchorTopRule + labelAndYearHeight + anchorGaps + textHeight(
    title,
    width,
    14.85,
    `600 11px ${NODE_FONT_FAMILY}`,
    fallbackCharactersPerLine,
  );
}

function textHeight(
  value: string,
  width: number,
  lineHeight: number,
  font: string,
  fallbackCharactersPerLine: number,
  firstLinePrefixWidth = 0,
  fallbackPrefixCharacters = 0,
): number {
  if (!value) {
    return 0;
  }
  const context = textMeasureContext();
  const lines = context
    ? wrappedLineCount(value, width, font, firstLinePrefixWidth, context)
    : fallbackLineCount(value, fallbackCharactersPerLine, fallbackPrefixCharacters);
  return lines * lineHeight;
}

function textWidth(value: string, font: string): number {
  const context = textMeasureContext();
  if (!context) {
    return 0;
  }
  context.font = font;
  return context.measureText(value).width;
}

function textMeasureContext(): CanvasRenderingContext2D | null {
  if (cachedTextMeasureContext !== undefined) {
    return cachedTextMeasureContext;
  }
  if (typeof document === "undefined") {
    cachedTextMeasureContext = null;
    return null;
  }
  const canvas = document.createElement("canvas");
  cachedTextMeasureContext = canvas.getContext("2d");
  return cachedTextMeasureContext;
}

function wrappedLineCount(
  value: string,
  width: number,
  font: string,
  firstLinePrefixWidth: number,
  context: CanvasRenderingContext2D,
): number {
  context.font = font;
  const spaceWidth = context.measureText(" ").width;
  return value.split(/\r?\n/).reduce((total, line) => {
    if (!line.trim()) {
      return total + 1;
    }
    const words = line.trim().split(/\s+/);
    let lineCount = 1;
    let lineWidth = firstLinePrefixWidth;
    let hasWord = false;
    for (const word of words) {
      const wordWidth = context.measureText(word).width;
      const separatorWidth = hasWord ? spaceWidth : 0;
      if (hasWord && lineWidth + separatorWidth + wordWidth > width) {
        lineCount += 1;
        lineWidth = wordWidth;
        continue;
      }
      if (!hasWord && lineWidth + wordWidth > width) {
        lineCount += Math.max(0, Math.ceil(wordWidth / width) - 1);
        lineWidth = wordWidth % width || width;
        hasWord = true;
        continue;
      }
      lineWidth += separatorWidth + wordWidth;
      hasWord = true;
    }
    return total + lineCount;
  }, 0);
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
  if (authors.length === 0) {
    return "Authors unavailable";
  }
  if (authors.length === 1) {
    return authors[0];
  }
  if (authors.length === 2) {
    return `${authors[0]} & ${authors[1]}`;
  }
  return `${authors[0]} et al.`;
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
