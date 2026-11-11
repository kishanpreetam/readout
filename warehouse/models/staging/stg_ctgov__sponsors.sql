-- Lead sponsors and collaborators in one long table: one row per study and organization.
with studies as (
    select nct_id, lead_sponsor_name, lead_sponsor_class from {{ source('raw', 'ct_studies') }}
),

collaborators as (
    select nct_id, ord, trim(name) as sponsor_name, class as sponsor_class
    from {{ source('raw', 'ct_collaborators') }}
    where name is not null
),

unioned as (
    select nct_id, trim(lead_sponsor_name) as sponsor_name, lead_sponsor_class as sponsor_class,
           'lead' as sponsor_role, 0 as sponsor_order
    from studies
    union all
    select nct_id, sponsor_name, sponsor_class, 'collaborator', ord + 1
    from collaborators
)

-- a few studies list the same organization twice; keep its first (most senior) role
select * exclude (rn)
from (
    select *, row_number() over (partition by nct_id, sponsor_name order by sponsor_order) as rn
    from unioned
)
where rn = 1
