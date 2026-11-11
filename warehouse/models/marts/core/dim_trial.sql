with studies as (
    select * from {{ ref('stg_ctgov__studies') }}
),

conditions as (
    select nct_id, string_agg(condition_name, '; ' order by condition_order) as conditions, count(*) as n_conditions
    from {{ ref('stg_ctgov__conditions') }}
    group by nct_id
),

interventions as (
    select
        nct_id,
        string_agg(intervention_name, '; ' order by intervention_order) as interventions,
        count(*) as n_interventions,
        bool_or(intervention_type in ('DRUG', 'BIOLOGICAL', 'GENETIC')) as has_drug_or_biologic
    from {{ ref('stg_ctgov__interventions') }}
    group by nct_id
),

lead_org as (
    select b.nct_id, b.org_id as lead_org_id, o.org_name as lead_org_name, o.cik as lead_cik
    from {{ ref('bridge_trial_organization') }} as b
    join {{ ref('dim_organization') }} as o using (org_id)
    where b.sponsor_role = 'lead'
)

select
    s.nct_id,
    s.brief_title,
    s.official_title,
    s.acronym,
    s.phase,
    s.phase_label,
    s.phase_number,
    s.overall_status,
    s.is_active,
    s.why_stopped,
    s.study_type,
    s.enrollment_count,
    s.enrollment_type,
    s.allocation,
    s.primary_purpose,
    s.start_date,
    s.start_date_type,
    s.primary_completion_date,
    s.primary_completion_date_type,
    s.primary_completion_date_precision,
    s.completion_date,
    s.completion_date_type,
    s.first_posted_date,
    s.last_update_date,
    s.has_results,
    s.lead_sponsor_name,
    s.lead_sponsor_class,
    l.lead_org_id,
    l.lead_org_name,
    l.lead_cik,
    c.conditions,
    coalesce(c.n_conditions, 0) as n_conditions,
    i.interventions,
    coalesce(i.n_interventions, 0) as n_interventions,
    coalesce(i.has_drug_or_biologic, false) as has_drug_or_biologic,
    s.brief_summary
from studies as s
left join conditions as c using (nct_id)
left join interventions as i using (nct_id)
left join lead_org as l using (nct_id)
