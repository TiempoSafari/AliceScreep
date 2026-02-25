#!/usr/bin/env python3
"""下载 alicesw 小说目录并导出为 EPUB（含 GUI）。"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from threading import Thread
from typing import Callable, Iterable, List, Optional
from urllib.error import URLError
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

try:
    from opencc import OpenCC
except Exception:  # optional dependency
    OpenCC = None


class OpenCCConverter:
    def __init__(self) -> None:
        self._converter = None
        if OpenCC is not None:
            try:
                self._converter = OpenCC("t2s")
            except Exception:
                self._converter = None

    @property
    def available(self) -> bool:
        return self._converter is not None

    def convert(self, text: str) -> str:
        if not self._converter:
            return text
        return self._converter.convert(text)


OPENCC = OpenCCConverter()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

SOURCE_ALICESW = "alicesw"
SOURCE_SILVERNOELLE = "silvernoelle"
SOURCE_GENERIC = "generic"


@dataclass
class Chapter:
    title: str
    url: str
    order: int = sys.maxsize


@dataclass
class ChapterContent:
    title: str
    content: str
    source_url: str


@dataclass
class NovelMeta:
    title: str
    author: str
    language: str = "zh-Hant"


@dataclass
class DownloadPayload:
    meta: NovelMeta
    chapters: list[ChapterContent]
    cover_bytes: bytes | None
    cover_type: str | None
    cover_name: str | None


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_map = dict(attrs)
        self._href = attr_map.get("href")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        text = "".join(self._text_parts).strip()
        if self._href:
            self.links.append((self._href, text))
        self._href = None
        self._text_parts = []


def detect_source(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "alicesw" in host:
        return SOURCE_ALICESW
    if "silvernoelle.com" in host:
        return SOURCE_SILVERNOELLE
    return SOURCE_GENERIC


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_html(url: str, timeout: int = 30) -> str:
    raw = fetch_bytes(url, timeout=timeout)
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")




def fetch_html_with_retry(
    url: str,
    logger: Callable[[str], None] | None = None,
    retries: int = 2,
    wait_seconds: float = 0.8,
) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            return fetch_html(url)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            last_exc = exc
            if attempt > retries:
                break
            if logger:
                logger(f"❌ [警告] 请求失败，准备重试({attempt}/{retries}): {url} | 错误: {exc}")
            time.sleep(wait_seconds)

    assert last_exc is not None
    raise last_exc
def sanitize_url(raw_url: str, base_url: str = "") -> Optional[str]:
    """清理并规范化 URL，避免被当作本地文件路径打开。"""
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

    # 站点常见尾巴："_...-愛麗絲書屋 (ALICESW.COM) - ..."
    title = re.sub(r"[\s_\-]*(?:愛麗絲書屋|ALICESW\.COM).*$", "", title, flags=re.IGNORECASE)

    # 常见模式："章节名_副标题"，优先保留章节主标题
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


def extract_content(page_html: str, source: str = SOURCE_GENERIC) -> str:
    if source == SOURCE_SILVERNOELLE:
        match = re.search(
            r'<div[^>]+class=["\'][^"\']*entry-content[^"\']*["\'][^>]*>(.*?)</div>',
            page_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            text = strip_tags(match.group(1))
            if len(text) > 60:
                return text

    patterns = (
        r'<div[^>]+id=["\']content["\'][^>]*>(.*?)</div>',
        r'<div[^>]+class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
    )
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            text = strip_tags(match.group(1))
            if len(text) > 60:
                return text

    body = re.search(r"<body[^>]*>(.*?)</body>", page_html, flags=re.IGNORECASE | re.DOTALL)
    if not body:
        return ""
    return strip_tags(body.group(1))


def extract_chapter_order(title: str, url: str) -> int:
    title_match = re.search(r"第\s*(\d+)\s*章", title)
    if title_match:
        return int(title_match.group(1))

    url_match = re.search(r"(\d+)(?=\.html(?:$|\?))", url)
    if url_match:
        return int(url_match.group(1))
    return sys.maxsize


def extract_novel_id(url: str) -> str:
    path = urlparse(url).path
    patterns = (r"/novel/(\d+)\.html", r"/other/chapters/id/(\d+)\.html")
    for pattern in patterns:
        match = re.search(pattern, path)
        if match:
            return match.group(1)
    return ""


def build_chapter_index_url(input_url: str, source: str | None = None) -> Optional[str]:
    parsed = urlparse(input_url)
    if not parsed.scheme or not parsed.netloc:
        return None

    source = source or detect_source(input_url)
    if source != SOURCE_ALICESW:
        return input_url

    novel_id = extract_novel_id(input_url)
    if not novel_id:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/other/chapters/id/{novel_id}.html"


def build_novel_url(input_url: str, source: str | None = None) -> Optional[str]:
    parsed = urlparse(input_url)
    if not parsed.scheme or not parsed.netloc:
        return None

    source = source or detect_source(input_url)
    if source != SOURCE_ALICESW:
        return None

    novel_id = extract_novel_id(input_url)
    if not novel_id:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/novel/{novel_id}.html"


def pick_chapter_list_html(page_html: str) -> str:
    match = re.search(
        r'<ul[^>]+class=["\'][^"\']*mulu_list[^"\']*["\'][^>]*>(.*?)</ul>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1)
    return page_html


def discover_silvernoelle_chapters(index_url: str, html_text: str) -> list[Chapter]:
    """解析 silvernoelle 的分类目录页（WordPress）。"""
    articles = re.findall(r"<article\b[^>]*>(.*?)</article>", html_text, flags=re.IGNORECASE | re.DOTALL)
    chapters: list[Chapter] = []
    seen: set[str] = set()

    for article_html in articles:
        m = re.search(
            r'<h[1-4][^>]+class=["\'][^"\']*entry-title[^"\']*["\'][^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            article_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not m:
            m = re.search(
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]*(?:rel=["\'][^"\']*bookmark[^"\']*["\'])?[^>]*>(.*?)</a>',
                article_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
        if not m:
            continue
        chapter_url = sanitize_url(m.group(1), base_url=index_url)
        if not chapter_url or chapter_url in seen:
            continue

        title = strip_tags(m.group(2))
        if not title:
            continue

        seen.add(chapter_url)
        chapters.append(Chapter(title=title, url=chapter_url))

    chapters.reverse()
    return chapters


def discover_chapters(
    index_url: str,
    html_text: str,
    source: str = SOURCE_GENERIC,
    logger: Callable[[str], None] = print,
) -> List[Chapter]:
    if source == SOURCE_SILVERNOELLE:
        chapters = discover_silvernoelle_chapters(index_url, html_text)
        logger(f"章节解析完成：候选文章 {len(chapters)}，有效章节 {len(chapters)}")
        return chapters

    chapter_area = pick_chapter_list_html(html_text)
    parser = AnchorParser()
    parser.feed(chapter_area)

    parsed_index = urlparse(index_url)
    links_found = len(parser.links)
    chapters: list[Chapter] = []
    seen: set[str] = set()
    skipped_non_html = 0
    skipped_cross_site = 0
    skipped_non_book = 0

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
        order = extract_chapter_order(title, absolute_url)
        chapters.append(Chapter(title=title, url=absolute_url, order=order))

    chapters.sort(key=lambda c: (c.order, c.url))
    logger(
        "章节解析完成："
        f"候选链接 {links_found}，"
        f"有效章节 {len(chapters)}，"
        f"过滤(跨站={skipped_cross_site}, 非html={skipped_non_html}, 非/book/={skipped_non_book})"
    )
    return chapters


def extract_meta(index_html: str, fallback_title: str = "未命名小说", source: str = SOURCE_GENERIC) -> NovelMeta:
    if source == SOURCE_SILVERNOELLE:
        title_match = re.search(r'<h1[^>]+class=["\'][^"\']*archive-title[^"\']*["\'][^>]*>(.*?)</h1>', index_html, re.I | re.S)
        title = strip_tags(title_match.group(1)) if title_match else extract_title(index_html)
        title = re.sub(r"^分类：", "", title).strip() or fallback_title
        return NovelMeta(title=title, author="Silvernoelle")

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


def extract_cover_url(page_html: str, base_url: str) -> Optional[str]:
    patterns = (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<img[^>]+class=["\'][^"\']*(?:book|cover|pic)[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>',
    )
    for pattern in patterns:
        m = re.search(pattern, page_html, re.I | re.S)
        if not m:
            continue
        normalized = sanitize_url(m.group(1), base_url=base_url)
        if normalized:
            return normalized
    return None


def fetch_cover_bytes(cover_url: str) -> tuple[bytes, str, str]:
    data = fetch_bytes(cover_url)
    lower = cover_url.lower()
    if lower.endswith(".png"):
        return data, "image/png", "cover.png"
    return data, "image/jpeg", "cover.jpg"


def to_xhtml_paragraphs(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    paragraphs = [line for line in lines if line]
    if not paragraphs:
        return "<p></p>"
    return "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


def build_epub(
    output_file: Path,
    meta: NovelMeta,
    chapters: list[ChapterContent],
    cover_bytes: bytes | None,
    cover_media_type: str | None,
    cover_name: str | None,
) -> None:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    manifest_items: list[str] = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine_items: list[str] = []
    nav_points: list[str] = []
    nav_links: list[str] = []

    if cover_bytes and cover_media_type and cover_name:
        manifest_items.append(
            f'<item id="cover-image" href="images/{cover_name}" media-type="{cover_media_type}" properties="cover-image"/>'
        )
        manifest_items.append('<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine_items.append('<itemref idref="cover-page"/>')

    for idx, _chapter in enumerate(chapters, start=1):
        manifest_items.append(f'<item id="chap{idx}" href="text/chapter{idx}.xhtml" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="chap{idx}"/>')
        nav_points.append(
            f'''<navPoint id="navPoint-{idx}" playOrder="{idx}">
      <navLabel><text>{html.escape(chapters[idx-1].title)}</text></navLabel>
      <content src="text/chapter{idx}.xhtml"/>
    </navPoint>'''
        )
        nav_links.append(f'<li><a href="text/chapter{idx}.xhtml">{html.escape(chapters[idx-1].title)}</a></li>')

    opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{html.escape(meta.title)}</dc:title>
    <dc:creator>{html.escape(meta.author)}</dc:creator>
    <dc:language>{meta.language}</dc:language>
    <dc:date>{now_iso}</dc:date>
    <meta property="dcterms:modified">{now_iso}</meta>
  </metadata>
  <manifest>
    {''.join(manifest_items)}
  </manifest>
  <spine toc="ncx">
    {''.join(spine_items)}
  </spine>
</package>
'''

    toc_ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{html.escape(meta.title)}</text></docTitle>
  <navMap>
    {''.join(nav_points)}
  </navMap>
</ncx>
'''

    nav_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-Hant">
<head><title>目录</title></head>
<body>
  <nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
    <h1>{html.escape(meta.title)}</h1>
    <ol>
      {''.join(nav_links)}
    </ol>
  </nav>
</body>
</html>
'''

    with zipfile.ZipFile(output_file, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">
  <rootfiles>
    <rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\"/>
  </rootfiles>
</container>
""",
        )

        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/toc.ncx", toc_ncx)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)

        if cover_bytes and cover_media_type and cover_name:
            cover_page = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>封面</title></head>
<body>
  <div style="text-align:center; margin:0; padding:0;">
    <img src="images/{cover_name}" alt="cover" style="max-width:100%; height:auto;"/>
  </div>
</body>
</html>
'''
            zf.writestr("OEBPS/cover.xhtml", cover_page)
            zf.writestr(f"OEBPS/images/{cover_name}", cover_bytes)

        for idx, chapter in enumerate(chapters, start=1):
            chapter_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-Hant">
<head><title>{html.escape(chapter.title)}</title></head>
<body>
  <h1>{html.escape(chapter.title)}</h1>
  {to_xhtml_paragraphs(chapter.content)}
</body>
</html>
'''
            zf.writestr(f"OEBPS/text/chapter{idx}.xhtml", chapter_xhtml)


def maybe_convert_to_simplified(text: str, enabled: bool) -> str:
    if not enabled:
        return text
    return OPENCC.convert(text)


def download_chapters(
    chapters: Iterable[Chapter],
    delay: float = 0.2,
    logger: Callable[[str], None] = print,
    to_simplified: bool = True,
    source: str = SOURCE_GENERIC,
) -> list[ChapterContent]:
    results: list[ChapterContent] = []

    for idx, chapter in enumerate(chapters, start=1):
        chapter_url = sanitize_url(chapter.url)
        if not chapter_url:
            logger(f"❌ [警告] 跳过非法章节链接: {chapter.url}")
            continue

        logger(f"[{idx}] 下载中: {chapter.title} -> {chapter_url}")
        try:
            chapter_html = fetch_html_with_retry(chapter_url, logger=logger, retries=2, wait_seconds=1.0)
        except (URLError, ValueError, OSError) as exc:
            logger(f"❌ [警告] 章节下载失败，已跳过: {chapter_url} | 错误: {exc}")
            continue

        page_title = extract_title(chapter_html)
        title = normalize_chapter_title(chapter.title or page_title)
        if title == "未知章节":
            title = normalize_chapter_title(page_title)

        content = extract_content(chapter_html, source=source)
        if not content:
            logger(f"❌ [警告] 正文提取失败，已跳过: {chapter_url}")
            continue

        title = maybe_convert_to_simplified(title, to_simplified)
        content = maybe_convert_to_simplified(content, to_simplified)

        results.append(ChapterContent(title=title, content=content, source_url=chapter_url))
        logger(f"✅ 下载成功: {title}")
        time.sleep(delay)

    return results


def download_novel_payload(
    input_url: str,
    start: int,
    end: int,
    delay: float,
    logger: Callable[[str], None] = print,
    to_simplified: bool = True,
) -> DownloadPayload | None:
    logger(f"输入链接: {input_url}")
    source = detect_source(input_url)

    chapter_index_url = build_chapter_index_url(input_url, source=source)
    if chapter_index_url:
        logger(f"使用章节目录页: {chapter_index_url}")
    else:
        logger("❌ [警告] 无法自动识别小说ID，将直接使用输入链接作为目录页。")
        chapter_index_url = input_url

    try:
        index_html = fetch_html_with_retry(chapter_index_url, logger=logger, retries=2, wait_seconds=1.0)
    except URLError as exc:
        logger(f"❌ 目录页请求失败: {exc}")
        return None

    meta = extract_meta(index_html, source=source)
    logger(f"小说信息: 标题={meta.title} | 作者={meta.author}")
    if to_simplified:
        if OPENCC.available:
            logger("✅ 已启用繁体转简体（OpenCC t2s）")
        else:
            logger("❌ [警告] 未安装 opencc，暂无法自动繁转简（可 `pip install opencc-python-reimplemented`）")

    chapters = discover_chapters(chapter_index_url, index_html, source=source, logger=logger)
    if not chapters:
        logger("❌ 未发现章节链接：请确认链接是否为小说详情页/章节目录页，或网站结构已变化。")
        return None

    safe_start = max(start, 1)
    safe_end = end if end > 0 else len(chapters)
    selected = chapters[safe_start - 1 : safe_end]

    if not selected:
        logger("❌ 筛选后没有章节，请检查起始章节/结束章节。")
        return None

    logger(f"准备下载：总章节 {len(chapters)}，本次下载 {len(selected)}（范围 {safe_start}-{safe_end}）")
    downloaded = download_chapters(
        selected,
        delay=max(delay, 0),
        logger=logger,
        to_simplified=to_simplified,
        source=source,
    )
    if not downloaded:
        logger("❌ 没有成功下载任何章节，未生成 EPUB。")
        return None

    cover_bytes = None
    cover_type = None
    cover_name = None
    novel_url = build_novel_url(input_url, source=source)
    if novel_url:
        try:
            novel_html = fetch_html_with_retry(novel_url, logger=logger, retries=1, wait_seconds=1.0)
            cover_url = extract_cover_url(novel_html, base_url=novel_url)
            if cover_url:
                cover_bytes, cover_type, cover_name = fetch_cover_bytes(cover_url)
                logger(f"✅ 已获取封面图: {cover_url}")
            else:
                logger("❌ [警告] 未找到封面图，将生成无封面 EPUB。")
        except Exception as exc:
            logger(f"❌ [警告] 获取封面失败，将生成无封面 EPUB: {exc}")

    if to_simplified:
        meta.title = maybe_convert_to_simplified(meta.title, True)
        meta.author = maybe_convert_to_simplified(meta.author, True)

    return DownloadPayload(
        meta=meta,
        chapters=downloaded,
        cover_bytes=cover_bytes,
        cover_type=cover_type,
        cover_name=cover_name,
    )


def save_payload_to_epub(
    payload: DownloadPayload,
    output_file: Path,
    logger: Callable[[str], None] = print,
) -> int:
    if output_file.suffix.lower() != ".epub":
        output_file = output_file.with_suffix(".epub")
        logger(f"输出格式已切换为 EPUB: {output_file}")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    build_epub(
        output_file,
        payload.meta,
        payload.chapters,
        payload.cover_bytes,
        payload.cover_type,
        payload.cover_name,
    )
    logger(f"✅ 完成：共写入 {len(payload.chapters)} 章 -> {output_file}")
    return 0


def run_download(
    input_url: str,
    output_file: Path,
    start: int,
    end: int,
    delay: float,
    logger: Callable[[str], None] = print,
    to_simplified: bool = True,
) -> int:
    payload = download_novel_payload(input_url, start, end, delay, logger=logger, to_simplified=to_simplified)
    if payload is None:
        return 1
    return save_payload_to_epub(payload, output_file, logger=logger)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 AliceSW/SilverNoelle 小说并导出成 EPUB")
    parser.add_argument("index_url", nargs="?", help="小说链接，例如 https://www.alicesw.tw/novel/2735.html 或 https://silvernoelle.com/category/.../")
    parser.add_argument("-o", "--output", default="novel.epub", help="输出 EPUB 文件路径")
    parser.add_argument("--delay", type=float, default=0.2, help="每章下载间隔秒数，默认 0.2")
    parser.add_argument("--start", type=int, default=1, help="起始章节（从1开始）")
    parser.add_argument("--end", type=int, default=0, help="结束章节（0 表示到最后）")
    parser.add_argument("--no-simplified", action="store_true", help="关闭繁体转简体（默认开启）")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    return parser.parse_args()


def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except Exception as exc:
        print(f"GUI 启动失败：{exc}")
        return 1

    try:
        root = tk.Tk()
    except Exception as exc:
        print(f"GUI 启动失败：{exc}")
        return 1

    root.title("AliceSW 小说下载器")
    root.geometry("920x700")
    root.configure(bg="#f4f6fb")

    style = ttk.Style(root)
    for theme in ("vista", "xpnative", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break

    style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"), foreground="#1f2a44")
    style.configure("Sub.TLabel", font=("Segoe UI", 10), foreground="#5a6579")
    style.configure("TButton", font=("Segoe UI", 10))
    style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    container = ttk.Frame(root, padding=16)
    container.pack(fill="both", expand=True)

    header = ttk.Frame(container)
    header.pack(fill="x", pady=(0, 8))
    ttk.Label(header, text="AliceSW EPUB 下载器", style="Title.TLabel").pack(anchor="w")
    ttk.Label(header, text="下载 → 编辑章节名/封面 → 保存 EPUB", style="Sub.TLabel").pack(anchor="w")

    card = ttk.Frame(container, padding=12)
    card.pack(fill="x", pady=(6, 8))

    ttk.Label(card, text="小说链接").grid(row=0, column=0, sticky="w")
    url_var = tk.StringVar(value="https://www.alicesw.tw/novel/2735.html")
    ttk.Entry(card, textvariable=url_var, width=88).grid(row=0, column=1, columnspan=4, sticky="we", pady=4)

    ttk.Label(card, text="输出文件").grid(row=1, column=0, sticky="w")
    output_var = tk.StringVar(value=str(Path.cwd() / "novel.epub"))
    ttk.Entry(card, textvariable=output_var, width=68).grid(row=1, column=1, columnspan=3, sticky="we", pady=4)

    def choose_output() -> None:
        path = filedialog.asksaveasfilename(
            title="选择输出 EPUB 文件",
            defaultextension=".epub",
            filetypes=[("EPUB", "*.epub"), ("All files", "*.*")],
        )
        if path:
            output_var.set(path)

    ttk.Button(card, text="浏览", command=choose_output).grid(row=1, column=4, padx=(6, 0))

    ttk.Label(card, text="起始章节").grid(row=2, column=0, sticky="w")
    start_var = tk.StringVar(value="1")
    ttk.Entry(card, textvariable=start_var, width=10).grid(row=2, column=1, sticky="w", pady=4)

    ttk.Label(card, text="结束章节(0=最后)").grid(row=2, column=2, sticky="e")
    end_var = tk.StringVar(value="0")
    ttk.Entry(card, textvariable=end_var, width=10).grid(row=2, column=3, sticky="w", pady=4)

    ttk.Label(card, text="章节间隔(秒)").grid(row=2, column=4, sticky="e")
    delay_var = tk.StringVar(value="0.5")
    ttk.Entry(card, textvariable=delay_var, width=8).grid(row=2, column=5, sticky="w", padx=(6, 0), pady=4)

    simplified_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(card, text="保存前繁体转简体", variable=simplified_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

    card.columnconfigure(1, weight=1)
    card.columnconfigure(3, weight=1)

    progress = ttk.Progressbar(container, mode="indeterminate")
    progress.pack(fill="x", pady=(0, 8))

    log_box = ScrolledText(container, height=23, font=("Consolas", 10), bg="#0f172a", fg="#e2e8f0", insertbackground="#e2e8f0")
    log_box.pack(fill="both", expand=True, pady=(0, 10))
    log_box.tag_config("error", foreground="#ff6b6b")
    log_box.tag_config("success", foreground="#4ade80")
    log_box.tag_config("info", foreground="#cbd5e1")

    footer = ttk.Frame(container)
    footer.pack(fill="x")

    downloading = {"active": False}

    def log(msg: str) -> None:
        def _append() -> None:
            tag = "info"
            if "❌" in msg or "[警告]" in msg:
                tag = "error"
            elif "✅" in msg:
                tag = "success"
            log_box.insert("end", msg + "\n", tag)
            log_box.see("end")

        root.after(0, _append)

    def set_running(active: bool) -> None:
        downloading["active"] = active
        if active:
            progress.start(10)
        else:
            progress.stop()

    def open_editor(payload: DownloadPayload, output_path: str) -> bool:
        editor = tk.Toplevel(root)
        editor.title("编辑章节与封面")
        editor.geometry("860x620")
        editor.transient(root)
        editor.grab_set()

        editor_frame = ttk.Frame(editor, padding=12)
        editor_frame.pack(fill="both", expand=True)

        ttk.Label(editor_frame, text="书名").grid(row=0, column=0, sticky="w")
        title_var = tk.StringVar(value=payload.meta.title)
        ttk.Entry(editor_frame, textvariable=title_var, width=72).grid(row=0, column=1, columnspan=3, sticky="we", pady=4)

        ttk.Label(editor_frame, text="作者").grid(row=1, column=0, sticky="w")
        author_var = tk.StringVar(value=payload.meta.author)
        ttk.Entry(editor_frame, textvariable=author_var, width=30).grid(row=1, column=1, sticky="w", pady=4)

        cover_var = tk.StringVar(value=payload.cover_name or "(当前无封面)")
        ttk.Label(editor_frame, text="封面").grid(row=1, column=2, sticky="e")
        ttk.Label(editor_frame, textvariable=cover_var).grid(row=1, column=3, sticky="w", padx=(6, 0))

        list_frame = ttk.Frame(editor_frame)
        list_frame.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=(8, 6))

        chapter_list = tk.Listbox(list_frame, height=20)
        chapter_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=chapter_list.yview)
        scrollbar.pack(side="right", fill="y")
        chapter_list.configure(yscrollcommand=scrollbar.set)

        for i, ch in enumerate(payload.chapters, start=1):
            chapter_list.insert("end", f"{i:03d}. {ch.title}")

        edit_frame = ttk.Frame(editor_frame)
        edit_frame.grid(row=3, column=0, columnspan=4, sticky="we")
        ttk.Label(edit_frame, text="章节名").pack(side="left")
        chapter_title_var = tk.StringVar()
        chapter_entry = ttk.Entry(edit_frame, textvariable=chapter_title_var)
        chapter_entry.pack(side="left", fill="x", expand=True, padx=8)

        def on_select(_event: object = None) -> None:
            sel = chapter_list.curselection()
            if not sel:
                return
            idx = sel[0]
            chapter_title_var.set(payload.chapters[idx].title)

        def apply_title() -> None:
            sel = chapter_list.curselection()
            if not sel:
                return
            idx = sel[0]
            new_title = normalize_chapter_title(chapter_title_var.get())
            payload.chapters[idx].title = new_title
            chapter_list.delete(idx)
            chapter_list.insert(idx, f"{idx+1:03d}. {new_title}")
            chapter_list.selection_set(idx)
            chapter_list.activate(idx)

        chapter_list.bind("<<ListboxSelect>>", on_select)
        ttk.Button(edit_frame, text="应用章节名", command=apply_title).pack(side="left")

        def replace_cover() -> None:
            path = filedialog.askopenfilename(
                title="选择封面图片",
                filetypes=[("Image", "*.jpg *.jpeg *.png"), ("All files", "*.*")],
            )
            if not path:
                return
            try:
                raw = Path(path).read_bytes()
            except Exception as exc:
                messagebox.showerror("错误", f"读取封面失败: {exc}", parent=editor)
                return
            suffix = Path(path).suffix.lower()
            if suffix == ".png":
                payload.cover_type = "image/png"
                payload.cover_name = "cover.png"
            else:
                payload.cover_type = "image/jpeg"
                payload.cover_name = "cover.jpg"
            payload.cover_bytes = raw
            cover_var.set(Path(path).name)

        action_frame = ttk.Frame(editor_frame)
        action_frame.grid(row=4, column=0, columnspan=4, sticky="we", pady=(8, 0))
        saved = {"ok": False}

        def save_and_close() -> None:
            payload.meta.title = title_var.get().strip() or payload.meta.title
            payload.meta.author = author_var.get().strip() or payload.meta.author
            if not payload.chapters:
                messagebox.showerror("错误", "没有可保存的章节。", parent=editor)
                return
            saved["ok"] = True
            editor.destroy()

        ttk.Button(action_frame, text="更换封面", command=replace_cover).pack(side="left")
        ttk.Button(action_frame, text="保存修改并导出", style="Accent.TButton", command=save_and_close).pack(side="right")
        ttk.Button(action_frame, text="取消", command=editor.destroy).pack(side="right", padx=(0, 8))

        editor_frame.columnconfigure(1, weight=1)
        editor_frame.columnconfigure(3, weight=1)
        editor_frame.rowconfigure(2, weight=1)

        editor.wait_window()
        return saved["ok"]

    def start_download() -> None:
        if downloading["active"]:
            messagebox.showinfo("提示", "下载正在进行中，请稍候。")
            return

        input_url = url_var.get().strip()
        output_path = output_var.get().strip()
        if not input_url or not output_path:
            messagebox.showerror("参数错误", "请填写小说链接与输出文件路径。")
            return

        try:
            start = int(start_var.get().strip())
            end = int(end_var.get().strip())
            delay = float(delay_var.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "起始/结束章节必须是整数，间隔必须是数字。")
            return

        set_running(True)
        log_box.delete("1.0", "end")
        log("开始下载 EPUB 数据...")
        log("提示：下载完成后会进入“编辑章节/封面”界面。")

        def worker() -> None:
            payload = download_novel_payload(
                input_url,
                start,
                end,
                delay,
                logger=log,
                to_simplified=bool(simplified_var.get()),
            )

            def done() -> None:
                set_running(False)
                if payload is None:
                    messagebox.showerror("失败", "下载失败，请检查日志。")
                    return

                ok = open_editor(payload, output_path)
                if not ok:
                    log("❌ 已取消保存。")
                    return
                save_payload_to_epub(payload, Path(output_path), logger=log)
                messagebox.showinfo("完成", "下载完成，已编辑并生成 EPUB。")

            root.after(0, done)

        Thread(target=worker, daemon=True).start()

    ttk.Button(footer, text="开始下载并编辑", style="Accent.TButton", command=start_download).pack(side="left")
    ttk.Button(footer, text="退出", command=root.destroy).pack(side="right")

    root.mainloop()
    return 0


def main() -> int:
    args = parse_args()
    if args.gui or not args.index_url:
        return launch_gui()
    return run_download(args.index_url, Path(args.output), args.start, args.end, args.delay, to_simplified=not args.no_simplified)


if __name__ == "__main__":
    raise SystemExit(main())
