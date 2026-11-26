"""Unit tests for tailor.py pure helpers (escaping, archetype detection, markers, keywords)."""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tailor import (
    ARCHETYPE_ROLE_LABELS,
    CV_TITLE_MAX_LEN,
    build_cv_title,
    clean_role_for_prose,
    cv_quality_issues,
    detect_role_archetype,
    extract_matched_keywords,
    focus_job_text,
    latex_escape,
    replace_marker_block,
    sanitize_slug,
    select_and_format_projects,
)


class TailorTests(unittest.TestCase):
    def test_latex_escape(self) -> None:
        self.assertEqual(latex_escape("C & C++ 100% #1_a"), r"C \& C++ 100\% \#1\_a")
        self.assertEqual(latex_escape("{x}"), r"\{x\}")

    def test_clean_role_for_prose(self) -> None:
        self.assertEqual(clean_role_for_prose("Stage - Développeur Python (H/F)"), "Développeur Python")
        self.assertEqual(clean_role_for_prose("Stage de Cybersécurité F/H"), "Cybersécurité")
        self.assertEqual(clean_role_for_prose("Stagiaire Cybersécurité F/H"), "Stagiaire Cybersécurité")

    def test_detect_role_archetype(self) -> None:
        self.assertEqual(detect_role_archetype("Stage Analyste SOC / Cybersécurité", "", []), "cyber")
        self.assertEqual(detect_role_archetype("Stage DevOps & Cloud", "", []), "infra")
        self.assertEqual(detect_role_archetype("Stage Développeur Full-Stack React", "", []), "web")
        self.assertEqual(detect_role_archetype("Stage Programmation C++", "", ["C++"]), "systems")

    def test_replace_marker_block(self) -> None:
        src = "before\n% === CV_TITLE_START ===\nold\n% === CV_TITLE_END ===\nafter"
        out = replace_marker_block(src, "CV_TITLE", "new title")
        self.assertIn("new title", out)
        self.assertNotIn("\nold\n", out)
        with self.assertRaises(ValueError):
            replace_marker_block(src, "MISSING_BLOCK", "x")

    def test_extract_matched_keywords_strict_grounding(self) -> None:
        verified = {"C", "C++", "TypeScript", "Docker", "Python", "Frontend"}
        matched = extract_matched_keywords("Nous utilisons Python, Docker et Kubernetes.", verified)
        self.assertIn("Python", matched)
        self.assertIn("Docker", matched)
        self.assertNotIn("Kubernetes", matched)
        # Ensure short aliases ('ts', 'c', 'ui') do not false-match inside regular French words
        no_false = extract_matched_keywords("Conception et suivi de projets pour nos clients en C++.", verified)
        self.assertIn("C++", no_false)
        self.assertNotIn("TypeScript", no_false)
        self.assertNotIn("C", no_false)
        self.assertNotIn("Frontend", no_false)
        # Ensure standalone TS and C/C++ match accurately
        exact = extract_matched_keywords("Développement C/C++ et TS.", verified)
        self.assertIn("C", exact)
        self.assertIn("C++", exact)
        self.assertIn("TypeScript", exact)

    def test_single_letter_c_needs_programming_context(self) -> None:
        verified = {"C", "C++", "Python"}
        self.assertNotIn("C", extract_matched_keywords("C'est une équipe qui a de l'ambition. Option C retenue.", verified))
        self.assertIn("C", extract_matched_keywords("Compétences : C, Python et Git", verified))
        self.assertIn("C", extract_matched_keywords("Maîtrise du langage C exigée", verified))
        self.assertIn("C", extract_matched_keywords("Programmation en C sous Linux", verified))

    def test_weak_aliases_need_two_occurrences(self) -> None:
        verified = {"Cybersécurité", "Réseaux"}
        self.assertNotIn("Cybersécurité", extract_matched_keywords("Stage audit financier", verified))
        self.assertIn("Cybersécurité", extract_matched_keywords("Audit interne, audit de code", verified))
        self.assertIn("Cybersécurité", extract_matched_keywords("Pentest et sécurité applicative", verified))

    def test_focus_job_text_drops_similar_offers(self) -> None:
        body = "Développeur Python. " * 30
        text = body + "Offres similaires " + "Cybersécurité C++ Docker " * 10
        self.assertEqual(focus_job_text(text), body)
        self.assertEqual(focus_job_text("Offre courte"), "Offre courte")

    def test_build_cv_title(self) -> None:
        self.assertEqual(build_cv_title("Développeur Logiciel", []), "Stage -- Développeur Logiciel")
        self.assertEqual(build_cv_title("Stage - Analyste SOC", []), "Stage -- Analyste SOC")
        self.assertLessEqual(len(build_cv_title("Stage - " + "Développeur Backend " * 10, [])), CV_TITLE_MAX_LEN)

    def test_archetype_role_labels_cover_all_archetypes(self) -> None:
        self.assertEqual(set(ARCHETYPE_ROLE_LABELS), {"cyber", "infra", "web", "systems"})

    def test_cv_quality_issues_flags_scraped_noise(self) -> None:
        self.assertEqual(cv_quality_issues("Stage -- Développeur Logiciel", "Étudiant en BUT 2.", "Développeur Logiciel"), [])
        bad = cv_quality_issues("Stage -- Développeur Hellowork -- Paris 01 -- 75", "Il y a 12 jours", "")
        self.assertTrue(any("noise" in i for i in bad))

    def test_sanitize_slug_strips_accents(self) -> None:
        self.assertEqual(sanitize_slug("Développeur Sécurité"), "Developpeur_Securite")

    def test_project_selection_uses_archetype_to_break_ties(self) -> None:
        projects = [
            {"name": "A", "subtitle": "", "url": "", "bullets": ["x"], "search_blob": "zzz docker linux devops"},
            {"name": "B", "subtitle": "", "url": "", "bullets": ["x"], "search_blob": "zzz typescript react"},
        ]
        sel, _ = select_and_format_projects(projects, [], "texte neutre", archetype="web")
        self.assertEqual(sel[0]["name"], "B")
        sel, _ = select_and_format_projects(projects, [], "texte neutre", archetype="infra")
        self.assertEqual(sel[0]["name"], "A")


if __name__ == "__main__":
    unittest.main()
