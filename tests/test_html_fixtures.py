"""Offline HTML fixture, date/postal config, LLM cache CRUD, and ui_server API tests."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ui_server
from browser.question_rules import log_unanswered_question
from core.config import get_postal_code, parse_internship_dates
from core.llm import delete_llm_cache_entry, load_llm_cache, replace_llm_cache, save_llm_cache
from scrapers.http_client import is_login_wall
from scrapers.parsers import parse_offer_html


class HtmlFixturesAndCoreTests(unittest.TestCase):
    def test_parse_offer_html_fixtures(self) -> None:
        li_html = (FIXTURES_DIR / "linkedin_offer.html").read_text(encoding="utf-8")
        li_det = parse_offer_html({"source": "LinkedIn", "url": "https://fr.linkedin.com/jobs/view/123", "preview": ""}, li_html)
        self.assertEqual(li_det["company"], "Thales")
        self.assertIn("C++", li_det["text"])

        hw_html = (FIXTURES_DIR / "hellowork_offer.html").read_text(encoding="utf-8")
        hw_det = parse_offer_html({"source": "HelloWork", "url": "https://www.hellowork.com/fr-fr/emplois/1.html", "preview": ""}, hw_html)
        self.assertEqual(hw_det["company"], "Capgemini")
        self.assertNotIn("Navigation menu", hw_det["text"])

        wttj_html = (FIXTURES_DIR / "wttj_offer.html").read_text(encoding="utf-8")
        wttj_det = parse_offer_html(
            {"source": "WTTJ", "url": "https://www.welcometothejungle.com/fr/companies/doctolib/jobs/stage-dev", "preview": ""},
            wttj_html,
        )
        self.assertEqual(wttj_det["company"], "Doctolib")
        self.assertIn("TypeScript", wttj_det["text"])

        ind_html = (FIXTURES_DIR / "indeed_offer.html").read_text(encoding="utf-8")
        ind_det = parse_offer_html({"source": "Indeed", "url": "https://fr.indeed.com/viewjob?jk=abc", "preview": ""}, ind_html)
        self.assertEqual(ind_det["company"], "OVHcloud")
        self.assertIn("Debian", ind_det["text"])

    def test_login_wall_fixture(self) -> None:
        login_html = (FIXTURES_DIR / "login_wall.html").read_text(encoding="utf-8")
        self.assertTrue(is_login_wall("https://www.apec.fr/candidat/mon-compte", login_html))
        self.assertTrue(is_login_wall("https://www.linkedin.com/authwall", "<html></html>"))
        self.assertFalse(is_login_wall("https://www.hellowork.com/fr-fr/emplois/1.html", "<html><body>Offre de stage</body></html>"))

    def test_parse_internship_dates_and_postal_code(self) -> None:
        prof = {
            "personal": {"location": "Orsay, 91400, France"},
            "internship": {"year": "2027", "start_date": "12/04", "end_date": "02/07"},
        }
        dates = parse_internship_dates(prof)
        self.assertEqual(dates["start"]["iso"], "2027-04-12")
        self.assertEqual(dates["start"]["fr"], "12/04/2027")
        self.assertEqual(dates["start"]["day"], "12")
        self.assertEqual(dates["start"]["month_idx_0"], "3")
        self.assertEqual(dates["end"]["iso"], "2027-07-02")
        self.assertEqual(get_postal_code(prof), "91400")
        self.assertEqual(get_postal_code({"personal": {"postal_code": "75005"}}), "75005")

    def test_llm_cache_delete_replace_and_unanswered_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "llm_cache.json"
            save_llm_cache({"input|q1|": "A1", "radio|q2|Oui||Non": "Oui"}, cache_path)
            self.assertTrue(delete_llm_cache_entry("input|q1|", cache_path))
            self.assertFalse(delete_llm_cache_entry("missing", cache_path))
            self.assertEqual(load_llm_cache(cache_path), {"radio|q2|Oui||Non": "Oui"})
            replace_llm_cache({"k": "v"}, cache_path)
            self.assertEqual(load_llm_cache(cache_path), {"k": "v"})

            uq_path = Path(tmp) / "unanswered.json"
            log_unanswered_question("input", "Numéro INE ?", [], company="Acme", url="https://ex.com/1", path=uq_path)
            log_unanswered_question("input", "Numéro INE ?", [], company="Acme", url="https://ex.com/1", path=uq_path)
            entries = json.loads(uq_path.read_text(encoding="utf-8"))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["question"], "Numéro INE ?")

    def test_ui_server_health_bad_json_and_llm_cache_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "llm_cache.json"
            save_llm_cache({"radio|permis b ?|Oui||Non": "Non"}, cache_path)
            with mock.patch.object(ui_server, "LLM_CACHE_PATH", cache_path):
                srv = ThreadingHTTPServer(("127.0.0.1", 0), ui_server.StageHandler)
                port = srv.server_address[1]
                t = threading.Thread(target=srv.serve_forever, daemon=True)
                t.start()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                        health = json.loads(r.read().decode("utf-8"))
                    self.assertTrue(health["ok"])

                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/llm_cache", timeout=5) as r:
                        data = json.loads(r.read().decode("utf-8"))
                    self.assertEqual(len(data["entries"]), 1)
                    self.assertEqual(data["entries"][0]["answer"], "Non")

                    req_bad = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/llm_cache",
                        data=b"{not-json",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(req_bad, timeout=5)
                    self.assertEqual(ctx.exception.code, 400)

                    req_upd = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/llm_cache",
                        data=json.dumps({"action": "update", "key": "radio|permis b ?|Oui||Non", "answer": "Oui"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_upd, timeout=5) as r:
                        self.assertTrue(json.loads(r.read().decode("utf-8"))["ok"])
                    self.assertEqual(load_llm_cache(cache_path)["radio|permis b ?|Oui||Non"], "Oui")
                finally:
                    srv.shutdown()
                    srv.server_close()


if __name__ == "__main__":
    unittest.main()
