import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { messageFrom } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS, useDismissAnimation } from "../../lib/animation";
import { paperPdfUrl, repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { CloseIcon } from "../ui/icons";
import type {
  AnnotationRetrievalMode,
  AnnotationType,
  PaperAnnotation,
  PaperDetails,
} from "../../lib/types";

// react-pdf parses PDFs in a worker. Resolving it through the bundler keeps the
// worker on this origin, which the strict-origin worker policy requires.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const PAGE_WIDTH = 820;
const MARK_STYLES: Record<AnnotationType, string> = {
  highlight: "bg-annotation-highlight/25 border-annotation-highlight",
  note: "bg-annotation-note/20 border-annotation-note",
  definition: "bg-annotation-definition/25 border-annotation-definition",
};
const LEGEND: Array<{ type: AnnotationType; label: string }> = [
  { type: "highlight", label: "Claim or result" },
  { type: "note", label: "Implication" },
  { type: "definition", label: "Term" },
];

type ReaderProps = {
  workspaceId: string;
  paper: PaperDetails;
  onClose: () => void;
  /** Hands the reader off to the workspace assistant panel. */
  onOpenAssistant?: () => void;
};

/**
 * The paper, with its annotations drawn where they belong.
 *
 * Annotations are stored as fractions of a page rather than as pixels, so a
 * mark is positioned in percentages over the rendered page and stays correct at
 * whatever width the page is drawn.
 */
export function AnnotatedPaperReader({ workspaceId, paper, onClose, onOpenAssistant }: ReaderProps) {
  const { closing, dismiss } = useDismissAnimation(onClose, DIALOG_EXIT_MS);
  const [annotations, setAnnotations] = useState<PaperAnnotation[] | null>(null);
  const [mode, setMode] = useState<AnnotationRetrievalMode | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [pageCount, setPageCount] = useState(0);
  const [selected, setSelected] = useState<PaperAnnotation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  const file = useMemo(
    () => ({ url: paperPdfUrl(workspaceId, paper.paperId) }),
    [workspaceId, paper.paperId],
  );

  const fetchAnnotations = useCallback(
    (options?: { mode?: AnnotationRetrievalMode; refresh?: boolean }) => {
      const sequence = ++requestSequence.current;
      setAnnotations(null);
      setError(null);
      repositoryWorkspaceGateway
        .getPaperAnnotations(workspaceId, paper.paperId, options)
        .then((result) => {
          if (sequence !== requestSequence.current) return;
          setAnnotations(result.annotations);
          if (result.retrieval_mode === "fast" || result.retrieval_mode === "dense") {
            setMode(result.retrieval_mode);
          }
        })
        .catch((requestError) => {
          if (sequence !== requestSequence.current) return;
          setError(messageFrom(requestError));
        });
    },
    [workspaceId, paper.paperId],
  );

  useEffect(() => {
    fetchAnnotations();
    return () => {
      // Late responses for a previous paper must not land on this one.
      requestSequence.current += 1;
    };
  }, [fetchAnnotations]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      // One Escape closes one layer, innermost first.
      if (modeMenuOpen) setModeMenuOpen(false);
      else if (helpOpen) setHelpOpen(false);
      else if (selected) setSelected(null);
      else dismiss();
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [dismiss, helpOpen, modeMenuOpen, selected]);

  const byPage = useMemo(() => groupByPage(annotations ?? []), [annotations]);
  const generating = annotations === null && !error;
  const byline = [
    authorsLine(paper.authors),
    dateLine(paper.publicationDate, paper.year),
    arxivIdFrom(paper.arxivLink),
  ].filter(Boolean);

  return (
    <div
      className={cx(
        "fixed inset-0 z-creator flex flex-col bg-background",
        closing ? "animate-backdrop-exit" : "animate-backdrop-enter",
      )}
      role="dialog"
      aria-modal="true"
      aria-label={`${paper.title}, annotated`}
    >
      <header className="relative flex flex-none items-center gap-2.5 border-b border-hairline bg-surface px-4 py-2">
        <div className="min-w-0 flex-1">
          <h2 className="m-0 truncate text-[12.5px] font-semibold tracking-[-0.01em] text-text-primary">
            {paper.title}
          </h2>
          <p className="mt-px mb-0 truncate text-[10.5px] text-text-muted">
            {byline.join(" · ")}
            {byline.length ? " · " : null}
            <ReaderStatus annotations={annotations} error={error} />
          </p>
        </div>
        <ModeControl
          mode={mode}
          disabled={generating}
          open={modeMenuOpen}
          onOpenChange={setModeMenuOpen}
          onSelect={(next) => {
            if (next !== mode) fetchAnnotations({ mode: next });
          }}
        />
        <button
          className="flex flex-none items-center rounded-[6px] border border-border bg-transparent px-2.5 py-1 text-[11px] font-semibold text-text-primary transition-[background-color] duration-150 hover:bg-surface-subtle disabled:cursor-default disabled:opacity-50"
          type="button"
          disabled={generating}
          onClick={() => fetchAnnotations({ ...(mode ? { mode } : {}), refresh: true })}
          title="Regenerate the annotations from scratch. This takes a few minutes."
        >
          Reprocess
        </button>
        <button
          className="grid h-5 w-5 flex-none place-items-center rounded-full border border-border bg-surface-subtle text-[10.5px] font-semibold text-text-secondary transition-[background-color] duration-150 hover:bg-surface aria-expanded:bg-surface"
          type="button"
          onClick={() => setHelpOpen((open) => !open)}
          aria-expanded={helpOpen}
          aria-label="About this viewer"
        >
          ?
        </button>
        {onOpenAssistant ? (
          <button
            className="flex flex-none items-center gap-1.5 rounded-[7px] border border-accent-border bg-accent-subtle px-3 py-[5px] text-[11.5px] font-semibold text-accent-deep transition-[filter] duration-150 hover:brightness-95"
            type="button"
            onClick={() => {
              dismiss();
              onOpenAssistant();
            }}
            title="Chat with the workspace assistant, which can read this paper"
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M3 4.5 A1.5 1.5 0 0 1 4.5 3 H15.5 A1.5 1.5 0 0 1 17 4.5 V12 A1.5 1.5 0 0 1 15.5 13.5 H8 L4.5 17 V13.5 A1.5 1.5 0 0 1 3 12 Z" />
            </svg>
            Assistant
          </button>
        ) : null}
        <button
          className="grid h-[26px] w-[26px] flex-none place-items-center rounded-[6px] border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary"
          type="button"
          onClick={dismiss}
          aria-label="Close reader"
          title="Close"
        >
          <CloseIcon className="h-3 w-3" />
        </button>
        {helpOpen ? <ViewerHelp /> : null}
      </header>

      <div
        className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-6 py-6"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) setSelected(null);
        }}
      >
        <Document
          file={file}
          loading={<ReaderMessage>Loading the paper…</ReaderMessage>}
          error={<ReaderMessage>{documentError ?? "This PDF could not be displayed."}</ReaderMessage>}
          onLoadSuccess={({ numPages }) => setPageCount(numPages)}
          onLoadError={(loadError) => setDocumentError(messageFrom(loadError))}
        >
          <div className="mx-auto grid w-fit gap-5">
            {Array.from({ length: pageCount }, (_, index) => (
              <AnnotatedPage
                key={index}
                pageNumber={index + 1}
                annotations={byPage.get(index + 1) ?? []}
                selected={selected}
                onSelect={setSelected}
              />
            ))}
          </div>
        </Document>
      </div>
    </div>
  );
}

function AnnotatedPage({
  pageNumber,
  annotations,
  selected,
  onSelect,
}: {
  pageNumber: number;
  annotations: PaperAnnotation[];
  selected: PaperAnnotation | null;
  onSelect: (annotation: PaperAnnotation | null) => void;
}) {
  // The marks are positioned against this wrapper, which the rendered page
  // sizes, so no page dimensions have to be tracked. That holds only while the
  // page is the canvas alone: the viewer's own text and link layers stack below
  // it rather than over it, which would make the wrapper twice the page.
  return (
    <div className="relative shadow-dialog">
      <Page
        pageNumber={pageNumber}
        width={PAGE_WIDTH}
        renderTextLayer={false}
        renderAnnotationLayer={false}
      />
      {annotations.map((annotation) => (
        <AnnotationMark
          key={`${annotation.page_number}-${annotation.text_ref}`}
          annotation={annotation}
          open={selected === annotation}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function AnnotationMark({
  annotation,
  open,
  onSelect,
}: {
  annotation: PaperAnnotation;
  open: boolean;
  onSelect: (annotation: PaperAnnotation | null) => void;
}) {
  const fragments = annotation.bbox.fragments.length
    ? annotation.bbox.fragments
    : [annotation.bbox];
  return (
    <>
      {fragments.map((fragment, index) => (
        <button
          className={cx(
            "absolute cursor-pointer rounded-[2px] border-0 border-b-2 p-0 transition-[filter] duration-150 hover:brightness-95",
            MARK_STYLES[annotation.type],
            open && "brightness-90",
          )}
          key={index}
          type="button"
          style={{
            left: `${fragment.x * 100}%`,
            top: `${fragment.y * 100}%`,
            width: `${fragment.width * 100}%`,
            height: `${fragment.height * 100}%`,
          }}
          onClick={() => onSelect(open ? null : annotation)}
          aria-label={`${annotation.type}: ${annotation.text_ref}`}
        />
      ))}
      {open ? <AnnotationNote annotation={annotation} onClose={() => onSelect(null)} /> : null}
    </>
  );
}

function AnnotationNote({
  annotation,
  onClose,
}: {
  annotation: PaperAnnotation;
  onClose: () => void;
}) {
  const box = annotation.bbox;
  // Below the quote normally; above it when the quote sits near the page foot,
  // so the note stays on the page rather than hanging off the bottom.
  const below = box.y + box.height < 0.8;
  return (
    <div
      className="absolute z-dropdown w-[320px] max-w-[80%] rounded-[9px] border border-border bg-surface p-3 shadow-menu"
      style={{
        left: `${Math.min(Math.max(box.x, 0.02), 0.62) * 100}%`,
        ...(below
          ? { top: `calc(${(box.y + box.height) * 100}% + 6px)` }
          : { bottom: `calc(${(1 - box.y) * 100}% + 6px)` }),
      }}
      role="note"
    >
      <div className="flex items-baseline gap-2">
        <span
          className={cx(
            "flex-none rounded-[4px] px-1.5 py-0.5 text-[10.5px] font-medium tracking-[0.02em] uppercase",
            MARK_STYLES[annotation.type],
          )}
        >
          {annotation.type}
        </span>
        <span className="min-w-0 flex-1 truncate text-[11.5px] text-text-muted">
          {annotation.text_ref}
        </span>
        <button
          className="flex-none border-0 bg-transparent p-0 text-[11px] text-text-muted hover:text-text-primary"
          type="button"
          onClick={onClose}
          aria-label="Close note"
        >
          <CloseIcon className="h-2.5 w-2.5" />
        </button>
      </div>
      <p className="mt-2 mb-0 text-[12.5px] leading-[1.55] text-text-primary">{annotation.note}</p>
    </div>
  );
}

function ModeControl({
  mode,
  disabled,
  open,
  onOpenChange,
  onSelect,
}: {
  mode: AnnotationRetrievalMode | null;
  disabled: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (mode: AnnotationRetrievalMode) => void;
}) {
  return (
    <div className="relative flex-none">
      <button
        className="flex items-center gap-1 rounded-[6px] border border-border bg-transparent px-2.5 py-1 text-[11px] text-text-secondary transition-[background-color] duration-150 hover:bg-surface-subtle disabled:cursor-default disabled:opacity-50 aria-expanded:bg-surface-subtle"
        type="button"
        disabled={disabled}
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        aria-haspopup="menu"
        title="How thoroughly annotagent reads the paper"
      >
        Mode: <span className="font-semibold text-text-primary">{modeLabel(mode)}</span>
      </button>
      {open ? (
        <div
          className="fixed inset-0 z-dropdown"
          role="presentation"
          onClick={() => onOpenChange(false)}
        />
      ) : null}
      {open ? (
        <div
          className="absolute top-[calc(100%+4px)] right-0 z-dropdown w-[210px] rounded-[9px] border border-border bg-surface p-1 shadow-menu"
          role="menu"
        >
          {(
            [
              { value: "fast", label: "Fast", detail: "Sections as context" },
              { value: "dense", label: "Dense", detail: "More annotations, slower" },
            ] as const
          ).map((option) => (
            <button
              className="grid w-full gap-px rounded-[6px] border-0 bg-transparent px-2.5 py-1.5 text-left transition-[background-color] duration-150 hover:bg-surface-subtle aria-selected:bg-surface-subtle"
              key={option.value}
              type="button"
              role="menuitem"
              aria-selected={mode === option.value}
              onClick={() => {
                onOpenChange(false);
                onSelect(option.value);
              }}
            >
              <span className="text-[12px] font-semibold text-text-primary">{option.label}</span>
              <span className="text-[10.5px] text-text-muted">{option.detail}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function modeLabel(mode: AnnotationRetrievalMode | null): string {
  if (mode === "dense") return "Dense";
  if (mode === "fast") return "Fast";
  return "…";
}

/** The design's "About this viewer" popup, describing what each control does. */
function ViewerHelp() {
  return (
    <div className="absolute top-[calc(100%+4px)] right-[44px] z-dropdown w-[300px] rounded-[10px] border border-border bg-surface px-4 py-3 shadow-menu">
      <div className="text-[12px] font-bold text-text-primary">About this viewer</div>
      <p className="mt-1.5 mb-0 text-[11.5px] leading-[1.6] text-text-secondary">
        Annotations are generated by <b className="font-semibold text-text-primary">annotagent</b>.
        It extracts the PDF text, then writes highlights, notes, and jargon definitions anchored to
        the page layout.
      </p>
      <div className="mt-2 text-[11.5px] leading-[1.7] text-text-secondary">
        <div>
          <b className="font-semibold text-text-primary">Mode</b> · Fast, or Dense for more
          annotations
        </div>
        <div>
          <b className="font-semibold text-text-primary">Reprocess</b> · regenerate the annotations
          from scratch
        </div>
        <div>
          <b className="font-semibold text-text-primary">Assistant</b> · chat with the workspace
          agent, which can read this paper
        </div>
      </div>
      <ul className="mt-2.5 mb-0 flex list-none gap-3 border-t border-hairline p-0 pt-2.5">
        {LEGEND.map(({ type, label }) => (
          <li className="flex items-center gap-1.5 text-[11px] text-text-secondary" key={type}>
            <span className={cx("h-2.5 w-2.5 rounded-[3px] border-b-2", MARK_STYLES[type])} />
            {label}
          </li>
        ))}
      </ul>
    </div>
  );
}

function authorsLine(authors: string[]): string | null {
  const first = (authors[0] ?? "").trim();
  if (!first) return null;
  if (authors.length === 1) return first;
  const surname = first.split(/\s+/).pop() ?? first;
  return `${surname} et al.`;
}

function dateLine(publicationDate: string | null, year: number | null): string | null {
  if (publicationDate) {
    const parsed = new Date(publicationDate);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      });
    }
  }
  return year !== null ? String(year) : null;
}

function arxivIdFrom(arxivLink: string | null): string | null {
  const match = arxivLink?.match(/arxiv\.org\/(?:abs|pdf)\/([0-9]{4}\.[0-9]{4,5})/i);
  return match?.[1] ? `arXiv:${match[1]}` : null;
}

function ReaderStatus({
  annotations,
  error,
}: {
  annotations: PaperAnnotation[] | null;
  error: string | null;
}) {
  if (error) return <span className="text-error">{error}</span>;
  if (annotations === null) {
    return <>writing annotations — a few minutes the first time</>;
  }
  if (annotations.length === 0) return <>no annotations found</>;
  return <>{annotations.length} annotations</>;
}

function ReaderMessage({ children }: { children: ReactNode }) {
  return <p className="py-20 text-center text-[13px] text-text-secondary">{children}</p>;
}

function groupByPage(annotations: PaperAnnotation[]): Map<number, PaperAnnotation[]> {
  const byPage = new Map<number, PaperAnnotation[]>();
  for (const annotation of annotations) {
    const page = byPage.get(annotation.page_number);
    if (page) page.push(annotation);
    else byPage.set(annotation.page_number, [annotation]);
  }
  return byPage;
}
