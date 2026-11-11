-- One row per resolved organization (a corporate group): every spelling and stated subsidiary of a sponsor
-- name rolled up by the resolver. cik is filled when one of its names links to an SEC company.
with names as (
    select * from {{ ref('stg_resolution__sponsor_organizations') }}
),

roles as (
    select s.nct_id, s.sponsor_role, n.org_id, n.sponsor_name
    from {{ ref('stg_ctgov__sponsors') }} as s
    join names as n using (sponsor_name)
),

counts as (
    select
        org_id,
        count(distinct nct_id) as n_trials,
        count(distinct nct_id) filter (where sponsor_role = 'lead') as n_trials_as_lead,
        count(distinct nct_id) filter (where sponsor_role = 'collaborator') as n_trials_as_collaborator
    from roles
    group by org_id
),

name_weights as (
    select org_id, sponsor_name, count(distinct nct_id) as n_trials
    from roles
    group by org_id, sponsor_name
),

company_link as (
    -- the SEC company linked by the organization's names, weighted by their trials
    select org_id, cik
    from (
        select w.org_id, l.cik, sum(w.n_trials) as weight
        from name_weights as w
        join {{ ref('stg_links__sponsor_company') }} as l using (sponsor_name)
        where l.cik is not null
        group by w.org_id, l.cik
    )
    qualify row_number() over (partition by org_id order by weight desc, cik) = 1
),

orgs as (
    select org_id, any_value(org_name) as org_name, max(n_name_variants) as n_name_variants,
           count(*) filter (where name_clause = 'subsidiary') as n_names_stating_a_parent
    from names
    group by org_id
)

select
    o.org_id,
    o.org_name,
    o.n_name_variants,
    o.n_names_stating_a_parent,
    coalesce(c.n_trials, 0) as n_trials,
    coalesce(c.n_trials_as_lead, 0) as n_trials_as_lead,
    coalesce(c.n_trials_as_collaborator, 0) as n_trials_as_collaborator,
    k.cik,
    d.ticker,
    d.company_name as sec_company_name
from orgs as o
left join counts as c using (org_id)
left join company_link as k using (org_id)
left join {{ ref('dim_company') }} as d using (cik)
