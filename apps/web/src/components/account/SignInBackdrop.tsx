import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { TreeEdge } from "../tree/TreeEdge";
import { TreeNode } from "../tree/TreeNode";
import { normalizeWorkspaceForTree, type MeasuredNodeHeights } from "../../lib/workspaceAdapter";
import type { BranchNode, PaperCard, PaperPath, WorkspaceDocument } from "../../lib/types";

/**
 * The product as the backdrop of the sign-in page (design 1c). A presentation
 * tree, not a workspace export: the product's flagship topic, four branches a
 * strong build of it would produce, and three landmark papers per branch in
 * reading order, with real titles, authors and arXiv dates. It is drawn
 * exactly as the canvas draws a workspace: the data is shaped as a workspace
 * document, laid out by the canvas's adapter after the same hidden
 * measurement pass, and painted with the canvas's own node and edge
 * components, so tints, kickers, survey anchors and edges are the real thing.
 * Only the TLDR line of each paper card is hidden. Centred, scaled down and
 * faded under the form; inert, so none of it is focusable or announced.
 */

type Paper = { title: string; authors: string[]; date: string };
type Branch = { id: string; title: string; description: string; papers: Paper[] };

const ROOT = {
  label: "Prompting",
  overview:
    "How natural-language instructions, demonstrations, and learned context specify tasks and steer language models.",
  survey: {
    title:
      "Pre-train, Prompt, and Predict: A Systematic Survey of Prompting Methods in Natural Language Processing",
    authors: ["Pengfei Liu", "Weizhe Yuan", "Jinlan Fu"],
    date: "2021-07-28",
  },
};

const BRANCHES: Branch[] = [
  {
    id: "in_context",
    title: "In-context learning and prompt design",
    description:
      "Instructions and examples in the context specify a task at inference time, without updating weights.",
    papers: [
      {
        title: "Language Models are Few-Shot Learners",
        authors: ["Tom B. Brown", "Benjamin Mann", "Nick Ryder"],
        date: "2020-05-28",
      },
      {
        title: "Calibrate Before Use: Improving Few-Shot Performance of Language Models",
        authors: ["Tony Z. Zhao", "Eric Wallace", "Shi Feng"],
        date: "2021-02-19",
      },
      {
        title: "Rethinking the Role of Demonstrations: What Makes In-Context Learning Work?",
        authors: ["Sewon Min", "Xinxi Lyu", "Ari Holtzman"],
        date: "2022-02-25",
      },
    ],
  },
  {
    id: "prompt_tuning",
    title: "Prompt tuning and prompt optimization",
    description: "Prompts as trainable vectors or searched text, while the model's weights stay fixed.",
    papers: [
      {
        title: "Prefix-Tuning: Optimizing Continuous Prompts for Generation",
        authors: ["Xiang Lisa Li", "Percy Liang"],
        date: "2021-01-01",
      },
      {
        title: "The Power of Scale for Parameter-Efficient Prompt Tuning",
        authors: ["Brian Lester", "Rami Al-Rfou", "Noah Constant"],
        date: "2021-04-18",
      },
      {
        title: "Large Language Models Are Human-Level Prompt Engineers",
        authors: ["Yongchao Zhou", "Andrei Ioan Muresanu", "Ziwen Han"],
        date: "2022-11-03",
      },
    ],
  },
  {
    id: "instruction_tuning",
    title: "Instruction tuning and alignment",
    description:
      "Training on instructions and human preferences so later prompts invoke general behavior.",
    papers: [
      {
        title: "Finetuned Language Models Are Zero-Shot Learners",
        authors: ["Jason Wei", "Maarten Bosma", "Vincent Y. Zhao"],
        date: "2021-09-03",
      },
      {
        title: "Training language models to follow instructions with human feedback",
        authors: ["Long Ouyang", "Jeff Wu", "Xu Jiang"],
        date: "2022-03-04",
      },
      {
        title: "Self-Instruct: Aligning Language Models with Self-Generated Instructions",
        authors: ["Yizhong Wang", "Yeganeh Kordi", "Swaroop Mishra"],
        date: "2022-12-20",
      },
    ],
  },
  {
    id: "reasoning",
    title: "Reasoning and acting at inference",
    description:
      "Prompting for intermediate steps, sampling over them, and interleaving reasoning with tool use.",
    papers: [
      {
        title: "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models",
        authors: ["Jason Wei", "Xuezhi Wang", "Dale Schuurmans"],
        date: "2022-01-28",
      },
      {
        title: "Self-Consistency Improves Chain of Thought Reasoning in Language Models",
        authors: ["Xuezhi Wang", "Jason Wei", "Dale Schuurmans"],
        date: "2022-03-21",
      },
      {
        title: "ReAct: Synergizing Reasoning and Acting in Language Models",
        authors: ["Shunyu Yao", "Jeffrey Zhao", "Dian Yu"],
        date: "2022-10-06",
      },
    ],
  },
];

function card(paperId: string, paper: Paper, branchId: string, role: string): PaperCard {
  return {
    paper_id: paperId,
    title: paper.title,
    authors: paper.authors,
    year: Number(paper.date.slice(0, 4)),
    publication_date: paper.date,
    venue: "",
    primary_link: null,
    doi: null,
    arxiv_id: null,
    abstract: "",
    primary_tree_location: { node_id: branchId, path: [] },
    secondary_tags: [],
    reading_status: "unread",
    paper_role: role,
    problem: "",
    core_idea: "",
    method: "",
    assumptions: "",
    datasets_or_benchmarks: "",
    results: "",
    limitations: "",
    read_before: [],
    read_after: [],
    user_notes: "",
    similar_papers: [],
  };
}

/** The presentation data in the shape the canvas lays out. */
function buildWorkspace(): WorkspaceDocument {
  const cards: Record<string, PaperCard> = {};
  const nodes: BranchNode[] = [];
  const paths: PaperPath[] = [];
  const surveyId = "survey";
  cards[surveyId] = card(surveyId, ROOT.survey, "root", "survey");
  for (const branch of BRANCHES) {
    const paperIds = branch.papers.map((paper, index) => {
      const paperId = `${branch.id}-${index + 1}`;
      cards[paperId] = card(paperId, paper, branch.id, "primary");
      return paperId;
    });
    nodes.push({
      node_id: branch.id,
      parent_id: "root",
      label: branch.title,
      description: branch.description,
      why_it_matters: "",
      is_leaf: true,
      child_node_ids: [],
      primary_paper_ids: paperIds,
      secondary_paper_ids: [],
      survey_anchor_paper_id: null,
      tags: [],
      open_questions: [],
    });
    paths.push({
      path_id: `${branch.id}-path`,
      branch_node_id: branch.id,
      path_type: "reading_path",
      label: branch.title,
      description: branch.description,
      paper_ids: paperIds,
      rationale: "",
    });
  }
  return {
    schema_version: "presentation",
    workspace_id: "sign-in",
    topic: ROOT.label,
    title: ROOT.label,
    scope: {},
    source_candidate_artifact: {},
    root: {
      node_id: "root",
      label: ROOT.label,
      overview: ROOT.overview,
      root_survey_type: "topic-specific survey",
      survey_anchor_paper_ids: [surveyId],
      representative_paper_ids: [],
      key_terms: [],
      open_questions: [],
      suggested_reading_direction: "",
    },
    tree: { root_node_id: "root", nodes },
    paper_paths: paths,
    paper_cards: cards,
    reading_order: [],
    comparison_tables: [],
    discarded_candidates: [],
    provenance: {},
  };
}

const workspace = buildWorkspace();

// 0.8 puts the canvas cards' text at the sizes the design's stand-in cards
// had at 1.25; 0.7 read as a blur. The tree shrinks below that only when the
// window cannot hold it with a margin, so nothing is cut mid-word, and never
// below 0.6, where it would stop being legible at all. Opacity 0.7 rather
// than the design's 0.6 because the canvas cards carry lighter meta lines;
// then 0.8, and the fade eased (94/78/12 instead of 96/85/20), at the
// user's "a bit less blurred".
const MAX_SCALE = 0.8;
const MIN_SCALE = 0.6;
const MARGIN = 24;

function useViewport(): { width: number; height: number } {
  const [size, setSize] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }));
  useEffect(() => {
    function update() {
      setSize({ width: window.innerWidth, height: window.innerHeight });
    }
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return size;
}

export function SignInBackdrop() {
  const [heights, setHeights] = useState<MeasuredNodeHeights | null>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const remeasuredForFontsRef = useRef(false);
  const estimate = useMemo(() => normalizeWorkspaceForTree(workspace), []);
  const tree = useMemo(
    () => (heights ? normalizeWorkspaceForTree(workspace, heights) : estimate),
    [heights, estimate],
  );
  const viewport = useViewport();
  const scale = Math.max(
    MIN_SCALE,
    Math.min(
      MAX_SCALE,
      (viewport.width - 2 * MARGIN) / tree.canvas.width,
      (viewport.height - 2 * MARGIN) / tree.canvas.height,
    ),
  );

  // Same as TreeCanvas: the cards' real heights come from the DOM, read in a
  // layout effect so the measured layout is what the first frame paints.
  useLayoutEffect(() => {
    const layer = layerRef.current;
    if (!layer) {
      return;
    }
    const next: MeasuredNodeHeights = {};
    layer.querySelectorAll<HTMLElement>("[data-measure-id]").forEach((element) => {
      next[element.dataset.measureId as string] = Math.ceil(element.getBoundingClientRect().height);
    });
    setHeights(next);
  }, [heights]);

  // Inter loads with `display=swap`; a cold load measures against the fallback
  // face, so one more pass runs once the real face is in.
  useEffect(() => {
    let active = true;
    void document.fonts?.ready?.then(() => {
      if (active && !remeasuredForFontsRef.current) {
        remeasuredForFontsRef.current = true;
        setHeights(null);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="absolute inset-0 overflow-hidden [&_.tldr]:hidden" aria-hidden="true" inert>
      {heights === null ? (
        <div ref={layerRef} className="pointer-events-none invisible absolute inset-0 overflow-hidden">
          {estimate.nodes.map((node) => (
            <TreeNode key={node.id} node={node} measure />
          ))}
        </div>
      ) : null}
      <div
        className="absolute top-1/2 left-1/2 opacity-80"
        style={{
          width: tree.canvas.width,
          height: tree.canvas.height,
          transform: `translate(-50%, -50%) scale(${scale})`,
        }}
      >
        <svg
          className="pointer-events-none absolute inset-0 z-0"
          width={tree.canvas.width}
          height={tree.canvas.height}
        >
          {tree.edges.map((edge, index) => (
            <TreeEdge
              key={`${edge.from.x}:${edge.from.y}:${edge.to.x}:${edge.to.y}:${index}`}
              edge={edge}
            />
          ))}
        </svg>
        {tree.nodes.map((node) => (
          <TreeNode key={node.id} node={node} />
        ))}
      </div>
      {/* The fade is a tall centre band rather than the design's wide ellipse:
          the sides stay clear and the column the form sits in is quieter, at
          the user's ask. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 36% 85% at 50% 50%, rgb(247 248 248 / 96%) 0%, rgb(247 248 248 / 82%) 55%, rgb(247 248 248 / 0%) 100%)",
        }}
      />
    </div>
  );
}
