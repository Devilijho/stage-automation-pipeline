#!/usr/bin/env python3
"""
Spontaneous Local Tech Company Finder & Email Draft Generator (spontaneous_mail.py)

Queries the official open French government API (`recherche-entreprises.api.gouv.fr`)
to find IT/Software companies (NAF 62.01Z / 62.02A) near Bures-sur-Yvette / Orsay /
Saclay / Massy / Palaiseau, generates tailored CV + LM PDFs via `tailor.py`, and
creates ready-to-send `.eml` drafts + `mailto:` links for spontaneous applications.
"""

import argparse
import json
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import requests

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.config import OUTPUT_DIR, PROFILE_PATH, SCRIPTS_DIR, WORKSPACE, load_profile

PYTHON_BIN = sys.executable


def find_local_tech_companies(lat: float, lon: float, radius_km: int, limit: int):
    url = (
        f"https://recherche-entreprises.api.gouv.fr/near_point"
        f"?lat={lat}&long={lon}&radius={radius_km}"
        f"&activite_principale=62.01Z,62.02A&per_page={min(limit * 2, 25)}"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    companies = []
    for r in results:
        name = (r.get("nom_complet") or r.get("nom_raison_sociale") or "").strip().title()
        siege = r.get("siege", {})
        city = siege.get("libelle_commune", "Île-de-France")
        naf = r.get("activite_principale", "62.01Z")
        if name:
            companies.append({"name": name, "city": city, "naf": naf})
        if len(companies) >= limit:
            break
    return companies


def create_eml_draft(profile: dict, company: str, city: str, cv_pdf: Path, lm_pdf: Path) -> Path:
    p = profile["personal"]
    intern = profile["internship"]
    edu = profile["education"]
    subject = f"Candidature spontanée — Stage Développeur / Réseaux ({intern['start_date']} - {intern['end_date']}) — {p['full_name']}"
    body = (
        f"Bonjour,\n\n"
        f"Actuellement étudiant en {edu['degree']} à {edu['school']} ({edu['secondary_school']}), "
        f"je recherche un {intern['availability_full']}.\n\n"
        f"Basé à {p['city']} (à proximité de {city}), je maîtrise C/C++, TypeScript, Linux, Docker, Git et SQL, "
        f"et serais ravi de contribuer à vos projets techniques.\n\n"
        f"Vous trouverez ci-joint mon CV et ma lettre de motivation.\n\n"
        f"Cordialement,\n"
        f"{p['full_name']}\n"
        f"{p['phone']} | {profile['links']['github_url']}"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = p["email"]
    msg["To"] = f"recrutement@{company.lower().replace(' ', '')}.fr"
    msg.set_content(body)
    for pdf in (cv_pdf, lm_pdf):
        if pdf.exists():
            msg.add_attachment(
                pdf.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=pdf.name,
            )

    eml_path = cv_pdf.parent / f"Candidature_{cv_pdf.parent.name}.eml"
    eml_path.write_bytes(bytes(msg))
    mailto = f"mailto:?subject={quote(subject)}&body={quote(body)}"
    (cv_pdf.parent / "mailto_link.txt").write_text(mailto, encoding="utf-8")
    return eml_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Find local tech companies & build spontaneous .eml drafts")
    parser.add_argument("--lat", type=float, default=48.697, help="Latitude (default: Bures-sur-Yvette)")
    parser.add_argument("--lon", type=float, default=2.162, help="Longitude (default: Bures-sur-Yvette)")
    parser.add_argument("--radius", type=int, default=15, help="Radius in km")
    parser.add_argument("--limit", type=int, default=5, help="Number of local companies")
    args = parser.parse_args()

    profile = load_profile(PROFILE_PATH)
    print(f"[*] Searching local IT/Software companies within {args.radius}km of Bures-sur-Yvette / Saclay...")
    companies = find_local_tech_companies(args.lat, args.lon, args.radius, args.limit)
    print(f"[*] Found {len(companies)} local tech companies.")

    for idx, c in enumerate(companies, 1):
        print(f"\n[{idx}/{len(companies)}] {c['name']} ({c['city']})")
        job_desc = (
            f"Candidature spontanée stage informatique {c['name']} à {c['city']}. "
            f"Développement logiciel, TypeScript, C, C++, Linux, Docker, Git, SQL, API REST."
        )
        slug = "".join(ch if ch.isalnum() else "_" for ch in c["name"]).strip("_")
        res = subprocess.run(
            [
                PYTHON_BIN,
                str(SCRIPTS_DIR / "tailor.py"),
                "--url",
                f"spontaneous://{slug}",
                "--company",
                c["name"],
                "--role",
                "Stagiaire Développeur Informatique (Candidature Spontanée)",
                "--text",
                job_desc,
                "--json",
            ],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print(f"  [!] tailor.py failed: {res.stderr or res.stdout}")
            continue
        cv_pdf = None
        lm_pdf = None
        for line in reversed(res.stdout.splitlines()):
            s = line.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    meta = json.loads(s)
                    if meta.get("cv_pdf") and meta.get("lm_pdf"):
                        cv_pdf = Path(meta["cv_pdf"])
                        lm_pdf = Path(meta["lm_pdf"])
                        break
                except json.JSONDecodeError:
                    pass
        if not (cv_pdf and lm_pdf):
            for line in res.stdout.splitlines():
                if line.startswith("CV PDF"):
                    cv_pdf = Path(line.split(":", 1)[1].strip())
                elif line.startswith("LM PDF"):
                    lm_pdf = Path(line.split(":", 1)[1].strip())
        if cv_pdf and lm_pdf:
            eml = create_eml_draft(profile, c["name"], c["city"], cv_pdf, lm_pdf)
            print(f"  -> Draft .eml ready (double-click to open in Apple Mail): {eml}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
