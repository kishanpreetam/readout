{#
  raw.ct_sites is loaded by `make sites`. Until that has run (a fresh checkout, or the small fixture in
  tests/test_dbt_financials.py), this model is empty instead of failing the whole build.
#}
{%- set source_relation = source('raw', 'ct_sites') -%}
{%- set source_loaded = execute and load_relation(source_relation) is not none -%}

with source as (
{%- if source_loaded %}
    select * from {{ source_relation }}
{%- else %}
    select
        null::varchar as nct_id, null::integer as site_order, null::varchar as facility, null::varchar as city,
        null::varchar as state, null::varchar as zip, null::varchar as country, null::varchar as site_status,
        null::double as lat, null::double as lon
    where false
{%- endif %}
),

cleaned as (
    select
        nullif(trim(nct_id), '') as nct_id,
        site_order,
        nullif(trim(facility), '') as facility,
        nullif(trim(city), '') as city,
        nullif(trim(state), '') as state,
        nullif(trim(zip), '') as zip,
        nullif(trim(country), '') as country,
        nullif(trim(site_status), '') as site_status,
        cast(lat as double) as lat,
        cast(lon as double) as lon
    from source
),

checked as (
    select
        *,
        -- a pair outside the valid ranges, or exactly (0, 0), is a placeholder rather than a place
        coalesce(lat between -90 and 90 and lon between -180 and 180 and not (lat = 0 and lon = 0), false)
            as has_coordinates
    from cleaned
)

select
    nct_id,
    site_order,
    facility,
    city,
    state,
    zip,
    country,
    site_status,
    case when has_coordinates then lat end as lat,
    case when has_coordinates then lon end as lon
from checked
