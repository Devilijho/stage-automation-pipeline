"""Offer scoring, BUT 2/3 & Paris/Saclay ranking, and PFE/Bac+5 filtering."""

import re
from typing import Any, Dict, List, Optional, Tuple

from core.config import PROFILE_PATH, load_preferences, load_profile

DEFAULT_QUERIES = [
    "stage informatique",
    "stage développeur",
    "stage cybersécurité",
    "stage réseaux systèmes",
]
DEFAULT_LEVEL_BOOST = ["but 2", "but 3", "bac+2", "bac+3", "3 mois"]

EXCLUDE_TITLE_TERMS = [
    "cdi",
    "alternance",
    "apprentissage",
    "senior",
    "lead ",
    "principal",
    "manager",
    "directeur",
]

FINAL_YEAR_TERMS = (
    "fin d'études",
    "fin d'etudes",
    "fin de cursus",
    "pfe",
    "bac+5",
    "bac +5",
)

BUT_FRIENDLY_TERMS = (
    "but 2",
    "but 3",
    "but2",
    "but3",
    "bac+2",
    "bac +2",
    "bac+3",
    "bac +3",
    "iut",
    "dut",
    "2ème année",
    "3ème année",
)


def search_cfg() -> Dict[str, Any]:
    return load_profile(PROFILE_PATH).get("search") or {}


def search_location_li() -> str:
    return search_cfg().get("location", "Paris, Île-de-France, France")


def search_location_hw() -> str:
    return search_cfg().get("location_short", "Paris")


def level_boost() -> List[str]:
    return [str(x).lower() for x in (search_cfg().get("level_boost") or DEFAULT_LEVEL_BOOST)]


def is_excluded_final_year_offer(title: str, description: str = "") -> bool:
    """Return True if offer targets 6-month PFE / Bac+5 without accepting Bac+2/3."""
    t_low = title.lower()
    d_low = description.lower()
    combined = f"{t_low} {d_low}"
    if any(ok in combined for ok in BUT_FRIENDLY_TERMS):
        return False
    if any(term in t_low for term in FINAL_YEAR_TERMS):
        return True
    if any(term in d_low for term in (*FINAL_YEAR_TERMS, "master 2", "élève-ingénieur")):
        return True
    return False


def classify_offer_quality(
    role: str,
    location: str = "",
    description: str = "",
    matched_keywords: str = "",
    url: str = "",
    prefs: Optional[Dict[str, List[str]]] = None,
) -> Tuple[str, int]:
    """Classify an offer into ('high' | 'medium' | 'low', score) using role, distance, work type, and level."""
    p = prefs if prefs is not None else load_preferences(PROFILE_PATH)
    r_low = (role or "").lower()
    l_low = (location or "").lower()
    d_low = (description or "").lower()
    u_low = (url or "").lower()
    kw_low = (matched_keywords or "").lower()
    loc_blob = f"{l_low} {u_low} {d_low[:800]}"

    score = 0
    if any(t in r_low for t in ("stage", "intern", "stagiaire")):
        score += 10

    if any(bad in r_low for bad in p.get("avoid_roles", [])):
        score -= 40
    elif any(good in r_low or good in kw_low for good in p.get("target_roles", [])):
        score += 30

    kw_count = len([k for k in re.split(r"[;,]", kw_low) if k.strip()])
    score += min(15, kw_count * 3)

    is_remote = any(w in f"{r_low} {l_low} {d_low}" for w in p.get("work_types", []))
    if is_remote:
        score += 15

    if any(loc in loc_blob for loc in p.get("preferred_locations", [])):
        score += 25
    elif any(loc in loc_blob for loc in p.get("idf_locations", [])):
        score += 12
    elif any(loc in f"{l_low} {u_low}" for loc in p.get("avoid_locations", [])) and not is_remote:
        score -= 30

    if any(lvl in f"{r_low} {d_low}" for lvl in level_boost()):
        score += 10
    elif any(lvl in f"{r_low} {d_low}" for lvl in ("bac+5", "bac +5", "master 2", "m2", "pfe")) and not any(
        ok in f"{r_low} {d_low}" for ok in ("bac+2", "bac +2", "bac+3", "bac +3", "but 2", "but 3")
    ):
        score -= 20

    tier = "high" if score >= 40 else ("medium" if score >= 15 else "low")
    return tier, score


def _term_hit(term: str, text: str) -> bool:
    """Word-start match (stems like 'comptab' work); short terms ('ia', 'web') need a full-word match."""
    t = term.strip().lower()
    if not t:
        return False
    end = r"(?![a-z0-9])" if len(t) <= 3 else ""
    return re.search(rf"(?<![a-z0-9]){re.escape(t)}{end}", text) is not None


def is_irrelevant_offer(
    title: str, description: str = "", prefs: Optional[Dict[str, List[str]]] = None
) -> bool:
    """True when the offer has no tech/target-role signal (e.g. 'Assistant administratif', 'Documentaliste').

    Driven entirely by `preferences.target_roles` / `avoid_roles` in profile.json (no hardcoded lists).
    Offers with a target term in the title are always kept; offers with fewer than 2 target-term hits in the description are dropped; descriptions too short to judge
    are kept unless the title hits `avoid_roles`.
    """
    p = prefs if prefs is not None else load_preferences(PROFILE_PATH)
    t_low = (title or "").lower()
    d_low = (description or "").lower()[:2500]
    targets = p.get("target_roles", [])
    if any(_term_hit(x, t_low) for x in targets):
        return False
    if any(_term_hit(x, t_low) for x in p.get("avoid_roles", [])):
        return True
    if len(d_low) < 200:
        return False
    # Stems (first 6 letters of long terms) so "développement"/"réseau" count for "développeur"/"réseaux".
    stems = {x[:6] if len(x) >= 7 else x for x in targets}
    return len({x for x in stems if _term_hit(x, d_low)}) < 2


def score_offer(title: str, location: str, description: str) -> int:
    t_low = title.lower()
    if any(bad in t_low for bad in ("page introuvable", "page not found", "just a moment", "access denied")):
        return -100
    if any(bad in t_low for bad in EXCLUDE_TITLE_TERMS) and "stage" not in t_low:
        return -100

    if is_irrelevant_offer(title, description):
        return -100

    # Filter out Bac+5 / PFE / Fin d'études / Fin de cursus unless Bac+2/3 or BUT is explicitly accepted
    if is_excluded_final_year_offer(title, description):
        return -50

    _, score = classify_offer_quality(title, location=location, description=description)
    return max(0, score)
