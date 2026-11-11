select
    nct_id,
    ord as outcome_order,
    trim(measure) as outcome_measure,
    trim(time_frame) as time_frame
from {{ source('raw', 'ct_primary_outcomes') }}
where measure is not null
