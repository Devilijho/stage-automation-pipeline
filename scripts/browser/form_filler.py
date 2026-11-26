"""Form filling: standard contact inputs, datepickers, radios, checkboxes, selects, and CV/LM uploads."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from browser.dom_helpers import (
    _commit_autocomplete_if_open,
    _has_country_code_select,
    _local_fr_phone,
    get_form_roots,
    get_human_question_text,
    get_label_text,
)
from browser.question_rules import (
    classify_yes_no_question,
    load_master_profile_skills,
    load_saved_unanswered_answers,
    log_unanswered_question,
)
from core.config import (
    PROFILE_PATH,
    get_postal_code,
    load_custom_rules,
    load_profile,
    match_custom_rule,
    parse_internship_dates,
)
from core.llm import ask_llm_for_field
from core.logging_utils import get_logger

_log = get_logger("browser.form_filler")


async def fill_matching_fields(
    page: Page,
    answers: Dict[str, str],
    profile: Optional[Dict[str, Any]] = None,
) -> List[str]:
    prof = profile if profile is not None else load_profile(PROFILE_PATH)
    postal_code = get_postal_code(prof)
    field_rules = [
        (["prénom", "prenom", "first name", "firstname", "first_name", "fname"], answers["prenom"]),
        (["nom", "last name", "lastname", "last_name", "lname", "surname"], answers["nom"]),
        (["full name", "nom complet"], answers["nom_complet"]),
        (["e-mail", "email", "courriel", "mail"], answers["email"]),
        (["téléphone", "telephone", "phone", "mobile", "tel", "06 ", "07 "], answers["telephone"]),
        (["code postal", "postal code", "postalcode", "codepostal", "zip"], postal_code),
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


async def try_fill_date_or_calendar(
    page: Page,
    el,
    is_end: bool,
    profile: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Fill <input type='date'>, text date input, or click through a calendar selection table."""
    prof = profile if profile is not None else load_profile(PROFILE_PATH)
    parsed_dates = parse_internship_dates(prof)
    spec = parsed_dates["end" if is_end else "start"]
    iso_val = spec["iso"]
    fr_val = spec["fr"]
    target_day = spec["day"]
    target_month_idx = spec["month_idx_0"]
    target_year = spec["year"]
    target_month_names = spec["month_names"]

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
        except PlaywrightTimeout:
            pass
        except Exception as exc:
            _log.debug("Text date fill failed (%s), falling back to calendar", exc)

    try:
        await el.click()
        await page.wait_for_timeout(400)
        cal = page.locator("[role='grid']:visible, table:has(td):visible, .artdeco-datepicker:visible").last
        if await cal.count() > 0 and await cal.is_visible(timeout=800):
            parent = cal.locator("xpath=ancestor::div[1]").first
            for sel in await parent.locator("select:visible").all():
                opts = [await o.inner_text() for o in await sel.locator("option").all()]
                if any(target_year in o for o in opts):
                    await sel.select_option(label=next(o for o in opts if target_year in o))
                elif any(m in o.lower() for o in opts for m in target_month_names):
                    m_lbl = next(o for o in opts if any(m in o.lower() for m in target_month_names))
                    await sel.select_option(label=m_lbl)

            for _ in range(8):
                cal_txt = (await parent.inner_text()).lower()
                if any(m in cal_txt for m in target_month_names) and target_year in cal_txt:
                    break
                nxt = parent.locator("button[aria-label*='next' i], button[aria-label*='suivant' i], button:has-text('>')").first
                if await nxt.count() > 0 and await nxt.is_visible(timeout=300):
                    await nxt.click()
                    await page.wait_for_timeout(200)
                else:
                    break

            day_btn = cal.locator(
                f"button:text-is('{target_day}'), td:text-is('{target_day}'), [data-month='{target_month_idx}'] button:text-is('{target_day}')"
            ).first
            if await day_btn.count() > 0 and await day_btn.is_visible(timeout=500):
                await day_btn.click()
                await page.wait_for_timeout(300)
                return f"calendar -> {fr_val}"
    except PlaywrightTimeout:
        pass
    except Exception as exc:
        _log.warning("Calendar date fill error: %s", exc)
    return None


async def extract_radio_options(doc_ctx: Any, fs: Any) -> List[Tuple[str, Any]]:
    """Extract `(option_text, clickable_locator)` for all radio options inside a fieldset/radiogroup."""
    results: List[Tuple[str, Any]] = []
    seen_texts: Set[str] = set()

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


async def _answer_text_inputs(
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
    *,
    profile: Optional[Dict[str, Any]] = None,
    company: str = "",
    url: str = "",
) -> None:
    prof = profile if profile is not None else load_profile(PROFILE_PATH)
    edu = prof.get("education") or {}
    default_school = answers.get("ecole") or edu.get("school") or "IUT d'Orsay"
    secondary_school = edu.get("secondary_school") or ""
    school_org = f"{secondary_school} / {default_school}".strip(" /")
    postal_code = get_postal_code(prof)
    formation = answers.get("formation") or edu.get("degree") or "BUT 2 Informatique"
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
                    val = f"Étudiant {formation} / Développeur"
                elif any(k in q for k in ("company", "entreprise", "société")) and "linkedin" not in q:
                    val = school_org
                elif "description" in q:
                    skills_preview = ", ".join(sorted(verified_skills)[:8]) or "C/C++, TypeScript, Linux, Docker, SQL"
                    val = f"Développement logiciel, systèmes et sécurité ({skills_preview})."
                elif any(
                    k in q
                    for k in (
                        "message au recruteur", "motivations", "message personnalisé", "pourquoi ce poste",
                        "expliquez-nous pourquoi", "souhaitez nous rejoindre", "pourquoi voulez-vous",
                        "cover letter", "coverletter", "motivationletter", "lettre de motivation",
                    )
                ):
                    val = (
                        f"Bonjour, étudiant en {formation} ({default_school}), "
                        f"je recherche un stage ({answers.get('disponibilite', '3 mois')}). "
                        f"Opérationnel sur {', '.join(sorted(verified_skills)[:6]) or 'C/C++, Linux, SQL'}, "
                        f"je serais ravi de rejoindre votre équipe. "
                        f"Cordialement, {answers.get('nom_complet', '')}."
                    )
                elif any(k in q_all for k in ("code postal", "postal code", "postalcode", "codepostal", "zip")):
                    val = postal_code
                elif any(k in q for k in ("adresse électronique", "e-mail", "email")):
                    val = answers.get("email", "")
                elif any(k in q for k in ("année", "années", "years", "combien d'exp", "how long", "how many years")):
                    val = "1"
                elif any(k in q for k in ("durée", "duration", "combien de mois", "months")):
                    val = "3" if is_num else answers.get("duree", "3 mois")
                elif any(k in q for k in ("date de fin", "end date", "jusqu", "to date", "fin de stage")):
                    res_date = await try_fill_date_or_calendar(page, el, is_end=True, profile=prof)
                    if res_date:
                        answered.append(f"end_date -> {res_date}")
                        continue
                elif any(k in q for k in ("date de début", "start date", "quand pouvez", "when can you", "disponib", "à partir", "date")):
                    res_date = await try_fill_date_or_calendar(page, el, is_end=False, profile=prof)
                    if res_date:
                        answered.append(f"start_date -> {res_date}")
                        continue
                elif any(k in q for k in ("salaire", "rémunération", "gratification", "salary", "compensation")):
                    val = "800" if is_num else "Gratification légale"
                elif any(k in q for k in ("ville", "city", "localisation", "londres, paris", "paris, new york", "location")):
                    val = f"{answers.get('ville', 'Paris')}, France"
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
                            log_unanswered_question("input", human_q, [], company=company, url=url)
                            print(f"  [?] Unanswered required input: {human_q}")
            except PlaywrightTimeout:
                continue
            except Exception as exc:
                _log.warning("Text input fill error: %s", exc)
                continue
    except Exception as exc:
        _log.warning("Text inputs scan error: %s", exc)


async def _answer_selects(
    doc_ctx: Any,
    root: Any,
    answers: Dict[str, str],
    verified_skills: Set[str],
    seen_unanswered: Set[str],
    step_unanswered: List[str],
    saved_answers: Dict[str, str],
    custom_rules: List[Dict[str, str]],
    answered: List[str],
    *,
    company: str = "",
    url: str = "",
) -> None:
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
                            log_unanswered_question("select", human_q, clean_opts, company=company, url=url)
                            print(f"  [?] Unanswered required select: {human_q} | Options: {clean_opts}")
            except PlaywrightTimeout:
                continue
            except Exception as exc:
                _log.warning("Select fill error: %s", exc)
                continue
    except Exception as exc:
        _log.warning("Selects scan error: %s", exc)


async def _answer_radios_and_pills(
    doc_ctx: Any,
    root: Any,
    answers: Dict[str, str],
    verified_skills: Set[str],
    seen_unanswered: Set[str],
    step_unanswered: List[str],
    saved_answers: Dict[str, str],
    custom_rules: List[Dict[str, str]],
    answered: List[str],
    *,
    company: str = "",
    url: str = "",
) -> None:
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
                            log_unanswered_question("radio", human_q, option_texts, company=company, url=url)
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

    # 3a. Loose radio groups NOT wrapped in <fieldset> or [role='radiogroup']
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
                choices = (saved_val,) if saved_val else classify_yes_no_question(legend, verified_skills)
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


async def _answer_checkboxes(
    doc_ctx: Any,
    root: Any,
    verified_skills: Set[str],
    seen_unanswered: Set[str],
    step_unanswered: List[str],
    saved_answers: Dict[str, str],
    custom_rules: List[Dict[str, str]],
    answered: List[str],
    *,
    company: str = "",
    url: str = "",
) -> None:
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
                        log_unanswered_question("checkbox", human_q, ["checked", "unchecked"], company=company, url=url)
                        print(f"  [?] Unanswered required checkbox: {human_q}")
            except PlaywrightTimeout:
                continue
            except Exception as exc:
                _log.warning("Checkbox fill error: %s", exc)
                continue
    except Exception as exc:
        _log.warning("Checkboxes scan error: %s", exc)


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
    *,
    profile: Optional[Dict[str, Any]] = None,
    company: str = "",
    url: str = "",
) -> None:
    await _answer_text_inputs(
        page, doc_ctx, root, answers, verified_skills,
        seen_unanswered, step_unanswered, saved_answers, custom_rules, answered,
        profile=profile, company=company, url=url,
    )
    await _answer_selects(
        doc_ctx, root, answers, verified_skills,
        seen_unanswered, step_unanswered, saved_answers, custom_rules, answered,
        company=company, url=url,
    )
    await _answer_radios_and_pills(
        doc_ctx, root, answers, verified_skills,
        seen_unanswered, step_unanswered, saved_answers, custom_rules, answered,
        company=company, url=url,
    )
    await _answer_checkboxes(
        doc_ctx, root, verified_skills,
        seen_unanswered, step_unanswered, saved_answers, custom_rules, answered,
        company=company, url=url,
    )


async def answer_common_questions(
    page: Page,
    answers: Dict[str, str],
    verified_skills: Optional[Set[str]] = None,
    seen_unanswered: Optional[Set[str]] = None,
    step_unanswered: Optional[List[str]] = None,
    company: str = "",
    url: str = "",
    profile: Optional[Dict[str, Any]] = None,
) -> List[str]:
    if verified_skills is None:
        verified_skills = load_master_profile_skills()
    if seen_unanswered is None:
        seen_unanswered = set()
    if step_unanswered is None:
        step_unanswered = []
    prof = profile if profile is not None else load_profile(PROFILE_PATH)
    saved_answers = load_saved_unanswered_answers()
    custom_rules = load_custom_rules()
    answered: List[str] = []
    for doc_ctx, root in await get_form_roots(page):
        await _answer_common_questions_in_root(
            page, doc_ctx, root, answers, verified_skills,
            seen_unanswered, step_unanswered, saved_answers, custom_rules, answered,
            profile=prof, company=company, url=url,
        )
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
