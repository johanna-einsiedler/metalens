"""PaperLens — provenance-tracked extracted-record layer (clean rebuild).

Phase 1 spine: canonical-record contract -> normalized records in Postgres,
with a faithful round-trip back to the publishable format.
"""
from __future__ import annotations



def _version() -> str:
    """The one version, from the installed package (pyproject's) — never a second constant to
    forget: the launcher's --version, /api/version and every export's `engine` show it."""
    try:
        from importlib.metadata import version
        return version("maseminer")
    except Exception:  # noqa: BLE001 - a checkout that is not installed: read pyproject directly
        pass
    try:
        import pathlib
        import tomllib
        return tomllib.loads((pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())["project"]["version"]
    except Exception:  # noqa: BLE001
        return "0.0.0"


__version__ = _version()
