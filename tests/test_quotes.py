"""The 200-character rule (paperlens/quotes.py): what a public reader sees of a quotation. Pure."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import quotes as q  # noqa: E402


def test_short_quotes_pass_untouched() -> None:
    assert q.shorten("accuracy 0.70 0.80 0.78", value=0.7) == ("accuracy 0.70 0.80 0.78", False)
    it = {"snippet": "short", "page": 3, "source": None}
    assert q.shorten_item(it) is it                       # the same object: nothing to mark
    assert q.shorten(None) == ("", False)


def test_a_long_quote_is_cut_around_the_value() -> None:
    text = "A" * 300 + " the effect is 0.42 (0.11) " + "B" * 300
    s, cut = q.shorten(text, value=0.42)
    assert cut and len(s) <= 200 and "0.42" in s and s.startswith("…") and s.endswith("…")
    head, _ = q.shorten(text)                             # no value known: from the start
    assert len(head) <= 200 and head.startswith("AAA") and head.endswith("…")
    tail, _ = q.shorten("C " * 150 + "N = 1,204.", value=1204)      # the value at the very end stays in
    assert tail.endswith("N = 1,204.") and tail.startswith("…") and len(tail) <= 200
    assert q.shorten(text, value=0.42) == q.shorten(text, value=0.42)    # deterministic


def test_values_are_matched_as_printed_and_never_inside_longer_numbers() -> None:
    text = "x" * 250 + " loading .775 on factor 2 " + "y" * 250
    assert ".775" in q.shorten(text, value=0.775)[0]
    text = "10.70 " + "x" * 250 + " human 0.70 team 0.80 " + "y" * 250
    s, _ = q.shorten(text, value=0.70)
    assert "human 0.70" in s and "10.70" not in s
    assert q.forms_of(True) == [] and q.forms_of(None) == []
    assert q.forms_of(0.775)[:2] == ["0.775", ".775"] and "0.78" in q.forms_of(0.775)   # exact forms first
    assert "12,345" in q.forms_of(12345) and "−0.5" in q.forms_of(-0.5)


def test_cuts_fall_on_words_and_items_say_they_were_cut() -> None:
    words = " ".join(f"word{i}" for i in range(80))
    s, cut = q.shorten(words, value="word40")
    assert cut and len(s) <= 200 and "word40" in s
    body = s.strip("…")
    assert body.startswith("word") and body.split(" ")[-1].startswith("word")   # no half words at the cuts
    it = q.shorten_item({"snippet": words, "page": 4, "source": "Table 1"}, value="word40")
    assert it["truncated"] is True and it["page"] == 4 and it["source"] == "Table 1" and len(it["snippet"]) <= 200


def test_value_at_walks_record_paths() -> None:
    fv = {"n": 288, "factor_loadings": [{"item": "a", "loading": 0.775}, {"item": "b", "loading": {"value": 0.5, "note": "x"}}], "flag": True}
    assert q.value_at(fv, "n") == 288 and q.value_at(fv, "factor_loadings[0].loading") == 0.775
    assert q.value_at(fv, "factor_loadings[1].loading") == 0.5                 # a wrapped value
    assert q.value_at(fv, "factor_loadings[5].loading") is None and q.value_at(fv, "flag") is None
    assert q.value_at(fv, "factor_loadings") is None and q.value_at(None, "n") is None and q.value_at(fv, "") is None


def test_policy_names_the_limit_and_the_page() -> None:
    p = q.policy("https://metalens.tech/dataset?id=x")
    assert p["max_chars"] == 200 and p["full_quotes_url"].endswith("dataset?id=x") and "200" in p["text"]
    assert "full_quotes_url" not in q.policy()
