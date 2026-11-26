"""APEC site adapter."""

from browser.adapters.base import (
    DEFAULT_APPLY_SELECTORS,
    DEFAULT_SUBMIT_SELECTORS,
    SiteAdapter,
)


class APECAdapter(SiteAdapter):
    key = "apec"
    name = "APEC"
    is_native_board = True
    url_patterns = ("apec.fr",)

    apply_selectors = (
        "button[data-testid*='apply' i]:visible",
        "a[data-testid*='apply' i]:visible",
        *DEFAULT_APPLY_SELECTORS,
    )
    submit_selectors = DEFAULT_SUBMIT_SELECTORS
    confirmation_phrases = (
        "votre candidature a bien été transmise",
        "votre candidature a été transmise",
        "candidature transmise",
        "votre candidature a été envoyée",
    )

    def is_login_wall(
        self,
        url: str,
        body_lower: str = "",
        has_password_input: bool = False,
    ) -> bool:
        if super().is_login_wall(url, body_lower=body_lower, has_password_input=has_password_input):
            return True
        return "mot de passe" in body_lower and any(
            k in body_lower for k in ("connectez-vous", "me connecter", "se connecter", "identifiant")
        )
