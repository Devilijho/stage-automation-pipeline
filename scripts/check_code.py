#!/usr/bin/env python3
"""Static checks (py_compile, AST undefined-name scan, optional ruff) + unit tests."""

import ast
import builtins
import py_compile
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
TESTS_DIR = ROOT_DIR / "tests"

BUILTIN_NAMES = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}


def iter_python_files() -> List[Path]:
    files: List[Path] = []
    for base in (SCRIPTS_DIR, TESTS_DIR):
        if base.exists():
            files.extend(sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts))
    return files


def check_syntax_and_compile(files: List[Path]) -> List[str]:
    errors: List[str] = []
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"py_compile failed for {path.relative_to(ROOT_DIR)}: {exc.msg}")
    return errors


def check_undefined_names(files: List[Path]) -> List[str]:
    """Scan AST for Load names that are never bound anywhere in the module."""
    errors: List[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined = set(BUILTIN_NAMES)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    defined.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
                for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                    defined.add(arg.arg)
                if node.args.vararg:
                    defined.add(node.args.vararg.arg)
                if node.args.kwarg:
                    defined.add(node.args.kwarg.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
            elif isinstance(node, ast.comprehension):
                for t in ast.walk(node.target):
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
            elif isinstance(node, ast.Lambda):
                for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                    defined.add(arg.arg)
                if node.args.vararg:
                    defined.add(node.args.vararg.arg)
                if node.args.kwarg:
                    defined.add(node.args.kwarg.arg)
            elif hasattr(ast, "MatchAs") and isinstance(node, ast.MatchAs) and node.name:
                defined.add(node.name)

        undef: List[Tuple[int, str]] = sorted({
            (node.lineno, node.id)
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in defined
        })
        if undef:
            rel = path.relative_to(ROOT_DIR)
            formatted = ", ".join(f"{name} (L{ln})" for ln, name in undef)
            errors.append(f"Undefined name(s) in {rel}: {formatted}")
    return errors


def run_optional_ruff() -> int:
    venv_ruff = ROOT_DIR / ".venv" / "bin" / "ruff"
    ruff_bin = str(venv_ruff) if venv_ruff.exists() else shutil.which("ruff")
    if not ruff_bin:
        print("[*] ruff not installed (optional) — skipping ruff lint.")
        return 0
    print(f"[*] Running ruff check ({ruff_bin})...")
    res = subprocess.run([ruff_bin, "check", "scripts", "tests"], cwd=str(ROOT_DIR))
    return res.returncode


def run_unit_tests() -> bool:
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS_DIR), pattern="test_*.py", top_level_dir=str(ROOT_DIR))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


def main() -> int:
    files = iter_python_files()
    print(f"[*] Checking {len(files)} Python file(s)...")

    compile_errs = check_syntax_and_compile(files)
    undef_errs = check_undefined_names(files)
    all_static_errs = compile_errs + undef_errs
    if all_static_errs:
        for err in all_static_errs:
            print(f"[!] {err}")
        return 1
    print("[+] Syntax & undefined-name checks passed.")

    ruff_rc = run_optional_ruff()
    if ruff_rc != 0:
        return ruff_rc

    print("[*] Running unit tests...")
    if not run_unit_tests():
        return 1
    print("[+] All checks and unit tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
