"""Pure, site-agnostic helpers that turn noisy scraped offer text into clean role/company strings.

Job boards (APEC, Hellowork, Indeed, ...) often expose a whole listing card as the "title"
(title + employer blurb + contract badges + "Il y a 12 jours"). Everything here is deterministic,
offline, and free of personal data so it is safe to reuse from scrapers and from `tailor.py`.
"""

import re
from typing import List

# Job boards / aggregators: never the real employer, never part of a role title.
BOARD_NAMES = (
    "apec",
    "hellowork",
    "indeed",
    "linkedin",
    "jobteaser",
    "meteojob",
    "welcome to the jungle",
    "wttj",
)

# Placeholder employers (anonymous recruiters, fallbacks) that must not be addressed by name.
GENERIC_COMPANY_NAMES = (
    "other",
    "entreprise cible",
    "recruteur anonyme",
    "anonyme",
    "confidentiel",
    "entreprise confidentielle",
    "postuler sur le site de l entreprise",
)

GENERIC_COMPANY_PROSE = "votre entreprise"

_GENDER_RE = re.compile(r"\(?\s*(?<![A-Za-z0-9])[HFMXN]\s*/\s*[HFMXN](?:\s*/\s*[HFMXN])?(?![A-Za-z0-9+#])\s*\)?", re.I)
_BOARD_RE = re.compile(r"\b(?:" + "|".join(re.escape(b) for b in BOARD_NAMES) + r")\b", re.I)
_TAIL_MARKERS_RE = re.compile(
    r"(?:\bIl y a \d+|\bPostuler\b|\bStage\s+(?:Minimum|\d+\s*€)|\bTélétravail\b|\bAucune expérience\b|…|\.\.\.)",
    re.I,
)
_TRAILING_LOCATION_RE = re.compile(r"(?:\s*[-–—|,]\s*[^-–—|,]*\d{2,5}[^-–—|,]*)+\s*$")
_BAD_TITLES = (
    "javascript is disabled", "enable javascript", "just a moment", "access denied",
    "page not found", "page introuvable",
)
_UI_LABELS = ("déjà vue", "deja vue", "nouveau", "sponsorisé", "promoted", "candidature simplifiée", "postuler")
_DANGLING_WORDS = {
    "de", "du", "des", "en", "et", "à", "a", "au", "aux", "chez", "pour", "la", "le", "les",
    "un", "une", "d", "l", "dans", "sur", "avec", "and", "of", "the", "in", "for",
}


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (name or "").lower())).strip()


def is_generic_company(name: str) -> bool:
    """True when `name` is empty, a job board, or an anonymous placeholder (not a real employer)."""
    n = _norm_name(name)
    return not n or n in BOARD_NAMES or n in GENERIC_COMPANY_NAMES


def company_prose(name: str) -> str:
    """Company name for letters; neutral wording when the employer is unknown."""
    return GENERIC_COMPANY_PROSE if is_generic_company(name) else name


def truncate_words(text: str, max_len: int) -> str:
    """Cut at a word boundary (never mid-word), dropping dangling connectors/separators."""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    if text[max_len] != " ":
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
    words = cut.rstrip(" -–—:|,/").split(" ")
    while len(words) > 1 and words[-1].lower().strip("'’") in _DANGLING_WORDS:
        words.pop()
    return " ".join(words).rstrip(" -–—:|,/'’")


_DOUBLET_RE = re.compile(r"(?<![\w])([^\W\d_]{4,})\s*[-–/]\s*([^\W\d_]{4,})(?![\w])")


def _collapse_gender_doublets(text: str) -> str:
    """'Développeur - Développeuse' / 'Développeuse / Développeur' -> shorter form."""
    pos = 0
    while True:
        m = _DOUBLET_RE.search(text, pos)
        if not m:
            return text
        a, b = m.group(1), m.group(2)
        n = 0
        for x, y in zip(a.lower(), b.lower(), strict=False):
            if x != y:
                break
            n += 1
        if n >= 5 and n >= min(len(a), len(b)) - 3:
            keep = a if len(a) <= len(b) else b
            text = text[: m.start()] + keep + text[m.end():]
            pos = m.start() + len(keep)
        else:
            pos = m.start(2)


def _looks_like_blob(raw: str) -> bool:
    g = _GENDER_RE.search(raw)
    return (
        bool(g and re.search(r"[^\W\d_]", raw[g.end():]))
        or len(raw) > 90
        or bool(re.search(r"\s{2,}", raw))
        or bool(_TAIL_MARKERS_RE.search(raw))
        or bool(_BOARD_RE.search(raw))
    )


def sanitize_role(raw: str, max_len: int = 70) -> str:
    """Return a short, clean job title, or '' if nothing title-like can be recovered.

    Handles full listing cards (title + employer + badges + description) by cutting at the
    gender marker (H/F), board name, "Il y a N jours", double-space or ellipsis, then strips
    trailing location/postcode segments and duplicated gendered forms.
    """
    s = (raw or "").replace("\u00a0", " ").strip()
    if not s or any(bad in s.lower() for bad in _BAD_TITLES):
        return ""
    blob = _looks_like_blob(s)
    if blob:
        s = re.split(r"\s{2,}", s, maxsplit=1)[0]
        cuts: List[int] = []
        g = _GENDER_RE.search(s)
        if g and g.start() > 0:
            cuts.append(g.start())
        for rx in (_BOARD_RE, _TAIL_MARKERS_RE):
            m = rx.search(s)
            if m and m.start() > 0:
                cuts.append(m.start())
        if cuts:
            s = s[: min(cuts)]
    s = _GENDER_RE.sub(" ", s)
    s = re.sub(r"\(\s*\)", " ", s)
    s = _TRAILING_LOCATION_RE.sub("", s.strip())
    s = _collapse_gender_doublets(s)
    s = re.sub(r"\s+", " ", s).strip(" -–—:|,/")
    # "Développeur Logiciel - Stage" -> "Développeur Logiciel" (the CV title re-adds "Stage")
    s = re.sub(r"\s*[-–—:|,]\s*(?:stage|stagiaire)\s*$", "", s, flags=re.I).strip(" -–—:|,/")
    if len(s) > max_len:
        seps = [m.start() for m in re.finditer(r"\s[-–—]\s|\s?:\s", s[: max_len + 1])]
        seps = [
            i for i in seps
            if len(re.sub(r"^(?:stage|stagiaire|internship)\b[\s\-–—:|]*", "", s[:i], flags=re.I)) >= 15
        ]
        if seps:
            s = s[: seps[-1]]
        elif len(s) > int(max_len * 1.3):
            return ""
        s = truncate_words(s, max_len)
    if s.count("(") > s.count(")"):
        s = s[: s.rindex("(")]
    s = s.strip(" -–—:|,/")
    letters = re.sub(r"^(?:stage|stagiaire)\b[\s\-–—:|]*", "", s, flags=re.I)
    return s if len(re.findall(r"[^\W\d_]", letters)) >= 3 else ""


def company_from_listing(preview: str) -> str:
    """Best-effort employer name from a listing card shaped like 'Title H/F <Company> - City - 75 ...'.

    Returns '' unless a short, digit-free segment follows the gender marker and is not a job board.
    """
    s = (preview or "").replace("\u00a0", " ")
    g = _GENDER_RE.search(s)
    if not g:
        return ""
    after = re.split(r"\s+[-–—|]\s+|\s{2,}", s[g.end():].strip(), maxsplit=1)[0].strip()
    if not after or not after[0].isalpha() or len(after.split()) > 6 or re.search(r"\d", after):
        return ""
    if any(ui in after.lower() for ui in _UI_LABELS):
        return ""
    return "" if is_generic_company(after) else after
