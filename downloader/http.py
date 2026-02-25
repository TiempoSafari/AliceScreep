from __future__ import annotations

import time
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


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
