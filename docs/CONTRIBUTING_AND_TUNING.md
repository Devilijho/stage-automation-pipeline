# Tuning & Customization Guide

## Updating Personal Info, Dates, or Search Queries
Edit `config/profile.json` (single source of truth):
- `internship.start_date`, `end_date`, `availability_short`, `availability_full` → automatically reflected in `tailor.py` CVs/LMs and the UI sidebar.
- `search.queries`, `location`, `location_boost`, `level_boost` → automatically used by `batch_apply.py`.
- `form_answers` → automatically used by `apply_browser.py`.

## Adding New Screening Question Rules
In `scripts/apply_browser.py`:
- Text/number inputs: add keyword checks inside `answer_common_questions()` (Section 1).
- Dropdowns / Radios: add keywords to Section 2 (`select`) or Section 3 (`fieldset`).
- Submit buttons: add text/ARIA selectors to `find_submit_button()`.

## Adding Academic Background
Place `curriculum_but2_b.md` inside `config/` and run `./stage sync` to merge your official BUT 2 curriculum into `config/master_profile.md`.
