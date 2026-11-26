"""Shared configuration, workspace paths, profile I/O, and custom rules."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

WORKSPACE = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = WORKSPACE / "config"
DATA_DIR = WORKSPACE / "data"
OUTPUT_DIR = WORKSPACE / "output"
SCRIPTS_DIR = WORKSPACE / "scripts"
TEMPLATES_DIR = WORKSPACE / "templates"
UI_DIR = WORKSPACE / "ui"
CHROME_PROFILE_DIR = WORKSPACE / ".chrome_profile"
DEBUG_DIR = WORKSPACE / "debug"

PROFILE_PATH = CONFIG_DIR / "profile.json"
MASTER_PROFILE_PATH = CONFIG_DIR / "master_profile.md"
APPS_CSV_PATH = DATA_DIR / "applications.csv"
UNANSWERED_QUESTIONS_PATH = DATA_DIR / "unanswered_questions.json"
LLM_CACHE_PATH = DATA_DIR / "llm_answer_cache.json"
MANUAL_CURRENT_PATH = DATA_DIR / "manual_current.json"
MANUAL_SKIP_PATH = DATA_DIR / ".manual_skip"

DEFAULT_PREFERENCES: Dict[str, List[str]] = {
    "target_roles": [
        "développeur", "developpeur", "software", "fullstack", "full-stack",
        "backend", "frontend", "web", "cybersécurité", "cybersecurite",
        "réseaux", "reseaux", "systèmes", "systemes", "devops", "data",
        "ia", "cloud", "linux", "informatique",
    ],
    "avoid_roles": [
        "paie", "ressources humaines", "comptab", "commercial", "vente",
        "juridique", "agronomie", "formulation", "achats", "marketing", "finance", "audit",
    ],
    "preferred_locations": [
        "bures", "orsay", "saclay", "palaiseau", "massy", "gif",
        "les ulis", "91", "essonne", "velizy", "vélizy", "versailles", "antony",
    ],
    "idf_locations": [
        "paris", "île-de-france", "ile-de-france", "nanterre", "courbevoie",
        "la défense", "la defense", "boulogne", "issy", "montrouge", "clichy",
        "levallois", "neuilly", "92", "93", "94", "78", "75",
    ],
    "work_types": ["télétravail", "teletravail", "remote", "hybride", "hybrid"],
    "avoid_locations": [
        "lyon", "toulouse", "marseille", "bordeaux", "nantes", "lille",
        "roubaix", "annecy", "saint-etienne", "saint-étienne", "canada",
        "strasbourg", "rennes", "nice", "montpellier", "grenoble", "la hague", "penly", "cholet",
    ],
}


def load_project_env(env_path: Path = WORKSPACE / ".env") -> None:
    """Load key=value pairs from project-local .env without touching global PC env."""
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                os.environ.setdefault(k, v)
    except OSError:
        pass


def load_profile(path: Path = PROFILE_PATH) -> Dict[str, Any]:
    """Read fresh profile JSON from disk, returning {} if missing."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_profile(profile: Dict[str, Any], path: Path = PROFILE_PATH) -> None:
    """Atomically write profile JSON to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(profile, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_preferences(path: Path = PROFILE_PATH) -> Dict[str, List[str]]:
    """Load offer scoring preferences fresh from profile.json merged with defaults."""
    profile = load_profile(path)
    raw = profile.get("preferences") or {}
    return {
        k: [str(x).lower() for x in (raw.get(k) or v)]
        for k, v in DEFAULT_PREFERENCES.items()
    }


def load_custom_rules(path: Path = PROFILE_PATH) -> List[Dict[str, str]]:
    """Load user-defined keyword->answer rules from config/profile.json."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("custom_rules", [])
        if not isinstance(rules, list):
            return []
        return [
            {"keyword": str(r.get("keyword", "")).strip().lower(), "answer": str(r.get("answer", "")).strip()}
            for r in rules
            if str(r.get("keyword", "")).strip() and str(r.get("answer", "")).strip()
        ]
    except Exception:
        return []


def match_custom_rule(question: str, custom_rules: List[Dict[str, str]]) -> str:
    """Return the answer for the first custom rule whose keyword matches question."""
    q_low = (question or "").lower()
    for rule in custom_rules:
        if rule["keyword"] in q_low:
            return rule["answer"]
    return ""
