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
from typing import Callable, Iterable, List
from urllib.error import URLError
from urllib.parse import unquote, urljoin, urlparse
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


def chapter_sort_key(url: str) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.html(?:$|\?))", url)
    if match:
        return int(match.group(1)), url
    return sys.maxsize, url


def extract_novel_id(index_url: str) -> str:
    path = urlparse(index_url).path.strip("/")
    if not path:
        return ""
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".html"):
        name = name[:-5]
    match = re.search(r"(\d+)", name)
    return match.group(1) if match else ""


def collect_candidate_links(index_url: str, html_text: str) -> list[tuple[str, str]]:
    parser = AnchorParser()
    parser.feed(html_text)
    links = list(parser.links)

    # 兼容 script/json 里的链接
    script_patterns = (
        r'(?:href|url)\s*[:=]\s*["\']([^"\']+\.html(?:\?[^"\']*)?)["\']',
        r'["\'](\/[^"\']+\.html(?:\?[^"\']*)?)["\']',
        r'["\'](https?:\/\/[^"\']+\.html(?:\?[^"\']*)?)["\']',
    )
    for pattern in script_patterns:
        for href in re.findall(pattern, html_text, flags=re.IGNORECASE):
            links.append((href, ""))

    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, text in links:
        href = html.unescape(href).replace("\\/", "/")
        absolute_url = urljoin(index_url, href).split("#", 1)[0]
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        normalized.append((absolute_url, text.strip()))
    return normalized


def looks_like_chapter_url(index_url: str, candidate_url: str, novel_id: str) -> bool:
    index_parsed = urlparse(index_url)
    parsed = urlparse(candidate_url)

    if parsed.netloc and parsed.netloc != index_parsed.netloc:
        return False

    path = unquote(parsed.path)
    if not path.lower().endswith(".html"):
        return False

    if path.rstrip("/") == index_parsed.path.rstrip("/"):
        return False

    if novel_id and f"/novel/{novel_id}/" in path:
        return True

    # 其它常见结构，只要路径里有小说 id 且是 html 即视为章节
    if novel_id and novel_id in path and "/novel/" in path:
        return True

    filename = path.rsplit("/", 1)[-1].lower()
    if re.search(r"(chapter|chap|\d+)", filename):
        return True

    return False


def discover_chapters(index_url: str, html_text: str) -> List[Chapter]:
    novel_id = extract_novel_id(index_url)
    candidates = collect_candidate_links(index_url, html_text)

    seen: set[str] = set()
    chapters: list[Chapter] = []

    for absolute_url, text in candidates:
        if not looks_like_chapter_url(index_url, absolute_url, novel_id):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        parsed_abs = urlparse(absolute_url)
        title = text or parsed_abs.path.rsplit("/", 1)[-1].replace(".html", "")
        chapters.append(Chapter(title=title, url=absolute_url))

    chapters.sort(key=lambda c: chapter_sort_key(c.url))
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
            logger(f"[{idx}] 下载 {chapter.title} -> {chapter.url}")
            try:
                chapter_html = fetch_html(chapter.url)
            except URLError as exc:
                logger(f"[警告] 章节下载失败，已跳过: {exc}")
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
    index_url: str,
    output_file: Path,
    start: int,
    end: int,
    delay: float,
    logger: Callable[[str], None] = print,
) -> int:
    logger(f"读取目录页: {index_url}")
    try:
        index_html = fetch_html(index_url)
    except URLError as exc:
        logger(f"目录页请求失败: {exc}")
        return 1

    chapters = discover_chapters(index_url, index_html)
    if not chapters:
        logger("未发现章节链接，请检查目录页结构或链接是否可访问。")
        return 1

    safe_start = max(start, 1)
    safe_end = end if end > 0 else len(chapters)
    selected = chapters[safe_start - 1 : safe_end]

    if not selected:
        logger("筛选后没有章节，请检查起始章节/结束章节。")
        return 1

    logger(f"发现 {len(chapters)} 章，准备下载 {len(selected)} 章。")
    save_novel(selected, output_file, delay=max(delay, 0), logger=logger)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 alicesw 小说并导出成 txt")
    parser.add_argument("index_url", nargs="?", help="小说目录页链接，例如 https://www.alicesw.tw/novel/19861.html")
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

    tk.Label(frame, text="目录链接").grid(row=0, column=0, sticky="w")
    url_var = tk.StringVar(value="https://www.alicesw.tw/novel/19861.html")
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

        url = url_var.get().strip()
        output_path = output_var.get().strip()
        if not url or not output_path:
            messagebox.showerror("参数错误", "请填写目录链接与输出文件路径。")
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
            code = run_download(url, Path(output_path), start, end, delay, logger=log)

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
