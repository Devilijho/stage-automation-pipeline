"""HTML parsing, company extraction, and offer detail fetching for scraped job pages."""

import json
import re
from typing import Dict

from bs4 import BeautifulSoup

from core.offer_text import company_from_listing, is_generic_company, sanitize_role, truncate_words
from scrapers.http_client import AsyncBrowserPool, get_http_session, is_login_wall

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

    preview = offer.get("preview") or ""
    if is_generic_company(company):
        # Listing cards often read "<Title> H/F <Employer> - <City> - 75 ..."; never trust the board name.
        company = company_from_listing(preview) or company
    clean_role = sanitize_role(role) or sanitize_role(preview) or truncate_words(role or preview, 70)

    return {
        **offer,
        "company": company or offer["source"],
        "role": clean_role or "Stagiaire Informatique",
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
