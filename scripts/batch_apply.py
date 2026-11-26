#!/usr/bin/env python3
"""
Batch Job Searcher, Tailoring & Application Queue (batch_apply.py)

1. Searches LinkedIn, HelloWork, Welcome to the Jungle, JobTeaser, Indeed,
   APEC, and Meteojob for Stage / Internship offers in Informatique.
2. Deduplicates against `data/applications.csv`.
3. Runs `scripts/tailor.py` on each matched offer to generate 1-page CV + LM PDFs.
4. Launches `scripts/apply_browser.py` for manual review or `--auto` submission.
"""

import argparse
import asyncio
import json
import math
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.config import (
    APPS_CSV_PATH,
    DEFAULT_PREFERENCES,
    PROFILE_PATH,
    SCRIPTS_DIR,
    WORKSPACE,
    load_preferences,
)
from core.offer_text import is_generic_company
from core.storage import (
    detect_source,
    load_existing_offers,
    normalize_company_role_key,
    normalize_url,
    read_applications,
)
from scrapers.boards import (
    ALL_SOURCES,
    BROWSER_SCRAPERS,
    COOKIE_SOURCES,
    scrape_hellowork_paginated,
    scrape_linkedin_paginated,
    scrape_wttj_paginated,
)
from scrapers.http_client import AsyncBrowserPool
from scrapers.parsers import (
    clean_company_name,
    fetch_offer_details_browser,
    fetch_offer_details_http,
)
from scrapers.scoring import (
    DEFAULT_QUERIES,
    classify_offer_quality,
    is_excluded_final_year_offer,
    score_offer,
    search_cfg,
)

__all__ = [
    "ALL_SOURCES",
    "COOKIE_SOURCES",
    "DEFAULT_PREFERENCES",
    "DEFAULT_QUERIES",
    "classify_offer_quality",
    "clean_company_name",
    "is_excluded_final_year_offer",
    "load_preferences",
    "score_offer",
]

PYTHON_BIN = sys.executable


def run_tailor_for_offer(offer: Dict[str, str], extra_args: Optional[List[str]] = None) -> Path | None:
    cmd = [
        PYTHON_BIN,
        str(SCRIPTS_DIR / "tailor.py"),
        "--url",
        offer["url"],
        "--text",
        offer["text"],
        "--json",
    ]
    cmd.extend(extra_args or [])
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


def remake_offers(urls: List[str]) -> int:
    """Re-fetch each tracked offer and regenerate its CV/LM with the current tailoring logic.

    Rows are updated in place (status/date kept); `applied` offers are never touched.
    """
    rows = {normalize_url(r.get("URL") or ""): r for r in read_applications(APPS_CSV_PATH)}
    todo: List[Dict[str, str]] = []
    for url in urls:
        row = rows.get(normalize_url(url))
        if row is None:
            print(f"  [!] Not in tracker, skipping: {url}")
        elif (row.get("Status") or "").strip().lower() == "applied":
            print(f"  [-] Already applied, keeping submitted documents: {row.get('Company')} - {row.get('Role')}")
        else:
            todo.append({"source": detect_source(url), "url": row["URL"], "preview": row.get("Role", ""), "_row": row})
    print(f"[*] Remaking {len(todo)} offer(s)...")

    http_sources = {"LinkedIn", "HelloWork", "WTTJ"}
    details: List[Tuple[Dict[str, str], Optional[Dict[str, str]]]] = []
    browser_offers: List[Dict[str, str]] = []
    for o in todo:
        if o["source"] in http_sources:
            try:
                details.append((o, fetch_offer_details_http(o)))
                continue
            except Exception as exc:
                print(f"  [!] HTTP fetch failed for {o['url']} ({exc}); trying browser session...")
        browser_offers.append(o)

    if browser_offers:
        async def _fetch_all() -> List[Tuple[Dict[str, str], Optional[Dict[str, str]]]]:
            pool = AsyncBrowserPool(max_tabs=5)
            out: List[Tuple[Dict[str, str], Optional[Dict[str, str]]]] = []
            try:
                async def one(o: Dict[str, str]) -> Tuple[Dict[str, str], Optional[Dict[str, str]]]:
                    try:
                        return o, await fetch_offer_details_browser(o, pool)
                    except Exception as exc:
                        print(f"  [!] Could not re-fetch {o['url']}: {exc}")
                        return o, None
                out = list(await asyncio.gather(*[one(o) for o in browser_offers]))
            finally:
                await pool.close()
            return out

        details.extend(asyncio.run(_fetch_all()))

    ok = 0
    for o, det in details:
        if det is None:
            continue
        row = o["_row"]
        if is_generic_company(det.get("company", "")) and not is_generic_company(row.get("Company", "")):
            det["company"] = row["Company"]
        print(f"  [*] Remaking: {det['role']} @ {det['company']}")
        if run_tailor_for_offer(det, extra_args=["--remake"]):
            ok += 1
    print(f"[+] Remade {ok}/{len(todo)} offer(s). Failures usually mean a login wall: run './stage login' and retry.")
    return 0 if ok == len(todo) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search French internship boards, tailor CV/LM, and run browser queue."
    )
    parser.add_argument("--queries", nargs="+", default=None, help="Search queries")
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
    parser.add_argument(
        "--remake",
        nargs="+",
        metavar="URL",
        default=None,
        help="Regenerate CV/LM for these tracked offer URLs with the current tailoring logic",
    )
    args = parser.parse_args()
    if args.remake:
        return remake_offers(args.remake)
    if not args.queries:
        args.queries = search_cfg().get("queries") or DEFAULT_QUERIES
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
                    coros = [
                        BROWSER_SCRAPERS[src_key](pool, q, per_query)
                        for q in args.queries
                        for src_key in search_sources
                        if src_key in BROWSER_SCRAPERS
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
            tier, _ = classify_offer_quality(
                det["role"],
                location=det["location"],
                description=det["text"],
                url=det.get("url", ""),
            )
            if quality_filter and tier not in quality_filter:
                qual_drop += 1
                continue
            existing_keys.add(dedup_key)
            det["score"] = sc
            det["quality"] = tier
            detailed_offers.append(det)

        print(
            f"[*] Filter summary: {len(fetched_details)} fetched -> {dup_count} duplicates, "
            f"{excl_count} excluded (title/Bac+5), {qual_drop} filtered by quality, {len(detailed_offers)} eligible."
        )

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
