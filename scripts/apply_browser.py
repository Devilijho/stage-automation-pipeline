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
        "button:has-text('Envoyer ma candidature')",
        "button:has-text('Valider ma candidature')",
        "button:has-text('Je postule')",
        "button:has-text('Postuler maintenant')",
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


def uncheck_follow_company(page: Page) -> None:
    """Uncheck LinkedIn's pre-checked 'Follow <Company>' checkbox if present."""
    selectors = [
        "#follow-company-checkbox",
        "input[id*='follow-company' i]",
    ]
    for sel in selectors:
        try:
            cb = page.locator(sel).first
            if cb.count() > 0 and cb.is_checked():
                # LinkedIn styles the checkbox with a clickable <label for="follow-company-checkbox">
                lbl = page.locator("label[for='follow-company-checkbox']").first
                if lbl.count() > 0 and lbl.is_visible(timeout=500):
                    lbl.click()
                else:
                    cb.uncheck(force=True)
                print("  [*] Unchecked 'Follow company' checkbox.")
                return
        except Exception:
            continue


CONFIRMATION_PHRASES = [
    "votre candidature a été envoyée",
    "candidature envoyée",
    "application sent",
    "your application was sent",
    "merci pour votre candidature",
    "thank you for applying",
    "candidature enregistrée",
]


def check_submission_confirmed(page: Page) -> bool:
    """Check if the page displays a confirmed submission message."""
    try:
        if page.is_closed():
            return False
        body_text = page.locator("body").inner_text(timeout=800).lower()
        return any(phrase in body_text for phrase in CONFIRMATION_PHRASES)
    except Exception:
        return False


def get_label_text(page: Page, el) -> str:
    """Extract label/aria/placeholder text for a form element."""
    parts = []
    for attr in ("aria-label", "placeholder", "name", "id"):
        val = el.get_attribute(attr)
        if val:
            parts.append(val)
    el_id = el.get_attribute("id")
    if el_id:
        try:
            lbl = page.locator(f"label[for='{el_id}']").first
            if lbl.count() > 0:
                parts.append(lbl.inner_text())
        except Exception:
            pass
    try:
        # LinkedIn wraps question text in ancestor .fb-dash-form-element or label
        parent_lbl = el.locator("xpath=ancestor::div[contains(@class,'fb-dash-form-element') or contains(@class,'jobs-easy-apply-form-element')][1]").first
        if parent_lbl.count() > 0:
            parts.append(parent_lbl.inner_text())
    except Exception:
        pass
    return " ".join(parts).lower()


def get_form_root(page: Page):
    """Scope queries to LinkedIn Easy Apply modal when open so we don't touch the navbar search box."""
    try:
        modal = page.locator(".jobs-easy-apply-modal, div[role='dialog'][aria-modal='true']").first
        if modal.count() > 0 and modal.is_visible(timeout=400):
            return modal
    except Exception:
        pass
    return page


def answer_common_questions(page: Page, answers: Dict[str, str]) -> List[str]:
    """Handle LinkedIn Easy Apply text/number questions, <select> dropdowns, radios, and checkboxes."""
    answered: List[str] = []
    root = get_form_root(page)

    # 1. Text & numeric screening inputs (experience years, dates, duration, salary, SmartRecruiters work history)
    try:
        inputs = root.locator("input:visible, textarea:visible")
        for i in range(inputs.count()):
            el = inputs.nth(i)
            try:
                itype = (el.get_attribute("type") or "text").lower()
                if itype in {"hidden", "file", "submit", "button", "checkbox", "radio", "search"}:
                    continue
                if (el.input_value() or "").strip():
                    continue
                q = get_label_text(page, el)
                if "search" in q and "job" not in q:
                    continue
                is_num = itype == "number" or "numeric" in (el.get_attribute("inputmode") or "")

                val = ""
                if any(k in q for k in ("your title", "votre titre", "intitulé du poste")):
                    val = "Étudiant BUT 2 Informatique / Développeur (École 42)"
                elif any(k in q for k in ("company", "entreprise", "société")) and "linkedin" not in q:
                    val = "École 42 Paris / IUT d'Orsay"
                elif "description" in q:
                    val = "Développement C/C++/TypeScript, systèmes Linux, Docker, SQL, réseaux et sécurité (projets Webserv, Transcendence, Chaski-Booking)."
                elif any(k in q for k in ("message au recruteur", "motivations", "message personnalisé", "pourquoi ce poste")):
                    val = (
                        f"Bonjour, étudiant en {answers.get('formation', 'BUT 2 Informatique')} ({answers.get('ecole', 'IUT d\'Orsay')}) et à l'École 42 Paris, "
                        f"je recherche un stage de {answers.get('disponibilite', '3 mois du 03/05 au 16/07')}. "
                        f"Opérationnel en C/C++, TypeScript, Linux, Docker et SQL, je serais ravi de rejoindre votre équipe. "
                        f"Cordialement, {answers.get('nom_complet', 'Santiago FUENTES LLANOS')}."
                    )
                elif any(k in q for k in ("année", "années", "years", "combien d'exp", "how long", "how many years")):
                    val = "1"
                elif any(k in q for k in ("durée", "duration", "combien de mois", "months")):
                    val = "3" if is_num else answers.get("duree", "3 mois")
                elif any(k in q for k in ("date de début", "start date", "quand pouvez", "when can you", "disponib")):
                    val = "2026-05-03" if itype == "date" else "03/05/2026"
                elif any(k in q for k in ("salaire", "rémunération", "gratification", "salary", "compensation")):
                    val = "800" if is_num else "Gratification légale"
                elif any(k in q for k in ("ville", "city", "localisation")):
                    val = answers.get("ville", "Bures-sur-Yvette")
                elif is_num:
                    val = "1"

                if val:
                    el.fill(val)
                    answered.append(f"input -> {val[:30]}")
                elif q.strip():
                    print(f"  [?] Unanswered input: {q.strip()[:90]}")
            except Exception:
                continue
    except Exception:
        pass

    # 2. Select dropdowns (country code, Oui/Yes, English/French proficiency, education)
    try:
        selects = root.locator("select:visible")
        for i in range(selects.count()):
            sel = selects.nth(i)
            try:
                cur = (sel.input_value() or "").strip()
                if cur and cur.lower() not in {"select an option", "sélectionner une option", ""}:
                    continue
                q = get_label_text(page, sel)
                opts = [o.inner_text().strip() for o in sel.locator("option").all()]
                prefer_no = any(k in q for k in ("visa", "sponsor", "handicap"))
                targets = (
                    ("Non", "No")
                    if prefer_no
                    else ("Oui", "Yes", "France (+33)", "C1", "Courant", "Fluent", "Bac+2", "Bac +2")
                )
                for target in targets:
                    match = next((o for o in opts if target.lower() in o.lower()), None)
                    if match:
                        sel.select_option(label=match)
                        answered.append(f"select -> {match}")
                        break
            except Exception:
                continue
    except Exception:
        pass

    # 3. Radio groups (read ancestor container text so 'yes / no' radios have question context)
    try:
        fieldsets = root.locator("fieldset:visible")
        for i in range(fieldsets.count()):
            fs = fieldsets.nth(i)
            try:
                if fs.locator("input[type='radio']:checked").count() > 0:
                    continue
                legend = get_label_text(page, fs) + " " + fs.inner_text().lower()
                if any(k in legend for k in ("visa", "sponsor", "handicap", "disability")):
                    choices = ("Non", "No")
                elif any(k in legend for k in ("autoris", "légale", "work", "france", "stage", "convention", "disponib", "majeur", "bac+", "hybride", "présentiel", "remote", "english", "french", "français", "anglais", "degree", "diplôme")):
                    choices = ("Oui", "Yes")
                else:
                    print(f"  [?] Unanswered radio: {legend.strip()[:90]}")
                    continue

                for lbl_txt in choices:
                    lbl = fs.locator(f"label:has-text('{lbl_txt}')").first
                    if lbl.count() > 0 and lbl.is_visible(timeout=300):
                        lbl.click()
                        answered.append(f"radio -> {lbl_txt}")
                        break
            except Exception:
                continue
    except Exception:
        pass

    # 4. Required privacy/terms checkboxes (never check marketing/newsletter)
    try:
        cbs = root.locator("input[type='checkbox']:visible")
        for i in range(cbs.count()):
            cb = cbs.nth(i)
            try:
                if cb.is_checked():
                    continue
                q = get_label_text(page, cb)
                if any(k in q for k in ("politique", "confidentialité", "privacy", "conditions", "terms", "certifie", "exact")):
                    cb.check(force=True)
                    answered.append("checkbox -> accepted terms")
            except Exception:
                continue
    except Exception:
        pass

    return answered


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
        filled.extend(answer_common_questions(page, answers))
        attached = inject_files(page, cv_pdf, lm_pdf)
        all_filled.extend(filled)
        all_attached.extend(attached)
        if attached:
            page.wait_for_timeout(800)

        submit_btn = find_submit_button(page)
        if submit_btn is not None:
            uncheck_follow_company(page)
            if auto_submit:
                submit_btn.click()
                page.wait_for_timeout(2000)
                if check_submission_confirmed(page):
                    print("  [+] Submission confirmed on page!")
                    return all_filled, all_attached, "applied"
                return all_filled, all_attached, "applied"
            print("  [*] Reached final submit step — paused for your manual review.")
            return all_filled, all_attached, "ready_for_review"

        if not try_click_next_step(page):
            break

    return all_filled, all_attached, "manual_portal"


def run_single_offer_on_page(
    context: BrowserContext,
    page: Page,
    profile: Dict[str, Any],
    meta: Dict[str, Any],
    url: str,
    auto: bool,
    skip_external: bool,
) -> Tuple[Page, str]:
    """Process one offer on a given page; returns (active_page, status_or_state)."""
    cv_pdf = Path(meta["cv_pdf"])
    lm_pdf = Path(meta["lm_pdf"])

    print(f"\n[*] [{meta['company']}] Opening {url} ...")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1000)

    cookies_closed = accept_cookies_if_present(page)
    print(f"[*] [{meta['company']}] Cookie banner dismissed: {cookies_closed}")

    active_page, clicked = click_apply_and_follow_redirect(context, page)
    if active_page.is_closed() and context.pages:
        active_page = context.pages[-1]
    if active_page.is_closed():
        return active_page, "closed"

    ats = detect_ats_provider(active_page.url)
    print(f"[*] [{meta['company']}] Apply clicked: {clicked} | Target: {ats}")

    if any(k in active_page.url.lower() for k in ("authwall", "/login", "connexion", "signin")):
        print(f"[!] [{meta['company']}] Login wall detected! Run Login Chrome once.")
        update_tracker_status(url, meta["company"], "manual_portal")
        return active_page, "manual_portal"

    try:
        filled, attached, wizard_status = process_application_steps(
            active_page, profile["form_answers"], cv_pdf, lm_pdf, auto_submit=auto
        )
    except Exception as e:
        print(f"[!] [{meta['company']}] Form auto-fill interrupted ({e}).")
        filled, attached, wizard_status = [], [], "manual_portal"

    is_external = ats not in {"LinkedIn", "HelloWork", "Welcome to the Jungle"}
    if skip_external and is_external and (len(filled) == 0 and len(attached) == 0):
        print(f"[*] [{meta['company']}] External portal ({ats}) -> marking 'manual_portal' and skipping.")
        update_tracker_status(url, meta["company"], "manual_portal")
        return active_page, "manual_portal"

    print(f"[*] [{meta['company']}] Filled: {len(filled)} | Attached: {', '.join(attached) if attached else 'None'}")
    if auto:
        update_tracker_status(url, meta["company"], wizard_status)
        print(f"[+] [{meta['company']}] Status: '{wizard_status}'")
    return active_page, wizard_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Playwright Browser Application Assistant")
    parser.add_argument("--meta", help="Path to output/<Company>/metadata.json")
    parser.add_argument("--metas", nargs="+", help="Multiple metadata.json paths to process in 1 Chrome window")
    parser.add_argument("--tabs", type=int, default=4, help="Concurrent tabs per batch in --metas mode")
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

    meta_paths: List[Path] = []
    if args.metas:
        meta_paths = [Path(m) for m in args.metas]
    elif args.meta:
        meta_paths = [Path(args.meta)]
    else:
        print("[!] --meta or --metas is required unless --login is used.")
        return 1

    profile = load_json(PROFILE_PATH)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 860},
        )

        # True concurrent multi-tab batch mode inside 1 persistent Chrome window
        if len(meta_paths) > 1:
            batch_size = max(1, args.tabs)
            for start in range(0, len(meta_paths), batch_size):
                chunk = meta_paths[start : start + batch_size]
                print(f"\n=== Opening {len(chunk)} tabs simultaneously ({start + 1}-{start + len(chunk)}/{len(meta_paths)}) ===")

                # Phase 1: Open & navigate all N tabs at once
                tabs: List[Dict[str, Any]] = []
                for idx_in_chunk, mp in enumerate(chunk):
                    meta = load_json(mp)
                    u = meta.get("url", "")
                    if not u:
                        continue
                    pg = context.pages[0] if (idx_in_chunk == 0 and context.pages) else context.new_page()
                    print(f"  [*] [{meta['company']}] Loading {u}")
                    try:
                        pg.goto(u, wait_until="domcontentloaded", timeout=30000)
                    except Exception as e:
                        print(f"  [!] [{meta['company']}] Load error: {e}")
                    tabs.append({"page": pg, "meta": meta, "url": u, "done": False})

                if tabs:
                    tabs[0]["page"].wait_for_timeout(1500)

                # Phase 2: Dismiss cookies & click Apply on all N tabs
                for item in tabs:
                    pg = item["page"]
                    meta = item["meta"]
                    if pg.is_closed():
                        item["done"] = True
                        continue
                    accept_cookies_if_present(pg)
                    active_pg, clicked = click_apply_and_follow_redirect(context, pg)
                    if active_pg.is_closed() and context.pages:
                        active_pg = context.pages[-1]
                    item["page"] = active_pg
                    if active_pg.is_closed():
                        item["done"] = True
                        continue
                    ats = detect_ats_provider(active_pg.url)
                    item["ats"] = ats
                    print(f"  [*] [{meta['company']}] Apply clicked: {clicked} | Target: {ats}")

                    if any(k in active_pg.url.lower() for k in ("authwall", "/login", "connexion", "signin")):
                        print(f"  [!] [{meta['company']}] Login wall -> manual_portal")
                        update_tracker_status(item["url"], meta["company"], "manual_portal")
                        item["done"] = True

                # Phase 3: Step through forms across all active tabs in parallel
                for step in range(1, 7):
                    active_items = [it for it in tabs if not it["done"] and not it["page"].is_closed()]
                    if not active_items:
                        break
                    for it in active_items:
                        pg = it["page"]
                        meta = it["meta"]
                        cv_pdf = Path(meta["cv_pdf"])
                        lm_pdf = Path(meta["lm_pdf"])
                        try:
                            filled = fill_matching_fields(pg, profile["form_answers"])
                            filled.extend(answer_common_questions(pg, profile["form_answers"]))
                            attached = inject_files(pg, cv_pdf, lm_pdf)
                            if attached:
                                pg.wait_for_timeout(600)

                            is_external = it.get("ats", "") not in {"LinkedIn", "HelloWork", "Welcome to the Jungle"}
                            if step == 1 and args.skip_external and is_external and len(filled) == 0 and len(attached) == 0:
                                print(f"  [*] [{meta['company']}] External portal ({it.get('ats')}) -> skipping (manual_portal)")
                                update_tracker_status(it["url"], meta["company"], "manual_portal")
                                it["done"] = True
                                continue

                            submit_btn = find_submit_button(pg)
                            if submit_btn is not None:
                                uncheck_follow_company(pg)
                                if args.auto:
                                    submit_btn.click()
                                    pg.wait_for_timeout(1500)
                                    update_tracker_status(it["url"], meta["company"], "applied")
                                    print(f"  [+] [{meta['company']}] Submitted -> 'applied'!")
                                else:
                                    print(f"  [*] [{meta['company']}] Ready for your review on tab.")
                                it["done"] = True
                                continue

                            if not try_click_next_step(pg):
                                if args.auto:
                                    update_tracker_status(it["url"], meta["company"], "manual_portal")
                                it["done"] = True
                        except Exception as e:
                            print(f"  [!] [{meta['company']}] Step {step} error: {e}")
                            it["done"] = True

                # Phase 4: Close finished tabs in auto mode, or wait for manual review
                if args.auto:
                    for it in tabs:
                        if not it["page"].is_closed() and len(context.pages) > 1:
                            try:
                                it["page"].close()
                            except Exception:
                                pass
                else:
                    open_review = [it for it in tabs if not it["page"].is_closed()]
                    if open_review:
                        print(f"[*] {len(open_review)} tab(s) open for manual review. Submit & close tabs when done.")
                        confirmed_urls = set()
                        try:
                            while any(not it["page"].is_closed() for it in open_review):
                                for it in open_review:
                                    if it["url"] not in confirmed_urls and check_submission_confirmed(it["page"]):
                                        confirmed_urls.add(it["url"])
                                        update_tracker_status(it["url"], it["meta"]["company"], "applied")
                                        print(f"  [+] Confirmed submission for {it['meta']['company']}! Marked 'applied'.")
                                if context.pages:
                                    context.pages[0].wait_for_timeout(500)
                                else:
                                    break
                        except Exception:
                            pass
            context.close()
            return 0

        # Single offer mode
        meta = load_json(meta_paths[0])
        url = args.url or meta.get("url")
        if not url:
            print("[!] No URL found in metadata.json. Pass --url <job_url>.")
            context.close()
            return 1

        page = context.pages[0] if context.pages else context.new_page()
        active_page, wizard_status = run_single_offer_on_page(
            context, page, profile, meta, url, auto=args.auto, skip_external=args.skip_external
        )
        if not args.auto and not active_page.is_closed():
            if not sys.stdin.isatty():
                print("[*] UI mode: Chrome is open. Review & submit manually, then close Chrome (auto-detecting confirmation).")
                confirmed = False
                try:
                    while len(context.pages) > 0:
                        for p_open in list(context.pages):
                            if not confirmed and check_submission_confirmed(p_open):
                                confirmed = True
                                update_tracker_status(url, meta["company"], "applied")
                                print("[+] Confirmed submission on page! Updated status to 'applied'.")
                        context.pages[0].wait_for_timeout(500)
                except Exception:
                    pass
                if not confirmed:
                    print("[*] Window closed without confirmation; status left unchanged.")
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
