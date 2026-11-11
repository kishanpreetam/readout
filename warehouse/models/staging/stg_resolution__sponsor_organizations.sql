select
    sponsor_name,
    org_id,
    org_name,
    n_name_variants,
    base_name,
    name_clause,
    stated_parents
from {{ source('raw', 'sponsor_organizations') }}
