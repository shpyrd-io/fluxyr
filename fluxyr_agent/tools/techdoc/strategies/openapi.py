"""OpenAPI / Swagger strategy.

For any site that renders an OpenAPI spec (Redoc, Swagger UI, ReadMe
references), the smartest path is to find the spec JSON/YAML and convert
*that* to Markdown, not scrape the rendered HTML.

Discovery order:
  1. Fingerprint already found an openapi_url_hint in the page HTML.
  2. Try a list of well-known spec paths relative to the entry URL.
  3. Give up — return None.

Conversion: we don't depend on widdershins (Node). We render a focused
Markdown ourselves: title, description, servers, then one section per
tag, then one subsection per operation with method/path/summary/params/
request body schema/response schemas.

The output is "good enough for an LLM" — not pretty docs for humans.
That's the whole point: maximize signal density, minimize tokens.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml

from ..http import get_with_retry, make_client
from ..models import Bundle, Fingerprint, Page
from .base import Strategy

log = logging.getLogger(__name__)


# Paths to try if no hint was found in the page HTML.
# Ordered roughly by frequency in the wild.
_WELL_KNOWN_SPEC_PATHS = [
    "/openapi.json",
    "/openapi.yaml",
    "/openapi.yml",
    "/swagger.json",
    "/swagger.yaml",
    "/v2/api-docs",
    "/v3/api-docs",
    "/api-docs",
    "/docs/swagger.json",
    "/docs/openapi.json",
    "/api/openapi.json",
    "/api/swagger.json",
    "/spec.json",
    "/spec.yaml",
]


class OpenApiStrategy(Strategy):
    name = "openapi"

    def can_handle(self, fp: Fingerprint) -> bool:
        # Worth trying if:
        #  - we already spotted a spec URL,
        #  - or the platform is Redoc/Swagger UI,
        #  - or the platform is "custom" (many bank portals embed Redoc),
        #  - or it's UNKNOWN — cheap to probe, no harm in trying.
        return True  # Always worth trying first; well-known paths are cheap probes.

    def extract(self, fp: Fingerprint, name: str) -> Bundle | None:
        client = make_client()
        spec_url, spec = self._find_spec(client, fp)
        if not spec:
            return None

        try:
            pages = self._spec_to_pages(spec, spec_url)
        except Exception as e:
            log.exception("openapi: spec parse failed for %s: %s", spec_url, e)
            return None

        if not pages:
            return None

        return Bundle(
            name=name,
            entry_url=fp.url,
            platform=fp.platform,
            strategy=self.name,
            pages=pages,
        )

    # ── discovery ────────────────────────────────────────────────────

    def _find_spec(
        self, client: httpx.Client, fp: Fingerprint
    ) -> tuple[str | None, dict[str, Any] | None]:
        candidates: list[str] = []
        if fp.openapi_url_hint:
            candidates.append(fp.openapi_url_hint)
        # Also try sibling paths relative to the final URL.
        base = fp.signals.get("final_url") or fp.url
        for path in _WELL_KNOWN_SPEC_PATHS:
            candidates.append(urljoin(str(base), path))
        # And relative to the URL stripped of path.
        # e.g. https://api.hevyapp.com/docs/ -> https://api.hevyapp.com/<path>
        from urllib.parse import urlparse

        parsed = urlparse(str(base))
        root = f"{parsed.scheme}://{parsed.netloc}"
        for path in _WELL_KNOWN_SPEC_PATHS:
            candidates.append(root + path)

        seen: set[str] = set()
        for url in candidates:
            if url in seen:
                continue
            seen.add(url)
            spec = self._try_fetch_spec(client, url)
            if spec:
                log.info("openapi: found spec at %s", url)
                return url, spec

        # swagger-ui-init.js: embeds the full spec as swaggerDoc JSON (swagger-jsdoc /
        # swagger-ui-express pattern, common in Node APIs like Hevy).
        for init_url in (
            urljoin(str(base), "swagger-ui-init.js"),
            root + "/swagger-ui-init.js",
        ):
            spec = self._try_extract_inline_swagger(client, init_url)
            if spec:
                log.info("openapi: extracted inline spec from %s", init_url)
                return init_url, spec

        # swagger-initializer.js: standard Swagger UI distribution file that holds
        # url: "openapi.json" — the spec is external, we resolve and fetch it.
        for init_url in (
            urljoin(str(base), "swagger-initializer.js"),
            root + "/swagger-initializer.js",
        ):
            spec_url = self._extract_spec_url_from_initializer(client, init_url)
            if spec_url:
                spec = self._try_fetch_spec(client, spec_url)
                if spec:
                    log.info(
                        "openapi: found spec %s via swagger-initializer.js", spec_url
                    )
                    return spec_url, spec

        # Scan the main JS bundle for embedded spec URL references (e.g. starkbank
        # embeds '/static/openapi-v2.yml' as a string literal in their SPA bundle).
        bundle_url = fp.signals.get("main_js_bundle")
        if bundle_url:
            spec_url, spec = self._scan_js_bundle_for_spec(client, bundle_url, root)
            if spec:
                log.info("openapi: found spec URL %s via JS bundle scan", spec_url)
                return spec_url, spec

        # Redocly-hosted portals inline the full spec in a state JS file.
        redocly_state_url = fp.signals.get("redocly_state_js")
        if redocly_state_url:
            spec = self._try_extract_redocly_state(client, redocly_state_url)
            if spec:
                log.info(
                    "openapi: extracted inline spec from Redocly state JS %s",
                    redocly_state_url,
                )
                return redocly_state_url, spec

        return None, None

    @staticmethod
    def _extract_spec_url_from_initializer(
        client: httpx.Client, init_url: str
    ) -> str | None:
        """Parse swagger-initializer.js and extract the spec URL.

        The standard Swagger UI distribution ships a swagger-initializer.js that
        contains: SwaggerUIBundle({ url: "openapi.json", ... }).
        We extract that url value and resolve it relative to the initializer's directory.
        """
        r = get_with_retry(client, init_url)
        if not r or r.status_code >= 400:
            return None
        m = re.search(r'\burl\s*:\s*["\']([^"\']+)["\']', r.text)
        if not m:
            return None
        spec_path = m.group(1)
        # Resolve relative to the directory that contains swagger-initializer.js.
        base_dir = init_url.rsplit("/", 1)[0] + "/"
        return urljoin(base_dir, spec_path)

    @staticmethod
    def _scan_js_bundle_for_spec(
        client: httpx.Client, bundle_url: str, root: str
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Download the main JS bundle and extract any OpenAPI spec URL references.

        Looks for string literals that look like spec paths:
          - any path ending in .json / .yaml / .yml that also contains an
            OpenAPI-ish keyword (openapi, swagger, api-docs, spec)
          - any plain .yaml / .yml path under /static/, /api/, /docs/
        Returns (spec_url, spec_dict) for the first parseable spec found.
        """

        r = get_with_retry(client, bundle_url)
        if not r or r.status_code >= 400:
            return None, None

        text = r.text
        candidates: list[str] = []

        # Pattern 1: paths with openapi/swagger keyword and spec extension
        for m in re.finditer(
            r"""['"]((?:/|https?://)[^'"]*(?:openapi|swagger|api[-_]docs|api[-_]spec)[^'"]*\.(?:json|ya?ml))['"']""",
            text,
            re.IGNORECASE,
        ):
            candidates.append(m.group(1))

        # Pattern 2: any .yaml/.yml under common static dirs (no keyword required)
        for m in re.finditer(
            r"""['"](?P<path>/(?:static|api|docs|spec|assets)/[^'"]+\.ya?ml)['"]""",
            text,
        ):
            candidates.append(m.group("path"))

        seen: set[str] = set()
        for path in candidates:
            url = (
                path
                if path.startswith("http")
                else urljoin(root + "/", path.lstrip("/"))
            )
            if url in seen:
                continue
            seen.add(url)
            spec = OpenApiStrategy._try_fetch_spec(client, url)
            if spec:
                return url, spec

        return None, None

    @staticmethod
    def _try_extract_inline_swagger(
        client: httpx.Client, url: str
    ) -> dict[str, Any] | None:
        """Pull the `swaggerDoc` JSON object out of a swagger-ui-init.js file.

        The file looks like:
            var options = {
              "swaggerDoc": { ...full spec... },
              ...
            };
        We find the swaggerDoc key and balance braces to extract the object.
        """
        r = get_with_retry(client, url)
        if not r or r.status_code >= 400:
            return None
        text = r.text
        marker = '"swaggerDoc":'
        idx = text.find(marker)
        if idx == -1:
            return None
        start = text.find("{", idx + len(marker))
        if start == -1:
            return None
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = text[start : i + 1]
                    try:
                        data = json.loads(blob)
                    except Exception:
                        return None
                    if isinstance(data, dict) and (
                        "openapi" in data or "swagger" in data
                    ):
                        return data
                    return None
        return None

    @staticmethod
    def _try_extract_redocly_state(
        client: httpx.Client, url: str
    ) -> dict[str, Any] | None:
        """Extract OpenAPI spec from a Redocly inline state JS file.

        Redocly-hosted portals embed the full spec as:
            const __redoc_state = JSON.parse("...");
        where the inner string is a JSON-encoded object with the spec at
        definition.data.
        """
        r = get_with_retry(client, url)
        if not r or r.status_code >= 400:
            return None
        m = re.search(
            r'const __redoc_state = JSON\.parse\((".*")\);', r.text, re.DOTALL
        )
        if not m:
            return None
        try:
            outer = json.loads(m.group(1))  # the outer JSON string
            inner = json.loads(outer)  # the inner JSON object
            spec = inner.get("definition", {}).get("data", {})
            if isinstance(spec, dict) and ("openapi" in spec or "swagger" in spec):
                return spec
        except Exception:
            pass
        return None

    @staticmethod
    def _try_fetch_spec(client: httpx.Client, url: str) -> dict[str, Any] | None:
        r = get_with_retry(client, url)
        if not r or r.status_code >= 400:
            return None
        ctype = r.headers.get("content-type", "")
        text = r.text
        try:
            if "yaml" in ctype or url.endswith((".yaml", ".yml")):
                data = yaml.safe_load(text)
            else:
                data = json.loads(text)
        except Exception:
            # Some servers return JSON without proper content-type. Try both.
            try:
                data = json.loads(text)
            except Exception:
                try:
                    data = yaml.safe_load(text)
                except Exception:
                    return None
        # Sanity check: must look like an OpenAPI/Swagger spec.
        if not isinstance(data, dict):
            return None
        if "openapi" not in data and "swagger" not in data:
            return None
        return data

    # ── conversion ───────────────────────────────────────────────────

    def _spec_to_pages(self, spec: dict[str, Any], spec_url: str | None) -> list[Page]:
        """Render the spec into one overview page + one page per tag.

        Splitting by tag gives the LLM-side a natural retrieval unit and
        keeps individual pages small enough to embed."""
        info = spec.get("info", {})
        title = info.get("title", "API")
        version = info.get("version", "")
        description = info.get("description", "")
        servers = spec.get("servers") or []

        # ── overview page ───────────────────────────────────────────
        overview_lines = [f"# {title}".strip()]
        if version:
            overview_lines.append(f"_Version: {version}_")
        if description:
            overview_lines.append("")
            overview_lines.append(description)
        if servers:
            overview_lines.append("\n## Servers\n")
            for s in servers:
                url = s.get("url", "")
                desc = s.get("description", "")
                overview_lines.append(f"- `{url}`" + (f" — {desc}" if desc else ""))

        overview_md = "\n".join(overview_lines)

        # ── group operations by tag ──────────────────────────────────
        paths = spec.get("paths") or {}
        by_tag: dict[str, list[str]] = {}

        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                if method.lower() not in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                }:
                    continue
                if not isinstance(op, dict):
                    continue
                tags = op.get("tags") or ["default"]
                op_md = _render_operation(path, method, op, spec)
                for tag in tags:
                    by_tag.setdefault(tag, []).append(op_md)

        pages: list[Page] = [
            Page(
                url=spec_url or "",
                title=title,
                markdown=overview_md,
                source_strategy="openapi",
                word_count=len(overview_md.split()),
                metadata={"role": "overview", "spec_url": spec_url},
            )
        ]
        for tag, ops in sorted(by_tag.items()):
            body = f"# {tag}\n\n" + "\n\n---\n\n".join(ops)
            pages.append(
                Page(
                    url=spec_url or "",
                    title=f"{title} — {tag}",
                    markdown=body,
                    source_strategy="openapi",
                    word_count=len(body.split()),
                    metadata={"role": "tag", "tag": tag, "spec_url": spec_url},
                )
            )

        return pages


def _json_default(obj: object) -> str:
    """JSON serializer fallback: converts datetime/date to ISO string."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ── operation rendering ────────────────────────────────────────────────


def _render_operation(
    path: str, method: str, op: dict[str, Any], spec: dict[str, Any]
) -> str:
    parts: list[str] = []
    summary = op.get("summary", "").strip()
    op_id = op.get("operationId", "").strip()
    heading = f"## `{method.upper()} {path}`"
    if summary:
        heading += f" — {summary}"
    parts.append(heading)
    if op_id:
        parts.append(f"_operationId: `{op_id}`_")

    desc = op.get("description", "").strip()
    if desc:
        parts.append(desc)

    # parameters
    params = op.get("parameters") or []
    if params:
        parts.append("### Parameters\n")
        parts.append("| Name | In | Type | Required | Description |")
        parts.append("|---|---|---|---|---|")
        for p in params:
            name = p.get("name", "")
            in_ = p.get("in", "")
            schema = p.get("schema") or {}
            ptype = schema.get("type", "")
            required = "yes" if p.get("required") else ""
            pdesc = (p.get("description") or "").replace("\n", " ").strip()
            parts.append(f"| `{name}` | {in_} | {ptype} | {required} | {pdesc} |")

    # request body
    rb = op.get("requestBody") or {}
    content = (rb.get("content") or {}) if isinstance(rb, dict) else {}
    if content:
        parts.append("### Request body\n")
        for ctype, c in content.items():
            schema = c.get("schema") or {}
            parts.append(f"**`{ctype}`**")
            parts.append("```json")
            parts.append(
                json.dumps(
                    _compact_schema(schema, spec), indent=2, default=_json_default
                )[:2000]
            )
            parts.append("```")

    # responses
    responses = op.get("responses") or {}
    if responses:
        parts.append("### Responses\n")
        for code, r in responses.items():
            rdesc = (r.get("description") or "").strip() if isinstance(r, dict) else ""
            parts.append(f"- **{code}** — {rdesc}")
            rcontent = (r.get("content") or {}) if isinstance(r, dict) else {}
            for ctype, c in rcontent.items():
                schema = c.get("schema") or {}
                parts.append(f"  - `{ctype}`:")
                parts.append("    ```json")
                # indent each line
                compact = json.dumps(
                    _compact_schema(schema, spec), indent=2, default=_json_default
                )[:1500]
                parts.extend("    " + line for line in compact.splitlines())
                parts.append("    ```")

    return "\n\n".join(parts)


def _compact_schema(
    schema: dict[str, Any], spec: dict[str, Any], depth: int = 0
) -> Any:
    """Inline $ref to make the schema legible without external context.
    Cap recursion to avoid blowing up on recursive schemas."""
    if depth > 5 or not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], spec)
        if target is None:
            return {"$ref": schema["$ref"]}
        return _compact_schema(target, spec, depth + 1)
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _compact_schema(pv, spec, depth + 1) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = _compact_schema(v, spec, depth + 1)
        elif k in {"allOf", "anyOf", "oneOf"} and isinstance(v, list):
            out[k] = [_compact_schema(it, spec, depth + 1) for it in v]
        else:
            out[k] = v
    return out


def _resolve_ref(ref: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None
