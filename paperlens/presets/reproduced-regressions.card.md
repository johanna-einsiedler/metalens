# Robustness at Scale: re-estimated regressions

The output of the pipeline's re-estimation stage (appendix E.4 of the paper): for every regression of the blinded grid, a sandboxed agent ran the deposited Stata code and filled in the withheld cells, or recorded why it could not. One entry per regression; the cells are positioned by the row index of the published table, so the grade step can compare them cell by cell at printed precision.

This dataset is never reviewed on its own: the robustness workflow reads it against the regression tables of the same paper and writes the verdict on each column there.
