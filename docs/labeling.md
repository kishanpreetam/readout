# How the labels were made

Every accuracy number in this project is measured against labels in `labels/` or in `results/`. This page says
exactly how each set was produced, so anyone can check, dispute or redo a label. A Claude-based AI agent, the
same model family the project evaluates, judged every label individually, following the rules below, on
2026-11-03 and 2026-11-04. No person has reviewed the labels yet. The resolver was frozen before the
labels were scored; no rule was changed after looking at the scores.

## 1. Gold corporate groups (`labels/resolution_gold_groups.csv`)

**Which groups.** The 20 corporate groups behind the 20 sponsor names that lead the most studies in the
snapshot (AstraZeneca, Pfizer, Novartis, Eli Lilly, Merck & Co., AbbVie, Roche, GSK, Jiangsu Hengrui,
Boehringer Ingelheim, Bristol-Myers Squibb, Johnson & Johnson, Sanofi, Novo Nordisk, Amgen,
Sino Biopharmaceutical, Takeda, Regeneron, Bayer, Incyte). The groups were chosen from raw sponsor names,
not from the resolver's output.

**Finding candidates.** For each group, all 10,998 sponsor names were searched for the terms in
`labels/gold_group_keywords.json`: the company's own name and the names of subsidiaries and acquired companies
known to the labeler (for example "genentech", "chugai" and "spark therapeutics" for Roche). That produced 271
candidate names. One more name ("Vaccinex Inc.") was added because the resolver put it inside a gold group,
which made 272.

**Judging each candidate.** A name was labeled a member of a group when the organization it names was the
group's parent company or a company or unit controlled by it on the snapshot date (2026-11-03). The rules:

1. Spelling variants, country affiliates and business units count (Pfizer Japan, Janssen-Cilag).
2. Acquired companies count, because their programs now belong to the buyer (Seagen under Pfizer).
3. Divested or spun-off units do not count; they belong to their new owner (Sandoz is not Novartis,
   Haleon is not GSK, Kenvue and the former J&J consumer units are not Johnson & Johnson).
4. An ownership clause written in the sponsor name is accepted unless the relationship is known to have ended
   (Opella was labeled not Sanofi because control passed to CD&R in 2025).
5. Buying one drug from a company does not make the seller part of the buyer (Boston Pharmaceuticals is not GSK).
6. Unrelated companies with similar names are not members (Abbott is not AbbVie, Merck KGaA is not Merck & Co.).
7. When ownership on the snapshot date could not be established, the name was labeled UNSURE and left out of
   every metric (5 names, each with a reason in the file).

Result: 209 member names, 58 non-members, 5 unsure.

**Limit.** Members whose names contain none of the search terms cannot be in the gold set, so recall can be
overstated for groups with many differently named subsidiaries.

## 2. Long-tail pair audit (`labels/resolution_pair_audit.csv`)

Outside the 20 gold groups, a seeded random sample (seed 11) of 120 pairs:

- 60 pairs drawn from organizations the resolver built from two or more names (one random pair from each of
  60 random organizations). This estimates precision of merges.
- 60 pairs of names that look alike (character 3-gram cosine of at least 0.6) but were kept apart. This
  estimates how often a real match was missed among similar names.

Each pair was labeled `same` (same company or same corporate group on the snapshot date), `different`, or
`unsure` (excluded), using the names, the registry and public company information. Notes explain every
`different` merge and every `same` miss.

## 3. Extraction disagreements (`results/extraction/error_review.csv`)

The model's extractions disagreed with the registry fields 114 times on the 80 sampled studies. A seeded
random sample of 40 (seed 5) was read against the study title and summary and put in one category:

- `same_concept_different_wording`: correct, but worded differently from the registry (COVID-19 vs SARS-CoV-2 infection)
- `granularity`: broader or narrower than the registry entry (knee osteoarthritis vs osteoarthritis)
- `valid_but_not_registered`: named in the text as studied or used, but not in the registry field
- `registry_item_not_in_text`: the registry lists it, the text never mentions it
- `extraction_error`: wrong or invented (none were found)

## 4. Text-to-SQL failures (`results/qa/failure_review.csv`)

Every wrong answer in the benchmark (3 of 96) was read and given a cause. Gold answers themselves come from
SQL written in advance in `readout/qa.py`, run on the warehouse; ties were checked so that "which one" questions
have a single correct answer.

## Redoing the labels

Edit the CSV files and run `make rescore`. It recomputes every metric that depends on a label and rebuilds the
report, with no network requests and no model calls.
