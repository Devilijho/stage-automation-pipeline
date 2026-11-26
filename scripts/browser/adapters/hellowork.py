"""HelloWork site adapter."""

from browser.adapters.base import (
    DEFAULT_APPLY_SELECTORS,
    DEFAULT_SUBMIT_SELECTORS,
    SiteAdapter,
)


class HelloWorkAdapter(SiteAdapter):
    key = "hellowork"
    name = "HelloWork"
    is_native_board = True
    url_patterns = ("hellowork.com",)

    form_root_selectors = ("#postuler", "div[role='dialog'][aria-modal='true']")
    apply_selectors = (
        "button[data-cy='applyButton']:visible",
        "a[data-cy='applyButton']:visible",
        *DEFAULT_APPLY_SELECTORS,
    )
    submit_selectors = (
        "button[data-cy='submitButton']",
        "#postuler button[type='submit']:visible",
        *DEFAULT_SUBMIT_SELECTORS,
    )
    confirmation_phrases = (
        "votre candidature a bien été envoyée",
        "votre candidature a été envoyée",
        "candidature bien envoyée",
        "candidature transmise",
        "nous avons bien reçu votre candidature",
    )
    ignored_confirmation_url_tokens = (
        "/bounce/createalert",
        "origin=responseoffer",
    )
