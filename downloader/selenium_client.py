from __future__ import annotations

import time
from typing import Callable


class SeleniumClient:
    def __init__(self, headless: bool = True, logger: Callable[[str], None] | None = None) -> None:
        if logger:
            logger("⏳ Selenium: 正在导入 selenium 模块...")
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("未安装 selenium，请先执行: pip install selenium") from exc

        if logger:
            logger("⏳ Selenium: 正在配置浏览器启动参数...")
        options = Options()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1200")
        options.add_argument("--lang=zh-CN")

        if logger:
            logger("⏳ Selenium: 正在启动 ChromeDriver（首次可能下载驱动，耗时较长）...")
        try:
            self.driver = webdriver.Chrome(options=options)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Selenium ChromeDriver 启动失败，请安装 Chrome/Chromium 与兼容的 chromedriver（或确保 Selenium Manager 可用）"
            ) from exc
        if logger:
            logger("✅ Selenium: ChromeDriver 启动成功。")

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

        wait = WebDriverWait(self.driver, 25)
        if logger:
            logger("⏳ ESJ: 等待登录表单加载...")

        # ESJ 登录区与注册区都含 email 字段，优先锁定 login-box 表单
        email = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.login-box input[name='email']")))
        pwd = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.login-box input[name='pwd']")))

        email.clear()
        email.send_keys(username)
        pwd.clear()
        pwd.send_keys(password)

        if logger:
            logger("⏳ ESJ: 已填入账号密码，正在提交登录...")

        # 该站点登录按钮是 a.btn-send[data-send='mem_login']，并非 submit
        login_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "form.login-box .btn-send[data-send='mem_login']")))
        try:
            login_btn.click()
        except Exception:
            # 某些情况下被广告层遮挡，用 JS 兜底点击
            self.driver.execute_script("arguments[0].click();", login_btn)

        # 等待登录完成：登录入口消失 / 出现登出标记 / 跳转
        WebDriverWait(self.driver, 25).until(
            lambda d: (
                d.current_url != login_url
                or "/my/login" not in d.current_url
                or "logout" in d.page_source.lower()
                or "登出" in d.page_source
                or "我的书架" in d.page_source
                or ("登入 / 註冊" not in d.page_source and "/my/login" not in d.page_source)
            )
        )

        page = self.driver.page_source
        if logger:
            if "logout" in page.lower() or "登出" in page or "我的书架" in page or "登入 / 註冊" not in page:
                logger("✅ ESJ Selenium 登录成功，已应用浏览器会话。")
            else:
                logger("❌ [警告] ESJ Selenium 登录后未检测到明显登录态标记，后续抓取可能失败。")



def create_selenium_client_with_timeout(
    logger: Callable[[str], None] | None = None,
    timeout_seconds: float = 180.0,
    headless: bool = True,
) -> SeleniumClient | None:
    """在超时时间内创建 SeleniumClient，避免 driver 初始化长时间卡住主流程。默认超时时间较长以适配慢速环境。"""
    import threading

    holder: dict[str, SeleniumClient | Exception] = {}

    def _worker() -> None:
        try:
            holder["client"] = SeleniumClient(headless=headless, logger=logger)
        except Exception as exc:
            holder["error"] = exc

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    th.join(timeout_seconds)

    if th.is_alive():
        if logger:
            logger(f"❌ [警告] Selenium 启动超时（>{timeout_seconds:.0f}s）。可能卡在驱动下载/浏览器启动阶段。")
        return None

    if "error" in holder:
        if logger:
            logger(f"❌ [警告] Selenium 启动失败: {holder['error']}")
        return None

    return holder.get("client")  # type: ignore[return-value]
