-- Three-month values for income statement and cash flow metrics.
-- 10-Q filings report some flows only year to date (6 and 9 months) and 10-K filings report 12 months,
-- so a quarter is either reported directly or derived as a longer year-to-date value minus the shorter
-- one with the same start date (for example the fourth quarter is the fiscal year minus nine months).
with flows as (
    select *
    from {{ ref('int_sec__facts_latest') }}
    where period_kind = 'duration' and duration_days between 80 and 380
),

reported as (
    select cik, metric, concept, concept_priority, period_end, value, 'reported' as derivation, 1 as derivation_rank
    from flows
    where duration_days between 80 and 100
),

derived as (
    select
        longer.cik,
        longer.metric,
        longer.concept,
        longer.concept_priority,
        longer.period_end,
        longer.value - shorter.value as value,
        'ytd_difference' as derivation,
        2 as derivation_rank
    from flows as longer
    join flows as shorter
        on longer.cik = shorter.cik
        and longer.concept = shorter.concept
        and longer.period_start = shorter.period_start
        and shorter.period_end < longer.period_end
        and datediff('day', shorter.period_end, longer.period_end) between 80 and 100
),

candidates as (
    select * from reported
    union all
    select * from derived
),

keyed as (
    select
        *,
        -- align fiscal quarters that end a few days off a month end (52/53 week years) to that month end
        last_day(period_end - interval 15 day)::date as period_key
    from candidates
)

select * exclude (rn)
from (
    select
        *,
        row_number() over (
            partition by cik, metric, period_key
            order by derivation_rank, concept_priority, period_end desc
        ) as rn
    from keyed
)
where rn = 1
