import { lazy, Suspense } from "react";
import { Button } from "./components";
import { filePreviewKind } from "./file-preview-kind";

const PdfPreview = lazy(() => import("./pdf-preview"));

export function LocalFilePreview({
  url,
  title = "File preview",
  onClose,
}: {
  url: string;
  title?: string;
  onClose?: () => void;
}) {
  if (!url.startsWith("/preview/"))
    return <p role="alert">Invalid local preview.</p>;
  const kind = filePreviewKind(url);
  return (
    <div className="panel preview-card">
      <div className="preview-toolbar">
        <strong>{title}</strong>
        <a href={url} download>
          Download
        </a>
        {kind === "frame" && (
          <a href={url} target="_blank" rel="noreferrer">
            Open ↗
          </a>
        )}
        {onClose && <Button onClick={onClose}>Close</Button>}
      </div>
      {kind === "pdf" ? (
        <Suspense fallback={<p role="status">Loading PDF viewer…</p>}>
          <PdfPreview key={url} url={url} />
        </Suspense>
      ) : kind === "image" ? (
        <img className="file-image-preview" src={url} alt={title} />
      ) : (
        <iframe title={title} src={url} sandbox="allow-scripts" />
      )}
    </div>
  );
}
