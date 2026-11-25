# Functionalities Summary (AI & Developer Quick Reference)

Use this reference to understand what each module does without reading the full source code.

## 1. GitHub Indexer (`scripts/sync_github.py`)
- **Purpose**: Builds `config/master_profile.md` from verified GitHub repos and `templates/base_cv.tex`.
- **Attribution safety**: Shallow-clones repos into `.cache/repos/` and runs `git log --author=...` so collaborative repos (`chaski-booking`, `Transcendence`, `cgj-26`) only credit commits and files authored by the user.
- **Flags**: `--skip-clone`, `--include-forks`.

## 2. ATS Tailoring Engine (`scripts/tailor.py`)
- **Purpose**: Generates grounded, 1-page `CV_<Name>_<Company>.pdf` and `LM_<Name>_<Company>.pdf` in `output/<Company>/`, plus `metadata.json`.
- **Grounding**: Only matches keywords from `KNOWN_KEYWORDS` that are verified in `config/master_profile.md` or `templates/base_cv.tex`.
- **CV Adaptation**: Updates `CV_TITLE`, `CV_AVAILABILITY` (from `profile.json`), `CV_ACCROCHE`, reorders `SKILLS_SECTION` categories/items by job overlap, and selects the top 3 matching projects.
- **Page Verification**: Compiles via `pdflatex` or `tectonic` and verifies `len(PdfReader.pages) == 1`, auto-trimming project bullets (3 → 2 → 1) if needed.
- **Tracker**: Appends to `data/applications.csv` with columns `Date,Company,Role,URL,Status,CV_Title_Used,Matched_Keywords,CV_PDF,LM_PDF` and prevents duplicates by both normalized URL and normalized `(Company, Role)` across job boards.

## 3. Batch Scraper & Ranker (`scripts/batch_apply.py`)
- **Sources**:
  - LinkedIn public guest API (`fr.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search`, supports `--easy-apply` via `f_AL=true`).
  - HelloWork (`www.hellowork.com/fr-fr/emploi/recherche.html?c=Stage&p=1..5`).
- **Scoring (`score_offer`) & Pre-Filter (`is_excluded_final_year_offer`)**: Excludes/penalizes `PFE`, `Fin d'études`, `Fin de cursus`, `Bac+5`, `Master 2` (`-50` or skipped before detail fetch) unless `BUT 2/3` or `Bac+2/3` is explicitly accepted; boosts `BUT 2`, `BUT 3`, `Bac+2`, `Bac+3`, `IUT`, `3 mois`, and Paris/Saclay/Bures-sur-Yvette locations.
- **Modes**: `--scrape-only`, `--auto`, `--retry drafted|manual_portal|pending`, `--skip-external`.

## 4. Async Multi-Tab Browser Assistant (`scripts/apply_browser.py`)
- **Concurrency**: Uses `playwright.async_api` + `asyncio.Semaphore(5)` (`--tabs 5`) inside 1 persistent Chrome profile (`.chrome_profile/`). `--auto` runs headless (`channel="chromium"`) by default so it never steals OS keyboard/window focus (pass `--headed` to watch it).
- **Automation**:
  - `accept_cookies_if_present`: Dismisses Didomi, OneTrust, Axeptio, Tarteaucitron, and generic French consent modals (including iframes).
  - `click_apply_and_follow_redirect`: Uses `page.expect_popup()` per tab and handles secondary ATS landing buttons (e.g., Lever `/apply`).
  - `fill_matching_fields` + `answer_common_questions`: Scoped inside `.jobs-easy-apply-modal`; fills contact info, `fname`/`lname`, `06`/`07` phone, SmartRecruiters work history (`your title`, `company`, `description`), HelloWork motivation message, cover letter textareas, numeric experience years, `<select>` dropdowns, `<fieldset>` & `[role='radiogroup']` radios (via `classify_yes_no_question` grounded in `load_master_profile_skills()` from `config/master_profile.md`), `<input type="checkbox">` / `[role="checkbox"]` legal/GDPR/required consent toggles (leaving non-required marketing unchecked), and `try_fill_date_or_calendar` (start `03/05/2026`, end `16/07/2026`, including calendar grid popups). Unknown questions + options are logged to `data/unanswered_questions.json` (and saved answers there are reused on future runs), and exact bailout reasons are printed before marking `manual_portal`.
  - `inject_files`: Attaches `CV_*.pdf` / `LM_*.pdf` via `<input type="file">` (page + iframes) or `page.expect_file_chooser()`.
  - `uncheck_follow_company`: Unchecks LinkedIn's `#follow-company-checkbox`.
  - `check_submission_confirmed`: Polls for French/English confirmation messages and updates `data/applications.csv` to `applied` only upon confirmation.
- **Session Setup**: `--login` opens LinkedIn and HelloWork login pages in `.chrome_profile/`.

## 5. Spontaneous Local Mailer (`scripts/spontaneous_mail.py`)
- **Purpose**: Queries `recherche-entreprises.api.gouv.fr` for NAF `62.01Z` / `62.02A` companies within 15 km of Bures-sur-Yvette, runs `tailor.py` with `spontaneous://<slug>`, and writes ready-to-open `.eml` drafts + `mailto_link.txt`.

## 6. macOS Web UI (`scripts/ui_server.py` & `ui/index.html`)
- **Server**: `ThreadingHTTPServer` on `127.0.0.1:8765` with endpoints `GET /api/applications`, `POST /api/applications/status`, `GET /api/profile`, `GET/POST /api/questions`, `POST /api/run`, `POST /api/stop`, `GET /api/logs`, `GET /api/report`.
- **UI**: 7-step sidebar (`1. Intro & Guide`, `2. Fetch Offers`, `3. Apply`, `4. Applications`, `5. CV & Letter Preview`, `6. Profile & GitHub`, `7. Q&A & Rules`), `ES | FR | EN` language switcher, live log viewer with `Copy` & `Stop`, Q&A editor for `data/unanswered_questions.json` and `profile.json` `form_answers`, and printable IUT University Proof Report (`data/rapport_candidatures.html`).
