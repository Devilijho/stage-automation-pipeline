"""ATS provider detection, login URLs, submission confirmation, and expired offer checks."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from playwright.async_api import Page

from browser.adapters import GENERIC_ADAPTER, SITE_ADAPTERS, SiteAdapter, resolve_adapter
from browser.adapters.base import (
    DEFAULT_CONFIRMATION_PHRASES,
    DEFAULT_CONFIRMATION_URL_TOKENS,
    DEFAULT_EXPIRED_PHRASES,
)
from browser.dom_helpers import get_form_roots
from browser.navigation import collect_validation_errors
from core.config import DEBUG_DIR
from core.logging_utils import get_logger

_log = get_logger("browser.ats_rules")

SITE_CONFIRMATION_PHRASES: Dict[str, Tuple[str, ...]] = {
    adapter.name: adapter.confirmation_phrases for adapter in SITE_ADAPTERS
}

CONFIRMATION_PHRASES_EXTERNAL: Tuple[str, ...] = DEFAULT_CONFIRMATION_PHRASES

EXPIRED_OFFER_PHRASES = list(DEFAULT_EXPIRED_PHRASES)

LOGIN_URLS: Dict[str, Tuple[str, str]] = {
    "linkedin": ("LinkedIn", "https://www.linkedin.com/login"),
    "hellowork": ("HelloWork", "https://www.hellowork.com/fr-fr/candidat/connexion-inscription.html"),
    "wttj": ("Welcome to the Jungle", "https://www.welcometothejungle.com/fr"),
    "jobteaser": ("JobTeaser", "https://www.jobteaser.com/fr/users/sign_in"),
    "indeed": ("Indeed", "https://fr.indeed.com/account/login"),
    "apec": ("APEC", "https://www.apec.fr"),
    "meteojob": ("Meteojob", "https://www.meteojob.com/"),
}

CONFIRMATION_URL_TOKENS = DEFAULT_CONFIRMATION_URL_TOKENS


def detect_ats_provider(url: str) -> str:
    u = url.lower()
    if "greenhouse.io" in u:
        return "Greenhouse"
    if "lever.co" in u:
        return "Lever"
    if "smartrecruiters.com" in u:
        return "SmartRecruiters"
    if "myworkdayjobs.com" in u:
        return "Workday"
    if "welcometothejungle.com" in u:
        return "Welcome to the Jungle"
    if "jobteaser.com" in u:
        return "JobTeaser"
    if "indeed.com" in u:
        return "Indeed"
    if "apec.fr" in u:
        return "APEC"
    if "meteojob.com" in u:
        return "Meteojob"
    if "hellowork.com" in u:
        return "HelloWork"
    if "linkedin.com" in u:
        return "LinkedIn"
    if "taleo.net" in u:
        return "Taleo"
    return "External Portal"


async def _has_visible_modal(page: Page) -> bool:
    try:
        modal = page.locator(".jobs-easy-apply-modal, div[role='dialog'][aria-modal='true']").first
        return await modal.count() > 0 and await modal.is_visible(timeout=300)
    except Exception:
        return False


async def save_debug_screenshot(page: Page, company: str, suffix: str = "after_submit") -> Optional[Path]:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        safe_comp = re.sub(r"[^\w\-]+", "_", company or "offer").strip("_")[:50] or "offer"
        out_path = DEBUG_DIR / f"{safe_comp}_{suffix}.png"
        await page.screenshot(path=str(out_path), full_page=True, timeout=6000)
        return out_path
    except Exception as exc:
        _log.warning("Failed saving debug screenshot for %s: %s", company, exc)
        return None


async def check_submission_confirmed(
    page: Page,
    pre_submit_url: str = "",
    had_modal: bool = False,
    submit_ok_responses: Optional[List[str]] = None,
    adapter: Optional[SiteAdapter] = None,
) -> bool:
    try:
        if page.is_closed():
            return False
        active_adapter = adapter or resolve_adapter(page.url or "")
        if active_adapter.is_confirmation_url(page.url or ""):
            return True

        phrases = active_adapter.confirmation_phrases or GENERIC_ADAPTER.confirmation_phrases
        for _, ctx in await get_form_roots(page):
            try:
                body_text = (await ctx.locator("body").inner_text(timeout=2000)).lower()
                if any(phrase in body_text for phrase in phrases):
                    return True
            except Exception:
                continue

        if had_modal and not await _has_visible_modal(page):
            if submit_ok_responses:
                return True
            if not await collect_validation_errors(page):
                body_low = (await page.locator("body").inner_text(timeout=1500)).lower()
                if any(tok in body_low for tok in ("postulé", "applied", "candidature envoyée", "merci")):
                    return True

        if pre_submit_url and page.url != pre_submit_url and submit_ok_responses:
            if not await collect_validation_errors(page):
                return True

        return False
    except Exception as exc:
        _log.warning("check_submission_confirmed failed on %s: %s", getattr(page, "url", ""), exc)
        return False
