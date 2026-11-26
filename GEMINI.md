# StageBot Project Rules

## Documentation & Context
- Before making architectural, CLI, UI, or cross-module changes, consult `docs/` (`docs/ARCHITECTURE.md`, `docs/FUNCTIONALITIES.md`, `docs/CONTRIBUTING_AND_TUNING.md`) for project structure and module behavior.
- **Keep `docs/` up to date**: After any major modification to the code (new scripts, endpoints, CLI flags, scraper/automation behaviors, config schema changes, or UI features), update the relevant files in `docs/` (or add new documentation there) so the docs remain an accurate reference.
- **Docs + Commit on Every Feature**: Whenever a feature or significant change is added, update the relevant `docs/` files and create a clean `git commit` (staging only source/config-example/docs files—never personal or generated artifacts).

## Core Project Invariants
- **Strict Grounding**: Never invent or exaggerate skills, projects, or experience in generated CVs or cover letters. `scripts/tailor.py` must only match keywords and projects verified in `config/master_profile.md` and `templates/base_cv.tex`.
- **Single Source of Truth**: Keep personal details, internship dates, search queries, and form answers in `config/profile.json`. If you add or rename keys in `config/profile.json`, update `config/profile.example.json` and `docs/CONTRIBUTING_AND_TUNING.md` accordingly.
- **1-Page PDF Guarantee**: Changes to `templates/base_cv.tex`, `templates/base_lm.tex`, or `scripts/tailor.py` must preserve the 1-page compiled PDF constraint.
- **Privacy & Gitignore Hygiene**: Never commit or expose personal/generated artifacts (`config/profile.json`, `config/master_profile.md`, `data/applications.csv`, `data/rapport_candidatures.html`, `output/`, `.chrome_profile/`).
- **Safe Application Tracking**: Only mark an application as `applied` in `data/applications.csv` after submission is explicitly confirmed (`check_submission_confirmed`); otherwise use `manual_portal` or `drafted`.
