# Internship (Stage) Application Automation Pipeline

Automated, grounded application pipeline for **Santiago FUENTES LLANOS** (BUT 2 Informatique — IUT d'Orsay & École 42 Paris).  
**Availability:** `03/05` to `16/07` (3 months, extendable over the summer / *prolongeable durant l'été*).

## Folder Structure
```text
cv-proyect/
├── stage                  # 1-word CLI launcher (./stage search, apply, auto, status, sync)
├── README.md              # This guide
├── config/
│   ├── profile.json       # Contact info, availability, & standard French form answers
│   └── master_profile.md  # Verified GitHub + academic inventory
├── templates/
│   ├── base_cv.tex        # Base CV LaTeX template
│   └── base_lm.tex        # Base Lettre de Motivation LaTeX template
├── scripts/
│   ├── sync_github.py     # GitHub project indexer
│   ├── tailor.py          # ATS CV & LM generator (1-page PDF verified)
│   ├── apply_browser.py   # Playwright Chrome assistant (manual review or --auto)
│   └── batch_apply.py     # Batch job searcher + ranking + queue
├── data/
│   └── applications.csv   # Offer tracker & duplicate prevention log
└── output/
    └── <Company>/         # Tailored CV_*.pdf, LM_*.pdf, and metadata.json
```

## Simple Commands

```bash
# Launch the macOS desktop UI (or double-click StageBot.command)
./stage ui

# Export & open your University Proof Report (rapport_candidatures.html)
./stage report

# Search LinkedIn + HelloWork & generate tailored CV/LM PDFs
./stage search --limit 12

# Search + tailor + open manual-review Chrome queue (you review & click Envoyer)
./stage apply --limit 8

# Search + tailor + auto-submit simple forms (complex ones marked manual_portal)
./stage auto --limit 8

# Check all tracked offers and their statuses
./stage status

# Open Chrome assistant for one already-tailored company
./stage open Thales

# Re-index your GitHub repos into config/master_profile.md
./stage sync
```

*(Optional)* Add a global `stage` command to your terminal:
```bash
echo 'alias stage="/Users/devilijho/Documents/cv-proyect/stage"' >> ~/.zshrc && source ~/.zshrc
```

## Documentation & License
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Directory layout and data flow
- [docs/FUNCTIONALITIES.md](docs/FUNCTIONALITIES.md) — Quick reference of all modules, flags, and UI endpoints
- [docs/CONTRIBUTING_AND_TUNING.md](docs/CONTRIBUTING_AND_TUNING.md) — Customizing `profile.json` and screening rules
- [LICENSE](LICENSE) — **All Rights Reserved (Private Personal Use Only)**
