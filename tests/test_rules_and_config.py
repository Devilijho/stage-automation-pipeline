"""Unit tests for apply_browser rules, ATS detection, live config reloads, and LLM cache."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from apply_browser import classify_yes_no_question, detect_ats_provider
from core.config import load_custom_rules, load_preferences, match_custom_rule, save_profile
from core.llm import load_llm_cache, save_llm_cache


class RulesAndConfigTests(unittest.TestCase):
    def test_classify_yes_no_question(self) -> None:
        skills = {"python", "docker", "c++", "frontend", "application web"}
        self.assertEqual(classify_yes_no_question("Do you require visa sponsorship?", skills), ("Non", "No"))
        self.assertEqual(classify_yes_no_question("Stage de fin d'études Bac+5 uniquement", skills), ("Non", "No"))
        self.assertEqual(classify_yes_no_question("Stage ouvert aux Bac+2 / BUT 2 ou Bac+5", skills), ("Oui", "Yes"))
        self.assertEqual(classify_yes_no_question("Maîtrisez-vous Docker ?", skills), ("Oui", "Yes"))
        self.assertEqual(classify_yes_no_question("Expérience en développement mobile Flutter ?", skills), ("Non", "No"))
        self.assertIsNone(classify_yes_no_question("Avez-vous déjà piloté un drone ?", skills))

    def test_detect_ats_provider(self) -> None:
        self.assertEqual(detect_ats_provider("https://boards.greenhouse.io/acme/jobs/1"), "Greenhouse")
        self.assertEqual(detect_ats_provider("https://www.hellowork.com/fr-fr/emplois/1.html"), "HelloWork")
        self.assertEqual(detect_ats_provider("https://acme.myworkdayjobs.com/fr-FR/ext"), "Workday")
        self.assertEqual(detect_ats_provider("https://custom.example.com/apply"), "External Portal")

    def test_preferences_and_custom_rules_reload_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prof_path = Path(tmp) / "profile.json"
            save_profile({"preferences": {"target_roles": ["Rust"]}, "custom_rules": [{"keyword": "permis b", "answer": "Non"}]}, prof_path)
            self.assertEqual(load_preferences(prof_path)["target_roles"], ["rust"])

            # Edit on disk and verify next call sees fresh values (no stale global)
            save_profile({"preferences": {"target_roles": ["Go"]}, "custom_rules": [{"keyword": "permis b", "answer": "Oui"}]}, prof_path)
            self.assertEqual(load_preferences(prof_path)["target_roles"], ["go"])
            rules = load_custom_rules(prof_path)
            self.assertEqual(match_custom_rule("Avez-vous le permis B ?", rules), "Oui")

    def test_llm_cache_roundtrip_and_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "llm_cache.json"
            save_llm_cache({"k1": "v1"}, cache_path)
            save_llm_cache({"k2": "v2"}, cache_path)
            loaded = load_llm_cache(cache_path)
            self.assertEqual(loaded, {"k1": "v1", "k2": "v2"})
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(len(raw), 2)

    def test_all_cli_modules_import_cleanly(self) -> None:
        import importlib

        for mod_name in (
            "apply_browser",
            "batch_apply",
            "spontaneous_mail",
            "sync_github",
            "tailor",
            "ui_server",
            "core",
            "core.browser_session",
            "core.config",
            "core.llm",
            "core.storage",
            "scrapers",
            "scrapers.boards",
            "scrapers.http_client",
            "scrapers.parsers",
            "scrapers.scoring",
            "browser",
            "browser.ats_rules",
            "browser.dom_helpers",
            "browser.form_filler",
            "browser.navigation",
            "browser.question_rules",
            "browser.runner",
        ):
            mod = importlib.import_module(mod_name)
            self.assertIsNotNone(mod)


if __name__ == "__main__":
    unittest.main()
