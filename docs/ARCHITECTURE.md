# Architecture & Workspace Structure

StageBot is a local, grounded internship application pipeline designed for **Santiago FUENTES LLANOS** (BUT 2 Informatique — Université Paris-Saclay / IUT d'Orsay & École 42 Paris).

## Directory Layout

```text
cv-proyect/
├── stage                  # 1-word CLI launcher (ui, search, apply, auto, mail, sync, status, report, login, check)
├── StageBot.command       # Double-clickable macOS launcher for the desktop UI
├── pyproject.toml         # Project metadata, Python >=3.12 requirement, dependencies, ruff & pytest config
├── LICENSE                # All Rights Reserved — Private Personal Use Only
├── README.md              # Quick-start guide
├── config/
│   ├── profile.json       # Single source of truth (contact, availability, search queries, form answers) [gitignored]
│   ├── profile.example.json # Anonymized template for profile.json
│   └── master_profile.md  # Verified GitHub + academic skill/project inventory [gitignored]
├── templates/
│   ├── base_cv.tex        # Base 1-page LaTeX CV template with marker blocks
│   └── base_lm.tex        # Base 1-page LaTeX Lettre de Motivation template (Vous / Moi / Nous)
├── scripts/
│   ├── core/              # Shared core package (config/profile I/O, locked atomic CSV storage, Chrome session, LLM cache)
│   ├── sync_github.py     # Indexes public/private/collaborative GitHub repos via `gh` + `git log --author`
│   ├── tailor.py          # ATS keyword matcher + grounded CV/LM LaTeX compiler + 1-page verifier (`--json` supported)
│   ├── batch_apply.py     # Paginated LinkedIn, HelloWork, WTTJ, JobTeaser, Indeed, APEC & Meteojob scraper + BUT 2/3 scorer
│   ├── apply_browser.py   # Async Playwright 5-tab Chrome runner (`.chrome_profile/` cookies, redirects, Easy Apply, portals)
│   ├── spontaneous_mail.py # Local tech company finder (`api.gouv.fr`) + `.eml` draft builder
│   ├── ui_server.py       # Local HTTP server (`127.0.0.1:8765`) for the macOS Web UI
│   └── check_code.py      # Static syntax/undefined-name checks, optional ruff lint, and unit test runner (`./stage check`)
├── tests/                 # Offline unit test suite (storage locking, scoring, tailor helpers, rules & config)
├── ui/
│   └── index.html         # Single-page dark-mode dashboard (ES | FR | EN)
├── data/
│   ├── applications.csv   # Deduplicated offer tracker with `CV_PDF` & `LM_PDF` links [gitignored]
│   ├── llm_answer_cache.json # Cached grounded LLM answers for unknown form fields [gitignored]
│   └── rapport_candidatures.html # Printable University Proof Report [gitignored]
├── docs/                  # Architecture, functionalities summary, and tuning guide
└── output/                # Per-company folders containing tailored CV/LM PDFs, `.tex`, `.eml`, & `metadata.json` [gitignored]
```

## Data Flow

1. **Index (`sync_github.py`)** → Reads `gh` repos + `git log --author` → writes `config/master_profile.md`.
2. **Fetch & Tailor (`batch_apply.py` → `tailor.py`)** → Scrapes LinkedIn, HelloWork, WTTJ, JobTeaser, Indeed, APEC & Meteojob (reusing `.chrome_profile/` cookies automatically) → deduplicates against `data/applications.csv` → ranks for BUT 2/3 & Paris/Saclay → compiles 1-page `CV_*.pdf` & `LM_*.pdf` → logs `drafted` in `data/applications.csv`.
3. **Apply (`apply_browser.py`)** → Launches 1 persistent Chrome window (`.chrome_profile/`) with up to 5 concurrent tabs (`asyncio.gather`) → fills forms, attaches PDFs, unchecks *Follow company*, and updates `data/applications.csv` (`applied` on confirmed submission, `manual_portal` otherwise).
4. **Spontaneous Outreach (`spontaneous_mail.py`)** → Queries `recherche-entreprises.api.gouv.fr` around Bures-sur-Yvette/Saclay → runs `tailor.py` → saves `.eml` drafts with both PDFs attached.
