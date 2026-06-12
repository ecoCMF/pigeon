"""Pointer resolver.

Handoffs carry *pointers*, never payloads. Any agent receiving one must be able
to turn a pointer into bytes on demand — so the resolver is shipped, not assumed.

Supported pointer forms:

- ``repo://<relpath>``     — relative to the repo root (required)
- ``file://<abspath>``     — absolute file URL (required)
- ``<relpath>`` / ``<abspath>`` — a bare path, resolved against the repo root (required)
- ``manifest@HEAD``        — the current generated manifest
- ``manifest@<gitrev>``    — the manifest as of a git revision (needs git)
- ``s3://<bucket>/<key>``  — only when ``resolve.allow_s3`` is true AND boto3 is installed
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .config import Config

SUPPORTED = ("repo://", "file://", "s3://", "manifest@", "<path>")


class PointerError(ValueError):
    """A pointer could not be understood or resolved."""


@dataclass
class Resolved:
    """A resolved pointer. Content comes from ``path`` or pre-fetched ``data``."""

    pointer: str
    scheme: str
    path: Path | None = None
    data: bytes | None = None

    def exists(self) -> bool:
        if self.data is not None:
            return True
        return self.path is not None and self.path.is_file()

    def read_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        if self.path is None:
            raise PointerError(f"Pointer {self.pointer!r} has no resolvable content")
        if not self.path.is_file():
            raise FileNotFoundError(f"Pointer {self.pointer!r} -> missing file {self.path}")
        return self.path.read_bytes()

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding)


_REV_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^-]*$")


def _resolve_manifest(pointer: str, config: Config) -> Resolved:
    _, _, rev = pointer.partition("@")
    rev = rev or "HEAD"
    if not _REV_RE.match(rev):
        raise PointerError(
            f"Cannot resolve {pointer!r}: revision {rev!r} contains characters "
            "outside the git-ref charset (no leading dashes, no whitespace)"
        )
    if rev == "HEAD":
        return Resolved(pointer=pointer, scheme="manifest", path=config.manifest)
    # A specific revision: pull the committed manifest blob from git.
    rel = config.manifest.relative_to(config.root).as_posix()
    try:
        out = subprocess.run(
            ["git", "show", f"{rev}:{rel}"],
            cwd=config.root,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:  # git missing
        raise PointerError(f"Cannot resolve {pointer!r}: git is not available") from exc
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.decode("utf-8", "replace").strip()
        raise PointerError(f"Cannot resolve {pointer!r}: {msg}") from exc
    return Resolved(pointer=pointer, scheme="manifest", data=out.stdout)


def _resolve_s3(pointer: str, config: Config) -> Resolved:
    if not config.resolve_cfg.get("allow_s3", False):
        raise PointerError(
            f"s3 pointer {pointer!r} rejected: set resolve.allow_s3=true in "
            ".pigeon/config.yaml to enable."
        )
    try:
        import boto3  # noqa: PLC0415
    except ImportError as exc:
        raise PointerError(
            f"s3 pointer {pointer!r} needs the optional [s3] extra (boto3)."
        ) from exc
    parsed = urlparse(pointer)
    bucket, key = parsed.netloc, parsed.path.lstrip("/")
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return Resolved(pointer=pointer, scheme="s3", data=body)


def resolve(pointer: str, config: Config) -> Resolved:
    """Resolve a pointer to a :class:`Resolved` handle. Does not read content."""
    if not isinstance(pointer, str) or not pointer:
        raise PointerError("Pointer must be a non-empty string")

    if pointer.startswith("repo://"):
        rel = pointer[len("repo://"):]
        return Resolved(pointer=pointer, scheme="repo", path=(config.root / rel).resolve())

    if pointer.startswith("file://"):
        parsed = urlparse(pointer)
        return Resolved(pointer=pointer, scheme="file", path=Path(parsed.path).resolve())

    if pointer.startswith("s3://"):
        return _resolve_s3(pointer, config)

    if pointer.startswith("manifest@") or pointer == "manifest":
        return _resolve_manifest(pointer, config)

    if "://" in pointer:
        scheme = pointer.split("://", 1)[0]
        raise PointerError(
            f"Unsupported pointer scheme {scheme!r}. Supported: {', '.join(SUPPORTED)}"
        )

    # Bare path: absolute as-is, relative against repo root.
    p = Path(pointer)
    resolved = p if p.is_absolute() else (config.root / p)
    return Resolved(pointer=pointer, scheme="path", path=resolved.resolve())


def resolve_text(pointer: str, config: Config, encoding: str = "utf-8") -> str:
    """Convenience: resolve and read text in one call."""
    return resolve(pointer, config).read_text(encoding=encoding)
