"""Multi-step form navigation, submit button discovery, and validation error diagnostics."""

import re
from typing import List, Set

from playwright.async_api import Page

from browser.dom_helpers import (
    get_form_root,
    get_form_roots,
    get_human_question_text,
    get_label_text,
)


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
        "site du recruteur", "site de l'entreprise", "site de l’entreprise", "sur le site",
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
