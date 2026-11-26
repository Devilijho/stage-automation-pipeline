"""Runtime mode paths (`prod` vs `debug` sandbox) and isolated Law (Droit) test profile."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import (
    APPS_CSV_PATH,
    CHROME_PROFILE_DIR,
    DATA_DIR,
    DEBUG_DIR,
    LLM_CACHE_PATH,
    MASTER_PROFILE_PATH,
    OUTPUT_DIR,
    PROFILE_PATH,
    UNANSWERED_QUESTIONS_PATH,
    WORKSPACE,
    save_profile,
)
from core.offer_text import is_generic_company, sanitize_role
from core.storage import check_and_log_application, detect_source, normalize_url


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolved runtime paths for either production or the isolated debug sandbox."""

    mode: str
    is_debug: bool
    profile_path: Path
    master_profile_path: Path
    apps_csv_path: Path
    unanswered_questions_path: Path
    llm_cache_path: Path
    output_dir: Path
    chrome_profile_dir: Path
    report_html_path: Path
    empty_cv_pdf: Path
    empty_lm_pdf: Path


def get_runtime_paths(debug: bool = False, workspace: Path = WORKSPACE) -> WorkspacePaths:
    """Return production paths (`debug=False`) or isolated debug sandbox paths (`debug=True`)."""
    if not debug:
        return WorkspacePaths(
            mode="prod",
            is_debug=False,
            profile_path=PROFILE_PATH if workspace == WORKSPACE else workspace / "config" / "profile.json",
            master_profile_path=(
                MASTER_PROFILE_PATH if workspace == WORKSPACE else workspace / "config" / "master_profile.md"
            ),
            apps_csv_path=APPS_CSV_PATH if workspace == WORKSPACE else workspace / "data" / "applications.csv",
            unanswered_questions_path=(
                UNANSWERED_QUESTIONS_PATH
                if workspace == WORKSPACE
                else workspace / "data" / "unanswered_questions.json"
            ),
            llm_cache_path=LLM_CACHE_PATH if workspace == WORKSPACE else workspace / "data" / "llm_answer_cache.json",
            output_dir=OUTPUT_DIR if workspace == WORKSPACE else workspace / "output",
            chrome_profile_dir=CHROME_PROFILE_DIR if workspace == WORKSPACE else workspace / ".chrome_profile",
            report_html_path=(
                DATA_DIR / "rapport_candidatures.html"
                if workspace == WORKSPACE
                else workspace / "data" / "rapport_candidatures.html"
            ),
            empty_cv_pdf=(DEBUG_DIR if workspace == WORKSPACE else workspace / "debug") / "sandbox" / "empty_cv.pdf",
            empty_lm_pdf=(DEBUG_DIR if workspace == WORKSPACE else workspace / "debug") / "sandbox" / "empty_lm.pdf",
        )

    sandbox_root = (DEBUG_DIR if workspace == WORKSPACE else workspace / "debug") / "sandbox"
    chrome_dir = CHROME_PROFILE_DIR if workspace == WORKSPACE else workspace / ".chrome_profile"
    return WorkspacePaths(
        mode="debug",
        is_debug=True,
        profile_path=sandbox_root / "profile.json",
        master_profile_path=sandbox_root / "master_profile.md",
        apps_csv_path=sandbox_root / "applications.csv",
        unanswered_questions_path=sandbox_root / "unanswered_questions.json",
        llm_cache_path=sandbox_root / "llm_answer_cache.json",
        output_dir=sandbox_root / "output",
        chrome_profile_dir=chrome_dir,
        report_html_path=sandbox_root / "rapport_candidatures.html",
        empty_cv_pdf=sandbox_root / "empty_cv.pdf",
        empty_lm_pdf=sandbox_root / "empty_lm.pdf",
    )


DEBUG_DEFAULT_PROFILE: Dict[str, Any] = {
    "personal": {
        "first_name": "Camille",
        "last_name": "MOREAU",
        "full_name": "Camille MOREAU",
        "email": "camille.moreau.debug@example.com",
        "phone": "+33 6 11 22 33 44",
        "location": "Paris, France",
        "city": "Paris",
        "postal_code": "75005",
        "country": "France",
    },
    "links": {
        "github_username": "",
        "github_url": "",
        "linkedin_url": "https://www.linkedin.com/in/camille-moreau-droit-debug",
    },
    "education": {
        "school": "Université Paris-Panthéon-Assas",
        "degree": "Licence 3 Droit",
        "track": "Parcours Droit privé & Droit des affaires",
        "level": "Bac+3 / Licence 3",
        "secondary_school": "Clinique Juridique Paris II",
        "graduation_year": "2027",
    },
    "experience": {
        "currently_working": True,
        "start_month": "September",
        "start_year": "2024",
    },
    "internship": {
        "year": "2026",
        "start_date": "04/05",
        "end_date": "24/07",
        "start_date_fr": "4 mai",
        "end_date_fr": "24 juillet",
        "duration": "3 mois",
        "contract_label": "Stage conventionné (Université Paris-Panthéon-Assas)",
        "contract_note": "stage conventionné",
        "availability_short": "Du 04/05 au 24/07 (3 mois)",
        "availability_full": "Stage de 3 mois du 4 mai au 24 juillet, avec convention de stage universitaire",
        "priority_repos": [],
    },
    "cover_letter": {
        "experience_sentence": "Mon engagement à la Clinique Juridique m'a formé à l'analyse de dossiers et à la rédaction d'actes.",
        "strengths_sentence": "ma rigueur rédactionnelle, mon esprit de synthèse et ma maîtrise des bases de données juridiques",
    },
    "search": {
        "queries": [
            "stage droit",
            "stage juriste",
            "stage cabinet avocats",
            "stage droit des affaires",
        ],
        "location": "Paris, Île-de-France, France",
        "location_short": "Paris",
        "location_boost": ["paris", "île-de-france", "nanterre", "la défense"],
        "level_boost": ["l3", "licence 3", "bac+3", "m1", "master 1", "3 mois"],
    },
    "commit_authors": [],
    "form_answers": {
        "prenom": "Camille",
        "nom": "MOREAU",
        "nom_complet": "Camille MOREAU",
        "email": "camille.moreau.debug@example.com",
        "telephone": "+33 6 11 22 33 44",
        "ville": "Paris",
        "pays": "France",
        "ecole": "Université Paris-Panthéon-Assas",
        "niveau": "Bac+3 / Licence 3",
        "formation": "Licence 3 Droit",
        "disponibilite": "Du 04/05 au 24/07 (3 mois)",
        "duree": "3 mois",
        "github": "",
        "linkedin": "https://www.linkedin.com/in/camille-moreau-droit-debug",
        "autorisation_travail": "Oui",
        "mobilite": "Paris / Île-de-France",
    },
    "custom_rules": [
        {"keyword": "permis b", "answer": "Non"},
        {"keyword": "crfpa", "answer": "Non"},
        {"keyword": "efp", "answer": "Non"},
    ],
    "preferences": {
        "target_roles": [
            "droit",
            "juriste",
            "juridique",
            "avocat",
            "legal",
            "conformité",
            "compliance",
            "contrats",
            "rgpd",
            "notaire",
            "contentieux",
            "fiscal",
            "social",
        ],
        "avoid_roles": [
            "développeur",
            "developpeur",
            "devops",
            "informatique",
            "commercial",
            "vente",
            "comptab",
            "manutention",
        ],
        "preferred_locations": [
            "paris",
            "75",
            "nanterre",
            "la défense",
            "la defense",
            "boulogne",
            "issy",
            "neuilly",
            "levallois",
        ],
        "idf_locations": [
            "île-de-france",
            "ile-de-france",
            "92",
            "93",
            "94",
            "91",
            "78",
            "versailles",
            "créteil",
        ],
        "work_types": ["télétravail", "teletravail", "remote", "hybride", "hybrid"],
        "avoid_locations": [
            "lyon",
            "toulouse",
            "marseille",
            "bordeaux",
            "nantes",
            "lille",
            "strasbourg",
            "rennes",
            "nice",
            "montpellier",
            "grenoble",
        ],
    },
}


DEBUG_DEFAULT_MASTER_MD = """# Profil Maître Debug — Étudiant(e) en Droit

> Profil fictif généré pour le mode Debug Sandbox (tests de recherche, formulaire & Q&A hors domaine IT).

## Formation Académique
- **Université Paris-Panthéon-Assas** — Licence 3 Droit (Parcours Droit privé & Droit des affaires)
- Matières principales : Droit des obligations & des contrats, Droit des sociétés, Droit du travail, Droit public, Procédure civile, Protection des données (RGPD)

## Expériences & Activités Juridiques
- **Clinique Juridique Paris II** — Rédaction de notes de synthèse, recherche de jurisprudence et accueil d'usagers
- **Concours de plaidoirie (Moot Court)** — Argumentation écrite et orale en droit des contrats

## Outils & Langues
- **Bases juridiques** : Dalloz, LexisNexis, Lamyline, Légifrance, Doctrine
- **Bureautique** : Word, Excel, PowerPoint
- **Langues** : Français (natif), Anglais (C1 — Legal English)
"""


def build_minimal_blank_pdf_bytes() -> bytes:
    """Build a valid minimal 1-page blank PDF without external LaTeX dependencies."""
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>\nendobj\n",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(body))
        body.extend(obj)
    xref_pos = len(body)
    xref = [f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii")]
    for off in offsets[1:]:
        xref.append(f"{off:010d} 00000 n \n".encode("ascii"))
    trailer = f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    return bytes(body) + b"".join(xref) + trailer


def create_minimal_blank_pdf(path: Path) -> Path:
    """Write a valid blank 1-page PDF to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_minimal_blank_pdf_bytes())
    return path


def ensure_debug_sandbox(paths: WorkspacePaths) -> None:
    """Idempotently seed the debug sandbox directory, Droit profile, and empty CV/LM PDFs."""
    if not paths.is_debug:
        return
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.apps_csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not paths.profile_path.exists():
        save_profile(DEBUG_DEFAULT_PROFILE, paths.profile_path)
    if not paths.master_profile_path.exists():
        paths.master_profile_path.write_text(DEBUG_DEFAULT_MASTER_MD, encoding="utf-8")
    if not paths.empty_cv_pdf.exists():
        create_minimal_blank_pdf(paths.empty_cv_pdf)
    if not paths.empty_lm_pdf.exists():
        create_minimal_blank_pdf(paths.empty_lm_pdf)


def _slugify(text: str, fallback: str = "offer") -> str:
    norm = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", norm).strip("_")
    return slug[:60] or fallback


def _relativize(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def create_debug_offer_metadata(
    offer: Dict[str, str],
    paths: WorkspacePaths,
    workspace: Path = WORKSPACE,
) -> Optional[Path]:
    """Log a debug offer to the sandbox CSV with empty CV/LM PDFs and write its metadata.json."""
    ensure_debug_sandbox(paths)
    url = offer.get("url") or ""
    raw_company = (offer.get("company") or "").strip()
    raw_role = (offer.get("role") or offer.get("preview") or "").strip()
    role = sanitize_role(raw_role) or raw_role or "Stage Juridique"
    company = raw_company if not is_generic_company(raw_company) else detect_source(url)
    if not company:
        company = "Cabinet_Debug"

    logged = check_and_log_application(
        company=company,
        role=role,
        url=url,
        cv_title="DEBUG (Empty CV)",
        matched_keywords=[],
        cv_pdf=paths.empty_cv_pdf,
        lm_pdf=paths.empty_lm_pdf,
        path=paths.apps_csv_path,
        workspace=workspace,
    )
    if not logged:
        return None

    url_hash = hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:8]
    folder = paths.output_dir / f"{_slugify(company)}_{url_hash}"
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = folder / "metadata.json"
    meta_path.write_text(
        json.dumps(
            {
                "company": company,
                "role": role,
                "url": url,
                "cv_title": "DEBUG (Empty CV)",
                "cv_pdf": _relativize(paths.empty_cv_pdf, workspace),
                "lm_pdf": _relativize(paths.empty_lm_pdf, workspace),
                "debug": True,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return meta_path


def resolve_offer_metadata_path(
    row: Dict[str, str],
    paths: WorkspacePaths,
    workspace: Path = WORKSPACE,
) -> Optional[Path]:
    """Locate (or synthesize in debug mode) the metadata.json path for a tracker row."""
    url = (row.get("URL") or "").strip()
    cv_pdf = (row.get("CV_PDF") or "").strip()

    if not paths.is_debug:
        if not cv_pdf:
            return None
        mp = workspace / Path(cv_pdf).parent / "metadata.json"
        return mp if mp.exists() else None

    ensure_debug_sandbox(paths)
    norm = normalize_url(url)
    if norm and paths.output_dir.exists():
        for mp in paths.output_dir.glob("*/metadata.json"):
            try:
                data = json.loads(mp.read_text(encoding="utf-8"))
                if normalize_url(data.get("url") or "") == norm:
                    return mp
            except Exception:
                continue

    if not norm:
        return None
    company = (row.get("Company") or detect_source(url) or "Debug").strip()
    role = (row.get("Role") or "Stage").strip()
    url_hash = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    folder = paths.output_dir / f"{_slugify(company)}_{url_hash}"
    folder.mkdir(parents=True, exist_ok=True)
    mp = folder / "metadata.json"
    mp.write_text(
        json.dumps(
            {
                "company": company,
                "role": role,
                "url": url,
                "cv_title": row.get("CV_Title_Used") or "DEBUG (Empty CV)",
                "cv_pdf": _relativize(paths.empty_cv_pdf, workspace),
                "lm_pdf": _relativize(paths.empty_lm_pdf, workspace),
                "debug": True,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return mp
