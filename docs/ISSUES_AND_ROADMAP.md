# Known Issues & Roadmap

Tracked improvements and architectural gaps to address next, especially for reducing `manual_portal` fallbacks.

## 1. Custom / Non-Native Dropdowns (`[role="combobox"]`, `aria-haspopup="listbox"`) — *Deferred (#2)*
- **Problem**: `_answer_selects()` in `scripts/browser/form_filler.py` only handles native `<select>` elements. Modern portals (WTTJ, JobTeaser, Workday, SmartRecruiters, Ashby, Greenhouse) use custom ARIA comboboxes/listboxes, leaving required dropdowns blank (`stuck_step_*` in `debug/`).
- **Proposed Fix**: Add a handler in `form_filler.py` for `[role='combobox']` / `button[aria-haspopup='listbox']` (click to open, read `[role='option']`, match via rules or `ask_llm_for_field`, and click the target option).

## 2. Dedicated ATS Adapters for Top Blockers (`APEC`, `JobTeaser`, `WTTJ / WelcomeKit`) — *Deferred (#4)*
- **Problem**: `APEC`, `JobTeaser`, and `WTTJ` make up the majority of `manual_portal` rows because each has custom modal/redirect flows and confirmation screens not always recognized by the generic loop.
- **Proposed Fix**:
  - Add per-ATS adapters under `scripts/browser/ats_adapters/` for WelcomeKit's inline drawer, APEC's "Postuler avec mon profil" flow, and JobTeaser's internal vs external apply redirects.
  - Inspect `debug/*_after_submit.png` screenshots to expand `SITE_CONFIRMATION_PHRASES` in `scripts/browser/ats_rules.py`.

## 3. Shadow DOM Traversal in `dom_helpers.py` & `form_filler.py`
- **Problem**: `xpath=ancestor::...` queries do not cross Shadow DOM boundaries (used by Workday and custom web components).
- **Proposed Fix**: Replace `xpath=ancestor::...` lookups with CSS/Playwright `locator.evaluate(...)` DOM traversal that walks `getRootNode().host` when needed.

## 4. Manual Review Step-by-Step Assist ("Auto-Fill Current Step")
- **Problem**: `_run_manual_review()` only auto-fills step 1; clicking past a login wall, cookie banner, or "Next" button manually leaves steps 2–5 unfilled.
- **Proposed Fix**: Add an **Auto-Fill Current Step** action in the UI Manual Review card (and `/api/manual_action`) that re-runs `fill_matching_fields`, `answer_common_questions`, and `inject_files` on the active tab on demand.

## 5. Externalize `tailor.py` Projects & Skills Catalog
- **Problem**: `curated_catalog`, `build_skills_block()`, and parts of `build_lm_tex()` in `scripts/tailor.py` are still hardcoded in Python rather than loaded from `config/`.
- **Proposed Fix**: Move curated projects/skills metadata to `config/projects.json` (or `config/profile.json`) so adding projects or updating skills requires zero Python edits.

## 6. Spontaneous Mailer Domain Lookup & UI Re-Enable
- **Problem**: `btn-mail` is disabled in `ui/index.html`, and `scripts/spontaneous_mail.py` guesses `recrutement@<company>.fr` without verifying domains/MX records.
- **Proposed Fix**: Add website/MX lookup or `api.gouv.fr` enrichment before re-enabling the UI button.
