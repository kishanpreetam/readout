-- Pipeline summary per organization: studies by phase and status, and readouts in the next 12 months.
with trials as (
    select b.org_id, b.sponsor_role, t.*
    from {{ ref('bridge_trial_organization') }} as b
    join {{ ref('dim_trial') }} as t using (nct_id)
),

counts as (
    select
        org_id,
        count(*) as n_trials,
        count(*) filter (where sponsor_role = 'lead') as n_trials_as_lead,
        count(*) filter (where is_active) as n_active_trials,
        count(*) filter (where is_active and phase = 'PHASE1') as n_active_phase1,
        count(*) filter (where is_active and phase in ('PHASE1_PHASE2', 'PHASE2')) as n_active_phase2,
        count(*) filter (where is_active and phase in ('PHASE2_PHASE3', 'PHASE3')) as n_active_phase3,
        count(*) filter (where overall_status = 'TERMINATED') as n_terminated,
        count(*) filter (where overall_status = 'COMPLETED') as n_completed
    from trials
    group by org_id
),

readouts as (
    select
        org_id,
        count(*) as n_readouts_12m,
        count(*) filter (where is_phase3) as n_phase3_readouts_12m,
        min(primary_completion_date) filter (where is_phase3) as next_phase3_primary_completion
    from {{ ref('mart_upcoming_readouts') }}
    group by org_id
)

select
    o.org_id,
    o.org_name,
    o.cik,
    o.ticker,
    o.n_name_variants,
    coalesce(n.n_trials, 0) as n_trials,
    coalesce(n.n_trials_as_lead, 0) as n_trials_as_lead,
    coalesce(n.n_active_trials, 0) as n_active_trials,
    coalesce(n.n_active_phase1, 0) as n_active_phase1,
    coalesce(n.n_active_phase2, 0) as n_active_phase2,
    coalesce(n.n_active_phase3, 0) as n_active_phase3,
    coalesce(n.n_terminated, 0) as n_terminated,
    coalesce(n.n_completed, 0) as n_completed,
    coalesce(r.n_readouts_12m, 0) as n_readouts_12m,
    coalesce(r.n_phase3_readouts_12m, 0) as n_phase3_readouts_12m,
    r.next_phase3_primary_completion
from {{ ref('dim_organization') }} as o
left join counts as n using (org_id)
left join readouts as r using (org_id)
