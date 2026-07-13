export type WorkspaceDocument = {
  schema_version: string;
  workspace_id: string;
  current_workspace_version_hash?: string;
  workspace_versions?: WorkspaceVersion[];
  topic: string;
  title: string;
  scope: Record<string, unknown>;
  source_candidate_artifact: Record<string, unknown>;
  root: WorkspaceRoot;
  tree: WorkspaceTree;
  paper_paths: PaperPath[];
  paper_cards: Record<string, PaperCard>;
  reading_order: ReadingOrderItem[];
  comparison_tables: Record<string, unknown>[];
  discarded_candidates: Record<string, unknown>[];
  provenance: Record<string, unknown>;
};

export type WorkspaceSummary = {
  workspace_id: string;
  workspace_version_hash: string;
  title: string;
  topic: string;
  paper_count: number;
  branch_count: number;
  paper_path_count: number;
  updated_at: string | null;
};

export type WorkspaceVersion = {
  schema_version: string;
  workspace_id: string;
  version_hash: string;
  parent_version_hash: string | null;
  actor: string;
  actor_type?: string;
  actor_id?: string;
  reason: string;
  agent_run_id?: string | null;
  created_at: string;
  is_current?: boolean;
  navigation_index?: number;
};

export type WorkspaceRoot = {
  node_id: string;
  label: string;
  overview: string;
  why_it_matters?: string;
  root_survey_type: string;
  survey_anchor_paper_ids: string[];
  representative_paper_ids: string[];
  key_terms: string[];
  open_questions: string[];
  suggested_reading_direction: string;
};

export type TopicReview = {
  submitted_topic: string;
  normalized_topic: string;
  is_research_topic: boolean;
  guidance: string;
  existing_workspace: { workspace_id: string; title: string } | null;
  can_create: boolean;
  model: string | null;
  source_paper: { provider: string; title: string; abstract: string } | null;
  topic_review_token: string | null;
};

export type PipelineRun = {
  run_id: string;
  workspace_id: string;
  topic: string;
  model: string;
  status: "queued" | "running" | "completed" | "completed_with_warnings" | "failed" | "cancelled";
  current_stage: string | null;
  requested_stages: string[];
  stages: Record<string, { status: string; updated_at: string; error?: string | null }>;
  warnings: string[];
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentRunResult = {
  workspace_id: string;
  status: string;
  thread_id: string | null;
  agent_run_id: string | null;
  review_id: string | null;
  final_response: string | null;
  diff_summary: Record<string, unknown> | null;
  warnings: string[];
  errors: string[];
};

export type WorkspaceTree = {
  root_node_id: string;
  nodes: BranchNode[];
};

export type BranchNode = {
  node_id: string;
  parent_id: string;
  label: string;
  description: string;
  why_it_matters: string;
  is_leaf: boolean;
  child_node_ids: string[];
  primary_paper_ids: string[];
  secondary_paper_ids: string[];
  survey_anchor_paper_id?: string | null;
  tags: string[];
  open_questions: string[];
};

export type PaperPath = {
  path_id: string;
  branch_node_id: string;
  path_type: string;
  label: string;
  description: string;
  paper_ids: string[];
  paper_steps?: PaperStep[];
  rationale: string;
};

export type PaperStep = {
  paper_id: string;
  why_read_here: string;
};

export type PaperCard = {
  paper_id: string;
  title: string;
  authors: string[];
  year: number | null;
  publication_date?: string | null;
  venue: string;
  primary_link: string | null;
  doi: string | null;
  arxiv_id: string | null;
  arxiv_link?: string | null;
  doi_link?: string | null;
  s2_link?: string | null;
  citation_count?: number | null;
  tldr?: string | null;
  importance?: string;
  abstract: string;
  primary_tree_location: {
    node_id: string;
    path: string[];
  };
  secondary_tags: string[];
  reading_status: string;
  paper_role: string;
  problem: string;
  core_idea: string;
  method: string;
  assumptions: string;
  datasets_or_benchmarks: string;
  results: string;
  limitations: string;
  read_before: string[];
  read_after: string[];
  user_notes: string;
  similar_papers: SimilarPaper[];
};

export type SimilarPaper = {
  paper_id: string;
  title: string;
  authors?: string[];
  year: number | null;
  publication_date?: string | null;
  venue?: string;
  primary_link?: string | null;
  arxiv_link?: string | null;
  s2_link?: string | null;
};

export type ReadingOrderItem = {
  order: number;
  paper_id: string;
  reason: string;
};

export type TreeNodeId = string;

export type TreeViewModel = {
  workspaceId: string;
  title: string;
  paperCount: number;
  branchCount: number;
  pathCount: number;
  currentVersionHash: string | null;
  versionCount: number;
  canvas: {
    width: number;
    height: number;
  };
  root: RootTreeNode;
  nodes: TreeNodeViewModel[];
  nodesById: Record<TreeNodeId, TreeNodeViewModel>;
  edges: TreeEdgeViewModel[];
  pathLabels: TreePathLabelViewModel[];
};

export type TreeNodeViewModel = RootTreeNode | BranchTreeNode | PaperTreeNode;

export type PaperDetails = {
  paperId: string;
  title: string;
  authors: string[];
  year: number | null;
  publicationDate: string | null;
  venue: string;
  primaryLink: string | null;
  doi: string | null;
  arxivId: string | null;
  arxivLink: string | null;
  semanticScholarLink: string | null;
  citationCount: number | null;
  tldr: string | null;
  importance: string;
  abstract: string;
  similarPapers: SimilarPaper[];
};

export type RootTreeNode = {
  id: TreeNodeId;
  kind: "root";
  title: string;
  overview: string;
  whyItMatters: string;
  surveyType: string;
  suggestedReadingDirection: string;
  keyTerms: string[];
  openQuestions: string[];
  paperCount: number;
  branchCount: number;
  pathCount: number;
  anchorPaper: PaperDetails | null;
  position: Point;
  size: NodeSize;
};

export type BranchTreeNode = {
  id: TreeNodeId;
  kind: "branch";
  family: number | "group" | null;
  branchNodeId: string;
  title: string;
  description: string;
  whyItMatters: string;
  breadcrumb: string[];
  tags: string[];
  openQuestions: string[];
  paperCount: number;
  pathCount: number;
  anchorPaper: PaperDetails | null;
  position: Point;
  size: NodeSize;
};

export type PaperTreeNode = PaperDetails & {
  id: TreeNodeId;
  kind: "paper";
  family: number | "group" | null;
  whyReadHere: string;
  pathId: string;
  position: Point;
  size: NodeSize;
};

export type TreePathLabelViewModel = {
  id: TreeNodeId;
  label: string;
  description: string;
  position: Point;
};

export type TreeEdgeViewModel = {
  from: Point;
  to: Point;
  kind: "tree" | "timeline";
};

export type Point = {
  x: number;
  y: number;
};

export type NodeSize = {
  width: number;
  height: number;
};
