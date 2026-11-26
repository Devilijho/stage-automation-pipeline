"""Shared persistent Chrome profile & cookie management for Playwright runners."""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict

from core.config import CHROME_PROFILE_DIR


def prepare_chrome_profile_dir(profile_dir: Path = CHROME_PROFILE_DIR) -> bool:
    """Clean stale Chromium Singleton locks; return False if an active process owns it."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_file = profile_dir / "SingletonLock"
    if lock_file.exists() or lock_file.is_symlink():
        pid = None
        try:
            target = os.readlink(lock_file)
            m = re.search(r"-(\d+)$", target)
            if m:
                pid = int(m.group(1))
        except Exception:
            pass
        if pid is not None:
            try:
                os.kill(pid, 0)
                print(
                    f"[!] .chrome_profile/ is currently in use by PID {pid}. "
                    "Close that Chrome window or stop the running task first."
                )
                return False
            except ProcessLookupError:
                pass
            except PermissionError:
                print(
                    f"[!] .chrome_profile/ is currently in use by PID {pid}. "
                    "Close that Chrome window or stop the running task first."
                )
                return False
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            p = profile_dir / name
            if p.exists() or p.is_symlink():
                try:
                    p.unlink()
                except OSError:
                    pass
    return True


def build_chrome_launch_kwargs(
    profile_dir: Path = CHROME_PROFILE_DIR,
    *,
    headless: bool = True,
    no_viewport: bool = False,
) -> Dict[str, Any]:
    """Build consistent Playwright launch_persistent_context kwargs for Chrome/Chromium."""
    kwargs: Dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "locale": "fr-FR",
        "ignore_default_args": ["--enable-automation"],
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if no_viewport:
        kwargs["no_viewport"] = True
    else:
        kwargs["viewport"] = {"width": 1280, "height": 860}

    if Path("/Applications/Google Chrome.app").exists():
        kwargs["channel"] = "chrome"
    elif headless:
        kwargs["channel"] = "chromium"
        kwargs["user_agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
    return kwargs


async def restore_storage_cookies(context: Any, profile_dir: Path = CHROME_PROFILE_DIR) -> None:
    """Restore saved cookies from storage_state.json into a BrowserContext if present."""
    storage_state_path = profile_dir / "storage_state.json"
    if not storage_state_path.exists():
        return
    try:
        saved = json.loads(storage_state_path.read_text(encoding="utf-8"))
        if saved.get("cookies"):
            await context.add_cookies(saved["cookies"])
    except Exception:
        pass


async def save_storage_cookies(context: Any, profile_dir: Path = CHROME_PROFILE_DIR) -> None:
    """Atomically snapshot cookies from BrowserContext to storage_state.json."""
    storage_state_path = profile_dir / "storage_state.json"
    try:
        cookies = await context.cookies()
        profile_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(dir=profile_dir, prefix=".storage_state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
            os.replace(tmp_name, storage_state_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    except Exception:
        pass
