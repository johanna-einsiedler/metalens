"""A stored schema row takes the preset file's wording and display when the file has the same
schema id: labels, help, titles, option help, stage help, the version and the whole display block
move; the contract never does. A file with another contract leaves the row alone. The DB parts
skip without Postgres."""
from __future__ import annotations

import copy
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import preset_spec as ps, records  # noqa: E402

OLD = {
    "format": 2, "id": "refresh-demo", "version": 1, "meta": {"title": "Refresh demo"},
    "prompt": {"text": ""},
    "paper": {"fields": [{"name": "design", "type": "enum", "label": "Design", "options": [{"value": "rct", "label": "RCT"}, "obs"], "evidence": "value"}]},
    "entries": {"key": "tasks", "label": "Task", "id_field": "task_id",
                "fields": [{"name": "task_id", "type": "string", "evidence": "row"},
                           {"name": "items", "type": "table", "evidence": "none", "columns": [
                               {"name": "n", "type": "integer"}, {"name": "page", "type": "integer"},
                               {"name": "kind", "type": "enum", "options": ["passage", "table"]},
                               {"name": "text", "type": "text", "verbatim": True}, {"name": "bears_on", "type": "list"}]}]},
    "confidence": {"groups": []},
    "display": {"tabs": [{"id": "t", "label": "Task", "fields": ["task_id", "items"]}],
                "review": {"layout": "stages", "items": "items", "stages": [
                    {"id": "design", "label": "Design", "help": "old help", "paper_fields": ["design"]},
                    {"id": "tasks", "label": "Tasks", "help": "old tasks", "fields": ["task_id"]}]}},
}
OLD["meta"]["mode"] = "import"


def _new_file() -> dict:
    """The same contract, re-worded: option help, field help, a new stage help, a version bump,
    an auto_id on the tasks stage."""
    d = copy.deepcopy(OLD)
    d["version"] = 2
    d["meta"]["title"] = "Refresh demo, reworded"
    d["paper"]["fields"][0]["label"] = "Research design"
    d["paper"]["fields"][0]["help"] = "the main comparison"
    d["paper"]["fields"][0]["options"][0]["help"] = "random assignment"
    d["display"]["review"]["stages"][0]["help"] = "new help"
    d["display"]["review"]["stages"][1]["auto_id"] = {"field": "task_id", "next": "T{n}"}
    return d


def test_the_wording_and_display_move_and_the_contract_stays() -> None:
    old, new = ps.normalize(OLD), ps.normalize(_new_file())
    assert ps.content_hash(old) == ps.content_hash(new) and ps.schema_id(old) == ps.schema_id(new)
    out = ps.refreshed_spec(old, new)
    assert out is not None and ps.content_hash(out) == ps.content_hash(old)
    f = out["paper"]["fields"][0]
    assert f["label"] == "Research design" and f["help"] == "the main comparison" and f["options"][0]["help"] == "random assignment"
    assert f["options"][1] == "obs" and f["type"] == "enum"                      # the contract keys as stored
    assert out["version"] == 2 and out["meta"]["title"] == "Refresh demo, reworded"
    assert out["display"] == new["display"]                                          # the whole block
    assert out["display"]["review"]["stages"][1]["auto_id"] == {"field": "task_id", "next": "T{n}"}
    assert ps.field_defs_for(out)["label"] == ps.field_defs_for(new)["label"]       # "v2" in the label


def test_another_contract_or_id_is_left_alone() -> None:
    old = ps.normalize(OLD)
    other = _new_file(); other["paper"]["fields"][0]["options"].append("quasi")     # a new option: another contract
    assert ps.refreshed_spec(old, ps.normalize(other)) is None
    renamed = _new_file(); renamed["id"] = "refresh-demo-2"
    assert ps.refreshed_spec(old, ps.normalize(renamed)) is None
    assert ps.merge_wording([1, 2], [1, 2, 3]) == [1, 2]                              # lists of another length: as stored


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def test_a_stored_row_gains_the_new_file_s_help_and_display() -> None:
    if not _db_ok():
        pytest.skip("no Postgres available")
    conn = records.connect(); records.init_db(conn)
    old, new = ps.normalize(OLD), ps.normalize(_new_file())
    sid = ps.schema_id(old)
    conn.execute("DELETE FROM schema WHERE id = %s", (sid,)); conn.commit()
    records.upsert_schema(conn, sid, ps.field_defs_for(old)); conn.commit()
    other_id = "refresh-demo@deadbeef"
    conn.execute("DELETE FROM schema WHERE id = %s", (other_id,))
    records.upsert_schema(conn, other_id, {**ps.field_defs_for(old), "schema_id": other_id}); conn.commit()

    assert records.refresh_schema_wording(conn, sid, new) is True
    assert records.refresh_schema_wording(conn, sid, new) is False                     # already current
    conn.commit()
    fd = records.get_schema(conn, sid)["field_defs"]
    assert fd["schema_id"] == sid and fd["hash"] == ps.content_hash(old)
    assert fd["spec"]["paper"]["fields"][0]["options"][0]["help"] == "random assignment"
    assert fd["spec"]["display"]["review"]["stages"][0]["help"] == "new help" and fd["spec"]["version"] == 2
    assert fd["label"].endswith("v2 (" + ps.content_hash(old)[:8] + ")") or "v2" in fd["label"]
    # the row of another contract is untouched by the file (its id differs)
    assert records.refresh_schema_wording(conn, other_id, new) is False
    assert records.get_schema(conn, other_id)["field_defs"]["spec"]["display"]["review"]["stages"][0]["help"] == "old help"
    # the sweep over the loaded files ({id: spec}, as load_all gives them) finds the row by its schema id
    conn.execute("UPDATE schema SET field_defs = %s WHERE id = %s", (records.Json(ps.field_defs_for(old)), sid)); conn.commit()
    assert records.refresh_stored_presets(conn, {new["id"]: new}) == [sid]
    assert records.refresh_stored_presets(conn, {new["id"]: new}) == []                         # current now
    conn.execute("UPDATE schema SET field_defs = %s WHERE id = %s", (records.Json(ps.field_defs_for(old)), sid)); conn.commit()
    meta = {"id": new["id"], "schema_id": sid, "spec": new, "legacy": False}                    # a meta works too
    assert records.refresh_stored_presets(conn, {new["id"]: meta}) == [sid]
    conn.commit()
    conn.execute("DELETE FROM schema WHERE id IN (%s, %s)", (sid, other_id)); conn.commit()
