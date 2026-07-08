export type WorkspaceDocument = {
  schema_version: string;
  workspace_id: string;
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

export type WorkspaceRoot = {
  node_id: string;
  label: string;
  overview: string;
  root_survey_type: string;
  survey_anchor_paper_ids: string[];
  representative_paper_ids: string[];
  key_terms: string[];
  open_questions: string[];
  suggested_reading_direction: string;
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
  rationale: string;
};

export type PaperCard = {
  paper_id: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string;
  primary_link: string | null;
  doi: string | null;
  arxiv_id: string | null;
  abstract: string;
  primary_tree_location: {
    node_id: string;
    path: string[];
  };
  secondary_tags: string[];
  reading_status: string;
  paper_role: string;
  one_sentence_contribution: string;
  problem: string;
  core_idea: string;
  method: string;
  assumptions: string;
  datasets_or_benchmarks: string;
  results: string;
  limitations: string;
  why_it_belongs: string;
  read_before: string[];
  read_after: string[];
  user_notes: string;
  similar_papers: Record<string, unknown>[];
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
  canvas: {
    width: number;
    height: number;
  };
  nodes: TreeNodeViewModel[];
  nodesById: Record<TreeNodeId, TreeNodeViewModel>;
  edges: TreeEdgeViewModel[];
};

export type TreeNodeViewModel = RootTreeNode | BranchTreeNode | PaperTreeNode;

export type RootTreeNode = {
  id: TreeNodeId;
  kind: "root";
  title: string;
  overview: string;
  surveyType: string;
  suggestedReadingDirection: string;
  keyTerms: string[];
  openQuestions: string[];
  paperCount: number;
  position: Point;
};

export type BranchTreeNode = {
  id: TreeNodeId;
  kind: "branch";
  title: string;
  description: string;
  whyItMatters: string;
  tags: string[];
  openQuestions: string[];
  paperCount: number;
  position: Point;
};

export type PaperTreeNode = {
  id: TreeNodeId;
  kind: "paper";
  title: string;
  year: number | null;
  venue: string;
  role: string;
  readingStatus: string;
  contribution: string;
  abstractPreview: string;
  similarPaperCount: number;
  position: Point;
};

export type TreeEdgeViewModel = {
  from: Point;
  to: Point;
};

export type Point = {
  x: number;
  y: number;
};
