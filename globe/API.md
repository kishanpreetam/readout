# Readout Globe: data and API contract

Readout Globe shows Readout's 49,798 industry trials on a 3D globe: one point per trial site location,
filters, a time slider, and plain-English questions answered by the same text-to-SQL setup Readout
benchmarked. This file is the contract between the data layer, the API server and the page.

## Tables (built by `make sites`, stored in `data/warehouse/readout.duckdb`)

`marts.fct_trial_sites`: one row per site listed on a trial, only for trials in `marts.dim_trial`.

| column | type | notes |
|---|---|---|
| nct_id | VARCHAR | not null, in `marts.dim_trial` |
| site_order | INTEGER | position of the site in the registry record, 1-based |
| facility | VARCHAR | |
| city | VARCHAR | |
| state | VARCHAR | |
| zip | VARCHAR | |
| country | VARCHAR | |
| site_status | VARCHAR | registry recruitment status of the site, may be null |
| lat | DOUBLE | null when the registry has no coordinates |
| lon | DOUBLE | null when the registry has no coordinates |
| geo_key | VARCHAR | `printf('%.4f,%.4f', lat, lon)`, null when no coordinates |

`(nct_id, site_order)` is unique. ClinicalTrials.gov coordinates are city-level, so a `geo_key` is
effectively a city point.

`marts.dim_site_location`: one row per `geo_key`.

| column | type | notes |
|---|---|---|
| geo_key | VARCHAR | unique, not null |
| lat | DOUBLE | |
| lon | DOUBLE | |
| city | VARCHAR | most common spelling at this point |
| state | VARCHAR | |
| country | VARCHAR | |
| n_sites | BIGINT | site rows at this point |
| n_trials | BIGINT | distinct trials at this point |

## API (served by `python -m globe` on `http://127.0.0.1:8091`)

All responses are JSON. All database access is read-only.

### Common filters

These query parameters apply to `/api/points` and `/api/points/{geo_key}/trials`. All are optional.

| param | meaning |
|---|---|
| phase | comma list of `dim_trial.phase` values: PHASE1, PHASE2, PHASE3, PHASE1_PHASE2, PHASE2_PHASE3, PHASE4, EARLY_PHASE1, NA, UNKNOWN |
| status | comma list of `dim_trial.overall_status` values |
| org_id | integer, the lead sponsor (`dim_trial.lead_org_id`) |
| q | 2 to 80 characters, matched case-insensitively against brief_title, conditions and interventions |
| active_on | `YYYY-MM`: trials with start_date on or before the last day of that month and primary_completion_date on or after its first day |
| due_within | integer 1 to 36: active trials (`is_active`) whose primary_completion_date falls between the as-of date and the as-of date plus that many months |
| nct | comma list of NCT IDs, at most 500, restricts results to those trials (used to highlight `/api/ask` answers) |

Invalid values return 400 `{"error": "...", "kind": "bad_filter"}`.

### GET /api/meta

```json
{"as_of": "2026-11-03", "sites_fetched_at": "2026-11-11T18:00:00Z",
 "trials": 49798, "trials_with_geo": 41000, "sites": 900000, "points": 25000,
 "phases": [{"value": "PHASE3", "label": "Phase 3", "count": 8239}],
 "statuses": [{"value": "RECRUITING", "label": "Recruiting", "active": true, "count": 8671}],
 "months": {"min": "2020-01", "max": "2030-12"}}
```

`as_of` is `readout.config.as_of_date()`, the trial snapshot date. The numbers above are examples.

### GET /api/points

GeoJSON, one feature per location with at least one matching trial, sorted by `trials` descending, at
most 20,000 features.

```json
{"type": "FeatureCollection",
 "summary": {"trials": 1234, "sites": 5678, "points": 910},
 "features": [{"type": "Feature",
   "geometry": {"type": "Point", "coordinates": [-71.0589, 42.3601]},
   "properties": {"geo_key": "42.3601,-71.0589", "city": "Boston", "state": "Massachusetts",
                  "country": "United States", "trials": 87, "due": 6, "top_org": "Example Pharma"}}]}
```

`trials` counts distinct matching trials with a site at the point. `due` counts those that are active
with primary completion within 12 months after `as_of`. `top_org` is the most frequent lead sponsor
among them. `summary` counts distinct trials, site rows and points across the whole response.

### GET /api/points/{geo_key}/trials

```json
{"geo_key": "42.3601,-71.0589", "city": "Boston", "state": "Massachusetts", "country": "United States",
 "total": 87,
 "trials": [{"nct_id": "NCT01234567", "title": "...", "phase": "PHASE3", "phase_label": "Phase 3",
             "status": "RECRUITING", "lead_org_name": "Example Pharma", "conditions": "...",
             "start_date": "2024-01-15", "primary_completion_date": "2027-03-01", "due": true,
             "facilities": ["Example Hospital"], "url": "https://clinicaltrials.gov/study/NCT01234567"}]}
```

At most 100 trials, ordered by primary_completion_date ascending, nulls last. Unknown `geo_key`: 404.

### GET /api/trial/{nct_id}

`nct_id` must match `^NCT\d{8}$`, otherwise 400; unknown: 404.

```json
{"nct_id": "NCT01234567", "title": "...", "official_title": "...", "phase": "PHASE3",
 "phase_label": "Phase 3", "status": "RECRUITING", "is_active": true, "lead_org_id": 12,
 "lead_org_name": "Example Pharma", "conditions": "...", "interventions": "...",
 "enrollment_count": 300, "start_date": "2024-01-15", "primary_completion_date": "2027-03-01",
 "due": false, "n_sites": 120, "n_countries": 14, "countries": ["United States"],
 "brief_summary": "...", "url": "https://clinicaltrials.gov/study/NCT01234567"}
```

### GET /api/orgs?q=pfi

Lead sponsors whose name contains `q` (2 to 60 characters), top 10 by trial count.

```json
{"orgs": [{"org_id": 12, "org_name": "Example Pharma", "n_trials": 1500}]}
```

### POST /api/ask

Request: `{"question": "..."}`, 1 to 300 characters.

The server turns the question into one DuckDB SELECT with Readout's text-to-SQL prompt plus notes on
the two site tables, checks it with `readout.sqlguard`, and runs it on a read-only, no-file-access
connection with a 20 second timeout and a 500 row cap. The model is told to return one row per trial
with a plain `nct_id` column when the answer is a list of trials, and to include a `geo_key` column
(grouping by it) when the answer is about places, never packing either into lists or strings. The
globe highlights the places in `geo_keys` when there are any, otherwise every site of the trials in
`nct_ids`.

```json
{"question": "Phase 3 trials near Boston finishing next year", "sql": "SELECT ...",
 "columns": ["nct_id", "brief_title"], "rows": [["NCT01234567", "..."]], "row_count": 42,
 "nct_ids": ["NCT01234567"], "geo_keys": [], "model": "claude-opus-5-5", "seconds": 6.1,
 "cached": false}
```

`rows` holds at most 50 rows for display; `row_count` is the number returned (at most 500);
`nct_ids` holds the distinct values of a column named `nct_id`, if there is one; `geo_keys` holds the
distinct values of a column named `geo_key`, if there is one. Both lists also accept list-valued cells
(flattened), keep only well-formed values (`^NCT\d{8}$`, `^-?\d+\.\d{4},-?\d+\.\d{4}$`) and stop at 500.
A `geo_key` is `lat,lon`, so the page can place it without another request.

Errors: 400 `bad_question`, 422 `unsafe_sql` (with the rejected `sql`), 429 `busy` (one question at a
time), 503 `llm_unavailable`, 504 `timeout`, 500 `query_failed`. Each error body is
`{"error": "...", "kind": "..."}`.

## Security

- The server binds 127.0.0.1 only and rejects requests whose Host header is not `127.0.0.1:8091` or
  `localhost:8091`.
- Filters are bound as SQL parameters, never pasted into SQL text.
- Model-written SQL goes through `readout.sqlguard`: one SELECT, deny-listed functions refused,
  `enable_external_access` off, read-only, timeout and row cap.
- Every response carries these headers:
  - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://tiles.openfreemap.org; font-src 'self'; connect-src 'self' https://tiles.openfreemap.org; worker-src 'self' blob:; child-src blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`
  - `X-Frame-Options: DENY`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- The page loads nothing from other origins except the OpenFreeMap basemap
  (`https://tiles.openfreemap.org`). MapLibre GL JS is vendored under `globe/static/vendor/`.
