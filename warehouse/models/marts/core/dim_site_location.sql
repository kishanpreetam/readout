-- One row per globe point (geo_key) with the place name most sites there use, and its site and trial counts.
with sites as (
    select * from {{ ref('fct_trial_sites') }}
    where geo_key is not null
),

points as (
    select geo_key, count(*) as n_sites, count(distinct nct_id) as n_trials
    from sites
    group by geo_key
),

-- the most common city, state and country at each point, taken together so they always agree;
-- ties go to the alphabetically first spelling
place_names as (
    select
        geo_key,
        city,
        state,
        country,
        row_number() over (
            partition by geo_key
            order by count(*) desc, city nulls last, state nulls last, country nulls last
        ) as rn
    from sites
    group by geo_key, city, state, country
)

select
    p.geo_key,
    -- read back from the key, so lat and lon are exactly the point the key names
    cast(split_part(p.geo_key, ',', 1) as double) as lat,
    cast(split_part(p.geo_key, ',', 2) as double) as lon,
    n.city,
    n.state,
    n.country,
    p.n_sites,
    p.n_trials
from points as p
inner join place_names as n on n.geo_key = p.geo_key and n.rn = 1
