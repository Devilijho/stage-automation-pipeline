"""LinkedIn Easy Apply & redirect site adapter."""

from playwright.async_api import Page

from browser.adapters.base import (
    DEFAULT_APPLY_SELECTORS,
    DEFAULT_NEXT_SELECTORS,
    DEFAULT_SUBMIT_SELECTORS,
    SiteAdapter,
)


class LinkedInAdapter(SiteAdapter):
    key = "linkedin"
    name = "LinkedIn"
    is_native_board = True
    url_patterns = ("linkedin.com",)

    form_root_selectors = (".jobs-easy-apply-modal", "div[role='dialog'][aria-modal='true']")
    apply_wait_selector = (
        "button.jobs-apply-button, button:has-text('Candidature simplifiée'), "
        "button:has-text('Easy Apply'), button:has-text('Postuler'), button:has-text('Apply')"
    )
    apply_selectors = (
        "button.jobs-apply-button:visible",
        "button[aria-label*='Candidature simplifiée' i]:visible",
        "button[aria-label*='Easy Apply' i]:visible",
        "button:has-text('Candidature simplifiée'):visible",
        "button:has-text('Easy Apply'):visible",
        *DEFAULT_APPLY_SELECTORS,
    )
    next_selectors = (
        "button[data-easy-apply-next-button]",
        *DEFAULT_NEXT_SELECTORS,
    )
    submit_selectors = DEFAULT_SUBMIT_SELECTORS
    confirmation_phrases = (
        "your application was sent",
        "votre candidature a été envoyée",
        "application sent",
        "candidature envoyée",
    )

    def normalize_url(self, url: str) -> str:
        return url.replace("https://fr.linkedin.com/jobs/view/", "https://www.linkedin.com/jobs/view/")

    def is_expired_redirect(self, current_url: str, original_url: str) -> bool:
        return "linkedin.com/jobs/search" in (current_url or "").lower() and "/jobs/view/" in (original_url or "").lower()

    def is_in_page_apply_selector(self, selector: str) -> bool:
        low = selector.lower()
        return ("jobs-apply-button" in low) or ("easy apply" in low) or ("candidature simplifiée" in low)

    async def after_apply_click(self, page: Page) -> None:
        if "linkedin.com/jobs/view" not in (page.url or "").lower():
            return
        try:
            modal = page.locator(".jobs-easy-apply-modal, div[role='dialog'][aria-modal='true']").first
            if await modal.count() > 0 and await modal.is_visible(timeout=300):
                return
            await page.wait_for_timeout(1000)
            if await modal.count() > 0 and await modal.is_visible(timeout=300):
                return
            retry_btn = page.locator("button.jobs-apply-button:visible").first
            if await retry_btn.is_visible(timeout=1000):
                await retry_btn.click()
                await page.wait_for_timeout(1500)
        except Exception:
            pass

    async def before_step_fill(self, page: Page) -> None:
        from browser.form_filler import _prune_blank_linkedin_experience

        await _prune_blank_linkedin_experience(page)

    async def before_next_step(self, page: Page) -> None:
        try:
            discard_btn = page.locator(
                "div[role='dialog']:has-text('Save this application') button[aria-label='Dismiss'], "
                "div[role='dialog']:has-text('Enregistrer cette candidature') button[aria-label='Ignorer']"
            ).first
            if await discard_btn.count() > 0 and await discard_btn.is_visible(timeout=200):
                await discard_btn.click()
                await page.wait_for_timeout(300)
        except Exception:
            pass
        await super().before_next_step(page)
        try:
            del_exp = page.locator(
                "button:has-text('Delete experience'):visible, button:has-text('Supprimer l’expérience'):visible"
            ).first
            save_exp = page.locator("button:has-text('Save'):visible, button:has-text('Enregistrer'):visible").first
            if await del_exp.count() > 0 and await save_exp.count() > 0 and await save_exp.is_enabled():
                await save_exp.click()
                await page.wait_for_timeout(600)
        except Exception:
            pass

    async def before_submit(self, page: Page) -> None:
        from browser.navigation import uncheck_follow_company

        await uncheck_follow_company(page)
