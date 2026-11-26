#!/usr/bin/env python3
"""
Local macOS Web UI Server (ui_server.py)

Serves the StageBot dashboard at http://127.0.0.1:8765, exposes JSON APIs for
reading/updating `data/applications.csv`, previewing tailored PDFs in `output/`,
exporting the University Proof Report (`data/rapport_candidatures.html`), and
running pipeline commands (`search`, `apply`, `auto`, `open`, `sync`) with live logs.
"""

import argparse
import html
import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.config import (
    CONFIG_DIR,
    DATA_DIR,
    DEFAULT_PREFERENCES,
    LLM_CACHE_PATH,
    MANUAL_CURRENT_PATH,
    MANUAL_FILL_PATH,
    MANUAL_SKIP_PATH,
    PROFILE_PATH,
    SCRIPTS_DIR,
    UI_DIR,
    UNANSWERED_QUESTIONS_PATH,
    WORKSPACE,
    load_preferences,
    load_profile,
    load_project_env,
    save_profile,
)
from core.llm import call_gemini, delete_llm_cache_entry, load_llm_cache, save_llm_cache
from core.logging_utils import get_logger
from core.sandbox import (
    WorkspacePaths,
    ensure_debug_sandbox,
    get_runtime_paths,
    resolve_offer_metadata_path,
)
from core.storage import (
    bulk_update_application_status,
    normalize_url,
    read_applications,
    update_application_status,
)

load_project_env()
_log = get_logger("ui_server")

REPORT_HTML_PATH = DATA_DIR / "rapport_candidatures.html"
PYTHON_BIN = sys.executable

LOG_LOCK = threading.Lock()
CURRENT_PROC: Optional[subprocess.Popen] = None
LOG_LINES: List[str] = []


def append_log(line: str) -> None:
    with LOG_LOCK:
        LOG_LINES.append(line.rstrip("\n"))
        if len(LOG_LINES) > 400:
            del LOG_LINES[: len(LOG_LINES) - 400]


def run_background_script(cmd: List[str], label: str) -> bool:
    global CURRENT_PROC
    with LOG_LOCK:
        if CURRENT_PROC is not None and CURRENT_PROC.poll() is None:
            return False
        for stale in (MANUAL_CURRENT_PATH, MANUAL_SKIP_PATH, MANUAL_FILL_PATH):
            if stale.exists():
                try:
                    stale.unlink()
                except Exception:
                    pass
        LOG_LINES.clear()
        LOG_LINES.append(f"[{datetime.now().strftime('%H:%M:%S')}] Starting: {label}")

    def worker() -> None:
        global CURRENT_PROC
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(WORKSPACE),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            CURRENT_PROC = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                append_log(line)
            code = proc.wait()
            append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Finished (exit code {code}).")
        except Exception as exc:
            _log.exception("Background script failed (%s): %s", label, exc)
            append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Error: {exc}")
        finally:
            with LOG_LOCK:
                CURRENT_PROC = None

    threading.Thread(target=worker, daemon=True).start()
    return True


def _is_debug_mode(parsed: ParseResult, body: Optional[Dict[str, Any]] = None) -> bool:
    if isinstance(body, dict) and "debug" in body:
        return bool(body.get("debug"))
    qs = parse_qs(parsed.query)
    val = (qs.get("debug") or [""])[0].strip().lower()
    return val in {"1", "true", "yes"}


def _get_paths(parsed: Optional[ParseResult] = None, body: Optional[Dict[str, Any]] = None) -> WorkspacePaths:
    is_dbg = _is_debug_mode(parsed, body) if parsed is not None else bool((body or {}).get("debug"))
    paths = get_runtime_paths(debug=is_dbg)
    if is_dbg:
        ensure_debug_sandbox(paths)
        return paths
    return replace(
        paths,
        profile_path=PROFILE_PATH,
        master_profile_path=CONFIG_DIR / "master_profile.md",
        unanswered_questions_path=UNANSWERED_QUESTIONS_PATH,
        llm_cache_path=LLM_CACHE_PATH,
        report_html_path=REPORT_HTML_PATH,
    )


def generate_university_report(paths: Optional[WorkspacePaths] = None) -> Path:
    p_ctx = paths if paths is not None else _get_paths()
    rows = read_applications(p_ctx.apps_csv_path)
    profile = load_profile(p_ctx.profile_path)
    p = profile["personal"]
    edu = profile["education"]
    now_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
    rel_prefix = "../../" if p_ctx.is_debug else "../"

    row_html = []
    for idx, r in enumerate(rows, 1):
        cv_rel = r.get("CV_PDF") or ""
        lm_rel = r.get("LM_PDF") or ""
        cv_link = f'<a href="{rel_prefix}{html.escape(cv_rel)}" target="_blank">CV PDF</a>' if cv_rel else "-"
        lm_link = f'<a href="{rel_prefix}{html.escape(lm_rel)}" target="_blank">LM PDF</a>' if lm_rel else "-"
        row_html.append(
            f"<tr>"
            f"<td>{idx}</td>"
            f"<td>{html.escape(r.get('Date', ''))}</td>"
            f"<td><strong>{html.escape(r.get('Company', ''))}</strong></td>"
            f"<td>{html.escape(r.get('Role', ''))}</td>"
            f"<td><span class='badge {html.escape(r.get('Status', ''))}'>{html.escape(r.get('Status', ''))}</span></td>"
            f"<td><a href='{html.escape(r.get('URL', ''))}' target='_blank'>Lien offre</a></td>"
            f"<td>{cv_link} · {lm_link}</td>"
            f"</tr>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Justificatif de Candidatures — {html.escape(p['full_name'])}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 32px; color: #111; }}
  h1 {{ margin-bottom: 4px; }}
  .meta {{ color: #444; margin-bottom: 20px; font-size: 14px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; }}
  th {{ background: #f3f4f6; }}
  .badge {{ padding: 2px 8px; border-radius: 12px; font-weight: 600; font-size: 11px; }}
  .applied {{ background: #dcfce7; color: #166534; }}
  .drafted {{ background: #e0f2fe; color: #075985; }}
  .manual_portal {{ background: #fef3c7; color: #92400e; }}
  .needs_login {{ background: #ede9fe; color: #5b21b6; }}
  .expired {{ background: #fee2e2; color: #991b1b; }}
  .skipped {{ background: #f3f4f6; color: #4b5563; }}
  @media print {{ a {{ color: #111; text-decoration: none; }} }}
</style>
</head>
<body>
  <h1>Suivi des Candidatures de Stage</h1>
  <div class="meta">
    <strong>Étudiant :</strong> {html.escape(p['full_name'])} · {html.escape(edu['degree'])} ({html.escape(edu['school'])})<br>
    <strong>Période recherchée :</strong> {html.escape(profile['internship']['availability_full'])}<br>
    <strong>Généré le :</strong> {now_str} · <strong>Total :</strong> {len(rows)} offres suivies
  </div>
  <table>
    <thead>
      <tr><th>#</th><th>Date</th><th>Entreprise</th><th>Poste</th><th>Statut</th><th>Offre</th><th>Documents envoyés</th></tr>
    </thead>
    <tbody>
      {"".join(row_html)}
    </tbody>
  </table>
</body>
</html>"""
    p_ctx.report_html_path.parent.mkdir(parents=True, exist_ok=True)
    p_ctx.report_html_path.write_text(doc, encoding="utf-8")
    return p_ctx.report_html_path


class StageHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path, *, no_cache: bool = False) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "File not found")
            return
        ctype, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        if no_cache or path.suffix.lower() in {".html", ".js", ".css"}:
            self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/" or path == "/index.html":
            self.send_file(UI_DIR / "index.html")
        elif path in {"/styles.css", "/i18n.js", "/app.js", "/qa.js"}:
            self.send_file(UI_DIR / path.lstrip("/"))
        elif path == "/api/applications":
            paths = _get_paths(parsed)
            rows = read_applications(paths.apps_csv_path)
            import batch_apply

            prefs = load_preferences(paths.profile_path)
            counts = {"total": len(rows), "applied": 0, "drafted": 0, "manual_portal": 0, "needs_login": 0, "expired": 0, "skipped": 0}
            enriched: List[Dict[str, Any]] = []
            for r in rows:
                st = r.get("Status", "")
                if st in counts:
                    counts[st] += 1
                tier, sc = batch_apply.classify_offer_quality(
                    r.get("Role", ""),
                    matched_keywords=r.get("Matched_Keywords", ""),
                    url=r.get("URL", ""),
                    prefs=prefs,
                    profile_path=paths.profile_path,
                )
                item = dict(r)
                item["Quality"] = tier
                item["Score"] = sc
                enriched.append(item)
            self.send_json({"rows": list(reversed(enriched)), "counts": counts, "mode": paths.mode})
        elif path == "/api/profile":
            paths = _get_paths(parsed)
            profile = load_profile(paths.profile_path)
            if "preferences" not in profile:
                profile["preferences"] = DEFAULT_PREFERENCES
            master_md = (
                paths.master_profile_path.read_text(encoding="utf-8")
                if paths.master_profile_path.exists()
                else ""
            )
            self.send_json({"profile": profile, "master_md": master_md, "mode": paths.mode})
        elif path == "/api/questions":
            paths = _get_paths(parsed)
            profile = load_profile(paths.profile_path)
            unanswered: List[Dict[str, Any]] = []
            if paths.unanswered_questions_path.exists():
                try:
                    loaded = json.loads(paths.unanswered_questions_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        unanswered = loaded
                except Exception:
                    unanswered = []
            import apply_browser
            verified_skills = sorted(apply_browser.load_master_profile_skills(paths.master_profile_path))
            intern = profile.get("internship", {})
            start_d = intern.get("start_date", "03/05")
            end_d = intern.get("end_date", "16/07")
            dur = intern.get("duration", "3 mois")
            builtin_rules = [
                {"category": "Non / No", "rule": "Visa, sponsorship, parrainage, handicap / disability"},
                {"category": "Non / No", "rule": "Bac+5, Master 2, PFE / fin d'études (unless Bac+2/3 accepted)"},
                {"category": "Non / No", "rule": "Mobile dev (Android, iOS, Flutter, React Native, Kotlin, Swift)"},
                {"category": "Oui / Yes", "rule": "Work authorization, France, stage, convention, disponibilité, Bac+2/3, BUT 2, remote/hybride, FR/EN"},
                {"category": "Oui / Yes", "rule": "Full-stack web (frontend, backend, base de données), AI coding tools, recruitment events"},
                {"category": "Oui / Yes", "rule": "Any verified skill from master_profile.md"},
                {"category": "Checkboxes", "rule": "Auto-check legal/RGPD/terms/required; leave non-required marketing unchecked"},
                {"category": "Inputs & Dates", "rule": f"Experience years -> 1 | Duration -> {dur} | Start -> {start_d} | End -> {end_d}"},
            ]
            self.send_json({
                "unanswered": unanswered,
                "form_answers": profile.get("form_answers", {}),
                "custom_rules": profile.get("custom_rules", []),
                "verified_skills": verified_skills,
                "builtin_rules": builtin_rules,
                "mode": paths.mode,
            })
        elif path == "/api/health":
            with LOG_LOCK:
                running = CURRENT_PROC is not None and CURRENT_PROC.poll() is None
            self.send_json({"ok": True, "running": running})
        elif path == "/api/llm_cache":
            paths = _get_paths(parsed)
            cache = load_llm_cache(paths.llm_cache_path)
            entries: List[Dict[str, Any]] = []
            for k, ans in cache.items():
                parts = k.split("|", 2)
                ft = parts[0] if len(parts) > 0 else ""
                q_txt = parts[1] if len(parts) > 1 else k
                opts = [o for o in (parts[2].split("||") if len(parts) > 2 and parts[2] else []) if o]
                entries.append({
                    "key": k,
                    "field_type": ft,
                    "question": q_txt,
                    "options": opts,
                    "answer": str(ans),
                })
            self.send_json({"entries": entries, "mode": paths.mode})
        elif path == "/api/logs":
            with LOG_LOCK:
                running = CURRENT_PROC is not None and CURRENT_PROC.poll() is None
                self.send_json({"running": running, "lines": list(LOG_LINES)})
        elif path == "/api/manual_current":
            mc_path = DATA_DIR / "manual_current.json"
            with LOG_LOCK:
                running = CURRENT_PROC is not None and CURRENT_PROC.poll() is None
            if running and mc_path.exists():
                try:
                    self.send_json(json.loads(mc_path.read_text(encoding="utf-8")))
                    return
                except Exception:
                    pass
            self.send_json({"active": False})
        elif path == "/api/report":
            paths = _get_paths(parsed)
            rep = generate_university_report(paths)
            self.send_file(rep)
        elif path.startswith("/output/") or path.startswith("/data/") or path.startswith("/debug/"):
            rel = path.lstrip("/")
            target = (WORKSPACE / rel).resolve()
            if WORKSPACE in target.parents:
                self.send_file(target)
            else:
                self.send_error(403)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"ok": False, "error": "Invalid Content-Length"}, status=400)
            return
        try:
            raw_bytes = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw_bytes.decode("utf-8")) if raw_bytes else {}
            if not isinstance(body, dict):
                self.send_json({"ok": False, "error": "JSON body must be an object"}, status=400)
                return
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.send_json({"ok": False, "error": f"Invalid JSON: {exc}"}, status=400)
            return
        parsed = urlparse(self.path)

        if parsed.path == "/api/llm_cache":
            paths = _get_paths(parsed, body)
            action = str(body.get("action") or "").strip().lower()
            key = str(body.get("key") or "")
            if not key:
                self.send_json({"ok": False, "error": "Missing key"}, status=400)
                return
            if action == "delete":
                ok = delete_llm_cache_entry(key, paths.llm_cache_path)
                self.send_json({"ok": ok})
                return
            if action == "update":
                ans = str(body.get("answer") or "").strip()
                if not ans:
                    self.send_json({"ok": False, "error": "Answer cannot be empty"}, status=400)
                    return
                save_llm_cache({key: ans}, paths.llm_cache_path)
                self.send_json({"ok": True})
                return
            self.send_json({"ok": False, "error": "Unsupported action"}, status=400)
            return
        if parsed.path == "/api/applications/status":
            paths = _get_paths(parsed, body)
            notes_arg = body.get("notes") if "notes" in body else None
            ok = update_application_status(
                body.get("url", ""),
                body.get("status", ""),
                notes=notes_arg,
                path=paths.apps_csv_path,
            )
            self.send_json({"ok": ok})
        elif parsed.path == "/api/applications/bulk_status":
            paths = _get_paths(parsed, body)
            urls = body.get("urls") or []
            new_st = body.get("status", "")
            notes_arg = body.get("notes") if "notes" in body else None
            updated = (
                bulk_update_application_status(
                    [str(u) for u in urls],
                    new_st,
                    notes=notes_arg,
                    path=paths.apps_csv_path,
                )
                if isinstance(urls, list) and new_st
                else 0
            )
            self.send_json({"ok": True, "updated": updated})
        elif parsed.path == "/api/applications/remake":
            paths = _get_paths(parsed, body)
            if paths.is_debug:
                self.send_json(
                    {"ok": False, "error": "Remake CV/LM is disabled in Debug Mode (uses empty placeholder PDFs)."}
                )
                return
            urls = body.get("urls") or []
            if not isinstance(urls, list) or not urls:
                self.send_json({"ok": False, "error": "Select at least one offer"}, status=400)
                return
            wanted = {normalize_url(str(u)) for u in urls}
            eligible = [
                r["URL"] for r in read_applications(paths.apps_csv_path)
                if normalize_url(r.get("URL") or "") in wanted
                and (r.get("Status") or "").strip().lower() != "applied"
            ]
            if not eligible:
                self.send_json({"ok": False, "error": "No eligible offers (already-applied offers are kept as submitted)"})
                return
            cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "batch_apply.py"), "--remake", *eligible]
            started = run_background_script(cmd, f"Remake {len(eligible)} CV/LM")
            self.send_json({"ok": started, "count": len(eligible), "skipped": len(wanted) - len(eligible)})
        elif parsed.path == "/api/questions":
            paths = _get_paths(parsed, body)
            q_type = body.get("type", "")
            if q_type == "unanswered":
                items = body.get("items", [])
                if isinstance(items, list):
                    paths.unanswered_questions_path.parent.mkdir(parents=True, exist_ok=True)
                    paths.unanswered_questions_path.write_text(
                        json.dumps(items, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    self.send_json({"ok": True})
                else:
                    self.send_json({"ok": False}, status=400)
            elif q_type == "form_answers":
                fa = body.get("form_answers", {})
                if isinstance(fa, dict):
                    profile = load_profile(paths.profile_path)
                    profile["form_answers"] = fa
                    save_profile(profile, paths.profile_path)
                    self.send_json({"ok": True})
                else:
                    self.send_json({"ok": False}, status=400)
            elif q_type == "custom_rules":
                rules = body.get("custom_rules", [])
                if isinstance(rules, list):
                    profile = load_profile(paths.profile_path)
                    profile["custom_rules"] = [
                        {"keyword": str(r.get("keyword", "")).strip(), "answer": str(r.get("answer", "")).strip()}
                        for r in rules
                        if str(r.get("keyword", "")).strip() and str(r.get("answer", "")).strip()
                    ]
                    save_profile(profile, paths.profile_path)
                    self.send_json({"ok": True, "custom_rules": profile["custom_rules"]})
                else:
                    self.send_json({"ok": False}, status=400)
            else:
                self.send_json({"ok": False}, status=400)
        elif parsed.path == "/api/manual_action":
            act = body.get("action", "")
            mc_path = MANUAL_CURRENT_PATH
            skip_path = MANUAL_SKIP_PATH
            if act == "finder" and mc_path.exists():
                try:
                    cur = json.loads(mc_path.read_text(encoding="utf-8"))
                    cv_rel = cur.get("cv_pdf", "")
                    if cv_rel:
                        cv_abs = WORKSPACE / cv_rel
                        if cv_abs.exists():
                            subprocess.run(["open", "-R", str(cv_abs)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                            subprocess.Popen(["open", "-a", "Finder"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            self.send_json({"ok": True})
                            return
                        folder = cv_abs.parent
                        if folder.exists():
                            subprocess.run(["open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                            subprocess.Popen(["open", "-a", "Finder"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            self.send_json({"ok": True})
                            return
                except Exception:
                    pass
                self.send_json({"ok": False})
            elif act == "fill":
                MANUAL_FILL_PATH.parent.mkdir(parents=True, exist_ok=True)
                MANUAL_FILL_PATH.write_text("fill", encoding="utf-8")
                self.send_json({"ok": True})
            elif act == "next":
                skip_path.parent.mkdir(parents=True, exist_ok=True)
                skip_path.write_text("next", encoding="utf-8")
                self.send_json({"ok": True})
            elif act in {"skip", "applied"}:
                skip_path.parent.mkdir(parents=True, exist_ok=True)
                skip_path.write_text("skipped" if act == "skip" else "applied", encoding="utf-8")
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False}, status=400)
        elif parsed.path == "/api/preferences":
            paths = _get_paths(parsed, body)
            profile = load_profile(paths.profile_path)
            prefs = body.get("preferences")
            if isinstance(prefs, dict):
                profile["preferences"] = prefs
            queries = body.get("queries")
            if isinstance(queries, list) and queries:
                profile.setdefault("search", {})["queries"] = [str(q).strip() for q in queries if str(q).strip()]
            save_profile(profile, paths.profile_path)
            self.send_json({"ok": True})
        elif parsed.path == "/api/import_resume":
            paths = _get_paths(parsed, body)
            import base64
            import io
            import re
            from pypdf import PdfReader

            b64 = body.get("pdf_base64", "")
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            try:
                pdf_bytes = base64.b64decode(b64)
                reader = PdfReader(io.BytesIO(pdf_bytes))
                cv_text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
            except Exception as e:
                self.send_json({"ok": False, "error": f"PDF parse error: {e}"}, status=400)
                return
            if not cv_text:
                self.send_json({"ok": False, "error": "No readable text found in PDF"}, status=400)
                return

            extracted: Dict[str, Any] = {}
            used_gemini = False
            if os.environ.get("GEMINI_API_KEY", "").strip():
                prompt = (
                    "Extract candidate profile fields from this resume text into strict JSON with keys: "
                    "first_name, last_name, email, phone, city, github_url, linkedin_url, school, degree, level, "
                    "target_roles (list of 5-10 lowercase tech role keywords), search_queries (list of 4 French/English internship search queries). "
                    "Only include values explicitly supported by the resume; leave unknown strings empty. Return ONLY valid JSON.\n\n"
                    + cv_text[:6000]
                )
                raw_txt, _ = call_gemini(prompt, timeout=15, json_mode=True)
                if raw_txt:
                    try:
                        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_txt)
                        extracted = json.loads(cleaned)
                        used_gemini = True
                    except Exception:
                        extracted = {}

            # Regex fallback for core contact/link fields
            if not extracted.get("email"):
                m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", cv_text)
                if m:
                    extracted["email"] = m.group(0)
            if not extracted.get("phone"):
                m = re.search(r"(?:\+33|0)\s*[1-9](?:[\s.-]*\d{2}){4}", cv_text)
                if m:
                    extracted["phone"] = m.group(0).strip()
            if not extracted.get("github_url"):
                m = re.search(r"https?://(?:www\.)?github\.com/[\w-]+", cv_text, re.I)
                if m:
                    extracted["github_url"] = m.group(0)
            if not extracted.get("linkedin_url"):
                m = re.search(r"https?://(?:www\.)?linkedin\.com/in/[\w-]+", cv_text, re.I)
                if m:
                    extracted["linkedin_url"] = m.group(0)

            profile = load_profile(paths.profile_path)
            updated_fields: List[str] = []

            def _set(d: Dict[str, Any], key: str, val: Any, label: str) -> None:
                if isinstance(val, str) and val.strip():
                    d[key] = val.strip()
                    updated_fields.append(label)

            p_sec = profile.setdefault("personal", {})
            fa_sec = profile.setdefault("form_answers", {})
            l_sec = profile.setdefault("links", {})
            e_sec = profile.setdefault("education", {})

            _set(p_sec, "first_name", extracted.get("first_name"), "first_name")
            _set(p_sec, "last_name", extracted.get("last_name"), "last_name")
            if extracted.get("first_name") or extracted.get("last_name"):
                fn = f"{p_sec.get('first_name', '')} {p_sec.get('last_name', '')}".strip()
                p_sec["full_name"] = fn
                fa_sec["nom_complet"] = fn
            _set(fa_sec, "prenom", extracted.get("first_name"), "form_answers.prenom")
            _set(fa_sec, "nom", extracted.get("last_name"), "form_answers.nom")
            _set(p_sec, "email", extracted.get("email"), "email")
            _set(fa_sec, "email", extracted.get("email"), "form_answers.email")
            _set(p_sec, "phone", extracted.get("phone"), "phone")
            _set(fa_sec, "telephone", extracted.get("phone"), "form_answers.telephone")
            _set(p_sec, "city", extracted.get("city"), "city")
            _set(fa_sec, "ville", extracted.get("city"), "form_answers.ville")
            _set(l_sec, "github_url", extracted.get("github_url"), "github_url")
            _set(fa_sec, "github", extracted.get("github_url"), "form_answers.github")
            if extracted.get("github_url"):
                l_sec["github_username"] = extracted["github_url"].rstrip("/").split("/")[-1]
            _set(l_sec, "linkedin_url", extracted.get("linkedin_url"), "linkedin_url")
            _set(fa_sec, "linkedin", extracted.get("linkedin_url"), "form_answers.linkedin")
            _set(e_sec, "school", extracted.get("school"), "school")
            _set(fa_sec, "ecole", extracted.get("school"), "form_answers.ecole")
            _set(e_sec, "degree", extracted.get("degree"), "degree")
            _set(fa_sec, "formation", extracted.get("degree"), "form_answers.formation")
            _set(e_sec, "level", extracted.get("level"), "level")
            _set(fa_sec, "niveau", extracted.get("level"), "form_answers.niveau")

            if isinstance(extracted.get("target_roles"), list) and extracted["target_roles"]:
                profile.setdefault("preferences", {})["target_roles"] = [
                    str(x).strip().lower() for x in extracted["target_roles"] if str(x).strip()
                ]
                updated_fields.append("preferences.target_roles")
            if isinstance(extracted.get("search_queries"), list) and extracted["search_queries"]:
                profile.setdefault("search", {})["queries"] = [
                    str(x).strip() for x in extracted["search_queries"] if str(x).strip()
                ]
                updated_fields.append("search.queries")

            save_profile(profile, paths.profile_path)
            self.send_json({"ok": True, "updated_fields": updated_fields, "used_gemini": used_gemini})
        elif parsed.path == "/api/stop":
            with LOG_LOCK:
                if CURRENT_PROC is not None and CURRENT_PROC.poll() is None:
                    try:
                        os.killpg(os.getpgid(CURRENT_PROC.pid), signal.SIGTERM)
                    except Exception:
                        CURRENT_PROC.terminate()
                    LOG_LINES.append(f"[{datetime.now().strftime('%H:%M:%S')}] Stopped by user.")
                    self.send_json({"stopped": True})
                else:
                    self.send_json({"stopped": False})
        elif parsed.path == "/api/run":
            paths = _get_paths(parsed, body)
            action = body.get("action", "search")
            limit = str(int(body.get("limit", 8)))
            queries = body.get("queries") or []
            sources = body.get("sources") or ["linkedin", "hellowork"]

            if action == "sync":
                if paths.is_debug:
                    append_log("[!] GitHub sync is disabled in Debug Mode (uses fake Droit profile).")
                    self.send_json({"started": False, "error": "GitHub sync is disabled in Debug Mode."})
                    return
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "sync_github.py")]
                started = run_background_script(cmd, "Sync GitHub")
            elif action == "login":
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "apply_browser.py"), "--login"]
                started = run_background_script(cmd, "Login Chrome (All Job Boards)")
            elif action == "mail":
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "spontaneous_mail.py"), "--limit", limit]
                started = run_background_script(cmd, "Spontaneous Local Emails")
            elif action == "retry":
                retry_status = body.get("retry_status", "drafted")
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "batch_apply.py"), "--retry", retry_status, "--limit", limit]
                retry_sources = body.get("sources") or []
                if retry_sources:
                    cmd.extend(["--sources", *retry_sources])
                retry_quality = body.get("quality") or []
                if retry_quality:
                    cmd.extend(["--quality", *retry_quality])
                if body.get("auto"):
                    cmd.append("--auto")
                if paths.is_debug:
                    cmd.append("--debug")
                if body.get("skip_external"):
                    cmd.append("--skip-external")
                label = f"Debug Apply ({retry_status})" if paths.is_debug else f"Retry ({retry_status})"
                started = run_background_script(cmd, label)
            elif action == "open":
                cv_pdf = body.get("cv_pdf", "")
                offer_url = body.get("url", "")
                meta_path = resolve_offer_metadata_path(
                    {
                        "URL": offer_url,
                        "CV_PDF": cv_pdf,
                        "Company": body.get("company", ""),
                        "Role": body.get("role", ""),
                    },
                    paths,
                )
                if meta_path and meta_path.exists():
                    cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "apply_browser.py"), "--meta", str(meta_path)]
                    if offer_url:
                        cmd.extend(["--url", offer_url])
                    if body.get("auto"):
                        cmd.append("--auto")
                    if paths.is_debug:
                        cmd.append("--debug")
                    if body.get("skip_external"):
                        cmd.append("--skip-external")
                    prefix = "Debug Chrome" if paths.is_debug else "Open Chrome"
                    started = run_background_script(cmd, f"{prefix} for {meta_path.parent.name}")
                else:
                    append_log(f"[!] metadata.json not found for cv_pdf={cv_pdf!r}")
                    started = False
            else:
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "batch_apply.py"), "--limit", limit, "--sources", *sources]
                if queries:
                    cmd.extend(["--queries", *queries])
                fetch_quality = body.get("quality") or []
                if fetch_quality:
                    cmd.extend(["--quality", *fetch_quality])
                if action == "search":
                    cmd.append("--scrape-only")
                elif action == "auto":
                    cmd.append("--auto")
                if paths.is_debug:
                    cmd.append("--debug")
                if body.get("easy_apply"):
                    cmd.append("--easy-apply")
                if body.get("skip_external"):
                    cmd.append("--skip-external")
                label = f"Debug Batch ({action})" if paths.is_debug else f"Batch ({action})"
                started = run_background_script(cmd, label)

            self.send_json({"started": started})
        else:
            self.send_error(404)


def open_macos_window(url: str) -> None:
    chrome_app = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome_app.exists():
        subprocess.Popen(
            [str(chrome_app), f"--app={url}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        webbrowser.open(url)


def main() -> int:
    parser = argparse.ArgumentParser(description="StageBot macOS UI Server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), StageHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"[*] StageBot UI running at {url} (Ctrl+C to quit)")
    if not args.no_open:
        threading.Timer(0.6, lambda: open_macos_window(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down StageBot UI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
