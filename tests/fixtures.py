"""Canonical-record fixtures for the round-trip slice.

Two evidence-placement conventions, both contract-compliant (every evidence
item has exactly the four keys snippet/page/source/field):

  * FORESTPLOT — flat TOP-LEVEL ``evidence`` array, ``studies._table[N]`` paths.
  * MASEM      — per-entry NESTED ``evidence``, descriptive (non-path) field
                 strings, markdown-fenced (exercises fence repair).
  * MASEM_RICH — adds ``paper_metadata`` + a top-level ``extraction_confidence``
                 block (non-publishable) across multiple samples.

Mined from web/presets/forestplot.json (documented output) and
web/tests/conftest.py (``evidence_json_payload``).
"""
from __future__ import annotations

# Forestplot: top-level evidence, studies wrapped in _table.
FORESTPLOT_JSON = """{
  "paper_metadata": {"title": "A meta-analysis of remote-work productivity", "doi": "10.1037/abc.0000123", "year": 2022, "authors": ["Smith J", "Jones K"]},
  "metric": "SMD",
  "studies": {
    "_table": [
      {"id": "Smith 2018", "yi": -0.42, "vi": 0.052, "year": 2018, "n": 312, "design": "RCT", "notes": ""},
      {"id": "Jones 2020", "yi": 0.13, "vi": 0.021, "year": 2020, "n": 488, "design": "cohort", "notes": "vi computed from 95% CI"}
    ]
  },
  "evidence": [
    {"snippet": "Smith et al. 2018 ... SMD -0.42 (95% CI -0.61 to -0.23)", "page": 4, "source": "Figure 2", "field": "studies._table[0]"},
    {"snippet": "Jones (2020): SMD 0.13, 95% CI 0.04-0.22", "page": 5, "source": "Table 1", "field": "studies._table[1]"},
    {"snippet": "Figure 2. Forest plot of included studies.", "page": 4, "source": "Figure 2", "field": "studies"}
  ]
}"""

# Masem: nested per-sample evidence, markdown-fenced, no top-level evidence.
MASEM_JSON = """```json
{
  "samples": [
    {
      "sample_id": "S1",
      "n": 147,
      "factor_loadings": {
        "item1": {"F1": 0.83, "F2": 0.12},
        "item2": {"F1": 0.45, "F2": 0.71}
      },
      "evidence": [
        {"snippet": "N = 147 undergraduate students participated", "page": 1, "source": null, "field": "sample identification"},
        {"snippet": "Table 2. Rotated factor matrix", "page": 3, "source": "Table 2", "field": "factor loadings"}
      ]
    }
  ]
}
```"""

# Masem with paper_metadata + non-publishable extraction_confidence, 2 samples.
MASEM_RICH_JSON = """{
  "paper_metadata": {"title": "Need for Cognition across two samples", "doi": "https://doi.org/10.1016/J.PAID.2021.99999", "year": 2021, "authors": ["Mueller A", "Becker B"]},
  "schema_version": "masem-v3",
  "samples": [
    {
      "sample_id": "S1",
      "n": 220,
      "factor_loadings": {"item1": {"F1": 0.71}, "item2": {"F1": 0.66}},
      "evidence": [
        {"snippet": "Study 1 (N = 220)", "page": 5, "source": null, "field": "samples[0]"},
        {"snippet": "Table 3. Standardized loadings, Study 1.", "page": 6, "source": "Table 3", "field": "samples[0].factor_loadings"}
      ]
    },
    {
      "sample_id": "S2",
      "n": 305,
      "factor_loadings": {"item1": {"F1": 0.74}, "item2": {"F1": 0.69}},
      "evidence": [
        {"snippet": "Study 2 comprised 305 adults", "page": 8, "source": null, "field": "samples[1]"},
        {"snippet": "Table 5. Standardized loadings, Study 2.", "page": 9, "source": "Table 5", "field": "samples[1].factor_loadings"}
      ]
    }
  ],
  "extraction_confidence": {
    "factor_loadings": {"level": "high", "notes": ""},
    "metadata": {"level": "medium", "notes": "sample 2 N inferred from text"}
  }
}"""

# Format-2 document: declared paper field + paper-scope confidence, samples with a
# sub-entry array (records) that carries its own confidence, per-sample confidence,
# and top-level evidence addressing sub-entries by path.
MASEM_V2_JSON = """{
  "paper_metadata": {"title": "Video-game use and physical activity in adolescents", "doi": "10.1000/vg.2023.001", "year": 2023, "authors": ["Lee C", "Park D"], "journal": "J Behav Med",
                     "preregistered": true,
                     "confidence": {"design": {"level": "high", "notes": "stated in methods"}}},
  "samples": [
    {"sample_id": "S1", "pubyear": 2023, "country": "KR", "n": 412,
     "records": [
        {"var1": "pa", "var2": "vg", "es": -0.21, "type": "r", "n": 412, "confidence": {"row_check": {"level": "high", "notes": "Table 2 row 3"}}},
        {"var1": "bm", "var2": "vg", "es": 0.08, "type": "r", "n": 412}
     ],
     "confidence": {"effect_sizes": {"level": "high", "notes": "all from Table 2"}, "metadata": {"level": "medium", "notes": "n inferred from df"}}},
    {"sample_id": "S2", "pubyear": 2023, "country": "KR", "n": 198,
     "records": [{"var1": "pa", "var2": "vg", "es": -0.17, "type": "r", "n": 198}],
     "confidence": {"effect_sizes": {"level": "medium", "notes": "correlation read from figure"}, "metadata": {"level": "high", "notes": ""}}}
  ],
  "evidence": [
    {"snippet": "Study 1 (N = 412)", "page": 3, "source": null, "field": "samples[0]"},
    {"snippet": "r = -.21", "page": 5, "source": "Table 2", "field": "samples[0].records[0].es"},
    {"snippet": "r = .08", "page": 5, "source": "Table 2", "field": "samples[0].records[1].es"},
    {"snippet": "Study 2 (N = 198)", "page": 7, "source": null, "field": "samples[1]"},
    {"snippet": "r = -.17", "page": 8, "source": "Figure 3", "field": "samples[1].records[0].es"}
  ]
}"""

# What the shipping MASEM prompts actually produce: a PER-SAMPLE ``extraction_confidence``
# block of bare strings. Ingest used to drop it while strip_to_publishable kept it, so the
# round-trip invariant silently failed for every real MASEM document.
MASEM_LEGACY_ENTRY_CONF_JSON = """{
  "paper_metadata": {"title": "Need for Cognition, Sample X", "doi": "10.5555/ncs.2020.7", "year": 2020, "authors": ["Q R"]},
  "samples": [
    {"sample_id": "S1", "n": 300, "nfac": 1,
     "extraction_confidence": {"factor_loadings": "high", "factor_correlations": "low", "metadata": "medium"},
     "evidence": [{"snippet": "N = 300", "page": 2, "source": null, "field": "samples[0].n"}]}
  ]
}"""

# One quote reused for several values: ``field`` is a LIST of paths. The spine stores one
# span per path, and the publishable form is the expanded list — so the model can cite the
# shared design sentence once for every condition instead of dropping the later ones.
HAC_LINKED_EVIDENCE_JSON = """{
  "paper_metadata": {"title": "Benchmarking human-AI collaboration", "doi": null, "year": 2024, "authors": ["Woelfle T"], "journal": "J Clin Epidemiol"},
  "experiments": [
    {"Exp_ID": "PRISMA", "Task_Type": "Decide",
     "conditions": [
        {"Condition_Name": "Human Rater 1 & Claude-3-Opus", "Final_Decision": "AI", "measures": [{"Perf_Metric": "Agreement (%)", "Avg_Perf_HumanAI": 94}]},
        {"Condition_Name": "Human Rater 1 & GPT-4", "Final_Decision": "AI", "measures": [{"Perf_Metric": "Agreement (%)", "Avg_Perf_HumanAI": 89}]}
     ],
     "confidence": {"design": {"level": "high", "notes": ""}}}
  ],
  "evidence": [
    {"snippet": "Ratings were marked as deferred (undecided) in case of inconsistency", "page": 1, "source": null,
     "field": ["experiments[0].conditions[0].Final_Decision", "experiments[0].conditions[1].Final_Decision"]},
    {"snippet": "(4) Human Rater 1 & Claude-3-Opus 1878/1989 (94%)", "page": 7, "source": "Table 3", "field": "experiments[0].conditions[0].measures[0]"}
  ]
}"""

ALL_FIXTURES = {
    "forestplot": FORESTPLOT_JSON,
    "masem": MASEM_JSON,
    "masem_rich": MASEM_RICH_JSON,
    "masem_v2": MASEM_V2_JSON,
    "masem_legacy_entry_conf": MASEM_LEGACY_ENTRY_CONF_JSON,
}
