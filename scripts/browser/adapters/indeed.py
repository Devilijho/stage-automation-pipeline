"""Indeed & SmartApply site adapter."""

from browser.adapters.base import (
    DEFAULT_APPLY_SELECTORS,
    DEFAULT_NEXT_SELECTORS,
    DEFAULT_SUBMIT_SELECTORS,
    SiteAdapter,
)


class IndeedAdapter(SiteAdapter):
    key = "indeed"
    name = "Indeed"
    is_native_board = True
    url_patterns = ("indeed.com",)

    apply_selectors = (
        "#indeedApplyButton:visible",
        "button[id*='indeedApply' i]:visible",
        "button:has-text('Postuler maintenant'):visible",
        "button:has-text('Apply now'):visible",
        *DEFAULT_APPLY_SELECTORS,
    )
    next_selectors = (
        "button[data-testid*='continue' i]",
        *DEFAULT_NEXT_SELECTORS,
    )
    submit_selectors = (
        "button[data-testid*='submit' i]",
        "button:has-text('Déposer ma candidature')",
        "button:has-text('Submit your application')",
        *DEFAULT_SUBMIT_SELECTORS,
    )
    confirmation_phrases = (
        "your application has been submitted",
        "votre candidature a été envoyée",
        "candidature envoyée",
        "application submitted",
    )
