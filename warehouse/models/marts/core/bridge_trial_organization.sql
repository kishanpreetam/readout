-- Studies and the organizations named on them (lead sponsor or industry collaborator). An organization
-- counts once per study, in its most senior role, even when the study names two of its units.
select nct_id, org_id, sponsor_role, sponsor_name
from (
    select
        s.nct_id,
        n.org_id,
        s.sponsor_role,
        s.sponsor_name,
        row_number() over (partition by s.nct_id, n.org_id order by s.sponsor_order) as rn
    from {{ ref('stg_ctgov__sponsors') }} as s
    join {{ ref('stg_resolution__sponsor_organizations') }} as n using (sponsor_name)
)
where rn = 1
