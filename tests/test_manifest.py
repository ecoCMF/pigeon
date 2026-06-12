"""Manifest determinism and content."""

from __future__ import annotations

from pigeon import manifest


def test_deterministic_bytes(repo):
    a = manifest.serialize_manifest(manifest.build_manifest(repo))
    b = manifest.serialize_manifest(manifest.build_manifest(repo))
    assert a == b


def test_no_volatile_fields(repo):
    text = manifest.serialize_manifest(manifest.build_manifest(repo))
    for forbidden in ("\"ts\"", "timestamp", "generated_at"):
        assert forbidden not in text


def test_public_api_extraction(repo):
    m = manifest.build_manifest(repo)
    alpha = next(mod for mod in m["modules"] if mod["module"] == "pkg.alpha")
    assert alpha["path"] == "src/pkg/alpha.py"
    assert alpha["functions"] == ["public_alpha"]          # private excluded
    assert alpha["classes"] == [{"name": "Widget", "methods": ["spin"]}]  # _internal excluded
    assert alpha["doc"].startswith("Alpha module")


def test_entry_points_and_metadata(repo):
    m = manifest.build_manifest(repo)
    assert m["entry_points"] == {"fixture": "pkg.cli:main"}
    assert m["manifest_version"] == manifest.MANIFEST_VERSION


def test_write_manifest(repo):
    path = manifest.write_manifest(repo)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == manifest.serialize_manifest(manifest.build_manifest(repo))


# ----------------------------------------------------------- audit hardening
def test_refresh_survives_broken_python_file(repo):
    from pigeon import manifest as mf
    (repo.root / "src" / "pkg" / "broken.py").write_text(
        "def oops(:\n  syntax error here\n", encoding="utf-8")
    path = mf.write_manifest(repo)  # must not raise
    import json
    modules = json.loads(path.read_text(encoding="utf-8"))["modules"]
    broken = [m for m in modules if m["path"].endswith("broken.py")]
    assert broken and broken[0]["functions"] == []


def test_root_level_src_py_keeps_its_name():
    from pigeon.manifest import _module_name
    assert _module_name("src.py") == "src"
    assert _module_name("src/pkg/alpha.py") == "pkg.alpha"
