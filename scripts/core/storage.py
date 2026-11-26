"""Locked, atomic CSV storage and deduplication for data/applications.csv."""

import csv
import fcntl
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from core.config import APPS_CSV_PATH, WORKSPACE

CSV_HEADERS = [
    "Date",
    "Company",
    "Role",
    "URL",
    "Status",
    "CV_Title_Used",
    "Matched_Keywords",
    "CV_PDF",
    "LM_PDF",
    "Notes",
]

SOURCE_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "LinkedIn": ("linkedin.com",),
    "HelloWork": ("hellowork.com",),
    "WTTJ": ("welcometothejungle.com",),
    "JobTeaser": ("jobteaser.com",),
    "Indeed": ("indeed.com",),
    "APEC": ("apec.fr",),
    "Meteojob": ("meteojob.com",),
}


def detect_source(url: str) -> str:
    """Identify the job board name from an offer URL."""
    u = (url or "").lower()
    for label, domains in SOURCE_DOMAINS.items():
        if any(dom in u for dom in domains):
            return label
    return "Other"


def normalize_url(url: str) -> str:
    """Normalize an offer URL for deduplication and lookup."""
    return (url or "").strip().rstrip("/").lower()


def normalize_company_role_key(company: str, role: str) -> Tuple[str, str]:
    """Normalize (company, role) for cross-site deduplication (LinkedIn vs HelloWork vs WTTJ)."""
    c = re.sub(r"[^\w\s]", " ", (company or "").lower())
    c = re.sub(r"\s+", " ", c).strip()
    r = (role or "").lower()
    r = re.sub(r"\b(h\s*/\s*f|f\s*/\s*h|h\s*/\s*f\s*/\s*x|x\s*/\s*f\s*/\s*m|m\s*/\s*f)\b", " ", r)
    r = re.sub(r"[^\w\s]", " ", r)
    r = re.sub(r"\s+", " ", r).strip()
    return c, r


def _lock_path_for(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


def _atomic_write_csv(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    """Write CSV rows via a same-directory temp file + os.replace so writes never truncate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
        mode="w",
        newline="",
        encoding="utf-8",
    ) as f:
        tmp_name = f.name
        try:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    os.replace(tmp_name, path)


def read_applications(path: Path = APPS_CSV_PATH) -> List[Dict[str, str]]:
    """Read all rows from applications.csv under a shared fcntl lock."""
    if not path.exists():
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_path_for(path).open("a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_SH)
        try:
            with path.open("r", newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def load_existing_offers(path: Path = APPS_CSV_PATH) -> Tuple[Set[str], Set[Tuple[str, str]]]:
    """Return sets of normalized URLs and normalized (company, role) keys already tracked."""
    urls: Set[str] = set()
    keys: Set[Tuple[str, str]] = set()
    for r in read_applications(path):
        u = normalize_url(r.get("URL") or "")
        if u:
            urls.add(u)
        comp = (r.get("Company") or "").strip()
        role = (r.get("Role") or "").strip()
        if comp and role:
            keys.add(normalize_company_role_key(comp, role))
    return urls, keys


def _relativize_pdf(pdf_path: Union[Path, str], workspace: Path) -> str:
    p = Path(pdf_path)
    try:
        return str(p.relative_to(workspace))
    except ValueError:
        return str(p)


def check_and_log_application(
    company: str,
    role: str,
    url: str,
    cv_title: str,
    matched_keywords: List[str],
    cv_pdf: Union[Path, str],
    lm_pdf: Union[Path, str],
    *,
    force: bool = False,
    notes: str = "",
    path: Path = APPS_CSV_PATH,
    workspace: Path = WORKSPACE,
) -> bool:
    """Atomically deduplicate and append a newly tailored offer to applications.csv."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_path_for(path).open("a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            rows: List[Dict[str, str]] = []
            if path.exists():
                with path.open("r", newline="", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))

            for r in rows:
                for col in CSV_HEADERS:
                    r.setdefault(col, "")

            norm_url = normalize_url(url)
            target_pair = normalize_company_role_key(company, role)
            if not force:
                for r in rows:
                    existing_url = normalize_url(r.get("URL") or "")
                    if norm_url and existing_url == norm_url:
                        return False
                    if (
                        target_pair[0]
                        and target_pair[1]
                        and normalize_company_role_key(r.get("Company") or "", r.get("Role") or "") == target_pair
                    ):
                        return False

            rows.append({
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "Company": company,
                "Role": role,
                "URL": url or "manual_input",
                "Status": "drafted",
                "CV_Title_Used": cv_title,
                "Matched_Keywords": "; ".join(matched_keywords),
                "CV_PDF": _relativize_pdf(cv_pdf, workspace),
                "LM_PDF": _relativize_pdf(lm_pdf, workspace),
                "Notes": notes,
            })
            _atomic_write_csv(path, CSV_HEADERS, rows)
            return True
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def refresh_application(
    url: str,
    company: str,
    role: str,
    cv_title: str,
    matched_keywords: List[str],
    cv_pdf: Union[Path, str],
    lm_pdf: Union[Path, str],
    *,
    path: Path = APPS_CSV_PATH,
    workspace: Path = WORKSPACE,
) -> Tuple[bool, str]:
    """Update an existing row in place after a CV remake (keeps Date and Status).

    Returns (found, previous_cv_pdf). Never creates rows; `found=False` means no row matched the URL.
    """
    if not path.exists() or not normalize_url(url):
        return False, ""
    target = normalize_url(url)
    with _lock_path_for(path).open("a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            with path.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            old_cv = ""
            found = False
            for r in rows:
                for col in CSV_HEADERS:
                    r.setdefault(col, "")
                if normalize_url(r.get("URL") or "") != target:
                    continue
                found = True
                old_cv = r.get("CV_PDF") or old_cv
                r.update({
                    "Company": company,
                    "Role": role,
                    "CV_Title_Used": cv_title,
                    "Matched_Keywords": "; ".join(matched_keywords),
                    "CV_PDF": _relativize_pdf(cv_pdf, workspace),
                    "LM_PDF": _relativize_pdf(lm_pdf, workspace),
                })
            if found:
                _atomic_write_csv(path, CSV_HEADERS, rows)
            return found, old_cv
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def update_application_status(
    url: str,
    new_status: str,
    *,
    company: str = "",
    force: bool = True,
    notes: Optional[str] = None,
    path: Path = APPS_CSV_PATH,
) -> bool:
    """Update the Status (and optional Notes) column for matching offer(s) atomically under an exclusive lock."""
    if not path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_path_for(path).open("a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            with path.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if not rows:
                return False
            for r in rows:
                for col in CSV_HEADERS:
                    r.setdefault(col, "")
            headers = list(dict.fromkeys([*list(rows[0].keys()), *CSV_HEADERS]))
            norm_url = normalize_url(url)
            comp_low = (company or "").strip().lower()
            updated = False
            for r in rows:
                row_url = normalize_url(r.get("URL") or "")
                if norm_url:
                    matched = row_url == norm_url
                else:
                    matched = bool(comp_low) and (r.get("Company") or "").strip().lower() == comp_low
                if not matched:
                    continue
                if not force and (r.get("Status") or "").strip().lower() == "applied" and new_status != "applied":
                    continue
                r["Status"] = new_status
                if notes is not None:
                    r["Notes"] = notes.strip()
                updated = True
            if updated:
                _atomic_write_csv(path, headers, rows)
            return updated
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def bulk_update_application_status(
    urls: List[str],
    new_status: str,
    *,
    force: bool = True,
    notes: Optional[str] = None,
    path: Path = APPS_CSV_PATH,
) -> int:
    """Update multiple URLs in a single lock acquisition and atomic CSV rewrite."""
    if not path.exists() or not urls or not new_status:
        return 0
    targets = {normalize_url(str(u)) for u in urls if normalize_url(str(u))}
    if not targets:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_path_for(path).open("a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            with path.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if not rows:
                return 0
            for r in rows:
                for col in CSV_HEADERS:
                    r.setdefault(col, "")
            headers = list(dict.fromkeys([*list(rows[0].keys()), *CSV_HEADERS]))
            updated = 0
            for r in rows:
                row_url = normalize_url(r.get("URL") or "")
                if row_url not in targets:
                    continue
                if not force and (r.get("Status") or "").strip().lower() == "applied" and new_status != "applied":
                    continue
                r["Status"] = new_status
                if notes is not None:
                    r["Notes"] = notes.strip()
                updated += 1
            if updated:
                _atomic_write_csv(path, headers, rows)
            return updated
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)
