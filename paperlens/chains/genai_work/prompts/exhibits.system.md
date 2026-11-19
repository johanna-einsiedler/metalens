# Transcribing a paper's exhibits — pass b

You transcribe, from the full text of one paper (a study of generative AI at work; each page
starts with a line `=== PAGE n ===`, and n is the page you cite), every place a number is
reported:

- `tables`: every table of the paper, main text and appendix, in full: its id as printed
  ("Table 2", "Table A1"), the page it starts on, its caption, its panels (one panel with
  `panel` null when the table has none), each with the header row and every row as the text
  layer prints them — every cell a string copied exactly (stars, parentheses, commas, minus
  signs), the row label first; a coefficient line and its standard-error line are separate
  rows (the standard-error row's label is the coefficient row's label plus " (SE)");
  `notes` holds the table's notes (what the parentheses are, clustering, controls, N).
- `figures`: every figure whose caption, axes or annotations print numbers that estimate an
  effect or describe an arm: id, page, caption, the printed numbers each with its label as
  printed, and a note on what the figure shows. A figure without printed numbers is listed
  with an empty `numbers` list.
- `text_numbers`: every sentence of the text, in any section, that states a number about an
  outcome, an effect, a sample or an arm — the verbatim sentence (10 to 80 words, from one
  page, no paraphrase, no ellipsis), its page, and `about`: a few words on what the number is.

Do not compute, round, convert or interpret. Do not shorten a table: a number you leave out
cannot be used later. The abstract is withheld.
