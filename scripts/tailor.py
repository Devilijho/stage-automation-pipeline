#!/usr/bin/env python3
"""
ATS Keyword & Tailoring Engine (tailor.py)

Takes a job offer URL or pasted/file job description, extracts role, company,
and ATS keywords strictly grounded in `master_profile.md` and `base_cv.tex`,
generates tailored `CV_<MyName>_<Company>.tex/.pdf` (strictly 1 page) and
`LM_<MyName>_<Company>.tex/.pdf` (Vous / Moi / Nous), and logs the entry
to `applications.csv` while preventing duplicates.
"""

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

WORKSPACE = Path(__file__).resolve().parent.parent
PROFILE_PATH = WORKSPACE / "config" / "profile.json"
MASTER_PROFILE_PATH = WORKSPACE / "config" / "master_profile.md"
BASE_CV_PATH = WORKSPACE / "templates" / "base_cv.tex"
BASE_LM_PATH = WORKSPACE / "templates" / "base_lm.tex"
APPS_CSV_PATH = WORKSPACE / "data" / "applications.csv"
OUTPUT_DIR = WORKSPACE / "output"

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
]

KNOWN_KEYWORDS: Dict[str, List[str]] = {
    "C": [" c ", "langage c", "c/c++"],
    "C++": ["c++", "cpp", "c/c++"],
    "TypeScript": ["typescript", "ts"],
    "JavaScript": ["javascript", "js", "ecmascript"],
    "Python": ["python"],
    "Java": ["java"],
    "SQL": ["sql", "postgresql", "postgres", "mysql", "sqlite", "oracle"],
    "HTML5/CSS3": ["html", "css", "html5", "css3", "tailwind"],
    "React": ["react", "reactjs", "next.js", "nextjs"],
    "Node.js": ["node.js", "nodejs", "express", "nestjs"],
    "Three.js": ["three.js", "threejs", "webgl"],
    "Docker": ["docker", "conteneur", "container", "dockerfile", "docker-compose"],
    "Git": ["git", "github", "gitlab", "ci/cd"],
    "Linux": ["linux", "unix", "bash", "shell", "debian", "ubuntu"],
    "Cybersécurité": ["cybersécurité", "cybersecurity", "sécurité", "security", "pentest", "ctf", "owasp", "audit"],
    "Réseaux": ["réseau", "réseaux", "network", "tcp", "ip", "http", "https", "dns", "ssh", "vpn", "firewall", "pare-feu"],
    "Cryptographie": ["cryptographie", "cryptography", "chiffrement", "tls", "ssl"],
    "Reverse Engineering": ["reverse engineering", "ingénierie inverse", "assembleur", "assembly", "x86", "gdb", "binaire", "binary"],
    "Valgrind": ["valgrind", "mémoire", "memory", "leak", "buffer overflow"],
    "API REST": ["api", "rest", "restful", "graphql", "websocket", "json"],
    "Backend": ["backend", "back-end", "serveur", "server", "microservices"],
    "Frontend": ["frontend", "front-end", "ui", "ux"],
    "Full-Stack": ["full-stack", "fullstack", "full stack"],
    "DevOps": ["devops", "déploiement", "sysadmin", "administration système"],
    "Agile": ["agile", "scrum", "kanban", "peer-learning"],
}


def latex_escape(text: str) -> str:
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def sanitize_slug(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name.strip())
    return cleaned.strip("_") or "Target"


def load_profile() -> Dict[str, Any]:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def fetch_job_from_url(url: str) -> Tuple[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return title, text


def infer_company_and_role(
    job_text: str, page_title: str, url: str
) -> Tuple[str, str]:
    company = ""
    role = ""

    if page_title:
        parts = re.split(r"\s+[\|\-–—]\s+", page_title)
        if parts:
            role = parts[0].strip()
        if len(parts) >= 2 and parts[1].lower() not in {"linkedin", "indeed", "welcome to the jungle", "hellowork"}:
            company = parts[1].strip()

    for line in job_text.splitlines()[:40]:
        s = line.strip()
        if not company:
            m = re.match(r"^(?:entreprise|company|société)\s*[:\-]\s*(.+)$", s, re.I)
            if m:
                company = m.group(1).strip()
        if not role:
            m = re.match(r"^(?:poste|role|titre|intitulé)\s*[:\-]\s*(.+)$", s, re.I)
            if m:
                role = m.group(1).strip()

    if not company and url:
        host = urlparse(url).netloc.replace("www.", "")
        company = host.split(".")[0].capitalize()

    if not company:
        company = "Entreprise_Cible"
    if not role:
        role = "Stagiaire Développeur Informatique"

    return company, role


def parse_verified_master_profile() -> Tuple[set, List[Dict[str, Any]]]:
    verified_text = ""
    if MASTER_PROFILE_PATH.exists():
        verified_text += MASTER_PROFILE_PATH.read_text(encoding="utf-8") + "\n"
    if BASE_CV_PATH.exists():
        verified_text += BASE_CV_PATH.read_text(encoding="utf-8") + "\n"
    verified_lower = verified_text.lower()

    verified_keywords = set()
    for canonical, aliases in KNOWN_KEYWORDS.items():
        if canonical.lower() in verified_lower or any(a.strip() in verified_lower for a in aliases):
            verified_keywords.add(canonical)

    projects: List[Dict[str, Any]] = [
        {
            "name": "Webserv -- École 42",
            "subtitle": r"Serveur HTTP \& Sécurité Réseau en C++",
            "bullets": [
                r"Développement d'un serveur HTTP/1.1 conforme RFC avec multiplexation d'I/O non-bloquantes (\texttt{select}/\texttt{poll}/\texttt{kqueue}).",
                r"Sécurisation du parsing des requêtes contre les attaques par déni de service (DoS), corruption de flux et injection de payloads.",
                r"Gestion rigoureuse des descripteurs de fichiers, des sessions TCP et des codes d'état HTTP.",
            ],
            "search_blob": "webserv c++ http tcp réseau sécurité backend serveur linux unix select poll kqueue",
        },
        {
            "name": "Cub3d -- École 42",
            "subtitle": r"Moteur 3D \& Gestion Mémoire Bas Niveau en C",
            "bullets": [
                r"Implémentation d'un moteur de rendu graphique par raycasting (algorithmique bas niveau et arithmétique de pointeurs).",
                r"Audit de sécurité mémoire : prévention proactive des débordements de tampon (buffer overflows) et validation stricte avec Valgrind.",
                r"Parsing défensif des fichiers de configuration de scènes (validation stricte des entrées utilisateurs).",
            ],
            "search_blob": "cub3d c valgrind gdb mémoire sécurité buffer overflow parsing linux",
        },
        {
            "name": "Game Jam IUT Paris-Saclay",
            "subtitle": "Hackathon de Programmation (36h)",
            "bullets": [
                r"Conception rapide et sécurisée d'un exécutable complet sous contrainte de temps stricte.",
                r"Structuration de la logique applicative, gestion des états et tests fonctionnels en équipe.",
            ],
            "search_blob": "game jam hackathon agile équipe c# java python c++ logique applicative tests",
        },
    ]

    if MASTER_PROFILE_PATH.exists():
        md = MASTER_PROFILE_PATH.read_text(encoding="utf-8")
        repo_blocks = re.split(r"\n###\s+", md)
        for block in repo_blocks[1:]:
            lines = block.splitlines()
            repo_header = lines[0].replace("[PRIORITY]", "").strip()
            repo_short = repo_header.split("/")[-1]
            if repo_short.lower() in {"webserv", "cub3d"}:
                continue

            desc = ""
            langs = ""
            commits = 0
            contribs: List[str] = []
            files: List[str] = []
            for l in lines[1:]:
                if l.startswith("- **Description**:"):
                    desc = l.split(":", 1)[1].strip()
                elif l.startswith("- **Languages**:"):
                    langs = l.split(":", 1)[1].strip()
                elif l.startswith("- **Verified Personal Commits**:"):
                    try:
                        commits = int(l.split(":", 1)[1].strip())
                    except ValueError:
                        commits = 0
                elif l.startswith("- **Files Personally Touched**:"):
                    files = re.findall(r"`([^`]+)`", l)
                elif l.startswith("  - "):
                    contribs.append(l[4:].strip())

            if commits == 0 and "priority" not in lines[0].lower():
                continue

            bullets: List[str] = []
            if desc and desc != "No description provided":
                bullets.append(latex_escape(desc))
            if langs and langs != "Unspecified":
                bullets.append(f"Stack technique vérifiée : {latex_escape(langs)}.")
            if contribs:
                summary_contrib = "; ".join(contribs[:3])
                bullets.append(f"Contributions personnelles ({commits} commits) : {latex_escape(summary_contrib)}.")
            elif files:
                bullets.append(f"Modules développés : \\texttt{{{latex_escape(', '.join(files[:4]))}}}.")

            if bullets:
                projects.append({
                    "name": latex_escape(repo_short),
                    "subtitle": latex_escape(f"Projet GitHub ({langs})" if langs != "Unspecified" else "Projet GitHub"),
                    "bullets": bullets[:3],
                    "search_blob": f"{repo_short} {desc} {langs} {' '.join(contribs)} {' '.join(files)}".lower(),
                })

    return verified_keywords, projects


def extract_matched_keywords(job_text: str, verified_keywords: set) -> List[str]:
    text_lower = " " + job_text.lower() + " "
    matched: List[str] = []
    for kw in verified_keywords:
        aliases = KNOWN_KEYWORDS.get(kw, [kw.lower()])
        if kw.lower() in text_lower or any(a in text_lower for a in aliases):
            matched.append(kw)
    return matched


def build_cv_title(role: str, matched_keywords: List[str]) -> str:
    role_clean = re.sub(r"\s*\([^)]*\)", "", role).strip()
    if not re.search(r"stage|stagiaire", role_clean, re.I):
        role_clean = f"Stage -- Stagiaire {role_clean}"
    else:
        role_clean = re.sub(r"\s+[-–—]\s+", " -- ", role_clean)
    return role_clean[:78]


def build_cv_accroche(
    profile: Dict[str, Any], role: str, matched_keywords: List[str]
) -> str:
    kw_list = matched_keywords[:5] if matched_keywords else ["C/C++", "Linux", "Git", "SQL"]
    kw_str = latex_escape(", ".join(kw_list))
    edu = profile.get("education", {})
    intern = profile.get("internship", {})
    school = edu.get("school", "IUT d'Orsay")
    degree = edu.get("degree", "BUT Informatique")
    secondary = edu.get("secondary_school", "École 42 Paris")
    avail = intern.get("availability_short", "Du 03/05 au 16/07 (3 mois, prolongeable durant l'été)")
    return (
        f"Étudiant en {latex_escape(degree)} ({latex_escape(school)}) "
        f"avec {latex_escape(secondary)}, je recherche un stage ({latex_escape(avail)}) "
        f"en tant que {latex_escape(role)}. Opérationnel sur {kw_str}, rigoureux et autonome, "
        f"je souhaite m'investir pleinement au sein de vos équipes techniques."
    )


def build_skills_block(matched_keywords: List[str]) -> str:
    matched_set = set(matched_keywords)
    categories = [
        (
            "Langages",
            ["C", "C++", "Assembly (x86)", "TypeScript", "JavaScript", "Java", "SQL (Oracle / SQLite)", "HTML5/CSS3", "Notions de C\\#"],
        ),
        (
            "Systèmes \\& DevOps",
            ["Linux (UNIX)", "Docker", "Git", "Windows", "MacOS", "VirtualBox"],
        ),
        (
            "Sécurité \\& Débogage",
            ["Cybersécurité", "Réseaux", "Cryptographie", "GDB", "Valgrind (Memcheck, Helgrind)", "Analyse mémoire", "Reverse Engineering"],
        ),
        (
            "Frameworks \\& Outils",
            ["Node.js", "React", "Three.js", "API REST", "Eclipse", "Visual Paradigm", "Modélisation (MCD/SR)"],
        ),
    ]

    def score_item(item: str) -> int:
        return 1 if any(m.lower() in item.lower() for m in matched_set) else 0

    scored_cats = []
    for cat_name, items in categories:
        reordered = sorted(items, key=lambda x: -score_item(x))
        cat_score = sum(score_item(x) for x in items)
        scored_cats.append((cat_score, cat_name, reordered))

    scored_cats.sort(key=lambda t: -t[0])
    lines = ["\\section*{Compétences Techniques}", "{\\small"]
    for idx, (_, cat_name, items) in enumerate(scored_cats):
        suffix = "\\\\" if idx < len(scored_cats) - 1 else ""
        lines.append(f"\\textbf{{{cat_name} :}} {', '.join(items)}{suffix}")
    lines.append("}")
    return "\n".join(lines)


def select_and_format_projects(
    projects: List[Dict[str, Any]],
    matched_keywords: List[str],
    job_text: str,
    max_bullets: int = 3,
) -> Tuple[List[Dict[str, Any]], str]:
    job_lower = job_text.lower()
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for proj in projects:
        blob = proj["search_blob"].lower()
        score = sum(3 for kw in matched_keywords if kw.lower() in blob)
        for token in set(re.findall(r"[a-z0-9\+\#]{3,}", blob)):
            if token in job_lower:
                score += 1
        scored.append((score, proj))

    scored.sort(key=lambda t: -t[0])
    selected = [p for _, p in scored[:3]]

    lines = ["\\section*{Projets Informatiques}", "{\\small"]
    for idx, proj in enumerate(selected):
        if idx > 0:
            lines.append("\\vspace{0.22em}")
        lines.append(f"\\textbf{{{proj['name']}}} --- \\textit{{{proj['subtitle']}}}")
        lines.append("\\begin{itemize}")
        for b in proj["bullets"][:max_bullets]:
            lines.append(f"  \\item {b}")
        lines.append("\\end{itemize}")
    lines.append("}")
    return selected, "\n".join(lines)


def replace_marker_block(tex: str, marker: str, new_body: str) -> str:
    start_tag = f"% === {marker}_START ==="
    end_tag = f"% === {marker}_END ==="
    pattern = re.compile(
        re.escape(start_tag) + r".*?" + re.escape(end_tag),
        re.DOTALL,
    )
    replacement = f"{start_tag}\n{new_body}\n{end_tag}"
    if not pattern.search(tex):
        raise ValueError(f"Marker block {marker} not found in template.")
    return pattern.sub(lambda _: replacement, tex, count=1)


def compile_tex(tex_path: Path) -> Tuple[Path, str]:
    out_dir = tex_path.parent
    pdf_path = tex_path.with_suffix(".pdf")
    pdflatex_bin = shutil.which("pdflatex")
    tectonic_bin = shutil.which("tectonic")

    if pdflatex_bin:
        res = subprocess.run(
            [pdflatex_bin, "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=str(out_dir),
            capture_output=True,
            text=True,
        )
        log_path = tex_path.with_suffix(".log")
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else res.stdout
        if res.returncode != 0:
            raise RuntimeError(f"pdflatex failed for {tex_path.name}:\n{res.stdout[-1500:]}")
        return pdf_path, log_text
    elif tectonic_bin:
        res = subprocess.run(
            [tectonic_bin, tex_path.name],
            cwd=str(out_dir),
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(f"tectonic failed for {tex_path.name}:\n{res.stderr}\n{res.stdout}")
        return pdf_path, res.stdout + "\n" + res.stderr
    else:
        raise RuntimeError("Neither pdflatex nor tectonic is installed in PATH.")


def verify_pdf_one_page(pdf_path: Path, log_text: str) -> Tuple[bool, int, List[str]]:
    reader = PdfReader(str(pdf_path))
    pages = len(reader.pages)
    overfull = [
        l.strip()
        for l in log_text.splitlines()
        if "Overfull \\hbox" in l or "Overfull \\vbox" in l
    ]
    return (pages == 1 and len(overfull) == 0), pages, overfull


def build_lm_tex(
    profile: Dict[str, Any],
    company: str,
    role: str,
    selected_projects: List[Dict[str, Any]],
    matched_keywords: List[str],
) -> str:
    base_lm = BASE_LM_PATH.read_text(encoding="utf-8")
    proj_names = ", ".join(p["name"].split("--")[0].strip() for p in selected_projects[:2])
    kw_str = ", ".join(matched_keywords[:5]) if matched_keywords else "C/C++, TypeScript, Linux, Git, Docker"
    edu = profile.get("education", {})
    intern = profile.get("internship", {})
    avail_short = intern.get("availability_short", "du 03/05 au 16/07 (3 mois, prolongeable l'été)")
    avail_full = intern.get("availability_full", "Stage de 3 mois du 3 mai au 16 juillet, avec possibilité de prolongation sur l'été")
    degree = edu.get("degree", "BUT Informatique")
    school = edu.get("school", "IUT d'Orsay")
    secondary = edu.get("secondary_school", "École 42 Paris")

    recipient = (
        f"\\textbf{{À l'attention de l'équipe de recrutement}}\\\\\n"
        f"{latex_escape(company)}\\\\\nFrance"
    )
    obj = (
        f"\\textbf{{Objet :}} Candidature pour un stage ({latex_escape(avail_short)}) "
        f"-- {latex_escape(role)}"
    )
    vous = (
        f"Actuellement étudiant en {latex_escape(degree)} à {latex_escape(school)} "
        f"et fort de {latex_escape(secondary)}, je souhaite rejoindre {latex_escape(company)} dans le cadre d'un "
        f"{latex_escape(avail_full.lower())}. "
        f"Votre offre de {latex_escape(role)} retient particulièrement mon attention par la richesse de ses enjeux techniques."
    )
    moi = (
        f"Ma double formation m'a permis de développer une solide maîtrise de la programmation système et applicative "
        f"({latex_escape(kw_str)}). À travers des projets concrets tels que \\textit{{{latex_escape(proj_names)}}}, "
        f"j'ai conçu, sécurisé et livré des solutions fiables en autonomie comme en travail d'équipe sous Git et Linux."
    )
    nous = (
        f"Rejoindre {latex_escape(company)} serait l'opportunité de mettre ma rigueur, ma capacité d'adaptation "
        f"et mon esprit d'équipe au service de vos projets en production, tout en progressant aux côtés de vos ingénieurs."
    )

    lm = replace_marker_block(base_lm, "LM_RECIPIENT", recipient)
    lm = replace_marker_block(lm, "LM_OBJECT", obj)
    lm = replace_marker_block(lm, "LM_VOUS", vous)
    lm = replace_marker_block(lm, "LM_MOI", moi)
    lm = replace_marker_block(lm, "LM_NOUS", nous)
    return lm


def check_and_log_application(
    company: str,
    role: str,
    url: str,
    cv_title: str,
    matched_keywords: List[str],
    cv_pdf: Path,
    lm_pdf: Path,
    force: bool = False,
) -> bool:
    if not APPS_CSV_PATH.exists():
        with APPS_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_HEADERS)

    with APPS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Upgrade legacy CSV rows without CV_PDF / LM_PDF columns
    upgraded = False
    for r in rows:
        for col in ("CV_PDF", "LM_PDF"):
            if col not in r:
                r[col] = ""
                upgraded = True
    if upgraded:
        with APPS_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()
            writer.writerows(rows)

    norm_url = url.strip().rstrip("/").lower()
    def _norm_pair(c: str, ro: str) -> Tuple[str, str]:
        c_n = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (c or "").lower())).strip()
        r_n = re.sub(r"\b(h\s*/\s*f|f\s*/\s*h|h\s*/\s*f\s*/\s*x|x\s*/\s*f\s*/\s*m|m\s*/\s*f)\b", " ", (ro or "").lower())
        r_n = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", r_n)).strip()
        return c_n, r_n

    target_pair = _norm_pair(company, role)
    for r in rows:
        existing_url = (r.get("URL") or "").strip().rstrip("/").lower()
        if norm_url and existing_url == norm_url and not force:
            return False
        if (
            target_pair[0]
            and target_pair[1]
            and _norm_pair(r.get("Company") or "", r.get("Role") or "") == target_pair
            and not force
        ):
            return False

    try:
        cv_rel = str(cv_pdf.relative_to(WORKSPACE))
    except ValueError:
        cv_rel = str(cv_pdf)
    try:
        lm_rel = str(lm_pdf.relative_to(WORKSPACE))
    except ValueError:
        lm_rel = str(lm_pdf)

    with APPS_CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            company,
            role,
            url or "manual_input",
            "drafted",
            cv_title,
            "; ".join(matched_keywords),
            cv_rel,
            lm_rel,
        ])
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="ATS CV & LM Tailoring Engine")
    parser.add_argument("--url", default="", help="Job offer URL")
    parser.add_argument("--text", default="", help="Pasted job description text")
    parser.add_argument("--file", default="", help="Path to file containing job description")
    parser.add_argument("--company", default="", help="Override company name")
    parser.add_argument("--role", default="", help="Override role title")
    parser.add_argument("--force", action="store_true", help="Allow overwriting duplicate entry")
    args = parser.parse_args()

    profile = load_profile()
    page_title = ""
    job_text = args.text

    if args.file:
        job_text = Path(args.file).read_text(encoding="utf-8")
    elif args.url and not job_text:
        print(f"[*] Fetching job offer from {args.url} ...")
        try:
            page_title, job_text = fetch_job_from_url(args.url)
        except Exception as e:
            print(f"[!] Could not fetch URL automatically ({e}). Pass --text or --file.")
            return 1

    if not job_text.strip():
        print("[!] No job description provided. Use --url, --text, or --file.")
        return 1

    inferred_company, inferred_role = infer_company_and_role(job_text, page_title, args.url)
    company = args.company.strip() or inferred_company
    role = args.role.strip() or inferred_role

    verified_keywords, projects = parse_verified_master_profile()
    matched_keywords = extract_matched_keywords(job_text, verified_keywords)
    cv_title = build_cv_title(role, matched_keywords)

    company_slug = sanitize_slug(company)
    my_name_slug = sanitize_slug(profile["personal"]["full_name"])
    target_dir = OUTPUT_DIR / company_slug
    # Avoid overwriting a different offer at the same company
    meta_file = target_dir / "metadata.json"
    if meta_file.exists() and not args.force:
        try:
            existing_meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if (existing_meta.get("url") or "").strip().rstrip("/").lower() != args.url.strip().rstrip("/").lower():
                role_slug = sanitize_slug(role)[:24]
                target_dir = OUTPUT_DIR / f"{company_slug}_{role_slug}"
        except Exception:
            pass
    target_dir.mkdir(parents=True, exist_ok=True)

    cv_tex_path = target_dir / f"CV_{my_name_slug}_{company_slug}.tex"
    lm_tex_path = target_dir / f"LM_{my_name_slug}_{company_slug}.tex"

    base_cv = BASE_CV_PATH.read_text(encoding="utf-8")
    accroche = build_cv_accroche(profile, role, matched_keywords)
    skills_block = build_skills_block(matched_keywords)

    selected_projects: List[Dict[str, Any]] = []
    cv_pdf_path: Optional[Path] = None
    cv_ok = False
    pages = 1
    for max_bullets in (3, 2, 1):
        selected_projects, projects_block = select_and_format_projects(
            projects, matched_keywords, job_text, max_bullets=max_bullets
        )
        cv_tex = replace_marker_block(
            base_cv, "CV_TITLE", f"{{\\large\\bfseries {latex_escape(cv_title)}}}\\\\[0.2em]"
        )
        avail_short = profile.get("internship", {}).get(
            "availability_short", "Du 03/05 au 16/07 (3 mois, prolongeable durant l'été)"
        )
        if "% === CV_AVAILABILITY_START ===" in cv_tex:
            cv_tex = replace_marker_block(
                cv_tex,
                "CV_AVAILABILITY",
                f"{{\\small\\textbf{{Disponibilité :}} {latex_escape(avail_short)}}}\\\\[0.15em]",
            )
        cv_tex = replace_marker_block(cv_tex, "CV_ACCROCHE", f"{{\\small\\itshape\n{accroche}\n}}")
        cv_tex = replace_marker_block(cv_tex, "SKILLS_SECTION", skills_block)
        cv_tex = replace_marker_block(cv_tex, "PROJECTS_SECTION", projects_block)
        cv_tex_path.write_text(cv_tex, encoding="utf-8")

        cv_pdf_path, log_text = compile_tex(cv_tex_path)
        cv_ok, pages, overfull = verify_pdf_one_page(cv_pdf_path, log_text)
        if pages == 1:
            if overfull:
                print(f"[!] Warning: LaTeX overfull detected ({len(overfull)} lines).")
            break
        print(f"[*] CV was {pages} pages with max_bullets={max_bullets}, tightening...")

    if not cv_ok and cv_pdf_path:
        print(f"[!] Check {cv_pdf_path} (pages={pages}).")

    lm_tex = build_lm_tex(profile, company, role, selected_projects, matched_keywords)
    lm_tex_path.write_text(lm_tex, encoding="utf-8")
    lm_pdf_path, lm_log = compile_tex(lm_tex_path)
    _, lm_pages, _ = verify_pdf_one_page(lm_pdf_path, lm_log)

    logged = check_and_log_application(
        company, role, args.url, cv_title, matched_keywords, cv_pdf_path, lm_pdf_path, force=args.force
    )

    metadata = {
        "company": company,
        "role": role,
        "url": args.url,
        "cv_title": cv_title,
        "matched_keywords": matched_keywords,
        "selected_projects": [p["name"] for p in selected_projects],
        "cv_pdf": str(cv_pdf_path),
        "lm_pdf": str(lm_pdf_path),
        "cv_pages": pages,
        "lm_pages": lm_pages,
    }
    (target_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== TAILORING COMPLETE ===")
    print(f"Company          : {company}")
    print(f"Role             : {role}")
    print(f"CV Title Used    : {cv_title}")
    print(f"Selected Projects: {', '.join(metadata['selected_projects'])}")
    print(f"Matched Keywords : {', '.join(matched_keywords) or 'None'}")
    print(f"CV PDF (1p={pages==1}) : {cv_pdf_path}")
    print(f"LM PDF (1p={lm_pages==1}) : {lm_pdf_path}")
    print(f"Tracker Logged   : {'Yes (drafted)' if logged else 'Skipped (duplicate)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
