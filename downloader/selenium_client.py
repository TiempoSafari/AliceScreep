from __future__ import annotations

import time
from typing import Callable


class SeleniumClient:
    def __init__(self, headless: bool = True) -> None:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("未安装 selenium，请先执行: pip install selenium") from exc

        options = Options()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1400,1000")
        options.add_argument("--lang=zh-CN")

        try:
            self.driver = webdriver.Chrome(options=options)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Selenium ChromeDriver 启动失败，请安装 Chrome/Chromium 与兼容的 chromedriver（或确保 Selenium Manager 可用）"
            ) from exc

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def fetch_html(self, url: str, wait_seconds: float = 1.2) -> str:
        self.driver.get(url)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        return self.driver.page_source

    def fetch_html_with_retry(
        self,
        url: str,
        logger: Callable[[str], None] | None = None,
        retries: int = 2,
        wait_seconds: float = 1.2,
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                return self.fetch_html(url, wait_seconds=wait_seconds)
            except Exception as exc:
                last_exc = exc
                if attempt > retries:
                    break
                if logger:
                    logger(f"❌ [警告] Selenium 请求失败，准备重试({attempt}/{retries}): {url} | 错误: {exc}")
                time.sleep(0.8)
        assert last_exc is not None
        raise last_exc

    def login_esjzone(self, username: str, password: str, logger: Callable[[str], None] | None = None) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        login_url = "https://www.esjzone.cc/my/login"
        self.driver.get(login_url)

        wait = WebDriverWait(self.driver, 20)
        email = wait.until(EC.presence_of_element_located((By.NAME, "email")))
        pwd = wait.until(EC.presence_of_element_located((By.NAME, "password")))

        email.clear()
        email.send_keys(username)
        pwd.clear()
        pwd.send_keys(password)

        buttons = self.driver.find_elements(By.CSS_SELECTOR, 'button[type="submit"], input[type="submit"]')
        if not buttons:
            raise RuntimeError("未找到 ESJ 登录提交按钮")
        buttons[0].click()

        # 等待登录完成（URL变化或页面出现登出关键词）
        WebDriverWait(self.driver, 20).until(
            lambda d: (
                d.current_url != login_url
                or "logout" in d.page_source.lower()
                or "登出" in d.page_source
                or "我的书架" in d.page_source
            )
        )

        if logger:
            page = self.driver.page_source
            if "logout" in page.lower() or "登出" in page or "我的书架" in page:
                logger("✅ ESJ Selenium 登录成功，已应用浏览器会话。")
            else:
                logger("❌ [警告] ESJ Selenium 登录后未检测到明显登录态标记，后续抓取可能失败。")



def create_selenium_client_with_timeout(
    logger: Callable[[str], None] | None = None,
    timeout_seconds: float = 20.0,
    headless: bool = True,
) -> SeleniumClient | None:
    """在超时时间内创建 SeleniumClient，避免 driver 初始化长时间卡住主流程。"""
    import threading

    holder: dict[str, SeleniumClient | Exception] = {}

    def _worker() -> None:
        try:
            holder["client"] = SeleniumClient(headless=headless)
        except Exception as exc:
            holder["error"] = exc

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    th.join(timeout_seconds)

    if th.is_alive():
        if logger:
            logger(f"❌ [警告] Selenium 启动超时（>{timeout_seconds:.0f}s），将回退 HTTP 抓取。")
        return None

    if "error" in holder:
        if logger:
            logger(f"❌ [警告] Selenium 启动失败，将回退 HTTP 抓取: {holder['error']}")
        return None

    return holder.get("client")  # type: ignore[return-value]
