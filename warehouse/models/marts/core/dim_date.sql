with bounds as (
    select
        date_trunc('year', least(min(start_date), min(primary_completion_date)))::date as lo,
        (date_trunc('year', greatest(max(completion_date), max(primary_completion_date), date '{{ var("as_of_date") }}'))
            + interval 1 year - interval 1 day)::date as hi
    from {{ ref('stg_ctgov__studies') }}
),

days as (
    select unnest(generate_series(lo, hi, interval 1 day))::date as date_day
    from bounds
)

select
    cast(strftime(date_day, '%Y%m%d') as integer) as date_key,
    date_day,
    year(date_day) as year,
    quarter(date_day) as quarter,
    month(date_day) as month,
    strftime(date_day, '%B') as month_name,
    year(date_day) || '-Q' || quarter(date_day) as year_quarter,
    dayofweek(date_day) in (0, 6) as is_weekend
from days
