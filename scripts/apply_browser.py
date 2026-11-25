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
    Wait for SPA Apply button, click Postuler / Candidature simplifiée,
    and catch new-tab external ATS redirects using `context.expect_page()`.
    """
    selectors = [
        "button.jobs-apply-button:visible",
        "button[aria-label*='Candidature simplifiée' i]:visible",
        "button[aria-label*='Easy Apply' i]:visible",
        "button:has-text('Candidature simplifiée'):visible",
        "button:has-text('Easy Apply'):visible",
        "button:has-text('Postuler sur le site'):visible",
        "a:has-text('Postuler sur le site'):visible",
        "button:has-text('Postuler'):visible",
        "a:has-text('Postuler'):visible",
        "button:has-text('Apply'):visible",
        "a:has-text('Apply'):visible",
    ]
    # Wait up to 8s for SPA pages (LinkedIn/WTTJ) to render the Apply button
    try:
        page.wait_for_selector(
            "button.jobs-apply-button, button:has-text('Candidature simplifiée'), button:has-text('Easy Apply'), button:has-text('Postuler'), a:has-text('Postuler')",
            timeout=8000,
        )
    except Exception:
        pass

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if not loc.is_visible(timeout=1000):
                continue

            initial_url = page.url
            try:
                with context.expect_page(timeout=4000) as new_page_info:
                    loc.click()
                new_page = new_page_info.value
                new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                new_page.wait_for_timeout(1200)
                accept_cookies_if_present(new_page)
                # Some ATS landing pages (e.g. Lever) open the job description first and require clicking "Apply for this job"
                for sub_sel in [
                    "a:has-text('Apply for this job')",
                    "a:has-text('Postuler à ce poste')",
                    "button:has-text('Apply for this job')",
                    "button:has-text('Postuler à ce poste')",
                    "a.postings-btn[href*='/apply']",
                ]:
                    try:
                        sub_btn = new_page.locator(sub_sel).first
                        if sub_btn.is_visible(timeout=1200):
                            sub_btn.click()
                            new_page.wait_for_load_state("domcontentloaded", timeout=12000)
                            new_page.wait_for_timeout(1000)
                            break
                    except Exception:
                        continue
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

    # 1. Direct <input type='file'> on main page or any iframe
    contexts_to_check = [page, *[f for f in page.frames if f != page.main_frame]]
    for ctx in contexts_to_check:
        try:
            file_inputs = ctx.locator("input[type='file']")
            count = file_inputs.count()
        except Exception:
            continue
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
                            el.get_attribute("data-qa"),
                            el.get_attribute("class"),
                        ],
                    )
                ).lower()
                el_id = el.get_attribute("id")
                if el_id:
                    lbl = ctx.locator(f"label[for='{el_id}']").first
                    if lbl.count() > 0:
                        hint += " " + lbl.inner_text().lower()

                if any(k in hint for k in ["lettre", "motivation", "cover", "lm"]):
                    el.set_input_files(str(lm_pdf))
                    el.dispatch_event("change")
                    attached.append(f"LM -> {lm_pdf.name}")
                elif any(k in hint for k in ["cv", "resume", "curriculum"]):
                    el.set_input_files(str(cv_pdf))
                    el.dispatch_event("change")
                    attached.append(f"CV -> {cv_pdf.name}")
                elif i == 0:
                    el.set_input_files(str(cv_pdf))
                    el.dispatch_event("change")
                    attached.append(f"CV (slot 0) -> {cv_pdf.name}")
                elif i == 1:
                    el.set_input_files(str(lm_pdf))
                    el.dispatch_event("change")
                    attached.append(f"LM (slot 1) -> {lm_pdf.name}")
            except Exception:
                continue

    # 2. Fallback: LinkedIn Easy Apply / React upload buttons via FileChooser
    if not attached:
        upload_selectors = [
            "label:has-text('Importer un CV'):visible",
            "button:has-text('Importer un CV'):visible",
            "label:has-text('Upload resume'):visible",
            "button:has-text('Upload resume'):visible",
            "button:has-text('Télécharger un CV'):visible",
            "label:has-text('Télécharger un CV'):visible",
            "button[aria-label*='Importer' i]:visible",
            "button[aria-label*='Upload resume' i]:visible",
        ]
        for sel in upload_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=700):
                    with page.expect_file_chooser(timeout=3000) as fc_info:
                        btn.click()
                    fc_info.value.set_files(str(cv_pdf))
                    attached.append(f"CV (chooser) -> {cv_pdf.name}")
                    page.wait_for_timeout(1200)
                    break
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


def try_click_next_step(page: Page) -> bool:
    """Click Next / Suivant / Vérifier / Review in multi-step wizards (e.g. LinkedIn Easy Apply)."""
    next_selectors = [
        "button[data-easy-apply-next-button]",
        "button[aria-label*='étape suivante' i]",
        "button[aria-label*='next step' i]",
        "button[aria-label*='Vérifier' i]",
        "button[aria-label*='Review' i]",
        "button:has-text('Suivant')",
        "button:has-text('Vérifier')",
        "button:has-text('Continuer')",
        "button:has-text('Next')",
        "button:has-text('Review')",
    ]
    for sel in next_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=900) and btn.is_enabled():
                btn.click()
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def find_submit_button(page: Page):
    """Return visible & enabled final submit button if present on current step."""
    sub_selectors = [
        "button[aria-label*='Envoyer la candidature' i]",
        "button[aria-label*='Submit application' i]",
        "button:has-text('Envoyer la candidature')",
        "button:has-text('Submit application')",
        "button:has-text('Envoyer')",
        "button:has-text('Soumettre')",
        "button:has-text('Submit')",
    ]
    for sel in sub_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800) and btn.is_enabled():
                return btn
        except Exception:
            continue
    return None


def process_application_steps(
    page: Page,
    answers: Dict[str, str],
    cv_pdf: Path,
    lm_pdf: Path,
    auto_submit: bool,
) -> Tuple[List[str], List[str], str]:
    """
    Walk through multi-step application modals (e.g. LinkedIn Easy Apply):
    - Step 1: Fill contact info -> click Suivant/Next
    - Step 2: Attach CV/LM PDFs -> click Suivant/Vérifier
    - Final step: Submit if auto_submit=True, else stop right before Envoyer for manual review.
    """
    all_filled: List[str] = []
    all_attached: List[str] = []

    for step in range(1, 7):
        if page.is_closed():
            break
        print(f"  [*] Processing form step {step}...")
        filled = fill_matching_fields(page, answers)
        attached = inject_files(page, cv_pdf, lm_pdf)
        all_filled.extend(filled)
        all_attached.extend(attached)
        if attached:
            page.wait_for_timeout(800)

        submit_btn = find_submit_button(page)
        if submit_btn is not None:
            if auto_submit:
                submit_btn.click()
                page.wait_for_timeout(1800)
                return all_filled, all_attached, "applied"
            print("  [*] Reached final submit step — paused for your manual review.")
            return all_filled, all_attached, "ready_for_review"

        if not try_click_next_step(page):
            break

    return all_filled, all_attached, "manual_portal"


def main() -> int:
    parser = argparse.ArgumentParser(description="Playwright Browser Application Assistant")
    parser.add_argument("--meta", help="Path to output/<Company>/metadata.json")
    parser.add_argument("--url", default="", help="Override job URL")
    parser.add_argument("--auto", action="store_true", help="Auto-submit simple forms without pausing")
    parser.add_argument(
        "--skip-external",
        action="store_true",
        help="Automatically mark external ATS redirects as manual_portal and close without pausing",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open persistent Chrome profile to LinkedIn & HelloWork login pages and save session",
    )
    args = parser.parse_args()

    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    # Remove stale SingletonLock if a previous Chrome crashed
    lock_file = CHROME_PROFILE_DIR / "SingletonLock"
    if lock_file.exists() or lock_file.is_symlink():
        try:
            lock_file.unlink()
        except Exception:
            pass

    if args.login:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 860},
            )
            p1 = context.pages[0] if context.pages else context.new_page()
            p1.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            p2 = context.new_page()
            p2.goto("https://www.hellowork.com/fr-fr/candidat/connexion-inscription.html", wait_until="domcontentloaded")
            print("[*] Log into LinkedIn and HelloWork in the Chrome tabs, then close the Chrome window when done.")
            try:
                while len(context.pages) > 0:
                    context.pages[0].wait_for_timeout(500)
            except Exception:
                pass
            context.close()
        print("[+] Browser session saved in .chrome_profile/.")
        return 0

    if not args.meta:
        print("[!] --meta is required unless --login is used.")
        return 1

    profile = load_json(PROFILE_PATH)
    meta = load_json(Path(args.meta))
    url = args.url or meta.get("url")
    if not url:
        print("[!] No URL found in metadata.json. Pass --url <job_url>.")
        return 1

    cv_pdf = Path(meta["cv_pdf"])
    lm_pdf = Path(meta["lm_pdf"])

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

        if any(k in active_page.url.lower() for k in ("authwall", "/login", "connexion", "signin")):
            print("[!] Login wall detected! Click 'Login Chrome' in the UI (or run './stage login') once to save your session.")
            update_tracker_status(url, meta["company"], "manual_portal")
            context.close()
            return 0

        try:
            filled, attached, wizard_status = process_application_steps(
                active_page, profile["form_answers"], cv_pdf, lm_pdf, auto_submit=args.auto
            )
        except Exception as e:
            print(f"[!] Form auto-fill interrupted ({e}); keeping browser open if still active.")
            filled, attached, wizard_status = [], [], "manual_portal"

        is_external = ats not in {"LinkedIn", "HelloWork", "Welcome to the Jungle"}
        if args.skip_external and is_external and (len(filled) == 0 and len(attached) == 0):
            print(f"[*] External portal ({ats}) requires manual registration/fill -> marking 'manual_portal' and skipping.")
            update_tracker_status(url, meta["company"], "manual_portal")
            context.close()
            return 0

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
            update_tracker_status(url, meta["company"], wizard_status)
            print(f"[+] Auto-mode finished with status: '{wizard_status}'.")
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
