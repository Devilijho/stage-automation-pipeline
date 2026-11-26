# Known Issues & Roadmap

Tracked improvements and architectural gaps to address next, especially for reducing `manual_portal` fallbacks.

## 1. Custom / Non-Native Dropdowns (`[role="combobox"]`, `aria-haspopup="listbox"`) — *Deferred (#2)*
- **Problem**: `_answer_selects()` in `scripts/browser/form_filler.py` only handles native `<select>` elements. Modern portals (WTTJ, JobTeaser, Workday, SmartRecruiters, Ashby, Greenhouse) use custom ARIA comboboxes/listboxes, leaving required dropdowns blank (`stuck_step_*` in `debug/`).
- **Proposed Fix**: Add a handler in `form_filler.py` for `[role='combobox']` / `button[aria-haspopup='listbox']` (click to open, read `[role='option']`, match via rules or `ask_llm_for_field`, and click the target option).

## 2. Dedicated Site Adapters & Per-Site Hardening (`APEC`, `JobTeaser`, `WTTJ`, `Indeed`, `Meteojob`) — *Phase 1 Implemented (`scripts/browser/adapters/`), Phase 2 Next*
- **Phase 1 (Done)**: Split site-specific selectors, confirmation phrases, login-wall rules, and lifecycle hooks into `scripts/browser/adapters/` (`base.py`, `registry.py`, and 7 thin site adapters: `linkedin.py`, `hellowork.py`, `wttj.py`, `jobteaser.py`, `indeed.py`, `apec.py`, `meteojob.py`) wired into both Auto-Apply and Semi-Automatic mode with dynamic adapter switching on redirect.
- **Phase 2 (Next)**: Inspect real failure notes & `debug/*_after_submit.png` screenshots per site to harden `wttj.py`, `jobteaser.py`, `indeed.py`, `apec.py`, and `meteojob.py` individually without affecting LinkedIn/HelloWork.

## 3. Shadow DOM Traversal in `dom_helpers.py` & `form_filler.py`
- **Problem**: `xpath=ancestor::...` queries do not cross Shadow DOM boundaries (used by Workday and custom web components).
- **Proposed Fix**: Replace `xpath=ancestor::...` lookups with CSS/Playwright `locator.evaluate(...)` DOM traversal that walks `getRootNode().host` when needed.

## 4. Manual Review Step-by-Step Assist — *Implemented (`Fill Current Page`)*
- Added the **Fill Current Page** button to the Manual Review card (`ui/index.html`, `ui/app.js`, `/api/manual_action`, `runner.py`) so clicking past login walls or "Next" steps lets you re-run `fill_matching_fields`, `answer_common_questions`, and `inject_files` on demand.

## 5. Externalize `tailor.py` Projects & Skills Catalog — *Implemented (`config/projects.json`)*
- Externalized `projects`, `skills_categories`, `extra_keywords`, and `cover_letter` prose from `scripts/tailor.py` into `config/projects.json` / `config/profile.json` (with `config/projects.example.json` template).

## 6. Spontaneous Mailer Domain Lookup & UI Re-Enable
- **Problem**: `btn-mail` is disabled in `ui/index.html`, and `scripts/spontaneous_mail.py` guesses `recrutement@<company>.fr` without verifying domains/MX records.
- **Proposed Fix**: Add website/MX lookup or `api.gouv.fr` enrichment before re-enabling the UI button.

## 7. Debug Sandbox Mode — Known Limitations & Future Upgrades
- **Fake CV/LM Tailoring (Future Upgrade)**: Currently Debug Fetch (`--debug`) skips `tailor.py` / LaTeX compilation and attaches shared blank 1-page PDFs (`debug/sandbox/empty_cv.pdf` and `debug/sandbox/empty_lm.pdf`) to save disk space and time. A future upgrade can add a fake Law (`Droit`) LaTeX template + `projects.json` so `tailor.py` can also be tested end-to-end in Debug Mode.
- **Shared Chrome Cookies (Known Limitation)**: Debug Mode currently reuses `.chrome_profile/` cookies so login sessions do not have to be re-entered. A future upgrade can add an optional isolated `.chrome_profile_debug/` session if testing logged-out vs logged-in flows separately is needed.
- **Non-Tech Question Rules (Known Gap)**: `scripts/browser/question_rules.py::classify_yes_no_question()` and `_PROFILE_SKILL_CATALOG` still contain IT/BUT-specific defaults (e.g., mobile dev, full-stack, tech stack keywords) that may not answer Law (`Droit`) screening questions automatically until rules are generalized per profile domain.
- **Debug Sandbox Reset Button**: Add a one-click **Reset Debug Sandbox** action in the UI (and `POST /api/debug/reset`) to wipe `debug/sandbox/` and re-seed the default Droit profile (`Camille MOREAU`), CSV, and caches cleanly.
- **LinkedIn Easy Apply vs External Redirects on Law Offers**: Legal/law offers on LinkedIn, APEC, and WTTJ frequently redirect to external ATS portals (Workday, Taleo, Flatchr, Softy) — making Debug Sandbox ideal for testing `GenericAdapter` and custom ARIA comboboxes (Issue #1).
