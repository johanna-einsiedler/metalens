"""Enrichment pipeline tests — offline via httpx.MockTransport (no network).

Verifies source parsing, the merge precedence (Crossref biblio, Unpaywall OA-PDF
override, OpenAlex topics + cross-fill), DOI-prefix link typing, and JEL
heuristic prediction — plus that everything persists with provenance.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx  # noqa: E402

from paperlens import enrich  # noqa: E402

DOI = "10.1257/aer.20211001"

_CROSSREF = {"message": {
    "title": ["Automation and the Wage Structure"],
    "container-title": ["American Economic Review"],
    "publisher": "American Economic Association",
    "type": "journal-article",
    "ISSN": ["0002-8282"],
    "author": [{"given": "Daron", "family": "Acemoglu", "sequence": "first",
                "affiliation": [{"name": "MIT"}], "ORCID": "http://orcid.org/0000-0001-2345-6789"}],
    "issued": {"date-parts": [[2022, 6, 1]]},
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
    "reference": [{"DOI": "10.1257/aer.20150012"}],
    "funder": [{"name": "National Science Foundation", "DOI": "10.13039/100000001"}],
    "relation": {"is-supplemented-by": [{"id-type": "doi", "id": "10.3886/E120000V1"}]},
}}

_UNPAYWALL = {"is_oa": True, "oa_status": "green",
              "best_oa_location": {"url_for_pdf": "https://repo.example/paper.pdf"}}

_OPENALEX = {
    "id": "https://openalex.org/W42",
    "primary_topic": {"display_name": "Labor Economics", "score": 0.981},
    "topics": [{"id": "https://openalex.org/T12", "display_name": "Labor Economics",
                "score": 0.981, "subfield": {"display_name": "Economics and Econometrics"},
                "field": {"display_name": "Economics, Econometrics and Finance"},
                "domain": {"display_name": "Social Sciences"}}],
    "keywords": [{"display_name": "automation"}, {"display_name": "wage inequality"}],
    "primary_location": {"source": {"display_name": "American Economic Review"}},
    "publication_year": 2022,
    "open_access": {"is_oa": True, "oa_url": "https://oa.example/aer"},
}


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "api.crossref.org" in url:
        return httpx.Response(200, json=_CROSSREF)
    if "api.unpaywall.org" in url:
        return httpx.Response(200, json=_UNPAYWALL)
    if "api.openalex.org" in url:
        return httpx.Response(200, json=_OPENALEX)
    return httpx.Response(404)


def _mock_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler))


def test_enrich_fields_merge_and_provenance() -> None:
    with _mock_client() as client:
        m = enrich.enrich_fields(DOI, client=client)

    # Crossref biblio
    assert m["title"].value == "Automation and the Wage Structure"
    assert m["title"].source == "crossref" and m["title"].method == "api"
    assert m["journal"].value == "American Economic Review" and m["journal"].source == "crossref"
    assert m["year"].value == 2022
    assert m["publication_date"].value == "2022-06-01"
    assert m["license"].value.endswith("/by/4.0/")
    assert m["referenced_works"].value == ["10.1257/aer.20150012"]
    assert m["authors"].value[0]["family"] == "Acemoglu"

    # Unpaywall wins OA-PDF (override), even though OpenAlex also offers one
    assert m["oa_pdf_url"].value == "https://repo.example/paper.pdf"
    assert m["oa_pdf_url"].source == "unpaywall"
    assert m["is_oa"].value is True and m["is_oa"].source == "unpaywall"

    # OpenAlex topics + keywords
    assert m["primary_topic"].value == "Labor Economics" and m["primary_topic"].source == "openalex"
    assert m["openalex_topics"].value[0]["field"] == "Economics, Econometrics and Finance"
    assert m["author_keywords"].value == ["automation", "wage inequality"]

    # DOI-prefix link typing: openICPSR (10.3886) -> data_links (id kept verbatim);
    # empty code/supplementary buckets are omitted entirely.
    assert any(e["id"].lower().startswith("10.3886/") for e in m["data_links"].value)
    assert "code_links" not in m and "supplementary_links" not in m

    # JEL predicted from the title (automation -> O33, wage -> J31), flagged predicted
    jel = m["jel_codes"]
    assert set(jel.value) == {"J31", "O33"}
    assert jel.method == "predicted" and jel.confidence < 1.0


def test_jel_heuristic_human_capital() -> None:
    hits = enrich.predict_jel_heuristic("Returns to skill and on-the-job training", None)
    codes = {h.value for h in hits}
    assert "J24" in codes
    assert all(h.method == "predicted" for h in hits)


# ── DB persistence (skips without Postgres) ───────────────────────────────────

def _db_ok() -> bool:
    try:
        from paperlens import records
        c = records.connect(); c.close()
        return True
    except Exception:
        return False


def test_enrich_persists_with_provenance() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    from paperlens import records
    conn = records.connect()
    records.init_db(conn)
    with _mock_client() as client:
        out = enrich.enrich_paper(conn, DOI, client=client)
    assert out["doi"] == DOI and out["provenance_count"] >= 10

    paper = records.paper_with_provenance(conn, DOI)
    assert paper["title"] == "Automation and the Wage Structure"
    assert paper["primary_topic"] == "Labor Economics"
    assert set(paper["jel_codes"]) == {"J31", "O33"}
    assert paper["oa_pdf_url"] == "https://repo.example/paper.pdf"
    assert paper["last_enriched_at"] is not None
    # provenance footer: per-field source/method present
    by_field = {p["field"]: p for p in paper["provenance"]}
    assert by_field["title"]["source"] == "crossref"
    assert by_field["oa_pdf_url"]["source"] == "unpaywall"
    assert by_field["jel_codes"]["method"] == "predicted"
    conn.close()


def _main() -> int:
    failures = 0
    for label, fn in [
        ("enrich:merge+provenance", test_enrich_fields_merge_and_provenance),
        ("enrich:jel-human-capital", test_jel_heuristic_human_capital),
        ("enrich:persists", test_enrich_persists_with_provenance),
    ]:
        try:
            fn()
            print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "Skipped":
                print(f"  SKIP  {label}: {exc}")
                continue
            failures += 1
            print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
