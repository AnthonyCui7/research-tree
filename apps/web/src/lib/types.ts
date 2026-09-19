export type WorkspaceDocument = {
  schema_version: string;
  workspace_id: string;
  current_workspace_version_hash?: string;
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

/** What the server will say about a provider key: never the key itself. */
export type ApiKeyStatus = {
  configured: boolean;
  /** Last four characters only; a whole key never reaches the browser. */
  masked: string | null;
  /** `environment` for the local profile's key, `account` for a saved one. */
  source: string | null;
  created_at: string | null;
};

/** A sponsored allowance on the operator's key, granted by email. */
export type AllowanceSummary = {
  id: string;
  limit_usd: number;
  spent_usd: number;
  remaining_usd: number;
  period: "one_off" | "monthly";
  period_start: string | null;
  expires_at: string | null;
  exhausted: boolean;
};

export type ApiKeysResult = {
  openai: ApiKeyStatus;
  allowance: AllowanceSummary | null;
  /** False for the local profile and when the server holds no key-encryption key. */
  saving_enabled: boolean;
  /** Whether the server holds a platform key an allowance could spend. */
  platform_key: boolean;
};

export type SaveApiKeyResult = { stored: boolean; detail: string; openai: ApiKeyStatus | null };
export type RemoveApiKeyResult = { removed: boolean; detail: string };

export type UsageEvent = {
  created_at: string | null;
  source: string;
  feature: string | null;
  label: string | null;
  model: string;
  cost_usd: number;
};

export type UsageSummary = {
  days: number;
  calls: number;
  total_usd: number;
  byok_usd: number;
  sponsored_usd: number;
  recent: UsageEvent[];
};

/** Bug reports answer with `stored: false` until there is somewhere to keep them. */
export type BugReportResult = { received: boolean; stored: boolean; detail: string };

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
  /** When a worker picked the build up; absent while it waits in the queue. */
  started_at?: string | null;
  updated_at: string;
};

/**
 * One concrete change inside a proposed revision (backend workspace/diff.py).
 * `target_ids` keys vary per operation type, and `before`/`after` carry whole
 * card or path dicts, so both stay loosely typed and are read through the
 * guarded helpers in lib/proposedOperations.ts.
 */
export type ProposedOperation = {
  operation_type: string;
  target_ids?: Record<string, unknown>;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  rationale?: string;
  confidence?: number;
};

export type AgentValidationSummary = {
  valid?: boolean;
  error_count?: number;
  warning_count?: number;
  errors?: string[];
  warnings?: string[];
};

/** The review payload an agent run pauses on; stored verbatim on the review record. */
export type AgentInterruptPayload = {
  type?: string;
  question?: string;
  review_id?: string;
  diff_summary?: Record<string, unknown> | null;
  proposed_operations?: ProposedOperation[];
  validation_summary?: AgentValidationSummary | null;
  skeptic_notes?: string[];
  warnings?: string[];
  choices?: string[];
};

/**
 * What the assistant is doing right now, streamed while a turn runs: a model
 * turn, one read tool and what it was asked about, or a stage of an edit.
 */
export type AgentActivity =
  | { kind: "thinking" }
  | { kind: "tool"; name: string; subject: string | null }
  | { kind: "stage"; stage: string };

/** An activity worth keeping once the turn is over: everything but thinking. */
export type AgentStep = Exclude<AgentActivity, { kind: "thinking" }>;

export type AgentRunResult = {
  workspace_id: string;
  status: string;
  thread_id: string | null;
  agent_run_id: string | null;
  review_id: string | null;
  interrupt_payload?: AgentInterruptPayload | null;
  final_response: string | null;
  diff_summary: Record<string, unknown> | null;
  validation_summary?: AgentValidationSummary | null;
  warnings: string[];
  errors: string[];
};

/** A stored review record, as returned by GET /workspaces/{id}/reviews. */
export type WorkspaceReview = {
  review_id: string;
  status: string;
  review_type?: string;
  agent_run_id?: string;
  user_message?: string;
  created_at?: string;
  updated_at?: string;
  proposed_operations?: ProposedOperation[];
  diff_summary?: Record<string, unknown> | null;
  validation_summary?: AgentValidationSummary | null;
  interrupt_payload?: AgentInterruptPayload | null;
};

/**
 * A change made by hand on the canvas, in the operation shape the server
 * applies for the assistant's proposals too.
 */
export type WorkspaceEditOperation =
  | { op: "set"; entity_type: "branch"; branch_id: string; field: "label"; value: string }
  | {
      op: "move";
      entity_type: "paper_placement";
      paper_id: string;
      to_branch_id: string;
      path_id?: string;
    }
  | { op: "remove"; entity_type: "paper_placement"; paper_id: string };

export type WorkspaceEditResult = {
  workspace_id: string;
  workspace_version_hash: string;
  /** What the edit replaced; restoring it is the undo. */
  previous_version_hash: string;
  changed: boolean;
  /** The version reason, as history shows it. */
  summary: string;
  warnings: string[];
};

export type ReviewActionResponse = {
  workspace_id: string;
  review_id: string;
  status: string;
  idempotent?: boolean;
  workspace_version_hash?: string | null;
  persisted_version_hash?: string | null;
  /** Set when approving a rebuild proposal: the run it started. */
  pipeline_run?: PipelineRun | null;
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
  paper_content?: {
    status?: string | null;
    source_type?: string | null;
    source_url?: string | null;
    page_count?: number | null;
    truncated?: boolean | null;
  } | null;
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
  currentVersionHash: string | null;
  canvas: {
    width: number;
    height: number;
  };
  root: RootTreeNode;
  nodes: TreeNodeViewModel[];
  nodesById: Record<TreeNodeId, TreeNodeViewModel>;
  edges: TreeEdgeViewModel[];
};

export type TreeNodeViewModel = RootTreeNode | BranchTreeNode | PaperTreeNode;

/**
 * What the workspace document records about a paper's open-access PDF — enough
 * to offer the download. The extracted text itself stays on the server, where
 * `GET /workspaces/{id}/paper-content` serves it; carrying it in every card
 * would dwarf the editorial content.
 */
export type PaperContentSummary = {
  status: string;
  sourceUrl: string | null;
  pageCount: number | null;
  truncated: boolean;
};

export type AnnotationType = "highlight" | "note" | "definition";

/** `fast` groups the paper into sections; `dense` situates every passage. */
export type AnnotationRetrievalMode = "fast" | "dense";

/** One rectangle of a highlight, as a fraction of the page it sits on. */
export type HighlightFragment = {
  x: number;
  y: number;
  width: number;
  height: number;
};

/**
 * An annotation the reader draws over the PDF: the quote it marks, the note
 * explaining why the quote matters, and where the quote sits on its page. A
 * quote that wraps across lines occupies one fragment per line.
 */
export type PaperAnnotation = {
  type: AnnotationType;
  text_ref: string;
  note: string;
  importance: 1 | 2 | 3;
  page_number: number;
  bbox: HighlightFragment & { fragments: HighlightFragment[] };
};

export type PaperDetails = {
  paperId: string;
  title: string;
  authors: string[];
  year: number | null;
  publicationDate: string | null;
  venue: string;
  arxivLink: string | null;
  semanticScholarLink: string | null;
  citationCount: number | null;
  tldr: string | null;
  importance: string;
  abstract: string;
  similarPapers: SimilarPaper[];
  content: PaperContentSummary | null;
};

export type RootTreeNode = {
  id: TreeNodeId;
  kind: "root";
  title: string;
  overview: string;
  whyItMatters: string;
  suggestedReadingDirection: string;
  keyTerms: string[];
  openQuestions: string[];
  branchCount: number;
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
  tags: string[];
  openQuestions: string[];
  anchorPaper: PaperDetails | null;
  /** Papers laid out on this branch's reading paths. */
  paperCount: number;
  position: Point;
  size: NodeSize;
};

export type PaperTreeNode = PaperDetails & {
  id: TreeNodeId;
  kind: "paper";
  family: number | "group" | null;
  /** The branch this paper's reading path belongs to. */
  branchId: string;
  branchTitle: string;
  /** 1-based position in that reading path, and the path's length. */
  readingIndex: number;
  readingLength: number;
  whyReadHere: string;
  position: Point;
  size: NodeSize;
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

/** Who is signed in, as the server reports it. */
export type SessionUser = {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
  is_admin: boolean;
  is_verified: boolean;
};

export type SessionInfo = {
  /** `accounts` behind sign-in; `none` for the single-user local build. */
  auth_mode: "accounts" | "none";
  google_sign_in: boolean;
  user: SessionUser;
};
