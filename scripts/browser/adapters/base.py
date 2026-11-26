"""Base SiteAdapter and GenericAdapter for external ATS portals."""

from typing import Tuple

from playwright.async_api import Page

DEFAULT_CONFIRMATION_PHRASES: Tuple[str, ...] = (
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

DEFAULT_EXPIRED_PHRASES: Tuple[str, ...] = (
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
)

DEFAULT_CONFIRMATION_URL_TOKENS: Tuple[str, ...] = (
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

DEFAULT_LOGIN_URL_TOKENS: Tuple[str, ...] = (
    "authwall",
    "/login",
    "connexion",
    "signin",
    "se-connecter",
)

DEFAULT_APPLY_SELECTORS: Tuple[str, ...] = (
    "button:has-text('Postuler sur le site'):visible",
    "a:has-text('Postuler sur le site'):visible",
    "button:has-text('Postuler'):visible",
    "a:has-text('Postuler'):visible",
    "a[role='button']:has-text('Postuler'):visible",
    "button:has-text('Apply'):visible",
    "a:has-text('Apply'):visible",
    "a[role='button']:has-text('Apply'):visible",
)

DEFAULT_SECONDARY_APPLY_SELECTORS: Tuple[str, ...] = (
    "a:has-text('Apply for this job'):visible",
    "a:has-text('Postuler à ce poste'):visible",
    "button:has-text('Apply for this job'):visible",
    "button:has-text('Postuler à ce poste'):visible",
    "button:has-text('Postulez maintenant'):visible",
    "a:has-text('Postulez maintenant'):visible",
    "button:has-text('Postuler maintenant'):visible",
    "a:has-text('Postuler maintenant'):visible",
    "button:has-text('Apply Now'):visible",
    "a:has-text('Apply Now'):visible",
    "button:has-text('APPLY'):visible",
    "a:has-text('APPLY'):visible",
    "a.postings-btn[href*='/apply']:visible",
    "button[data-testid*='apply' i]:visible",
    "a[data-testid*='apply' i]:visible",
)

DEFAULT_NEXT_SELECTORS: Tuple[str, ...] = (
    "button[aria-label*='étape suivante' i]",
    "button[aria-label*='next step' i]",
    "button[aria-label*='Vérifier' i]",
    "button[aria-label*='Review' i]",
    "button:has-text('Save and Continue')",
    "button:has-text('Enregistrer et continuer')",
    "button:has-text('Suivant')",
    "a[role='button']:has-text('Suivant')",
    "button:has-text('Vérifier')",
    "button:has-text('Continuer')",
    "a[role='button']:has-text('Continuer')",
    "button:has-text('Continue')",
    "a[role='button']:has-text('Continue')",
    "button:has-text('Next')",
    "a[role='button']:has-text('Next')",
    "button:has-text('Review')",
    "button:has-text('Valider')",
)

DEFAULT_SUBMIT_SELECTORS: Tuple[str, ...] = (
    "button[aria-label*='Envoyer la candidature' i]",
    "button[aria-label*='Submit application' i]",
    "button:has-text('Envoyer la candidature')",
    "button:has-text('Envoyer ma candidature')",
    "button:has-text('Valider ma candidature')",
    "button:has-text('Je postule')",
    "button:has-text('Postuler maintenant')",
    "button:has-text('Submit application')",
    "button:has-text('Envoyer')",
    "a[role='button']:has-text('Envoyer')",
    "button:has-text('Valider')",
    "a[role='button']:has-text('Valider')",
    "button:has-text('Soumettre')",
    "button:has-text('Submit')",
    "a[role='button']:has-text('Submit')",
    "input[type='submit']:visible",
    "form button[type='submit']:visible",
    "button:has-text('Postuler')",
    "a[role='button']:has-text('Postuler')",
)

DEFAULT_BAD_BUTTON_TOKENS: Tuple[str, ...] = (
    "rechercher",
    "search",
    "connexion",
    "connecter",
    "login",
    "sign in",
    "créer un compte",
    "alerte",
    "alert",
    "newsletter",
    "annuler",
    "cancel",
    "cookie",
    "refuser",
    "fermer",
    "close",
    "suivant",
    "next",
    "continue",
    "continuer",
    "précédent",
    "previous",
    "back",
    "retour",
    "review",
    "vérifier",
    "site du recruteur",
    "site de l'entreprise",
    "site de l’entreprise",
    "sur le site",
)


class SiteAdapter:
    """Declarative selectors, detection rules, and lifecycle hooks for a job site or external ATS."""

    key: str = "generic"
    name: str = "External Portal"
    is_native_board: bool = False
    url_patterns: Tuple[str, ...] = ()

    form_root_selectors: Tuple[str, ...] = ("div[role='dialog'][aria-modal='true']",)
    apply_wait_selector: str = (
        "button:has-text('Postuler'), a:has-text('Postuler'), button:has-text('Apply'), a:has-text('Apply')"
    )
    apply_selectors: Tuple[str, ...] = DEFAULT_APPLY_SELECTORS
    secondary_apply_selectors: Tuple[str, ...] = DEFAULT_SECONDARY_APPLY_SELECTORS
    next_selectors: Tuple[str, ...] = DEFAULT_NEXT_SELECTORS
    submit_selectors: Tuple[str, ...] = DEFAULT_SUBMIT_SELECTORS
    bad_button_tokens: Tuple[str, ...] = DEFAULT_BAD_BUTTON_TOKENS

    confirmation_phrases: Tuple[str, ...] = DEFAULT_CONFIRMATION_PHRASES
    confirmation_url_tokens: Tuple[str, ...] = DEFAULT_CONFIRMATION_URL_TOKENS
    ignored_confirmation_url_tokens: Tuple[str, ...] = ()
    login_url_tokens: Tuple[str, ...] = DEFAULT_LOGIN_URL_TOKENS
    expired_phrases: Tuple[str, ...] = DEFAULT_EXPIRED_PHRASES

    def matches_url(self, url: str) -> bool:
        u = (url or "").lower()
        return any(pat in u for pat in self.url_patterns)

    def normalize_url(self, url: str) -> str:
        return url

    def is_expired_redirect(self, current_url: str, original_url: str) -> bool:
        return False

    def is_confirmation_url(self, url: str) -> bool:
        u = (url or "").lower()
        if any(tok in u for tok in self.ignored_confirmation_url_tokens):
            return False
        return any(tok in u for tok in self.confirmation_url_tokens)

    def is_expired_text(self, body_lower: str) -> bool:
        return any(phrase in body_lower for phrase in self.expired_phrases)

    def is_login_wall(
        self,
        url: str,
        body_lower: str = "",
        has_password_input: bool = False,
    ) -> bool:
        u = (url or "").lower()
        if any(tok in u for tok in self.login_url_tokens):
            return True
        if has_password_input:
            return True
        if "vous avez déjà un compte" in body_lower and "mot de passe" in body_lower:
            return True
        return False

    def is_in_page_apply_selector(self, selector: str) -> bool:
        return False

    async def after_apply_click(self, page: Page) -> None:
        return None

    async def before_step_fill(self, page: Page) -> None:
        return None

    async def before_next_step(self, page: Page) -> None:
        try:
            open_opt = page.locator(
                ".basic-typeahead__selectable:visible, .artdeco-typeahead__result:visible, [role='listbox'] [role='option']:visible"
            ).first
            if await open_opt.count() > 0 and await open_opt.is_visible(timeout=200):
                await open_opt.click()
                await page.wait_for_timeout(200)
        except Exception:
            pass

    async def before_submit(self, page: Page) -> None:
        return None


class GenericAdapter(SiteAdapter):
    """Fallback adapter for external ATS portals (Workday, Greenhouse, Lever, SmartRecruiters, etc.)."""

    key = "generic"
    name = "External Portal"
    is_native_board = False
