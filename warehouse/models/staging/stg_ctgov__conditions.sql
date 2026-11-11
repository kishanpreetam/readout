select
    nct_id,
    ord as condition_order,
    trim(condition) as condition_name,
    lower(trim(condition)) as condition_name_lower
from {{ source('raw', 'ct_conditions') }}
where condition is not null and trim(condition) <> ''
