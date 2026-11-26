"""Unit tests for batch_apply offer scoring, PFE filtering, and company cleaning."""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from batch_apply import (
    classify_offer_quality,
    clean_company_name,
    is_excluded_final_year_offer,
    score_offer,
)
from core.config import DEFAULT_PREFERENCES


class ScoringTests(unittest.TestCase):
    def test_semicolon_matched_keywords_are_counted(self) -> None:
        prefs = {k: [x.lower() for x in v] for k, v in DEFAULT_PREFERENCES.items()}
        _, sc_one = classify_offer_quality(
            "Stagiaire",
            matched_keywords="Python",
            prefs=prefs,
        )
        _, sc_five = classify_offer_quality(
            "Stagiaire",
            matched_keywords="Python; Docker; Linux; SQL; TypeScript",
            prefs=prefs,
        )
        self.assertGreater(sc_five, sc_one)
        self.assertEqual(sc_five - sc_one, 12)  # 5*3=15 vs 1*3=3

    def test_quality_tiers(self) -> None:
        prefs = {k: [x.lower() for x in v] for k, v in DEFAULT_PREFERENCES.items()}
        tier_hi, sc_hi = classify_offer_quality(
            "Stage Développeur Full-Stack (H/F)",
            location="Orsay",
            description="Stage BUT 2 / Bac+2 télétravail hybride",
            matched_keywords="TypeScript; React; Docker",
            prefs=prefs,
        )
        self.assertEqual(tier_hi, "high")
        self.assertGreaterEqual(sc_hi, 40)

        tier_lo, sc_lo = classify_offer_quality(
            "Stage Ressources Humaines Paie",
            location="Lyon",
            prefs=prefs,
        )
        self.assertEqual(tier_lo, "low")
        self.assertLess(sc_lo, 0)

    def test_pfe_exclusion_and_but_exception(self) -> None:
        self.assertTrue(is_excluded_final_year_offer("Stage PFE Cybersécurité Bac+5"))
        self.assertFalse(is_excluded_final_year_offer("Stage PFE ou BUT 2 / Bac+2 Cybersécurité"))
        self.assertEqual(score_offer("Stage PFE Ingénieur", "Paris", "Stage de fin d'études Master 2"), -50)
        self.assertEqual(score_offer("Page introuvable", "Paris", ""), -100)

    def test_clean_company_name_strips_cta_boilerplate(self) -> None:
        self.assertEqual(clean_company_name("Chez Thales"), "Thales")
        self.assertEqual(clean_company_name("Postuler sur le site de l'entreprise"), "")
        self.assertEqual(clean_company_name("Voir toutes les offres de Capgemini"), "Capgemini")


if __name__ == "__main__":
    unittest.main()
