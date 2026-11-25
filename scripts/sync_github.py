#!/usr/bin/env python3
"""
GitHub Project Indexer (sync_github.py)

Connects to GitHub via the `gh` CLI to inspect all owned, private, and
collaborative repositories. For collaborative repositories, inspects
`git log --author=...` so only verified personal contributions are attributed.
Combines GitHub findings with `base_cv.tex` and `curriculum_but2_b.md` (if
present) to generate `master_profile.md`.
"""

import argparse
import base64
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE = Path(__file__).resolve().parent.parent
PROFILE_PATH = WORKSPACE / "config" / "profile.json"
BASE_CV_PATH = WORKSPACE / "templates" / "base_cv.tex"
CURRICULUM_PATH = WORKSPACE / "config" / "curriculum_but2_b.md"
MASTER_PROFILE_PATH = WORKSPACE / "config" / "master_profile.md"
CACHE_DIR = WORKSPACE / ".cache" / "repos"


def run_cmd(
    cmd: List[str], cwd: Optional[Path] = None, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=check,
    )


def gh_api_json(endpoint: str) -> Any:
    res = run_cmd(["gh", "api", endpoint], check=False)
    if res.returncode != 0:
        raise RuntimeError(f"gh api {endpoint} failed: {res.stderr.strip()}")
    return json.loads(res.stdout)


def load_profile() -> Dict[str, Any]:
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(f"Missing {PROFILE_PATH}")
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def fetch_all_repos() -> List[Dict[str, Any]]:
    repos: List[Dict[str, Any]] = []
    page = 1
    while True:
        batch = gh_api_json(
            f"/user/repos?affiliation=owner,collaborator,organization_member&per_page=100&page={page}&sort=updated"
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def fetch_readme(full_name: str) -> str:
    try:
        data = gh_api_json(f"/repos/{full_name}/readme")
        content = data.get("content", "")
        if content:
            return base64.b64decode(content).decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


def fetch_languages(full_name: str) -> Dict[str, int]:
    try:
        return gh_api_json(f"/repos/{full_name}/languages")
    except Exception:
        return {}


def fetch_contributors(full_name: str) -> List[Dict[str, Any]]:
    try:
        data = gh_api_json(f"/repos/{full_name}/contributors?per_page=30")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def sync_and_inspect_git_contributions(
    full_name: str, authors: List[str]
) -> Tuple[bool, int, List[str], List[str]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = full_name.replace("/", "__")
    repo_dir = CACHE_DIR / safe_name

    if not repo_dir.exists():
        res = run_cmd(
            ["gh", "repo", "clone", full_name, str(repo_dir), "--", "--filter=blob:none", "--quiet"],
            check=False,
        )
        if res.returncode != 0:
            return (False, 0, [], [])
    else:
        run_cmd(["git", "fetch", "--all", "--quiet"], cwd=repo_dir, check=False)

    all_authors_res = run_cmd(
        ["git", "shortlog", "-sn", "--all"], cwd=repo_dir, check=False
    )
    author_lines = [l.strip() for l in all_authors_res.stdout.splitlines() if l.strip()]
    is_collab = len(author_lines) > 1

    seen_hashes = set()
    commit_subjects: List[str] = []
    file_counts: Dict[str, int] = {}

    for author in authors:
        log_res = run_cmd(
            [
                "git",
                "log",
                "--all",
                f"--author={author}",
                "-i",
                "--no-merges",
                "--pretty=format:%H\t%s",
                "--name-only",
            ],
            cwd=repo_dir,
            check=False,
        )
        if log_res.returncode != 0:
            continue

        current_hash = None
        for line in log_res.stdout.splitlines():
            if "\t" in line and len(line.split("\t", 1)[0]) == 40:
                h, subj = line.split("\t", 1)
                if h in seen_hashes:
                    current_hash = None
                    continue
                seen_hashes.add(h)
                current_hash = h
                if subj.strip():
                    commit_subjects.append(subj.strip())
            elif line.strip() and current_hash is not None:
                fpath = line.strip()
                file_counts[fpath] = file_counts.get(fpath, 0) + 1

    top_files = [
        f for f, _ in sorted(file_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    ]
    return (is_collab, len(seen_hashes), commit_subjects[:12], top_files)


def summarize_readme(readme_text: str, max_lines: int = 12) -> str:
    if not readme_text:
        return ""
    lines = []
    for raw in readme_text.splitlines():
        s = raw.strip()
        if not s or s.startswith("![") or s.startswith("[!["):
            continue
        s = re.sub(r"^#+\s*", "", s)
        lines.append(s)
        if len(lines) >= max_lines:
            break
    return " ".join(lines)[:500]


def extract_base_cv_sections() -> str:
    if not BASE_CV_PATH.exists():
        return "_`base_cv.tex` not found yet._"
    tex = BASE_CV_PATH.read_text(encoding="utf-8", errors="replace")
    body = tex.split(r"\begin{document}", 1)[-1].split(r"\end{document}", 1)[0]
    body = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", body)
    body = body.replace("{", "").replace("}", "")
    lines = [re.sub(r"\s+", " ", l).strip() for l in body.splitlines()]
    cleaned = "\n".join(l for l in lines if l)
    return cleaned[:3500]


def generate_master_profile(
    profile: Dict[str, Any],
    indexed_repos: List[Dict[str, Any]],
) -> str:
    p = profile["personal"]
    edu = profile["education"]
    intern = profile["internship"]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: List[str] = [
        "# Master Technical Profile",
        f"_Generated on {now_str} by `sync_github.py`_",
        "",
        "## 1. Candidate Identity & Availability",
        f"- **Name**: {p['full_name']}",
        f"- **Email**: {p['email']}",
        f"- **Phone**: {p['phone']}",
        f"- **Location**: {p['location']}",
        f"- **GitHub**: {profile['links']['github_url']}",
        f"- **LinkedIn**: {profile['links']['linkedin_url']}",
        f"- **Education**: {edu['degree']} — {edu['school']} ({edu['level']})",
        f"- **Additional Training**: {edu['secondary_school']}",
        f"- **Internship Availability**: {intern['availability_full']}",
        "",
    ]

    lines.append("## 2. Official Academic Background (BUT 2 Informatique — Parcours B)")
    if CURRICULUM_PATH.exists():
        lines.append(CURRICULUM_PATH.read_text(encoding="utf-8").strip())
    else:
        lines.extend([
            "_`curriculum_but2_b.md` was not found in the workspace. Add it to enrich this section._",
            "- **Core BUT 2 Parcours B (from CV)**: Réseaux avancés, Cryptographie, Sécurité, Administration et supervision des systèmes/réseaux, Modélisation (MCD/SR), SQL (Oracle/SQLite), Java, C/C++, Web (HTML/CSS/JS/TS).",
        ])
    lines.append("")

    lang_totals: Dict[str, int] = {}
    for r in indexed_repos:
        if r["my_commits"] > 0 or not r["is_fork"]:
            for lang, byte_count in r["languages"].items():
                lang_totals[lang] = lang_totals.get(lang, 0) + byte_count

    sorted_langs = [k for k, _ in sorted(lang_totals.items(), key=lambda kv: -kv[1])]
    lines.extend([
        "## 3. Verified Languages & Technologies (GitHub + CV)",
        f"- **GitHub Languages (by verified repo footprint)**: {', '.join(sorted_langs) if sorted_langs else 'N/A'}",
        "",
        "## 4. Verified GitHub Repositories & Personal Contributions",
        "> Only commits and files authored by the candidate (`git log --author=...`) are attributed in collaborative repositories.",
        "",
    ])

    for r in indexed_repos:
        badge = "Private" if r["private"] else "Public"
        collab_tag = "Collaborative" if r["is_collaborative"] else "Solo"
        prio_tag = " [PRIORITY]" if r["is_priority"] else ""
        langs_str = ", ".join(list(r["languages"].keys())[:6]) or "Unspecified"

        lines.extend([
            f"### {r['full_name']}{prio_tag}",
            f"- **Visibility / Mode**: {badge} · {collab_tag}" + (" · Fork" if r["is_fork"] else ""),
            f"- **URL**: {r['html_url']}",
            f"- **Description**: {r['description'] or 'No description provided'}",
            f"- **Languages**: {langs_str}",
            f"- **Verified Personal Commits**: {r['my_commits']}",
        ])
        if r["topics"]:
            lines.append(f"- **Topics**: {', '.join(r['topics'])}")
        if r["top_files"]:
            lines.append(f"- **Files Personally Touched**: `{'`, `'.join(r['top_files'][:10])}`")
        if r["commit_subjects"]:
            lines.append("- **Sample Verified Contributions (git log)**:")
            for subj in r["commit_subjects"][:6]:
                lines.append(f"  - {subj}")
        if r["readme_summary"]:
            lines.append(f"- **README Context**: {r['readme_summary']}")
        lines.append("")

    lines.extend([
        "## 5. Verified Baseline CV Content (`base_cv.tex`)",
        "```text",
        extract_base_cv_sections(),
        "```",
        "",
    ])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Index GitHub repos into master_profile.md")
    parser.add_argument("--skip-clone", action="store_true", help="Skip git clone/log inspection")
    parser.add_argument("--include-forks", action="store_true", help="Include forked repos with 0 personal commits")
    args = parser.parse_args()

    profile = load_profile()
    authors = profile.get("commit_authors", [profile["links"]["github_username"]])
    priority_names = {n.lower() for n in profile.get("internship", {}).get("priority_repos", [])}

    print("[*] Fetching repositories from GitHub via gh CLI...")
    raw_repos = fetch_all_repos()
    print(f"[*] Found {len(raw_repos)} accessible repositories.")

    indexed: List[Dict[str, Any]] = []
    for repo in raw_repos:
        full_name = repo["full_name"]
        name_lower = repo["name"].lower()
        is_priority = any(p in name_lower for p in priority_names)

        if repo.get("fork") and not is_priority and not args.include_forks and args.skip_clone:
            continue

        print(f"  -> Inspecting {full_name}...")
        languages = fetch_languages(full_name)
        readme = fetch_readme(full_name)
        contributors = fetch_contributors(full_name)

        is_collab = len(contributors) > 1
        my_commits = 0
        subjects: List[str] = []
        top_files: List[str] = []

        if not args.skip_clone:
            c_collab, my_commits, subjects, top_files = sync_and_inspect_git_contributions(
                full_name, authors
            )
            is_collab = is_collab or c_collab

        if repo.get("fork") and my_commits == 0 and not is_priority and not args.include_forks:
            continue

        indexed.append({
            "name": repo["name"],
            "full_name": full_name,
            "html_url": repo["html_url"],
            "description": repo.get("description") or "",
            "private": bool(repo.get("private")),
            "is_fork": bool(repo.get("fork")),
            "is_collaborative": is_collab,
            "is_priority": is_priority,
            "updated_at": repo.get("updated_at") or "",
            "topics": repo.get("topics") or [],
            "languages": languages,
            "my_commits": my_commits,
            "commit_subjects": subjects,
            "top_files": top_files,
            "readme_summary": summarize_readme(readme),
        })

    indexed.sort(key=lambda r: r["updated_at"], reverse=True)
    indexed.sort(key=lambda r: (not r["is_priority"], r["my_commits"] == 0))

    md_content = generate_master_profile(profile, indexed)
    MASTER_PROFILE_PATH.write_text(md_content, encoding="utf-8")
    print(f"[+] Wrote {MASTER_PROFILE_PATH} with {len(indexed)} verified repositories.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
