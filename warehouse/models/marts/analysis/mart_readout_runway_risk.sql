-- Public companies (organizations linked to an SEC registrant) with at least one Phase 3 or Phase 2/3
-- readout in the next 12 months, with their cash runway. funding_risk is true when runway is under 12 months.
select
    p.org_id,
    p.org_name,
    p.cik,
    p.ticker,
    p.n_phase3_readouts_12m,
    p.next_phase3_primary_completion,
    r.latest_period_end,
    r.cash_and_short_term_investments,
    r.avg_quarterly_burn,
    r.runway_months,
    r.runway_months_remaining,
    r.runway_band,
    r.is_stale,
    coalesce(r.runway_months < {{ var('runway_alert_months') }}, false) as funding_risk
from {{ ref('mart_org_pipeline') }} as p
left join {{ ref('mart_cash_runway') }} as r using (cik)
where p.cik is not null
  and p.n_phase3_readouts_12m > 0
