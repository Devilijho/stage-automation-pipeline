#!/usr/bin/env python3
"""
Browser Application Assistant (apply_browser.py)

Uses Playwright with a persistent Chrome/Chromium profile so you stay logged
into LinkedIn, Welcome to the Jungle, HelloWork, Workday, etc.
Features:
- Auto-dismisses French/EU cookie consent banners (Didomi, OneTrust, Axeptio, etc.)
- Follows cross-tab external ATS redirects (HelloWork -> Greenhouse/Lever/Workday/etc.)
- Auto-fills standard French & ATS form fields from `config/profile.json`
- Injects tailored `CV_*.pdf` and `LM_*.pdf` into <input type="file"> fields
- Pauses at a Human-in-the-Loop Gate (or auto-submits simple forms with --auto)
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeout, sync_playwright

WORKSPACE = Path(__file__).resolve().parent.parent
PROFILE_PATH = WORKSPACE / "config" / "profile.json"
APPS_CSV_PATH = WORKSPACE / "data" / "applications.csv"
CHROME_PROFILE_DIR = WORKSPACE / ".chrome_profile"


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def accept_cookies_if_present(page: Page) -> bool:
    """Auto-dismiss Didomi, OneTrust, Axeptio, Tarteaucitron, and generic FR cookie banners."""
    selectors = [
        "#didomi-notice-agree-button",
        "#onetrust-accept-btn-handler",
        "#axeptio_btn_acceptAll",
        "#tarteaucitronPersonalize2",
        "button:has-text('Tout accepter')",
        "button:has-text('Accepter et continuer')",
        "button:has-text('Accepter tout')",
        "button:has-text(\"J'accepte\")",
        "button:has-text('Accepter')",
        "[aria-label='Tout accepter']",
        "[aria-label='Accept all']",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=900):
                loc.click()
                page.wait_for_timeout(400)
                return True
        except Exception:
            continue

    # Check inside iframes (some CMPs render inside an iframe)
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for sel in selectors[:6]:
            try:
                loc = frame.locator(sel).first
                if loc.is_visible(timeout=400):
                    loc.click()
                    page.wait_for_timeout(400)
                    return True
            except Exception:
                continue
    return False


def detect_ats_provider(url: str) -> str:
    u = url.lower()
    if "greenhouse.io" in u:
        return "Greenhouse"
    if "lever.co" in u:
        return "Lever"
    if "smartrecruiters.com" in u:
        return "SmartRecruiters"
    if "myworkdayjobs.com" in u:
        return "Workday"
    if "welcometothejungle.com" in u:
        return "Welcome to the Jungle"
    if "hellowork.com" in u:
        return "HelloWork"
    if "linkedin.com" in u:
        return "LinkedIn"
    if "taleo.net" in u:
        return "Taleo"
    return "External Portal"


def click_apply_and_follow_redirect(
    context: BrowserContext, page: Page
) -> Tuple[Page, bool]:
    """
    Click Postuler / Candidature simplifiée and catch new-tab external ATS redirects
    using `context.expect_page()` before clicking.
    """
    selectors = [
        "button:has-text('Candidature simplifiée')",
        "button:has-text('Postuler sur le site')",
        "a:has-text('Postuler sur le site')",
        "button:has-text('Postuler')",
        "a:has-text('Postuler')",
        "button:has-text('Easy Apply')",
        "button:has-text('Apply')",
        "a:has-text('Apply')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if not loc.is_visible(timeout=1500):
                continue

            initial_url = page.url
            try:
                with context.expect_page(timeout=4000) as new_page_info:
                    loc.click()
                new_page = new_page_info.value
                new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                new_page.wait_for_timeout(1200)
                accept_cookies_if_present(new_page)
                return new_page, True
            except PlaywrightTimeout:
                page.wait_for_timeout(1500)
                if page.url != initial_url:
                    accept_cookies_if_present(page)
                return page, True
        except Exception:
            continue
    return page, False


def fill_matching_fields(page: Page, answers: Dict[str, str]) -> List[str]:
    """Heuristically fill visible text/email/tel/textarea inputs (French + common ATS)."""
    field_rules = [
        (["prénom", "prenom", "first name", "firstname", "first_name"], answers["prenom"]),
        (["nom", "last name", "lastname", "last_name", "surname"], answers["nom"]),
        (["full name", "nom complet"], answers["nom_complet"]),
        (["e-mail", "email", "courriel", "mail"], answers["email"]),
        (["téléphone", "telephone", "phone", "mobile", "tel"], answers["telephone"]),
        (["ville", "city", "localisation", "location"], answers["ville"]),
        (["école", "ecole", "school", "université", "university"], answers["ecole"]),
        (["niveau", "level", "études", "diplôme"], answers["niveau"]),
        (["disponibilité", "disponibilite", "availability", "date de début"], answers["disponibilite"]),
        (["durée", "duree", "duration"], answers["duree"]),
        (["github"], answers["github"]),
        (["linkedin"], answers["linkedin"]),
    ]

    filled: List[str] = []
    inputs = page.locator("input:visible, textarea:visible")
    count = inputs.count()
    for i in range(count):
        el = inputs.nth(i)
        try:
            itype = (el.get_attribute("type") or "text").lower()
            if itype in {"hidden", "file", "submit", "button", "checkbox", "radio"}:
                continue
            current_val = (el.input_value() or "").strip()
            if current_val:
                continue

            hint = " ".join(
                filter(
                    None,
                    [
                        el.get_attribute("name"),
                        el.get_attribute("id"),
                        el.get_attribute("placeholder"),
                        el.get_attribute("aria-label"),
                    ],
                )
            ).lower()

            el_id = el.get_attribute("id")
            if el_id:
                lbl = page.locator(f"label[for='{el_id}']").first
                if lbl.count() > 0:
                    hint += " " + lbl.inner_text().lower()

            for patterns, value in field_rules:
                if any(p in hint for p in patterns):
                    el.fill(value)
                    filled.append(f"{patterns[0]} -> {value}")
                    break
        except Exception:
            continue
    return filled


def inject_files(page: Page, cv_pdf: Path, lm_pdf: Path) -> List[str]:
    attached: List[str] = []
    file_inputs = page.locator("input[type='file']")
    count = file_inputs.count()
    for i in range(count):
        el = file_inputs.nth(i)
        try:
            hint = " ".join(
                filter(
                    None,
                    [
                        el.get_attribute("name"),
                        el.get_attribute("id"),
                        el.get_attribute("aria-label"),
                    ],
                )
            ).lower()
            if any(k in hint for k in ["lettre", "motivation", "cover", "lm"]):
                el.set_input_files(str(lm_pdf))
                attached.append(f"LM -> {lm_pdf.name}")
            elif any(k in hint for k in ["cv", "resume", "curriculum"]):
                el.set_input_files(str(cv_pdf))
                attached.append(f"CV -> {cv_pdf.name}")
            elif i == 0:
                el.set_input_files(str(cv_pdf))
                attached.append(f"CV (slot 0) -> {cv_pdf.name}")
            elif i == 1:
                el.set_input_files(str(lm_pdf))
                attached.append(f"LM (slot 1) -> {lm_pdf.name}")
        except Exception:
            continue
    return attached


def update_tracker_status(url: str, company: str, new_status: str) -> None:
    if not APPS_CSV_PATH.exists():
        return
    with APPS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return
    headers = list(rows[0].keys())
    norm_url = url.strip().rstrip("/").lower()
    for r in rows:
        if (norm_url and (r.get("URL") or "").strip().rstrip("/").lower() == norm_url) or (
            (r.get("Company") or "").strip().lower() == company.lower()
        ):
            r["Status"] = new_status
    with APPS_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def try_auto_submit(page: Page) -> str:
    for _ in range(4):
        for sub_sel in [
            "button:has-text('Envoyer la candidature')",
            "button:has-text('Envoyer')",
            "button:has-text('Soumettre')",
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
        ]:
            try:
                btn = page.locator(sub_sel).first
                if btn.is_visible(timeout=1000) and btn.is_enabled():
                    btn.click()
                    page.wait_for_timeout(1500)
                    return "applied"
            except Exception:
                continue

        advanced = False
        for next_sel in [
            "button:has-text('Suivant')",
            "button:has-text('Vérifier')",
            "button:has-text('Continuer')",
            "button:has-text('Next')",
            "button:has-text('Review')",
        ]:
            try:
                btn = page.locator(next_sel).first
                if btn.is_visible(timeout=1000) and btn.is_enabled():
                    btn.click()
                    page.wait_for_timeout(1200)
                    advanced = True
                    break
            except Exception:
                continue
        if not advanced:
            break
    return "manual_portal"


def main() -> int:
    parser = argparse.ArgumentParser(description="Playwright Browser Application Assistant")
    parser.add_argument("--meta", required=True, help="Path to output/<Company>/metadata.json")
    parser.add_argument("--url", default="", help="Override job URL")
    parser.add_argument("--auto", action="store_true", help="Auto-submit simple forms without pausing")
    args = parser.parse_args()

    profile = load_json(PROFILE_PATH)
    meta = load_json(Path(args.meta))
    url = args.url or meta.get("url")
    if not url:
        print("[!] No URL found in metadata.json. Pass --url <job_url>.")
        return 1

    cv_pdf = Path(meta["cv_pdf"])
    lm_pdf = Path(meta["lm_pdf"])
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    # Remove stale SingletonLock if a previous Chrome crashed
    lock_file = CHROME_PROFILE_DIR / "SingletonLock"
    if lock_file.exists() or lock_file.is_symlink():
        try:
            lock_file.unlink()
        except Exception:
            pass

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 860},
        )
        page = context.pages[0] if context.pages else context.new_page()
        print(f"[*] Opening {url} ...")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)

        cookies_closed = accept_cookies_if_present(page)
        print(f"[*] Cookie banner dismissed: {cookies_closed}")

        active_page, clicked = click_apply_and_follow_redirect(context, page)
        if active_page.is_closed() and context.pages:
            active_page = context.pages[-1]

        if active_page.is_closed():
            print("[*] Browser tab was closed by user.")
            context.close()
            return 0

        ats = detect_ats_provider(active_page.url)
        print(f"[*] Apply clicked: {clicked} | Active target: {ats} ({active_page.url})")

        try:
            filled = fill_matching_fields(active_page, profile["form_answers"])
            attached = inject_files(active_page, cv_pdf, lm_pdf)
        except Exception as e:
            print(f"[!] Form auto-fill interrupted ({e}); keeping browser open if still active.")
            filled, attached = [], []

        print("\n" + "=" * 56)
        print("HUMAN-IN-THE-LOOP GATE — VERIFY BEFORE SUBMITTING")
        print("=" * 56)
        print(f"Company          : {meta['company']}")
        print(f"Role             : {meta['role']}")
        print(f"Target ATS       : {ats}")
        print(f"New CV Title     : {meta['cv_title']}")
        print(f"Selected Projects: {', '.join(meta['selected_projects'])}")
        print(f"Matched Keywords : {', '.join(meta['matched_keywords'])}")
        print(f"Auto-filled      : {len(filled)} fields")
        print(f"Files Attached   : {', '.join(attached) if attached else 'None (attach manually if needed)'}")
        print(f"CV PDF           : {cv_pdf}")
        print(f"LM PDF           : {lm_pdf}")
        print("-" * 56)
        if args.auto:
            result_status = try_auto_submit(active_page)
            update_tracker_status(url, meta["company"], result_status)
            print(f"[+] Auto-mode finished with status: '{result_status}'.")
        elif not sys.stdin.isatty():
            print("[*] UI mode: Chrome is open. Review & submit manually, then close the Chrome window when done.")
            try:
                while len(context.pages) > 0:
                    context.pages[0].wait_for_timeout(500)
            except Exception:
                pass
        else:
            print("Review the browser form and click 'Envoyer' manually.")
            try:
                status_choice = input(
                    "After submitting (or if redirected to external portal), type status [applied / manual_portal / skipped]: "
                ).strip().lower()
            except EOFError:
                status_choice = ""
            if status_choice in {"a", "applied"}:
                status_choice = "applied"
            elif status_choice in {"m", "manual_portal"}:
                status_choice = "manual_portal"
            elif status_choice in {"s", "skip", "skipped"}:
                status_choice = "skipped"
            if status_choice in {"applied", "manual_portal", "skipped", "drafted"}:
                update_tracker_status(url, meta["company"], status_choice)
                print(f"[+] Updated applications.csv status to '{status_choice}'.")
        context.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
