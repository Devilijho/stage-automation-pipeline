"""JobTeaser site adapter."""

from browser.adapters.base import (
    DEFAULT_APPLY_SELECTORS,
    DEFAULT_LOGIN_URL_TOKENS,
    DEFAULT_SUBMIT_SELECTORS,
    SiteAdapter,
)


class JobTeaserAdapter(SiteAdapter):
    key = "jobteaser"
    name = "JobTeaser"
    is_native_board = True
    url_patterns = ("jobteaser.com",)

    apply_selectors = (
        "button[data-testid*='apply' i]:visible",
        "a[data-testid*='apply' i]:visible",
        *DEFAULT_APPLY_SELECTORS,
    )
    submit_selectors = DEFAULT_SUBMIT_SELECTORS
    login_url_tokens = (
        *DEFAULT_LOGIN_URL_TOKENS,
        "users/sign_in",
    )
    confirmation_phrases = (
        "votre candidature a bien été envoyée",
        "votre candidature a été envoyée",
        "candidature enregistrée",
        "candidature envoyée",
        "your application has been submitted",
    )
