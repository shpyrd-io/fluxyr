"""WebBrowserToolProvider — native web browsing and content extraction tool.

Pure-stdlib implementation (urllib + html.parser + re). No external dependencies.
Inspired by Mozilla Readability / Firecrawl / Jina Reader heuristics.
"""

import json
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

import certifi

from fluxyr_agent.tools.contracts import (
    ToolDefinition,
    ToolExecutionContext,
    ToolProvider,
    ToolResult,
)
from fluxyr_agent.tools.urls import validate_url

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SECONDS = 30
_MAX_DOWNLOAD_BYTES = 10_000_000  # 10 MB hard cap on download
_MAX_CONTENT_LENGTH = 50_000  # chars returned to the AI
_USER_AGENT = "Mozilla/5.0 (compatible; FluxyrBot/1.0; +https://fluxyr.com/bot)"
_SNIPPET_MAX_CHARS = 200  # web_extract's persisted-friendly preview

# ---------------------------------------------------------------------------
# HTML parsing constants
# ---------------------------------------------------------------------------

_DROP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "iframe",
        "object",
        "embed",
        "form",
        "button",
        "input",
        "select",
        "textarea",
        "meta",
        "link",
        "head",
        "template",
        "canvas",
        "audio",
        "video",
        "source",
        "track",
        "map",
        "area",
        "param",
        "base",
        "dialog",
    }
)
_CHROME_TAGS = frozenset({"nav", "aside", "footer", "header"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_INLINE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "cite",
        "code",
        "em",
        "i",
        "kbd",
        "mark",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "br",
    }
)
_NEG_RE = re.compile(
    r"(nav(?:igation|bar)?|footer|sidebar|comments?|advert(?:isement)?s?|ads"
    r"|promo|share|social|breadcrumb|pagination|related|recommend|popup"
    r"|modal|cookie|banner|widget|toolbar|disclaimer|copyright|legal"
    r"|hidden|skip-?link|hamburger|dropdown|tooltip|tags?|meta-?info"
    r"|byline|subscribe|newsletter|signup|login|search-?box|menu|topbar"
    r")(?:[\s_-]|$)",
    re.IGNORECASE,
)
_POS_RE = re.compile(
    r"(article|content|post|story|main|body|entry|text|page"
    r"|markdown|prose|read|chapter|hentry|h-entry)(?:[\s_-]|$)",
    re.IGNORECASE,
)
_PRE_CLEAN_PATTERNS = [
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<noscript\b[^>]*>.*?</noscript>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<head\b[^>]*>.*?</head>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<iframe\b[^>]*>.*?</iframe>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<template\b[^>]*>.*?</template>", re.DOTALL | re.IGNORECASE),
]
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_META_RE = re.compile(
    r'<meta\b[^>]*?(?:name|property)\s*=\s*["\']([^"\']+)["\'][^>]*?'
    r'content\s*=\s*["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_META_RE_REV = re.compile(
    r'<meta\b[^>]*?content\s*=\s*["\']([^"\']*)["\'][^>]*?'
    r'(?:name|property)\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# HTML tree
# ---------------------------------------------------------------------------


class _Element:
    __slots__ = ("attrs", "children", "parent", "tag")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent

    def text(self, sep=" "):
        parts = []
        stack = [self]
        while stack:
            n = stack.pop()
            if isinstance(n, str):
                parts.append(n)
            else:
                for c in reversed(n.children):
                    stack.append(c)
        return sep.join(p for p in parts if p)

    def find_all(self, tag):
        out = []
        stack = list(self.children)
        while stack:
            n = stack.pop()
            if isinstance(n, _Element):
                if n.tag == tag:
                    out.append(n)
                stack.extend(reversed(n.children))
        return out

    def find_by_class_or_id(self, token: str):
        """Find elements whose class or id contains *token* (case-insensitive)."""
        token_lower = token.lower()
        out = []
        stack = list(self.children)
        while stack:
            n = stack.pop()
            if isinstance(n, _Element):
                ci = f"{n.attrs.get('class', '')} {n.attrs.get('id', '')}".lower()
                if token_lower in ci:
                    out.append(n)
                stack.extend(reversed(n.children))
        return out


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("__root__")
        self.cur = self.root
        self.skip_tag = None

    def handle_starttag(self, tag, attrs):
        if self.skip_tag is not None:
            return
        if tag in _DROP_TAGS:
            if tag not in _VOID_TAGS:
                self.skip_tag = tag
            return
        node = _Element(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID_TAGS:
            self.cur = node

    def handle_endtag(self, tag):
        if self.skip_tag is not None:
            if tag == self.skip_tag:
                self.skip_tag = None
            return
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_startendtag(self, tag, attrs):
        if self.skip_tag is not None or tag in _DROP_TAGS:
            return
        node = _Element(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)

    def handle_data(self, data):
        if self.skip_tag is None and data:
            self.cur.children.append(data)


def _parse_html(html_str: str) -> _Element:
    b = _TreeBuilder()
    b.feed(html_str)
    b.close()
    return b.root


# ---------------------------------------------------------------------------
# Content heuristics
# ---------------------------------------------------------------------------


def _pre_clean(html_str: str) -> str:
    for pat in _PRE_CLEAN_PATTERNS:
        html_str = pat.sub("", html_str)
    return html_str


def _class_id(elem: _Element) -> str:
    return f"{elem.attrs.get('class', '')} {elem.attrs.get('id', '')}"


def _remove_by_predicate(root: _Element, predicate) -> None:
    def walk(n):
        new_children = []
        for c in n.children:
            if isinstance(c, _Element):
                if predicate(c):
                    continue
                walk(c)
            new_children.append(c)
        n.children = new_children

    walk(root)


def _score_element(elem: _Element) -> float:
    text = elem.text()
    text_len = len(text)
    if text_len < 100:
        return -1.0
    p_count = len(elem.find_all("p"))
    a_links = elem.find_all("a")
    link_text_len = sum(len(a.text()) for a in a_links)
    link_density = link_text_len / text_len if text_len else 0
    score = text_len + (p_count * 100) - (link_density * text_len * 0.5)
    ci = _class_id(elem)
    if _POS_RE.search(ci):
        score += 250
    if _NEG_RE.search(ci):
        score -= 250
    if len(a_links) > p_count * 3 and len(a_links) > 5:
        score -= 200
    return score


def _find_main_content(root: _Element) -> _Element:
    for tag in ("main", "article"):
        nodes = root.find_all(tag)
        if nodes:
            cand = max(nodes, key=lambda e: len(e.text()))
            if len(cand.text()) > 100:
                return cand
    _remove_by_predicate(root, lambda e: e.tag in _CHROME_TAGS)
    _remove_by_predicate(
        root,
        lambda e: (
            e.tag not in _INLINE_TAGS
            and bool(_class_id(e).strip())
            and bool(_NEG_RE.search(_class_id(e)))
            and not bool(_POS_RE.search(_class_id(e)))
        ),
    )
    candidates = []
    stack = [root]
    while stack:
        n = stack.pop()
        if isinstance(n, _Element):
            if n.tag in ("div", "section"):
                s = _score_element(n)
                if s > 0:
                    candidates.append((s, n))
            stack.extend(c for c in n.children if isinstance(c, _Element))
    if candidates:
        return max(candidates, key=lambda x: x[0])[1]
    bodies = root.find_all("body")
    return bodies[0] if bodies else root


# ---------------------------------------------------------------------------
# Markdown conversion
# ---------------------------------------------------------------------------


def _to_markdown(
    elem: _Element,
    base_url: str = "",
    include_links: bool = True,
    include_images: bool = True,
) -> str:
    buffers = [[]]
    list_stack: list = []

    def emit(s):
        buffers[-1].append(s)

    def push():
        buffers.append([])

    def pop():
        return "".join(buffers.pop())

    def render_table(table):
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for cell in tr.children:
                if isinstance(cell, _Element) and cell.tag in ("th", "td"):
                    t = re.sub(r"\s+", " ", cell.text(sep=" ")).strip()
                    cells.append(t.replace("|", r"\|").replace("\n", " "))
            if cells:
                rows.append(cells)
        if not rows:
            return
        ncols = len(rows[0])
        emit("| " + " | ".join(rows[0]) + " |\n")
        emit("| " + " | ".join("---" for _ in range(ncols)) + " |\n")
        for r in rows[1:]:
            r = list(r) + [""] * (ncols - len(r))
            emit("| " + " | ".join(r[:ncols]) + " |\n")

    def render(n, in_pre=False):
        if isinstance(n, str):
            emit(n if in_pre else re.sub(r"\s+", " ", n))
            return
        tag = n.tag
        if tag == "__root__":
            for c in n.children:
                render(c, in_pre)
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            push()
            for c in n.children:
                render(c, in_pre)
            inner = pop().strip()
            if inner:
                emit(f"\n\n{'#' * int(tag[1])} {inner}\n\n")
            return
        if tag == "p":
            push()
            for c in n.children:
                render(c, in_pre)
            inner = pop().strip()
            if inner:
                emit(f"\n\n{inner}\n\n")
            return
        if tag == "br":
            emit("  \n")
            return
        if tag == "hr":
            emit("\n\n---\n\n")
            return
        if tag in ("strong", "b"):
            push()
            for c in n.children:
                render(c, in_pre)
            inner = pop().strip()
            if inner:
                emit(f"**{inner}**")
            return
        if tag in ("em", "i"):
            push()
            for c in n.children:
                render(c, in_pre)
            inner = pop().strip()
            if inner:
                emit(f"*{inner}*")
            return
        if tag == "code" and not in_pre:
            push()
            for c in n.children:
                render(c, True)
            inner = pop()
            if inner:
                emit(f"`{inner}`")
            return
        if tag == "pre":
            lang = ""
            for c in n.children:
                if isinstance(c, _Element) and c.tag == "code":
                    m = re.search(r"language-([\w+-]+)", c.attrs.get("class", ""))
                    if m:
                        lang = m.group(1)
                    break
            push()
            for c in n.children:
                render(c, True)
            inner = pop().strip("\n")
            emit(f"\n\n```{lang}\n{inner}\n```\n\n")
            return
        if tag == "a":
            push()
            for c in n.children:
                render(c, in_pre)
            inner = pop().strip()
            href = n.attrs.get("href", "")
            if href and base_url:
                href = urljoin(base_url, href)
            cls = n.attrs.get("class", "").lower()
            if (
                inner in ("¶", "#", "§", "¤", "⌘", "🔗", "")
                or "headerlink" in cls
                or "anchor-link" in cls
            ):
                return
            if (
                not include_links
                or not href
                or href.startswith("#")
                or href.startswith("javascript:")
            ):
                if inner:
                    emit(inner)
                return
            if inner:
                emit(f"[{inner}]({href})")
            return
        if tag == "img":
            if not include_images:
                return
            alt = (n.attrs.get("alt") or "").strip()
            src = n.attrs.get("src") or n.attrs.get("data-src") or ""
            if src and base_url:
                src = urljoin(base_url, src)
            if src:
                emit(f"![{alt}]({src})")
            return
        if tag in ("ul", "ol"):
            list_stack.append(["ol" if tag == "ol" else "ul", 0])
            emit("\n\n")
            for c in n.children:
                render(c, in_pre)
            list_stack.pop()
            emit("\n\n")
            return
        if tag == "li":
            depth = max(len(list_stack) - 1, 0)
            indent = "  " * depth
            if list_stack and list_stack[-1][0] == "ol":
                list_stack[-1][1] += 1
                marker = f"{list_stack[-1][1]}. "
            else:
                marker = "- "
            push()
            for c in n.children:
                render(c, in_pre)
            inner = re.sub(r"\n{2,}", "\n", pop().strip()).replace(
                "\n", f"\n{indent}  "
            )
            if inner:
                emit(f"\n{indent}{marker}{inner}")
            return
        if tag == "blockquote":
            push()
            for c in n.children:
                render(c, in_pre)
            inner = pop().strip()
            if inner:
                emit("\n\n")
                for line in inner.split("\n"):
                    emit(f"> {line}\n" if line else ">\n")
                emit("\n")
            return
        if tag == "table":
            emit("\n\n")
            render_table(n)
            emit("\n\n")
            return
        if tag in (
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "th",
            "td",
            "caption",
            "col",
            "colgroup",
        ):
            for c in n.children:
                render(c, in_pre)
            return
        if tag in ("div", "section", "article", "main"):
            push()
            for c in n.children:
                render(c, in_pre)
            inner = pop().strip()
            if inner:
                emit(f"\n\n{inner}\n\n")
            return
        for c in n.children:
            render(c, in_pre)

    render(elem)
    return "".join(buffers[0])


def _post_clean(md: str) -> str:
    md = unescape(md)
    md = re.sub(r"(?<! ) +\n", "\n", md)
    md = re.sub(r"\n[ \t]+\n", "\n\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"(\S)  +", r"\1 ", md)
    return md.strip()


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def _extract_metadata(html_str: str) -> dict:
    meta: dict = {}
    m = _TITLE_RE.search(html_str)
    if m:
        meta["title"] = re.sub(r"\s+", " ", unescape(m.group(1))).strip()
    pairs = [
        (m.group(1).lower(), unescape(m.group(2))) for m in _META_RE.finditer(html_str)
    ]
    pairs += [
        (m.group(2).lower(), unescape(m.group(1)))
        for m in _META_RE_REV.finditer(html_str)
    ]
    for name, content in pairs:
        if name == "description" and "description" not in meta:
            meta["description"] = content
        elif name in ("og:title", "twitter:title") and "title" not in meta:
            meta["title"] = content
        elif (
            name in ("og:description", "twitter:description")
            and "description" not in meta
        ):
            meta["description"] = content
        elif name == "author" and "author" not in meta:
            meta["author"] = content
        elif (
            name in ("article:published_time", "datepublished")
            and "published" not in meta
        ):
            meta["published"] = content
        elif name == "og:site_name" and "site_name" not in meta:
            meta["site_name"] = content
    return meta


# ---------------------------------------------------------------------------
# HTTP fetch + parse
# ---------------------------------------------------------------------------


def _detect_charset(content_type: str, body: bytes) -> str:
    ct = (content_type or "").lower()
    if "charset=" in ct:
        cs = ct.split("charset=", 1)[1].split(";", 1)[0].strip().strip("\"'")
        if cs:
            return cs
    m = re.search(
        rb'<meta[^>]+charset\s*=\s*["\']?([\w-]+)', body[:4096], re.IGNORECASE
    )
    if m:
        return m.group(1).decode("ascii", errors="ignore")
    return "utf-8"


def _ascii_safe_url(url: str) -> str:
    """Return an ASCII-safe, percent-encoded version of *url*.

    Splits the URL into components, re-encodes only the non-ASCII characters in
    the path and query while preserving existing ``%xx`` escapes, and IDNA-encodes
    a non-ASCII hostname.  A plain ASCII, already-percent-encoded URL passes
    through unchanged.
    """
    parts = urllib.parse.urlsplit(url)

    # IDNA-encode non-ASCII host; fall back to the original host on failure.
    host = parts.hostname or ""
    port = parts.port
    if host and not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except (UnicodeError, UnicodeDecodeError):
            pass  # keep original host; urllib will raise a clear error later
    # Reconstruct userinfo from components so it is preserved regardless of whether
    # a port is also present.  Non-ASCII credentials are percent-encoded; existing
    # '%' escapes are kept intact by including '%' in the safe set.
    userinfo = ""
    if parts.username is not None:
        userinfo = urllib.parse.quote(parts.username, safe="%")
        if parts.password is not None:
            userinfo += f":{urllib.parse.quote(parts.password, safe='%')}"
        userinfo += "@"
    netloc = f"{userinfo}{host}"
    if port is not None:
        netloc += f":{port}"

    # Re-encode path: preserve existing % escapes and ASCII structural chars.
    safe_path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=")

    # Re-encode query: preserve existing % escapes, structural chars, and +.
    safe_query = urllib.parse.quote(parts.query, safe="%=&+:@!$'()*,;/?")

    return urllib.parse.urlunsplit(
        (parts.scheme, netloc, safe_path, safe_query, parts.fragment)
    )


def _make_snippet(content: str) -> str:
    """Collapse whitespace and cap at ``_SNIPPET_MAX_CHARS`` characters.

    Gives web_extract's slim persistence rule (ai_chat_tool_persistence.py)
    something short to show after reload without persisting the full
    extracted content.
    """
    collapsed = re.sub(r"\s+", " ", content or "").strip()
    return collapsed[:_SNIPPET_MAX_CHARS]


def _run_fetch(
    url: str,
    css_selector: str | None = None,
    max_chars: int = _MAX_CONTENT_LENGTH,
    include_links: bool = True,
    include_images: bool = True,
    timeout: int = _FETCH_TIMEOUT_SECONDS,
    headers: dict | None = None,
    user_agent: str | None = None,
    fmt: str = "markdown",
) -> dict:
    """Fetch *url* and return a structured dict with parsed content.

    Args:
        url: The URL to fetch.
        css_selector: Optional simple tag name or class/id token to scope content
            (e.g. "article", ".main-content"). Falls back to full-page heuristics
            when the selector cannot be matched.
        max_chars: Maximum characters to return in *content*.
        include_links: Whether to render links in markdown and populate the links list.
        include_images: Whether to render images in markdown output.
        timeout: Request timeout in seconds.
        headers: Additional HTTP headers to send (e.g. Authorization).
        user_agent: Custom User-Agent string.
        fmt: Output format — 'markdown' (default), 'text' (plain text), or 'raw' (HTML).

    Returns:
        Dict with keys: success (bool), content (str), title (str), links (list),
        url (str), truncated (bool). On failure: success=False, error (str).
    """
    url = _ascii_safe_url(url)
    req_headers = {
        "User-Agent": user_agent or _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            body = resp.read(_MAX_DOWNLOAD_BYTES + 1)
            if len(body) > _MAX_DOWNLOAD_BYTES:
                body = body[:_MAX_DOWNLOAD_BYTES]
            content_type = resp.headers.get("Content-Type", "")
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        # HTTP status text is already user-facing — pass it through as-is.
        return {"success": False, "error": f"HTTP {exc.code}: {exc.reason}", "url": url}
    except urllib.error.URLError:
        logger.warning("web_browser fetch: URL error for %s", url, exc_info=True)
        return {"success": False, "error": "Could not reach that URL.", "url": url}
    except TimeoutError:
        return {
            "success": False,
            "error": f"Request timed out after {_FETCH_TIMEOUT_SECONDS} seconds.",
            "url": url,
        }
    except UnicodeError:
        logger.warning(
            "web_browser fetch: URL encoding error for %s", url, exc_info=True
        )
        return {
            "success": False,
            "error": "The URL contains characters that could not be encoded.",
            "url": url,
        }
    except OSError:
        logger.warning("web_browser fetch: network error for %s", url, exc_info=True)
        return {
            "success": False,
            "error": "A network error occurred while fetching the page.",
            "url": url,
        }

    charset = _detect_charset(content_type, body)
    try:
        html_str = body.decode(charset, errors="replace")
    except LookupError:
        html_str = body.decode("utf-8", errors="replace")

    meta = _extract_metadata(html_str)

    # Raw format: return HTML directly without parsing
    if fmt == "raw":
        original_length = len(html_str)
        truncated = original_length > max_chars
        content = html_str[:max_chars] if truncated else html_str
        return {
            "success": True,
            "content": content,
            "title": meta.get("title", ""),
            "links": [],
            "url": final_url,
            "truncated": truncated,
            "length": len(content),
            "original_length": original_length,
        }

    is_html = "html" in content_type.lower() or (
        not content_type and body[:200].lstrip().lower().startswith(b"<")
    )
    if not is_html:
        return {
            "success": False,
            "error": "Non-HTML content type; use raw format for plain text or JSON.",
            "url": final_url,
        }

    cleaned = _pre_clean(html_str)
    root = _parse_html(cleaned)

    # Scope to CSS selector when provided
    content_node = None
    if css_selector:
        selector = css_selector.strip().lstrip(".")
        # Try simple tag match first, then class/id token match
        tag_nodes = root.find_all(selector)
        if tag_nodes:
            content_node = max(tag_nodes, key=lambda e: len(e.text()))
        else:
            ci_nodes = root.find_by_class_or_id(selector)
            if ci_nodes:
                content_node = max(ci_nodes, key=lambda e: len(e.text()))
    if content_node is None:
        content_node = _find_main_content(root)

    if fmt == "text":
        content = re.sub(r"\s+", " ", content_node.text(sep=" ")).strip()
    else:  # markdown (default)
        md = _to_markdown(
            content_node,
            base_url=final_url,
            include_links=include_links,
            include_images=include_images,
        )
        content = _post_clean(md)

    links: list = []
    if include_links:
        for a in root.find_all("a"):
            href = a.attrs.get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                links.append(urljoin(final_url, href))

    original_length = len(content)
    truncated = original_length > max_chars
    if truncated:
        content = content[:max_chars]

    out: dict = {
        "success": True,
        "content": content,
        "title": meta.get("title", ""),
        "links": links,
        "url": final_url,
        "truncated": truncated,
        "length": len(content),
        "original_length": original_length,
    }
    for key in ("description", "author", "published", "site_name"):
        if key in meta:
            out[key] = meta[key]
    return out


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_COMMON_OPTIONAL_PARAMS = {
    "format": {
        "type": "string",
        "enum": ["markdown", "text", "raw"],
        "description": (
            "Output format: 'markdown' (default, main content converted to markdown), "
            "'text' (plain text only), or 'raw' (raw HTML, no extraction)."
        ),
    },
    "include_links": {
        "type": "boolean",
        "description": "Include links in markdown output and the links list (default: true). Set false to reduce output size.",
    },
    "include_images": {
        "type": "boolean",
        "description": "Include images in markdown output (default: true). Set false to reduce output size.",
    },
    "max_chars": {
        "type": "integer",
        "description": f"Maximum content size to return in characters (default: {_MAX_CONTENT_LENGTH}). Use smaller values (e.g. 30000) to reduce context usage.",
    },
    "timeout": {
        "type": "integer",
        "description": f"Request timeout in seconds (default: {_FETCH_TIMEOUT_SECONDS}).",
    },
    "headers": {
        "type": "object",
        "description": 'Additional HTTP headers to send, e.g. {"Authorization": "Bearer ..."} (optional).',
    },
    "user_agent": {
        "type": "string",
        "description": "Custom User-Agent string to send with the request (optional).",
    },
}

_WEB_BROWSE = ToolDefinition(
    name="web_browse",
    description=(
        "Fetch a web page and return its content as clean markdown. "
        "Use this to read documentation, articles, or any publicly accessible web content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL of the web page to fetch (must be http or https).",
            },
            **_COMMON_OPTIONAL_PARAMS,
        },
        "required": ["url"],
    },
    parallel_safe=True,
)

_WEB_EXTRACT = ToolDefinition(
    name="web_extract",
    description=(
        "Fetch a web page and extract content matching a CSS selector. "
        "Use this to focus on a specific section of a page. "
        'Supports simple tag names (e.g. "article", "main") and class/id tokens '
        '(e.g. ".main-content", "#content").'
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL of the web page to fetch (must be http or https).",
            },
            "css_selector": {
                "type": "string",
                "description": 'CSS selector to scope the extracted content (e.g. "article", ".main-content").',
            },
            **_COMMON_OPTIONAL_PARAMS,
        },
        "required": ["url", "css_selector"],
    },
    parallel_safe=True,
)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class WebBrowserToolProvider(ToolProvider):
    """Native tool provider for web browsing and content extraction.

    Exposes two tools to the AI brain:
    - web_browse: fetch a URL and return markdown content.
    - web_extract: fetch a URL and extract content for a CSS selector.

    All calls are stateless. SSRF protection prevents access to private networks.
    Uses only Python stdlib — no browser, no external dependencies.
    """

    def get_tools(
        self,
        instance: str = "local",
        features: dict | None = None,
        ctx: "ToolExecutionContext | None" = None,
    ) -> list[ToolDefinition]:
        return [_WEB_BROWSE, _WEB_EXTRACT]

    def execute(self, name: str, args: dict, ctx: ToolExecutionContext) -> ToolResult:
        dispatch = {
            "web_browse": self._web_browse,
            "web_extract": self._web_extract,
        }
        handler = dispatch.get(name)
        if handler is None:
            return ToolResult(success=False, output="", error=f"Unknown tool: {name}")
        return handler(args, ctx)

    def _web_browse(self, args: dict, _ctx: ToolExecutionContext) -> ToolResult:
        url = (args.get("url") or "").strip()
        error = validate_url(url)
        if error:
            return ToolResult(success=False, output="", error=error)
        return self._run_and_format(
            url=url, css_selector=None, args=args, default_include_links=True
        )

    def _web_extract(self, args: dict, _ctx: ToolExecutionContext) -> ToolResult:
        url = (args.get("url") or "").strip()
        error = validate_url(url)
        if error:
            return ToolResult(success=False, output="", error=error)
        css_selector = (args.get("css_selector") or "").strip()
        if not css_selector:
            return ToolResult(
                success=False, output="", error="css_selector is required."
            )
        return self._run_and_format(
            url=url,
            css_selector=css_selector,
            args=args,
            default_include_links=False,
            include_snippet=True,
        )

    @staticmethod
    def _run_and_format(
        url: str,
        css_selector: str | None,
        args: dict,
        default_include_links: bool,
        include_snippet: bool = False,
    ) -> ToolResult:
        result = _run_fetch(
            url=url,
            css_selector=css_selector,
            include_links=bool(args.get("include_links", default_include_links)),
            include_images=bool(args.get("include_images", True)),
            max_chars=int(args.get("max_chars", _MAX_CONTENT_LENGTH)),
            timeout=int(args.get("timeout", _FETCH_TIMEOUT_SECONDS)),
            headers=args.get("headers") or None,
            user_agent=(args.get("user_agent") or "").strip() or None,
            fmt=args.get("format", "markdown"),
        )
        if not result.get("success"):
            return ToolResult(
                success=False,
                output="",
                error=result.get("error", "Failed to fetch the page."),
            )
        payload = {
            "url": result["url"],
            "title": result["title"],
            "content": result["content"],
            "links": result["links"],
            "truncated": result["truncated"],
            "length": result["length"],
            "original_length": result["original_length"],
        }
        for key in ("description", "author", "published", "site_name"):
            if key in result:
                payload[key] = result[key]
        if include_snippet:
            payload["snippet"] = _make_snippet(result["content"])
        return ToolResult(success=True, output=json.dumps(payload), error=None)
