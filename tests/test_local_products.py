"""The two local commands and the two packages: ``maseminer`` and ``metalens`` are the same engine
with different defaults, and the release builds each from the repository with its own script
(scripts/masem_only.py, scripts/package_as.py). No database, no server."""
from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import local  # noqa: E402


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(_ROOT) / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)   # type: ignore[union-attr]
    return mod


def test_each_command_has_its_own_defaults(monkeypatch, tmp_path) -> None:
    for k in ("PAPERLENS_BRAND", "MASEMINER_PORT", "METALENS_PORT", "MASEMINER_HOME", "METALENS_HOME"):
        monkeypatch.delenv(k, raising=False)
    m, ml = local.parser("maseminer").parse_args([]), local.parser("metalens").parse_args([])
    assert (m.brand, m.port) == ("maseminer", 8765) and (ml.brand, ml.port) == ("metalens", 8775)
    monkeypatch.setenv("METALENS_HOME", str(tmp_path / "ml"))
    d = local._data_dir(None, "metalens")
    assert d == (tmp_path / "ml").resolve() and all((d / s).is_dir() for s in ("pg", "artifacts", "presets"))
    monkeypatch.setenv("METALENS_PORT", "9100")
    assert local.parser("metalens").parse_args([]).port == 9100 and local.parser("maseminer").parse_args([]).port == 8765
    assert local.main_metalens(["--version"]) == 0


def test_the_repository_declares_both_commands() -> None:
    project = tomllib.loads((Path(_ROOT) / "pyproject.toml").read_text())["project"]
    assert project["name"] == "maseminer"
    assert project["scripts"] == {"maseminer": "paperlens.local:main", "metalens": "paperlens.local:main_metalens"}


def test_package_as_metalens_renames_the_project_and_keeps_its_command_only() -> None:
    mod = _script("package_as")
    text = (Path(_ROOT) / "pyproject.toml").read_text()
    new = tomllib.loads(mod.rewrite(text, "metalens"))
    old = tomllib.loads(text)
    assert new["project"]["name"] == "metalens" and new["project"]["scripts"] == {"metalens": "paperlens.local:main_metalens"}
    assert new["project"]["version"] == old["project"]["version"] and new["project"]["dependencies"] == old["project"]["dependencies"]
    assert new["tool"] == old["tool"] and new["build-system"] == old["build-system"] and new["project"]["urls"] == old["project"]["urls"]
    assert "Metalens" in new["project"]["description"] and "MASEM" not in new["project"]["keywords"]


def test_the_maseminer_distribution_drops_the_metalens_command() -> None:
    import re
    text = (Path(_ROOT) / "pyproject.toml").read_text()
    trimmed = re.sub(r'(?m)^metalens = "paperlens\.local:main_metalens"\n', "", text)
    assert tomllib.loads(trimmed)["project"]["scripts"] == {"maseminer": "paperlens.local:main"}
    assert r'metalens = "paperlens\.local:main_metalens"\n' in (Path(_ROOT) / "scripts" / "masem_only.py").read_text()


def test_the_engine_reports_the_package_version() -> None:
    """Exports (metadata.engine.version), /api/version and `--version` report paperlens.__version__;
    the release checks the tag against pyproject, so the two must agree."""
    import paperlens
    assert paperlens.__version__ == tomllib.loads((Path(_ROOT) / "pyproject.toml").read_text())["project"]["version"]
