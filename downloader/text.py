from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse


def sanitize_url(raw_url: str, base_url: str = "") -> str | None:
    candidate = html.unescape(raw_url or "").strip()
    if not candidate:
        return None
    if base_url:
        candidate = urljoin(base_url, candidate)
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = quote(unquote(parsed.path), safe="/%:@-._~!$&'()*+,;=")
    query = quote(unquote(parsed.query), safe="=&/%:@-._~!$'()*+,;?")
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, query, ""))


def strip_tags(content_html: str) -> str:
    content_html = re.sub(r"<br\s*/?>", "\n", content_html, flags=re.IGNORECASE)
    content_html = re.sub(r"</p\s*>", "\n\n", content_html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", content_html)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_chapter_title(raw_title: str) -> str:
    title = html.unescape((raw_title or "").strip())
    if not title:
        return "未知章节"
    title = re.sub(r"[\s_\-]*(?:愛麗絲書屋|ALICESW\.COM).*$", "", title, flags=re.IGNORECASE)
    if "_" in title:
        left, right = title.split("_", 1)
        if re.search(r"第\s*\d+\s*章", left) and len(right) > 3:
            title = left
    title = re.sub(r"\s+", " ", title).strip(" _-")
    return title or "未知章节"


def extract_title(page_html: str) -> str:
    for pattern in (r"<h1[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>"):
        match = re.search(pattern, page_html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return strip_tags(match.group(1))
    return "未知标题"


def safe_filename(name: str, suffix: str = ".epub") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    base = cleaned or "novel"
    if not base.lower().endswith(suffix.lower()):
        base = f"{base}{suffix}"
    return Path(base).name
