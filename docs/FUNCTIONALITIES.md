# Functionalities Summary (AI & Developer Quick Reference)

Use this reference to understand what each module does without reading the full source code.

## 1. GitHub Indexer (`scripts/sync_github.py`)
- **Purpose**: Builds `config/master_profile.md` from verified GitHub repos and `templates/base_cv.tex`.
- **Attribution safety**: Shallow-clones repos into `.cache/repos/` and runs `git log --author=...` so collaborative repos (`chaski-booking`, `Transcendence`, `cgj-26`) only credit commits and files authored by the user.
- **Flags**: `--skip-clone`, `--include-forks`.

## 2. ATS Tailoring Engine (`scripts/tailor.py`)
- **Purpose**: Generates grounded, 1-page `CV_<Name>_<Company>.pdf` and `LM_<Name>_<Company>.pdf` in `output/<Company>/`, plus `metadata.json`.
- **Grounding**: Only matches keywords from `KNOWN_KEYWORDS` that are verified in `config/master_profile.md` or `templates/base_cv.tex`.
- **CV Adaptation**: Updates `CV_TITLE`, `CV_AVAILABILITY` (including *Stage conventionné — Université Paris-Saclay* and Île-de-France mobility), role-archetype `CV_ACCROCHE` (`cyber`, `infra`, `web`, `systems`), dynamically includes verified skills from `master_profile.md` with matched keywords **bolded**, and selects the top 3 curated, verified projects with clickable GitHub links.
- **Cover Letter Adaptation (`build_lm_tex`)**: French *Vous / Moi / Nous* letter with auto-formatted French date (`Bures-sur-Yvette, le ...`), inferred offer city, cleaned prose role titles (no double `"stage"` or `"(H/F)"`), archetype-specific *Vous* hook, and concrete project accomplishments in *Moi*.
- **Page Verification**: Compiles via `pdflatex` or `tectonic` and verifies `len(PdfReader.pages) == 1`, auto-trimming project bullets (3 → 2 → 1) if needed.
- **Tracker**: Appends to `data/applications.csv` with columns `Date,Company,Role,URL,Status,CV_Title_Used,Matched_Keywords,CV_PDF,LM_PDF` and prevents duplicates by both normalized URL and normalized `(Company, Role)` across job boards.

## 3. Batch Scraper & Ranker (`scripts/batch_apply.py`)
- **Sources** (`--sources`):
  - LinkedIn public guest API (`fr.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search`, supports `--easy-apply` via `f_AL=true`).
  - HelloWork (`www.hellowork.com/fr-fr/emploi/recherche.html?c=Stage&p=1..5`).
  - Welcome to the Jungle (`wttj`: `www.welcometothejungle.com/fr/jobs?...&refinementList[contract_type][]=internship`).
  - JobTeaser (`jobteaser`: `www.jobteaser.com/fr/job-offers?...&contract_types[]=internship`).
  - Indeed France (`indeed`: `fr.indeed.com/jobs?...&jt=internship`).
  - APEC (`apec`: `www.apec.fr/candidat/recherche-emploi.html/offres-stage.html`).
  - Meteojob (`meteojob`: `www.meteojob.com/jobs?...&contractType=INTERNSHIP`).
- **Cookie Reuse & Concurrency (`AsyncBrowserPool` + `ThreadPoolExecutor`)**: HTTP searches (`linkedin`, `hellowork`, `wttj` Algolia) and HTTP detail fetches (`LinkedIn`, `HelloWork`, and `WTTJ` fast-path) run concurrently using pooled `requests.Session`s. For `jobteaser`, `indeed`, `apec`, `meteojob` (and `wttj` fallback), `batch_apply.py` launches an async 5-tab headless Playwright Chromium pool on `.chrome_profile/` so saved login cookies are reused with smart selector waits and once-per-domain cookie dismissal. Tailoring (`tailor.py`) also runs concurrently across up to 4 workers with `fcntl.flock` CSV locking.
- **Scaling >20 Offers**: `--per-query` auto-scales with `--limit` when omitted, per-source page caps are expanded (including Algolia pagination for `wttj`), and the Web UI (`#q-limit`) supports `5`, `10`, `20`, `30`, `50`, and `100` offers.
- **Scoring (`score_offer`) & Pre-Filter (`is_excluded_final_year_offer`)**: Excludes/penalizes `PFE`, `Fin d'études`, `Fin de cursus`, `Bac+5`, `Master 2` (`-50` or skipped before detail fetch) unless `BUT 2/3` or `Bac+2/3` is explicitly accepted; boosts `BUT 2`, `BUT 3`, `Bac+2`, `Bac+3`, `IUT`, `3 mois`, and Paris/Saclay/Bures-sur-Yvette locations.
- **Modes**: `--scrape-only`, `--auto`, `--retry drafted|manual_portal|pending`, `--skip-external`.

## 4. Async Multi-Tab Browser Assistant (`scripts/apply_browser.py`)
- **Concurrency**: Uses `playwright.async_api` + `asyncio.Semaphore(5)` (`--tabs 5`) inside 1 persistent Chrome profile (`.chrome_profile/`). `--auto` runs headless (`channel="chromium"`) by default so it never steals OS keyboard/window focus (pass `--headed` to watch it).
- **Automation**:
  - `accept_cookies_if_present`: Dismisses Didomi, OneTrust, Axeptio, Tarteaucitron, and generic French consent modals (including iframes).
  - `click_apply_and_follow_redirect`: Scans main page + iframes, uses `page.expect_popup()` + `context.pages` new-tab fallback, and handles secondary ATS landing buttons (e.g., Lever `/apply`).
  - `fill_matching_fields` + `answer_common_questions`: Scans active modal/page + all `page.frames` (iframes) via `get_form_roots()`; extracts radio options from `<label for>`, wrapping `<label>`, sibling text, `aria-label`, and LinkedIn `.fb-text-selectable__option` via `extract_radio_options()`; matches mandatory GDPR/privacy/contact-consent radios & checkboxes; handles up to `15` multi-step form pages with stuck-step detection (`get_step_signature`) and visible button dumps (`dump_visible_buttons`) on bailout.
  - `inject_files`: Attaches `CV_*.pdf` / `LM_*.pdf` via `<input type="file">` (page + iframes) or `page.expect_file_chooser()`.
  - `uncheck_follow_company`: Unchecks LinkedIn's `#follow-company-checkbox`.
  - `check_submission_confirmed`: Uses per-site confirmation phrases (`SITE_CONFIRMATION_PHRASES` for LinkedIn, HelloWork, WTTJ, JobTeaser, Indeed, APEC, Meteojob) with a generalistic fallback (`CONFIRMATION_PHRASES_EXTERNAL`) for external portals, plus strict URL confirmation tokens (excluding HelloWork `/bounce/createalert` & `origin=responseoffer` redirect URLs), modal closing, and `200/201` POST/PUT submit responses. Manual UI `Skip`/`Mark Applied` actions override status with `force=True`. Separates `needs_login` (e.g. APEC login walls, retryable via `--retry needs_login`) and `expired` (closed/expired Indeed or LinkedIn offers) from `manual_portal`.
- **Session Setup**: `--login` (with optional `--sources ...`) opens LinkedIn, HelloWork, Welcome to the Jungle, JobTeaser, Indeed, APEC, and Meteojob login pages in `.chrome_profile/` so cookies are persisted and reused automatically by both `batch_apply.py` and `apply_browser.py`.

## 5. Spontaneous Local Mailer (`scripts/spontaneous_mail.py`)
- **Purpose**: Queries `recherche-entreprises.api.gouv.fr` for NAF `62.01Z` / `62.02A` companies within 15 km of Bures-sur-Yvette, runs `tailor.py` with `spontaneous://<slug>`, and writes ready-to-open `.eml` drafts + `mailto_link.txt`.

## 6. macOS Web UI (`scripts/ui_server.py` & `ui/index.html`)
- **Server**: `ThreadingHTTPServer` on `127.0.0.1:8765` with endpoints `GET /api/applications`, `POST /api/applications/status`, `GET /api/profile`, `GET/POST /api/questions`, `POST /api/run`, `POST /api/stop`, `GET /api/logs`, `GET /api/report`.
- **UI**: 7-step sidebar (`1. Intro & Guide`, `2. Fetch Offers`, `3. Apply`, `4. Applications`, `5. CV & Letter Preview`, `6. Profile & GitHub`, `7. Q&A & Rules`), `ES | FR | EN` language switcher, live log viewer with actionable `Clean | Raw` toggle, full status sidebar counters (`total`, `applied`, `drafted`, `manual_portal`, `needs_login`, `expired/skipped`), per-platform & 3-tier quality (`high`/`medium`/`low`) filters on Fetch/Apply/Tracker, 1-by-1 Manual Review card with Finder reveal + `View CV & LM` + `Mark Applied` + `Skip`, Resume PDF auto-import (`POST /api/import_resume`), `preferences` editor (`POST /api/preferences`), bulk-skip in Tracker (`POST /api/applications/bulk_status`), and printable IUT University Proof Report (`data/rapport_candidatures.html`).
- **CV Tailoring (`scripts/tailor.py`)**: Per-bullet keyword relevance ranking inside `select_and_format_projects()` and gradual 1-page shrinking (`(3,3,3) -> (3,3,2) -> (3,2,2) -> ...`) with zero LLM calls.
