#!/usr/bin/env python3
"""下载 alicesw 小说目录并导出为 txt（含 GUI）。"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from threading import Thread
from typing import Callable, Iterable, List, Optional
from urllib.error import URLError
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse
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
    order: int = sys.maxsize


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

    # 防止章节 URL 中出现未转义空格或中文导致请求失败
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


def extract_title(page_html: str) -> str:
    for pattern in (
        r"<h1[^>]*>(.*?)</h1>",
        r"<title[^>]*>(.*?)</title>",
    ):
        match = re.search(pattern, page_html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return strip_tags(match.group(1))
    return "未知标题"


def extract_content(page_html: str) -> str:
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
    patterns = (
        r"/novel/(\d+)\.html",
        r"/other/chapters/id/(\d+)\.html",
    )
    for pattern in patterns:
        match = re.search(pattern, path)
        if match:
            return match.group(1)
    return ""


def build_chapter_index_url(input_url: str) -> Optional[str]:
    parsed = urlparse(input_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    novel_id = extract_novel_id(input_url)
    if not novel_id:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/other/chapters/id/{novel_id}.html"


def pick_chapter_list_html(page_html: str) -> str:
    match = re.search(
        r'<ul[^>]+class=["\'][^"\']*mulu_list[^"\']*["\'][^>]*>(.*?)</ul>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1)
    return page_html


def discover_chapters(index_url: str, html_text: str, logger: Callable[[str], None] = print) -> List[Chapter]:
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

        # 强约束：章节页应在 /book/ 路径下，可避免抓到导航/分类/个人中心等页面
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


def save_novel(
    chapters: Iterable[Chapter],
    output_file: Path,
    delay: float = 0.2,
    logger: Callable[[str], None] = print,
) -> int:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_file.open("w", encoding="utf-8") as file_obj:
        for idx, chapter in enumerate(chapters, start=1):
            chapter_url = sanitize_url(chapter.url)
            if not chapter_url:
                logger(f"[警告] 跳过非法章节链接: {chapter.url}")
                continue

            logger(f"[{idx}] 下载 {chapter.title} -> {chapter_url}")
            try:
                chapter_html = fetch_html(chapter_url)
            except (URLError, ValueError, OSError) as exc:
                logger(f"[警告] 章节下载失败，已跳过: {chapter_url} | 错误: {exc}")
                continue
            title = extract_title(chapter_html)
            content = extract_content(chapter_html)
            if not content:
                content = "[警告] 未能提取正文。"

            file_obj.write(f"{title}\n")
            file_obj.write("=" * len(title) + "\n\n")
            file_obj.write(content)
            file_obj.write("\n\n\n")
            count += 1
            time.sleep(delay)

    logger(f"完成：共写入 {count} 章 -> {output_file}")
    return count


def run_download(
    input_url: str,
    output_file: Path,
    start: int,
    end: int,
    delay: float,
    logger: Callable[[str], None] = print,
) -> int:
    logger(f"输入链接: {input_url}")

    chapter_index_url = build_chapter_index_url(input_url)
    if chapter_index_url:
        logger(f"使用章节目录页: {chapter_index_url}")
    else:
        logger("[警告] 无法自动识别小说ID，将直接使用输入链接作为目录页。")
        chapter_index_url = input_url

    try:
        index_html = fetch_html(chapter_index_url)
    except URLError as exc:
        logger(f"目录页请求失败: {exc}")
        return 1

    chapters = discover_chapters(chapter_index_url, index_html, logger=logger)
    if not chapters:
        logger("未发现章节链接：请确认链接是否为小说详情页/章节目录页，或网站结构已变化。")
        return 1

    safe_start = max(start, 1)
    safe_end = end if end > 0 else len(chapters)
    selected = chapters[safe_start - 1 : safe_end]

    if not selected:
        logger("筛选后没有章节，请检查起始章节/结束章节。")
        return 1

    logger(f"准备下载：总章节 {len(chapters)}，本次下载 {len(selected)}（范围 {safe_start}-{safe_end}）")
    save_novel(selected, output_file, delay=max(delay, 0), logger=logger)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 alicesw 小说并导出成 txt")
    parser.add_argument("index_url", nargs="?", help="小说链接，例如 https://www.alicesw.tw/novel/2735.html")
    parser.add_argument("-o", "--output", default="novel.txt", help="输出 txt 文件路径")
    parser.add_argument("--delay", type=float, default=0.2, help="每章下载间隔秒数，默认 0.2")
    parser.add_argument("--start", type=int, default=1, help="起始章节（从1开始）")
    parser.add_argument("--end", type=int, default=0, help="结束章节（0 表示到最后）")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    return parser.parse_args()


def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
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
    root.geometry("760x560")

    frame = tk.Frame(root, padx=12, pady=12)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="小说链接").grid(row=0, column=0, sticky="w")
    url_var = tk.StringVar(value="https://www.alicesw.tw/novel/2735.html")
    tk.Entry(frame, textvariable=url_var, width=72).grid(row=0, column=1, columnspan=3, sticky="we", pady=4)

    tk.Label(frame, text="输出文件").grid(row=1, column=0, sticky="w")
    output_var = tk.StringVar(value=str(Path.cwd() / "novel.txt"))
    tk.Entry(frame, textvariable=output_var, width=58).grid(row=1, column=1, columnspan=2, sticky="we", pady=4)

    def choose_output() -> None:
        path = filedialog.asksaveasfilename(
            title="选择输出 TXT 文件",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if path:
            output_var.set(path)

    tk.Button(frame, text="浏览", command=choose_output, width=10).grid(row=1, column=3, padx=6)

    tk.Label(frame, text="起始章节").grid(row=2, column=0, sticky="w")
    start_var = tk.StringVar(value="1")
    tk.Entry(frame, textvariable=start_var, width=10).grid(row=2, column=1, sticky="w", pady=4)

    tk.Label(frame, text="结束章节(0=最后)").grid(row=2, column=2, sticky="e")
    end_var = tk.StringVar(value="0")
    tk.Entry(frame, textvariable=end_var, width=10).grid(row=2, column=3, sticky="w", pady=4)

    tk.Label(frame, text="章节间隔秒").grid(row=3, column=0, sticky="w")
    delay_var = tk.StringVar(value="0.2")
    tk.Entry(frame, textvariable=delay_var, width=10).grid(row=3, column=1, sticky="w", pady=4)

    log_box = ScrolledText(frame, height=22)
    log_box.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=(10, 6))
    frame.rowconfigure(4, weight=1)
    frame.columnconfigure(1, weight=1)

    downloading = {"active": False}

    def log(msg: str) -> None:
        def _append() -> None:
            log_box.insert("end", msg + "\n")
            log_box.see("end")

        root.after(0, _append)

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

        downloading["active"] = True
        log_box.delete("1.0", "end")
        log("开始下载...")

        def worker() -> None:
            code = run_download(input_url, Path(output_path), start, end, delay, logger=log)

            def done() -> None:
                downloading["active"] = False
                if code == 0:
                    messagebox.showinfo("完成", "下载完成。")
                else:
                    messagebox.showerror("失败", "下载失败，请检查日志。")

            root.after(0, done)

        Thread(target=worker, daemon=True).start()

    tk.Button(frame, text="开始下载", command=start_download, width=16).grid(row=5, column=0, sticky="w", pady=4)
    tk.Button(frame, text="退出", command=root.destroy, width=12).grid(row=5, column=3, sticky="e", pady=4)

    root.mainloop()
    return 0


def main() -> int:
    args = parse_args()
    if args.gui or not args.index_url:
        return launch_gui()
    return run_download(args.index_url, Path(args.output), args.start, args.end, args.delay)


if __name__ == "__main__":
    raise SystemExit(main())
