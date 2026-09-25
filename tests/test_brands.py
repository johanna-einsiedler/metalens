"""Product surfaces on one deployment: the brand is resolved from the Host header (or
pinned by PAPERLENS_BRAND), `/` serves the brand's landing, `/api/brand` describes the
chrome, and `/api/presets` lists only the presets a brand offers. The page routes and
`/api/brand` need no database; the preset listing does (skips without Postgres).
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import app as appmod, brands, records  # noqa: E402


def _client():
    from fastapi.testclient import TestClient
    return TestClient(appmod.app)


def test_resolve_by_host_env_and_default(monkeypatch) -> None:
    monkeypatch.delenv("PAPERLENS_BRAND", raising=False)
    monkeypatch.delenv("PAPERLENS_BRAND_HOSTS", raising=False)
    assert brands.resolve("beta.metalens.tech").id == "metalens"
    assert brands.resolve(None).id == "metalens"
    assert brands.resolve("maseminer.metalens.tech:8000").id == "maseminer"
    assert brands.resolve("www.maseminer.org").id == "maseminer"
    monkeypatch.setenv("PAPERLENS_BRAND_HOSTS", "maseminer=extract.example.org")
    assert brands.resolve("extract.example.org").id == "maseminer"
    monkeypatch.setenv("PAPERLENS_BRAND", "maseminer")
    assert brands.resolve("beta.metalens.tech").id == "maseminer"     # the pin wins


def test_preset_visibility_per_brand() -> None:
    assert brands.METALENS.shows_preset({}) and brands.MASEMINER.shows_preset({})
    assert brands.METALENS.shows_preset({"brands": ["metalens"]})
    assert not brands.MASEMINER.shows_preset({"brands": ["metalens"]})
    assert brands.MASEMINER.shows_preset({"brands": ["maseminer", "metalens"]})


def test_landing_and_brand_api_follow_the_host(monkeypatch) -> None:
    monkeypatch.delenv("PAPERLENS_BRAND", raising=False)
    monkeypatch.delenv("PAPERLENS_BASIC_PASSWORD", raising=False)
    c = _client()
    r = c.get("/", headers={"host": "maseminer.metalens.tech"})
    assert r.status_code == 200 and "MASEMiner" in r.text and "mm-hero" in r.text
    r = c.get("/", headers={"host": "beta.metalens.tech"})
    assert r.status_code == 200 and "hero-c" in r.text                 # the Metalens landing
    assert "mm-hero" in c.get("/maseminer").text                       # reachable on every host
    assert 'href="/extract?preset=masem-direct"' in c.get("/maseminer").text   # CTA lands in the MASEM builder
    assert "What happens to my API key" in c.get("/faq").text          # the FAQ page
    about = c.get("/about")
    assert about.status_code == 200 and "About Metalens" in about.text
    assert 'href="/faq"' in about.text and 'href="/catalog"' in about.text   # it leads on to the detail
    b = c.get("/api/brand", headers={"host": "maseminer.metalens.tech"}).json()
    assert b["id"] == "maseminer" and b["default_preset"] == "masem-direct" and b["logo"].endswith("maseminer-mark.svg")
    assert [n["href"] for n in b["nav_personal"]] == ["/extract", "/import", "/workspace"]
    b = c.get("/api/brand").json()
    assert b["id"] == "metalens" and b["default_preset"] is None
    assert [n["href"] for n in b["nav_shared"]] == ["/dashboards", "/catalog", "/about"]   # About is reachable from every page


def test_beta_gate_realm_names_the_brand(monkeypatch) -> None:
    monkeypatch.setenv("PAPERLENS_BASIC_PASSWORD", "pw")
    monkeypatch.delenv("PAPERLENS_BRAND", raising=False)
    r = _client().get("/", headers={"host": "maseminer.metalens.tech"})
    assert r.status_code == 401 and 'realm="MASEMiner beta"' in r.headers["www-authenticate"]


def test_presets_listed_per_brand(monkeypatch) -> None:
    try:
        conn = records.connect(); records.init_db(conn)
    except Exception:
        import pytest; pytest.skip("no Postgres")
    monkeypatch.delenv("PAPERLENS_BRAND", raising=False)
    monkeypatch.delenv("PAPERLENS_BASIC_PASSWORD", raising=False)
    c = _client()
    sess = "brand-test-sess"
    on_metalens = {p["preset_id"] for p in c.get("/api/presets", headers={"X-Session-Id": sess}).json()["presets"]}
    on_maseminer = {p["preset_id"] for p in c.get("/api/presets", headers={"X-Session-Id": sess, "host": "maseminer.org"}).json()["presets"]}
    assert {"masem-direct", "summarize", "human-ai-collab"} <= on_metalens
    assert "masem-direct" in on_maseminer
    assert not {"summarize", "human-ai-collab"} & on_maseminer          # tagged brands: ["metalens"]
    # a public personal preset lists on the brand it was created on; its owner sees it everywhere
    spec = {"format": 2, "id": "x", "meta": {"title": "Brand test preset", "mode": "extraction"},
            "prompt": {"text": "Extract ${output_schema}"},
            "entries": {"key": "items", "fields": [{"name": "value", "type": "string"}]}}
    made = c.post("/api/presets", json={"spec": spec, "visibility": "public"},
                  headers={"X-Session-Id": sess, "host": "maseminer.org"}).json()
    pid = made["id"]
    other = {"X-Session-Id": "brand-test-other"}
    assert pid in {p["preset_id"] for p in c.get("/api/presets", headers={**other, "host": "maseminer.org"}).json()["presets"]}
    assert pid not in {p["preset_id"] for p in c.get("/api/presets", headers=other).json()["presets"]}
    assert pid in {p["preset_id"] for p in c.get("/api/presets", headers={"X-Session-Id": sess}).json()["presets"]}
    records.delete_personal_preset(conn, pid); conn.commit(); conn.close()


def test_import_picker_keeps_the_presets_the_extract_picker_hides(monkeypatch) -> None:
    """The extract picker answers "what can I run?"; the import picker answers "what shape is
    the data I already have?". A landing-hidden variant and an import-only schema are invalid
    answers to the first and the only sensible answers to the second."""
    monkeypatch.delenv("PAPERLENS_BRAND", raising=False)
    monkeypatch.delenv("PAPERLENS_BASIC_PASSWORD", raising=False)
    c = _client()
    ids = lambda usage: {r["preset_id"] for r in c.get(f"/api/presets?usage={usage}").json()["presets"]}
    extract, imprt = ids("extract"), ids("import")
    assert "masem-direct" in extract and "masem-direct" in imprt          # visible either way
    assert "masem-indirect" not in extract and "masem-indirect" in imprt  # landing-hidden variant
    for pid in ("register-claims", "register-tables"):                    # mode: "import" schemas
        assert pid not in extract and pid in imprt
    assert c.get("/api/presets").json() == c.get("/api/presets?usage=extract").json()   # the default
