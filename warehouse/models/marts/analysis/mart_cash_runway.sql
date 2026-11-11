-- Cash runway per company at its latest reported quarter.
--   average quarterly burn = mean cash burn over the quarters ending in the trailing 12 months (at least 2)
--   runway_months          = 3 x (cash, equivalents and short-term investments) / average quarterly burn
-- A company that is not burning cash on average gets no runway figure (band 'not_burning').
{% set as_of = "date '" ~ var('as_of_date') ~ "'" %}

with fin as (
    select * from {{ ref('fct_financials') }}
),

trailing_12m as (
    select
        *,
        avg(cash_burn) over w as avg_quarterly_burn,
        count(cash_burn) over w as burn_quarters,
        sum(rd_expense) over w as rd_expense_ttm,
        sum(net_income) over w as net_income_ttm
    from fin
    window w as (partition by cik order by period_end range between interval 11 month preceding and current row)
),

latest as (
    select *
    from trailing_12m
    where cash_and_equivalents is not null
    qualify row_number() over (partition by cik order by period_end desc) = 1
),

runway as (
    select
        l.cik,
        c.company_name,
        c.ticker,
        l.period_end as latest_period_end,
        datediff('month', l.period_end, {{ as_of }}) as months_since_period_end,
        l.cash_and_equivalents,
        l.short_term_investments,
        l.cash_and_short_term_investments,
        l.avg_quarterly_burn,
        l.burn_quarters,
        l.rd_expense_ttm,
        l.net_income_ttm,
        case when l.burn_quarters >= 2 and l.avg_quarterly_burn > 0
             then round(3 * l.cash_and_short_term_investments / l.avg_quarterly_burn, 1) end as runway_months,
        case when l.burn_quarters >= 2 and l.avg_quarterly_burn > 0
             then round(3 * l.cash_and_equivalents / l.avg_quarterly_burn, 1) end as runway_months_cash_only
    from latest as l
    join {{ ref('dim_company') }} as c using (cik)
)

select
    *,
    case when runway_months is not null
         then round(runway_months - months_since_period_end, 1) end as runway_months_remaining,
    case
        when burn_quarters < 2 then 'insufficient_data'
        when avg_quarterly_burn <= 0 then 'not_burning'
        when runway_months < {{ var('runway_alert_months') }} then 'under_12_months'
        when runway_months < 24 then '12_to_24_months'
        else 'over_24_months'
    end as runway_band,
    months_since_period_end > 6 as is_stale
from runway
