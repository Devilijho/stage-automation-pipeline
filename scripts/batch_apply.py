#!/usr/bin/env python3
"""
Batch Job Searcher, Tailoring & Application Queue (batch_apply.py)

1. Searches LinkedIn, HelloWork, Welcome to the Jungle, JobTeaser, Indeed,
   APEC, and Meteojob for Stage / Internship offers in Informatique
   (prioritizing BUT 2 / BUT 3 / Bac+2 / Bac+3 near Bures-sur-Yvette, Saclay,
   Massy, Palaiseau, Paris). Uses `.chrome_profile/` cookies automatically
   for authenticated / JS-rendered boards.
2. Deduplicates against `data/applications.csv`.
3. Runs `scripts/tailor.py` on each matched offer to generate 1-page CV + LM PDFs.
4. Opens each offer one-by-one in Chrome, auto-fills fields & attaches PDFs,
   and either pauses for manual review (default) or auto-submits simple forms (--auto).
"""

import argparse
import asyncio
import json
import math
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.browser_session import (
    build_chrome_launch_kwargs,
    prepare_chrome_profile_dir,
    restore_storage_cookies,
)
from core.config import (
    APPS_CSV_PATH,
    CHROME_PROFILE_DIR,
    DEFAULT_PREFERENCES,
    PROFILE_PATH,
    SCRIPTS_DIR,
    WORKSPACE,
    load_preferences,
    load_profile,
)
from core.storage import load_existing_offers, normalize_company_role_key, read_applications

PYTHON_BIN = sys.executable

COOKIE_SOURCES = {"jobteaser", "indeed", "apec", "meteojob"}
ALL_SOURCES = ["linkedin", "hellowork", "wttj", "jobteaser", "indeed", "apec", "meteojob"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

_THREAD_LOCAL = threading.local()


def get_http_session() -> requests.Session:
    """Return a thread-local requests.Session with connection pooling."""
    sess = getattr(_THREAD_LOCAL, "session", None)
    if sess is None:
        sess = requests.Session()
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        sess.headers.update(HEADERS)
        _THREAD_LOCAL.session = sess
    return sess

DEFAULT_QUERIES = [
    "stage informatique",
    "stage développeur",
    "stage cybersécurité",
    "stage réseaux systèmes",
]
DEFAULT_LEVEL_BOOST = ["but 2", "but 3", "bac+2", "bac+3", "3 mois"]


def _search_cfg() -> Dict[str, Any]:
    return load_profile(PROFILE_PATH).get("search") or {}


def _search_location_li() -> str:
    return _search_cfg().get("location", "Paris, Île-de-France, France")


def _search_location_hw() -> str:
    return _search_cfg().get("location_short", "Paris")


def _level_boost() -> List[str]:
    return [str(x).lower() for x in (_search_cfg().get("level_boost") or DEFAULT_LEVEL_BOOST)]


EXCLUDE_TITLE_TERMS = [
    "cdi",
    "alternance",
    "apprentissage",
    "senior",
    "lead ",
    "principal",
    "manager",
    "directeur",
]


FINAL_YEAR_TERMS = (
    "fin d'études",
    "fin d'etudes",
    "fin de cursus",
    "pfe",
    "bac+5",
    "bac +5",
)

BUT_FRIENDLY_TERMS = (
    "but 2",
    "but 3",
    "but2",
    "but3",
    "bac+2",
    "bac +2",
    "bac+3",
    "bac +3",
    "iut",
    "dut",
    "2ème année",
    "3ème année",
)


def is_excluded_final_year_offer(title: str, description: str = "") -> bool:
    """Return True if offer targets 6-month PFE / Bac+5 without accepting Bac+2/3."""
    t_low = title.lower()
    d_low = description.lower()
    combined = f"{t_low} {d_low}"
    if any(ok in combined for ok in BUT_FRIENDLY_TERMS):
        return False
    if any(term in t_low for term in FINAL_YEAR_TERMS):
        return True
    if any(term in d_low for term in (*FINAL_YEAR_TERMS, "master 2", "élève-ingénieur")):
        return True
    return False


def classify_offer_quality(
    role: str,
    location: str = "",
    description: str = "",
    matched_keywords: str = "",
    url: str = "",
    prefs: Optional[Dict[str, List[str]]] = None,
) -> Tuple[str, int]:
    """Classify an offer into ('high' | 'medium' | 'low', score) using role, distance, work type, and level."""
    p = prefs if prefs is not None else load_preferences(PROFILE_PATH)
    r_low = (role or "").lower()
    l_low = (location or "").lower()
    d_low = (description or "").lower()
    u_low = (url or "").lower()
    kw_low = (matched_keywords or "").lower()
    loc_blob = f"{l_low} {u_low} {d_low[:800]}"

    score = 0
    if any(t in r_low for t in ("stage", "intern", "stagiaire")):
        score += 10

    if any(bad in r_low for bad in p.get("avoid_roles", [])):
        score -= 40
    elif any(good in r_low or good in kw_low for good in p.get("target_roles", [])):
        score += 30

    kw_count = len([k for k in re.split(r"[;,]", kw_low) if k.strip()])
    score += min(15, kw_count * 3)

    is_remote = any(w in f"{r_low} {l_low} {d_low}" for w in p.get("work_types", []))
    if is_remote:
        score += 15

    if any(loc in loc_blob for loc in p.get("preferred_locations", [])):
        score += 25
    elif any(loc in loc_blob for loc in p.get("idf_locations", [])):
        score += 12
    elif any(loc in f"{l_low} {u_low}" for loc in p.get("avoid_locations", [])) and not is_remote:
        score -= 30

    if any(lvl in f"{r_low} {d_low}" for lvl in _level_boost()):
        score += 10
    elif any(lvl in f"{r_low} {d_low}" for lvl in ("bac+5", "bac +5", "master 2", "m2", "pfe")) and not any(
        ok in f"{r_low} {d_low}" for ok in ("bac+2", "bac +2", "bac+3", "bac +3", "but 2", "but 3")
    ):
        score -= 20

    tier = "high" if score >= 40 else ("medium" if score >= 15 else "low")
    return tier, score


def score_offer(title: str, location: str, description: str) -> int:
    t_low = title.lower()
    if any(bad in t_low for bad in ("page introuvable", "page not found", "just a moment", "access denied")):
        return -100
    if any(bad in t_low for bad in EXCLUDE_TITLE_TERMS) and "stage" not in t_low:
        return -100

    # Filter out Bac+5 / PFE / Fin d'études / Fin de cursus unless Bac+2/3 or BUT is explicitly accepted
    if is_excluded_final_year_offer(title, description):
        return -50

    _, score = classify_offer_quality(title, location=location, description=description)
    return max(0, score)


def scrape_linkedin_paginated(query: str, max_per_query: int, easy_apply: bool = False) -> List[Dict[str, str]]:
    """Paginate LinkedIn's public guest job postings endpoint (no login wall)."""
    print(f"[*] Searching LinkedIn{' (Easy Apply only)' if easy_apply else ''}: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    start = 0

    while len(offers) < max_per_query and start <= 175:
        url = (
            f"https://fr.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={quote_plus(query)}"
            f"&location={quote_plus(_search_location_li())}"
            f"&f_JT=I{'&f_AL=true' if easy_apply else ''}&start={start}"
        )
        try:
            resp = get_http_session().get(url, timeout=15)
            if resp.status_code != 200 or not resp.text.strip():
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            anchors = soup.select("a[href*='/jobs/view/']")
            if not anchors:
                break
            new_on_page = 0
            for a in anchors:
                if len(offers) >= max_per_query:
                    break
                href = (a.get("href") or "").split("?")[0]
                if not href:
                    continue
                if not href.startswith("http"):
                    href = f"https://fr.linkedin.com{href}"
                if href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                new_on_page += 1
                offers.append({
                    "source": "LinkedIn",
                    "url": href,
                    "preview": a.get_text(" ", strip=True),
                })
            if new_on_page == 0:
                break
            start += 10
            time.sleep(0.15)
        except Exception as e:
            print(f"  [!] LinkedIn pagination error (start={start}): {e}")
            break
    return offers


def scrape_hellowork_paginated(query: str, max_per_query: int) -> List[Dict[str, str]]:
    """Paginate HelloWork (open French job board, stage filter c=Stage)."""
    print(f"[*] Searching HelloWork: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    page_num = 1

    while len(offers) < max_per_query and page_num <= 12:
        url = (
            f"https://www.hellowork.com/fr-fr/emploi/recherche.html"
            f"?k={quote_plus(query)}&l={quote_plus(_search_location_hw())}&c=Stage&p={page_num}"
        )
        try:
            resp = get_http_session().get(url, timeout=15)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            anchors = soup.select("a[href*='/fr-fr/emplois/']")
            if not anchors:
                break
            new_on_page = 0
            for a in anchors:
                if len(offers) >= max_per_query:
                    break
                href = (a.get("href") or "").split("?")[0]
                if not href or not href.endswith(".html"):
                    continue
                full_url = href if href.startswith("http") else f"https://www.hellowork.com{href}"
                if full_url in seen_hrefs:
                    continue
                seen_hrefs.add(full_url)
                new_on_page += 1
                offers.append({
                    "source": "HelloWork",
                    "url": full_url,
                    "preview": a.get_text(" ", strip=True),
                })
            if new_on_page == 0:
                break
            page_num += 1
        except Exception as e:
            print(f"  [!] HelloWork error (p={page_num}): {e}")
            break
    return offers


class AsyncBrowserPool:
    """Async multi-tab headless Chromium pool sharing `.chrome_profile/` login cookies."""

    def __init__(self, max_tabs: int = 5) -> None:
        self.max_tabs = max_tabs
        self._pw = None
        self._context = None
        self._pages: Optional[asyncio.Queue] = None
        self._failed = False
        self._start_lock: Optional[asyncio.Lock] = None
        self._dismissed_domains: Set[str] = set()

    async def _ensure_started(self) -> bool:
        if self._pages is not None:
            return True
        if self._failed:
            return False
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._pages is not None:
                return True
            if self._failed:
                return False
            try:
                from playwright.async_api import async_playwright

                if not prepare_chrome_profile_dir(CHROME_PROFILE_DIR):
                    self._failed = True
                    return False
                self._pw = await async_playwright().start()
                pool_kwargs = build_chrome_launch_kwargs(CHROME_PROFILE_DIR, headless=True)
                self._context = await self._pw.chromium.launch_persistent_context(**pool_kwargs)
                await restore_storage_cookies(self._context, CHROME_PROFILE_DIR)
                q: asyncio.Queue = asyncio.Queue()
                existing_pages = list(self._context.pages)
                for p in existing_pages[: self.max_tabs]:
                    await q.put(p)
                for _ in range(max(0, self.max_tabs - len(existing_pages))):
                    await q.put(await self._context.new_page())
                self._pages = q
                return True
            except Exception as e:
                self._failed = True
                print(f"  [!] Could not start .chrome_profile browser session: {e}")
                print("  [!] Close any open StageBot Chrome window and retry.")
                return False

    async def _dismiss_cookies(self, page: Any, domain: str) -> None:
        if domain in self._dismissed_domains:
            return
        for sel in (
            "#didomi-notice-agree-button",
            "#onetrust-accept-btn-handler",
            "#axeptio_btn_acceptAll",
            "button:has-text('Tout accepter')",
            "button:has-text('Accepter tout')",
            "button:has-text(\"J'accepte\")",
        ):
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=350):
                    await loc.click()
                    await page.wait_for_timeout(200)
                    self._dismissed_domains.add(domain)
                    return
            except Exception:
                continue
        self._dismissed_domains.add(domain)

    async def fetch_html(
        self,
        url: str,
        wait_selector: str = "h1, main",
        wait_ms: int = 1200,
    ) -> Tuple[str, str]:
        """Navigate with `.chrome_profile/` cookies using a pooled tab and return `(final_url, html)`."""
        if not await self._ensure_started():
            return url, ""
        assert self._pages is not None
        page = await self._pages.get()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=wait_ms)
                except Exception:
                    await page.wait_for_timeout(min(wait_ms, 800))
            else:
                await page.wait_for_timeout(min(wait_ms, 800))
            await page.wait_for_timeout(150)
            from urllib.parse import urlparse as _urlparse
            domain = _urlparse(page.url or url).netloc
            await self._dismiss_cookies(page, domain)
            return page.url, await page.content()
        except Exception as e:
            print(f"  [!] Browser fetch error for {url}: {e}")
            return url, ""
        finally:
            await self._pages.put(page)

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._pages = None
        self._context = None
        self._pw = None


def is_login_wall(final_url: str, html: str) -> bool:
    u = (final_url or "").lower()
    if any(tok in u for tok in ("/login", "/signin", "/sign_in", "/connexion", "authwall", "/account/login")):
        return True
    low = (html or "")[:4000].lower()
    if "sign in to" in low or "connectez-vous pour" in low:
        return True
    return False


def scrape_wttj_paginated(query: str, max_per_query: int) -> List[Dict[str, str]]:
    """Search Welcome to the Jungle via its public French Algolia index (internship + FR)."""
    print(f"[*] Searching Welcome to the Jungle: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    clean_q = re.sub(r"\bstage\b", "", query, flags=re.I).strip() or query

    algolia_url = "https://csekhvms53-dsn.algolia.net/1/indexes/wttj_jobs_production_fr/query"
    algolia_headers = {
        **HEADERS,
        "x-algolia-application-id": "CSEKHVMS53",
        "x-algolia-api-key": "4bd8f6215d0cc52b26430765769e65a0",
        "Referer": "https://www.welcometothejungle.com/",
        "Origin": "https://www.welcometothejungle.com",
    }
    page_num = 0
    hits_per_page = min(max(max_per_query, 20), 50)
    while len(offers) < max_per_query and page_num < 5:
        try:
            resp = get_http_session().post(
                algolia_url,
                headers=algolia_headers,
                json={
                    "query": clean_q,
                    "filters": "contract_type:internship AND offices.country_code:FR",
                    "hitsPerPage": hits_per_page,
                    "page": page_num,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"  [!] WTTJ search returned HTTP {resp.status_code}")
                break
            data = resp.json()
            hits = data.get("hits", [])
            if not hits:
                break
            new_on_page = 0
            for hit in hits:
                if len(offers) >= max_per_query:
                    break
                org = hit.get("organization") or {}
                org_slug = org.get("slug", "")
                job_slug = hit.get("slug", "")
                if not org_slug or not job_slug:
                    continue
                full_url = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{job_slug}"
                if full_url in seen_hrefs:
                    continue
                seen_hrefs.add(full_url)
                new_on_page += 1
                offices = hit.get("offices") or []
                loc_hint = offices[0].get("city", "") if offices and isinstance(offices[0], dict) else ""
                offers.append({
                    "source": "WTTJ",
                    "url": full_url,
                    "preview": hit.get("name") or "",
                    "company_hint": org.get("name") or "",
                    "location_hint": loc_hint,
                })
            if new_on_page == 0 or page_num + 1 >= int(data.get("nbPages", 1)):
                break
            page_num += 1
        except Exception as e:
            print(f"  [!] WTTJ search error: {e}")
            break
    return offers


async def scrape_jobteaser_paginated(browser: AsyncBrowserPool, query: str, max_per_query: int) -> List[Dict[str, str]]:
    """Paginate JobTeaser (university/IUT stage portal) using `.chrome_profile/` cookies."""
    print(f"[*] Searching JobTeaser: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    page_num = 1

    while len(offers) < max_per_query and page_num <= 8:
        url = (
            f"https://www.jobteaser.com/fr/job-offers"
            f"?q={quote_plus(query)}&contract_types[]=internship&page={page_num}"
        )
        final_url, html = await browser.fetch_html(url, wait_selector="a[href*='/job-offers/']", wait_ms=1800)
        if not html:
            break
        if is_login_wall(final_url, html):
            print("  [!] JobTeaser requires login — run './stage login' (or Open Login Chrome) to save cookies.")
            break
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select("a[href*='/job-offers/']")
        if not anchors:
            break
        new_on_page = 0
        for a in anchors:
            if len(offers) >= max_per_query:
                break
            href = (a.get("href") or "").split("?")[0]
            if not href or href.rstrip("/").endswith("/job-offers"):
                continue
            full_url = urljoin("https://www.jobteaser.com", href)
            if full_url in seen_hrefs:
                continue
            seen_hrefs.add(full_url)
            new_on_page += 1
            offers.append({
                "source": "JobTeaser",
                "url": full_url,
                "preview": a.get_text(" ", strip=True),
            })
        if new_on_page == 0:
            break
        page_num += 1
    return offers


async def scrape_indeed_paginated(browser: AsyncBrowserPool, query: str, max_per_query: int) -> List[Dict[str, str]]:
    """Paginate Indeed France (internship filter) using `.chrome_profile/` cookies."""
    print(f"[*] Searching Indeed France: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    start = 0

    while len(offers) < max_per_query and start <= 80:
        url = (
            f"https://fr.indeed.com/jobs"
            f"?q={quote_plus(query)}&l={quote_plus(_search_location_hw())}&jt=internship&start={start}"
        )
        final_url, html = await browser.fetch_html(url, wait_selector="a[data-jk], a[href*='/viewjob']", wait_ms=1800)
        if not html:
            break
        if is_login_wall(final_url, html):
            print("  [!] Indeed France requested login — run './stage login' to save cookies.")
            break
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select("a[data-jk], a[href*='/viewjob']")
        if not anchors:
            break
        new_on_page = 0
        for a in anchors:
            if len(offers) >= max_per_query:
                break
            jk = (a.get("data-jk") or "").strip()
            href = (a.get("href") or "").strip()
            if jk:
                full_url = f"https://fr.indeed.com/viewjob?jk={jk}"
            elif href:
                full_url = urljoin("https://fr.indeed.com", href)
            else:
                continue
            if full_url in seen_hrefs:
                continue
            seen_hrefs.add(full_url)
            new_on_page += 1
            offers.append({
                "source": "Indeed",
                "url": full_url,
                "preview": a.get_text(" ", strip=True),
            })
        if new_on_page == 0:
            break
        start += 10
    return offers


async def scrape_apec_paginated(browser: AsyncBrowserPool, query: str, max_per_query: int) -> List[Dict[str, str]]:
    """Paginate APEC (offres de stage) using `.chrome_profile/` cookies."""
    print(f"[*] Searching APEC: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    page_num = 0

    while len(offers) < max_per_query and page_num <= 6:
        url = (
            f"https://www.apec.fr/candidat/recherche-emploi.html/emploi"
            f"?q={quote_plus(query)}&page={page_num}"
        )
        final_url, html = await browser.fetch_html(url, wait_selector="a[href*='detail-offre/']", wait_ms=2000)
        if not html:
            break
        if is_login_wall(final_url, html):
            print("  [!] APEC requested login — run './stage login' to save cookies.")
            break
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select("a[href*='detail-offre/']")
        if not anchors:
            break
        new_on_page = 0
        for a in anchors:
            if len(offers) >= max_per_query:
                break
            href = (a.get("href") or "").split("?")[0]
            if not href:
                continue
            full_url = urljoin("https://www.apec.fr", href)
            if full_url in seen_hrefs:
                continue
            seen_hrefs.add(full_url)
            new_on_page += 1
            offers.append({
                "source": "APEC",
                "url": full_url,
                "preview": a.get_text(" ", strip=True),
            })
        if new_on_page == 0:
            break
        page_num += 1
    return offers


async def scrape_meteojob_paginated(browser: AsyncBrowserPool, query: str, max_per_query: int) -> List[Dict[str, str]]:
    """Paginate Meteojob (internship filter) using `.chrome_profile/` cookies."""
    print(f"[*] Searching Meteojob: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    page_num = 1

    while len(offers) < max_per_query and page_num <= 8:
        url = (
            f"https://www.meteojob.com/jobs"
            f"?what={quote_plus(query)}&where={quote_plus(_search_location_hw())}"
            f"&contractType=INTERNSHIP&page={page_num}"
        )
        final_url, html = await browser.fetch_html(url, wait_selector="a[href*='/jobs/'], a[href*='/candidat/offres/']", wait_ms=1800)
        if not html:
            break
        if is_login_wall(final_url, html):
            print("  [!] Meteojob requested login — run './stage login' to save cookies.")
            break
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select("a[href*='/jobs/'], a[href*='/candidat/offres/']")
        if not anchors:
            break
        new_on_page = 0
        for a in anchors:
            if len(offers) >= max_per_query:
                break
            href = (a.get("href") or "").split("?")[0].rstrip("/")
            if not href:
                continue
            if "/jobs/" in href and not re.search(r"/jobs/\d+$", href):
                continue
            full_url = urljoin("https://www.meteojob.com", href)
            if full_url in seen_hrefs:
                continue
            seen_hrefs.add(full_url)
            new_on_page += 1
            offers.append({
                "source": "Meteojob",
                "url": full_url,
                "preview": a.get_text(" ", strip=True),
            })
        if new_on_page == 0:
            break
        page_num += 1
    return offers


_COMPANY_CTA_REJECT = (
    "postuler",
    "site de l'entreprise",
    "site de l’entreprise",
    "candidater",
    "voir l'offre",
    "voir l’offre",
    "en savoir plus",
    "se connecter",
    "connecter",
    "créer un compte",
    "sauvegarder",
    "partager",
    "offres d'emploi",
    "offres d’emploi",
)


def clean_company_name(raw: str) -> str:
    """Strip CTA boilerplate and reject button text accidentally scraped as company names."""
    s = re.sub(r"\s+", " ", (raw or "")).strip()[:100]
    if not s:
        return ""
    s = re.sub(
        r"^(?:Plus d['’]infos sur|Voir toutes les offres de|Toutes les offres de|Recrutement par|À propos de|A propos de|Chez)\s+",
        "",
        s,
        flags=re.I,
    ).strip()
    low = s.lower()
    if not s or any(bad in low for bad in _COMPANY_CTA_REJECT):
        return ""
    return s


def extract_jsonld_company(soup: BeautifulSoup) -> str:
    """Extract schema.org JobPosting hiringOrganization.name from JSON-LD scripts if present."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or "")
        except Exception:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                items.extend(x for x in graph if isinstance(x, dict))
            org = item.get("hiringOrganization")
            if isinstance(org, dict) and org.get("name"):
                cleaned = clean_company_name(str(org["name"]))
                if cleaned:
                    return cleaned
    return ""


def parse_offer_html(offer: Dict[str, str], html: str) -> Dict[str, str]:
    """Extract company, role, location, and body text from raw offer HTML."""
    company = clean_company_name(offer.get("company_hint") or "")
    role = ""
    body_text = ""
    src = offer["source"]

    soup = BeautifulSoup(html, "html.parser")
    if not company:
        company = extract_jsonld_company(soup)

    h1 = soup.select_one("h1")
    if h1:
        role = h1.get_text(" ", strip=True)

    if src == "LinkedIn":
        if not company:
            comp_el = soup.select_one("a.topcard__org-name-link, span.topcard__flavor")
            if comp_el:
                company = clean_company_name(comp_el.get_text(" ", strip=True))
        if not company:
            m_li = re.search(r"-at-([a-z0-9-]+)-\d+$", offer["url"].rstrip("/"))
            if m_li:
                company = clean_company_name(m_li.group(1).replace("-", " ").title())
        desc_el = soup.select_one("div.show-more-less-html__markup, div.description__text")
        if desc_el:
            body_text = desc_el.get_text("\n", strip=True)

    elif src == "HelloWork":
        if not company:
            page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
            parts = [p.strip() for p in re.split(r"\s+-\s+|\s+\|\s+", page_title)]
            if len(parts) >= 2 and parts[1].lower() != "hellowork":
                company = clean_company_name(parts[1])
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
            tag.decompose()
        main_el = soup.select_one("main") or soup
        body_text = main_el.get_text("\n", strip=True)

    elif src == "WTTJ":
        if not company:
            m_wttj = re.search(r"/companies/([^/]+)/jobs/", offer["url"])
            if m_wttj:
                company = clean_company_name(m_wttj.group(1).replace("-", " ").title())
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
            tag.decompose()
        main_el = soup.select_one("main") or soup
        body_text = main_el.get_text("\n", strip=True)

    elif src == "Indeed":
        if not company:
            comp_el = soup.select_one("[data-company-name], [data-testid='inlineHeader-companyName']")
            if comp_el:
                company = clean_company_name(comp_el.get_text(" ", strip=True))
        desc_el = soup.select_one("#jobDescriptionText")
        if desc_el:
            body_text = desc_el.get_text("\n", strip=True)

    else:
        if not company:
            for comp_el in soup.select("[class*='company' i], [data-testid*='company' i], [class*='organization' i]"):
                cand = clean_company_name(comp_el.get_text(" ", strip=True))
                if cand:
                    company = cand
                    break
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
            tag.decompose()
        main_el = soup.select_one("main") or soup
        body_text = main_el.get_text("\n", strip=True)

    if not body_text:
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        body_text = soup.get_text("\n", strip=True)

    return {
        **offer,
        "company": company or offer["source"],
        "role": role or offer["preview"] or "Stagiaire Informatique",
        "location": offer.get("location_hint") or "Île-de-France",
        "text": body_text[:12000],
    }


def fetch_offer_details_http(offer: Dict[str, str]) -> Dict[str, str]:
    """Fetch offer details via pooled HTTP session."""
    resp = get_http_session().get(offer["url"], timeout=15)
    resp.raise_for_status()
    if is_login_wall(resp.url, resp.text):
        raise RuntimeError(f"{offer['source']} redirected to login")
    det = parse_offer_html(offer, resp.text)
    if offer["source"] == "WTTJ" and len(det["text"]) < 150:
        raise RuntimeError("WTTJ HTTP response lacked job body")
    return det


async def fetch_offer_details_browser(offer: Dict[str, str], browser: AsyncBrowserPool) -> Dict[str, str]:
    """Fetch offer details via async multi-tab `.chrome_profile/` browser."""
    final_url, html = await browser.fetch_html(offer["url"], wait_selector="h1, main", wait_ms=1400)
    if not html:
        raise RuntimeError("Empty HTML from browser session")
    if is_login_wall(final_url, html):
        raise RuntimeError(f"{offer['source']} redirected to login — run './stage login'")
    det = parse_offer_html(offer, html)
    role_low = (det.get("role") or "").lower()
    if any(bad in role_low for bad in ("page introuvable", "page not found", "just a moment", "access denied")):
        raise RuntimeError(f"Blocked/404 page ({det.get('role')})")
    return det


def run_tailor_for_offer(offer: Dict[str, str]) -> Path | None:
    cmd = [
        PYTHON_BIN,
        str(SCRIPTS_DIR / "tailor.py"),
        "--url",
        offer["url"],
        "--text",
        offer["text"],
        "--json",
    ]
    if offer.get("company"):
        cmd.extend(["--company", offer["company"]])
    if offer.get("role"):
        cmd.extend(["--role", offer["role"]])

    res = subprocess.run(cmd, cwd=str(WORKSPACE), capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  [!] tailor.py failed for {offer['url']}:\n{res.stderr or res.stdout}")
        return None

    for line in reversed(res.stdout.splitlines()):
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                meta = json.loads(s)
                if meta.get("metadata_path"):
                    mp = Path(meta["metadata_path"])
                    if mp.exists():
                        return mp
                if meta.get("cv_pdf"):
                    mp = Path(meta["cv_pdf"]).parent / "metadata.json"
                    if mp.exists():
                        return mp
            except json.JSONDecodeError:
                pass

    for line in res.stdout.splitlines():
        if line.startswith("CV PDF"):
            pdf_path = Path(line.split(":", 1)[1].strip())
            meta_path = pdf_path.parent / "metadata.json"
            if meta_path.exists():
                return meta_path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search French internship boards, tailor CV/LM, and run browser queue."
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="Search queries",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=ALL_SOURCES,
        default=None,
        help=f"Job boards to search or filter on --retry (choices: {', '.join(ALL_SOURCES)})",
    )
    parser.add_argument("--limit", type=int, default=12, help="Max new offers to tailor per run")
    parser.add_argument(
        "--per-query",
        type=int,
        default=None,
        help="Max offers fetched per query per source (auto-scaled with --limit if omitted)",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Only search & tailor PDFs; do not launch the browser apply queue",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-submit simple forms without pausing (marks complex ones as manual_portal)",
    )
    parser.add_argument(
        "--easy-apply",
        action="store_true",
        help="Filter LinkedIn search to Easy Apply (Candidature simplifiée) only",
    )
    parser.add_argument(
        "--skip-external",
        action="store_true",
        help="Skip external ATS redirects that require manual account/form entry",
    )
    parser.add_argument(
        "--retry",
        choices=["drafted", "manual_portal", "needs_login", "pending"],
        default="",
        help="Skip scraping and retry existing offers in applications.csv by status",
    )
    parser.add_argument(
        "--quality",
        nargs="+",
        choices=["high", "medium", "low"],
        default=None,
        help="Filter offers by desirability tier (high, medium, low)",
    )
    args = parser.parse_args()
    if not args.queries:
        args.queries = _search_cfg().get("queries") or DEFAULT_QUERIES
    retry_sources = set(args.sources) if args.sources else None
    quality_filter = set(args.quality) if args.quality else None
    if args.sources is None:
        args.sources = ["linkedin", "hellowork"]

    source_domains = {
        "linkedin": ("linkedin.com",),
        "hellowork": ("hellowork.com",),
        "wttj": ("welcometothejungle.com",),
        "jobteaser": ("jobteaser.com",),
        "indeed": ("indeed.com",),
        "apec": ("apec.fr",),
        "meteojob": ("meteojob.com",),
    }

    def _url_in_sources(offer_url: str, allowed: Optional[Set[str]]) -> bool:
        if not allowed:
            return True
        low = (offer_url or "").lower()
        return any(dom in low for s in allowed for dom in source_domains.get(s, ()))

    queue_metas: List[Path] = []
    if args.retry:
        target_statuses = {"drafted", "manual_portal"} if args.retry == "pending" else {args.retry}
        prefs = load_preferences(PROFILE_PATH)
        for r in read_applications(APPS_CSV_PATH):
            tier, _ = classify_offer_quality(
                r.get("Role", ""),
                matched_keywords=r.get("Matched_Keywords", ""),
                url=r.get("URL", ""),
                prefs=prefs,
            )
            if (
                (r.get("Status") or "").strip().lower() in target_statuses
                and r.get("CV_PDF")
                and _url_in_sources(r.get("URL", ""), retry_sources)
                and (not quality_filter or tier in quality_filter)
            ):
                mp = WORKSPACE / Path(r["CV_PDF"]).parent / "metadata.json"
                if mp.exists() and mp not in queue_metas:
                    queue_metas.append(mp)
        queue_metas = queue_metas[: args.limit]
        src_note = f" (sources: {', '.join(sorted(retry_sources))})" if retry_sources else ""
        q_note = f" (quality: {', '.join(sorted(quality_filter))})" if quality_filter else ""
        print(f"[*] Retrying {len(queue_metas)} existing '{args.retry}' offer(s){src_note}{q_note}...")
    else:
        existing_urls, existing_keys = load_existing_offers()
        raw_offers: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()
        per_query = (
            args.per_query
            if args.per_query is not None
            else max(12, math.ceil((args.limit * 2) / max(1, len(args.queries) * len(args.sources))) + 4)
        )

        # 1. Run HTTP-based searches concurrently (LinkedIn, HelloWork, WTTJ Algolia)
        http_tasks = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for q in args.queries:
                if "linkedin" in args.sources:
                    http_tasks.append(pool.submit(scrape_linkedin_paginated, q, per_query, args.easy_apply))
                if "hellowork" in args.sources:
                    http_tasks.append(pool.submit(scrape_hellowork_paginated, q, per_query))
                if "wttj" in args.sources:
                    http_tasks.append(pool.submit(scrape_wttj_paginated, q, per_query))
            for fut in as_completed(http_tasks):
                try:
                    for o in fut.result():
                        norm = o["url"].rstrip("/").lower()
                        if norm not in existing_urls and norm not in seen_urls:
                            seen_urls.add(norm)
                            raw_offers.append(o)
                except Exception as e:
                    print(f"  [!] Search task error: {e}")

        # 2. Run cookie-walled browser searches concurrently via 5-tab AsyncBrowserPool
        browser_source_keys = [s for s in args.sources if s in COOKIE_SOURCES]
        async def _run_browser_phase(
            search_sources: List[str],
            detail_browser_offers: List[Dict[str, str]],
        ) -> Tuple[List[Dict[str, str]], List[Tuple[Dict[str, str], Optional[Dict[str, str]], Optional[Exception]]]]:
            pool = AsyncBrowserPool(max_tabs=5)
            search_results: List[Dict[str, str]] = []
            detail_results: List[Tuple[Dict[str, str], Optional[Dict[str, str]], Optional[Exception]]] = []
            try:
                if search_sources:
                    browser_scrapers = {
                        "jobteaser": scrape_jobteaser_paginated,
                        "indeed": scrape_indeed_paginated,
                        "apec": scrape_apec_paginated,
                        "meteojob": scrape_meteojob_paginated,
                    }
                    coros = [
                        browser_scrapers[src_key](pool, q, per_query)
                        for q in args.queries
                        for src_key in search_sources
                        if src_key in browser_scrapers
                    ]
                    if coros:
                        batches = await asyncio.gather(*coros, return_exceptions=True)
                        for b in batches:
                            if isinstance(b, Exception):
                                print(f"  [!] Browser search error: {b}")
                            else:
                                search_results.extend(b)

                if detail_browser_offers:
                    async def _fetch_one(o: Dict[str, str]) -> Tuple[Dict[str, str], Optional[Dict[str, str]], Optional[Exception]]:
                        try:
                            return o, await fetch_offer_details_browser(o, pool), None
                        except Exception as exc:
                            return o, None, exc
                    detail_results = list(await asyncio.gather(*[_fetch_one(o) for o in detail_browser_offers]))
            finally:
                await pool.close()
            return search_results, detail_results

        if browser_source_keys:
            b_search_offers, _ = asyncio.run(_run_browser_phase(browser_source_keys, []))
            for o in b_search_offers:
                norm = o["url"].rstrip("/").lower()
                if norm not in existing_urls and norm not in seen_urls:
                    seen_urls.add(norm)
                    raw_offers.append(o)

        print(f"\n[*] Found {len(raw_offers)} new unseen offers. Pre-filtering & fetching details...")
        detail_cap = max(args.limit * 2, args.limit + 15)
        candidates: List[Dict[str, str]] = []
        for o in raw_offers:
            if len(candidates) >= detail_cap:
                break
            if is_excluded_final_year_offer(o.get("preview", "")):
                print(f"  [-] Pre-filtered PFE/Bac+5 offer: {o.get('preview', o['url'])[:80]}")
                continue
            candidates.append(o)

        http_candidates = [o for o in candidates if o["source"] in {"LinkedIn", "HelloWork", "WTTJ"}]
        browser_candidates = [o for o in candidates if o["source"] not in {"LinkedIn", "HelloWork", "WTTJ"}]

        fetched_details: List[Dict[str, str]] = []
        wttj_fallback: List[Dict[str, str]] = []

        if http_candidates:
            with ThreadPoolExecutor(max_workers=8) as pool:
                future_map = {pool.submit(fetch_offer_details_http, o): o for o in http_candidates}
                for fut in as_completed(future_map):
                    o = future_map[fut]
                    try:
                        fetched_details.append(fut.result())
                    except Exception as e:
                        if o["source"] == "WTTJ":
                            wttj_fallback.append(o)
                        else:
                            print(f"  [!] Skipping {o['url']}: {e}")

        browser_candidates.extend(wttj_fallback)
        if browser_candidates:
            _, b_details = asyncio.run(_run_browser_phase([], browser_candidates))
            for o, det, err in b_details:
                if err is not None or det is None:
                    print(f"  [!] Skipping {o['url']}: {err}")
                else:
                    fetched_details.append(det)

        detailed_offers: List[Dict[str, Any]] = []
        dup_count = 0
        excl_count = 0
        qual_drop = 0
        for det in fetched_details:
            dedup_key = normalize_company_role_key(det["company"], det["role"])
            if dedup_key in existing_keys:
                dup_count += 1
                print(f"  [-] Skipping cross-site duplicate: {det['role']} @ {det['company']}")
                continue
            sc = score_offer(det["role"], det["location"], det["text"])
            if sc < 0:
                excl_count += 1
                continue
            tier, _ = classify_offer_quality(det["role"], location=det["location"], description=det["text"], url=det.get("url", ""))
            if quality_filter and tier not in quality_filter:
                qual_drop += 1
                continue
            existing_keys.add(dedup_key)
            det["score"] = sc
            det["quality"] = tier
            detailed_offers.append(det)

        print(f"[*] Filter summary: {len(fetched_details)} fetched -> {dup_count} duplicates, {excl_count} excluded (title/Bac+5), {qual_drop} filtered by quality, {len(detailed_offers)} eligible.")

        detailed_offers.sort(key=lambda x: -x["score"])
        selected = detailed_offers[: args.limit]
        print(f"[*] Selected top {len(selected)} offers (ranked for BUT 2/3 & Paris/Saclay area).")

        company_locks: Dict[str, threading.Lock] = {}
        locks_guard = threading.Lock()

        def _tailor_safe(offer: Dict[str, str]) -> Optional[Path]:
            comp_key = re.sub(r"[^\w]+", "_", (offer.get("company") or "company").lower()).strip("_")
            with locks_guard:
                lock = company_locks.setdefault(comp_key, threading.Lock())
            with lock:
                return run_tailor_for_offer(offer)

        with ThreadPoolExecutor(max_workers=4) as pool:
            tailor_futs = {pool.submit(_tailor_safe, offer): (idx, offer) for idx, offer in enumerate(selected, 1)}
            for fut in as_completed(tailor_futs):
                idx, offer = tailor_futs[fut]
                meta_path = fut.result()
                print(
                    f"\n[{idx}/{len(selected)}] Tailored ({offer['source']}): "
                    f"{offer['role']} @ {offer['company']} (score={offer['score']})"
                )
                if meta_path:
                    queue_metas.append(meta_path)
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    print(f"  -> CV : {meta['cv_pdf']}")
                    print(f"  -> LM : {meta['lm_pdf']}")

        if not queue_metas and not args.scrape_only and APPS_CSV_PATH.exists():
            # Fall back to existing 'drafted' offers so launching manual queue always opens pending offers
            for r in read_applications(APPS_CSV_PATH):
                if (r.get("Status") or "").strip().lower() == "drafted" and r.get("CV_PDF"):
                    mp = WORKSPACE / Path(r["CV_PDF"]).parent / "metadata.json"
                    if mp.exists() and mp not in queue_metas:
                        queue_metas.append(mp)
            if queue_metas:
                print(f"[*] No brand-new offers found; queuing {len(queue_metas)} pending 'drafted' offer(s).")

    if args.scrape_only or not queue_metas:
        print(f"\n[+] Done! Tailored {len(queue_metas)} offers. Logged in {APPS_CSV_PATH}.")
        return 0

    print("\n" + "=" * 60)
    mode_label = "AUTO-SUBMIT" if args.auto else "MANUAL-REVIEW"
    print(f"STARTING {mode_label} BROWSER QUEUE ({len(queue_metas)} OFFERS IN 1 CHROME WINDOW)")
    print("=" * 60)
    cmd = [
        PYTHON_BIN,
        str(SCRIPTS_DIR / "apply_browser.py"),
        "--metas",
        *[str(mp) for mp in queue_metas],
        "--tabs",
        "5",
    ]
    if args.auto:
        cmd.append("--auto")
    if args.skip_external:
        cmd.append("--skip-external")
    res = subprocess.run(cmd, cwd=str(WORKSPACE))
    if res.returncode != 0:
        print("[!] apply_browser.py exited with non-zero status.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
