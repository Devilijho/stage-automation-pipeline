"""Grounded question classification and verified master_profile.md skill matching."""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.config import MASTER_PROFILE_PATH, UNANSWERED_QUESTIONS_PATH

# Known multi-word or symbolic skills to match safely against config/master_profile.md
_PROFILE_SKILL_CATALOG = [
    "c++", "c#", "typescript", "javascript", "python", "java", "sql", "oracle",
    "sqlite", "postgresql", "plpgsql", "html", "html5", "css", "css3", "php",
    "shell", "bash", "gdscript", "docker", "linux", "unix", "git", "react",
    "node.js", "three.js", "vite", "supabase", "valgrind", "gdb",
    "réseaux", "réseau", "network", "networking", "cryptographie", "sécurité",
    "cybersécurité", "cybersecurity", "http", "tcp", "frontend", "backend",
    "base de données", "application web", "devops", "eclipse", "virtualbox",
]


def load_saved_unanswered_answers(path: Path = UNANSWERED_QUESTIONS_PATH) -> Dict[str, str]:
    """Load user-filled answers from data/unanswered_questions.json keyed by normalized question."""
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return {}
        return {
            (e.get("question") or "").strip().lower(): str(e.get("answer")).strip()
            for e in entries
            if (e.get("question") or "").strip() and str(e.get("answer") or "").strip()
        }
    except Exception:
        return {}


def log_unanswered_question(
    field_type: str,
    question: str,
    options: List[str],
    company: str = "",
    url: str = "",
    path: Path = UNANSWERED_QUESTIONS_PATH,
) -> None:
    """Manual unanswered_questions.json logging disabled in favor of cached LLM auto-answer."""
    return


def load_master_profile_skills(path: Path = MASTER_PROFILE_PATH) -> Set[str]:
    """Extract verified skills and domains present in config/master_profile.md."""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    verified: Set[str] = set()
    for skill in _PROFILE_SKILL_CATALOG:
        if len(skill) <= 3 and skill.isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", text):
                verified.add(skill)
        elif skill in text:
            verified.add(skill)
    # Full-stack web evidence from verified repos (chaski-booking, Transcendence, portfolio-iut, webserv)
    if any(k in text for k in ("typescript", "react", "plpgsql", "supabase", "html", "php")):
        verified.update({"frontend", "backend", "base de données", "application web", "fullstack"})
    return verified


def classify_yes_no_question(question: str, verified_skills: Set[str]) -> Optional[Tuple[str, ...]]:
    """Return ('Non', 'No') or ('Oui', 'Yes') for a form question grounded in master_profile.md."""
    q = question.lower()

    # 1. Explicit "No" cases (visa/disability, higher degree than BUT 2, or unverified domains like mobile)
    if any(k in q for k in ("visa", "sponsor", "parrainage", "handicap", "disability")):
        return ("Non", "No")
    if any(k in q for k in ("fonction publique", "public official", "politically exposed", "membre proche de votre famille")):
        return ("Non", "No")
    if any(k in q for k in ("receive text", "sms messages", "communities for future")):
        return ("Non", "No")
    if any(k in q for k in (
        "privacy notice", "privacy policy", "read and agree", "politique de confidentialit",
        "lu et accept", "allowing", "contact me", "me contacter", "rgpd", "gdpr",
        "données personnelles", "personal data", "data processing", "traitement de mes données",
        "consent", "i agree", "j'accepte", "j’accepte",
    )):
        return ("Oui", "Yes")
    if any(k in q for k in ("bac+5", "bac +5", "master 2", "m2", "fin d'études", "fin d'étude", "pfe")):
        if not any(k in q for k in ("bac+2", "bac +2", "bac+3", "bac +3", "but 2", "but 3")):
            return ("Non", "No")
    if any(k in q for k in (
        "développement mobile", "dev mobile", "mobile dev", "application mobile",
        "applications mobiles", "android", "ios", "flutter", "react native", "kotlin", "swift",
    )):
        return ("Non", "No")

    # 2. Recruitment events & AI coding tools
    if any(k in q for k in ("événement", "événements", "invitation", "formation de code")):
        return ("Oui", "Yes")
    if any(k in q for k in ("outils d'ia", "outil d'ia", "claude", "cursor", "copilot", "chatgpt", "ia pour développer", "ai tools")):
        return ("Oui", "Yes")

    # 3. Verified full-stack / web / cursus / work authorization questions
    if any(k in q for k in (
        "application web", "frontend", "backend", "base de donnée", "base de données",
        "fullstack", "full-stack", "full stack",
    )):
        if "application web" in verified_skills or "frontend" in verified_skills:
            return ("Oui", "Yes")

    if any(k in q for k in (
        "autoris", "légale", "work", "france", "stage", "convention", "disponib",
        "majeur", "bac+2", "bac +2", "bac+3", "bac +3", "but 2", "but2",
        "hybride", "présentiel", "remote", "télétravail", "english", "french",
        "français", "anglais", "degree", "diplôme", "projet académique", "projets académiques",
    )):
        return ("Oui", "Yes")

    # 4. Dynamic match against verified skills from config/master_profile.md
    for skill in verified_skills:
        if len(skill) <= 3 and skill.isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", q):
                return ("Oui", "Yes")
        elif skill in q:
            return ("Oui", "Yes")

    return None
