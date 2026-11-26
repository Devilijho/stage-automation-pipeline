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

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.config import (
    MASTER_PROFILE_PATH,
    OUTPUT_DIR,
    TEMPLATES_DIR,
    load_profile,
)
from core.storage import check_and_log_application

BASE_CV_PATH = TEMPLATES_DIR / "base_cv.tex"
BASE_LM_PATH = TEMPLATES_DIR / "base_lm.tex"

KNOWN_KEYWORDS: Dict[str, List[str]] = {
    "C": [" c ", "langage c", "c/c++"],
    "C++": ["c++", "cpp", "c/c++"],
    "TypeScript": ["typescript", "ts"],
    "JavaScript": ["javascript", "js", "ecmascript"],
    "Python": ["python"],
    "Java": ["java"],
    "SQL": ["sql", "postgresql", "postgres", "mysql", "sqlite", "oracle", "mariadb", "supabase", "plpgsql"],
    "HTML5/CSS3": ["html", "css", "html5", "css3", "tailwind"],
    "React": ["react", "reactjs", "next.js", "nextjs", "vite"],
    "Node.js": ["node.js", "nodejs", "express", "nestjs"],
    "Three.js": ["three.js", "threejs", "webgl"],
    "Docker": ["docker", "conteneur", "container", "dockerfile", "docker-compose", "nginx"],
    "Git": ["git", "github", "gitlab", "ci/cd"],
    "Linux": ["linux", "unix", "bash", "shell", "debian", "ubuntu"],
    "Cybersécurité": ["cybersécurité", "cybersecurity", "sécurité", "security", "pentest", "ctf", "owasp", "audit", "soc"],
    "Réseaux": ["réseau", "réseaux", "network", "tcp", "ip", "http", "https", "dns", "ssh", "vpn", "firewall", "pare-feu"],
    "Cryptographie": ["cryptographie", "cryptography", "chiffrement", "tls", "ssl"],
    "Reverse Engineering": ["reverse engineering", "ingénierie inverse", "assembleur", "assembly", "x86", "gdb", "binaire", "binary"],
    "Valgrind": ["valgrind", "mémoire", "memory", "leak", "buffer overflow"],
    "API REST": ["api", "rest", "restful", "graphql", "websocket", "json"],
    "Backend": ["backend", "back-end", "serveur", "server", "microservices"],
    "Frontend": ["frontend", "front-end", "ui", "ux"],
    "Full-Stack": ["full-stack", "fullstack", "full stack"],
    "DevOps": ["devops", "déploiement", "sysadmin", "administration système", "infrastructure"],
    "Agile": ["agile", "scrum", "kanban", "peer-learning"],
}

FRENCH_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

KNOWN_CITIES = [
    "Paris", "La Défense", "Saclay", "Orsay", "Bures-sur-Yvette", "Palaiseau",
    "Massy", "Vélizy-Villacoublay", "Vélizy", "Nanterre", "Boulogne-Billancourt",
    "Issy-les-Moulineaux", "Courbevoie", "Neuilly-sur-Seine", "Levallois-Perret",
    "Montrouge", "Antony", "Les Ulis", "Évry", "Saint-Denis", "Versailles",
    "Lyon", "Toulouse", "Lille", "Nantes", "Bordeaux", "Marseille",
]


def format_french_date(dt: Optional[datetime] = None) -> str:
    now = dt or datetime.now()
    return f"{now.day} {FRENCH_MONTHS[now.month - 1]} {now.year}"


def clean_role_for_prose(role: str) -> str:
    """Clean role title for natural insertion in French prose (strip H/F & leading 'Stage - ')."""
    r = re.sub(r"\s*\(\s*(?:h\s*/\s*f|f\s*/\s*h|h\s*/\s*f\s*/\s*x|x\s*/\s*f\s*/\s*m|m\s*/\s*f)\s*\)", "", role, flags=re.I)
    r = re.sub(r"\b(?:h\s*/\s*f|f\s*/\s*h|h\s*/\s*f\s*/\s*x|m\s*/\s*f)\b", "", r, flags=re.I)
    r = re.sub(r"^\s*(?:stage|stagiaire)\s*(?:[-–—:|/]\s*|\bde\s+)", "", r, flags=re.I)
    r = re.sub(r"\s+", " ", r).strip(" -–—:/|")
    return r or "Développeur Informatique"


def infer_offer_city(job_text: str) -> str:
    sample = job_text[:2500]
    for city in KNOWN_CITIES:
        if re.search(rf"\b{re.escape(city)}\b", sample, re.I):
            return f"{city}, France"
    return "Île-de-France, France"


def detect_role_archetype(role: str, job_text: str, matched_keywords: List[str]) -> str:
    """Classify the offer into cyber / infra / web / systems using title + matched keywords."""
    title_l = role.lower()
    kw_set = {k.lower() for k in matched_keywords}
    blob = f"{title_l} {job_text[:1500].lower()}"

    if any(t in title_l for t in ("cyber", "sécurit", "securit", "soc", "pentest", "audit", "crypto", "reverse")):
        return "cyber"
    if any(t in title_l for t in ("réseau", "reseau", "système", "systeme", "devops", "infra", "sysadmin", "cloud", "sre")):
        return "infra"
    if any(t in title_l for t in ("full", "front", "web", "react", "typescript", "javascript", "node", "back-end", "backend", "saas", "mobile")):
        return "web"

    cyber_score = sum(1 for k in ("cybersécurité", "cryptographie", "reverse engineering", "valgrind") if k in kw_set)
    infra_score = sum(1 for k in ("réseaux", "linux", "docker", "devops") if k in kw_set)
    web_score = sum(1 for k in ("typescript", "javascript", "react", "node.js", "html5/css3", "full-stack", "frontend", "api rest", "sql") if k in kw_set)
    sys_score = sum(1 for k in ("c", "c++", "linux", "valgrind") if k in kw_set)

    if cyber_score >= 2 or "cybersécurité" in blob:
        return "cyber"
    if infra_score >= 3 and infra_score >= web_score:
        return "infra"
    if web_score >= 2 and web_score > sys_score:
        return "web"
    return "systems"


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

    # Curated, strictly grounded project catalog (only included when verified in master_profile.md or base_cv.tex)
    curated_catalog: List[Dict[str, Any]] = [
        {
            "verify_key": "webserv",
            "name": "Webserv -- École 42",
            "subtitle": r"Serveur HTTP/1.1 \& Sécurité Réseau en C++ (Équipe)",
            "url": "https://github.com/Devilijho/webserv",
            "bullets": [
                r"Développement d'un serveur HTTP/1.1 conforme RFC avec multiplexation d'I/O non-bloquantes (\texttt{select}/\texttt{poll}/\texttt{kqueue}).",
                r"Sécurisation du parsing des requêtes contre les attaques par déni de service (DoS), corruption de flux et injection de payloads.",
                r"Gestion rigoureuse des descripteurs de fichiers, des sessions TCP et des codes d'état HTTP.",
            ],
            "lm_pitch": "Webserv, un serveur HTTP/1.1 non-bloquant en C++ intégrant multiplexation I/O et sécurisation contre les requêtes malveillantes",
            "search_blob": "webserv c++ http tcp réseau sécurité backend serveur linux unix select poll kqueue api",
        },
        {
            "verify_key": "inception",
            "name": "Inception -- École 42",
            "subtitle": r"Infrastructure Conteneurisée Docker (Solo)",
            "url": "https://github.com/Devilijho/Inception",
            "bullets": [
                r"Déploiement d'une infrastructure multi-services isolée (NGINX TLS, WordPress + PHP-FPM, MariaDB) orchestrée via Docker Compose.",
                r"Rédaction de Dockerfiles sécurisés sous Linux, gestion des réseaux virtuels dédiés et persistance des volumes.",
                r"Configuration du reverse proxy NGINX et durcissement des règles réseau entre conteneurs.",
            ],
            "lm_pitch": "Inception, une infrastructure Docker Compose sécurisée combinant NGINX, PHP-FPM et MariaDB sous Linux",
            "search_blob": "inception docker dockerfile docker-compose nginx mariadb linux sysadmin devops réseau sécurité infrastructure shell",
        },
        {
            "verify_key": "transcendence",
            "name": "Transcendence -- École 42",
            "subtitle": r"Application Web Temps Réel TypeScript \& Docker (Équipe)",
            "url": "https://github.com/LucLgdm/Transcendence",
            "bullets": [
                r"Conception d'une application web multijoueur en temps réel (Pong et Échecs) avec fonctionnalités sociales.",
                r"Développement en TypeScript, JavaScript, HTML5/CSS3 avec environnement conteneurisé via Docker et Makefile.",
                r"Travail collaboratif sous Git avec gestion de branches, revue de code et intégration continue des modules.",
            ],
            "lm_pitch": "Transcendence, une application web multijoueur temps réel en TypeScript et Docker réalisée en équipe",
            "search_blob": "transcendence typescript javascript html css fullstack frontend backend web docker git équipe temps réel",
        },
        {
            "verify_key": "chaski-booking",
            "name": "Chaski Booking",
            "subtitle": r"Plateforme SaaS Multi-Tenant TypeScript \& PostgreSQL (Équipe)",
            "url": "",
            "bullets": [
                r"Développement d'une plateforme de réservation en ligne multi-tenant (catalogue de services, fidélité, cartes cadeaux).",
                r"Modélisation et versioning du schéma PostgreSQL / PLpgSQL (migrations Supabase) et intégrations Stripe / Google Calendar.",
                r"Architecture modulaire en TypeScript et scripts Shell pour le déploiement et la maintenance.",
            ],
            "lm_pitch": "Chaski Booking, une plateforme SaaS multi-tenant en TypeScript et PostgreSQL/Supabase avec paiements Stripe",
            "search_blob": "chaski-booking typescript javascript sql postgresql plpgsql supabase fullstack backend frontend api rest saas",
        },
        {
            "verify_key": "minishell",
            "name": "Minishell -- École 42",
            "subtitle": r"Interpréteur de Commandes UNIX en C (Solo)",
            "url": "https://github.com/Devilijho/Minishell",
            "bullets": [
                r"Implémentation d'un shell POSIX-like en C gérant l'exécution de commandes, variables d'environnement et \texttt{PATH}.",
                r"Gestion bas niveau des appels système UNIX : processus (\texttt{fork}/\texttt{execve}), pipes (\texttt{|}), redirections et signaux.",
                r"Validation mémoire stricte sous Valgrind et gestion robuste des cas limites de parsing.",
            ],
            "lm_pitch": "Minishell, un interpréteur de commandes UNIX développé en C autour des appels système, pipes et signaux",
            "search_blob": "minishell c linux unix shell bash processus pipes signaux système valgrind parsing",
        },
        {
            "verify_key": "cub3d",
            "name": "Cub3d -- École 42",
            "subtitle": r"Moteur 3D \& Audit Mémoire Bas Niveau en C (Solo)",
            "url": "https://github.com/Devilijho/Cub3d",
            "bullets": [
                r"Implémentation d'un moteur de rendu graphique 3D par raycasting (algorithmique bas niveau et arithmétique de pointeurs).",
                r"Audit de sécurité mémoire : prévention des débordements de tampon (buffer overflows) et validation zéro-fuite sous Valgrind.",
                r"Parsing défensif des fichiers de configuration de scènes avec validation stricte des entrées.",
            ],
            "lm_pitch": "Cub3d, un moteur 3D en C validé sans fuite mémoire sous Valgrind avec parsing défensif",
            "search_blob": "cub3d c valgrind gdb mémoire sécurité buffer overflow parsing linux algorithmique",
        },
        {
            "verify_key": "stage-automation-pipeline",
            "name": "StageBot -- Pipeline d'Automatisation",
            "subtitle": r"Outil Python, Web UI \& LaTeX (Solo)",
            "url": "https://github.com/Devilijho/stage-automation-pipeline",
            "bullets": [
                r"Développement en Python d'une application locale avec serveur HTTP, interface web et automatisation navigateur (Playwright).",
                r"Extraction et analyse de données web multi-sources avec génération automatisée de documents PDF via LaTeX.",
                r"Architecture modulaire CLI + Web UI avec suivi structuré des données et tests de validation.",
            ],
            "lm_pitch": "StageBot, un pipeline Python d'automatisation web (Playwright, serveur HTTP et génération PDF)",
            "search_blob": "stagebot python html automation playwright scraping backend web api cli shell",
        },
        {
            "verify_key": "portfolio-iut",
            "name": "Portfolio Professionnel -- IUT d'Orsay",
            "subtitle": r"Application Frontend React 18, TypeScript \& Vite (Solo)",
            "url": "https://github.com/Devilijho/portfolio-iut",
            "bullets": [
                r"Développement d'une application web modulaire en React 18 et TypeScript avec build Vite et thème Dark/Light en CSS3.",
                r"Structuration en composants réutilisables, typage strict et interface responsive.",
            ],
            "lm_pitch": "une application web modulaire développée en React 18, TypeScript et Vite à l'IUT d'Orsay",
            "search_blob": "portfolio react typescript javascript html css frontend ui ux vite web",
        },
        {
            "verify_key": "game jam",
            "name": "Game Jam IUT Paris-Saclay",
            "subtitle": "Hackathon de Programmation (36h, Équipe)",
            "url": "",
            "bullets": [
                r"Conception rapide et fiable d'un exécutable complet sous contrainte de temps stricte en équipe.",
                r"Structuration de la logique applicative, gestion des états et tests fonctionnels.",
            ],
            "lm_pitch": "la Game Jam de l'IUT Paris-Saclay, un hackathon de 36h en équipe sous contrainte de temps",
            "search_blob": "game jam hackathon agile équipe java python c++ logique applicative tests",
        },
    ]

    projects: List[Dict[str, Any]] = [
        p for p in curated_catalog if p["verify_key"] in verified_lower
    ]
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
    profile: Dict[str, Any], role: str, matched_keywords: List[str], archetype: str = "systems"
) -> str:
    kw_list = matched_keywords[:5] if matched_keywords else ["C/C++", "Linux", "Git", "SQL"]
    kw_str = latex_escape(", ".join(kw_list))
    role_prose = latex_escape(clean_role_for_prose(role))
    edu = profile.get("education", {})
    intern = profile.get("internship", {})
    school = edu.get("school", "IUT d'Orsay")
    secondary = edu.get("secondary_school", "École 42 Paris (Tronc Commun validé)")
    avail = intern.get("availability_short", "Du 03/05 au 16/07 (3 mois, prolongeable durant l'été)")

    focus_map = {
        "cyber": "Spécialisé en réseaux, cryptographie et sécurité bas niveau",
        "infra": "Orienté administration systèmes Linux, conteneurisation Docker et architecture réseau",
        "web": "Expérimenté en développement web full-stack, bases de données SQL et conteneurisation",
        "systems": "Orienté ingénierie logicielle, programmation systèmes UNIX et architecture réseau",
    }
    focus_line = focus_map.get(archetype, focus_map["systems"])
    return (
        f"Étudiant en BUT 2 Informatique à {latex_escape(school)} et issu de {latex_escape(secondary)}, "
        f"je recherche un stage conventionné ({latex_escape(avail)}) en tant que {role_prose}. "
        f"{focus_line}, je suis opérationnel sur {kw_str} et prêt à contribuer concrètement à vos projets techniques."
    )


def build_skills_block(matched_keywords: List[str], verified_keywords: Optional[set] = None) -> str:
    matched_set = {m.lower() for m in matched_keywords}
    verified_set = {v.lower() for v in (verified_keywords or set())}

    lang_items = ["C", "C++", "TypeScript", "JavaScript", "Java", "SQL (PostgreSQL / Oracle / SQLite)", "HTML5/CSS3", "Assembly (x86)", "Notions de C\\#"]
    if "python" in verified_set:
        lang_items.insert(2, "Python")

    categories = [
        ("Langages", lang_items),
        (
            "Systèmes \\& DevOps",
            ["Linux (UNIX / Bash)", "Docker \\& Docker Compose", "NGINX", "Git", "Windows", "MacOS", "VirtualBox"],
        ),
        (
            "Sécurité \\& Débogage",
            ["Cybersécurité", "Réseaux (TCP/IP, HTTP)", "Cryptographie", "GDB", "Valgrind (Memcheck, Helgrind)", "Analyse mémoire", "Reverse Engineering"],
        ),
        (
            "Frameworks \\& Outils",
            ["React", "Node.js", "Vite", "Three.js", "API REST", "Supabase", "Eclipse", "Modélisation (MCD/SR)"],
        ),
    ]

    def is_matched(item: str) -> bool:
        it_l = item.lower()
        return any(
            (m == "c" and item == "C")
            or (m != "c" and m in it_l)
            for m in matched_set
        )

    scored_cats = []
    for cat_name, items in categories:
        reordered = sorted(items, key=lambda x: -int(is_matched(x)))
        formatted = [f"\\textbf{{{x}}}" if is_matched(x) else x for x in reordered]
        cat_score = sum(int(is_matched(x)) for x in items)
        scored_cats.append((cat_score, cat_name, formatted))

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
    bullet_caps: Tuple[int, int, int] = (3, 3, 3),
) -> Tuple[List[Dict[str, Any]], str]:
    job_lower = job_text.lower()
    job_tokens = set(re.findall(r"[a-z0-9\+\#]{3,}", job_lower))
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
        link_str = f" \\textperiodcentered{{}} \\href{{{proj['url']}}}{{\\texttt{{GitHub}}}}" if proj.get("url") else ""
        lines.append(f"\\textbf{{{proj['name']}}} --- \\textit{{{proj['subtitle']}}}{link_str}")
        cap = bullet_caps[idx] if idx < len(bullet_caps) else bullet_caps[-1]
        ranked_bullets = sorted(
            enumerate(proj["bullets"]),
            key=lambda pair: (
                -(
                    sum(2 for kw in matched_keywords if kw.lower() in pair[1].lower())
                    + sum(1 for tok in job_tokens if tok in pair[1].lower())
                ),
                pair[0],
            ),
        )
        chosen_bullets = [b for _, b in ranked_bullets[:cap]]
        lines.append("\\begin{itemize}")
        for b in chosen_bullets:
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
        line.strip()
        for line in log_text.splitlines()
        if "Overfull \\hbox" in line or "Overfull \\vbox" in line
    ]
    return (pages == 1 and len(overfull) == 0), pages, overfull


def build_lm_tex(
    profile: Dict[str, Any],
    company: str,
    role: str,
    selected_projects: List[Dict[str, Any]],
    matched_keywords: List[str],
    job_text: str = "",
    archetype: str = "systems",
) -> str:
    base_lm = BASE_LM_PATH.read_text(encoding="utf-8")
    kw_str = ", ".join(matched_keywords[:5]) if matched_keywords else "C/C++, TypeScript, Linux, Git, Docker"
    role_prose = clean_role_for_prose(role)
    city = infer_offer_city(job_text)
    edu = profile.get("education", {})
    intern = profile.get("internship", {})
    personal = profile.get("personal", {})
    my_city = personal.get("city", "Bures-sur-Yvette")
    avail_short = intern.get("availability_short", "du 03/05 au 16/07 (3 mois, prolongeable l'été)")
    avail_full = intern.get("availability_full", "Stage de 3 mois du 3 mai au 16 juillet, avec possibilité de prolongation sur l'été")
    school = edu.get("school", "Université Paris-Saclay (IUT d'Orsay)")
    secondary = edu.get("secondary_school", "École 42 Paris (Tronc Commun validé)")

    p1_pitch = selected_projects[0].get("lm_pitch", selected_projects[0]["name"]) if selected_projects else "Webserv"
    p2_pitch = selected_projects[1].get("lm_pitch", selected_projects[1]["name"]) if len(selected_projects) > 1 else ""

    date_line = f"{latex_escape(my_city)}, le {latex_escape(format_french_date())}\\\\[0.6em]"
    recipient = (
        f"\\textbf{{À l'attention de l'équipe de recrutement}}\\\\\n"
        f"{latex_escape(company)}\\\\\n{latex_escape(city)}"
    )
    obj = (
        f"\\textbf{{Objet :}} Candidature pour un stage conventionné ({latex_escape(avail_short)}) "
        f"-- {latex_escape(role_prose)}"
    )

    vous_hooks = {
        "cyber": (
            f"Les enjeux de sécurité, de fiabilité et de protection des systèmes portés par {latex_escape(company)} "
            f"résonnent directement avec mon parcours. Votre offre de stage en tant que {latex_escape(role_prose)} "
            f"autour de {latex_escape(kw_str)} retient particulièrement mon attention, et je souhaite vous proposer "
            f"ma candidature pour un {latex_escape(avail_full.lower())} (stage conventionné, Université Paris-Saclay)."
        ),
        "infra": (
            f"La solidité des infrastructures et l'exigence opérationnelle de {latex_escape(company)} "
            f"correspondent pleinement à ce que je recherche pour mettre en pratique mes acquis en systèmes et réseaux. "
            f"Votre offre de stage de {latex_escape(role_prose)} mobilisant {latex_escape(kw_str)} a immédiatement retenu "
            f"mon attention dans le cadre de mon {latex_escape(avail_full.lower())} (stage conventionné, Université Paris-Saclay)."
        ),
        "web": (
            f"La conception de produits logiciels fiables et évolutifs au sein de {latex_escape(company)} "
            f"correspond directement à mon envie de contribuer à des applications en production. "
            f"Votre offre de stage de {latex_escape(role_prose)} autour de {latex_escape(kw_str)} m'intéresse tout particulièrement "
            f"pour mon {latex_escape(avail_full.lower())} (stage conventionné, Université Paris-Saclay)."
        ),
        "systems": (
            f"Les défis d'ingénierie logicielle et de performance technique menés chez {latex_escape(company)} "
            f"correspondent pleinement à mes aspirations professionnelles. Votre offre de stage de {latex_escape(role_prose)} "
            f"autour de {latex_escape(kw_str)} a retenu toute mon attention dans le cadre de mon "
            f"{latex_escape(avail_full.lower())} (stage conventionné, Université Paris-Saclay)."
        ),
    }
    vous = vous_hooks.get(archetype, vous_hooks["systems"])

    proj_sentence = f"J'ai notamment conçu {latex_escape(p1_pitch)}"
    if p2_pitch:
        proj_sentence += f", ainsi que {latex_escape(p2_pitch)}"
    proj_sentence += "."

    moi = (
        f"Actuellement en BUT 2 Informatique à {latex_escape(school)} (parcours Réseaux, Cryptographie et Sécurité) "
        f"et fort de {latex_escape(secondary)}, j'ai développé une solide autonomie technique sur {latex_escape(kw_str)} "
        f"et les environnements Linux/Git. {proj_sentence} "
        f"Mon expérience antérieure en gestion opérationnelle e-commerce m'a également appris le sens des priorités, "
        f"la résolution pragmatique de problèmes et le travail rigoureux orienté résultat."
    )

    nous = (
        f"Intégrer l'équipe de {latex_escape(company)} serait l'opportunité d'apporter ma capacité d'apprentissage rapide "
        f"(peer-learning à 42), ma rigueur technique et mon profil trilingue (français, anglais, espagnol) sur vos "
        f"chantiers en cours, tout en gagnant en maturité au contact de vos ingénieurs. "
        f"Je serais ravi d'échanger avec vous lors d'un entretien ou d'un test technique."
    )

    if "% === LM_DATE_START ===" in base_lm:
        base_lm = replace_marker_block(base_lm, "LM_DATE", date_line)
    lm = replace_marker_block(base_lm, "LM_RECIPIENT", recipient)
    lm = replace_marker_block(lm, "LM_OBJECT", obj)
    lm = replace_marker_block(lm, "LM_VOUS", vous)
    lm = replace_marker_block(lm, "LM_MOI", moi)
    lm = replace_marker_block(lm, "LM_NOUS", nous)
    return lm


def main() -> int:
    parser = argparse.ArgumentParser(description="ATS CV & LM Tailoring Engine")
    parser.add_argument("--url", default="", help="Job offer URL")
    parser.add_argument("--text", default="", help="Pasted job description text")
    parser.add_argument("--file", default="", help="Path to file containing job description")
    parser.add_argument("--company", default="", help="Override company name")
    parser.add_argument("--role", default="", help="Override role title")
    parser.add_argument("--force", action="store_true", help="Allow overwriting duplicate entry")
    parser.add_argument("--json", action="store_true", help="Output metadata as JSON on stdout")
    args = parser.parse_args()

    profile = load_profile()
    page_title = ""
    job_text = args.text

    if args.file:
        job_text = Path(args.file).read_text(encoding="utf-8")
    elif args.url and not job_text:
        if not args.json:
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
    archetype = detect_role_archetype(role, job_text, matched_keywords)
    accroche = build_cv_accroche(profile, role, matched_keywords, archetype=archetype)
    skills_block = build_skills_block(matched_keywords, verified_keywords=verified_keywords)

    selected_projects: List[Dict[str, Any]] = []
    cv_pdf_path: Optional[Path] = None
    cv_ok = False
    pages = 1
    for bullet_caps in ((3, 3, 3), (3, 3, 2), (3, 2, 2), (2, 2, 2), (2, 2, 1), (2, 1, 1), (1, 1, 1)):
        selected_projects, projects_block = select_and_format_projects(
            projects, matched_keywords, job_text, bullet_caps=bullet_caps
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
                f"{{\\small\\textbf{{Disponibilité :}} {latex_escape(avail_short)} \\textperiodcentered{{}} Stage conventionné (Université Paris-Saclay)}}\\\\[0.15em]",
            )
        cv_tex = replace_marker_block(cv_tex, "CV_ACCROCHE", f"{{\\small\\itshape\n{accroche}\n}}")
        cv_tex = replace_marker_block(cv_tex, "SKILLS_SECTION", skills_block)
        cv_tex = replace_marker_block(cv_tex, "PROJECTS_SECTION", projects_block)
        cv_tex_path.write_text(cv_tex, encoding="utf-8")

        cv_pdf_path, log_text = compile_tex(cv_tex_path)
        cv_ok, pages, overfull = verify_pdf_one_page(cv_pdf_path, log_text)
        if pages == 1:
            if overfull and not args.json:
                print(f"[!] Warning: LaTeX overfull detected ({len(overfull)} lines).")
            break
        if not args.json:
            print(f"[*] CV was {pages} pages with bullet_caps={bullet_caps}, tightening...")

    if not cv_ok and cv_pdf_path and not args.json:
        print(f"[!] Check {cv_pdf_path} (pages={pages}).")

    lm_tex = build_lm_tex(
        profile, company, role, selected_projects, matched_keywords, job_text=job_text, archetype=archetype
    )
    lm_tex_path.write_text(lm_tex, encoding="utf-8")
    lm_pdf_path, lm_log = compile_tex(lm_tex_path)
    _, lm_pages, _ = verify_pdf_one_page(lm_pdf_path, lm_log)

    logged = check_and_log_application(
        company, role, args.url, cv_title, matched_keywords, cv_pdf_path, lm_pdf_path, force=args.force
    )

    meta_path = target_dir / "metadata.json"
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
        "metadata_path": str(meta_path),
        "tracker_logged": logged,
    }
    meta_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False))
        return 0

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
