#!/usr/bin/env python3
"""
Browser Application Assistant (apply_browser.py)

Uses Playwright (`async_api`) with a persistent Chrome/Chromium profile so you stay
logged into LinkedIn, Welcome to the Jungle, HelloWork, Workday, etc.
Features:
- True concurrent multi-tab execution (`asyncio.gather`) across 5 tabs at once
- Auto-dismisses French/EU cookie consent banners (Didomi, OneTrust, Axeptio, etc.)
- Follows per-tab external ATS redirects (`page.expect_popup()`)
- Auto-fills standard French & LinkedIn Easy Apply / SmartRecruiters / HelloWork fields
- Injects tailored `CV_*.pdf` and `LM_*.pdf` (including FileChooser fallback)
- Unchecks LinkedIn's pre-checked 'Follow company' checkbox
- Watches for submission confirmation and updates `data/applications.csv`
"""

import argparse
import asyncio
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeout, async_playwright

WORKSPACE = Path(__file__).resolve().parent.parent
PROFILE_PATH = WORKSPACE / "config" / "profile.json"
MASTER_PROFILE_PATH = WORKSPACE / "config" / "master_profile.md"
APPS_CSV_PATH = WORKSPACE / "data" / "applications.csv"
UNANSWERED_QUESTIONS_PATH = WORKSPACE / "data" / "unanswered_questions.json"
CHROME_PROFILE_DIR = WORKSPACE / ".chrome_profile"

CONFIRMATION_PHRASES = [
    "votre candidature a été envoyée",
    "candidature envoyée",
    "application sent",
    "your application was sent",
    "merci pour votre candidature",
    "thank you for applying",
    "candidature enregistrée",
]


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_custom_rules(path: Path = PROFILE_PATH) -> List[Dict[str, str]]:
    """Load user-defined keyword->answer rules from config/profile.json."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("custom_rules", [])
        if not isinstance(rules, list):
            return []
        return [
            {"keyword": str(r.get("keyword", "")).strip().lower(), "answer": str(r.get("answer", "")).strip()}
            for r in rules
            if str(r.get("keyword", "")).strip() and str(r.get("answer", "")).strip()
        ]
    except Exception:
        return []


def match_custom_rule(question: str, custom_rules: List[Dict[str, str]]) -> str:
    """Return the answer for the first custom rule whose keyword matches question."""
    q_low = (question or "").lower()
    for rule in custom_rules:
        if rule["keyword"] in q_low:
            return rule["answer"]
    return ""


def load_saved_unanswered_answers(path: Path = UNANSWERED_QUESTIONS_PATH) -> Dict[str, str]:
    """Load user-filled answers from data/unanswered_questions.json keyed by normalized question."""
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return {}
        return {
            (e.get("question") or "").strip().lower(): str(e.get("answer")).strip()
            for e in entries
            if (e.get("question") or "").strip() and str(e.get("answer") or "").strip()
        }
    except Exception:
        return {}


def log_unanswered_question(
    field_type: str,
    question: str,
    options: List[str],
    company: str = "",
    url: str = "",
    path: Path = UNANSWERED_QUESTIONS_PATH,
) -> None:
    """Append a deduplicated unanswered question with blank answer to data/unanswered_questions.json."""
    q_clean = re.sub(r"\s+", " ", question).strip()
    if not q_clean:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    entries: List[Dict[str, Any]] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                entries = loaded
        except Exception:
            entries = []

    norm_key = (field_type.lower(), q_clean.lower())
    for existing in entries:
        if (
            str(existing.get("field_type", "")).lower(),
            str(existing.get("question", "")).strip().lower(),
        ) == norm_key:
            return

    entries.append(
        {
            "field_type": field_type,
            "question": q_clean,
            "options": options,
            "company": company,
            "url": url,
            "answer": "",
        }
    )
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# Known multi-word or symbolic skills to match safely against config/master_profile.md
_PROFILE_SKILL_CATALOG = [
    "c++", "c#", "typescript", "javascript", "python", "java", "sql", "oracle",
    "sqlite", "postgresql", "plpgsql", "html", "html5", "css", "css3", "php",
    "shell", "bash", "gdscript", "docker", "linux", "unix", "git", "react",
    "node.js", "three.js", "vite", "supabase", "valgrind", "gdb",
    "réseaux", "réseau", "network", "networking", "cryptographie", "sécurité",
    "cybersécurité", "cybersecurity", "http", "tcp", "frontend", "backend",
    "base de données", "application web", "devops", "eclipse", "virtualbox",
]


def load_master_profile_skills(path: Path = MASTER_PROFILE_PATH) -> Set[str]:
    """Extract verified skills and domains present in config/master_profile.md."""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    verified: Set[str] = set()
    for skill in _PROFILE_SKILL_CATALOG:
        if len(skill) <= 3 and skill.isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", text):
                verified.add(skill)
        elif skill in text:
            verified.add(skill)
    # Full-stack web evidence from verified repos (chaski-booking, Transcendence, portfolio-iut, webserv)
    if any(k in text for k in ("typescript", "react", "plpgsql", "supabase", "html", "php")):
        verified.update({"frontend", "backend", "base de données", "application web", "fullstack"})
    return verified


def classify_yes_no_question(question: str, verified_skills: Set[str]) -> Optional[Tuple[str, ...]]:
    """Return ('Non', 'No') or ('Oui', 'Yes') for a form question grounded in master_profile.md."""
    q = question.lower()

    # 1. Explicit "No" cases (visa/disability, higher degree than BUT 2, or unverified domains like mobile)
    if any(k in q for k in ("visa", "sponsor", "parrainage", "handicap", "disability")):
        return ("Non", "No")
    if any(k in q for k in ("bac+5", "bac +5", "master 2", "m2", "fin d'études", "fin d'étude", "pfe")):
        if not any(k in q for k in ("bac+2", "bac +2", "bac+3", "bac +3", "but 2", "but 3")):
            return ("Non", "No")
    if any(k in q for k in (
        "développement mobile", "dev mobile", "mobile dev", "application mobile",
        "applications mobiles", "android", "ios", "flutter", "react native", "kotlin", "swift",
    )):
        return ("Non", "No")

    # 2. Recruitment events & AI coding tools
    if any(k in q for k in ("événement", "événements", "invitation", "formation de code")):
        return ("Oui", "Yes")
    if any(k in q for k in ("outils d'ia", "outil d'ia", "claude", "cursor", "copilot", "chatgpt", "ia pour développer", "ai tools")):
        return ("Oui", "Yes")

    # 3. Verified full-stack / web / cursus / work authorization questions
    if any(k in q for k in (
        "application web", "frontend", "backend", "base de donnée", "base de données",
        "fullstack", "full-stack", "full stack",
    )):
        if "application web" in verified_skills or "frontend" in verified_skills:
            return ("Oui", "Yes")

    if any(k in q for k in (
        "autoris", "légale", "work", "france", "stage", "convention", "disponib",
        "majeur", "bac+2", "bac +2", "bac+3", "bac +3", "but 2", "but2",
        "hybride", "présentiel", "remote", "télétravail", "english", "french",
        "français", "anglais", "degree", "diplôme", "projet académique", "projets académiques",
    )):
        return ("Oui", "Yes")

    # 4. Dynamic match against verified skills from config/master_profile.md
    for skill in verified_skills:
        if len(skill) <= 3 and skill.isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", q):
                return ("Oui", "Yes")
        elif skill in q:
            return ("Oui", "Yes")

    return None


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


async def accept_cookies_if_present(page: Page) -> bool:
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
            if await loc.is_visible(timeout=700):
                await loc.click()
                await page.wait_for_timeout(300)
                return True
        except Exception:
            continue

    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for sel in selectors[:6]:
            try:
                loc = frame.locator(sel).first
                if await loc.is_visible(timeout=400):
                    await loc.click()
                    await page.wait_for_timeout(300)
                    return True
            except Exception:
                continue
    return False


async def click_apply_and_follow_redirect(page: Page) -> Tuple[Page, bool]:
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
    try:
        await page.wait_for_selector(
            "button.jobs-apply-button, button:has-text('Candidature simplifiée'), button:has-text('Easy Apply'), button:has-text('Postuler'), a:has-text('Postuler')",
            timeout=12000,
        )
    except Exception:
        pass

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if not await loc.is_visible(timeout=1200):
                continue

            initial_url = page.url
            try:
                async with page.expect_popup(timeout=6000) as popup_info:
                    await loc.click()
                new_page = await popup_info.value
                await new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                await new_page.wait_for_timeout(1000)
                await accept_cookies_if_present(new_page)
                for sub_sel in [
                    "a:has-text('Apply for this job')",
                    "a:has-text('Postuler à ce poste')",
                    "button:has-text('Apply for this job')",
                    "button:has-text('Postuler à ce poste')",
                    "a.postings-btn[href*='/apply']",
                ]:
                    try:
                        sub_btn = new_page.locator(sub_sel).first
                        if await sub_btn.is_visible(timeout=1000):
                            await sub_btn.click()
                            await new_page.wait_for_load_state("domcontentloaded", timeout=12000)
                            await new_page.wait_for_timeout(800)
                            break
                    except Exception:
                        continue
                return new_page, True
            except PlaywrightTimeout:
                await page.wait_for_timeout(1200)
                if page.url != initial_url:
                    await accept_cookies_if_present(page)
                return page, True
        except Exception:
            continue
    return page, False


async def get_label_text(page: Page, el) -> str:
    parts = []
    for attr in ("aria-label", "placeholder", "name", "id"):
        val = await el.get_attribute(attr)
        if val:
            parts.append(val)
    el_id = await el.get_attribute("id")
    if el_id:
        try:
            lbl = page.locator(f"label[for='{el_id}']").first
            if await lbl.count() > 0:
                parts.append(await lbl.inner_text())
        except Exception:
            pass
    try:
        parent_lbl = el.locator(
            "xpath=ancestor-or-self::div[contains(@class,'fb-dash-form-element') or contains(@class,'jobs-easy-apply-form-element')][1]"
        ).first
        if await parent_lbl.count() > 0:
            parts.append(await parent_lbl.inner_text())
    except Exception:
        pass
    try:
        prev = el.locator("xpath=preceding-sibling::*[1]").first
        if await prev.count() > 0:
            parts.append(await prev.inner_text())
    except Exception:
        pass
    return " ".join(parts).lower()


async def get_human_question_text(page: Page, el, exclude_texts: Optional[List[str]] = None) -> str:
    """Extract clean human-readable question text without raw HTML name/id attributes."""
    candidates: List[str] = []

    # 1. <fieldset><legend> (either el itself or ancestor fieldset)
    try:
        legend = el.locator("legend, xpath=ancestor::fieldset[1]/legend").first
        if await legend.count() > 0:
            txt = re.sub(r"\s+", " ", await legend.inner_text()).strip()
            if txt:
                candidates.append(txt)
    except Exception:
        pass

    # 2. <label for="...">
    try:
        el_id = await el.get_attribute("id")
        if el_id:
            lbl = page.locator(f"label[for='{el_id}']").first
            if await lbl.count() > 0:
                txt = re.sub(r"\s+", " ", await lbl.inner_text()).strip()
                if txt:
                    candidates.append(txt)
    except Exception:
        pass

    # 3. Wrapping <label>
    try:
        wrap_lbl = el.locator("xpath=ancestor::label[1]").first
        if await wrap_lbl.count() > 0:
            txt = re.sub(r"\s+", " ", await wrap_lbl.inner_text()).strip()
            if txt:
                candidates.append(txt)
    except Exception:
        pass

    # 4. aria-labelledby
    try:
        labelled_by = await el.get_attribute("aria-labelledby")
        if labelled_by:
            lbl_parts = []
            for ref_id in labelled_by.split():
                ref = page.locator(f"#{ref_id}").first
                if await ref.count() > 0:
                    lbl_parts.append((await ref.inner_text()).strip())
            joined = re.sub(r"\s+", " ", " ".join(lbl_parts)).strip()
            if joined:
                candidates.append(joined)
    except Exception:
        pass

    # 5. aria-label / placeholder
    for attr in ("aria-label", "placeholder"):
        try:
            val = await el.get_attribute(attr)
            if val and val.strip():
                candidates.append(val.strip())
        except Exception:
            pass

    # 6. Closest preceding label / heading / paragraph / span
    try:
        prev = el.locator(
            "xpath=preceding-sibling::*[self::label or self::h1 or self::h2 or self::h3 or self::h4 or self::p or self::span or self::div][1]"
        ).first
        if await prev.count() > 0:
            txt = re.sub(r"\s+", " ", await prev.inner_text()).strip()
            if txt and len(txt) < 300:
                candidates.append(txt)
    except Exception:
        pass

    # 7. Parent form container fallback
    try:
        parent_el = el.locator(
            "xpath=ancestor-or-self::div[contains(@class,'fb-dash-form-element') or contains(@class,'jobs-easy-apply-form-element') or contains(@class,'field') or contains(@class,'question')][1]"
        ).first
        if await parent_el.count() > 0:
            txt = re.sub(r"\s+", " ", await parent_el.inner_text()).strip()
            if exclude_texts:
                for ex in exclude_texts:
                    if ex:
                        txt = txt.replace(ex, " ")
                txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                candidates.append(txt)
    except Exception:
        pass

    # Return the first meaningful candidate (preferring specific legend/label over broad container)
    for c in candidates:
        if c:
            return c[:250]
    return ""


async def get_form_root(page: Page):
    try:
        modal = page.locator(".jobs-easy-apply-modal, div[role='dialog'][aria-modal='true']").first
        if await modal.count() > 0 and await modal.is_visible(timeout=400):
            return modal
    except Exception:
        pass
    return page


async def fill_matching_fields(page: Page, answers: Dict[str, str]) -> List[str]:
    field_rules = [
        (["prénom", "prenom", "first name", "firstname", "first_name", "fname"], answers["prenom"]),
        (["nom", "last name", "lastname", "last_name", "lname", "surname"], answers["nom"]),
        (["full name", "nom complet"], answers["nom_complet"]),
        (["e-mail", "email", "courriel", "mail"], answers["email"]),
        (["téléphone", "telephone", "phone", "mobile", "tel", "06 ", "07 "], answers["telephone"]),
        (["ville", "city", "localisation", "location"], answers["ville"]),
        (["école", "ecole", "school", "université", "university"], answers["ecole"]),
        (["niveau", "level", "études", "diplôme"], answers["niveau"]),
        (["disponibilité", "disponibilite", "availability", "date de début"], answers["disponibilite"]),
        (["durée", "duree", "duration"], answers["duree"]),
        (["github"], answers["github"]),
        (["linkedin"], answers["linkedin"]),
    ]

    filled: List[str] = []
    root = await get_form_root(page)
    inputs = root.locator("input:visible, textarea:visible")
    count = await inputs.count()
    for i in range(count):
        el = inputs.nth(i)
        try:
            itype = (await el.get_attribute("type") or "text").lower()
            if itype in {"hidden", "file", "submit", "button", "checkbox", "radio", "search"}:
                continue
            if (await el.input_value() or "").strip():
                continue
            hint = await get_label_text(page, el)
            for patterns, value in field_rules:
                if any(p in hint for p in patterns):
                    await el.fill(value)
                    filled.append(f"{patterns[0]} -> {value}")
                    break
        except Exception:
            continue
    return filled


async def try_fill_date_or_calendar(page: Page, el, is_end: bool) -> Optional[str]:
    """Fill <input type='date'>, text date input, or click through a calendar selection table."""
    iso_val = "2026-07-16" if is_end else "2026-05-03"
    fr_val = "16/07/2026" if is_end else "03/05/2026"
    target_day = "16" if is_end else "3"
    target_month_idx = "6" if is_end else "4"  # 0-based: May=4, July=6
    target_month_names = ("juillet", "july") if is_end else ("mai", "may")

    itype = (await el.get_attribute("type") or "text").lower()
    if itype == "date":
        await el.fill(iso_val)
        return iso_val

    is_readonly = await el.get_attribute("readonly") is not None
    if not is_readonly:
        try:
            await el.fill(fr_val)
            await el.press("Escape")
            if (await el.input_value() or "").strip():
                return fr_val
        except Exception:
            pass

    # Click to open calendar selection table (datepicker popup)
    try:
        await el.click()
        await page.wait_for_timeout(400)
        cal = page.locator("[role='grid']:visible, table:has(td):visible, .artdeco-datepicker:visible").last
        if await cal.count() > 0 and await cal.is_visible(timeout=800):
            # Try month/year <select> inside calendar container if present
            parent = cal.locator("xpath=ancestor::div[1]").first
            for sel in await parent.locator("select:visible").all():
                opts = [await o.inner_text() for o in await sel.locator("option").all()]
                if any("2026" in o for o in opts):
                    await sel.select_option(label=next(o for o in opts if "2026" in o))
                elif any(m in o.lower() for o in opts for m in target_month_names):
                    m_lbl = next(o for o in opts if any(m in o.lower() for m in target_month_names))
                    await sel.select_option(label=m_lbl)

            # Advance next-month arrow up to 8 times until target month appears
            for _ in range(8):
                cal_txt = (await parent.inner_text()).lower()
                if any(m in cal_txt for m in target_month_names) and "2026" in cal_txt:
                    break
                nxt = parent.locator("button[aria-label*='next' i], button[aria-label*='suivant' i], button:has-text('>')").first
                if await nxt.count() > 0 and await nxt.is_visible(timeout=300):
                    await nxt.click()
                    await page.wait_for_timeout(200)
                else:
                    break

            # Click exact day cell
            day_btn = cal.locator(
                f"button:text-is('{target_day}'), td:text-is('{target_day}'), [data-month='{target_month_idx}'] button:text-is('{target_day}')"
            ).first
            if await day_btn.count() > 0 and await day_btn.is_visible(timeout=500):
                await day_btn.click()
                await page.wait_for_timeout(300)
                return f"calendar -> {fr_val}"
    except Exception:
        pass
    return None


async def answer_common_questions(
    page: Page,
    answers: Dict[str, str],
    verified_skills: Optional[Set[str]] = None,
    seen_unanswered: Optional[Set[str]] = None,
    step_unanswered: Optional[List[str]] = None,
    company: str = "",
    url: str = "",
) -> List[str]:
    if verified_skills is None:
        verified_skills = load_master_profile_skills()
    if seen_unanswered is None:
        seen_unanswered = set()
    if step_unanswered is None:
        step_unanswered = []
    saved_answers = load_saved_unanswered_answers()
    custom_rules = load_custom_rules()
    answered: List[str] = []
    root = await get_form_root(page)

    # 1. Text & numeric inputs
    try:
        inputs = root.locator("input:visible, textarea:visible")
        for i in range(await inputs.count()):
            el = inputs.nth(i)
            try:
                itype = (await el.get_attribute("type") or "text").lower()
                if itype in {"hidden", "file", "submit", "button", "checkbox", "radio", "search", "password"}:
                    continue
                if (await el.input_value() or "").strip():
                    continue
                q = await get_label_text(page, el)
                if "search" in q and "job" not in q:
                    continue
                is_num = itype == "number" or "numeric" in (await el.get_attribute("inputmode") or "")

                val = ""
                if any(k in q for k in ("your title", "votre titre", "intitulé du poste")):
                    val = "Étudiant BUT 2 Informatique / Développeur (École 42)"
                elif any(k in q for k in ("company", "entreprise", "société")) and "linkedin" not in q:
                    val = "École 42 Paris / IUT d'Orsay"
                elif "description" in q:
                    val = "Développement C/C++/TypeScript, systèmes Linux, Docker, SQL, réseaux et sécurité (projets Webserv, Transcendence, Chaski-Booking)."
                elif any(k in q for k in ("message au recruteur", "motivations", "message personnalisé", "pourquoi ce poste", "cover letter", "lettre de motivation")):
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
                elif any(k in q for k in ("date de fin", "end date", "jusqu", "to date", "fin de stage")):
                    res_date = await try_fill_date_or_calendar(page, el, is_end=True)
                    if res_date:
                        answered.append(f"end_date -> {res_date}")
                        continue
                elif any(k in q for k in ("date de début", "start date", "quand pouvez", "when can you", "disponib", "à partir", "date")):
                    res_date = await try_fill_date_or_calendar(page, el, is_end=False)
                    if res_date:
                        answered.append(f"start_date -> {res_date}")
                        continue
                elif any(k in q for k in ("salaire", "rémunération", "gratification", "salary", "compensation")):
                    val = "800" if is_num else "Gratification légale"
                elif any(k in q for k in ("ville", "city", "localisation")):
                    val = answers.get("ville", "Bures-sur-Yvette")
                elif is_num:
                    val = "1"

                if not val:
                    human_q = await get_human_question_text(page, el)
                    saved_val = saved_answers.get(human_q.lower(), "") if human_q else ""
                    if not saved_val:
                        saved_val = match_custom_rule(human_q or q, custom_rules)
                    if saved_val:
                        val = saved_val

                if val:
                    await el.fill(val)
                    answered.append(f"input -> {val[:30]}")
                else:
                    human_q = await get_human_question_text(page, el)
                    if human_q:
                        step_unanswered.append(human_q)
                        if human_q not in seen_unanswered:
                            seen_unanswered.add(human_q)
                            print(f"  [?] Unanswered input: {human_q}")
                            log_unanswered_question("input", human_q, [], company=company, url=url)
            except Exception:
                continue
    except Exception:
        pass

    # 2. Select dropdowns
    try:
        selects = root.locator("select:visible")
        for i in range(await selects.count()):
            sel = selects.nth(i)
            try:
                cur = (await sel.input_value() or "").strip()
                if cur and cur.lower() not in {"select an option", "sélectionner une option", ""}:
                    continue
                q = await get_label_text(page, sel)
                opts = [await o.inner_text() for o in await sel.locator("option").all()]
                opts = [o.strip() for o in opts if o.strip()]
                clean_opts = [o for o in opts if o.lower() not in {"select an option", "sélectionner une option", "choisir...", "--"}]
                human_q = await get_human_question_text(page, sel)
                saved_val = saved_answers.get(human_q.lower(), "") if human_q else ""
                if not saved_val:
                    saved_val = match_custom_rule(human_q or q, custom_rules)

                matched_opt = None
                if saved_val:
                    matched_opt = next((o for o in clean_opts if saved_val.lower() in o.lower()), None)
                if not matched_opt:
                    yn = classify_yes_no_question(q, verified_skills)
                    if yn == ("Non", "No"):
                        targets = ("Non", "No")
                    else:
                        targets = (
                            "Oui", "Yes", "France (+33)", "C1", "Courant", "Fluent",
                            "B2", "Intermédiaire", "Bac+2", "Bac +2", "BUT 2",
                            "1 an", "Moins d'un an", "3 mois",
                        )
                    for target in targets:
                        matched_opt = next((o for o in clean_opts if target.lower() in o.lower()), None)
                        if matched_opt:
                            break

                if matched_opt:
                    await sel.select_option(label=matched_opt)
                    answered.append(f"select -> {matched_opt}")
                elif human_q:
                    step_unanswered.append(human_q)
                    if human_q not in seen_unanswered:
                        seen_unanswered.add(human_q)
                        print(f"  [?] Unanswered select: {human_q} | Options: {clean_opts}")
                        log_unanswered_question("select", human_q, clean_opts, company=company, url=url)
            except Exception:
                continue
    except Exception:
        pass

    # 3. Radio groups (fieldset & ARIA radiogroup)
    try:
        fieldsets = root.locator("fieldset:visible, [role='radiogroup']:visible")
        for i in range(await fieldsets.count()):
            fs = fieldsets.nth(i)
            try:
                if await fs.locator("input[type='radio']:checked, [role='radio'][aria-checked='true']").count() > 0:
                    continue
                all_labels = await fs.locator("label:visible, [role='radio']:visible").all()
                option_texts = [re.sub(r"\s+", " ", await lbl.inner_text()).strip() for lbl in all_labels]
                option_texts = [o for o in option_texts if o]
                human_q = await get_human_question_text(page, fs, exclude_texts=option_texts)
                legend = (human_q + " " + (await fs.inner_text())).lower()

                saved_val = saved_answers.get(human_q.lower(), "") if human_q else ""
                if not saved_val:
                    saved_val = match_custom_rule(human_q or legend, custom_rules)
                choices: Optional[Tuple[str, ...]] = (saved_val,) if saved_val else classify_yes_no_question(legend, verified_skills)
                if not choices:
                    if human_q:
                        step_unanswered.append(human_q)
                        if human_q not in seen_unanswered:
                            seen_unanswered.add(human_q)
                            print(f"  [?] Unanswered radio: {human_q} | Options: {option_texts}")
                            log_unanswered_question("radio", human_q, option_texts, company=company, url=url)
                    continue

                clicked_radio = False
                for lbl_txt in choices:
                    target_l = lbl_txt.lower()
                    for lbl in all_labels:
                        txt = (await lbl.inner_text()).strip().lower()
                        if txt == target_l or txt.startswith(f"{target_l} ") or txt.startswith(f"{target_l},"):
                            await lbl.click()
                            answered.append(f"radio -> {lbl_txt}")
                            clicked_radio = True
                            break
                    if clicked_radio:
                        break
                    lbl = fs.locator(f"label:has-text('{lbl_txt}')").first
                    if await lbl.count() > 0 and await lbl.is_visible(timeout=300):
                        await lbl.click()
                        answered.append(f"radio -> {lbl_txt}")
                        break
            except Exception:
                continue
    except Exception:
        pass

    # 4. Checkboxes & Consent Toggles (<input type="checkbox"> and role="checkbox")
    try:
        cbs = root.locator("input[type='checkbox']:visible, [role='checkbox']:visible")
        for i in range(await cbs.count()):
            cb = cbs.nth(i)
            try:
                cb_id = (await cb.get_attribute("id") or "").lower()
                if "follow-company" in cb_id:
                    continue

                role_attr = (await cb.get_attribute("role") or "").lower()
                is_checked = (
                    (await cb.get_attribute("aria-checked") == "true")
                    if role_attr == "checkbox"
                    else await cb.is_checked()
                )
                if is_checked:
                    continue

                human_q = await get_human_question_text(page, cb)
                q_low = (human_q + " " + await get_label_text(page, cb)).lower()
                is_required = (
                    await cb.get_attribute("required") is not None
                    or await cb.get_attribute("aria-required") == "true"
                    or "*" in human_q
                )
                is_marketing = any(
                    k in q_low
                    for k in ("newsletter", "marketing", "promotion", "offres commerciales", "partenaires", "actualités", "publicité")
                )
                is_legal = any(
                    k in q_low
                    for k in (
                        "accepte", "j'accepte", "conditions", "politique", "confidentialité",
                        "rgpd", "gdpr", "privacy", "terms", "certifie", "exactitude",
                        "exact", "lu et approuvé", "consent", "autorise le traitement",
                    )
                )
                saved_val = saved_answers.get(human_q.lower(), "").lower() if human_q else ""
                if not saved_val:
                    saved_val = match_custom_rule(human_q or q_low, custom_rules).lower()
                yn_cb = classify_yes_no_question(q_low, verified_skills)

                if saved_val in {"checked", "oui", "yes", "true", "1"} or is_legal or (is_required and not is_marketing) or (yn_cb == ("Oui", "Yes") and not is_marketing):
                    if role_attr == "checkbox":
                        await cb.click()
                    else:
                        await cb.check(force=True)
                    answered.append(f"checkbox -> checked ({human_q[:40]})")
                elif is_marketing and not is_required:
                    continue
                elif saved_val in {"unchecked", "non", "no", "false", "0"}:
                    continue
                elif human_q:
                    step_unanswered.append(human_q)
                    if human_q not in seen_unanswered:
                        seen_unanswered.add(human_q)
                        print(f"  [?] Unanswered checkbox: {human_q} | Options: ['checked', 'unchecked']")
                        log_unanswered_question("checkbox", human_q, ["checked", "unchecked"], company=company, url=url)
            except Exception:
                continue
    except Exception:
        pass

    return answered


async def inject_files(page: Page, cv_pdf: Path, lm_pdf: Path) -> List[str]:
    attached: List[str] = []
    contexts_to_check = [page, *[f for f in page.frames if f != page.main_frame]]
    for ctx in contexts_to_check:
        try:
            file_inputs = ctx.locator("input[type='file']")
            count = await file_inputs.count()
        except Exception:
            continue
        for i in range(count):
            el = file_inputs.nth(i)
            try:
                hint_parts = []
                for attr in ("name", "id", "aria-label", "data-qa", "class"):
                    v = await el.get_attribute(attr)
                    if v:
                        hint_parts.append(v)
                el_id = await el.get_attribute("id")
                if el_id:
                    lbl = ctx.locator(f"label[for='{el_id}']").first
                    if await lbl.count() > 0:
                        hint_parts.append(await lbl.inner_text())
                hint = " ".join(hint_parts).lower()

                if any(k in hint for k in ["lettre", "motivation", "cover", "lm"]):
                    await el.set_input_files(str(lm_pdf))
                    await el.dispatch_event("change")
                    attached.append(f"LM -> {lm_pdf.name}")
                elif any(k in hint for k in ["cv", "resume", "curriculum"]):
                    await el.set_input_files(str(cv_pdf))
                    await el.dispatch_event("change")
                    attached.append(f"CV -> {cv_pdf.name}")
                elif i == 0:
                    await el.set_input_files(str(cv_pdf))
                    await el.dispatch_event("change")
                    attached.append(f"CV (slot 0) -> {cv_pdf.name}")
                elif i == 1:
                    await el.set_input_files(str(lm_pdf))
                    await el.dispatch_event("change")
                    attached.append(f"LM (slot 1) -> {lm_pdf.name}")
            except Exception:
                continue

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
                if await btn.is_visible(timeout=600):
                    async with page.expect_file_chooser(timeout=2500) as fc_info:
                        await btn.click()
                    fc = await fc_info.value
                    await fc.set_files(str(cv_pdf))
                    attached.append(f"CV (chooser) -> {cv_pdf.name}")
                    await page.wait_for_timeout(1000)
                    break
            except Exception:
                continue

    return attached


async def uncheck_follow_company(page: Page) -> None:
    for sel in ("#follow-company-checkbox", "input[id*='follow-company' i]"):
        try:
            cb = page.locator(sel).first
            if await cb.count() > 0 and await cb.is_checked():
                lbl = page.locator("label[for='follow-company-checkbox']").first
                if await lbl.count() > 0 and await lbl.is_visible(timeout=400):
                    await lbl.click()
                else:
                    await cb.uncheck(force=True)
                print("  [*] Unchecked 'Follow company' checkbox.")
                return
        except Exception:
            continue


async def find_submit_button(page: Page):
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
        "form button[type='submit']:visible",
        "form input[type='submit']:visible",
    ]
    for sel in sub_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=600) and await btn.is_enabled():
                return btn
        except Exception:
            continue
    return None


async def try_click_next_step(page: Page) -> bool:
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
            if await btn.is_visible(timeout=700) and await btn.is_enabled():
                await btn.click()
                await page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    return False


async def check_submission_confirmed(page: Page) -> bool:
    try:
        if page.is_closed():
            return False
        body_text = (await page.locator("body").inner_text(timeout=600)).lower()
        return any(phrase in body_text for phrase in CONFIRMATION_PHRASES)
    except Exception:
        return False


async def process_offer_concurrently(
    page: Page,
    profile: Dict[str, Any],
    meta: Dict[str, Any],
    url: str,
    auto: bool,
    skip_external: bool,
) -> Tuple[Page, str]:
    """Run one offer end-to-end asynchronously so 5 tabs execute simultaneously."""
    company = meta["company"]
    cv_pdf = Path(meta["cv_pdf"])
    lm_pdf = Path(meta["lm_pdf"])

    print(f"[*] [{company}] Opening {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
    except Exception as e:
        print(f"[!] Bailout [{company}]: Timeout/load error opening offer URL ({e})")
        return page, "manual_portal"

    await page.wait_for_timeout(1200)
    await accept_cookies_if_present(page)
    active_page, clicked = await click_apply_and_follow_redirect(page)
    if active_page.is_closed():
        print(f"[!] Bailout [{company}]: Tab closed unexpectedly after clicking Apply")
        return active_page, "closed"

    ats = detect_ats_provider(active_page.url)
    print(f"[*] [{company}] Apply clicked: {clicked} | Target: {ats}")

    if not clicked:
        print(f"[!] Bailout [{company}]: Apply button not found on page ({active_page.url})")
        if auto:
            update_tracker_status(url, company, "manual_portal")
        return active_page, "manual_portal"

    if any(k in active_page.url.lower() for k in ("authwall", "/login", "connexion", "signin")):
        print(f"[!] Bailout [{company}]: Login wall detected ({active_page.url})")
        update_tracker_status(url, company, "manual_portal")
        return active_page, "manual_portal"

    verified_skills = load_master_profile_skills()
    seen_unanswered: Set[str] = set()
    last_step_unanswered: List[str] = []
    bailout_reason = ""
    final_status = "manual_portal"
    for step in range(1, 9):
        if active_page.is_closed():
            bailout_reason = f"Tab closed mid-fill at step {step}"
            break
        try:
            try:
                await active_page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            await active_page.wait_for_timeout(600)

            step_unanswered: List[str] = []
            filled = await fill_matching_fields(active_page, profile["form_answers"])
            filled.extend(
                await answer_common_questions(
                    active_page,
                    profile["form_answers"],
                    verified_skills=verified_skills,
                    seen_unanswered=seen_unanswered,
                    step_unanswered=step_unanswered,
                    company=company,
                    url=url,
                )
            )
            last_step_unanswered = step_unanswered
            attached = await inject_files(active_page, cv_pdf, lm_pdf)
            if attached:
                await active_page.wait_for_timeout(700)

            is_external = ats not in {"LinkedIn", "HelloWork", "Welcome to the Jungle"}
            if step == 1 and skip_external and is_external and len(filled) == 0 and len(attached) == 0:
                print(f"[!] Bailout [{company}]: External ATS redirect ({ats}: {active_page.url})")
                update_tracker_status(url, company, "manual_portal")
                return active_page, "manual_portal"

            submit_btn = await find_submit_button(active_page)
            if submit_btn is not None:
                await uncheck_follow_company(active_page)
                if auto:
                    await submit_btn.click()
                    await active_page.wait_for_timeout(1500)
                    update_tracker_status(url, company, "applied")
                    print(f"[+] [{company}] Submitted -> 'applied'!")
                    return active_page, "applied"
                print(f"[*] [{company}] Ready for manual review.")
                return active_page, "ready_for_review"

            if not await try_click_next_step(active_page):
                if step_unanswered:
                    bailout_reason = f"Blocked by unanswered required fields: {step_unanswered}"
                else:
                    bailout_reason = f"No Next/Submit button found at step {step} ({ats})"
                break
        except Exception as e:
            bailout_reason = f"Step {step} error ({e})"
            print(f"[!] [{company}] Step {step} error: {e}")
            break
    else:
        if last_step_unanswered:
            bailout_reason = f"Blocked by unanswered required fields: {last_step_unanswered}"
        else:
            bailout_reason = f"Form did not reach Submit after 8 steps ({ats})"

    if not bailout_reason:
        bailout_reason = f"Form incomplete ({ats})"

    if auto:
        print(f"[!] Bailout [{company}]: {bailout_reason}")
        update_tracker_status(url, company, final_status)
        print(f"[*] [{company}] Finished -> '{final_status}'")
    return active_page, final_status


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Playwright Async Multi-Tab Application Assistant")
    parser.add_argument("--meta", help="Path to output/<Company>/metadata.json")
    parser.add_argument("--metas", nargs="+", help="Multiple metadata.json paths to run simultaneously")
    parser.add_argument("--tabs", type=int, default=5, help="Simultaneous tabs per batch (default: 5)")
    parser.add_argument("--url", default="", help="Override job URL")
    parser.add_argument("--auto", action="store_true", help="Auto-submit simple forms without pausing (runs headless by default)")
    parser.add_argument("--headed", action="store_true", help="Show the browser window even in --auto mode")
    parser.add_argument("--skip-external", action="store_true", help="Skip external ATS redirects")
    parser.add_argument("--login", action="store_true", help="Open Chrome to log into LinkedIn & HelloWork")
    args = parser.parse_args()

    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = CHROME_PROFILE_DIR / "SingletonLock"
    if lock_file.exists() or lock_file.is_symlink():
        try:
            lock_file.unlink()
        except Exception:
            pass

    if args.login:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 860},
            )
            p1 = context.pages[0] if context.pages else await context.new_page()
            await p1.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            p2 = await context.new_page()
            await p2.goto("https://www.hellowork.com/fr-fr/candidat/connexion-inscription.html", wait_until="domcontentloaded")
            print("[*] Log into LinkedIn and HelloWork, then close the Chrome window when done.")
            try:
                while len(context.pages) > 0:
                    await asyncio.sleep(0.5)
            except Exception:
                pass
            await context.close()
        return 0

    meta_paths = [Path(m) for m in args.metas] if args.metas else ([Path(args.meta)] if args.meta else [])
    if not meta_paths:
        print("[!] --meta or --metas is required unless --login is used.")
        return 1

    profile = load_json(PROFILE_PATH)

    run_headless = bool(args.auto and not args.headed)
    launch_kwargs: Dict[str, Any] = {
        "user_data_dir": str(CHROME_PROFILE_DIR),
        "headless": run_headless,
        "viewport": {"width": 1280, "height": 860},
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if run_headless:
        launch_kwargs["channel"] = "chromium"
        launch_kwargs["user_agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(**launch_kwargs)

        max_workers = max(1, args.tabs)
        if args.auto and len(meta_paths) > 1:
            print(f"\n=== Running {len(meta_paths)} offer(s) with {max_workers}-slot rolling worker pool ===")
            sem = asyncio.Semaphore(max_workers)

            async def worker_task(idx: int, mp: Path) -> None:
                async with sem:
                    meta = load_json(mp)
                    u = meta.get("url", "")
                    if not u:
                        return
                    pg = await context.new_page()
                    active_pg = pg
                    try:
                        active_pg, _ = await process_offer_concurrently(
                            pg, profile, meta, u, auto=True, skip_external=args.skip_external
                        )
                    finally:
                        for tab_to_close in {pg, active_pg}:
                            if tab_to_close and not tab_to_close.is_closed() and len(context.pages) > 1:
                                try:
                                    await tab_to_close.close()
                                except Exception:
                                    pass

            await asyncio.gather(*(worker_task(i, mp) for i, mp in enumerate(meta_paths)))
            await context.close()
            return 0

        for start in range(0, len(meta_paths), max_workers):
            chunk = meta_paths[start : start + max_workers]
            print(f"\n=== Running {len(chunk)} tab(s) SIMULTANEOUSLY ({start + 1}-{start + len(chunk)}/{len(meta_paths)}) ===")

            tasks = []
            task_metas = []
            for idx_in_chunk, mp in enumerate(chunk):
                meta = load_json(mp)
                u = (args.url if len(meta_paths) == 1 and args.url else meta.get("url", ""))
                if not u:
                    continue
                pg = context.pages[0] if (idx_in_chunk == 0 and context.pages) else await context.new_page()
                tasks.append(
                    process_offer_concurrently(
                        pg, profile, meta, u, auto=args.auto, skip_external=args.skip_external
                    )
                )
                task_metas.append((meta, u))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            if args.auto:
                for res in results:
                    if isinstance(res, tuple):
                        pg_done, _ = res
                        if not pg_done.is_closed() and len(context.pages) > 1:
                            try:
                                await pg_done.close()
                            except Exception:
                                pass
            else:
                open_review = [
                    (res[0], meta, u)
                    for res, (meta, u) in zip(results, task_metas)
                    if isinstance(res, tuple) and not res[0].is_closed()
                ]
                if open_review:
                    print(f"[*] {len(open_review)} tab(s) open for manual review. Submit & close tabs when done.")
                    confirmed_urls = set()
                    try:
                        while any(not pg.is_closed() for pg, _, _ in open_review):
                            for pg, meta, u in open_review:
                                if u not in confirmed_urls and await check_submission_confirmed(pg):
                                    confirmed_urls.add(u)
                                    update_tracker_status(u, meta["company"], "applied")
                                    print(f"[+] Confirmed submission for {meta['company']}! Marked 'applied'.")
                            await asyncio.sleep(0.5)
                    except Exception:
                        pass

        await context.close()
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
