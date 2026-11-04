"""The sampler and the outcome rule of a sampled review: pure, no DB."""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import records
from paperlens import review_gate as rg


def _frame(ns: dict[str, int], *, papers: int = 1) -> list[dict]:
    """``ns`` = stratum value → how many records; records spread over ``papers`` papers."""
    out, k = [], 0
    for value, n in ns.items():
        for _ in range(n):
            out.append({"record_id": f"r{k}", "document_id": f"d{k % papers}", "paper_id": f"p{k % papers}", "entry_index": k, "strata": {"table": value}})
            k += 1
    return out


def test_the_draw_is_a_function_of_seed_and_frame_only() -> None:
    fr = _frame({"T1": 50})
    a = rg.draw(fr, n=10, seed=7)
    b = rg.draw(list(reversed(fr)), n=10, seed=7)               # the order the frame comes in does not matter
    assert [i["record_id"] for i in a["items"]] == [i["record_id"] for i in b["items"]] and len(a["items"]) == 10
    assert a["frame_n"] == 50 and a["n_target"] == 10 and a["exhaustive"] is False and a["strata"] == {"all": {"N": 50, "n": 10}}
    c = rg.draw(fr, n=10, seed=8)
    assert [i["record_id"] for i in c["items"]] != [i["record_id"] for i in a["items"]]
    assert [i["ord"] for i in a["items"]] == list(range(10)) and all(i["kind"] == "random" for i in a["items"])


def test_size_share_and_exhaustive() -> None:
    fr = _frame({"T1": 7})
    d = rg.draw(fr, n=30, seed=1)
    assert len(d["items"]) == 7 and d["exhaustive"] is True
    assert rg.draw(fr, share=0.5, seed=1)["n_target"] == 4                     # ceil(3.5)
    assert rg.draw(fr, share=1.0, seed=1)["exhaustive"] is True
    assert rg.draw([], n=5, seed=1) == {"unit": "entry", "frame_n": 0, "n_target": 0, "seed": 1, "exhaustive": True, "strata": {}, "items": [], "supplement": []}


def test_strata_get_proportional_quotas_and_at_least_one_each() -> None:
    d = rg.draw(_frame({"A": 120, "B": 60, "C": 20}), n=10, seed=3, stratify=["table"])
    assert {k: v["n"] for k, v in d["strata"].items()} == {"A": 6, "B": 3, "C": 1}
    assert sum(1 for i in d["items"] if i["stratum"]["table"] == "A") == 6
    d = rg.draw(_frame({"A": 100, "B": 1}), n=2, seed=3, stratify=["table"])   # the minimum-one rule
    assert {k: v["n"] for k, v in d["strata"].items()} == {"A": 1, "B": 1}
    d = rg.draw(_frame({"A": 100, "B": 1}), n=1, seed=3, stratify=["table"])   # too small for every stratum: largest remainder wins
    assert sum(v["n"] for v in d["strata"].values()) == 1
    d = rg.draw(_frame({"A": 3, "B": 3}), n=4, seed=3, stratify=["table"])
    assert {k: v["n"] for k, v in d["strata"].items()} == {"A": 2, "B": 2}


def test_whole_papers_and_supplements() -> None:
    fr = _frame({"T1": 12}, papers=4)                                          # 4 papers × 3 records
    d = rg.draw(fr, unit="paper", n=2, seed=5)
    assert d["frame_n"] == 4 and d["n_target"] == 2 and len(d["items"]) == 6
    assert len({i["paper_id"] for i in d["items"]}) == 2                       # every record of the two drawn papers
    d = rg.draw(_frame({"T1": 5}), n=2, seed=5, supplement_ids=["x1", "x2"])
    kinds = [(i["record_id"], i["kind"]) for i in d["items"] + d["supplement"]]
    assert [k for _, k in kinds] == ["random", "random", "supplement", "supplement"] and ("x2", "supplement") in kinds
    drawn_id = d["items"][0]["record_id"]                                       # a drawn record is not a supplement as well
    d2 = rg.draw(_frame({"T1": 5}), n=2, seed=5, supplement_ids=[drawn_id, "x1"])
    assert [i["record_id"] for i in d2["supplement"]] == ["x1"]
    assert [i["ord"] for i in d["items"] + d["supplement"]] == [0, 1, 2, 3]
    assert all(i["kind"] == "random" for i in d["items"])                      # the estimate never sees a supplement


def test_the_outcome_of_a_drawn_record_reads_the_events_after_the_draw() -> None:
    t0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    drawn = t0 + timedelta(minutes=5)
    ev = lambda st, minutes, diff=None, kind="maintainer": (st, diff, t0 + timedelta(minutes=minutes), kind)
    assert rg.outcome([], drawn) == {"status": None, "checked": False, "disagreed": False}
    assert rg.outcome([ev("verified", 1)], drawn)["checked"] is False          # a verdict before the draw does not count
    assert rg.outcome([ev("verified", 10)], drawn) == {"status": "verified", "checked": True, "disagreed": False}
    assert rg.outcome([ev("flagged", 10)], drawn) == {"status": "flagged", "checked": True, "disagreed": True}
    change = [{"field_path": "n", "original_value": 1, "final_value": 2}]
    same = [{"field_path": "n", "original_value": 1, "final_value": 1}]
    assert rg.outcome([ev("corrected", 8, change), ev("verified", 10)], drawn)["disagreed"] is True    # corrected then verified: a disagreement
    assert rg.outcome([ev("verified", 10), ev("corrected", 12, change)], drawn)["disagreed"] is True   # or after the tick
    assert rg.outcome([ev("corrected", 8, same), ev("verified", 10)], drawn)["disagreed"] is False
    assert rg.outcome([ev("verified", 10), ev("unverified", 12)], drawn)["checked"] is False           # withdrawn
    assert rg.outcome([ev("verified", 10, kind="community")], drawn)["checked"] is False               # a reader's opinion is not the gate's verdict
    assert records._diff_changes_value(change) and not records._diff_changes_value(same)


def test_badge_labels_tell_how_the_figures_came_about() -> None:
    b = records._badge_from_counts(412, 0, 0)
    assert b["tier"] == "ai_only" and b["label"] == "AI-only" and "No record was reviewed" in b["label_long"]
    b = records._badge_from_counts(412, 0, 0, checks={"findings": 7, "by_severity": {"error": 5, "warn": 2}})
    assert b["label"] == "AI-only · 7 check findings" and "5 error, 2 warn" in b["label_long"]
    assert records._badge_from_counts(412, 0, 0, checks={"findings": 0, "by_severity": {}})["label"] == "AI-only · checks passed"
    sample = {"frame": 412, "drawn": 30, "checked": 30, "pending": 0, "disagreed": 2, "error_rate": 0.0667, "error_ci": [0.0184, 0.2133],
              "steps": [{"stratify": ["table_number"]}]}
    b = records._badge_from_counts(412, 30, 28, sample=sample)
    assert b["tier"] == "sample_verified" and b["label"] == "Sampled 30/412 · 93% agree"
    assert "30 of 412 records drawn at random (stratified by table_number), 30 checked, 2 disagreed" in b["label_long"]
    assert "estimated error rate 6.7% (95% CI 1.8–21.3%)" in b["label_long"] and b["sample"] is sample
    b = records._badge_from_counts(412, 9, 9, sample={**sample, "checked": 9, "pending": 21, "disagreed": 0, "error_rate": 0.0, "error_ci": [0, 0.3]})
    assert b["label"] == "Sampled 9 of 30 · 100% agree"
    b = records._badge_from_counts(412, 40, 38)
    assert b["label"] == "10% audited · 95% agree" and "not a random sample" in b["label_long"]
    b = records._badge_from_counts(30, 30, 29)
    assert b["tier"] == "human_verified" and b["label"] == "Human-verified · 97% agree" and "Every record" in b["label_long"]
