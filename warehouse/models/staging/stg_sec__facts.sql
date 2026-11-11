-- XBRL facts for the metrics the warehouse uses. Each metric can come from several us-gaap concepts;
-- concept_priority says which one wins when a company reports more than one for the same period.
with source as (
    select * from {{ source('raw', 'sec_facts') }}
),

mapped as (
    select
        f.*,
        m.metric,
        m.concept_priority,
        m.period_kind
    from source f
    join (values
        ('CashAndCashEquivalentsAtCarryingValue', 'cash_and_equivalents', 1, 'instant'),
        ('CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents', 'cash_and_equivalents', 2, 'instant'),
        ('CashAndDueFromBanks', 'cash_and_equivalents', 3, 'instant'),
        ('ShortTermInvestments', 'short_term_investments', 1, 'instant'),
        ('MarketableSecuritiesCurrent', 'short_term_investments', 2, 'instant'),
        ('AvailableForSaleSecuritiesDebtSecuritiesCurrent', 'short_term_investments', 3, 'instant'),
        ('ResearchAndDevelopmentExpense', 'rd_expense', 1, 'duration'),
        ('ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost', 'rd_expense', 2, 'duration'),
        ('NetIncomeLoss', 'net_income', 1, 'duration'),
        ('ProfitLoss', 'net_income', 2, 'duration'),
        ('NetCashProvidedByUsedInOperatingActivities', 'operating_cash_flow', 1, 'duration'),
        ('NetCashProvidedByUsedInOperatingActivitiesContinuingOperations', 'operating_cash_flow', 2, 'duration'),
        ('Revenues', 'revenue', 1, 'duration'),
        ('RevenueFromContractWithCustomerExcludingAssessedTax', 'revenue', 2, 'duration')
    ) as m(concept, metric, concept_priority, period_kind)
        on f.concept = m.concept
    where f.unit = 'USD' and f.value is not null
)

select
    cik,
    metric,
    concept,
    concept_priority,
    period_kind,
    value,
    try_cast(period_start as date) as period_start,
    try_cast(period_end as date) as period_end,
    datediff('day', try_cast(period_start as date), try_cast(period_end as date)) as duration_days,
    fiscal_year,
    fiscal_period,
    form,
    try_cast(filed as date) as filed_date,
    accession,
    frame
from mapped
where try_cast(period_end as date) is not null
