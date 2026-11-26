"""Unit tests for tailor.py pure helpers (escaping, archetype detection, markers, keywords)."""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tailor import (
    clean_role_for_prose,
    detect_role_archetype,
    extract_matched_keywords,
    latex_escape,
    replace_marker_block,
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
        verified = {"C++", "Docker", "Python"}
        matched = extract_matched_keywords("Nous utilisons Python, Docker et Kubernetes.", verified)
        self.assertIn("Python", matched)
        self.assertIn("Docker", matched)
        self.assertNotIn("Kubernetes", matched)


if __name__ == "__main__":
    unittest.main()
