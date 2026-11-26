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
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
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
    PROJECTS_PATH,
    TEMPLATES_DIR,
    WORKSPACE,
    load_profile,
)
from core.offer_text import (
    BOARD_NAMES,
    company_prose,
    is_generic_company,
    sanitize_role,
    truncate_words,
)
from core.storage import check_and_log_application, read_applications, refresh_application

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

# Aliases too generic to count as a skill on a single occurrence (e.g. "audit" in finance offers).
WEAK_ALIASES = {"audit", "shell", "server", "serveur", "infrastructure", "rest", "json", "ip", "ui", "ux"}

# Everything after these markers is page chrome (similar offers...), not the offer itself.
NOISE_CUT_MARKERS = (
    "offres similaires", "offres d'emploi similaires", "ces offres peuvent vous intéresser",
    "autres offres", "vous pourriez aussi", "similar jobs", "voir aussi",
)

# Generic, non-invented fallbacks when no clean job title can be recovered from the offer.
ARCHETYPE_ROLE_LABELS: Dict[str, str] = {
    "cyber": "Analyste Cybersécurité",
    "infra": "Administrateur Systèmes et Réseaux",
    "web": "Développeur Web Full-Stack",
    "systems": "Développeur Logiciel",
}

# Terms (matched against each project's search_blob) that break ties toward the offer's archetype.
ARCHETYPE_TERMS: Dict[str, Tuple[str, ...]] = {
    "cyber": ("sécurité", "cryptographie", "valgrind", "réseau"),
    "infra": ("docker", "linux", "devops", "réseau", "infrastructure"),
    "web": ("typescript", "fullstack", "frontend", "react", "api"),
    "systems": ("c++", "unix", "linux", "valgrind", "système"),
}

CV_TITLE_MAX_LEN = 78
CV_ACCROCHE_MAX_LEN = 520
CV_MIN_FILL_RATIO = 0.70

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
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
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


def load_projects_config(projects_path: Path = PROJECTS_PATH) -> Dict[str, Any]:
    """Load externalized projects, skill categories, and cover-letter copy from config/projects.json."""
    if not projects_path.exists():
        return {}
    try:
        data = json.loads(projects_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def parse_verified_master_profile(
    master_profile_path: Path = MASTER_PROFILE_PATH,
    base_cv_path: Path = BASE_CV_PATH,
    projects_path: Path = PROJECTS_PATH,
    profile: Optional[Dict[str, Any]] = None,
) -> Tuple[set, List[Dict[str, Any]]]:
    verified_text = ""
    if master_profile_path.exists():
        verified_text += master_profile_path.read_text(encoding="utf-8") + "\n"
    if base_cv_path.exists():
        verified_text += base_cv_path.read_text(encoding="utf-8") + "\n"
    verified_lower = verified_text.lower()

    proj_cfg = load_projects_config(projects_path)
    kw_map: Dict[str, List[str]] = dict(KNOWN_KEYWORDS)
    extra_kw = proj_cfg.get("extra_keywords")
    if isinstance(extra_kw, dict):
        for k, v in extra_kw.items():
            if isinstance(v, list):
                kw_map[str(k)] = [str(x) for x in v]

    verified_keywords = set()
    for canonical, aliases in kw_map.items():
        if canonical.lower() in verified_lower or any(a.strip().lower() in verified_lower for a in aliases):
            verified_keywords.add(canonical)

    raw_catalog = (profile or {}).get("projects") or proj_cfg.get("projects") or []
    curated_catalog = [p for p in raw_catalog if isinstance(p, dict) and p.get("verify_key")]

    projects: List[Dict[str, Any]] = [
        p for p in curated_catalog if str(p["verify_key"]).lower() in verified_lower
    ]
    return verified_keywords, projects


def _term_in_text(term: str, text_lower: str) -> bool:
    t = term.strip().lower()
    if not t:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9+#])", text_lower) is not None


def _term_count(term: str, text_lower: str) -> int:
    t = term.strip().lower()
    if not t:
        return 0
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9+#])", text_lower))


def _letter_keyword_in_context(letter: str, text: str) -> bool:
    """Single-letter languages ('C', 'R') only count as an uppercase token in a programming context.

    Avoids false positives from French ("c'est", "C" in lists of options) while still matching
    "C, Python", "Python/C", "langage C", "programmation en C", "C/C++".
    """
    letter = letter.upper()
    not_part = r"(?![\w'’+#.\-])"
    sep_before = rf"[,;/|•·(]\s*{letter}{not_part}"
    sep_after = rf"(?<![\w'’.+#\-]){letter}\s*[,;/|•·)]"
    prefix = (
        r"(?i:\b(?:langages?|language|programmation|programming|développement|developpement|maîtrise|"
        r"maitrise|connaissances?|notions?|compétences?|competences?|expérience|experience)"
        r"\s+(?:en\s+|du\s+|de\s+|of\s+|in\s+)?)"
    ) + letter + not_part
    return any(re.search(rx, text) for rx in (sep_before, sep_after, prefix))


def focus_job_text(job_text: str) -> str:
    """Drop trailing page chrome (similar offers, ...) so unrelated keywords don't pollute matching."""
    low = job_text.lower()
    cuts = [i for i in (low.find(m) for m in NOISE_CUT_MARKERS) if i > 300]
    return job_text[: min(cuts)] if cuts else job_text


def extract_matched_keywords(job_text: str, verified_keywords: set) -> List[str]:
    text_lower = " " + job_text.lower() + " "
    matched: List[str] = []
    for kw in sorted(verified_keywords):
        terms = [kw] + [a for a in KNOWN_KEYWORDS.get(kw, [kw.lower()]) if a.strip().lower() != kw.lower()]
        for term in terms:
            t = term.strip()
            if len(t) == 1:
                hit = _letter_keyword_in_context(t, job_text)
            elif t.lower() in WEAK_ALIASES:
                hit = _term_count(t, text_lower) >= 2
            else:
                hit = _term_in_text(t, text_lower)
            if hit:
                matched.append(kw)
                break
    return matched


def build_cv_title(role: str, matched_keywords: List[str]) -> str:
    role_clean = re.sub(r"\s*\([^)]*\)", "", role).strip()
    if not re.search(r"stage|stagiaire|intern", role_clean, re.I):
        role_clean = f"Stage -- {role_clean}"
    else:
        role_clean = re.sub(r"\s+[-–—]\s+", " -- ", role_clean)
    return truncate_words(role_clean, CV_TITLE_MAX_LEN)


def build_cv_accroche(
    profile: Dict[str, Any], role: str, matched_keywords: List[str], archetype: str = "systems"
) -> str:
    kw_list = matched_keywords[:5] if matched_keywords else ["Linux", "Git", "SQL"]
    kw_str = latex_escape(", ".join(kw_list))
    role_prose = latex_escape(clean_role_for_prose(role))
    edu = profile.get("education", {})
    intern = profile.get("internship", {})
    degree = edu.get("degree", "Informatique")
    school = edu.get("school", "Université")
    secondary = (edu.get("secondary_school") or "").strip()
    avail = intern.get("availability_short", intern.get("duration", "3 mois"))

    focus_map = {
        "cyber": "Spécialisé en réseaux, cryptographie et sécurité bas niveau",
        "infra": "Orienté administration systèmes Linux, conteneurisation Docker et architecture réseau",
        "web": "Expérimenté en développement web full-stack, bases de données SQL et conteneurisation",
        "systems": "Orienté ingénierie logicielle, programmation systèmes UNIX et architecture réseau",
    }
    focus_line = focus_map.get(archetype, focus_map["systems"])
    edu_clause = f"Étudiant en {latex_escape(degree)} à {latex_escape(school)}"
    if secondary:
        edu_clause += f" et issu de {latex_escape(secondary)}"
    return (
        f"{edu_clause}, je recherche un stage conventionné ({latex_escape(avail)}) en tant que {role_prose}. "
        f"{focus_line}, je suis opérationnel sur {kw_str} et prêt à contribuer concrètement à vos projets techniques."
    )


def build_skills_block(
    matched_keywords: List[str],
    verified_keywords: Optional[set] = None,
    skills_categories: Optional[List[Dict[str, Any]]] = None,
    projects_path: Path = PROJECTS_PATH,
) -> str:
    matched_set = {m.lower() for m in matched_keywords}
    verified_set = {v.lower() for v in (verified_keywords or set())}

    raw_cats = skills_categories
    if raw_cats is None:
        raw_cats = load_projects_config(projects_path).get("skills_categories")

    categories: List[Tuple[str, List[str]]] = []
    if isinstance(raw_cats, list) and raw_cats:
        for cat in raw_cats:
            if not isinstance(cat, dict) or not cat.get("name") or not cat.get("items"):
                continue
            reqs = cat.get("require_verified_keywords") or {}
            items = [
                str(it)
                for it in cat["items"]
                if str(it) not in reqs or str(reqs[str(it)]).lower() in verified_set
            ]
            if items:
                categories.append((str(cat["name"]), items))
    if not categories:
        fallback_items = sorted(verified_keywords) if verified_keywords else ["Linux", "Git", "SQL"]
        categories = [("Compétences", [latex_escape(x) for x in fallback_items])]

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
    archetype: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    job_lower = job_text.lower()
    job_tokens = set(re.findall(r"[a-z0-9\+\#]{3,}", job_lower))
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for proj in projects:
        blob = proj["search_blob"].lower()
        score = sum(3 for kw in matched_keywords if kw.lower() in blob)
        score += sum(2 for term in ARCHETYPE_TERMS.get(archetype or "", ()) if term in blob)
        for token in set(re.findall(r"[a-z0-9\+\#]{3,}", blob)):
            if token in job_lower:
                score += 1
        scored.append((score, proj))

    scored.sort(key=lambda t: -t[0])
    selected = [p for _, p in scored[:3]]
    if not selected:
        return [], ""

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
        try:
            res = subprocess.run(
                [pdflatex_bin, "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
                cwd=str(out_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"pdflatex timed out after 30s for {tex_path.name}") from exc
        log_path = tex_path.with_suffix(".log")
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else res.stdout
        if res.returncode != 0:
            raise RuntimeError(f"pdflatex failed for {tex_path.name}:\n{res.stdout[-1500:]}")
        return pdf_path, log_text
    elif tectonic_bin:
        try:
            res = subprocess.run(
                [tectonic_bin, tex_path.name],
                cwd=str(out_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"tectonic timed out after 30s for {tex_path.name}") from exc
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


def cv_quality_issues(cv_title: str, accroche: str, role: str = "") -> List[str]:
    """Return human-readable problems that make a generated CV look sloppy (empty list = clean)."""
    issues: List[str] = []
    if len(cv_title) > CV_TITLE_MAX_LEN:
        issues.append(f"CV title too long ({len(cv_title)} > {CV_TITLE_MAX_LEN})")
    if len(accroche) > CV_ACCROCHE_MAX_LEN:
        issues.append(f"Accroche too long ({len(accroche)} > {CV_ACCROCHE_MAX_LEN})")
    board_rx = "|".join(re.escape(b) for b in BOARD_NAMES)
    noise = re.compile(rf"\bil y a \d+|\b(?:{board_rx})\b|\b\d{{2}}\s*[-–]\s*\d{{2}}\b|…|\.\.\.", re.I)
    for label, text in (("title", cv_title), ("accroche", accroche), ("role", role)):
        m = noise.search(text or "")
        if m:
            issues.append(f"Scraped noise in {label}: '{m.group(0)}'")
    return issues


def pdf_fill_ratio(pdf_path: Path) -> Optional[float]:
    """Fraction of page height covered by text (lowest text baseline); None if undeterminable."""
    try:
        page = PdfReader(str(pdf_path)).pages[0]
        height = float(page.mediabox.height)
        ys: List[float] = []

        def visitor(text, cm, tm, font_dict, font_size):  # noqa: ANN001
            if text.strip():
                ys.append(tm[4] * cm[1] + tm[5] * cm[3] + cm[5])

        page.extract_text(visitor_text=visitor)
        return round((height - min(ys)) / height, 3) if ys and height else None
    except Exception:
        return None


def render_preview_png(pdf_path: Path) -> Optional[Path]:
    """Optional PNG preview next to the PDF when poppler's `pdftoppm` is installed."""
    exe = shutil.which("pdftoppm")
    if not exe:
        return None
    stem = pdf_path.with_suffix("")
    try:
        subprocess.run(
            [exe, "-png", "-r", "70", "-singlefile", str(pdf_path), str(stem)],
            capture_output=True, timeout=20, check=True,
        )
    except Exception:
        return None
    png = stem.parent / f"{stem.name}.png"
    return png if png.exists() else None


def remove_orphan_output(old_cv_pdf: str, keep_dir: Path) -> bool:
    """After a remake moved outputs to a new folder, delete the old folder if nothing else uses it."""
    if not old_cv_pdf:
        return False
    old_dir = (WORKSPACE / Path(old_cv_pdf)).parent.resolve()
    try:
        old_dir.relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return False
    if old_dir == OUTPUT_DIR.resolve() or old_dir == keep_dir.resolve() or not old_dir.is_dir():
        return False
    for row in read_applications():
        if row.get("CV_PDF") and (WORKSPACE / Path(row["CV_PDF"])).parent.resolve() == old_dir:
            return False
    shutil.rmtree(old_dir, ignore_errors=True)
    return True


def audit_outputs() -> int:
    """Scan existing `output/*/metadata.json` + CV `.tex` for scraped-noise problems; return #flagged."""
    flagged = 0
    for meta_path in sorted(OUTPUT_DIR.glob("*/metadata.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        accroche = ""
        for tex in meta_path.parent.glob("CV_*.tex"):
            m = re.search(r"CV_ACCROCHE_START ===(.*?)% === CV_ACCROCHE_END", tex.read_text(encoding="utf-8"), re.S)
            accroche = re.sub(r"^\s*\{\\small\\itshape|\}\s*$", "", m.group(1)).strip() if m else ""
        issues = cv_quality_issues(meta.get("cv_title", ""), accroche, meta.get("role", ""))
        if is_generic_company(meta.get("company", "")):
            issues.append(f"Generic company '{meta.get('company')}' (job board, not employer)")
        if issues:
            flagged += 1
            print(f"[!] {meta_path.parent.name}: " + "; ".join(issues))
    print(f"[*] Audit complete: {flagged} output folder(s) flagged.")
    return flagged


def build_lm_tex(
    profile: Dict[str, Any],
    company: str,
    role: str,
    selected_projects: List[Dict[str, Any]],
    matched_keywords: List[str],
    job_text: str = "",
    archetype: str = "systems",
    projects_path: Path = PROJECTS_PATH,
) -> str:
    base_lm = BASE_LM_PATH.read_text(encoding="utf-8")
    kw_str = ", ".join(matched_keywords[:5]) if matched_keywords else "Linux, Git, SQL"
    role_prose = clean_role_for_prose(role)
    generic_company = is_generic_company(company)
    co = latex_escape(company_prose(company))
    city = infer_offer_city(job_text)
    edu = profile.get("education", {})
    intern = profile.get("internship", {})
    personal = profile.get("personal", {})
    proj_cfg = load_projects_config(projects_path)
    lm_cfg = {**(proj_cfg.get("cover_letter") or {}), **(profile.get("cover_letter") or {})}

    my_city = personal.get("city", "Paris")
    avail_short = intern.get("availability_short", intern.get("duration", "3 mois"))
    avail_full = intern.get("availability_full", f"Stage de {intern.get('duration', '3 mois')}")
    degree = edu.get("degree", "Informatique")
    school = edu.get("school", "Université")
    secondary = (edu.get("secondary_school") or "").strip()
    track = (edu.get("track") or lm_cfg.get("track") or "").strip()
    contract_note = (intern.get("contract_note") or lm_cfg.get("contract_note") or f"stage conventionné, {school}").strip()

    p1_pitch = selected_projects[0].get("lm_pitch", selected_projects[0]["name"]) if selected_projects else ""
    p2_pitch = selected_projects[1].get("lm_pitch", selected_projects[1]["name"]) if len(selected_projects) > 1 else ""

    date_line = f"{latex_escape(my_city)}, le {latex_escape(format_french_date())}\\\\[0.6em]"
    recipient = (
        "\\textbf{À l'attention de l'équipe de recrutement}\\\\\n"
        + ("" if generic_company else f"{latex_escape(company)}\\\\\n")
        + latex_escape(city)
    )
    obj = (
        f"\\textbf{{Objet :}} Candidature pour un stage conventionné ({latex_escape(avail_short)}) "
        f"-- {latex_escape(role_prose)}"
    )

    vous_hooks = {
        "cyber": (
            f"Les enjeux de sécurité, de fiabilité et de protection des systèmes portés par {co} "
            f"résonnent directement avec mon parcours. Votre offre de stage en tant que {latex_escape(role_prose)} "
            f"autour de {latex_escape(kw_str)} retient particulièrement mon attention, et je souhaite vous proposer "
            f"ma candidature pour un {latex_escape(avail_full.lower())} ({latex_escape(contract_note)})."
        ),
        "infra": (
            f"La solidité des infrastructures et l'exigence opérationnelle de {co} "
            f"correspondent pleinement à ce que je recherche pour mettre en pratique mes acquis en systèmes et réseaux. "
            f"Votre offre de stage de {latex_escape(role_prose)} mobilisant {latex_escape(kw_str)} a immédiatement retenu "
            f"mon attention dans le cadre de mon {latex_escape(avail_full.lower())} ({latex_escape(contract_note)})."
        ),
        "web": (
            f"La conception de produits logiciels fiables et évolutifs au sein de {co} "
            f"correspond directement à mon envie de contribuer à des applications en production. "
            f"Votre offre de stage de {latex_escape(role_prose)} autour de {latex_escape(kw_str)} m'intéresse tout particulièrement "
            f"pour mon {latex_escape(avail_full.lower())} ({latex_escape(contract_note)})."
        ),
        "systems": (
            f"Les défis d'ingénierie logicielle et de performance technique menés chez {co} "
            f"correspondent pleinement à mes aspirations professionnelles. Votre offre de stage de {latex_escape(role_prose)} "
            f"autour de {latex_escape(kw_str)} a retenu toute mon attention dans le cadre de mon "
            f"{latex_escape(avail_full.lower())} ({latex_escape(contract_note)})."
        ),
    }
    vous = vous_hooks.get(archetype, vous_hooks["systems"])

    proj_sentence = ""
    if p1_pitch:
        proj_sentence = f" J'ai notamment conçu {latex_escape(p1_pitch)}"
        if p2_pitch:
            proj_sentence += f", ainsi que {latex_escape(p2_pitch)}"
        proj_sentence += "."

    edu_intro = f"Actuellement en {latex_escape(degree)} à {latex_escape(school)}"
    if track:
        edu_intro += f" (parcours {latex_escape(track)})"
    if secondary:
        edu_intro += f" et fort de {latex_escape(secondary)}"
    exp_sentence = (lm_cfg.get("experience_sentence") or "").strip()
    exp_part = f" {latex_escape(exp_sentence)}" if exp_sentence else ""

    moi = (
        f"{edu_intro}, j'ai développé une solide autonomie technique sur {latex_escape(kw_str)} "
        f"et les environnements Linux/Git.{proj_sentence}{exp_part}"
    )

    strengths = (
        lm_cfg.get("strengths_sentence")
        or "ma capacité d'apprentissage rapide, ma rigueur technique et mon autonomie"
    ).strip()
    nous = (
        f"Intégrer l'équipe de {co} serait l'opportunité d'apporter {latex_escape(strengths)} sur vos "
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
    parser.add_argument(
        "--remake", action="store_true",
        help="Regenerate documents for an offer already in applications.csv (updates the row in place, keeps its status)",
    )
    parser.add_argument("--audit", action="store_true", help="Audit existing output/ folders for scraped-noise CV issues")
    args = parser.parse_args()

    if args.audit:
        return 0 if audit_outputs() == 0 else 2

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

    job_text = focus_job_text(job_text)
    inferred_company, inferred_role = infer_company_and_role(job_text, page_title, args.url)
    company = args.company.strip() or inferred_company
    generic_company = is_generic_company(company)
    role = sanitize_role(args.role.strip() or inferred_role)

    verified_keywords, projects = parse_verified_master_profile(profile=profile)
    matched_keywords = extract_matched_keywords(job_text, verified_keywords)
    archetype = detect_role_archetype(role, job_text, matched_keywords)
    if not role:
        role = ARCHETYPE_ROLE_LABELS.get(archetype, ARCHETYPE_ROLE_LABELS["systems"])
    cv_title = build_cv_title(role, matched_keywords)

    role_slug = sanitize_slug(role)[:30]
    # Unknown employer (job board / anonymous): name the folder after board + role so offers never collide.
    company_slug = sanitize_slug(f"{company or 'Offre'}_{role_slug}") if generic_company else sanitize_slug(company)
    my_name_slug = sanitize_slug(profile["personal"]["full_name"])
    target_dir = OUTPUT_DIR / company_slug
    # Avoid overwriting a different offer at the same company
    meta_file = target_dir / "metadata.json"
    if meta_file.exists() and not args.force:
        try:
            existing_meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if (existing_meta.get("url") or "").strip().rstrip("/").lower() != args.url.strip().rstrip("/").lower():
                suffix = hashlib.md5(args.url.encode("utf-8")).hexdigest()[:6] if generic_company else role_slug
                target_dir = OUTPUT_DIR / f"{company_slug}_{suffix}"
        except Exception:
            pass
    target_dir.mkdir(parents=True, exist_ok=True)

    cv_tex_path = target_dir / f"CV_{my_name_slug}_{company_slug}.tex"
    lm_tex_path = target_dir / f"LM_{my_name_slug}_{company_slug}.tex"

    base_cv = BASE_CV_PATH.read_text(encoding="utf-8")
    accroche = build_cv_accroche(profile, role, matched_keywords, archetype=archetype)
    skills_block = build_skills_block(matched_keywords, verified_keywords=verified_keywords)

    selected_projects: List[Dict[str, Any]] = []
    cv_pdf_path: Optional[Path] = None
    cv_ok = False
    pages = 1
    for bullet_caps in ((3, 3, 3), (3, 3, 2), (3, 2, 2), (2, 2, 2), (2, 2, 1), (2, 1, 1), (1, 1, 1)):
        selected_projects, projects_block = select_and_format_projects(
            projects, matched_keywords, job_text, bullet_caps=bullet_caps, archetype=archetype
        )
        cv_tex = replace_marker_block(
            base_cv, "CV_TITLE", f"{{\\large\\bfseries {latex_escape(cv_title)}}}\\\\[0.2em]"
        )
        intern_cfg = profile.get("internship", {})
        edu_cfg = profile.get("education", {})
        proj_cfg = load_projects_config()
        lm_cfg = proj_cfg.get("cover_letter") or {}
        avail_short = intern_cfg.get("availability_short", intern_cfg.get("duration", "3 mois"))
        contract_label = intern_cfg.get("contract_label") or lm_cfg.get("contract_label") or f"Stage conventionné ({edu_cfg.get('school', 'Université')})"
        if "% === CV_AVAILABILITY_START ===" in cv_tex:
            cv_tex = replace_marker_block(
                cv_tex,
                "CV_AVAILABILITY",
                f"{{\\small\\textbf{{Disponibilité :}} {latex_escape(avail_short)} \\textperiodcentered{{}} {latex_escape(contract_label)}}}\\\\[0.15em]",
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

    warnings = cv_quality_issues(cv_title, accroche, role)
    fill = pdf_fill_ratio(cv_pdf_path) if cv_pdf_path else None
    if fill is not None and fill < CV_MIN_FILL_RATIO:
        warnings.append(f"CV page only {fill:.0%} filled (< {CV_MIN_FILL_RATIO:.0%})")
    preview_png = render_preview_png(cv_pdf_path) if cv_pdf_path else None
    if warnings and not args.json:
        for w in warnings:
            print(f"[!] Quality: {w}")

    lm_tex = build_lm_tex(
        profile, company, role, selected_projects, matched_keywords, job_text=job_text, archetype=archetype
    )
    lm_tex_path.write_text(lm_tex, encoding="utf-8")
    lm_pdf_path, lm_log = compile_tex(lm_tex_path)
    _, lm_pages, _ = verify_pdf_one_page(lm_pdf_path, lm_log)

    logged = False
    refreshed = False
    if args.remake:
        refreshed, old_cv = refresh_application(
            args.url, company, role, cv_title, matched_keywords, cv_pdf_path, lm_pdf_path
        )
        if refreshed:
            remove_orphan_output(old_cv, target_dir)
    if not refreshed:
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
        "tracker_refreshed": refreshed,
        "company_is_generic": generic_company,
        "cv_fill_ratio": fill,
        "cv_preview_png": str(preview_png) if preview_png else "",
        "quality_warnings": warnings,
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
    if refreshed:
        print("Tracker Logged   : Updated existing row (remake)")
    else:
        print(f"Tracker Logged   : {'Yes (drafted)' if logged else 'Skipped (duplicate)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
