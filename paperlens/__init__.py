"""PaperLens — provenance-tracked extracted-record layer (clean rebuild).

Phase 1 spine: canonical-record contract -> normalized records in Postgres,
with a faithful round-trip back to the publishable format.
"""
from __future__ import annotations

__version__ = "2.0.0"   # kept equal to pyproject.toml (tests/test_local_products.py); exports and the launcher report it
