-- Balance sheet values (instants) at each quarter end.
with balances as (
    select
        *,
        last_day(period_end - interval 15 day)::date as period_key
    from {{ ref('int_sec__facts_latest') }}
    where period_kind = 'instant'
)

select cik, metric, concept, period_end, period_key, value, filed_date, form
from (
    select
        *,
        row_number() over (
            partition by cik, metric, period_key
            order by concept_priority, period_end desc, filed_date desc
        ) as rn
    from balances
)
where rn = 1
