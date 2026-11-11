-- One row per company and fiscal quarter end: balances at the quarter end and flows for the quarter.
with flows as (
    select
        cik,
        period_key,
        max(value) filter (where metric = 'rd_expense') as rd_expense,
        max(value) filter (where metric = 'net_income') as net_income,
        max(value) filter (where metric = 'operating_cash_flow') as operating_cash_flow,
        max(value) filter (where metric = 'revenue') as revenue,
        max(derivation) filter (where metric = 'operating_cash_flow') as operating_cash_flow_derivation
    from {{ ref('int_sec__quarterly_flows') }}
    group by cik, period_key
),

balances as (
    select
        cik,
        period_key,
        max(value) filter (where metric = 'cash_and_equivalents') as cash_and_equivalents,
        max(value) filter (where metric = 'short_term_investments') as short_term_investments
    from {{ ref('int_sec__quarterly_balances') }}
    group by cik, period_key
),

joined as (
    select
        coalesce(f.cik, b.cik) as cik,
        coalesce(f.period_key, b.period_key) as period_end,
        b.cash_and_equivalents,
        b.short_term_investments,
        f.rd_expense,
        f.net_income,
        f.operating_cash_flow,
        f.revenue,
        f.operating_cash_flow_derivation
    from flows as f
    full outer join balances as b
        on f.cik = b.cik and f.period_key = b.period_key
)

select
    j.cik,
    j.period_end,
    cast(strftime(j.period_end, '%Y%m%d') as integer) as date_key,
    year(j.period_end) as calendar_year,
    quarter(j.period_end) as calendar_quarter,
    j.cash_and_equivalents,
    j.short_term_investments,
    case when j.cash_and_equivalents is not null
         then j.cash_and_equivalents + coalesce(j.short_term_investments, 0) end as cash_and_short_term_investments,
    j.rd_expense,
    j.net_income,
    j.operating_cash_flow,
    j.revenue,
    -- burn is cash used by operations; when a quarter has no cash flow figure the net loss stands in
    case
        when j.operating_cash_flow is not null then -j.operating_cash_flow
        when j.net_income is not null then -j.net_income
    end as cash_burn,
    case
        when j.operating_cash_flow is not null then 'operating_cash_flow'
        when j.net_income is not null then 'net_income'
    end as burn_basis,
    j.operating_cash_flow_derivation
from joined as j
join {{ ref('dim_company') }} as c using (cik)
where j.period_end <= date '{{ var("as_of_date") }}'
