-- One value per company, concept and period: the most recently filed one (later filings restate earlier ones).
select * exclude (rn)
from (
    select
        *,
        row_number() over (
            partition by cik, concept, period_start, period_end
            order by filed_date desc, accession desc
        ) as rn
    from {{ ref('stg_sec__facts') }}
)
where rn = 1
