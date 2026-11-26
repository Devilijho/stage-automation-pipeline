#!/usr/bin/env python3
"""
Browser Application Assistant CLI entrypoint (apply_browser.py)

Delegates to `scripts/browser/` modules while re-exporting public symbols for
backwards compatibility with existing callers and tests.
"""

import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from browser.ats_rules import LOGIN_URLS, detect_ats_provider
from browser.question_rules import classify_yes_no_question, load_master_profile_skills
from browser.runner import async_main, update_tracker_status
from core.browser_session import prepare_chrome_profile_dir
from core.config import load_custom_rules, load_project_env, match_custom_rule
from core.llm import ask_llm_for_field, load_llm_cache, save_llm_cache

load_project_env()

__all__ = [
    "LOGIN_URLS",
    "ask_llm_for_field",
    "async_main",
    "classify_yes_no_question",
    "detect_ats_provider",
    "load_custom_rules",
    "load_llm_cache",
    "load_master_profile_skills",
    "match_custom_rule",
    "prepare_chrome_profile_dir",
    "save_llm_cache",
    "update_tracker_status",
]


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
