import assert from "node:assert/strict";
import test from "node:test";
import { filePreviewKind, filePreviewUrl } from "./file-preview-kind.ts";

test("PDFs and images open as previews instead of UTF-8 editors", () => {
  assert.equal(filePreviewKind("extrato.PDF"), "pdf");
  assert.equal(filePreviewKind("certificates/scan.png"), "image");
  assert.equal(filePreviewKind("report.pdf/notes.txt"), "frame");
  assert.equal(filePreviewKind("report.html"), "frame");
});

test("preview URLs preserve filenames containing accents and URL delimiters", () => {
  const name = "relatórios/100% saldo?#.PDF";
  assert.equal(filePreviewUrl(name), "/preview/relat%C3%B3rios/100%25%20saldo%3F%23.PDF");
  assert.equal(filePreviewKind(name), "pdf");
  assert.equal(filePreviewKind(filePreviewUrl(name)), "pdf");
  assert.equal(filePreviewKind("/preview/report.PDF?version=2"), "pdf");
});
