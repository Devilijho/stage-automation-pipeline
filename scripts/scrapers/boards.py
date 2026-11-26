"""Paginated job board search scrapers (LinkedIn, HelloWork, WTTJ, JobTeaser, Indeed, APEC, Meteojob)."""

import re
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Set
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from core.config import PROFILE_PATH
from scrapers.http_client import HEADERS, AsyncBrowserPool, http_get, http_post, is_login_wall
from scrapers.scoring import search_location_hw, search_location_li

COOKIE_SOURCES = {"jobteaser", "indeed", "apec", "meteojob"}
ALL_SOURCES = ["linkedin", "hellowork", "wttj", "jobteaser", "indeed", "apec", "meteojob"]


def scrape_linkedin_paginated(
    query: str,
    max_per_query: int,
    easy_apply: bool = False,
    *,
    profile_path: Path = PROFILE_PATH,
) -> List[Dict[str, str]]:
    """Paginate LinkedIn's public guest job postings endpoint (no login wall)."""
    print(f"[*] Searching LinkedIn{' (Easy Apply only)' if easy_apply else ''}: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    start = 0

    while len(offers) < max_per_query and start <= 175:
        url = (
            f"https://fr.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={quote_plus(query)}"
            f"&location={quote_plus(search_location_li(profile_path))}"
            f"&f_JT=I{'&f_AL=true' if easy_apply else ''}&start={start}"
        )
        try:
            resp = http_get(url, timeout=15)
            if resp.status_code != 200 or not resp.text.strip():
                if resp.status_code == 429:
                    print(f"  [!] LinkedIn search rate-limited (429) at start={start}")
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
        except Exception as e:
            print(f"  [!] LinkedIn pagination error (start={start}): {e}")
            break
    return offers


def scrape_hellowork_paginated(
    query: str,
    max_per_query: int,
    *,
    profile_path: Path = PROFILE_PATH,
) -> List[Dict[str, str]]:
    """Paginate HelloWork (open French job board, stage filter c=Stage)."""
    print(f"[*] Searching HelloWork: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    page_num = 1

    while len(offers) < max_per_query and page_num <= 12:
        url = (
            f"https://www.hellowork.com/fr-fr/emploi/recherche.html"
            f"?k={quote_plus(query)}&l={quote_plus(search_location_hw(profile_path))}&c=Stage&p={page_num}"
        )
        try:
            resp = http_get(url, timeout=15)
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
            resp = http_post(
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


async def scrape_jobteaser_paginated(
    browser: AsyncBrowserPool,
    query: str,
    max_per_query: int,
    *,
    profile_path: Path = PROFILE_PATH,
) -> List[Dict[str, str]]:
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


async def scrape_indeed_paginated(
    browser: AsyncBrowserPool,
    query: str,
    max_per_query: int,
    *,
    profile_path: Path = PROFILE_PATH,
) -> List[Dict[str, str]]:
    """Paginate Indeed France (internship filter) using `.chrome_profile/` cookies."""
    print(f"[*] Searching Indeed France: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    start = 0

    while len(offers) < max_per_query and start <= 80:
        url = (
            f"https://fr.indeed.com/jobs"
            f"?q={quote_plus(query)}&l={quote_plus(search_location_hw(profile_path))}&jt=internship&start={start}"
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


async def scrape_apec_paginated(
    browser: AsyncBrowserPool,
    query: str,
    max_per_query: int,
    *,
    profile_path: Path = PROFILE_PATH,
) -> List[Dict[str, str]]:
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


async def scrape_meteojob_paginated(
    browser: AsyncBrowserPool,
    query: str,
    max_per_query: int,
    *,
    profile_path: Path = PROFILE_PATH,
) -> List[Dict[str, str]]:
    """Paginate Meteojob (internship filter) using `.chrome_profile/` cookies."""
    print(f"[*] Searching Meteojob: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    page_num = 1

    while len(offers) < max_per_query and page_num <= 8:
        url = (
            f"https://www.meteojob.com/jobs"
            f"?what={quote_plus(query)}&where={quote_plus(search_location_hw(profile_path))}"
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


BROWSER_SCRAPERS: Dict[str, Callable[[AsyncBrowserPool, str, int], Coroutine[Any, Any, List[Dict[str, str]]]]] = {
    "jobteaser": scrape_jobteaser_paginated,
    "indeed": scrape_indeed_paginated,
    "apec": scrape_apec_paginated,
    "meteojob": scrape_meteojob_paginated,
}
