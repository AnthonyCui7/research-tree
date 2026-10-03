import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { messageFrom } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS } from "../../lib/animation";
import { useModalDialog } from "../../lib/modalDialog";
import { paperPdfUrl, repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { iconButtonClass, inlineIconButtonClass, secondaryActionClass } from "../../lib/controlClasses";
import { MenuItem, MenuSection, PopoverMenu, anchorFromEvent, type MenuAnchor } from "../ui/PopoverMenu";
import { ChatIcon, ChevronDownIcon, CloseIcon, HelpIcon } from "../ui/icons";
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
  // The pages are what the reader came for, so they take focus first and the
  // keyboard scrolls them; the controls in the header are a Tab away.
  const pagesRef = useRef<HTMLDivElement>(null);
  const { ref, closing, dismiss } = useModalDialog(DIALOG_EXIT_MS, pagesRef);
  const [annotations, setAnnotations] = useState<PaperAnnotation[] | null>(null);
  const [mode, setMode] = useState<AnnotationRetrievalMode | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [modeAnchor, setModeAnchor] = useState<MenuAnchor | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [selected, setSelected] = useState<PaperAnnotation | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The request in flight. Annotating a paper the first time is minutes of
  // polling; leaving the reader, or asking for another mode, ends it.
  const requestRef = useRef<AbortController | null>(null);

  const file = useMemo(
    () => ({ url: paperPdfUrl(workspaceId, paper.paperId) }),
    [workspaceId, paper.paperId],
  );

  const fetchAnnotations = useCallback(
    (options?: { mode?: AnnotationRetrievalMode; refresh?: boolean }) => {
      requestRef.current?.abort();
      const request = new AbortController();
      requestRef.current = request;
      setAnnotations(null);
      setError(null);
      repositoryWorkspaceGateway
        .getPaperAnnotations(workspaceId, paper.paperId, options, request.signal)
        .then((result) => {
          if (request.signal.aborted) return;
          setAnnotations(result.annotations);
          if (result.retrieval_mode === "fast" || result.retrieval_mode === "dense") {
            setMode(result.retrieval_mode);
          }
        })
        .catch((requestError) => {
          if (request.signal.aborted) return;
          setError(messageFrom(requestError));
        });
    },
    [workspaceId, paper.paperId],
  );

  useEffect(() => {
    fetchAnnotations();
    return () => requestRef.current?.abort();
  }, [fetchAnnotations]);

  function onKeyDown(event: React.KeyboardEvent) {
    // The shell's shortcuts stop at a modal.
    event.stopPropagation();
    if (event.key !== "Escape") return;
    event.preventDefault();
    // One Escape closes one layer, innermost first. The mode menu answers
    // its own Escape before it reaches here.
    if (helpOpen) setHelpOpen(false);
    else if (selected) setSelected(null);
    else dismiss();
  }

  const byPage = useMemo(() => groupByPage(annotations ?? []), [annotations]);
  const generating = annotations === null && !error;
  const byline = [
    authorsLine(paper.authors),
    dateLine(paper.publicationDate, paper.year),
    arxivIdFrom(paper.arxivLink),
  ].filter(Boolean);

  return (
    <dialog
      ref={ref}
      className={cx(
        "fixed inset-0 m-0 flex h-full max-h-none w-full max-w-none flex-col overflow-hidden border-0 bg-background p-0 text-text-primary outline-none [&::backdrop]:bg-transparent",
        closing ? "animate-backdrop-exit" : "animate-backdrop-enter",
      )}
      tabIndex={-1}
      aria-label={`${paper.title}, annotated`}
      onClose={onClose}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
      onKeyDown={onKeyDown}
    >
      <header className="box-content flex h-14 flex-none items-center gap-2 border-b border-hairline bg-surface pr-3.5 pl-6">
        <div className="min-w-0 flex-1">
          <h2 className="m-0 truncate text-14 font-semibold text-text-primary">
            {paper.title}
          </h2>
          <p className="m-0 truncate text-12 text-text-muted">
            {byline.join(" · ")}
            {byline.length ? " · " : null}
            <ReaderStatus annotations={annotations} error={error} />
          </p>
        </div>
        {/* The chevron is drawn 4px inside its box, so the right padding is
            that much less to end it as far from the edge as the label starts. */}
        <button
          className={cx(secondaryActionClass, "pr-2")}
          type="button"
          disabled={generating}
          onClick={(event) => setModeAnchor(anchorFromEvent(event.currentTarget, "right"))}
          aria-haspopup="menu"
          aria-expanded={modeAnchor !== null}
          title="How thoroughly annotagent reads the paper"
        >
          <span className="text-text-secondary max-[640px]:sr-only">Mode</span>
          {modeLabel(mode)}
          <ChevronDownIcon className="size-4 text-text-muted" />
        </button>
        <button
          className={secondaryActionClass}
          type="button"
          disabled={generating}
          onClick={() => fetchAnnotations({ ...(mode ? { mode } : {}), refresh: true })}
          title="Regenerate the annotations from scratch. This takes a few minutes."
        >
          Reprocess
        </button>
        {onOpenAssistant ? (
          <button
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-accent-border bg-surface px-3 text-13 font-medium whitespace-nowrap text-accent-deep transition-[background-color] duration-150 hover:bg-accent-wash"
            type="button"
            onClick={() => {
              dismiss();
              onOpenAssistant();
            }}
            title="Chat with the workspace assistant, which can read this paper"
          >
            <ChatIcon className="size-4" />
            <span className="max-[640px]:sr-only">Assistant</span>
          </button>
        ) : null}
        {/* The popup hangs from this button, as a menu hangs from its trigger. */}
        <div className="relative">
          <button
            className={iconButtonClass}
            type="button"
            onClick={() => setHelpOpen((open) => !open)}
            aria-expanded={helpOpen}
            aria-label="About this viewer"
            title="About this viewer"
          >
            <HelpIcon className="size-4" />
          </button>
          {helpOpen ? <ViewerHelp /> : null}
        </div>
        <button className={iconButtonClass} type="button" onClick={dismiss} aria-label="Close reader" title="Close">
          <CloseIcon className="size-3" />
        </button>
      </header>
      {modeAnchor ? (
        <PopoverMenu anchor={modeAnchor} onClose={() => setModeAnchor(null)} label="Annotation mode" width={220}>
          <MenuSection>
            {MODES.map((option) => (
              <MenuItem
                key={option.value}
                checked={mode === option.value}
                description={option.detail}
                onClick={() => {
                  if (option.value !== mode) fetchAnnotations({ mode: option.value });
                }}
              >
                {option.label}
              </MenuItem>
            ))}
          </MenuSection>
        </PopoverMenu>
      ) : null}

      <div
        ref={pagesRef}
        // The ring is drawn inside, where the dialog's edges cannot clip it.
        className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-6 py-6 focus-visible:-outline-offset-2"
        tabIndex={0}
        role="region"
        aria-label="Pages"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) setSelected(null);
        }}
      >
        <Document
          file={file}
          loading={<ReaderMessage>Loading the paper…</ReaderMessage>}
          error={<ReaderMessage>This PDF could not be displayed.</ReaderMessage>}
          onLoadSuccess={({ numPages }) => setPageCount(numPages)}
          // The viewer's own error text is for its developers; the fixed
          // sentence above is for the reader, and the cause goes to the console.
          onLoadError={(loadError) => console.error(loadError)}
        >
          <div className="mx-auto grid w-fit gap-6">
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
    </dialog>
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
      {annotations.map((annotation, index) => (
        <AnnotationMark
          // Two annotations can quote the same words on one page; the index
          // keeps their keys apart.
          key={`${index}:${annotation.text_ref}`}
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
      className="absolute z-dropdown w-[320px] max-w-[80%] rounded-xl border border-border bg-surface p-4 shadow-popover"
      style={{
        left: `${Math.min(Math.max(box.x, 0.02), 0.62) * 100}%`,
        ...(below
          ? { top: `calc(${(box.y + box.height) * 100}% + 6px)` }
          : { bottom: `calc(${(1 - box.y) * 100}% + 6px)` }),
      }}
      role="note"
    >
      <div className="flex items-center gap-2">
        <span
          className={cx(
            "inline-flex h-5 flex-none items-center rounded-xs px-1.5 text-11 font-semibold tracking-[0.02em] uppercase",
            MARK_STYLES[annotation.type],
          )}
        >
          {annotation.type}
        </span>
        <span className="min-w-0 flex-1 truncate text-12 text-text-muted">
          {annotation.text_ref}
        </span>
        <button
          className={cx(inlineIconButtonClass, "-mr-1.5 text-text-muted hover:bg-surface-subtle hover:text-text-primary")}
          type="button"
          onClick={onClose}
          aria-label="Close note"
        >
          <CloseIcon className="size-3" />
        </button>
      </div>
      <p className="mt-3 mb-0 text-13 text-text-primary text-trim">{annotation.note}</p>
    </div>
  );
}

const MODES = [
  { value: "fast", label: "Fast", detail: "Sections as context" },
  { value: "dense", label: "Dense", detail: "More annotations, slower" },
] as const;

function modeLabel(mode: AnnotationRetrievalMode | null): string {
  return MODES.find((option) => option.value === mode)?.label ?? "…";
}

/** The design's "About this viewer" popup, describing what each control does. */
function ViewerHelp() {
  return (
    <div className="absolute top-[calc(100%+6px)] right-0 z-dropdown w-[320px] rounded-xl border border-border bg-surface p-4 shadow-popover">
      <div className="text-13 font-semibold text-text-primary text-trim">About this viewer</div>
      <p className="mt-2 mb-0 text-13 text-text-secondary text-trim">
        Annotations are generated by <b className="font-semibold text-text-primary">annotagent</b>.
        It extracts the PDF text, then writes highlights, notes, and jargon definitions anchored to
        the page layout.
      </p>
      <ul className="mt-3 mb-0 flex list-none gap-3 border-t border-hairline p-0 pt-3">
        {LEGEND.map(({ type, label }) => (
          <li className="flex items-center gap-1.5 text-12 text-text-secondary" key={type}>
            <span className={cx("h-2.5 w-2.5 rounded-[2px] border-b-2", MARK_STYLES[type])} />
            <span className="text-trim">{label}</span>
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
  return <p className="py-20 text-center text-13 text-text-secondary">{children}</p>;
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
