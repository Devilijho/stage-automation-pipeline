"""Offline unit tests for browser site adapters and URL registry."""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from browser.adapters import (
    GENERIC_ADAPTER,
    SITE_ADAPTERS,
    resolve_adapter,
)
from browser.ats_rules import SITE_CONFIRMATION_PHRASES


class SiteAdapterTests(unittest.TestCase):
    def test_resolve_all_seven_native_boards_and_generic_fallback(self) -> None:
        cases = [
            ("https://www.linkedin.com/jobs/view/123456/", "linkedin", True),
            ("https://www.hellowork.com/fr-fr/emplois/98765.html", "hellowork", True),
            ("https://www.welcometothejungle.com/fr/companies/acme/jobs/dev", "wttj", True),
            ("https://www.jobteaser.com/fr/job-offers/1111", "jobteaser", True),
            ("https://fr.indeed.com/viewjob?jk=abc123", "indeed", True),
            ("https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/123", "apec", True),
            ("https://www.meteojob.com/candidat/offres/emploi-123", "meteojob", True),
            ("https://acme.myworkdayjobs.com/en-US/Careers/job/Dev", "generic", False),
            ("https://boards.greenhouse.io/acme/jobs/42", "generic", False),
            ("https://careers.example.org/apply", "generic", False),
        ]
        for url, expected_key, expected_native in cases:
            adapter = resolve_adapter(url)
            self.assertEqual(adapter.key, expected_key, f"Failed for {url}")
            self.assertEqual(adapter.is_native_board, expected_native, f"Failed native flag for {url}")
        self.assertIs(resolve_adapter("https://unknown.example.com"), GENERIC_ADAPTER)
        self.assertEqual(len(SITE_ADAPTERS), 7)

    def test_linkedin_url_normalization_and_expired_redirect(self) -> None:
        li = resolve_adapter("https://fr.linkedin.com/jobs/view/999/")
        self.assertEqual(
            li.normalize_url("https://fr.linkedin.com/jobs/view/999/"),
            "https://www.linkedin.com/jobs/view/999/",
        )
        self.assertTrue(
            li.is_expired_redirect(
                "https://www.linkedin.com/jobs/search/?keywords=stage",
                "https://www.linkedin.com/jobs/view/999/",
            )
        )
        self.assertFalse(
            li.is_expired_redirect(
                "https://www.linkedin.com/jobs/view/999/",
                "https://www.linkedin.com/jobs/view/999/",
            )
        )

    def test_hellowork_ignores_bounce_alert_confirmation_urls(self) -> None:
        hw = resolve_adapter("https://www.hellowork.com/fr-fr/emplois/123.html")
        self.assertFalse(
            hw.is_confirmation_url("https://www.hellowork.com/fr-fr/bounce/createalert?applied=true")
        )
        self.assertFalse(
            hw.is_confirmation_url("https://www.hellowork.com/fr-fr/confirmation?origin=responseoffer")
        )
        self.assertTrue(
            hw.is_confirmation_url("https://www.hellowork.com/fr-fr/candidat/confirmation")
        )

    def test_apec_and_jobteaser_login_wall_detection(self) -> None:
        apec = resolve_adapter("https://www.apec.fr/candidat/emploi/123")
        self.assertTrue(
            apec.is_login_wall(
                "https://www.apec.fr/candidat/emploi/123",
                body_lower="connectez-vous avec votre identifiant et mot de passe",
            )
        )
        self.assertFalse(
            apec.is_login_wall(
                "https://www.apec.fr/candidat/emploi/123",
                body_lower="postuler à cette offre de stage",
            )
        )
        jt = resolve_adapter("https://www.jobteaser.com/fr/users/sign_in")
        self.assertTrue(jt.is_login_wall("https://www.jobteaser.com/fr/users/sign_in"))

    def test_ats_rules_confirmation_phrases_stay_synced_with_adapters(self) -> None:
        for adapter in SITE_ADAPTERS:
            self.assertIn(adapter.name, SITE_CONFIRMATION_PHRASES)
            self.assertEqual(SITE_CONFIRMATION_PHRASES[adapter.name], adapter.confirmation_phrases)


if __name__ == "__main__":
    unittest.main()
