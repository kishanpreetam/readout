-- Active Phase 2, 2/3 and 3 studies whose primary completion (actual or anticipated) falls in the
-- 12 months after the snapshot date, one row per study and organization (lead or collaborator).
{% set as_of = "date '" ~ var('as_of_date') ~ "'" %}

select
    b.org_id,
    o.org_name,
    o.cik,
    o.ticker,
    b.sponsor_role,
    t.nct_id,
    t.brief_title,
    t.phase,
    t.phase_label,
    t.phase in ('PHASE3', 'PHASE2_PHASE3') as is_phase3,
    t.overall_status,
    t.primary_completion_date,
    t.primary_completion_date_type,
    t.primary_completion_date_precision,
    t.enrollment_count,
    t.conditions,
    t.interventions,
    datediff('day', {{ as_of }}, t.primary_completion_date) as days_until_primary_completion
from {{ ref('bridge_trial_organization') }} as b
join {{ ref('dim_trial') }} as t using (nct_id)
join {{ ref('dim_organization') }} as o using (org_id)
where t.phase in ('PHASE2', 'PHASE2_PHASE3', 'PHASE3')
  and t.is_active
  and t.primary_completion_date >= {{ as_of }}
  and t.primary_completion_date < {{ as_of }} + interval {{ var('readout_window_months') }} month
