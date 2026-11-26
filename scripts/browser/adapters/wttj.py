"""Welcome to the Jungle (WTTJ) site adapter."""

from browser.adapters.base import (
    DEFAULT_APPLY_SELECTORS,
    DEFAULT_SUBMIT_SELECTORS,
    SiteAdapter,
)


class WTTJAdapter(SiteAdapter):
    key = "wttj"
    name = "Welcome to the Jungle"
    is_native_board = True
    url_patterns = ("welcometothejungle.com",)

    form_root_selectors = (
        "div[role='dialog'][aria-modal='true']",
        "form[data-testid='apply-form']",
    )
    apply_selectors = (
        "[data-testid='job_header-button-apply']:visible",
        "[data-testid='job_bottom-button-apply']:visible",
        *DEFAULT_APPLY_SELECTORS,
    )
    submit_selectors = (
        "button[data-testid='apply-form-submit']",
        *DEFAULT_SUBMIT_SELECTORS,
    )
    confirmation_phrases = (
        "votre candidature a été envoyée",
        "candidature envoyée",
        "candidature bien envoyée",
        "your application has been sent",
        "application sent",
    )
