export function filePreviewKind(path: string): "pdf" | "image" | "frame" {
  let pathname = path;
  if (path.startsWith("/preview/")) {
    try {
      pathname = decodeURIComponent(path.split(/[?#]/, 1)[0]);
    } catch {
      /* Keep malformed URLs as literal paths. */
    }
  }
  pathname = pathname.toLowerCase();
  if (pathname.endsWith(".pdf")) return "pdf";
  if (/\.(png|jpe?g|gif|webp|svg|avif|bmp|ico)$/.test(pathname)) return "image";
  return "frame";
}

export function filePreviewUrl(path: string) {
  return "/preview/" + path.split("/").map(encodeURIComponent).join("/");
}

// Markdown image paths refer to the Files directory, not frontend routes.
// Preserve URL encoding: Markdown has already converted the destination to a URL.
export function markdownImageUrl(url: string) {
  if (!url || /^(?:[a-z][\w+.-]*:|\/|#)/i.test(url)) return url;
  return "/preview/" + url.replace(/^(?:\.\/)+/, "");
}
