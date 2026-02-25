from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import URLError

from .conversion import OPENCC, maybe_convert_to_simplified
from .epub import build_epub
from .http import fetch_html_with_retry
from .models import Chapter, ChapterContent, DownloadPayload
from .selenium_client import SeleniumClient, create_selenium_client_with_timeout
from .sites import detect_source, extract_cover_url, fetch_cover_bytes, get_site_adapter
from .text import extract_title, normalize_chapter_title, safe_filename, sanitize_url


def _fetch_html(
    url: str,
    logger: Callable[[str], None],
    selenium_client: SeleniumClient | None,
    retries: int = 2,
    wait_seconds: float = 1.0,
) -> str:
    if selenium_client:
        return selenium_client.fetch_html_with_retry(url, logger=logger, retries=retries, wait_seconds=max(wait_seconds, 1.2))
    return fetch_html_with_retry(url, logger=logger, retries=retries, wait_seconds=wait_seconds)


def _download_chapters(
    chapters: Iterable[Chapter],
    adapter,
    delay: float = 0.2,
    logger: Callable[[str], None] = print,
    to_simplified: bool = True,
    progress_callback: Callable[[int, int], None] | None = None,
    selenium_client: SeleniumClient | None = None,
) -> list[ChapterContent]:
    chapter_list = list(chapters)
    total = len(chapter_list)
    if total == 0:
        return []

    downloaded: list[ChapterContent] = []
    for idx, chapter in enumerate(chapter_list, start=1):
        chapter_url = sanitize_url(chapter.url)
        if not chapter_url:
            logger(f"❌ [警告] 跳过非法章节链接: {chapter.url}")
            if progress_callback:
                progress_callback(idx, total)
            continue

        logger(f"[{idx}] 下载中: {chapter.title} -> {chapter_url}")
        try:
            chapter_html = _fetch_html(
                chapter_url,
                logger=logger,
                selenium_client=selenium_client,
                retries=2,
                wait_seconds=1.0,
            )
        except (URLError, ValueError, OSError) as exc:
            logger(f"❌ [警告] 章节下载失败，已跳过: {chapter_url} | 错误: {exc}")
            if progress_callback:
                progress_callback(idx, total)
            continue

        page_title = extract_title(chapter_html)
        title = normalize_chapter_title(chapter.title or page_title)
        if title == "未知章节":
            title = normalize_chapter_title(page_title)

        content = adapter.extract_content(chapter_html)
        if not content:
            logger(f"❌ [警告] 正文提取失败，已跳过: {chapter_url}")
            if progress_callback:
                progress_callback(idx, total)
            continue

        title = maybe_convert_to_simplified(title, to_simplified)
        content = maybe_convert_to_simplified(content, to_simplified)
        downloaded.append(ChapterContent(title=title, content=content, source_url=chapter_url))
        logger(f"✅ 下载成功: {title}")

        if delay > 0:
            time.sleep(delay)
        if progress_callback:
            progress_callback(idx, total)

    return downloaded


def download_novel_payload(
    input_url: str,
    start: int,
    end: int,
    delay: float,
    logger: Callable[[str], None] = print,
    to_simplified: bool = True,
    progress_callback: Callable[[int, int], None] | None = None,
    site_auth: dict | None = None,
) -> DownloadPayload | None:
    logger(f"输入链接: {input_url}")
    adapter = get_site_adapter(input_url)

    selenium_client: SeleniumClient | None = None
    auth = site_auth or {}
    source = detect_source(input_url)

    if source == "esj":
        logger("⏳ ESJ: 正在初始化 Selenium（无界面模式，最多等待 180 秒）...")
        selenium_client = create_selenium_client_with_timeout(
            logger=logger,
            timeout_seconds=180.0,
            headless=True,
        )
        if selenium_client is None:
            logger("❌ ESJ Selenium 初始化失败或超时，已中止。")
            logger("💡 建议检查：1) Chrome 是否已安装 2) 是否可访问 Google 驱动下载源 3) 杀毒软件是否拦截 chromedriver")
            return None

        if auth.get("use_login"):
            username = str(auth.get("username", "")).strip()
            password = str(auth.get("password", ""))
            if not username or not password:
                logger("❌ ESJ 已启用登录，但用户名或密码为空，已中止。")
                selenium_client.close()
                return None
            try:
                logger("⏳ ESJ: 正在使用 Selenium 登录...")
                selenium_client.login_esjzone(username, password, logger=logger)
                logger("✅ ESJ: Selenium 登录流程结束，开始抓取目录页...")
            except Exception as exc:
                logger(f"❌ ESJ Selenium 登录失败，已中止: {exc}")
                selenium_client.close()
                return None
        else:
            logger("❌ [警告] ESJ 未启用登录，可能无法看到章节列表。建议在站点配置中开启登录。")

    chapter_index_url = adapter.build_chapter_index_url(input_url)
    if chapter_index_url:
        logger(f"使用章节目录页: {chapter_index_url}")
    else:
        logger("❌ [警告] 无法自动识别小说ID，将直接使用输入链接作为目录页。")
        chapter_index_url = input_url

    try:
        index_html = _fetch_html(
            chapter_index_url,
            logger=logger,
            selenium_client=selenium_client,
            retries=2,
            wait_seconds=1.0,
        )
    except URLError as exc:
        logger(f"❌ 目录页请求失败: {exc}")
        if selenium_client:
            selenium_client.close()
        return None

    meta = adapter.extract_meta(index_html)
    logger(f"小说信息: 标题={meta.title} | 作者={meta.author}")

    if to_simplified:
        if OPENCC.available:
            logger("✅ 已启用繁体转简体（OpenCC t2s）")
        else:
            logger("❌ [警告] 未安装 opencc，暂无法自动繁转简（可 `pip install opencc-python-reimplemented`）")

    chapters = adapter.discover_chapters(chapter_index_url, index_html, logger=logger)
    if not chapters:
        logger("❌ 未发现章节链接：请确认链接是否为小说详情页/章节目录页，或网站结构已变化。")
        if selenium_client:
            selenium_client.close()
        return None

    safe_start = max(start, 1)
    safe_end = end if end > 0 else len(chapters)
    selected = chapters[safe_start - 1 : safe_end]
    if not selected:
        logger("❌ 筛选后没有章节，请检查起始章节/结束章节。")
        if selenium_client:
            selenium_client.close()
        return None

    logger(f"准备下载：总章节 {len(chapters)}，本次下载 {len(selected)}（范围 {safe_start}-{safe_end}）")
    downloaded = _download_chapters(
        selected,
        adapter,
        delay=max(delay, 0),
        logger=logger,
        to_simplified=to_simplified,
        progress_callback=progress_callback,
        selenium_client=selenium_client,
    )
    if not downloaded:
        logger("❌ 没有成功下载任何章节，未生成 EPUB。")
        if selenium_client:
            selenium_client.close()
        return None

    cover_bytes = cover_type = cover_name = None
    novel_url = adapter.build_novel_url(input_url)
    if novel_url:
        try:
            novel_html = _fetch_html(
                novel_url,
                logger=logger,
                selenium_client=selenium_client,
                retries=1,
                wait_seconds=1.0,
            )
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

    payload = DownloadPayload(meta=meta, chapters=downloaded, cover_bytes=cover_bytes, cover_type=cover_type, cover_name=cover_name)
    if selenium_client:
        selenium_client.close()
    return payload


def _resolve_output_file(payload: DownloadPayload, output_file: Path) -> Path:
    if output_file.name.lower() == "novel.epub":
        return output_file.with_name(safe_filename(payload.meta.title, suffix=".epub"))
    return output_file if output_file.suffix.lower() == ".epub" else output_file.with_suffix(".epub")


def save_payload_to_epub(payload: DownloadPayload, output_file: Path, logger: Callable[[str], None] = print) -> int:
    output_file = _resolve_output_file(payload, output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    build_epub(output_file, payload.meta, payload.chapters, payload.cover_bytes, payload.cover_type, payload.cover_name)
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
    site_auth: dict | None = None,
) -> int:
    payload = download_novel_payload(input_url, start, end, delay, logger=logger, to_simplified=to_simplified, site_auth=site_auth)
    if payload is None:
        return 1
    return save_payload_to_epub(payload, output_file, logger=logger)
