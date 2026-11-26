"""Pooled HTTP sessions and async Playwright Chromium browser pool for scrapers."""

import asyncio
import threading
from typing import Any, Optional, Set, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.browser_session import (
    build_chrome_launch_kwargs,
    prepare_chrome_profile_dir,
    restore_storage_cookies,
)
from core.config import CHROME_PROFILE_DIR

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

_THREAD_LOCAL = threading.local()


def get_http_session() -> requests.Session:
    """Return a thread-local requests.Session with connection pooling and retry/backoff."""
    sess = getattr(_THREAD_LOCAL, "session", None)
    if sess is None:
        sess = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=retry)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        sess.headers.update(HEADERS)
        _THREAD_LOCAL.session = sess
    return sess


def is_login_wall(final_url: str, html: str) -> bool:
    u = (final_url or "").lower()
    if any(tok in u for tok in ("/login", "/signin", "/sign_in", "/connexion", "authwall", "/account/login")):
        return True
    low = (html or "")[:4000].lower()
    if "sign in to" in low or "connectez-vous pour" in low:
        return True
    return False


class AsyncBrowserPool:
    """Async multi-tab headless Chromium pool sharing `.chrome_profile/` login cookies."""

    def __init__(self, max_tabs: int = 5) -> None:
        self.max_tabs = max_tabs
        self._pw = None
        self._context = None
        self._pages: Optional[asyncio.Queue] = None
        self._failed = False
        self._start_lock: Optional[asyncio.Lock] = None
        self._dismissed_domains: Set[str] = set()

    async def _ensure_started(self) -> bool:
        if self._pages is not None:
            return True
        if self._failed:
            return False
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._pages is not None:
                return True
            if self._failed:
                return False
            try:
                from playwright.async_api import async_playwright

                if not prepare_chrome_profile_dir(CHROME_PROFILE_DIR):
                    self._failed = True
                    return False
                self._pw = await async_playwright().start()
                pool_kwargs = build_chrome_launch_kwargs(CHROME_PROFILE_DIR, headless=True)
                self._context = await self._pw.chromium.launch_persistent_context(**pool_kwargs)
                await restore_storage_cookies(self._context, CHROME_PROFILE_DIR)
                q: asyncio.Queue = asyncio.Queue()
                existing_pages = list(self._context.pages)
                for p in existing_pages[: self.max_tabs]:
                    await q.put(p)
                for _ in range(max(0, self.max_tabs - len(existing_pages))):
                    await q.put(await self._context.new_page())
                self._pages = q
                return True
            except Exception as e:
                self._failed = True
                print(f"  [!] Could not start .chrome_profile browser session: {e}")
                print("  [!] Close any open StageBot Chrome window and retry.")
                return False

    async def _dismiss_cookies(self, page: Any, domain: str) -> None:
        if domain in self._dismissed_domains:
            return
        for sel in (
            "#didomi-notice-agree-button",
            "#onetrust-accept-btn-handler",
            "#axeptio_btn_acceptAll",
            "button:has-text('Tout accepter')",
            "button:has-text('Accepter tout')",
            "button:has-text(\"J'accepte\")",
        ):
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=350):
                    await loc.click()
                    await page.wait_for_timeout(200)
                    self._dismissed_domains.add(domain)
                    return
            except Exception:
                continue
        self._dismissed_domains.add(domain)

    async def fetch_html(
        self,
        url: str,
        wait_selector: str = "h1, main",
        wait_ms: int = 1200,
    ) -> Tuple[str, str]:
        """Navigate with `.chrome_profile/` cookies using a pooled tab and return `(final_url, html)`."""
        if not await self._ensure_started():
            return url, ""
        assert self._pages is not None
        page = await self._pages.get()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=wait_ms)
                except Exception:
                    await page.wait_for_timeout(min(wait_ms, 800))
            else:
                await page.wait_for_timeout(min(wait_ms, 800))
            await page.wait_for_timeout(150)
            domain = urlparse(page.url or url).netloc
            await self._dismiss_cookies(page, domain)
            return page.url, await page.content()
        except Exception as e:
            print(f"  [!] Browser fetch error for {url}: {e}")
            return url, ""
        finally:
            await self._pages.put(page)

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._pages = None
        self._context = None
        self._pw = None
