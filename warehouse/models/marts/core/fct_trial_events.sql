-- One row per study milestone: start, primary completion and completion, each actual or anticipated.
with studies as (
    select * from {{ ref('stg_ctgov__studies') }}
),

events as (
    select nct_id, 'start' as event_type, start_date as event_date,
           start_date_type as date_type, start_date_precision as date_precision
    from studies where start_date is not null
    union all
    select nct_id, 'primary_completion', primary_completion_date,
           primary_completion_date_type, primary_completion_date_precision
    from studies where primary_completion_date is not null
    union all
    select nct_id, 'completion', completion_date,
           completion_date_type, completion_date_precision
    from studies where completion_date is not null
)

select
    e.nct_id || ':' || e.event_type as event_id,
    e.nct_id,
    t.lead_org_id,
    e.event_type,
    e.event_date,
    cast(strftime(e.event_date, '%Y%m%d') as integer) as date_key,
    e.date_type,
    e.date_precision
from events as e
join {{ ref('dim_trial') }} as t using (nct_id)
