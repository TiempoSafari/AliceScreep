#!/usr/bin/env python3
"""下载 alicesw 小说目录并导出为 txt。"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List
from urllib.parse import urljoin, urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class Chapter:
    title: str
    url: str


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


def fetch_html(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def strip_tags(content_html: str) -> str:
    content_html = re.sub(r"<br\\s*/?>", "\n", content_html, flags=re.IGNORECASE)
    content_html = re.sub(r"</p\\s*>", "\n\n", content_html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", content_html)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_title(page_html: str) -> str:
    for pattern in (
        r"<h1[^>]*>(.*?)</h1>",
        r"<title[^>]*>(.*?)</title>",
    ):
        m = re.search(pattern, page_html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return strip_tags(m.group(1))
    return "未知标题"


def extract_content(page_html: str) -> str:
    patterns = (
        r'<div[^>]+id=["\']content["\'][^>]*>(.*?)</div>',
        r'<div[^>]+class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
    )
    for pattern in patterns:
        m = re.search(pattern, page_html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            text = strip_tags(m.group(1))
            if len(text) > 60:
                return text

    body = re.search(r"<body[^>]*>(.*?)</body>", page_html, flags=re.IGNORECASE | re.DOTALL)
    if not body:
        return ""
    return strip_tags(body.group(1))


def chapter_sort_key(url: str) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.html(?:$|\?))", url)
    if match:
        return int(match.group(1)), url
    return sys.maxsize, url


def discover_chapters(index_url: str, html_text: str) -> List[Chapter]:
    parser = AnchorParser()
    parser.feed(html_text)

    parsed = urlparse(index_url)
    chapter_prefix = f"/novel/{parsed.path.strip('/').split('/')[-1].replace('.html', '')}/"

    seen: set[str] = set()
    chapters: list[Chapter] = []

    for href, text in parser.links:
        absolute_url = urljoin(index_url, href)
        parsed_abs = urlparse(absolute_url)
        if parsed_abs.netloc != parsed.netloc:
            continue
        if not parsed_abs.path.startswith(chapter_prefix):
            continue
        if not parsed_abs.path.endswith(".html"):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        title = text.strip() or parsed_abs.path.rsplit("/", 1)[-1].replace(".html", "")
        chapters.append(Chapter(title=title, url=absolute_url))

    chapters.sort(key=lambda c: chapter_sort_key(c.url))
    return chapters


def save_novel(chapters: Iterable[Chapter], output_file: Path, delay: float = 0.2) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_file.open("w", encoding="utf-8") as f:
        for idx, chapter in enumerate(chapters, start=1):
            print(f"[{idx}] 下载 {chapter.title} -> {chapter.url}")
            try:
                chapter_html = fetch_html(chapter.url)
            except URLError as exc:
                print(f"[警告] 章节下载失败，已跳过: {exc}")
                continue
            title = extract_title(chapter_html)
            content = extract_content(chapter_html)
            if not content:
                content = "[警告] 未能提取正文。"

            f.write(f"{title}\n")
            f.write("=" * len(title) + "\n\n")
            f.write(content)
            f.write("\n\n\n")
            count += 1
            time.sleep(delay)

    print(f"完成：共写入 {count} 章 -> {output_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 alicesw 小说并导出成 txt")
    parser.add_argument("index_url", help="小说目录页链接，例如 https://www.alicesw.tw/novel/19861.html")
    parser.add_argument("-o", "--output", default="novel.txt", help="输出 txt 文件路径")
    parser.add_argument("--delay", type=float, default=0.2, help="每章下载间隔秒数，默认 0.2")
    parser.add_argument("--start", type=int, default=1, help="起始章节（从1开始）")
    parser.add_argument("--end", type=int, default=0, help="结束章节（0 表示到最后）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(f"读取目录页: {args.index_url}")
    try:
        index_html = fetch_html(args.index_url)
    except URLError as exc:
        print(f"目录页请求失败: {exc}")
        return 1
    chapters = discover_chapters(args.index_url, index_html)

    if not chapters:
        print("未发现章节链接，请检查目录页结构或链接是否可访问。")
        return 1

    start = max(args.start, 1)
    end = args.end if args.end > 0 else len(chapters)
    selected = chapters[start - 1 : end]

    if not selected:
        print("筛选后没有章节，请检查 --start/--end 参数。")
        return 1

    print(f"发现 {len(chapters)} 章，准备下载 {len(selected)} 章。")
    save_novel(selected, Path(args.output), delay=max(args.delay, 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
