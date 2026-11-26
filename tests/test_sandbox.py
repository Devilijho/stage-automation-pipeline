"""Offline unit tests for Debug Sandbox Mode (`core.sandbox`, `ui_server`, and UI header)."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from pypdf import PdfReader

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ui_server
from core.sandbox import (
    create_debug_offer_metadata,
    ensure_debug_sandbox,
    get_runtime_paths,
    resolve_offer_metadata_path,
)
from core.storage import read_applications
from scrapers.http_client import DomainRateLimiter
from scrapers.parsers import HttpRateLimitError, fetch_offer_details_http, linkedin_guest_detail_url
from scrapers.scoring import classify_offer_quality, score_offer, search_location_hw, search_location_li


class SandboxModeTests(unittest.TestCase):
    def test_runtime_paths_isolate_all_state_except_chrome_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            prod = get_runtime_paths(debug=False, workspace=ws)
            dbg = get_runtime_paths(debug=True, workspace=ws)

            self.assertFalse(prod.is_debug)
            self.assertTrue(dbg.is_debug)
            self.assertNotEqual(prod.profile_path, dbg.profile_path)
            self.assertNotEqual(prod.master_profile_path, dbg.master_profile_path)
            self.assertNotEqual(prod.apps_csv_path, dbg.apps_csv_path)
            self.assertNotEqual(prod.unanswered_questions_path, dbg.unanswered_questions_path)
            self.assertNotEqual(prod.llm_cache_path, dbg.llm_cache_path)
            self.assertNotEqual(prod.output_dir, dbg.output_dir)
            self.assertEqual(prod.chrome_profile_dir, dbg.chrome_profile_dir)

    def test_ensure_debug_sandbox_seeds_droit_profile_and_empty_pdfs_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            dbg = get_runtime_paths(debug=True, workspace=ws)
            ensure_debug_sandbox(dbg)

            self.assertTrue(dbg.profile_path.exists())
            self.assertTrue(dbg.master_profile_path.exists())
            self.assertTrue(dbg.empty_cv_pdf.exists())
            self.assertTrue(dbg.empty_lm_pdf.exists())

            self.assertEqual(len(PdfReader(str(dbg.empty_cv_pdf)).pages), 1)
            self.assertEqual(len(PdfReader(str(dbg.empty_lm_pdf)).pages), 1)

            prof = json.loads(dbg.profile_path.read_text(encoding="utf-8"))
            self.assertIn("Droit", prof["education"]["degree"])
            self.assertIn("stage droit", prof["search"]["queries"])

            # Scoring against the Droit profile keeps legal offers and drops unrelated IT offers.
            self.assertGreater(
                score_offer("Stage Juriste Droit des Contrats", "Paris", "Stage juridique droit des affaires L3", profile_path=dbg.profile_path),
                0,
            )
            self.assertLess(
                score_offer("Stage Développeur DevOps Python", "Paris", "Kubernetes CI/CD Python", profile_path=dbg.profile_path),
                0,
            )
            tier, _ = classify_offer_quality(
                "Stage Juriste Droit Social",
                location="Paris",
                description="Stage droit du travail L3 Paris",
                profile_path=dbg.profile_path,
            )
            self.assertEqual(tier, "high")

            # Custom user edits to debug profile must survive subsequent ensure_debug_sandbox calls.
            prof["personal"]["city"] = "Nanterre"
            dbg.profile_path.write_text(json.dumps(prof), encoding="utf-8")
            ensure_debug_sandbox(dbg)
            reloaded = json.loads(dbg.profile_path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["personal"]["city"], "Nanterre")

    def test_create_and_resolve_debug_offer_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            prod = get_runtime_paths(debug=False, workspace=ws)
            dbg = get_runtime_paths(debug=True, workspace=ws)

            mp1 = create_debug_offer_metadata(
                {"company": "Cabinet Alpha", "role": "Stage Juriste Contrats", "url": "https://fr.linkedin.com/jobs/view/1001/"},
                dbg,
                workspace=ws,
            )
            mp2 = create_debug_offer_metadata(
                {"company": "Cabinet Beta", "role": "Stage Droit Social", "url": "https://fr.linkedin.com/jobs/view/1002/"},
                dbg,
                workspace=ws,
            )
            self.assertIsNotNone(mp1)
            self.assertIsNotNone(mp2)
            self.assertNotEqual(mp1, mp2)

            # Prod CSV remains completely untouched.
            self.assertFalse(prod.apps_csv_path.exists())
            rows = read_applications(dbg.apps_csv_path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["CV_PDF"], "debug/sandbox/empty_cv.pdf")
            self.assertEqual(rows[0]["LM_PDF"], "debug/sandbox/empty_lm.pdf")

            # Resolve returns distinct metadata.json per offer URL even when sharing empty_cv.pdf.
            self.assertEqual(resolve_offer_metadata_path(rows[0], dbg, workspace=ws), mp1)
            self.assertEqual(resolve_offer_metadata_path(rows[1], dbg, workspace=ws), mp2)

    def test_ui_server_debug_sandbox_isolation_and_global_header_button(self) -> None:
        html = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
        dbg_idx = html.find('id="btn-debug-mode"')
        lang_idx = html.find('class="lang-bar"')
        self.assertGreater(dbg_idx, 0)
        self.assertLess(dbg_idx, lang_idx)
        self.assertNotIn('class="pill debug-toggle hidden"', html)

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            prod = get_runtime_paths(debug=False, workspace=ws)
            dbg = get_runtime_paths(debug=True, workspace=ws)
            prod.profile_path.parent.mkdir(parents=True, exist_ok=True)
            prod.profile_path.write_text(
                json.dumps({"personal": {"full_name": "Prod Candidate"}, "education": {"level": "BUT 2"}, "internship": {"start_date": "03/05", "end_date": "16/07"}, "form_answers": {"prenom": "Prod"}, "custom_rules": [], "preferences": {}}),
                encoding="utf-8",
            )
            ensure_debug_sandbox(dbg)
            create_debug_offer_metadata(
                {"company": "Cabinet Lex", "role": "Stage Juriste", "url": "https://fr.linkedin.com/jobs/view/7777/"},
                dbg,
                workspace=ws,
            )

            captured = []

            def _fake_paths(_parsed=None, body=None):
                is_dbg = False
                if isinstance(body, dict) and "debug" in body:
                    is_dbg = bool(body["debug"])
                elif _parsed is not None and "debug=1" in (_parsed.query or ""):
                    is_dbg = True
                return dbg if is_dbg else prod

            def _fake_bg(cmd, label):
                captured.append((cmd, label))
                return True

            with (
                mock.patch.object(ui_server, "_get_paths", side_effect=_fake_paths),
                mock.patch.object(ui_server, "run_background_script", side_effect=_fake_bg),
            ):
                srv = ThreadingHTTPServer(("127.0.0.1", 0), ui_server.StageHandler)
                port = srv.server_address[1]
                t = threading.Thread(target=srv.serve_forever, daemon=True)
                t.start()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profile", timeout=5) as r:
                        p_prod = json.loads(r.read().decode("utf-8"))
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profile?debug=1", timeout=5) as r:
                        p_dbg = json.loads(r.read().decode("utf-8"))
                    self.assertEqual(p_prod["profile"]["personal"]["full_name"], "Prod Candidate")
                    self.assertEqual(p_dbg["profile"]["personal"]["full_name"], "Camille MOREAU")

                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/applications", timeout=5) as r:
                        a_prod = json.loads(r.read().decode("utf-8"))
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/applications?debug=1", timeout=5) as r:
                        a_dbg = json.loads(r.read().decode("utf-8"))
                    self.assertEqual(a_prod["counts"]["total"], 0)
                    self.assertEqual(a_dbg["counts"]["total"], 1)

                    # Debug Fetch passes --scrape-only and --debug
                    req_fetch = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/run",
                        data=json.dumps({
                            "action": "search",
                            "limit": 5,
                            "queries": ["stage droit"],
                            "sources": ["linkedin"],
                            "quality": ["high"],
                            "debug": True,
                        }).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_fetch, timeout=5) as r:
                        self.assertTrue(json.loads(r.read().decode("utf-8"))["started"])
                    self.assertIn("--scrape-only", captured[0][0])
                    self.assertIn("--debug", captured[0][0])

                    # GitHub sync is rejected in Debug Mode
                    req_sync = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/run",
                        data=json.dumps({"action": "sync", "debug": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_sync, timeout=5) as r:
                        sync_res = json.loads(r.read().decode("utf-8"))
                    self.assertFalse(sync_res["started"])
                finally:
                    srv.shutdown()
                    srv.server_close()

    def test_linkedin_guest_url_rate_limiter_and_profile_locations(self) -> None:
        self.assertEqual(
            linkedin_guest_detail_url("https://fr.linkedin.com/jobs/view/stage-juriste-4495042227"),
            "https://fr.linkedin.com/jobs-guest/jobs/api/jobPosting/4495042227",
        )
        self.assertEqual(
            linkedin_guest_detail_url("https://www.hellowork.com/fr-fr/emplois/123.html"),
            "https://www.hellowork.com/fr-fr/emplois/123.html",
        )

        # Verify DomainRateLimiter records next_allowed per host without blocking other hosts.
        limiter = DomainRateLimiter(jitter=0.0)
        with mock.patch("scrapers.http_client.time.sleep") as mock_sleep:
            limiter.wait("https://fr.linkedin.com/jobs/view/1")
            limiter.wait("https://www.hellowork.com/fr-fr/emplois/1.html")
            self.assertEqual(mock_sleep.call_count, 0)
            limiter.wait("https://fr.linkedin.com/jobs/view/2")
            self.assertEqual(mock_sleep.call_count, 1)

        # Verify HTTP 429 raises HttpRateLimitError for clean browser fallback.
        fake_resp = mock.MagicMock(status_code=429)
        with mock.patch("scrapers.parsers.http_get", return_value=fake_resp):
            with self.assertRaises(HttpRateLimitError):
                fetch_offer_details_http({"source": "LinkedIn", "url": "https://fr.linkedin.com/jobs/view/123"})

        # Verify search_location_li/hw isolate prod vs debug profile locations.
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            prod = get_runtime_paths(debug=False, workspace=ws)
            dbg = get_runtime_paths(debug=True, workspace=ws)
            prod.profile_path.parent.mkdir(parents=True, exist_ok=True)
            prod.profile_path.write_text(
                json.dumps({"search": {"location": "Lyon, France", "location_short": "Lyon"}}),
                encoding="utf-8",
            )
            ensure_debug_sandbox(dbg)
            self.assertEqual(search_location_li(prod.profile_path), "Lyon, France")
            self.assertEqual(search_location_hw(prod.profile_path), "Lyon")
            self.assertEqual(search_location_li(dbg.profile_path), "Paris, Île-de-France, France")
            self.assertEqual(search_location_hw(dbg.profile_path), "Paris")


if __name__ == "__main__":
    unittest.main()
