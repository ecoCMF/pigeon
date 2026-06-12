"""Pointer resolution across supported schemes."""

from __future__ import annotations

import pytest

from pigeon import manifest
from pigeon import resolve as rs


def test_repo_pointer(repo):
    r = rs.resolve("repo://AGENTS.md", repo)
    assert r.scheme == "repo"
    assert "canonical" in r.read_text()


def test_bare_path(repo):
    assert rs.resolve("AGENTS.md", repo).read_text().startswith("# AGENTS.md")


def test_file_url(repo):
    abs_path = (repo.root / "AGENTS.md").resolve()
    r = rs.resolve(f"file://{abs_path}", repo)
    assert r.scheme == "file"
    assert r.exists()


def test_manifest_head(repo):
    manifest.write_manifest(repo)
    r = rs.resolve("manifest@HEAD", repo)
    assert r.scheme == "manifest"
    assert r.path == repo.manifest
    assert "manifest_version" in r.read_text()


def test_unknown_scheme_rejected(repo):
    with pytest.raises(rs.PointerError):
        rs.resolve("ftp://example.com/x", repo)


def test_missing_file_raises(repo):
    r = rs.resolve("repo://does/not/exist.txt", repo)
    assert not r.exists()
    with pytest.raises(FileNotFoundError):
        r.read_bytes()


def test_s3_disabled_by_default(repo):
    with pytest.raises(rs.PointerError) as exc:
        rs.resolve("s3://bucket/key", repo)
    assert "allow_s3" in str(exc.value)


def test_manifest_rev_charset_validated(repo):
    from pigeon import resolve
    with pytest.raises(resolve.PointerError, match="charset"):
        resolve.resolve("manifest@--upload-pack=evil", repo)
    with pytest.raises(resolve.PointerError, match="charset"):
        resolve.resolve("manifest@-rf", repo)
