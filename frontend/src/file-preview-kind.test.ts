import assert from "node:assert/strict";
import test from "node:test";
import { filePreviewKind, filePreviewUrl, markdownImageUrl } from "./file-preview-kind.ts";

test("PDFs and images open as previews instead of UTF-8 editors", () => {
  assert.equal(filePreviewKind("extrato.PDF"), "pdf");
  assert.equal(filePreviewKind("certificates/scan.png"), "image");
  assert.equal(filePreviewKind("report.pdf/notes.txt"), "frame");
  assert.equal(filePreviewKind("report.html"), "frame");
});

test("Markdown screenshot paths use the Files preview route", () => {
  assert.equal(markdownImageUrl("browser/capture.webp"), "/preview/browser/capture.webp");
  assert.equal(markdownImageUrl("./browser/my%20capture.webp"), "/preview/browser/my%20capture.webp");
  for (const url of ["", "/preview/browser/capture.webp", "https://example.com/image.webp", "//example.com/image.webp"]) {
    assert.equal(markdownImageUrl(url), url);
  }
});

test("preview URLs preserve filenames containing accents and URL delimiters", () => {
  const name = "relatórios/100% saldo?#.PDF";
  assert.equal(filePreviewUrl(name), "/preview/relat%C3%B3rios/100%25%20saldo%3F%23.PDF");
  assert.equal(filePreviewKind(name), "pdf");
  assert.equal(filePreviewKind(filePreviewUrl(name)), "pdf");
  assert.equal(filePreviewKind("/preview/report.PDF?version=2"), "pdf");
});
