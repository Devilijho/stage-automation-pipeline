"""DOM helpers: cookie consent dismissal, Apply redirect following, labels, and form roots."""

import re
from typing import Any, List, Optional, Tuple

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from browser.adapters import SiteAdapter, resolve_adapter


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


async def click_apply_and_follow_redirect(
    page: Page, adapter: Optional[SiteAdapter] = None
) -> Tuple[Page, bool]:
    active_adapter = adapter or resolve_adapter(page.url or "")
    selectors = active_adapter.apply_selectors
    try:
        await page.wait_for_selector(active_adapter.apply_wait_selector, timeout=12000)
    except Exception:
        pass

    async def _handle_new_page(new_page: Page) -> Page:
        await new_page.wait_for_load_state("domcontentloaded", timeout=15000)
        await new_page.wait_for_timeout(1000)
        await accept_cookies_if_present(new_page)
        dest_adapter = resolve_adapter(new_page.url or "")
        for sub_sel in dest_adapter.secondary_apply_selectors:
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
                if active_adapter.is_in_page_apply_selector(sel):
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

    try:
        legend = el.locator("legend, xpath=ancestor::fieldset[1]/legend").first
        if await legend.count() > 0:
            txt = re.sub(r"\s+", " ", await legend.inner_text()).strip()
            if txt:
                candidates.append(txt)
    except Exception:
        pass

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

    try:
        wrap_lbl = el.locator("xpath=ancestor::label[1]").first
        if await wrap_lbl.count() > 0:
            txt = re.sub(r"\s+", " ", await wrap_lbl.inner_text()).strip()
            if txt:
                candidates.append(txt)
    except Exception:
        pass

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

    for attr in ("aria-label", "placeholder"):
        try:
            val = await el.get_attribute(attr)
            if val and val.strip():
                candidates.append(val.strip())
        except Exception:
            pass

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

    for c in candidates:
        if c:
            return c[:250]
    return ""


async def get_form_root(page: Page, adapter: Optional[SiteAdapter] = None):
    active_adapter = adapter or resolve_adapter(getattr(page, "url", "") or "")
    for sel in active_adapter.form_root_selectors:
        try:
            root_el = page.locator(sel).first
            if await root_el.count() > 0 and await root_el.is_visible(timeout=400):
                return root_el
        except Exception:
            continue
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
