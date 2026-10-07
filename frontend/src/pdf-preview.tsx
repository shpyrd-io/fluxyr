import { useEffect, useRef, useState } from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentProxy,
  type RenderTask,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Button } from "./components";

GlobalWorkerOptions.workerSrc = workerUrl;

export default function PdfPreview({ url }: { url: string }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [width, setWidth] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const task = getDocument({ url });
    task.promise
      .then((document) => {
        if (alive) setPdf(document);
      })
      .catch(() => {
        if (alive) {
          setError("Unable to open this PDF. Download it to view it locally.");
          setBusy(false);
        }
      });
    return () => {
      alive = false;
      void task.destroy();
    };
  }, [url]);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.floor(entry.contentRect.width)),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!pdf || width <= 0) return;
    let cancelled = false;
    let render: RenderTask | undefined;
    setBusy(true);
    setError("");
    const canvas = document.createElement("canvas");
    void (async () => {
      try {
        const pdfPage = await pdf.getPage(page);
        if (cancelled) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const viewport = pdfPage.getViewport({
          scale: Math.min(width / base.width, 2),
        });
        // Render one page at a time, with a bounded backing buffer on Retina screens.
        const ratio = Math.min(
          window.devicePixelRatio || 1,
          2,
          Math.sqrt(4_000_000 / (viewport.width * viewport.height)),
        );
        canvas.width = Math.ceil(viewport.width * ratio);
        canvas.height = Math.ceil(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        canvas.setAttribute("role", "img");
        canvas.setAttribute(
          "aria-label",
          `PDF page ${page} of ${pdf.numPages}`,
        );
        render = pdfPage.render({
          canvas,
          viewport,
          transform: [ratio, 0, 0, ratio, 0, 0],
        });
        await render.promise;
        if (!cancelled) host.current?.replaceChildren(canvas);
        pdfPage.cleanup();
      } catch {
        if (!cancelled)
          setError(
            "Unable to render this page. You can still download the PDF.",
          );
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
      render?.cancel();
    };
  }, [pdf, page, width]);

  return (
    <div className="pdf-preview">
      <div className="pdf-controls">
        <Button disabled={!pdf || page <= 1} onClick={() => setPage(page - 1)}>
          Previous
        </Button>
        <span>{pdf ? `Page ${page} of ${pdf.numPages}` : "PDF"}</span>
        <Button
          disabled={!pdf || page >= pdf.numPages}
          onClick={() => setPage(page + 1)}
        >
          Next
        </Button>
      </div>
      {busy && <p role="status">Loading PDF…</p>}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="pdf-page" ref={host} aria-busy={busy} />
    </div>
  );
}
