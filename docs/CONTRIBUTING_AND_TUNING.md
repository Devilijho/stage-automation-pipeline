# Tuning & Customization Guide

## Updating Personal Info, Dates, or Search Queries
Edit `config/profile.json` (single source of truth):
- `internship.start_date`, `end_date`, `availability_short`, `availability_full` → automatically reflected in `tailor.py` CVs/LMs and the UI sidebar.
- `search.queries`, `location`, `location_boost`, `level_boost` → automatically used by `batch_apply.py`.
- `preferences` (`target_roles`, `avoid_roles`, `preferred_locations`, `idf_locations`, `work_types`, `avoid_locations`) → controls 3-tier offer quality scoring (`high`, `medium`, `low`) in `batch_apply.py` and the Web UI (also editable in **6. Profile & GitHub** or auto-filled via **Import from Resume (PDF)**).
- `form_answers` → automatically used by `apply_browser.py`.
- `custom_rules` → list of `{"keyword": "...", "answer": "..."}` rules matched case-insensitively across `<input>`, `<select>`, radio groups, and checkboxes in `apply_browser.py` (also editable via the **7. Q&A & Rules** UI tab).

## Adding New Screening Question Rules
In `scripts/apply_browser.py`:
- Text/number inputs: add keyword checks inside `answer_common_questions()` (Section 1).
- Dropdowns / Radios (`<select>`, `<fieldset>`, `[role='radiogroup']`): add grounded yes/no rules in `classify_yes_no_question()` or verified skill tokens in `_PROFILE_SKILL_CATALOG` (matched against `config/master_profile.md` via `load_master_profile_skills()`).
- Submit buttons: add text/ARIA selectors to `find_submit_button()`.

## Adding Academic Background
Place `curriculum_but2_b.md` inside `config/` and run `./stage sync` to merge your official BUT 2 curriculum into `config/master_profile.md`.

## Verifying Changes (`./stage check`)
Before committing any change, run `./stage check` to verify Python compilation, catch undefined names across `scripts/` and `tests/`, run `ruff` (if installed), and pass the offline unit tests in `tests/`.
