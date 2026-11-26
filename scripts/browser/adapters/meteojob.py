"""Meteojob site adapter."""

from browser.adapters.base import (
    DEFAULT_APPLY_SELECTORS,
    DEFAULT_SUBMIT_SELECTORS,
    SiteAdapter,
)


class MeteojobAdapter(SiteAdapter):
    key = "meteojob"
    name = "Meteojob"
    is_native_board = True
    url_patterns = ("meteojob.com",)

    apply_selectors = (
        "button:has-text('Candidater'):visible",
        "a:has-text('Candidater'):visible",
        *DEFAULT_APPLY_SELECTORS,
    )
    submit_selectors = DEFAULT_SUBMIT_SELECTORS
    confirmation_phrases = (
        "votre candidature a bien été envoyée",
        "votre candidature a été envoyée",
        "candidature envoyée",
        "candidature enregistrée",
    )
