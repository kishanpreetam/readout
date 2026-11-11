-- Studies linked to SEC universe companies through their organizations (empty without SEC data).
select b.nct_id, o.cik, b.sponsor_role, b.org_id
from {{ ref('bridge_trial_organization') }} as b
join {{ ref('dim_organization') }} as o using (org_id)
where o.cik is not null
qualify row_number() over (partition by b.nct_id, o.cik order by case b.sponsor_role when 'lead' then 0 else 1 end) = 1
