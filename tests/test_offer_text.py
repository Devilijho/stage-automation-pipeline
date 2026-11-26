"""Unit tests for site-agnostic role/company cleaning of scraped listing cards."""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.offer_text import (
    company_from_listing,
    company_prose,
    is_generic_company,
    sanitize_role,
    truncate_words,
)

APEC_CARD = (
    "Développeur - Développeuse Logiciel - Stage F/H Hellowork - Paris 01 - 75  Vos missions  "
    "Vous interviendrez en stage au sein des équipes de Capgemini Engineering à Paris... "
    "Stage Minimum légal Télétravail ponctuel Aucune expérience exigée Il y a 12 jours"
)


class SanitizeRoleTests(unittest.TestCase):
    def test_apec_card_collapses_to_title(self) -> None:
        self.assertEqual(sanitize_role(APEC_CARD), "Développeur Logiciel")

    def test_employer_after_gender_marker_is_dropped(self) -> None:
        self.assertEqual(
            sanitize_role("STAGE - Documentaliste/bibliothécaire F/H CINEMATHEQUE FRANCAISE - Paris 12 - 75 La Cinémathèque"),
            "STAGE - Documentaliste/bibliothécaire",
        )
        self.assertEqual(
            sanitize_role("Stage - Technicien Supérieur Systèmes et Réseaux H/F SPIE ICS"),
            "Stage - Technicien Supérieur Systèmes et Réseaux",
        )

    def test_clean_titles_stay_intact(self) -> None:
        self.assertEqual(sanitize_role("Stage Développeur C/C++ (H/F)"), "Stage Développeur C/C++")
        self.assertEqual(sanitize_role("Stage - Cybersécurité & IA"), "Stage - Cybersécurité & IA")
        self.assertEqual(sanitize_role("Stage Full-Stack Front-end / Back-end"), "Stage Full-Stack Front-end / Back-end")

    def test_gendered_doublets_collapse(self) -> None:
        self.assertEqual(sanitize_role("Développeuse - Développeur Web"), "Développeur Web")
        self.assertEqual(sanitize_role("Stage Ingénieure/Ingénieur Réseau"), "Stage Ingénieur Réseau")

    def test_unrecoverable_or_error_titles_return_empty(self) -> None:
        self.assertEqual(sanitize_role(""), "")
        self.assertEqual(sanitize_role("JavaScript is disabled"), "")
        self.assertEqual(sanitize_role("Stage"), "")

    def test_long_title_is_cut_on_word_boundary(self) -> None:
        out = sanitize_role("Stage - Développeur Backend - " + "Plateforme de paiement internationale et services associés " * 2)
        self.assertLessEqual(len(out), 70)
        self.assertFalse(out.endswith((" de", " et", "-")))

    def test_truncate_words_never_cuts_mid_word(self) -> None:
        self.assertEqual(truncate_words("Développeur Logiciel Embarqué", 22), "Développeur Logiciel")


class CompanyTests(unittest.TestCase):
    def test_generic_companies(self) -> None:
        for name in ("APEC", "Hellowork", "Indeed", "Recruteur anonyme", "Entreprise_Cible", "Welcome to the Jungle", ""):
            self.assertTrue(is_generic_company(name), name)
        for name in ("Thales", "Capgemini Engineering", "CINEMATHEQUE FRANCAISE"):
            self.assertFalse(is_generic_company(name), name)

    def test_company_prose_is_neutral_when_unknown(self) -> None:
        self.assertEqual(company_prose("APEC"), "votre entreprise")
        self.assertEqual(company_prose("Thales"), "Thales")

    def test_company_from_listing(self) -> None:
        self.assertEqual(company_from_listing("Stage - Analyste F/H DYNATOS DESIGN - Saint-Denis - 93 Texte"), "DYNATOS DESIGN")
        self.assertEqual(company_from_listing("Développeur Logiciel F/H Hellowork - Paris 01 - 75 Vos missions"), "")
        self.assertEqual(company_from_listing("Stage Développeur Python"), "")
        self.assertEqual(company_from_listing("Stage X (H/F/X) - Mode - Fin"), "")


if __name__ == "__main__":
    unittest.main()
