"""Deterministic project manifest generator.

Emits a small, byte-stable ``manifest.json``: modules and their public
interfaces (via :mod:`ast`), entry points (from ``pyproject.toml``), plus the
declarative decisions and owners from config. This replaces ever sending the
full file tree to an agent.

Determinism contract: for a fixed set of input files and config, the serialized
bytes are identical across runs and machines — no timestamps, no absolute
paths, all collections sorted.
"""

from __future__ import annotations

import ast
import fnmatch
import glob
import json
import sys
from pathlib import Path
from typing import Any

from .config import Config

MANIFEST_VERSION = "1.0"

import tomllib  # project requires Python >= 3.11


def _candidate_files(config: Config) -> list[str]:
    """Relative posix paths matching include globs and no exclude glob."""
    root = str(config.root)
    cfg = config.manifest_cfg
    found: set[str] = set()
    for pattern in cfg["include"]:
        for hit in glob.glob(pattern, root_dir=root, recursive=True):
            found.add(Path(hit).as_posix())
    excludes = cfg["exclude"]
    kept = [
        rel
        for rel in found
        if rel.endswith(".py")
        and not any(fnmatch.fnmatch(rel, pat) for pat in excludes)
        and (config.root / rel).is_file()
    ]
    return sorted(kept)


def _module_name(rel: str) -> str:
    """Dotted module name for a repo-relative path (strips leading ``src/``)."""
    parts = Path(rel).with_suffix("").parts
    if len(parts) > 1 and parts[0] == "src":  # a literal root-level src.py stays
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _first_doc_line(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _public_api(py_path: Path) -> dict[str, Any]:
    """Extract public functions and classes (with public methods) via ast.

    A file that does not parse (mid-edit syntax error, mislabeled binary)
    must never crash ``pigeon refresh`` — it just contributes no symbols.
    """
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return {"functions": [], "classes": [], "doc": None}
    functions: list[str] = []
    classes: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            methods = sorted(
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
            )
            classes.append({"name": node.name, "methods": methods})
    return {
        "doc": _first_doc_line(tree),
        "functions": sorted(functions),
        "classes": sorted(classes, key=lambda c: c["name"]),
    }


def _entry_points(config: Config) -> dict[str, str]:
    """Read ``[project.scripts]`` from pyproject.toml if present."""
    pyproject = config.root / "pyproject.toml"
    if not pyproject.is_file() or tomllib is None:
        return {}
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    return {str(k): str(v) for k, v in scripts.items()}


def build_manifest(config: Config) -> dict[str, Any]:
    """Build the manifest as a plain dict (no I/O beyond reading sources)."""
    modules: list[dict[str, Any]] = []
    for rel in _candidate_files(config):
        api = _public_api(config.root / rel)
        modules.append(
            {
                "path": rel,
                "module": _module_name(rel),
                "doc": api["doc"],
                "functions": api["functions"],
                "classes": api["classes"],
            }
        )
    modules.sort(key=lambda m: m["path"])
    return {
        "manifest_version": MANIFEST_VERSION,
        "decisions": config.manifest_cfg.get("decisions", {}),
        "owners": config.manifest_cfg.get("owners", {}),
        "entry_points": _entry_points(config),
        "modules": modules,
    }


def serialize_manifest(manifest: dict[str, Any]) -> str:
    """Serialize to canonical, byte-stable JSON (sorted keys, trailing newline)."""
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_manifest(config: Config) -> Path:
    """Build and write ``manifest.json``; return its path."""
    manifest = build_manifest(config)
    out = config.manifest
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(serialize_manifest(manifest), encoding="utf-8")
    return out
