"""Shared locked LLM answer cache and multi-backend LLM helpers."""

import fcntl
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.config import LLM_CACHE_PATH


def _cache_lock_path(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


def load_llm_cache(path: Path = LLM_CACHE_PATH) -> Dict[str, str]:
    """Read cached LLM field answers under a shared file lock."""
    if not path.exists():
        return {}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _cache_lock_path(path).open("a+", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_SH)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except Exception:
        return {}


def _write_cache_atomic(path: Path, data: Dict[str, str]) -> None:
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def save_llm_cache(cache: Dict[str, str], path: Path = LLM_CACHE_PATH) -> None:
    """Atomically persist cached LLM answers under an exclusive file lock."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _cache_lock_path(path).open("a+", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                merged: Dict[str, str] = {}
                if path.exists():
                    try:
                        existing = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(existing, dict):
                            merged.update(existing)
                    except Exception:
                        pass
                merged.update(cache)
                _write_cache_atomic(path, merged)
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except Exception:
        pass


def replace_llm_cache(cache: Dict[str, str], path: Path = LLM_CACHE_PATH) -> None:
    """Atomically replace the entire LLM cache dictionary under an exclusive file lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _cache_lock_path(path).open("a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            _write_cache_atomic(path, cache)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def delete_llm_cache_entry(key: str, path: Path = LLM_CACHE_PATH) -> bool:
    """Delete a single entry from the LLM answer cache under an exclusive lock."""
    if not path.exists() or not key:
        return False
    with _cache_lock_path(path).open("a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or key not in data:
                return False
            del data[key]
            _write_cache_atomic(path, data)
            return True
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def call_gemini(
    prompt: str,
    *,
    timeout: int = 15,
    max_output_tokens: Optional[int] = None,
    json_mode: bool = False,
) -> Tuple[str, str]:
    """Call Gemini REST API with model fallback; returns (response_text, model_name)."""
    g_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not g_key:
        return "", ""
    g_models = [os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip(), "gemini-3.5-flash"]
    gen_cfg: Dict[str, Any] = {"temperature": 0.0}
    if max_output_tokens is not None:
        gen_cfg["maxOutputTokens"] = max_output_tokens
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"

    for g_model in dict.fromkeys(m for m in g_models if m):
        try:
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{g_model}:generateContent?key={g_key}",
                data=json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": gen_cfg,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
                raw = (
                    body.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                if raw:
                    return raw, g_model
        except Exception as e:
            err_txt = str(e)
            if "404" in err_txt:
                continue
            print(f"  [!] Gemini API error ({g_model}): {err_txt[:120]}")
            break
    return "", ""


def ask_llm_for_field(
    field_type: str,
    question: str,
    options: List[str],
    answers: Dict[str, str],
    verified_skills: Set[str],
    *,
    cache_path: Path = LLM_CACHE_PATH,
) -> str:
    """Lightweight cached LLM fallback for required unknown form questions.

    Auto-selects first available backend:
      1. Local Ollama (http://127.0.0.1:11434, 100% free / 0 tokens)
      2. Gemini API free tier (GEMINI_API_KEY)
      3. Groq API free tier (GROQ_API_KEY -> llama-3.1-8b-instant)
      4. OpenAI API (OPENAI_API_KEY -> gpt-4o-mini)
    """
    q_clean = re.sub(r"\s+", " ", (question or "")).strip()
    if not q_clean:
        return ""
    cache_key = f"{field_type}|{q_clean.lower()}|{'||'.join(options[:20])}"
    cache = load_llm_cache(cache_path)
    if cache_key in cache:
        return cache[cache_key]

    default_school = "IUT d'Orsay"
    facts = (
        f"Candidate: {answers.get('nom_complet', 'Santiago FUENTES LLANOS')}, "
        f"Education: {answers.get('formation', 'BUT 2 Informatique')} ({answers.get('ecole', default_school)}) & École 42 Paris, "
        f"Level: {answers.get('niveau', 'Bac+2 / BUT 2')}, "
        f"City: {answers.get('ville', 'Bures-sur-Yvette')} (91440, Essonne, Île-de-France), "
        f"Internship: {answers.get('disponibilite', '03/05 to 16/07, 3 months, extendable summer')}, "
        f"Work auth France: {answers.get('autorisation_travail', 'Oui')}, Visa sponsorship needed: Non, "
        f"Languages: Spanish (native), French (C1), English (C1), "
        f"Verified skills: {', '.join(sorted(verified_skills)[:25])}."
    )
    opt_line = f"\nAllowed options (pick ONE verbatim): {options[:25]}" if options else ""
    prompt = (
        f"You fill job application forms strictly grounded in these candidate facts:\n{facts}\n\n"
        f"Field type: {field_type}\nQuestion: {q_clean}{opt_line}\n"
        f"Rules: Reply with ONLY the concise answer value (no quotes, no explanation). "
        f"If options are listed, return ONE exact option string verbatim. Never invent skills. "
        f"If unsure, reply EMPTY."
    )

    provider = ""
    raw_ans = ""

    # 1. Local Ollama (free, unlimited)
    try:
        ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps({"model": ollama_model, "prompt": prompt, "stream": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            body = json.loads(r.read().decode("utf-8"))
            raw_ans = (body.get("response") or "").strip()
            if raw_ans:
                provider = f"ollama:{ollama_model}"
    except Exception:
        pass

    # 2. Gemini API free tier
    if not raw_ans and os.environ.get("GEMINI_API_KEY"):
        raw_ans, g_model = call_gemini(prompt, timeout=10, max_output_tokens=40)
        if raw_ans:
            provider = f"gemini:{g_model}"

    # 3. Groq / OpenAI compatible
    if not raw_ans and (os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        try:
            use_groq = bool(os.environ.get("GROQ_API_KEY"))
            api_key = (os.environ.get("GROQ_API_KEY") if use_groq else os.environ.get("OPENAI_API_KEY") or "").strip()
            endpoint = (
                "https://api.groq.com/openai/v1/chat/completions"
                if use_groq
                else "https://api.openai.com/openai/v1/chat/completions"
            )
            model = "llama-3.1-8b-instant" if use_groq else "gpt-4o-mini"
            req = urllib.request.Request(
                endpoint,
                data=json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 40,
                    "temperature": 0.0,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                body = json.loads(r.read().decode("utf-8"))
                raw_ans = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if raw_ans:
                    provider = f"{'groq' if use_groq else 'openai'}:{model}"
        except Exception:
            pass

    ans = raw_ans.splitlines()[0].strip(" \"'`") if raw_ans else ""
    if not ans or ans.upper() == "EMPTY":
        return ""
    if options:
        exact = next((o for o in options if o.lower() == ans.lower()), None)
        if not exact:
            exact = next((o for o in options if ans.lower() in o.lower() or o.lower() in ans.lower()), None)
        if not exact:
            return ""
        ans = exact

    cache[cache_key] = ans
    save_llm_cache({cache_key: ans}, cache_path)
    print(f"  [LLM:{provider}] {q_clean} -> {ans}")
    return ans
