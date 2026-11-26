"""ATS provider detection, login URLs, submission confirmation, and expired offer checks."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from playwright.async_api import Page

from browser.dom_helpers import get_form_roots
from browser.navigation import collect_validation_errors
from core.config import DEBUG_DIR

SITE_CONFIRMATION_PHRASES: Dict[str, Tuple[str, ...]] = {
    "LinkedIn": (
        "your application was sent",
        "votre candidature a été envoyée",
        "application sent",
        "candidature envoyée",
    ),
    "HelloWork": (
        "votre candidature a bien été envoyée",
        "votre candidature a été envoyée",
        "candidature bien envoyée",
        "candidature transmise",
        "nous avons bien reçu votre candidature",
    ),
    "Welcome to the Jungle": (
        "votre candidature a été envoyée",
        "candidature envoyée",
        "candidature bien envoyée",
        "your application has been sent",
        "application sent",
    ),
    "JobTeaser": (
        "votre candidature a bien été envoyée",
        "votre candidature a été envoyée",
        "candidature enregistrée",
        "candidature envoyée",
        "your application has been submitted",
    ),
    "Indeed": (
        "your application has been submitted",
        "votre candidature a été envoyée",
        "candidature envoyée",
        "application submitted",
    ),
    "APEC": (
        "votre candidature a bien été transmise",
        "votre candidature a été transmise",
        "candidature transmise",
        "votre candidature a été envoyée",
    ),
    "Meteojob": (
        "votre candidature a bien été envoyée",
        "votre candidature a été envoyée",
        "candidature envoyée",
        "candidature enregistrée",
    ),
}

CONFIRMATION_PHRASES_EXTERNAL: Tuple[str, ...] = (
    "votre candidature a été envoyée",
    "votre candidature a bien été",
    "votre candidature a été transmise",
    "candidature bien envoyée",
    "candidature envoyée",
    "candidature transmise",
    "candidature prise en compte",
    "nous avons bien reçu votre candidature",
    "félicitations, votre candidature",
    "application sent",
    "application submitted",
    "your application was sent",
    "your application has been submitted",
    "we have received your application",
    "received your application",
    "merci pour votre candidature",
    "thank you for applying",
    "thank you for your application",
    "candidature enregistrée",
)

EXPIRED_OFFER_PHRASES = [
    "offre expirée",
    "cette offre a expiré",
    "n'est plus disponible",
    "n’est plus disponible",
    "cette offre n'est plus",
    "cette offre n’est plus",
    "poste pourvu",
    "offre pourvue",
    "les candidatures ne sont plus acceptées",
    "no longer accepting applications",
    "this job has expired",
    "job has expired",
    "no longer available",
    "expired",
]

LOGIN_URLS: Dict[str, Tuple[str, str]] = {
    "linkedin": ("LinkedIn", "https://www.linkedin.com/login"),
    "hellowork": ("HelloWork", "https://www.hellowork.com/fr-fr/candidat/connexion-inscription.html"),
    "wttj": ("Welcome to the Jungle", "https://www.welcometothejungle.com/fr"),
    "jobteaser": ("JobTeaser", "https://www.jobteaser.com/fr/users/sign_in"),
    "indeed": ("Indeed", "https://fr.indeed.com/account/login"),
    "apec": ("APEC", "https://www.apec.fr"),
    "meteojob": ("Meteojob", "https://www.meteojob.com/"),
}

CONFIRMATION_URL_TOKENS = (
    "/jobs/apply/confirm",
    "/apply/confirm",
    "/application/confirm",
    "/confirmation",
    "/thank-you",
    "/thankyou",
    "/merci",
    "applied=true",
    "submitted=true",
)


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
    except Exception:
        return None


async def check_submission_confirmed(
    page: Page,
    pre_submit_url: str = "",
    had_modal: bool = False,
    submit_ok_responses: Optional[List[str]] = None,
) -> bool:
    try:
        if page.is_closed():
            return False
        u = (page.url or "").lower()
        if any(tok in u for tok in CONFIRMATION_URL_TOKENS):
            return True

        site_name = detect_ats_provider(page.url)
        phrases = SITE_CONFIRMATION_PHRASES.get(site_name, CONFIRMATION_PHRASES_EXTERNAL)
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
    except Exception:
        return False
