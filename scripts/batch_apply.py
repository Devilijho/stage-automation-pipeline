#!/usr/bin/env python3
"""
Batch Job Searcher, Tailoring & Application Queue (batch_apply.py)

1. Searches LinkedIn (paginated guest API) & HelloWork (French job board) —
   plus optional Welcome to the Jungle — for Stage / Internship offers in
   Informatique (prioritizing BUT 2 / BUT 3 / Bac+2 / Bac+3 near Bures-sur-Yvette,
   Saclay, Massy, Palaiseau, Paris).
2. Deduplicates against `data/applications.csv`.
3. Runs `scripts/tailor.py` on each matched offer to generate 1-page CV + LM PDFs.
4. Opens each offer one-by-one in Chrome, auto-fills fields & attaches PDFs,
   and either pauses for manual review (default) or auto-submits simple forms (--auto).
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

WORKSPACE = Path(__file__).resolve().parent.parent
PROFILE_PATH = WORKSPACE / "config" / "profile.json"
APPS_CSV_PATH = WORKSPACE / "data" / "applications.csv"
OUTPUT_DIR = WORKSPACE / "output"
SCRIPTS_DIR = WORKSPACE / "scripts"
PYTHON_BIN = sys.executable

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

PROFILE = json.loads(PROFILE_PATH.read_text(encoding="utf-8")) if PROFILE_PATH.exists() else {}
SEARCH_CFG = PROFILE.get("search", {})

DEFAULT_QUERIES = SEARCH_CFG.get("queries", [
    "stage informatique",
    "stage développeur",
    "stage cybersécurité",
    "stage réseaux systèmes",
])
SEARCH_LOCATION_LI = SEARCH_CFG.get("location", "Paris, Île-de-France, France")
SEARCH_LOCATION_HW = SEARCH_CFG.get("location_short", "Paris")
LOCATION_BOOST = SEARCH_CFG.get("location_boost", ["bures-sur-yvette", "orsay", "saclay", "paris"])
LEVEL_BOOST = SEARCH_CFG.get("level_boost", ["but 2", "but 3", "bac+2", "bac+3", "3 mois"])

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


def load_existing_urls() -> Set[str]:
    if not APPS_CSV_PATH.exists():
        return set()
    with APPS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        return {
            (r.get("URL") or "").strip().rstrip("/").lower()
            for r in csv.DictReader(f)
            if (r.get("URL") or "").strip()
        }


def score_offer(title: str, location: str, description: str) -> int:
    t_low = title.lower()
    l_low = location.lower()
    d_low = description.lower()

    if any(bad in t_low for bad in EXCLUDE_TITLE_TERMS) and "stage" not in t_low:
        return -100

    # Filter out Bac+5 / PFE / Fin d'études unless Bac+2/3 or BUT is explicitly accepted
    is_but_friendly = any(k in d_low or k in t_low for k in ("but 2", "but 3", "but2", "but3", "bac+2", "bac +2", "bac+3", "bac +3", "iut", "dut", "2ème année", "3ème année"))
    is_pfe_or_m2 = any(k in t_low or k in d_low for k in ("pfe", "fin d'études", "fin d'etudes", "bac+5", "bac +5", "master 2", "élève-ingénieur"))
    if is_pfe_or_m2 and not is_but_friendly:
        return -50

    score = 0
    if "stage" in t_low or "intern" in t_low or "stagiaire" in t_low:
        score += 10
    for loc in LOCATION_BOOST:
        if loc in l_low or loc in d_low:
            score += 4
    for lvl in LEVEL_BOOST:
        if lvl in d_low or lvl in t_low:
            score += 8
    return score


def scrape_linkedin_paginated(query: str, max_per_query: int, easy_apply: bool = False) -> List[Dict[str, str]]:
    """Paginate LinkedIn's public guest job postings endpoint (no login wall)."""
    print(f"[*] Searching LinkedIn{' (Easy Apply only)' if easy_apply else ''}: '{query}' ...")
    offers: List[Dict[str, str]] = []
    seen_hrefs: Set[str] = set()
    start = 0

    while len(offers) < max_per_query and start <= 75:
        url = (
            f"https://fr.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={quote_plus(query)}"
            f"&location={quote_plus(SEARCH_LOCATION_LI)}"
            f"&f_JT=I{'&f_AL=true' if easy_apply else ''}&start={start}"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
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
            time.sleep(0.6)
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

    while len(offers) < max_per_query and page_num <= 5:
        url = (
            f"https://www.hellowork.com/fr-fr/emploi/recherche.html"
            f"?k={quote_plus(query)}&l={quote_plus(SEARCH_LOCATION_HW)}&c=Stage&p={page_num}"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
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
            time.sleep(0.6)
        except Exception as e:
            print(f"  [!] HelloWork error (p={page_num}): {e}")
            break
    return offers


def fetch_offer_details(offer: Dict[str, str]) -> Dict[str, str]:
    """Fetch full title, company, and job description via HTTP."""
    company = ""
    role = ""
    body_text = ""

    resp = requests.get(offer["url"], headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    h1 = soup.select_one("h1")
    if h1:
        role = h1.get_text(" ", strip=True)

    if offer["source"] == "LinkedIn":
        comp_el = soup.select_one("a.topcard__org-name-link, span.topcard__flavor")
        if comp_el:
            company = comp_el.get_text(" ", strip=True)
        if not company:
            m_li = re.search(r"-at-([a-z0-9-]+)-\d+$", offer["url"].rstrip("/"))
            if m_li:
                company = m_li.group(1).replace("-", " ").title()
        desc_el = soup.select_one("div.show-more-less-html__markup, div.description__text")
        if desc_el:
            body_text = desc_el.get_text("\n", strip=True)

    elif offer["source"] == "HelloWork":
        # HelloWork title is typically "Role - Company - City | HelloWork" or inside header
        page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
        parts = [p.strip() for p in re.split(r"\s+-\s+|\s+\|\s+", page_title)]
        if len(parts) >= 2 and parts[1].lower() != "hellowork":
            company = re.sub(r"^Recrutement\s+par\s+", "", parts[1], flags=re.I).strip()
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
        "location": "Île-de-France",
        "text": body_text[:12000],
    }


def run_tailor_for_offer(offer: Dict[str, str]) -> Path | None:
    cmd = [
        PYTHON_BIN,
        str(SCRIPTS_DIR / "tailor.py"),
        "--url",
        offer["url"],
        "--text",
        offer["text"],
    ]
    if offer.get("company"):
        cmd.extend(["--company", offer["company"]])
    if offer.get("role"):
        cmd.extend(["--role", offer["role"]])

    res = subprocess.run(cmd, cwd=str(WORKSPACE), capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  [!] tailor.py failed for {offer['url']}:\n{res.stderr or res.stdout}")
        return None

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
        default=DEFAULT_QUERIES,
        help="Search queries",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=["linkedin", "hellowork"],
        default=["linkedin", "hellowork"],
        help="Job boards to search (default: linkedin hellowork)",
    )
    parser.add_argument("--limit", type=int, default=12, help="Max new offers to tailor per run")
    parser.add_argument("--per-query", type=int, default=10, help="Max offers fetched per query per source")
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
        choices=["drafted", "manual_portal", "pending"],
        default="",
        help="Skip scraping and retry existing offers in applications.csv by status",
    )
    args = parser.parse_args()

    queue_metas: List[Path] = []
    if args.retry:
        target_statuses = {"drafted", "manual_portal"} if args.retry == "pending" else {args.retry}
        if APPS_CSV_PATH.exists():
            with APPS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if (r.get("Status") or "").strip().lower() in target_statuses and r.get("CV_PDF"):
                        mp = WORKSPACE / Path(r["CV_PDF"]).parent / "metadata.json"
                        if mp.exists() and mp not in queue_metas:
                            queue_metas.append(mp)
        queue_metas = queue_metas[: args.limit]
        print(f"[*] Retrying {len(queue_metas)} existing '{args.retry}' offer(s)...")
    else:
        existing_urls = load_existing_urls()
        raw_offers: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()

        for q in args.queries:
            if "linkedin" in args.sources:
                for o in scrape_linkedin_paginated(q, args.per_query, easy_apply=args.easy_apply):
                    norm = o["url"].rstrip("/").lower()
                    if norm not in existing_urls and norm not in seen_urls:
                        seen_urls.add(norm)
                        raw_offers.append(o)
            if "hellowork" in args.sources:
                for o in scrape_hellowork_paginated(q, args.per_query):
                    norm = o["url"].rstrip("/").lower()
                    if norm not in existing_urls and norm not in seen_urls:
                        seen_urls.add(norm)
                        raw_offers.append(o)

        print(f"\n[*] Found {len(raw_offers)} new unseen offers. Fetching details & scoring...")
        detailed_offers: List[Dict[str, Any]] = []
        for o in raw_offers[: args.limit * 2]:
            try:
                det = fetch_offer_details(o)
                sc = score_offer(det["role"], det["location"], det["text"])
                if sc >= 0:
                    det["score"] = sc
                    detailed_offers.append(det)
                time.sleep(0.3)
            except Exception as e:
                print(f"  [!] Skipping {o['url']}: {e}")

        detailed_offers.sort(key=lambda x: -x["score"])
        selected = detailed_offers[: args.limit]
        print(f"[*] Selected top {len(selected)} offers (ranked for BUT 2/3 & Paris/Saclay area).")

        for idx, offer in enumerate(selected, 1):
            print(
                f"\n[{idx}/{len(selected)}] Tailoring ({offer['source']}): "
                f"{offer['role']} @ {offer['company']} (score={offer['score']})"
            )
            meta_path = run_tailor_for_offer(offer)
            if meta_path:
                queue_metas.append(meta_path)
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                print(f"  -> CV : {meta['cv_pdf']}")
                print(f"  -> LM : {meta['lm_pdf']}")

        if not queue_metas and not args.scrape_only and APPS_CSV_PATH.exists():
            # Fall back to existing 'drafted' offers so launching manual queue always opens pending offers
            with APPS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
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
        "4",
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
