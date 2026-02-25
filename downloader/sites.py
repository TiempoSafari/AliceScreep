from __future__ import annotations

import re
import sys
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlparse

from .http import fetch_bytes, fetch_html_with_retry
from .models import Chapter, NovelMeta, SOURCE_ALICESW, SOURCE_GENERIC, SOURCE_SILVERNOELLE
from .text import extract_title, sanitize_url, strip_tags


def detect_source(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "alicesw" in host:
        return SOURCE_ALICESW
    if "silvernoelle.com" in host:
        return SOURCE_SILVERNOELLE
    return SOURCE_GENERIC


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._href = dict(attrs).get("href")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, "".join(self._text_parts).strip()))
            self._href = None
            self._text_parts = []


class SiteAdapter(ABC):
    source = SOURCE_GENERIC

    @abstractmethod
    def build_chapter_index_url(self, input_url: str) -> str | None: ...

    def build_novel_url(self, input_url: str) -> str | None:
        return None

    @abstractmethod
    def discover_chapters(self, index_url: str, html_text: str, logger: Callable[[str], None]) -> list[Chapter]: ...

    @abstractmethod
    def extract_meta(self, index_html: str, fallback_title: str = "未命名小说") -> NovelMeta: ...

    @abstractmethod
    def extract_content(self, chapter_html: str) -> str: ...


class GenericSiteAdapter(SiteAdapter):
    source = SOURCE_GENERIC

    def build_chapter_index_url(self, input_url: str) -> str | None:
        parsed = urlparse(input_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return input_url

    def _extract_chapter_order(self, title: str, url: str) -> int:
        title_match = re.search(r"第\s*(\d+)\s*章", title)
        if title_match:
            return int(title_match.group(1))
        url_match = re.search(r"(\d+)(?=\.html(?:$|\?))", url)
        if url_match:
            return int(url_match.group(1))
        return sys.maxsize

    def _pick_chapter_list_html(self, page_html: str) -> str:
        m = re.search(r'<ul[^>]+class=["\'][^"\']*mulu_list[^"\']*["\'][^>]*>(.*?)</ul>', page_html, re.I | re.S)
        return m.group(1) if m else page_html

    def discover_chapters(self, index_url: str, html_text: str, logger: Callable[[str], None]) -> list[Chapter]:
        parser = AnchorParser()
        parser.feed(self._pick_chapter_list_html(html_text))
        parsed_index = urlparse(index_url)
        chapters: list[Chapter] = []
        seen: set[str] = set()
        skipped_non_html = skipped_cross_site = skipped_non_book = 0

        for href, text in parser.links:
            normalized = sanitize_url(href, base_url=index_url)
            if not normalized:
                skipped_non_html += 1
                continue
            absolute_url = normalized.split("#", 1)[0]
            parsed_abs = urlparse(absolute_url)
            if parsed_abs.netloc and parsed_abs.netloc != parsed_index.netloc:
                skipped_cross_site += 1
                continue
            if not parsed_abs.path.lower().endswith(".html"):
                skipped_non_html += 1
                continue
            if "/book/" not in parsed_abs.path:
                skipped_non_book += 1
                continue
            if absolute_url in seen:
                continue
            seen.add(absolute_url)
            title = text.strip() or parsed_abs.path.rsplit("/", 1)[-1].replace(".html", "")
            chapters.append(Chapter(title=title, url=absolute_url, order=self._extract_chapter_order(title, absolute_url)))

        chapters.sort(key=lambda c: (c.order, c.url))
        logger(f"章节解析完成：候选链接 {len(parser.links)}，有效章节 {len(chapters)}，过滤(跨站={skipped_cross_site}, 非html={skipped_non_html}, 非/book/={skipped_non_book})")
        return chapters

    def extract_meta(self, index_html: str, fallback_title: str = "未命名小说") -> NovelMeta:
        title = fallback_title
        author = "未知作者"
        title_match = re.search(r'<div[^>]+class=["\'][^"\']*mu_h1[^"\']*["\'][^>]*>\s*<h1[^>]*>(.*?)</h1>', index_html, re.I | re.S)
        if title_match:
            title = strip_tags(title_match.group(1))
        else:
            title = extract_title(index_html)
        author_match = re.search(r"作者：\s*<a[^>]*>(.*?)</a>", index_html, re.I | re.S)
        if author_match:
            author = strip_tags(author_match.group(1))
        return NovelMeta(title=title or fallback_title, author=author or "未知作者")

    def extract_content(self, chapter_html: str) -> str:
        for pattern in (
            r'<div[^>]+id=["\']content["\'][^>]*>(.*?)</div>',
            r'<div[^>]+class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>',
            r'<article[^>]*>(.*?)</article>',
        ):
            m = re.search(pattern, chapter_html, re.I | re.S)
            if m:
                text = strip_tags(m.group(1))
                if len(text) > 60:
                    return text
        body = re.search(r"<body[^>]*>(.*?)</body>", chapter_html, re.I | re.S)
        return strip_tags(body.group(1)) if body else ""


class AliceSWSiteAdapter(GenericSiteAdapter):
    source = SOURCE_ALICESW

    def _extract_novel_id(self, url: str) -> str:
        path = urlparse(url).path
        for pattern in (r"/novel/(\d+)\.html", r"/other/chapters/id/(\d+)\.html"):
            m = re.search(pattern, path)
            if m:
                return m.group(1)
        return ""

    def build_chapter_index_url(self, input_url: str) -> str | None:
        parsed = urlparse(input_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        novel_id = self._extract_novel_id(input_url)
        if not novel_id:
            return None
        return f"{parsed.scheme}://{parsed.netloc}/other/chapters/id/{novel_id}.html"

    def build_novel_url(self, input_url: str) -> str | None:
        parsed = urlparse(input_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        novel_id = self._extract_novel_id(input_url)
        if not novel_id:
            return None
        return f"{parsed.scheme}://{parsed.netloc}/novel/{novel_id}.html"


class SilverNoelleSiteAdapter(GenericSiteAdapter):
    source = SOURCE_SILVERNOELLE

    def _find_older_posts_url(self, page_html: str, base_url: str) -> str | None:
        for pattern in (
            r'<a[^>]+class=["\'][^"\']*(?:nav-previous|nextpostslink|older-posts)[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(?:\s*较旧文章\s*|\s*Older Posts\s*)</a>',
            r'<a[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']',
        ):
            m = re.search(pattern, page_html, re.I | re.S)
            if m:
                normalized = sanitize_url(m.group(1), base_url=base_url)
                if normalized:
                    return normalized
        return None

    def _collect_pages(self, index_url: str, first_html: str, logger: Callable[[str], None], max_pages: int = 80) -> list[tuple[str, str]]:
        pages = [(index_url, first_html)]
        visited = {index_url}
        next_url = self._find_older_posts_url(first_html, base_url=index_url)
        while next_url and len(pages) < max_pages:
            if next_url in visited:
                break
            visited.add(next_url)
            try:
                html_text = fetch_html_with_retry(next_url, logger=logger, retries=2, wait_seconds=1.0)
            except Exception as exc:
                logger(f"❌ [警告] 拉取分页失败，后续页面将跳过: {next_url} | 错误: {exc}")
                break
            pages.append((next_url, html_text))
            logger(f"✅ 已拉取 SilverNoelle 目录分页: {len(pages)} -> {next_url}")
            next_url = self._find_older_posts_url(html_text, base_url=next_url)
        if len(pages) >= max_pages and next_url:
            logger(f"❌ [警告] 目录分页达到上限 {max_pages} 页，可能仍有章节未抓取。")
        return pages

    def discover_chapters(self, index_url: str, html_text: str, logger: Callable[[str], None]) -> list[Chapter]:
        pages = self._collect_pages(index_url, html_text, logger=logger)
        chapters: list[Chapter] = []
        seen: set[str] = set()
        for page_url, page_html in pages:
            for article_html in re.findall(r"<article\b[^>]*>(.*?)</article>", page_html, re.I | re.S):
                m = re.search(r'<h[1-4][^>]+class=["\'][^"\']*entry-title[^"\']*["\'][^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', article_html, re.I | re.S)
                if not m:
                    m = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*(?:rel=["\'][^"\']*bookmark[^"\']*["\'])?[^>]*>(.*?)</a>', article_html, re.I | re.S)
                if not m:
                    continue
                chapter_url = sanitize_url(m.group(1), base_url=page_url)
                title = strip_tags(m.group(2))
                if not chapter_url or not title or chapter_url in seen:
                    continue
                seen.add(chapter_url)
                chapters.append(Chapter(title=title, url=chapter_url))
        chapters.reverse()
        logger(f"章节解析完成：候选文章 {len(chapters)}，有效章节 {len(chapters)}")
        return chapters

    def extract_meta(self, index_html: str, fallback_title: str = "未命名小说") -> NovelMeta:
        title_match = re.search(r'<h1[^>]+class=["\'][^"\']*archive-title[^"\']*["\'][^>]*>(.*?)</h1>', index_html, re.I | re.S)
        title = strip_tags(title_match.group(1)) if title_match else extract_title(index_html)
        title = re.sub(r"^分类：", "", title).strip() or fallback_title
        return NovelMeta(title=title, author="Silvernoelle")

    def extract_content(self, chapter_html: str) -> str:
        m = re.search(r'<div[^>]+class=["\'][^"\']*entry-content[^"\']*["\'][^>]*>(.*?)</div>', chapter_html, re.I | re.S)
        if m:
            entry_html = re.sub(
                r'<div[^>]+class=["\'][^"\']*(?:sharedaddy|sd-sharing|shared-post|jp-sharing-input-touch)[^"\']*["\'][^>]*>.*?</div>',
                "",
                m.group(1),
                flags=re.I | re.S,
            )
            text = strip_tags(entry_html)
            text = re.sub(r"共享此文章：[\s\S]*$", "", text).strip()
            if text:
                return text

        # 某些页面正文结构不规则时，回退到通用正文提取，并继续清理分享文案。
        fallback = super().extract_content(chapter_html)
        fallback = re.sub(r"共享此文章：[\s\S]*$", "", fallback).strip()
        return fallback

def get_site_adapter(input_url: str) -> SiteAdapter:
    source = detect_source(input_url)
    if source == SOURCE_ALICESW:
        return AliceSWSiteAdapter()
    if source == SOURCE_SILVERNOELLE:
        return SilverNoelleSiteAdapter()
    return GenericSiteAdapter()


def extract_cover_url(page_html: str, base_url: str) -> str | None:
    for pattern in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<img[^>]+class=["\'][^"\']*(?:book|cover|pic)[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>',
    ):
        m = re.search(pattern, page_html, re.I | re.S)
        if m:
            normalized = sanitize_url(m.group(1), base_url=base_url)
            if normalized:
                return normalized
    return None


def fetch_cover_bytes(cover_url: str) -> tuple[bytes, str, str]:
    data = fetch_bytes(cover_url)
    if cover_url.lower().endswith('.png'):
        return data, 'image/png', 'cover.png'
    return data, 'image/jpeg', 'cover.jpg'
