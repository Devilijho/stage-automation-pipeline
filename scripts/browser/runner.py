"""Concurrent Playwright offer runner, manual review loop, and login session setup."""

import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.async_api import Page, async_playwright

from browser.ats_rules import (
    EXPIRED_OFFER_PHRASES,
    LOGIN_URLS,
    _has_visible_modal,
    check_submission_confirmed,
    detect_ats_provider,
    save_debug_screenshot,
)
from browser.dom_helpers import accept_cookies_if_present, click_apply_and_follow_redirect
from browser.form_filler import (
    _prune_blank_linkedin_experience,
    answer_common_questions,
    fill_matching_fields,
    inject_files,
)
from browser.navigation import (
    collect_validation_errors,
    dump_visible_buttons,
    find_submit_button,
    get_step_signature,
    try_click_next_step,
    uncheck_follow_company,
)
from browser.question_rules import load_master_profile_skills
from core.browser_session import (
    build_chrome_launch_kwargs,
    prepare_chrome_profile_dir,
    restore_storage_cookies,
    save_storage_cookies,
)
from core.config import (
    CHROME_PROFILE_DIR,
    MANUAL_CURRENT_PATH,
    MANUAL_SKIP_PATH,
    PROFILE_PATH,
    WORKSPACE,
)
from core.logging_utils import get_logger
from core.storage import update_application_status

_log = get_logger("browser.runner")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def update_tracker_status(
    url: str,
    company: str,
    new_status: str,
    force: bool = False,
    notes: Optional[str] = None,
) -> None:
    update_application_status(url, new_status, company=company, force=force, notes=notes)


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
        update_tracker_status(url, company, "needs_login", notes=f"Login wall ({detect_ats_provider(page.url)})")
        return page, "needs_login"
    if "linkedin.com/jobs/search" in page.url.lower() and "/jobs/view/" in nav_url.lower():
        print(f"[!] Bailout [{company}]: LinkedIn offer expired/redirected to search results -> 'expired'")
        update_tracker_status(url, company, "expired", notes="Offer expired/redirected to search")
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
        update_tracker_status(url, company, "expired", notes="Offer expired/redirected to search")
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
            update_tracker_status(url, company, "expired", notes=f"Offer closed/expired ({ats})")
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
            update_tracker_status(url, company, "needs_login", notes=f"Login wall ({ats})")
            return active_page, "needs_login"
        vis_btns = await dump_visible_buttons(active_page)
        print(f"[!] Bailout [{company}]: Apply button not found on page ({active_page.url}) | Visible buttons: {vis_btns}")
        if auto:
            update_tracker_status(url, company, "manual_portal", notes=f"Apply button not found ({ats})")
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
        update_tracker_status(url, company, "needs_login", notes=f"Login/account wall ({ats})")
        return active_page, "needs_login"

    verified_skills = load_master_profile_skills()
    seen_unanswered: Set[str] = set()
    last_step_unanswered: List[str] = []
    bailout_reason = ""
    final_status = "manual_portal"
    max_steps = 15

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
            filled = await fill_matching_fields(active_page, profile["form_answers"], profile=profile)
            filled.extend(
                await answer_common_questions(
                    active_page,
                    profile["form_answers"],
                    verified_skills=verified_skills,
                    seen_unanswered=seen_unanswered,
                    step_unanswered=step_unanswered,
                    company=company,
                    url=url,
                    profile=profile,
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
                update_tracker_status(url, company, "manual_portal", notes=f"Skipped external ATS ({ats})")
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

                    def _on_response(resp, _sink: List[str] = submit_ok_responses) -> None:
                        try:
                            if resp.status in (200, 201, 202, 204) and resp.request.method in ("POST", "PUT", "PATCH"):
                                r_url = (resp.url or "").lower()
                                if any(k in r_url for k in ("apply", "application", "candidat", "submit", "postul", "response", "jobs")):
                                    _sink.append(f"{resp.status} {resp.url}")
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
                        update_tracker_status(url, company, "applied", notes="")
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
                        update_tracker_status(url, company, "applied", notes="")
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
        update_tracker_status(url, company, final_status, notes=bailout_reason[:300])
        print(f"[*] [{company}] Finished -> '{final_status}'")
    return active_page, final_status


async def _run_login_session(sources: List[str]) -> int:
    async with async_playwright() as p:
        login_kwargs = build_chrome_launch_kwargs(CHROME_PROFILE_DIR, headless=False, no_viewport=True)
        context = await p.chromium.launch_persistent_context(**login_kwargs)
        await restore_storage_cookies(context, CHROME_PROFILE_DIR)
        labels: List[str] = []
        for idx, src_key in enumerate(sources):
            label, login_url = LOGIN_URLS[src_key]
            labels.append(label)
            page = context.pages[0] if (idx == 0 and context.pages) else await context.new_page()
            try:
                await page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                print(f"  [!] Could not open {label} ({login_url}): {e}")
        print(f"[*] Opened login tabs for: {', '.join(labels)}.")
        print("[*] Sign in on each tab so cookies are saved in .chrome_profile/, then close the Chrome window when done.")

        ticks = 0
        try:
            while len(context.pages) > 0:
                await asyncio.sleep(0.5)
                ticks += 1
                if ticks % 4 == 0:
                    await save_storage_cookies(context, CHROME_PROFILE_DIR)
        except Exception:
            pass
        await save_storage_cookies(context, CHROME_PROFILE_DIR)
        await context.close()
    return 0


async def _run_auto_pool(
    context: Any,
    profile: Dict[str, Any],
    meta_paths: List[Path],
    max_workers: int,
    skip_external: bool,
    url_override: str,
) -> None:
    print(f"\n=== Running {len(meta_paths)} offer(s) with {max_workers}-slot rolling worker pool ===")
    sem = asyncio.Semaphore(max_workers)

    async def worker_task(mp: Path) -> None:
        async with sem:
            meta = load_json(mp)
            u = url_override if (len(meta_paths) == 1 and url_override) else meta.get("url", "")
            comp = meta.get("company", "Offer")
            if not u:
                return
            pg = await context.new_page()
            active_pg = pg
            try:
                active_pg, _ = await asyncio.wait_for(
                    process_offer_concurrently(
                        pg, profile, meta, u, auto=True, skip_external=skip_external
                    ),
                    timeout=90,
                )
            except asyncio.TimeoutError:
                _log.warning("[%s] Worker timed out after 90s", comp)
                print(f"[!] Bailout [{comp}]: Timed out after 90s -> 'manual_portal'")
                update_tracker_status(u, comp, "manual_portal", notes="Timed out after 90s")
            except Exception as e:
                _log.exception("[%s] Unexpected worker error: %s", comp, e)
                print(f"[!] Bailout [{comp}]: Unexpected worker error ({e}) -> 'manual_portal'")
                update_tracker_status(u, comp, "manual_portal", notes=f"Worker error: {str(e)[:200]}")
            finally:
                for tab_to_close in {pg, active_pg}:
                    if tab_to_close and not tab_to_close.is_closed() and len(context.pages) > 1:
                        try:
                            await asyncio.wait_for(tab_to_close.close(), timeout=5)
                        except Exception:
                            pass

    await asyncio.gather(*(worker_task(mp) for mp in meta_paths))
    try:
        await asyncio.wait_for(context.close(), timeout=15)
    except Exception:
        pass


async def _run_manual_review(
    playwright_inst: Any,
    context: Any,
    launch_kwargs: Dict[str, Any],
    profile: Dict[str, Any],
    meta_paths: List[Path],
    skip_external: bool,
    url_override: str,
) -> None:
    for idx, mp in enumerate(meta_paths):
        meta = load_json(mp)
        u = url_override if (len(meta_paths) == 1 and url_override) else meta.get("url", "")
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
            context = await playwright_inst.chromium.launch_persistent_context(**launch_kwargs)

        pg = context.pages[0] if context.pages else await context.new_page()
        res = await process_offer_concurrently(
            pg, profile, meta, u, auto=False, skip_external=skip_external
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
                            update_tracker_status(
                                u,
                                meta.get("company", ""),
                                action,
                                force=True,
                                notes="" if action == "applied" else "Skipped from UI",
                            )
                            print(f"[*] [{meta.get('company', '')}] Marked '{action}' from UI — advancing...")
                        try:
                            await active_pg.close()
                        except Exception:
                            pass
                        break
                    if not confirmed_here and await check_submission_confirmed(active_pg):
                        confirmed_here = True
                        update_tracker_status(u, meta.get("company", ""), "applied", notes="")
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
        return await _run_login_session(args.sources)

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
    launch_kwargs = build_chrome_launch_kwargs(CHROME_PROFILE_DIR, headless=run_headless)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(**launch_kwargs)
        await restore_storage_cookies(context, CHROME_PROFILE_DIR)
        if args.auto:
            await _run_auto_pool(
                context,
                profile,
                meta_paths,
                max_workers=max(1, args.tabs),
                skip_external=args.skip_external,
                url_override=args.url,
            )
        else:
            await _run_manual_review(
                p,
                context,
                launch_kwargs,
                profile,
                meta_paths,
                skip_external=args.skip_external,
                url_override=args.url,
            )
    return 0
