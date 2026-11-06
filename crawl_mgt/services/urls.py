"""URL normalisation and crawl scope."""
from __future__ import annotations

import posixpath
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_ga"}
NON_HTML_EXT = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js", ".json", ".xml",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".mp3", ".mp4", ".avi", ".mov", ".woff", ".woff2", ".ttf",
    ".eot", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".exe", ".dmg",
}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def normalize_url(url: str, include_query: bool = True) -> str:
    p = urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    port = p.port
    if (scheme, port) in (("http", 80), ("https", 443)):
        port = None
    netloc = host if ":" not in host else f"[{host}]"
    if port:
        netloc += f":{port}"
    path = posixpath.normpath(p.path) if p.path else "/"   # also drops a trailing slash
    if not path.startswith("/"):
        path = "/" + path
    if path.startswith("//"):
        path = "/" + path.lstrip("/")
    query = ""
    if include_query and p.query:
        pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
        query = urlencode(sorted(pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def looks_non_html(url: str) -> bool:
    return posixpath.splitext(urlsplit(url).path.lower())[1] in NON_HTML_EXT


# A crawler must never GET a URL that may delete something (e.g. <a href="/items/5/delete">).
DESTRUCTIVE_URL = re.compile(r"/(delete|destroy|remove|erase|purge|logout|signout|sign-out)(\b|/|_|-|\.|$)", re.I)


def looks_destructive(url: str) -> bool:
    return bool(DESTRUCTIVE_URL.search(urlsplit(url).path))


class CrawlScope:
    def __init__(self, start_url: str, allow_subdomains: bool, path_prefix: str | None, exclude_patterns: list[str]):
        s = urlsplit(start_url)
        self.host = (s.hostname or "").lower()
        self.port = s.port
        self.allow_subdomains = allow_subdomains
        self.path_prefix = path_prefix
        self.exclude = [re.compile(p) for p in exclude_patterns]

    def allows(self, normalized_url: str) -> bool:
        p = urlsplit(normalized_url)
        if p.scheme not in ("http", "https"):
            return False
        host = (p.hostname or "").lower()
        same = host == self.host or (self.allow_subdomains and host.endswith("." + self.host))
        if not same:
            return False
        if self.host in LOOPBACK and p.port != self.port:  # different local app on another port
            return False
        if self.path_prefix and not p.path.startswith(self.path_prefix):
            return False
        return not any(rx.search(normalized_url) for rx in self.exclude)
