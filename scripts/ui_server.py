#!/usr/bin/env python3
"""
Local macOS Web UI Server (ui_server.py)

Serves the StageBot dashboard at http://127.0.0.1:8765, exposes JSON APIs for
reading/updating `data/applications.csv`, previewing tailored PDFs in `output/`,
exporting the University Proof Report (`data/rapport_candidatures.html`), and
running pipeline commands (`search`, `apply`, `auto`, `open`, `sync`) with live logs.
"""

import argparse
import csv
import html
import json
import mimetypes
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

WORKSPACE = Path(__file__).resolve().parent.parent
UI_DIR = WORKSPACE / "ui"
DATA_DIR = WORKSPACE / "data"
CONFIG_DIR = WORKSPACE / "config"
OUTPUT_DIR = WORKSPACE / "output"
SCRIPTS_DIR = WORKSPACE / "scripts"
APPS_CSV_PATH = DATA_DIR / "applications.csv"
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
        LOG_LINES.clear()
        LOG_LINES.append(f"[{datetime.now().strftime('%H:%M:%S')}] Starting: {label}")

    def worker() -> None:
        global CURRENT_PROC
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd,
            cwd=str(WORKSPACE),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        CURRENT_PROC = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            append_log(line)
        code = proc.wait()
        append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Finished (exit code {code}).")

    threading.Thread(target=worker, daemon=True).start()
    return True


def read_applications() -> List[Dict[str, str]]:
    if not APPS_CSV_PATH.exists():
        return []
    with APPS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def update_application_status(url: str, new_status: str) -> bool:
    rows = read_applications()
    if not rows:
        return False
    headers = list(rows[0].keys())
    norm_target = url.strip().rstrip("/").lower()
    updated = False
    for r in rows:
        if (r.get("URL") or "").strip().rstrip("/").lower() == norm_target:
            r["Status"] = new_status
            updated = True
    if updated:
        with APPS_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
    return updated


def generate_university_report() -> Path:
    rows = read_applications()
    profile = json.loads((CONFIG_DIR / "profile.json").read_text(encoding="utf-8"))
    p = profile["personal"]
    edu = profile["education"]
    now_str = datetime.now().strftime("%d/%m/%Y à %H:%M")

    row_html = []
    for idx, r in enumerate(rows, 1):
        cv_rel = r.get("CV_PDF") or ""
        lm_rel = r.get("LM_PDF") or ""
        cv_link = f'<a href="../{html.escape(cv_rel)}" target="_blank">CV PDF</a>' if cv_rel else "-"
        lm_link = f'<a href="../{html.escape(lm_rel)}" target="_blank">LM PDF</a>' if lm_rel else "-"
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
    REPORT_HTML_PATH.write_text(doc, encoding="utf-8")
    return REPORT_HTML_PATH


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

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "File not found")
            return
        ctype, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/" or path == "/index.html":
            self.send_file(UI_DIR / "index.html")
        elif path == "/api/applications":
            rows = read_applications()
            counts = {"total": len(rows), "applied": 0, "drafted": 0, "manual_portal": 0, "skipped": 0}
            for r in rows:
                st = r.get("Status", "")
                if st in counts:
                    counts[st] += 1
            self.send_json({"rows": list(reversed(rows)), "counts": counts})
        elif path == "/api/profile":
            profile = json.loads((CONFIG_DIR / "profile.json").read_text(encoding="utf-8"))
            master_md = (CONFIG_DIR / "master_profile.md").read_text(encoding="utf-8") if (CONFIG_DIR / "master_profile.md").exists() else ""
            self.send_json({"profile": profile, "master_md": master_md})
        elif path == "/api/logs":
            with LOG_LOCK:
                running = CURRENT_PROC is not None and CURRENT_PROC.poll() is None
                self.send_json({"running": running, "lines": list(LOG_LINES)})
        elif path == "/api/report":
            rep = generate_university_report()
            self.send_file(rep)
        elif path.startswith("/output/") or path.startswith("/data/"):
            rel = path.lstrip("/")
            target = (WORKSPACE / rel).resolve()
            if WORKSPACE in target.parents:
                self.send_file(target)
            else:
                self.send_error(403)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        parsed = urlparse(self.path)

        if parsed.path == "/api/applications/status":
            ok = update_application_status(body.get("url", ""), body.get("status", ""))
            self.send_json({"ok": ok})
        elif parsed.path == "/api/stop":
            with LOG_LOCK:
                if CURRENT_PROC is not None and CURRENT_PROC.poll() is None:
                    CURRENT_PROC.terminate()
                    LOG_LINES.append(f"[{datetime.now().strftime('%H:%M:%S')}] Stopped by user.")
                    self.send_json({"stopped": True})
                else:
                    self.send_json({"stopped": False})
        elif parsed.path == "/api/run":
            action = body.get("action", "search")
            limit = str(int(body.get("limit", 8)))
            queries = body.get("queries") or []
            sources = body.get("sources") or ["linkedin", "hellowork"]

            if action == "sync":
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "sync_github.py")]
                started = run_background_script(cmd, "Sync GitHub")
            elif action == "login":
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "apply_browser.py"), "--login"]
                started = run_background_script(cmd, "Login Chrome (LinkedIn & HelloWork)")
            elif action == "mail":
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "spontaneous_mail.py"), "--limit", limit]
                started = run_background_script(cmd, "Spontaneous Local Emails")
            elif action == "retry":
                retry_status = body.get("retry_status", "drafted")
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "batch_apply.py"), "--retry", retry_status, "--limit", limit]
                if body.get("auto"):
                    cmd.append("--auto")
                if body.get("skip_external"):
                    cmd.append("--skip-external")
                started = run_background_script(cmd, f"Retry ({retry_status})")
            elif action == "open":
                cv_pdf = body.get("cv_pdf", "")
                offer_url = body.get("url", "")
                meta_path = WORKSPACE / Path(cv_pdf).parent / "metadata.json" if cv_pdf else None
                if meta_path and meta_path.exists():
                    cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "apply_browser.py"), "--meta", str(meta_path)]
                    if offer_url:
                        cmd.extend(["--url", offer_url])
                    if body.get("auto"):
                        cmd.append("--auto")
                    if body.get("skip_external"):
                        cmd.append("--skip-external")
                    started = run_background_script(cmd, f"Open Chrome for {meta_path.parent.name}")
                else:
                    append_log(f"[!] metadata.json not found for cv_pdf={cv_pdf!r}")
                    started = False
            else:
                cmd = [PYTHON_BIN, str(SCRIPTS_DIR / "batch_apply.py"), "--limit", limit, "--sources", *sources]
                if queries:
                    cmd.extend(["--queries", *queries])
                if action == "search":
                    cmd.append("--scrape-only")
                elif action == "auto":
                    cmd.append("--auto")
                if body.get("easy_apply"):
                    cmd.append("--easy-apply")
                if body.get("skip_external"):
                    cmd.append("--skip-external")
                started = run_background_script(cmd, f"Batch ({action})")

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
