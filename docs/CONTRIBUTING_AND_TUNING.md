# Tuning & Customization Guide

## Updating Personal Info, Dates, or Search Queries
Edit `config/profile.json` (single source of truth):
- `internship.start_date`, `end_date`, `availability_short`, `availability_full` → automatically reflected in `tailor.py` CVs/LMs and the UI sidebar.
- `search.queries`, `location`, `location_boost`, `level_boost` → automatically used by `batch_apply.py`.
- `preferences` (`target_roles`, `avoid_roles`, `preferred_locations`, `idf_locations`, `work_types`, `avoid_locations`) → controls 3-tier offer quality scoring (`high`, `medium`, `low`) in `batch_apply.py` and the Web UI (also editable in **6. Profile & GitHub** or auto-filled via **Import from Resume (PDF)**).
- `form_answers` → automatically used by `apply_browser.py`.
- `custom_rules` → list of `{"keyword": "...", "answer": "..."}` rules matched case-insensitively across `<input>`, `<select>`, radio groups, and checkboxes in `apply_browser.py` (also editable via the **7. Q&A & Rules** UI tab).

## Adding New Screening Question Rules
- Grounded yes/no rules and verified skill tokens (`_PROFILE_SKILL_CATALOG`, `classify_yes_no_question()`) live in `scripts/browser/question_rules.py`.
- Text/number inputs, dropdowns, radios, and checkboxes are filled in `scripts/browser/form_filler.py` (`_answer_text_inputs`, `_answer_selects`, `_answer_radios_and_pills`, `_answer_checkboxes`).
- Submit button selectors (`find_submit_button()`) live in `scripts/browser/navigation.py`, and per-site confirmation phrases live in `scripts/browser/ats_rules.py`.
- Board scrapers and scoring live in `scripts/scrapers/boards.py` and `scripts/scrapers/scoring.py`.

## Adding Academic Background
Place `curriculum_but2_b.md` inside `config/` and run `./stage sync` to merge your official BUT 2 curriculum into `config/master_profile.md`.

## Verifying Changes (`./stage check`)
Before committing any change, run `./stage check` to verify Python compilation, catch undefined names across `scripts/` and `tests/`, run `ruff` (if installed), and pass the offline unit tests in `tests/`.
