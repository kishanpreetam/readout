select
    nct_id,
    ord as intervention_order,
    upper(type) as intervention_type,
    trim(name) as intervention_name,
    other_names
from {{ source('raw', 'ct_interventions') }}
where name is not null
