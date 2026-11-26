"""Unit tests for core.storage (locking, deduplication, status protection, bulk updates)."""

import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.storage import (
    bulk_update_application_status,
    check_and_log_application,
    detect_source,
    load_existing_offers,
    normalize_company_role_key,
    normalize_url,
    read_applications,
    refresh_application,
    update_application_status,
)


class StorageTests(unittest.TestCase):
    def test_normalize_and_detect_source(self) -> None:
        self.assertEqual(normalize_url(" https://www.linkedin.com/jobs/view/123/ "), "https://www.linkedin.com/jobs/view/123")
        self.assertEqual(
            normalize_company_role_key("Thales!", "Stage Développeur C++ (H/F)"),
            normalize_company_role_key("Thales", "Stage Développeur C++ F/H"),
        )
        self.assertEqual(detect_source("https://fr.linkedin.com/jobs/view/1"), "LinkedIn")
        self.assertEqual(detect_source("https://www.hellowork.com/fr-fr/emplois/1.html"), "HelloWork")
        self.assertEqual(detect_source("https://www.welcometothejungle.com/fr/companies/x/jobs/y"), "WTTJ")
        self.assertEqual(detect_source("https://example.org/careers"), "Other")

    def test_dedup_and_status_protection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            csv_path = ws / "data" / "applications.csv"
            cv_pdf = ws / "output" / "Acme" / "CV_Test_Acme.pdf"
            lm_pdf = ws / "output" / "Acme" / "LM_Test_Acme.pdf"

            added = check_and_log_application(
                "Acme Corp",
                "Stage Développeur (H/F)",
                "https://www.hellowork.com/fr-fr/emplois/123.html/",
                "Stage -- Stagiaire Développeur",
                ["Python", "Docker", "SQL"],
                cv_pdf,
                lm_pdf,
                path=csv_path,
                workspace=ws,
            )
            self.assertTrue(added)

            # Duplicate URL (without trailing slash)
            dup_url = check_and_log_application(
                "Acme Corp",
                "Autre Titre",
                "https://www.hellowork.com/fr-fr/emplois/123.html",
                "Stage",
                ["Python"],
                cv_pdf,
                lm_pdf,
                path=csv_path,
                workspace=ws,
            )
            self.assertFalse(dup_url)

            # Cross-site duplicate by normalized (company, role)
            dup_pair = check_and_log_application(
                "Acme Corp",
                "Stage Développeur F/H",
                "https://fr.linkedin.com/jobs/view/999",
                "Stage",
                ["Python"],
                cv_pdf,
                lm_pdf,
                path=csv_path,
                workspace=ws,
            )
            self.assertFalse(dup_pair)

            # Promote to applied, then verify force=False protects applied from downgrade
            self.assertTrue(
                update_application_status(
                    "https://www.hellowork.com/fr-fr/emplois/123.html",
                    "manual_portal",
                    force=False,
                    notes="Stuck on step 2",
                    path=csv_path,
                )
            )
            self.assertEqual(read_applications(csv_path)[0]["Notes"], "Stuck on step 2")
            self.assertTrue(
                update_application_status(
                    "https://www.hellowork.com/fr-fr/emplois/123.html",
                    "applied",
                    force=False,
                    notes="",
                    path=csv_path,
                )
            )
            self.assertEqual(read_applications(csv_path)[0]["Notes"], "")
            self.assertFalse(
                update_application_status(
                    "https://www.hellowork.com/fr-fr/emplois/123.html",
                    "manual_portal",
                    force=False,
                    notes="Should not overwrite",
                    path=csv_path,
                )
            )
            rows = read_applications(csv_path)
            self.assertEqual(rows[0]["Status"], "applied")
            self.assertEqual(rows[0]["CV_PDF"], "output/Acme/CV_Test_Acme.pdf")

            # Manual UI override with force=True succeeds
            self.assertTrue(
                update_application_status(
                    "https://www.hellowork.com/fr-fr/emplois/123.html",
                    "skipped",
                    force=True,
                    path=csv_path,
                )
            )
            self.assertEqual(read_applications(csv_path)[0]["Status"], "skipped")

    def test_bulk_update_and_concurrent_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            csv_path = ws / "data" / "applications.csv"

            def _worker(i: int) -> bool:
                return check_and_log_application(
                    f"Company_{i}",
                    f"Stage Dev {i}",
                    f"https://example.com/job/{i}",
                    "Stage",
                    ["C++", "Linux"],
                    ws / "output" / f"C_{i}" / "CV.pdf",
                    ws / "output" / f"C_{i}" / "LM.pdf",
                    path=csv_path,
                    workspace=ws,
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(_worker, range(25)))
            self.assertEqual(sum(results), 25)
            urls, keys = load_existing_offers(csv_path)
            self.assertEqual(len(urls), 25)
            self.assertEqual(len(keys), 25)

            updated = bulk_update_application_status(
                [f"https://example.com/job/{i}" for i in range(10)],
                "skipped",
                path=csv_path,
            )
            self.assertEqual(updated, 10)
            rows = read_applications(csv_path)
            self.assertEqual(sum(1 for r in rows if r["Status"] == "skipped"), 10)

    def test_refresh_application_updates_in_place_and_keeps_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            csv_path = ws / "data" / "applications.csv"
            kw = {"path": csv_path, "workspace": ws}
            url = "https://www.apec.fr/x/1"
            check_and_log_application("APEC", "blob " * 30, url, "t", ["C"], ws / "a/CV.pdf", ws / "a/LM.pdf", **kw)
            update_application_status(url, "manual_portal", path=csv_path)
            found, old = refresh_application(
                url + "/", "Capgemini", "Développeur Logiciel", "Stage -- Développeur Logiciel",
                ["C++", "Python"], ws / "b/CV.pdf", ws / "b/LM.pdf", **kw,
            )
            self.assertTrue(found)
            self.assertEqual(old, "a/CV.pdf")
            rows = read_applications(csv_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Status"], "manual_portal")
            self.assertEqual(rows[0]["Company"], "Capgemini")
            self.assertEqual(rows[0]["CV_PDF"], "b/CV.pdf")
            self.assertEqual(rows[0]["Matched_Keywords"], "C++; Python")
            self.assertEqual(refresh_application("https://other/9", "X", "Y", "t", [], "c", "l", **kw), (False, ""))


if __name__ == "__main__":
    unittest.main()
