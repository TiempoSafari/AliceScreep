from __future__ import annotations

import time
from http.cookiejar import CookieJar
from typing import Callable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class HttpClient:
    def __init__(self) -> None:
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))

    def fetch_bytes(self, url: str, timeout: int = 30) -> bytes:
        req = Request(url, headers={"User-Agent": UA})
        with self.opener.open(req, timeout=timeout) as resp:
            return resp.read()

    def fetch_html(self, url: str, timeout: int = 30) -> str:
        raw = self.fetch_bytes(url, timeout=timeout)
        for encoding in ("utf-8", "gb18030", "big5"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")

    def fetch_html_with_retry(
        self,
        url: str,
        logger: Callable[[str], None] | None = None,
        retries: int = 2,
        wait_seconds: float = 0.8,
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                return self.fetch_html(url)
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                last_exc = exc
                if attempt > retries:
                    break
                if logger:
                    logger(f"❌ [警告] 请求失败，准备重试({attempt}/{retries}): {url} | 错误: {exc}")
                time.sleep(wait_seconds)
        assert last_exc is not None
        raise last_exc

    def post_form(self, url: str, data: dict[str, str], timeout: int = 30) -> bytes:
        body = urlencode(data).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with self.opener.open(req, timeout=timeout) as resp:
            return resp.read()


_DEFAULT_CLIENT = HttpClient()


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    return _DEFAULT_CLIENT.fetch_bytes(url, timeout=timeout)


def fetch_html(url: str, timeout: int = 30) -> str:
    return _DEFAULT_CLIENT.fetch_html(url, timeout=timeout)


def fetch_html_with_retry(
    url: str,
    logger: Callable[[str], None] | None = None,
    retries: int = 2,
    wait_seconds: float = 0.8,
) -> str:
    return _DEFAULT_CLIENT.fetch_html_with_retry(url, logger=logger, retries=retries, wait_seconds=wait_seconds)


def login_esjzone(username: str, password: str, logger: Callable[[str], None] | None = None) -> HttpClient:
    client = HttpClient()
    login_url = "https://www.esjzone.cc/my/login"

    # 先访问登录页建立 cookie（某些站点要求）
    try:
        login_page = client.fetch_html(login_url, timeout=30)
    except Exception as exc:
        raise URLError(f"访问 ESJ 登录页失败: {exc}") from exc

    token = ""
    import re

    for pattern in (
        r'''name=["\']_token["\']\s+value=["\']([^"\']+)["\']''',
        r'''name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']''',
        r'''name=["\']csrf-token["\']\s+value=["\']([^"\']+)["\']''',
    ):
        m = re.search(pattern, login_page, re.I)
        if m:
            token = m.group(1)
            break

    form = {
        "email": username,
        "password": password,
    }
    if token:
        form["_token"] = token

    try:
        raw = client.post_form(login_url, form, timeout=30)
    except Exception as exc:
        raise URLError(f"提交 ESJ 登录表单失败: {exc}") from exc

    text = raw.decode("utf-8", errors="ignore")
    if "logout" not in text.lower() and "登出" not in text and "我的书架" not in text:
        if logger:
            logger("❌ [警告] ESJ 登录后未检测到明显登录态标记，后续抓取可能失败（请确认账号密码）。")
    else:
        if logger:
            logger("✅ ESJ 登录成功，已应用登录会话。")

    return client
