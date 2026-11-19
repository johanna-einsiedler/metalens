"""The codes of the GenAI-at-work chain's checks with their severities (the project's fail as
error): the record checks, the pass checks and the readings' disagreements. No imports: the
engine's check registry reads this at import time."""
from __future__ import annotations

CODES = {
    # the record (checks.py)
    "quote_not_found": "error", "number_not_in_quote": "error", "number_on_page_not_in_quote": "warn", "n_not_in_quote": "warn",
    "no_results": "error", "bad_date": "warn", "duplicate_task_id": "error", "duplicate_arm_id": "error", "several_baselines": "warn",
    "no_baseline": "error", "no_control_type": "warn", "baseline_ai_role": "warn", "control_type_on_treatment": "error", "arm_sample_missing": "warn",
    "n_arm_exceeds_sample": "error", "arm_unknown": "error", "vs_arm_unknown": "error", "vs_self": "error", "baseline_with_effect": "error",
    "baseline_contrast": "warn", "no_contrast_type": "warn", "contrast_mismatch": "warn", "no_estimand_type": "warn", "n_obs_below_n": "error",
    "n_exceeds_arm": "warn", "ci_reversed": "error", "effect_outside_ci": "error", "p_out_of_range": "error", "duplicate_main": "error",
    "no_baseline_row": "warn", "sign_disagrees": "error", "se_rounds_to_zero": "warn", "se_nonpositive": "error", "sd_negative": "error",
    "n_nonpositive": "error", "ratio_nonpositive": "error", "percent_below_minus_100": "error", "implausible_smd": "warn", "p_ci_inconsistent": "warn",
    "sign_corrected": "info", "grim_inconsistent": "warn", "percent_as_proportion": "error", "percent_vs_means": "warn", "effect_vs_means": "warn",
    "implausible_g": "warn", "not_in_options": "error", "other_without_term": "warn", "kind_off_performance": "warn", "kind_finding_mismatch": "warn",
    "low_confidence": "error",
    # the passes (pass_checks.py)
    "source_exhibit_unknown": "error", "source_row_unresolved": "warn", "source_column_unresolved": "warn", "source_page_off": "warn",
    "source_cell_mismatch": "error", "number_not_in_source_row": "warn", "exhibit_row_unreferenced": "warn", "exhibit_column_unreferenced": "warn",
    "exhibit_unverified": "warn", "estimate_unknown_estimand": "error", "estimand_unmapped": "warn", "no_main": "warn", "grid_gap": "info",
    "duplicate_source_cell": "warn", "duplicate_estimate": "warn", "duplicate_numbers_across_estimands": "warn", "ci_se_inconsistent": "warn",
    "p_se_inconsistent": "warn", "stars_se_inconsistent": "warn", "t_se_inconsistent": "warn", "effect_reported_mismatch": "error",
    "sd_looks_like_se": "error", "se_vs_sd_inconsistent": "warn", "sd_below_se": "warn", "pm_unlabelled": "warn", "sd_from_se_parentheses": "error",
    "se_from_sd_parentheses": "error", "sign_vs_text": "warn", "se_differs_from_table": "error", "se_missing_from_table": "warn",
    "n_obs_differs_from_table": "warn", "cell_text_mismatch": "warn",
    # the readings (match.py)
    "runs_disagree": "warn", "not_in_all_runs": "warn", "task_count_differs": "error", "runs_settled": "info", "runs_unsettled": "error", "runs_wording": "info",
}
