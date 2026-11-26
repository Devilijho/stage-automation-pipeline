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
import fcntl
import json
import os
import re
import sys
import urllib.request
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
LLM_CACHE_PATH = WORKSPACE / "data" / "llm_answer_cache.json"
CHROME_PROFILE_DIR = WORKSPACE / ".chrome_profile"
DEBUG_DIR = WORKSPACE / "debug"
MANUAL_CURRENT_PATH = WORKSPACE / "data" / "manual_current.json"
MANUAL_SKIP_PATH = WORKSPACE / "data" / ".manual_skip"


def _load_project_env(env_path: Path = WORKSPACE / ".env") -> None:
    """Load key=value pairs from project-local .env without touching global PC env."""
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                os.environ.setdefault(k, v)
    except Exception:
        pass


_load_project_env()

CONFIRMATION_PHRASES = [
    "votre candidature a été envoyée",
    "votre candidature a bien été",
    "candidature a bien été",
    "votre candidature a été transmise",
    "candidature bien envoyée",
    "candidature envoyée",
    "candidature transmise",
    "candidature prise en compte",
    "nous avons bien reçu votre candidature",
    "vous avez déjà postulé",
    "vous avez postulé",
    "déjà postulé",
    "postulé",
    "félicitations, votre candidature",
    "application sent",
    "application submitted",
    "your application was sent",
    "your application has been submitted",
    "we have received your application",
    "received your application",
    "thank you for your interest",
    "merci pour votre candidature",
    "merci !",
    "merci,",
    "thank you for applying",
    "thank you for your application",
    "thank you!",
    "candidature enregistrée",
]

EXPIRED_OFFER_PHRASES = [
    "offre expirée",
    "cette offre a expiré",
    "n'est plus disponible",
    "n’est plus disponible",
    "cette offre n'est plus",
    "cette offre n’est plus",
    "poste pourvu",
    "offre pourvue",
    "les candidatures ne sont plus acceptées",
    "no longer accepting applications",
    "this job has expired",
    "job has expired",
    "no longer available",
    "expired",
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


def load_llm_cache() -> Dict[str, str]:
    if not LLM_CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(LLM_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_llm_cache(cache: Dict[str, str]) -> None:
    try:
        LLM_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LLM_CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        pass


def ask_llm_for_field(
    field_type: str,
    question: str,
    options: List[str],
    answers: Dict[str, str],
    verified_skills: Set[str],
) -> str:
    """Lightweight cached LLM fallback for required unknown form questions.

    Auto-selects first available backend:
      1. Local Ollama (http://127.0.0.1:11434, 100% free / 0 tokens)
      2. Gemini API free tier (GEMINI_API_KEY -> gemini-2.5-flash-lite)
      3. Groq API free tier (GROQ_API_KEY -> llama-3.1-8b-instant)
      4. OpenAI API (OPENAI_API_KEY -> gpt-4o-mini)
    """
    q_clean = re.sub(r"\s+", " ", (question or "")).strip()
    if not q_clean:
        return ""
    cache_key = f"{field_type}|{q_clean.lower()}|{'||'.join(options[:20])}"
    cache = load_llm_cache()
    if cache_key in cache:
        return cache[cache_key]

    facts = (
        f"Candidate: {answers.get('nom_complet', 'Santiago FUENTES LLANOS')}, "
        f"Education: {answers.get('formation', 'BUT 2 Informatique')} ({answers.get('ecole', 'IUT d\'Orsay')}) & École 42 Paris, "
        f"Level: {answers.get('niveau', 'Bac+2 / BUT 2')}, "
        f"City: {answers.get('ville', 'Bures-sur-Yvette')} (91440, Essonne, Île-de-France), "
        f"Internship: {answers.get('disponibilite', '03/05 to 16/07, 3 months, extendable summer')}, "
        f"Work auth France: {answers.get('autorisation_travail', 'Oui')}, Visa sponsorship needed: Non, "
        f"Languages: Spanish (native), French (C1), English (C1), "
        f"Verified skills: {', '.join(sorted(verified_skills)[:25])}."
    )
    opt_line = f"\nAllowed options (pick ONE verbatim): {options[:25]}" if options else ""
    prompt = (
        f"You fill job application forms strictly grounded in these candidate facts:\n{facts}\n\n"
        f"Field type: {field_type}\nQuestion: {q_clean}{opt_line}\n"
        f"Rules: Reply with ONLY the concise answer value (no quotes, no explanation). "
        f"If options are listed, return ONE exact option string verbatim. Never invent skills. "
        f"If unsure, reply EMPTY."
    )

    provider = ""
    raw_ans = ""

    # 1. Local Ollama (free, unlimited)
    try:
        ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps({"model": ollama_model, "prompt": prompt, "stream": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            body = json.loads(r.read().decode("utf-8"))
            raw_ans = (body.get("response") or "").strip()
            provider = f"ollama:{ollama_model}"
    except Exception:
        pass

    # 2. Gemini API free tier
    if not raw_ans and os.environ.get("GEMINI_API_KEY"):
        g_key = os.environ["GEMINI_API_KEY"].strip()
        g_models = [os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip(), "gemini-3.5-flash"]
        for g_model in dict.fromkeys(m for m in g_models if m):
            try:
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{g_model}:generateContent?key={g_key}",
                    data=json.dumps({
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 40, "temperature": 0.0},
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    body = json.loads(r.read().decode("utf-8"))
                    raw_ans = (
                        body.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                        .strip()
                    )
                    provider = f"gemini:{g_model}"
                    break
            except Exception as e:
                err_txt = str(e)
                if "404" in err_txt:
                    continue
                print(f"  [!] Gemini API error ({g_model}): {err_txt[:120]}")
                break

    # 3. Groq / OpenAI compatible
    if not raw_ans and (os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        try:
            use_groq = bool(os.environ.get("GROQ_API_KEY"))
            api_key = (os.environ.get("GROQ_API_KEY") if use_groq else os.environ.get("OPENAI_API_KEY") or "").strip()
            endpoint = "https://api.groq.com/openai/v1/chat/completions" if use_groq else "https://api.openai.com/openai/v1/chat/completions"
            model = "llama-3.1-8b-instant" if use_groq else "gpt-4o-mini"
            req = urllib.request.Request(
                endpoint,
                data=json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 40,
                    "temperature": 0.0,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                body = json.loads(r.read().decode("utf-8"))
                raw_ans = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                provider = f"{'groq' if use_groq else 'openai'}:{model}"
        except Exception:
            pass

    ans = raw_ans.splitlines()[0].strip(" \"'`") if raw_ans else ""
    if not ans or ans.upper() == "EMPTY":
        return ""
    if options:
        exact = next((o for o in options if o.lower() == ans.lower()), None)
        if not exact:
            exact = next((o for o in options if ans.lower() in o.lower() or o.lower() in ans.lower()), None)
        if not exact:
            return ""
        ans = exact

    cache[cache_key] = ans
    save_llm_cache(cache)
    print(f"  [LLM:{provider}] {q_clean} -> {ans}")
    return ans


def log_unanswered_question(
    field_type: str,
    question: str,
    options: List[str],
    company: str = "",
    url: str = "",
    path: Path = UNANSWERED_QUESTIONS_PATH,
) -> None:
    """Manual unanswered_questions.json logging disabled in favor of cached LLM auto-answer."""
    return


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
    if any(k in q for k in ("fonction publique", "public official", "politically exposed", "membre proche de votre famille")):
        return ("Non", "No")
    if any(k in q for k in ("receive text", "sms messages", "communities for future")):
        return ("Non", "No")
    if any(k in q for k in (
        "privacy notice", "privacy policy", "read and agree", "politique de confidentialit",
        "lu et accept", "allowing", "contact me", "me contacter", "rgpd", "gdpr",
        "données personnelles", "personal data", "data processing", "traitement de mes données",
        "consent", "i agree", "j'accepte", "j’accepte",
    )):
        return ("Oui", "Yes")
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
    APPS_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = APPS_CSV_PATH.parent / ".applications.csv.lock"
    with lock_path.open("w", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            with APPS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if not rows:
                return
            headers = list(rows[0].keys())
            norm_url = (url or "").strip().rstrip("/").lower()
            updated = False
            for r in rows:
                row_url = (r.get("URL") or "").strip().rstrip("/").lower()
                if norm_url:
                    matched = row_url == norm_url
                else:
                    matched = (r.get("Company") or "").strip().lower() == (company or "").strip().lower()
                if not matched:
                    continue
                if (r.get("Status") or "").strip().lower() == "applied" and new_status != "applied":
                    continue
                r["Status"] = new_status
                updated = True
            if updated:
                with APPS_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=headers)
                    writer.writeheader()
                    writer.writerows(rows)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


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
    if "jobteaser.com" in u:
        return "JobTeaser"
    if "indeed.com" in u:
        return "Indeed"
    if "apec.fr" in u:
        return "APEC"
    if "meteojob.com" in u:
        return "Meteojob"
    if "hellowork.com" in u:
        return "HelloWork"
    if "linkedin.com" in u:
        return "LinkedIn"
    if "taleo.net" in u:
        return "Taleo"
    return "External Portal"


LOGIN_URLS: Dict[str, Tuple[str, str]] = {
    "linkedin": ("LinkedIn", "https://www.linkedin.com/login"),
    "hellowork": ("HelloWork", "https://www.hellowork.com/fr-fr/candidat/connexion-inscription.html"),
    "wttj": ("Welcome to the Jungle", "https://www.welcometothejungle.com/fr"),
    "jobteaser": ("JobTeaser", "https://www.jobteaser.com/fr/users/sign_in"),
    "indeed": ("Indeed", "https://fr.indeed.com/account/login"),
    "apec": ("APEC", "https://www.apec.fr"),
    "meteojob": ("Meteojob", "https://www.meteojob.com/"),
}


async def accept_cookies_if_present(page: Page) -> bool:
    selectors = [
        "#didomi-notice-agree-button",
        "#onetrust-accept-btn-handler",
        "#axeptio_btn_acceptAll",
        "#tarteaucitronPersonalize2",
        "[data-testid='uc-accept-all-button']",
        "button:has-text('Tout accepter')",
        "button:has-text('Accepter et continuer')",
        "button:has-text('Accepter tout')",
        "button:has-text(\"J'accepte\")",
        "button:has-text('Accepter')",
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
        "button:has-text('Accept cookies')",
        "button:has-text('Accept')",
        "button:has-text('I agree')",
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
        for sel in selectors:
            try:
                loc = frame.locator(sel).first
                if await loc.is_visible(timeout=350):
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
        "a[role='button']:has-text('Postuler'):visible",
        "button:has-text('Apply'):visible",
        "a:has-text('Apply'):visible",
        "a[role='button']:has-text('Apply'):visible",
    ]
    try:
        await page.wait_for_selector(
            "button.jobs-apply-button, button:has-text('Candidature simplifiée'), button:has-text('Easy Apply'), button:has-text('Postuler'), a:has-text('Postuler'), button:has-text('Apply'), a:has-text('Apply')",
            timeout=12000,
        )
    except Exception:
        pass

    async def _handle_new_page(new_page: Page) -> Page:
        await new_page.wait_for_load_state("domcontentloaded", timeout=15000)
        await new_page.wait_for_timeout(1000)
        await accept_cookies_if_present(new_page)
        for sub_sel in [
            "a:has-text('Apply for this job'):visible",
            "a:has-text('Postuler à ce poste'):visible",
            "button:has-text('Apply for this job'):visible",
            "button:has-text('Postuler à ce poste'):visible",
            "button:has-text('Postulez maintenant'):visible",
            "a:has-text('Postulez maintenant'):visible",
            "button:has-text('Postuler maintenant'):visible",
            "a:has-text('Postuler maintenant'):visible",
            "button:has-text('Apply Now'):visible",
            "a:has-text('Apply Now'):visible",
            "button:has-text('APPLY'):visible",
            "a:has-text('APPLY'):visible",
            "a.postings-btn[href*='/apply']:visible",
            "button[data-testid*='apply' i]:visible",
            "a[data-testid*='apply' i]:visible",
        ]:
            try:
                sub_btn = new_page.locator(sub_sel).first
                if await sub_btn.is_visible(timeout=1000):
                    try:
                        async with new_page.expect_popup(timeout=2500) as sub_pop:
                            await sub_btn.click()
                        new_page = await sub_pop.value
                        await new_page.wait_for_load_state("domcontentloaded", timeout=12000)
                    except PlaywrightTimeout:
                        await new_page.wait_for_load_state("domcontentloaded", timeout=12000)
                    await new_page.wait_for_timeout(800)
                    await accept_cookies_if_present(new_page)
                    break
            except Exception:
                continue
        return new_page

    # Scan main page and iframes for the Apply trigger
    search_contexts = [page, *[f for f in page.frames if f != page.main_frame]]
    for ctx in search_contexts:
        for sel in selectors:
            try:
                loc = ctx.locator(sel).first
                if not await loc.is_visible(timeout=1000):
                    continue

                initial_url = page.url
                is_easy_apply_sel = ("jobs-apply-button" in sel) or ("easy apply" in sel.lower()) or ("candidature simplifiée" in sel.lower())
                if is_easy_apply_sel:
                    await loc.click()
                    await page.wait_for_timeout(1200)
                    return page, True

                before_pages = list(page.context.pages)
                try:
                    async with page.expect_popup(timeout=5000) as popup_info:
                        await loc.click()
                    new_page = await popup_info.value
                    return await _handle_new_page(new_page), True
                except PlaywrightTimeout:
                    await page.wait_for_timeout(1200)
                    # Only adopt a newly opened tab whose opener is THIS page (never steal another worker's tab)
                    owned_tabs = []
                    for p in page.context.pages:
                        if p in before_pages or p.is_closed():
                            continue
                        try:
                            if await p.opener() == page:
                                owned_tabs.append(p)
                        except Exception:
                            continue
                    if owned_tabs:
                        return await _handle_new_page(owned_tabs[-1]), True
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
        ea_modal = page.locator(".jobs-easy-apply-modal").first
        if await ea_modal.count() > 0 and await ea_modal.is_visible(timeout=400):
            return ea_modal
        modal = page.locator("div[role='dialog'][aria-modal='true']").first
        if await modal.count() > 0 and await modal.is_visible(timeout=400):
            return modal
    except Exception:
        pass
    return page


async def get_form_roots(page: Page) -> List[Tuple[Any, Any]]:
    """Return list of `(doc_ctx, form_root)` covering active modal/page and all iframes."""
    roots: List[Tuple[Any, Any]] = []
    main_root = await get_form_root(page)
    roots.append((page, main_root))
    if main_root is page:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                if await frame.locator("input, select, textarea, button, [role='button'], [role='radio']").count() > 0:
                    roots.append((frame, frame))
            except Exception:
                continue
    return roots


async def _commit_autocomplete_if_open(page: Page, el, is_location: bool = False) -> None:
    """If an input opened a typeahead/autocomplete dropdown (e.g. LinkedIn Location), pick the first option or dismiss it."""
    try:
        is_combo = is_location or (await el.get_attribute("role") == "combobox") or bool(await el.get_attribute("aria-autocomplete"))
        if not is_combo:
            return
        opt_sel = ".basic-typeahead__selectable:visible, .artdeco-typeahead__result:visible, [role='listbox'] [role='option']:visible"
        opt_fr = page.locator(f"{opt_sel}:has-text('France')").first
        opt_any = page.locator(opt_sel).first
        try:
            await opt_any.wait_for(state="visible", timeout=1400)
        except Exception:
            pass
        if await opt_fr.count() > 0 and await opt_fr.is_visible(timeout=300):
            await opt_fr.click()
            await page.wait_for_timeout(250)
            return
        if await opt_any.count() > 0 and await opt_any.is_visible(timeout=300):
            await opt_any.click()
            await page.wait_for_timeout(250)
            return
        if is_location and await opt_any.count() > 0:
            await el.press("ArrowDown")
            await page.wait_for_timeout(150)
            await el.press("Enter")
            await page.wait_for_timeout(150)
    except Exception:
        pass


async def _has_country_code_select(root: Any) -> bool:
    """Detect whether the form already has a separate phone country-code dropdown (e.g. France (+33))."""
    try:
        sels = root.locator("select:visible")
        for i in range(await sels.count()):
            s = sels.nth(i)
            val = (await s.input_value() or "").lower()
            txt = ""
            try:
                txt = (await s.locator("option:checked").first.inner_text() or "").lower()
            except Exception:
                pass
            if "+33" in val or "+33" in txt or "france (+33)" in txt:
                return True
    except Exception:
        pass
    return False


def _local_fr_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if digits.startswith("33") and len(digits) == 11:
        return "0" + digits[2:]
    if len(digits) == 9 and digits[0] in ("6", "7"):
        return "0" + digits
    return phone


async def fill_matching_fields(page: Page, answers: Dict[str, str]) -> List[str]:
    field_rules = [
        (["prénom", "prenom", "first name", "firstname", "first_name", "fname"], answers["prenom"]),
        (["nom", "last name", "lastname", "last_name", "lname", "surname"], answers["nom"]),
        (["full name", "nom complet"], answers["nom_complet"]),
        (["e-mail", "email", "courriel", "mail"], answers["email"]),
        (["téléphone", "telephone", "phone", "mobile", "tel", "06 ", "07 "], answers["telephone"]),
        (["code postal", "postal code", "postalcode", "codepostal", "zip"], "91440"),
        (["ville", "city", "localisation", "location"], answers["ville"]),
        (["école", "ecole", "school", "université", "university"], answers["ecole"]),
        (["niveau", "level", "études", "diplôme"], answers["niveau"]),
        (["disponibilité", "disponibilite", "availability", "date de début"], answers["disponibilite"]),
        (["durée", "duree", "duration"], answers["duree"]),
        (["github"], answers["github"]),
        (["linkedin"], answers["linkedin"]),
    ]

    filled: List[str] = []
    for doc_ctx, root in await get_form_roots(page):
        has_cc = await _has_country_code_select(root)
        inputs = root.locator("input:visible, textarea:visible")
        count = await inputs.count()
        for i in range(count):
            el = inputs.nth(i)
            try:
                itype = (await el.get_attribute("type") or "text").lower()
                if itype in {"hidden", "file", "submit", "button", "checkbox", "radio", "search"}:
                    continue
                cur_val = (await el.input_value() or "").strip()
                hint = await get_label_text(doc_ctx, el)
                is_phone_field = any(p in hint for p in ("téléphone", "telephone", "phone", "mobile", "tel"))
                if cur_val:
                    cur_digits = re.sub(r"\D+", "", cur_val)
                    if has_cc and is_phone_field and (cur_val.startswith("+33") or (len(cur_digits) == 9 and cur_digits[0] in ("6", "7"))):
                        local_num = _local_fr_phone(cur_val)
                        await el.fill(local_num)
                        await el.press("Tab")
                        filled.append(f"telephone(local) -> {local_num}")
                    continue
                for patterns, value in field_rules:
                    if patterns[0] == "nom" and any(bad in hint for bad in ("voie", "adresse", "address", "entreprise", "société", "preferred", "préféré")):
                        continue
                    if any(p in hint for p in patterns):
                        write_val = _local_fr_phone(value) if (has_cc and patterns[0] == "téléphone") else value
                        await el.fill(write_val)
                        await _commit_autocomplete_if_open(page, el, is_location=(patterns[0] == "ville"))
                        if patterns[0] == "téléphone":
                            await el.press("Tab")
                        filled.append(f"{patterns[0]} -> {write_val}")
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


async def extract_radio_options(doc_ctx: Any, fs: Any) -> List[Tuple[str, Any]]:
    """Extract `(option_text, clickable_locator)` for all radio options inside a fieldset/radiogroup."""
    results: List[Tuple[str, Any]] = []
    seen_texts: Set[str] = set()

    # 1. Inspect every radio input, ARIA radio, and LinkedIn custom radio option container
    candidates = await fs.locator("input[type='radio'], [role='radio'], .fb-text-selectable__option").all()
    for el in candidates:
        try:
            text = ""
            click_target = el
            tag = (await el.evaluate("e => e.tagName.toLowerCase()") or "")
            cls = (await el.get_attribute("class") or "")

            if "fb-text-selectable__option" in cls:
                lbl = el.locator("label").first
                if await lbl.count() > 0:
                    text = re.sub(r"\s+", " ", await lbl.inner_text()).strip()
                    click_target = lbl
                if not text:
                    text = re.sub(r"\s+", " ", await el.inner_text()).strip()
            elif tag == "input":
                el_id = await el.get_attribute("id")
                if el_id:
                    lbl = doc_ctx.locator(f"label[for='{el_id}']").first
                    if await lbl.count() > 0:
                        text = re.sub(r"\s+", " ", await lbl.inner_text()).strip()
                        click_target = lbl
                if not text:
                    wrap = el.locator("xpath=ancestor::label[1]").first
                    if await wrap.count() > 0:
                        text = re.sub(r"\s+", " ", await wrap.inner_text()).strip()
                        click_target = wrap
                if not text:
                    sib_txt = await el.evaluate(
                        """e => {
                            let nxt = e.nextSibling;
                            while (nxt) {
                                const t = (nxt.textContent || '').trim();
                                if (t) return t;
                                nxt = nxt.nextSibling;
                            }
                            if (e.parentElement) {
                                return (e.parentElement.innerText || '').trim();
                            }
                            return '';
                        }"""
                    )
                    text = re.sub(r"\s+", " ", sib_txt or "").strip()
                    if text and await el.is_visible():
                        click_target = el
                if not text:
                    text = (await el.get_attribute("aria-label") or await el.get_attribute("value") or "").strip()
            else:
                text = re.sub(r"\s+", " ", await el.inner_text()).strip()
                if not text:
                    text = (await el.get_attribute("aria-label") or "").strip()
                if not text:
                    lbl_by = await el.get_attribute("aria-labelledby")
                    if lbl_by:
                        parts = []
                        for ref_id in lbl_by.split():
                            ref = doc_ctx.locator(f"#{ref_id}").first
                            if await ref.count() > 0:
                                parts.append((await ref.inner_text()).strip())
                        text = re.sub(r"\s+", " ", " ".join(parts)).strip()

            if text and text.lower() not in seen_texts:
                seen_texts.add(text.lower())
                results.append((text, click_target))
        except Exception:
            continue

    # 2. Fallback: visible labels inside the fieldset
    if not results:
        for lbl in await fs.locator("label:visible").all():
            try:
                text = re.sub(r"\s+", " ", await lbl.inner_text()).strip()
                if text and text.lower() not in seen_texts:
                    seen_texts.add(text.lower())
                    results.append((text, lbl))
            except Exception:
                continue

    return results


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
    form_roots = await get_form_roots(page)
    for doc_ctx, root in form_roots:
        await _answer_common_questions_in_root(
            page, doc_ctx, root, answers, verified_skills,
            seen_unanswered, step_unanswered, saved_answers, custom_rules, answered,
        )
    return answered


async def _prune_blank_linkedin_experience(page: Page) -> bool:
    """If LinkedIn Easy Apply opened a blank Work Experience card with empty Month/Year dates, delete it instead of inventing dates."""
    try:
        del_btn = page.locator("button:has-text('Delete experience'):visible, button:has-text('Supprimer l’expérience'):visible").first
        if await del_btn.count() == 0 or not await del_btn.is_visible(timeout=250):
            return False
        sels = page.locator(".jobs-easy-apply-modal select:visible, div[role='dialog'] select:visible")
        has_empty_date = False
        for i in range(await sels.count()):
            cur = (await sels.nth(i).input_value() or "").strip().lower()
            txt = ""
            try:
                txt = (await sels.nth(i).locator("option:checked").first.inner_text() or "").strip().lower()
            except Exception:
                pass
            if cur in ("", "month", "year", "mois", "année") or txt in ("month", "year", "mois", "année"):
                has_empty_date = True
                break
        if has_empty_date:
            await del_btn.click()
            await page.wait_for_timeout(400)
            return True
    except Exception:
        pass
    return False


async def _answer_common_questions_in_root(
    page: Page,
    doc_ctx: Any,
    root: Any,
    answers: Dict[str, str],
    verified_skills: Set[str],
    seen_unanswered: Set[str],
    step_unanswered: List[str],
    saved_answers: Dict[str, str],
    custom_rules: List[Dict[str, str]],
    answered: List[str],
) -> None:

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
                q = await get_label_text(doc_ctx, el)
                human_q_pre = await get_human_question_text(doc_ctx, el)
                q_all = f"{q} {human_q_pre}".lower()
                if ("search" in q and "job" not in q) or any(
                    skip in q_all for skip in ("mots clés", "mots-clés", "rechercher")
                ):
                    continue
                is_num = itype == "number" or "numeric" in (await el.get_attribute("inputmode") or "")

                val = ""
                if any(k in q for k in ("your title", "votre titre", "intitulé du poste")):
                    val = "Étudiant BUT 2 Informatique / Développeur (École 42)"
                elif any(k in q for k in ("company", "entreprise", "société")) and "linkedin" not in q:
                    val = "École 42 Paris / IUT d'Orsay"
                elif "description" in q:
                    val = "Développement C/C++/TypeScript, systèmes Linux, Docker, SQL, réseaux et sécurité (projets Webserv, Transcendence, Chaski-Booking)."
                elif any(
                    k in q
                    for k in (
                        "message au recruteur",
                        "motivations",
                        "message personnalisé",
                        "pourquoi ce poste",
                        "expliquez-nous pourquoi",
                        "souhaitez nous rejoindre",
                        "pourquoi voulez-vous",
                        "cover letter",
                        "coverletter",
                        "motivationletter",
                        "lettre de motivation",
                    )
                ):
                    val = (
                        f"Bonjour, étudiant en {answers.get('formation', 'BUT 2 Informatique')} ({answers.get('ecole', 'IUT d\'Orsay')}) et à l'École 42 Paris, "
                        f"je recherche un stage de {answers.get('disponibilite', '3 mois du 03/05 au 16/07')}. "
                        f"Opérationnel en C/C++, TypeScript, Linux, Docker et SQL, je serais ravi de rejoindre votre équipe. "
                        f"Cordialement, {answers.get('nom_complet', 'Santiago FUENTES LLANOS')}."
                    )
                elif any(k in q_all for k in ("code postal", "postal code", "postalcode", "codepostal", "zip")):
                    val = "91440"
                elif any(k in q for k in ("adresse électronique", "e-mail", "email")):
                    val = answers.get("email", "")
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
                elif any(k in q for k in ("ville", "city", "localisation", "londres, paris", "paris, new york", "location")):
                    val = f"{answers.get('ville', 'Bures-sur-Yvette')}, France"
                elif is_num:
                    val = "1"

                if not val:
                    human_q = await get_human_question_text(doc_ctx, el)
                    saved_val = saved_answers.get(human_q.lower(), "") if human_q else ""
                    if not saved_val:
                        saved_val = match_custom_rule(human_q or q, custom_rules)
                    if saved_val:
                        val = saved_val

                if not val:
                    human_q = await get_human_question_text(doc_ctx, el)
                    is_req = (
                        (await el.get_attribute("required") is not None)
                        or ((await el.get_attribute("aria-required") or "").lower() == "true")
                        or ("*" in (human_q or ""))
                    )
                    if human_q and is_req:
                        llm_val = ask_llm_for_field("input", human_q, [], answers, verified_skills)
                        if llm_val:
                            val = llm_val

                if val:
                    await el.fill(val)
                    await _commit_autocomplete_if_open(page, el, is_location=any(k in q for k in ("ville", "city", "localisation", "location")))
                    answered.append(f"input -> {val[:30]}")
                else:
                    human_q = await get_human_question_text(doc_ctx, el)
                    is_req = (
                        (await el.get_attribute("required") is not None)
                        or ((await el.get_attribute("aria-required") or "").lower() == "true")
                        or ("*" in (human_q or ""))
                    )
                    if human_q and is_req:
                        step_unanswered.append(human_q)
                        if human_q not in seen_unanswered:
                            seen_unanswered.add(human_q)
                            print(f"  [?] Unanswered required input: {human_q}")
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
                cur_txt = ""
                try:
                    cur_txt = (await sel.locator("option:checked").first.inner_text() or "").strip().lower()
                except Exception:
                    pass
                placeholder_vals = {"", "select an option", "sélectionner une option", "veuillez sélectionner", "please select", "choisir...", "--", "select"}
                if cur and cur.lower() not in placeholder_vals and cur_txt not in placeholder_vals:
                    continue
                q = f"{await get_label_text(doc_ctx, sel)} {await get_human_question_text(doc_ctx, sel)}".lower()
                opts = [await o.inner_text() for o in await sel.locator("option").all()]
                opts = [o.strip() for o in opts if o.strip()]
                clean_opts = [
                    o for o in opts
                    if o.lower() not in {"select an option", "sélectionner une option", "veuillez sélectionner", "please select", "choisir...", "--"}
                ]
                human_q = await get_human_question_text(doc_ctx, sel)
                saved_val = saved_answers.get(human_q.lower(), "") if human_q else ""
                if not saved_val:
                    saved_val = match_custom_rule(human_q or q, custom_rules)

                matched_opt = None
                if saved_val:
                    matched_opt = next((o for o in clean_opts if saved_val.lower() in o.lower()), None)
                if not matched_opt:
                    if any(k in q for k in ("département", "departement")):
                        targets: Tuple[str, ...] = ("Essonne", "91 -", "91")
                    elif any(k in q for k in ("pays", "country", "résidence", "residence")):
                        targets = ("France",)
                    elif any(k in q for k in ("genre", "gender", "sexe")):
                        targets = ("Je préfère ne pas répondre", "Prefer not to", "Ne souhaite pas", "Homme", "Male")
                    elif any(k in q for k in ("appareil téléphonique", "phone device", "type de téléphone")):
                        targets = ("Mobile", "Portable", "Cell", "Cellulaire")
                    elif any(k in q for k in ("entendu parler", "hear about", "connu")):
                        targets = ("Job Board", "Site d'emploi", "Website", "Site web", "LinkedIn")
                    else:
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

                if not matched_opt and human_q:
                    is_req = (
                        (await sel.get_attribute("required") is not None)
                        or ((await sel.get_attribute("aria-required") or "").lower() == "true")
                        or ("*" in human_q)
                    )
                    if is_req and clean_opts:
                        matched_opt = ask_llm_for_field("select", human_q, clean_opts, answers, verified_skills) or None

                if matched_opt:
                    await sel.select_option(label=matched_opt)
                    answered.append(f"select -> {matched_opt}")
                elif human_q and clean_opts:
                    is_req = (
                        (await sel.get_attribute("required") is not None)
                        or ((await sel.get_attribute("aria-required") or "").lower() == "true")
                        or ("*" in human_q)
                    )
                    if is_req:
                        step_unanswered.append(human_q)
                        if human_q not in seen_unanswered:
                            seen_unanswered.add(human_q)
                            print(f"  [?] Unanswered required select: {human_q} | Options: {clean_opts}")
            except Exception:
                continue
    except Exception:
        pass

    # 3. Radio groups (fieldset & ARIA radiogroup)
    try:
        fieldsets = root.locator("fieldset, [role='radiogroup']")
        for i in range(await fieldsets.count()):
            fs = fieldsets.nth(i)
            try:
                if await fs.locator("input[type='radio'], [role='radio'], .fb-text-selectable__option").count() == 0:
                    continue
                if await fs.locator("label:visible, [role='radio']:visible, .fb-text-selectable__option:visible, input[type='radio']:visible").count() == 0:
                    continue
                if await fs.locator("input[type='radio']:checked, [role='radio'][aria-checked='true']").count() > 0:
                    continue
                opt_pairs = await extract_radio_options(doc_ctx, fs)
                option_texts = [t for t, _ in opt_pairs]
                human_q = await get_human_question_text(doc_ctx, fs, exclude_texts=option_texts)
                legend = (human_q + " " + (await fs.inner_text())).lower()

                saved_val = saved_answers.get(human_q.lower(), "") if human_q else ""
                if not saved_val:
                    saved_val = match_custom_rule(human_q or legend, custom_rules)
                choices: Optional[Tuple[str, ...]] = (saved_val,) if saved_val else classify_yes_no_question(legend, verified_skills)

                # Mandatory GDPR/privacy/contact-consent radios (e.g. Pretto Privacy Policy *)
                is_privacy_consent = any(
                    k in legend
                    for k in (
                        "privacy policy", "privacy notice", "politique de confidentialit",
                        "rgpd", "gdpr", "allowing", "contact me", "me contacter",
                        "données personnelles", "personal data", "consent", "j'accepte", "i agree",
                    )
                )
                if not choices and is_privacy_consent:
                    choices = ("Oui", "Yes", "J'accepte", "I agree", "Accept", "Accepter")

                if not choices and human_q and option_texts:
                    llm_choice = ask_llm_for_field("radio", human_q, option_texts, answers, verified_skills)
                    if llm_choice:
                        choices = (llm_choice,)
                if not choices:
                    if human_q:
                        step_unanswered.append(human_q)
                        if human_q not in seen_unanswered:
                            seen_unanswered.add(human_q)
                            print(f"  [?] Unanswered radio: {human_q} | Options: {option_texts}")
                    continue

                clicked_radio = False
                for lbl_txt in choices:
                    target_l = lbl_txt.lower()
                    for idx_opt, (opt_txt, opt_el) in enumerate(opt_pairs):
                        txt = opt_txt.strip().lower()
                        if txt == target_l or txt.startswith(f"{target_l} ") or txt.startswith(f"{target_l},") or (is_privacy_consent and target_l in txt):
                            try:
                                await opt_el.scroll_into_view_if_needed()
                                await opt_el.click(force=True)
                            except Exception:
                                try:
                                    await opt_el.evaluate("e => e.click()")
                                except Exception:
                                    pass
                            if await fs.locator("input[type='radio']:checked, [role='radio'][aria-checked='true']").count() == 0:
                                inps = fs.locator("input[type='radio']")
                                if idx_opt < await inps.count():
                                    try:
                                        await inps.nth(idx_opt).check(force=True)
                                    except Exception:
                                        try:
                                            await inps.nth(idx_opt).evaluate("e => e.click()")
                                        except Exception:
                                            pass
                            answered.append(f"radio -> {opt_txt[:40]}")
                            clicked_radio = True
                            break
                    if clicked_radio:
                        break
                    lbl = fs.locator(f"label:has-text('{lbl_txt}')").first
                    if await lbl.count() > 0 and await lbl.is_visible(timeout=300):
                        try:
                            await lbl.click(force=True)
                        except Exception:
                            await lbl.evaluate("e => e.click()")
                        answered.append(f"radio -> {lbl_txt}")
                        clicked_radio = True
                        break
                # Single-option required privacy/consent radio fallback
                if not clicked_radio and is_privacy_consent and len(opt_pairs) == 1:
                    opt_txt, opt_el = opt_pairs[0]
                    try:
                        await opt_el.click(force=True)
                    except Exception:
                        try:
                            await opt_el.evaluate("e => e.click()")
                        except Exception:
                            pass
                    answered.append(f"radio -> {opt_txt[:40]}")
            except Exception:
                continue
    except Exception:
        pass

    # 3a. Fallback for radio groups NOT wrapped in <fieldset> or [role='radiogroup']
    try:
        loose_radios = root.locator("input[type='radio']")
        handled_names: Set[str] = set()
        for i in range(await loose_radios.count()):
            r_el = loose_radios.nth(i)
            try:
                r_name = await r_el.get_attribute("name") or f"__idx_{i}"
                if r_name in handled_names:
                    continue
                handled_names.add(r_name)
                group_container = r_el.locator(
                    "xpath=ancestor::div[contains(@class,'fb-dash-form-element') or contains(@class,'jobs-easy-apply-form-element') or contains(@data-test-form-builder-radio-button-form-component,'true')][1]"
                ).first
                if await group_container.count() == 0:
                    group_container = r_el.locator("xpath=ancestor::div[2]").first
                if await group_container.count() == 0:
                    continue
                if await group_container.locator("input[type='radio']:checked").count() > 0:
                    continue
                opt_pairs = await extract_radio_options(doc_ctx, group_container)
                if not opt_pairs:
                    continue
                option_texts = [t for t, _ in opt_pairs]
                human_q = await get_human_question_text(doc_ctx, group_container, exclude_texts=option_texts)
                legend = (human_q + " " + (await group_container.inner_text())).lower()
                saved_val = saved_answers.get(human_q.lower(), "") if human_q else ""
                if not saved_val:
                    saved_val = match_custom_rule(human_q or legend, custom_rules)
                choices: Optional[Tuple[str, ...]] = (saved_val,) if saved_val else classify_yes_no_question(legend, verified_skills)
                if not choices and any(k in legend for k in ("privacy policy", "privacy notice", "allowing", "contact me", "me contacter", "rgpd", "gdpr", "consent")):
                    choices = ("Oui", "Yes", "J'accepte", "I agree")
                if not choices and human_q and option_texts:
                    llm_choice = ask_llm_for_field("radio", human_q, option_texts, answers, verified_skills)
                    if llm_choice:
                        choices = (llm_choice,)
                if not choices:
                    continue
                for lbl_txt in choices:
                    target_l = lbl_txt.lower()
                    matched = False
                    for opt_txt, opt_el in opt_pairs:
                        if opt_txt.strip().lower() == target_l or opt_txt.strip().lower().startswith(f"{target_l} "):
                            try:
                                await opt_el.click(force=True)
                            except Exception:
                                await opt_el.check(force=True)
                            answered.append(f"loose-radio -> {opt_txt[:40]}")
                            matched = True
                            break
                    if matched:
                        break
            except Exception:
                continue
    except Exception:
        pass

    # 3b. Button-based Yes/No toggle groups (e.g. Ashby Oui/Non buttons)
    try:
        yn_containers = root.locator(
            "div:has(> button:visible):has(button:has-text('Oui')):has(button:has-text('Non')), "
            "div:has(> button:visible):has(button:has-text('Yes')):has(button:has-text('No'))"
        )
        seen_yn_q: Set[str] = set()
        for i in range(await yn_containers.count()):
            box = yn_containers.nth(i)
            try:
                btns = await box.locator(":scope > button:visible").all()
                if len(btns) != 2:
                    continue
                btn_pairs = [(re.sub(r"\s+", " ", await b.inner_text()).strip(), b) for b in btns]
                btn_lows = {t.lower() for t, _ in btn_pairs}
                if btn_lows not in ({"oui", "non"}, {"yes", "no"}):
                    continue
                already_selected = False
                for _, b in btn_pairs:
                    cls = (await b.get_attribute("class") or "").lower()
                    if (
                        (await b.get_attribute("aria-pressed") == "true")
                        or (await b.get_attribute("aria-checked") == "true")
                        or (await b.get_attribute("data-selected") == "true")
                        or ("active" in cls or "selected" in cls or "checked" in cls)
                    ):
                        already_selected = True
                        break
                if already_selected:
                    continue

                parent_box = box.locator("xpath=ancestor::div[1]").first
                human_q = await get_human_question_text(doc_ctx, box, exclude_texts=[t for t, _ in btn_pairs])
                if not human_q and await parent_box.count() > 0:
                    raw_q = re.sub(r"\s+", " ", await parent_box.inner_text()).strip()
                    for t, _ in btn_pairs:
                        raw_q = re.sub(rf"\b{re.escape(t)}\b", " ", raw_q, flags=re.IGNORECASE)
                    human_q = re.sub(r"\s+", " ", raw_q).strip()[:250]
                if not human_q or human_q in seen_yn_q:
                    continue
                seen_yn_q.add(human_q)

                saved_val = saved_answers.get(human_q.lower(), "")
                if not saved_val:
                    saved_val = match_custom_rule(human_q, custom_rules)
                choices = (saved_val,) if saved_val else classify_yes_no_question(human_q, verified_skills)
                if not choices:
                    llm_choice = ask_llm_for_field("radio", human_q, [t for t, _ in btn_pairs], answers, verified_skills)
                    if llm_choice:
                        choices = (llm_choice,)
                if not choices:
                    continue

                for choice_txt in choices:
                    target_b = next((b for t, b in btn_pairs if t.lower() == choice_txt.lower()), None)
                    if target_b is not None:
                        await target_b.click()
                        answered.append(f"yn-btn -> {choice_txt} ({human_q[:35]})")
                        break
            except Exception:
                continue
    except Exception:
        pass

    # 4. Checkboxes & Consent Toggles (<input type="checkbox"> and role="checkbox", including sr-only styled inputs)
    try:
        cbs = root.locator("input[type='checkbox'], [role='checkbox']:visible")
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

                human_q = await get_human_question_text(doc_ctx, cb)
                q_low = (human_q + " " + await get_label_text(doc_ctx, cb)).lower()
                cb_name = (await cb.get_attribute("name") or "").lower()
                is_visible = await cb.is_visible()
                is_required = (
                    await cb.get_attribute("required") is not None
                    or await cb.get_attribute("aria-required") == "true"
                    or "*" in human_q
                )
                is_marketing = any(
                    k in q_low
                    for k in (
                        "newsletter", "marketing", "promotion", "offres commerciales", "partenaires",
                        "actualités", "publicité", "alertes par email", "alerte emploi",
                        "receive email alerts", "rester connecté", "stay signed in",
                        "se souvenir de moi", "remember me",
                    )
                )
                is_refusal = any(k in q_low for k in ("je refuse", "i refuse", "decline"))
                is_legal = (not is_refusal) and any(
                    k in f"{q_low} {cb_name}"
                    for k in (
                        "accepte", "j'accepte", "j’accepte", "conditions", "cgu", "politique", "confidentialité",
                        "rgpd", "gdpr", "privacy", "terms", "certifie", "exactitude",
                        "exact", "lu et approuvé", "consent", "autorise le traitement",
                        "données personnelles", "traitement de mes données", "cadre de ma candidature",
                    )
                )
                if is_refusal and not is_required:
                    continue
                # Ignore hidden non-legal checkboxes (e.g. off-screen filters)
                if not is_visible and not is_legal and not is_required:
                    continue
                saved_val = saved_answers.get(human_q.lower(), "").lower() if human_q else ""
                if not saved_val:
                    saved_val = match_custom_rule(human_q or q_low, custom_rules).lower()
                yn_cb = classify_yes_no_question(q_low, verified_skills)

                if saved_val in {"checked", "oui", "yes", "true", "1"} or is_legal or (is_required and not is_marketing) or (yn_cb == ("Oui", "Yes") and not is_marketing):
                    if role_attr == "checkbox":
                        await cb.click(force=True)
                    else:
                        try:
                            await cb.check(force=True)
                        except Exception:
                            pass
                        if not await cb.is_checked():
                            raw_id = await cb.get_attribute("id") or ""
                            lbl = doc_ctx.locator(f"label[for='{raw_id}']").first if raw_id else cb.locator("xpath=ancestor::label[1]").first
                            if await lbl.count() > 0:
                                await lbl.click(force=True)
                            else:
                                await cb.click(force=True)
                    answered.append(f"checkbox -> checked ({(human_q or cb_name)[:40]})")
                elif is_marketing and not is_required:
                    continue
                elif saved_val in {"unchecked", "non", "no", "false", "0"}:
                    continue
                elif human_q and is_required:
                    step_unanswered.append(human_q)
                    if human_q not in seen_unanswered:
                        seen_unanswered.add(human_q)
                        print(f"  [?] Unanswered required checkbox: {human_q}")
            except Exception:
                continue
    except Exception:
        pass


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
                if not hint_parts:
                    hq = await get_human_question_text(ctx, el)
                    if hq:
                        hint_parts.append(hq)
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
        "button[data-cy='submitButton']",
        "button[data-testid='apply-form-submit']",
        "button[aria-label*='Envoyer la candidature' i]",
        "button[aria-label*='Submit application' i]",
        "button:has-text('Envoyer la candidature')",
        "button:has-text('Envoyer ma candidature')",
        "button:has-text('Valider ma candidature')",
        "button:has-text('Je postule')",
        "button:has-text('Postuler maintenant')",
        "button:has-text('Submit application')",
        "#postuler button[type='submit']:visible",
        "button:has-text('Envoyer')",
        "a[role='button']:has-text('Envoyer')",
        "button:has-text('Valider')",
        "a[role='button']:has-text('Valider')",
        "button:has-text('Soumettre')",
        "button:has-text('Submit')",
        "a[role='button']:has-text('Submit')",
        "input[type='submit']:visible",
        "form button[type='submit']:visible",
        "button:has-text('Postuler')",
        "a[role='button']:has-text('Postuler')",
    ]
    bad_tokens = (
        "rechercher", "search", "connexion", "connecter", "login", "sign in",
        "créer un compte", "alerte", "alert", "newsletter", "annuler", "cancel",
        "cookie", "refuser", "fermer", "close",
        "suivant", "next", "continue", "continuer", "précédent", "previous",
        "back", "retour", "review", "vérifier",
    )
    for _, ctx in await get_form_roots(page):
        for sel in sub_selectors:
            try:
                btns = await ctx.locator(sel).all()
                for btn in btns[:8]:
                    if not (await btn.is_visible(timeout=350) and await btn.is_enabled()):
                        continue
                    lbl = " ".join([
                        (await btn.inner_text() if await btn.evaluate("e => e.tagName !== 'INPUT'") else ""),
                        (await btn.get_attribute("value") or ""),
                        (await btn.get_attribute("aria-label") or ""),
                    ]).lower()
                    if any(bad in lbl for bad in bad_tokens):
                        continue
                    return btn
            except Exception:
                continue
    return None


async def try_click_next_step(page: Page) -> bool:
    # Dismiss LinkedIn "Save this application?" dialog if accidentally triggered
    try:
        discard_btn = page.locator("div[role='dialog']:has-text('Save this application') button[aria-label='Dismiss'], div[role='dialog']:has-text('Enregistrer cette candidature') button[aria-label='Ignorer']").first
        if await discard_btn.count() > 0 and await discard_btn.is_visible(timeout=200):
            await discard_btn.click()
            await page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        open_opt = page.locator(".basic-typeahead__selectable:visible, .artdeco-typeahead__result:visible, [role='listbox'] [role='option']:visible").first
        if await open_opt.count() > 0 and await open_opt.is_visible(timeout=200):
            await open_opt.click()
            await page.wait_for_timeout(200)
    except Exception:
        pass
    # If an inline LinkedIn Work Experience / Education edit card is open ("Save" + "Delete experience"), save it first
    try:
        del_exp = page.locator("button:has-text('Delete experience'):visible, button:has-text('Supprimer l’expérience'):visible").first
        save_exp = page.locator("button:has-text('Save'):visible, button:has-text('Enregistrer'):visible").first
        if await del_exp.count() > 0 and await save_exp.count() > 0 and await save_exp.is_enabled():
            await save_exp.click()
            await page.wait_for_timeout(600)
    except Exception:
        pass
    next_selectors = [
        "button[data-easy-apply-next-button]",
        "button[aria-label*='étape suivante' i]",
        "button[aria-label*='next step' i]",
        "button[aria-label*='Vérifier' i]",
        "button[aria-label*='Review' i]",
        "button:has-text('Save and Continue')",
        "button:has-text('Enregistrer et continuer')",
        "button:has-text('Suivant')",
        "a[role='button']:has-text('Suivant')",
        "button:has-text('Vérifier')",
        "button:has-text('Continuer')",
        "a[role='button']:has-text('Continuer')",
        "button:has-text('Continue')",
        "a[role='button']:has-text('Continue')",
        "button:has-text('Next')",
        "a[role='button']:has-text('Next')",
        "button:has-text('Review')",
        "button:has-text('Valider')",
    ]
    for _, ctx in await get_form_roots(page):
        for sel in next_selectors:
            try:
                btn = ctx.locator(sel).first
                if await btn.is_visible(timeout=500) and await btn.is_enabled():
                    await btn.click()
                    await page.wait_for_timeout(1000)
                    return True
            except Exception:
                continue
    return False


async def dump_visible_buttons(page: Page) -> List[str]:
    """Return a concise list of visible button/submit labels across page and iframes for bailout diagnostics."""
    labels: List[str] = []
    seen: Set[str] = set()
    for _, ctx in await get_form_roots(page):
        try:
            btns = await ctx.locator("button:visible, a[role='button']:visible, input[type='submit']:visible").all()
            for btn in btns[:25]:
                try:
                    txt = (await btn.inner_text() if await btn.evaluate("e => e.tagName !== 'INPUT'") else (await btn.get_attribute("value") or ""))
                    txt = re.sub(r"\s+", " ", txt or "").strip()
                    if not txt:
                        txt = (await btn.get_attribute("aria-label") or "").strip()
                    if txt and txt.lower() not in seen:
                        seen.add(txt.lower())
                        labels.append(txt[:60])
                except Exception:
                    continue
        except Exception:
            continue
    return labels[:15]


async def collect_validation_errors(page: Page) -> List[str]:
    """Inspect page & iframes for visible HTML5/CSS validation errors or empty required fields."""
    errors: List[str] = []
    seen: Set[str] = set()
    for doc_ctx, root in await get_form_roots(page):
        # 1. Explicit validation error banners/messages
        err_sel = (
            ".artdeco-inline-feedback--error:visible, "
            ".invalid-feedback:visible, "
            "[role='alert']:visible, "
            ".error:visible, "
            "[class*='error-message' i]:visible, "
            "[class*='field-error' i]:visible"
        )
        try:
            for el in (await root.locator(err_sel).all())[:10]:
                txt = re.sub(r"\s+", " ", await el.inner_text()).strip()
                txt_low = txt.lower()
                is_benign_notice = any(
                    ok in txt_low
                    for ok in (
                        "uploaded successfully",
                        "téléchargé avec succès",
                        "autofill completed",
                        "saved successfully",
                        "enregistré avec succès",
                    )
                ) and not any(
                    bad in txt_low
                    for bad in ("error", "erreur", "invalid", "required", "requis", "failed", "échec", "missing")
                )
                if is_benign_notice:
                    continue
                if txt and len(txt) < 220 and txt_low not in seen:
                    seen.add(txt_low)
                    errors.append(txt)
        except Exception:
            pass

        # 2. Invalid or empty required inputs/selects
        try:
            inv_sel = "input[aria-invalid='true']:visible, select[aria-invalid='true']:visible, textarea[aria-invalid='true']:visible, input:invalid:visible, select:invalid:visible, textarea:invalid:visible"
            for el in (await root.locator(inv_sel).all())[:8]:
                q = await get_human_question_text(doc_ctx, el) or await get_label_text(doc_ctx, el)
                msg = f"Invalid field: {q[:100]}" if q else "Invalid field"
                if msg.lower() not in seen:
                    seen.add(msg.lower())
                    errors.append(msg)
        except Exception:
            pass
    return errors[:8]


async def get_step_signature(page: Page) -> str:
    """Fingerprint the current form step (URL + modal heading + visible field labels) to detect stuck Next loops."""
    parts: List[str] = [page.url]
    try:
        root = await get_form_root(page)
        for h in (await root.locator("h1:visible, h2:visible, h3:visible, legend:visible, label:visible").all())[:12]:
            txt = re.sub(r"\s+", " ", await h.inner_text()).strip()
            if txt:
                parts.append(txt[:80])
    except Exception:
        pass
    return "|".join(parts)


async def _has_visible_modal(page: Page) -> bool:
    try:
        modal = page.locator(".jobs-easy-apply-modal, div[role='dialog'][aria-modal='true']").first
        return await modal.count() > 0 and await modal.is_visible(timeout=300)
    except Exception:
        return False


async def save_debug_screenshot(page: Page, company: str, suffix: str = "after_submit") -> Optional[Path]:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        safe_comp = re.sub(r"[^\w\-]+", "_", company or "offer").strip("_")[:50] or "offer"
        out_path = DEBUG_DIR / f"{safe_comp}_{suffix}.png"
        await page.screenshot(path=str(out_path), full_page=True, timeout=6000)
        return out_path
    except Exception:
        return None


CONFIRMATION_URL_TOKENS = (
    "origin=responseoffer",
    "/bounce/createalert",
    "/jobs/apply/confirm",
    "/apply/confirm",
    "/application/confirm",
    "/confirmation",
    "/thank-you",
    "/thankyou",
    "/merci",
    "applied=true",
    "submitted=true",
)


async def check_submission_confirmed(
    page: Page,
    pre_submit_url: str = "",
    had_modal: bool = False,
    submit_ok_responses: Optional[List[str]] = None,
) -> bool:
    try:
        if page.is_closed():
            return False
        u = (page.url or "").lower()
        if any(tok in u for tok in CONFIRMATION_URL_TOKENS):
            return True

        # Check visible text in main page and iframes for confirmation phrases
        for _, ctx in await get_form_roots(page):
            try:
                body_text = (await ctx.locator("body").inner_text(timeout=2000)).lower()
                if any(phrase in body_text for phrase in CONFIRMATION_PHRASES):
                    return True
            except Exception:
                continue

        # Check if modal/drawer closed after submit with a 200/201 submit response (and no validation errors)
        if had_modal and not await _has_visible_modal(page):
            if submit_ok_responses:
                return True
            if not await collect_validation_errors(page):
                # Check if post-submit LinkedIn/WTTJ toast appeared or Apply button turned into "Postulé"/"Applied"
                body_low = (await page.locator("body").inner_text(timeout=1500)).lower()
                if any(tok in body_low for tok in ("postulé", "applied", "candidature envoyée", "merci")):
                    return True

        # Check if URL redirected away from /apply form cleanly with a 200/201 submit response and no errors
        if pre_submit_url and page.url != pre_submit_url and submit_ok_responses:
            if not await collect_validation_errors(page):
                return True

        return False
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

    nav_url = url.replace("https://fr.linkedin.com/jobs/view/", "https://www.linkedin.com/jobs/view/")
    print(f"[*] [{company}] Opening {nav_url}")
    try:
        await page.goto(nav_url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
    except Exception as e:
        print(f"[!] Bailout [{company}]: Timeout/load error opening offer URL ({e})")
        return page, "manual_portal"

    await page.wait_for_timeout(1200)
    if any(k in page.url.lower() for k in ("authwall", "/login", "connexion", "signin", "se-connecter")):
        print(f"[!] Bailout [{company}]: Login wall detected ({page.url}) — run './stage login'")
        update_tracker_status(url, company, "needs_login")
        return page, "needs_login"
    if "linkedin.com/jobs/search" in page.url.lower() and "/jobs/view/" in nav_url.lower():
        print(f"[!] Bailout [{company}]: LinkedIn offer expired/redirected to search results -> 'expired'")
        update_tracker_status(url, company, "expired")
        return page, "expired"
    await accept_cookies_if_present(page)
    active_page, clicked = await click_apply_and_follow_redirect(page)
    if active_page.is_closed():
        print(f"[!] Bailout [{company}]: Tab closed unexpectedly after clicking Apply")
        return active_page, "closed"

    ats = detect_ats_provider(active_page.url)
    print(f"[*] [{company}] Apply clicked: {clicked} | Target: {ats}")

    if "linkedin.com/jobs/search" in active_page.url.lower() and "/jobs/view/" in nav_url.lower():
        print(f"[!] Bailout [{company}]: LinkedIn offer expired/redirected to search results -> 'expired'")
        update_tracker_status(url, company, "expired")
        return active_page, "expired"

    if ats == "LinkedIn" and "linkedin.com/jobs/view" in active_page.url.lower() and not await _has_visible_modal(active_page):
        try:
            await active_page.wait_for_timeout(1000)
            if not await _has_visible_modal(active_page):
                retry_btn = active_page.locator("button.jobs-apply-button:visible").first
                if await retry_btn.is_visible(timeout=1000):
                    await retry_btn.click()
                    await active_page.wait_for_timeout(1500)
        except Exception:
            pass

    if not clicked:
        page_low = ""
        try:
            page_low = (await active_page.locator("body").inner_text(timeout=1500)).lower()
        except Exception:
            pass
        if any(p in page_low for p in EXPIRED_OFFER_PHRASES):
            print(f"[!] Bailout [{company}]: Offer appears closed/expired ({ats}) -> 'expired'")
            update_tracker_status(url, company, "expired")
            return active_page, "expired"
        has_pwd_input = False
        try:
            has_pwd_input = await active_page.locator("input[type='password']:visible").count() > 0
        except Exception:
            pass
        if any(k in active_page.url.lower() for k in ("authwall", "/login", "connexion", "signin", "se-connecter")) or (
            has_pwd_input
        ) or (
            ats == "APEC"
            and "mot de passe" in page_low
            and any(k in page_low for k in ("connectez-vous", "me connecter", "se connecter", "identifiant"))
        ):
            print(f"[!] Bailout [{company}]: Login wall detected ({ats}) -> 'needs_login'")
            update_tracker_status(url, company, "needs_login")
            return active_page, "needs_login"
        vis_btns = await dump_visible_buttons(active_page)
        print(f"[!] Bailout [{company}]: Apply button not found on page ({active_page.url}) | Visible buttons: {vis_btns}")
        if auto:
            update_tracker_status(url, company, "manual_portal")
        return active_page, "manual_portal"

    body_low_check = ""
    try:
        body_low_check = (await active_page.locator("body").inner_text(timeout=1000)).lower()[:2000]
    except Exception:
        pass
    has_pwd_after_click = False
    try:
        has_pwd_after_click = await active_page.locator("input[type='password']:visible").count() > 0
    except Exception:
        pass
    if any(k in active_page.url.lower() for k in ("authwall", "/login", "connexion", "signin", "se-connecter")) or (
        has_pwd_after_click
    ) or (
        "vous avez déjà un compte" in body_low_check and "mot de passe" in body_low_check
    ) or (
        ats == "APEC"
        and "mot de passe" in body_low_check
        and any(k in body_low_check for k in ("connectez-vous", "identifiant", "me connecter", "se connecter"))
    ):
        print(f"[!] Bailout [{company}]: Login wall detected ({ats}) -> 'needs_login' — sign in via './stage login'")
        update_tracker_status(url, company, "needs_login")
        return active_page, "needs_login"

    verified_skills = load_master_profile_skills()
    seen_unanswered: Set[str] = set()
    last_step_unanswered: List[str] = []
    bailout_reason = ""
    final_status = "manual_portal"
    max_steps = 15

    # Wait briefly for dynamic JS application forms to render before step 1
    try:
        await active_page.wait_for_selector(
            "form, input:visible, select:visible, button[type='submit'], .jobs-easy-apply-modal, [role='dialog']",
            timeout=4000,
        )
    except Exception:
        pass

    for step in range(1, max_steps + 1):
        if active_page.is_closed():
            bailout_reason = f"Tab closed mid-fill at step {step}"
            break
        try:
            try:
                await active_page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            await active_page.wait_for_timeout(600)

            if ats == "LinkedIn":
                await _prune_blank_linkedin_experience(active_page)
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

            is_external = ats not in {
                "LinkedIn", "HelloWork", "Welcome to the Jungle",
                "JobTeaser", "Indeed", "APEC", "Meteojob",
            }
            if step == 1 and skip_external and is_external:
                print(f"[!] Bailout [{company}]: Skipped external ATS ({ats}: {active_page.url})")
                update_tracker_status(url, company, "manual_portal")
                return active_page, "manual_portal"

            submit_btn = await find_submit_button(active_page)
            if submit_btn is not None:
                await uncheck_follow_company(active_page)
                if auto:
                    if step_unanswered:
                        bailout_reason = f"Blocked by unanswered required fields before submit: {step_unanswered}"
                        break

                    pre_submit_url = active_page.url
                    had_modal = await _has_visible_modal(active_page)
                    submit_ok_responses: List[str] = []

                    def _on_response(resp) -> None:
                        try:
                            if resp.status in (200, 201, 202, 204) and resp.request.method in ("POST", "PUT", "PATCH"):
                                r_url = (resp.url or "").lower()
                                if any(k in r_url for k in ("apply", "application", "candidat", "submit", "postul", "response", "jobs")):
                                    submit_ok_responses.append(f"{resp.status} {resp.url}")
                        except Exception:
                            pass

                    active_page.on("response", _on_response)
                    try:
                        await submit_btn.click()
                        confirmed = False
                        val_errors: List[str] = []
                        for _ in range(4):
                            await active_page.wait_for_timeout(1500)
                            if await check_submission_confirmed(
                                active_page,
                                pre_submit_url=pre_submit_url,
                                had_modal=had_modal,
                                submit_ok_responses=submit_ok_responses,
                            ):
                                confirmed = True
                                break
                            val_errors = await collect_validation_errors(active_page)
                            if val_errors:
                                break
                    finally:
                        try:
                            active_page.remove_listener("response", _on_response)
                        except Exception:
                            pass

                    if confirmed:
                        update_tracker_status(url, company, "applied")
                        print(f"[+] [{company}] Confirmed -> 'applied'!")
                        return active_page, "applied"
                    shot = await save_debug_screenshot(active_page, company, "after_submit")
                    shot_note = f" | Screenshot: {shot.relative_to(WORKSPACE)}" if shot else ""
                    if val_errors:
                        bailout_reason = f"Blocked by form validation errors after submit ({ats}): {val_errors}{shot_note}"
                    else:
                        bailout_reason = f"Clicked submit but confirmation was not detected ({ats}){shot_note}"
                    break
                print(f"[*] [{company}] Ready for manual review.")
                return active_page, "ready_for_review"

            pre_sig = await get_step_signature(active_page)
            if not await try_click_next_step(active_page):
                if step > 1 and ats == "LinkedIn" and not await _has_visible_modal(active_page):
                    if await check_submission_confirmed(active_page, had_modal=True):
                        update_tracker_status(url, company, "applied")
                        print(f"[+] [{company}] Confirmed -> 'applied'!")
                        return active_page, "applied"
                if step_unanswered:
                    bailout_reason = f"Blocked by unanswered required fields: {step_unanswered}"
                else:
                    vis_btns = await dump_visible_buttons(active_page)
                    shot = await save_debug_screenshot(active_page, company, f"no_next_step_{step}")
                    shot_note = f" | Screenshot: {shot.relative_to(WORKSPACE)}" if shot else ""
                    bailout_reason = f"No Next/Submit button found at step {step} ({ats}) | Visible buttons: {vis_btns}{shot_note}"
                break

            await active_page.wait_for_timeout(500)
            post_sig = await get_step_signature(active_page)
            if post_sig == pre_sig:
                val_errors = await collect_validation_errors(active_page)
                if val_errors:
                    bailout_reason = f"Stuck on step {step} ({ats}) — validation errors: {val_errors}"
                elif step_unanswered:
                    bailout_reason = f"Stuck on step {step} ({ats}) — unanswered fields: {step_unanswered}"
                else:
                    shot = await save_debug_screenshot(active_page, company, f"stuck_step_{step}")
                    shot_note = f" | Screenshot: {shot.relative_to(WORKSPACE)}" if shot else ""
                    bailout_reason = f"Stuck on step {step} ({ats}) — clicking Next did not advance modal{shot_note}"
                break
        except Exception as e:
            if active_page.is_closed():
                bailout_reason = f"Tab closed mid-fill at step {step}"
            else:
                bailout_reason = f"Step {step} error ({e})"
                print(f"[!] [{company}] Step {step} error: {e}")
            break
    else:
        if last_step_unanswered:
            bailout_reason = f"Blocked by unanswered required fields: {last_step_unanswered}"
        else:
            bailout_reason = f"Form did not reach Submit after {max_steps} steps ({ats})"

    if not bailout_reason:
        bailout_reason = f"Form incomplete ({ats})"

    if auto:
        print(f"[!] Bailout [{company}]: {bailout_reason}")
        update_tracker_status(url, company, final_status)
        print(f"[*] [{company}] Finished -> '{final_status}'")
    return active_page, final_status


def prepare_chrome_profile_dir(profile_dir: Path) -> bool:
    """Clean stale Chromium Singleton locks; return False if an active process owns it."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_file = profile_dir / "SingletonLock"
    if lock_file.exists() or lock_file.is_symlink():
        pid = None
        try:
            target = os.readlink(lock_file)
            m = re.search(r"-(\d+)$", target)
            if m:
                pid = int(m.group(1))
        except Exception:
            pass
        if pid is not None:
            try:
                os.kill(pid, 0)
                print(f"[!] .chrome_profile/ is currently in use by PID {pid}. Close that Chrome window or stop the running task first.")
                return False
            except ProcessLookupError:
                pass
            except PermissionError:
                print(f"[!] .chrome_profile/ is currently in use by PID {pid}. Close that Chrome window or stop the running task first.")
                return False
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            p = profile_dir / name
            if p.exists() or p.is_symlink():
                try:
                    p.unlink()
                except Exception:
                    pass
    return True


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Playwright Async Multi-Tab Application Assistant")
    parser.add_argument("--meta", help="Path to output/<Company>/metadata.json")
    parser.add_argument("--metas", nargs="+", help="Multiple metadata.json paths to run simultaneously")
    parser.add_argument("--tabs", type=int, default=5, help="Simultaneous tabs per batch (default: 5)")
    parser.add_argument("--url", default="", help="Override job URL")
    parser.add_argument("--auto", action="store_true", help="Auto-submit simple forms without pausing (runs headless by default)")
    parser.add_argument("--headed", action="store_true", help="Show the browser window even in --auto mode")
    parser.add_argument("--skip-external", action="store_true", help="Skip external ATS redirects")
    parser.add_argument("--login", action="store_true", help="Open Chrome to log into all supported job boards (saves cookies)")
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=list(LOGIN_URLS.keys()),
        default=list(LOGIN_URLS.keys()),
        help="Job boards to open in --login mode (default: all)",
    )
    args = parser.parse_args()

    if not prepare_chrome_profile_dir(CHROME_PROFILE_DIR):
        return 1

    if args.login:
        storage_state_path = CHROME_PROFILE_DIR / "storage_state.json"
        async with async_playwright() as p:
            login_kwargs: Dict[str, Any] = {
                "user_data_dir": str(CHROME_PROFILE_DIR),
                "headless": False,
                "no_viewport": True,
                "locale": "fr-FR",
                "ignore_default_args": ["--enable-automation"],
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if Path("/Applications/Google Chrome.app").exists():
                login_kwargs["channel"] = "chrome"
            context = await p.chromium.launch_persistent_context(**login_kwargs)
            if storage_state_path.exists():
                try:
                    saved = json.loads(storage_state_path.read_text(encoding="utf-8"))
                    if saved.get("cookies"):
                        await context.add_cookies(saved["cookies"])
                except Exception:
                    pass
            labels: List[str] = []
            for idx, src_key in enumerate(args.sources):
                label, login_url = LOGIN_URLS[src_key]
                labels.append(label)
                page = context.pages[0] if (idx == 0 and context.pages) else await context.new_page()
                try:
                    await page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    print(f"  [!] Could not open {label} ({login_url}): {e}")
            print(f"[*] Opened login tabs for: {', '.join(labels)}.")
            print("[*] Sign in on each tab so cookies are saved in .chrome_profile/, then close the Chrome window when done.")
            async def _save_cookies_snapshot() -> None:
                try:
                    cookies = await context.cookies()
                    storage_state_path.write_text(
                        json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass

            ticks = 0
            try:
                while len(context.pages) > 0:
                    await asyncio.sleep(0.5)
                    ticks += 1
                    if ticks % 4 == 0:
                        await _save_cookies_snapshot()
            except Exception:
                pass
            await _save_cookies_snapshot()
            await context.close()
        return 0

    meta_paths = [Path(m) for m in args.metas] if args.metas else ([Path(args.meta)] if args.meta else [])
    if not meta_paths:
        print("[!] --meta or --metas is required unless --login is used.")
        return 1

    profile = load_json(PROFILE_PATH)

    if args.auto and MANUAL_CURRENT_PATH.exists():
        try:
            MANUAL_CURRENT_PATH.unlink()
        except Exception:
            pass

    run_headless = bool(args.auto and not args.headed)
    launch_kwargs: Dict[str, Any] = {
        "user_data_dir": str(CHROME_PROFILE_DIR),
        "headless": run_headless,
        "viewport": {"width": 1280, "height": 860},
        "locale": "fr-FR",
        "ignore_default_args": ["--enable-automation"],
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if Path("/Applications/Google Chrome.app").exists():
        launch_kwargs["channel"] = "chrome"
    elif run_headless:
        launch_kwargs["channel"] = "chromium"
        launch_kwargs["user_agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/159.0.0.0 Safari/537.36"
        )

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(**launch_kwargs)
        storage_state_path = CHROME_PROFILE_DIR / "storage_state.json"
        if storage_state_path.exists():
            try:
                saved = json.loads(storage_state_path.read_text(encoding="utf-8"))
                if saved.get("cookies"):
                    await context.add_cookies(saved["cookies"])
            except Exception:
                pass

        max_workers = max(1, args.tabs)
        if args.auto and len(meta_paths) > 1:
            print(f"\n=== Running {len(meta_paths)} offer(s) with {max_workers}-slot rolling worker pool ===")
            sem = asyncio.Semaphore(max_workers)

            async def worker_task(idx: int, mp: Path) -> None:
                async with sem:
                    meta = load_json(mp)
                    u = meta.get("url", "")
                    comp = meta.get("company", "Offer")
                    if not u:
                        return
                    pg = await context.new_page()
                    active_pg = pg
                    try:
                        active_pg, _ = await asyncio.wait_for(
                            process_offer_concurrently(
                                pg, profile, meta, u, auto=True, skip_external=args.skip_external
                            ),
                            timeout=90,
                        )
                    except asyncio.TimeoutError:
                        print(f"[!] Bailout [{comp}]: Timed out after 90s -> 'manual_portal'")
                        update_tracker_status(u, comp, "manual_portal")
                    except Exception as e:
                        print(f"[!] Bailout [{comp}]: Unexpected worker error ({e}) -> 'manual_portal'")
                        update_tracker_status(u, comp, "manual_portal")
                    finally:
                        for tab_to_close in {pg, active_pg}:
                            if tab_to_close and not tab_to_close.is_closed() and len(context.pages) > 1:
                                try:
                                    await asyncio.wait_for(tab_to_close.close(), timeout=5)
                                except Exception:
                                    pass

            await asyncio.gather(*(worker_task(i, mp) for i, mp in enumerate(meta_paths)))
            try:
                await asyncio.wait_for(context.close(), timeout=15)
            except Exception:
                pass
            return 0

        if not args.auto:
            max_workers = 1
            for idx, mp in enumerate(meta_paths):
                meta = load_json(mp)
                u = (args.url if len(meta_paths) == 1 and args.url else meta.get("url", ""))
                if not u:
                    continue
                if MANUAL_SKIP_PATH.exists():
                    try:
                        MANUAL_SKIP_PATH.unlink()
                    except Exception:
                        pass

                cv_rel = meta.get("cv_pdf", "")
                lm_rel = meta.get("lm_pdf", "")
                cv_abs = (WORKSPACE / cv_rel) if cv_rel else None
                lm_abs = (WORKSPACE / lm_rel) if lm_rel else None
                try:
                    cv_ui = str(cv_abs.relative_to(WORKSPACE)) if cv_abs and cv_abs.is_relative_to(WORKSPACE) else cv_rel
                except Exception:
                    cv_ui = cv_rel
                try:
                    lm_ui = str(lm_abs.relative_to(WORKSPACE)) if lm_abs and lm_abs.is_relative_to(WORKSPACE) else lm_rel
                except Exception:
                    lm_ui = lm_rel
                try:
                    MANUAL_CURRENT_PATH.parent.mkdir(parents=True, exist_ok=True)
                    MANUAL_CURRENT_PATH.write_text(
                        json.dumps({
                            "active": True,
                            "index": idx + 1,
                            "total": len(meta_paths),
                            "company": meta.get("company", ""),
                            "role": meta.get("role", ""),
                            "url": u,
                            "cv_pdf": cv_ui,
                            "lm_pdf": lm_ui,
                        }, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception:
                    pass

                print(f"\n=== Manual Review [{idx + 1}/{len(meta_paths)}]: {meta.get('company', '')} — {meta.get('role', '')} ===")

                try:
                    _ = context.pages
                except Exception:
                    context = await p.chromium.launch_persistent_context(**launch_kwargs)

                pg = context.pages[0] if context.pages else await context.new_page()
                res = await process_offer_concurrently(
                    pg, profile, meta, u, auto=False, skip_external=args.skip_external
                )
                active_pg = res[0] if isinstance(res, tuple) else pg
                status_res = res[1] if isinstance(res, tuple) else "manual_portal"

                if status_res not in {"applied", "expired", "needs_login", "closed"} and not active_pg.is_closed():
                    if cv_abs and cv_abs.exists():
                        try:
                            subprocess.run(["open", "-R", str(cv_abs)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                            subprocess.Popen(["open", "-a", "Finder"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                    elif cv_abs and cv_abs.parent.exists():
                        try:
                            subprocess.run(["open", str(cv_abs.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                            subprocess.Popen(["open", "-a", "Finder"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                    print(f"[*] [{meta.get('company', '')}] Ready — submit in Chrome (or click Skip / Mark Applied in UI), then close tab.")
                    confirmed_here = False
                    try:
                        while not active_pg.is_closed():
                            if MANUAL_SKIP_PATH.exists():
                                action = "skipped"
                                try:
                                    action = MANUAL_SKIP_PATH.read_text(encoding="utf-8").strip().lower() or "skipped"
                                    MANUAL_SKIP_PATH.unlink()
                                except Exception:
                                    pass
                                if action in {"skipped", "applied"}:
                                    update_tracker_status(u, meta.get("company", ""), action)
                                    print(f"[*] [{meta.get('company', '')}] Marked '{action}' from UI — advancing...")
                                try:
                                    await active_pg.close()
                                except Exception:
                                    pass
                                break
                            if not confirmed_here and await check_submission_confirmed(active_pg):
                                confirmed_here = True
                                update_tracker_status(u, meta.get("company", ""), "applied")
                                print(f"[+] Confirmed submission for {meta.get('company', '')}! Marked 'applied'.")
                            await asyncio.sleep(0.5)
                    except Exception:
                        pass

                for extra_pg in list(context.pages):
                    if extra_pg is not context.pages[0] or idx == len(meta_paths) - 1:
                        try:
                            if not extra_pg.is_closed():
                                await extra_pg.close()
                        except Exception:
                            pass

            if MANUAL_CURRENT_PATH.exists():
                try:
                    MANUAL_CURRENT_PATH.unlink()
                except Exception:
                    pass
            try:
                await context.close()
            except Exception:
                pass
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
