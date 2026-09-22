"""A DOI per release on Zenodo: the first deposit creates the dataset's record, the next one a new
version of it; the files are the release export and release.json names its own DOI. Zenodo is a
mock transport. Skips without Postgres."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid

import httpx
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import records, releases, zenodo  # noqa: E402
from test_analysis_table import HAC, _db_ok, _seed, _seed_into  # noqa: E402


class FakeZenodo:
    """Just enough of the deposit API: depositions with reserved DOIs, flat file buckets,
    new versions that copy the previous files, publish."""
    def __init__(self):
        self.deps: dict[int, dict] = {}
        self.buckets: dict[str, dict[str, bytes]] = {}
        self.log: list[str] = []
        self.n = 100

    def _new(self, concept: int | None = None) -> dict:
        self.n += 1
        d = {"id": self.n, "conceptrecid": concept or self.n, "state": "unsubmitted", "files": [],
             "metadata": {"prereserve_doi": {"doi": f"10.5072/zenodo.{self.n}"}},
             "links": {"bucket": f"https://z/api/files/b{self.n}", "html": f"https://z/deposit/{self.n}"}}
        self.deps[self.n] = d; self.buckets[f"b{self.n}"] = {}
        return d

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.log.append(f"{req.method} {req.url.path}")
        assert req.headers.get("Authorization") == "Bearer t-test"
        path, parts = req.url.path, req.url.path.strip("/").split("/")
        if req.method == "POST" and path == "/api/deposit/depositions":
            return httpx.Response(201, json=self._new())
        if req.method == "POST" and path.endswith("/actions/newversion"):
            prev = self.deps[int(parts[3])]
            d = self._new(prev["conceptrecid"])
            d["files"] = [{"id": f"f{k}", "filename": n} for k, n in enumerate(self.buckets[f"b{prev['id']}"])]   # copied along
            return httpx.Response(201, json={**prev, "links": {**prev["links"], "latest_draft": f"https://z/api/deposit/depositions/{d['id']}"}})
        if req.method == "GET" and len(parts) == 4 and parts[2] == "depositions":
            return httpx.Response(200, json=self.deps[int(parts[3])])
        if req.method == "DELETE" and "files" in parts:
            d = self.deps[int(parts[3])]; d["files"] = [f for f in d["files"] if f["id"] != parts[5]]
            return httpx.Response(204)
        if req.method == "PUT" and parts[1] == "files":
            self.buckets[parts[2]][parts[3]] = req.content
            return httpx.Response(201, json={"key": parts[3]})
        if req.method == "PUT" and len(parts) == 4:
            self.deps[int(parts[3])]["metadata"].update(json.loads(req.content)["metadata"])
            return httpx.Response(200, json=self.deps[int(parts[3])])
        if req.method == "POST" and path.endswith("/actions/publish"):
            d = self.deps[int(parts[3])]
            if d["files"] or not self.buckets[f"b{d['id']}"]:
                return httpx.Response(400, json={"message": "Validation error", "errors": [{"field": "files", "message": "missing or stale files"}]})
            d["state"] = "done"; d["doi"] = d["metadata"]["prereserve_doi"]["doi"]; d["conceptdoi"] = f"10.5072/zenodo.{d['conceptrecid']}"
            d["links"]["record_html"] = f"https://z/records/{d['id']}"
            return httpx.Response(202, json=d)
        return httpx.Response(404, json={"message": f"no route {req.method} {path}"})


def _second_paper() -> dict:
    doc = copy.deepcopy(HAC)
    doc["paper_metadata"] = {"title": "Another teams paper", "doi": "10.1/z", "year": 2025, "authors": ["Qazi A"], "journal": "K"}
    return doc


def test_first_deposit_then_a_new_version(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    monkeypatch.setenv("PAPERLENS_ZENODO_TOKEN", "t-test"); monkeypatch.setenv("PAPERLENS_ZENODO_SANDBOX", "1")
    assert zenodo.status() == {"configured": True, "sandbox": True} and zenodo.base_url().startswith("https://sandbox.")
    conn = records.connect(); records.init_db(conn)
    sess = f"zen-{uuid.uuid4().hex[:6]}"
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    records.update_dataset_meta(conn, ds, description="Teams & tasks", keywords=["human–AI", "teams"])
    z = FakeZenodo(); client = httpx.Client(transport=httpx.MockTransport(z.handler))

    r1 = releases.create(conn, ds, notes="first cut")
    out = zenodo.deposit(conn, r1, client=client)
    assert out["doi"] == "10.5072/zenodo.101" and out["zenodo_record_id"] == 101 and out["zenodo_url"] == "https://z/records/101"
    assert (records.get_dataset(conn, ds) or {}).get("zenodo_concept_doi") == "10.5072/zenodo.101"
    # the deposited release.json names its DOI; the flat bucket holds every export file
    files = z.buckets["b101"]
    assert set(files) == {"release.json", "README.md", "evidence.json", "tables__entries.json", "tables__conditions.json", "tables__conditions.measures.json"}
    meta = json.loads(files["release.json"])
    assert meta["release"]["doi"] == "10.5072/zenodo.101" and "10.5072/zenodo.101" in files["README.md"].decode()
    m = z.deps[101]["metadata"]
    assert m["title"].endswith("— release v1") and m["version"] == "v1" and m["upload_type"] == "dataset" and m["license"] == "cc-by-4.0"
    assert m["keywords"] == ["human–AI", "teams", "Metalens"] and "Teams &amp; tasks" in m["description"] and "first cut" in m["description"]
    assert m["creators"] == [{"name": "Anonymous"}]                    # no citation name on this owner
    # the suggested citation now points at the concept DOI
    assert "https://doi.org/10.5072/zenodo.101" in (records.get_dataset(conn, ds) or {})["citation"]
    with pytest.raises(RuntimeError, match="already has a DOI"):
        zenodo.deposit(conn, releases.get(conn, r1["id"]), client=client)

    # the dataset moves on → release v2 → a NEW VERSION of the same record, with only v2's files
    _seed_into(conn, _second_paper(), "human-ai-collab", sess, ds)
    r2 = releases.create(conn, ds)
    out2 = zenodo.deposit(conn, r2, client=client)
    assert out2["doi"] == "10.5072/zenodo.102" and z.deps[102]["conceptrecid"] == 101
    assert "POST /api/deposit/depositions/101/actions/newversion" in z.log and z.log.count("POST /api/deposit/depositions") == 1
    assert any(l.startswith("DELETE /api/deposit/depositions/102/files/") for l in z.log) and z.deps[102]["files"] == []
    assert json.loads(z.buckets["b102"]["release.json"])["release"]["number"] == 2
    listed = releases.list_for_dataset(conn, ds)
    assert [x["doi"] for x in listed] == ["10.5072/zenodo.102", "10.5072/zenodo.101"]
    assert releases.public_row(listed[0])["doi"] == "10.5072/zenodo.102" and "zenodo_record_id" not in releases.public_row(listed[0])
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_a_refusal_is_explained_and_nothing_is_stored(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    monkeypatch.setenv("PAPERLENS_ZENODO_TOKEN", "t-test")
    conn = records.connect(); records.init_db(conn)
    ds, _ = _seed(conn, HAC, "human-ai-collab", f"zen-{uuid.uuid4().hex[:6]}")
    rel = releases.create(conn, ds)
    def refuse(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "The token has no deposit:write scope"})
    with pytest.raises(RuntimeError, match="create deposition: HTTP 403 — The token has no deposit:write scope"):
        zenodo.deposit(conn, rel, client=httpx.Client(transport=httpx.MockTransport(refuse)))
    assert releases.get(conn, rel["id"])["doi"] is None
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_api_is_owner_only_and_needs_configuration(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    monkeypatch.delenv("PAPERLENS_ZENODO_TOKEN", raising=False)
    conn = records.connect(); records.init_db(conn)
    sess = f"zen-api-{uuid.uuid4().hex[:6]}"; mine, other = {"X-Session-Id": sess}, {"X-Session-Id": f"o-{uuid.uuid4().hex[:6]}"}
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    releases.create(conn, ds)
    c, stranger = TestClient(appmod.app), TestClient(appmod.app)
    assert stranger.post(f"/api/datasets/{ds}/releases/1/doi", headers=other).status_code in (403, 404)
    assert c.post(f"/api/datasets/{ds}/releases/1/doi", headers=mine).status_code == 400          # not configured
    z = c.get(f"/api/datasets/{ds}/releases", headers=mine).json()["zenodo"]
    assert z["configured"] is False and z["allowed"] is False
    monkeypatch.setenv("PAPERLENS_ZENODO_TOKEN", "t-test")
    # configured, but an anonymous session may not mint: DOIs are permanent and under the server's account
    r = c.post(f"/api/datasets/{ds}/releases/1/doi", headers=mine)
    assert r.status_code == 401 and "Sign in" in r.json()["detail"]
    email = f"zen-{uuid.uuid4().hex[:8]}@example.org"
    uid = c.post("/api/auth/register", json={"email": email, "password": "zenodo-test-pass-1"}, headers=mine).json()["user"]["id"]
    conn.execute("UPDATE record SET owner_user_id = %s::uuid WHERE dataset_id = %s::uuid", (uid, ds)); conn.commit()
    monkeypatch.setenv("PAPERLENS_ZENODO_USERS", "someone-else@example.org")               # an allow-list without this account
    r = c.post(f"/api/datasets/{ds}/releases/1/doi", headers=mine)
    assert r.status_code == 403 and "isn’t allowed" in r.json()["detail"]
    assert c.get(f"/api/datasets/{ds}/releases", headers=mine).json()["zenodo"]["allowed"] is False
    monkeypatch.setenv("PAPERLENS_ZENODO_USERS", f" {email.upper()} , other@example.org")   # case and spaces do not matter
    assert c.get(f"/api/datasets/{ds}/releases", headers=mine).json()["zenodo"] == {"configured": True, "sandbox": False, "allowed": True, "why_not": None}
    assert c.post(f"/api/datasets/{ds}/releases/9/doi", headers=mine).status_code == 404
    # the daily cap counts deposits made by this account
    monkeypatch.setenv("PAPERLENS_ZENODO_PER_DAY", "1")
    conn.execute("UPDATE dataset_release SET zenodo_record_id = 7, doi = '10.5281/zenodo.7', doi_minted_at = now() WHERE dataset_id = %s::uuid", (ds,)); conn.commit()
    r = c.post(f"/api/datasets/{ds}/releases/1/doi", headers=mine)                          # has its DOI, not on GitHub: nothing to do
    assert r.status_code == 409 and "already has a DOI" in r.json()["detail"]
    _seed_into(conn, _second_paper(), "human-ai-collab", sess, ds)
    conn.execute("UPDATE record SET owner_user_id = %s::uuid WHERE dataset_id = %s::uuid", (uid, ds)); conn.commit()
    releases.create(conn, ds)
    r = c.post(f"/api/datasets/{ds}/releases/2/doi", headers=mine)
    assert r.status_code == 403 and "limit is 1" in r.json()["detail"]
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()


def test_switching_from_the_sandbox_to_the_real_zenodo_starts_a_fresh_record(monkeypatch) -> None:
    """A sandbox DOI (10.5072) is a test artefact: once the server talks to zenodo.org it no longer
    counts — the release can be minted again, and the first real deposit is a new record, not a
    'new version' of something the real Zenodo never saw."""
    if not _db_ok():
        pytest.skip("no Postgres")
    monkeypatch.setenv("PAPERLENS_ZENODO_TOKEN", "t-test"); monkeypatch.setenv("PAPERLENS_ZENODO_SANDBOX", "1")
    conn = records.connect(); records.init_db(conn)
    ds, _ = _seed(conn, HAC, "human-ai-collab", f"zen-sw-{uuid.uuid4().hex[:6]}")
    rel = releases.create(conn, ds)
    z = FakeZenodo(); client = httpx.Client(transport=httpx.MockTransport(z.handler))
    assert zenodo.deposit(conn, rel, client=client)["doi"] == "10.5072/zenodo.101"
    assert "10.5072/zenodo.101" in (records.get_dataset(conn, ds) or {})["citation"]
    monkeypatch.delenv("PAPERLENS_ZENODO_SANDBOX")                      # the switch
    assert not zenodo.counts_here("10.5072/zenodo.101") and zenodo.counts_here("10.5281/zenodo.7")
    assert "10.5072" not in (records.get_dataset(conn, ds) or {})["citation"]   # the test DOI leaves the citation
    real = FakeZenodo(); real.n = 500
    out = zenodo.deposit(conn, releases.get(conn, rel["id"]), client=httpx.Client(transport=httpx.MockTransport(real.handler)))
    assert out["doi"] == "10.5072/zenodo.501" and real.log[0] == "POST /api/deposit/depositions"   # a fresh record (the fake mints 10.5072 too)
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()
