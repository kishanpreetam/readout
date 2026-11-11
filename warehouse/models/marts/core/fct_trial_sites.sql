-- One row per site listed on a study, for studies in dim_trial. ClinicalTrials.gov geocodes sites to the city,
-- so a geo_key (latitude and longitude to four decimals) is effectively a city point on the globe.
select
    s.nct_id,
    s.site_order,
    s.facility,
    s.city,
    s.state,
    s.zip,
    s.country,
    s.site_status,
    s.lat,
    s.lon,
    case when s.lat is not null and s.lon is not null then printf('%.4f,%.4f', s.lat, s.lon) end as geo_key
from {{ ref('stg_ctgov__sites') }} as s
inner join {{ ref('dim_trial') }} as t on t.nct_id = s.nct_id
