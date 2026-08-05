import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { messageFrom } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS, useDismissAnimation } from "../../lib/animation";
import { paperPdfUrl, repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { CloseIcon } from "../ui/icons";
import type { AnnotationType, PaperAnnotation, PaperDetails } from "../../lib/types";

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
};

/**
 * The paper, with its annotations drawn where they belong.
 *
 * Annotations are stored as fractions of a page rather than as pixels, so a
 * mark is positioned in percentages over the rendered page and stays correct at
 * whatever width the page is drawn.
 */
export function AnnotatedPaperReader({ workspaceId, paper, onClose }: ReaderProps) {
  const { closing, dismiss } = useDismissAnimation(onClose, DIALOG_EXIT_MS);
  const [annotations, setAnnotations] = useState<PaperAnnotation[] | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [selected, setSelected] = useState<PaperAnnotation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [documentError, setDocumentError] = useState<string | null>(null);

  const file = useMemo(
    () => ({ url: paperPdfUrl(workspaceId, paper.paperId) }),
    [workspaceId, paper.paperId],
  );

  useEffect(() => {
    let cancelled = false;
    repositoryWorkspaceGateway
      .getPaperAnnotations(workspaceId, paper.paperId)
      .then((result) => {
        if (!cancelled) setAnnotations(result);
      })
      .catch((requestError) => {
        if (!cancelled) setError(messageFrom(requestError));
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, paper.paperId]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      if (selected) setSelected(null);
      else dismiss();
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [dismiss, selected]);

  const byPage = useMemo(() => groupByPage(annotations ?? []), [annotations]);

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
      <header className="flex flex-none items-center gap-3 border-b border-hairline bg-surface px-5 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="m-0 truncate text-[14px] font-semibold tracking-[-0.01em] text-text-primary">
            {paper.title}
          </h2>
          <p className="mt-0.5 mb-0 text-[12px] text-text-secondary">
            <ReaderStatus annotations={annotations} error={error} />
          </p>
        </div>
        <Legend />
        <button
          className="grid h-[26px] w-[26px] flex-none place-items-center rounded-[6px] border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary"
          type="button"
          onClick={dismiss}
          aria-label="Close reader"
          title="Close"
        >
          <CloseIcon className="h-3 w-3" />
        </button>
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

function ReaderStatus({
  annotations,
  error,
}: {
  annotations: PaperAnnotation[] | null;
  error: string | null;
}) {
  if (error) return <span className="text-error">{error}</span>;
  if (annotations === null) {
    return <>Reading the paper and writing annotations. This takes a few minutes the first time.</>;
  }
  if (annotations.length === 0) return <>No annotations were found in this paper.</>;
  return <>{annotations.length} annotations</>;
}

function Legend() {
  return (
    <ul className="m-0 hidden list-none gap-3 p-0 md:flex">
      {LEGEND.map(({ type, label }) => (
        <li className="flex items-center gap-1.5 text-[11.5px] text-text-secondary" key={type}>
          <span className={cx("h-2.5 w-2.5 rounded-[3px] border-b-2", MARK_STYLES[type])} />
          {label}
        </li>
      ))}
    </ul>
  );
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
