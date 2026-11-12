# Readout

A biotech research engine that turns ClinicalTrials.gov records into a clean, tested warehouse of who is running
which trials and what reads out next, with an evaluated AI layer on top. It is built to link trial pipelines to
company financials from SEC EDGAR; in the shipped run SEC refused automated requests without a contact email, so
the financial side is built and tested on a synthetic fixture but holds no real data (details below).

Open `results/report.html` for the tables and charts.

## Results (snapshot of 2026-11-03)

Every number below comes from a run saved under `results/`. The gold labels, gold SQL and error reviews
behind them were made by a Claude-based AI agent following the written rules in `docs/labeling.md`; no
person has reviewed them yet (see Limitations).

| What | Result | Source file |
|---|---|---|
| Studies loaded | 49,798 interventional, industry-sponsored studies with primary completion in 2020 or later | `results/data_manifest.json` |
| Sponsor resolution | 10,998 sponsor name strings resolved to 10,508 organizations | `results/resolution/summary.json` |
| Resolution precision, 20 largest groups | 97.5% of names placed in a group belong there (209 labeled member names) | `results/resolution/metrics.json` |
| Resolution recall, 20 largest groups | 84.8% of the groups' trials attributed to the right group; 37.8% of their names | same |
| Recall by kind of name | spelling or unit variants 83.3% (35 of 42); names that state their parent 75.0% (36 of 48); separately named subsidiaries 6.7% (8 of 119) | same |
| Long-tail audit | 83.1% of audited merges correct (49 of 59); 8.6% of look-alike pairs kept apart were in fact the same company (5 of 58) | same |
| Upcoming readouts | 2,118 active Phase 2, 2/3 or 3 studies with primary completion in the next 12 months; 1,003 of them Phase 3 or 2/3, across 546 organizations | `results/report.html` |
| Warehouse | 24 dbt models, 74 data tests: 73 pass, 1 warning by design (6 studies whose primary completion precedes their start) | `results/dbt_build.json` |
| Entity extraction, 80 studies | F1 for conditions 0.688 with the model vs 0.420 for a dictionary baseline; interventions 0.846 vs 0.625 | `results/extraction/metrics.json` |
| Extraction error review | of 40 sampled model disagreements with the registry, 0 were clear extraction errors; 16 were the same concept in other words, 14 differed in granularity | `results/extraction/error_review.csv` |
| Text-to-SQL, 48 questions | 48 of 48 correct with schema plus documentation; 45 of 48 with the schema alone (all 3 misses: phase codes) | `results/qa/metrics.json` |
| Model use | 104 calls to claude-opus-5-5 (8 extraction batches, 96 SQL answers), reported cost $2.78 | `results/llm_usage.json` |
| Tests | 34 pytest tests pass, including 3 that run the dbt project on a synthetic company to check burn and runway math | `tests/` |

## Readout Globe (added 2026-11-11)

A local web app that puts the 49,798 trials on a 3D globe: one point per site location, filters for phase,
status, lead sponsor and text, a month slider that shows the trials active at that time, a switch for readouts
due in the next 12 months, and a question box that answers plain-English questions with the text-to-SQL setup
benchmarked above. Run `make sites` once, then `.venv/bin/python -m globe` and open http://127.0.0.1:8091.

| What | Result | Source file |
|---|---|---|
| Trial sites | 942,520 site rows for the 49,798 trials (fetched 2026-11-11); 915,378 (97.1%) have coordinates | `results/sites/summary.json` |
| Coverage | 46,587 trials (93.6%) have at least one mapped site, at 11,285 locations in 156 countries | same |
| Warehouse | 3 new dbt models (`stg_ctgov__sites`, `fct_trial_sites`, `dim_site_location`) with 23 data tests, all passing | `results/sites/dbt_build.json` |
| Questions about places | 20 of 20 correct on location questions (country, state, city, sponsor, phase, readout timing), median 3.8 s per answer, claude-opus-5-5 | `results/globe_qa/metrics.json` |
| Tests | 262 pytest tests pass: the 34 above, 5 for sites, 129 for the API and 94 for the question set | `tests/` |

How it works:

1. **Sites.** `readout/sites.py` repeats the trial query with the location fields (50 pages) and loads
   `raw.ct_sites`. dbt cleans the coordinates and keys each point by `geo_key` (latitude and longitude to 4
   decimals). ClinicalTrials.gov coordinates are city-level, so a point is a city, not a hospital.
2. **API.** FastAPI in `globe/`, bound to 127.0.0.1:8091. Every filter is a bound parameter, every query is
   read-only and aggregated in DuckDB per request. The contract is `globe/API.md`.
3. **Questions.** The same prompt and sandbox as the benchmark, plus notes on the two site tables. The model
   answers at the level asked (country, state, city or trial) and returns `geo_key` or `nct_id` so the globe can
   highlight the places or trials. Its calls are counted in `data/globe/llm_usage.json`, apart from the
   benchmark, with a cap of 200 (`GLOBE_LLM_BUDGET`).
4. **Page.** Plain HTML, CSS and one JavaScript module with MapLibre GL JS 5.24.1 (vendored) on OpenFreeMap's
   light basemap, with a Natural Earth fallback so the globe still draws if the basemap is unreachable.
5. **Security.** The server refuses other Host headers, sends a strict Content-Security-Policy and related
   headers on every response, accepts questions only as same-origin JSON, and runs model SQL in the sandbox.

## What did not run: SEC EDGAR

SEC's fair access policy asks automated clients to declare who they are. This project declares itself with a
User-Agent naming the project and `github.com/kishanpreetam`, and by design sends no email address. SEC answered
every such request with HTTP 403 (www.sec.gov, data.sec.gov and efts.sec.gov alike), on 2026-11-03 and again
about nine hours later, so the pipeline stopped SEC fetching at the first refusal and continued without it
(`results/sec_access.json`). As a result the company universe, `fct_financials`, `mart_cash_runway` and
`mart_readout_runway_risk` are empty in this run.

Everything for that path is in place: the universe rules (Nasdaq or NYSE, SIC 2834 or 2836, US GAAP R&D
reported for 2024, the 50 largest R&D spenders plus a seeded random 100 if more than 200 qualify), the
companyfacts parser, the sponsor-to-SEC linker, quarterly burn from year-to-date cash flow, and the runway and
risk marts. `tests/test_dbt_financials.py` runs those dbt models on a synthetic company and checks the results.
To fill them with real data, set `READOUT_SEC_USER_AGENT` to a declared contact string and run `make all`.

## How it works

1. **Fetch.** ClinicalTrials.gov API v2, 50 pages of 1,000 studies, saved gzip compressed in `data/raw` with a
   manifest of URL, size and SHA-256 for every file. Requests are rate limited and size capped, and only JSON
   is parsed.
2. **Load.** Python flattens the JSON into typed tables in a DuckDB `raw` schema.
3. **Resolve sponsors.** Names are normalized (legal suffixes, SEC state suffixes, R&D and A/S forms, glued
   suffixes such as "ModernaTX"). Candidate pairs come from blocking on normalized name, core name and first
   word plus character 3-gram TF-IDF nearest neighbors, so 22,323 pairs are compared instead of 60 million.
   Pair rules (exact, core with compatible industry words, affiliate, fuzzy) merge spellings of one company.
   Ownership and alias clauses that sponsors write into their names ("Seagen, a wholly owned subsidiary of
   Pfizer", "X d/b/a Y") are parsed and roll subsidiaries up to parents, closed with union-find. No hand-made
   alias list is used, which keeps the labels independent of the resolver.
4. **Warehouse.** dbt on DuckDB: staging views, intermediate views (latest XBRL fact per period, quarters from
   year-to-date values), and marts: `dim_trial`, `dim_organization`, `dim_company`, `dim_date`,
   `bridge_trial_organization`, `bridge_trial_company`, `fct_trial_events` (start, primary completion and
   completion, actual or anticipated), `fct_financials`, `mart_upcoming_readouts`, `mart_org_pipeline`,
   `mart_cash_runway` and `mart_readout_runway_risk`. Window functions handle deduplication, trailing 12-month
   burn and ranking. Tests cover unique, not null, relationships, accepted values and custom expressions.
5. **Evaluate resolution** against the gold labels (see `docs/labeling.md`).
6. **Extract entities** (conditions, interventions, primary endpoint) from trial text with a model and with a
   dictionary baseline built only from other studies, scored against the registry's structured fields.
7. **Question answering.** 48 questions with gold SQL written in advance. The model writes DuckDB SQL under two
   prompts (schema only; schema plus column meanings, code values and three worked examples). With the
   documented prompt, an SQL error gets one repair attempt (none was needed). Model SQL runs in a sandbox:
   sqlglot must parse exactly one SELECT, a deny list blocks file, network and settings functions, and DuckDB
   opens the file read only with external access off and its configuration locked, under a timeout and a row cap.
8. **Report.** `results/report.html`, a single page with inline charts and a table view for each.

Definitions used throughout: "active" means recruiting, not yet recruiting, active not recruiting or enrolling
by invitation. "Next 12 months" means on or after the snapshot date and before the same date a year later.
Month-only registry dates are read as the last day of the month. Runway months = 3 x (cash, equivalents and
short-term investments) / average quarterly cash burn over the trailing 12 months, with burn taken from
operating cash flow (net loss when no cash flow figure exists).

## Run it

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv installs the pinned packages in `.venv`).

```sh
make all       # setup, fetch, load, resolve, warehouse, evaluate, extract, bench, report, test
make rescore   # recompute label-based metrics and the report; no network, no model calls
make test
make sites     # fetch trial site locations and build the site models (about a minute)
.venv/bin/python -m globe              # Readout Globe on http://127.0.0.1:8091
.venv/bin/python -m globe.eval.run_eval  # the 20 location questions, about 20 model calls
```

While the globe server runs it holds a read-only connection, so stop it before `make sites` or `make all`.

The first run takes about 10 minutes, most of it model calls. Downloads and model answers are cached, so later
runs make no new requests and no new model calls and finish in about 2 minutes.

Model access, in order of preference:

- `ANTHROPIC_API_KEY` in the environment: the official `anthropic` SDK is used (model `claude-opus-5-5` by
  default, `READOUT_MODEL` to change it). No key is stored anywhere in the repo.
- otherwise the `claude` command line, if installed and signed in, run headless with no tools, no MCP servers
  and no saved settings, from an empty temporary folder, at low effort.
- otherwise none: the model steps keep the saved results and say so.

A hard cap (`READOUT_LLM_MAX_CALLS`, default 120) stops runaway use, and a usage-limit reply stops all further
calls.

## Sources and terms

- **ClinicalTrials.gov API v2** (U.S. National Library of Medicine), https://clinicaltrials.gov/data-api/api.
  Terms: https://clinicaltrials.gov/about-site/terms-conditions. Records are used as published, credited to
  ClinicalTrials.gov with the retrieval date, and not altered.
- **SEC EDGAR** (submissions, XBRL frames and companyfacts, company tickers). Public data; automated access must
  follow SEC's fair access rules (declared User-Agent, at most 10 requests per second; this code uses 5), see
  https://www.sec.gov/developer. Not used in this run, see above.
- **Readout Globe map.** Basemap tiles from OpenFreeMap (https://openfreemap.org), built on OpenMapTiles and
  OpenStreetMap data (ODbL); the page shows their attribution. MapLibre GL JS is BSD-3-Clause and is served
  from `globe/static/vendor/` with its license. The fallback land outline is Natural Earth (public domain).

Raw data stays in `data/` (git ignored) and is never committed.

## Repository layout

```
readout/          Python package: fetch, load, names, clauses, resolve, linking, evaluation, extraction,
                  text-to-SQL benchmark, SQL sandbox, LLM client, report
warehouse/        dbt project (models, tests, macros)
globe/            Readout Globe: API server, question answering, eval set, and the page in globe/static
labels/           gold labels for sponsor resolution, with the search terms used to find candidates
docs/labeling.md  exactly how every label was made
results/          metrics as JSON, predictions, reviews, report.html, data manifest
tests/            pytest suite
```

## Limitations

- No SEC data in this run, so no financial results; see above.
- Resolution recall is low for subsidiaries whose names never mention the parent (Genentech for Roche, Janssen
  for Johnson & Johnson): 6.7% for that kind of name. A curated alias table or SEC subsidiary lists would fix
  most of it; neither is used, so the evaluation stays honest.
- Gold groups cover the 20 largest sponsors; the long tail is measured by a 120-pair audit. The gold sets were
  built by keyword search, so a member with an unrelated name could be missing from both the gold and the
  resolver, which would overstate recall.
- Registry fields are a silver standard for extraction: the text may not mention a registered item, and the
  registry may omit an item the text names.
- The text-to-SQL benchmark reached its ceiling with documentation (48 of 48); harder or more open questions
  would be needed to separate stronger prompts.
- Anticipated dates are sponsors' forecasts and slip often; a readout date here is a registry date, not a data
  release.
- All labels, the gold SQL and the error reviews were made by one Claude-based AI agent, the same model
  family that Readout evaluates, following `docs/labeling.md`. No person has reviewed them yet and there is
  no second-annotator agreement measure, so treat them as a careful first pass. The text-to-SQL and
  extraction results in particular may favor the model's own habits.
- Globe points are city centroids from the registry, not hospitals. 2.9% of site rows have no coordinates
  (27% of Japanese sites, whose cities are listed as wards), and 284 trials have sites but none mapped.
- Sites were fetched on 2026-11-11, eight days after the trial snapshot. Studies registered since then are left
  out, and a site list may have changed in between.
- The 20 location questions and their gold SQL were written with an AI agent and checked against the
  warehouse (every query runs; no answer is empty or tied), but not reviewed by hand. Like the benchmark above,
  the set reached its ceiling.
- Country and state answers appear as a table only; the globe highlights cities, sites and trials.

## Not investment advice

Readout is a research and portfolio project. Nothing in this repository or its report is investment advice or a
recommendation to buy or sell any security.

## License

MIT, see `LICENSE`.
